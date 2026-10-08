"""Idea #3, step 2: can a FREE eval tell in advance which openings are already decided?

Step 1 (`opening_power.py`) measured where the WDL SPRT's pairs went and found the waste had moved:

    8-ply  / 60 s    18.6% of pairs had the SAME COLOUR win both games
    24-ply / 500 ms  45.8%

A pair where the same colour wins both scores exactly 1.0 for the candidate, so it says nothing
about which engine is better -- the OPENING decided it and both engines converted.  Raising
`--opening-plies` to 24 cut the both-drawn waste (30.5% -> 22.4%) but nearly tripled this one, and
the informative fraction fell from 50.9% to 31.8%.

THE CHEAP FIX, IF IT WORKS.  Screening openings by playing games is hopeless: every opening occurs
exactly once, so ranking them needs tens of games each, which costs more than it saves.  But a
FILTER is different -- it needs no games at all.  Replay the opening, evaluate the resulting
position with the net, and throw the opening away if the position is already lopsided.  That is one
forward pass per opening, microseconds, and it can be done inside the referee before play starts.

THIS SCRIPT TESTS THE PREMISE, on openings that have already been played out, at zero cost:
does |eval| after the opening predict that the same colour wins both games?  If it does, the filter
works and idea #3 becomes cheap.  If it does not, openings are decided by something the eval cannot
see and the filter is worthless -- and we have learned that without buying a single game.

No sys.argv: constants below, run straight from PyCharm.
"""
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "nnue_train", "sploppy_repo", "models", "nnue", "py"))

# Inlined from untouchable_census.py rather than imported: that module runs its whole census at
# import time, which would print a report and cost a minute every time this script starts.
ROCK, PAPER, SCISSORS = 0, 1, 2
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}


def mirror_anti(sq):
    rank, file = divmod(sq, 9)
    return (8 - file) * 9 + (8 - rank)


def initial():
    board = [None] * 81
    for piece, squares in ((ROCK, (28, 20, 12)), (PAPER, (37, 29, 21, 13)),
                           (SCISSORS, (38, 30, 22))):
        for sq in squares:
            board[sq] = (0, piece)
            board[mirror_anti(sq)] = (1, piece)
    return board


def parse(tok):
    t = tok.strip()
    if t and t[0] in "RPSrps" and len(t) > 5:
        t = t[1:]
    sep = "x" if "x" in t else "-"
    if sep not in t:
        return None
    a, b = t.split(sep, 1)
    try:
        return ("abcdefghi".index(a[0]) + (int(a[1:]) - 1) * 9,
                "abcdefghi".index(b[0]) + (int(b[1:]) - 1) * 9)
    except (ValueError, IndexError):
        return None

NET = os.path.join(ROOT, "nnue_train", "sploppy_repo", "runs", "sf2_wdl", "best.nnue")
STREAMS = [
    ("24-ply 500ms  sf2_wdl vs live",
     os.path.join(ROOT, "nnue_train", "logs", "wdl_sprt", "seq_wdl_sf2wdl_vs_live_500ms.0.jsonl")),
    ("24-ply 500ms  sf2_wdl vs pass6",
     os.path.join(ROOT, "nnue_train", "logs", "wdl_sprt", "seq_wdl_sf2wdl_vs_pass6_500ms.0.jsonl")),
]
CLOCK = 200          # capture_clock is a constant 200 everywhere in the training corpus
CUTS = [0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]


def replay_opening(tokens):
    """-> (board, side_to_move, since_capture) after the opening, or None if it will not replay."""
    board, since = initial(), 0
    for i, tok in enumerate(tokens):
        mv = parse(tok)
        if mv is None:
            return None
        frm, to = mv
        side = i % 2
        src = board[frm]
        if src is None or src[0] != side:
            return None
        dst = board[to]
        if dst is not None and (dst[0] == side or BEATS[src[1]] != dst[1]):
            return None
        since = 0 if dst is not None else since + 1
        board[to], board[frm] = src, None
    return board, len(tokens) % 2, since


def to_format8(board, mover):
    """81 cell codes in the MOVER's frame: 0 empty, 1-3 own R/P/S, 4-6 enemy.

    Side 1 plays toward square 0, so its frame is the anti-diagonal mirror -- the same map the
    initial position is built with."""
    cells = [0] * 81
    for sq, cell in enumerate(board):
        if cell is None:
            continue
        side, piece = cell
        at = sq if mover == 0 else mirror_anti(sq)
        cells[at] = (1 if side == mover else 4) + piece
    return cells


def load(path):
    """-> list of (opening_tokens, same_colour_won_both, colour_that_won_or_None)"""
    games = collections.defaultdict(dict)
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("event") == "start":
                games[r["pair"]][r["game"]] = {"opening": r.get("opening") or []}
            elif r.get("event") == "end":
                g = games.get(r["pair"], {}).get(r["game"])
                if g is not None:
                    g["winner"] = r["winner"]
    out = []
    for pair, gs in sorted(games.items()):
        if set(gs) != {0, 1} or any("winner" not in g for g in gs.values()):
            continue
        cols = [gs[i]["winner"] for i in (0, 1)]
        decided = cols[0] is not None and cols[0] == cols[1]
        both_drawn = cols[0] is None and cols[1] is None
        out.append((gs[0]["opening"], decided, cols[0] if decided else None, both_drawn))
    return out


def main():
    import numpy as np
    from nnue import export

    net = export.read(NET)
    net["weights_padded"] = export.pad_rows(net)

    rows = []
    for label, path in STREAMS:
        if not os.path.exists(path):
            print("MISSING %s" % path)
            continue
        for tokens, decided, colour, both_drawn in load(path):
            got = replay_opening(tokens)
            if got is None:
                continue
            board, mover, since = got
            rows.append((to_format8(board, mover), mover, since, decided, colour, both_drawn))
        print("%-32s %d pairs replayed" % (label, len(rows)))
    if not rows:
        return

    boards = np.array([r[0] for r in rows], dtype=np.uint8)
    since = np.array([r[2] for r in rows], dtype=np.int32)
    clock = np.full(len(rows), CLOCK, dtype=np.int32)
    raw = export.integer_eval(net, boards, since, clock)
    # put every eval in SIDE 0's frame, so its sign names a colour
    mover = np.array([r[1] for r in rows])
    ev = np.where(mover == 0, raw, -raw)
    decided = np.array([r[3] for r in rows], dtype=bool)
    colour = np.array([-1 if r[4] is None else r[4] for r in rows])

    n = len(rows)
    print("-----")
    print("%d openings replayed and evaluated.  %d (%.1f%%) were opening-decided"
          % (n, decided.sum(), 100.0 * decided.mean()))
    print("mean |eval| after the opening: decided %.4f   not decided %.4f"
          % (np.abs(ev[decided]).mean(), np.abs(ev[~decided]).mean()))
    print("-----")
    print("DOES A FILTER ON |eval| HELP?  drop every opening with |eval| above the cut")
    drawn = np.array([r[5] for r in rows], dtype=bool)
    info = ~decided & ~drawn
    print("A pair is INFORMATIVE only if it is neither opening-decided nor a double draw; anything")
    print("else scores exactly 1.0 for the candidate and moves the LLR by ~nothing. Pairs to a")
    print("verdict is inversely proportional to that fraction, so `speed` below is the whole point.")
    print("%8s %8s %10s %10s %10s %8s" % ("cut", "kept", "decided%", "drawn%", "informative", "speed"))
    base = 100.0 * info.mean()
    print("%8s %7.1f%% %9.1f%% %9.1f%% %10.1f%% %8s"
          % ("none", 100.0, 100.0 * decided.mean(), 100.0 * drawn.mean(), base, "1.00x"))
    for c in CUTS:
        keep = np.abs(ev) <= c
        if keep.sum() < 20:
            continue
        f = 100.0 * info[keep].mean()
        print("%8.2f %7.1f%% %9.1f%% %9.1f%% %10.1f%% %7.2fx"
              % (c, 100.0 * keep.mean(), 100.0 * decided[keep].mean(),
                 100.0 * drawn[keep].mean(), f, f / base))
    print("-----")
    # does the SIGN pick the winning colour?
    dd = decided & (colour >= 0)
    if dd.sum():
        agree = ((ev[dd] > 0) == (colour[dd] == 0)).mean()
        print("when the opening decided it, the eval's SIGN named the winning colour %.1f%% of the time"
              % (100.0 * agree))
    print("-----")
    print("A filter is worth building only if `decided%` falls MATERIALLY while `kept%` stays high.")
    print("If the column barely moves, openings are decided by something this eval cannot see.")


if __name__ == "__main__":
    main()
