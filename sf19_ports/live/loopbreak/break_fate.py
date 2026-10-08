import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

"""For every long loop, follow it to the end: was the draw secured, or reset?"""
import sys, json, io
from collections import Counter
sys.path.insert(0, os.path.dirname(HERE))
import live_boards as lb
sys.path.insert(0, HERE)
from cycle_break import loops, HIGH

games = lb.parse()
rows = json.loads(io.open(os.path.join(HERE, "breaks.json")).read())

# re-walk each break inside its game and find the FIRST capture after it
out = []
for r in rows:
    sn = games[r["game"]]["plies"]
    bi = next((k for k, s in enumerate(sn) if s["ply"] == r["ply"]), None)
    if bi is None:
        continue
    fate, at = "clock ran out -> DRAW", None
    for k in range(bi, len(sn)):
        c = sn[k].get("counter")
        if c is None:
            continue
        if c <= 1:                              # the clock was reset: a capture
            fate = "capture by " + ("us" if c == 0 else "them")
            at = sn[k]["ply"] - r["ply"]
            break
        if c >= 199:
            fate, at = "clock ran out -> DRAW", sn[k]["ply"] - r["ply"]
            break
    rr = dict(r); rr["fate"] = fate; rr["plies_later"] = at
    out.append(rr)

print("long loops (counter >= %d): %d" % (HIGH, len(out)))
print()
print("-----what happened to the draw, by who left the cycle-----")
print("%-6s %-22s %5s" % ("who", "fate", "n"))
for who in ("us", "them"):
    sub = [r for r in out if r["who"] == who]
    for f, n in Counter(r["fate"] for r in sub).most_common():
        print("%-6s %-22s %5d" % (who, f, n))
    print("%-6s %-22s %5d" % (who, "TOTAL", len(sub)))
print()
print("-----the dangerous event: we leave the cycle AND the clock then resets-----")
bad = [r for r in out if r["who"] == "us" and r["fate"].startswith("capture")]
print("n = %d of %d our breaks" % (bad and len(bad) or 0, len([r for r in out if r["who"]=="us"])))
seen, w, d, l = set(), 0, 0, 0
for r in bad:
    if r["game"] in seen: continue
    seen.add(r["game"])
    res = (r["result"] or "").upper()
    w += res == "WIN"; d += res == "DRAW"; l += res == "LOSS"
n = w + d + l
if n: print("games: %dW %dD %dL  n=%d  score %.3f" % (w, d, l, n, (w + .5*d)/n))
pl = sorted(r["plies_later"] for r in bad if r["plies_later"] is not None)
if pl: print("plies from the break to the capture: median %d (%d .. %d)" % (pl[len(pl)//2], pl[0], pl[-1]))
print("capturer:", dict(Counter(r["fate"] for r in bad)))
print()
print("%-4s %-12s %-6s %-7s %-4s %-5s %-6s %-9s %-9s %s" %
      ("gm", "opp", "ply", "cnt", "p", "vis", "mat", "our move", "cycle", "fate"))
for r in sorted(bad, key=lambda r: r["ply"]):
    mine, theirs = r["mine"], r["theirs"]
    m = "?" if None in (mine, theirs) else ("%dv%d" % (mine // 208, theirs // 208) if mine > 300 else "%dv%d" % (mine, theirs))
    print("%-4d %-12s %-6d %-7s %-4d %-5d %-6s %-9s %-9s %s"
          % (r["game"], (r["opp"] or "?")[:12], r["ply"], r["counter"], r["p"], r["visits"],
             m, r["our_mv"] or "?", r["our_cyc"] or "?", r["fate"] + (" +%d" % r["plies_later"] if r["plies_later"] else "")))
io.open(os.path.join(HERE, "fates.json"),"w").write(json.dumps(out))
print("confirm")
