"""What the overnight chain is doing right now. Read-only; safe to run any time from PyCharm. No sys.argv."""
import os, subprocess

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
T = os.path.dirname(os.path.abspath(__file__))
S = _WORK
LOGS = [("n1 training", T + "/logs/n1_25k.log"), ("n1 gates", S + "/n1_gates.log"),
        ("generator queue", S + "/after_gates.log"), ("generator", T + "/logs/sf1_ex_50k.log"),
        ("s1 chain", S + "/sf1_chain.log"), ("s1 training", T + "/logs/sf1_ex_50k.train.log"),
        ("s1 gates", S + "/sf1_gates.log"),
        ("s2 chain", S + "/sf2_chain.log"), ("generator 2", T + "/logs/sf2_ex_50k.log"),
        ("s2 training", T + "/logs/sf2_ex_50k.train.log"), ("s2 gates", S + "/sf2_gates.log"),
        ("s3 chain", S + "/sf3_chain.log"), ("generator 3", T + "/logs/sf3_ex_50k.log"),
        ("s3 training", T + "/logs/sf3_ex_50k.train.log"), ("s3 gates", S + "/sf3_gates.log"),
        ("recipe sweep", S + "/sweep_s1.log"), ("node SPRT queue", S + "/nodes_queue.log"),
        ("node SPRT s1", S + "/nodes_gate_s1n.log"), ("node SPRT s2", S + "/nodes_gate_s2n.log"),
        ("node SPRT s3", S + "/nodes_gate_s3n.log"), ("outcome-label arm", S + "/wdl_arm.log"),
        ("pv-rows arm", S + "/pv_arm.log"), ("format-9 arm", S + "/v9_arm.log"),
        ("node scaling", S + "/node_scaling.log"), ("n1 node SPRT", S + "/n1n_gate.log"),
        ("n1n gate", S + "/nodes_gate_n1n.log"), ("teacher arm", S + "/teacher_arm.log"),
        ("label-scale arm", S + "/scale_arm.log"),
        ("leaderboard", S + "/leaderboard.log"),
        ("per-game weight arm", S + "/weight_arm.log"),
        ("watchdog", S + "/watchdog.log")]
JOBS = ["overnight_selfplay.py", "n1_gates.py", "after_gates.py", "sf19nn_selfplay.py", "sf1_chain.py",
        "mix_train.py", "sf1_gates.py",
        "sf2_chain.py", "sf2_gen.py", "mix_train_s2.py", "sf2_gates.py",
        "sf3_chain.py", "sf3_gen.py", "mix_train_s3.py", "sf3_gates.py", "sweep_s1.py",
        "nodes_queue.py", "nodes_gate_s1n.py", "nodes_gate_s2n.py", "nodes_gate_s3n.py", "wdl_arm.py", "pv_arm.py", "v9_arm.py", "node_scaling.py", "n1n_gate.py", "leaderboard.py", "weight_arm.py", "nodes_gate_n1n.py", "teacher_arm.py", "scale_arm.py", "watchdog.py"]

print("RUNNING:")
for job in JOBS:
    pids = subprocess.run(["pgrep", "-f", job], capture_output=True, text=True).stdout.split()
    print("  %-24s %s" % (job, " ".join(pids) if pids else "-"))
print("\nLAST LINES:")
for name, path in LOGS:
    if not os.path.exists(path):
        print("  %-16s (not started)" % name)
        continue
    tail = [x for x in open(path, errors="replace").read().splitlines() if x.strip()][-2:]
    print("  %s:" % name)
    for line in tail:
        print("     " + line[:200])
