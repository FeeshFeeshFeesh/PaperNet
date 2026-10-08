"""Solve small signatures FROM THE RULES ALONE, bottom-up, and compare every position to the probe.

`verify_tbz.py` proves the packed table returns the byte the solver wrote. That is a statement
about the CONVERSION only: it would pass just as happily if tb_solve's values were wrong, or if
tb_probe picked the wrong member of the relabelling orbit. This closes both gaps with an oracle
that shares no code with either side -- a plain retrograde solve written against `rps_rules`, the
module the engine port and the live gate already use.

INDEPENDENCE IS THE WHOLE POINT, so the oracle never consults the table. Signatures are solved in
increasing piece order and a capture is resolved from the oracle's OWN earlier results; a capture
that takes the opponent's last piece is decided from the rules (a side with no pieces has no move
and loses). Nothing is read from tbz except the values being tested.

WHAT IT COVERS, end to end:
  rules -> independent DTZ solve   vs   tb_solve -> dense conversion -> orbit fold -> probe

All nine 1v1 signatures are checked, but only two are STORED -- the other seven exist solely as
orbit images, so they are reachable only through the fold. That is the case no byte-for-byte check
can reach at all: a fold that returned a plausible wrong table would sail through verify_tbz.

DTZ semantics are matched deliberately: distance to a ZEROING event (a capture or a goal entry),
not to the end of the game, because that is what tb_solve stores (tb_solve.c:467 gives a capture
into a lost child dtz 1). The capture clock is ignored on both sides -- it is applied later, by
tb_wdl, as dtz <= 200 - clock.

Run from PyCharm. No sys.argv; constants below.
"""
import itertools
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import rps_rules as R
import tb_probe
from tb_probe import DRAW, WIN, LOSS

# (white R,P,S) (black R,P,S). Solved in this order, so a capture always lands on a signature
# already solved. Pieces must be pairwise distinct: identical pieces need the k!-folded ordering
# the probe applies, and an oracle that has to replicate that is no longer independent.
TAGS = ["100_100", "100_010", "100_001", "010_100", "010_010",
        "010_001", "001_100", "001_010", "001_001"]
REPORT = 6

# A three-piece signature costs ~1.02M states and several minutes of pure Python, so it is not
# in the default list. Add one (e.g. "001_110") to exercise the path 1v1 cannot reach: a capture
# that lands in a SMALLER signature rather than ending the game, which is what tb_solve's
# capture_child does and what the oracle answers from its own earlier results.
THREE_PIECE_NOTE = "001_110"


def pieces_of(tag):
    w, b = tag.split("_")
    out = []
    for c, part in ((R.WHITE, w), (R.BLACK, b)):
        for ti, ch in enumerate(part):
            out.extend([(c, ti + 1)] * int(ch))
    return out


def key_of(bd):
    """A signature-and-placement key independent of slot order: sorted (colour, type, square)."""
    return tuple(sorted((x[0], x[1], s) for s, x in enumerate(bd) if x))


def decided(p):
    """Value from the side to move when the rules settle it outright, else None."""
    if not R.pieces(p, p.stm):
        return (LOSS, 0)                       # nothing to move with
    if not R.pieces(p, 1 - p.stm):
        return (WIN, 0)                        # opponent has nothing left
    if not R.has_move(p, p.stm):
        return (LOSS, 0)
    return None


def solve(tag, known):
    """Retrograde DTZ solve of one signature. Fills `known[(key, stm)] = (kind, dtz)`."""
    slots = pieces_of(tag)
    n = len(slots)
    goal = {R.WHITE: R.GOAL[R.WHITE], R.BLACK: R.GOAL[R.BLACK]}
    val, quiet_kids, counter, zdraw = {}, {}, {}, {}

    states = [sqs for sqs in itertools.permutations(range(R.NSQ), n)
              if not any(s == goal[c] for (c, _), s in zip(slots, sqs))]

    for sqs in states:
        bd = [None] * R.NSQ
        for (c, t), s in zip(slots, sqs):
            bd[s] = (c, t)
        k0 = key_of(bd)
        for stm in (R.WHITE, R.BLACK):
            p = R.Pos(list(bd), stm, 0)
            st = (k0, stm)
            mv = list(R.gen_legal(p))
            if not mv:
                val[st] = (LOSS, 0)
                continue
            win1 = False
            zd = False
            kids = []
            for m in mv:
                if m[1] == goal[stm]:
                    win1 = True                          # straight into our own corner
                    break
                q = R.do_move(p, m)
                if p.bd[m[1]] is not None:               # a capture leaves this signature
                    sub = decided(q) or known.get((key_of(q.bd), q.stm))
                    if sub is None:
                        raise SystemExit("oracle: unsolved sub-position for %s" % tag)
                    if sub[0] == LOSS:
                        win1 = True                      # capture into a lost position
                        break
                    if sub[0] == DRAW:
                        zd = True
                else:
                    kids.append((key_of(q.bd), q.stm))
            if win1:
                val[st] = (WIN, 1)
            elif not kids:
                val[st] = (DRAW, 0) if zd else (LOSS, 1)  # only zeroing moves, none of them win
            else:
                quiet_kids[st] = kids
                counter[st] = len(kids)
                zdraw[st] = zd

    # invert the quiet edges, then resolve level by level so each dtz comes out minimal
    pred = {}
    for st, kids in quiet_kids.items():
        for ch in kids:
            pred.setdefault(ch, []).append(st)

    # Level-ordered on the CHILD's own distance, so a parent is always child + 1.
    #
    # The seeds do NOT all sit at distance 0: a state with no legal move is LOSS 0, but one
    # holding a winning capture is WIN 1 and one whose every move is a losing capture is
    # LOSS 1. Sweeping all seeds as a single first wave gives the parents of a distance-1 seed
    # a distance of 1 as well, which is uniformly one ply short -- the bug that made this
    # oracle disagree with the solver on every non-draw position while agreeing on every WDL.
    levels = {}
    for st, (_, dd) in val.items():
        levels.setdefault(dd, []).append(st)
    # Level 0 is EMPTY in most signatures -- it holds only states with no legal move at all,
    # which two pieces on a 9x9 board essentially never produce. The seeds then start at
    # distance 1, so the walk has to skip empty levels and stop only when none remain above.
    d = 0
    maxd = max(levels) if levels else -1
    while d <= maxd:
        for ch in levels.get(d, ()):
            kind = val[ch][0]
            for st in pred.get(ch, ()):
                if st in val:
                    continue
                if kind == LOSS:
                    val[st] = (WIN, d + 1)               # a quiet move into a lost child wins
                    levels.setdefault(d + 1, []).append(st)
                    maxd = max(maxd, d + 1)
                elif kind == WIN:
                    counter[st] -= 1
                    if counter[st] == 0 and not zdraw[st]:
                        val[st] = (LOSS, d + 1)          # every option loses
                        levels.setdefault(d + 1, []).append(st)
                        maxd = max(maxd, d + 1)
        d += 1

    for st in quiet_kids:
        val.setdefault(st, (DRAW, 0))
    known.update(val)
    return val, slots, states


def main():
    tb = tb_probe.TB()
    print("-----")
    print("%d tables available to the probe; %d stored of the 9 one-v-one signatures"
          % (len(tb.tags), sum(1 for t in TAGS if t in tb.tags and sum(
              int(c) for c in t.replace("_", "")) == 2)))
    known = {}
    allok = True
    for tag in TAGS:
        val, slots, states = solve(tag, known)
        stored = "stored" if tag in tb.tags else "fold only"
        bad = agree = kindok = 0
        for sqs in states:
            bd = [None] * R.NSQ
            for (c, t), s in zip(slots, sqs):
                bd[s] = (c, t)
            k0 = key_of(bd)
            probe_in = [(c, t - 1, s) for (c, t), s in zip(slots, sqs)]
            for stm in (R.WHITE, R.BLACK):
                mine = val[(k0, stm)]
                got = tb.probe(probe_in, stm)
                if got is not None and got[0] == mine[0]:
                    kindok += 1
                if got is None or got[0] != mine[0] or (mine[0] != DRAW and got[1] != mine[1]):
                    if bad < REPORT:
                        print("  MISMATCH %s %s stm=%d oracle=%s probe=%s"
                              % (tag, sqs, stm, mine, got))
                    bad += 1
                else:
                    agree += 1
        allok &= (bad == 0)
        print("  %-8s %-10s %8d positions, %8d exact, %8d WDL-correct, %d mismatches"
              % (tag, stored, agree + bad, agree, kindok, bad))
    print("-----")
    print("VERDICT: %s" % ("the solver's values AND the orbit fold are correct from first principles"
                           if allok else "MISMATCHES -- do not trust the probe"))


if __name__ == "__main__":
    main()
