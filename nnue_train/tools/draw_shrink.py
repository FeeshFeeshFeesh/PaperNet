"""Does the draw head actually FIX the overclaim, or just make the net timid?

`draw_probe.py` established the premise: the frozen trunk linearly encodes
"drawn" and the scalar output does not express it. That says a second head has
something to say. It does not say that using it helps.

This is the cheapest possible test of the fix itself. Bolt a decisive readout
onto the FROZEN live net -- trunk frozen, value head frozen, only the new
readout trained -- and shrink the eval by it:

    eval = cp * (1 - P(draw)) ** gamma

then ask the question that decides everything, on held-out games:

    does the shrink suppress the claims in games that were DRAWN, while leaving
    the claims in games that were WON alone?

If it suppresses both equally the head is worthless: that is timidity, the
exact failure mode the calibration work already ruled out for the WDL labels
(the shipped net reached +400 MORE often than the control, not less). The
number to read is the ratio of the two suppressions. `gamma = 0` is the live
net untouched, so row one of the table is the baseline.

This buys the same thing the `tail_weight` calibration table bought: a reason
to build, or a reason not to, before a host is provisioned or a file format is
bumped.

No sys.argv: constants below, run straight from PyCharm.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import torch

from draw_probe import CHUNK, CLAIM_CP, DATA, EPOCHS, LinearProbe, auc, prepare, train_probe

GAMMAS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)  # shrink strength; 0 is the live net untouched
CLAIM_CP_LOCAL = CLAIM_CP                  # the regime the defect lives in


def main() -> None:
    ctx = prepare()
    act, cp, draw = ctx["act"], ctx["cp"], ctx["draw"]
    tr, te, sel, hidden = ctx["tr"], ctx["te"], ctx["sel"], ctx["hidden"]
    outcome = np.load(DATA / "outcome.npy")[sel].astype(np.int64)

    print("-----[ fit the decisive readout (trunk frozen, value head frozen) ]-----")
    probe = LinearProbe(hidden)
    train_probe(probe, act, draw, tr, te, "decisive")
    with torch.no_grad():
        logit = torch.cat([probe(act[te[s : s + CHUNK]].float()) for s in range(0, len(te), CHUNK)]).numpy()
    p_draw = 1.0 / (1.0 + np.exp(-logit))
    print("    held-out P(draw): mean %.3f on drawn rows, %.3f on decided rows"
          % (p_draw[draw[te]].mean(), p_draw[~draw[te]].mean()))

    cp_te, out_te = cp[te], outcome[te]
    claim = cp_te >= CLAIM_CP_LOCAL
    drawn = claim & (out_te == 0)
    won = claim & (out_te == 1)
    lost = claim & (out_te == -1)
    print("-----[ held-out rows the live net calls >= +%d cp ]-----" % CLAIM_CP_LOCAL)
    print("    %d rows: %d drawn, %d won, %d LOST (the net was on the wrong side)"
          % (claim.sum(), drawn.sum(), won.sum(), lost.sum()))

    truth = out_te.astype(np.float64)
    print("-----[ what the shrink does ]-----")
    print("    %-7s %-22s %-22s %-10s  %s" % ("gamma", "DRAWN claims", "WON claims", "LOST", "all held-out"))
    print("    %-7s %-22s %-22s %-10s  %s" % ("", "mean cp / still>=200", "mean cp / still>=200",
                                              "mean cp", "MSE vs outcome"))
    base = None
    for gamma in GAMMAS:
        shrink = np.ones_like(cp_te, dtype=np.float64) if gamma == 0 else (1.0 - p_draw) ** gamma
        new = cp_te * shrink
        mse = float((np.tanh(new / 600.0) - truth) ** 2 @ np.ones(len(truth)) / len(truth))
        row = (gamma,
               new[drawn].mean(), 100 * (new[drawn] >= CLAIM_CP_LOCAL).mean(),
               new[won].mean(), 100 * (new[won] >= CLAIM_CP_LOCAL).mean(),
               new[lost].mean(), mse)
        print("    %-7.2f %+7.0f / %5.1f%%        %+7.0f / %5.1f%%        %+7.0f    %.4f" % row)
        if gamma == 0:
            base = (new[drawn].mean(), new[won].mean(), mse)

    print("-----[ verdict: suppression is only useful if it is SELECTIVE ]-----")
    print("    %-7s %-14s %-14s %s" % ("gamma", "drawn cut", "won cut", "selectivity (drawn/won)"))
    for gamma in GAMMAS:
        if gamma == 0:
            continue
        shrink = (1.0 - p_draw) ** gamma
        new = cp_te * shrink
        cut_d = 1 - new[drawn].mean() / base[0]
        cut_w = 1 - new[won].mean() / base[1]
        ratio = cut_d / cut_w if cut_w > 1e-9 else float("inf")
        print("    %-7.2f %-14s %-14s %.2fx" % (gamma, "%5.1f%%" % (100 * cut_d), "%5.1f%%" % (100 * cut_w), ratio))
    print("    (1.00x would be pure timidity -- every claim shrunk alike, nothing learned.)")
    print("    baseline MSE vs outcome %.4f" % base[2])

    # CONTROL.  Shrinking lowers every claim, and a net that is simply globally
    # overconfident would look better under ANY multiplier -- eval scale is a free
    # knob that was already swept (runs/scale_400, scale_700, scale_1100).  So
    # give the baseline its best constant, and give each gamma its best constant
    # too.  Only the gap that survives belongs to the head.
    print("-----[ control: is this just a global rescale? ]-----")

    def best_scale(shrink):
        grid = np.linspace(0.10, 3.00, 146)
        losses = [float(((np.tanh(cp_te * shrink * c / 600.0) - truth) ** 2).mean()) for c in grid]
        i = int(np.argmin(losses))
        return float(grid[i]), losses[i]

    flat = np.ones_like(cp_te, dtype=np.float64)
    c0, mse0 = best_scale(flat)
    print("    %-22s best constant %.2f -> MSE %.4f   (from %.4f at 1.00)" % ("no head, scale only", c0, mse0, base[2]))
    for gamma in GAMMAS:
        if gamma == 0:
            continue
        c, mse = best_scale((1.0 - p_draw) ** gamma)
        print("    %-22s best constant %.2f -> MSE %.4f   (%+.4f vs scale-only)"
              % ("gamma %.2f + scale" % gamma, c, mse, mse - mse0))
    print("    A head that only rescaled would land ON the scale-only line.")

    print("-----[ sanity: does the shrunk eval still rank positions? ]-----")
    decided = out_te != 0
    print("    AUC of the eval picking the winner among decided rows:")
    for gamma in GAMMAS:
        shrink = np.ones_like(cp_te, dtype=np.float64) if gamma == 0 else (1.0 - p_draw) ** gamma
        print("      gamma %-5.2f AUC %.4f" % (gamma, auc((cp_te * shrink)[decided], out_te[decided] == 1)))


if __name__ == "__main__":
    main()
