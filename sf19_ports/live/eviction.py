"""The eviction override: a root-level move policy for the endgame PaperNet keeps drawing.

WHAT IS BROKEN. Measured 2026-09-24 with eviction_probe.py on the deployed net. Red's scissors
sits on its own corner, red's paper guards Ring 1, two blue rocks wait in Ring 2. The engine scores
this +8.6 to +9.2 at depth 60 -- it KNOWS it is winning -- and then shuffles a rock back and forth
until the 200-ply capture clock draws it. Handed the finished formation (both blue papers already
in Ring 1) at depth 63 it plays a paper back OUT. Handed a position one move from the formation it
plays the second paper away rather than onto the empty Ring 1 square. The march is priced at zero
by the net: the far position and the assembled position score the same, so nothing pulls the papers
forward. This is not a horizon problem and not a clock problem; it is an absent plan.

WHY RING 1 IS THE WHOLE GAME. Ring 1 is poison for both sides at once. Red's paper cannot take a
blue paper. Red's scissors can -- but the instant it steps off the corner to do so, a blue rock
walks in from Ring 2 and wins. Blue's rocks cannot sit in Ring 1 because red's paper eats them.
So blue papers, and only blue papers, can stand in Ring 1, and once both do, red's paper has
nowhere legal left inside the ring and must abandon the corner. That equilibrium only holds while
two rocks actually cover the corner, which is why the papers assemble in Ring 2 FIRST and commit to
Ring 1 only after: Vladimir's rule, and the probe backs it.

WHAT THIS DOES AND DOES NOT DO. It never invents a score and never enters the search, so it cannot
poison the transposition table or fake a mate. It only narrows the ROOT move list and lets the real
search choose inside it, which is why there are two modes and one mechanism:

    MARCH  the papers are not yet assembled -> allow only moves that advance one toward Ring 2,
           or step one into Ring 1 once the assembly and the two rocks are in place.
    HOLD   the formation is built -> allow everything EXCEPT the moves that dismantle it, so the
           search can hunt for the win without being able to retreat.

A forced win found by the ordinary search always wins over both; the seat checks that first and
never calls this when it has a mate. If every allowed move is a forced loss the seat throws the
whole thing away and plays what the engine wanted. That is the user's policy verbatim: if a forced
win is seen, do that; otherwise, unless it loses, march the papers.

COORDINATES. Engine coordinates throughout: index = rank * 9 + file, a1 = 0, i9 = 80. The seat's
make_transform() has already normalised the board so blue's home is a1 and blue's GOAL is i9.

SCOPE IS DELIBERATELY NARROW. RED_MAX = 2 means this only fires on the plain scenario -- red has
nothing but the corner scissors and the guard paper. With a third red piece the race to OUR corner
becomes real and the theory is not worked out. Widen it when evictionology says so, not before.

No sys.argv; constants below.
"""

GOAL       = 80     # i9 in engine coordinates: the square blue must reach
MAX_PIECES = 8      # above this it is not an endgame and this has no business firing
RED_MAX    = 2      # the plain scenario only: red's corner scissors plus its guard paper
NEED_ROCKS = 2      # rocks covering the corner from Ring 2, without which Ring 1 is not poison
MARCH_WALL = 3      # papers may drop back to Ring 3 to re-arrange Ring 2, never to Ring 4
BEATS      = {"R": "S", "S": "P", "P": "R"}
STEPS      = [(dr, df) for dr in (-1, 0, 1) for df in (-1, 0, 1) if dr or df]


def cheb(a, b):
    return max(abs(a // 9 - b // 9), abs(a % 9 - b % 9))


def ring(d):
    return [s for s in range(81) if cheb(s, GOAL) == d]


def parse_fen(fen):
    """An engine FEN -> (board as a list of 81, blue_to_move). Uppercase is blue."""
    parts = fen.split()
    rows = parts[0].split("/")
    if len(rows) != 9:
        raise ValueError("FEN has %d rows" % len(rows))
    board = [None] * 81
    for i, row in enumerate(rows):
        r, f = 8 - i, 0            # the first row of an engine FEN is rank 9
        for ch in row:
            if ch.isdigit():
                f += int(ch)
            elif ch.upper() in "RPS":
                board[r * 9 + f] = ch
                f += 1
            else:
                raise ValueError("unknown piece %r" % ch)
        if f != 9:
            raise ValueError("row %d covers %d files" % (r + 1, f))
    return board, (len(parts) < 2 or parts[1] != "b")


def name(i):
    return "abcdefghi"[i % 9] + str(i // 9 + 1)


def moves_of(board, sq):
    """Every square that piece can step to. A move onto a piece is legal only if we beat it."""
    out = []
    r, f = sq // 9, sq % 9
    me = board[sq]
    for dr, df in STEPS:
        nr, nf = r + dr, f + df
        if not (0 <= nr < 9 and 0 <= nf < 9):
            continue
        to = nr * 9 + nf
        there = board[to]
        if there is None or (there.isupper() != me.isupper() and BEATS[me.upper()] == there.upper()):
            out.append(to)
    return out


def detect(board, blue_to_move):
    """The pattern, or None. Loose on purpose -- Vladimir: 'most eviction scenarios are just plain'."""
    if not blue_to_move:
        return None
    blue = [s for s in range(81) if board[s] and board[s].isupper()]
    red  = [s for s in range(81) if board[s] and board[s].islower()]
    if len(blue) + len(red) > MAX_PIECES or len(red) > RED_MAX:
        return None
    if board[GOAL] != "s":                                   # red's scissors on its own corner
        return None
    guards = [s for s in red if board[s] == "p" and cheb(s, GOAL) == 1]
    if len(guards) != 1:
        return None
    rocks  = [s for s in blue if board[s] == "R" and cheb(s, GOAL) == 2]
    papers = [s for s in blue if board[s] == "P"]
    if len(rocks) < NEED_ROCKS or len(papers) < 2:
        return None
    return {"guard": guards[0], "rocks": rocks, "papers": papers,
            "in1": [s for s in papers if cheb(s, GOAL) == 1],
            "in2": [s for s in papers if cheb(s, GOAL) == 2]}


def allowed(board, blue_to_move):
    """(set of allowed root moves as 'a1b2' strings, mode, why) -- or (None, None, why not)."""
    pat = detect(board, blue_to_move)
    if pat is None:
        return None, None, "not an eviction position"

    free1 = [s for s in ring(1) if board[s] is None]
    in1, in2 = pat["in1"], pat["in2"]
    covered = len(pat["rocks"]) >= NEED_ROCKS

    # HOLD. The formation is built, or as built as it gets. Forbid only the moves that undo it and
    # let the search work inside what is left -- this is where the actual win has to be found.
    if len(in1) >= 2 or (len(in1) >= 1 and not free1):
        keep = set()
        for s in range(81):
            if not (board[s] and board[s].isupper()):
                continue
            for to in moves_of(board, s):
                if board[s] == "P" and cheb(s, GOAL) == 1 and cheb(to, GOAL) != 1:
                    continue                       # a paper must not walk out of Ring 1
                if board[s] == "R" and cheb(s, GOAL) == 2 and cheb(to, GOAL) > 2:
                    continue                       # a rock must not stop covering the corner
                keep.add(name(s) + name(to))
        return (keep or None), "HOLD", "formation built; retreats forbidden"

    # MARCH, in three priorities. The commit into Ring 1 outranks everything, because the engine's
    # demonstrated failure is exactly refusing to make it: offered both, it re-arranges forever.
    #
    #   COMMIT     both papers assembled and the rocks cover the corner -> step one into Ring 1.
    #   REARRANGE  papers are all within Ring 3 but no commit is available -> let them move freely
    #              inside Rings 2 and 3. Vladimir: re-arranging Ring 2 sometimes needs a Ring 3
    #              step, and never more than that, so Ring 4 is the wall.
    #   APPROACH   a paper is still outside Ring 3 -> walk it strictly closer, stopping at Ring 2.
    out = set()
    if covered and len(in2) + len(in1) >= 2 and free1:
        for s in in2:
            for to in moves_of(board, s):
                if to in free1:
                    out.add(name(s) + name(to))
        if out:
            return out, "MARCH", "assembly complete; stepping a paper into Ring 1"

    if all(cheb(s, GOAL) <= MARCH_WALL for s in pat["papers"]):
        for s in pat["papers"]:
            if cheb(s, GOAL) < 2:
                continue                       # a paper already in Ring 1 stays there
            for to in moves_of(board, s):
                if 2 <= cheb(to, GOAL) <= MARCH_WALL:
                    out.add(name(s) + name(to))
        if out:
            return out, "MARCH", "re-arranging inside Rings 2-%d" % MARCH_WALL

    for s in pat["papers"]:
        d = cheb(s, GOAL)
        if d <= 2:
            continue
        for to in moves_of(board, s):
            if 2 <= cheb(to, GOAL) < d:
                out.add(name(s) + name(to))
    if out:
        return out, "MARCH", "walking papers toward Ring 2"
    return None, None, "eviction position but no move advances the plan"
