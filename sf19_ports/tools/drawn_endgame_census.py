"""What do the games we fail to convert actually LOOK like at the end?

`conversion_census.py` measured the defect: at 1000 ms, 45% of the positions our own search called
won never finish, and the score sits flat at +400 for a hundred to four hundred plies while the
200-ply capture clock runs out. That says the engine cannot find a conversion. It does not say what
it is failing to convert, and a fix has to target something.

So replay every capture-clock draw to its final position and look. If the drawn endings cluster into
a handful of material shapes, each shape is a candidate for one small, targeted rule -- the way
king-and-rook endings in chess are handled by "push the king to the edge" rather than by a better
evaluation of the middlegame.

Replay needs no engine and costs nothing: the referee's stream holds every move of every game, and
the starting position is fixed (`sploppy_repo/engine/src/board.rs::initial_board`). Nothing here is
trusted for free -- every move is checked against the rules as it is applied, and a game whose
replay does not reconcile with the referee's own verdict is dropped and counted, not smoothed over.
A census built on a replay that silently went wrong would be worse than no census.

Reads only. No games are played and nothing is written.
"""
import collections
import json
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK

STREAMS = [
    ("20k nodes",  S + "/seq_port_s3_s2_20k.0.jsonl"),
    ("100/200k",   S + "/ab_nsdeep_eval.jsonl"),
    ("200/400k",   S + "/ab_nsdeeper_eval.jsonl"),
    ("1000 ms",    S + "/seq_port_sf2_ex_1s.3.jsonl"),
    ("60 s (cloud SPRT)", S + "/cloud_sprt_deep_0.jsonl"),
]

SHOW_BOARDS = 3          # example positions to print per stream
TOP_SHAPES  = 8          # material shapes to list per stream

# ---------------------------------------------------------------- the rules, only what replay needs
GOAL, HOME = 80, 0                         # i9 is Blue's goal, a1 is Red's
ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}


def mirror_anti(sq):
    rank, file = divmod(sq, 9)
    return (8 - file) * 9 + (8 - rank)


def initial():
    """board[sq] = (side, piece) or None. Side 0 is Blue, who moves first."""
    board = [None] * 81
    for piece, squares in ((ROCK, (28, 20, 12)),
                           (PAPER, (37, 29, 21, 13)),
                           (SCISSORS, (38, 30, 22))):
        for sq in squares:
            board[sq] = (0, piece)
            board[mirror_anti(sq)] = (1, piece)
    return board


def name(sq):
    return "abcdefghi"[sq % 9] + str(sq // 9 + 1)


def parse(tok):
    """'e5-d4', 'e5xd4', 'Re2-e3' -> (from, to). Returns None if it is not a move."""
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


def apply_move(board, side, frm, to):
    """Apply one move, or raise ValueError saying exactly what did not hold."""
    src = board[frm]
    if src is None:
        raise ValueError("%s is empty" % name(frm))
    if src[0] != side:
        raise ValueError("%s holds the other side" % name(frm))
    if max(abs(frm // 9 - to // 9), abs(frm % 9 - to % 9)) != 1:
        raise ValueError("%s-%s is not a king step" % (name(frm), name(to)))
    dst = board[to]
    captured = False
    if dst is not None:
        if dst[0] == side:
            raise ValueError("%s is our own piece" % name(to))
        if BEATS[src[1]] != dst[1]:
            raise ValueError("%s cannot take %s" % (LETTER[src[1]], LETTER[dst[1]]))
        captured = True
    board[to] = src
    board[frm] = None
    return captured


def replay(moves):
    """Return (board, plies, since_capture) or raise. Blue is side 0 and moves first."""
    board = initial()
    since = 0
    for i, (frm, to) in enumerate(moves):
        since = 0 if apply_move(board, i % 2, frm, to) else since + 1
    return board, len(moves), since


# ---------------------------------------------------------------- reading a stream
def stream_games(path):
    """(key, opening, moves, winner, plies, end) per game, moves in play order."""
    opening, moves, meta = {}, collections.defaultdict(list), {}
    for line in open(path, errors="replace"):
        if '"event"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        k = (ev.get("pair"), ev.get("game"))
        e = ev.get("event")
        if e == "start":
            opening[k] = ev.get("opening") or []
        elif e == "move":
            moves[k].append(ev.get("move"))
        elif e == "end":
            meta[k] = (ev.get("winner"), ev.get("plies"), ev.get("end"))
    out = []
    for k, m in meta.items():
        out.append((k, opening.get(k, []), moves.get(k, []), m[0], m[1], m[2]))
    return out


def shape(board):
    cnt = [[0, 0, 0], [0, 0, 0]]
    for cell in board:
        if cell is not None:
            cnt[cell[0]][cell[1]] += 1
    return tuple(cnt[0]), tuple(cnt[1])


def render(board):
    rows = []
    for rank in range(8, -1, -1):
        row = ""
        for file in range(9):
            cell = board[rank * 9 + file]
            if cell is None:
                row += " ."
            else:
                ch = LETTER[cell[1]]
                row += " " + (ch if cell[0] == 0 else ch.lower())
        rows.append("  %d %s" % (rank + 1, row))
    rows.append("      a b c d e f g h i")
    return rows


def chebyshev(sq, target):
    return max(abs(sq // 9 - target // 9), abs(sq % 9 - target % 9))


def main():
    print("drawn-endgame census -- the final position of every capture-clock draw")
    print("uppercase = Blue (goal i9), lowercase = Red (goal a1)")
    print()
    for label, path in STREAMS:
        print("-----", label)
        if not os.path.exists(path):
            print("  no stream at %s" % path)
            print()
            continue
        games = stream_games(path)
        draws, bad, checked = [], collections.Counter(), 0
        for key, opening, mvs, winner, plies, end in games:
            toks = list(opening) + list(mvs)
            parsed = [parse(t) for t in toks]
            if any(p is None for p in parsed):
                bad["unparsable move token"] += 1
                continue
            try:
                board, n, since = replay(parsed)
            except ValueError as e:
                bad[str(e).split(" is ")[0][:0] or "illegal move during replay"] += 1
                continue
            checked += 1
            # Reconcile with the referee rather than assume the replay is right.
            if end == "Goal":
                if board[GOAL] is None and board[HOME] is None:
                    bad["Goal game ended with neither corner occupied"] += 1
                    continue
            elif end == "CaptureClock":
                if since < 200:
                    bad["CaptureClock game had a capture %d plies ago" % since] += 1
                    continue
                draws.append((key, board, n, since))
        print("  %d games, %d replayed and reconciled, %d capture-clock draws"
              % (len(games), checked, len(draws)))
        for what, c in bad.most_common(4):
            print("  DROPPED %4d: %s" % (c, what))
        if not draws:
            print()
            continue

        shapes = collections.Counter(shape(b) for _, b, _, _ in draws)
        print("  material at the end (ours R P S | theirs R P S), %d distinct shapes:"
              % len(shapes))
        for (us, them), c in shapes.most_common(TOP_SHAPES):
            print("    %d%d%d | %d%d%d   %4d   %5.1f%%"
                  % (us + them + (c, 100.0 * c / len(draws))))
        tot = collections.Counter()
        for _, b, _, _ in draws:
            us, them = shape(b)
            tot["pieces %2d" % (sum(us) + sum(them))] += 1
        print("  total pieces left: " + ", ".join("%s x%d" % (k, v) for k, v in sorted(tot.items())))

        # How close did anyone get? A draw where nobody is near a goal is a different problem
        # from one where a piece is parked one square away and cannot step in.
        near = collections.Counter()
        for _, b, _, _ in draws:
            ours = min([chebyshev(i, GOAL) for i, c in enumerate(b) if c and c[0] == 0] or [9])
            theirs = min([chebyshev(i, HOME) for i, c in enumerate(b) if c and c[0] == 1] or [9])
            near[(ours, theirs)] += 1
        print("  nearest piece to its own goal (ours, theirs):")
        for (a, d), c in near.most_common(6):
            print("    ring %d / ring %d   %4d   %5.1f%%" % (a, d, c, 100.0 * c / len(draws)))

        # THE question the eviction work was really about, asked of the whole corpus rather than
        # of one remembered game: is the drawn ending a BLOCKADE? A piece of the defending side
        # standing on the attacker's goal corner stops the game dead if the attacker owns nothing
        # that beats it, because in this game a piece can only be removed by the type it loses to.
        block = collections.Counter()
        for _, b2, _, _ in draws:
            for attacker, corner in ((0, GOAL), (1, HOME)):
                cell = b2[corner]
                if cell is None:
                    block["corner empty"] += 1
                elif cell[0] == attacker:
                    block["corner held by the attacker (should have won)"] += 1
                else:
                    need = [k for k, v in BEATS.items() if v == cell[1]][0]
                    have = any(c and c[0] == attacker and c[1] == need for c in b2)
                    block["BLOCKADED, attacker still owns a %s" % LETTER[need] if have
                          else "BLOCKADED, attacker owns NO %s -- uncapturable" % LETTER[need]] += 1
        n2 = 2 * len(draws)
        print("  the two goal corners at the end (%d corners over %d draws):" % (n2, len(draws)))
        for k, c in block.most_common():
            print("    %-52s %4d   %5.1f%%" % (k, c, 100.0 * c / n2))
        stuck = sum(c for k, c in block.items() if "uncapturable" in k)
        both = sum(1 for _, b2, _, _ in draws
                   if all(b2[cn] is not None and b2[cn][0] != at
                          and not any(c and c[0] == at
                                      and c[1] == [k for k, v in BEATS.items() if v == b2[cn][1]][0]
                                      for c in b2)
                          for at, cn in ((0, GOAL), (1, HOME))))
        print("  at least one corner permanently blockaded: %d of %d corners (%.1f%%); "
              "BOTH corners in the same game: %d draws (%.1f%%)"
              % (stuck, n2, 100.0 * stuck / n2, both, 100.0 * both / len(draws)))

        print("  example final positions:")
        for key, b, n, since in draws[:SHOW_BOARDS]:
            print("    pair %s game %s, %d plies, %d since the last capture"
                  % (key[0], key[1], n, since))
            for row in render(b):
                print("  " + row)
            print()
        print()


main()
