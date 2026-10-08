import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

"""Did the search SEE something at the moment we left the cycle?

The user's argument: a forced win visible 10 plies before the clock fires was
visible 100 plies earlier too, so leaving the cycle can only be right if the
search has just now found something it could not see before.  That predicts a
JUMP in eval (or a mate score) at the break.  Flat eval = nothing was seen.
"""
import sys, json, io
from statistics import median
from collections import Counter
sys.path.insert(0, os.path.dirname(HERE))
import live_boards as lb
sys.path.insert(0, HERE)
from cycle_break import loops, key, HIGH

games = lb.parse()
rows = json.loads(io.open(os.path.join(HERE, "fates.json")).read())

MATE = 30000
recs = []
for r in rows:
    if r["who"] != "us":
        continue
    sn = games[r["game"]]["plies"]
    bi = next((k for k, s in enumerate(sn) if s["ply"] == r["ply"]), None)
    if bi is None or bi < 4:
        continue
    # the cycle itself: walk back while the board keeps matching at period p
    p = r["p"]; i = bi - 1
    while i - p >= 0 and key(sn[i]) == key(sn[i - p]):
        i -= 1
    cyc = [s for s in sn[i:bi] if s.get("eval") is not None]
    if len(cyc) < 3:
        continue
    ins  = [s["eval"] for s in cyc]
    at   = sn[bi].get("eval")
    dcyc = [s["depth"] for s in cyc if s.get("depth") is not None]
    recs.append(dict(game=r["game"], ply=r["ply"], counter=r["counter"], fate=r["fate"],
                     mine=r["mine"], theirs=r["theirs"],
                     cyc_med=median(ins), cyc_last=ins[-1], at=at,
                     jump=(None if at is None else at - median(ins)),
                     mate=(at is not None and abs(at) >= MATE),
                     dep_med=(median(dcyc) if dcyc else None),
                     dep_at=sn[bi].get("depth")))

print("our breaks with a readable cycle: %d" % len(recs))
print()
print("-----did the eval JUMP when we left?-----")
j = sorted(r["jump"] for r in recs if r["jump"] is not None)
print("  eval(break) - median eval(cycle):  median %+d cp,  p10 %+d,  p90 %+d"
      % (j[len(j)//2], j[max(0,len(j)//10)], j[min(len(j)-1, 9*len(j)//10)]))
print("  |jump| >= 100 cp : %d of %d" % (sum(1 for x in j if abs(x) >= 100), len(j)))
print("  jump >= +200 cp  : %d of %d" % (sum(1 for x in j if x >= 200), len(j)))
print("  a MATE score at the break: %d of %d" % (sum(1 for r in recs if r["mate"]), len(recs)))
print()
print("-----and did the search get DEEPER (the horizon collapse)?-----")
d1 = [r["dep_med"] for r in recs if r["dep_med"] is not None]
d2 = [r["dep_at"] for r in recs if r["dep_at"] is not None]
print("  depth during the cycle: median %d     at the break: median %d" % (median(d1), median(d2)))
gap = sorted(r["dep_at"] - r["dep_med"] for r in recs if r["dep_at"] is not None and r["dep_med"] is not None)
print("  depth(break) - depth(cycle): median %+.0f  (%+.0f .. %+.0f)" % (gap[len(gap)//2], gap[0], gap[-1]))
print()
print("-----the 41 breaks that reset the clock, sorted by how much the eval moved-----")
bad = [r for r in recs if r["fate"].startswith("capture")]
print("%-5s %-6s %-5s %-6s %-9s %-8s %-8s %s" % ("gm","ply","cnt","mat","cyc med","at break","jump","fate"))
for r in sorted(bad, key=lambda r: -(abs(r["jump"]) if r["jump"] is not None else -1))[:14]:
    m = "%dv%d" % ((r["mine"] or 0)//208, (r["theirs"] or 0)//208)
    print("%-5d %-6d %-5s %-6s %+9d %+8s %+8s %s"
          % (r["game"], r["ply"], r["counter"], m, r["cyc_med"],
             r["at"] if r["at"] is not None else "?",
             ("%d" % r["jump"]) if r["jump"] is not None else "?", r["fate"]))
print("  ... %d of these %d moved the eval by less than 100 cp"
      % (sum(1 for r in bad if r["jump"] is not None and abs(r["jump"]) < 100), len(bad)))
print("confirm")
