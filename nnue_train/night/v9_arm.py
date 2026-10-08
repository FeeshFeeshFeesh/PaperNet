"""HANDOFF 231ci: is the file format itself a lever? Two arms at format 9, the sweep's protocol.

Format 9 is already supported end to end and nothing has ever trained one:
  * the trainer promotes on --init: 6 -> widen_contexts -> 8 -> widen_buckets -> 9 (train.py:215);
  * it adds eight goal-corner rows (the occupant of each goal square, one per perspective) and
    eight output heads chosen by the pieces on the board, bucket = min(7, (pieces - 2) * 8 // 19);
  * both readers take it: the Rust crate (net.rs:198) and the C++ port the gates play
    (verified here -- n1_25k promoted to v9 gave the same bestmove, score, depth and node count
    on all 14 plies of a random line, and the engine printed "format 9 features 13648 heads 8");
  * the promotion is evaluation-preserving, so an arm starts exactly where the baseline started
    and only the new parameters are new.

What it can buy: a goal-corner row is the one feature the piece-square table cannot express (the
board tells you the occupant, not that it is ON the goal), and per-piece-count heads let the
endgame disagree with the opening about what the same activation means. What it can cost: eight
heads on the same rows is eight times the readout to fit from the same data, and this data is
already the binding constraint -- which is exactly why both passes counts are run.

Protocol copied from sweep_s1.py so the numbers land in its table: same init, same two teacher
sets, same seed (so the same validation split), same knobs, same fixed-node eval on the same
openings. Screening, not shipping; a winner would still have to pass the 231bv gates.

No sys.argv: constants below. Run from PyCharm or: .venv/bin/python -u v9_arm.py
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
SETS = ["selfplay_sf1_ex_50k:1.0", "selfplay_n1_25k:1.0"]
BASE_RUN = "sf1_ex_50k"
S1_TRAIN_LOG = os.path.join(T, "logs", BASE_RUN + ".train.log")
SWEEP_LOG = os.path.join(S, "sweep_s1.log")
OUT = os.path.join(S, "v9_arm.log")
SEED = 20260926
HIDDEN, BATCH, EPOCHS, LR = 512, 2048, 20, 0.0001
THREADS, NICE = 2, 15
EVAL_PAIRS, EVAL_SIMS, EVAL_SEED = 100, 8, 20260927
POLL_S = 120
ATTEMPTS = 2
WAIT_FOR = ("sweep_s1.py", "wdl_arm.py", "pv_arm.py", "[0-9]_gates.py", "mix_train")
# tag -> (run name, passes): the same arm at the baseline's step count and at twice it,
# because eight heads have more to fit from the same rows.
ARMS = [("v9", "sweep_v9", 4), ("v9p8", "sweep_v9p8", 8)]



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
    line = "%s V9 %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
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
    """The baseline's objective formula over the teacher sets (every set here IS a teacher set,
    but a zero-row set would still count in the trainer's own divisor, so it is recomputed)."""
    shares = {x.split(":")[0]: float(x.split(":")[1]) for x in SETS}
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
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)


def train(tag, run, passes):
    run_dir = os.path.join(REPO, "runs", run)
    out = os.path.join(S, "v9_arm_%s.out" % tag)
    rows = sum(rows_of(x.split(":")[0]) or 0 for x in SETS)
    steps = max(10, (rows * passes + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))
    warmup = min(200, max(1, steps * EPOCHS // 10))
    if os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("%s: already trained" % tag)
        return True
    cmd = ["nice", "-n", str(NICE), VPY, "-u", "-m", "nnue.train", "--run", run, "--init", INIT,
           "--hidden", str(HIDDEN), "--version", "9", "--batch", str(BATCH),
           "--steps_per_epoch", str(steps), "--epochs", str(EPOCHS), "--lr", str(LR),
           "--weight_decay", "1e-5", "--warmup_steps", str(warmup), "--lr_floor", "0.15",
           "--qat_start_epoch", "1", "--raw_weight", "0.02", "--symmetry_weight", "0.2",
           "--grad_clip", "5", "--patience", str(EPOCHS), "--val_rows", "8192",
           "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED)]
    for entry in SETS:
        cmd += ["--data", entry]
    say("%s: training %d rows, %d passes, %d x %d steps, warmup %d" % (tag, rows, passes, EPOCHS, steps, warmup))
    for attempt in range(1, ATTEMPTS + 1):
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(out, "a"), stderr=subprocess.STDOUT).returncode
        if rc == 0 and os.path.exists(os.path.join(run_dir, "best.nnue")):
            say("%s: trained, objective %s" % (tag, best_objective(out)))
            return True
        say("%s: attempt %d exited rc %d" % (tag, attempt, rc))
        for line in read(out).splitlines()[-3:]:
            say("%s: %s" % (tag, line[:200]))
        time.sleep(60)
    say("%s: GAVE UP" % tag)
    return False


def loads(run):
    """The gates play the C++ port, so a net that only the Rust crate reads is no use."""
    net = os.path.join(REPO, "runs", run, "best.nnue")
    eng = os.path.join(S, "ab_bu_bin", "base", "stockfish")
    if not os.path.exists(eng):
        return None
    cmds = ["uci", "setoption name UCI_Variant value rps-v6", "setoption name Threads value 1",
            "setoption name EvalFile value " + net, "isready", "quit"]
    p = subprocess.run([eng], input="\n".join(cmds) + "\n", capture_output=True, text=True)
    good = [x for x in p.stdout.splitlines() if "RPSNNUE1 format" in x]
    bad = [x for x in p.stdout.splitlines() if "info string EvalFile:" in x]
    say("port load: %s" % (good[-1][:120] if good else bad[-1][:120] if bad else "no word from the engine"))
    return bool(good) and not bad


def screen(tag, run):
    path = os.path.join(REPO, "runs", run, "eval_sweep.json")
    if not _usable(path):
        cmd = ["nice", "-n", str(NICE), BOT, "eval",
               "--candidate", "nnue:runs/%s/best.nnue?hash=64" % run,
               "--reference", "nnue:%s?hash=64" % INIT,
               "--sims", str(EVAL_SIMS), "--pairs", str(EVAL_PAIRS),
               "--threads", str(THREADS), "--seed", str(EVAL_SEED)]
        say("%s: eval %d openings at %d nodes (the sweep's openings)" % (tag, EVAL_PAIRS, EVAL_SIMS * 2500))
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                            stderr=open(os.path.join(S, "v9_arm.err"), "a")).returncode
        if rc == 0:
            os.replace(path + ".part", path)  # never leave a half-written eval that looks cached
        if rc != 0:
            say("%s: eval rc %d" % (tag, rc))
            return None
    ev = json.load(open(path))
    games = ev["wins"] + ev["draws"] + ev["losses"]
    ev["score"] = (ev["wins"] + 0.5 * ev["draws"]) / games if games else None
    say("%s: score %.3f over %d games (W/D/L %d/%d/%d), margin %s, bootstrap %s, forfeits %s"
        % (tag, ev["score"], games, ev["wins"], ev["draws"], ev["losses"],
           ev.get("margin"), ev.get("bootstrap_interval"), ev.get("forfeits")))
    return ev


def main():
    say("started pid %d" % os.getpid())
    wait_its_turn()
    if "RUN COMPLETE %s" % BASE_RUN not in read(S1_TRAIN_LOG):
        say("STOP: %s never completed, so there is no baseline and no sf1 set" % BASE_RUN)
        return
    done = []
    for tag, run, passes in ARMS:
        if not train(tag, run, passes):
            continue
        ok = loads(run)
        if ok is False:
            say("%s: the C++ port did not load it; screening anyway, but it could not be gated" % tag)
        done.append((tag, run, screen(tag, run)))
    base_obj = best_objective(S1_TRAIN_LOG)
    base_line = [x for x in read(SWEEP_LOG).splitlines() if "base(s1): score" in x]
    say("TABLE  arm | val objective | eval score   (same val rows, same openings)")
    say("TABLE  base(s1,v8) | %s | %s" % (base_obj, base_line[-1].split("score")[-1].strip()[:40] if base_line else "?"))
    for tag, run, ev in done:
        say("TABLE  %s | %s | %s" % (tag, best_objective(os.path.join(S, "v9_arm_%s.out" % tag)),
                                     ("%.3f" % ev["score"]) if ev else "?"))
    say("exit")


main()
