"""Idea #1's two arms, trained and screened on the cloud VM instead of the 8 GB Mac.

Why here.  The mixture needs ~1 GB of ids8 caches resident and the Mac has ~90 MB free with Chrome
up: the local run sat at 0.0% CPU, 100% of samples blocked in _platform_memmove faulting mmap pages.
This host has 62 GB and 16 cores, and it is the same host the SPRT needs afterwards, so the data only
moves once.

The design is unchanged from HANDOFF_wdl_match.md -- same arms, same 552 steps, same seed:

  sf2_wdl    teacher x3 @1.0 + WDL x3 @0.5, 552 steps   -- the candidate
  sf2_pass6  teacher x3 @1.0,               552 steps   -- the control for the extra steps alone

STEPS = 368 x 4.5/3.0 = 552 because Mixture splits each BATCH by share, not the corpus: at 368 the
teacher would fall from 4.00 passes to 2.67 and the arm could not say whether the label helped.

AMENDMENT, recorded before any arm was trained: threads 3 -> 16.  A speed knob.  Batch composition is
driven by the seeded numpy RNG so it is identical; only float reduction order can differ, which is
numerical noise, not a design change.

No sys.argv: constants below.
"""
import datetime
import json
import os
import subprocess

REPO = "/var/tmp/rps/sploppy"
PY = os.path.expanduser("~/tvenv/bin/python")
BOT = os.path.join(REPO, "target", "release", "bot")

INIT = "models/nnue/examples/example.nnue"
LIVE = "runs/sf2_ex_50k/best.nnue"                  # the net every arm has to beat

TEACHER = ["selfplay_sf2_ex_50k:1.0", "selfplay_n1_25k:1.0", "selfplay_sf1_ex_50k:1.0"]
WDL = ["selfplay_sf2_ex_50k_wdl:0.5", "selfplay_n1_25k_wdl:0.5", "selfplay_sf1_ex_50k_wdl:0.5"]

BASE_STEPS = 368                                    # runs/sf2_ex_50k/config.json
STEPS = 552                                         # BASE_STEPS * 4.5/3.0
ARMS = [("sf2_wdl", TEACHER + WDL), ("sf2_pass6", TEACHER)]

HIDDEN, BATCH, EPOCHS, LR = 512, 2048, 20, 0.0001
WARMUP = 200
SEED = 20260929                                     # the baseline's seed
THREADS = 16
SCREEN_PAIRS, SCREEN_SIMS, SCREEN_SEED = 300, 8, 20260927
LOG = "/var/tmp/rps/cloud_wdl_arms.log"
ENV = dict(os.environ, PYTHONPATH=os.path.join(REPO, "models", "nnue", "py"))


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
    t0 = datetime.datetime.now()
    rc = subprocess.run(cmd, cwd=REPO, env=ENV, stdout=fh, stderr=subprocess.STDOUT).returncode
    mins = (datetime.datetime.now() - t0).total_seconds() / 60.0
    if rc != 0:
        log(fh, "TRAIN FAILED %s rc=%d after %.1f min" % (run, rc, mins))
        return False
    log(fh, "TRAIN DONE %s -> runs/%s/best.nnue in %.1f min" % (run, run, mins))
    return True


def screen(fh, run):
    """.part then rename: stdout= creates the file the instant the eval starts, so a killed eval
    would otherwise leave a 0-byte file that looks like a finished result."""
    path = os.path.join(REPO, "runs", run, "h2h_vs_live.json")
    cmd = [BOT, "eval",
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
