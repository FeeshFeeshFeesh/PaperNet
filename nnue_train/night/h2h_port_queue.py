"""HANDOFF 231ed: the queue in front of nodes_gate_h2h_port.py, and why it is a separate file.

The port head-to-head is named `nodes_gate_h2h_port.py` because that prefix is this tree's token
for "a node-limited match holds the cores": regime_test.py's already-running copy waits for it
before each of its node columns, and holds its 500 ms phase B while it plays. A running job is
the text it was launched with (231dz), so regime_test.py's wait lists can no longer be edited --
which means the ORDER of these two jobs is decided entirely by which one starts first.

231ed puts the port head-to-head first, and the reason is not convenience:

  * it is 300 pairs (+-35 Elo) where regime_test's columns are 100 (+-45 Elo);
  * it is paired game for game with h2h_s1_s2.py, which lands minutes before it starts, and the
    pairing is only readable once both halves exist;
  * it re-tests a HEADLINE conclusion in the engine that ships, which is the single most
    decision-relevant number left tonight;
  * and the night has already had jobs parked and restarted, so the most important measurement
    should not be the last one in a four-hour chain.

regime_test.py's columns then queue behind it and still finish today. Nothing is lost either way;
only the order changes.

This file therefore waits for the sploppy match to exit and launches the port match IMMEDIATELY --
POLL_S is 5 seconds against regime_test.py's 120, so the match process exists long before
regime_test.py's next poll and that poll correctly sees the cores as busy. If the two polls do
collide inside the same five seconds, both run at once: both are node-limited, so 231cu says that
costs time and not validity. This file exits as soon as it has launched the match.

No sys.argv; constants below.
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
MATCH = os.path.join(S, "nodes_gate_h2h_port.py")
OUT = os.path.join(S, "h2h_port.log")
LAUNCH_OUT = os.path.join(S, "h2h_port.out")
AHEAD = ("h2h_s1_s2.py", "leaderboard.py")   # the matches that must finish first
POLL_S = 5                                   # fast on purpose: see the docstring
SAY_EVERY = 360                              # a line every half hour while waiting


def say(text):
    line = "%s H2HPORT %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


say("queue started pid %d: the port match goes FIRST (231ed), so this releases the moment the "
    "sploppy match exits and polls every %d s to get there before regime_test.py's 120 s poll"
    % (os.getpid(), POLL_S))
n = 0
while True:
    busy = [x for x in AHEAD if alive(x)]
    if not busy:
        break
    if n % SAY_EVERY == 0:
        say("waiting for %s" % ", ".join(busy))
    n += 1
    time.sleep(POLL_S)

say("releasing the match: %s" % ", ".join(AHEAD) + " are all done")
with open(LAUNCH_OUT, "a") as fh:
    proc = subprocess.Popen([PY, "-u", MATCH], stdout=fh, stderr=subprocess.STDOUT,
                            start_new_session=True)
say("launched nodes_gate_h2h_port.py as pid %d" % proc.pid)
