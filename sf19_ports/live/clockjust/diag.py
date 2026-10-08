"""Is the eval INVERTED at high clock, or is the sub-0.5 AUC an artifact?"""
import json, collections
rows = [json.loads(l) for l in open("evals.jsonl")]
hi = [r for r in rows if r["clock"] >= 60]

print("outcome mix, clock>=60 :", dict(collections.Counter(r["result"] for r in hi)))
print("  -> AUC(WIN) and AUC(DRAW) are near-complements here, so the negative")
print("     DRAW gap is the SAME fact as the positive WIN gap, not a conflict.")
print()

for key in ("ev_real", "ev_zero"):
    hi.sort(key=lambda r: r[key])
    print("%s decile -> realised mean game score (clock>=60, n=%d)" % (key, len(hi)))
    q = len(hi) // 5
    for i in range(5):
        chunk = hi[i*q:(i+1)*q] if i < 4 else hi[4*q:]
        s = sum(r["score"] for r in chunk) / len(chunk)
        print("   quintile %d  ev %6d..%-6d  n=%3d  score %.3f  games %2d"
              % (i+1, chunk[0][key], chunk[-1][key], len(chunk), s,
                 len({r["game"] for r in chunk})))
    print()

# is it one-position-per-game leakage? collapse each game to its MEDIAN eval
print("collapsed to ONE row per game (median ev_real), clock>=60:")
bygame = {}
for r in hi:
    bygame.setdefault(r["game"], []).append(r)
g = []
for k, v in bygame.items():
    v.sort(key=lambda r: r["ev_real"])
    g.append((v[len(v)//2]["ev_real"], v[0]["score"], v[0]["result"]))
g.sort()
h = len(g) // 2
lo, hh = g[:h], g[h:]
print("   lower half med-eval  n=%d  score %.3f" % (len(lo), sum(x[1] for x in lo)/len(lo)))
print("   upper half med-eval  n=%d  score %.3f" % (len(hh), sum(x[1] for x in hh)/len(hh)))
print("   -> games where we look MOST winning at high clock score %s"
      % ("WORSE" if sum(x[1] for x in hh)/len(hh) < sum(x[1] for x in lo)/len(lo) else "better"))
