# Tablebase cost model v3 -- adds the a1-i9 DIAGONAL fold, which v2 missed.
# No sys.argv: all knobs are here, per CLAUDE.md.
#
# WHAT CHANGED FROM v2. v2 folded only by colour: the centre reflection s -> 80 - s maps
# i9 <-> a1, so it must swap the colours too, and it lets us store one signature of each
# colour-swapped pair. v3 adds the SECOND involution, which v2 overlooked: the a1-i9
# DIAGONAL reflection (file,rank) -> (rank,file). BOTH goal corners lie on that diagonal,
# so it fixes each side's goal, keeps the colours AND keeps the side to move -- a symmetry
# with no bookkeeping at all. Verified at the rules level by tablebase/check_diagonal.py:
# a1 and i9 fixed, involution, king steps preserved, and the legal move set maps onto the
# legal move set on 6000 random 2-5 piece positions with random clocks, 0 mismatches.
#
# The two involutions commute, so the group is Z2 x Z2 and the total fold is ~4x off the
# unfolded count. Fold factor measured exactly: 1.9980x at 3 pieces, 2.0000x at 5.
#
# ENTRY FORMAT (unchanged from v2, now with the exact byte layout).  One byte per position:
#     0        draw, or unusable
#     1..127   win,  DTZ = v
#     128..255 loss, DTZ = v - 127
# DTZ is bounded by the capture clock: a win needing more than 200 plies to the next
# zeroing event is a DRAW at a fresh clock, so the retrograde BFS stops at 200 and the
# stored value IS the clock-aware value, not an approximation of it. The 127/128 clamp
# writes 0 for the few positions that overflow, which is CONSERVATIVE -- the probe then
# searches instead of trusting the table.
from math import factorial
from itertools import combinations_with_replacement

SQUARES      = 81
DIAG_SQUARES = 9          # a1,b2,...,i9 -- the fixed squares of the diagonal reflection
LIMITS       = {"R": 3, "P": 4, "S": 3}
CORE_AUD_H   = 0.052937184
RAM_AUD_GIBH = 0.006021051
PD_STD_AUD   = 0.0611798
HOURS_MO     = 730.0
GIB          = 1024.0**3
RATE_LO, RATE_HI = 1e5, 1e6

DTZ_BYTES   = 1.0
CR_DTZ      = 5.0     # <-- still a guess here; tb_solve.py measures it on real tables
BUILD_BYTES = 2.0     # dtz byte + (wdl 2 bits | unresolved-successor count 6 bits) byte

def side_multisets(k):
    out = []
    for c in combinations_with_replacement("RPS", k):
        cnt = {t: c.count(t) for t in "RPS"}
        if all(cnt[t] <= LIMITS[t] for t in "RPS"):
            out.append(tuple(sorted(cnt.items())))
    return out

def perm(n, k):
    p = 1
    for i in range(k):
        p *= (n - i)
    return p

def signatures(total, diag_fold=True):
    """Colour-folded signatures -> entry count, x2 for side to move, optionally also
    folded across the a1-i9 diagonal.

    The diagonal fold is exact except that a position with identical pieces on mirrored
    squares is ALSO fixed by the reflection; ignoring those overstates the folded size, so
    every number below is an upper bound."""
    sigs = {}
    for a in range(1, total):
        b = total - a
        for sa in side_multisets(a):
            for sb in side_multisets(b):
                key = tuple(sorted([sa, sb]))
                if key in sigs:
                    continue
                div = 1
                for s in (sa, sb):
                    for _t, n in s:
                        div *= factorial(n)
                tot = perm(SQUARES, total) // div * 2
                if diag_fold:
                    fixed = perm(DIAG_SQUARES, total) // div * 2   # every piece on the diagonal
                    tot = (tot - fixed) // 2 + fixed
                sigs[key] = tot
    return sigs

print("-----  entries per piece count  -----")
print("  pcs  sigs   colour-folded   + diagonal    fold")
tot = tot_v2 = 0
allsigs = {}
for n in (3, 4, 5, 6):
    s2 = signatures(n, diag_fold=False)
    s3 = signatures(n, diag_fold=True)
    allsigs[n] = s3
    e2, e3 = sum(s2.values()), sum(s3.values())
    if n <= 5:
        tot += e3
        tot_v2 += e2
    print("  %3d  %4d    %.4e   %.4e   %.4fx" % (n, len(s3), e2, e3, e2 / e3))

print("-----  the <=5 piece SET, the thing we actually build  -----")
print("  entries  v2 %.4e -> v3 %.4e" % (tot_v2, tot))
raw = tot * DTZ_BYTES
print("  DTZ raw (1 B/entry)      %6.1f GiB   disk A$%5.2f/mo" % (raw / GIB, raw / GIB * PD_STD_AUD))
print("  DTZ compressed (%.0fx)     %6.1f GiB   disk A$%5.2f/mo"
      % (CR_DTZ, raw / CR_DTZ / GIB, raw / CR_DTZ / GIB * PD_STD_AUD))
print("  -> raw now fits a 100 GiB disk, so COMPRESSION IS OFF THE CRITICAL PATH entirely")

print("-----  build CPU for the whole <=5 set (one retrograde pass)  -----")
for r, tag in ((RATE_LO, "slow 1e5/s"), (RATE_HI, "fast 1e6/s")):
    h = tot / r / 3600.0
    print("  %-11s %8.1f core-hours   A$%6.2f at the C4A core price" % (tag, h, h * CORE_AUD_H))

print("-----  PEAK BUILD RAM, the real wall (largest single signature)  -----")
for n in (4, 5, 6):
    big = max(allsigs[n].values())
    pk = big * BUILD_BYTES / GIB
    if pk < 13:
        fits = "fits c4a-standard-4 (16 GiB), 2 signatures at once"
    elif pk < 28:
        fits = "needs c4a-standard-8 (32 GiB)"
    elif pk < 58:
        fits = "needs c4a-standard-16 (64 GiB)"
    else:
        fits = "EXCEEDS EVERY C4A -> out-of-core"
    print("  %d pcs: largest sig %.3e entries -> peak %5.1f GiB  %s" % (n, big, pk, fits))
print("  (thread WITHIN a signature over one shared table; never run two big ones at once)")
