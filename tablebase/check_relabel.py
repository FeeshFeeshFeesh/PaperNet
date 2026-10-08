"""
check_relabel.py -- is the cyclic relabelling R -> P -> S -> R a symmetry of this game?

Spotted in the tb_solve.py 3-piece output: the three signatures S-vs-SS, P-vs-PP and R-vs-RR
came out with byte-identical statistics (W 266832 / L 226286 / maxDTZ 32 / lzma 16256 B), and
so did the three-signature orbit {S vs P+S, R vs S+R, P vs R+P}.  That is not a coincidence.

The prey relation is a 3-CYCLE -- PREY = {R:S, P:R, S:P} -- so the relabelling that advances
every piece one step round the cycle commutes with it.  Unlike the colour and diagonal folds,
this one maps a signature to a DIFFERENT signature, so it does not shrink a table: it shrinks
the SET OF TABLES, and therefore cuts storage and build CPU by the same factor.

The catch is the starting material: 3 rocks, 4 papers, 3 scissors per side (position.h:19-23).
The limits are NOT invariant under the cycle, so an orbit containing a 4-paper signature has
members we never need.  The saving below is computed on legal signatures only.
"""
import random
from itertools import combinations_with_replacement
from math import factorial

import rps_rules as R

N_POS = 6000
SEED  = 20260927
LIMITS = (3, 4, 3)           # (R, P, S) per side
SQUARES = 81

# sigma: a rock becomes a paper, a paper a scissors, a scissors a rock.
SIGMA = {R.ROCK: R.PAPER, R.PAPER: R.SCISSORS, R.SCISSORS: R.ROCK}

# --- 1. the relation really is preserved ------------------------------------------
for pt in (R.ROCK, R.PAPER, R.SCISSORS):
    assert SIGMA[R.PREY[pt]] == R.PREY[SIGMA[pt]], "sigma does not commute with PREY"
    assert SIGMA[R.PREDATOR[pt]] == R.PREDATOR[SIGMA[pt]], "sigma breaks PREDATOR"
print("prey/predator: sigma commutes with both (the prey relation is a 3-cycle)")

# --- 2. and so are the legal moves, on real positions ------------------------------
rng = random.Random(SEED)
bad = tested = 0
while tested < N_POS:
    npc = rng.randint(2, 5)
    sqs = rng.sample(range(R.NSQ), npc)
    pcs = [(rng.randint(0, 1), rng.choice([R.ROCK, R.PAPER, R.SCISSORS])) for _ in range(npc)]
    if not any(c == R.WHITE for c, _ in pcs) or not any(c == R.BLACK for c, _ in pcs):
        continue
    p = R.Pos(); p.bd = [None] * R.NSQ
    q = R.Pos(); q.bd = [None] * R.NSQ
    for k in range(npc):
        p.bd[sqs[k]] = pcs[k]
        q.bd[sqs[k]] = (pcs[k][0], SIGMA[pcs[k][1]])
    p.stm = q.stm = rng.randint(0, 1)
    p.clock = q.clock = rng.randint(0, 199)
    if R.reached_goal(p, R.WHITE) or R.reached_goal(p, R.BLACK):
        continue
    tested += 1
    if set(R.gen_legal(p)) != set(R.gen_legal(q)):
        bad += 1
        if bad <= 3:
            print("  MISMATCH", R.to_fen(p), R.to_fen(q))
print("rules-level: %s -- %d mismatches over %d positions (2-5 pieces, random clocks); the "
      "move sets are EQUAL, squares are untouched" % ("SYMMETRY HOLDS" if bad == 0 else "NOT A SYMMETRY", bad, tested))

# --- 3. how much does it save on the <=5 piece set? --------------------------------
def legal_sides(k):
    out = []
    for c in combinations_with_replacement((0, 1, 2), k):
        cnt = tuple(c.count(i) for i in (0, 1, 2))
        if all(cnt[i] <= LIMITS[i] for i in (0, 1, 2)):
            out.append(cnt)
    return out

def sig_sigma(c):
    """counts are (R,P,S); a rock becomes a paper so new_P = old_R, new_S = old_P, new_R = old_S"""
    return (c[2], c[0], c[1])

def perm(n, k):
    p = 1
    for i in range(k):
        p *= (n - i)
    return p

def entries(sig, total):
    div = 1
    for side in sig:
        for n in side:
            div *= factorial(n)
    tot = perm(SQUARES, total) // div * 2
    fixed = perm(9, total) // div * 2               # diagonal fold, as in tb_cost3.py
    return (tot - fixed) // 2 + fixed

print("-----")
print("  pcs   legal sigs   orbits   sigs/orbit   entries legal    entries folded   fold")
gtot_all = gtot_fold = 0
for total in range(2, 6):
    sigs = set()
    for a in range(1, total):
        for sa in legal_sides(a):
            for sb in legal_sides(total - a):
                sigs.add((sa, sb))
    seen, orbits = set(), []
    for s in sorted(sigs):
        if s in seen:
            continue
        orb = set()
        cur = s
        for _ in range(3):                                  # sigma has order 3
            orb.add(cur)
            orb.add((cur[1], cur[0]))                       # and the colour swap
            cur = (sig_sigma(cur[0]), sig_sigma(cur[1]))
        orb = {x for x in orb}
        legal_in_orbit = orb & sigs
        seen |= legal_in_orbit
        orbits.append(sorted(legal_in_orbit))
    e_all = sum(entries(s, total) for s in sigs)
    e_fold = sum(entries(o[0], total) for o in orbits)
    gtot_all += e_all
    gtot_fold += e_fold
    print("  %3d   %8d   %6d   %10.3f   %.4e     %.4e   %.3fx"
          % (total, len(sigs), len(orbits), len(sigs) / len(orbits), e_all, e_fold, e_all / e_fold))
print("-----")
print("  <=5 pieces: %.4e -> %.4e entries, a further %.3fx" % (gtot_all, gtot_fold, gtot_all / gtot_fold))
GIB = 1024.0 ** 3
for tag, e in (("before", gtot_all), ("after ", gtot_fold)):
    print("  %s: %6.1f GiB raw at 1 B/entry, disk A$%5.2f/mo, build %6.1f core-h at 1e5/s (A$%5.2f)"
          % (tag, e / GIB, e / GIB * 0.0611798, e / 1e5 / 3600.0, e / 1e5 / 3600.0 * 0.052937184))
