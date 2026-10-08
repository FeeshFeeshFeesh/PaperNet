# Idea #4 — WDL share sweep + fine-tune arm. TRAINED AND RANKED, NOT DECIDED. 2026-09-25

**Nothing has shipped. No SPRT has been run.** Four arms trained on the cloud, ranked by the
zero-game proxy, host stopped. `nnue_train/cloud/cloud_wdl_sweep.py`, results in
`nnue_train/logs/cloud_wdl_sweep.{json,log}`.

## Design

The 0.5 WDL share was a guess and it won (+33 Elo vs live, +23 vs the step-matched control). So the
neighbourhood was unexplored in both directions.

Steps are **derived, never typed in** — `Mixture` splits each *batch* by share, so adding WDL rows
at share `s` starves the teacher unless steps grow. `STEPS(s) = 368 × (3+3s)/3` holds the teacher at
exactly its baseline 4.00 passes, making **share the only thing that varies**:

| arm | share | steps | init | lr | epochs | train time |
|---|---|---|---|---|---|---|
| `sf2_wdl025` | 0.25 | 460 | example.nnue | 1e-4 | 20 | 13.8 min |
| *(`sf2_wdl`)* | *0.50* | *552* | *example.nnue* | *1e-4* | *20* | *already live* |
| `sf2_wdl075` | 0.75 | 644 | example.nnue | 1e-4 | 20 | 18.8 min |
| `sf2_wdl100` | 1.00 | 736 | example.nnue | 1e-4 | 20 | 21.4 min |
| `sf2_ft_live` | 0.50 | 552 | **the live net** | 2e-5 | 6 | 4.8 min |

The formula reproduces the shipped arm's 552 at s=0.50 exactly — a free check that it is right. The
fine-tune arm's init was verified genuine: the host's `sf2_wdl/best.nnue` is md5 `8db06fa5…`,
byte-identical to what the bot loads.

## Ranking (zero games, held-out split=2, 20,473 rows, 48.9% drawn)

| # | net | `sep` | `corr` |
|---|---|---|---|
| 1 | `sf2_wdl100` (share 1.00) | **6.8815** | 0.7398 |
| 2 | `sf2_wdl075` (share 0.75) | 6.5237 | **0.7407** |
| 3 | `sf2_ft_live` (fine-tune) | 6.3324 | 0.7353 |
| 4 | `sf2_wdl` — **LIVE, SPRT +33** | 6.3132 | 0.7346 |
| 5 | `sf2_wdl025` (share 0.25) | 6.0731 | 0.7303 |
| 6 | `sf2_pass6` (step-matched ctrl) | 5.5431 | 0.7212 |
| 7 | `sf2_ex_50k` (old live) | 5.4550 | 0.7219 |

**`sep` is perfectly monotone in share** — 6.073, 6.313, 6.524, 6.882 for 0.25/0.50/0.75/1.00. So
0.5 is **not** near an optimum on this metric; it is on a slope.

## The catch, and it is a serious one

**`sep` is not independent of the knob being tuned here.** `sep` = mean |raw| on decisive rows ÷
mean |raw| on drawn rows. WDL training pushes drawn positions toward 0 — which is the denominator.
Raising the WDL share therefore raises `sep` *almost by construction*. A monotone `sep` is close to
what this sweep would produce whether or not the nets play better.

This is the same shape as two traps this project already hit: `draw|pred|` was not scale-invariant,
and the 8-sim screen was wrong in sign. The proxy's 3/3 record was earned on comparisons of **label
presence** (0 vs 0.5) and of capacity — never on **share magnitude**, where the metric aligns with
the objective. That the 0→0.5 step was worth +23 Elo does **not** imply 0.5→1.0 is worth anything.

`corr` (correlation of raw eval with the true outcome) is less directly aligned, and it does **not**
agree: it peaks at **0.75** and dips at 1.00. The gap is tiny (0.7407 vs 0.7398) and may be noise,
but it is the only hint of a turning point, and `sep` is structurally incapable of showing one.

## Recommendation — one SPRT, on share 0.75

`sf2_wdl075` is the **only arm that beats the live net on both metrics while topping `corr`**.
Share 1.00 wins `sep` by a clear margin, but `sep` is the compromised metric here, and 1.00 devotes
half of every batch to a coarse −1/0/+1 signal, which is where a positional regression would come
from if one exists.

Protocol unchanged: 500 ms/move, H0 = +0 / H1 = +20, alpha = beta = 0.05, a fresh seed fixed before
any game, and **nothing ships until a human reads the verdict**.

If idea #3's opening filter lands first, that SPRT costs about **1.54× fewer pairs**.

## The fine-tune arm is a null

`sf2_ft_live` scores 6.3324 / 0.7353 against the live net's 6.3132 / 0.7346 — a difference of
+0.019 `sep`, far inside the noise of this metric. **Fine-tuning from the live net rather than
re-deriving from `example.nnue` bought nothing measurable.** That was flagged in `IDEAS.md` as
"likely the bigger win of the two". It was not. Worth recording so it is not tried again on a hunch.

## Cost

~1 h of c4a-standard-16 for the four arms ≈ **A$1.2**, plus ~1.5 h of idle burn after completion
(~A$1.8) before the host was stopped — the run finished at 05:57 UTC and was caught at 07:28.
**Total ~A$3.** Deadman was armed before the job started and never needed. Host now TERMINATED.
