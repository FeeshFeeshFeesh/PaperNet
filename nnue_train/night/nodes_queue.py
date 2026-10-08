"""HANDOFF 231cf. Runs a node-limited SPRT for each night net, after its move-time gate is done.

One job per trained net. A job waits until that net's gate script has written DONE
(so no move-time SPRT is running), then builds its own copy of nodes_gate.py by
whole-line replacement and runs it: 20k nodes a move, SPRT[0, +10], cap 1000 pairs.
Jobs run one at a time, and each match SIGSTOPs itself if a gate starts while it runs.

Nothing is shipped from here; it is a measurement with enough pairs to mean something.
No sys.argv; constants below. Progress lines go to OUT (append), prefixed "NQ".
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
OUT = os.path.join(S, "nodes_queue.log")
DONE_MARK = "DONE. Nothing shipped"
POLL_S = 120
NODES = 20000
PAIRS = 600
THREADS = 2      # a quarter of the machine: generation stays the critical path

# tag, gate log to wait on, frozen net the gate makes, seed
JOBS = [
    ("s1n", os.path.join(S, "sf1_gates.log"), os.path.join(S, "ab_s1_bin", "sf1_ex_50k.nnue"), 20260941),
    ("s2n", os.path.join(S, "sf2_gates.log"), os.path.join(S, "ab_s2_bin", "sf2_ex_50k.nnue"), 20260942),
    ("s3n", os.path.join(S, "sf3_gates.log"), os.path.join(S, "ab_s3_bin", "sf3_ex_50k.nnue"), 20260943),
]


def say(msg):
    line = "%s NQ %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def rewrite(src, dst, pairs):
    lines = open(src).read().splitlines(True)
    out, hits = [], [0] * len(pairs)
    for line in lines:
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


def wait_for(tag, log, net):
    said = False
    while True:
        done = os.path.exists(log) and DONE_MARK in open(log).read()
        if done and os.path.exists(net):
            return True
        if done and not os.path.exists(net):
            say("%s: gate finished but %s is missing -- skipping this job" % (tag, os.path.basename(net)))
            return False
        if not said:
            say("%s: waiting for %s to finish" % (tag, os.path.basename(log)))
            said = True
        time.sleep(POLL_S)


def main():
    say("START, %d jobs, %d nodes a move, cap %d pairs, %d in flight" % (len(JOBS), NODES, PAIRS, THREADS))
    for tag, log, net, seed in JOBS:
        if not wait_for(tag, log, net):
            continue
        script = os.path.join(S, "nodes_gate_%s.py" % tag)
        rewrite(os.path.join(S, "nodes_gate.py"), script,
                [('TAG = ', 'TAG = "%s"' % tag),
                 ('CAND_NET = ', 'CAND_NET = "%s"' % net),
                 ('CAND_LABEL = ', 'CAND_LABEL = "SF19-NN + %s"' % os.path.basename(net)),
                 ('NODES = ', 'NODES = %d' % NODES),
                 ('PAIRS = ', 'PAIRS = %d' % PAIRS),
                 ('THREADS = ', 'THREADS = %d' % THREADS),
                 ('SEED = ', 'SEED = %d' % seed),
                 ('SMOKE_ONLY = ', 'SMOKE_ONLY = False'),
                 ('OUT = ', 'OUT = "%s"' % os.path.join(S, "nodes_gate_%s.log" % tag))])
        say("%s: running %s on %s" % (tag, os.path.basename(script), os.path.basename(net)))
        t0 = time.time()
        rc = subprocess.run([PY, "-u", script], capture_output=True, text=True)
        tail = [x for x in rc.stdout.splitlines() if "VERDICT" in x or "FINAL" in x or "FAIL" in x]
        say("%s: exit %d after %.1f min" % (tag, rc.returncode, (time.time() - t0) / 60.0))
        for x in tail[-4:]:
            say("%s: %s" % (tag, x.split("NGATE ")[-1]))
    say("exit")


main()
