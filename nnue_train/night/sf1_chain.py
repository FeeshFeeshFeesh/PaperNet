"""HANDOFF §231bx chain: after the sf1_ex_50k generator (after_gates.py) is DONE, train the mixed net
(nnue_train/mix_train.py), then run its gates (sf1_gates.py). Every step blocks; nothing touches the live bot. No sys.argv."""
import datetime, json, os, subprocess, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S = _WORK
T = _ROOT + "/nnue_train"
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
QUEUE_LOG = S + "/after_gates.log"
MANIFEST = T + "/sploppy_repo/runs/nnue_selfplay/sf1_ex_50k/manifest.json"
OUT = S + "/sf1_chain.log"

def say(text):
    with open(OUT, "a") as fh:
        fh.write("%s CHAIN %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))

def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0

def queue_text():
    return open(QUEUE_LOG).read() if os.path.exists(QUEUE_LOG) else ""

if __name__ == "__main__":
    say("started pid %d; waiting for QUEUE DONE" % os.getpid())
    while "QUEUE DONE" not in queue_text():
        if "QUEUE STOP" in queue_text() or "QUEUE GAVE UP" in queue_text() or not alive("after_gates.py"):
            say("STOP: the generator queue ended without DONE; nothing started")
            raise SystemExit(1)
        time.sleep(120)
    if not json.load(open(MANIFEST)).get("complete"):
        say("STOP: %s is not complete" % MANIFEST)
        raise SystemExit(1)
    say("mix_train.py start")
    rc = subprocess.run([T + "/.venv/bin/python", "-u", T + "/mix_train.py"], cwd=T,
                        stdout=open(S + "/sf1_chain.out", "a"), stderr=subprocess.STDOUT).returncode
    say("mix_train.py exited rc %d; sf1_gates.py start (it checks RUN COMPLETE itself)" % rc)
    rc = subprocess.run([PY, "-u", S + "/sf1_gates.py"], cwd=S,
                        stdout=open(S + "/sf1_chain.out", "a"), stderr=subprocess.STDOUT).returncode
    say("sf1_gates.py exited rc %d" % rc)
