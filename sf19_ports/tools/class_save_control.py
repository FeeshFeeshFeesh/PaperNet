"""Was the piece rescuable at all? The save-rate gap means nothing until this is answered.

`class_save_probe.py` found the engine saves its LAST piece of a type 88.0% of the time and a spare
one 92.7% -- no extra urgency for the irreplaceable one, slightly less. But it only required the
piece to be ATTACKED. If last-of-type pieces are more often simply trapped, with no legal move that
gets any piece of that type to safety, then the engine's lower save rate is arithmetic, not
priorities, and the whole comparison collapses.

So enumerate the side's legal moves in each recorded position and ask whether ANY of them leaves a
piece of that type unattacked. Re-tally the save rate over the rescuable cases only. No engine is
needed: the positions and the moves the engine chose are already on disk.

Also reports the reverse case -- how often the engine had a rescue and declined it -- because that,
not the raw rate, is the behaviour that would indict search.

No sys.argv.
"""
import collections
import json
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
ROWS = os.path.join(S, "class_save_probe.jsonl")

ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}


def unfen(text):
    board = [None] * 81
    for r, row in enumerate(text.split("/")):
        f = 0
        for ch in row:
            if ch == ".":
                f += 1
            elif ch.isdigit():
                f += int(ch)
            else:
                board[r * 9 + f] = (0 if ch.isupper() else 1, LETTER.index(ch.upper()))
                f += 1
    return board


def neighbours(sq):
    r, f = divmod(sq, 9)
    for dr in (-1, 0, 1):
        for df in (-1, 0, 1):
            if dr or df:
                rr, ff = r + dr, f + df
                if 0 <= rr < 9 and 0 <= ff < 9:
                    yield rr * 9 + ff


def attacked(board, sq):
    c = board[sq]
    return any(board[n] and board[n][0] != c[0] and BEATS[board[n][1]] == c[1]
               for n in neighbours(sq))


def moves(board, side):
    for sq, c in enumerate(board):
        if not c or c[0] != side:
            continue
        for n in neighbours(sq):
            d = board[n]
            if d is None or (d[0] != side and BEATS[c[1]] == d[1]):
                yield sq, n


def rescuable(board, side, u):
    """Is there ANY legal move after which a piece of type u stands unattacked?"""
    for frm, to in moves(board, side):
        nb = list(board)
        nb[to], nb[frm] = nb[frm], None
        mine = [s for s, c in enumerate(nb) if c and c[0] == side and c[1] == u]
        if mine and not all(attacked(nb, s) for s in mine):
            return True
    return False


def main():
    rows = [json.loads(l) for l in open(ROWS)]
    print("%d recorded positions" % len(rows))

    tally = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        board = unfen(r["fen"])
        side = r["side"]
        u = LETTER.index(r["u"])
        can = rescuable(board, side, u)
        t = tally[r["group"]]
        t["n"] += 1
        t["rescuable"] += 1 if can else 0
        if can:
            t["saved_when_possible"] += 1 if r["saved"] else 0
        else:
            t["doomed"] += 1

    print()
    print("  %-14s %5s %12s %20s" % ("group", "n", "rescuable", "saved WHEN POSSIBLE"))
    rates = {}
    for g in ("last-of-type", "has-a-spare"):
        t = tally[g]
        if not t["n"]:
            continue
        can = t["rescuable"]
        rate = 100.0 * t["saved_when_possible"] / can if can else 0.0
        rates[g] = rate
        print("  %-14s %5d %10.1f%% %18.1f%%  (%d of %d)"
              % (g, t["n"], 100.0 * can / t["n"], rate, t["saved_when_possible"], can))

    if len(rates) == 2:
        gap = rates["last-of-type"] - rates["has-a-spare"]
        print()
        print("  EXTRA URGENCY, once trapped pieces are removed: %+.1f points" % gap)
        print()
        for g in ("last-of-type", "has-a-spare"):
            t = tally[g]
            can = t["rescuable"]
            declined = can - t["saved_when_possible"]
            print("  %-14s had a rescue and did not take it: %d of %d (%.1f%%)"
                  % (g, declined, can, 100.0 * declined / can if can else 0.0))
        print()
        print("  A rescue declined is the behaviour that would indict search: the engine could")
        print("  have kept the class open and chose otherwise. If it declines the LAST one as")
        print("  often as a spare, its moves do not price the permanence at all.")


main()
