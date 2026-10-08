"""HANDOFF §231cd: what recipe trains best on the data we already have (n1 + sf1), measured while batch 2 generates.

Training turned out to be the cheap step: n1_25k was 20 epochs x 78 steps in 3.8 minutes, and sploppy's own
eval is 15 minutes. So a recipe is worth about half an hour to test, against five and a half hours to make a
batch of data -- and n1's validation objective stopped improving after epoch 2 (0.020676 -> 0.019776 over the
remaining 18), which is what a data-limited run looks like. This asks whether the recipe is leaving anything
on the table before we spend more nights on rows.

Baseline: the s1 run itself (same mixture, same seed, same val split), so only the variants are trained here.
Every variant keeps sf1's mixture, seed and every other knob; one thing changes each time.

Screening, not shipping: the comparison to read is the validation objective (identical data and split, so it
is a like-for-like number). sploppy's eval at a fixed NODE budget is reported beside it because contention
cannot bias a node-limited match -- this runs while the batch-2 generator has the cores. Nothing that comes
out of here goes near the live bot, and a winner would still have to pass the §231bv gates.

No sys.argv: constants below. Run from PyCharm or: .venv/bin/python -u sweep_s1.py
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
INIT = "models/nnue/examples/example.nnue"        # relative to REPO, the net every arm continues
SETS = ["selfplay_sf1_ex_50k:1.0", "selfplay_n1_25k:1.0"]
BASE_RUN = "sf1_ex_50k"                           # the baseline arm: already trained by mix_train.py
GATES_LOG = os.path.join(S, "sf1_gates.log")      # its SPRT times moves: nothing trains until it has exited
S1_TRAIN_LOG = os.path.join(T, "logs", BASE_RUN + ".train.log")
OUT = os.path.join(S, "sweep_s1.log")
SEED = 20260926                                   # s1's seed: same val split, so the objectives compare
HIDDEN = 512
BATCH = 2048
EPOCHS = 20
PASSES = 4
LR = 0.0001
THREADS = 2                                       # the generator has the rest
NICE = 15
EVAL_PAIRS = 100
EVAL_SIMS = 8                                     # 20,000 nodes a move, a budget contention cannot change
EVAL_SEED = 20260927
POLL_S = 120
ATTEMPTS = 2

# tag -> what this arm changes, as extra flags for nnue.train (and the passes it gets)
ARMS = [
    ("p8", [], 8),                                # twice the steps: is the baseline under-trained?
    ("lr2", ["--lr", "0.0002"], 4),               # twice the learning rate for a continuation
    ("ema", ["--ema", "0.999"], 4),               # a 1000-step weight average is the network of record
]



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
    with open(OUT, "a") as fh:
        fh.write("%s SWEEP %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def read(path):
    return open(path, errors="replace").read() if os.path.exists(path) else ""


def rows_of(name):
    path = os.path.join(REPO, "runs", "nnue_data", name, "provenance.json")
    return json.load(open(path))["rows"] if os.path.exists(path) else None


def wait_for_s1():
    say("waiting for the s1 gates to exit (their SPRT is move-time limited; nothing may train beside it)")
    while "GATES exit" not in read(GATES_LOG):
        if not any(alive(x) for x in ("sf1_gates.py", "sf1_chain.py", "mix_train.py", "quickstart.sh")):
            say("NOTE: the s1 chain is gone without 'GATES exit'; starting anyway")
            return True
        time.sleep(POLL_S)
    return True


def best_objective(run_out):
    """The best validation objective a run reached, from the JSON lines it printed."""
    best = None
    for line in read(run_out).splitlines():
        if line.startswith("{") and '"objective"' in line:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if "best" in rec:
                best = rec["best"]
    return best


def train(tag, extra, passes):
    run = "sweep_" + tag
    run_dir = os.path.join(REPO, "runs", run)
    out = os.path.join(S, "sweep_%s.out" % tag)
    total = sum(rows_of(x.split(":")[0]) or 0 for x in SETS)
    steps = max(10, (total * passes + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))
    warmup = min(200, max(1, steps * EPOCHS // 10))
    if os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("%s: already trained" % tag)
        return run
    cmd = ["nice", "-n", str(NICE), VPY, "-u", "-m", "nnue.train", "--run", run, "--init", INIT,
           "--hidden", str(HIDDEN), "--version", "8", "--batch", str(BATCH),
           "--steps_per_epoch", str(steps), "--epochs", str(EPOCHS), "--lr", str(LR),
           "--weight_decay", "1e-5", "--warmup_steps", str(warmup), "--lr_floor", "0.15",
           "--qat_start_epoch", "1", "--raw_weight", "0.02", "--symmetry_weight", "0.2",
           "--grad_clip", "5", "--patience", str(EPOCHS), "--val_rows", "8192",
           "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED)]
    for entry in SETS:
        cmd += ["--data", entry]
    cmd += extra
    say("%s: training %d rows, %d x %d steps at batch %d (%d passes), warmup %d, extra %s"
        % (tag, total, EPOCHS, steps, BATCH, passes, warmup, " ".join(extra)))
    for attempt in range(1, ATTEMPTS + 1):
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(out, "a"), stderr=subprocess.STDOUT).returncode
        if rc == 0 and os.path.exists(os.path.join(run_dir, "best.nnue")):
            say("%s: trained, best validation objective %s" % (tag, best_objective(out)))
            return run
        say("%s: attempt %d exited rc %d" % (tag, attempt, rc))
        time.sleep(60)
    say("%s: GAVE UP" % tag)
    return None


def screen(tag, run):
    """sploppy's own eval, candidate against the net every arm continued from."""
    # never quickstart's own eval.json: every arm is measured on the same openings, so the seed is EVAL_SEED
    path = os.path.join(REPO, "runs", run, "eval_sweep.json")
    if not _usable(path):
        cmd = ["nice", "-n", str(NICE), BOT, "eval",
               "--candidate", "nnue:runs/%s/best.nnue?hash=64" % run,
               "--reference", "nnue:%s?hash=64" % INIT,
               "--sims", str(EVAL_SIMS), "--pairs", str(EVAL_PAIRS),
               "--threads", str(THREADS), "--seed", str(EVAL_SEED)]
        say("%s: eval %d openings at %d nodes" % (tag, EVAL_PAIRS, EVAL_SIMS * 2500))
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"), stderr=open(os.path.join(S, "sweep_%s.err" % tag), "a")).returncode
        if rc == 0:
            os.replace(path + ".part", path)  # never leave a half-written eval that looks cached
        if rc != 0:
            say("%s: eval rc %d" % (tag, rc))
            return None
    ev = json.load(open(path))
    games = ev["wins"] + ev["draws"] + ev["losses"]
    ev["score"] = (ev["wins"] + 0.5 * ev["draws"]) / games if games else None
    ev["games"] = games
    say("%s: score %.3f over %d games (W/D/L %d/%d/%d), margin %s, bootstrap %s, forfeits %s"
        % (tag, ev["score"], games, ev["wins"], ev["draws"], ev["losses"],
           ev.get("margin"), ev.get("bootstrap_interval"), ev.get("forfeits")))
    return ev


def main():
    say("started pid %d" % os.getpid())
    wait_for_s1()
    if "RUN COMPLETE %s" % BASE_RUN not in read(S1_TRAIN_LOG):
        say("STOP: %s never completed, so there is no baseline and no imported sf1 set" % BASE_RUN)
        return
    missing = [x.split(":")[0] for x in SETS if rows_of(x.split(":")[0]) is None]
    if missing:
        say("STOP: not imported: %s" % missing)
        return
    table = []
    base_out = None
    base_ev = screen("base(s1)", BASE_RUN)
    table.append(("base(s1)", best_objective(S1_TRAIN_LOG), base_ev))
    for tag, extra, passes in ARMS:
        run = train(tag, extra, passes)
        if not run:
            continue
        table.append((tag, best_objective(os.path.join(S, "sweep_%s.out" % tag)), screen(tag, run)))
    say("TABLE  arm | best val objective (lower is better) | eval score vs example.nnue | bootstrap")
    for tag, obj, ev in table:
        say("TABLE  %-9s | %s | %s | %s" % (tag, obj, None if not ev else round(ev["score"], 3),
                                            None if not ev else ev.get("bootstrap_interval")))
    say("Read the objectives: same rows, same split, same seed. Nothing is shipped; the winner would still "
        "have to pass the gates.")
    say("exit")


if __name__ == "__main__":
    main()
