"""Overnight NNUE self-play + training run (HANDOFF §231bv).

Runs sploppy's models/nnue/quickstart.sh (MIT, see sploppy_repo/LICENSE) with the settings below:
  1. sploppy's engine plays itself with START_NET, every searched position becomes a training row
  2-3. the games are imported and encoded
  4. a continuation of START_NET is trained on them -> sploppy_repo/runs/<NAME>/best.nnue
  5. sploppy's own quick check: the new net against START_NET
The whole job runs at a lower priority (NICE) so the live bot keeps the CPU when it needs it.
If the job dies, it is started again: quickstart continues an unfinished batch from its records.

No sys.argv: edit the constants and run from PyCharm (or: .venv/bin/python -u overnight_selfplay.py).
"""
import datetime
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "sploppy_repo")
PY = os.path.join(HERE, ".venv", "bin", "python")
START_NET = "models/nnue/examples/example.nnue"   # relative to REPO
NAME = "n1_25k"
SETTINGS = {
    "GAMES": "3200",       # about 222 positions per game
    "NODES": "25000",      # search nodes per move = label quality (sploppy's loop uses 100000; 4x slower here)
    "THREADS": "6",        # games in flight; leaves room for the live bot's 2 threads
    "HASH": "64",
    "SEED": "20260918",    # change for every new batch: the generator is deterministic
    "BATCH": "2048",       # QUICKSTART.md: 2048 on a machine with little memory
    "DEVICE": "cpu",       # mps works but measured slower for this net (5.3 s vs 4.5 s per 40 steps)
    "PAIRS": "100",
    "SIMS": "8",
}
NICE = 10
MAX_ATTEMPTS = 20
RETRY_WAIT_S = 60
LOG = os.path.join(HERE, "logs", NAME + ".log")


def log(fh, text):
    fh.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    fh.flush()


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    env = dict(os.environ, PY=PY, **SETTINGS)
    run_dir = os.path.join(REPO, "runs", NAME)
    with open(LOG, "a") as fh:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            log(fh, "ATTEMPT %d START %s %s" % (attempt, NAME, " ".join("%s=%s" % kv for kv in SETTINGS.items())))
            # quickstart appends to an existing training run; a retry after a failed training step starts it clean
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
