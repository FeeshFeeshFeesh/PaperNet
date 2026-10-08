# The no-capture clock: what the eval decay does, and what it does not

Started from a hypothesis: *"decay scores when drawing is sabotaging us, because
then it's incentivised to take a perfectly good drawing position and sell it, to
cause a capture, then either lose by corner, or drag out the game another 200
plies, and lose by time."* Worked example given: the lost Mocca game.

The hypothesis is **partly confirmed and partly wrong**, and two of my own
intermediate claims were wrong too. Both are recorded below, because the wrong
turns are the reusable part.

## 0. The answer, in one page

**Your hypothesis had two limbs. One is confirmed with its mechanism corrected;
the other is real and already fixed. The step in the middle — "sells the
position" — is the one part that is *not* true, and the truth is worse.**

| your claim | verdict | where |
|---|---|---|
| the drawing score is decayed | **half right** — the *search* goes blind, but the eval damping is `#ifdef CLOCK_DAMP` and is **not compiled into the live binary** | §13, §19e |
| it sells a good drawing position to force a capture | **CONFIRMED on the corrected definition** — we leave the repetition first in **80 of 102** long loops, and 43 of those hand back the clock | §19b, §19c |
| ...then loses by corner | **confirmed in the Mocca game** (mate-10, 1 piece vs 3) | §13 |
| ...or drags on 200 plies and loses on time | **real: 6 losses on time** — gradual decay fixed 09-25, spikes still open | §12 |

**⚠ The second row was marked "not confirmed" for most of this document, on my
own wrong definition of breaking a loop (whoever made the capture). Corrected in
§19b: the breaker is whoever first departs from the repeating cycle, the capture
lands a median 32 plies later, and on that definition the hypothesis holds. The
retraction is §19a.**

**What actually happens (Mocca, 09:32:58 LOSS, traced over 197 positions).** We
were a piece up (5v4) the whole time. Eval `+55` at counter 150 → **exactly
`0.00` for plies 236-250** → `−78` at counter 194 → piece lost at ply 258,
counter resets → mate-10 at ply 378 → corner. The zero switches on at the exact
ply where **`200 − counter ≤ depth`**: at ply 234 there were 28 plies to the draw
against depth 25 and the eval was `+7`; at ply 236, 26 plies against depth 32,
and it was `0`. Mean `|eval|` while a piece up: **48.8 cp outside the horizon
(n=97) vs 11.8 inside (n=11)**. And depth *jumps* 25 → 32 as the draw comes into
view — every line reaches the same drawn leaf, the tree collapses onto one value,
the search gets cheap, so **the blindness is self-reinforcing.**

**Why "selling" is the wrong word, and why that is worse news.** The piece went
only *after* the eval had already gone to `−78`. Damping `+55` toward zero cannot
manufacture `−78`, so the engine was reading a genuine threat: Mocca, a piece
down, *had* to break the fortress and did. We did not throw a won position to
escape a draw — **we stopped being able to see anything at all, and then got
hit.** That diagnosis also explains the three preceding Mocca draws, which
"selling" does not.

**What it is worth (301 live games: 215W 68D 18L, score 0.827, Elo 272).** The
no-capture draw is **22.6% of all games** -- nearly one in four -- and its share
tracks our score monotonically with no exceptions across 13 opponents: **0%**
against every bot we beat clean (Psionic 51-0-1, VladKlentBot 31-0-0), 10%
VladLargeConvNet, 27% Vlad_NNUE, 46% hushlite, **54% hushhush2 (3-7-3)**, **79%
Mocca (0-19-5)**. The two opponents that hold us are the two with the largest
share. That is not a strength gap, it is a **specific exploitable hole.**
Converting one draw in four is **+37 Elo**; half is +80. On the measured record
this is the largest single axis quantified so far.

**Why we leave (§19d–§19e).** Across all 80 of our departures the eval moves a
median of **−5 cp** and **0 of 80** carry a mate score; depth does not increase.
Nothing is seen — we step out into a position our own engine rates no better.
The cause is not a score: `position.h` says "There is no repetition rule" and
both `is_repetition` and `upcoming_repetition` are `return false` stubs, so **the
engine has no representation of a loop and cannot choose to hold one.** From
boards revisited 3+ times near the draw we reproduce our own move 94.0% of the
time against their 98.0% — 46% of our revisited positions get more than one
answer. Drift, not greed. The fix is move-selection stickiness, not an eval
clamp, which is why §16's SPRT was testing the wrong side of the engine.

*(These numbers are the corrected ones. My first pass counted 212 games at a
13.7% draw share; the status log is a redrawn terminal display and I had counted
redraws as games while missing a whole earlier era in a second result format --
see §17. Every conclusion kept its direction; the prize got bigger.)*

**What is NOT established -- and the patch is now dead.** The SPRT came back
**H0: `NC_DRAW_CAP = 50` is NOT stronger** (46 pairs, score 0.4674, estimate
**-23 Elo**, LLR -3.02 past the -2.94 bound). The point estimate is negative, so
this is not "needed more pairs" -- the arm closed on the wrong side and per the
pre-registration it stays closed. That kills the *patch*, not the diagnosis:
clamping the number at the root does not un-collapse a tree that has already
collapsed onto one drawn leaf, which is exactly what §13 measured. Fixing it is
a **search** change, not an eval constant. Also still unestablished: whether
those fortresses are winnable at all -- so +37 Elo is an upper bound on a prize
whose existence is not yet measured. `live_boards.py` (§18) now recovers the
real boards, which is what a playout test needs.

---

## 1. The decay is real, large, and deliberate

`evaluate.cpp:100-120` holds an arithmetic damping behind `#ifdef CLOCK_DAMP`
(never defined, so inert), but the **net itself has learned the decay**. Raw net
output on a fixed position as the capture clock runs: `553 / 476 / 311 / 227 /
59` at rule50 `0 / 50 / 100 / 150 / 199`. Search scores on a position a rock up,
1200 ms: `+313 / +183 / +84 / +31` at rule50 `0 / 150 / 175 / 185`.

`evaluate.cpp`'s own comment says why this is wanted: *"Damping makes a shuffling
line decay while a capture, which zeroes the clock, does not, so converting
lines outrank waiting ones."* That reasoning is sound. Without any decay,
shuffling and converting would both read +313 and the search could not tell them
apart at all. **The decay is the gradient that rewards conversion.** It is not,
by itself, the defect.

## 2. What IS broken: the gradient dies at the top of the clock

Once the draw is inside the search horizon, `rps_terminal()` (`search.cpp:128`)
returns a flat `VALUE_DRAW` for every non-converting line. MultiPV 8 on the same
position, 1200 ms:

| rule50 | best | spread | distinct root scores |
|---|---|---|---|
| 0 | +313 | 16 | 6 |
| 150 | +183 | 14 | 6 |
| 175 | +84 | 2 | 3 |
| 185 | +31 | 4 | 4 |
| 190 | **+0** | **0** | **1** |
| 196 | **+0** | **0** | **1** |

A whole rock up, and the engine has **no preference whatsoever** among its eight
root moves -- the choice falls to move ordering.

**The onset is depth-dependent, and this is the new result.** Same position, same
clock, different search time:

| search | rule50 185 | rule50 190 |
|---|---|---|
| 250 ms (depth 9-12) | +39, spread 8, 6 distinct | **+19, spread 7, 5 distinct** |
| 4000 ms (depth 15-22) | +50, spread 31, 6 distinct | **+0, spread 0, 1 distinct** |

Deeper search reaches the draw sooner, so it goes blind *earlier*. That supplies
a mechanism for the previously unexplained `conversion-measured` finding that the
throw rate rises **10% -> 45% as search deepens**. The live bot searches depth
20-31, i.e. well inside the blind regime.

## 3. Corpus: the cost is concentrated and visible

All 363 games in `sep2026/papernet-status.log`:

| outcome | games | mean material | ahead / level / behind | mean final eval |
|---|---|---|---|---|
| WIN | 279 | +0.71 | 152 / 120 / 7 | +1449 |
| DRAW | 67 | +0.21 | 19 / 42 / 6 | +39 |
| LOSS | 17 | -0.41 | 2 / 11 / 4 | -1890 |

**Every one of the 67 draws is a no-capture draw.** In 19 of them (28%) we
finished a **mean +1.21 stones up**, with the eval **peaking at +689 and decaying
to +127**. That is ~5% of all games where we were winning and did not convert,
which is the same defect `conversion-not-eval` priced at ~190 Elo.

Against Mocca specifically the record is **13 draws / 4 losses / 0 wins** -- we
never beat this opponent, we shuffle to the no-capture draw, and occasionally
fall over. (Our Elo 285, theirs 273: a peer, not an upset.)

## 4. Two claims of mine that did NOT survive

**(a) "The flat zero is the bug."** Wrong, or at least unproven. The decisive
test: at rule50 190 take the twelve root moves that all score 0, replay each with
the clock forced back to 0, and measure what they are really worth.

| | |
|---|---|
| spread the engine sees | 2 cp across 12 moves |
| real spread once the clock resets | **20 cp** (+317 .. +297) |

The moves genuinely *are* interchangeable there. A line that reaches ply 200
without a capture **is** a draw -- the game ends -- so scoring them all 0 is
game-theoretically correct. Flat is not automatically harmful.

**(b) "The Mocca loss was caused by the engine going blind."** Wrong. The log
shows the engine's PV at ply 256 was already `I expect b5-a5` -- it *saw* the
capture that cost the paper, scored it, and could not avoid it. It was not
blind; the capture was forced.

What the Mocca game does show is the drift. Cycle 1, a piece up (5v4) for 195
plies:

| no-capture counter | eval |
|---|---|
| 88 -> 148 | +49 .. +72 |
| 166 | +12 |
| 178 / 184 | **0** |
| 190 | -15 |
| 192 | they take the paper on a5; 5v4 becomes 4v4, counter resets |

Then cycle 2: dead level, -76 -> -801 over 115 plies, three more pieces, corner
loss. Only **6 captures in 386 plies** -- a shuffling game.

Also checked and *not* the cause here: the clock. It stabilised at **0:29 for 280
plies**, and we spent ~720 ms against a budget the adapter formula puts near
1800 ms. With the site's fixed ~250 ms/move charge (`live-clock-overhead`),
720 ms of thinking is almost exactly break-even against a 1.0 s increment, so the
adapter had found equilibrium. Depth still fell 30 -> 16 at constant time, which
is the position getting harder, not starvation.

## 5. The change that is built (flag-gated, default OFF, NOT shipped)

One site: `rps_terminal()`'s draw branch, reached first at both the search
(`search.cpp:780`) and qsearch (`:1648`) entries, which makes the later
`is_draw` checks dead paths for this rule.

`-DNC_DRAW_GRADE` turns on a signed contempt for the no-capture draw:

    Value nc_draw_grade(Value tv, Value ev, const Position& pos) {
        Color us  = pos.side_to_move();
        Value mat = pos.non_pawn_material(us) - pos.non_pawn_material(~us);
        Value adv = mat / NC_DRAW_MAT_DIV + ev / NC_DRAW_EV_DIV;
        return tv - std::clamp(adv, -Value(NC_DRAW_CAP), Value(NC_DRAW_CAP));
    }

Defaults `NC_DRAW_CAP 50`, `NC_DRAW_MAT_DIV 8`, `NC_DRAW_EV_DIV 4`; one stone
(208) charges 26, two saturate the 50 cap.

Rationale: reaching ply 200 while ahead is a *failed conversion*, so it should be
charged, which pushes the search to keep the game alive rather than shuffle the
clock out. The charge is **signed, not one-sided**: the trailing side is credited
the same amount, because for them ply 200 is a rescue -- and because a terminal
node's value must negate exactly between the two sides or the transposition
table is poisoned.

**Inertness is proven, not assumed.** With the flag off the preprocessed source
is textually unchanged, and the base arm relinks **bit-identical**
(`48aa5d6e837ab4b9`) across all four iterations of the patch. The live bot is
untouched. (`sf19_ports/live/stockfish` is `4b7fabf0...`, a different binary
again.)

Two earlier forms were wrong and are recorded so they are not retried:

* **Eval-only grading** (`tv + clamp(ev, ±50)`) barely moved rule50 190 and left
  196 dead flat -- because it reads the static eval *at the terminal node*, where
  rule50 is at the limit and the net's learned decay has already crushed it to
  the same small number for every line. Material is clock-independent and is the
  term that survives there.
* **Wrong sign.** `tv + clamp(mat/8)` scores a draw **+24 when a stone up**,
  making the draw *more* attractive exactly when it is a failure. The measured
  defect is 19 draws while ahead, so the charge must be negative.

## 6. Status

Measured and pending:

* base-vs-base move-change **noise floor** -- mandatory, because `go movetime` is
  nondeterministic in this build (`go nodes` and `go depth` do not search: they
  return at depth 1 with `nodes 0`) and `value_draw()` adds a deliberate
  `nodes & 0x2` jitter. A first uncontrolled run showed 62.5% of moves changing,
  including 63% at rule50 120-159 where the fix cannot fire -- i.e. almost
  certainly noise, not signal. **Do not quote that number.**
* SPRT of the contempt arm vs base. The variant's capture clock is intrinsic to
  the game, so `bot eval` *can* test this -- unlike the time-management defect,
  which needs a game clock the harness does not have.

Nothing ships before the SPRT verdict is read.

---

## 7. The sign is probably backwards — reopened before the SPRT

Section 5 records that I flipped the material term from `+` to `−` (contempt:
score the draw as *bad* when ahead). Re-deriving it against the actual mocca
mechanism, that flip looks wrong.

**What actually happens at the top of the clock.** Holding 5v4 at rule50 199 is
a certain draw, worth 0. Letting the opponent take our paper resets the clock to
0, and the net then evaluates 4v4 *undamped* — equal material, ≈ 0. So the two
score the same, which is why move ordering decided. The engine is not confused:
it is correct that both are ≈ 0.

**The inconsistency is across the reset, not at the terminal.** A 1-stone
advantage at rule50 190 is damped to near nothing, so the engine will pay nothing
to keep it. But that same stone is worth a full `StoneValue = 208` on the other
side of a capture, because the capture resets the clock and undoes the damping.
The damping is applied to the clock of the *current* node and silently un-applied
one ply later. That is what makes selling a stone free.

**Which sign follows.** Contempt (`tv − adv`, the built arm) scores the draw
−26 when we are a stone up, which makes *allowing the capture* (≈ 0) look better
than the draw. It pushes the engine toward exactly the move that lost the mocca
game. A draw bonus (`tv + adv`) scores the draw +26 when a stone up, so the
engine prefers the certain draw to an equal-material continuation.

**The empirical case for the bonus is on the record already.** vs Mocca we are
**0W / 13D / 4L**. At equal Elo (285 vs 273) a certain draw is worth strictly
more than a fresh equal-material game we lose 4 times out of 4. Corpus-wide the
same holds at the top of the clock: by rule50 190 the win is already gone
(MultiPV 8 shows one distinct score), so contempt has almost no power to *create*
a win, and a large power to *destroy* a draw.

Section 5's rejection of the `+` sign said it "makes the draw more attractive
exactly when it is a failure". That conflated two moments. The 19 draws-while-
ahead are failures **~50 plies earlier**, where a win still existed; a terminal
score 40+ plies below the horizon cannot reach them. At the terminal itself the
draw is the best remaining outcome, and the `+` sign is what protects it.

The antisymmetry fix (v3 → v4) was real and is **independent of the sign**:
`tv + clamp(adv)` negates correctly between sides too, because `tv = 0` there.

**So the arm that matches the hypothesis has not been built yet.** Both signs go
to SPRT; the `+` arm goes first, because it is the one the mocca mechanism and
the head-to-head record predict.

---

## 8. The gate: the change does reach the root, and the engine is ~50% nondeterministic

`movechange.py`, 422 cached self-play positions at rule50 >= 120, 400 ms/move,
two base processes plus the contempt arm.

| rule50 | n | base-vs-base (noise) | contempt outside base |
|---|---|---|---|
| 120-159 | 258 | 48.1% | 27.1% |
| 160-179 | 100 | 49.0% | 46.0% |
| 180-199 | 64 | 51.6% | 42.2% |
| ALL | 422 | 48.8% | 33.9% |

**Two things to read off this, one of which was not the point of the test.**

**(a) The engine is nondeterministic about half the time.** Two runs of the
*identical* binary on the identical FEN at 400 ms choose different moves in
48.8% of these positions, flat across every clock bucket. That is not a bug in
the harness; it is the flat-score finding showing up from a second direction. If
many moves score exactly 0, the tie is broken by whatever the timing jitter and
`value_draw()`'s `nodes & 0x2` happen to do. It also means **any** move-level
comparison here needs a control, and that the 62.5% figure from the first
uncontrolled run should not be quoted.

**(b) The two columns are not comparable, so the headline needs the internal
control.** "Outside *both* base samples" is easier to avoid than "differs from
one", precisely because the two base samples already differ half the time. The
matching floor is `P(base3 not in {base1, base2})`, which this run did not
measure. What it *does* have is a bucket where the fix cannot fire: at rule50
120-159 the terminal is 40-80 plies away, past the horizon at 400 ms, so 27.1%
**is** the floor for that statistic. Against it:

| comparison | floor | fires | z | p |
|---|---|---|---|---|
| 180-199 vs 120-159 | 27.1% | 42.2% | 2.35 | 0.019 |
| 160-199 vs 120-159 | 27.1% | 44.5% | 3.68 | 0.00024 |

The base-vs-base column being flat across buckets (48.1 / 49.0 / 51.6) is what
licenses this: the noise mechanism does not vary with the clock, so the 17-point
rise is the grade, not the jitter. **Gate passed** -- the change reaches the root,
and only where it can.

It says nothing about whether the change is an improvement, and nothing about
which sign. `gate2.py` adds the third base sample, a low-clock control bucket,
both signs side by side, and the reported score.

## 9. One source, both signs, and two binaries that reproduced exactly

`NC_DRAW_SIGN` is now a compile-time knob (`-1` contempt, `+1` draw bonus),
defaulting to `-1`, and the helper was rewritten to do its arithmetic in `int`
and cast once. Three arms from the one source:

| arm | flags | sha256 (first 16) |
|---|---|---|
| base | `-O2` | `48aa5d6e837ab4b9` |
| minus | `-DNC_DRAW_GRADE -DNC_DRAW_SIGN=-1` | `b919a115a7a296ff` |
| plus | `-DNC_DRAW_GRADE -DNC_DRAW_SIGN=1` | `3f47b2d4ecf2a960` |

All three hashes were already known from the earlier hand-written iterations:
base is unchanged through a fifth rewrite of this file, `minus` reproduces the v4
contempt binary bit-for-bit, and `plus` reproduces the v2 "material grade"
binary bit-for-bit. So the refactor is provably semantics-preserving, and the
`+` arm argued for in section 7 is the binary I had already built and then
reasoned my way out of.

---

## 10. Half of all self-match games are no-capture draws

Before spending cloud hours on an SPRT, the population the fix acts on had to be
sized. Every archived journal in `w1_match_archive/seq/` carries an `end` event
with a termination reason, so this needed no new games. 1074 games, five matches,
500 ms/move:

| end reason | games | share | mean plies | winner |
|---|---|---|---|---|
| **CaptureClock** | 539 | **50.2%** | 436 | `None` in all 539 |
| Goal | 479 | 44.6% | 248 | 315 / 164 |
| Elimination | 56 | 5.2% | 243 | 44 / 12 |

Per match the CaptureClock share runs 33.5% to 59.7%, so it is not an artefact of
one pairing.

**Why this matters more than any other number here.** The flat-VALUE_DRAW
terminal is not a rare tail case — it is the **modal way our engine's games end**.
And because those games run 436 plies against 248 for a decisive one, they consume
roughly 64% of a match's wall-clock. So the defect is both the most common outcome
and the most expensive one.

It also settles the SPRT's power, which was the real worry: a change gated on the
capture clock that only touched the corpus's 18% drawn games would need to be
worth ~100 Elo inside them to move a ±20 Elo test. Acting on 50% of games, it
does not. The test is properly powered on the relevant population.

Two side observations, recorded not chased:
- Decisive games split **315 / 164** between the two seats, a ~2:1 first-mover
  advantage. Pairs swap colours so the SPRT is unaffected, but it is large.
- 50% draws is well clear of the ~10% decisive rate that made the 8-ply-opening
  SPRT powerless, so `--opening-plies 24` is doing its job.

---

## 11. The corrected gate: weaker than I first reported, and it cannot pick the sign

`gate2.py`, 350 positions, 400 ms/move, one net, three *base* samples per
position so the nondeterminism floor is measured by the same statistic as the
effect. **This supersedes §8's z = 3.68.** That number compared
`P(cand outside {b1,b2})` against `P(b1 != b2)` in a different bucket — two
different statistics. The matching floor is `P(b3 outside {b1,b2})`, a third
sample of the *unmodified* binary, and against it the effect is much smaller.

### Move change, against the matching floor

| bucket | n | b1≠b2 | **b3 outside** (floor) | plus outside | minus outside |
|---|---|---|---|---|---|
| low, r50 ≤ 40 (control) | 130 | 43.8% | **33.1%** | 31.5% | 36.2% |
| 120-159 | 126 | 46.8% | **27.8%** | 31.0% | 28.6% |
| 160-179 | 59 | 42.4% | **32.2%** | 42.4% | 42.4% |
| 180-199 | 35 | 51.4% | **37.1%** | 51.4% | 62.9% |
| **HIGH ALL** | 220 | 46.4% | **30.5%** | 37.3% | 37.7% |

Two-proportion z against the floor:

| bucket | plus | minus |
|---|---|---|
| HIGH ALL (fix can fire) | z = +1.51, p = 0.13 | z = +1.59, p = 0.11 |
| low control (fix *cannot* fire) | z = −0.28, p = 0.78 | z = +0.53, p = 0.60 |

The control behaves exactly as it must — both arms sit on the floor where the
code is inert, which is what validates the instrument. But in the bucket where
the fix *can* fire, neither arm clears p = 0.05. **The honest reading is a
~1.5σ hint, not a passed gate.**

### Score at the root: this part is real

| bucket | n | base | plus | minus |
|---|---|---|---|---|
| low, r50 ≤ 40 (control) | 130 | 22.9 | 22.2 | 22.0 |
| 120-159 | 126 | 140.9 | 135.1 | 142.5 |
| 160-179 | 59 | 23.6 | 28.6 | 34.1 |
| 180-199 | 35 | **3.6** | 6.2 | 10.2 |

Mean `|score|` in cp. The control gives this statistic a noise scale: three
binaries within **0.9 cp** of each other where the grade cannot fire. Against
that, 160-179 (+5.0 / +10.5) and 180-199 (+2.6 / +6.6) are real — the graded
draw value does propagate to the root. It propagates *weakly*, a few cp out of a
±50 cap, because the root is a minimax over many lines and only the fully-drawn
ones carry the term.

The 120-159 row is the useful negative: mean `|score|` is 140 cp there, so the
engine is not seeing a draw at all in that bucket and the grade is mostly noise
(−5.8 / +1.6). **The flat zero only exists above ~160**, which narrows the
earlier claim, and the 3.6 cp at 180-199 is the flatness itself, measured.

### The gate cannot choose the sign, so the sign is chosen on argument

`|score|` is unsigned, so adding ±adv to a near-zero value raises its magnitude
either way — both arms go up, by construction. Move change is 37.3% vs 37.7%,
indistinguishable. So gate2 does *not* name the arm, and the pre-registration's
"whichever arm gate2 supports" cannot be honoured as written.

**`plus` is chosen, on two arguments stated before the match is launched:**

1. **Direction of the failure the hypothesis names.** With `plus`, a side that
   is ahead sees the 200-ply draw as *better* than a bare 0, so breaking a
   holdable position has to beat a higher bar. `minus` (contempt) does the
   opposite: it penalises the draw for whoever is ahead, which is precisely the
   push toward selling a stone that the mocca game looked like. Contempt is the
   right default when you are the stronger player; against Mocca we are
   **0W/13D/4L**, so a draw is *above* our realised expectation of 0.38 and we
   should not be paying to avoid it.
2. **The clock-reset asymmetry.** The net damps material by the *current*
   no-capture clock, but a capture *resets* that clock — so a stone is worth
   less than `StoneValue` while we hold it and full `StoneValue` one ply after
   we lose it. Damping what we stand to lose but not what we lose it into is
   what makes selling look free. A positive term on the draw leaf partly
   compensates; a negative one compounds it.

**This is a genuine coin-flip, not a confirmation.** A 1.5σ move-change hint
plus a few cp at the root, in the 50.2% of games that end this way, is enough to
justify one SPRT and not enough to predict its sign with confidence. H0 closes
the arm — no re-run at another cap, move time or constant.

---

## 12. The other half of the hypothesis: "drag it out and lose by time"

The hypothesis had two limbs. Everything above tests the first (the draw score
makes selling a stone look free). This tests the second, and it resolves
cleanly — **the mechanism was real, and it was already fixed on 2026-09-25.**

`sf19_ports/live/clock_audit.py`, zero games: 26,876 live moves from 31 seat
logs, which record both clocks, the increment, the budget we chose and the time
we actually spent. Our own clock is identified per move as whichever of
`rtime`/`btime` reproduces `move_budget_ms`.

**First, a correction to my own earlier framing.** I had filed this as
"`MOVES_TO_GO = 30` vs a median of 75 and a p90 of 230 of our moves". That
comparison is meaningless: the site runs a **Fischer clock with inc = 1000 ms**,
so `clock/30` is a decay constant with a *fixed point*, not a sudden-death
divisor. Game length alone cannot drain the clock. Setting charge = inc gives

    clock* = 30 * (MOVE_OVERHEAD_MS - site_overhead + 0.2 * inc)

and with the site's measured ~250 ms/move that is **3,000 ms at OV = 150** —
sitting exactly on `LOW_CLOCK_MS`, i.e. pinned to the panic boundary — and
**13,500 ms at OV = 500**. The seat's own comment predicted this; nothing had
checked it against the logs.

### Measured, and it lands on the prediction

| OV | moves | clock p05 | median | min | < 3000 (panic) | < 800 | budget maxed |
|---|---|---|---|---|---|---|---|
| 150 (pre-fix) | 23,057 | **3,017** | 19,002 | 1,038 | **4.0%** | 0.0% | 23.0% |
| 500 (post-fix) | 3,819 | **12,033** | 19,956 | **11,362** | **0.0%** | 0.0% | 0.0% |

The p05 at OV = 150 is 3,017 ms against a predicted fixed point of 3,000 — the
theory is confirmed to 17 ms.

### The user's mechanism, isolated by game length

| ply band | n@150 | median | min | <3000 | n@500 | median | min | <3000 |
|---|---|---|---|---|---|---|---|---|
| 0-99 | 9,783 | 60,614 | 2,815 | 0.1% | 1,085 | 45,487 | 14,178 | 0.0% |
| 100-199 | 6,203 | 12,974 | 1,038 | 3.1% | 837 | 22,567 | 13,627 | 0.0% |
| 200-299 | 3,588 | **4,011** | 1,419 | **10.3%** | 760 | 15,152 | 11,943 | 0.0% |
| 300-499 | 2,656 | **3,199** | 1,717 | **10.8%** | 999 | 13,121 | 11,699 | 0.0% |
| 500+ | 827 | **3,183** | 2,488 | **8.6%** | 138 | 11,956 | 11,362 | 0.0% |

**The left half is the hypothesis, measured.** Under the old constant the clock
decayed monotonically with game length — 60.6 s at ply < 100 down to the 3.0 s
fixed point past ply 200 — and about **one move in ten** was then played from
inside the panic branch, which throttles the search to `0.5 * inc` = 500 ms.
A dragged-out game really did end up being played at a third of our normal
thinking time, exactly as the hypothesis said.

**The right half is the fix, verified.** Zero of 3,819 post-fix moves entered
the panic zone, at *every* game length, and this is not a short-game artifact:
max ply per log is median 360 / p90 734 / longest 734 after the fix against
365 / 619 / 776 before, so the two regimes are matched on the very thing being
tested. At 500+ plies the clock still sits at ~12 s, converging on the predicted
13.5 s.

### Two caveats worth keeping

- **We never flagged in the moves I parsed — but we HAVE lost on time.** The six
  lowest clocks in the seat logs are 1,038 / 1,247 / 1,250 / 1,273 / 1,293 /
  1,318 ms, all pre-fix, and the `PANIC_CLOCK_MS` = 800 floor held every time. I
  first wrote from that that "lose by time is not in these logs", which was too
  strong: §15 reads the *result* trace and finds **6 games decided on time, 1W
  5L**. The seat move logs and the result trace disagree because a forfeit comes
  from a single spike (overhead worst-seen 11 s) rather than the gradual decay
  the move logs capture, and because not every game's seat log survives. The
  gradual-decay mechanism is fixed; **single-spike forfeits are a separate, still
  open failure.**
- **Whether that throttling cost us games is genuinely open**, and points the
  other way from what one would assume: `conversion-measured` found the throw
  rate *rising* 10% -> 45% as search deepens. Less thinking time in long
  endgames might have helped. Do not claim this fix as an Elo gain on the
  strength of the clock table alone.

**Consequence for every SPRT we run.** `bot eval` and `bot play` accept only
`--sims` and `--move-ms`: there is no game clock, no increment and no time
forfeit anywhere in the harness. So the seat's time manager — the thing this
section is about — is **bypassed in every internal match we have ever played**.
That cuts both ways: no time-management change can be measured internally, and
an eval change that tests well internally is being tested without the clock
regime it will actually run under.

---

## 13. The mocca game, traced — the hypothesis is right, its mechanism is not

The user named one game: *"the last game with mocca. It was drawing then randomly
started doing shit then compromised its position and lost."* That game is the
**09:32:58 LOSS vs Mocca, "reached the corner"** — the fourth game in a row
against Mocca, the previous three all drawn at 200 plies without a capture.
`sf19_ports/live/mocca_collapse.py` walks its 197 recorded positions.

### What actually happened

| ply | eval | us | them | no-capture |
|---|---|---|---|---|
| 212 | **+55** | 5 | 4 | 150/200 |
| 224 | +30 | 5 | 4 | 162/200 |
| 228 | +12 | 5 | 4 | 166/200 |
| 232 | +3 | 5 | 4 | 170/200 |
| **236-250** | **0** (every ply) | 5 | 4 | 174-188/200 |
| 252 | −15 | 5 | 4 | 190/200 |
| 256 | **−78** | 5 | 4 | 194/200 |
| 258 | −76 | **4** | 4 | **reset to 1** |

We were **a piece up for the entire window**, and the evaluation slid from +55 to
exactly 0.00 and then to −78 while the material never changed. Six plies from the
draw the extra piece was gone and the counter reset; from there it was mate-10 at
ply 378, down to one piece against three, and the corner.

### Why it collapsed: the search horizon, confirmed to the ply

The collapse is **not** the net damping material by the clock. It is the forced
draw entering the search horizon — once every line reaches the drawn terminal
node, the root score *is* the draw score. Prediction: `|eval| ~ 0` exactly when
`200 - counter <= depth`. Measured, restricted to positions where we were a piece
up:

| ply | plies to draw | depth | in horizon | eval |
|---|---|---|---|---|
| 232 | 30 | 25 | no | +3 |
| 234 | 28 | 25 | no | +7 |
| **236** | **26** | **32** | **YES** | **0** |
| 238 | 24 | 30 | YES | 0 |
| 250 | 12 | 31 | YES | 0 |

| group (all a piece up) | n | mean abs eval |
|---|---|---|
| draw **outside** the horizon | 97 | **48.8 cp** |
| draw **inside** the horizon | 11 | **11.8 cp** |

It flips on the **exact ply** the horizon first covers the draw — not one early,
not one late. This is `nocapture-draw-flat`'s "deeper = blinder" caught per-ply
in a real game.

**A second-order effect worth keeping:** depth *jumps* 25 → 32 at ply 236. Once
the draw is in view the tree collapses onto one value, the search gets cheap, and
it sees further — which confirms the draw harder. The blindness is
self-reinforcing.

### The hypothesis is confirmed; one limb of its mechanism is not

- **Confirmed, and this is the core of it:** the eval really does collapse to
  zero while materially ahead. Fourteen consecutive plies reading **0.00** while
  holding an extra piece. The engine had no gradient telling it that it owned
  something worth defending.
- **Not confirmed — the "selling" step.** The piece was lost *after* the eval had
  already reached −78, and damping a +55 toward zero cannot produce −78. So at
  the end the engine was reading a real threat (Mocca, a piece down, had to break
  the fortress and could do so on its own terms), not choosing to smash a
  fortress to escape a draw. **It did not sell the piece. It went blind, and then
  the piece was taken.**

That distinction matters because it is a *worse* problem, not a lesser one:
"blind for fourteen plies while a piece up" explains the three preceding draws
against Mocca as well as this loss, whereas "sells stones" would not.

### An accidental calibration check on the running SPRT

The layer is right: the zero at the root *is* the terminal draw value propagated
through a collapsed tree, so grading the terminal node is the correct place to
intervene.

**But the magnitude is not — see §14, which retracts what this paragraph first
said.** I noted that `NC_DRAW_CAP = 50` landed within 2 cp of this game's 48.8 cp
mean and called it an accidental calibration. Across 94 games the same statistic
is **278 cp median / 457 cp mean**, so the 48.8 was one unusually quiet game and
the coincidence was an artifact of n=1. The cap restores roughly an eighth of the
edge it is standing in for.

---

## 14. How often does the blindness happen? 94 live games

§13 is one game. `sf19_ports/live/horizon_population.py` applies the same test to
every game in the status trace: 94 games, 10,388 scored positions.

| | positions | share |
|---|---|---|
| scored | 10,388 | — |
| we are materially **ahead** | 4,417 | 42.5% |
| the draw is **inside the horizon** | 825 | 7.9% |
| **both — ahead and blind** | **511** | **4.9%** |

**Games containing at least one ahead-and-blind position: 22 of 94 (23.4%).**
Their results: **2 LOSS, 9 DRAW, 0 WIN**, plus 11 whose result is not in the
trace. Of the eleven decided, not one was won.

That last figure is suggestive and **not causal** — reaching this state requires a
long capture-free stretch, which selects for blocked, drawish positions where an
extra piece may genuinely be worth little. It is an association, not a
measurement of cost. But 0 wins in 11 decided games is worth having on record.

### The retraction §13 needs

| while materially ahead | n | mean abs eval | median |
|---|---|---|---|
| draw **outside** the horizon | 3,906 | 457.1 cp | **278.0** |
| draw **inside** the horizon | 511 | 64.3 cp | **0.0** |

The median position where we are ahead and the draw is in view scores **exactly
0.0**. But the "outside" group is not matched to it — it includes crushing
positions, which is why the mean is 457 cp. The matched comparison is §13's:
the same position sequence, material unchanged, crossing the boundary.

What this does establish is that **`NC_DRAW_CAP = 50` is small**, not
well-calibrated. With `StoneValue = 208` and `NC_DRAW_MAT_DIV = 8`, one stone of
edge buys 26 cp and the term saturates at two stones. Against a median edge of
278 cp it restores about an eighth. That is defensible — a draw should not be
scored as a win, and the term is meant to break ties toward holding what we have,
not to pretend the draw is a victory — but it is a *choice*, and the running SPRT
is testing that choice and not just the sign. **If the SPRT returns H0, the cap is
the first thing to suspect, and the honest statement is that the arm as specified
is closed rather than that the idea is dead.**

---

## 15. What it is worth: 212 live games, and who exploits it

`sf19_ports/live/live_record.py` reads every `Result:` line in the status trace.
**212 finished games: 172W 29D 11L, score 0.880, Elo 285.**

### By ending reason

| reason | W | D | L | n | share |
|---|---|---|---|---|---|
| reached the corner | 156 | 0 | 4 | 160 | 75.5% |
| **200 plies without a capture** | **0** | **29** | **0** | **29** | **13.7%** |
| resignation | 13 | 0 | 0 | 13 | 6.1% |
| **time** | **1** | 0 | **5** | 6 | 2.8% |
| abandonment | 1 | 0 | 2 | 3 | 1.4% |
| stalemate | 1 | 0 | 0 | 1 | 0.5% |

Two things fall out. **The 29 no-capture games are 0W-29D-0L**, which is
tautological — the rule declares the draw — so the right reading is not "we lose
these" but "we reach move 200 without having converted, 13.7% of the time."
And **six games were decided on time, five of them losses**, which is the
correction §12 now carries.

### Who exploits it — this is the sharp part

| opponent | Elo | W | D | L | n | score | no-capture draws |
|---|---|---|---|---|---|---|---|
| 1secondBot | 149 | 17 | 0 | 0 | 17 | 1.000 | 0% |
| VladKlentBot | 226 | 22 | 0 | 0 | 22 | 1.000 | 0% |
| Psionic | 1 | 13 | 0 | 1 | 14 | 0.929 | 0% |
| hushlite | 292 | 88 | 8 | 3 | 99 | 0.929 | 8% |
| Lora-Bot | 175 | 5 | 1 | 0 | 6 | 0.917 | 17% |
| VladHugeConvNet | 255 | 4 | 3 | 0 | 7 | 0.786 | 43% |
| Vlad_NNUE | 258 | 15 | 9 | 1 | 25 | 0.780 | 36% |
| VladLargeConvNet | 251 | 5 | 0 | 2 | 7 | 0.714 | 0% |
| **hushhush2** | **108** | **0** | **3** | **1** | **4** | **0.375** | **75%** |
| **Mocca** | **273** | **0** | **5** | **2** | **7** | **0.357** | **71%** |

**The no-capture share tracks our score almost monotonically.** Against every
opponent we beat clean — 1secondBot, VladKlentBot, Psionic — it is 0%. Against
the two we cannot beat at all it is 71% and 75%.

And the second of those is the finding worth acting on: **hushhush2 is rated
108.** We are 285. A 177-point favourite scores 0.375 against it, 0W-3D-1L, with
three of the four games running out the capture clock. Mocca at least is a peer.
hushhush2 is not a strength problem — it is a **specific exploitable hole**, and
the bots that have found it are the only ones we cannot beat.

### The prize, bounded

| convert this share of the 29 no-capture draws to wins | score | implied |
|---|---|---|
| 25% | 0.897 | **+30 Elo** |
| 50% | 0.914 | **+65 Elo** |
| 100% | 0.948 | +159 Elo |

**The 100% row is an upper bound and almost certainly unreachable** — a blocked
fortress can be genuinely drawn, and `eviction-endgame` already records that the
drawn verdict for the solved family did not survive scrutiny while the rock-race
theorem did. But the first row needs only one no-capture draw in four to be
winnable, and 23.4% of games reach a state where we are materially ahead and
cannot see it (§14). On the measured record this is the **largest single axis I
have quantified**, and it is the one the user pointed at.

## 16. The SPRT verdict: H0. The fix does not work.

Pre-registered in §11 and run to its bound with nobody watching, so there is no
stopping-rule leak:

    ncdraw_plus_vs_base_500ms
    H0 = +0 Elo   H1 = +20 Elo   alpha = beta = 0.05   cap 1000 pairs
    500 ms/move, 24 opening plies, seed 20260926, 8 in flight, 16 vCPU c4a

    pairs 46   penta(0,.25,.5,.75,1) = [1, 6, 37, 2, 0]
    score 0.4674   estimate -23 Elo   LLR -3.02   bounds [-2.94, +2.94]
    VERDICT: H0 -- candidate is NOT STRONGER

**`NC_DRAW_CAP = 50` is rejected at 500 ms/move.** The point estimate is
*negative*, so this is not "we needed more pairs": the arm closed on the wrong
side after 46 pairs, 37 of which were pure draw-draw. Per the pre-registration
the arm is closed — no re-run at another cap, another move time or another
constant without a new reason.

**What H0 does and does not kill.** It kills *this patch*, not the diagnosis.
§13's horizon measurement stands on its own: the eval really does go to exactly
0.00 fifteen plies before the draw while a piece up, and depth really does jump
25 → 32 as it happens. What H0 says is that **clamping the score floor does not
recover the lost play** — which is consistent with §13's own conclusion that the
failure is the *search collapsing onto one leaf*, not the number at the root. A
floor gives the search a different number to report about a tree that has
already collapsed. To fix a collapsed tree you have to stop it collapsing, and
that is a search change, not an eval constant.

The honest summary: the hypothesis was right about the symptom, my patch aimed at
the wrong layer, and the measurement that told me so cost one SPRT.

## 17. Correction: the live record was inflated by terminal redraws

**Everything §15 says about the size of the prize was computed on a bad count,
including the table I put in §0.** The status log is a *redrawn terminal
display*: each `Result:` line is emitted many times over, each redraw ending in
`\x1b[K`. 385 raw `Result:` lines are 301 games. And there are **two** result
formats, the older one carrying no Elo:

    A  HH:MM:SS  Result: WIN vs hushlite (Elo 281), reached the corner. My Elo 290 (+0).
    B  HH:MM:SS  Result: WIN (resignation) vs Psionic, 143 plies, tc 60+1.

§15 matched only form A and did not dedupe, so it counted 212 "games" that were
really 128, and missed 173 lines of an entire earlier era. `live_record.py` now
parses both shapes and collapses only *adjacent* identical records, which is
exactly the redraw case and cannot merge two distinct games.

**The corrected record: 301 games, 215W 68D 18L, score 0.8272, Elo +272.**

| reason | W | D | L | n | share |
|---|---|---|---|---|---|
| reached the corner | 157 | 0 | 10 | 167 | 55.5% |
| **200 plies without a capture** | 0 | **68** | 0 | **68** | **22.6%** |
| resignation | 47 | 0 | 0 | 47 | 15.6% |
| **time** | 7 | 0 | **6** | 13 | 4.3% |
| stalemate | 3 | 0 | 0 | 3 | 1.0% |
| abandonment | 1 | 0 | 2 | 3 | 1.0% |

**The exploit pattern survives the correction and is stronger than I reported.**
It is now monotone with no exceptions across 13 opponents:

| opponent | W-D-L | n | score | no-capture share |
|---|---|---|---|---|
| **Mocca** | 0-19-5 | 24 | **0.396** | **79.2%** |
| **hushhush2** | 3-7-3 | 13 | **0.500** | **53.8%** |
| hushlite | 16-16-3 | 35 | 0.686 | 45.7% |
| Vlad_NNUE | 36-14-2 | 52 | 0.827 | 26.9% |
| VladHugeConvNet | 17-7-0 | 24 | 0.854 | 29.2% |
| VladLargeConvNet | 33-4-3 | 40 | 0.875 | 10.0% |
| Lora-Bot | 5-1-0 | 6 | 0.917 | 16.7% |
| Psionic | 51-0-1 | 52 | 0.981 | **0%** |
| 1secondBot / VladKlentBot / brute / Guest | 53-0-0 | 53 | 1.000 | **0%** |

Every opponent we beat cleanly has a **0%** no-capture share. Every opponent that
holds us has a large one. The two worst are the two largest.

**The prize is bigger than §15 said**, because the draws are 22.6% of games, not
13.7%: converting 25% / 50% / 100% of the 68 draws gives score 0.8555 / 0.8837 /
0.9402 = **+37 / +80 / +207 Elo**. The 100% row remains unreachable.

**What did NOT change:** the direction of every conclusion, and the time limb —
still 13 time-decided games, 7W **6L**, still the separate unfixed
single-spike failure of §12.

## 18. The real boards are recoverable, and that is the next instrument

`sf19_ports/live/live_boards.py`. The status log renders **the whole 9x9 board
once per our move — 22,874 of them**, and that is the only surviving record of
the actual positions: the seat logs and the result trace keep piece *counts*
only. The parser reproduces §13's blind window exactly from the board stream
(ply 236, counter 174, 5v4, depth 32, eval 0.00), which is the check that it is
reading the right games.

It emits a ready `bot analyse` request: the 81-cell `setup` in the site frame
`analysis.rs` wants (rank 1 first, a→i, codes 1/2/3 for the side to move and
4/5/6 for the other), plus `to_move` / `psc` / `ply`. `psc` is directly
settable, so the same position can be asked twice — once at its true counter,
once at 0.

Two parsing traps worth keeping: the status line has two shapes (`ply N of N`
and `PaperNet2 · I'm Red · ply N · clocks:`) and matching one dropped 13,211 of
24,632 boards; and `Joined a game in progress` recurs on every reconnect, so
segmenting on it split 192 games into 815 — ply numbers are monotone inside a
game, so a *decrease* is the reliable boundary.

**The question this unlocks is the one that gates the whole +37 Elo: are those
fortresses winnable at all?** Here is the extreme case, the final position of a
Mocca draw, straight from the log:

      5  . . . . . . p . .
      4  . r . . . . . . .
      2  . . . . r . . . .
      1  S P . · . . . . .
    I'm Red · ply 678 of 678 · eval 0 (dead level) · depth 127 · 12.0M nodes
    pieces: me R2 P1 S0 (3) · them R0 P1 S1 (2) · no capture for 199/200

**A piece up, and depth 127.** That is the collapse in its purest form: the
search reaches 127 plies deep because every line it looks at arrives at the same
drawn leaf, and its whole plan is one move (`b1-b2`). Whether a fresh 200 plies
from that position converts is a playout question, and the playout is now the
only missing piece.
## 19. Who actually breaks the loop — WRONG ANSWER FIRST, then the right one

**This section had the wrong definition of "break" and reached the opposite
conclusion. The retraction is kept because the error is instructive.**

**What I measured first (wrong):** I attributed each near-draw break to *whoever
made the capturing move*, read off the counter reset. That gave 44 breaks split
22 ours / 22 theirs, an apparent symmetry, and a story in which our real failure
was sitting still while ahead.

**Why it is wrong (user, 2026-09-26):**

> "Mocca made the capturing move, but we were the ones to do Sd5-e6, which went
> against the loop. We changed the state. […] Breaking the loop means that its
> alternating between two boards and one side makes a move that goes against it,
> not the side that captures the next piece."

The capture is downstream. In a repetition the position returns to itself; the
side that *breaks* it is the one whose move first departs from the cycle, and
the capture can land dozens of plies later, made by either side. Attributing the
capture measures who cashed in, not who opened the door.

## 19b. Redone under the right definition: we break 78, they break 22

`sf19_ports/live/loopbreak/cycle_break.py`. The board is hashed per snapshot (snapshots are
after *our* move, so one snapshot = 2 plies and a shuffle has period p ≥ 2). A
loop is a maximal run with `board[k] == board[k-p]` seen at least 3 times. The
break is the first off-cycle board. Attribution follows the clock: after the
last on-cycle board **their** move comes first, then ours — so their move is
checked against its cycle counterpart first, and only if it matches is the
deviation ours.

**103 loops reach counter ≥ 100. Periods: 101 are p=2, two are p=4.**

*(Snapshot at 2026-09-26 17:04. `papernet-status.log` is live and the bot is
still playing: an earlier run 70 minutes before gave 101 loops and 78/22. Every
number below moves as games arrive; the direction has not.)*

| who first leaves the cycle | n |
|---|---|
| **us** | **80** |
| them | 22 |
| unattributable (a token missing) | 1 |

**Not a data artifact.** On all 80 of our breaks the opponent's move on that
visit is known *and* equals its cycle counterpart — they were still playing
along. Restricting to the 102 breaks where all four move tokens are known
leaves the split unchanged at 80 / 22.

**The user's example verifies exactly.** Game 391 vs Mocca, ply 214, counter
152, we are a piece up at 5v4, the cycle had been traversed **35 times**, and we
played `d5-e6` where the cycle move was `d5-e4`. Their capture lands 44 plies
later. The recollection was right down to the move.

**How long we hold before deviating:** median 7 visits to the cycle (max 50).
They hold for a median of 20. We are the impatient side by a factor of three.

## 19c. What the break costs

| who left | clock ran out → draw | a capture followed | total |
|---|---|---|---|
| us | 37 | **43** (29 by us, 14 by them) | 80 |
| them | 13 | 9 (8 by us, 1 by them) | 22 |

So **43 times we left a repetition and the capture clock was then reset** —
the draw we had in hand was given back. Median 32 plies from the break to the
capture (2 … 86), i.e. the break and its consequence are far apart, which is
why attributing the capture hid this completely.

Material when we left: level 28, ahead 19, behind 31. **This also kills the
"we only break when level or behind" line from §19a** — 19 of our breaks are
made while ahead, and in 3 of those we have lost a piece five snapshots later
with nothing to show for it.

## 19d. The decisive test: nothing was seen

The user's argument is an *a priori* one and it is worth restating because it is
correct:

> "if we break 10 moves before the clock fires, because we can force a win in
> 10, we could have forced a win 100 moves ago when the loop started, too […]
> and even if theres some esoteric strategy where it involves the draw clock,
> well, the enemy would just refuse to take our pieces and draw, so that by
> definition is NOT a forced win."

The position is identical each cycle — only the capture clock advances. A forced
win is a property of the position, so it cannot appear late; and a "win" that
needs the clock is refuted by the opponent declining to capture. Therefore
leaving a loop is right only if the search has *just now* found something it
could not see before. That is a measurable prediction: the eval must jump.

`loopbreak/break_why.py`, over all 80 of our breaks, comparing the eval at the
break against the median eval across the cycle we were sitting in:

| statistic | value |
|---|---|
| median eval change at the break | **−5 cp** |
| p10 … p90 | −141 … +22 |
| \|change\| ≥ 100 cp | 11 of 80 |
| change ≥ +200 cp | **1 of 80** |
| **a mate score at the break** | **0 of 80** |
| depth during the cycle → at the break | 26 → 25 (median change **+0**) |

**Nothing is seen.** No mate, no deeper search, and the median move in the eval
is slightly *negative* — we step out of the cycle into a position our own
engine rates no better, and often worse. Of the 43 breaks that reset the clock,
**36 moved the eval by less than 100 cp**. The single real exception is game 184
at ply 214: +1016 → +2865, a genuine win found, and we captured. One in
eighty.

**The user's rule survives the data:** absent a newly-visible forced win, do not
leave the loop. 79 of our 80 departures had no such justification.

## 19e. But the mechanism is *not* the one either of us named

Two candidate causes are now excluded by reading the live engine.

**It is not score decay.** `evaluate.cpp:109` does damp the eval by the clock —
`v -= v * pos.rule50_count() / (2 * CAPTURE_CLOCK - 1)` — and its own comment
says damping "makes a shuffling line decay while a capture, which zeroes the
clock, does not, so converting lines outrank waiting ones", which is precisely
the original hypothesis. **But it sits inside `#ifdef CLOCK_DAMP`, on the
`RpsNet::loaded()` branch, and `CLOCK_DAMP` appears nowhere in the Makefile and
its string is absent from the shipped `live/stockfish`.** Dead code in the live
build. The decay survives only on the `raw_eval` fallback path, which the live
net does not take. This is consistent with the §16 SPRT: the eval-side patch
returned H0 because the eval side is not where this lives.

**It is not that repetition is scored as a draw either.** The opposite —
`position.h:23` states "200 plies without a capture draw. There is no repetition
rule", and both hooks are stubs:

```cpp
bool is_repetition(int) const { return false; }
bool upcoming_repetition(int) const { return false; }
```

`is_draw` is nothing but `st->rule50 >= CAPTURE_CLOCK`. **The engine has no
representation of a loop at all.** It cannot know it is repeating, cannot score
a repetition, and cannot choose to hold one. Every visit to the same board is a
fresh search of a position it thinks it has never seen.

**What is left is drift.** From positions revisited three or more times at
counter ≥ 100 (`loopbreak/determinism.py`):

| side | positions | revisits | always the same move | more than one move | P(repeat the move) |
|---|---|---|---|---|---|
| them | 224 | 2546 | 189 | 35 | **0.980** |
| **us** | 215 | 2488 | 116 | **99** | **0.940** |

**We answer 46% of these positions in more than one way; they answer 16% of
theirs.** Per visit we are ~3x likelier to deviate (6.0% vs 2.0%), and 5.8% per
visit against a cycle traversed 20+ times makes a break near-certain — which is
exactly the median-7-visits-versus-20 asymmetry in §19b. Two things drive it,
and they are not separated yet: the engine is time-nondeterministic (a different
move ~49% of the time at a fixed 400 ms, see the `engine-move-nondeterminism`
finding), and the clock *is* a net input (`since_row`/`left_row` in
`rpsnet.cpp`), so the one thing that genuinely changes each cycle is fed to the
net and can flip its preference on its own. **The same-board key does not hold
the clock fixed, so "identical position" is identical to us but not to the net.**

## 19f. What this makes the fix

Not an eval clamp. The engine needs to *know* it is repeating:

1. **Give the search a repetition table** (the stubs are already there and
   already called at `search.cpp:793`). With no repetition rule in the game a
   repetition is not a draw, so it cannot simply return `VALUE_DRAW` — but it
   can be used to make the *same* choice as last time, which is all a fortress
   needs.
2. **Failing that, a stickiness rule at the seat level:** if the board matches a
   position seen before at a higher clock and the eval has not moved by more
   than some margin, replay the earlier move. This is testable without touching
   the engine and it directly targets the 79-of-80 unjustified departures.
3. **Neither of these is what §16 rejected.** The SPRT killed a score floor.
   This is a move-selection change, and the measurement above says the score was
   never the problem: at the moment we leave, our own eval says the departure
   gains nothing.

**Still unmeasured:** whether holding the loop is actually better, i.e. the
counterfactual. The 43 clock-resets sit in only 7 games that carry a parsed
result (2W 3D 2L, score 0.500 — the same 0.500 the draw was worth), so the
outcome evidence is too thin to lean on. The *mechanism* evidence above is the
strong part: 0 of 80 mate scores and a median eval change of −5 cp.

---

## 20. SHIPPED: the sticky loop lock (built 17:56, actually LIVE 20:00:02, no SPRT)

> **⚠ CORRECTION to this section's original claim.** It first read "SHIPPED
> 2026-09-26 17:56". Installing the binary did **not** ship it. `sf19nn_live_seat.py:148`
> opens the engine in `Engine.__init__`, called **once** per seat process, and between
> games the seat sends only `ucinewgame` (line 341); the seat itself is spawned **per game
> slot, not per game** (`rpsbot.py:304`, "the engine is not started until the server sends
> the first frame for it"). Seat 61024 / engine 61025 had been up since **13:53:41** and
> played ~10 more rated games on the **17 Sep** binary -- proved by **zero `depth=1`
> replies** in `logs/seat_61024.log` against 1,485 replies at depth >= 10.
> It went live only after the user authorized killing the seat: with no game in progress
> (both logs stale, 0.0% CPU on seat and engine) `kill 61024` left it `<defunct>`, took
> engine 61025 with it, and the next game brought up seat **78913** / engine **78914** at
> **20:00:02** with the correct net and threads. `connect_bot2` (56039) and
> `papernet_status.py` (8591) were untouched throughout.
> **Never infer "live" from the binary on disk** -- compare the engine pid's start time
> against the binary's mtime.

§19 ended with "the fix is move-selection stickiness, not an eval clamp". It is
now in the live engine. The user authorized shipping it without a match: *"I
think its like 99% chance a benefit so no need to really test it."*

**Four files in `sf19_ports/sf19nn/src/`:**

| file | change |
|---|---|
| `position.h` | `Key board_key() const { return st->key; }` — the clock-free key. `key()` calls `adjust_key50`, which mixes the capture clock in *on purpose* for the TT, so it can never match a loop across clock values. |
| `search.cpp` | a file-static `unordered_map<Key, StickyEntry>` plus the lock, placed in `Worker::start_searching()` just before `threads.start_searching()`. |
| `search.h` | declares `Search::sticky_clear()`. |
| `engine.cpp` | calls it from `Engine::search_clear()`, i.e. on `ucinewgame`. |

**The rule.** `STICKY_MIN_CLOCK = 60`, `STICKY_VISITS = 3`, `STICKY_RECHECK = 16`.
Below clock 60 nothing happens at all. At or above it, each root board's best move
is recorded; on the 4th visit to the same board the engine sets
`rootMoves = RootMoves{*rm}` and `limits.depth = 1` and replays it. Every 16th
locked reply re-searches at full budget. An entry whose recorded score reached
`VALUE_TB_WIN_IN_MAX_PLY` is **banned** and never locked — this is the one escape
the user's own argument demands: if a win is actually in sight, keep looking.

`limits.depth = 1` is doing three jobs at once. `search.cpp:305`
(`if (!limits.depth && !skill.enabled()) bestThread = threads.get_best_thread()`)
means setting it keeps `bestThread == this`, so the six helper threads cannot
outvote the lock; `search.cpp:395` exits the ID loop after depth 1; and normal
`info depth` / `bestmove` output still flows, so the seat's parsing, logging and
eval display are untouched.

**Time safety, which was the user's only stated constraint.** Nothing in the
patch touches the budget. Visits 1–3 each get the *normal* per-move budget, which
is where the "think a little extra when the loop starts" comes from — three full
searches instead of one. Locked replies measured **1 ms** at Threads = 1, 2 and 6,
so the clock *grows* through a held loop. A forfeit is impossible by construction.

**Smoke test (`b4a4 h6i6 a4b4 i6h6` is an exact 4-ply cycle from startpos, so
×16 = 64 plies returns the board to startpos with the clock at 64):**

| root clock | searches 1–3 | search 4 | searches 5–7 |
|---|---|---|---|
| 64 | depth 17–21, ~380k nodes, 800 ms | **depth 1, 28 nodes** | **depth 1, 1 ms** |
| 8 | full | full | full — never locks |
| 64 then `ucinewgame` | — | locked | **full again** |

Same move returned on every locked reply, at 1, 2 and 6 threads.

Two things the test also established, both worth keeping:

- **The first locked reply often still burns the whole movetime** (depth 1, 28
  nodes, 801 ms). That is **not the patch**: the unpatched 17 Sep binary shows the
  identical alternating "800 ms / ~700 nodes" stall when the same root is
  re-searched back to back. It is bounded by the budget either way, so it was left
  alone.
- **The last search before `quit` is always a lie** — depth 1, 0 nodes, and the
  first legal move — because piping the whole script in lets the engine read
  `quit` and abort the search in flight. Always send one extra `go` to discard.

**Build note.** `src/Makefile` is upstream Stockfish's, untouched, and this is a
pruned tree: its `SRCS` names `attacks.cpp`, `syzygy/` and the whole `nnue/`
subtree, none of which exist. `make build` dies on the absent `../scripts/net.sh`;
`make all` and `make stockfish` **exit 2 printing nothing whatsoever**. The real
build is `sf19_ports/sf19nn/build.sh` (clang++ directly, `-O2` default). It was run
serially under `nice -n 19` rather than its usual 4 parallel jobs, because the live
bot was mid-game on an 8 GB machine already 1.1 GB into swap.

Installed with `mv live/stockfish live/stockfish.pre_sticky_17sep` and then a
`cp` — overwriting in place risks `ETXTBSY` and disturbs the engine of the game in
progress, whereas `mv` leaves that process's inode alone. The seat spawns a fresh
engine per game, so it takes effect on the next game with no bot restart.

The new binary is functionally identical to the 17 Sep one apart from this rule:
the only other source changes since then are `evaluate.cpp`'s `CLOCK_DAMP` block
(macro never defined), the `raw_eval` damping (dead whenever a net is loaded), and
`Eval::classLoss` / `Eval::classDamp`, which default to 0 and which the seat never
sets.
