# Intransitive engine, tablebase and experiment log

An engine, an exact endgame tablebase, and the measurement harness for
**Intransitive** (the `rps-v6` rules): a 9x9 board game with rock, paper and
scissors pieces in a non-transitive capture relation, won by reaching the
opponent's goal square, drawn after 200 plies without a capture or a goal entry.

This repository is a research record as much as a program. Most of the ideas in
it **failed**, and the failures are written down with the numbers that killed
them, because that is the part that is usually missing.

---

## What is here

| Directory | What it is |
|---|---|
| `sf19_ports/sf19rps/` | Stockfish 19 with the rules hand-ported and a 4-line handcrafted eval. GPL-3.0. |
| `sf19_ports/sf19nn/` | The same port plus `rpsnet.*`, a C++ NNUE inference with NEON kernels. GPL-3.0. |
| `sf19_ports/tools/` | The SPRT harness, seat drivers, and the eval/search probes. |
| `sf19_ports/live/` | The live-play seat and the parsers for the live game log. |
| `tablebase/` | An exact solver for every position with <=5 pieces, plus prober, validators and an independent oracle. |
| `nnue_train/` | The NNUE training pipeline: datasets, mixtures, label formats, gates. |
| `general/` | Cloud costing and burn monitoring, the publish gate, and `IDEAS.md` -- the ranked ledger of what to try next and what is closed. |

Start with **`general/IDEAS.md`**. It is the spine of the project: every
numbered idea, its cost, its verdict, and the evidence.

---

## Building

The two engines are separate programs. Build each from its own directory:

    cd sf19_ports/sf19nn && ./build.sh          # use this, NOT src/Makefile
    cd sf19_ports/sf19rps/src && make -j build ARCH=apple-silicon

`sf19nn/src/Makefile` is vanilla Stockfish's and does not match this tree -- it
fails with exit 2 and no output. `build.sh` is the working entry point.

The tablebase solver is C with no dependencies; the packer needs libzstd:

    cc -O3 -o tb_solve tablebase/tb_solve.c
    cc -O3 -I$HOME/miniconda3/include -L$HOME/miniconda3/lib -lzstd \
       -o tablebase/probe/tb_pack tablebase/probe/tb_pack.c

To check a build of the rules in one line, from `tablebase/`:

    python3 -c "from rps_rules import parse_fen, perft, START_FEN; \
                p=parse_fen(START_FEN); print([perft(p,d) for d in (1,2,3)])"

Correct output is `[36, 1293, 52758]`.

The Python is standard library only, except the trainer (`nnue_train/`), which
needs `numpy` and `torch`.

---

## The tablebase

Every position with **<=5 pieces** is solved exactly, to distance-to-zeroing.

| | |
|---|---|
| Tables | 60 signatures |
| Dense index, if it were ever written | ~230 GiB |
| Solver output, `.dtz.zst` | 1.008 GiB (zstd-12, 54.6x measured) |
| Seekable prober format, `.tbz` | 2.865 GiB |
| Build time, dense -> `.tbz` | ~19 min, single 8-core host |
| Folds | colour x diagonal x rock->paper->scissors relabel = 11.9x |

Nothing uncompressed is ever written to disk: `tb_pack` streams the dense index
through zstd, because the machine this was built on had 8.5 GiB free against
~230 GiB of index. The build is resumable per signature for the same reason.

**The built tables are not in this repository** (1.0 GiB as `.dtz.zst`, 2.9 GiB
as `.tbz`, against GitHub's hard limit of 100 MB per file). Build them locally -- the recipe is in
`RELEASE.md` and the driver is `tablebase/build_tbz.py`. This is the same
arrangement Syzygy uses: ship the generator, not the gigabytes.

Validation, three independent ways:

1. **Spot-check against the solver's own prober** -- ~24M entries, 0 mismatches.
2. **Differential movegen test** vs the engine's `perft` on random sparse
   positions (`tablebase/validate_perft.py`), plus a MultiPV root-move check for
   the evasion rule that perft structurally cannot reach
   (`tablebase/validate_evasions.py`).
3. **An independent oracle** (`tablebase/probe/oracle_tbz.py`): a bottom-up
   retrograde solve written against the rules only, which never consults the
   table. Across 10 signatures -- **1,100,014 positions** -- it agrees on
   **both** exact distance-to-zeroing and WDL, with **0 mismatches**.

   Two things make this stronger than a spot-check. Seven of those signatures
   are reachable only *through* the orbit fold, so the oracle validates the
   fold itself, which the prober structurally cannot. And one of them is a
   three-piece signature (986,236 positions), where a capture lands in a
   *smaller* signature instead of ending the game -- the sub-signature lookup
   path that no one-piece-each position can exercise.

Three bugs surfaced during that oracle work. All three were in the oracle. The
table was right every time.

### One subtlety worth stating

The stored byte is a distance to **zeroing** -- a capture or a goal entry -- not
to the end of the game. The solver does not model the 200-ply draw rule. So a
stored WIN is only actually winnable when

    dtz <= 200 - clock

Without that clamp a prober reports wins that the draw rule takes away. This is
what `tablebase/probe/tb_wdl.py` exists to get right, in one place.

---

## Results

Measured with sequential probability ratio tests (SPRT) on paired games, except
where stated.

**What worked**

- **WDL labels** instead of a scalar score as the training target: **+33 Elo**,
  shipped. The mechanism was confirmed afterwards, not assumed: it halves the
  rate at which the net claims a win in a position that is actually drawn. The
  axis saturates at a mixture share of 0.5 -- pushing it to 0.75 and 1.00 both
  returned H0.
- **A sticky loop lock.** The engine has no representation of repetition, and in
  live play *we* left the repetition first in 80 of 102 cases, never once because
  of a mate. The fix was move stickiness, not evaluation. Live: 1,066 replies,
  zero drift, and the clock bank went from 23 s to 126 s.

**What failed, with the number that killed it**

- **A <=5-piece tablebase probe in search.** 42.8% of live games reach <=5
  pieces, which looked like a large prize. It buys nothing: of 3,728 judged live
  decisions the engine threw away **0** winnable positions. The stalled draws
  that motivated the whole exercise were already dead draws when they arrived.
  Conversion Elo, if it exists, lives at >=6 pieces -- and 6-piece needs 435 GiB
  per signature, so it is not reachable here.
- **No-capture draw grading in the eval.** 22.6% of live games end that way and
  the share tracks score monotonically, so one draw in four looked worth +37
  Elo. Both signs returned H0.
- **Capacity.** 768 vs 512 hidden units: H0 at -46 Elo.
- **Three zero-game proxies** that were supposed to rank nets without playing
  games -- a draw-horizon flat-zone gate, `sep` separation, and the tablebase
  probe. Each looked monotone on held-out data. None predicted Elo. One of them
  (`screen` vs SPRT) had a **measured sign flip**: an 8-simulation screen said
  -15 Elo where a 500 ms SPRT said +33.

The honest summary of that last point: on this problem, cheap proxies for
playing strength did not work, three times, in three different ways. Screens
here are used to veto, never to predict.

---

## Local settings a third party must change

Paths in this tree are anchored on each file's own location, so a fresh checkout
runs without editing -- with three exceptions, all of them machine-specific:

1. **`PY`** (17 files) points at the one virtualenv on the author's machine that
   has `numpy` and `torch`. Repoint it at your own interpreter.
2. **The `gcloud` binary path** (3 files in `general/`). It is not on `PATH`
   there, so it is called by absolute path.
3. **Runtime data under `project_files/`** -- live game logs, status logs, nets.
   None of it ships. Scripts that read it will need their input repointed.

Nothing in this repository reads a credential, and nothing writes one.

---

## Licences

This repository contains more than one licence. Read `RELEASE.md` for the full
inventory; the short version:

- Everything written for this project -- the tablebase solver, packer and probe,
  the training and SPRT tooling, the live harness, and these docs -- is **MIT**.
  See `LICENSE` at the root.
- `sf19_ports/sf19rps/` and `sf19_ports/sf19nn/` are **modified Stockfish** and
  are therefore **GPL-3.0-or-later**. Each carries `Copying.txt` and a
  `NOTICE-modifications.txt` stating what was changed, as GPL-3.0 section 5(a)
  requires. They are separate programs, run as separate processes.
- `sf19nn/src/rpsnet.*` is derived from sploppy's MIT-licensed Rust
  implementation. `LICENSE-sploppy-MIT` travels with it.
- **No network weights are distributed here.** The engine loads a net at runtime
  from a path given as a UCI option. The nets used in development are not ours
  to redistribute.

The root `LICENSE` is not a blanket licence for the whole tree: a licence file
inside a directory governs that directory. The ports stay GPL-3.0 and are
separate programs, so the two licences sit side by side without touching.

Before pushing anything, run the gate:

    python3 general/publish_check.py
