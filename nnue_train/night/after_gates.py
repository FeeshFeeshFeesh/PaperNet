"""Queue for the free CPU: once n1_gates.py has exited after a finished n1_25k run, generate sf1_ex_50k with
nnue_train/sf19nn_selfplay.py (HANDOFF §231bw). Retries a killed or partial run (the generator resumes).
Data only: no games against anything, nothing touches the live bot. No sys.argv."""
import datetime, os, subprocess, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S = _WORK
T = _ROOT + "/nnue_train"
GATES_LOG = S + "/n1_gates.log"
TRAIN_LOG = T + "/logs/n1_25k.log"
GEN_LOG = T + "/logs/sf1_ex_50k.log"
OUT = S + "/after_gates.log"
MAX_ATTEMPTS = 20

def say(text):
    with open(OUT, "a") as fh:
        fh.write("%s QUEUE %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))

def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0

def read(path):
    return open(path).read() if os.path.exists(path) else ""

if __name__ == "__main__":
    say("started pid %d; waiting for GATES exit" % os.getpid())
    while "GATES exit" not in read(GATES_LOG):
        if not alive("n1_gates.py"):
            say("STOP: n1_gates.py is gone without 'GATES exit'; generator not started")
            raise SystemExit(1)
        time.sleep(60)
    if "RUN COMPLETE n1_25k" not in read(TRAIN_LOG):
        say("STOP: gates exited but n1_25k did not complete; generator not started")
        raise SystemExit(1)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        say("attempt %d: sf19nn_selfplay.py" % attempt)
        rc = subprocess.run([T + "/.venv/bin/python", "-u", T + "/sf19nn_selfplay.py"], cwd=T,
                            stdout=open(S + "/after_gates.out", "a"), stderr=subprocess.STDOUT).returncode
        last = [x for x in read(GEN_LOG).splitlines() if " COMPLETE " in x or " PARTIAL " in x]
        if rc == 0 and last and " COMPLETE " in last[-1]:
            say("DONE: " + last[-1])
            raise SystemExit(0)
        say("attempt %d ended rc %d, last pack line %s" % (attempt, rc, last[-1] if last else None))
        time.sleep(60)
    say("GAVE UP after %d attempts" % MAX_ATTEMPTS)
