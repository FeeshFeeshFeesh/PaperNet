# Ground-truth rules for the 9x9 rock-paper-scissors race game, transcribed from the engine
# source (sf19_ports/sf19nn/src/) so the tablebase solver and the engine cannot silently diverge.
# No sys.argv: every knob is a constant here, runnable straight from PyCharm.
#
# Sources, so this can be re-checked against the tree:
#   position.h:19-23   rules in the author's words: king steps onto an empty square or onto their
#                      prey; entering your goal corner wins; a side with no move (or no pieces)
#                      loses; 200 plies without a capture draw; NO repetition rule.
#   types.h:206-207    prey_of / predator_of      types.h:322  goal_square(W)=I9, goal_square(B)=A1
#   movegen.cpp:34-70  NON_EVASIONS               movegen.cpp:96-128  EVASIONS (the check-analogue)
#   position.h:233     reached_goal               position.cpp:247   has_move
#   position.h:237     threats()                  position.cpp:164   checkers() = threats(~us)
#   perft.h:28-50      the engine's perft counts NON_EVASIONS and returns 0 for a finished game

FILES, RANKS = 9, 9
NSQ          = FILES * RANKS
ROCK, PAPER, SCISSORS = 1, 2, 3
WHITE, BLACK = 0, 1
CAPTURE_CLOCK = 200
START_FEN = "9/4pr3/4spr2/5spr1/1PS3sp1/1RPS5/2RPS4/3RP4/9 w - - 0 1"

PREY     = {ROCK: SCISSORS, PAPER: ROCK,     SCISSORS: PAPER}
PREDATOR = {ROCK: PAPER,    PAPER: SCISSORS, SCISSORS: ROCK}
PIECE_CHAR = {(WHITE, ROCK): 'R', (WHITE, PAPER): 'P', (WHITE, SCISSORS): 'S',
              (BLACK, ROCK): 'r', (BLACK, PAPER): 'p', (BLACK, SCISSORS): 's'}
CHAR_PIECE = {v: k for k, v in PIECE_CHAR.items()}

def sq(f, r):   return r * FILES + f
def file_of(s): return s % FILES
def rank_of(s): return s // FILES

GOAL = {WHITE: sq(FILES - 1, RANKS - 1), BLACK: sq(0, 0)}   # I9 and A1 at 9x9;
# parameterised on FILES/RANKS so a smaller board can exercise 5-piece code paths.

def _steps():
    out = []
    for s in range(NSQ):
        f, r, n = file_of(s), rank_of(s), []
        for df in (-1, 0, 1):
            for dr in (-1, 0, 1):
                if df or dr:
                    nf, nr = f + df, r + dr
                    if 0 <= nf < FILES and 0 <= nr < RANKS:
                        n.append(sq(nf, nr))
        out.append(tuple(n))
    return out
STEPS = _steps()

def sq_name(s): return "abcdefghi"[file_of(s)] + str(rank_of(s) + 1)

class Pos:
    __slots__ = ("bd", "stm", "clock")
    def __init__(self, bd=None, stm=WHITE, clock=0):
        self.bd = bd if bd is not None else [None] * NSQ
        self.stm, self.clock = stm, clock
    def copy(self): return Pos(list(self.bd), self.stm, self.clock)

def parse_fen(fen):
    parts = fen.split()
    bd = [None] * NSQ
    for i, row in enumerate(parts[0].split('/')):
        r, f = RANKS - 1 - i, 0
        for ch in row:
            if ch.isdigit(): f += int(ch)
            else:
                bd[sq(f, r)] = CHAR_PIECE[ch]; f += 1
        assert f == FILES, "bad FEN rank %r" % row
    clock = int(parts[4]) if len(parts) > 4 else 0
    return Pos(bd, WHITE if parts[1] == 'w' else BLACK, clock)

def to_fen(p):
    rows = []
    for r in range(RANKS - 1, -1, -1):
        row, empt = "", 0
        for f in range(FILES):
            pc = p.bd[sq(f, r)]
            if pc is None: empt += 1
            else:
                if empt: row += str(empt); empt = 0
                row += PIECE_CHAR[pc]
        if empt: row += str(empt)
        rows.append(row)
    return "/".join(rows) + (" w " if p.stm == WHITE else " b ") + "- - %d 1" % p.clock

def pieces(p, c, pt=None):
    return [s for s, x in enumerate(p.bd) if x is not None and x[0] == c and (pt is None or x[1] == pt)]

def reached_goal(p, c):
    x = p.bd[GOAL[c]]
    return x is not None and x[0] == c

def gen_non_evasions(p):
    """movegen.cpp:34-70. Target is empties | prey; the goal square is just an empty square here."""
    us, out = p.stm, []
    for frm, x in enumerate(p.bd):
        if x is None or x[0] != us: continue
        prey = PREY[x[1]]
        for to in STEPS[frm]:
            y = p.bd[to]
            if y is None or (y[0] != us and y[1] == prey):
                out.append((frm, to))
    return out

def threats(p, c):
    """position.h:237. Pieces of c that could enter c's goal right now."""
    g   = GOAL[c]
    occ = p.bd[g]
    if occ is None:
        return [s for s in STEPS[g] if p.bd[s] is not None and p.bd[s][0] == c]
    if occ[0] == c:
        return []
    want = PREDATOR[occ[1]]
    return [s for s in STEPS[g] if p.bd[s] is not None and p.bd[s] == (c, want)]

def checkers(p):
    """position.cpp:168. The OPPONENT's pieces poised to enter their goal = our home."""
    return threats(p, 1 - p.stm)

def gen_evasions(p):
    """movegen.cpp:96-128. Win first, capture a lone threat, or block our home with a piece none
    of the threats can eat. If none of those exist every move loses and all are generated."""
    us, them = p.stm, 1 - p.stm
    thr, out = checkers(p), []
    for s in threats(p, us):                                   # win first
        out.append((s, GOAL[us]))
    if len(thr) == 1:                                          # capture a lone threat
        t    = thr[0]
        want = PREDATOR[p.bd[t][1]]
        for s in STEPS[t]:
            if p.bd[s] == (us, want): out.append((s, t))
    home = GOAL[them]                                          # block our home corner
    if p.bd[home] is None:
        thr_types = {p.bd[t][1] for t in thr}
        for pt in (ROCK, PAPER, SCISSORS):
            if PREDATOR[pt] not in thr_types:
                for s in STEPS[home]:
                    if p.bd[s] == (us, pt): out.append((s, home))
    return out if out else gen_non_evasions(p)

def gen_legal(p):
    """movegen.cpp:134-141."""
    return gen_evasions(p) if checkers(p) else gen_non_evasions(p)

def has_move(p, c):
    """position.cpp:247."""
    for frm, x in enumerate(p.bd):
        if x is None or x[0] != c: continue
        prey = PREY[x[1]]
        for to in STEPS[frm]:
            y = p.bd[to]
            if y is None or (y[0] != c and y[1] == prey): return True
    return False

def do_move(p, m):
    """position.h:291 -- a goal entry counts as a capture, so it zeroes the clock too."""
    frm, to = m
    q = p.copy()
    zeroing = q.bd[to] is not None or to == GOAL[p.stm]
    q.bd[to], q.bd[frm] = q.bd[frm], None
    q.clock = 0 if zeroing else q.clock + 1
    q.stm = 1 - p.stm
    return q

def perft(p, depth):
    """Mirrors perft.h exactly: NON_EVASIONS, and a finished game has no successors."""
    if reached_goal(p, 1 - p.stm): return 0
    mv = gen_non_evasions(p)
    if depth <= 1: return len(mv)
    return sum(perft(do_move(p, m), depth - 1) for m in mv)

if __name__ == "__main__":
    p = parse_fen(START_FEN)
    assert to_fen(p).split()[0] == START_FEN.split()[0], "FEN round-trip broken"
    print("start position round-trips; white pieces %d, black %d"
          % (len(pieces(p, WHITE)), len(pieces(p, BLACK))))
    for d in (1, 2, 3, 4):
        print("  perft(%d) = %d" % (d, perft(p, d)))
