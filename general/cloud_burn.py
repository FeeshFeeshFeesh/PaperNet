"""Cloud burn window: what the trial credit is being spent on, at what rate, and how long it lasts.

WHY THIS EXISTS. Cloud spend is invisible until the credit is gone. The bot monitor shows a game;
this shows the other thing that can quietly end the project. Two bars (credit left, trial time
left), every billable resource with what it is for and what it costs an hour, and burn-rate
bars so a change of rate is visible as a step rather than as a surprise.

PRICES ARE REAL, NOT GUESSED. cloud_prices.py pulls published SKU prices from the Cloud Billing
Catalog API into cloud_prices.json (gcloud in this build has no `billing skus`). Checked 2026-09-24
for asia-southeast1, in AUD: a c4a-standard-4 is A$0.308/hr, which is about double the A$0.15/hr
that had been assumed before the catalog was read. Re-run cloud_prices.py to refresh.

THE CREDIT NUMBER IS THE ONE THING THAT CANNOT BE READ. This build of gcloud has no billing
subcommands, the Budget API is not enabled and the account cannot authorise it, so no API here
returns the balance. So the balance is ANCHORED: you paste the figure from the billing console
into ANCHOR_AUD/ANCHOR_AT below, and this integrates measured burn forward from that moment.
That makes the credit bar an ESTIMATE and it says so on screen. Re-anchor from the console every
few days; the drift between anchor and truth is the error bar, and it only grows.

Gaps are not free. While this is not running nothing is sampled, so a gap is integrated at the
last rate that was seen and counted as estimated. That is the honest direction to err in: it
assumes resources you left running kept running, because they did.

READ-ONLY. It lists instances, disks and snapshots and reads each host's scheduled shutdown over
ssh. It never creates, resizes or deletes anything, so it cannot itself cost money beyond the API
calls, and it can be killed at any time.

Run it from PyCharm. No sys.argv; constants below.
"""
import datetime
import json
import os
import subprocess
import sys
import time

GCLOUD   = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
HERE     = os.path.dirname(os.path.abspath(__file__))
PRICES   = os.path.join(HERE, "cloud_prices.json")   # written by cloud_prices.py, beside this file
HISTORY  = os.path.expanduser("~/cloud_burn_history.jsonl")

# --- the anchor: copy these two from the billing console, together ------------------------------
ANCHOR_AUD = 404.71
ANCHOR_AT  = "2026-09-23T12:00:00"
EXPIRES    = "2026-12-20T23:59:59"          # trial credit expiry
# ------------------------------------------------------------------------------------------------

DAILY_CEILING = 50.0        # Vladimir's hard rule: stop resources if the day would exceed this
FLOOR_AUD     = 25.0        # and stop everything if remaining credit reaches this

POLL_S     = 60             # gcloud listing takes a few seconds; the rate moves slowly

# Set by main() every time inventory() actually comes back. The header reports this, not
# time.now(), so a frozen gcloud call shows as a stale screen instead of a fresh-looking one:
# with the in-place redraw a dead poller leaves the last frame on screen forever.
LAST_REFRESH = None
SSH_EVERY  = 10             # probe each host's scheduled shutdown only every Nth poll
SSH_TIMEOUT = 25
GRAPH_W, GRAPH_H = 64, 8
BAR_W      = 58
MODE       = "auto"         # "auto": redraw in place in a terminal, scroll in PyCharm's console
COLOUR     = True

# What each instance is for. Anything not named here shows as "unlabelled".
JOBS = {
    "rps-w1":     "wide-arm SPRT: hidden 768 vs 512 at 500 ms",
    "rps-bench2": "parity/bench host (nps ranking of candidate machines)",
}


def paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if COLOUR else text


GREEN, AMBER, RED, DIM, BOLD, CYAN = "92;1", "93;1", "91;1", "90", "1", "96;1"


# ---------------------------------------------------------------- prices
def load_prices():
    if not os.path.exists(PRICES):
        return None
    return json.load(open(PRICES))


def city_of(prices):
    """The catalog names regions by city ('running in Singapore'); learn it from the table."""
    for d in prices["skus"]:
        if " running in " in d:
            return d.rsplit(" running in ", 1)[1]
    return None


def sku_price(prices, want):
    """First on-demand SKU whose description starts with `want`, skipping sole-tenancy variants."""
    for d, v in sorted(prices["skus"].items()):
        if d.startswith(want) and "Sole Tenancy" not in d and "Confidential" not in d:
            return v["aud"]
    return None


def cpu_ram_price(prices, family):
    """AUD per vCPU-hour and per GiB-hour for a machine family, e.g. 'c4a' -> C4A Arm Instance."""
    city = city_of(prices)
    fam = family.upper()
    for core_word in ("%s Arm Instance Core" % fam, "%s Instance Core" % fam):
        core = sku_price(prices, core_word)
        if core is not None:
            ram_word = core_word.replace("Core", "Ram")
            return core, sku_price(prices, ram_word) or 0.0
    return None, None


# ---------------------------------------------------------------- inventory
def gcloud_json(args):
    try:
        out = subprocess.run([GCLOUD] + args + ["--format=json"], capture_output=True,
                             text=True, timeout=120)
        return json.loads(out.stdout) if out.stdout.strip() else []
    except Exception:
        return []


MT_CACHE = {}


def machine_shape(name, zone):
    """vCPU and GiB for a machine type, asked once and cached."""
    if name in MT_CACHE:
        return MT_CACHE[name]
    d = gcloud_json(["compute", "machine-types", "describe", name, "--zone", zone])
    shape = (d.get("guestCpus", 0), d.get("memoryMb", 0) / 1024.0) if isinstance(d, dict) else (0, 0.0)
    MT_CACHE[name] = shape
    return shape


def age_h(stamp):
    if not stamp:
        return None
    try:
        t = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    now = datetime.datetime.now(t.tzinfo) if t.tzinfo else datetime.datetime.now()
    return (now - t).total_seconds() / 3600.0


def inventory(prices):
    """Every resource that currently costs money, with its AUD/hr."""
    items = []
    for inst in gcloud_json(["compute", "instances", "list"]):
        zone = inst["zone"].rsplit("/", 1)[1]
        mt = inst["machineType"].rsplit("/", 1)[1]
        running = inst.get("status") == "RUNNING"
        cpus, gib = machine_shape(mt, zone)
        core_p, ram_p = cpu_ram_price(prices, mt.split("-")[0])
        rate = (cpus * core_p + gib * ram_p) if (running and core_p is not None) else 0.0
        started = inst.get("lastStartTimestamp") or inst.get("creationTimestamp")
        items.append({"kind": "vm", "name": inst["name"], "detail": "%s  %dv/%.0fGiB  %s"
                      % (mt, cpus, gib, inst.get("status", "?").lower()),
                      "rate": rate, "runtime_h": age_h(started) if running else None,
                      "job": JOBS.get(inst["name"], "unlabelled"), "zone": zone,
                      "running": running})
    cap = sku_price(prices, "Hyperdisk Balanced Capacity")
    pd = sku_price(prices, "Storage PD Capacity")
    for d in gcloud_json(["compute", "disks", "list"]):
        gb = float(d.get("sizeGb", 0))
        per_gib_mo = cap if "hyperdisk" in d.get("type", "") else (pd or cap)
        rate = gb * (per_gib_mo or 0.0) / 730.0
        users = [u.rsplit("/", 1)[1] for u in d.get("users", [])]
        items.append({"kind": "disk", "name": d["name"],
                      "detail": "%.0f GB  %s%s" % (gb, d.get("type", "").rsplit("/", 1)[-1],
                                                   ("  -> " + ", ".join(users)) if users else "  detached"),
                      "rate": rate, "runtime_h": age_h(d.get("creationTimestamp")),
                      "job": "disk for %s" % (users[0] if users else "nothing"),
                      "zone": d.get("zone", "").rsplit("/", 1)[-1], "running": True})
    snap_p = sku_price(prices, "Storage PD Snapshot")
    snaps = gcloud_json(["compute", "snapshots", "list"])
    if snaps:
        tot = sum(float(s.get("storageBytes", 0)) for s in snaps) / (1024.0 ** 3)
        items.append({"kind": "snap", "name": "%d snapshots" % len(snaps),
                      "detail": "%.1f GiB stored" % tot,
                      "rate": tot * (snap_p or 0.0) / 730.0,
                      "runtime_h": None, "job": "saved machine images", "zone": "-",
                      "running": True})
    return items


# ---------------------------------------------------------------- scheduled shutdowns (the ETA)
ETA_CACHE = {}


def scheduled_shutdown(name, zone):
    """When the host's own deadman will stop the meter. That is the honest ETA for a VM."""
    try:
        out = subprocess.run([GCLOUD, "compute", "ssh", name, "--zone", zone, "--command",
                              "shutdown --show 2>&1 | head -1"],
                             capture_output=True, text=True, timeout=SSH_TIMEOUT)
        txt = (out.stdout + out.stderr)
        for line in txt.splitlines():
            if "Shutdown scheduled for" in line:
                body = line.split("Shutdown scheduled for", 1)[1].split(", use")[0].strip()
                return body
        if "No scheduled shutdown" in txt:
            return "none armed"
    except Exception:
        pass
    return "?"


def refresh_etas(items, tick):
    for it in items:
        if it["kind"] != "vm" or not it["running"]:
            continue
        if tick % SSH_EVERY == 0 or it["name"] not in ETA_CACHE:
            ETA_CACHE[it["name"]] = scheduled_shutdown(it["name"], it["zone"])
        it["eta"] = ETA_CACHE.get(it["name"], "?")


def eta_hours(text):
    """Hours from now until a `shutdown --show` timestamp, or None if it is not one."""
    if not text or text in ("?", "none armed"):
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
        try:
            t = datetime.datetime.strptime(text, fmt).replace(tzinfo=datetime.timezone.utc)
            # the host reports UTC; compare in UTC
            return (t - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 3600.0
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- spend integration
def load_history():
    rows = []
    if os.path.exists(HISTORY):
        for line in open(HISTORY):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def integrate(rows, rate, now):
    """Trapezoid between samples. A gap is charged at the last rate seen, and flagged."""
    if not rows:
        anchor = datetime.datetime.fromisoformat(ANCHOR_AT).timestamp()
        # Nothing was sampled between the anchor and this first run. Charge that stretch at the
        # rate we can see now -- resources left running were running the whole time.
        spent = max(0.0, (now - anchor) / 3600.0) * rate
        return spent, True
    last = rows[-1]
    dt_h = max(0.0, (now - last["t"]) / 3600.0)
    spent = last["spent"] + dt_h * (last["rate"] + rate) / 2.0
    return spent, dt_h > (POLL_S * 4) / 3600.0


def record(now, rate, spent):
    with open(HISTORY, "a") as fh:
        fh.write(json.dumps({"t": now, "rate": rate, "spent": spent}) + "\n")


# ---------------------------------------------------------------- drawing
def bar(frac, width, code):
    frac = max(0.0, min(1.0, frac))
    fill = int(round(frac * width))
    return paint("█" * fill, code) + paint("░" * (width - fill), DIM)


def cell(text, width, code=None):
    """Left-justify to a VISIBLE width. paint() adds 11 invisible bytes, so %-*s cannot be
    trusted once a cell is coloured -- it pads to the byte count and the column walks."""
    body = text[:width].ljust(width)
    return paint(body, code) if code else body


def hours_text(h):
    if h is None:
        return "  --  "
    if h < 48:
        return "%.1f h" % h
    return "%.1f d" % (h / 24.0)


BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def graph(rows, w, h):
    """Burn rate over time, as columns standing on a ZERO baseline.

    Bars, not a line, and the baseline is zero on purpose: a bar's height is read as its value,
    so cropping the axis to min..max would make a 5% wobble look like a host had been switched
    on. Flat bars are normal; a taller block means something started, a shorter one means
    something stopped, and the step lands where it happened.
    """
    if len(rows) < 2:
        return [paint("  (burn-rate bars appear once there are two samples)", DIM)]
    pts = rows[-w:]
    vals = [r["rate"] for r in pts]
    lo, hi = min(vals), max(vals)
    span_h = (pts[-1]["t"] - pts[0]["t"]) / 3600.0
    if hi <= 0:
        return [paint("  nothing billable running for the last %.1f h" % span_h, DIM)]
    if hi - lo < max(1e-6, 0.001 * hi):
        # Nothing has started or stopped. Eight rows of identical bars say less than one line
        # that says so, and the repeated axis label reads like a bug.
        return [paint("%7.4f " % hi, DIM) + paint("\u2588" * len(vals), CYAN)
                + paint("  flat over the last %.1f h" % span_h, DIM)]
    # A rate that is billing but tiny must not render as an empty column -- blank means zero
    # here, so anything above zero gets at least one eighth of a block.
    eighths = [max(1, int(round(v / hi * h * 8))) if v > 0 else 0 for v in vals]
    out = []
    for i in range(h):
        level = h - 1 - i                       # 0 is the bottom row of the column
        row = "".join(BLOCKS[max(0, min(8, e - 8 * level))] for e in eighths)
        label = ("%7.3f " % hi) if i == 0 else (("%7.3f " % 0.0) if i == h - 1 else " " * 8)
        out.append(paint(label, DIM) + paint(row, CYAN))
    out.append(paint("        last %.1f h of burn rate (A$/hr), bars from zero" % span_h, DIM))
    return out


def screen(items, rate, spent, estimated, stale):
    remaining = ANCHOR_AUD - spent
    frac_credit = remaining / ANCHOR_AUD if ANCHOR_AUD else 0.0
    exp = datetime.datetime.fromisoformat(EXPIRES)
    anchor = datetime.datetime.fromisoformat(ANCHOR_AT)
    left_h = (exp - datetime.datetime.now()).total_seconds() / 3600.0
    total_h = (exp - anchor).total_seconds() / 3600.0
    frac_time = max(0.0, left_h / total_h) if total_h else 0.0

    credit_code = GREEN if remaining > 2 * FLOOR_AUD else (AMBER if remaining > FLOOR_AUD else RED)
    daily = rate * 24.0
    day_code = GREEN if daily < DAILY_CEILING * 0.5 else (AMBER if daily < DAILY_CEILING else RED)
    runway_h = (remaining / rate) if rate > 1e-9 else None

    L = []
    if LAST_REFRESH is None:
        age_txt, age_code = "never", RED
    else:
        age = time.time() - LAST_REFRESH
        age_code = (GREEN if age < 2 * POLL_S else
                    AMBER if age < 5 * POLL_S else RED)
        age_txt = ("%.0fs ago" % age if age < 90 else
                   "%.1f min ago" % (age / 60.0) if age < 5400 else
                   "%.1f h ago" % (age / 3600.0))
    stamp = ("--:--:--" if LAST_REFRESH is None
             else datetime.datetime.fromtimestamp(LAST_REFRESH).strftime("%H:%M:%S"))
    L.append(paint("  CLOUD BURN", BOLD)
             + paint("   %s   project-43725f9d" %
                     datetime.datetime.now().strftime("%a %d %b %H:%M:%S"), DIM))
    L.append("  " + paint("last refreshed", DIM) + "  "
             + paint("%s  (%s)" % (stamp, age_txt), age_code)
             + paint("   polling every %ds" % POLL_S, DIM))
    L.append("")
    L.append("  %s  %s" % (paint("CREDIT", BOLD), bar(frac_credit, BAR_W, credit_code)))
    L.append("          %s of A$%.2f left  (%.1f%%)   spent A$%.2f since anchor%s"
             % (paint("A$%.2f" % remaining, credit_code), ANCHOR_AUD, frac_credit * 100.0,
                spent, paint("  ESTIMATED", AMBER) if estimated else ""))
    L.append("")
    L.append("  %s    %s" % (paint("TIME", BOLD), bar(frac_time, BAR_W, GREEN if frac_time > .25 else AMBER)))
    L.append("          %s until credit expires %s  (%.1f%%)"
             % (paint("%.0f days" % (left_h / 24.0), BOLD), exp.strftime("%d %b %Y"), frac_time * 100.0))
    L.append("")
    L.append("  %s A$%.4f/hr   =  A$%s/day   %s"
             % (paint("BURN", BOLD), rate, paint("%.2f" % daily, day_code),
                paint("(ceiling A$%.0f/day)" % DAILY_CEILING, DIM)))
    if runway_h is not None:
        verdict = "outlasts the expiry" if runway_h > left_h else paint("RUNS OUT FIRST", RED)
        L.append("          at this rate the credit lasts %s -- %s" % (hours_text(runway_h), verdict))
    L.append("")
    L.append(paint("  RESOURCE            WHAT IT IS FOR                        A$/hr     RUN     STOPS         BY STOP", BOLD))
    for it in sorted(items, key=lambda x: -x["rate"]):
        eta = it.get("eta", "-")
        eh = eta_hours(eta)
        by = ("A$%.2f" % (it["rate"] * eh)) if (eh is not None and eh > 0) else "  -"
        code = DIM if it["rate"] < 1e-6 else (AMBER if it["rate"] > 0.2 else GREEN)
        if eh is not None:
            short, scode = "stops in %s" % hours_text(eh), None
        elif eta == "none armed":
            short, scode = "no deadman", RED
        else:
            short, scode = ("-" if eta in ("-", "?") else str(eta)[:18]), None
        L.append("  %-19s %-36s %s  %7s  %s %8s"
                 % (it["name"][:19], it["job"][:36], paint("%7.4f" % it["rate"], code),
                    hours_text(it.get("runtime_h")), cell(short, 18, scode), by))
        L.append(paint("    %s" % it["detail"], DIM))
    L.append("")
    L += graph(load_history(), GRAPH_W, GRAPH_H)
    L.append("")
    note = "anchored %s (%.1f d ago) -- re-read the console and update ANCHOR_AUD/ANCHOR_AT" % (
        anchor.strftime("%d %b"), (datetime.datetime.now() - anchor).total_seconds() / 86400.0)
    L.append(paint("  " + note, DIM))
    if stale:
        L.append(paint("  gap since last sample: charged at the previous rate, so spend is a guess",
                       AMBER))
    if remaining <= FLOOR_AUD:
        L.append(paint("  STOP: remaining credit is at or below the A$%.0f floor -- shut resources down"
                       % FLOOR_AUD, RED))
    return L


def main():
    live = MODE == "live" or (MODE == "auto" and sys.stdout.isatty())
    prices = load_prices()
    if not prices:
        print("STOP: no %s -- run cloud_prices.py first" % PRICES)
        return
    if live:
        sys.stdout.write("\033[2J")
    tick = 0
    while True:
        items = inventory(prices)
        global LAST_REFRESH
        LAST_REFRESH = time.time()
        refresh_etas(items, tick)
        rate = sum(i["rate"] for i in items)
        now = time.time()
        rows = load_history()
        spent, stale = integrate(rows, rate, now)
        record(now, rate, spent)
        lines = screen(items, rate, spent, estimated=(not rows) or stale, stale=stale)
        if live:
            sys.stdout.write("\033[H" + "".join(r + "\033[K\n" for r in lines) + "\033[J")
            sys.stdout.flush()
        else:
            print("\n".join(lines), flush=True)
        tick += 1
        time.sleep(POLL_S)


main()
