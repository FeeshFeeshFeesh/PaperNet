"""Does the eval price a CLASS CLOSURE, with everything else held fixed?

`class_loss_probe.py` searched real positions either side of the capture that takes a side's last
piece of some type, and found the victim's own score moves by about -6 cp -- statistically the same
as the -1 cp of an ordinary capture. That is suggestive but not clean: those captures happen at
median ply 166 while ordinary ones happen at ply 91, so phase is confounded with the effect.

This removes the confounder by never comparing two different positions in time. It takes ONE real
board and flips the TYPE of a single piece, leaving every square, every piece count and the side to
move identical. A piece is only ever removed by the one type that beats it, so flipping the last
rock of side A into a paper permanently closes A's access to every enemy scissors, while changing
nothing else about the material.

The flip is not free on its own -- a rock and a paper capture different things -- so the flip alone
is not the measurement. The measurement is a difference of differences:

    TREAT    A holds exactly ONE piece of type u.  Flipping it to w CLOSES the class.
    CONTROL  A holds TWO OR MORE of type u.        Flipping one to w changes nothing about classes.

Both groups undergo the identical physical edit, drawn from the same games and matched on the (u, w)
type pair. Whatever a rock-to-paper swap is worth in general cancels, and what survives is the price
the engine puts on the class closing. Near zero means the eval is blind to it and the fix is a
feature; a large negative number means the eval already knows and the fix belongs in search.

Reads the referee streams and drives the engine. Plays no games, writes only its own log directory.
"""
import collections
import json
import os
import random
import statistics
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
OUT    = os.path.join(S, "class_pair_probe2.jsonl")
LOGDIR = os.path.join(S, "class_pair_probe2_logs")

STREAMS = [
    S + "/seq_port_sf2_ex_1s.3.jsonl",
    S + "/cloud_sprt_deep_0.jsonl",
    S + "/seq_port_s3_s2_20k.0.jsonl",
]

NODES      = 200000   # a real search score, not a raw net trace
THREADS    = 1        # the live bot owns this machine
HASH_MB    = 64
MIN_PLY    = 100      # sample where the census found the effect, not the opening
MAX_TREAT  = 1400   # exact matching drops most of these
SEED       = 20260924

ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
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


def fen(board):
    rows = []
    for r in range(9):
        row = ""
        for f in range(9):
            c = board[r * 9 + f]
            row += "." if c is None else (LETTER[c[1]] if c[0] == 0 else LETTER[c[1]].lower())
        rows.append(row)
    return "/".join(rows)


def harvest(path, out):
    """Every legal position at or past MIN_PLY, with the side to move."""
    opening, moves = {}, collections.defaultdict(list)
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

    for k in moves:
        toks = opening.get(k, []) + moves[k]
        parsed = [parse(t) for t in toks]
        if any(p is None for p in parsed):
            continue
        board = initial()
        for i, (frm, to) in enumerate(parsed):
            side = i % 2
            src = board[frm]
            if src is None or src[0] != side:
                break
            dst = board[to]
            if dst is not None and (dst[0] == side or BEATS[src[1]] != dst[1]):
                break
            board[to], board[frm] = src, None
            if i + 1 >= MIN_PLY:
                out.append((list(board), (i + 1) % 2, os.path.basename(path), k, i + 1))


def candidates(positions):
    """Find every legal one-piece type flip and label it TREAT or CONTROL."""
    treat, control = [], []
    for board, stm, stream, k, ply in positions:
        a = stm                                   # measure from the side to move's point of view
        mine = collections.Counter(c[1] for c in board if c and c[0] == a)
        theirs = collections.Counter(c[1] for c in board if c and c[0] != a)
        for u in (ROCK, PAPER, SCISSORS):
            if not mine.get(u) or not theirs.get(BEATS[u]):
                continue                          # nothing to lose, or nothing it would set free
            for w in (ROCK, PAPER, SCISSORS):
                if w == u or not mine.get(w):
                    continue                      # flipping into a type we lack would OPEN a class
                goal = 80 if a == 0 else 0
                for sq, c in enumerate(board):
                    if not c or c[0] != a or c[1] != u:
                        continue
                    flipped = list(board)
                    flipped[sq] = (a, w)
                    # How advanced the flipped piece is. Taking the first square in board
                    # order silently made the control flip the piece nearest home -- the
                    # least important one -- while the treated group had no choice. That
                    # bias alone would manufacture the effect, so keep every candidate and
                    # match on this instead.
                    rec = {"stream": stream, "pair": k[0], "game": k[1], "ply": ply,
                           "u": LETTER[u], "w": LETTER[w], "pieces": sum(1 for x in board if x),
                           "dist": max(abs(sq // 9 - goal // 9), abs(sq % 9 - goal % 9)),
                           "stm": "b" if a == 0 else "r",
                           "orig": fen(board), "flip": fen(flipped)}
                    (treat if mine[u] == 1 else control).append(rec)
    return treat, control


class Seat(object):
    def __init__(self, logdir):
        src = open(os.path.join(ROOT, "nnue_train", "night", "ab_nodes_seat.py")).read()
        for prefix, repl in (("ENGINE    =", "ENGINE    = %r" % ENGINE),
                             ("NET       =", "NET       = %r" % NET),
                             ("THREADS   =", "THREADS   = %d" % THREADS),
                             ("HASH_MB   =", "HASH_MB   = %d" % HASH_MB),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                             ("LOG_DIR   =", "LOG_DIR   = %r" % logdir)):
            src = "\n".join(repl if ln.startswith(prefix) else ln for ln in src.split("\n"))
        os.makedirs(logdir, exist_ok=True)
        self.path = os.path.join(logdir, "pair_seat.py")
        open(self.path, "w").write(src)
        self.logdir = logdir
        self.p = subprocess.Popen([sys.executable, "-u", self.path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.send("rpsi"); self.expect("rpsiok")
        self.send("isready"); self.expect("readyok")
        self.log = os.path.join(logdir, "fsf_seat_%d.log" % self.p.pid)

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
        self.send("newgame")
        self.send("position fen %s %s" % (board_fen, stm))
        self.send("go nodes %d" % NODES)
        self.expect("bestmove")
        last = None
        for line in open(self.log, errors="replace"):
            if "score=" in line:
                last = line
        if last is None:
            return None
        tok = last.split("score=")[1].split()[0]
        if tok.startswith("mate"):
            return 30000 if int(tok[4:]) >= 0 else -30000
        return int(tok)

    def close(self):
        try:
            self.send("quit"); self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def summarise(rows, label):
    v = sorted(r["delta"] for r in rows)
    if not v:
        return None
    n = len(v)
    mean = sum(v) / n
    sd = statistics.pstdev(v) if n > 1 else 0.0
    print("  %-9s n=%4d   mean %+8.1f cp   median %+7.1f   sd %7.1f   sem %6.1f"
          % (label, n, mean, statistics.median(v), sd, sd / (n ** 0.5) if n else 0))
    return mean, sd, n


def main():
    random.seed(SEED)
    positions = []
    for path in STREAMS:
        if os.path.exists(path):
            harvest(path, positions)
    print("harvested %d positions at ply >= %d" % (len(positions), MIN_PLY))
    random.shuffle(positions)
    positions = positions[:9000]

    treat, control = candidates(positions)
    print("found %d class-closing flips and %d class-neutral flips" % (len(treat), len(control)))

    random.shuffle(treat)
    treat = treat[:MAX_TREAT]
    # Match the control to the treated set on the exact (u, w) type pair, so the raw cost of
    # turning a u into a w is the same on both sides and cancels in the difference.
    def key_of(r):
        return (r["u"], r["w"], r["pieces"], r["dist"])

    by_pair = collections.defaultdict(list)
    for r in control:
        by_pair[key_of(r)].append(r)
    for v in by_pair.values():
        random.shuffle(v)
    # Exact 1:1 matching on (type lost, type gained, total pieces, how advanced the flipped
    # piece is). A treated flip with no exact partner is DROPPED rather than matched loosely:
    # an unmatched pair is exactly how the first run's bias got in.
    matched_treat, control = [], []
    for r in treat:
        pool = by_pair.get(key_of(r))
        if pool:
            matched_treat.append(r)
            control.append(pool.pop())
    dropped = len(treat) - len(matched_treat)
    treat = matched_treat
    print("  %d treated flips had no exact (u, w, pieces, dist) partner and were dropped"
          % dropped)
    print("probing %d treated and %d matched control flips at %d nodes (%d searches)"
          % (len(treat), len(control), NODES, 2 * (len(treat) + len(control))))
    sys.stdout.flush()

    seat = Seat(LOGDIR)
    out = open(OUT, "w")
    kept = {"treat": [], "control": []}
    t0, done, total = time.time(), 0, len(treat) + len(control)
    for label, group in (("treat", treat), ("control", control)):
        for rec in group:
            o = seat.score(rec["orig"], rec["stm"])
            f = seat.score(rec["flip"], rec["stm"])
            done += 1
            if done % 50 == 0:
                print("  %d/%d  %.0fs" % (done, total, time.time() - t0)); sys.stdout.flush()
            if o is None or f is None:
                continue
            rec.update({"group": label, "orig_cp": o, "flip_cp": f, "delta": f - o})
            out.write(json.dumps(rec) + "\n"); out.flush()
            if abs(o) < 20000 and abs(f) < 20000:      # mate scores are a different measurement
                kept[label].append(rec)
    seat.close(); out.close()

    print()
    print("DELTA = score after the flip minus before, from the flipping side's point of view")
    print("        (non-mate positions only; negative means the engine thinks it got worse)")
    t = summarise(kept["treat"], "treat")
    c = summarise(kept["control"], "control")
    if t and c:
        (mt, st, nt), (mc, sc, nc) = t, c
        diff = mt - mc
        se = ((st * st / nt) + (sc * sc / nc)) ** 0.5
        print()
        print("  PRICE THE ENGINE PUTS ON CLOSING THE CLASS: %+.1f cp  (+-%.1f, 1 sem)" % (diff, se))
        if se > 0:
            print("  that is %.1f standard errors from zero" % (abs(diff) / se))
        print()
        print("  Near zero  -> the eval is blind to the class; the fix is an EVAL FEATURE.")
        print("  Strongly negative -> the eval already knows; the fix belongs in SEARCH.")

    print()
    print("  by type lost (treated group only)")
    for u in ("R", "P", "S"):
        v = [r["delta"] for r in kept["treat"] if r["u"] == u]
        if v:
            print("    last %s gone, enemy %s set free: n=%3d  mean %+7.1f"
                  % (u, LETTER[BEATS["RPS".index(u)]], len(v), sum(v) / len(v)))
    print("\nrows written to %s" % OUT)


main()
