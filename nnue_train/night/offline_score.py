"""HANDOFF 231dl's pre-registered repair, and 231ef's reason for needing it: score two nets
OFFLINE, on ONE row set that is clean for BOTH, instead of comparing the validation objectives
their own training runs printed.

Why the printed objectives cannot be compared. Each run validates on ITS OWN dataset's split.
`selfplay_sf2_ex_50k`'s validation split holds PV line rows; `selfplay_sf2_ex_50k_nopv`'s holds
roots only; `selfplay_sf1_ex_50k`'s is a different sample again. Two numbers measured on two
different row sets are not a comparison, however many digits they agree to -- 231ef is what that
mistake looks like when it goes all the way (two runs, one number, because they were the same run).

What this does instead:
  * builds ONE evaluation set: the validation rows of EVAL_SET,
  * drops every row whose symmetry ORBIT appears in ANY train split either net was trained on
    (a net that has seen a board's mirror has seen the board),
  * scores every net on exactly those rows with sploppy's own `nnue.train.evaluate`, the same
    function and the same "selection" statistic (kind != outcome) that the trainer's objective is
    built from,
  * and reports the PAIRED difference with a row-level bootstrap, because the two nets are scored
    on identical rows and the paired difference has far less variance than either score alone.

Read the bootstrap as row noise only. It says nothing about how much a different training SEED
would have moved the net; only a repeat run can say that, and none has been done.

Licence: sploppy's tree is MIT. This imports its modules and copies none of its source.
"""
import json
import os
import pathlib
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
PYDIR = os.path.join(REPO, "models", "nnue", "py")

# (label, net file relative to REPO, the datasets that net TRAINED on)
NETS = [
    ("sf1", "runs/sf1_ex_50k/best.nnue", ["selfplay_sf1_ex_50k", "selfplay_n1_25k"]),
    ("sf2", "runs/sf2_ex_50k/best.nnue", ["selfplay_sf2_ex_50k", "selfplay_n1_25k", "selfplay_sf1_ex_50k"]),
]
EVAL_SET = "selfplay_sf2_ex_50k_nopv"   # roots only: the eval rows are positions, never PV lines
LIMIT = None                            # None = every clean row; a small int for a smoke test
BOOT = 2000
BOOT_SEED = 20260958
THREADS = 2
OUT = os.path.join(S, "offline_score.log")
RESULT = os.path.join(S, "offline_score.json")


def say(msg, path=OUT):
    line = "%s SCORE %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    with open(path, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def clean_rows(eval_set, nets, limit=None, log=OUT):
    """The validation rows of `eval_set` whose orbit is in no net's training mixture."""
    import numpy as np
    from nnue import data as data_mod

    d = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", eval_set), split=data_mod.VALIDATION)
    rows = d.all()
    keep = np.ones(len(rows["orbit"]), dtype=bool)
    for label, _, sets in nets:
        for name in sets:
            path = os.path.join(REPO, "runs", "nnue_data", name)
            train = data_mod.Dataset.open(path, split=data_mod.TRAIN)
            seen = np.unique(np.asarray(train.rows["orbit"][train.index]))
            hit = np.isin(rows["orbit"], seen)
            say("%s trained on %s: %d of %d eval rows share an orbit with its train split"
                % (label, name, int(hit.sum()), len(keep)), log)
            keep &= ~hit
    idx = np.flatnonzero(keep)
    if limit:
        idx = idx[:limit]
    say("clean for every net: %d rows of %d" % (len(idx), len(keep)), log)
    return {k: v[idx] for k, v in rows.items()}


class _Rows(object):
    """`evaluate()` touches its dataset only through `.all(limit)`, so a chosen set of rows
    can stand in for a Dataset. This is what lets the nets be scored on rows that are not any
    one dataset's split."""

    def __init__(self, rows):
        self._rows = rows

    def all(self, limit=None):
        return self._rows


def score(rows, nets, boot=BOOT, boot_seed=BOOT_SEED, log=OUT):
    import numpy as np
    import torch
    from nnue import export as export_mod
    from nnue import train as train_mod
    from nnue.features import symmetry_table

    torch.set_num_threads(THREADS)
    out, per_row = {}, {}
    for label, net, _ in nets:
        model = export_mod.load(pathlib.Path(os.path.join(REPO, net)))
        model.eval()
        table = torch.from_numpy(symmetry_table(model.layout))
        t0 = time.time()
        m = train_mod.evaluate(model, _Rows(rows), table, limit=None)
        sel = m["selection"] or m
        out[label] = {"mse": sel["mse"], "mae": sel["mae"], "bias": sel["bias"], "rows": sel["rows"],
                      "seconds": round(time.time() - t0, 1)}
        say("%s: selection mse %.8f  mae %.6f  bias %+.6f on %d rows (%.0fs)"
            % (label, sel["mse"], sel["mae"], sel["bias"], sel["rows"], time.time() - t0), log)
        # the same rows again, keeping each row's squared error, for the paired bootstrap.
        # The weighted mean of these must reproduce evaluate()'s own number; if it does not,
        # the bootstrap is measuring something other than the statistic being reported.
        per_row[label] = _row_errors(model, table, rows)
        check = _weighted(per_row[label], rows, train_mod)
        # float32 inside evaluate(), float64 here, so they agree to about 1e-7 relative and no
        # closer. A tolerance tighter than the arithmetic reports a bug that is not there.
        rel = abs(check - sel["mse"]) / max(sel["mse"], 1e-12)
        say("%s: row-level check %.10f vs evaluate %.10f (relative %.2e, %s)"
            % (label, check, sel["mse"], rel, "agree" if rel < 1e-5 else "DISAGREE"), log)

    labels = [n[0] for n in nets]
    if len(labels) == 2 and boot:
        a, b = per_row[labels[0]], per_row[labels[1]]
        w = np.asarray(rows["weight"], dtype=np.float64)
        keep = np.asarray(rows["kind"]) != train_mod.KIND_RETURN
        a, b, w = a[keep], b[keep], w[keep]
        diff = float((a * w).sum() / w.sum() - (b * w).sum() / w.sum())
        # CLUSTER the bootstrap BY GAME. Rows inside one game are the same position pushed one
        # ply at a time: a 20,955-row set is about 200 independent games, not 20,955 independent
        # samples, and a row-level resample reports an interval several times too narrow. The
        # smoke test is what showed this -- its first 2,048 rows (a prefix, not a sample: both
        # Dataset.all(limit) and the trainer's own --val_rows take a PREFIX) came from a couple
        # of dozen games and swung the answer by 15 points with an interval that excluded zero
        # just as confidently. Both are reported below so the cost of getting this wrong is on
        # the record.
        game = np.asarray(rows["game"])[keep]
        games = np.unique(game)
        where = {g: np.flatnonzero(game == g) for g in games}
        rng = np.random.default_rng(boot_seed)
        n = len(a)

        def one(pick):
            ww = w[pick]
            return (a[pick] * ww).sum() / ww.sum() - (b[pick] * ww).sum() / ww.sum()

        flat = np.empty(boot)
        for i in range(boot):
            flat[i] = one(rng.integers(0, n, n))
        clustered = np.empty(boot)
        for i in range(boot):
            pick = np.concatenate([where[g] for g in rng.choice(games, len(games), replace=True)])
            clustered[i] = one(pick)
        lo, hi = float(np.quantile(clustered, 0.025)), float(np.quantile(clustered, 0.975))
        flo, fhi = float(np.quantile(flat, 0.025)), float(np.quantile(flat, 0.975))
        rel = 100.0 * diff / ((b * w).sum() / w.sum())
        out["paired"] = {"a": labels[0], "b": labels[1], "mse_diff": diff, "rel_percent": rel,
                         "interval": [lo, hi], "row_level_interval": [flo, fhi],
                         "rows": int(n), "games": int(len(games)), "boot": boot}
        say("paired %s - %s: mse diff %+.8f (%+.2f%% of %s)" % (labels[0], labels[1], diff, rel, labels[1]), log)
        say("  bootstrap by GAME (%d games, the honest one): [%+.8f, %+.8f]" % (len(games), lo, hi), log)
        say("  bootstrap by ROW  (%d rows, too narrow):      [%+.8f, %+.8f]" % (n, flo, fhi), log)
        say("VERDICT: %s" % ("the game-clustered interval contains zero: no difference these rows can see"
                             if lo <= 0 <= hi else
                             "%s is better on these rows, and the game-clustered interval excludes zero"
                             % (labels[1] if diff > 0 else labels[0])), log)
    return out


def _weighted(err, rows, train_mod):
    """evaluate()'s `selection` mse, recomputed from per-row errors."""
    import numpy as np
    keep = np.asarray(rows["kind"]) != train_mod.KIND_RETURN
    w = np.asarray(rows["weight"], dtype=np.float64)[keep]
    return float((err[keep] * w).sum() / w.sum())


def _row_errors(model, table, rows):
    """Each row's symmetry-averaged squared error, so the bootstrap can resample rows."""
    import numpy as np
    import torch
    from nnue import train as train_mod

    vals = []
    for start in range(0, len(rows["board"]), 2048):
        part = {k: v[start:start + 2048] for k, v in rows.items()}
        ids = train_mod.encode(part, model.layout)
        with torch.no_grad():
            views = [model(table[k, ids], qat=True).tanh() for k in range(6)]
        vals.append(torch.stack(views, 1).cpu().numpy())
    values = np.concatenate(vals)
    err = values - np.asarray(rows["target"])[:, None]
    return (err * err).mean(axis=1)      # evaluate() divides by 6 the same way


def main():
    sys.path.insert(0, PYDIR)
    say("start: %s on %s, eval rows from %s"
        % (", ".join(n[0] for n in NETS), time.strftime("%m-%d %H:%M"), EVAL_SET))
    rows = clean_rows(EVAL_SET, NETS, LIMIT)
    out = score(rows, NETS)
    out["eval_set"], out["limit"] = EVAL_SET, LIMIT
    with open(RESULT, "w") as fh:
        json.dump(out, fh, indent=1)
    say("wrote %s" % RESULT)


main()
