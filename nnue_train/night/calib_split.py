"""How much of sf2's 10.7% lower value error is a monotone recalibration? (HANDOFF §231dt)

§231dt left two worlds open: either the search cannot use a uniform improvement in value
accuracy, or 600 games cannot see what it is worth. This splits the improvement itself.

A monotone map applied to a value function preserves every ordering it induces. If the best
monotone map fitted to sf1's outputs closes the gap to sf2, then sf2 is not ranking positions
differently -- it is the same ranking, better calibrated, and a search that compares children by
value sees very little of it. If the gap survives recalibration, sf2 genuinely orders positions
better and the honest reading is that 600 games is too few.

Fitted on one half of the clean rows, measured on the other, so the map cannot fit its own test.
Isotonic regression is the best monotone map in weighted least squares (pool adjacent violators).

CAVEAT, stated before the numbers: this engine backs up averages of values, and an average is not
invariant under a monotone map, so "calibration only" does not mean "invisible to search". And
ordering ACROSS positions is not the same as ordering among one position's children. This is
evidence about which of §231dt's two worlds is more likely, not a proof of either.

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
SEED = 20260953                   # a seed of its own; nothing else tonight has used it
BATCH = 1024
MAX_ROWS = 22000                  # half fits the map, half measures it
TORCH_THREADS = 1                 # a 600-game H2H and a 500 ms SPRT are on the same four cores

CASES = [
    ("231dt: where the 10.7% lives", "sf1_ex_50k", "sf2_ex_50k", "selfplay_sf1_ex_50k"),
    ("231dt: the tied flavour, as a control", "sf1_ex_50k", "sf2_ex_50k", "selfplay_n1_25k"),
]


def say(msg):
    print(msg, flush=True)


def mixture_of(run):
    cfg = json.loads((REPO / "runs" / run / "config.json").read_text())
    return [name for name, _share in cfg["data"]]


def column(name, field):
    return np.asarray(np.load(DATA / name / (field + ".npy"), mmap_mode="r"))


def clean_rows(scored, runs):
    """Indices into `scored` that no run has seen, up to symmetry (§231dp's repair, unchanged)."""
    split = column(scored, "split")
    index = np.flatnonzero(split == VALIDATION)
    orbit = column(scored, "orbit")[index]
    seen = np.zeros(len(index), dtype=bool)
    for name in sorted({d for run in runs for d in mixture_of(run)}):
        if name == scored:
            continue
        train_orbits = column(name, "orbit")[column(name, "split") == TRAIN]
        seen |= np.isin(orbit, train_orbits)
    return index[~seen], index


def load_net(run):
    from nnue.model import NNUE
    import torch
    cfg = json.loads((REPO / "runs" / run / "config.json").read_text())["config"]
    model = NNUE(cfg["hidden"], cfg["version"])
    model.load_state_dict(torch.load(REPO / "runs" / run / "best.pt", map_location="cpu", weights_only=False)["model"])
    model.eval()
    return model


def predict(model, rows, table):
    """One scalar per row: the mean of the six symmetry views. evaluate() averages the SQUARED
    error over views instead; the mean view is the quantity a calibration map can act on, and both
    are reported below so the raw number still ties back to §231dt."""
    import torch
    from nnue.train import encode
    views = []
    with torch.no_grad():
        for start in range(0, len(rows["board"]), BATCH):
            part = {k: v[start:start + BATCH] for k, v in rows.items()}
            ids = encode(part, model.layout)
            views.append(torch.stack([model(table[k, ids], qat=True).tanh() for k in range(6)], 1).numpy())
    return np.concatenate(views)


def isotonic(x, y, w):
    """Weighted pool-adjacent-violators. Returns (knot x, knot value) for np.interp."""
    order = np.argsort(x, kind="mergesort")
    xs, ys, ws = x[order], y[order], w[order]
    vals, wts, cnt = [], [], []
    for yi, wi in zip(ys.tolist(), ws.tolist()):
        vals.append(yi); wts.append(wi); cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, c2 = vals.pop(), wts.pop(), cnt.pop()
            v1, w1, c1 = vals.pop(), wts.pop(), cnt.pop()
            vals.append((v1 * w1 + v2 * w2) / (w1 + w2)); wts.append(w1 + w2); cnt.append(c1 + c2)
    return xs, np.repeat(np.asarray(vals), np.asarray(cnt))


def wmse(err, w):
    return float((err * err * w).sum() / w.sum())


def boot_q(qa, qb, w, rng):
    """Paired bootstrap of (weighted mean of qb) - (weighted mean of qa), as a fraction of qa.
    `q` is any per-row non-negative quantity: squared error, or symmetry variance."""
    d = (qb - qa) * w
    point = d.sum() / w.sum()
    out = np.empty(BOOTSTRAP)
    for start in range(0, BOOTSTRAP, 100):
        n = min(100, BOOTSTRAP - start)
        draws = rng.integers(0, len(d), size=(n, len(d)))
        out[start:start + n] = d[draws].sum(axis=1) / w[draws].sum(axis=1)
    out = np.sort(out)
    ref = (qa * w).sum() / w.sum()
    return point, out[int(0.025 * BOOTSTRAP)], out[int(0.975 * BOOTSTRAP)], ref


def boot_gap(da, db, w, rng):
    return boot_q(da * da, db * db, w, rng)


def main():
    sys.path.insert(0, str(REPO / "models/nnue/py"))
    import torch
    torch.set_num_threads(TORCH_THREADS)
    from nnue.features import symmetry_table
    rng = np.random.default_rng(SEED)          # the fit/test split only
    brng = np.random.default_rng(SEED + 1)     # the bootstraps, so adding one does not move the split
    say("PRE-REGISTERED READING (written before the numbers): if the gap between the two nets")
    say("survives recalibration, sf2 orders positions better and 600 games is simply too few. If")
    say("recalibration closes it, the 10.7% is calibration, which a value-ordering search barely")
    say("uses -- and the objective is the wrong target to keep minimising. Both are reported below.")

    for title, run_a, run_b, scored in CASES:
        say("\n" + "=" * 96)
        say("%s   -- %s vs %s on %s" % (title, run_a, run_b, scored))
        keep, index = clean_rows(scored, [run_a, run_b])
        say("   clean rows %d of %d validation rows (%.1f%% kept)"
            % (len(keep), len(index), 100.0 * len(keep) / len(index)))
        if len(keep) < 4000:
            say("   TOO FEW CLEAN ROWS -- not reporting"); continue
        keep = keep[:MAX_ROWS]
        rows = {f: column(scored, f)[keep] for f in
                ("board", "since_capture", "ply", "capture_clock", "target", "weight", "kind")}
        target, weight = rows["target"], rows["weight"]
        views = {}
        for run in (run_a, run_b):
            model = load_net(run)
            table = torch.from_numpy(symmetry_table(model.layout))
            views[run] = predict(model, rows, table)
            e6 = views[run] - target[:, None]
            say("   %-14s evaluate-style mse %.6f   (the §231dt quantity)"
                % (run, float((e6 * e6 * weight[:, None]).sum() / (6 * weight.sum()))))

        half = rng.permutation(len(keep))
        fit, test = half[: len(half) // 2], half[len(half) // 2:]
        say("   fitted on %d rows, measured on the other %d" % (len(fit), len(test)))
        raw, cal = {}, {}
        for run in (run_a, run_b):
            pred = views[run].mean(axis=1)
            raw[run] = pred[test] - target[test]
            xs, ys = isotonic(pred[fit], target[fit], weight[fit])
            cal[run] = np.interp(pred[test], xs, ys) - target[test]
            say("   %-14s view-mean mse %.6f raw -> %.6f recalibrated  (%+.2f%% from calibration alone)"
                % (run, wmse(raw[run], weight[test]), wmse(cal[run], weight[test]),
                   100.0 * (wmse(cal[run], weight[test]) / wmse(raw[run], weight[test]) - 1.0)))

        w = weight[test]
        for label, d in (("RAW         ", raw), ("RECALIBRATED", cal)):
            point, lo, hi, ref = boot_gap(d[run_a], d[run_b], w, brng)
            say("   %s %s - %s = %+.6f (%+.2f%%)  95%% [%+.2f%%, %+.2f%%]  %s"
                % (label, run_b, run_a, point, 100.0 * point / ref, 100.0 * lo / ref, 100.0 * hi / ref,
                   "b better" if hi < 0 else "a better" if lo > 0 else "TIE, interval includes zero"))

        # The same rows under evaluate()'s own metric, split into its two parts. For six views v
        # and a target t, mean_k (v_k - t)^2 = (mean_k v_k - t)^2 + Var_k(v): the error of the
        # average view plus the disagreement between views (§231di). evaluate() reports the sum;
        # they are different quantities and they do not have to move together.
        say("   -- evaluate()'s metric on the SAME test rows, decomposed --")
        parts = {}
        for run in (run_a, run_b):
            e6 = views[run][test] - target[test][:, None]
            full = (e6 * e6).mean(axis=1)
            mean_err = (views[run][test].mean(axis=1) - target[test]) ** 2
            parts[run] = (full, mean_err, full - mean_err)
            say("   %-14s evaluate %.6f = value %.6f + symmetry disagreement %.6f"
                % (run, (full * w).sum() / w.sum(), (mean_err * w).sum() / w.sum(),
                   ((full - mean_err) * w).sum() / w.sum()))
        for i, label in ((0, "EVALUATE    "), (1, "VALUE part  "), (2, "SYMMETRY part")):
            point, lo, hi, ref = boot_q(parts[run_a][i], parts[run_b][i], w, brng)
            say("   %s %s - %s = %+.6f (%+.2f%%)  95%% [%+.2f%%, %+.2f%%]  %s"
                % (label, run_b, run_a, point, 100.0 * point / ref, 100.0 * lo / ref, 100.0 * hi / ref,
                   "b better" if hi < 0 else "a better" if lo > 0 else "TIE, interval includes zero"))


main()
