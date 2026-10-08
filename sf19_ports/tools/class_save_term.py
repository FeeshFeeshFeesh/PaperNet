"""Does ClassLoss change the engine's MOVES? Re-run the same positions with the term on.

Theory about what an eval term ought to do is cheap. Before spending hours of match time on it, put
it in front of the exact positions where it is supposed to matter and see whether a single move
changes. These are the same recorded positions as `class_save_probe.py` -- the side to move has a
piece standing next to something that can take it, and in the TREAT half it is the last piece of its
type, so losing it permanently frees every enemy piece that it alone could have removed.

The baseline engine saved its last one 90.4% of the time and a spare 92.7%: no extra urgency. If the
term works, the treated rate should rise and the control rate should barely move. If nothing moves,
the term is inert at this strength and the value needs raising or the shape is wrong -- either way
that is worth knowing in minutes rather than after a match.

Each position is searched with the term OFF and ON in the same process, so the comparison is exact
and paired.

No sys.argv.
"""
import collections
import queue
import threading
import json
import os
import subprocess
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT = _ROOT
S = _WORK

ENGINE = os.path.join(ROOT, "sf19_ports", "sf19nn", "stockfish")   # the one with the new options
NET    = os.path.join(ROOT, "nnue_train", "sploppy_repo", "runs", "sf2_ex_50k", "best.nnue")
ROWS   = os.path.join(S, "class_save_probe.jsonl")
OUT    = os.path.join(S, "class_save_term.jsonl")
LOGDIR = os.path.join(S, "class_save_term_logs")

OPTION  = "ClassLoss"
VALUES  = [0, -150, -330]  # 0 must reproduce the baseline exactly, or something is wrong.
                           # -330 cancels exactly the charge the pair probe measured:
                           # printed cp = internal*100/StoneValue, so 330 cp = 3.30 stones,
                           # and ClassLoss is in those same printed-cp units.
NODES   = 200000
SEARCH_TIMEOUT = 90   # a search that takes 450x its median is stalled, not thinking
RETRIES        = 1    # one seat restart per stalled position, then give up on it
THREADS = 1

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
    for frm, to in moves(board, side):
        nb = list(board)
        nb[to], nb[frm] = nb[frm], None
        mine = [s for s, c in enumerate(nb) if c and c[0] == side and c[1] == u]
        if mine and not all(attacked(nb, s) for s in mine):
            return True
    return False


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


class Seat(object):
    def __init__(self, logdir, options):
        src = open(os.path.join(ROOT, "nnue_train", "night", "ab_nodes_seat.py")).read()
        for prefix, repl in (("ENGINE    =", "ENGINE    = %r" % ENGINE),
                             ("NET       =", "NET       = %r" % NET),
                             ("THREADS   =", "THREADS   = %d" % THREADS),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                             ("EXTRA_OPTIONS =", "EXTRA_OPTIONS = %r" % (options,)),
                             ("LOG_DIR   =", "LOG_DIR   = %r" % logdir)):
            src = "\n".join(repl if ln.startswith(prefix) else ln for ln in src.split("\n"))
        os.makedirs(logdir, exist_ok=True)
        path = os.path.join(logdir, "term_seat.py")
        open(path, "w").write(src)
        self.path = path
        self._spawn()

    def _spawn(self):
        self.p = subprocess.Popen([sys.executable, "-u", self.path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        # A dedicated reader means a stalled engine shows up as a timeout instead of
        # wedging the whole run.  On an 8 GB box under swap pressure a `go` can block
        # in a page fault for minutes with the process at 0% CPU; that looks identical
        # to a hang, and without this it silently eats the rest of the run.
        self.q = queue.Queue()
        t = threading.Thread(target=self._reader, args=(self.p,), daemon=True)
        t.start()
        self.send("rpsi"); self.expect("rpsiok")
        self.send("isready"); self.expect("readyok")

    def _reader(self, proc):
        for line in proc.stdout:
            self.q.put(line)
        self.q.put(None)

    def send(self, line):
        try:
            self.p.stdin.write(line + "\n"); self.p.stdin.flush()
        except (BrokenPipeError, ValueError):
            raise SeatStalled("seat stdin closed")

    def expect(self, token, timeout=SEARCH_TIMEOUT):
        # ABSOLUTE deadline, not a per-line one.  A per-line timeout looks like a
        # watchdog and is not: any seat that emits a non-matching line more often
        # than `timeout` keeps resetting it and the loop never exits.  Verified --
        # a chattering stand-in seat ran past a 10s hard deadline with timeout=3.
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise SeatStalled("no %s within %ds" % (token, timeout))
            try:
                line = self.q.get(timeout=remaining)
            except queue.Empty:
                raise SeatStalled("no %s within %ds" % (token, timeout))
            if line is None:
                raise SeatStalled("seat died waiting for %s" % token)
            if line.startswith(token):
                return line.strip()

    def restart(self):
        try:
            self.p.kill(); self.p.wait(timeout=5)
        except Exception:
            pass
        self._spawn()

    def best(self, fen, stm):
        self.send("newgame")
        self.send("position fen %s %s" % (fen, stm))
        self.send("go nodes %d" % NODES)
        return self.expect("bestmove").split()[1]

    def close(self):
        try:
            self.send("quit"); self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


class SeatStalled(RuntimeError):
    pass


def saved_after(rec, mv):
    board = unfen(rec["fen"])
    fr = parse(mv)
    if fr is None or board[fr[0]] is None:
        return None
    board[fr[1]], board[fr[0]] = board[fr[0]], None
    u = LETTER.index(rec["u"])
    mine = [s for s, c in enumerate(board) if c and c[0] == rec["side"] and c[1] == u]
    return bool(mine) and not all(attacked(board, s) for s in mine)


def main():
    rows = [json.loads(l) for l in open(ROWS)]
    # Only cases where a rescue exists; a trapped piece cannot show a change of mind.
    rows = [r for r in rows
            if rescuable(unfen(r["fen"]), r["side"], LETTER.index(r["u"]))]
    print("%d rescuable positions (%d treated, %d control)"
          % (len(rows),
             sum(1 for r in rows if r["group"] == "last-of-type"),
             sum(1 for r in rows if r["group"] == "has-a-spare")))
    print("searching each at %s in {%s} x %d nodes"
          % (OPTION, ", ".join(str(v) for v in VALUES), NODES))
    sys.stdout.flush()

    out = open(OUT, "w")
    results = {}
    for val in VALUES:
        seat = Seat(os.path.join(LOGDIR, "v%d" % val), [(OPTION, val)] if val else [])
        tally = collections.defaultdict(lambda: [0, 0])
        changed = collections.Counter()
        t0 = time.time()
        stalled = []
        for i, rec in enumerate(rows):
            for attempt in range(RETRIES + 1):
                try:
                    mv = seat.best(rec["fen"], rec["stm"])
                    break
                except SeatStalled as e:
                    print("  STALL at row %d (%s); restarting seat" % (i, e))
                    sys.stdout.flush()
                    seat.restart()
            else:
                stalled.append(i)
                continue
            s = saved_after(rec, mv)
            if s is None:
                continue
            t = tally[rec["group"]]
            t[0] += 1; t[1] += 1 if s else 0
            if mv != rec["best"]:
                changed[rec["group"]] += 1
            out.write(json.dumps({"group": rec["group"], "u": rec["u"], "fen": rec["fen"],
                                  OPTION.lower(): val, "best": mv, "saved": s,
                                  "base_best": rec["best"], "base_saved": rec["saved"]}) + "\n")
            if (i + 1) % 100 == 0:
                print("  %s=%d  %d/%d  %.0fs" % (OPTION, val, i + 1, len(rows), time.time() - t0))
                sys.stdout.flush()
        seat.close()
        if stalled:
            print("  %d position(s) dropped to stalls: %s" % (len(stalled), stalled[:20]))
        results[val] = (dict(tally), dict(changed))
        out.flush()
    out.close()

    print()
    print("SAVE RATE for the attacked piece, by %s" % OPTION)
    print("  %-8s %22s %22s %12s" % (OPTION, "last-of-type", "has-a-spare", "gap"))
    for val in VALUES:
        tally, changed = results[val]
        cells = []
        for g in ("last-of-type", "has-a-spare"):
            n, s = tally.get(g, [0, 0])
            cells.append((100.0 * s / n if n else 0.0, n, s))
        gap = cells[0][0] - cells[1][0]
        print("  %-8d %13.1f%% (%d/%d) %13.1f%% (%d/%d) %11.1f"
              % (val, cells[0][0], cells[0][2], cells[0][1],
                 cells[1][0], cells[1][2], cells[1][1], gap))
    print()
    print("  moves that differ from the baseline run")
    for val in VALUES:
        _, changed = results[val]
        print("    %s=%-4d  last-of-type %3d, has-a-spare %3d"
              % (OPTION, val, changed.get("last-of-type", 0), changed.get("has-a-spare", 0)))
    print()
    print("  %s=0 must reproduce the baseline (0 changed moves, 90.4%% / 92.7%%)." % OPTION)
    print("  If it does not, the comparison is not measuring the term.")
    print("\nrows written to %s" % OUT)


main()
