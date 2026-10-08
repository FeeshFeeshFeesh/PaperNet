"""Idea #4 -- tune the axis that just worked.  Trains on the CLOUD, never on the 8 GB Mac.

WHY THE CLOUD, AS A RULE NOW.  2026-09-25: I ran NumPy eval jobs locally while the live bot was
playing and drove the machine to 842 MB of swap with ~73 MB free.  The site charges wall-clock, so
that came straight out of the bot's move budget -- two of the worst overheads ever seen in a live
game (3.5 s and 7.3 s) were mine.  Anything that touches torch or numpy over the corpus runs here.

WHAT IS BEING TESTED.  The 0.5 WDL share was a guess and it won (+33 Elo vs live, +23 vs the
step-matched control, both SPRT H1), so the neighbourhood around it has never been looked at:

  share sweep   0.25 / 0.75 / 1.00   -- is 0.5 near an optimum, or on a slope we stopped early on?
  fine-tune     init from the LIVE net instead of example.nnue -- never tried, and plausibly the
                bigger of the two, because every arm so far has re-derived from scratch.

STEPS ARE NOT CONSTANT ACROSS ARMS, AND THAT IS THE WHOLE TRAP.  `Mixture` splits each BATCH by
share, not the corpus, so adding WDL rows at share s starves the teacher unless the step count
grows to compensate.  This trap has already been walked into twice on this project.  Holding the
teacher at its baseline 4.00 passes means

    STEPS(s) = 368 * (3.0 + 3s) / 3.0        368 = runs/sf2_ex_50k/config.json

    s=0.25 -> 460      s=0.50 -> 552 (this is sf2_wdl, already trained, not repeated)
    s=0.75 -> 644      s=1.00 -> 736

so every arm below gives the teacher exactly the same number of passes and the SHARE is the only
thing that varies.  An arm trained at a flat 552 steps would confound share with teaching volume.

THE GATE IS THE ZERO-GAME PROXY, NOT THE 8-SIM SCREEN.  cloud_wdl_arms.py screened its arms with
`bot eval --sims 8`.  That screen was later measured to be WRONG IN SIGN -- it called a +33 Elo net
-15.  So this script ranks with `sep` (mean |raw| on decisive rows / mean |raw| on drawn rows) on
the held-out split, which is scale-invariant and has ranked 3/3 correctly including a negative.

  It RANKS.  It does not decide.  One SPRT, on the single best arm, decides -- and a human reads
  the verdict first.  Nothing here ships anything.

No sys.argv: constants below.
"""
import datetime
import json
import os
import subprocess

REPO = "/var/tmp/rps/sploppy"
PY = os.path.expanduser("~/tvenv/bin/python")
LOG = "/var/tmp/rps/cloud_wdl_sweep.log"
RESULT = "/var/tmp/rps/cloud_wdl_sweep.json"

SCRATCH = "models/nnue/examples/example.nnue"     # what every arm so far re-derived from
LIVE = "runs/sf2_wdl/best.nnue"                   # shipped 2026-09-25; the net to beat

TEACHER = ["selfplay_sf2_ex_50k", "selfplay_n1_25k", "selfplay_sf1_ex_50k"]
BASE_STEPS = 368

# (run, wdl share, init, lr, epochs, warmup).  Steps are DERIVED, never typed in -- see the header.
ARMS = [
    ("sf2_wdl025",  0.25, SCRATCH, 1e-4, 20, 200),
    ("sf2_wdl075",  0.75, SCRATCH, 1e-4, 20, 200),
    ("sf2_wdl100",  1.00, SCRATCH, 1e-4, 20, 200),
    # Fine-tune: the PROVEN share, but starting from the live net, with a fifth of the learning rate
    # and a short schedule.  At lr 1e-4 for 20 epochs the init would simply be washed out and this
    # would be sf2_wdl with a different seed, which answers nothing.
    ("sf2_ft_live", 0.50, LIVE,    2e-5,  6,  50),
]

HIDDEN, BATCH = 512, 2048
SEED = 20260929                                   # the baseline's seed, held across the sweep
THREADS = 16

# Proxy ranking: everything new, plus the three nets whose truth an SPRT already settled.
RANK_EXTRA = [("sf2_wdl  (LIVE, +33)", LIVE),
              ("sf2_pass6 (ctrl)", "runs/sf2_pass6/best.nnue"),
              ("sf2_ex_50k (old live)", "runs/sf2_ex_50k/best.nnue")]
DATA_DIR = os.path.join(REPO, "runs", "nnue_data", "selfplay_sf2_ex_50k")
SPLIT, ROWS, PROXY_SEED = 2, 30000, 20260925
ENV = dict(os.environ, PYTHONPATH=os.path.join(REPO, "models", "nnue", "py"))


def steps_for(share):
    """Hold the teacher at its baseline passes: Mixture divides the BATCH, not the corpus."""
    return int(round(BASE_STEPS * (3.0 + 3.0 * share) / 3.0))


def log(fh, text):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    fh.write(line + "\n")
    fh.flush()
    print(line, flush=True)


def train(fh, run, share, init, lr, epochs, warmup):
    steps = steps_for(share)
    data_args = []
    for name in TEACHER:
        data_args += ["--data", "%s:1.0" % name]
    for name in TEACHER:
        data_args += ["--data", "%s_wdl:%g" % (name, share)]
    cmd = [PY, "-u", "-m", "nnue.train", "--run", run, "--init", init] + data_args + [
        "--hidden", str(HIDDEN), "--version", "8",
        "--batch", str(BATCH), "--steps_per_epoch", str(steps), "--epochs", str(epochs),
        "--lr", str(lr), "--weight_decay", "1e-5", "--warmup_steps", str(warmup),
        "--lr_floor", "0.15", "--qat_start_epoch", "1", "--raw_weight", "0.02",
        "--symmetry_weight", "0.2", "--grad_clip", "5", "--patience", str(epochs),
        "--val_rows", "8192", "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED),
    ]
    log(fh, "TRAIN START %s  share %.2f  init %s  lr %g  %dx%d steps" %
        (run, share, init, lr, epochs, steps))
    t0 = datetime.datetime.now()
    rc = subprocess.run(cmd, cwd=REPO, env=ENV, stdout=fh, stderr=subprocess.STDOUT).returncode
    mins = (datetime.datetime.now() - t0).total_seconds() / 60.0
    if rc != 0:
        log(fh, "TRAIN FAILED %s rc=%d after %.1f min" % (run, rc, mins))
        return False
    log(fh, "TRAIN DONE %s in %.1f min" % (run, mins))
    return True


def rank(fh, names):
    """sep = mean|raw| decisive / mean|raw| drawn, on held-out rows. Scale-invariant; higher better."""
    import sys
    sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))
    import numpy as np
    from nnue import export

    split = np.load(os.path.join(DATA_DIR, "split.npy"), mmap_mode="r")
    ok = np.load(os.path.join(DATA_DIR, "outcome_ok.npy"), mmap_mode="r")
    pool = np.flatnonzero((np.asarray(split) == SPLIT) & np.asarray(ok))
    rng = np.random.default_rng(PROXY_SEED)
    idx = np.sort(rng.choice(pool, size=min(ROWS, len(pool)), replace=False))
    take = lambda n: np.asarray(np.load(os.path.join(DATA_DIR, n + ".npy"), mmap_mode="r")[idx])
    board, since, clock = take("board"), take("since_capture"), take("capture_clock")
    outcome = take("outcome").astype(np.float64)
    drawn = outcome == 0
    log(fh, "PROXY rows %d  drawn %d (%.1f%%)" % (len(idx), drawn.sum(), 100.0 * drawn.mean()))

    out = []
    for label, path in names:
        full = path if os.path.isabs(path) else os.path.join(REPO, path)
        if not os.path.exists(full):
            log(fh, "PROXY MISSING %s (%s)" % (label, path))
            continue
        net = export.read(full)
        net["weights_padded"] = export.pad_rows(net)
        raw = export.integer_eval(net, board, since, clock)
        sep = float(np.abs(raw[~drawn]).mean() / np.abs(raw[drawn]).mean())
        corr = float(np.corrcoef(raw, outcome)[0, 1])
        out.append({"net": label, "path": path, "sep": sep, "corr": corr})
        log(fh, "PROXY %-24s sep %.4f  corr %.4f" % (label, sep, corr))
        del net, raw
    out.sort(key=lambda r: -r["sep"])
    return out


def main():
    with open(LOG, "a") as fh:
        log(fh, "SWEEP START  arms=%s" % ", ".join(a[0] for a in ARMS))
        for name in TEACHER:
            for suffix in ("", "_wdl"):
                prov = os.path.join(REPO, "runs", "nnue_data", name + suffix, "provenance.json")
                if not os.path.exists(prov):
                    log(fh, "GAVE UP: %s%s has no provenance.json" % (name, suffix))
                    return
        if not os.path.exists(os.path.join(REPO, LIVE)):
            log(fh, "GAVE UP: the live net %s is not on this host" % LIVE)
            return

        for run, share, init, lr, epochs, warmup in ARMS:
            if os.path.exists(os.path.join(REPO, "runs", run, "best.nnue")):
                log(fh, "TRAIN SKIP %s: best.nnue already there" % run)   # resumable
                continue
            train(fh, run, share, init, lr, epochs, warmup)

        names = [("%s (share %.2f)" % (r, s), "runs/%s/best.nnue" % r) for r, s, _, _, _, _ in ARMS]
        table = rank(fh, names + RANK_EXTRA)
        with open(RESULT, "w") as jf:
            json.dump({"when": datetime.datetime.now().isoformat(), "table": table}, jf, indent=2)
        log(fh, "TABLE sorted by sep (higher = separates draws from wins better)")
        for i, r in enumerate(table, 1):
            log(fh, "  %2d  %-24s sep %.4f  corr %.4f" % (i, r["net"], r["sep"], r["corr"]))
        log(fh, "SWEEP COMPLETE -- this RANKS. The SPRT decides, and a human reads it first.")


if __name__ == "__main__":
    main()
