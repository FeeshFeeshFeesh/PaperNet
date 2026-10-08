"""Is "drawn" already in the trunk?  The decisive cheap test for IDEAS #6.1.

The calibration work left exactly one explanation standing for the residual
overclaim: the net's output is a single scalar (`model.py`, `nn.Linear(2H, 1)`),
so it cannot say "I am ahead AND this is still drawn".  The proposed fix is a
second output head -- which costs a file-format bump (version 10), a new Rust
reader, new SIMD kernels, new fixtures and the whole FORMAT9-grade test battery.

Before paying that, answer the question it rests on.  A second head reads the
SAME frozen trunk the scalar reads.  So:

  * If a head-shaped probe on the frozen trunk predicts "this game was drawn"
    much better than the net's own scalar does, the information is already in
    the accumulator and the scalar is the bottleneck.  #6.1 is justified, and
    the AUC gap is an upper bound on what it can buy.

  * If the probe does no better than the scalar, the trunk does not know either.
    A second head has nothing extra to say, and #6.1 should be CLOSED -- the
    defect is upstream, in the features or the data.

The probe is deliberately the same shape as one real output head (a 2H readout
plus the 2H->32->1 dense path), so it is an apples-to-apples stand-in for the
head that #6.1 would add, not a stronger model that could flatter the idea.

Held out BY GAME.  Positions from one game share one outcome, so a row-wise
split would leak the label and every probe would look brilliant.

No sys.argv: constants below, run straight from PyCharm.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(_ROOT)
sys.path.insert(0, str(ROOT / "nnue_train" / "sploppy_repo" / "models" / "nnue" / "py"))

import numpy as np
import torch

from nnue.export import load
from nnue.features import MAX_PIECES
from nnue.model import EVAL_SCALE, DENSE, accumulate
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

NET = ROOT / "nnue_train" / "nets_from_w1" / "sf2_wdl.nnue"      # the live net
DATA = ROOT / "nnue_train" / "sploppy_repo" / "runs" / "nnue_data" / "selfplay_sf2_ex_50k_wdl"

ROWS = 120_000          # subsample; an 8 GB Mac with a live bot on it
TEST_FRACTION = 0.30    # held out by game
SEED = 20260926
EPOCHS = 14
BATCH = 4096
LR = 3e-3
CHUNK = 8192            # rows per trunk forward pass
CLAIM_CP = 200          # the regime the defect lives in
DEEP_CLOCK = 100        # "plenty of clock left": at most this many plies since a capture
TORCH_THREADS = 2       # leave the live bot its clock


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
    ranks = np.empty(len(s), dtype=np.float64)
    start = 0
    for i in range(1, len(s) + 1):
        if i == len(s) or srt[i] != srt[start]:
            ranks[order[start:i]] = (start + 1 + i) / 2.0
            start = i
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


class HeadProbe(torch.nn.Module):
    """Exactly one output head's shape, reading the frozen trunk."""

    def __init__(self, hidden: int) -> None:
        super().__init__()
        self.output = torch.nn.Linear(2 * hidden, 1)
        self.dense = torch.nn.Linear(2 * hidden, DENSE)
        self.delta = torch.nn.Linear(DENSE, 1, bias=False)
        torch.nn.init.zeros_(self.output.weight)
        torch.nn.init.zeros_(self.output.bias)
        torch.nn.init.normal_(self.dense.weight, std=0.03)
        torch.nn.init.constant_(self.dense.bias, 0.2)
        torch.nn.init.zeros_(self.delta.weight)

    def forward(self, act: torch.Tensor) -> torch.Tensor:
        hidden = self.dense(act).clamp(0, 1)
        return (self.output(act) + self.delta(hidden)).squeeze(-1)


class SmallProbe(torch.nn.Module):
    """A logistic regression over a few hand-made columns (the control)."""

    def __init__(self, width: int) -> None:
        super().__init__()
        self.output = torch.nn.Linear(width, 1)
        torch.nn.init.zeros_(self.output.weight)
        torch.nn.init.zeros_(self.output.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.output(x).squeeze(-1)


class LinearProbe(torch.nn.Module):
    """The weakest honest probe: a bare readout, no dense path."""

    def __init__(self, hidden: int) -> None:
        super().__init__()
        self.output = torch.nn.Linear(2 * hidden, 1)
        torch.nn.init.zeros_(self.output.weight)
        torch.nn.init.zeros_(self.output.bias)

    def forward(self, act: torch.Tensor) -> torch.Tensor:
        return self.output(act).squeeze(-1)


def train_probe(probe, act, draw, tr, te, tag):
    """BCE on the training games; returns held-out logits."""
    opt = torch.optim.AdamW(probe.parameters(), lr=LR, weight_decay=1e-4)
    y = torch.from_numpy(draw.astype(np.float32))
    rng = np.random.default_rng(SEED)
    for epoch in range(EPOCHS):
        perm = rng.permutation(tr)
        total, seen = 0.0, 0
        for start in range(0, len(perm), BATCH):
            idx = perm[start : start + BATCH]
            x = act[idx].float()
            loss = torch.nn.functional.binary_cross_entropy_with_logits(probe(x), y[idx])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            total += float(loss) * len(idx)
            seen += len(idx)
        if epoch in (0, EPOCHS // 2, EPOCHS - 1):
            with torch.no_grad():
                held = torch.cat([probe(act[te[s : s + CHUNK]].float()) for s in range(0, len(te), CHUNK)])
            print("    %-12s epoch %2d  train BCE %.4f   held-out AUC %.4f"
                  % (tag, epoch, total / seen, auc(held.numpy(), draw[te])))
    with torch.no_grad():
        return torch.cat([probe(act[te[s : s + CHUNK]].float()) for s in range(0, len(te), CHUNK)]).numpy()


def prepare() -> dict:
    """Load the net, the rows and the frozen trunk, and split held out BY GAME.
    Shared with `draw_shrink.py` so both instruments see the same split."""
    torch.set_num_threads(TORCH_THREADS)
    torch.manual_seed(SEED)

    print("-----[ setup ]-----")
    model = load(NET).eval()
    hidden = model.hidden
    print("  net      %s  version %d  H %d  heads %d" % (NET.name, model.version, hidden, model.heads))

    ids_all = np.load(DATA / "ids8.npy", mmap_mode="r")
    outcome = np.load(DATA / "outcome.npy")
    ok = np.load(DATA / "outcome_ok.npy")
    game = np.load(DATA / "game.npy")
    n_all = len(outcome)
    print("  dataset  %s  rows %d  ids %s" % (DATA.name, n_all, (ids_all.shape,)))

    # Subsample whole games, so the held-out split can be by game.
    rng = np.random.default_rng(SEED)
    games = np.unique(game[ok])
    rng.shuffle(games)
    keep_games, taken, budget = [], 0, ROWS
    counts = {int(g): int(c) for g, c in zip(*np.unique(game[ok], return_counts=True))}
    for g in games:
        if taken >= budget:
            break
        keep_games.append(int(g))
        taken += counts[int(g)]
    keep_games = np.array(sorted(keep_games))
    sel = np.flatnonzero(ok & np.isin(game, keep_games))
    print("  sampled  %d games, %d rows" % (len(keep_games), len(sel)))

    draw = (outcome[sel] == 0)
    print("  draw rate %.3f" % draw.mean())

    # Frozen trunk: the accumulator both the scalar and any second head read.
    print("-----[ trunk forward ]-----")
    with torch.no_grad():
        rows = model.rows(qat=False)
        weight = torch.cat([rows, rows.new_zeros(1, hidden)])
        bias = model.bias
        act = torch.empty((len(sel), 2 * hidden), dtype=torch.float16)
        raw = np.empty(len(sel), dtype=np.float32)
        pieces = np.empty(len(sel), dtype=np.float32)
        for start in range(0, len(sel), CHUNK):
            block = sel[start : start + CHUNK]
            ids = torch.from_numpy(np.asarray(ids_all[block]).astype(np.int64))
            acc = accumulate(ids.reshape(-1, model.layout.slots), weight) + bias
            a = acc.clamp(0, 1).square().reshape(len(block), 2 * hidden)
            act[start : start + len(block)] = a.half()
            raw[start : start + len(block)] = model.value(a, 0, qat=False).numpy()
            pieces[start : start + len(block)] = (
                (ids[:, 0, :MAX_PIECES] != model.layout.pad).sum(-1).float().numpy()
            )
    cp = raw * EVAL_SCALE
    print("  act %s float16 (%.0f MB)   scalar cp: mean %+0.0f  |cp| mean %0.0f"
          % ((tuple(act.shape),), act.numel() * 2 / 1e6, cp.mean(), np.abs(cp).mean()))

    # Split BY GAME.
    g_sel = game[sel]
    order = rng.permutation(keep_games)
    n_test = max(1, int(len(order) * TEST_FRACTION))
    test_games = set(int(g) for g in order[:n_test])
    is_test = np.array([int(g) in test_games for g in g_sel])
    tr = np.flatnonzero(~is_test)
    te = np.flatnonzero(is_test)
    print("  split    train %d rows / %d games,  test %d rows / %d games"
          % (len(tr), len(keep_games) - n_test, len(te), n_test))
    return {"model": model, "hidden": hidden, "act": act, "cp": cp, "raw": raw,
            "draw": draw, "tr": tr, "te": te, "sel": sel, "pieces": pieces}


def main() -> None:
    ctx = prepare()
    hidden, act, cp, draw = ctx["hidden"], ctx["act"], ctx["cp"], ctx["draw"]
    tr, te, sel, pieces = ctx["tr"], ctx["te"], ctx["sel"], ctx["pieces"]

    print("-----[ baseline: what the net's own scalar already says ]-----")
    base = -np.abs(cp[te])          # drawish = close to zero
    base_auc = auc(base, draw[te])
    print("    %-12s held-out AUC %.4f   (-|cp| predicting a draw)" % ("scalar", base_auc))
    signed_auc = auc(-cp[te], draw[te])
    print("    %-12s held-out AUC %.4f   (sanity: signed cp should be ~0.5)" % ("signed", signed_auc))

    print("-----[ probes on the frozen trunk ]-----")
    lin = train_probe(LinearProbe(hidden), act, draw, tr, te, "linear")
    head = train_probe(HeadProbe(hidden), act, draw, tr, te, "head-shaped")

    print("-----[ verdict ]-----")
    lin_auc, head_auc = auc(lin, draw[te]), auc(head, draw[te])
    print("    scalar |cp|      %.4f" % base_auc)
    print("    linear probe     %.4f   (%+.4f over the scalar)" % (lin_auc, lin_auc - base_auc))
    print("    head-shaped      %.4f   (%+.4f over the scalar)" % (head_auc, head_auc - base_auc))

    print("-----[ the defect's own regime: net claims >= +%d cp ]-----" % CLAIM_CP)
    claim = te[cp[te] >= CLAIM_CP]
    if len(claim) < 200:
        print("    too few claiming rows (%d) to judge" % len(claim))
        return
    d = draw[claim]
    print("    %d held-out rows claim >= +%d cp; %d of them (%.1f%%) were drawn"
          % (len(claim), CLAIM_CP, int(d.sum()), 100 * d.mean()))
    pos = {int(i): j for j, i in enumerate(te)}
    take = np.array([pos[int(i)] for i in claim])
    print("    within them, separating the drawn ones from the won ones:")
    print("      scalar |cp|    AUC %.4f" % auc(-np.abs(cp[claim]), d))
    print("      linear probe   AUC %.4f" % auc(lin[take], d))
    print("      head-shaped    AUC %.4f" % auc(head[take], d))

    # CONTROL.  The clock is already a feature and the search sees it directly,
    # so a probe that only reads "the clock is nearly out" has found nothing the
    # engine lacks.  Fit the same logistic on a few hand-made columns and see
    # how much of the trunk probe's edge it accounts for.
    print("-----[ control: is the probe just reading the clock? ]-----")
    since = np.load(DATA / "since_capture.npy")[sel].astype(np.float32)
    plies = np.load(DATA / "ply.npy")[sel].astype(np.float32)
    columns = np.stack(
        [since, 200.0 - since, plies, pieces, since * since / 200.0, pieces * pieces / 20.0], axis=1
    )
    mu, sd = columns[tr].mean(0), columns[tr].std(0) + 1e-6
    trivial = torch.from_numpy(((columns - mu) / sd).astype(np.float32))
    triv = train_probe(SmallProbe(columns.shape[1]), trivial, draw, tr, te, "clock-only")
    print("    clock-only columns: since_capture, clock left, ply, pieces, and two squares")
    print("      held-out AUC   %.4f   (vs linear probe %.4f, scalar %.4f)"
          % (auc(triv, draw[te]), auc(lin, draw[te]), base_auc))
    print("      in the claim regime AUC %.4f   (vs linear probe %.4f)"
          % (auc(triv[take], d), auc(lin[take], d)))

    # And the sharper cut: where the clock is NOT nearly out, so neither the
    # control nor the search can win by reading it.
    deep = since[claim] <= DEEP_CLOCK
    print("-----[ claiming rows with >= %d plies of clock left ]-----" % (200 - DEEP_CLOCK))
    if deep.sum() < 200:
        print("    too few (%d) to judge" % int(deep.sum()))
        return
    dd = d[deep]
    print("    %d rows, %d of them (%.1f%%) drawn" % (int(deep.sum()), int(dd.sum()), 100 * dd.mean()))
    print("      scalar |cp|    AUC %.4f" % auc(-np.abs(cp[claim][deep]), dd))
    print("      clock-only     AUC %.4f" % auc(triv[take][deep], dd))
    print("      linear probe   AUC %.4f" % auc(lin[take][deep], dd))
    print("      head-shaped    AUC %.4f" % auc(head[take][deep], dd))


if __name__ == "__main__":
    main()
