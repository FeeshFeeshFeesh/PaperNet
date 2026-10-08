"""THE decisive test: in the transition band, does MORE SEARCH blind the engine?

`nocapture-draw-flat` claims deeper search reaches the 200-ply draw sooner and so
goes flat at a LOWER clock -- the proposed mechanism for conversion-measured's
10%->45% throw rate. The main sweep could not test it (only 2 of 132 positions in
clock 170-194). This run has 254 there.

Prediction if the claim is true: at a FIXED clock, the fraction of positions with
zero root-move spread RISES with the node count, and the crossing depth>=plies_left
is what flips each one.
"""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "sweep_band.jsonl"))]
N = sorted({r["ask_nodes"] for r in rows})
idx = {(r["game"], r["ply"], r["arm"], r["ask_nodes"]): r for r in rows}
POS = sorted({(r["game"], r["ply"]) for r in rows})
def med(xs):
    xs = [x for x in xs if x is not None]; return st.median(xs) if xs else float("nan")

print("band sweep: %d rows, %d positions, %d games, clock %d-%d"
      % (len(rows), len(POS), len({r["game"] for r in rows}),
         min(r["real_clock"] for r in rows), max(r["real_clock"] for r in rows)))

print("\n=== THE TEST: does the flat fraction RISE with search size? (high arm) ===")
print("%9s | %8s %8s | %9s %9s | %8s %8s %8s"
      % ("nodes", "med depth", "d>=left", "spread==0", "spread<=10", "med best", "med spread", "|best|<=5"))
for n in N:
    g = [idx[(gg,pp,"high",n)] for gg,pp in POS if idx.get((gg,pp,"high",n))]
    cross = sum(1 for r in g if r["depth"] >= 200 - r["real_clock"])
    z  = sum(1 for r in g if r["spread"] == 0)
    z10= sum(1 for r in g if r["spread"] is not None and r["spread"] <= 10)
    t  = sum(1 for r in g if r["best"] is not None and abs(r["best"]) <= 5)
    print("%9d | %8.0f %4d/%-3d | %4d/%-4d %4d/%-4d | %8.0f %8.0f %5d/%-4d"
          % (n, med([r["depth"] for r in g]), cross, len(g), z, len(g), z10, len(g),
             med([r["best"] for r in g]), med([r["spread"] for r in g]), t, len(g)))

print("\n=== PAIRED: positions that go flat only at the TOP of the ladder ===")
a = b = both = neither = 0
flipped = []
for gg, pp in POS:
    lo, hi = idx.get((gg,pp,"high",N[0])), idx.get((gg,pp,"high",N[-1]))
    if not (lo and hi and lo["spread"] is not None and hi["spread"] is not None): continue
    fl, fh = lo["spread"] == 0, hi["spread"] == 0
    if fl and fh: both += 1
    elif fh and not fl: a += 1; flipped.append((gg,pp,lo,hi))
    elif fl and not fh: b += 1
    else: neither += 1
print("  flat at 20k AND 800k      : %3d   (already blind at the smallest search)" % both)
print("  flat ONLY at 800k         : %3d   <-- deeper search CAUSED the blindness" % a)
print("  flat ONLY at 20k          : %3d   (deeper search RESTORED a gradient)" % b)
print("  never flat                : %3d" % neither)
if flipped:
    print("  the %d that went blind with depth (clock, depth 20k -> 800k, best 20k -> 800k):" % len(flipped))
    for gg,pp,lo,hi in flipped[:12]:
        print("     g%-3d ply %-4d clock %3d   depth %2d -> %3d   best %+5s -> %+5s"
              % (gg, pp, lo["real_clock"], lo["depth"], hi["depth"], lo["best"], hi["best"]))

print("\n=== by clock band: where in 164-193 does the cliff sit, and does depth move it? ===")
bands = [(164,169),(170,174),(175,179),(180,184),(185,189),(190,193)]
print("%9s %4s | %s" % ("clock", "n", "  flat fraction at each node level (20k .. 800k)"))
for lo, hi in bands:
    sel = [(gg,pp) for gg,pp in POS if idx.get((gg,pp,"high",N[-1]))
           and lo <= idx[(gg,pp,"high",N[-1])]["real_clock"] <= hi]
    if not sel: continue
    cells = []
    for n in N:
        g = [idx[(gg,pp,"high",n)] for gg,pp in sel if idx.get((gg,pp,"high",n))]
        z = sum(1 for r in g if r["spread"] == 0)
        cells.append("%2d/%-2d" % (z, len(g)))
    print("%9s %4d |  %s" % ("%d-%d"%(lo,hi), len(sel), "  ".join(cells)))

print("\n=== control: same positions, clock field zeroed (the cliff must vanish) ===")
for n in (N[0], N[-1]):
    g = [idx[(gg,pp,"low",n)] for gg,pp in POS if idx.get((gg,pp,"low",n))]
    z = sum(1 for r in g if r["spread"] == 0)
    print("  %7d nodes, low arm: flat %2d/%-3d   med best %+5.0f   med spread %4.0f"
          % (n, z, len(g), med([r["best"] for r in g]), med([r["spread"] for r in g])))

print("\n=== how much is at stake in this band? (clock zeroed, 800k) ===")
L = [idx[(gg,pp,"low",N[-1])] for gg,pp in POS if idx.get((gg,pp,"low",N[-1]))]
H = [idx[(gg,pp,"high",N[-1])] for gg,pp in POS if idx.get((gg,pp,"high",N[-1]))]
print("  clock zeroed: median best %+5.0f   >=+200 in %d/%d   >=+1000 in %d/%d"
      % (med([r["best"] for r in L]), sum(1 for r in L if r["best"] and r["best"]>=200), len(L),
         sum(1 for r in L if r["best"] and r["best"]>=1000), len(L)))
print("  real clock  : median best %+5.0f   >=+200 in %d/%d   >=+1000 in %d/%d"
      % (med([r["best"] for r in H]), sum(1 for r in H if r["best"] and r["best"]>=200), len(H),
         sum(1 for r in H if r["best"] and r["best"]>=1000), len(H)))
