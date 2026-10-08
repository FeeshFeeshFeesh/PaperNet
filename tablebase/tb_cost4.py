# Tablebase cost model v4 -- all THREE symmetries.  No sys.argv, per CLAUDE.md.
#
# The <=5 piece set has three independent folds, each verified rather than assumed:
#
#  1. COLOUR   centre reflection s -> 80 - s, which maps i9 <-> a1 and so must swap the
#              colours and the side to move.  ~2x.  (in the model since v2)
#  2. DIAGONAL a1-i9 reflection (file,rank) -> (rank,file).  Both goals lie ON that
#              diagonal, so it fixes them and keeps colours AND side to move.  ~2x.
#              Verified by check_diagonal.py: 0/6000 move-set mismatches.  (new in v3)
#  3. RELABEL  the cyclic relabelling R -> P -> S -> R.  The prey relation is a 3-CYCLE,
#              so advancing every piece one step round it commutes with prey and predator
#              and leaves the legal move set literally EQUAL (squares are untouched).
#              ~2.98x on top of colour.  Verified by check_relabel.py (0/6000) AND by
#              md5: the solved tables for S/SS, R/RR and P/PP are byte-identical, and the
#              mixed orbit S/PS, R/SR, P/RP has an identical value histogram.  (new in v4)
#
# 1 and 2 shrink a table.  3 does NOT -- it maps a signature to a DIFFERENT signature, so
# it removes whole tables from the set, cutting storage and build CPU by the same factor
# while leaving PEAK BUILD RAM per signature unchanged.  It costs a signature relabelling
# at probe time (apply sigma^k and/or the colour swap to reach the stored representative),
# which is a lookup on the material key, not per-position work.
#
# The relabelling fold is less than a clean 3x because the starting material is NOT
# invariant under the cycle: 3 rocks, 4 papers, 3 scissors per side (position.h:19-23), so
# an orbit through a 4-paper signature has members that can never arise.
from itertools import combinations_with_replacement
from math import factorial

SQUARES, DIAG = 81, 9
LIMITS = (3, 4, 3)                      # (R, P, S) per side
CORE_AUD_H = 0.052937184
PD_STD_AUD = 0.0611798
GIB = 1024.0 ** 3
RATE_LO, RATE_HI = 1e5, 1e6
DTZ_BYTES, BUILD_BYTES = 1.0, 2.0

def perm(n, k):
    p = 1
    for i in range(k):
        p *= (n - i)
    return p

def legal_sides(k):
    out = []
    for c in combinations_with_replacement((0, 1, 2), k):
        cnt = tuple(c.count(i) for i in (0, 1, 2))
        if all(cnt[i] <= LIMITS[i] for i in (0, 1, 2)):
            out.append(cnt)
    return out

def sigma(c):
    return (c[2], c[0], c[1])           # a rock becomes a paper: new_P = old_R, etc.

def entries(sig, total, diag=True):
    div = 1
    for side in sig:
        for n in side:
            div *= factorial(n)
    tot = perm(SQUARES, total) // div * 2
    if not diag:
        return tot
    fixed = perm(DIAG, total) // div * 2
    return (tot - fixed) // 2 + fixed   # upper bound: ignores mirrored identical pieces

def fold(total):
    sigs = set()
    for a in range(1, total):
        for sa in legal_sides(a):
            for sb in legal_sides(total - a):
                sigs.add((sa, sb))
    seen, reps = set(), []
    for s in sorted(sigs):
        if s in seen:
            continue
        orb, cur = set(), s
        for _ in range(3):
            orb.add(cur)
            orb.add((cur[1], cur[0]))
            cur = (sigma(cur[0]), sigma(cur[1]))
        legal = orb & sigs
        seen |= legal
        reps.append(sorted(legal)[0])
    return sigs, reps

print("-----  the fold, piece count by piece count  -----")
print("  pcs   all sigs  stored  unfolded entries  all folds      total fold")
tot5 = 0
biggest = {}
for n in (3, 4, 5, 6):
    sigs, reps = fold(n)
    raw = sum(entries(s, n, diag=False) for s in sigs)
    fin = sum(entries(s, n, diag=True) for s in reps)
    biggest[n] = max(entries(s, n, diag=True) for s in reps)
    if n <= 5:
        tot5 += fin
    print("  %3d   %8d  %6d  %.4e       %.4e     %6.2fx"
          % (n, len(sigs), len(reps), raw, fin, raw / fin))

print("-----  the <=5 piece SET, the thing we actually build  -----")
raw = tot5 * DTZ_BYTES
print("  entries                  %.4e" % tot5)
print("  raw, 1 B/entry           %6.1f GiB   disk A$%5.2f/mo" % (raw / GIB, raw / GIB * PD_STD_AUD))
print("  -> SHIP IT UNCOMPRESSED. 29 GiB is a rounding error on a boot disk, so the codec,")
print("     its decompression latency and its bug surface all leave the critical path.")
print("-----  build CPU  -----")
for r, tag in ((RATE_LO, "slow 1e5/s"), (RATE_HI, "fast 1e6/s")):
    h = tot5 / r / 3600.0
    print("  %-11s %7.1f core-hours   A$%5.2f" % (tag, h, h * CORE_AUD_H))
print("-----  PEAK BUILD RAM -- unchanged by the relabelling fold, still the only wall  -----")
for n in (4, 5, 6):
    pk = biggest[n] * BUILD_BYTES / GIB
    verdict = ("fits c4a-standard-4 (16 GiB), two signatures at once" if pk < 7 else
               "fits c4a-standard-4 (16 GiB)" if pk < 13 else
               "needs c4a-standard-8 (32 GiB)" if pk < 28 else
               "needs c4a-standard-16 (64 GiB)" if pk < 58 else
               "EXCEEDS EVERY C4A -> out-of-core")
    print("  %d pcs: largest stored sig %.3e entries -> peak %6.1f GiB  %s" % (n, biggest[n], pk, verdict))
print("  (thread WITHIN a signature over one shared table; never two big ones concurrently)")
