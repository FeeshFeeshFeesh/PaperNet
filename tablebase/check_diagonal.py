"""
check_diagonal.py -- is the a1-i9 diagonal a symmetry of this game?

If it is, every table folds by a further ~2x on top of the colour fold, because the two
goal corners a1 and i9 both LIE ON that diagonal, so the reflection (file,rank) ->
(rank,file) maps each side's goal to itself and keeps the side to move.  (The colour fold
already in the cost model is the OTHER involution: the centre reflection s -> 80 - s,
which SWAPS the goals and so must swap the colours too.)

Verified here at the rules level: the legal move set must map onto the legal move set.
Value invariance then follows by induction, since the reflection commutes with do_move.
"""
import random
import rps_rules as R

N_POS = 6000
SEED = 20260927

def refl(s):
    return R.file_of(s) * R.FILES + R.rank_of(s)

rng = random.Random(SEED)
assert refl(R.GOAL[R.WHITE]) == R.GOAL[R.WHITE], "i9 is not fixed"
assert refl(R.GOAL[R.BLACK]) == R.GOAL[R.BLACK], "a1 is not fixed"
assert all(refl(refl(s)) == s for s in range(R.NSQ)), "not an involution"
assert all(sorted(refl(t) for t in R.STEPS[s]) == sorted(R.STEPS[refl(s)])
           for s in range(R.NSQ)), "king steps not preserved"
print("board-level: a1 and i9 both fixed, involution, king steps preserved")

bad = tested = 0
while tested < N_POS:
    npc = rng.randint(2, 5)
    sqs = rng.sample(range(R.NSQ), npc)
    pcs = [(rng.randint(0, 1), rng.choice([R.ROCK, R.PAPER, R.SCISSORS])) for _ in range(npc)]
    if not any(c == R.WHITE for c, _ in pcs) or not any(c == R.BLACK for c, _ in pcs):
        continue
    p = R.Pos(); p.bd = [None] * R.NSQ
    for k in range(npc):
        p.bd[sqs[k]] = pcs[k]
    p.stm = rng.randint(0, 1); p.clock = rng.randint(0, 199)
    if R.reached_goal(p, R.WHITE) or R.reached_goal(p, R.BLACK):
        continue
    q = R.Pos(); q.bd = [None] * R.NSQ
    for k in range(npc):
        q.bd[refl(sqs[k])] = pcs[k]
    q.stm = p.stm; q.clock = p.clock
    tested += 1
    mine = {(refl(f), refl(t)) for f, t in R.gen_legal(p)}
    theirs = set(R.gen_legal(q))
    if mine != theirs:
        bad += 1
        if bad <= 3:
            print("  MISMATCH", R.to_fen(p), sorted(mine ^ theirs))
print("rules-level: %s -- %d mismatches over %d positions (%d-5 pieces, random clocks)"
      % ("SYMMETRY HOLDS" if bad == 0 else "NOT A SYMMETRY", bad, tested, 2))

# How much does it actually save?  Only positions NOT fixed by the reflection fold 2:1.
# A position is fixed iff every piece sits on the diagonal (9 squares).
from math import comb, perm
print("-----")
for n in (3, 4, 5):
    tot = perm(R.NSQ, n)
    fixed = perm(9, n)                     # all n pieces on the 9 diagonal squares
    folded = (tot - fixed) // 2 + fixed
    print("  %d pieces: %d placements -> %d folded (%.4fx), diagonal-fixed %d (%.5f%%)"
          % (n, tot, folded, tot / folded, fixed, 100.0 * fixed / tot))
