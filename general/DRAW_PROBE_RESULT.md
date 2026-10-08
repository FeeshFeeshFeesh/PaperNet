# Is "drawn" already in the trunk? — IDEAS #6.1 answered before it was built

**Verdict: YES. The accumulator linearly encodes drawishness that the scalar
output does not express, and it is not just the capture clock. #6.1 is
justified — and it is a much smaller change than feared.**

Run it with `nnue_train/tools/draw_probe.py` (no sys.argv; constants at the top).

## Why this was the right thing to measure first

The calibration work left exactly one explanation standing for the residual
overclaim: the output is a single scalar (`models/nnue/py/nnue/model.py:80`,
`nn.Linear(2 * hidden, 1)`), so the net cannot say "I am ahead **and** this is
still drawn". The proposed fix — a second output head — costs a file-format
bump, a new Rust reader, new fixtures and the whole FORMAT9-grade test battery.

But a second head reads the **same frozen trunk** the scalar reads. So the
premise is checkable for the price of one local training run and zero games:

* if a probe on the frozen trunk predicts "this game was drawn" much better
  than the net's own scalar does, the information is already in the accumulator
  and the scalar is the bottleneck;
* if it does not, a second head has nothing extra to say and #6.1 should be
  closed — the defect would be upstream, in the features or the data.

## Method

* Net: `nnue_train/nets_from_w1/sf2_wdl.nnue` — the live net, **format 8,
  H=512, one head**. Weights frozen; `act` is the post-SCReLU accumulator
  (2H = 1024) that any head would read.
* Data: `runs/nnue_data/selfplay_sf2_ex_50k_wdl`, the set the live net was
  trained on. 419,142 rows, every row `outcome_ok`. Sampled 430 games /
  ~120k rows per resample.
* Label: `outcome == 0` (the game was drawn).
* **Held out BY GAME**, never by row. Positions of one game share one outcome,
  so a row-wise split leaks the label and every probe looks brilliant.
  Sanity check: signed cp predicts a draw at AUC **0.4937** — no leakage.
* Probes, all trained identically (BCE, AdamW, same rows, same schedule):
  * `linear` — a bare 2H readout.
  * `head-shaped` — exactly one real output head: a 2H readout **plus** the
    2H->32->1 dense path. Deliberately not stronger than the head #6.1 adds.
  * `clock-only` — the control: a logistic over six hand-made columns
    (since_capture, clock left, ply, pieces, two squares).

## Result

Whole held-out set, one resample:

| predictor | AUC |
|---|---|
| the net's own scalar, `-|cp|` | 0.8124 |
| clock-only control | 0.7575 |
| linear probe on the frozen trunk | **0.8791** |
| head-shaped probe | 0.8760 |

**The defect's own regime** — held-out rows where the net claims **>= +200 cp**.
38.6% of them were drawn. Separating the drawn from the won:

| predictor | AUC |
|---|---|
| the net's own scalar | 0.7396 |
| clock-only control | 0.8407 |
| linear probe | **0.8966** |
| head-shaped probe | 0.8767 |

The hardest cut — claiming rows with **>= 100 plies of clock left**, where
neither the control nor the search can win by reading the clock. Three
resamples (seeds 20260926 / 31337 / 4242):

| predictor | mean AUC | spread |
|---|---|---|
| the net's own scalar | 0.7837 | 0.7505 .. 0.8187 |
| clock-only control | 0.7900 | 0.7627 .. 0.8108 |
| linear probe | **0.8633** | 0.8583 .. 0.8725 |
| head-shaped probe | 0.8639 | 0.8526 .. 0.8724 |

## What this establishes

1. **The trunk knows.** A linear readout of the frozen accumulator separates
   drawn from won, among positions the net calls +200 or better, at 0.863 —
   against 0.784 for the scalar the net actually emits. The three resamples'
   spreads do not overlap.
2. **It is not the clock.** The clock-only control reaches 0.790 on the same
   rows. The trunk probe beats it by about +0.07 with 100+ plies of clock still
   on the board, so what it reads is positional, not the rule counting down.
3. **The net's scalar is a worse draw predictor than six hand-made numbers**
   (0.784 vs 0.790 deep-clock; 0.740 vs 0.841 across the whole claim regime).
   That is the bottleneck stated as plainly as the data can state it: the
   information is present and the output cannot carry it.
4. **A bare linear draw readout is enough.** `linear` and `head-shaped` tie
   (0.8633 vs 0.8639), and head-shaped overfits slightly (train BCE 0.30 vs
   0.43, held-out AUC peaking at epoch 7). The dense path buys nothing here.

## What it does NOT establish

The probe shows the **information is available**, not that using it **gains
Elo**. Shrinking the eval toward zero by a draw probability changes move
ordering and pruning, and could cost more in search behaviour than it gains in
calibration. That still needs an SPRT. What the probe buys is the right to
build it: the premise is no longer an assumption.

It also cannot distinguish "add a second output" from "keep one scalar and
change what it is trained to mean" on the evidence above alone. But the second
route's obvious knobs are already exhausted — WDL labels shipped (+33 Elo) and
both share settings returned H0 (#4). That is what leaves the head.

## Design consequence: the change is smaller than feared

Finding 4 removes most of the cost. A draw head needs only a **second readout
(2H i16) and its bias (i32)** — no second 32x2H i8 dense matrix, no second
delta. Concretely:

* `head_size` grows by `4 * hidden + 4` bytes. For H=512 that is 2,052 bytes on
  a head that is already ~35 KB.
* Rust reuses the **existing** `sum_with` SIMD dot product against the same
  accumulator. No new kernel, no new incremental-update path — the accumulator
  is untouched.
* The combination `eval = raw * shrink(draw_logit)` needs one monotone function
  shared bit-for-bit by the PyTorch model, the NumPy oracle and Rust. A 256-entry
  lookup table on the quantized logit keeps all three exact and is cheap.
* **The migration stays byte-verifiable**, which is the property format 9 was
  held to: `convert --to 10` writes a draw bias large enough to saturate the LUT
  at 1.0, so a converted file evaluates every position exactly as its source.

Training: the value head keeps its present target; the draw head gets a BCE
anchor on the actual draw label (what the probe just fit); and the **product**
is trained against the WDL target end-to-end, so the factorisation is what is
optimised rather than something assembled only at inference.

## Cost note

Local only. No cloud spend, no games played, and the live bot kept its clock
(`TORCH_THREADS = 2`).

---

# Part 2 — does the head actually FIX it, or just make the net timid?

**Verdict: it fixes it, selectively, and costs nothing in ranking.**
`nnue_train/tools/draw_shrink.py`.

Part 1 showed the trunk has something to say. It did not show that using it
helps. So bolt a decisive readout onto the **frozen** live net — trunk frozen,
value head frozen, only the new readout trained, held out by game — and shrink:

    eval = cp * (1 - P(draw)) ** gamma

`gamma = 0` is the live net untouched, so row one is the baseline.

Held-out rows the live net calls **>= +200 cp**: 8,454 of them — 3,260 drawn,
5,158 won, and only **36 outright lost**. The net is almost never on the wrong
side; the defect really is "drawn", not "losing".

| gamma | DRAWN: mean cp / still >= 200 | WON: mean cp / still >= 200 | MSE vs outcome |
|---|---|---|---|
| 0.00 (live) | +546 / 100.0% | +889 / 100.0% | 0.1753 |
| 0.25 | +329 / 73.7% | +812 / 98.3% | 0.1406 |
| 0.50 | +219 / 40.2% | +746 / 95.9% | **0.1284** |
| 1.00 | +122 / 19.9% | +638 / 88.3% | 0.1310 |
| 2.00 | +60 / 8.4% | +486 / 72.1% | 0.1578 |
| 4.00 | +25 / 2.8% | +315 / 47.0% | 0.2092 |

## It is selective, not timid

Timidity — shrinking every claim alike — would show selectivity 1.00x and
would be the failure mode the WDL work already ruled out (the shipped net
reached +400 **more** often than the control, not less).

| gamma | drawn cut | won cut | selectivity |
|---|---|---|---|
| 0.25 | 39.7% | 8.7% | **4.58x** |
| 0.50 | 59.8% | 16.1% | 3.71x |
| 1.00 | 77.6% | 28.3% | 2.74x |
| 2.00 | 89.1% | 45.4% | 1.96x |
| 4.00 | 95.4% | 64.6% | 1.48x |

At gamma 0.5 the drawn claims are cut by 60% while the won claims lose 16%.

## Control: is it just a global rescale?

It is not, and this had to be checked — eval scale is a free knob, and a net
that is merely overconfident everywhere would look better under **any**
multiplier. So the baseline was given its best constant, and each gamma its
best constant too. Only the surviving gap belongs to the head.

| | best constant | MSE | vs scale-only |
|---|---|---|---|
| live net, no rescale | 1.00 | 0.1753 | |
| **no head, scale only** | 0.62 | **0.1628** | — |
| gamma 0.25 + scale | 0.86 | 0.1395 | −0.0234 |
| gamma 0.50 + scale | 1.16 | 0.1274 | −0.0355 |
| **gamma 1.00 + scale** | **1.78** | **0.1186** | **−0.0442** |
| gamma 2.00 + scale | 3.00 | 0.1190 | −0.0438 |
| gamma 4.00 + scale | 3.00 | 0.1539 | −0.0089 |

A head that only rescaled would land on the scale-only line. It lands 27%
below it, at an interior optimum (gamma 1.00, constant 1.78).

Two things worth noticing:

* the best constant **without** the head is 0.62 — the live net is globally
  overconfident, which is a free finding on its own;
* the best constant **with** the head is 1.78, i.e. above 1. Once drawishness
  is divided out, the net should speak **more** loudly about the positions it
  is right about, not less. That is the opposite of timidity.

## Sanity: the eval still ranks

AUC of the eval picking the winner among decided held-out rows:

| gamma | 0.00 | 0.25 | 0.50 | 1.00 | 2.00 | 4.00 |
|---|---|---|---|---|---|---|
| AUC | 0.9881 | 0.9882 | 0.9882 | 0.9880 | 0.9874 | 0.9855 |

Flat through the useful range. The shrink removes false confidence without
disturbing which side is better.

## Still not Elo

Every number here is offline calibration on held-out games. Whether the search
converts it into strength is a separate question and needs an SPRT: shrinking
changes the eval distribution that move ordering, aspiration windows and
pruning margins are tuned around, and `WDL_CALIBRATION_RESULT.md` already found
that the engines see a clock draw once it enters the search horizon anyway.
What Part 2 changes is that the trade is now known to be **good** — unlike
`tail_weight`, whose table was bad at every setting, which is why it was
shelved rather than run.

## Where this leaves #6.1

Build it. The order that keeps each step cheap:

1. the second head in the **PyTorch model and trainer only**, flag-gated and
   defaulted off, trained jointly rather than frozen (this run froze the trunk,
   so it is a lower bound on what the head can do);
2. re-run `draw_shrink.py`-style calibration on the jointly trained net;
3. only if it holds: format 10 (a second 2H i16 readout + i32 bias per head, a
   shared shrink LUT, an identity `convert --to 10`), the Rust reader and the
   FORMAT9-grade fixtures;
4. SPRT against the live net.

---

# Step 1 — the head exists (2026-09-26)

Done and verified, PyTorch side only, flag-gated and default-off.

`model.py` gains an optional `decisive` readout (`nn.Linear(2H, 1)`, plus
per-head residuals when `heads > 1`), `decisive_logit()`, and
`attach_decisive()` — the counterpart of `widen_contexts`/`widen_buckets`, so a
`.nnue` start can gain the head without changing what it evaluates. `forward()`
now serves `value * sigmoid(logit)` and returns the logit only when asked.

The init is the detail that makes it safe: `DECISIVE_INIT_BIAS = 8.0`, so a
fresh head says "decisive" at p = 0.99966 and the model evaluates within 0.2 cp
of the incumbent at +600. Verified by test (relative max difference < 1e-3) and
again in training — the first epoch's anchor term reads 4.4748, which is the
arithmetic for that bias on a 59%-draw set (`-ln(1 - 0.99966) * 0.59`).

`train.py` gains `decisive_head` and `decisive_weight`, and a BCE anchor
against the row's own `outcome`, weighted by `outcome_ok`.

**One real bug was introduced and caught here.** The trainer has a `lean` batch
path that gathers only `("target", "weight")` when every dataset has an id
cache. With the head on, `rows["outcome"]` would simply be absent and the
anchor would have trained against nothing while every log line looked healthy.
Fixed twice over: `columns` now extends with `("outcome", "outcome_ok")` when
the head is on, and the permissive `if "outcome" in rows` guard became a raised
`KeyError`. A silent no-op is the worst outcome available here, because it
would have produced a confident negative result.

`export.py` refuses to export a model with a head: formats 6/8/9 cannot carry
it and a silent drop would ship a net that evaluates differently from the one
that was measured. The trainer therefore skips the `.nnue` export for these
runs and logs `no_export` once; `best.pt` still holds everything.

Evidence: 101 pre-existing tests still pass, 9 new ones in
`py/tests/test_decisive.py`, and the trainer runs end to end.

# Step 2 — first, the validation set was not real

Setting up the joint run surfaced two defects that invalidate any selection
number ever reported on a `_wdl` dataset.

**Every `_wdl` set has `split` all-zero.** The relabelling step drops it, so the
trainer's validation set is empty, `rows: 0`, `best: 0.0`. `best.pt` has been
the last epoch, and `patience` has never been able to fire.

**The importer holds out positions, not games.** `assign_splits` splits 90/5/5
by game, then moves every symmetry orbit seen in more than one split back to
TRAIN. That is the right guard for a value target. For a *game-outcome* target
— WDL labels, and this head's anchor — the leaking unit is the whole game,
because every row of a game shares one outcome. Measured on
`selfplay_sf2_ex_50k`: **55% of the games with validation rows also have
training rows** (sf1 47%, n1_25k 34%).

`nnue_train/tools/make_game_split.py` writes a sibling dataset,
`selfplay_sf2_ex_50k_wdl_gs`: every column a symlink, only `split.npy` new.
Datasets are immutable here (`data.write` refuses to overwrite) and
`provenance.json` is shared deliberately, so the `ids8` cache stays bound and
the `lean` path still works. 361,254 / 27,719 / 30,169 rows.

**The obvious granularity had to be rejected, on measurement.** Splitting by
the connected components of the game-orbit graph would close both holes at
once. It cannot be used here: drawn games run nearly twice as long (392 rows
against 200), so they touch more positions, share more orbits, and fuse into
one giant component of 564 games that is **79.1% drawn**, against **16.4%**
outside it and 39.7% overall. The first build of the tool did exactly that and
produced a validation split with a 0.189 draw rate against training's 0.600 —
worthless for measuring a draw head. So the split is by whole game, stratified
on the game's result (draw rates 0.563 / 0.572 / 0.546), accepting a measured
**12.9%** of held-out rows that have a symmetric twin in a training game. That
residual shares a *position*; the game split is what stops it sharing the
*outcome*, which is the label.

## The runs

Three, from the live net, same data, same schedule, seed 0:

| run | head | anchor weight | why |
|---|---|---|---|
| `dec_ctl` | no | — | the control: continued training alone also lowers MSE |
| `dec_w1` | yes | 1.00 | the weight the frozen probe used |
| `dec_w025` | yes | 0.25 | in case the anchor (4.5) tramples the value term (0.19) |

The control is not optional. At epoch 0 it already moves validation MSE from
the incumbent's 0.1467 to 0.1393 with no head at all, so "the decisive run
beats the incumbent" would have meant nothing on its own.

`nnue_train/tools/draw_calib.py` judges the result on the **TEST** split —
whole games the trainer never saw — and repeats the global-rescale control per
model, because a head that merely makes the net quieter is indistinguishable
from a constant unless that constant is fitted and compared against.

Note that validation MSE is now a live measure of the head: `evaluate()` calls
`model(...)`, which applies the shrink, so the trainer's own selection metric
already reflects the served value. Selection still ranks on value MSE alone and
ignores the anchor, which is the conservative direction.

## The step-2 verdict: the control wins

All three finished 01:30–01:56. Every one selected **epoch 0** and then got
monotonically worse, with `patience` stopping each at epoch 6 — `lr 3e-4` is
too hot for a fine-tune on data the net has already seen, and that applies to
the control too, so it does not explain the ordering below.

| run | best obj | epoch-0 decisive BCE |
|---|---|---|
| `dec_ctl` | 0.13928 | — |
| `dec_w025` | 0.14052 | 2.156 |
| `dec_w1` | 0.14379 | 1.873 |

`draw_calib.py` on the TEST split — 30,169 rows, 106 whole games, draw rate
0.5464 — gave the answer, and it is negative:

| model | MSE raw | MSE served | claim rows |
|---|---|---|---|
| live (incumbent) | 0.2135 | 0.2135 | 6,630-ish |
| `dec_ctl` best | 0.1690 | **0.1690** | 6,630 |
| `dec_w025` best | 0.2355 | 0.1719 | 10,193 |
| `dec_w1` best | 0.2586 | 0.1740 | 10,765 |

**The head loses to a control that does not have one.** And the mechanism is
visible in the raw column: the head models' *pre-shrink* eval is far worse than
the control's (0.2586 and 0.2355 vs 0.1690), and they claim ~60% more rows at
+200 cp. The shrink then claws most of that back. That is not a net that learned
to say "drawn" on top of a good eval; it is a net that learned to shout and
then apologise.

Two things that are true at the same time, and must not be conflated:

* **The head does carry signal.** Within `dec_w1`, the head's draw AUC in the
  claim regime is 0.9065 against its own scalar's 0.8328, and its served eval
  beats its own best global rescale by 10.7%. The probe was not wrong.
* **It is not worth what it cost.** The value head paid for the head's signal
  by decalibrating, and ranking degraded with it: AUC among decided rows went
  live 0.9815 → `dec_ctl` 0.9729 → `dec_w1` served 0.9639.

The control also repaid its cost immediately, exactly as designed. Draw AUC in
the claim regime is 0.6598 for the live net and **0.8437** for `dec_ctl` — no
head, same data. So a large part of what the frozen probe credited to "the
trunk knows and the scalar cannot say it" is really "the incumbent is
undertrained on WDL labels", and continued training alone recovers it. That is
a finding about the probe's framing, not just about this run.

## Diagnosis: the two heads were coupled

`forward` returned `value * sigmoid(logit)`, so `step_loss` computed the value
MSE on the **shrunk** value. The model's own comment had anticipated the risk —
"training the PRODUCT alone leaves the factorisation underdetermined" — and
relied on the BCE anchor to pin it down. Measurement says the anchor does not:
many `(value, logit)` pairs give the same product, and gradient descent picked
the one where the value head inflates and the logit cleans up after it. The
claim-row counts are that failure made countable.

The fix is `apply_shrink` on `forward` and `decisive_couple` on the trainer
config, defaulting to **decoupled**: the value term sees the pre-shrink value,
the decisive head is trained by its anchor alone, and the shrink applies only
when serving. That is exactly the arrangement `draw_shrink.py` measured to work
— frozen value head, post-hoc shrink — now with the trunk free to improve too.
110 tests pass, plus two new ones covering the flag.

## Step 2b: the decoupled arms, and a fairer serving rule

`dec_dw1` and `dec_dw4` (anchor weights 1.00 and 4.00, both
`--decisive_couple false`). The question is narrow: with the value head keeping
its calibration, does the head still pay?

`draw_calib.py` also gained the instrument it was missing. The in-model shrink
is `eval = cp * (1-P(draw))**gamma * constant` with **gamma and constant both
pinned to 1** — one arbitrary point on that surface, and not the point the
frozen probe won at, which was gamma 1.00 with constant **1.78**. So a head can
carry real signal and still lose at gamma=1 purely because the sigmoid is the
wrong strength. The tool now fits `(gamma, constant)` on the **validation**
split and scores the fitted rule on TEST, with the `gamma = 0` fit — a plain
global rescale, fitted the same way — as the paired floor. Two free parameters
fitted on the rows that score them would have handed every head model two extra
degrees of freedom; fitting them elsewhere is what keeps the comparison honest.

Checked on synthetic data before trusting it: the `gamma = 0` fit reproduces
`best_scale` exactly by an independent route, an informative head drives gamma
up, and a **pure-noise head returns gamma 0.000** — the instrument can say no.
A fit pinned to a grid edge is flagged rather than reported as an optimum.

If the decoupled arms also fail to clear `dec_ctl`'s 0.1690 under the best
fitted serving rule, the honest conclusion is that **#6.1 does not survive
joint training**, and format 10 should not be built.

## The decision rule, written down BEFORE the numbers (02:2x, 2026-09-26)

Recorded now, while `dec_dw4` is still training and `head_on_control.py` has not been
run, so the threshold cannot be chosen to fit whatever comes out. Step 2c is the
deciding measurement; steps 2 and 2b are context for it.

**The bar is `dec_ctl`'s fitted global rescale on TEST: constant 0.76 -> MSE 0.1641.**
Not the 0.1690 it scores at constant 1.0, and not the incumbent's 0.2135. The eval
scale is free, so the honest floor is the best a head-free net can do once that freedom
is used — and using it costs nothing at serving time.

Format 10 is worth building only if **all four** hold on `head_on_control.py`'s
`dec_ctl` row:

1. **Margin.** Tuned TEST MSE at least **5% below** 0.1641, i.e. **<= 0.1559**. The
   frozen probe claimed 27%; 5% is the point below which a file-format bump, a Rust
   reader, FORMAT9-grade fixtures and an SPRT are not worth the risk they carry.
2. **Ranking preserved.** Decided-row AUC drops by no more than **0.002** from the
   no-head row. The eval still has to order moves; `dec_w1` gave up 0.0176 and that is
   already too much.
3. **Not a grid artifact.** Neither gamma nor the constant pinned to a grid edge. An
   edge fit is a grid that stopped too early, not an optimum.
4. **The head, not the scalar.** Claim-regime draw AUC for the head clearly above the
   same model's scalar `|cp|` (0.8437 for `dec_ctl`). If the head only matches what the
   scalar already says, there is nothing to add a second readout for.

If any one fails, **#6.1 does not survive joint training and format 10 does not get
built.** The fallback is not "try harder": it is that the premise
(`draw_probe.py`) was measured against an undertrained incumbent, and ordinary
continued WDL training already collects most of the available gain.

One genuine escape hatch, and only this one: if 2c **passes** but the joint runs still
lose, the problem is the schedule (`lr 3e-4` overfits before the head converges) and
step 2d is a lower-LR or head-specific-LR run. If 2c **fails**, no schedule rescues it,
because 2c holds the net still for the head and is therefore the head's best case.

**Either way this goes to the user before anything ships.**

# The verdict (2026-09-26, 02:26)

## Step 2c, the deciding measurement

`head_on_control.py`: freeze a value net, train ONLY a linear readout on its frozen
activations, fit `(gamma, constant)` on validation, score on test. Whole games held
out; `assert` confirms zero game overlap between train, val and test.

| frozen net | no head (best rescale) | with head | gain | head draw AUC | scalar draw AUC | rank AUC |
|---|---|---|---|---|---|---|
| `dec_ctl` (retrained control) | 0.1641 (c 0.76) | 0.1621 (γ 0.375, c 0.94) | +1.3% | 0.7722 | **0.8437** | 0.9729 → 0.9716 |
| `live` (incumbent) | 0.2120 (c 0.88) | **0.1552** (γ 1.25, c 2.88) | **+26.8%** | **0.9248** | 0.6598 | 0.9815 → 0.9800 |

Against the rule written down before the run, on the `dec_ctl` row:

| # | condition | threshold | actual | |
|---|---|---|---|---|
| 1 | margin | ≤ 0.1559 | **0.1621** (+1.3%) | **FAIL** |
| 2 | ranking preserved | drop ≤ 0.002 | 0.0013 | pass |
| 3 | not a grid artifact | interior | γ 0.375, c 0.94 | pass |
| 4 | head beats the scalar | > 0.8437 | **0.7722** | **FAIL** |

**Two of four fail, so format 10 does not get built on this evidence.** Condition 4
fails in the most direct way possible: on the retrained net the head is a *worse* draw
predictor than the scalar the net already emits. There is nothing left for a second
readout to say.

The `live` row is why this is trustworthy rather than just disappointing. The same
tool, same procedure, pointed at the net `draw_shrink.py` froze, reproduces its
headline almost exactly: **+26.8% here against the published 27%**. So the instrument
is validated by reproducing the original positive, and then kills the idea on the
retrained net. That is the control structure that makes a negative worth believing.

**What was actually going on.** Continued WDL training does not *add* draw knowledge so
much as *move* it into the scalar. Across the two rows the scalar's claim-regime draw
AUC rises 0.6598 → 0.8437 while the probe-able residual in the trunk falls
0.9248 → 0.7722. The frozen probe measured a real thing; it measured the gap that 500
steps of ordinary training closes for free, with no format change, no Rust reader and
no fixtures.

## The joint runs, under the fitted rule

The decoupling fix worked exactly as designed — compare the raw column, which is where
the coupled runs gave themselves away:

| run | raw | served (γ=1,c=1) | own rescale floor | fitted rule | gain vs own floor |
|---|---|---|---|---|---|
| `dec_ctl` best | 0.1690 | 0.1690 | **0.1641** | — | — |
| `dec_w1` best (coupled) | 0.2586 | 0.1740 | 0.1956 | — | +10.7% |
| `dec_w025` best (coupled) | 0.2355 | 0.1719 | 0.1834 | — | +5.6% |
| `dec_dw1` best (decoupled) | **0.1840** | 0.1764 | 0.1765 | 0.1724 | +2.3% |
| `dec_dw4` best (decoupled) | 0.2024 | 0.1868 | 0.1901 | 0.1838 | +3.3% |

Decoupling recovered most of the value head's calibration: raw MSE 0.2586 → 0.1840 at
the same anchor weight. The fix was right. **It still does not matter** — the best
joint arm (`dec_dw1` best, fitted, 0.1724) is 5% *worse* than the head-free control's
0.1641. No arm, coupled or decoupled, at any anchor weight, beats a net with no head.

## One thing the rule did not anticipate

`live` + head reaches **0.1552**, the lowest TEST MSE anywhere in this work — below
`dec_ctl`'s 0.1641 — and it keeps the better ranking too (0.9800 vs 0.9729), because
the incumbent ranks better than the fine-tuned control to begin with (0.9815 vs
0.9729). Checked for grid truncation: re-fitting with the constant grid widened to 8.0
and gamma to 6.0 returns the identical optimum, so it is interior and real.

This is a post-hoc observation on data that has already been used, which is precisely
what the pre-registration existed to stop me doing, so it is **recorded, not acted on**.
It says the axis is not nonsense; it does not license a format bump. It is also offline
MSE, not Elo — shrinking the eval moves ordering and pruning, and this project has a
measured sign flip between an offline screen and an SPRT. **The user decides this one.**

## Where #6.1 stands

**Closed as specified: do not build format 10.** The premise (`draw_probe.py`) was
measured against an undertrained incumbent; ordinary continued WDL training collects
most of the gain with no format change. The code added along the way is flag-gated and
defaults off, so nothing in the existing pipeline changed: 110 + 2 tests pass, and a
model without the head is bit-identical under both settings.
