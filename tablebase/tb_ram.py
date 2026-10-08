# Peak-build-RAM audit for the C builder (tb_solve.c).  No argv, per CLAUDE.md.
#
# WHY THIS EXISTS.  tb_cost4.py reports peak RAM from the DIAGONALLY FOLDED entry count, i.e. it
# assumes the builder canonicalises every index on the a1-i9 reflection.  tb_solve.c does not: it
# allocates a DENSE base-81 array, 2 * 81^n cells.  Those are very different numbers, and the gap
# decides which cloud machine can build the 5-piece set.  This prints all three side by side.
from math import factorial
from itertools import combinations_with_replacement

SQUARES, DIAG = 81, 9
LIMITS = {"R": 3, "P": 4, "S": 3}
BUILD_BYTES = 2.0
GIB = 1024.0 ** 3

def perm(n, k):
    p = 1
    for i in range(k):
        p *= (n - i)
    return p

def legal_sides(k):
    out = []
    for c in combinations_with_replacement("RPS", k):
        cnt = tuple(c.count(t) for t in "RPS")
        if all(cnt[i] <= LIMITS[t] for i, t in enumerate("RPS")):
            out.append(cnt)
    return out

def sigma(c):
    return (c[2], c[0], c[1])

def div_of(sig):
    d = 1
    for side in sig:
        for n in side:
            d *= factorial(n)
    return d

def live(sig, total):
    return perm(SQUARES, total) // div_of(sig) * 2

def live_diag(sig, total):
    d = div_of(sig)
    tot = perm(SQUARES, total) // d * 2
    fixed = perm(DIAG, total) // d * 2
    return (tot - fixed) // 2 + fixed

def dense(total):
    return 2 * SQUARES ** total

def reps(total):
    sigs = set()
    for a in range(1, total):
        for sa in legal_sides(a):
            for sb in legal_sides(total - a):
                sigs.add((sa, sb))
    seen, out = set(), []
    for s in sorted(sigs):
        if s in seen:
            continue
        orb, cur = set(), s
        for _ in range(3):
            orb.add(cur)
            orb.add((cur[1], cur[0]))
            cur = (sigma(cur[0]), sigma(cur[1]))
        ok = orb & sigs
        seen |= ok
        out.append(sorted(ok)[0])
    return out

print("-----  peak RAM for the LARGEST stored signature, three indexing schemes  -----")
print("  pcs   dense 81^n      live (ranked)   live+diagonal    dense GiB  ranked GiB  +diag GiB")
for n in (3, 4, 5):
    rs = reps(n)
    bl = max(live(s, n) for s in rs)
    bd = max(live_diag(s, n) for s in rs)
    dn = dense(n)
    print("  %3d   %.4e     %.4e      %.4e     %8.2f    %8.2f   %8.2f"
          % (n, dn, bl, bd, dn * BUILD_BYTES / GIB, bl * BUILD_BYTES / GIB, bd * BUILD_BYTES / GIB))

print("-----  where the dense scheme wastes: twin-bearing signatures  -----")
print("  the builder allocates 2*81^n for EVERY signature, but only `live` slots are real")
for n in (4, 5):
    rs = reps(n)
    worst = max(rs, key=lambda s: dense(n) / live(s, n))
    print("  %d pcs worst ratio %s  dense/live = %.2fx  (div=%d)"
          % (n, worst, dense(n) / live(worst, n), div_of(worst)))
    tot_live = sum(live(s, n) for s in rs)
    print("       summed over the %d stored signatures: dense %.4e vs live %.4e = %.2fx waste"
          % (len(rs), len(rs) * dense(n), tot_live, len(rs) * dense(n) / tot_live))

print("-----  the 5-piece verdict on a c4a-standard-4 (16 GiB)  -----")
rs5 = reps(5)
d5 = dense(5) * BUILD_BYTES / GIB
l5 = max(live(s, 5) for s in rs5) * BUILD_BYTES / GIB
g5 = max(live_diag(s, 5) for s in rs5) * BUILD_BYTES / GIB
ret4 = len(reps(4)) * dense(4) * 0.25 / GIB          # retained 4-piece WDL at 2 bits/entry
ret4_all = 96 * dense(4) * 0.25 / GIB                # without relabel folding at probe time
print("  retained 4-piece WDL at 2 bits/entry: %.2f GiB folded (%d sigs) / %.2f GiB unfolded (96)"
      % (ret4, len(reps(4)), ret4_all))
for tag, pk in (("dense base-81 (what tb_solve.c does today)", d5),
                ("ranked index", l5),
                ("ranked + diagonal canonicalisation", g5)):
    print("  %-42s %6.2f + %.2f = %6.2f GiB   %s"
          % (tag, pk, ret4, pk + ret4, "FITS" if pk + ret4 < 15.0 else "DOES NOT FIT"))
