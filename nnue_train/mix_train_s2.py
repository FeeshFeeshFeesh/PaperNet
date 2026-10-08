"""Training on a mixture of batches (HANDOFF §231ca): the launcher for sf2_ex_50k.

sf2_ex_50k's records come from sf2_gen.py (SF19-NN at 50k nodes) and must be complete first, so that
sploppy's models/nnue/quickstart.sh (MIT, see sploppy_repo/LICENSE) skips its generation step. sf2_chain.py
also imports the records before this runs, with --pv-rows 8, so quickstart skips the import too and the
line rows survive; running this on its own would import sf2_ex_50k without them. It then
  3. encodes sf2_ex_50k
  4. trains a continuation of START_NET on sf2_ex_50k + EXTRA_DATA (n1_25k's and sf1_ex_50k's sets)
     -> sploppy_repo/runs/<NAME>/best.nnue
  5. runs sploppy's quick check: the new net against START_NET
If the job dies, it is started again (a training run without eval.json is removed first).

No sys.argv: edit the constants and run from PyCharm (or: .venv/bin/python -u mix_train.py).
"""
import datetime
import json
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "sploppy_repo")
PY = os.path.join(HERE, ".venv", "bin", "python")
START_NET = "models/nnue/examples/example.nnue"   # relative to REPO
NAME = "sf2_ex_50k"
SETTINGS = {
    "GAMES": "1600",       # informational: the records already exist
    "NODES": "50000",      # informational: SF19-NN's node budget for these records
    "THREADS": "6",
    "HASH": "64",
    "SEED": "20260929",    # training and eval seed
    "BATCH": "2048",
    "DEVICE": "cpu",
    "PAIRS": "100",
    "SIMS": "8",
    "EXTRA_DATA": "selfplay_n1_25k:1.0 selfplay_sf1_ex_50k:1.0",
}
NICE = 10
MAX_ATTEMPTS = 20
RETRY_WAIT_S = 60
LOG = os.path.join(HERE, "logs", NAME + ".train.log")


def log(fh, text):
    fh.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    fh.flush()


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    env = dict(os.environ, PY=PY, **SETTINGS)
    run_dir = os.path.join(REPO, "runs", NAME)
    manifest = os.path.join(REPO, "runs", "nnue_selfplay", NAME, "manifest.json")
    with open(LOG, "a") as fh:
        if not (os.path.exists(manifest) and json.load(open(manifest)).get("complete")):
            log(fh, "GAVE UP: %s is not complete (quickstart would generate with sploppy's bot)" % manifest)
            return
        for entry in SETTINGS["EXTRA_DATA"].split():
            prov = os.path.join(REPO, "runs", "nnue_data", entry.split(":")[0], "provenance.json")
            if not os.path.exists(prov):
                log(fh, "GAVE UP: extra data %s has no provenance.json" % entry)
                return
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log(fh, "ATTEMPT %d START %s %s" % (attempt, NAME, " ".join("%s=%s" % kv for kv in SETTINGS.items())))
            if os.path.isdir(run_dir) and not os.path.exists(os.path.join(run_dir, "eval.json")):
                shutil.rmtree(run_dir)
                log(fh, "removed unfinished training run %s" % run_dir)
            proc = subprocess.run(["nice", "-n", str(NICE), "bash", "models/nnue/quickstart.sh", START_NET, NAME],
                                  cwd=REPO, env=env, stdout=fh, stderr=subprocess.STDOUT)
            if proc.returncode == 0:
                log(fh, "RUN COMPLETE %s" % NAME)
                return
            log(fh, "ATTEMPT %d EXITED rc=%d" % (attempt, proc.returncode))
            time.sleep(RETRY_WAIT_S)
        log(fh, "GAVE UP after %d attempts" % MAX_ATTEMPTS)


if __name__ == "__main__":
    main()
