"""HANDOFF 231ch: do the PV rows actually help, or are they just more rows? One arm, the sweep's protocol.

231ca showed `--pv-rows 8` multiplies the row count about 4.7x for free (1357 -> 6362 rows on the probe,
line_illegal 0). It never showed that the extra rows make a better net. A line row's label is the root's
value sign-flipped per ply, which is an assumption, not a search result: if it is wrong often enough, the
volume is buying noise. That question decides PV_PLIES for every future batch, so it is worth 25 minutes.

The arm re-imports the SAME sf1 records with --pv-rows 8 and trains the same recipe on
n1 + that set, at the baseline's STEP COUNT rather than its pass count, so both nets see exactly as many
batches; the only difference is what the batches contain.

Read the eval score first here, not the objective: with PV rows the validation split holds line rows too,
so the two objectives are not measured on identical rows (the roots in them are the same rows, same seed,
same split). The eval is at a fixed node budget on the sweep's openings, so it compares directly.

Screening, not shipping. No sys.argv: constants below.
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
VPY = os.path.join(T, ".venv", "bin", "python")
BOT = os.path.join(REPO, "target", "release", "bot")
INIT = "models/nnue/examples/example.nnue"
RECORDS = "sf1_ex_50k"                            # runs/nnue_selfplay/<RECORDS>, the batch-1 games
K8_SET = "selfplay_sf1_ex_50k_k8"                 # the same records, plus 8 pv rows per root
BASE_SETS = ["selfplay_sf1_ex_50k:1.0", "selfplay_n1_25k:1.0"]   # the baseline: ROOTS ONLY (checked)
ARM_SETS = [K8_SET + ":1.0", "selfplay_n1_25k:1.0"]
BASE_RUN = "sf1_ex_50k"
S1_TRAIN_LOG = os.path.join(T, "logs", BASE_RUN + ".train.log")
SWEEP_LOG = os.path.join(S, "sweep_s1.log")
OUT = os.path.join(S, "pv_arm.log")
TAG = "k8"
RUN = "sweep_k8"
MIN_PLY = 16
PV_ROWS = 8
SEED = 20260926
HIDDEN, BATCH, EPOCHS, PASSES, LR = 512, 2048, 20, 4, 0.0001
THREADS, NICE = 2, 15
EVAL_PAIRS, EVAL_SIMS, EVAL_SEED = 100, 8, 20260927
POLL_S = 120
ATTEMPTS = 2



def _usable(path):
    """A cached eval counts only if it parses. `stdout=open(path, "w")` creates the file the
    instant the eval starts, so a killed eval leaves a 0-byte file that looks like a finished
    one; the next run would then skip the eval and die on json.load. Checked, not assumed."""
    try:
        json.load(open(path))
        return True
    except Exception:
        return False

def say(text):
    line = "%s PVARM %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def read(path):
    return open(path, errors="replace").read() if os.path.exists(path) else ""


def rows_of(name):
    path = os.path.join(REPO, "runs", "nnue_data", name, "provenance.json")
    return json.load(open(path))["rows"] if os.path.exists(path) else None


def best_objective(path):
    best = None
    for line in read(path).splitlines():
        if line.startswith("{") and '"objective"' in line:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "best" in rec:
                best = rec["best"]
    return best


def wait_its_turn():
    """One trainer at a time, and never beside a move-time SPRT."""
    said = False
    while True:
        busy = [x for x in ("sweep_s1.py", "wdl_arm.py", "[0-9]_gates.py", "mix_train") if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)


def import_k8():
    if rows_of(K8_SET):
        say("%s already imported: %d rows" % (K8_SET, rows_of(K8_SET)))
        return True
    records = os.path.join("runs", "nnue_selfplay", RECORDS)
    if not os.path.exists(os.path.join(REPO, records, "manifest.json")):
        say("STOP: no records at %s" % records)
        return False
    cmd = [VPY, "-u", "-m", "nnue.importer", "selfplay", "--records", records,
           "--out", K8_SET, "--min-ply", str(MIN_PLY), "--pv-rows", str(PV_ROWS)]
    say("importing the same records with %d pv rows per root -> %s" % (PV_ROWS, K8_SET))
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(os.path.join(S, "pv_arm.import"), "a"),
                        stderr=subprocess.STDOUT).returncode
    if rc != 0 or not rows_of(K8_SET):
        say("import rc %d" % rc)
        return False
    rc = subprocess.run([VPY, "-u", "-m", "nnue.data", "encode", K8_SET], cwd=REPO,
                        stdout=open(os.path.join(S, "pv_arm.import"), "a"),
                        stderr=subprocess.STDOUT).returncode
    say("imported %d rows (the roots-only set has %d), encode rc %d"
        % (rows_of(K8_SET), rows_of("selfplay_sf1_ex_50k") or -1, rc))
    return rc == 0


def train():
    run_dir = os.path.join(REPO, "runs", RUN)
    out = os.path.join(S, "pv_arm.out")
    base_rows = sum(rows_of(x.split(":")[0]) or 0 for x in BASE_SETS)
    steps = max(10, (base_rows * PASSES + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))  # the baseline's steps
    warmup = min(200, max(1, steps * EPOCHS // 10))
    if os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("already trained")
        return True
    cmd = ["nice", "-n", str(NICE), VPY, "-u", "-m", "nnue.train", "--run", RUN, "--init", INIT,
           "--hidden", str(HIDDEN), "--version", "8", "--batch", str(BATCH),
           "--steps_per_epoch", str(steps), "--epochs", str(EPOCHS), "--lr", str(LR),
           "--weight_decay", "1e-5", "--warmup_steps", str(warmup), "--lr_floor", "0.15",
           "--qat_start_epoch", "1", "--raw_weight", "0.02", "--symmetry_weight", "0.2",
           "--grad_clip", "5", "--patience", str(EPOCHS), "--val_rows", "8192",
           "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED)]
    for entry in ARM_SETS:
        cmd += ["--data", entry]
    say("training %d rows at the baseline's %d x %d steps (baseline mixture: %d rows)"
        % (sum(rows_of(x.split(":")[0]) or 0 for x in ARM_SETS), EPOCHS, steps, base_rows))
    for attempt in range(1, ATTEMPTS + 1):
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(out, "a"), stderr=subprocess.STDOUT).returncode
        if rc == 0 and os.path.exists(os.path.join(run_dir, "best.nnue")):
            say("trained, best validation objective %s (its own val rows: roots only)" % best_objective(out))
            return True
        say("attempt %d exited rc %d" % (attempt, rc))
        time.sleep(60)
    say("GAVE UP")
    return False


def screen():
    path = os.path.join(REPO, "runs", RUN, "eval_sweep.json")
    if not _usable(path):
        cmd = ["nice", "-n", str(NICE), BOT, "eval",
               "--candidate", "nnue:runs/%s/best.nnue?hash=64" % RUN,
               "--reference", "nnue:%s?hash=64" % INIT,
               "--sims", str(EVAL_SIMS), "--pairs", str(EVAL_PAIRS),
               "--threads", str(THREADS), "--seed", str(EVAL_SEED)]
        say("eval %d openings at %d nodes (the sweep's openings)" % (EVAL_PAIRS, EVAL_SIMS * 2500))
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                            stderr=open(os.path.join(S, "pv_arm.err"), "a")).returncode
        if rc == 0:
            os.replace(path + ".part", path)  # never leave a half-written eval that looks cached
        if rc != 0:
            say("eval rc %d" % rc)
            return None
    ev = json.load(open(path))
    games = ev["wins"] + ev["draws"] + ev["losses"]
    ev["score"] = (ev["wins"] + 0.5 * ev["draws"]) / games if games else None
    say("score %.3f over %d games (W/D/L %d/%d/%d), margin %s, bootstrap %s, forfeits %s"
        % (ev["score"], games, ev["wins"], ev["draws"], ev["losses"],
           ev.get("margin"), ev.get("bootstrap_interval"), ev.get("forfeits")))
    return ev


def main():
    say("started pid %d" % os.getpid())
    wait_its_turn()
    if "RUN COMPLETE %s" % BASE_RUN not in read(S1_TRAIN_LOG):
        say("STOP: %s never completed, so there is no baseline and no sf1 import" % BASE_RUN)
        return
    if not import_k8():
        return
    if not train():
        return
    ev = screen()
    base_line = [x for x in read(SWEEP_LOG).splitlines() if "base(s1): score" in x]
    say("TABLE  arm | rows | eval score on the sweep's openings")
    say("TABLE  base(s1) roots only | %s | %s"
        % (sum(rows_of(x.split(":")[0]) or 0 for x in BASE_SETS),
           base_line[-1].split("score")[-1].strip()[:40] if base_line else "?"))
    say("TABLE  %s pv-rows 8 | %s | %s"
        % (TAG, sum(rows_of(x.split(":")[0]) or 0 for x in ARM_SETS), ("%.3f" % ev["score"]) if ev else "?"))
    say("exit")


main()
