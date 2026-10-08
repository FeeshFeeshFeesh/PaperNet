# Differential test: my transcribed movegen vs the engine's own perft, on RANDOM SPARSE positions --
# the tablebase's actual domain (<=5 pieces, near the goal corners), which START_FEN never exercises.
# No sys.argv: knobs at the top, runnable from PyCharm.
#
# ⚠ SCOPE LIMIT: perft.h counts MoveList<NON_EVASIONS>, not <LEGAL>. So this test validates
# pseudo-legal generation, the prey relation and terminal detection -- it CANNOT catch a bug in
# gen_evasions(). That needs the separate MultiPV root-move check.
import random, subprocess, collections
from rps_rules import (Pos, NSQ, WHITE, BLACK, ROCK, PAPER, SCISSORS, GOAL, to_fen, perft,
                       reached_goal, gen_non_evasions, checkers, gen_legal)
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ENGINE   = _ROOT + "/sf19_ports/live/stockfish"
N_POS    = 120
DEPTH    = 3
SEED     = 20260927
MAX_PER_SIDE = 3          # so totals land in the 2..6 range, covering the <=5 band and one over
NEAR_GOAL_P  = 0.55       # fraction of pieces placed in a goal neighbourhood, to force threats

random.seed(SEED)
LIMITS = {ROCK: 3, PAPER: 4, SCISSORS: 3}

def goal_zone(c):
    g = GOAL[c]
    return [s for s in range(NSQ) if abs(s % 9 - g % 9) <= 2 and abs(s // 9 - g // 9) <= 2]

def random_pos():
    bd = [None] * NSQ
    for c in (WHITE, BLACK):
        n = random.randint(1, MAX_PER_SIDE)
        used = collections.Counter()
        for _ in range(n):
            pt = random.choice([ROCK, PAPER, SCISSORS])
            if used[pt] >= LIMITS[pt]: continue
            zone = goal_zone(random.choice([WHITE, BLACK])) if random.random() < NEAR_GOAL_P else range(NSQ)
            free = [s for s in zone if bd[s] is None]
            if not free: continue
            bd[random.choice(free)] = (c, pt); used[pt] += 1
    return Pos(bd, random.choice([WHITE, BLACK]), 0)

pos = []
while len(pos) < N_POS:
    p = random_pos()
    if any(x is not None and x[0] == WHITE for x in p.bd) and any(x is not None and x[0] == BLACK for x in p.bd):
        pos.append(p)

cmds = []
for p in pos:
    cmds.append("position fen %s" % to_fen(p))
    cmds.append("go perft %d" % DEPTH)
cmds.append("isready"); cmds.append("quit")
out = subprocess.run([ENGINE], input="\n".join(cmds) + "\n", capture_output=True, text=True, timeout=600).stdout
eng = [int(l.split(":")[1]) for l in out.splitlines() if l.lower().startswith("nodes searched")]

print("positions %d, engine replies %d, depth %d" % (len(pos), len(eng), DEPTH))
assert len(eng) == len(pos), "engine returned %d results for %d positions" % (len(eng), len(pos))

bad, stats = 0, collections.Counter()
for p, e in zip(pos, eng):
    mine = perft(p, DEPTH)
    stats["finished"] += 1 if reached_goal(p, 1 - p.stm) else 0
    stats["in_check"] += 1 if checkers(p) else 0
    stats["evasion_restricts"] += 1 if len(gen_legal(p)) != len(gen_non_evasions(p)) else 0
    if mine != e:
        bad += 1
        if bad <= 5:
            print("  MISMATCH mine=%d engine=%d  fen=%s" % (mine, e, to_fen(p)))
print("  mismatches: %d / %d" % (bad, len(pos)))
print("  coverage: finished=%d  goal-threat pending=%d  positions where EVASIONS really restricts=%d"
      % (stats["finished"], stats["in_check"], stats["evasion_restricts"]))
print("  VERDICT:", "MOVEGEN AGREES WITH THE ENGINE" if bad == 0 else "DIVERGENCE -- do not build a table")
