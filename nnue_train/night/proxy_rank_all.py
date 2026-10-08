"""Rank EVERY net on disk with the zero-game proxy, to find arms the 8-sim screen may have buried.

Why this exists.  On 2026-09-25 the 300-pair / 8-sim `bot eval` screen was caught pointing the WRONG
WAY: it called sf2_wdl -15 Elo, and the 500 ms SPRT then made it +33 and shipped it.  The screen's
gate passed by 0.03 of margin -- a +33 Elo net nearly died on a test that could not see it.  The
consequence written into HANDOFF_wdl_match.md is: **any arm rejected on 8-sim screen evidence alone
is suspect.**  This is the cheap half of that audit.  It plays no games and costs nothing, so it can
sweep all 25 nets and produce a shortlist for the one paid SPRT that follows.

What it measures is wdl_proxy.py's question, unchanged: on held-out positions whose game we KNOW
ended in a draw, how winning does the net think it is?  See that file's header for the honest limits
-- in particular this ranks the conversion/draw-calibration mechanism, NOT playing strength, and a
net can score well here and still lose games.  It orders candidates; the SPRT decides.

Nets differ in hidden size and feature version, so each one is read independently and anything that
will not evaluate is reported as SKIP rather than silently dropped.

WHY THE HEADLINE STATISTIC IS NOT draw|pred|.  The first run of this sweep put `scale_1100` on top
with 0.1003 -- far below even the teacher label -- while it had the WORST MSE and correlation in the
field.  `scale_*` is an EVAL_SCALE sweep: a bigger divisor shrinks every eval, drawn or not, so a
low draw|pred| comes for free and means nothing.  draw|pred| is not scale-invariant and must never
rank nets that differ in scale.  Two statistics here are:
    sep  = mean|raw| on DECISIVE rows / mean|raw| on DRAWN rows -- a ratio, so scale cancels.
           It asks the right question directly: does the net separate draws from wins?
    corr = correlation of raw with the true outcome; invariant to any linear rescaling.
Both are printed, the table is sorted by `sep`, and draw|pred| is kept only as a diagnostic.

BOTH ARE COMPUTED ON THE RAW VALUE, NOT THE CLIPPED ONE, and that detail matters.  wdl_proxy.py
clips to [-1,+1] so the prediction can be squared against an outcome of -1/0/+1.  But the clip caps
|eval| on DECISIVE rows at 1.0 while drawn rows sit far below it, so it shrinks sep's numerator only
-- reintroducing exactly the scale dependence sep exists to remove.  The clipped value is still used
for MSE_all and conf>.5, where a bound is what is wanted.

Held-out rows are honest for every arm: make_wdl_set.py line 45 keeps `outcome_ok & (split==TRAIN)`,
so split==2 was never trained on.  Nets predating this dataset never saw any of it.

No sys.argv: constants below, run straight from PyCharm.
"""
import os
import sys
import traceback

import numpy as np

T = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.join(T, "sploppy_repo")
sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))

RUNS = os.path.join(REPO, "runs")
DATA = os.path.join(RUNS, "nnue_data", "selfplay_sf2_ex_50k")
SPLIT = 2                      # 0 train, 1 val, 2 test -- untouched by every arm
ROWS = 30000                   # subsample; 8 GB Mac, keep the fancy-index small
SEED = 20260925                # same seed as wdl_proxy.py so the two runs are comparable
SKIP = ("smoke",)              # a throwaway
# Known ground truth, printed beside the ranking so the ordering can be sanity-checked on sight.
TRUTH = {
    "sf2_wdl":    "SPRT +33 vs live (H1) -- SHIPPED 09-25",
    "sf2_pass6":  "SPRT: lost to sf2_wdl by 23 Elo",
    "sf2_ex_50k": "was live until 09-25",
    "cont512":    "SPRT + (beat wide768 by 46 Elo)",
    "wide768":    "SPRT - (H0 at -46 Elo)",
}


def load_rows():
    split = np.load(os.path.join(DATA, "split.npy"), mmap_mode="r")
    ok = np.load(os.path.join(DATA, "outcome_ok.npy"), mmap_mode="r")
    pool = np.flatnonzero((np.asarray(split) == SPLIT) & np.asarray(ok))
    rng = np.random.default_rng(SEED)
    idx = np.sort(rng.choice(pool, size=min(ROWS, len(pool)), replace=False))
    take = lambda n: np.asarray(np.load(os.path.join(DATA, n + ".npy"), mmap_mode="r")[idx])
    return idx, take("board"), take("since_capture"), take("capture_clock"), \
        take("outcome").astype(np.float64), take("target").astype(np.float64)


def score_net(export, path, board, since, clock, outcome, drawn):
    net = export.read(path)
    net["weights_padded"] = export.pad_rows(net)
    raw = export.integer_eval(net, board, since, clock)
    pred = np.clip(raw, -1.0, 1.0)
    d_raw = float(np.abs(raw[drawn]).mean())
    row = dict(draw_abs=float(np.abs(pred[drawn]).mean()),
               sep=float(np.abs(raw[~drawn]).mean()) / d_raw if d_raw else float("nan"),
               conf=100.0 * float((np.abs(pred[drawn]) > 0.5).mean()),
               mse=float(((pred - outcome) ** 2).mean()),
               corr=float(np.corrcoef(raw, outcome)[0, 1]),
               hidden=int(net.get("hidden", 0)), version=int(net.get("version", 0)))
    del net, raw, pred
    return row


def main():
    from nnue import export

    names = sorted(n for n in os.listdir(RUNS)
                   if n not in SKIP and os.path.isfile(os.path.join(RUNS, n, "best.nnue")))
    idx, board, since, clock, outcome, teacher = load_rows()
    drawn = outcome == 0
    print("held-out split=%d rows: %d  (drawn %d = %.1f%%)  nets: %d"
          % (SPLIT, len(idx), drawn.sum(), 100.0 * drawn.mean(), len(names)))
    print("-----")

    rows, skipped = {}, {}
    for name in names:
        try:
            rows[name] = score_net(export, os.path.join(RUNS, name, "best.nnue"),
                                   board, since, clock, outcome, drawn)
        except Exception as exc:
            skipped[name] = "%s: %s" % (type(exc).__name__, exc)
            continue
        print("  scored %-14s sep %.3f" % (name, rows[name]["sep"]), flush=True)

    print("-----")
    print("%-15s %4s %4s %7s %8s %10s %8s  %s"
          % ("net", "hid", "ver", "sep", "corr", "draw|pred|", "conf>.5", "known truth"))
    t_draw = np.abs(teacher[drawn]).mean()   # the teacher label is already in tanh space, unclipped
    print("%-15s %4s %4s %7.3f %8.4f %10.4f %7.2f%%  %s"
          % ("TEACHER label", "-", "-", np.abs(teacher[~drawn]).mean() / t_draw,
             np.corrcoef(teacher, outcome)[0, 1], t_draw,
             100.0 * (np.abs(teacher[drawn]) > 0.5).mean(),
             "the label the nets are trained on"))
    for name in sorted(rows, key=lambda n: -rows[n]["sep"]):
        r = rows[name]
        print("%-15s %4d %4d %7.3f %8.4f %10.4f %7.2f%%  %s"
              % (name, r["hidden"], r["version"], r["sep"], r["corr"], r["draw_abs"], r["conf"],
                 TRUTH.get(name, "")))
    for name, why in sorted(skipped.items()):
        print("%-15s SKIP  %s" % (name, why))
    print("-----")
    print("sep        : |eval| on decisive rows / |eval| on drawn rows. HIGHER = separates draws")
    print("             from wins. A RATIO, so it survives a net trained at a different eval scale.")
    print("corr       : correlation with the true outcome; also scale-invariant. HIGHER is better.")
    print("draw|pred| : diagnostic only -- NOT comparable across nets of different eval scale.")
    print("A PROXY, NOT A VERDICT. Its job is to pick which net to spend an SPRT on.")
    print("Sanity check before believing any row: cont512 must rank above wide768, and sf2_wdl")
    print("above sf2_pass6 -- an SPRT already settled both of those pairs.")


if __name__ == "__main__":
    main()
