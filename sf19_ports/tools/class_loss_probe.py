"""When it loses the last piece of a type, does the engine NOTICE?

`untouchable_census.py` found that at ply 200, a side owning no piece of the type that beats
something the enemy holds draws about three times as often at matched material. A piece can only be
removed by the one type that beats it, so the loss is permanent: the moment your rock count hits
zero, every enemy scissors is untouchable to you for the rest of the game.

That is a correlation. This asks the causal question the fix depends on, and it forks the project:

  - if the engine's score barely moves across that capture, the EVAL is blind to the class loss and
    the fix is a feature;
  - if the score craters and the engine walked into it anyway, the eval knows and SEARCH is
    overriding it, and the fix is somewhere else entirely.

Building for the wrong branch costs days, so it is worth measuring before writing a line of C++.

Method. Replay every game from the referee stream. Find each capture that takes a side's LAST piece
of some type while the enemy still holds what that type beats -- the moment the class closes. Search
the position immediately before and immediately after with the deployed engine and net, always from
the perspective of the side that suffered the loss, and record the swing.

The control is the point. A capture costs material whatever else it does, so a score drop proves
nothing on its own. Every class-closing capture is therefore matched against ORDINARY captures of
the same piece type, taken from the same games, where the victim still had another piece of that
type. Same material change, same corpus; the only difference is whether a class closed. If the two
groups swing by the same amount, the eval does not price the class at all.

Writes only its own log directory. Plays no games.
"""
import collections
import json
import os
import random
import subprocess
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
S = _WORK

ENGINE = os.path.join(ROOT, "sf19_ports", "live", "stockfish")
NET    = os.path.join(ROOT, "nnue_train", "sploppy_repo", "runs", "sf2_ex_50k", "best.nnue")
OUT    = os.path.join(S, "class_loss_probe.jsonl")

STREAMS = [
    S + "/seq_port_sf2_ex_1s.3.jsonl",
    S + "/cloud_sprt_deep_0.jsonl",
    S + "/seq_port_s3_s2_20k.0.jsonl",
]

NODES       = 100000   # per position; the swing must be a real search score, not a raw net trace
THREADS     = 1        # the live bot owns this machine; stay out of its way
HASH_MB     = 64
MAX_EVENTS  = 220      # class-closing captures to probe
MAX_CONTROL = 220      # ordinary same-type captures to probe
SEED        = 20260924

ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}
TAKEN_BY = {v: k for k, v in BEATS.items()}


# ---------------------------------------------------------------- rules and replay
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


def fen(board):
    """The site FEN the seat expects: nine rows, rank 1 first, uppercase = Blue."""
    rows = []
    for r in range(9):
        row = ""
        for f in range(9):
            cell = board[r * 9 + f]
            row += "." if cell is None else (LETTER[cell[1]] if cell[0] == 0
                                             else LETTER[cell[1]].lower())
        rows.append(row)
    return "/".join(rows)


def counts(board, side, piece):
    return sum(1 for c in board if c and c[0] == side and c[1] == piece)


def scan(path, events, controls):
    """Walk every game, collecting class-closing captures and matched ordinary ones."""
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

    for k, (winner, plies, end) in meta.items():
        toks = opening.get(k, []) + moves.get(k, [])
        parsed = [parse(t) for t in toks]
        if any(p is None for p in parsed):
            continue
        board = initial()
        ok = True
        for i, (frm, to) in enumerate(parsed):
            side = i % 2
            src = board[frm]
            if src is None or src[0] != side:
                ok = False
                break
            dst = board[to]
            if dst is not None and (dst[0] == side or BEATS[src[1]] != dst[1]):
                ok = False
                break
            if dst is not None:
                victim, vtype = dst[0], dst[1]
                had = counts(board, victim, vtype)
                # does the victim still own something that beats an enemy piece on the board?
                closes = (had == 1 and
                          any(c and c[0] != victim and TAKEN_BY[c[1]] == vtype for c in board))
                rec = {
                    "stream": os.path.basename(path), "pair": k[0], "game": k[1], "ply": i,
                    "victim": victim, "piece": LETTER[vtype], "end": end,
                    "drew": winner is None,
                    "before": fen(board),
                    "before_stm": "b" if side == 0 else "r",
                }
                board[to], board[frm] = src, None
                rec["after"] = fen(board)
                rec["after_stm"] = "r" if side == 0 else "b"
                (events if closes else controls).append(rec)
                continue
            board[to], board[frm] = src, None
        if not ok:
            continue


# ---------------------------------------------------------------- the seat, driven as in a game
class Seat(object):
    """The real RPSI seat, so the engine sees exactly what it sees in a game."""

    def __init__(self, logdir):
        src = open(os.path.join(ROOT, "nnue_train", "night", "ab_nodes_seat.py")).read()
        for prefix, repl in (("ENGINE    =", 'ENGINE    = %r' % ENGINE),
                             ("NET       =", 'NET       = %r' % NET),
                             ("USE_NNUE  =", "USE_NNUE  = True"),
                             ("THREADS   =", "THREADS   = %d" % THREADS),
                             ("HASH_MB   =", "HASH_MB   = %d" % HASH_MB),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                             ("LOG_DIR   =", "LOG_DIR   = %r" % logdir)):
            out = []
            for line in src.split("\n"):
                out.append(repl if line.startswith(prefix) else line)
            src = "\n".join(out)
        self.path = os.path.join(logdir, "probe_seat.py")
        os.makedirs(logdir, exist_ok=True)
        open(self.path, "w").write(src)
        self.logdir = logdir
        self.p = subprocess.Popen([sys.executable, "-u", self.path],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.send("rpsi")
        self.expect("rpsiok")
        self.send("isready")
        self.expect("readyok")

    def send(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def expect(self, token):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("seat died waiting for %s" % token)
            if line.startswith(token):
                return line.strip()

    def score(self, board_fen, stm):
        """Search the position and return the seat's own logged centipawn score."""
        self.send("newgame")
        self.send("position fen %s %s" % (board_fen, stm))
        self.send("go nodes %d" % NODES)
        self.expect("bestmove")
        log = os.path.join(self.logdir, "fsf_seat_%d.log" % self.p.pid)
        last = None
        for line in open(log, errors="replace"):
            if "score=" in line:
                last = line
        if last is None:
            return None
        tok = last.split("score=")[1].split()[0]
        if tok.startswith("mate"):
            n = int(tok[4:])
            return 30000 if n >= 0 else -30000
        return int(tok)

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main():
    random.seed(SEED)
    events, controls = [], []
    for path in STREAMS:
        if os.path.exists(path):
            scan(path, events, controls)
    print("found %d class-closing captures and %d ordinary captures"
          % (len(events), len(controls)))

    # Match the control to the event set by piece type, so the two groups lose the same material.
    want = collections.Counter(e["piece"] for e in events)
    random.shuffle(events)
    events = events[:MAX_EVENTS]
    want = collections.Counter(e["piece"] for e in events)
    by_type = collections.defaultdict(list)
    for c in controls:
        by_type[c["piece"]].append(c)
    picked = []
    for t, n in want.items():
        random.shuffle(by_type[t])
        picked.extend(by_type[t][:min(n, MAX_CONTROL)])
    controls = picked
    print("probing %d events and %d type-matched controls at %d nodes"
          % (len(events), len(controls), NODES))
    sys.stdout.flush()

    seat = Seat(os.path.join(S, "class_loss_probe_logs"))
    out = open(OUT, "w")
    groups = [("class-closing", events), ("ordinary", controls)]
    swings = collections.defaultdict(list)
    t0 = time.time()
    done = 0
    for label, group in groups:
        for rec in group:
            # Always from the VICTIM's point of view. The seat reports the score for the side to
            # move, so flip when the side to move is not the victim.
            b = seat.score(rec["before"], rec["before_stm"])
            a = seat.score(rec["after"], rec["after_stm"])
            if b is None or a is None:
                continue
            victim_is_blue = rec["victim"] == 0
            bs = b if (rec["before_stm"] == "b") == victim_is_blue else -b
            as_ = a if (rec["after_stm"] == "b") == victim_is_blue else -a
            rec.update({"group": label, "before_cp": bs, "after_cp": as_, "swing": as_ - bs})
            out.write(json.dumps(rec) + "\n")
            out.flush()
            swings[(label, rec["piece"])].append(as_ - bs)
            swings[(label, "ALL")].append(as_ - bs)
            done += 1
            if done % 25 == 0:
                print("  %d/%d  %.0fs elapsed"
                      % (done, len(events) + len(controls), time.time() - t0))
                sys.stdout.flush()
    seat.close()
    out.close()

    print()
    print("SWING in the victim's own score across the capture (negative = it got worse)")
    print("  %-16s %-6s %6s %9s %9s" % ("group", "piece", "n", "mean cp", "median cp"))
    for label in ("class-closing", "ordinary"):
        for piece in ("ALL", "R", "P", "S"):
            v = swings.get((label, piece)) or []
            if not v:
                continue
            sv = sorted(v)
            print("  %-16s %-6s %6d %9.1f %9.1f"
                  % (label, piece, len(v), sum(v) / len(v), sv[len(sv) // 2]))
    a = swings.get(("class-closing", "ALL")) or []
    b = swings.get(("ordinary", "ALL")) or []
    if a and b:
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        print()
        print("  extra drop attributable to the class closing: %+.1f cp" % (ma - mb))
        print("  If that is near zero the eval does not price the class at all, and the fix is a")
        print("  FEATURE. If it is large, the eval already knows and the fix is in search.")
    print("\nrows written to %s" % OUT)


main()
