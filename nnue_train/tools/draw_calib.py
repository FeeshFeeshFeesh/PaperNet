"""Step 2 of IDEAS #6.1: does the JOINTLY trained decisive head still pay?

No sys.argv: constants below, run straight from PyCharm.

`draw_shrink.py` bolted a readout onto a FROZEN net and showed the shrink
`eval = cp * (1 - P(draw))**gamma` is selective (3.7x) and beats the best global
rescale by 27% MSE.  That was deliberately a lower bound: nothing but the new
readout could move.  Here the whole net trains with the head attached, so the
trunk is free to make room for it -- and free to wreck the value head paying
for it.  This file measures both halves of that.

What makes the measurement honest:

  * The rows are the TEST split of `selfplay_sf2_ex_50k_wdl_gs`, built by
    `make_game_split.py` so WHOLE GAMES are held out.  The trainer never saw
    them, and no row of a test game contributed a gradient.  Positions of one
    game share one outcome, so a row-wise split leaks the label outright.
  * `dec_ctl` is the control: the same net, the same data, the same steps, no
    head.  Without it, "the decisive run beats the incumbent" says nothing --
    continued training alone would do that.
  * The rescale control is repeated per model.  The eval scale is free, so a
    head that merely makes the net quieter is indistinguishable from a global
    constant unless you fit that constant and compare against it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(_ROOT)
sys.path.insert(0, str(ROOT / "nnue_train" / "sploppy_repo" / "models" / "nnue" / "py"))

import numpy as np
import torch

from nnue.export import load
from nnue.model import EVAL_SCALE, NNUE, accumulate
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

DATA = ROOT / "nnue_train" / "sploppy_repo" / "runs" / "nnue_data" / "selfplay_sf2_ex_50k_wdl_gs"
RUNS = ROOT / "nnue_train" / "sploppy_repo" / "runs"
LIVE = ROOT / "nnue_train" / "nets_from_w1" / "sf2_wdl.nnue"

# `best.pt` is selected on value MSE, which on this data peaks within the first
# epoch or two -- the net overfits at once because it is being fine-tuned on data
# it has already seen. The head starts from zero and needs steps, so its own best
# point can be later than the value head's. Reading `latest.pt` as well keeps a
# null result from being an artifact of where early stopping happened to land.
MODELS = [
    ("live (incumbent)", LIVE),
    ("dec_ctl  best   (no head)", RUNS / "dec_ctl" / "best.pt"),
    ("dec_ctl  latest (no head)", RUNS / "dec_ctl" / "latest.pt"),
    ("dec_w1   best   (w 1.00)", RUNS / "dec_w1" / "best.pt"),
    ("dec_w1   latest (w 1.00)", RUNS / "dec_w1" / "latest.pt"),
    ("dec_w025 best   (w 0.25)", RUNS / "dec_w025" / "best.pt"),
    ("dec_w025 latest (w 0.25)", RUNS / "dec_w025" / "latest.pt"),
    # Step 2b: the same head, DECOUPLED. The three runs above trained the value term on
    # the shrunk value, so the value head was free to decalibrate and let the logit clean
    # up after it -- and it did, nearly doubling the rows claimed at +200 cp. These two
    # train the value term on the pre-shrink value, which is the arrangement draw_shrink.py
    # measured to work.
    ("dec_dw1  best   (decoupled, w 1.00)", RUNS / "dec_dw1" / "best.pt"),
    ("dec_dw1  latest (decoupled, w 1.00)", RUNS / "dec_dw1" / "latest.pt"),
    ("dec_dw4  best   (decoupled, w 4.00)", RUNS / "dec_dw4" / "best.pt"),
    ("dec_dw4  latest (decoupled, w 4.00)", RUNS / "dec_dw4" / "latest.pt"),
]

TEST_SPLIT = 2
VALIDATION_SPLIT = 1    # where the serving rule is fitted, so TEST stays untouched
CLAIM_CP = 200          # the regime the defect lives in
CHUNK = 8192
TORCH_THREADS = 2       # leave the live bot its clock
SCALE_GRID = np.linspace(0.10, 3.00, 146)
GAMMA_GRID = np.array([0.0, 0.125, 0.25, 0.375, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0])


def auc(score: np.ndarray, label: np.ndarray) -> float:
    """Rank AUC of `score` predicting `label`, ties averaged."""
    s = np.asarray(score, dtype=np.float64)
    y = np.asarray(label).astype(bool)
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    srt = s[order]
    rank = np.empty(len(s), dtype=np.float64)
    i = 0
    while i < len(srt):
        j = i
        while j + 1 < len(srt) and srt[j + 1] == srt[i]:
            j += 1
        rank[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return float((rank[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def open_model(path: Path) -> NNUE:
    """A `.nnue` file, or a trainer checkpoint rebuilt from its own config."""
    if path.suffix == ".nnue":
        return load(path).eval()
    blob = torch.load(path, map_location="cpu", weights_only=False)
    config = blob["config"]
    model = NNUE(config["hidden"], config["version"], decisive=config.get("decisive_head", False))
    model.load_state_dict(blob["model"])
    return model.eval()


def evaluate(model: NNUE, ids_all: np.ndarray, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Raw cp and, when the model has a head, its decisive logit."""
    hidden = model.hidden
    with torch.no_grad():
        table = model.rows(qat=False)
        weight = torch.cat([table, table.new_zeros(1, hidden)])
        raw = np.empty(len(rows), dtype=np.float32)
        logit = np.empty(len(rows), dtype=np.float32) if model.decisive is not None else None
        for start in range(0, len(rows), CHUNK):
            block = rows[start : start + CHUNK]
            ids = torch.from_numpy(np.asarray(ids_all[block]).astype(np.int64))
            acc = accumulate(ids.reshape(-1, model.layout.slots), weight) + model.bias
            a = acc.clamp(0, 1).square().reshape(len(block), 2 * hidden)
            raw[start : start + len(block)] = model.value(a, 0, qat=False).numpy()
            if logit is not None:
                logit[start : start + len(block)] = model.decisive_logit(a, 0, qat=False).numpy()
    return raw * EVAL_SCALE, logit


def best_scale(cp: np.ndarray, truth: np.ndarray) -> tuple[float, float]:
    """The lowest MSE any GLOBAL rescale of this eval can reach, and where."""
    losses = [float(((np.tanh(cp * c / EVAL_SCALE) - truth) ** 2).mean()) for c in SCALE_GRID]
    i = int(np.argmin(losses))
    return float(SCALE_GRID[i]), losses[i]


def serve(cp: np.ndarray, shrink: np.ndarray, gamma: float, constant: float) -> np.ndarray:
    return cp * (shrink ** gamma) * constant


def fit_serving(cp, shrink, truth, gammas) -> tuple[float, float, float]:
    """The (gamma, constant) serving rule with the lowest MSE on these rows.

    `eval = cp * (1 - P(draw))**gamma * constant` is the rule `draw_shrink.py` measured
    on the frozen net, where it won at gamma 1.00 with constant **1.78** -- not 1.
    The in-model shrink pins both to 1, which is one arbitrary point on this surface, so
    a head can carry real signal and still lose at that point purely because the sigmoid
    is the wrong strength.  Fitting the rule separates "the head is useless" from "the
    serving rule is wrong", and only the first of those is a reason not to build format 10.

    `gammas=[0.0]` collapses to a global rescale, so the same code path produces the
    head-free floor that the head has to clear.  The eval scale is free; without that
    floor a head that merely makes the net quieter looks like a win.
    """
    best = (float("inf"), 0.0, 1.0)
    for g in gammas:
        shrunk = cp * (shrink ** float(g))
        for c in SCALE_GRID:
            mse = float(((np.tanh(shrunk * c / EVAL_SCALE) - truth) ** 2).mean())
            if mse < best[0]:
                best = (mse, float(g), float(c))
    return best


def main() -> None:
    torch.set_num_threads(TORCH_THREADS)

    split = np.load(DATA / "split.npy")
    ok = np.load(DATA / "outcome_ok.npy")
    outcome = np.load(DATA / "outcome.npy")
    game = np.load(DATA / "game.npy")
    ids_all = np.load(DATA / "ids8.npy", mmap_mode="r")

    rows = np.flatnonzero((split == TEST_SPLIT) & ok.astype(bool))
    truth = outcome[rows].astype(np.float64)
    drawn = truth == 0

    # The serving rule below has two free parameters. Fitting them on the same rows that
    # score them would quietly hand every head model two extra degrees of freedom, so they
    # are fitted HERE, on validation, and never refitted afterwards.
    fit_rows = np.flatnonzero((split == VALIDATION_SPLIT) & ok.astype(bool))
    fit_truth = outcome[fit_rows].astype(np.float64)

    print("-----[ held-out rows ]-----")
    print("  dataset %s" % DATA.name)
    print("  TEST rows %d over %d whole games, draw rate %.4f"
          % (len(rows), len(np.unique(game[rows])), drawn.mean()))
    print("  serving rule fitted on VALIDATION: %d rows over %d whole games, draw rate %.4f"
          % (len(fit_rows), len(np.unique(game[fit_rows])), float((fit_truth == 0).mean())))

    report = {}
    for label, path in MODELS:
        if not path.exists():
            print("\n-----[ %s ]-----\n  MISSING: %s" % (label, path))
            continue
        model = open_model(path)
        cp, logit = evaluate(model, ids_all, rows)
        shrink = 1.0 / (1.0 + np.exp(-logit.astype(np.float64))) if logit is not None else np.ones(len(cp))
        served = cp * shrink

        # Fit the serving rule on validation, score it on test. The gamma-0 fit is the
        # head-free floor; both are fitted the same way so the comparison is paired.
        fit_cp, fit_logit = evaluate(model, ids_all, fit_rows)
        fit_shrink = (1.0 / (1.0 + np.exp(-fit_logit.astype(np.float64)))
                      if fit_logit is not None else np.ones(len(fit_cp)))
        _, flat_g, flat_c = fit_serving(fit_cp, fit_shrink, fit_truth, [0.0])
        _, best_g, best_c = fit_serving(fit_cp, fit_shrink, fit_truth, GAMMA_GRID)
        mse_flat = float(((np.tanh(serve(cp, shrink, flat_g, flat_c) / EVAL_SCALE) - truth) ** 2).mean())
        mse_tuned = float(((np.tanh(serve(cp, shrink, best_g, best_c) / EVAL_SCALE) - truth) ** 2).mean())

        mse_raw = float(((np.tanh(cp / EVAL_SCALE) - truth) ** 2).mean())
        mse_served = float(((np.tanh(served / EVAL_SCALE) - truth) ** 2).mean())
        scale_c, scale_mse = best_scale(cp, truth)

        # The claim regime: where the net says "I am winning".
        claim = cp >= CLAIM_CP
        c_drawn = claim & drawn
        c_won = claim & (truth > 0)
        c_lost = claim & (truth < 0)
        cut = 1.0 - shrink
        cut_drawn = float(cut[c_drawn].mean()) if c_drawn.any() else float("nan")
        cut_won = float(cut[c_won].mean()) if c_won.any() else float("nan")
        selectivity = cut_drawn / cut_won if cut_won and np.isfinite(cut_won) and cut_won > 0 else float("nan")

        decided = ~drawn
        rank_raw = auc(cp[decided], truth[decided] > 0)
        rank_served = auc(served[decided], truth[decided] > 0)
        # Two draw AUCs, kept apart on purpose. The scalar one is what the net could
        # already say with its single output; the head one is what the new output adds.
        # Comparing a head model's logit against the incumbent's |cp| would credit the
        # head with everything continued training did as well.
        draw_auc_scalar = auc(-np.abs(cp[claim]), drawn[claim])
        draw_auc_head = auc(-logit[claim], drawn[claim]) if logit is not None else float("nan")
        still_claiming = int((served >= CLAIM_CP).sum())

        print("\n-----[ %s ]-----" % label)
        print("  claim rows %d   drawn %d   won %d   lost %d"
              % (int(claim.sum()), int(c_drawn.sum()), int(c_won.sum()), int(c_lost.sum())))
        print("  MSE vs outcome:  raw %.4f   served %.4f" % (mse_raw, mse_served))
        print("  best GLOBAL rescale: constant %.2f -> MSE %.4f   (served beats it by %+.1f%%)"
              % (scale_c, scale_mse, 100.0 * (scale_mse - mse_served) / scale_mse))
        if logit is not None:
            print("  claim-regime cut: drawn %.1f%%   won %.1f%%   selectivity %.2fx"
                  % (100 * cut_drawn, 100 * cut_won, selectivity))
        print("  ranking AUC among decided rows: raw %.4f -> served %.4f" % (rank_raw, rank_served))
        print("  draw AUC in the claim regime: scalar |cp| %.4f   head %s"
              % (draw_auc_scalar, "n/a" if logit is None else "%.4f" % draw_auc_head))
        print("  rows still >= %d cp after the shrink: %d of %d" % (CLAIM_CP, still_claiming, int(claim.sum())))
        print("  fitted on VAL, scored on TEST:  no head, constant %.2f -> %.4f"
              % (flat_c, mse_flat))
        print("                                  best rule, gamma %.3f constant %.2f -> %.4f   (%+.1f%%)"
              % (best_g, best_c, mse_tuned, 100.0 * (mse_flat - mse_tuned) / mse_flat))
        # A fit pinned to the edge of the grid is not an optimum, it is a grid that stopped
        # too early. Say so rather than let the number be read as a converged answer.
        edges = [n for n, v, g in (("gamma", best_g, GAMMA_GRID), ("constant", best_c, SCALE_GRID))
                 if v >= float(g.max()) - 1e-9 or v <= float(g.min()) + 1e-9]
        if edges and best_g > 0.0:
            print("    NOTE: %s pinned to the grid edge -- widen the grid before trusting this"
                  % " and ".join(edges))

        report[label] = {
            "mse_raw": round(mse_raw, 5),
            "mse_served": round(mse_served, 5),
            "rescale_constant": round(scale_c, 3),
            "rescale_mse": round(scale_mse, 5),
            "cut_drawn": None if not np.isfinite(cut_drawn) else round(cut_drawn, 4),
            "cut_won": None if not np.isfinite(cut_won) else round(cut_won, 4),
            "selectivity": None if not np.isfinite(selectivity) else round(selectivity, 3),
            "rank_auc_raw": round(rank_raw, 4),
            "rank_auc_served": round(rank_served, 4),
            "draw_auc_scalar": round(draw_auc_scalar, 4),
            "draw_auc_head": None if not np.isfinite(draw_auc_head) else round(draw_auc_head, 4),
            "claim_rows": int(claim.sum()),
            "claim_rows_after_shrink": still_claiming,
            "claim_drawn": int(c_drawn.sum()),
            "claim_won": int(c_won.sum()),
            "claim_lost": int(c_lost.sum()),
            "fit_flat_constant": round(flat_c, 3),
            "fit_flat_test_mse": round(mse_flat, 5),
            "fit_gamma": round(best_g, 4),
            "fit_constant": round(best_c, 3),
            "fit_test_mse": round(mse_tuned, 5),
        }

    out = RUNS / "draw_calib.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
