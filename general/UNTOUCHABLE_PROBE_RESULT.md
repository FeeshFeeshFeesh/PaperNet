# Idea #2 — does the eval know about untouchable trades? — ANSWERED, 2026-09-25

**The net is NOT blind. It prices the untouchable relation at about −854 cp, 16× the noise floor.**
Instrument: `nnue_train/night/untouchable_probe.py`. No games, no cloud, no training.

## Design

A real held-out position (mover's frame, codes 0 empty / 1–3 own R,P,S / 4–6 enemy) has exactly one
own piece changed from type `u` to type `v`. Same squares occupied, same piece count per side, only
the type composition moves. Three arms, all starting from positions with no pre-existing untouchable:

| arm | own count of `u` | enemy holds what `u` captures? | what the mutation does |
|---|---|---|---|
| **treatment** | 1 → 0 | **yes** | creates a permanent untouchable |
| **boundary** | 1 → 0 | **no** | crosses the same context bucket, creates nothing |
| **count** | n → n−1 (n≥2) | yes | zeroes nothing, crosses no bucket |

**The boundary arm is the one that makes this readable, and without it the result would have been
fake.** Format 8's feature context is `min(r,2) + 3·min(p,2) + 9·min(s,2)` over the opponent's
counts, so a 1→0 transition *crosses a context bucket* and a 3→2 transition does not. Treatment vs
count alone therefore compares "crossed a representational boundary" with "didn't", and would show a
large gap even from a net that understood nothing. The boundary arm crosses the identical bucket
with the semantics removed.

**Noise floor, built in:** `features.transform_board(b, k)` renames the piece types cyclically, and
such a board "is played exactly like the original" — so the true value is unchanged and any eval
movement is pure net noise. Measured: mean |Δ| **0.089** (53 cp), median 0.050.

## Result

| arm | n | mean Δ eval |
|---|---|---|
| treatment | 9000 | **−1.143** [−1.163, −1.122] |
| boundary | 7047 | **+0.281** [+0.261, +0.301] |
| count | 9000 | −0.382 [−0.396, −0.368] |

| contrast | value | in cp | vs noise |
|---|---|---|---|
| **relation** (treatment − boundary) | **−1.424** [−1.453, −1.396] | **−854 cp** | **16.0×** |
| bucket (boundary − count) | +0.663 [+0.640, +0.688] | +398 cp | 7.5× |

All six `u→v` cells point the same way.

**The boundary arm going POSITIVE is the sharpest part of the result.** Losing your last rock when
the enemy has no scissors is not a loss at all — the rock had nothing to capture, so converting it
into a useful type is mildly *good*, and the net says so. The penalty appears only when the prey
actually exists. That is the untouchable relation, not a count.

## What this closes, and what it opens

**CLOSED: adding untouchability as a feature or a label.** That was the obvious next move and it has
no headroom — the eval already prices it at more than a whole piece. Idea #1's shape does not repeat
here. This is the main value of the probe: it stopped a plausible, expensive cloud week.

**OPENED, and it is less comfortable than the framing that led here.** The reasoning was "the net is
probably blind, so teach it". It isn't blind. But if the eval screams −854 cp at these trades and
the search is deep, the engine should already be refusing them — yet the games still draw. Three
possibilities, and they are not equally good news:

1. **The transition is forced by the time it is reachable** — the side is already worse and every
   alternative is worse still. Then the untouchable state is a **symptom**, and the
   `untouchable-trades` census's own stated caveat ("observational, not causal") is the right
   reading. Chasing it as a cause would be wasted work.
2. **It happens beyond the horizon** — the trade looks fine and the consequence lands fifty plies
   later. Then it is a genuine search/planning target.
3. **The association is real but small** next to whatever else drives the drawn endgames.

**The cheap discriminator, and the data for it already exists.** In the archived games, find the ply
where a side loses its last piece of a type while the prey is on the board, and read that seat's own
reported score before and after. A sharp drop at the transition means the search walked into
something its eval hated — case 2, a real target. A score already near zero beforehand means case 1,
a symptom. `sf19_ports/data/conversion_archive/` has the streams and the per-move `ply= score=` logs
for the 100k–400k rungs. Still zero games, zero cloud.

**Do that before spending anything on a conversion fix.** The probe just showed how easily a
confident, well-argued plan can rest on an assumption nobody measured.
