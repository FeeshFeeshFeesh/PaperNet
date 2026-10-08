# The check perft CANNOT do: verify gen_evasions() against the engine's LEGAL move list.
# thread.cpp:317 builds rootMoves from MoveList<LEGAL>, so MultiPV at depth 1 emits one info line
# per legal root move. Compare those move sets against mine on positions where the evasion rule
# actually restricts the choice. No sys.argv; knobs at top.
#
# Gotcha handled: the last search before `quit` is aborted and reports garbage (memory
# sticky-loop-lock), so a throwaway position+go is appended and its reply discarded.
import random, subprocess, collections, re
from rps_rules import (Pos, NSQ, WHITE, BLACK, ROCK, PAPER, SCISSORS, GOAL, to_fen, sq_name,
                       gen_legal, gen_non_evasions, checkers, reached_goal, has_move, START_FEN)
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ENGINE  = _ROOT + "/sf19_ports/live/stockfish"
NET     = _ROOT + "/nnue_train/sploppy_repo/runs/sf2_wdl/best.nnue"
WANT    = 150          # positions where EVASIONS restricts
MULTIPV = 256   # ⚠ the option's max is 256; 400 is SILENTLY REJECTED and MultiPV stays 1,
                #   which makes the engine print only its best line and the whole test vacuous
SEED    = 20260927
MAX_PER_SIDE, NEAR_GOAL_P = 3, 0.75

random.seed(SEED)
LIMITS = {ROCK: 3, PAPER: 4, SCISSORS: 3}

def goal_zone(c):
    g = GOAL[c]
    return [s for s in range(NSQ) if abs(s % 9 - g % 9) <= 2 and abs(s // 9 - g // 9) <= 2]

def random_pos():
    bd = [None] * NSQ
    for c in (WHITE, BLACK):
        used = collections.Counter()
        for _ in range(random.randint(1, MAX_PER_SIDE)):
            pt = random.choice([ROCK, PAPER, SCISSORS])
            if used[pt] >= LIMITS[pt]: continue
            zone = goal_zone(random.choice([WHITE, BLACK])) if random.random() < NEAR_GOAL_P else range(NSQ)
            free = [s for s in zone if bd[s] is None]
            if not free: continue
            bd[random.choice(free)] = (c, pt); used[pt] += 1
    return Pos(bd, random.choice([WHITE, BLACK]), 0)

# keep only live positions where the evasion rule genuinely narrows the list
pos, tries = [], 0
while len(pos) < WANT and tries < 400000:
    tries += 1
    p = random_pos()
    if not (any(x and x[0] == WHITE for x in p.bd) and any(x and x[0] == BLACK for x in p.bd)): continue
    if reached_goal(p, p.stm) or reached_goal(p, 1 - p.stm) or not has_move(p, p.stm):     continue
    if not checkers(p):                                                                    continue
    if len(gen_legal(p)) == len(gen_non_evasions(p)):                                      continue
    pos.append(p)

cmds = ["setoption name EvalFile value %s" % NET, "setoption name MultiPV value %d" % MULTIPV,
        "setoption name Threads value 1"]
for p in pos:
    cmds += ["ucinewgame", "position fen %s" % to_fen(p), "go depth 1"]
cmds += ["ucinewgame", "position fen %s" % START_FEN, "go depth 1", "isready", "quit"]   # throwaway
out = subprocess.run([ENGINE], input="\n".join(cmds) + "\n", capture_output=True, text=True, timeout=900).stdout

# split the stream per search: bestmove terminates each
blocks, cur = [], []
for line in out.splitlines():
    cur.append(line)
    if line.startswith("bestmove"):
        blocks.append(cur); cur = []
blocks = blocks[:len(pos)]          # drop the throwaway
print("positions %d, engine search blocks %d" % (len(pos), len(blocks)))
assert len(blocks) == len(pos), "block/position count mismatch"

pv_re = re.compile(r"\bmultipv (\d+)\b.*\bpv ([a-i][1-9][a-i][1-9])")
bad = 0
for p, blk in zip(pos, blocks):
    eng = {}
    for line in blk:
        m = pv_re.search(line)
        if m: eng[int(m.group(1))] = m.group(2)
    engset  = set(eng.values())
    mineset = {sq_name(a) + sq_name(b) for a, b in gen_legal(p)}
    pseudo  = {sq_name(a) + sq_name(b) for a, b in gen_non_evasions(p)}
    if len(mineset) > MULTIPV:
        print("  SKIP: %d legal moves exceeds MultiPV %d" % (len(mineset), MULTIPV)); continue
    if engset != mineset:
        bad += 1
        if bad <= 6:
            print("  MISMATCH fen=%s" % to_fen(p))
            print("    mine-not-engine %s" % sorted(mineset - engset)[:8])
            print("    engine-not-mine %s" % sorted(engset - mineset)[:8])
            print("    (pseudo-legal count %d, my legal %d, engine %d)" % (len(pseudo), len(mineset), len(engset)))
print("  mismatches: %d / %d" % (bad, len(blocks)))
# ⚠ VACUITY GUARD. A test where the engine only ever lists ONE move proves nothing: that is what
# happened when MultiPV was set above its max of 256 and silently ignored.
import collections as _c
dist = _c.Counter(len(engset_sizes) for engset_sizes in [set()])
sizes = _c.Counter()
for p, blk in zip(pos, blocks):
    e = {m.group(2) for m in (pv_re.search(l) for l in blk) if m}
    sizes[len(e)] += 1
multi = sum(v for k, v in sizes.items() if k > 1)
print("  engine legal-list sizes: %s" % dict(sorted(sizes.items())))
print("  positions where the engine listed MORE THAN ONE move: %d / %d  (if this is 0 the test is vacuous)"
      % (multi, len(blocks)))
print("  VERDICT:", "EVASIONS AGREE WITH THE ENGINE" if bad == 0 and blocks else "DIVERGENCE -- do not build a table")
