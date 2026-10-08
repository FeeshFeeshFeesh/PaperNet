# Idea #1 (outcome/WDL labels) — VERDICT, 2026-09-25 morning

**NOTHING HAS SHIPPED.** The live bot is untouched and still playing `runs/sf2_ex_50k/best.nnue`.
Both overnight tests finished, the host shut itself down, and every decision below is yours.

---

## The short version

**Idea #1 worked, and the cheap screen I gated it on was wrong — not imprecise, wrong in sign.**

| test | what it compares | result | verdict |
|---|---|---|---|
| **A** (pre-registered) | `sf2_wdl` vs **the live net** | score 0.548, **+33.3 Elo**, 89 pairs, LLR 3.02 | **H1 — STRONGER** |
| **B** (exploratory) | `sf2_wdl` vs `sf2_pass6` (step-matched control) | score 0.533, **+23.3 Elo**, 112 pairs, LLR 3.26 | **H1 — STRONGER** |

Both at 500 ms/move, `--opening-plies 24`, H0 = +0 / H1 = +20 Elo, alpha = beta = 0.05.
Test B is the clean label A/B: same steps, same seed, same everything, **labels the only difference.**
So the outcome rows themselves are worth something — this is not a step-count artefact.

## What I got wrong, in writing, last night

Last night's version of this file said of test A: *"Expected to return H0."* It returned H1 at
+33 Elo. The reasoning behind that prediction was the 300-pair screen, and the screen did not merely
lack resolution — **it pointed the other way**:

| comparison | screen (sploppy `bot`, 8 sims) | SPRT (SF19-NN port, 500 ms) |
|---|---|---|
| `sf2_wdl` vs live | −0.042 margin (~**−15** Elo) | +0.048 margin (**+33** Elo, H1) |
| labels vs step-matched control | +0.008 (~+3 Elo, "noise") | **+23 Elo, H1** |

**Therefore the headline finding I wrote into the report and into memory last night — "step
compensation costs ~17 Elo" — is retracted.** It was an artefact of the 8-sim screen. Chaining the
two point estimates puts `sf2_pass6` (more steps, no labels) at roughly *+10* Elo against live, i.e.
the extra steps were probably mildly *helpful*, not a 17 Elo tax. That chained number is an
inference from two upward-biased estimates, so treat its size as unknown — but its sign contradicts
the screen.

What **does** survive from last night: the mixture-share trap is real (I verified it by reading
`data.py`; shares divide the *batch*, not the corpus), and step-compensating for it is still correct.
Only the claim that compensation is expensive is dead.

## Why the screen failed, and what it costs us elsewhere

The screen and the verdict never shared an engine:

| | engine | budget |
|---|---|---|
| screen | sploppy's `bot eval` | 8 sims |
| SPRT + live deployment | SF19-NN port | 500 ms/move (~100x the search) |

And this idea targets a defect that is known to *grow* with depth — the conversion census measured
throw rate rising 10% -> 45% as search deepens. An 8-sim screen is precisely the regime where
teaching the net that shuffles are draws does the least good. So the screen was blind to the effect
by construction, and noise did the rest.

**The wider consequence, which I think is the most valuable thing here:** the pre-registered stage-1
gate was "GO if the CI upper bound >= 0". It was +0.0287 — it passed by 0.03. A +33 Elo improvement
came within a whisker of being discarded by a test that could not see it. **Any past arm rejected on
8-sim screen evidence alone is now suspect and may be worth replaying at 500 ms.** That audit is
cheap relative to what it might recover.

## How much to believe the numbers

Honest limits, none of which change the sign:

- **The point estimates are biased upward.** An SPRT that stops the moment it crosses H1 overstates
  the gap — the driver's own wording says so. The defensible claim is "stronger than the live net",
  accepted at H1 = +20 with <= 5% error. The true value may sit well below +33.
- **One training seed.** Both tests used the same `sf2_wdl` net, so what is measured is *this net*,
  not *this recipe*. A second seed is what separates those.
- **Two tests at 5% each**, so family-wise error exceeds 5%. Test A was pre-registered as the decider
  and stands on its own; B is corroboration.
- 89 and 112 pairs are short runs, which is what an early H1 crossing looks like.

**Integrity checks that did pass:** all three nets logged `format 8 features 13640 hidden 512
heads 1` from the intended paths, and the md5 of the cloud reference matches the live net on this
Mac exactly (`f2b25b4c…`), so test A really did play the net that is currently deployed. The
candidates now on disk are byte-identical to the ones that played.

## What I recommend — your call

I would **not ship straight off this**, for one reason only: the same tooling that just proved
itself untrustworthy is what produced every other number in this project, so the cheap confirmation
is worth its cost here.

1. **Confirmation run, ~A$4–8.** A *fresh* test, new seed, **fixed length** (no stopping rule, ~400
   pairs) to get an unbiased Elo. This is a new match, not an extension of a closed one — I will not
   re-open a match whose score I have seen.
2. **Second training seed**, to turn "this net is stronger" into "this recipe is stronger".
3. Then ship, if both hold.

If you would rather move faster, shipping now is defensible — the pre-registered test passed at the
real time control against the real opponent — and the rollback is a one-line `ENGINE=` revert.

Cheaper follow-ups now that labels are known to help: raise the WDL share above 0.5, or fine-tune
from the live net rather than re-deriving from `example.nnue`.

## Artefacts (all local, host is off)

- Nets: `nnue_train/sploppy_repo/runs/sf2_wdl/best.nnue`, `.../sf2_pass6/best.nnue` (+ configs, logs)
- SPRT journals and summaries: `nnue_train/logs/wdl_sprt/`
- Driver log: `nnue_train/logs/wdl_sprt/wdl_cloud.log`
- Pre-registration: `HANDOFF_wdl_match.md`

## Cost and housekeeping

- Overnight run: **2.34 h** of a 9 h budget. Host self-shut-down at 15:38 UTC as designed; the
  mirror's "VM unreachable" was that shutdown, not a failure. **Spend ~A$3**, well under the ~A$12 cap.
- I restarted the host this morning **resized to c4a-standard-4** purely to read results, with a
  25-minute deadman armed first, and stopped it again. Both hosts are TERMINATED now.
- **Still billing, needs your decision:** `rps-bench2` and `rps-w1`'s old 20 GB disks plus 9
  snapshots (64.6 GiB) ≈ **A$8/month**. Deleting is destructive, so I have not touched it.
- `cloud_prices.py` still broken (`CERTIFICATE_VERIFY_FAILED`, missing CA bundle in its venv).
- `cloud_burn.py` still needs a restart in your own window.

---

# SHIPPED, and the screen audit that followed — 2026-09-25 11:47

`sf2_wdl` is **live**. `connect_bot2.py`'s `NET` points at `runs/sf2_wdl/best.nnue`
(md5 `8db06fa5…`, byte-identical to the net that played the SPRT) and a real game's seat was
observed loading it. Rollback is the one line written in the comment block above `NET`.

## The audit of arms the screen may have buried

The finding above says *"any past arm rejected on 8-sim screen evidence alone is now suspect."*
`night/proxy_rank_all.py` ran the zero-game proxy over **all 24 nets on disk** — no games, no cloud,
about two minutes.

**Two false starts, both worth recording, because both would have produced a confident wrong answer:**

1. Ranking by `draw|pred|` put `scale_1100` on top at 0.1003 — below even the teacher label — while
   it held the *worst* MSE and correlation in the field. `scale_*` is an `EVAL_SCALE` sweep: a bigger
   divisor shrinks every eval, so a low `draw|pred|` is free and meaningless. **`draw|pred|` is not
   scale-invariant and cannot rank nets trained at different eval scales.**
2. The replacement, `sep` = |eval| on decisive rows ÷ |eval| on drawn rows, is a ratio and should
   cancel scale — but computed on the **clipped** value it does not. The clip at ±1 caps the
   numerator (decisive rows) while leaving the denominator (drawn rows, far below 1) alone, so it
   penalises large-scale nets. Computed on the **raw** value it behaves.

`sep` on raw, sorted, with everything the SPRT already settled shown beside it:

| # | net | sep | known truth |
|---|---|---|---|
| **1** | **`sf2_wdl`** | **6.313** | SPRT **+33** vs live → shipped |
| 2 | `cont512` | 5.923 | SPRT + (beat `wide768` by 46) |
| 3 | `sweep_wdl_full` | 5.827 | never tested — the *other* label arm |
| 4 | `sf3_ex_50k` | 5.801 | never tested |
| 7 | `wide768` | 5.753 | SPRT − (H0 at −46) |
| 12 | `sf2_pass6` | 5.543 | lost to `sf2_wdl` by 23 |
| 14 | `sf2_ex_50k` | 5.455 | was live until today |
| 23 | `sf1_ex_50k` | 5.033 | — |

Both pre-committed sanity checks pass, with wider margins than the clipped version gave:
`cont512` > `wide768`, and `sf2_wdl` > `sf2_pass6`. The net with the only confirmed +33 Elo SPRT
ranks **first of 24**.

### Answer: nothing was buried

**No shelved arm outranks what is now live.** `sf2_wdl` leads the field by a clear margin, so the
unreliable screen did not cost us a better net that is already sitting on disk. The next cloud money
belongs to **new** arms on the proven axis, not to replaying old ones. That is a cheaper conclusion
than it sounds — it closes a question that would otherwise have justified several confirmation runs.

### The bonus: idea #1 replicated on a second recipe and a second seed, for free

The morning report's honest limit was *"One training seed. What is measured is this net, not this
recipe."* `sweep_wdl_full` answers it at zero cost. It is a label A/B that already existed: trained
2026-09-19 on the **sf1+n1** recipe, seed 20260926, 188 steps against `sf1_ex_50k`'s 125 — which is
exactly the 1.504 share ratio, so the teacher gets identical passes in both and the labels are the
only difference. Its no-label sibling is `sf1_ex_50k`.

| family | label arm | no-label control | gap |
|---|---|---|---|
| sf2 (seed 20260929) | `sf2_wdl` 6.313 | `sf2_pass6` 5.543 | **+0.770** |
| sf1 (seed 20260926) | `sweep_wdl_full` 5.827 | `sf1_ex_50k` 5.033 | **+0.794** |

Two recipes, two seeds, two independent data mixes — and the label gap comes out within 3% of the
same size. The sf2 gap is the one an SPRT confirmed as +23 Elo. `sweep_wdl_full`'s own 300-pair
screen said **+0.0433, CI [−0.0265, +0.1132]** — a positive point estimate that was correctly filed
at the time as "a prior, not evidence", and which now reads as a third consistent signal.

This does not replace a real second-seed SPRT, because the proxy measures draw calibration and not
strength. But "this recipe, not just this net" is now supported by three independent pieces of
evidence instead of one, and none of them cost a cloud hour.

### What this makes the next experiment

Raise the WDL share above 0.5 and fine-tune from the live net rather than re-deriving from
`example.nnue`. Train the variants, rank them here first, and spend the SPRT on one candidate only.
`night/proxy_rank_all.py` re-runs in two minutes and is now the gate the 8-sim screen used to be —
with its limits written into its own docstring so it cannot be misused the way the screen was.
