"""Differential check of SF19-RPS move generation against Fairy-SF (rps-v6) and a
tiny independent Python rules oracle, on random goal-biased playout positions.

For each non-terminal sampled position: the perft-1 move SET must match in all
three, and perft-3 node counts must match between the two engines. Terminal
rules are checked in Python only (and via search in the next step).
No sys.argv; constants below.
"""
import random
import subprocess
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
SF19 = S + "/sf19rps/stockfish"
FAIRY = S + "/Fairy-Stockfish/src/stockfish"
N_GAMES = 150
SAMPLE_EVERY = 3
PERFT_DEEP = 3
SEED = 20260917
GOAL_BIAS = 0.6          # chance to pick a move that gets closer to goal / captures
START = "9/4pr3/4spr2/5spr1/1PS3sp1/1RPS5/2RPS4/3RP4/9 w - - 0 1"

PREY = {"r": "s", "s": "p", "p": "r"}


def parse(fen):
    f = fen.split()
    board = [None] * 81
    for i, row in enumerate(f[0].split("/")):
        r = 8 - i
        c = 0
        for ch in row:
            if ch.isdigit():
                c += int(ch)
            else:
                board[r * 9 + c] = ch
                c += 1
    return board, f[1] == "w", int(f[4])


def to_fen(board, white, clock):
    rows = []
    for r in range(8, -1, -1):
        s, run = "", 0
        for c in range(9):
            pc = board[r * 9 + c]
            if pc is None:
                run += 1
            else:
                if run:
                    s += str(run)
                    run = 0
                s += pc
        if run:
            s += str(run)
        rows.append(s)
    return "/".join(rows) + (" w" if white else " b") + " - - %d 1" % clock


def name(i):
    return "abcdefghi"[i % 9] + str(i // 9 + 1)


def mine(pc, white):
    return pc is not None and pc.isupper() == white


def moves(board, white):
    out = []
    for s in range(81):
        pc = board[s]
        if not mine(pc, white):
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if not dr and not dc:
                    continue
                r, c = s // 9 + dr, s % 9 + dc
                if not (0 <= r < 9 and 0 <= c < 9):
                    continue
                t = r * 9 + c
                q = board[t]
                if q is None or (not mine(q, white) and PREY[pc.lower()] == q.lower()):
                    out.append((s, t))
    return out


def goal(white):
    return 80 if white else 0


def terminal(board, white, clock):
    """Result for the side to move: 'loss', 'draw' or None."""
    if board[goal(not white)] is not None and mine(board[goal(not white)], not white):
        return "loss"
    if not moves(board, white):
        return "loss"
    if clock >= 200:
        return "draw"
    return None


def cheb(a, b):
    return max(abs(a // 9 - b // 9), abs(a % 9 - b % 9))


class Eng:
    def __init__(self, path, fairy):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        if fairy:
            self.send("uci")
            self.until("uciok")
            self.send("setoption name UCI_Variant value rps-v6")
            self.send("setoption name Use NNUE value false")
        self.send("isready")
        self.until("readyok")

    def send(self, s):
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()

    def until(self, prefix):
        lines = []
        while True:
            ln = self.p.stdout.readline()
            if not ln:
                raise RuntimeError("engine died")
            ln = ln.strip()
            if ln.startswith(prefix):
                return ln, lines
            lines.append(ln)

    def perft(self, fen, depth):
        self.send("position fen " + fen)
        self.send("go perft %d" % depth)
        last, lines = self.until("Nodes searched")
        mv = set()
        for ln in lines:
            if ":" in ln and len(ln.split(":")[0]) == 4:
                mv.add(ln.split(":")[0])
        return int(last.split(":")[1]), mv


def main():
    rng = random.Random(SEED)
    sf, fy = Eng(SF19, False), Eng(FAIRY, True)
    checked = mism = deep_mism = 0
    kinds = {"threat": 0, "capture_avail": 0}
    for g in range(N_GAMES):
        board, white, clock = parse(START)
        ply = 0
        while True:
            t = terminal(board, white, clock)
            if t is not None:
                break
            ms = moves(board, white)
            if ply % SAMPLE_EVERY == 0:
                fen = to_fen(board, white, clock)
                py = {name(a) + name(b) for a, b in ms}
                n1, m1 = sf.perft(fen, 1)
                n2, m2 = fy.perft(fen, 1)
                checked += 1
                if any(board[b] is not None for a, b in ms):
                    kinds["capture_avail"] += 1
                if any(b == goal(white) for a, b in ms):
                    kinds["threat"] += 1
                if not (py == m1 == m2):
                    mism += 1
                    if mism <= 5:
                        print("MISMATCH perft1", fen, "py-sf", sorted(py - m1), "sf-py", sorted(m1 - py),
                              "py-fairy", sorted(py - m2), "fairy-py", sorted(m2 - py), flush=True)
                d1, _ = sf.perft(fen, PERFT_DEEP)
                d2, _ = fy.perft(fen, PERFT_DEEP)
                if d1 != d2:
                    deep_mism += 1
                    if deep_mism <= 5:
                        print("MISMATCH perft%d" % PERFT_DEEP, fen, "sf19", d1, "fairy", d2, flush=True)
            g_goal = goal(white)
            good = [m for m in ms if board[m[1]] is not None or cheb(m[1], g_goal) < cheb(m[0], g_goal)]
            a, b = rng.choice(good if good and rng.random() < GOAL_BIAS else ms)
            clock = 0 if board[b] is not None else clock + 1
            board[b], board[a] = board[a], None
            white = not white
            ply += 1
            if ply > 400:
                break
        if (g + 1) % 50 == 0:
            print("games %d  positions %d  perft1 mismatches %d  perft%d mismatches %d  %s"
                  % (g + 1, checked, mism, PERFT_DEEP, deep_mism, kinds), flush=True)
    print("DONE positions %d  perft1 mismatches %d  perft%d mismatches %d" % (checked, mism, PERFT_DEEP, deep_mism))


if __name__ == "__main__":
    main()
