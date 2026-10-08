"""HANDOFF 231cg. Derives a game-outcome ("WDL") dataset from an imported self-play set.

The self-play importer already carries each root's `outcome` (+1/0/-1 from the
mover's view) and `outcome_ok`, but nothing is ever trained on them: the label is
always the teacher's search score. This makes a second dataset out of the rows that
know their outcome -- same positions, same encoder cache, but target = the outcome,
weight 0.25, kind KIND_RETURN -- which is exactly the shape sploppy's own `human`
import produces, so the trainer and its validation metrics already understand it.

Two rules keep the A/B honest:
  * only rows whose split is TRAIN are taken, and they stay TRAIN, so the validation
    rows of the baseline mixture are untouched and the objectives stay comparable;
  * the encoded id cache is re-used by indexing, never re-encoded, so the features
    are bit-identical to the source set's.

No sys.argv; constants below. Safe to re-run: it rewrites the output set.
"""
import hashlib
import json
import os
import shutil
import sys

import numpy as np

T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
SETS = ["selfplay_sf2_ex_50k"]    # source sets under runs/nnue_data
SUFFIX = "_wdl"
WEIGHT = 0.25

sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))
os.chdir(REPO)
from nnue import data as D                                  # noqa: E402
from nnue.paths import data_dir                             # noqa: E402

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

FIELDS = ("board", "since_capture", "ply", "capture_clock", "target", "weight",
          "kind", "outcome", "outcome_ok", "source", "split", "game", "orbit")


def build(name):
    src = data_dir(name)
    out = data_dir(name + SUFFIX)
    rows = {f: np.load(src / (f + ".npy"), mmap_mode="r") for f in FIELDS}
    keep = np.asarray(rows["outcome_ok"]) & (np.asarray(rows["split"]) == D.TRAIN)
    idx = np.flatnonzero(keep)
    n = len(idx)
    if not n:
        print("%s: no train row knows its outcome -- nothing written" % name)
        return
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    outcome = np.asarray(rows["outcome"][idx]).astype(np.int8)
    new = {
        "board": np.asarray(rows["board"][idx]),
        "since_capture": np.asarray(rows["since_capture"][idx]),
        "ply": np.asarray(rows["ply"][idx]),
        "capture_clock": np.asarray(rows["capture_clock"][idx]),
        "target": outcome.astype(np.float32),
        "weight": np.full(n, WEIGHT, dtype=np.float32),
        "kind": np.full(n, D.KIND_RETURN, dtype=np.uint8),
        "outcome": outcome,
        "outcome_ok": np.ones(n, dtype=bool),
        "source": np.asarray(rows["source"][idx]),
        "split": np.full(n, D.TRAIN, dtype=np.uint8),
        "game": np.asarray(rows["game"][idx]),
        "orbit": np.asarray(rows["orbit"][idx]),
    }
    for field, array in new.items():
        np.save(out / (field + ".npy"), array)
    prov = json.loads((src / "provenance.json").read_text(encoding="utf-8"))
    prov = {
        "importer": "scratchpad/make_wdl_set.py (HANDOFF 231cg)",
        "derived_from": {"set": name, "provenance": prov},
        "rule": ("train-split rows with outcome_ok; target = outcome from the mover's view, "
                 "weight %.2f, kind KIND_RETURN; features re-used from the source id cache" % WEIGHT),
        "rows": int(n),
        "outcome_counts": {str(v): int((outcome == v).sum()) for v in (-1, 0, 1)},
    }
    (out / "provenance.json").write_text(json.dumps(prov, indent=2, sort_keys=True), encoding="utf-8")

    ids_src = src / "ids8.npy"
    if ids_src.is_file():
        ids = np.load(ids_src, mmap_mode="r")
        np.save(out / "ids8.npy", np.asarray(ids[idx]))
        (out / "ids8.json").write_text(json.dumps(D.cache_identity(out)), encoding="utf-8")
    opened = D.Dataset.open(out, D.TRAIN)
    print("%s -> %s: %d rows (of %d), outcomes %s, opens with %d train rows%s"
          % (name, name + SUFFIX, n, len(rows["split"]), prov["outcome_counts"], len(opened),
             ", id cache re-used" if "ids" in opened.rows else ", NO id cache"))


for s in SETS:
    build(s)
