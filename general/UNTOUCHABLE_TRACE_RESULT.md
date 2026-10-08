# Idea #2, final answer: untouchable trades are a SYMPTOM, not a cause — 2026-09-25

**Zero games played, zero cloud spend.** `sf19_ports/tools/untouchable_score_trace.py`, reading
games already in `sf19_ports/data/conversion_archive/`.

## The question this closes

The census found that at ply 200, holding no piece of a type that beats something the enemy has
roughly **triples the draw rate** at matched material (66.5% vs 20.6%). The probe then found the net
**knows**: creating that relation is worth **−854 cp**, 16× the noise floor, with the no-prey control
coming out slightly positive.

Those two facts together implied a search target: *the engine hates this, so why does it keep
doing it?* Two readings, needing opposite and very differently-priced fixes:

- **Search target** — it walks in anyway (beyond the horizon, or search overrides eval). Then the
  victim's own score should **fall sharply** across the transition. Worth real work.
- **Symptom** — by the time the transition is reachable it is forced, in games already going
  nowhere. Then the score is **already flat** and a conversion fix aimed here buys nothing.

## Method

The referee streams record every move but their `info` field is **null throughout** — no engine
scores. The seat logs record `ply= score= best=` but not the board. Neither alone is enough.

They join on the move: a seat plays one colour, so its plies are single-parity, and its `best=` at
each ply equals the move the stream recorded there. Matching the first 4 moves of one parity
identifies the game *and* the colour.

- **240 games, 240 replayed cleanly (100%)**, 480 seat segments joined (exactly 2 per game)
- 359 transition events; **224** had the victim's own scores on both sides
- Only `ab_nsdeep` / `ab_nsdeeper` could be used — the cloud SPRT streams kept no seat logs

**Sign convention, validated independently rather than assumed:** in decisive games the side that
lost its last piece of a type went on to **lose 190 of 225 times (84%)**. A negative victim score is
therefore the right orientation.

## Result

Victim's own search score, cp, in the victim's frame, along the run-up to the transition at `t`:

| bucket | n | t−60 | t−40 | t−20 | t−10 | t−4 | t+2 | t+10 | t+20 |
|---|---|---|---|---|---|---|---|---|---|
| **drawn** | 131 | +16 | +18 | +20 | +25 | +23 | **+39** | +46 | +67 |
| decisive | 148 | −174 | −224 | −347 | −598 | −600 | −811 | −1506 | −227 |

**In the games that matter — the draws — nothing happens.** The score is flat near zero for sixty
plies before the transition and drifts *up* afterwards. There is no cliff and no slow bleed. The
median |score| just before the transition is 83 cp; the median drop across it is **17 cp**.

The decisive row is a side already collapsing from t−60 onward. That is a lost game, not a trade
error.

### The last refuge, tested and empty

If the theory were right anywhere, it would be drawn games where the victim was genuinely winning
before the trade. Restricting to those:

| drawn-game victims with peak ≥ | n | peak | before | after |
|---|---|---|---|---|
| +0 cp | 129 | 109 | 31 | 42 |
| +150 cp | 19 | 401 | 255 | 339 |
| +300 cp | 10 | 560 | 401 | **516** |
| +500 cp | 6 | 697 | 555 | **700** |

**The score goes UP across the transition, not down — 0 of those 10 dropped by more than 200 cp.**
Only 7.6% of drawn-game transitions even had a victim who was ever above +300 cp, and the median
best score a drawn-game victim ever held was **63 cp**. They were not winning and did not throw it.

## Verdict

**The untouchable relation is a symptom of a sterile position, not the cause of a drawn one.** The
census's own caveat — "observational, not causal" — was the correct reading, and the −854 cp the
probe measured is a *counterfactual* price: real games only reach the relation where it costs
nothing, because by then there was nothing left to convert.

**This closes trade selection as the conversion fix.** Combined with the probe closing
feature/label work on untouchability, idea #2 is fully answered and neither branch is worth a cloud
day.

## What it reframes

Every drawn game here ended on the **capture clock** (122 of 122 draws; the 118 decisive games ended
by Goal 111 / Elimination 7). And in the peak ≥300 subgroup the engine is still claiming **+516 cp
after** the transition in a game that ends drawn — yet across *all* seats in *all* drawn games, the
score at the **final** move is median **+0 cp, with 0% still claiming ≥100 cp**.

So the belief does not break at the trade. It decays late, as the clock runs out. That is a
**horizon / draw-calibration** defect, not a trade-selection one — which is precisely the axis
outcome labels attack, and that axis has already produced the only confirmed +33 Elo of this
project.

## Honest limits

- 135 of 359 events were dropped for lacking the victim's scores on one side, which are
  disproportionately transitions very near the start or end of a game.
- 100k-node games, not the 60 s cloud SPRT games; the conversion defect is known to **grow** with
  depth, so this is measured in the weaker regime.
- Observational throughout. It rules out the proposed mechanism; it does not prove what causes the
  draws.
