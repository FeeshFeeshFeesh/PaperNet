"""Watcher for the night: the n1 and s1 chains (§231bv-§231bx) and the batch-2 chain (§231ca).
Prints only lines to act on. Offsets persist in watch_night.pos, so a re-armed watcher misses nothing."""
import json, os, subprocess, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S = _WORK
T = _ROOT + "/nnue_train"
FILES = {"train": T + "/logs/n1_25k.log", "gates": S + "/n1_gates.log", "queue": S + "/after_gates.log",
         "gen": T + "/logs/sf1_ex_50k.log", "mixtrain": T + "/logs/sf1_ex_50k.train.log",
         "chain": S + "/sf1_chain.log", "s1gates": S + "/sf1_gates.log",
         "chain2": S + "/sf2_chain.log", "gen2": T + "/logs/sf2_ex_50k.log",
         "mixtrain2": T + "/logs/sf2_ex_50k.train.log", "s2gates": S + "/sf2_gates.log",
         "chain3": S + "/sf3_chain.log", "gen3": T + "/logs/sf3_ex_50k.log",
         "mixtrain3": T + "/logs/sf3_ex_50k.train.log", "s3gates": S + "/sf3_gates.log",
         "sweep": S + "/sweep_s1.log", "nqueue": S + "/nodes_queue.log",
         "ngate1": S + "/nodes_gate_s1n.log", "ngate2": S + "/nodes_gate_s2n.log",
         "ngate3": S + "/nodes_gate_s3n.log", "wdl": S + "/wdl_arm.log",
         "pvarm": S + "/pv_arm.log", "v9": S + "/v9_arm.log",
         "nscale": S + "/node_scaling.log", "n1n": S + "/n1n_gate.log",
         "ngate4": S + "/nodes_gate_n1n.log", "teach": S + "/teacher_arm.log",
         "scale": S + "/scale_arm.log", "board": S + "/leaderboard.log", "warm": S + "/weight_arm.log",
         "h2hss": S + "/h2h_s1_s2.log", "wdlf": S + "/wdl_full.log",
         "regime": S + "/regime_test.log",
         "h2hport": S + "/h2h_port.log",
         "dog": S + "/watchdog.log"}
POS = S + "/watch_night.pos"
TRAIN_KEYS = ("ATTEMPT", "RUN COMPLETE", "GAVE UP", "== ", "Traceback", "Error", "error:", "candidate score", "Killed", "removed unfinished")
GEN_KEYS = ("START", "FAILED", "gave up", "COMPLETE", "PARTIAL", "Traceback", "resuming")

def keep(kind, line):
    if kind in ("train", "mixtrain", "mixtrain2", "mixtrain3"):
        return any(k in line for k in TRAIN_KEYS) and "UserWarning" not in line
    if kind in ("gates", "s1gates", "s2gates", "s3gates"):
        return "GATES R3 pairs" not in line
    if kind in ("gen", "gen2", "gen3"):
        return any(k in line for k in GEN_KEYS)
    if kind == "board":                       # it reprints the standings every 10 minutes
        return "TABLE" not in line and "waiting for" not in line
    if kind == "h2hss":                       # it says "waiting" once a minute until the board is free
        return "waiting for" not in line
    if kind == "regime":                      # phase B says HOLDING every half hour, by design
        return "HOLDING" not in line
    if kind in ("ngate1", "ngate2", "ngate3", "ngate4", "nscale"):
        return "pairs/h]" not in line or "FINAL" in line   # a status line a minute is too much
    return True

state = json.load(open(POS)) if os.path.exists(POS) else {
    "pos": {k: os.path.getsize(f) if os.path.exists(f) else 0 for k, f in FILES.items()}, "flags": {}}
pos, flags = state["pos"], state["flags"]
while True:
    for kind, path in FILES.items():
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            fh.seek(pos.get(kind, 0)); new = fh.read(); pos[kind] = fh.tell()
        for line in new.splitlines():
            if keep(kind, line):
                print("%s %s" % (kind, line.strip()[:220]), flush=True)
    train_done = "RUN COMPLETE n1_25k" in open(FILES["train"]).read()
    for name, pattern, relevant in (("launcher", "overnight_selfplay.py", not train_done),
                                    ("gates", "n1_gates.py", True), ("queue", "after_gates.py", True),
                                    ("chain", "sf1_chain.py", True), ("chain2", "sf2_chain.py", True),
                                    ("chain3", "sf3_chain.py", True),
                                    ("sweep", "sweep_s1.py", True),
                                    ("nqueue", "nodes_queue.py", True),
                                    ("wdl", "wdl_arm.py", True),
                                    ("pvarm", "pv_arm.py", True),
                                    ("v9", "v9_arm.py", True),
                                    ("nscale", "node_scaling.py", True),
                                    ("n1n", "n1n_gate.py", True),
                                    ("teach", "teacher_arm.py", True),
                                    ("scale", "scale_arm.py", True),
                                    ("board", "leaderboard.py", True),
                                    ("warm", "weight_arm.py", True),
                                    ("h2hss", "h2h_s1_s2.py", True),
                                    ("wdlf", "wdl_full.py", True),
                                    ("regime", "regime_test.py", True),
                                    ("h2hport", "h2h_port", True),
                                    ("dog", "watchdog.py", True)):
        up = subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0
        if relevant and flags.get(name, True) and not up:
            print("PROCESS GONE: %s (%s)" % (name, pattern), flush=True)
        flags[name] = up
    json.dump(state, open(POS, "w"))
    time.sleep(30)
