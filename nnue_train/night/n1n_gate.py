"""HANDOFF 231cl: the one node-limited SPRT the queue left out -- n1_25k vs example.nnue.

`nodes_queue.py` (231cf) was registered with three jobs, one per night batch (s1n, s2n, s3n).
It has no job for `n1_25k`, and that is the net the teacher question turns on: sploppy's own
evaluator scored it .595 against `example.nnue` (+67 Elo) in sploppy's RUST search, while the
port's 500 ms gate could only say +3. Nothing has ever measured it in the port with enough
pairs to separate +10 from 0 -- and the port is where data gets generated, so the port's
verdict is the one that may promote a teacher (231ck).

Same instrument as the queue's jobs: a copy of `nodes_gate.py` by whole-line replacement,
20,000 nodes a move, SPRT[0, +10], its own seed, 2 threads. It runs only when nothing else
is matching or training -- the queue, the node-scaling calibration, every gate and every arm
must be gone first -- so it can neither bias nor be biased by a timed comparison.

Nothing ships from here. No sys.argv; constants below.
"""
import datetime
import os
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
OUT = os.path.join(S, "n1n_gate.log")
TAG = "n1n"
CAND_NET = os.path.join(S, "ab_n1_bin", "n1_25k.nnue")
NODES = 20000
PAIRS = 600
THREADS = 2
SEED = 20260944
POLL_S = 120
WAIT_FOR = ("nodes_queue.py", "nodes_gate_", "node_scaling.py", "[0-9]_gates.py",
            "sweep_s1.py", "wdl_arm.py", "pv_arm.py", "v9_arm.py", "mix_train")


def say(msg):
    line = "%s N1N %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def rewrite(src, dst, pairs):
    out, hits = [], [0] * len(pairs)
    for line in open(src).read().splitlines(True):
        for i, (prefix, new) in enumerate(pairs):
            if line.startswith(prefix):
                hits[i] += 1
                out.append(new + "\n")
                break
        else:
            out.append(line)
    for i, (prefix, _) in enumerate(pairs):
        assert hits[i] == 1, "%s: %d lines start with %r" % (dst, hits[i], prefix)
    open(dst, "w").write("".join(out))


def main():
    say("START pid %d: %s vs example.nnue at %d nodes, cap %d pairs" % (os.getpid(), TAG, NODES, PAIRS))
    if not os.path.exists(CAND_NET):
        say("STOP: %s is missing" % CAND_NET)
        return
    said = False
    while True:
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            break
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)
    script = os.path.join(S, "nodes_gate_%s.py" % TAG)
    rewrite(os.path.join(S, "nodes_gate.py"), script,
            [('TAG = ', 'TAG = "%s"' % TAG),
             ('CAND_NET = ', 'CAND_NET = "%s"' % CAND_NET),
             ('CAND_LABEL = ', 'CAND_LABEL = "SF19-NN + %s"' % os.path.basename(CAND_NET)),
             ('NODES = ', 'NODES = %d' % NODES),
             ('PAIRS = ', 'PAIRS = %d' % PAIRS),
             ('THREADS = ', 'THREADS = %d' % THREADS),
             ('SEED = ', 'SEED = %d' % SEED),
             ('SMOKE_ONLY = ', 'SMOKE_ONLY = False'),
             ('OUT = ', 'OUT = "%s"' % os.path.join(S, "nodes_gate_%s.log" % TAG))])
    say("running %s on %s" % (os.path.basename(script), os.path.basename(CAND_NET)))
    t0 = time.time()
    rc = subprocess.run([PY, "-u", script], capture_output=True, text=True)
    say("exit %d after %.1f min" % (rc.returncode, (time.time() - t0) / 60.0))
    for x in [x for x in rc.stdout.splitlines() if "VERDICT" in x or "FINAL" in x or "FAIL" in x][-4:]:
        say(x.split("NGATE ")[-1])
    say("DONE. Nothing shipped")


main()
