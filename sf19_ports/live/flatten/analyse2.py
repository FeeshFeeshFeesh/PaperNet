"""Follow-ups: is the decay real & paired, is anything TOTALLY flat, does it track the clock?"""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "sweep.jsonl"))]
NODES = sorted({r["ask_nodes"] for r in rows})
idx = {}
for r in rows:
    idx[(r["game"], r["ply"], r["arm"], r["ask_nodes"])] = r
POS = sorted({(r["game"], r["ply"]) for r in rows})

def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else float("nan")

print("=== A. PAIRED decay within a position: best@20k -> best@800k ===")
for arm in ("high", "low"):
    d = []
    for g, p in POS:
        a, b = idx.get((g, p, arm, NODES[0])), idx.get((g, p, arm, NODES[-1]))
        if a and b and a["best"] is not None and b["best"] is not None:
            d.append(b["best"] - a["best"])
    fell = sum(1 for x in d if x < 0)
    print("  %-5s n=%3d  median change %+6.0f  (p10 %+.0f p90 %+.0f)  FELL in %d of %d (%.0f%%)"
          % (arm, len(d), med(d), sorted(d)[len(d)//10], sorted(d)[9*len(d)//10],
             fell, len(d), 100.0*fell/len(d)))

print("\n=== B. is anything TOTALLY flat? (all MultiPV root moves the same score) ===")
print("%8s | %s" % ("nodes", "  arm   spread==0   spread<=10   best==0   worst==0"))
for n in NODES:
    for arm in ("high", "low"):
        g = [r for r in rows if r["ask_nodes"] == n and r["arm"] == arm and r["spread"] is not None]
        z  = sum(1 for r in g if r["spread"] == 0)
        z10= sum(1 for r in g if r["spread"] <= 10)
        b0 = sum(1 for r in g if r["best"] == 0)
        w0 = sum(1 for r in g if r["worst"] == 0)
        print("%8d |  %-5s %6d/%d %9d %10d %10d" % (n, arm, z, len(g), z10, b0, w0))

print("\n=== C. does the decay track HOW CLOSE the clock is to 200? (high arm, 800k) ===")
print("%14s %5s  %8s %8s %8s %8s" % ("real_clock", "n", "static", "best20k", "best800k", "spread800k"))
for lo, hi in ((119, 139), (140, 159), (160, 179), (180, 199)):
    sel = [(g, p) for g, p in POS
           if idx.get((g, p, "high", NODES[-1])) and lo <= idx[(g, p, "high", NODES[-1])]["real_clock"] <= hi]
    if not sel: continue
    print("%14s %5d  %8.0f %8.0f %8.0f %8.0f"
          % ("%d-%d" % (lo, hi), len(sel),
             med([idx[(g,p,"high",NODES[-1])]["static"] for g,p in sel]),
             med([idx[(g,p,"high",NODES[0])]["best"] for g,p in sel]),
             med([idx[(g,p,"high",NODES[-1])]["best"] for g,p in sel]),
             med([idx[(g,p,"high",NODES[-1])]["spread"] for g,p in sel])))
print("  (low arm control, same positions, 800k):")
for lo, hi in ((119, 139), (140, 159), (160, 179), (180, 199)):
    sel = [(g, p) for g, p in POS
           if idx.get((g, p, "high", NODES[-1])) and lo <= idx[(g, p, "high", NODES[-1])]["real_clock"] <= hi]
    if not sel: continue
    print("%14s %5d  %8.0f %8.0f %8.0f %8.0f"
          % ("%d-%d" % (lo, hi), len(sel),
             med([idx[(g,p,"low",NODES[-1])]["static"] for g,p in sel]),
             med([idx[(g,p,"low",NODES[0])]["best"] for g,p in sel]),
             med([idx[(g,p,"low",NODES[-1])]["best"] for g,p in sel]),
             med([idx[(g,p,"low",NODES[-1])]["spread"] for g,p in sel])))

print("\n=== D. does the BEST MOVE change between arms / with depth? ===")
agree_arm, agree_dep_h, agree_dep_l = [], [], []
for g, p in POS:
    h8, l8 = idx.get((g,p,"high",NODES[-1])), idx.get((g,p,"low",NODES[-1]))
    h2, l2 = idx.get((g,p,"high",NODES[0])),  idx.get((g,p,"low",NODES[0]))
    if h8 and l8: agree_arm.append(h8["bestmove"] == l8["bestmove"])
    if h8 and h2: agree_dep_h.append(h8["bestmove"] == h2["bestmove"])
    if l8 and l2: agree_dep_l.append(l8["bestmove"] == l2["bestmove"])
for name, a in (("high vs low  @800k", agree_arm), ("20k vs 800k  high", agree_dep_h), ("20k vs 800k  low ", agree_dep_l)):
    print("  %s : same move in %d of %d (%.0f%%)" % (name, sum(a), len(a), 100.0*sum(a)/max(1,len(a))))

print("\n=== E. the clock is a NET INPUT: static high vs low, paired ===")
d = [idx[(g,p,"high",NODES[-1])]["static"] - idx[(g,p,"low",NODES[-1])]["static"]
     for g,p in POS if idx.get((g,p,"high",NODES[-1])) and idx.get((g,p,"low",NODES[-1]))
     and idx[(g,p,"high",NODES[-1])]["static"] is not None and idx[(g,p,"low",NODES[-1])]["static"] is not None]
print("  paired static diff: median %+.0f  p10 %+.0f  p90 %+.0f  negative in %d of %d"
      % (med(d), sorted(d)[len(d)//10], sorted(d)[9*len(d)//10], sum(1 for x in d if x < 0), len(d)))
