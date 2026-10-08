"""Restart whatever part of the overnight chain is not running (after a reboot or a kill).

Safe to run repeatedly: each job is started only if no process of that name exists, and every job resumes
where it stopped (generation skips recorded games, quickstart skips finished steps). Run from PyCharm. No sys.argv.
"""
import os, subprocess, time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
T = os.path.dirname(os.path.abspath(__file__))
S = _WORK
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
NIGHT = T + "/night"          # copies of the scratchpad scripts, in case /private/tmp was cleared
GATES_LOG = S + "/n1_gates.log"

def alive(name):
    return bool(subprocess.run(["pgrep", "-f", name], capture_output=True, text=True).stdout.split())

def script(name):
    return (S if os.path.exists(S + "/" + name) else NIGHT) + "/" + name

def start(name, path, interp=PY):
    if alive(name):
        print("%-20s already running" % name)
        return
    subprocess.Popen(["caffeinate", "-i", interp, "-u", path], cwd=os.path.dirname(path),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(1)
    print("%-20s started" % name)

if __name__ == "__main__":
    done = os.path.exists(GATES_LOG) and "GATES exit" in open(GATES_LOG).read()
    if not done:
        # the n1 test never resumes a half-finished match: it restarts from pair 0 with the same seed
        start("n1_gates.py", script("n1_gates.py"))
    else:
        print("n1_gates.py           finished")
    start("after_gates.py", script("after_gates.py"))
    start("sf1_chain.py", script("sf1_chain.py"))
    # batch 2 waits for the s1 gates by itself, so it is safe to have it running from the start
    start("sf2_chain.py", script("sf2_chain.py"))
    start("sf3_chain.py", script("sf3_chain.py"))
    # the recipe sweep needs the interpreter with torch, not the launcher's
    start("sweep_s1.py", script("sweep_s1.py"), T + "/.venv/bin/python")
    # the node-limited SPRT queue: it waits for each gate to be DONE before it runs
    start("nodes_queue.py", script("nodes_queue.py"), T + "/.venv/bin/python")
    # the outcome-label arm: it waits for the sweep, then trains one net on WDL rows too
    start("wdl_arm.py", script("wdl_arm.py"), T + "/.venv/bin/python")
    # the pv-rows ablation: it waits for the sweep and the outcome arm
    start("pv_arm.py", script("pv_arm.py"), T + "/.venv/bin/python")
    start("v9_arm.py", script("v9_arm.py"), T + "/.venv/bin/python")
    start("node_scaling.py", script("node_scaling.py"), T + "/.venv/bin/python")
    start("n1n_gate.py", script("n1n_gate.py"), T + "/.venv/bin/python")
    start("watchdog.py", script("watchdog.py"), T + "/.venv/bin/python")
    start("scale_arm.py", script("scale_arm.py"), T + "/.venv/bin/python")
    start("leaderboard.py", script("leaderboard.py"), T + "/.venv/bin/python")
    start("weight_arm.py", script("weight_arm.py"), T + "/.venv/bin/python")
    start("teacher_arm.py", script("teacher_arm.py"), T + "/.venv/bin/python")
    print("\nnow run status_night.py to see where it is")
