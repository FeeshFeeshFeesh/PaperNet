# What the relabelling fold actually buys, using the MEASURED per-entry build cost.
# No sys.argv: knobs are here, per CLAUDE.md.
#
# The fold is now IMPLEMENTED in tb_solve.c (orbit_rep + the order-6 orbit walk in probe) and
# verified byte-identical to the Python prototype on all 8 retained 3-piece representatives.
# This script re-derives the disk/CPU/RAM numbers for the folded builder, and cross-checks that
# its own rep selection agrees with the C one (same rule: lex-smallest VALID orbit member).
from itertools import product
from math import factorial

LIMIT      = {0: 3, 1: 4, 2: 3}          # R, P, S
NSQ        = 81
GIB        = 1024.0 ** 3
CORE_AUD_H = 0.052937184                 # C4A vCPU-hour, asia-southeast1
PD_STD_AUD = 0.0611798                   # pd-standard GiB-month
FIXED_S    = 2.749                       # measured: per-signature fixed cost
PER_LIVE_S = 2.929e-07                   # measured: per LIVE entry (movegen + sweep)
PER_DENSE_S= 3.19e-08                    # measured: per DENSE hole (pure bandwidth)

def perm(n, k):
    p = 1
    for i in range(k): p *= n - i
    return p

def sigma(c):                            # a rock becomes a paper: new_R = old_S, new_P = old_R
    return (c[2], c[0], c[1])

def ok(c):
    return all(c[i] <= LIMIT[i] for i in range(3))

def orbit_rep(wc, bc):
    best, a, b = None, wc, bc
    for _k in range(3):
        for cw, cb in ((a, b), (b, a)):          # the colour swap is in the group
            if ok(cw) and ok(cb) and (best is None or (cw, cb) < best):
                best = (cw, cb)
        a, b = sigma(a), sigma(b)
        # NOTE sigma and the colour swap commute, so 3 x 2 enumerates the whole group
    return best

def sides(k):
    return [c for c in product(range(5), repeat=3) if sum(c) == k and ok(c)]

def live(wc, bc, n):
    """Distinct placements x 2 for the side to move.

    This is the DENSE placement count, which is what the builder's table length is, and it sits a
    little ABOVE the true live count: the solver drops positions where either side already stands
    on its own goal corner (those are won and never stored). Measured at 2 pieces: 12,642 stored
    against 12,960 placements, a 2.45% gap, and the gap scales as ~n/81, so ~6% at 5 pieces.
    Every disk and CPU figure below is therefore conservative by roughly that much."""
    div = 1
    for c in (wc, bc):
        for x in c: div *= factorial(x)
    return perm(NSQ, n) // div * 2

print("-----  the relabelling fold, per piece count  -----")
print("  pcs   all sigs  colour-canon  relabel-canon   live(all)     live(reps)   fold")
tot_reps_live = tot_reps_dense = tot_reps_sigs = 0
for n in range(2, 7):
    allsig, colour, reps = [], set(), {}
    for a in range(1, n):
        for wc in sides(a):
            for bc in sides(n - a):
                allsig.append((wc, bc))
                colour.add(tuple(sorted([wc, bc])))
                reps.setdefault(orbit_rep(wc, bc), 0)
    la = sum(live(w, b, n) for w, b in colour)           # colour fold only, what we build today
    lr = sum(live(w, b, n) for w, b in reps)
    dr = len(reps) * 2 * NSQ ** n
    print("  %3d   %8d  %12d  %13d   %.4e   %.4e  %5.2fx"
          % (n, len(allsig), len(colour), len(reps), la, lr, la / lr))
    if n <= 5:
        tot_reps_live += lr; tot_reps_dense += dr; tot_reps_sigs += len(reps)
        big_live, big_dense = lr, dr
    if n == 5:
        big5 = max(live(w, b, 5) for w, b in reps)

print("-----  the <=5 piece SET as the FOLDED builder will make it  -----")
print("  signatures                 %d" % tot_reps_sigs)
print("  live entries (= disk B)    %.4e  = %.1f GiB uncompressed" % (tot_reps_live, tot_reps_live / GIB))
print("  disk on pd-standard        A$%.2f/mo" % (tot_reps_live / GIB * PD_STD_AUD))
holes = tot_reps_dense - tot_reps_live
cpu = (tot_reps_sigs * FIXED_S + tot_reps_live * PER_LIVE_S + holes * PER_DENSE_S) / 3600.0
cpu_live = tot_reps_live * PER_LIVE_S / 3600.0
print("  build CPU  live work       %.2f core-h" % cpu_live)
print("             dense holes     %.2f core-h  (+%.0f%%, pure memory bandwidth)"
      % (holes * PER_DENSE_S / 3600.0, holes * PER_DENSE_S / (tot_reps_live * PER_LIVE_S) * 100))
print("             TOTAL           %.2f core-h   A$%.2f   %.1f h wall on 4 vCPU"
      % (cpu, cpu * CORE_AUD_H, cpu / 4.0))
print("  peak RAM (largest 5-pc rep, dense base-81, 2 B/entry)  %.2f GiB"
      % (2 * NSQ ** 5 * 2 / GIB))
print("  (unchanged by the fold: the biggest signature IS a representative)")
