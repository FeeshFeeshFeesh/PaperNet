"""When its LAST piece of a type is attacked, does the engine actually save it?

The two static probes disagree about whether the evaluation prices a permanently closed capture
class, and a static score is the wrong thing to argue about anyway. What the conversion defect is
made of is MOVES. So ask the behavioural question directly.

Find real positions where the side to move has a piece of type u standing next to an enemy piece of
the one type that can take it, so it will be gone next move unless something is done. Ask the engine
for its best move and see whether the piece ends up safe.

  TREAT    that is the side's LAST piece of type u, and the enemy holds pieces of prey_of(u).
           Losing it closes the class permanently: those enemy pieces become untouchable forever.
  CONTROL  the side has two or more of type u, so losing this one closes nothing.

Same threat, same rescue options, same engine. If the engine saves its last rock no more often than
it saves a spare one, then whatever the eval says statically, the SEARCH is not acting on it, and
the fix does not belong in the evaluation. If it saves the last one far more often, the engine
already plays this correctly and the conversion defect is somewhere else entirely.

Reads the referee streams and drives the engine. Plays no games.
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
OUT    = os.path.join(S, "class_save_probe.jsonl")
LOGDIR = os.path.join(S, "class_save_probe_logs")

STREAMS = [
    S + "/seq_port_sf2_ex_1s.3.jsonl",
    S + "/cloud_sprt_deep_0.jsonl",
    S + "/seq_port_s3_s2_20k.0.jsonl",
]

NODES     = 200000
THREADS   = 1
HASH_MB   = 64
MIN_PLY   = 100
MAX_EACH  = 300
HARVEST_KEEP = 0.05   # fraction of eligible positions to keep (memory, not bias)
SEED      = 20260924

ROCK, PAPER, SCISSORS = 0, 1, 2
LETTER = "RPS"
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}
TAKEN_BY = {v: k for k, v in BEATS.items()}      # type t is removed only by TAKEN_BY[t]


def mirror_anti(sq):
    r, f = divmod(sq, 9)
    return (8 - f) * 9 + (8 - r)


def initial():
    b = [None] * 81
    for piece, sqs in ((ROCK, (28, 20, 12)), (PAPER, (37, 29, 21, 13)), (SCISSORS, (38, 30, 22))):
        for s in sqs:
            b[s] = (0, piece)
            b[mirror_anti(s)] = (1, piece)
    return b


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


def name(sq):
    return "abcdefghi"[sq % 9] + str(sq // 9 + 1)


def neighbours(sq):
    r, f = divmod(sq, 9)
    for dr in (-1, 0, 1):
        for df in (-1, 0, 1):
            if dr or df:
                rr, ff = r + dr, f + df
                if 0 <= rr < 9 and 0 <= ff < 9:
                    yield rr * 9 + ff


def attacked(board, sq):
    """Is the piece on sq standing next to something that can remove it?"""
    c = board[sq]
    return any(board[n] and board[n][0] != c[0] and BEATS[board[n][1]] == c[1]
               for n in neighbours(sq))


def harvest(path, out):
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
        parsed = [parse(t) for t in opening.get(k, []) + moves[k]]
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
            # Subsample here, not after. Keeping every position means ~284k board copies
            # held at once, which is over a gigabyte on this 8 GB machine and invites the
            # host memory guard. A 5% random sample is plenty and is still unbiased.
            if i + 1 >= MIN_PLY and random.random() < HARVEST_KEEP:
                out.append((list(board), (i + 1) % 2, os.path.basename(path), k, i + 1))


def cases(positions):
    treat, control = [], []
    for board, a, stream, k, ply in positions:
        mine = collections.Counter(c[1] for c in board if c and c[0] == a)
        theirs = collections.Counter(c[1] for c in board if c and c[0] != a)
        for sq, c in enumerate(board):
            if not c or c[0] != a:
                continue
            u = c[1]
            if not attacked(board, sq):
                continue
            if not theirs.get(BEATS[u]):
                continue          # losing it would set nothing free; not the case under test
            rec = {"stream": stream, "pair": k[0], "game": k[1], "ply": ply,
                   "u": LETTER[u], "sq": name(sq), "square": sq,
                   "have": mine[u], "pieces": sum(1 for x in board if x),
                   "stm": "b" if a == 0 else "r", "side": a, "fen": fen(board),
                   "board": [list(x) if x else None for x in board]}
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
        self.path = os.path.join(logdir, "save_seat.py")
        open(self.path, "w").write(src)
        self.p = subprocess.Popen([sys.executable, "-u", self.path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.send("rpsi"); self.expect("rpsiok")
        self.send("isready"); self.expect("readyok")

    def send(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()

    def expect(self, token):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("seat died waiting for %s" % token)
            if line.startswith(token):
                return line.strip()

    def best(self, board_fen, stm):
        self.send("newgame")
        self.send("position fen %s %s" % (board_fen, stm))
        self.send("go nodes %d" % NODES)
        return self.expect("bestmove").split()[1]

    def close(self):
        try:
            self.send("quit"); self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main():
    random.seed(SEED)
    positions = []
    for p in STREAMS:
        if os.path.exists(p):
            harvest(p, positions)
    random.shuffle(positions)
    positions = positions[:9000]
    treat, control = cases(positions)
    print("threatened pieces found: %d last-of-type, %d with a spare" % (len(treat), len(control)))

    random.shuffle(treat); random.shuffle(control)
    treat, control = treat[:MAX_EACH], control[:MAX_EACH]
    print("probing %d + %d at %d nodes" % (len(treat), len(control), NODES)); sys.stdout.flush()

    seat = Seat(LOGDIR)
    out = open(OUT, "w")
    tally = collections.defaultdict(lambda: [0, 0])
    t0, done = time.time(), 0
    for label, group in (("last-of-type", treat), ("has-a-spare", control)):
        for rec in group:
            mv = seat.best(rec["fen"], rec["stm"])
            done += 1
            if done % 50 == 0:
                print("  %d/%d  %.0fs" % (done, len(treat) + len(control), time.time() - t0))
                sys.stdout.flush()
            frm = parse(mv) or (None, None)
            if frm[0] is None:
                continue
            board = [tuple(x) if x else None for x in rec["board"]]
            src = board[frm[0]]
            if src is None:
                continue
            board[frm[1]], board[frm[0]] = src, None
            u = "RPS".index(rec["u"])
            still = [s for s, c in enumerate(board) if c and c[0] == rec["side"] and c[1] == u]
            safe = bool(still) and not all(attacked(board, s) for s in still)
            rec.pop("board")
            rec.update({"group": label, "best": mv, "saved": safe,
                        "moved_it": frm[0] == rec["square"]})
            out.write(json.dumps(rec) + "\n"); out.flush()
            t = tally[label]
            t[0] += 1; t[1] += 1 if safe else 0
    seat.close(); out.close()

    print()
    print("Did the engine leave a piece of that type standing safe?")
    print("  %-14s %6s %8s %9s" % ("group", "n", "saved", "rate"))
    rates = {}
    for label in ("last-of-type", "has-a-spare"):
        n, s = tally[label]
        if n:
            rates[label] = 100.0 * s / n
            print("  %-14s %6d %8d %8.1f%%" % (label, n, s, rates[label]))
    if len(rates) == 2:
        gap = rates["last-of-type"] - rates["has-a-spare"]
        print()
        print("  EXTRA URGENCY THE ENGINE SHOWS FOR ITS LAST ONE: %+.1f points" % gap)
        print("  Near zero -> the engine does not defend the class in its MOVES, whatever its")
        print("  static score says, and the fix belongs in search, not in a new eval term.")
    print("\nrows written to %s" % OUT)


main()
