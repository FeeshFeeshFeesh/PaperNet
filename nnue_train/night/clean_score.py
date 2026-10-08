"""Score two trained nets on ONE row set that is clean for BOTH of them (HANDOFF §231dp).

log.csv's per-dataset `.mse` columns are paired but leaky: a run trained on a mixture is scored
on each member's validation split, and nothing repairs orbit leakage *between* datasets, so the
run with more datasets in its batch has seen more of what it is scored on. This drops every
scored row whose symmetry orbit appears in the TRAIN split of any dataset either net trained on,
then reports both nets on the surviving rows with a paired bootstrap on the difference.

Constants at the top; run it from PyCharm.
"""
import json, pathlib, sys
import numpy as np
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

REPO = pathlib.Path(_ROOT + "/nnue_train/sploppy_repo")
DATA = REPO / "runs/nnue_data"
TRAIN, VALIDATION = 0, 1
BOOTSTRAP = 2000
SEED = 20260931
BATCH = 1024                      # small: this shares an 8 GB machine
MAX_ROWS = 16000                  # enough for a tight paired interval; keeps the run to minutes
TORCH_THREADS = 1                 # a 600-game H2H and a 500 ms SPRT are on the same four cores

# (title, run A, run B, dataset whose VALIDATION split is scored)
CASES = [
    ("231do/231dp: 3x data, re-read clean", "sf1_ex_50k", "sf2_ex_50k", "selfplay_sf1_ex_50k"),
    ("231do/231dp: the other flavour",      "sf1_ex_50k", "sf2_ex_50k", "selfplay_n1_25k"),
]


def say(msg):
    print(msg, flush=True)


def mixture_of(run):
    """The datasets a run trained on, from its own recorded binding."""
    cfg = json.loads((REPO / "runs" / run / "config.json").read_text())
    return [name for name, _share in cfg["data"]]


def column(name, field):
    return np.asarray(np.load(DATA / name / (field + ".npy"), mmap_mode="r"))


def clean_rows(scored, runs):
    """Indices into `scored`'s VALIDATION split that no run has seen, up to symmetry."""
    split = column(scored, "split")
    index = np.flatnonzero(split == VALIDATION)
    orbit = column(scored, "orbit")[index]
    seen = np.zeros(len(index), dtype=bool)
    for name in sorted({d for run in runs for d in mixture_of(run)}):
        if name == scored:
            continue          # a dataset's own split repair already handles this
        train_orbits = column(name, "orbit")[column(name, "split") == TRAIN]
        hit = np.isin(orbit, train_orbits)
        say("      %-26s removes %6d of %6d (%.2f%%)" % (name, hit.sum(), len(index), 100.0 * hit.sum() / len(index)))
        seen |= hit
    return index[~seen], index


def load_net(run):
    from nnue.model import NNUE
    import torch
    cfg = json.loads((REPO / "runs" / run / "config.json").read_text())["config"]
    model = NNUE(cfg["hidden"], cfg["version"])
    model.load_state_dict(torch.load(REPO / "runs" / run / "best.pt", map_location="cpu", weights_only=False)["model"])
    model.eval()
    return model


def per_row_error(model, rows, table):
    """Weighted squared error per row, averaged over the six symmetries -- the same quantity
    train.evaluate() sums into `mse`, kept per row so the difference can be bootstrapped."""
    import torch
    from nnue.train import encode
    values = []
    with torch.no_grad():
        for start in range(0, len(rows["board"]), BATCH):
            part = {k: v[start:start + BATCH] for k, v in rows.items()}
            ids = encode(part, model.layout)
            views = [model(table[k, ids], qat=True).tanh() for k in range(6)]
            values.append(torch.stack(views, 1).numpy())
    values = np.concatenate(values)
    error = values - rows["target"][:, None]
    return (error * error).mean(axis=1) * rows["weight"], rows["weight"]


def main():
    sys.path.insert(0, str(REPO / "models/nnue/py"))
    import torch
    torch.set_num_threads(TORCH_THREADS)
    from nnue.features import symmetry_table
    rng = np.random.default_rng(SEED)

    for title, run_a, run_b, scored in CASES:
        say("\n" + "=" * 96)
        say("%s   -- %s vs %s scored on %s" % (title, run_a, run_b, scored))
        keep, index = clean_rows(scored, [run_a, run_b])
        say("   clean rows %d of %d validation rows (%.1f%% kept)"
            % (len(keep), len(index), 100.0 * len(keep) / len(index)))
        if len(keep) < 2000:
            say("   TOO FEW CLEAN ROWS -- not reporting"); continue
        if len(keep) > MAX_ROWS:
            keep = keep[:MAX_ROWS]        # the split is in file order; the first N is what log.csv uses too
            say("   capped to the first %d clean rows" % MAX_ROWS)
        rows = {f: column(scored, f)[keep] for f in
                ("board", "since_capture", "ply", "capture_clock", "target", "weight", "kind")}
        out = {}
        for run in (run_a, run_b):
            model = load_net(run)
            table = torch.from_numpy(symmetry_table(model.layout))
            err, w = per_row_error(model, rows, table)
            out[run] = err
            say("   %-14s mse %.6f over %d clean rows" % (run, err.sum() / w.sum(), len(err)))
        d = out[run_b] - out[run_a]           # negative: run_b is better
        w = rows["weight"]
        point = d.sum() / w.sum()
        boot = np.empty(BOOTSTRAP)        # in chunks: a (BOOTSTRAP, rows) index array is ~300 MB
        for start in range(0, BOOTSTRAP, 100):
            n = min(100, BOOTSTRAP - start)
            draws = rng.integers(0, len(d), size=(n, len(d)))
            boot[start:start + n] = d[draws].sum(axis=1) / w[draws].sum(axis=1)
        boot = np.sort(boot)
        lo, hi = boot[int(0.025 * BOOTSTRAP)], boot[int(0.975 * BOOTSTRAP)]
        base = out[run_a].sum() / w.sum()
        say("   DIFFERENCE %s - %s = %+.6f  (%+.2f%% of %s)  95%% [%+.6f, %+.6f] = [%+.2f%%, %+.2f%%]"
            % (run_b, run_a, point, 100.0 * point / base, run_a, lo, hi, 100.0 * lo / base, 100.0 * hi / base))
        say("   VERDICT: %s" % ("run_b better, interval excludes zero" if hi < 0 else
                                "run_a better, interval excludes zero" if lo > 0 else
                                "TIE -- the interval includes zero"))


main()
