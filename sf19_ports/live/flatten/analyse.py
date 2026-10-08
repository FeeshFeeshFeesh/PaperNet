"""Read sweep.jsonl and answer the flattening question."""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "sweep.jsonl"))]
NODES = sorted({r["ask_nodes"] for r in rows})

def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else float("nan")

print("rows %d   positions %d   games %d"
      % (len(rows), len({(r["game"], r["ply"]) for r in rows}), len({r["game"] for r in rows})))

print("\n=== 1. the ladder, both arms ===")
print("%8s | %s" % ("nodes", "  arm    n  med depth  med best  med spread  med worst"))
for n in NODES:
    for arm in ("high", "low"):
        g = [r for r in rows if r["ask_nodes"] == n and r["arm"] == arm]
        print("%8d |  %-5s %4d %9.0f %9.0f %11.0f %10.0f"
              % (n, arm, len(g), med([x["depth"] for x in g]), med([x["best"] for x in g]),
                 med([x["spread"] for x in g]), med([x["worst"] for x in g])))

print("\n=== 2. PAIRED, same position & node count: high minus low ===")
pair = {}
for r in rows:
    pair.setdefault((r["game"], r["ply"], r["ask_nodes"]), {})[r["arm"]] = r
print("%8s  %5s  %12s  %14s  %14s" % ("nodes", "n", "d best", "d spread", "d depth"))
for n in NODES:
    ps = [v for k, v in pair.items() if k[2] == n and "high" in v and "low" in v]
    db = [v["high"]["best"] - v["low"]["best"] for v in ps if v["high"]["best"] is not None and v["low"]["best"] is not None]
    ds = [v["high"]["spread"] - v["low"]["spread"] for v in ps if v["high"]["spread"] is not None and v["low"]["spread"] is not None]
    dd = [v["high"]["depth"] - v["low"]["depth"] for v in ps]
    print("%8d  %5d  %12.0f  %14.0f  %14.0f" % (n, len(ps), med(db), med(ds), med(dd)))

print("\n=== 3. does the SPREAD collapse as the search grows? (per position, 20k -> 800k) ===")
for arm in ("high", "low"):
    lo, hi = [], []
    for (g, p) in {(r["game"], r["ply"]) for r in rows}:
        a = [r for r in rows if r["game"] == g and r["ply"] == p and r["arm"] == arm and r["ask_nodes"] == NODES[0]]
        b = [r for r in rows if r["game"] == g and r["ply"] == p and r["arm"] == arm and r["ask_nodes"] == NODES[-1]]
        if a and b and a[0]["spread"] is not None and b[0]["spread"] is not None:
            lo.append(a[0]["spread"]); hi.append(b[0]["spread"])
    shrank = sum(1 for x, y in zip(lo, hi) if y < x)
    print("  %-5s n=%d  median spread %.0f -> %.0f   shrank in %d of %d (%.0f%%)"
          % (arm, len(lo), med(lo), med(hi), shrank, len(lo), 100.0 * shrank / max(1, len(lo))))

print("\n=== 4. is the level difference already in the STATIC eval? ===")
sh, sl, bh, bl = [], [], [], []
for (g, p) in {(r["game"], r["ply"]) for r in rows}:
    h = [r for r in rows if r["game"] == g and r["ply"] == p and r["arm"] == "high"]
    l = [r for r in rows if r["game"] == g and r["ply"] == p and r["arm"] == "low"]
    if h and l:
        sh.append(h[0]["static"]); sl.append(l[0]["static"])
        bh.append([r["best"] for r in h if r["ask_nodes"] == NODES[-1]][0])
        bl.append([r["best"] for r in l if r["ask_nodes"] == NODES[-1]][0])
print("  static  (net units): high %6.0f   low %6.0f   diff %6.0f" % (med(sh), med(sl), med(sh) - med(sl)))
print("  search @%dk (cp)  : high %6.0f   low %6.0f   diff %6.0f" % (NODES[-1] // 1000, med(bh), med(bl), med(bh) - med(bl)))

print("\n=== 5. how deep does the ladder actually get, and is the draw inside it? ===")
for n in NODES:
    g = [r for r in rows if r["ask_nodes"] == n and r["arm"] == "high"]
    d = [x["depth"] for x in g]
    reach = [x["real_clock"] + x["depth"] for x in g]
    print("  %7d nodes: median depth %2.0f (p90 %2.0f)  clock+depth median %3.0f  >=200 in %d of %d"
          % (n, med(d), sorted(d)[int(0.9 * len(d))], med(reach),
             sum(1 for x in reach if x >= 200), len(g)))
