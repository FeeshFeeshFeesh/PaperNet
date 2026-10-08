import json, bisect
rows = [json.loads(l) for l in open("evals.jsonl")]

def auc(pairs):
    pos = [s for s, y in pairs if y]; neg = sorted(s for s, y in pairs if not y)
    if not pos or not neg: return None
    t = 0.0
    for s in pos:
        lo, hi = bisect.bisect_left(neg, s), bisect.bisect_right(neg, s)
        t += lo + 0.5 * (hi - lo)
    return t / (len(pos) * len(neg))

for cut, name in ((0, "ALL"), (60, "clock>=60")):
    v = [r for r in rows if r["clock"] >= cut]
    print("%s (n=%d)" % (name, len(v)))
    for key in ("ev_real", "ev_zero"):
        wl = [r for r in v if r["result"] != "DRAW"]
        print("   %s  LOSS-vs-not %.3f | WIN-vs-LOSS only %.3f (n=%d) | DRAW-vs-decisive %.3f"
              % (key,
                 auc([(-r[key], r["result"] == "LOSS") for r in v]),
                 auc([(r[key], r["result"] == "WIN") for r in wl]), len(wl),
                 auc([(r[key], r["result"] == "DRAW") for r in v])))
    print()
print("=> if WIN-vs-LOSS is well above 0.5 while WIN-vs-not is below it, the eval")
print("   is ordering DRAWS above WINS: high eval at high clock = FORTRESS, not loss.")
