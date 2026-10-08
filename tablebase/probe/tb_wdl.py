"""One implementation of "what is the exact result of this position, and of each move".

Both gates import this. Having a single copy is the point: a win/draw/loss convention that
differs by one inversion between two scripts would produce two confident, contradictory
verdicts, and nothing downstream could tell which was right.

CONVENTIONS, fixed here once:
  * a verdict is always FROM THE SIDE TO MOVE: (WIN|DRAW|LOSS, distance in plies).
  * a MOVE's value is from the mover's side, so the child's verdict -- which belongs to the
    opponent -- is inverted.
  * terminal cases the table does not store, because tb_solve marks them dead or never
    enumerates them, are resolved from the rules instead: standing on your goal, having no
    pieces, having no legal move.

WHY WDL AND NOT THE DISTANCE. tb_solve clamps wins past 127 plies to 0, which is
indistinguishable from a draw in the file, and the 200-ply no-capture rule interacts with
distance in a way a single stored number cannot express. Win/draw/loss needs none of that and
is the only part Elo is made of. Distances are carried through and reported, never gated on.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import rps_rules as R
from tb_probe import DRAW, WIN, LOSS

MAX_PIECES = 5
ORDER = {WIN: 2, DRAW: 1, LOSS: 0}
NAME = {2: "WIN", 1: "DRAW", 0: "LOSS"}


def npieces(p):
    return len(R.pieces(p, R.WHITE)) + len(R.pieces(p, R.BLACK))


def to_probe_pieces(p):
    """rps_rules board -> tb_probe's [(colour, 0-based type, square)]."""
    out = []
    for c in (R.WHITE, R.BLACK):
        for pt in (R.ROCK, R.PAPER, R.SCISSORS):
            for s in R.pieces(p, c, pt):
                out.append((c, pt - 1, s))
    return out


def verdict(tb, p):
    """WDL for the side to move. (kind, plies) or None when nothing covers it."""
    stm = p.stm
    if R.reached_goal(p, R.WHITE) or R.reached_goal(p, R.BLACK):
        return None                                        # already decided
    for c in (R.WHITE, R.BLACK):
        if not R.pieces(p, c):
            return (LOSS, 0) if c == stm else (WIN, 0)
    if not R.has_move(p, stm):
        return (LOSS, 0)
    if npieces(p) > MAX_PIECES:
        return None

    # THE 200-PLY RULE, which the table does not contain. tb_solve stores a true
    # distance-to-zeroing (tb_solve.c:467 gives a capture into a lost child dtz 1, and the
    # sweep propagates only through non-zeroing moves), so the real-game condition is exactly
    # dtz <= 200 - clock: the attacker must reach a capture or the goal before the draw rule
    # fires, and after that zeroing event the clock restarts with the smaller table's own
    # dtz <= 127 to spend. Without this clamp the gate credits wins that cannot be played and
    # scores the engine for declining them -- the very phantom that sank the band study.
    budget = R.CAPTURE_CLOCK - p.clock
    if budget <= 0:
        return (DRAW, 0)
    v = tb.probe(to_probe_pieces(p), stm)
    if v is None:
        return None
    k, d = v
    if k != DRAW and d > budget:
        return (DRAW, 0)
    return v


def move_value(tb, p, m):
    """Value of playing m, from p's mover. Child verdict is the opponent's, so invert.

    do_move carries the capture clock, zeroing it on a capture or a goal entry, so the child's
    budget is correct without being passed down.
    """
    q = R.do_move(p, m)
    if R.reached_goal(q, p.stm):
        return (WIN, 1)                                    # into our own corner
    v = verdict(tb, q)
    if v is None:
        return None
    k, d = v
    if k == WIN:
        return (LOSS, d + 1)
    if k == LOSS:
        return (WIN, d + 1)
    return (DRAW, 0)


def judge(tb, p, played):
    """(best_rank, played_rank, moves) or None if any child is uncovered.

    `played` is an rps_rules move tuple.
    """
    kids = []
    for m in R.gen_legal(p):
        v = move_value(tb, p, m)
        if v is None:
            return None
        kids.append((m, v))
    if not kids:
        return None
    best = max(ORDER[v[0]] for _, v in kids)
    mine = [v for m, v in kids if m == played]
    if not mine:
        return None
    return best, ORDER[mine[0][0]], kids
