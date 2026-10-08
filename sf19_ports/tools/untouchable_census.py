"""Do our draws come from trading into a material class we can no longer win?

The drawn-endgame census said the blockade motif -- a defender squatting uncapturably on the goal
corner -- explains only 6-8% of the corners in drawn endings. Most drawn games end with both goal
corners EMPTY, five pieces on the board, and neither side able to do anything. That is a different
defect, and the material shapes name it.

In this game a piece can only ever be removed by the one type that beats it. So the moment a side
owns no scissors, every enemy paper on the board is UNTOUCHABLE by it, permanently, no matter how
the game goes. Material is not a scalar here: 3 pieces against 2 can be dead drawn if the 2 include
something you can never take, and a trade that looks even can close the win out forever.

If that is what is happening, the fix is not a conversion plan at the end. It is a term fifty plies
EARLIER about which trades to allow -- and nothing the project has tried so far touches it.

The claim is tested against a control, because "most draws have property X" means nothing until you
know how many WINS have it too. Same corpus, same replay, final positions of decisive games as the
comparison group.

Reads only. Shares the replay in drawn_endgame_census.py, re-implemented here so each tool stands
alone and neither can break the other.
"""
import collections
import json
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

# The raw runs were archived into the repo on 2026-09-25. They used to be read straight out of
# a Claude session scratchpad, which is wiped when the session ends -- and two of them were
# already gone by the time the archive was made: the 20k-nodes row and the 1000 ms row lost
# both their seat logs and their referee stream. Those rows now print "no logs"/"no referee
# stream" and are skipped; the ladder still runs 100k -> 400k, which is where the trend lives.
# See the archive's README.md. Do not point this back at /private/tmp.
S = os.path.join(_ROOT, "sf19_ports", "data", "conversion_archive")

STREAMS = [
    ("20k nodes",          S + "/seq_port_s3_s2_20k.0.jsonl"),
    ("100/200k",           S + "/ab_nsdeep_eval.jsonl"),
    ("200/400k",           S + "/ab_nsdeeper_eval.jsonl"),
    ("1000 ms",            S + "/seq_port_sf2_ex_1s.3.jsonl"),
    ("60 s (cloud SPRT)",  S + "/cloud_sprt_deep_0.jsonl"),
]

ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}
TAKEN_BY = {v: k for k, v in BEATS.items()}      # type t is removed only by TAKEN_BY[t]
GOAL, HOME = 80, 0


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


def replay(moves):
    board = initial()
    for i, (frm, to) in enumerate(moves):
        side = i % 2
        src = board[frm]
        if src is None or src[0] != side:
            raise ValueError("bad origin")
        dst = board[to]
        if dst is not None and (dst[0] == side or BEATS[src[1]] != dst[1]):
            raise ValueError("bad capture")
        board[to], board[frm] = src, None
    return board


def stream_games(path):
    opening, moves, meta = {}, collections.defaultdict(list), {}
    for line in open(path, errors="replace"):
        if '"event"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        k, e = (ev.get("pair"), ev.get("game")), ev.get("event")
        if e == "start":
            opening[k] = ev.get("opening") or []
        elif e == "move":
            moves[k].append(ev.get("move"))
        elif e == "end":
            meta[k] = (ev.get("winner"), ev.get("plies"), ev.get("end"))
    return [(k, opening.get(k, []) + moves.get(k, []), v) for k, v in meta.items()]


def untouchable(board, side):
    """Enemy piece types `side` can never remove, because it owns nothing that beats them."""
    mine = {c[1] for c in board if c and c[0] == side}
    theirs = {c[1] for c in board if c and c[0] != side}
    return {t for t in theirs if TAKEN_BY[t] not in mine}


def counts(board, side):
    c = [0, 0, 0]
    for cell in board:
        if cell and cell[0] == side:
            c[cell[1]] += 1
    return c


def classify(board):
    a, b = untouchable(board, 0), untouchable(board, 1)
    if a and b:
        return "BOTH sides hold something the other can never take"
    if a or b:
        return "one side does"
    return "neither -- every piece on the board is capturable"


SNAP_PLY = 200        # the common cross-section: the position after 100 moves each


def replay_to(moves, upto):
    board = initial()
    for i, (frm, to) in enumerate(moves[:upto]):
        side = i % 2
        src = board[frm]
        if src is None or src[0] != side:
            raise ValueError("bad origin")
        dst = board[to]
        if dst is not None and (dst[0] == side or BEATS[src[1]] != dst[1]):
            raise ValueError("bad capture")
        board[to], board[frm] = src, None
    return board


def chebyshev(sq, target):
    return max(abs(sq // 9 - target // 9), abs(sq % 9 - target % 9))


def features(board):
    """What might tell a position that gets converted from one that does not."""
    us, them = counts(board, 0), counts(board, 1)
    a, b2 = untouchable(board, 0), untouchable(board, 1)
    near = []
    for side, corner in ((0, GOAL), (1, HOME)):
        d = [chebyshev(i, corner) for i, c in enumerate(board) if c and c[0] == side]
        near.append(min(d) if d else 9)
    blocked = 0
    for attacker, corner in ((0, GOAL), (1, HOME)):
        cell = board[corner]
        if cell is not None and cell[0] != attacker:
            if not any(c and c[0] == attacker and c[1] == TAKEN_BY[cell[1]] for c in board):
                blocked += 1
    return {
        "pieces on the board": sum(us) + sum(them),
        "material difference": abs(sum(us) - sum(them)),
        "both sides hold an untouchable": int(bool(a) and bool(b2)),
        "either side holds an untouchable": int(bool(a) or bool(b2)),
        "a goal corner is uncapturably blockaded": blocked,
        "nearest-to-goal, the closer side": min(near),
        "nearest-to-goal, the further side": max(near),
    }


def main():
    print("cross-section control -- the SAME ply in games that converted and games that drew")
    print()
    print("Comparing final positions was unfair: a won game ends ON the goal square by definition,")
    print("so every feature of a win is contaminated by the win. The honest comparison is a common")
    print("cross-section. Every game still running at ply %d is snapshotted there, and the two" % SNAP_PLY)
    print("groups are the ones that went on to a decisive result and the ones that drew.")
    print()
    rows = collections.defaultdict(lambda: ([], []))
    n_d = n_w = 0
    for label, path in STREAMS:
        if not os.path.exists(path):
            continue
        for key, toks, (winner, plies, end) in stream_games(path):
            if (plies or 0) < SNAP_PLY:
                continue
            parsed = [parse(t) for t in toks]
            if any(p is None for p in parsed):
                continue
            try:
                board = replay_to(parsed, SNAP_PLY)
            except ValueError:
                continue
            drew = winner is None
            n_d += drew
            n_w += not drew
            for k, v in features(board).items():
                rows[k][0 if drew else 1].append(v)
    print("%d games reached ply %d: %d drew, %d finished decisively" % (n_d + n_w, SNAP_PLY, n_d, n_w))
    print()
    print("  %-42s %9s %9s %8s" % ("feature at ply %d" % SNAP_PLY, "drew", "won/lost", "gap"))
    for k in sorted(rows, key=lambda k: -abs(
            (sum(rows[k][0]) / max(1, len(rows[k][0]))) - (sum(rows[k][1]) / max(1, len(rows[k][1]))))):
        d, w = rows[k]
        md = sum(d) / len(d) if d else 0.0
        mw = sum(w) / len(w) if w else 0.0
        print("  %-42s %9.3f %9.3f %+8.3f" % (k, md, mw, md - mw))
    print()

    # "Either side holds an untouchable" is the only feature with a real gap. Before believing it,
    # rule out the obvious confounder: draws also have FEWER pieces, and fewer pieces means more
    # chance of having run out of some type. If the effect is only piece count wearing a costume it
    # will vanish once piece count is held fixed. So condition on it.
    print("-----")
    print("Is it really the untouchable, or just that draws have fewer pieces?")
    print("Draw rate at ply %d, split by piece count, with and without an untouchable:" % SNAP_PLY)
    print()
    print("  %-14s %22s %22s" % ("pieces", "an untouchable exists", "none"))
    band = collections.defaultdict(lambda: collections.Counter())
    for label, path in STREAMS:
        if not os.path.exists(path):
            continue
        for key, toks, (winner, plies, end) in stream_games(path):
            if (plies or 0) < SNAP_PLY:
                continue
            parsed = [parse(t) for t in toks]
            if any(q is None for q in parsed):
                continue
            try:
                board = replay_to(parsed, SNAP_PLY)
            except ValueError:
                continue
            f = features(board)
            n = f["pieces on the board"]
            key2 = "<=5" if n <= 5 else "6-7" if n <= 7 else "8-9" if n <= 9 else "10+"
            band[key2][(f["either side holds an untouchable"], winner is None)] += 1
    order = ["<=5", "6-7", "8-9", "10+"]
    for k in order:
        c = band[k]
        cells = []
        for has in (1, 0):
            d, w = c[(has, True)], c[(has, False)]
            cells.append("%5.1f%% of %3d" % (100.0 * d / (d + w), d + w) if d + w else "     --    ")
        print("  %-14s %22s %22s" % (k, cells[0], cells[1]))
    print()
    print("A gap near zero means the feature does not distinguish the two groups and no rule built")
    print("on it could either. Only a feature with a real gap is worth building a plan around.")


main()
