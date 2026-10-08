# Does the 200-ply draw entering the horizon flatten the search? (2026-09-26)

**Hypothesis under test.** `conversion-measured` found the throw rate on won endgames
**rising 10% -> 45% as search deepens**; `nocapture-draw-flat` found that once the
200-ply no-capture draw is inside the horizon the search returns one flat root score,
and proposed these are **the same defect**: deeper search reaches the draw sooner, so it
goes blind at a *lower* capture clock, spends more plies with no gradient, and converts
worse. Prediction: at a fixed clock, a bigger search should be flatter.

**VERDICT: CONFIRMED, and the effect is large.** At a fixed capture clock, the fraction
of positions where all 8 root moves score identically rises **5/254 -> 80/254** as the
search grows 20k -> 800k nodes. Paired within position: **58 positions go flat only at
the top of the ladder against 1 that goes the other way.** Deeper search causes the
blindness. Median root score falls **+56 -> 0** while the same boards with the capture
clock zeroed hold **+378 -> +364**.

**⚠ MY FIRST VERDICT ON THIS WAS WRONG AND IS RETRACTED.** The first sample (n=132)
made it look refuted. That sample was bimodal by construction -- 44 positions at clock
119-129 and 40 at 195-199, but only **2** in the whole 170-194 transition band, which is
the only place the claim can be tested. See "The hole that produced a wrong answer".

## Method -- zero games, all real positions

`sf19_ports/live/flatten/` (kept in-project). Two runs, identical protocol:

| | positions | games | clock | rows |
|---|---|---|---|---|
| main | `positions.jsonl` 132 | 44 | 119-199 (bimodal) | `sweep.jsonl` 1,584 |
| **band** | `positions_band.jsonl` **254** | 43 | **164-193** | `sweep_band.jsonl` **3,048** |

- `flatten_positions.py` / `flatten_band.py` -- a status-log snapshot is the board *after*
  our move, so the position with us to move is snapshot k+1 with `our_move` undone, which
  is only sound when no capture intervened -- exactly the high-clock case. Filters: us to
  move, ahead on material, clock in range. The band variant keeps up to 6 per game inside
  164-193 instead of the lowest/middle/highest of the whole range.
- `validate.py` / `validate_band.py` -- **132/132 and 254/254 round-trip**: board and clock
  survive the FEN and the move actually played live is legal in every reconstruction.
- `flatten_sweep.py` / `flatten_sweep_band.py` -- one engine process; per position
  **2 arms x 6 node levels**. Arm `high` = the real capture clock, arm `low` = the
  identical board with the clock field reset to 0; the clock is the only difference.
  Ladder `go depth 99 nodes N`, N = 20k/50k/100k/200k/400k/800k, `MultiPV 8`, `Threads 1`,
  `ucinewgame` before every search. 1,584 searches in 272.6 s and 3,048 in 541.3 s.
- `analyse.py` ... `analyse4.py`, `analyse_band.py` -- everything below.

**Protocol notes that cost real time to learn.** `Threads 1` + `ucinewgame` makes the whole
run **bit-exact** (identical node counts across repeats: 20,009 for an ask of 20,000); an
uncapped `go depth 14` ran **415 s** on one endgame, hence a node ladder; and **the last
`go` before `quit` in a piped script is aborted in flight**, reporting `depth 1 ... nodes 0`
with the first legal move -- always send one search to discard. The `ucinewgame` also clears
the new sticky-lock table, so the lock cannot contaminate the ladder (verified: **0 of 1,584
and 0 of 3,048 rows at depth 1**, no row under 9,108 nodes).

## 1. The decisive result: bigger search, blinder engine

254 positions at clock 164-193, `high` arm. `d>=left` is `depth >= 200 - clock`, i.e. the
search can reach the draw.

| nodes | median depth | d>=left | **all 8 root moves equal** | spread <=10 | median best | \|best\|<=5 |
|---|---|---|---|---|---|---|
| 20k | 8 | 23/254 | **5/254** | 43/254 | +56 | 43/254 |
| 50k | 10 | 56/254 | **26/254** | 53/254 | +42 | 69/254 |
| 100k | 12 | 75/254 | **42/254** | 56/254 | +37 | 91/254 |
| 200k | 14 | 98/254 | **57/254** | 71/254 | +7 | 118/254 |
| 400k | 17 | 125/254 | **74/254** | 84/254 | 0 | 140/254 |
| 800k | 20 | 140/254 | **80/254** | 91/254 | 0 | 162/254 |

Flatness tracks `d>=left` all the way up. **Paired within each position, 20k vs 800k:**

| | n |
|---|---|
| flat at both ends (already blind at the smallest search) | 4 |
| **flat ONLY at 800k -- deeper search caused the blindness** | **58** |
| flat only at 20k (deeper search restored a gradient) | **1** |
| never flat | 125 |

**58 to 1.** Individual cases are stark -- game 10 ply 295, clock 185: depth 8 sees
**+273**, depth 33 sees **0**. Game 21 ply 356, clock 180: **+293 -> 0**. Game 23 ply 263,
clock 180: **+183 -> 0**.

## 2. The cliff moves left as the search grows

Flat fraction per clock band, across the ladder (20k .. 800k):

| clock | n | 20k | 50k | 100k | 200k | 400k | 800k |
|---|---|---|---|---|---|---|---|
| 164-169 | 44 | 1 | 0 | 0 | 1 | 0 | **0** |
| 175-179 | 44 | 0 | 0 | 0 | 1 | 10 | **13** |
| 180-184 | 58 | 0 | 0 | 0 | 5 | 17 | **18** |
| 185-189 | 63 | 0 | 6 | 19 | 28 | 25 | **29** |
| 190-193 | 43 | 4 | 20 | 23 | 22 | 22 | **19** |

At clock 164-169 there are 31-36 plies to the draw and depth 20 never reaches it: **flat in
0 of 44 at every search size**. At 190-193 even a 50k search reaches it. In between, the
onset is set by depth, not by the clock -- which is the claim, measured.

**Control, same 254 boards with the clock field zeroed:** flat **4/254 at 20k and 9/254 at
800k**, median best **+378 -> +364**. The cliff is absent, so it is the clock, not the
positions. (The small residue is real: a few of these are drawn even with a fresh clock.)

## 3. What it costs: 209 of 254 are a stone up and score zero

At 800k nodes, the same boards:

| | median best | >= +200 (a stone up) | >= +1000 |
|---|---|---|---|
| clock zeroed | **+364** | **209 / 254** | 0 |
| real clock | **0** | 45 / 254 | 0 |

In the main sample's dead zone (clock >= 180, n=42) it is starker still: with the clock
zeroed, median **+432**, **35/42** at >= +200 and **16/42** at >= +1000 -- and at the real
clock, **28 of 42 score exactly 0 on every one of the 8 root moves, at every search size
from 20k to 800k**. There, more search is simply worthless.

## 4. The zero is *correct*, and that is why an eval clamp cannot fix it

A line that reaches ply 200 with no capture **is** a draw, so when the search proves every
continuation does that, 0 is the true value. The defect is not a wrong number -- it is the
**loss of all discrimination**. At score 0 the engine cannot tell "draw while keeping the
stone" from "draw after handing the stone over", because both really are draws; and the
second one is only a draw while the opponent cannot then convert. That is precisely the
`nocapture-draw-flat` Mocca loss: 14 consecutive plies at exactly 0.00 a piece up, then the
piece goes, then the game.

This is why `NC_DRAW_CAP = 50` returned **H0 at -23 Elo**: it handed a collapsed tree a
different number to report. The tree has one value because every leaf is the same drawn
terminal. **Only a search-side change reaches it.**

## 5. A second, independent mechanism: the clock is a net input

Separate from the horizon effect, `since_row` / `left_row` feed the capture clock to the
net, so the two arms see different inputs. Paired at 800k on the main sample:

| | high | low | diff |
|---|---|---|---|
| static (net units) | 299 | 698 | **-399** |
| search best (cp) | 46 | 386 | **-340** |

**The static eval already carries the whole level difference** -- search neither creates the
pessimism nor corrects it. And it scales smoothly, long before any horizon effect:
**-136 cp at clock 119-129**, -230 at 150-159, -261 at 160-169, -400 at 180-199. At clock
119-129 the draw is **81 plies** away and no search here reaches it, so that -136 is pure
net discount. The clock field also changes *which move we would play* in ~80% of positions
(same best move in only 19-21%).

So there are **two** defects, not one: a net that discounts a winning position for a clock
that has not run out yet, and a search that goes blind once it has.

## 6. This is the drift mechanism behind the loop breaks

`loop-break-asymmetry` measured that **we** leave a repetition first 80 times to their 22,
with **median eval change -5 cp and a mate score in 0 of 80** -- deviation with nothing
seen. Now measured from the engine side: in the blind zone the search assigns **every root
move the same score**, so move choice falls through to move-ordering and thread noise. That
is the ~6% per-visit deviation which breaks a 20-visit cycle.

It is also an independent justification for the **sticky loop lock**: locking the reply
costs nothing, because there is no information to recover -- 40x the nodes leaves 28 of 42
dead-zone positions perfectly flat. Visible in production; the seat log of the draw vs
hushlite at 18:17 ends

    ply=192 depth=19 score=7   ply=194 depth=20 score=8
    ply=196 depth=19 score=2   ply=198 depth=41 seldepth=3 score=0

and the bot's own status display labels those moves **"(dead level)"**.

## The hole that produced a wrong answer

Worth recording, because it nearly closed a live axis. The first sample took the lowest,
middle and highest clock **per game**, which is bimodal: 44 positions at clock 119-129 and
40 at 195-199, **2** in 170-194. On that sample the aggregate spread *grew* with the node
count (49 -> 68 at clock < 180) and the dead zone was flat at every search size, so the
depth effect appeared nowhere and I wrote it up as refuted. Both halves were true and
neither could see the effect: below 170 no search on the ladder reaches the draw, above 195
even the smallest does. **The transition is the whole experiment**, and a sampling scheme
chosen for "spread across the range" had excluded it. The fix was 254 positions inside the
band, and the sign flipped immediately.

## What this closes and what it opens

- **CONFIRMED:** deeper search goes blind at a lower clock. `conversion-measured`'s
  10% -> 45% throw rate and `nocapture-draw-flat`'s flat root score are **one defect**.
  The live bot searches depth 20-31, so it is blind from roughly clock **169-180** onward --
  the last 20-30 plies of every no-capture sequence.
- **CLOSED:** any eval-side clamp. The value 0 is correct; the problem is that all drawn
  leaves unify.
- **OPEN, and the sharpest target yet: tiebreak *inside* the draw.** Keep the
  game-theoretic value at `VALUE_DRAW` -- do not repeat the `NC_DRAW_CAP` mistake -- and
  give drawn terminals a secondary ordering key so the search prefers the drawn line that
  keeps more material and a better structure. That restores a gradient without changing any
  value, and it is complementary to the sticky lock (which handles the repeating case).
- **ALSO OPEN:** the net's clock discount, **-136 cp at 81 plies from the draw** where no
  forced draw exists. Unlike the horizon effect it is reachable by retraining with the clock
  feature ablated or rescaled.
