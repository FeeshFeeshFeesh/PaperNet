# Outcome-label (WDL) arm — PRE-REGISTRATION

**Written 2026-09-24 21:0x local, while both arms were still training and before any screen number
existed.** Nothing below was chosen after seeing a result.

## The claim under test

The net is trained only on `tanh(search_score / 600)`. Its teacher is a searcher that could not
convert its own won endgames, so the net is taught to hold a winning score into a drawn game — the
`#ifdef CLOCK_DAMP` note in `evaluate.cpp` says exactly this. Every imported row already carries the
game's `outcome` and `outcome_ok` and the trainer ignores them. Adding those rows as a second
dataset should teach the net that the shuffles are draws.

How much of the corpus this touches: `selfplay_sf2_ex_50k` has 419,142 train rows that know their
outcome, **235,842 of them (56%) drawn**.

## Arms

All knobs identical to `runs/sf2_ex_50k` (the live net): init `example.nnue`, hidden 512, batch 2048,
20 epochs, lr 1e-4, warmup 200, lr_floor 0.15, qat from epoch 1, raw_weight 0.02, symmetry 0.2,
grad_clip 5, val_rows 8192, seed 20260929. Threads 3 not 6 (the live bot holds the machine); a speed
knob only.

| arm | mixture | steps |
|---|---|---|
| live (exists) | teacher x3 @1.0 | 368 |
| `sf2_wdl` | teacher x3 @1.0 + WDL x3 @0.5 | **552** |
| `sf2_pass6` | teacher x3 @1.0 | **552** |

`STEPS = 368 x 4.5/3.0 = 552` because `Mixture` divides the **batch** by share: at 368 the teacher
would fall from 4.00 passes to 2.67 and the arm would be "two thirds of the teaching, plus a label".
`sf2_pass6` is the control for the extra steps alone. See [[nnue-mixture-shares]].

## Stage 1 — local screen (a sanity gate, NOT evidence)

`bot eval`, candidate vs **the live net**, 300 pairs, 8 sims, seed 20260927.

**This screen cannot detect the effect we care about, and must not be reported as if it could.**
At 300 pairs the bootstrap half-width is about ±0.068 of margin (measured:
`runs/sf2_ex_50k/h2h_s1_s2.json`). The gain worth shipping is ~20 Elo ≈ 0.029 of margin — less than
half the resolution. So the screen can only catch a net that is *badly* worse.

- **GO** to stage 2 if `sf2_wdl`'s CI upper bound ≥ 0 (i.e. not clearly worse).
- **NO-GO** if the CI upper bound < 0. Then #1 has failed cheaply and the cloud day is saved.
- `sf2_pass6`'s screen is recorded but **does not select** anything at this stage.

## Stage 2 — the SPRT that decides (pre-registered in full)

Candidate `sf2_wdl` vs the live net `runs/sf2_ex_50k/best.nnue`.

- 500 ms/move, 1 thread per seat, 3 pairs in flight
- **`--opening-plies 24`** (not 8 — at 8 almost every game draws and the SPRT returns H0 regardless
  of the truth; see [[wide-arm-openings]])
- H0 = +0 Elo, H1 = +20 Elo, alpha = beta = 0.05, **cap 1000 pairs**
- seed 20260996 (fixed now, before any game)
- host: one **c4a-standard-16**, asia-southeast1-b, built from `rps-w1`'s disk — TERMINATED but it
  still holds `/var/tmp/rps/wide_cloud.py` and the seat setup from the wide-arm match
- ~15 pairs in flight ≈ 115 pairs/hr, so the cap is ~8.7 h ≈ A$10.7 at ~A$1.232/hr (verify the
  price with `cloud_prices.py` before launching; A$50/day ceiling unchanged)
- self-shutdown on completion **and** a deadman `shutdown -h` backstop, as the wide match had

### Why the cap moved from 400 to 1000 (amended before any game was played)

The wide-arm match used cap 400. A power calculation (`scratchpad/sprt_power.py`, normal
approximation to the pentanomial SPRT) puts the expected stopping time at:

| pairing correlation | E[pairs to a bound] |
|---|---|
| 1.00 (no pairing gain) | 445 |
| 0.80 | 356 |
| 0.70 | 312 |
| 0.60 | 267 |

with `A = log(0.95/0.05) = 2.944`, an H1 score of 0.5288 and a 50% decisive rate at
`--opening-plies 24`. **A cap at 400 sits at the mean.** Stopping times are right-skewed, so a large
share of runs would end in `cap = not separable` — the one outcome that buys nothing. The wide match
escaped this only because the truth was −46 Elo, far outside both hypotheses, so its LLR ran to H0
quickly.

Going wider rather than longer keeps it to one host and one journal: a single c4a-standard-16 reaches
1000 pairs in the wall-clock a c4a-standard-4 needed for 250, for about the same money. Four separate
c4a-4s would need the driver to pool four journals into one sequential test, which is a change worth
avoiding on a test whose whole point is that it is pre-registered.

## Attribution, only if stage 2 returns H1

A win by `sf2_wdl` over the live net is confounded with the step count (552 vs 368). Only then is a
second SPRT of `sf2_pass6` vs live worth a cloud day, to say whether the labels or the steps did it.
Ordering matters: do not spend that day before knowing there is a gain to attribute.

## NOTHING SHIPS

A human reads the verdict. The match is never extended after its score is seen.

---

## Amendments recorded 2026-09-24 ~22:00 local, before any arm finished training

**1. The arms train on the cloud host, not the Mac.** The local launch sat at 0.0% CPU with 100% of
`sample` ticks in `_platform_memmove` under `mapiter_get`: NumPy fancy-indexing six mmap'd `ids8`
caches (1.0 GB) with `PhysMem ... 69M unused`. Chrome held 3146 MB across 27 processes and the user
was working in it, so that memory was not available. Disconnecting the live bot would not have helped
— its stockfish was idle at 928 KB and peaks near 330 MB.

So both arms run on `rps-w1`, resized from c4a-standard-4 to **c4a-standard-16** (16 vCPU, 62 GB,
aarch64), the same host the stage-2 SPRT needs. Data moved once.

**2. `--threads` 3 -> 16.** A speed knob, as the original 6 -> 3 was. Batch composition comes from the
seeded NumPy RNG and is identical; only float reduction order can differ, which is numerical noise.
Nothing else in the recipe changed: same init, hidden 512, batch 2048, 20 epochs, 552 steps, lr 1e-4,
warmup 200, lr_floor 0.15, qat from epoch 1, raw 0.02, symmetry 0.2, grad_clip 5, val_rows 8192,
seed 20260929. Launcher: `/var/tmp/rps/cloud_wdl_arms.py`, log `/var/tmp/rps/cloud_wdl_arms.log`.

**3. Setup cost, measured rather than estimated.** The "about an hour" guess was wrong by an order of
magnitude: VM boot + `torch 2.14.0+cpu`/`numpy 2.5.2` install + **1005 MB of data in 56 s** (~143
Mbps) + 409 MB of boards in 16 s. End to end about six minutes.

**4. A correction to a claim made earlier in this work.** `features.py` has `Layout.goal = False` at
version 8, and I inferred from that that `board.npy` was not needed and left it out of the upload.
Wrong: `Dataset.open` mmaps *every* field in `FIELDS` unconditionally. `goal=False` only means the
boards never reach the model. Both arms crashed on `FileNotFoundError: .../board.npy` and the boards
were uploaded. No effect on the design.

**5. Cost guard.** c4a-standard-16 at a derived **~A$1.232/hr** (4 x the Catalog-verified c4a-4 price
of A$0.3081/hr; `cloud_prices.py` could not re-verify — it dies on
`CERTIFICATE_VERIFY_FAILED`, a missing CA bundle in its venv, which is worth fixing separately).
A `shutdown -h +240` deadman is armed on the host. The A$50/day ceiling is unchanged.

## An exploratory screen, run alongside and explicitly NOT part of this experiment

`runs/sweep_wdl_full` was trained 2026-09-19 on the sf1+n1 recipe and **never evaluated**. Its 188
steps against `runs/sf1_ex_50k`'s 125 is 1.504 = the (2x1.0 + 2x0.5)/(2x1.0) share ratio, so the
teacher gets identical passes in both and they share seed 20260926, init and every other knob. That
makes it a label A/B that already exists. `night/wdl1_screen.py` plays it, 300 pairs at 8 sims.

It is a **prior, not evidence**: 300 pairs resolves ~+-0.07 of margin against a ~0.029 target, it is
a different recipe, and the extra steps remain confounded. It can lower confidence in spending the
cloud day; it cannot raise it to the point of shipping anything. The sf2 arms and the SPRT decide,
and the stage-1 GO/NO-GO rule above is unchanged.

---

# OUTCOME, recorded 2026-09-25 01:5x local — both tests returned H1

The pre-registered test and its exploratory companion both accepted H1. Recorded here against the
plan above, unedited.

| # | test | pre-registered? | result | verdict |
|---|---|---|---|---|
| A | `sf2_wdl` vs live `sf2_ex_50k` | **yes**, seed 20260996 | 0.548, **+33.3 Elo**, 89 pairs, LLR 3.024 | **H1 STRONGER** |
| B | `sf2_wdl` vs `sf2_pass6` | yes (in `wdl_cloud.py`, before any game), seed 20260997 | 0.533, **+23.3 Elo**, 112 pairs, LLR 3.256 | **H1 STRONGER** |

Run: 2.34 h of a 9.0 h budget, ~89 pairs/hr, host self-shut-down at 15:38 UTC. Both seats logged
`RPSNNUE1 format 8 features 13640 hidden 512 heads 1` from the intended paths, and the reference
net's md5 matches the deployed live net (`f2b25b4c…`).

## The stage-1 gate was very nearly a false negative

Stage 1 said **NO-GO if the screen CI upper bound < 0**. It was **+0.0287** — this passed by 0.03 of
margin. The screen's point estimate (−0.042, ~−15 Elo) had the **wrong sign**, not merely too little
resolution: the real harness says +33. The screen reasoning in "Stage 1" above is therefore sound as
written (it says the screen "cannot detect the effect we care about"), but the *interpretation* I
put in the first morning report — "the labels are innocent, the steps regressed it" — was built on
those screen numbers and is **retracted**. Test B refutes it directly.

**Rule going forward:** a screen may veto only a catastrophically worse net; anything near zero goes
to the SPRT. Arms previously rejected on 8-sim screens alone are suspect.

## The attribution clause above is now live

"Only then is a second SPRT of `sf2_pass6` vs live worth a cloud day." Test A returned H1, so that
condition is met — and test B already did the attribution more cheaply by playing the two arms
against *each other*, which is the tighter comparison anyway (no live-net intermediary).

## NOTHING SHIPPED

The live bot is untouched. Recommendation in `WDL_MORNING_REPORT.md`: one fixed-length confirmation
run at a fresh seed (the SPRT's early stop biases +33 upward) and a second training seed, before
deploying. **This match is closed and will not be extended now that its score has been seen.**
