"""231eh asked the obvious follow-up to 231eg and this answers it: WHERE is sf2's 8.94% better
value error, and is it anywhere the search would care?

A net that is 9% better at held-out value error but worth -4.9 Elo has to be spending that
accuracy somewhere useless. The cheapest hypothesis is that it is better at positions that are
already decided. A position at |target| ~ 1 is won; being more precisely right about HOW won it is
changes no move. A position at |target| ~ 0 is where a move choice is actually made, and an error
there flips a decision. So: split the same clean row set by |target| and score each stratum.

Pre-registered reading, fixed before the numbers land:
  * if sf2's gain is concentrated at |target| >= 0.8 and is small or absent below 0.2, the tie in
    231eg is explained: it learned to be precise about won positions;
  * if the gain is flat across strata, the explanation is elsewhere and this rules one thing out;
  * if the gain is LARGER at |target| < 0.2, the decoupling is deeper than a stratum effect and
    held-out value error is simply not the quantity that matters, which is the more interesting
    and more expensive answer.
Sign agreement (does the net get WHO IS WINNING right) is reported alongside, because that is the
crudest thing a search can act on and neither net's mse says anything about it directly.

Same row set, same function, same game-clustered bootstrap as offline_score.py.
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

S1 = ["selfplay_sf1_ex_50k", "selfplay_n1_25k"]
NETS = [
    ("base(s1)", "runs/sf1_ex_50k/best.nnue", S1),
    ("sf2", "runs/sf2_ex_50k/best.nnue", ["selfplay_sf2_ex_50k", "selfplay_n1_25k", "selfplay_sf1_ex_50k"]),
    ("p8", "runs/sweep_p8/best.nnue", S1),
    ("wdl", "runs/sweep_wdl/best.nnue", S1 + ["selfplay_sf1_ex_50k_wdl"]),
]
EVAL_SET = "selfplay_sf2_ex_50k_nopv"
STRATA = [("|t| < .2  (undecided)", 0.0, 0.2), ("|t| .2-.5", 0.2, 0.5),
          ("|t| .5-.8", 0.5, 0.8), ("|t| >= .8  (decided)", 0.8, 1.01)]
BOOT = 2000
BOOT_SEED = 20260959
THREADS = 2
OUT = os.path.join(S, "offline_strata.log")
RESULT = os.path.join(S, "offline_strata.json")


def say(msg):
    line = "%s STRATA %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def main():
    sys.path.insert(0, PYDIR)
    import numpy as np
    import torch
    from nnue import data as data_mod
    from nnue import export as export_mod
    from nnue import train as train_mod
    from nnue.features import symmetry_table

    torch.set_num_threads(THREADS)
    d = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", EVAL_SET), split=data_mod.VALIDATION)
    rows = d.all()
    keep = np.ones(len(rows["orbit"]), dtype=bool)
    for _, _, sets in NETS:
        for name in sets:
            tr = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", name), split=data_mod.TRAIN)
            keep &= ~np.isin(rows["orbit"], np.unique(np.asarray(tr.rows["orbit"][tr.index])))
    rows = {k: v[np.flatnonzero(keep)] for k, v in rows.items()}
    sel = np.asarray(rows["kind"]) != train_mod.KIND_RETURN
    say("%d rows clean for all %d nets, %d of them in the selection statistic"
        % (len(sel), len(NETS), int(sel.sum())))

    value, err = {}, {}
    for label, net, _ in NETS:
        model = export_mod.load(pathlib.Path(os.path.join(REPO, net)))
        model.eval()
        table = torch.from_numpy(symmetry_table(model.layout))
        vals = []
        for start in range(0, len(rows["board"]), 2048):
            part = {k: v[start:start + 2048] for k, v in rows.items()}
            ids = train_mod.encode(part, model.layout)
            with torch.no_grad():
                views = [model(table[k, ids], qat=True).tanh() for k in range(6)]
            vals.append(torch.stack(views, 1).cpu().numpy())
        v = np.concatenate(vals)
        value[label] = v.mean(axis=1)                       # the symmetry-averaged value
        e = v - np.asarray(rows["target"])[:, None]
        err[label] = (e * e).mean(axis=1)                   # evaluate()'s per-row statistic
        say("%s scored" % label)

    target = np.asarray(rows["target"])
    weight = np.asarray(rows["weight"], dtype=np.float64)
    game = np.asarray(rows["game"])
    rng = np.random.default_rng(BOOT_SEED)
    out = {"strata": [], "eval_set": EVAL_SET, "nets": [n[0] for n in NETS]}
    base = NETS[0][0]

    say("")
    say("%-22s %6s %5s  %-10s %s" % ("stratum", "rows", "games", "base mse", "each net vs base, by game"))
    for name, lo, hi in STRATA:
        m = sel & (np.abs(target) >= lo) & (np.abs(target) < hi)
        if m.sum() < 50:
            say("%-22s %6d  too few rows to report" % (name, int(m.sum())))
            continue
        w, g = weight[m], game[m]
        games = np.unique(g)
        where = [np.flatnonzero(g == x) for x in games]
        base_mse = float((err[base][m] * w).sum() / w.sum())
        row = {"stratum": name, "rows": int(m.sum()), "games": int(len(games)), "base_mse": base_mse,
               "nets": {}}
        cells = []
        for label, _, _ in NETS[1:]:
            a, b = err[label][m], err[base][m]
            diff = float((b * w).sum() / w.sum() - (a * w).sum() / w.sum())   # + means `label` better
            draws = np.empty(BOOT)
            for i in range(BOOT):
                pick = np.concatenate([where[k] for k in rng.integers(0, len(games), len(games))])
                ww = w[pick]
                draws[i] = (b[pick] * ww).sum() / ww.sum() - (a[pick] * ww).sum() / ww.sum()
            q = [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]
            rel = [100.0 * diff / base_mse, 100.0 * q[0] / base_mse, 100.0 * q[1] / base_mse]
            row["nets"][label] = {"mse": float((a * w).sum() / w.sum()), "rel": rel[0],
                                  "rel_interval": rel[1:]}
            cells.append("%s %+6.2f%% [%+6.1f,%+6.1f]%s"
                         % (label, rel[0], rel[1], rel[2], "*" if not (rel[1] <= 0 <= rel[2]) else " "))
        out["strata"].append(row)
        say("%-22s %6d %5d  %.8f  %s" % (name, m.sum(), len(games), base_mse, cells[0]))
        for c in cells[1:]:
            say("%-22s %6s %5s  %-10s  %s" % ("", "", "", "", c))

    say("")
    say("sign agreement (the net calls the right side to win), |target| > .05:")
    strong = sel & (np.abs(target) > 0.05)
    for label, _, _ in NETS:
        ok = np.sign(value[label][strong]) == np.sign(target[strong])
        out.setdefault("sign", {})[label] = float(ok.mean())
        say("  %-10s %.4f of %d rows" % (label, ok.mean(), int(strong.sum())))

    with open(RESULT, "w") as fh:
        json.dump(out, fh, indent=1)
    say("wrote %s" % RESULT)


main()
