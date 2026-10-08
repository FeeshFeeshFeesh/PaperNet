"""Idea #1 on the live recipe: train with the GAME RESULT beside the teacher's score, and control it.

Why.  Every row the self-play importer writes already carries `outcome` (+1/0/-1 from the mover's
view) and `outcome_ok`, and the trainer never looks at them -- the label is always
tanh(search score / 600).  The search score is a sharp short-horizon opinion from a searcher that
could not convert its own won endgames; the outcome is a blunt long-horizon fact.  This is the
`#ifdef CLOCK_DAMP` diagnosis in evaluate.cpp restated as a data problem: the net is taught to hold
+800 into a drawn game because its teacher did exactly that.

  selfplay_sf2_ex_50k : 419,142 train rows know their outcome -- 235,842 of them DRAWS (56%).
  Those are the shuffles.  The teacher scored many of them as wins.

What.  `make_wdl_set.py` turns those fields into a second dataset per source set (target = outcome,
weight 0.25, kind KIND_RETURN, the source set's own feature cache, every row TRAIN so the baseline's
validation rows are untouched).  That is the shape sploppy's `human` import already produces, so
nothing in the trainer changes.

THE TRAP THIS AVOIDS (231dy, and I walked into it again today).  `Mixture` splits each batch by
SHARE, not by rows: three teacher sets at 1.0 and three WDL sets at 0.5 give the teacher 3.0/4.5 =
two thirds of every batch.  Holding the step count at the baseline's 368 therefore drops the teacher
from 4.00 passes to 2.67 -- the arm becomes "two thirds of the baseline's teaching, plus a label",
which cannot say whether the label helped.  STEPS is 552 = 368 x 4.5/3.0, which puts the teacher back
on its 4 passes and adds the outcome rows on top.

Two arms, because 552 steps is itself a change:
  sf2_wdl    teacher + WDL at 552 steps   -- the candidate
  sf2_pass6  teacher ONLY at 552 steps    -- the control: the same extra steps without the labels
Everything else is runs/sf2_ex_50k (the live net) exactly: same init, hidden, batch, lr, schedule,
seed, val_rows.  Threads are 3 instead of 6 because the live bot is playing on this machine; that is
a speed knob.

Each arm is then screened against the LIVE net -- the thing it has to beat, not the init net --
300 pairs at 8 sims, the protocol of runs/sf2_ex_50k/h2h_s1_s2.json.  A screen at that size resolves
about +-0.07 of margin, so it is a go/no-go on spending cloud money, NOT a verdict.  An SPRT at the
real time control decides shipping.

No sys.argv: constants below, run straight from PyCharm.
"""
import datetime
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.dirname(HERE)
REPO = os.path.join(T, "sploppy_repo")
PY = os.path.join(T, ".venv", "bin", "python")
BOT = os.path.join(REPO, "target", "release", "bot")

INIT = "models/nnue/examples/example.nnue"          # relative to REPO, as the baseline's config records it
LIVE = "runs/sf2_ex_50k/best.nnue"                  # the net every arm has to beat

TEACHER = ["selfplay_sf2_ex_50k:1.0", "selfplay_n1_25k:1.0", "selfplay_sf1_ex_50k:1.0"]
WDL = ["selfplay_sf2_ex_50k_wdl:0.5", "selfplay_n1_25k_wdl:0.5", "selfplay_sf1_ex_50k_wdl:0.5"]

BASE_STEPS = 368                                    # runs/sf2_ex_50k/config.json
STEPS = 552                                         # BASE_STEPS * 4.5/3.0: the teacher keeps its 4 passes
ARMS = [("sf2_wdl", TEACHER + WDL), ("sf2_pass6", TEACHER)]

HIDDEN, BATCH, EPOCHS, LR = 512, 2048, 20, 0.0001
WARMUP = 200
SEED = 20260929                                     # the baseline's seed
THREADS, NICE = 3, 15                               # the live bot has the rest of the machine
SCREEN_PAIRS, SCREEN_SIMS, SCREEN_SEED = 300, 8, 20260927
LOG = os.path.join(T, "logs", "wdl2_arm.log")


def log(fh, text):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    fh.write(line + "\n")
    fh.flush()
    print(line, flush=True)


def train(fh, run, sets):
    data_args = []
    for entry in sets:
        data_args += ["--data", entry]
    cmd = [PY, "-u", "-m", "nnue.train", "--run", run, "--init", INIT] + data_args + [
        "--hidden", str(HIDDEN), "--version", "8",
        "--batch", str(BATCH), "--steps_per_epoch", str(STEPS), "--epochs", str(EPOCHS),
        "--lr", str(LR), "--weight_decay", "1e-5", "--warmup_steps", str(WARMUP),
        "--lr_floor", "0.15", "--qat_start_epoch", "1", "--raw_weight", "0.02",
        "--symmetry_weight", "0.2", "--grad_clip", "5", "--patience", str(EPOCHS),
        "--val_rows", "8192", "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED),
    ]
    log(fh, "TRAIN START %s  sets=%s  %dx%d steps batch %d threads %d"
        % (run, " ".join(sets), EPOCHS, STEPS, BATCH, THREADS))
    rc = subprocess.run(["nice", "-n", str(NICE)] + cmd, cwd=REPO,
                        stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        log(fh, "TRAIN FAILED %s rc=%d" % (run, rc))
        return False
    log(fh, "TRAIN DONE %s -> runs/%s/best.nnue" % (run, run))
    return True


def screen(fh, run):
    """`stdout=open(path)` creates the file the instant the eval starts, so a killed eval leaves a
    0-byte file that looks finished. Write a .part and rename, and never trust an unparseable one."""
    path = os.path.join(REPO, "runs", run, "h2h_vs_live.json")
    cmd = ["nice", "-n", str(NICE), BOT, "eval",
           "--candidate", "nnue:runs/%s/best.nnue?hash=64" % run,
           "--reference", "nnue:%s?hash=64" % LIVE,
           "--sims", str(SCREEN_SIMS), "--pairs", str(SCREEN_PAIRS),
           "--threads", str(THREADS), "--seed", str(SCREEN_SEED)]
    log(fh, "SCREEN START %s vs %s: %d pairs at %d sims" % (run, LIVE, SCREEN_PAIRS, SCREEN_SIMS))
    with open(path + ".part", "w") as jf:
        rc = subprocess.run(cmd, cwd=REPO, stdout=jf, stderr=fh).returncode
    if rc != 0:
        log(fh, "SCREEN FAILED %s rc=%d" % (run, rc))
        return None
    try:
        r = json.load(open(path + ".part"))
    except ValueError:
        log(fh, "SCREEN UNPARSEABLE %s" % run)
        return None
    os.replace(path + ".part", path)
    games = r["wins"] + r["draws"] + r["losses"]
    r["score"] = (r["wins"] + 0.5 * r["draws"]) / games if games else float("nan")
    log(fh, "SCREEN RESULT %s score %.4f over %d games (W %d D %d L %d) margin %+0.4f CI [%+0.4f, %+0.4f]"
        % (run, r["score"], games, r["wins"], r["draws"], r["losses"], r.get("margin", 0.0),
           r["interval"][0], r["interval"][1]))
    return r


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    results = {}
    with open(LOG, "a") as fh:
        for entry in TEACHER + WDL:
            prov = os.path.join(REPO, "runs", "nnue_data", entry.split(":")[0], "provenance.json")
            if not os.path.exists(prov):
                log(fh, "GAVE UP: %s has no provenance.json" % entry)
                return
        for run, sets in ARMS:
            if os.path.exists(os.path.join(REPO, "runs", run, "best.nnue")):
                log(fh, "TRAIN SKIP %s: best.nnue already there" % run)
            elif not train(fh, run, sets):
                continue
            results[run] = screen(fh, run)
        log(fh, "TABLE  arm | screen score vs the live net | margin | CI   (reference %s)" % LIVE)
        for run, _ in ARMS:
            r = results.get(run)
            log(fh, "  %-10s %s" % (run, "no result" if not r else
                "%.4f   %+0.4f   [%+0.4f, %+0.4f]" % (r["score"], r.get("margin", 0.0),
                                                      r["interval"][0], r["interval"][1])))
        log(fh, "ARMS COMPLETE")


if __name__ == "__main__":
    main()
