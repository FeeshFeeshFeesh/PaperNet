"""A ZERO-GAME proxy for the conversion defect: does the net still call drawn positions won?

Why this exists.  A 500 ms SPRT costs hours and cloud money; an 8-sim screen costs less and, as
2026-09-25 proved, can point the WRONG WAY (it said -15 Elo about a net that then won an SPRT by
+33).  Both play games.  This plays none.  It asks the one question idea #1 is actually about:

    take positions whose game we KNOW ended in a draw, and ask the net what it thinks.
    A net with the conversion defect says "winning".  A cured net says "drawn".

That is measurable in seconds from `export.integer_eval`, the NumPy evaluator over the .nnue bytes
-- no torch, no .pt checkpoint, no engine, no opponent.

HONEST LIMITS, so this is not misused the way the screen was:
  * This is a PROXY, not a verdict.  It measures the mechanism, not playing strength.  A net can fix
    its draw calibration and still lose games.  Shipping decisions stay with the SPRT.
  * Its value is ITERATION SPEED: rank many candidates in seconds, send only the best to the SPRT.
  * It is only meaningful for ideas aimed at the eval's draw calibration.  It says nothing about,
    say, a search change.

Held-out rows are honest here: make_wdl_set.py line 45 keeps `outcome_ok & (split == TRAIN)`, so
SPLIT below (2 = test) was never trained on by ANY arm, teacher or WDL.

No sys.argv: constants below, run straight from PyCharm.
"""
import os
import sys

import numpy as np

T = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.join(T, "sploppy_repo")
sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))

DATA = os.path.join(REPO, "runs", "nnue_data", "selfplay_sf2_ex_50k")
NETS = [
    ("live  sf2_ex_50k", os.path.join(REPO, "runs", "sf2_ex_50k", "best.nnue")),
    ("sf2_pass6 (ctrl)", os.path.join(REPO, "runs", "sf2_pass6", "best.nnue")),
    ("sf2_wdl  (cand) ", os.path.join(REPO, "runs", "sf2_wdl", "best.nnue")),
    # VALIDATION PAIR: an SPRT already settled these -- wide768 lost to cont512 by -46 Elo (H0).
    # Both trained on this dataset, so split==2 was held out for them too. If the proxy is worth
    # anything it must rank cont512 ABOVE wide768, i.e. it must reproduce a NEGATIVE result too.
    ("cont512 (SPRT +)", os.path.join(REPO, "runs", "cont512", "best.nnue")),
    ("wide768 (SPRT -)", os.path.join(REPO, "runs", "wide768", "best.nnue")),
]
SPLIT = 2            # 0 train, 1 val, 2 test -- 2 is untouched by every arm
ROWS = 30000         # subsample; 8 GB Mac, keep the fancy-index small
SEED = 20260925
# model.py: `EVAL_SCALE = 600.0  # search score per unit of raw value`, so integer_eval already
# returns the TANH-SPACE value the target lives in (centipawns would be raw * 600). It is
# unbounded, so clip to [-1, +1] to compare it with an outcome of -1/0/+1.


def load_rows():
    split = np.load(os.path.join(DATA, "split.npy"), mmap_mode="r")
    ok = np.load(os.path.join(DATA, "outcome_ok.npy"), mmap_mode="r")
    pool = np.flatnonzero((np.asarray(split) == SPLIT) & np.asarray(ok))
    rng = np.random.default_rng(SEED)
    idx = np.sort(rng.choice(pool, size=min(ROWS, len(pool)), replace=False))
    take = lambda n: np.asarray(np.load(os.path.join(DATA, n + ".npy"), mmap_mode="r")[idx])
    return idx, take("board"), take("since_capture"), take("capture_clock"), \
        take("outcome").astype(np.float64), take("target").astype(np.float64)


def main():
    from nnue import export

    idx, board, since, clock, outcome, teacher = load_rows()
    drawn = outcome == 0
    print("held-out split=%d rows: %d  (drawn %d = %.1f%%, decisive %d)"
          % (SPLIT, len(idx), drawn.sum(), 100.0 * drawn.mean(), (~drawn).sum()))
    print("-----")
    print("%-18s %10s %10s %10s %10s" % ("net", "draw|pred|", "conf>0.5", "MSE_all", "corr"))
    print("%-18s %10.4f %10.2f%% %10.4f %10.4f"
          % ("TEACHER label", np.abs(teacher[drawn]).mean(),
             100.0 * (np.abs(teacher[drawn]) > 0.5).mean(),
             ((teacher - outcome) ** 2).mean(), np.corrcoef(teacher, outcome)[0, 1]))
    for name, path in NETS:
        if not os.path.exists(path):
            print("%-18s MISSING %s" % (name, path))
            continue
        net = export.read(path)
        net["weights_padded"] = export.pad_rows(net)
        raw = export.integer_eval(net, board, since, clock)
        pred = np.clip(raw, -1.0, 1.0)
        print("%-18s %10.4f %10.2f%% %10.4f %10.4f"
              % (name, np.abs(pred[drawn]).mean(),
                 100.0 * (np.abs(pred[drawn]) > 0.5).mean(),
                 ((pred - outcome) ** 2).mean(), np.corrcoef(pred, outcome)[0, 1]))
        del net, raw, pred
    print("-----")
    print("draw|pred| : mean |eval| on positions whose game was DRAWN. LOWER = less delusional.")
    print("conf>0.5   : share of those it calls a clear win/loss. LOWER is better.")
    print("MSE_all    : squared error against the true outcome over all rows. LOWER is better.")
    print("corr       : correlation with the true outcome. HIGHER is better.")
    print("A PROXY, NOT A VERDICT -- rank candidates here, decide with the SPRT.")


if __name__ == "__main__":
    main()
