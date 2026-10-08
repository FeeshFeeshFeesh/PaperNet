"""Pull REAL Compute Engine prices from the Cloud Billing Catalog API and cache them.

gcloud in this build has no `billing skus` subcommand, so every price we have used so far
(~A$0.15/hr for c4a) has been an estimate. The Catalog REST API does work with an ordinary
access token, so the burn monitor can run on published prices instead of my guesses.

Writes cloud_prices.json. Run it again to refresh; the monitor reads the cache, so a stale
network does not take the monitor down with it.

No sys.argv: constants below.
"""
import json
import os
import ssl
import subprocess
import urllib.request

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _ROOT
GCLOUD = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
COMPUTE_SVC = "6F81-5844-456A"
REGION = "asia-southeast1"
CURRENCY = "AUD"                 # the billing account and the credit are both in AUD
OUT = os.path.join(S, "cloud_prices.json")


def tls():
    """A verifying SSL context that also works under PyCharm's interpreter.

    The python.org 3.12 in nnue_train/.venv ships NO CA bundle -- the path it
    advertises, .../Versions/3.12/etc/openssl/cert.pem, does not exist until
    somebody runs "Install Certificates.command" -- so urlopen dies with
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`, while
    the identical call under /usr/bin/python3 (3.9, which trusts
    /etc/ssl/cert.pem) succeeds. Running this from PyCharm therefore failed
    while running it from a shell worked, which is a confusing half hour.

    macOS keeps a usable bundle at /etc/ssl/cert.pem regardless, so fall back
    to it (or to certifi, if it is ever installed) when the default store came
    up empty. Verification stays ON in every branch: never reach for
    ssl._create_unverified_context to make this go away -- this call carries an
    OAuth bearer token.
    """
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"]:
        return ctx
    try:
        import certifi
        candidates = [certifi.where(), "/etc/ssl/cert.pem"]
    except ImportError:
        candidates = ["/etc/ssl/cert.pem"]
    for ca in candidates:
        if ca and os.path.exists(ca):
            ctx.load_verify_locations(cafile=ca)
            return ctx
    raise SystemExit("no CA bundle found; run \"Install Certificates.command\" for this Python")


def token():
    return subprocess.run([GCLOUD, "auth", "print-access-token"],
                          capture_output=True, text=True).stdout.strip()


CTX = tls()


def fetch_all(tok):
    skus, page, url = [], "", ("https://cloudbilling.googleapis.com/v1/services/%s/skus"
                               "?pageSize=5000&currencyCode=%s" % (COMPUTE_SVC, CURRENCY))
    while True:
        req = urllib.request.Request(url + page, headers={"Authorization": "Bearer " + tok})
        data = json.load(urllib.request.urlopen(req, timeout=60, context=CTX))
        skus += data.get("skus", [])
        nxt = data.get("nextPageToken")
        if not nxt:
            return skus
        page = "&pageToken=" + nxt


def unit_price(sku):
    """AUD per usageUnit, from the first (zero-start) tier."""
    for pi in sku.get("pricingInfo", []):
        for tier in pi["pricingExpression"]["tieredRates"]:
            if float(tier["startUsageAmount"]) == 0:
                up = tier["unitPrice"]
                return (int(up.get("units", 0)) + int(up.get("nanos", 0)) / 1e9,
                        pi["pricingExpression"]["usageUnit"], up.get("currencyCode"))
    return None


def main():
    tok = token()
    if not tok:
        print("STOP: no access token")
        return
    skus = fetch_all(tok)
    here = [s for s in skus if REGION in s.get("serviceRegions", [])]
    print("%d SKUs total, %d in %s" % (len(skus), len(here), REGION))

    table = {}
    for s in here:
        d = s["description"]
        if s["category"]["usageType"] != "OnDemand":
            continue
        p = unit_price(s)
        if p is None:
            continue
        table[d] = {"aud": p[0], "unit": p[1], "currency": p[2], "sku": s["skuId"],
                    "family": s["category"]["resourceFamily"],
                    "group": s["category"]["resourceGroup"]}

    json.dump({"region": REGION, "currency": CURRENCY, "skus": table}, open(OUT, "w"), indent=1)
    print("wrote %s (%d on-demand SKUs)" % (OUT, len(table)))

    for want in ("C4A Instance Core", "C4A Instance Ram", "T2A Instance Core", "T2A Instance Ram",
                 "Hyperdisk Balanced Capacity", "Hyperdisk Balanced IOPS",
                 "Hyperdisk Balanced Throughput", "Storage PD Snapshot", "External IP Charge"):
        for d, v in sorted(table.items()):
            if d.startswith(want):
                print("  %-58s %10.6f %s/%s" % (d[:58], v["aud"], v["currency"], v["unit"]))


main()
