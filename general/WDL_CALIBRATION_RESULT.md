# Did the WDL labels fix the thing they were aimed at?

**2026-09-25. Observational — these games were played for other purposes. Nothing was played,
nothing was decided, nothing ships from this file.**

`sf2_wdl` shipped this morning on +33 Elo vs live and +23 vs a step-matched control. It was built
on a mechanism: train on the game's *outcome* beside the teacher's search score, so the net learns
that a 200-ply shuffle is a draw and stops claiming a win in one. This asks whether that is in fact
what happened, or whether the net simply got stronger for some unrelated reason.

Tool: `sf19_ports/tools/wdl_calibration.py`. It joins the referee streams (board and result, no
scores) to the seat logs (per-move `score=`, no board) on the seat's own moves, over the three
matches archived in `sf19_ports/data/w1_match_archive/` — 683 games, 1184 seat segments joined,
75 ambiguous keys dropped.

---

## 0. The last moves of a drawn game carry no information

In **every** clock draw, in every match, **both** seats score the position exactly 0 over their last
several moves — 135 of 135 seats in the shipping match. The engines do not gradually lose faith;
they *see* the clock once it enters the search horizon and return a hard draw score.

So "what did it believe at the end" is unanswerable, and the earlier note in
`UNTOUCHABLE_TRACE_RESULT.md` that the score at the final move is a median +0 cp is this artifact,
not a finding. Everything below is measured by **distance from the end**, in the seat's own moves.

## 1. The signed mean is a trap

The two seats of a game are opponents. A belief that one side stands better appears as `+x` in one
log and `-x` in the other and cancels. The signed difference looks enormous — the candidate scores
87 cp *lower* than the step-matched control at 100+ of its own moves from the end — but the
agreement check (candidate + reference, same game, same band) comes to between −7 and +9 cp. They
largely **agree** on the asymmetry; the candidate is just on the other side of it.

In a game that was in fact drawn, a seat's honest error is its **distance from zero**, which cannot
cancel. Every number below is `|score|`.

## 2. The real signal: false hard claims, cut roughly in half

In clock draws, how often did a seat **ever** claim ≥ +200? Paired inside the same games, so the
denominators are matched and there is no selection confound:

| match | candidate | reference |
|---|---|---|
| `sf2_wdl` vs live | **11 of 67 (16%)** | 21 of 68 (31%) |
| `sf2_wdl` vs `pass6` (step-matched) | **12 of 72 (17%)** | 26 of 72 (36%) |
| share 0.75 vs `sf2_wdl` | 23 of 145 (16%) | 33 of 146 (23%) |

And it abandons the claim earlier: median **107** of its own moves still to play, against the
control's 135.

## 3. Distance from zero — real against the control, weak against live

Candidate closer to zero, paired inside the same drawn game:

| match | 60–100 moves left | 100+ moves left |
|---|---|---|
| vs `pass6` (step-matched) | **70% of games, −20 cp** | **73% of games, −15 cp** |
| vs live | 55%, −11 cp | 58%, −7 cp |
| share 0.75 vs `sf2_wdl` | 43%, +2 cp | 45%, −2 cp |

Aggregate `|score|` against the control: 104 / 117 cp for the WDL seat, 125 / 150 for the reference.

The step-matched control is the comparison that means something — it differs from the candidate
**only** in the labels. Live differs by more than that, which is why its row is noisier.

## 4. Conversion moved too, and not by timidity

Seats that sustained ≥ +400 for 6 of their own moves, and what actually happened:

| match | candidate | reference |
|---|---|---|
| vs live | 57 games, **won 91.2%**, clock-drew 8.8% | 45 games, 84.4% / 15.6% |
| vs `pass6` | 65 games, **won 95.4%**, clock-drew 4.6% | 59 games, 78.0% / **22.0%** |
| share 0.75 vs `sf2_wdl` | 48 games, 79.2% / 20.8% | 55 games, 72.7% / 27.3% |

Note the direction: the candidate reaches a sustained +400 **more** often (65 vs 59, 57 vs 45) and
still throws away fewer of them. It is not claiming less because it is timid.

---

## Verdict: MECHANISM CONFIRMED, in a narrower form than it was stated

Not "the WDL seat's score decays toward zero and the old one's does not" — against live the average
distance from zero is about the same. What the labels changed is the **tail**: the hard false claim
in a game that draws. It is cut roughly in half, it replicates against the step-matched control
(so it is the labels and not the extra training steps), and it is paired inside the same games (so
it is not a selection effect of the candidate converting its claims into wins).

Two things follow, both of which matter more than the verdict:

1. **The axis is saturated, confirmed from a direction that played no games.** Share 0.75 vs
   `sf2_wdl` shows *no* calibration difference at all — 43–45%, `|score|` identical. That is
   independent corroboration of #4's SPRT H0 at −9.7 Elo. Turning the share knob is dead twice over.
2. **The 190 Elo conversion target is dented, not closed.** The shipped net still clock-draws 4.6%
   of its sustained +400s, and 291 of the seats in the share match were still clock draws. Labels
   bought part of the gap. The rest is still a planning problem, and it still needs an attack that
   is not "label the outcome".

---

## Follow-up read of the model code (same day, free)

Three facts that turn "attack the tail" into a specific change, and kill one guess:

- **The output is one scalar** (`models/nnue/py/nnue/model.py:80`). The outcome label was blended
  into that same scalar by `share`, so the net has no way to represent "I am ahead and it is still
  drawn". **This is why the share knob saturated**: with a single output, more outcome signal can
  only pull the number toward zero — trading sharpness for calibration rather than buying both —
  which is exactly what #4's SPRT (H0, −9.7 Elo) and this probe (0.75 no better calibrated than
  0.5) each measured from their own side. The fix is a second output, not a bigger share.
- **The loss goes slack in the tail — but not where the defect is.** (`train.py:100-103`:
  `(raw.tanh() - target)² + 0.02·smooth_l1(raw, atanh(target))`.) I first wrote that the net is
  punished less for a +400 in a drawn game than for a +40. Measuring the absolute gradient on a
  drawn row corrects that: it **peaks at +200 (0.737) and is still 0.660 at +400**, collapsing only
  above +800 (0.156 → 0.023 → the Huber floor 0.020). Only the per-unit-of-error ratio falls, and
  learning follows the absolute gradient. A `tail_weight` knob adding `relu(|raw|-|logit|)²` is
  implemented and defaults to off, but its calibration says don't use it: 1.9× more push-back at
  +400 costs a 20× penalty on a *correct* confident win. **The loss is not the culprit** — which
  leaves the single scalar output as the explanation, and strengthens the case for a second head.
- **"Give it the clock" is already done.** `features.py` has 16 elapsed and 16 remaining-clock
  buckets, bounds `[0,1,2,3,4,6,8,12,16,24,32,48,64,96,128,160]` — fine near zero (hence the hard 0
  at the end of every clock draw), coarse but not blind across the 120–200 plies where the false
  claims live.
