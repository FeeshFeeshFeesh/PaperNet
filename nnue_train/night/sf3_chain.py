"""HANDOFF §231ca chain, batch 3: the same four steps as sf2_chain.py for sf3_ex_50k, so the machine never
runs out of queued work. Data and offline tests only; nothing touches the live bot. No sys.argv.

It starts when the batch-2 gates have exited (they time moves, so nothing may generate beside them), or
when the whole batch-2 chain is gone. Two differences from batch 2:
  - BATCHES lists every set the trainer will read, with the --pv-rows each needs, and each missing one is
    imported here. Batch 1 has no recorded lines (it was generated before the patch), so it imports with 0.
  - mix_train_s3.py picks its starting network from batch 2's result instead of always example.nnue.
"""
import datetime
import json
import os
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
VPY = os.path.join(T, ".venv", "bin", "python")      # the one with the engine and nnue packages
NAME = "sf3_ex_50k"
BATCHES = [("sf3_ex_50k", 8), ("sf2_ex_50k", 8), ("sf1_ex_50k", 0)]
MIN_PLY = 16
PREV_GATES_LOG = os.path.join(S, "sf2_gates.log")
GEN_LOG = os.path.join(T, "logs", NAME + ".log")
OUT = os.path.join(S, "sf3_chain.log")
CHAIN_OUT = os.path.join(S, "sf3_chain.out")
MAX_ATTEMPTS = 20
RETRY_WAIT_S = 60
POLL_S = 120
NICE = 10
PREV_JOBS = ["sf2_gates.py", "sf2_chain.py", "mix_train_s2.py", "quickstart.sh", "sf2_gen.py"]


def say(text):
    with open(OUT, "a") as fh:
        fh.write("%s CHAIN3 %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def read(path):
    return open(path, errors="replace").read() if os.path.exists(path) else ""


def records_complete(name):
    path = os.path.join(REPO, "runs", "nnue_selfplay", name, "manifest.json")
    return os.path.exists(path) and bool(json.load(open(path)).get("complete"))


def dataset_rows(name):
    path = os.path.join(REPO, "runs", "nnue_data", name, "provenance.json")
    return json.load(open(path))["rows"] if os.path.exists(path) else None


def wait_for_prev():
    say("waiting for the s2 gates to finish (or for the batch-2 chain to be gone)")
    while True:
        if "GATES exit" in read(PREV_GATES_LOG):
            say("s2 gates finished")
            return
        if not any(alive(job) for job in PREV_JOBS):
            say("NOTE: no batch-2 job is running and sf2_gates.log has no 'GATES exit'; the CPU is free, so "
                "batch 3 starts anyway")
            return
        time.sleep(POLL_S)


def generate():
    if records_complete(NAME):
        say("generation skipped: %s is already complete" % NAME)
        return True
    for attempt in range(1, MAX_ATTEMPTS + 1):
        say("generator attempt %d: sf3_gen.py" % attempt)
        rc = subprocess.run([VPY, "-u", os.path.join(T, "sf3_gen.py")], cwd=T,
                            stdout=open(CHAIN_OUT, "a"), stderr=subprocess.STDOUT).returncode
        last = [x for x in read(GEN_LOG).splitlines() if " COMPLETE " in x or " PARTIAL " in x]
        if rc == 0 and last and " COMPLETE " in last[-1]:
            say("generation DONE: " + last[-1])
            return True
        say("generator attempt %d ended rc %d, last pack line %s" % (attempt, rc, last[-1] if last else None))
        time.sleep(RETRY_WAIT_S)
    say("STOP: the generator gave up after %d attempts" % MAX_ATTEMPTS)
    return False


def importer(records_name, set_name, pv_rows):
    """sploppy's importer on one batch. Already-imported sets are left alone."""
    rows = dataset_rows(set_name)
    if rows is not None:
        say("import skipped: %s already has %d rows" % (set_name, rows))
        return True
    if not records_complete(records_name):
        say("STOP: %s has no complete manifest; not imported" % records_name)
        return False
    cmd = [VPY, "-u", "-m", "nnue.importer", "selfplay",
           "--records", os.path.join("runs", "nnue_selfplay", records_name),
           "--out", set_name, "--min-ply", str(MIN_PLY)]
    if pv_rows:
        cmd += ["--pv-rows", str(pv_rows)]
    say("import start: %s -> %s%s" % (records_name, set_name, " --pv-rows %d" % pv_rows if pv_rows else ""))
    rc = subprocess.run(["nice", "-n", str(NICE)] + cmd, cwd=REPO,
                        stdout=open(CHAIN_OUT, "a"), stderr=subprocess.STDOUT).returncode
    rows = dataset_rows(set_name)
    if rc != 0 or rows is None:
        say("STOP: import rc %d, no provenance.json for %s" % (rc, set_name))
        return False
    prov = json.load(open(os.path.join(REPO, "runs", "nnue_data", set_name, "provenance.json")))
    counts = prov.get("counts", {})
    say("import DONE: %s has %d rows (%s); counts %s"
        % (set_name, rows, {k: v for k, v in prov.items() if k in ("min_ply", "pv_rows", "pv_weight")},
           {k: counts.get(k) for k in ("games", "roots", "eligible", "line_rows", "line_duplicates",
                                       "line_illegal", "unique") if k in counts}))
    if counts.get("line_illegal"):
        say("WARNING: %d line moves were illegal on the replayed board -- the recorded action ids are wrong"
            % counts["line_illegal"])
    return True


def run(label, argv, cwd):
    say("%s start" % label)
    rc = subprocess.run(argv, cwd=cwd, stdout=open(CHAIN_OUT, "a"), stderr=subprocess.STDOUT).returncode
    say("%s exited rc %d" % (label, rc))
    return rc


def main():
    say("started pid %d" % os.getpid())
    wait_for_prev()
    if not generate():
        return
    for records_name, pv_rows in BATCHES:
        if not importer(records_name, "selfplay_" + records_name, pv_rows):
            return
    say("mixture rows: " + ", ".join("%s %s" % (n, dataset_rows("selfplay_" + n))
                                     for n in ["n1_25k"] + [b[0] for b in reversed(BATCHES)]))
    run("mix_train_s3.py", [VPY, "-u", os.path.join(T, "mix_train_s3.py")], T)
    run("sf3_gates.py", [PY, "-u", os.path.join(S, "sf3_gates.py")], S)   # it checks RUN COMPLETE itself
    say("exit")


if __name__ == "__main__":
    main()
