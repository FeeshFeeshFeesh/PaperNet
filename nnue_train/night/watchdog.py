"""HANDOFF 231cp: the host memory guard kills a job about every half hour, and nobody is awake.

`resume_night.py` is the right tool with a human present: it starts anything that is not running.
Unattended it is the wrong tool, because a job that FINISHED looks exactly like a job that died,
and restarting a finished chain would re-run a move-time SPRT beside whatever else is going --
the one thing the night's discipline forbids.

So this watchdog restarts a job only when all of these hold:
  * it is not running (a SIGSTOPped job is still running, so a paused match is left alone);
  * its log exists and its last lines carry NO terminal marker (exit / DONE / QUEUE DONE /
    GAVE UP / STOP), so a job that ended on purpose is never restarted;
  * it has not already been restarted RESTART_CAP times, and its last restart was over
    COOLDOWN_S ago.
The bias is deliberately conservative: a job wrongly judged finished is simply left dead (the
status quo, and a human reads it in the morning), while a job wrongly judged dead would be a
second copy of a match or a trainer. Every restart is logged with the reason.

It never touches the user's own processes and starts nothing that is not in JOBS.
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
T = _ROOT + "/nnue_train"
NIGHT = T + "/night"
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
VPY = T + "/.venv/bin/python"
OUT = os.path.join(S, "watchdog.log")
POLL_S = 300
RESTART_CAP = 3
COOLDOWN_S = 900
TERMINAL = ("exit", "DONE. Nothing shipped", "QUEUE DONE", "GAVE UP", "STOP:", "GATES exit")
TAIL_LINES = 3

# name, its log, the interpreter it needs
JOBS = [
    ("after_gates.py", S + "/after_gates.log", PY),
    ("sf1_chain.py", S + "/sf1_chain.log", PY),
    ("sf2_chain.py", S + "/sf2_chain.log", PY),
    ("sf3_chain.py", S + "/sf3_chain.log", PY),
    ("sweep_s1.py", S + "/sweep_s1.log", VPY),
    ("nodes_queue.py", S + "/nodes_queue.log", VPY),
    ("wdl_arm.py", S + "/wdl_arm.log", VPY),
    ("pv_arm.py", S + "/pv_arm.log", VPY),
    ("v9_arm.py", S + "/v9_arm.log", VPY),
    ("node_scaling.py", S + "/node_scaling.log", VPY),
    ("n1n_gate.py", S + "/n1n_gate.log", VPY),
    ("scale_arm.py", S + "/scale_arm.log", VPY),
    ("teacher_arm.py", S + "/teacher_arm.log", VPY),
    ("leaderboard.py", S + "/leaderboard.log", VPY),
    ("weight_arm.py", S + "/weight_arm.log", VPY),
]


def say(text):
    line = "%s DOG %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(name):
    return bool(subprocess.run(["pgrep", "-f", name], capture_output=True, text=True).stdout.split())


def finished(log):
    lines = [x for x in open(log, errors="replace").read().splitlines() if x.strip()]
    return any(any(k in line for k in TERMINAL) for line in lines[-TAIL_LINES:])


def script(name):
    return (S if os.path.exists(S + "/" + name) else NIGHT) + "/" + name


def main():
    say("started pid %d, %d jobs, poll %ds, cap %d restarts each" % (os.getpid(), len(JOBS), POLL_S, RESTART_CAP))
    restarts, last = {}, {}
    while True:
        for name, log, interp in JOBS:
            if alive(name) or not os.path.exists(log):
                continue
            if finished(log):
                continue
            if restarts.get(name, 0) >= RESTART_CAP:
                continue
            if time.time() - last.get(name, 0) < COOLDOWN_S:
                continue
            path = script(name)
            if not os.path.exists(path):
                say("%s is dead and its script is missing at %s" % (name, path))
                restarts[name] = RESTART_CAP
                continue
            tail = [x for x in open(log, errors="replace").read().splitlines() if x.strip()][-1:]
            subprocess.Popen(["caffeinate", "-i", interp, "-u", path], cwd=os.path.dirname(path),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            restarts[name] = restarts.get(name, 0) + 1
            last[name] = time.time()
            say("RESTARTED %s (restart %d of %d); its last log line was: %s"
                % (name, restarts[name], RESTART_CAP, (tail[0][:150] if tail else "(empty)")))
        time.sleep(POLL_S)


main()
