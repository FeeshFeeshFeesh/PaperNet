"""Step 2c of IDEAS #6.1: give the head its BEST possible shot, then decide.

No sys.argv: constants below, run straight from PyCharm.

Steps 2 and 2b both ran into the same confound, and it is not about the head.
`lr 3e-4` overfits this fine-tune immediately -- every arm, the head-free control
included, selected **epoch 0** -- while the decisive head starts from zero and needs
steps.  So `best.pt` holds a net whose head never trained, and `latest.pt` holds a
trained head bolted to an overfit trunk.  Neither checkpoint is the experiment.

This file removes the schedule conflict entirely, the same way `draw_shrink.py` did:
freeze the value net, train ONLY a readout on its activations.  The difference from
`draw_shrink.py` is which net gets frozen.  That one froze the stale incumbent, which
turned out to flatter the head badly -- continued WDL training alone lifted claim-regime
draw AUC from 0.6598 to 0.8437, so much of what the probe credited to "the trunk knows
and the scalar cannot say it" was really "the incumbent is undertrained".  Here the
frozen net is `dec_ctl`, the retrained head-free control: the strongest value net on
hand, and the one the head actually has to beat.

The question, narrowed as far as it will go:

    given the best value net available, does a draw head -- trained on its frozen
    activations and served under the best rule fitted off the test rows -- beat that
    same net's own global rescale, on whole games neither ever saw?

If the answer is no HERE, it is no everywhere, and format 10 should not be built: no
joint-training schedule can do better than a readout that had the net held still for it.
If the answer is yes, the schedule is the problem and step 2d is a lower-LR run, not a
retreat from the idea.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(_ROOT)
sys.path.insert(0, str(ROOT / "nnue_train" / "sploppy_repo" / "models" / "nnue" / "py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import torch

from nnue.model import EVAL_SCALE, accumulate
from draw_calib import DATA, GAMMA_GRID, SCALE_GRID, auc, fit_serving, open_model, serve
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

RUNS = ROOT / "nnue_train" / "sploppy_repo" / "runs"
LIVE = ROOT / "nnue_train" / "nets_from_w1" / "sf2_wdl.nnue"

# The frozen value nets to bolt a head onto. `live` is kept only as the bridge back to
# draw_shrink.py's published numbers -- dec_ctl is the one that decides the question.
FROZEN = [
    ("dec_ctl best (the control)", RUNS / "dec_ctl" / "best.pt"),
    ("live (incumbent)", LIVE),
]

TRAIN_SPLIT, VALIDATION_SPLIT, TEST_SPLIT = 0, 1, 2
TRAIN_ROWS = 120_000    # subsample; an 8 GB Mac with a live bot on it
CLAIM_CP = 200
CHUNK = 8192
TORCH_THREADS = 2       # leave the live bot its clock
EPOCHS = 14
BATCH = 4096
LR = 3e-3
SEED = 20260926
OUT = RUNS / "head_on_control.json"


def activations(model, ids_all, rows):
    """Frozen 2H activations, plus the raw cp the value head reads off them.

    Stored float16: 419k rows of 1024 floats is 1.7 GB in float32, which this machine
    does not have spare while a trainer and a bot are on it.
    """
    hidden = model.hidden
    with torch.no_grad():
        table = model.rows(qat=False)
        weight = torch.cat([table, table.new_zeros(1, hidden)])
        act = torch.empty((len(rows), 2 * hidden), dtype=torch.float16)
        cp = np.empty(len(rows), dtype=np.float32)
        for start in range(0, len(rows), CHUNK):
            block = rows[start : start + CHUNK]
            ids = torch.from_numpy(np.asarray(ids_all[block]).astype(np.int64))
            acc = accumulate(ids.reshape(-1, model.layout.slots), weight) + model.bias
            a = acc.clamp(0, 1).square().reshape(len(block), 2 * hidden)
            act[start : start + len(block)] = a.half()
            cp[start : start + len(block)] = model.value(a, 0, qat=False).numpy()
    return act, cp * EVAL_SCALE


def train_head(act, drawn, hidden):
    """A bare 2H linear readout, BCE against "this game was drawn".

    Linear on purpose: `draw_probe.py` measured a linear probe and a head-shaped probe
    to a TIE (0.8633 vs 0.8639 on the hardest cut), which is what keeps format 10 small.
    """
    probe = torch.nn.Linear(2 * hidden, 1)
    opt = torch.optim.AdamW(probe.parameters(), lr=LR, weight_decay=1e-4)
    y = torch.from_numpy(drawn.astype(np.float32))
    rng = np.random.default_rng(SEED)
    n = len(drawn)
    for epoch in range(EPOCHS):
        perm = rng.permutation(n)
        total = 0.0
        for start in range(0, n, BATCH):
            idx = perm[start : start + BATCH]
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                probe(act[idx].float()).squeeze(-1), y[idx])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            total += float(loss.detach()) * len(idx)
        if epoch in (0, EPOCHS // 2, EPOCHS - 1):
            print("    epoch %2d  train BCE %.4f" % (epoch, total / n))
    return probe


def p_draw(probe, act):
    with torch.no_grad():
        out = torch.cat([probe(act[s : s + CHUNK].float()).squeeze(-1)
                         for s in range(0, len(act), CHUNK)])
    return torch.sigmoid(out).numpy().astype(np.float64)


def main() -> None:
    torch.set_num_threads(TORCH_THREADS)
    torch.manual_seed(SEED)

    split = np.load(DATA / "split.npy")
    ok = np.load(DATA / "outcome_ok.npy").astype(bool)
    outcome = np.load(DATA / "outcome.npy")
    game = np.load(DATA / "game.npy")
    ids_all = np.load(DATA / "ids8.npy", mmap_mode="r")

    tr = np.flatnonzero((split == TRAIN_SPLIT) & ok)
    va = np.flatnonzero((split == VALIDATION_SPLIT) & ok)
    te = np.flatnonzero((split == TEST_SPLIT) & ok)
    rng = np.random.default_rng(SEED)
    if len(tr) > TRAIN_ROWS:
        tr = np.sort(rng.choice(tr, TRAIN_ROWS, replace=False))

    truth = {k: outcome[v].astype(np.float64) for k, v in (("tr", tr), ("va", va), ("te", te))}
    drawn = {k: v == 0 for k, v in truth.items()}

    print("-----[ rows ]-----")
    print("  dataset %s" % DATA.name)
    for tag, idx in (("train", tr), ("val", va), ("test", te)):
        print("  %-5s %6d rows  %4d whole games  draw rate %.4f"
              % (tag, len(idx), len(np.unique(game[idx])), float((outcome[idx] == 0).mean())))
    # The splits must not share a game, or the label leaks: positions of one game all
    # carry that game's outcome, which is exactly what the head predicts.
    gtr, gva, gte = (set(np.unique(game[i]).tolist()) for i in (tr, va, te))
    print("  game overlap  train/val %d  train/test %d  val/test %d"
          % (len(gtr & gva), len(gtr & gte), len(gva & gte)))
    assert not (gtr & gva) and not (gtr & gte) and not (gva & gte), "games shared between splits"

    report = {}
    for label, path in FROZEN:
        if not path.exists():
            print("\n-----[ %s ]-----\n  MISSING: %s" % (label, path))
            continue
        print("\n-----[ %s ]-----" % label)
        model = open_model(path)
        act, cp = {}, {}
        for tag, idx in (("tr", tr), ("va", va), ("te", te)):
            act[tag], cp[tag] = activations(model, ids_all, idx)

        probe = train_head(act["tr"], drawn["tr"], model.hidden)
        pd = {tag: p_draw(probe, act[tag]) for tag in act}
        shrink = {tag: 1.0 - pd[tag] for tag in pd}

        # Fitted on VALIDATION, scored on TEST -- twice, so the comparison is paired.
        flat_mse_va, flat_g, flat_c = fit_serving(cp["va"], shrink["va"], truth["va"], [0.0])
        tuned_mse_va, best_g, best_c = fit_serving(cp["va"], shrink["va"], truth["va"], GAMMA_GRID)
        mse_flat = float(((np.tanh(serve(cp["te"], shrink["te"], flat_g, flat_c) / EVAL_SCALE)
                           - truth["te"]) ** 2).mean())
        mse_tuned = float(((np.tanh(serve(cp["te"], shrink["te"], best_g, best_c) / EVAL_SCALE)
                            - truth["te"]) ** 2).mean())
        gain = 100.0 * (mse_flat - mse_tuned) / mse_flat

        claim = cp["te"] >= CLAIM_CP
        head_auc = auc(pd["te"][claim], drawn["te"][claim])
        scalar_auc = auc(-np.abs(cp["te"][claim]), drawn["te"][claim])
        decided = ~drawn["te"]
        rank_flat = auc(serve(cp["te"], shrink["te"], flat_g, flat_c)[decided], truth["te"][decided] > 0)
        rank_tuned = auc(serve(cp["te"], shrink["te"], best_g, best_c)[decided], truth["te"][decided] > 0)

        print("  TEST MSE   no head, constant %.2f        -> %.4f" % (flat_c, mse_flat))
        print("             with head, gamma %.3f c %.2f -> %.4f   (%+.1f%%)"
              % (best_g, best_c, mse_tuned, gain))
        edges = [n for n, v, g in (("gamma", best_g, GAMMA_GRID), ("constant", best_c, SCALE_GRID))
                 if v >= float(g.max()) - 1e-9 or v <= float(g.min()) + 1e-9]
        if edges and best_g > 0.0:
            print("    NOTE: %s pinned to the grid edge -- widen it before trusting this"
                  % " and ".join(edges))
        if best_g == 0.0:
            print("    the fit REFUSED the head: gamma 0 was best on validation")
        print("  claim-regime draw AUC  head %.4f   scalar |cp| %.4f" % (head_auc, scalar_auc))
        print("  ranking AUC among decided rows  no head %.4f -> with head %.4f"
              % (rank_flat, rank_tuned))

        report[label] = {
            "flat_constant": round(flat_c, 3), "flat_test_mse": round(mse_flat, 5),
            "gamma": round(best_g, 4), "constant": round(best_c, 3),
            "tuned_test_mse": round(mse_tuned, 5), "gain_pct": round(gain, 2),
            "val_flat_mse": round(flat_mse_va, 5), "val_tuned_mse": round(tuned_mse_va, 5),
            "head_auc_claim": round(head_auc, 4), "scalar_auc_claim": round(scalar_auc, 4),
            "rank_auc_flat": round(rank_flat, 4), "rank_auc_tuned": round(rank_tuned, 4),
            "claim_rows": int(claim.sum()),
        }

    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
