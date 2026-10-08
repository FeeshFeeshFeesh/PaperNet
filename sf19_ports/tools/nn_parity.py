"""Parity gates for the RPSNNUE1 port in SF19 (sf19nn).

Gate A (refresh parity): on random playout positions (both sides to move, random
capture clocks), the port's static net evaluation must EQUAL sploppy's own
Rust `Model::refresh` + `evaluate` (examples/fen_oracle.rs), exactly.
Gate B (incremental parity): short searches with NetVerify on, which recompute
every evaluation from scratch and abort on any accumulator mismatch.
No sys.argv; constants below.
"""
import random
import subprocess

from rps_diff import START, parse, to_fen, moves, terminal, cheb, goal
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
ENGINE = S + "/sf19nn/stockfish"
ORACLE = S + "/sploppy_repo/target/release/examples/fen_oracle"
NET = _ROOT + "/project_files/sploppy_engine/example.nnue"
N_GAMES = 120
SAMPLE_EVERY = 2
SEED = 20260918
GOAL_BIAS = 0.6
VERIFY_POSITIONS = 60     # gate B: positions searched with NetVerify
VERIFY_DEPTH = 9


class Engine:
    def __init__(self):
        self.p = subprocess.Popen([ENGINE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.send("setoption name EvalFile value " + NET)
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
                raise RuntimeError("engine died; last lines: %s" % lines[-5:])
            ln = ln.strip()
            if ln.startswith(prefix):
                return ln, lines
            lines.append(ln)

    def net_eval(self, fen):
        self.send("position fen " + fen)
        self.send("eval")
        ln, _ = self.until("Net evaluation")
        return int(ln.split()[2])


def positions(rng):
    out = []
    for g in range(N_GAMES):
        board, white, clock = parse(START)
        ply = 0
        while terminal(board, white, clock) is None and ply <= 400:
            if ply % SAMPLE_EVERY == 0:
                c = clock if rng.random() < 0.5 else rng.randrange(0, 200)
                out.append(to_fen(board, white, c))
            ms = moves(board, white)
            g_goal = goal(white)
            good = [m for m in ms if board[m[1]] is not None or cheb(m[1], g_goal) < cheb(m[0], g_goal)]
            a, b = rng.choice(good if good and rng.random() < GOAL_BIAS else ms)
            clock = 0 if board[b] is not None else clock + 1
            board[b], board[a] = board[a], None
            white = not white
            ply += 1
    return out


def main():
    rng = random.Random(SEED)
    fens = positions(rng)
    print("positions", len(fens), flush=True)

    oracle = subprocess.run([ORACLE], input="\n".join(fens) + "\n", capture_output=True, text=True)
    ref = [int(x) for x in oracle.stdout.split()]
    assert len(ref) == len(fens), (len(ref), oracle.stderr[-500:])

    eng = Engine()
    mism = 0
    for i, fen in enumerate(fens):
        v = eng.net_eval(fen)
        if v != ref[i]:
            mism += 1
            if mism <= 5:
                print("MISMATCH gate A", fen, "port", v, "rust", ref[i], flush=True)
    print("GATE A positions %d mismatches %d  eval range [%d, %d]"
          % (len(fens), mism, min(ref), max(ref)), flush=True)

    eng.send("setoption name NetVerify value true")
    picks = rng.sample(fens, VERIFY_POSITIONS)
    nodes = 0
    for fen in picks:
        eng.send("position fen " + fen)
        eng.send("go depth %d" % VERIFY_DEPTH)
        ln, lines = eng.until("bestmove")
        for x in lines:
            if "NETVERIFY" in x:
                print(x, flush=True)
        info = [x for x in lines if " nodes " in x]
        if info:
            t = info[-1].split()
            nodes += int(t[t.index("nodes") + 1])
    print("GATE B positions %d searched nodes %d  (an accumulator mismatch aborts the engine)"
          % (len(picks), nodes), flush=True)
    eng.send("quit")


if __name__ == "__main__":
    main()
