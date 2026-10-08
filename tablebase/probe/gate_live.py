"""R4's gate, on every real decision the live bot made that the <=5 table can see.

The pre-registered acceptance test in IDEAS.md is 158 positions from the near-draw band. This
is the same question asked of 18x more data and of actual play rather than a selected band:
every chain-verified live decision at <=5 pieces, 4,373 of them, deduplicated by (position,
move) so the status log's phantom redraws cannot inflate a result.

THE NUMBER THAT MATTERS is not "did the bot play the table's move" -- there are usually several
optimal moves and preferring one is not an error -- but "did the bot's move CHANGE THE RESULT":
a win played into a draw, or a draw played into a loss. That is the only part that costs Elo,
and it is exactly what a probe in search would prevent.

Reported separately, because they are different products:
  * throws out of all decisions           -- how often a probe would have changed the game
  * throws out of WINNABLE decisions      -- the conversion number, which is the 190 Elo target
  * the clock at the throw                -- whether these are the long no-capture stretches

Run from PyCharm. No sys.argv; constants below.
"""
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import rps_rules as R
import tb_probe
import tb_wdl
from live_decisions import decisions
from tb_wdl import ORDER, NAME, MAX_PIECES, npieces

SHOW_EXAMPLES = 15
DEDUPE = True

# How to date the capture clock, which the status log does not print and which decides whether
# a win the table reports is reachable before the 200-ply draw rule fires.
#   "skeptical" -- the largest clock consistent with the log: fewest wins, fewest throws
#   "generous"  -- the smallest: most throws
#   "exact"     -- only decisions where the two agree, so no assumption is in play at all
# A verdict is only worth reporting if all three say the same thing.
CLOCK_MODE = "skeptical"

# Null test. A gate that reports no throws is worthless until it is shown it CAN report one, so
# "worst" replaces the engine's move with the worst-ranked legal move and "random" with an
# arbitrary one. Both must light the gate up; if they do not, a clean run means the instrument
# is broken, not that the engine is right.
PLAYED = "engine"


def main():
    tb = tb_probe.TB()
    print("-----")
    print("%d tables loaded from %s, clock mode %s" % (len(tb.tags), tb.dir, CLOCK_MODE))

    seen = set()
    judged = agree = 0
    uncovered = 0
    throws = []
    winnable = winnable_thrown = 0
    kinds = Counter()
    rootmix = Counter()

    for d in decisions():
        p, m = d["pos"], d["move"]
        if npieces(p) > MAX_PIECES:
            continue
        if CLOCK_MODE == "exact":
            if not d["clock_exact"]:
                continue
        elif CLOCK_MODE == "generous":
            p.clock = d["clock_lo"]
        if p.clock > R.CAPTURE_CLOCK:
            continue                                   # a clock the log cannot justify
        if DEDUPE:
            key = (R.to_fen(p).split()[0], p.stm, d["move_str"], p.clock // 8)
            if key in seen:
                continue
            seen.add(key)
        if PLAYED != "engine":
            legal = list(R.gen_legal(p))
            if not legal:
                continue
            if PLAYED == "worst":
                scored = [(ORDER[v[0]], mm) for mm in legal
                          for v in [tb_wdl.move_value(tb, p, mm)] if v is not None]
                if len(scored) != len(legal):
                    uncovered += 1
                    continue
                m = min(scored)[1]
            else:
                m = legal[(d["ply"] or 0) % len(legal)]
        got = tb_wdl.judge(tb, p, m)
        if got is None:
            uncovered += 1
            continue
        best, mine, kids = got
        judged += 1
        rootmix[NAME[best]] += 1
        if best == 2:
            winnable += 1
        if mine == best:
            agree += 1
        else:
            throws.append((d, best, mine, kids))
            kinds["%s->%s" % (NAME[best], NAME[mine])] += 1
            if best == 2:
                winnable_thrown += 1

    print("-----")
    print("decisions judged         : %d" % judged)
    print("uncovered (skipped)      : %d" % uncovered)
    print("best-available mix       : %s"
          % ", ".join("%s %d" % (k, rootmix[k]) for k in ("WIN", "DRAW", "LOSS") if rootmix[k]))
    print("-----")
    print("preserved the result     : %d" % agree)
    print("THREW the result         : %d  (%.2f%% of all decisions)"
          % (len(throws), 100.0 * len(throws) / judged if judged else 0.0))
    if winnable:
        print("of WINNABLE decisions    : %d winnable, %d thrown  (%.2f%%)"
              % (winnable, winnable_thrown, 100.0 * winnable_thrown / winnable))
    if kinds:
        print("throw kinds              : %s"
              % ", ".join("%s %d" % kv for kv in sorted(kinds.items())))

    if throws:
        ev = [d["eval"] for d, _, _, _ in throws if d.get("eval") is not None]
        if ev:
            print("engine eval at the throw : median %+d cp, min %+d, max %+d"
                  % (sorted(ev)[len(ev) // 2], min(ev), max(ev)))
        print("-----")
        for (d, best, mine, kids) in throws[:SHOW_EXAMPLES]:
            bm = [R.sq_name(m[0]) + "-" + R.sq_name(m[1])
                  for m, v in kids if ORDER[v[0]] == best]
            print("  game %-3s ply %-4s played %-7s %s -> %s, best %s (%s)  eval %s"
                  % (d["game"], d["ply"], d["move_str"], NAME[best], NAME[mine],
                     "/".join(bm[:3]), len(bm), d.get("eval")))


if __name__ == "__main__":
    main()
