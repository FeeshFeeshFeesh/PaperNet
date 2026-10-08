"""Where exactly does the gradient die? The clock is the axis, not the depth."""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "sweep.jsonl"))]
NODES = sorted({r["ask_nodes"] for r in rows})
idx = {(r["game"], r["ply"], r["arm"], r["ask_nodes"]): r for r in rows}
POS = sorted({(r["game"], r["ply"]) for r in rows})
def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else float("nan")
def first(r):
    return next(iter(r["scores"]), None) if r and r.get("scores") else None

print("=== F. FINE clock scan, high arm @800k  (the low arm is the SAME positions, clock field zeroed) ===")
print("%9s %4s | %8s %7s %7s %7s | %6s %6s | %8s %7s"
      % ("clock", "n", "static", "best", "worst", "spread", "flat0", "abs<=5", "LOWstatic", "LOWbest"))
bands = [(119,129),(130,139),(140,149),(150,159),(160,169),(170,179),(180,184),(185,189),(190,194),(195,199)]
for lo, hi in bands:
    sel = [(g,p) for g,p in POS if idx.get((g,p,"high",NODES[-1]))
           and lo <= idx[(g,p,"high",NODES[-1])]["real_clock"] <= hi]
    if not sel: continue
    H = [idx[(g,p,"high",NODES[-1])] for g,p in sel]
    L = [idx[(g,p,"low", NODES[-1])] for g,p in sel if idx.get((g,p,"low",NODES[-1]))]
    flat = sum(1 for r in H if r["spread"] == 0)
    tiny = sum(1 for r in H if r["best"] is not None and abs(r["best"]) <= 5)
    print("%9s %4d | %8.0f %7.0f %7.0f %7.0f | %4d/%-3d %6d | %8.0f %7.0f"
          % ("%d-%d"%(lo,hi), len(sel), med([r["static"] for r in H]), med([r["best"] for r in H]),
             med([r["worst"] for r in H]), med([r["spread"] for r in H]), flat, len(H), tiny,
             med([r["static"] for r in L]), med([r["best"] for r in L])))

print("\n=== G. the dead zone: every position with clock >= 180, high arm @800k ===")
dead = [(g,p) for g,p in POS if idx.get((g,p,"high",NODES[-1])) and idx[(g,p,"high",NODES[-1])]["real_clock"] >= 180]
print("  n = %d" % len(dead))
allzero = sum(1 for g,p in dead
              if all(v == 0 for v in idx[(g,p,"high",NODES[-1])]["scores"].values()))
print("  every root move scores EXACTLY 0 : %d of %d" % (allzero, len(dead)))
print("  static exactly 0                 : %d of %d"
      % (sum(1 for g,p in dead if idx[(g,p,"high",NODES[-1])]["static"] == 0), len(dead)))
print("  material: mine-theirs median %+.0f  (ahead in %d of %d)"
      % (med([idx[(g,p,"high",NODES[-1])]["mine"] - idx[(g,p,"high",NODES[-1])]["theirs"] for g,p in dead]),
         sum(1 for g,p in dead if idx[(g,p,"high",NODES[-1])]["mine"] > idx[(g,p,"high",NODES[-1])]["theirs"]), len(dead)))
print("  SAME positions, clock zeroed: median best %+.0f  median spread %.0f  all-zero %d"
      % (med([idx[(g,p,"low",NODES[-1])]["best"] for g,p in dead if idx.get((g,p,"low",NODES[-1]))]),
         med([idx[(g,p,"low",NODES[-1])]["spread"] for g,p in dead if idx.get((g,p,"low",NODES[-1]))]),
         sum(1 for g,p in dead if idx.get((g,p,"low",NODES[-1])) and all(v==0 for v in idx[(g,p,"low",NODES[-1])]["scores"].values()))))
print("  does MORE SEARCH help the dead zone? median spread by nodes, high arm:")
for n in NODES:
    s = [idx[(g,p,"high",n)]["spread"] for g,p in dead if idx.get((g,p,"high",n)) and idx[(g,p,"high",n)]["spread"] is not None]
    z = sum(1 for g,p in dead if idx.get((g,p,"high",n)) and idx[(g,p,"high",n)]["spread"] == 0)
    print("     %7d nodes: median spread %4.0f   all-equal %2d/%d" % (n, med(s), z, len(s)))

print("\n=== H. the LIVE zone: clock 119-179 still has a gradient. Does depth erode it? ===")
alive = [(g,p) for g,p in POS if idx.get((g,p,"high",NODES[-1])) and idx[(g,p,"high",NODES[-1])]["real_clock"] < 180]
print("  n = %d" % len(alive))
print("%9s | %8s %7s %7s | %8s %7s" % ("nodes", "HIGHbest", "spread", "flat0", "LOWbest", "spread"))
for n in NODES:
    H = [idx[(g,p,"high",n)] for g,p in alive if idx.get((g,p,"high",n))]
    L = [idx[(g,p,"low", n)] for g,p in alive if idx.get((g,p,"low", n))]
    print("%9d | %8.0f %7.0f %5d/%-3d | %8.0f %7.0f"
          % (n, med([r["best"] for r in H]), med([r["spread"] for r in H]),
             sum(1 for r in H if r["spread"] == 0), len(H),
             med([r["best"] for r in L]), med([r["spread"] for r in L])))

print("\n=== I. best move: does zeroing the clock change what we would PLAY? ===")
for label, sel in (("clock>=180 (dead)", dead), ("clock<180 (live)", alive)):
    a = [(first(idx.get((g,p,"high",NODES[-1]))), first(idx.get((g,p,"low",NODES[-1])))) for g,p in sel]
    a = [(x,y) for x,y in a if x and y]
    d = [(first(idx.get((g,p,"high",NODES[0]))), first(idx.get((g,p,"high",NODES[-1])))) for g,p in sel]
    d = [(x,y) for x,y in d if x and y]
    print("  %-18s high vs low same move %2d/%2d (%3.0f%%)   20k vs 800k same %2d/%2d (%3.0f%%)"
          % (label, sum(x==y for x,y in a), len(a), 100.0*sum(x==y for x,y in a)/max(1,len(a)),
             sum(x==y for x,y in d), len(d), 100.0*sum(x==y for x,y in d)/max(1,len(d))))
