import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

import json, io
from collections import Counter
rows = json.loads(io.open(os.path.join(HERE, "breaks.json")).read())

def mat(r):
    a, b = r["mine"], r["theirs"]
    if a is None or b is None: return "?"
    return "ahead" if a > b else ("level" if a == b else "behind")

def delta(r):
    if None in (r["mine"], r["theirs"], r["mine_after"], r["theirs_after"]): return "?"
    dm = r["mine_after"] - r["mine"]; dt = r["theirs_after"] - r["theirs"]
    if dm == 0 and dt == 0: return "nothing yet"
    if dm == 0 and dt < 0:  return "we win a piece"
    if dm < 0 and dt == 0:  return "WE LOSE A PIECE"
    if dm < 0 and dt < 0:   return "trade"
    return "other"

for who in ("us", "them"):
    sub = [r for r in rows if r["who"] == who]
    print("-----%s leave the cycle: %d-----" % (who.upper(), len(sub)))
    print("  material when it breaks :", dict(Counter(mat(r) for r in sub)))
    print("  5 snapshots later       :", dict(Counter(delta(r) for r in sub)))
    print("  next capture made by    :", dict(Counter(r["capturer"] for r in sub)))
    ev = sorted(r["ev"] for r in sub if r["ev"] is not None)
    dp = sorted(r["depth"] for r in sub if r["depth"] is not None)
    ct = sorted(r["counter"] for r in sub if r["counter"] is not None)
    if ev: print("  eval at the break       : median %+d   (%+d .. %+d)" % (ev[len(ev)//2], ev[0], ev[-1]))
    if dp: print("  depth at the break      : median %d" % dp[len(dp)//2])
    if ct: print("  counter at the break    : median %d   (%d .. %d)" % (ct[len(ct)//2], ct[0], ct[-1]))
    # outcome of the GAMES in which we/they broke
    seen, w, d, l = set(), 0, 0, 0
    for r in sub:
        if r["game"] in seen: continue
        seen.add(r["game"])
        res = (r["result"] or "").upper()
        if res == "WIN": w += 1
        elif res == "DRAW": d += 1
        elif res == "LOSS": l += 1
    n = w + d + l
    if n: print("  games (deduped): %dW %dD %dL  n=%d  score %.3f" % (w, d, l, n, (w + .5*d)/n))
    print("  visits to the cycle before breaking: median %d, max %d" %
          (sorted(r["visits"] for r in sub)[len(sub)//2], max(r["visits"] for r in sub)))
print("-----our breaks, split by material-----")
sub = [r for r in rows if r["who"] == "us"]
for m in ("ahead", "level", "behind"):
    s2 = [r for r in sub if mat(r) == m]
    if not s2: continue
    print("  %-7s n=%-3d  next capture: %s   5-later: %s"
          % (m, len(s2), dict(Counter(r["capturer"] for r in s2)), dict(Counter(delta(r) for r in s2))))
print("confirm")
