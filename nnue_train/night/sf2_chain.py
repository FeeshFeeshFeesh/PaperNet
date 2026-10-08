"""HANDOFF §231ca chain, batch 2: generate sf2_ex_50k (PVs recorded), import it with line rows, train the
mixture n1 + sf1 + sf2, gate it. Data and offline tests only; nothing touches the live bot. No sys.argv.

It starts only when the batch-1 chain is out of the way: the s1 gates time moves (500 ms), so a generator
running beside them would change what they measure. Once "GATES exit" is in sf1_gates.log -- or the whole
s1 chain is gone, which leaves the CPU free either way -- batch 2 begins.

The one step that is not just batch 1 again: the import runs here, not inside quickstart.sh, so that it can
pass --pv-rows 8. Each searched root then contributes its first 8 principal-variation positions as rows of
weight 0.5, measured at about 4.7x the rows per generated game (6 games: 1357 rows -> 6362). quickstart.sh
skips an import whose provenance.json exists, so the training launcher reuses this dataset unchanged.
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
NAME = "sf2_ex_50k"
PREV = "sf1_ex_50k"
PV_ROWS = 8
MIN_PLY = 16
S1_GATES_LOG = os.path.join(S, "sf1_gates.log")
GEN_LOG = os.path.join(T, "logs", NAME + ".log")
OUT = os.path.join(S, "sf2_chain.log")
CHAIN_OUT = os.path.join(S, "sf2_chain.out")
MAX_ATTEMPTS = 20
RETRY_WAIT_S = 60
POLL_S = 120
NICE = 10
S1_JOBS = ["sf1_gates.py", "sf1_chain.py", "mix_train.py", "quickstart.sh", "sf19nn_selfplay.py"]


def say(text):
    with open(OUT, "a") as fh:
        fh.write("%s CHAIN2 %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))


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


def wait_for_s1():
    say("waiting for the s1 gates to finish (or for the s1 chain to be gone)")
    while True:
        if "GATES exit" in read(S1_GATES_LOG):
            say("s1 gates finished")
            return
        if not any(alive(job) for job in S1_JOBS):
            say("NOTE: no s1 job is running and sf1_gates.log has no 'GATES exit'; the CPU is free, so batch 2 "
                "starts anyway (batch 2 does not depend on what s1 scored)")
            return
        time.sleep(POLL_S)


def generate():
    if records_complete(NAME):
        say("generation skipped: %s is already complete" % NAME)
        return True
    for attempt in range(1, MAX_ATTEMPTS + 1):
        say("generator attempt %d: sf2_gen.py" % attempt)
        rc = subprocess.run([VPY, "-u", os.path.join(T, "sf2_gen.py")], cwd=T,
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
    wait_for_s1()
    if not generate():
        return
    # the mixture the trainer will read: batch 2 with its lines, and every earlier batch as it already is
    if not importer(NAME, "selfplay_" + NAME, PV_ROWS):
        return
    if not importer(PREV, "selfplay_" + PREV, 0):
        return
    say("mixture rows: n1 %s + %s %s + %s %s"
        % (dataset_rows("selfplay_n1_25k"), PREV, dataset_rows("selfplay_" + PREV),
           NAME, dataset_rows("selfplay_" + NAME)))
    run("mix_train_s2.py", [VPY, "-u", os.path.join(T, "mix_train_s2.py")], T)
    run("sf2_gates.py", [PY, "-u", os.path.join(S, "sf2_gates.py")], S)   # it checks RUN COMPLETE itself
    say("exit")


if __name__ == "__main__":
    main()
