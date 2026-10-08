"""Give a WDL dataset a validation/test split that holds out whole GAMES.

No sys.argv: constants below, run straight from PyCharm.

Why this exists
---------------
The `_wdl` datasets carry `split` all-zero -- the relabelling step dropped it --
so the trainer's validation set is empty and `best.pt` is really just the last
epoch. That has to be fixed before anything can be selected or measured.

But the fix cannot be the importer's `assign_splits`. That one splits 90/5/5 by
game and then moves every symmetry orbit seen in more than one split back to
TRAIN. For a value target that is the right guard: it stops a held-out position
from being a mirror image of a trained one. For a *game-outcome* target -- WDL
labels, and the decisive head's BCE anchor -- the leaking unit is the whole
game, because every row of a game shares one outcome. Measured on
`selfplay_sf2_ex_50k`: 55% of the games with validation rows ALSO have training
rows, so the outcome the probe is asked to predict was already shown to it.

The tempting fix is to split at the granularity that closes both holes at once,
the connected components of the game-orbit graph. That was tried here first and
it does not work on this data: drawn games run nearly twice as long (392 rows
against 200), so they touch more positions, share more orbits, and fuse into
one giant component of 564 games that is 79.1% drawn. Everything outside it is
16.4% drawn, against 39.7% overall -- so a component holdout is 3x too
decisive no matter how it is drawn, which is useless for measuring a DRAW head.
Measured, not guessed: the first build of this file produced a validation split
with a 0.189 draw rate against training's 0.600.

So split by whole GAME, stratified on the game's own result so the held-out
draw rate matches, and accept that some held-out positions have a symmetric
twin in a training game. That residual is the lesser leak by far -- it shares a
POSITION, while the game split is what stops it sharing the OUTCOME, which is
the label -- and this file measures it rather than assuming it is small.

Datasets are immutable here (`data.write` refuses to overwrite), so this writes
a SIBLING directory: every column is a symlink to the original, and only
`split.npy` is a new file. `provenance.json` is symlinked deliberately -- the
rows really do have the parent's provenance, and the `ids8` feature-id cache is
bound to that file's sha256, so symlinking keeps the cache valid. What is new
here, the split, is recorded in `split_provenance.json` beside it.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT = pathlib.Path(_ROOT)
DATA = ROOT / "nnue_train" / "sploppy_repo" / "runs" / "nnue_data"

SOURCE = "selfplay_sf2_ex_50k_wdl"
TARGET = "selfplay_sf2_ex_50k_wdl_gs"

VAL_FRACTION = 0.07   # of games, not rows
TEST_FRACTION = 0.07
SEED = 20260926

TRAIN, VALIDATION, TEST = 0, 1, 2


def assign(games: np.ndarray, drawn: np.ndarray) -> dict[int, int]:
    """Whole games to VALIDATION and TEST, stratified on the game's own result.

    Stratifying is the point: drawn and decided games have very different
    lengths and very different draw-head difficulty, so an unstratified draw
    would leave the held-out draw rate several points off the training one.
    """
    rng = np.random.default_rng(SEED)
    split = np.full(len(games), TRAIN, dtype=np.uint8)
    for result in (True, False):
        pool = np.flatnonzero(drawn == result)
        pool = pool[rng.permutation(len(pool))]
        n_val = int(round(VAL_FRACTION * len(pool)))
        n_test = int(round(TEST_FRACTION * len(pool)))
        split[pool[:n_val]] = VALIDATION
        split[pool[n_val : n_val + n_test]] = TEST
        print(f"{'drawn' if result else 'decided':>7} games {len(pool):>5}: {n_val} to VALIDATION, {n_test} to TEST")
    return {int(g): int(s) for g, s in zip(games, split)}


def orbit_overlap(orbit: np.ndarray, split: np.ndarray) -> dict[str, float]:
    """The residual this design accepts: held-out rows whose symmetry orbit also
    appears in a training game."""
    trained = np.unique(orbit[split == TRAIN])
    out = {}
    for name, code in (("validation", VALIDATION), ("test", TEST)):
        held = split == code
        out[name] = round(float(np.isin(orbit[held], trained).mean()), 4)
    return out


def main() -> None:
    source, target = DATA / SOURCE, DATA / TARGET
    if target.exists():
        raise FileExistsError(f"{target} exists; datasets are immutable")

    game = np.asarray(np.load(source / "game.npy", mmap_mode="r"))
    orbit = np.asarray(np.load(source / "orbit.npy", mmap_mode="r"))
    outcome = np.asarray(np.load(source / "outcome.npy", mmap_mode="r"))

    games, index = np.unique(game, return_inverse=True)
    drawn = np.ones(len(games), dtype=bool)
    np.logical_and.at(drawn, index, outcome == 0)
    print(f"{len(game):,} rows, {len(games):,} games, {int(drawn.sum()):,} of them drawn ({drawn.mean():.1%})")

    split_of_game = assign(games, drawn)
    split = np.array([split_of_game[int(g)] for g in game], dtype=np.uint8)

    # The property this file exists to guarantee, and the residual it accepts.
    spanning = sum(1 for _, s in _grouped(np.stack([game, split], 1)) if len(s) > 1)
    assert spanning == 0, f"{spanning} games span a split"
    overlap = orbit_overlap(orbit, split)
    print(f"checked: no game spans a split; residual orbit overlap {overlap}")

    target.mkdir(parents=True)
    for path in sorted(source.iterdir()):
        if path.name == "split.npy":
            continue
        (target / path.name).symlink_to(path.resolve())
    np.save(target / "split.npy", split)

    counts = {int(s): int((split == s).sum()) for s in (TRAIN, VALIDATION, TEST)}
    draw = {int(s): round(float((outcome[split == s] == 0).mean()), 4) for s in counts if counts[s]}
    (target / "split_provenance.json").write_text(
        json.dumps(
            {
                "source": SOURCE,
                "tool": "nnue_train/tools/make_game_split.py",
                "why": "whole-game holdout; the importer's orbit rule leaks the game outcome",
                "granularity": "whole games, stratified on the game's result",
                "orbit_overlap": overlap,
                "seed": SEED,
                "val_fraction": VAL_FRACTION,
                "test_fraction": TEST_FRACTION,
                "rows": counts,
                "draw_rate": draw,
                "note": "every column but split.npy is a symlink; provenance.json is shared so the ids8 cache stays bound",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {target}")
    print(f"rows by split {counts}")
    print(f"draw rate by split {draw}")


def _grouped(frame: np.ndarray):
    """(key, distinct values) for each key in a 2-column array."""
    frame = frame[np.argsort(frame[:, 0], kind="stable")]
    boundary = np.flatnonzero(np.r_[True, frame[1:, 0] != frame[:-1, 0]])
    for start, stop in zip(boundary, np.r_[boundary[1:], len(frame)]):
        yield int(frame[start, 0]), np.unique(frame[start:stop, 1])


if __name__ == "__main__":
    main()
