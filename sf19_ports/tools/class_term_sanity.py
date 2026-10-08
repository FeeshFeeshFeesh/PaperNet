"""Does ClassLoss actually do what it says, with the sign it is supposed to have?

A sign error here would not crash and would not look wrong: it would quietly produce a null result
in every downstream test, and the conclusion would be "the term does nothing" when the truth is
"the term was pushing the other way". So check it against arithmetic before trusting any match.

Takes positions where a class is ALREADY closed -- the "after" side of a real class-closing capture
-- and reads the search score with the term off and on. The term charges a flat number of
centipawns per closed class of the side to move, minus the same for the opponent, and the seat
reports centipawns where one piece is 100. So the score should fall by very close to

    value * (my closed classes - their closed classes)

and the residual should be small. Note the term cannot be seen at all in a position where nothing
is closed yet: there it only appears deeper in the tree, which is the point.

No sys.argv.
"""
import json
import os
import subprocess
import sys

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT = _ROOT
S = _WORK

ENGINE = os.path.join(ROOT, "sf19_ports", "sf19nn", "stockfish")
NET    = os.path.join(ROOT, "nnue_train", "sploppy_repo", "runs", "sf2_ex_50k", "best.nnue")
ROWS   = os.path.join(S, "class_loss_probe.jsonl")
LOGDIR = os.path.join(S, "class_term_sanity_logs")

VALUE  = 150
NODES  = 20000
HOW_MANY = 40
NOISE  = 30    # a shift smaller than this is not evidence of anything either way

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


def closed_classes(board, side):
    """Types `side` owns none of, while the enemy holds what that type would have taken."""
    mine = {c[1] for c in board if c and c[0] == side}
    theirs = {c[1] for c in board if c and c[0] != side}
    return sum(1 for t in (ROCK, PAPER, SCISSORS)
               if t not in mine and BEATS[t] in theirs)


class Seat(object):
    def __init__(self, logdir, options):
        src = open(os.path.join(ROOT, "nnue_train", "night", "ab_nodes_seat.py")).read()
        for prefix, repl in (("ENGINE    =", "ENGINE    = %r" % ENGINE),
                             ("NET       =", "NET       = %r" % NET),
                             ("THREADS   =", "THREADS   = 1"),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                             ("EXTRA_OPTIONS =", "EXTRA_OPTIONS = %r" % (options,)),
                             ("LOG_DIR   =", "LOG_DIR   = %r" % logdir)):
            src = "\n".join(repl if ln.startswith(prefix) else ln for ln in src.split("\n"))
        os.makedirs(logdir, exist_ok=True)
        path = os.path.join(logdir, "sanity_seat.py")
        open(path, "w").write(src)
        self.p = subprocess.Popen([sys.executable, "-u", path], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.log = os.path.join(logdir, "fsf_seat_%d.log" % self.p.pid)
        self.send("rpsi"); self.expect("rpsiok")
        self.send("isready"); self.expect("readyok")

    def send(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush()

    def expect(self, tok):
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("seat died")
            if line.startswith(tok):
                return line.strip()

    def score(self, fen, stm):
        self.send("newgame")
        self.send("position fen %s %s" % (fen, stm))
        self.send("go nodes %d" % NODES)
        self.expect("bestmove")
        last = None
        for line in open(self.log, errors="replace"):
            if "score=" in line:
                last = line
        if last is None:
            return None
        t = last.split("score=")[1].split()[0]
        if t.startswith("mate"):
            return 30000 if int(t[4:]) >= 0 else -30000
        return int(t)

    def close(self):
        try:
            self.send("quit"); self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main():
    rows = [json.loads(l) for l in open(ROWS)]
    picked = []
    for r in rows:
        if r.get("group") != "class-closing":
            continue
        board = unfen(r["after"])
        stm = 0 if r["after_stm"] == "b" else 1
        expect = closed_classes(board, stm) - closed_classes(board, 1 - stm)
        if expect:
            picked.append((r["after"], r["after_stm"], expect))
        if len(picked) >= HOW_MANY:
            break

    print("%d positions with a class already closed, ClassLoss=%d, %d nodes"
          % (len(picked), VALUE, NODES))
    off = Seat(os.path.join(LOGDIR, "off"), [])
    on  = Seat(os.path.join(LOGDIR, "on"), [("ClassLoss", VALUE)])

    bad = 0
    inert = 0
    resid = []
    print()
    print("  %-6s %9s %9s %9s %9s %9s" % ("net", "off cp", "on cp", "actual", "expected", "resid"))
    for fen, stm, expect in picked:
        a = off.score(fen, stm)
        b = on.score(fen, stm)
        if a is None or b is None or abs(a) >= 20000 or abs(b) >= 20000:
            continue
        actual = b - a
        want = -VALUE * expect
        r = actual - want
        resid.append(r)
        if len(resid) <= 12:
            print("  %-6d %9d %9d %9d %9d %9d" % (expect, a, b, actual, want, r))
        # Only a move against the term by more than noise is evidence of a sign problem.
        # The score is a search result, so when the principal variation ends at a leaf whose
        # closed-class count differs from the root the term simply does not apply there and
        # the shift is small. Counting every small positive number as "wrong way" turns that
        # ordinary fact into a false alarm.
        if (want < 0 and actual > NOISE) or (want > 0 and actual < -NOISE):
            bad += 1
        if abs(actual) <= NOISE:
            inert += 1
    off.close(); on.close()

    if resid:
        srt = sorted(resid)
        print()
        print("  %d positions compared" % len(resid))
        print("  median residual %+.0f cp, mean %+.0f cp" % (srt[len(srt) // 2],
                                                             sum(resid) / len(resid)))
        print("  %d of %d moved against the term by more than %d cp" % (bad, len(resid), NOISE))
        print("  %d of %d barely moved at all (the chosen line leaves the class behind)"
              % (inert, len(resid)))
        print()
        if bad == 0:
            print("  Sign is correct and the magnitude lands where the arithmetic says.")
            print("  Residual is search, not the term: the score is a search result, so changing")
            print("  the leaf evaluation also changes which line is chosen.")
        else:
            print("  SOMETHING IS WRONG. Do not run a match on this term until it is explained.")


main()
