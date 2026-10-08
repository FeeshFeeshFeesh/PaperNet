"""HANDOFF 231cg: does the game's result help, beside the teacher's score? One arm, the sweep's protocol.

Every row the self-play importer writes already carries `outcome` (+1/0/-1 from the mover's view) and
`outcome_ok`, and nothing is ever trained on them -- the label is always tanh(search score / 600). A
search score is a sharp, short-horizon opinion; the result of the game is a blunt, long-horizon fact,
and mixing the two is what NNUE training elsewhere does. `make_wdl_set.py` turns those fields into a
second dataset (target = outcome, weight 0.25, kind KIND_RETURN, the source set's own feature cache),
which is the same shape sploppy's `human` import produces, so nothing in the trainer needs changing.

Protocol copied from sweep_s1.py so the numbers line up with its table: sf1's seed, split, knobs and
4 passes, the step count computed from the TEACHER rows alone (so this arm trains exactly as many steps
as the baseline), and the same fixed-node eval on the same openings. The WDL rows are all TRAIN, so the
validation rows are the baseline's rows and the objectives are like-for-like.

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
TEACHER = ["selfplay_sf1_ex_50k:1.0", "selfplay_n1_25k:1.0"]
WDL_SHARE = 0.5                                   # half a teacher set's sampling share, rows weighted 0.25
BASE_RUN = "sf1_ex_50k"
S1_TRAIN_LOG = os.path.join(T, "logs", BASE_RUN + ".train.log")
SWEEP_LOG = os.path.join(S, "sweep_s1.log")
OUT = os.path.join(S, "wdl_arm.log")
TAG = "wdl"
RUN = "sweep_wdl"
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
    line = "%s WDL %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
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


def best_teacher_objective(path):
    """The baseline's objective formula, restricted to the TEACHER sets.

    nnue.train's own objective is the share-weighted mean of every --data set's validation mse
    DIVIDED BY THE TOTAL SHARE, and a set with no validation rows still counts in that total. The
    WDL sets are train-only by construction, so their zero would scale this arm's reported number
    by shares(teacher)/shares(all) and make it look better than the baseline for no reason. This
    recomputes the number over the teacher sets alone, which is exactly what the baseline reports."""
    names = [x.split(":")[0] for x in TEACHER]
    shares = {x.split(":")[0]: float(x.split(":")[1]) for x in TEACHER}
    best = None
    for line in read(path).splitlines():
        if not line.startswith("{") or '"validation"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        top, bottom = 0.0, 0.0
        for name, metrics in (rec.get("validation") or {}).items():
            if name not in shares or not metrics or not metrics.get("rows"):
                continue
            selection = metrics.get("selection") or metrics
            top += float(selection["mse"]) * shares[name]
            bottom += shares[name]
        if bottom:
            objective = top / bottom
            best = objective if best is None else min(best, objective)
    return best


def wait_its_turn():
    """One trainer at a time, and never beside a move-time SPRT."""
    said = False
    while True:
        # "[0-9]_gates.py" and not "_gates.py": after_gates.py is a queue, not a timed SPRT
        busy = [x for x in ("sweep_s1.py", "[0-9]_gates.py", "mix_train") if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)


def make_sets():
    """Derive one WDL set per teacher set, by rewriting make_wdl_set.py's SETS line."""
    names = [x.split(":")[0] for x in TEACHER]
    src, dst = os.path.join(S, "make_wdl_set.py"), os.path.join(S, "make_wdl_set_arm.py")
    lines, hits = open(src).read().splitlines(True), 0
    with open(dst, "w") as fh:
        for line in lines:
            if line.startswith("SETS = "):
                hits += 1
                fh.write("SETS = %r\n" % names)
            else:
                fh.write(line)
    assert hits == 1, "make_wdl_set.py has %d SETS lines" % hits
    p = subprocess.run([VPY, "-u", dst], capture_output=True, text=True, cwd=T)
    for line in (p.stdout + p.stderr).splitlines()[-4:]:
        say("sets: " + line[:200])
    return [n + "_wdl" for n in names if rows_of(n + "_wdl")]


def train(wdl_sets):
    run_dir = os.path.join(REPO, "runs", RUN)
    out = os.path.join(S, "wdl_arm.out")
    teacher_rows = sum(rows_of(x.split(":")[0]) or 0 for x in TEACHER)
    steps = max(10, (teacher_rows * PASSES + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))
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
    for entry in TEACHER:
        cmd += ["--data", entry]
    for name in wdl_sets:
        cmd += ["--data", "%s:%s" % (name, WDL_SHARE)]
    say("training: teacher %d rows, wdl %d rows at share %.2f, %d x %d steps, warmup %d"
        % (teacher_rows, sum(rows_of(n) or 0 for n in wdl_sets), WDL_SHARE, EPOCHS, steps, warmup))
    for attempt in range(1, ATTEMPTS + 1):
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(out, "a"), stderr=subprocess.STDOUT).returncode
        if rc == 0 and os.path.exists(os.path.join(run_dir, "best.nnue")):
            say("trained: reported objective %s, teacher-only objective %s"
                % (best_objective(out), best_teacher_objective(out)))
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
                            stderr=open(os.path.join(S, "wdl_arm.err"), "a")).returncode
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
        say("STOP: %s never completed, so there is no baseline and no sf1 set" % BASE_RUN)
        return
    wdl_sets = make_sets()
    if not wdl_sets:
        say("STOP: no wdl set was written")
        return
    if not train(wdl_sets):
        return
    ev = screen()
    base_obj = best_teacher_objective(S1_TRAIN_LOG)
    base_line = [x for x in read(SWEEP_LOG).splitlines() if "base(s1): score" in x]
    say("TABLE  arm | teacher-only val objective | eval score   (same val rows, same openings)")
    say("TABLE  base(s1) | %s | %s" % (base_obj, base_line[-1].split("score")[-1].strip()[:40] if base_line else "?"))
    say("TABLE  %s | %s | %s" % (TAG, best_teacher_objective(os.path.join(S, "wdl_arm.out")),
                                 ("%.3f" % ev["score"]) if ev else "?"))
    say("exit")


main()
