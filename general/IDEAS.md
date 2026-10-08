# Idea list — ranked, 2026-09-25

There was no written list; "idea #1" was only ever a phrase in a report. This is it, ordered by
expected value per dollar, with the closed ones kept so they are not re-run.

---

## ROADMAP — written 2026-09-26 20:4x, after the sticky lock fired live

Ordering rule used: **a free measurement that can kill an idea always outranks the idea.** Three
proxies on this project have now failed to predict an SPRT (8-sim screen, `draw|pred|`, `corr`), so
every item below says what it would take to be *falsified* cheaply, and nothing goes to a host until
its free test has run.

---

### R1 — The contempt arm of the drawn-terminal tiebreak. **Built, never tested.**

**The record correction that creates this item.** §16 of `NOCAPTURE_DRAW_RESULT.md` reports H0 at
−23 Elo and I have been treating "tiebreak inside the draw" as open and `NC_DRAW_CAP` as closed.
Both readings were wrong. The arm that ran was **`ncdraw_plus_vs_base_500ms` = `NC_DRAW_SIGN=+1`**,
the *draw bonus*. §7 ends "**Both signs go to SPRT**; the `+` arm goes first", §9 built and
digested both (`minus` = `b919a115a7a296ff`, `plus` = `3f47b2d4ecf2a960`), and §11's
pre-registration closes **the arm** — "no re-run at another cap, move time or constant". The
`minus` arm is a different arm, not a re-run. It has never played a game.

**What it does, exactly.** `search.cpp:196` `nc_draw_grade()`, already in the tree, gated behind
`-DNC_DRAW_GRADE`, absent from the live binary (0 hits for `NC_DRAW` in `live/stockfish`). At a
node where `rps_terminal()` returns `VALUE_DRAW` (i.e. `rule50 >= 200`):

    mat = non_pawn_material(us) - non_pawn_material(~us)
    adv = mat/8 + eval/4          clamped to +-50
    return VALUE_DRAW + SIGN*adv

`SIGN=-1` charges the draw against whoever is ahead, so **any line still alive outranks running the
clock out**. Capped at 50 against `StoneValue = 208`, so it can never buy a real sacrifice, and it
is signed rather than one-sided so a terminal negates exactly between the sides and does not
pollute the TT.

**Why it is now first, and what specifically changed.** §7 rejected `minus` on one empirical
premise: "by rule50 190 the win is already gone (MultiPV 8 shows one distinct score), so contempt
has almost no power to *create* a win, and a large power to *destroy* a draw." That premise was
n=1 (the mocca game) and **`draw-horizon-blindness` refutes it at n=254**: the flat zone is not the
last ~15 plies, and it **moves left as the search grows** — 0/44 flat at clock 175-179 at 20k
nodes but 13/44 at 800k, and 58 positions go flat *only* at the top of the ladder against 1 only at
the bottom. The live bot searches ~5M nodes. So the window where the search still has a gradient
and contempt can act is clock ~164-180, a window §7 assumed was already dead. That is a new
reason, which is exactly what §11's pre-registration requires before touching this axis again.

**The free test that can kill it, and its control.** Rebuild twice (`build.sh` links to
`sf19nn/stockfish`, NOT `live/stockfish`, so the live bot cannot be disturbed) and re-run the
identical 254-position band ladder from `flatten/flatten_sweep_band.py`:

| arm | expected if the mechanism story is right |
|---|---|
| `-DNC_DRAW_GRADE -DNC_DRAW_SIGN=-1` | `spread == 0` at 800k nodes falls well below 80/254 |
| `-DNC_DRAW_GRADE -DNC_DRAW_SIGN=1` | **the control** — same magnitude, opposite sign |
| unpatched | 80/254, already measured |

**The control is the point.** `plus` already returned −23 Elo. If `plus` reopens the spread just as
much as `minus`, then "reopens the spread" does not predict Elo and R1 must not be SPRT'd on that
evidence — the same trap that cost this project three proxies. Only a *differential* between the
signs justifies a host. Cost: ~10 min per arm, A$0, local, no games.

**Known conflict, stated before testing: R1 and the sticky lock fight each other.** Contempt only
bites once a drawn terminal is inside the horizon, i.e. clock ≳ 170 at depth 30 — which is *inside*
the locked zone (the lock engages at clock ≥ 60 on the 3rd repeat, measured firing at clock 74).
Where the board repeats, the lock replays at depth 1 and the search never runs, so contempt is
inert. Its remaining window is non-repeating play at high clock. Coherent division of labour
(contempt tries to convert; the lock holds the draw once the position is provably repeating) but it
caps R1's upside, and if the ladder shows a differential the SPRT must be run **against the live
lock binary**, not against a pre-lock base.

---

**GATE READ 2026-09-26 21:43 -- THE SIGNS DIFFER, SO R1 EARNED A HOST. SPRT LAUNCHED 21:58.**
`flatten/ladder_arms.py` -> `analyze_arms.py`, 254 positions x 6 rungs x 2 clock arms per
binary, 3,048 rows per arm, zero incomplete positions:

| rung | base | minus | plus |
|---|---|---|---|
| 100k | 42/254 (16.5%) | **12 (4.7%)** | 40 (15.7%) |
| 400k | 74/254 (29.1%) | **34 (13.4%)** | 72 (28.3%) |
| 800k | 80/254 (31.5%) | **44 (17.3%)** | 77 (30.3%) |

Paired at 800k: flat only in base **45** vs flat only in minus **9**; base-vs-plus a
symmetric 14 vs 11. Clock rewritten to 0 gives 9/9/9 across all three arms, so the collapse
is clock-specific. `plus` is the arm that already measured **-23 Elo** and it does not move
the statistic -- exactly the differential the pre-registration above demanded. **This is a
veto that did not fire, not a prediction.**

Mechanism for the asymmetry: plus credits the draw *to* whoever is ahead, minus charges it
*against* them (`search.cpp:175-178`). We lead in these positions, so plus leaves the draw
about as good as the best line (root stays flat) while minus makes it strictly worse.

**Validity checks -- two of these could have invalidated the gate.**
1. **The sticky lock could have corrupted the ladder.** Band positions sit at a high
   capture clock (the lock's firing condition) and each is searched 6 times. Clear both
   ways: `sticky_clear()` runs on `ucinewgame` (`uci.cpp:143` -> `engine.cpp:182`), and
   there are **zero `depth == 1` rows in 4,572 searches**.
2. **The arms could have been the same build.** All binaries are byte-identical in *size*
   (459,096). md5 proves minus/plus differ from base and from each other, and base's
   `13f11acd...` equals `live/stockfish` -- the baseline IS the shipping engine.
3. **`NC_DRAW_CAP = 50` is not an extra flag** but a default inside `#ifdef NC_DRAW_GRADE`
   (with `MAT_DIV 8`, `EV_DIV 4`), so the two defines alone reproduce the SPRT arms.

**MOVE-CHANGE RATE IS USELESS AS A SCREEN.** Best move differs in 70.9% / 69.3% / 67.3% of
positions (base-vs-minus / base-vs-plus / minus-vs-plus) -- including the arm that lost 23
Elo. Only the flat-zone count discriminates. Incidental cost: `minus` reaches 1-2 plies
**less** depth at equal nodes at every rung, so the grade widens the tree.

**The line-71 requirement is satisfied:** all three host binaries (`sf_base`, `sf_minus`,
`sf_plus`) carry the sticky-lock symbols, so the SPRT is lock-vs-lock on both sides.

**The run:** `ncdraw_minus_vs_base_500ms` on `rps-ncdraw` (c4a-standard-16,
asia-southeast1-b), H0 +0 / H1 +20 Elo, alpha=beta=0.05, cap 1000 pairs, 500 ms/move, 24
opening plies, IN_FLIGHT 8, seed 20260926 -- every measurement constant identical to the
closed plus run, so the arms are comparable on the same openings. `IN_FLIGHT` stayed at 8
although the script's own comment argues 16 fills the host: 16 searches shallower and would
break that comparability for about A$1. Cold start took 2.5 min. **`DO_SHUTDOWN` in
`cloud_ncdraw.py` is a dead flag (never read by `main()`); the only backstop is the
host-internal `shutdown -h`, lowered to +360 min, which HALTS but does not DELETE.** Host
and delete command are recorded in memory `cloud-sprt-minus-live`.



### R1 CLOSED 2026-09-26 22:46 — H0, BOTH SIGNS

| arm | pairs | penta | score | Elo | LLR | verdict |
|---|---|---|---|---|---|---|
| plus  (`-DNC_DRAW_SIGN=1`)  | 46 | [1, 6, 37, 2, 0]  | 0.467 | -23 | -3.02 | H0 |
| minus (`-DNC_DRAW_SIGN=-1`) | 64 | [0, 12, 47, 4, 1] | 0.477 | -16 | -3.07 | H0 |

-16 and -23 are not distinguishable at these pair counts. **The NC_DRAW_GRADE axis is
closed in both directions — do not reopen either sign.** Host `rps-ncdraw` deleted, GCP
verified empty (0 instances, 0 disks), ~A$1 total. Log: `nnue_train/cloud/ncdraw_minus_result.log`.

**The pre-registered flat-zone gate FAILED as a predictor.** It separated the arms
cleanly (base 80/254 flat, plus 77, minus 44) and the Elo came out the same. A proxy can
be sensitive to the intervention and still be orthogonal to strength. It was registered
as a veto-only screen, which is why the cost of being wrong was A$1 rather than a shipped
regression — keep doing that. Second proxy to die this way after `sep`.

**The no-capture draw prize (22.6% of live games, +37 Elo for one draw in four) is still
open.** What is ruled out is reaching it through the eval. Remaining routes: the shipped
[[sticky-loop-lock]] (accruing, R3) and the tablebase (R4).

### R2 -- is the net's capture-clock discount justified?  **CLOSED 2026-09-26, FREE, no host spent**

**Verdict: the discount is CORRECT. Do not ablate the clock feature, do not widen
the top clock bounds. The ~A$3 retrain arm is CANCELLED.**

Reframed from "how big is the discount" (needs an assumption) to **"which eval
predicts the real game result better -- discounted, or clock-zeroed?"**, which is
answerable with zero games. Built 2,116 positions from 259 archived games (<=12 per
game, spread across that game's clock range, each tagged with the game's real
result), read `eval` twice per position -- once at the true clock, once with FEN
field 5 rewritten to 0. Code: `sf19_ports/live/clockjust/`.

The discount is large and super-linear (median `ev_real - ev_zero`): -28 cp at
clock 0-19, -96 at 100-119, **-493 at 180-199** -- by clock 190 it erases a rook.

AUC for the game result, bootstrapped over **games** (labels are per-game, so the
clustering matters), 2,000 resamples:

| cut | n | discounted | zeroed | gap, 95% CI |
|---|---|---|---|---|
| all | 2116 | 0.5662 | 0.5370 | **+0.029 [+0.014, +0.044]** |
| clock >= 60 | 356 | 0.4327 | 0.3608 | **+0.072 [+0.036, +0.113]** |
| clock >= 100 | 209 | 0.5412 | 0.4481 | **+0.089 [+0.015, +0.165]** |
| clock 100-160 | 131 | 0.5106 | 0.4807 | **+0.027 [+0.003, +0.062]** |

Every CI excludes zero. Confirmed on the clean draws-excluded statistic
(**WIN vs LOSS only**: 0.710 vs 0.699 overall, 0.694 vs 0.665 at clock >= 60).

**Two traps, both checked rather than assumed.** (1) AUC(WIN vs not) sits *below*
0.5 at high clock and that is **not** an inversion -- realised score still rises
with `ev_real` across quintiles (0.465/0.521/0.570/0.556/0.562) and top-half-eval
games score 0.600 vs 0.569. It is below 0.5 because "not WIN" pools 247 draws with
42 losses and **the eval ranks draws above wins** (LOSS-vs-not = 0.782): a big eval
at a high clock is a *fortress detector*. (2) The negative DRAW-AUC gap is the same
fact as the positive WIN gap, not a contradiction -- with losses rare the two are
near-complements (0.433 + 0.687 = 1.12).

**Do not quote the descriptive clock->score table as the result.** Mean game score
falls 0.839 (clock 0-19) -> 0.478 (180-199), but a game only *reaches* a high clock
if no capture happened, which is most of the way to the draw. That is selection.
The AUC comparison is the finding because both arms are measured on the *same*
positions.

**Corollary that redirects the whole conversion axis:** the net *already knows* a
big lead at a high clock is probably a draw. So the fix must change **what the
search does about it**, not what the eval says -- which is what the sticky lock
did, and which is consistent with the eval-side SPRT returning H0 at -23 Elo.

### R3 — Let the sticky lock accumulate evidence. **Passive, free, zero risk.**

Shipped without an SPRT on the user's instruction, and its first firing is verified in detail
(63 visits, zero drift, clock 23.5 s → 125.6 s). What is *not* known is its effect on score. Do
nothing but record: per game, whether the lock fired, at what clock, for how many plies, and the
result. After ~40 games that is a same-binary before/after comparison against the 103 loops already
parsed in `loopbreak/`. Free, and it is the only honest way to price a change that shipped untested.

**Do not touch the threshold** until that record exists.

---

### R4 — 5-piece tablebase, built not downloaded. **The big-ticket conversion item.**

**Free gate, measured 2026-09-26: a 5-piece table fires on 62.2% of the 254-position
near-draw band (158 positions, which also become its acceptance test); 82.3% at 6 pieces.
Only 36.0% of genuinely-drawn clock>=60 positions qualify, but 20.2% of those carry full
20-piece material -- early no-capture stretches, not fortresses. R4 is correctly scoped.**

`tablebase-6piece` already settled the shape: do **not** take the 72 GB WDL-only Discord table
(WDL-only reintroduces the exact clock blindness R1/R2 are about), build our own 5-piece with
distance-to-goal, ~A$4. This is the only item that attacks conversion with *exact* values instead of
a heuristic, and conversion is the 190 Elo target from `conversion-not-eval`.

Deliberately ranked below R1/R2 because it needs a reader in the engine and a probe path in search —
days, not an afternoon — and because R1 may recover part of the same loss for the cost of a rebuild.

---

### R4 CLOSED 2026-10-08 — THE PROBE BUYS NOTHING, FREE, no host spent

**Verdict: do not wire a <=5-piece probe into search, and do not build the 6-piece table on this
evidence. On every live decision the table can see, the engine already played a move that
preserved the game-theoretic result -- 0 throws in 3,728.**

The table itself is finished and correct; it is the *probe path* that is closed. All 60
signatures are converted to a seekable dense format (`tablebase/probe/`, 2.865 GiB, built in
1,140 s, 0 failures) and verified byte-for-byte against the solver's own stream on ~24M entries
with 0 mismatches. What the gate killed is the reason to spend days putting it inside search.

**1. The gate, on real play rather than a chosen band.** 29,157 chain-verified live decisions
recovered from `papernet-status.log` (`live_decisions.py`: apply the recorded reply, take our
next recorded move, and keep it only when the reconstruction matches the next rendering --
29,157 of 29,766 survive). 4,373 are at <=5 pieces; deduplicated, 3,728 are judgeable, and the
complete table leaves **0 uncovered**.

| played | clock dating | judged | winnable | threw the result |
|---|---|---|---|---|
| the engine's move | skeptical | 3,728 | 144 | **0 (0.00%)** |
| the engine's move | generous | 3,775 | 156 | 6 (0.16%) |
| the engine's move | exact only | 1,017 | 9 | **0 (0.00%)** |
| the worst legal move | skeptical | 3,728 | 144 | 3,232 (86.70%) |
| an arbitrary legal move | skeptical | 3,728 | 144 | 1,514 (40.61%) |

The 254-position band that R4 pre-registered as its acceptance test agrees: of the 158
positions a <=5-piece table covers, **158 of 158 preserved the result, 0 throws**.

**2. The null is falsified, so the clean run means something.** A gate that reports no throws is
worthless until it is shown it can report one. Replacing the engine's move with the worst legal
move throws the result in 86.70% of the *same* positions, and an arbitrary legal move throws
40.61%. These endgames are emphatically not trivially safe -- a coin-flip loses four games in
ten. The engine loses none.

**3. The first answer was wrong, and the 200-ply rule is why.** Before the capture clock was
modelled, the gate read 18 throws, 13 of 179 winnable (7.26%), including 5 DRAW->LOSS -- a
material-looking signal. It was phantom. `tb_solve` stores a true **distance-to-zeroing**
(`tb_solve.c:467` gives a capture into a lost child dtz 1, and the sweep propagates only through
non-zeroing moves), so a stored WIN is only reachable in a real game when `dtz <= 200 - clock`.
Clamping wins that cannot outrun the draw rule removed 13 of the 18, every DRAW->LOSS among
them. **A WDL-only table would have had no way to apply this clamp** -- which is the same
blindness `tablebase-6piece` rejected the 72 GB Discord table for, arriving from the other side.

**4. The 6 survivors are a ±1 dating artifact, not a residue of doubt.** The status log does not
print the clock, so it is reconstructed by propagating `do_move` through the recovered chain,
which leaves one ambiguity: which ply of a logged pair made the capture. The six cases that only
the generous dating produces have dtz 3, 3, 3, 3, 7, 17 against a skeptical budget of exactly
2, 2, 2, 2, 6, 16 and a generous budget of exactly 3, 3, 3, 3, 7, 17. Every one sits on the
knife edge -- winnable if and only if the clock is dated one ply earlier -- at clocks of 183-198,
i.e. 2 to 17 plies from being declared drawn. All six have engine eval exactly 0.

**5. Two independent checks that the reconstruction is sound.** The longest no-capture stretch
across 478 live games is **exactly 200 plies with none over**, which is the referee enforcing
`CAPTURE_CLOCK` and the clock reconstruction agreeing with it. And the live-count formula
derived for the dense index predicted `001_001`'s 12,642 entries exactly.

**What this does and does not settle.** It settles the probe: the 2.865 GiB we own would not
have changed one achievable result, so there is no Elo in reading it, and the days of engine
work and the 158-position acceptance test are not worth starting. It does **not** settle 6
pieces by measurement -- scoring a 6-piece decision needs a >=6-piece oracle, which is the thing
in question. What it removes is the *argument* for building one: the case for 6 was that <=5
fires on 62.2% of the band and 6 would reach 82.3%, i.e. more of the same kind of position. At
<=5 that kind of position turns out to be one the engine already plays perfectly, and
`tb5-live-verdict` had already found those positions were dead draws on arrival. A 6-piece
build (possible but out-of-core, ~A$300-600 and multi-day, per the correction in
`tablebase-6piece`) would have to be justified by something other than extending this coverage.

Conversion remains the 190 Elo target from `conversion-not-eval`, and this is the **third**
proxy to die after `draw-horizon-blindness` and `zero-game-proxy`: exact values agree with the
engine exactly where we can check them, so the loss is not in the positions a tablebase can see.

### R5 — Make time management measurable. **Opens a blocked axis; touches the frozen referee.**

`bot eval`/`bot play` accept only `--sims`/`--move-ms`: no clock, no increment, no forfeit. So the
seat's time manager is bypassed in every SPRT we have ever run, six live games have been lost on
time, and the sticky lock just produced a **102-second** clock swing that no harness here can score.
The work is a clock-aware match mode in the referee. Precedent exists: `--openings` was added to the
same frozen component under an opt-in flag with a byte-identical protocol digest.

Ranked last of the live items because it buys no Elo directly — it buys the ability to measure a
class of change — and because the referee is the component every verdict depends on.

### R5 CLOSED 2026-09-27 — UNMEASURABLE BY BOTH ROUTES, FREE, no host spent

**Verdict: do not add a clock mode to the referee, and do not build the regime-vs-outcome join.**
Two routes, both dead:

1. **A clock-aware match mode would cost more than it buys.** `sprt_pool.FROZEN.py` pools pairs
   across hosts for one earned reason, stated in its own docstring: every game is NODE-limited, so a
   pair's outcome cannot depend on which box played it. `nps` depends on the cache hierarchy, so
   clock-limited games from different hosts **must never be pooled** — and pooling is what makes a
   verdict cost ~A$1. A clock mode buys measurability of one axis by making every verdict on every
   other axis several times dearer.

2. **The observational route is collinear, not merely confounded.** `sf19_ports/live/r5_confound.py`
   cross-tabulates each seat's regime against the engine it logged in its own `START` line:

   | net / threads | OV=150 games | OV=500 games |
   |---|---|---|
   | `sf2_wdl/best.nnue` thr=6 | 11 | **81** |
   | `sf2_ex_50k/best.nnue` thr=6 | 91 | 0 |
   | `sploppy_engine/example.nnue` thr=6/2/4 | 108 | 0 |

   OV=500 is **81 of 81 games on one net**, all 09-26..09-27; OV=150 is 199 games over three nets,
   all 09-17..09-25, **zero temporal overlap**. `sf2_wdl` is the WDL-labels net that shipped 09-25 at
   +33 Elo, so the comparison would largely recover that net change and book it to the clock
   constant. The only overlapping stratum is 11 games against 81 — 95% interval near **±270 Elo** —
   and it straddles the sticky lock shipping 09-26 as well.

**What this line of work did produce, and it is kept:** `clock_audit2.py`, which fixes a real
labelling bug in `clock_audit.py`. That script assigned a move's regime by first-match-wins, but
`budget()` is insensitive to the overhead on capped, panicking and `safe`-clamped rows — **21.8% of
all rows** — so every one was booked OV=150. Corrected (regime per SEAT, our colour per GAME), the
fixed-point result **survives and tightens**: p05 at OV=150 is 3,009 ms against a predicted 3,000.
But **"OV=500 has 0.0% budget maxed" was true by construction** and is really 17.1%, slightly more
than OV=150's 15.4% — which independently confirms that the constant bought clock safety and **not**
less thinking. See memory `live-clock-overhead`.

---

### Parked, with the reason

- **`live+head` (MSE 0.1552, ranking 0.9800).** The lowest offline MSE anywhere in `#6.1` and it
  survives a widened grid — but it is **post-hoc on used data**, it is MSE and not Elo, and serving
  it needs format 10 + the Rust reader that `#6.1` concluded should not be built. Needs a fresh
  held-out confirmation before it is worth that much plumbing. User's call, as recorded.
- **Anything on the WDL share axis.** Both ends H0. Closed.
- **`tail_weight`.** The calibration table is bad at every setting.

---

## DONE

**#1 — Outcome (WDL) labels.** Train on the game result beside the teacher's search score.
**+33 Elo vs live, +23 vs the step-matched control** (SPRT, 500 ms, H1). **Shipped 2026-09-25.**
The label gap replicates on a second recipe and seed in the zero-game proxy. See
`WDL_MORNING_REPORT.md`.

**The mechanism was then confirmed from the archived games** (`WDL_CALIBRATION_RESULT.md`,
`sf19_ports/tools/wdl_calibration.py`) — free, nothing played. In clock draws the WDL net makes a
hard false claim (≥ +200) about **half as often** as the step-matched control, paired inside the
same games: **12 of 72 vs 26 of 72**, and it drops the claim with 107 of its own moves left against
the control's 135. Against the control it is also closer to zero in 70–73% of the same drawn games.
It converts a sustained +400 at **95.4% vs 78.0%**, while *reaching* +400 more often — so it is not
timidity. The gain is in the tail of overconfident claims, not in a uniform shift toward 0.
Caveat recorded there: the last several moves of every clock draw are a hard 0 for both seats (the
clock enters the search horizon), so they carry no calibration signal at all.

---

## #2 — Untouchable trades — **FULLY ANSWERED AND CLOSED: the net knows, and it is a SYMPTOM**

**Why it is first: it is the biggest known defect and the probe is free.**

Won endgames get drawn — worth roughly **190 Elo** — and the cause is measured. It is not the eval
being wrong about endgames and not a missing endgame plan: it is **trade selection**, fifty plies
earlier. A piece can only be taken by the one type that beats it, so the moment your last rock dies,
every enemy scissors is untouchable to you forever. At matched material in the 6–7 piece band that
**triples the draw rate** (66.5% vs 20.6%).

The open question, from `untouchable-trades`: *does the net already know, and search overrides it,
or is the net blind?* Those need opposite fixes — one is a search problem, the other is a
feature/label problem — and picking wrong wastes a cloud week.

**The experiment plays no games.** Construct position pairs identical except for the untouchable
property, run them through `export.integer_eval`, read the score gap. Same machinery as
`night/proxy_rank_all.py`. Cost: an afternoon, A$0.

- Net knows (scores the untouchable side clearly worse) → the fix is in search/trade ordering.
- Net is blind (no gap) → add it as a feature or a label, which is idea #1's shape again, and #1 worked.

**RESULT, 2026-09-25 (`nnue_train/night/untouchable_probe.py`, no games, no cloud):**
the relation is worth **−854 cp, 16× the noise floor**, CI nowhere near zero. The control that
makes it readable — same 1→0 count transition but with no prey on the board — comes out
**+0.28, i.e. slightly good**, exactly as it should: a rock with nothing to capture is dead weight.
So the net is reacting to the *relation*, not to a count bucket.

**This CLOSES "add untouchability as a feature or a label" — no headroom there.**
It also undercuts the framing that produced this idea: if the eval hates these trades by a whole
piece and the search is deep, the engine should already refuse them, yet the games still draw.
**Next, and still free:** in `sf19_ports/data/conversion_archive/`, find the ply where a side loses
its last piece of a type with the prey on the board and read that seat's own `score=` either side of
it. A sharp drop means search walked into something its eval hated (a real target). A score already
near zero means the untouchable state is a *symptom* of a game that was drawn anyway — and the
census's own caveat ("observational, not causal") was the right reading. Full write-up:
`UNTOUCHABLE_PROBE_RESULT.md`.

**SECOND RESULT, same day, also free (`sf19_ports/tools/untouchable_score_trace.py`):** the score
trace the block above asked for has now been run, joining seat logs to referee streams on the move
(240 games, 240 replayed cleanly, 480 seat segments). **It is a SYMPTOM.** In drawn games the
victim's own score is flat near zero for SIXTY plies before the transition (+16 / +18 / +20 / +25 /
+23) and drifts UP after it (+39 / +46 / +67) -- no cliff, no bleed, median drop 17 cp. The last
refuge (drawn games where the victim was genuinely winning) is empty and inverted: at peak >= +300
cp, n=10, the score goes 401 -> **516**, and 0 of 10 dropped by more than 200 cp.

**So trade selection is CLOSED too, and idea #2 is finished.** Both branches are dead: the net
already knows (no feature/label headroom) and search is not walking into it (no search headroom).
The -854 cp is a counterfactual price -- real games only reach the relation once the position is
already sterile.

**What it reframes:** every draw here ended on the capture clock, and the engine still claimed
+516 cp *after* the transition, yet across all seats in all drawn games the score at the FINAL move
is median +0 cp. The belief does not break at the trade; it decays late. That is a horizon /
draw-calibration defect -- the axis outcome labels attack, which is the one axis that has produced
a confirmed gain. Full write-up: `UNTOUCHABLE_TRACE_RESULT.md`.

Either answer is worth more than another strength A/B, because it converts a 190 Elo target from
"we know it is there" into "we know where to put the fix".

---

## #3 — Make every future test cheaper — **BUILT, VERIFIED, AND READY TO USE**

**Why: testing is ~90% of the project's time and money, so a multiplier here beats a one-off gain.**

In the WDL SPRT, about **60% of pairs were pure draws**. A drawn pair moves the LLR by almost
nothing, so most of the money bought no information. `--opening-plies 24` already fixed the worst of
it (at 8 plies nearly everything drew and the test was powerless — `wide-arm-openings`), but 24 is a
blunt instrument: it makes games decisive by making them random.

Better: **select openings by measured decisive rate.** Play a cheap sweep once, keep the openings
that produce decisive games between near-equal engines, and reuse that book for every future SPRT.
Pairs to a verdict should fall materially; the same A$10 run then answers a question it could not
answer before.

Do this before #4, because #4 is several SPRTs and they would all get cheaper.

**RESULT, 2026-09-25 (`sf19_ports/tools/opening_power.py` + `opening_filter.py`, no games):**

**The objective above is wrong.** The SPRT's unit is the pair, and a pair scoring exactly 1.0 moves
the LLR by ~nothing — which happens when *both games draw* **or when the same colour wins both**,
i.e. the opening decided it. Selecting for raw decisive rate buys lopsided openings that are
decisive and useless.

Re-scoring every pair we have ever played: `--opening-plies 24` cut the both-drawn waste
(30.5% → 22.4%) but nearly tripled the colour-decided waste (18.6% → **45.8%**), and the
**informative fraction fell from 50.9% to 31.8%**. Nearly half the WDL SPRT was settled before
either net moved.

**The fix is a filter, not a screen.** Every opening occurs exactly once, so ranking openings by
playing them costs more than it saves. But evaluating the position after the opening costs one
forward pass: mean |eval| is **0.458** when the opening decided the pair and **0.089** when it did
not. Keeping only `|eval| <= 0.10` takes the informative fraction 31.8% → **49.0%** =
**1.54× fewer pairs to a verdict**, bootstrap CI [1.34×, 1.77×], and both held-out
directions independently pick the same cut (1.65× and 1.41× out of sample).

**Blocked on a decision, not on work:** it needs an opt-in `--openings` flag in the Rust referee —
the one component that produces every verdict and is deliberately frozen. Plan, parity check and
honest limits in `OPENING_FILTER_RESULT.md`.

**Byproduct:** `cloud/sprt_power.py`'s only guessed parameter is now measured — pairing cuts pair
variance to 0.263 of unpaired at 24 plies (saves 73.7%).

**BUILT, 2026-09-25 evening. No longer blocked — the decision was given and the work is done.**

Two opt-in flags on the referee, plus a builder:

| | |
|---|---|
| `bot eval --dump-openings N` | writes N openings from the existing `Opening::random` as token lines on stdout and exits without playing. Same generator, same seeded stream, so these are exactly the openings an eval with that `--seed`/`--opening-plies` would have played. |
| `bot eval --openings <file>` | plays that book instead of generating. One opening per line, `#` comments and blanks skipped, a short book cycled. |
| `sf19_ports/tools/opening_book.py` | dump -> replay -> evaluate -> keep `\|eval\| <= 0.10` -> write the book. |

`Opening::from_tokens` / `Opening::read_book` (`match/src/game.rs`) are the exact inverse of
`Opening::tokens`, and every ply is checked legal and checked to leave the game ongoing, so a
malformed or stale book fails with a `file:line` before a single game starts.

**Verified, not assumed:**
- **Round trip exact on 6/6 pairs.** Dumped at seed 7, played back through `--openings`: the
  openings the referee actually played are identical, pair for pair, to the ones it generates
  without the flag.
- **The no-flag path is untouched.** 56/56 `match` tests pass, including
  `default_bounds_keep_the_protocol_byte_identical` — the frozen protocol digest did not move.
  `EvalConfig.openings_file` defaults to `None` and nothing was added to the protocol, because
  `openings_sha256` already digests the plies actually played: a booked run is already
  distinguishable from a random one and a journal cannot resume across the two.
- **Every failure mode is loud:** unparsable token, legal-looking but illegal ply, missing file and
  empty book all error out before play.

**The book:** `nnue_train/openings/balanced_24ply.txt` — 12,000 dumped, **5,603 kept (46.7%)**,
built in **3.5 s**. It independently reproduces step 2's measurement: dropped openings have mean
|eval| **0.4597** against the 0.458 measured from played games; kept openings **0.0427**. 5,603 is
comfortably above any pair cap in use, so the book never cycles in a real SPRT.

`cargo build --release` is 36 s from warm, not a reason to hesitate.

---

## #4 — Tune the axis that just worked — **CLOSED 2026-09-25: BOTH SPRTs say no**

The 0.5 WDL share was a guess and it won, so the neighbourhood is unexplored.

- share **0.25 / 0.75 / 1.0** — is 0.5 near an optimum or on a slope?
- **fine-tune from the live net** rather than re-deriving from `example.nnue` — likely the bigger
  win of the two, and it was never tried.

**RESULT, 2026-09-25 (`nnue_train/cloud/cloud_wdl_sweep.py`, ~A$3, host stopped):** four arms
trained with steps DERIVED so the teacher keeps exactly its baseline 4.00 passes. `sep` is
**perfectly monotone in share** (0.25 -> 6.073, 0.50 -> 6.313, 0.75 -> 6.524, 1.00 -> 6.882), so 0.5
is on a slope, not at an optimum.

**But `sep` is compromised on this particular axis** -- it divides by mean |eval| on drawn rows, and
WDL training pushes drawn rows toward zero, so raising the share raises `sep` nearly by
construction. The proxy's 3/3 record was earned on label PRESENCE and capacity, never on share
MAGNITUDE. `corr`, which is less aligned with the objective, disagrees: it peaks at **0.75** and
dips at 1.00.

**Recommendation: one SPRT on share 0.75**, the only arm that beats live on both metrics while
topping `corr`. **The fine-tune-from-live arm is a NULL** (6.3324 vs live 6.3132) -- it was flagged
here as "likely the bigger win of the two" and it was not. Write-up:
`WDL_SHARE_SWEEP_RESULT.md`.

**THE SPRT RAN AND RETURNED H0.** `share075_vs_live_500ms` (rps-w1, 500 ms/move, 14 pairs in
flight, booked openings, `nnue_train/cloud/share_cloud.py`):

    score 0.486 = -9.7 Elo, 126 pairs, penta [2, 28, 74, 19, 3], LLR -2.969 through -2.94
    verdict H0: NOT STRONGER than live (+0 Elo accepted over +20, error <= 5%)
    decided in 102.6 min at 74 pairs/hr

**AND THE ENDPOINT RAN TOO.** `share100_vs_live_500ms` (exploratory, same host and settings; it
ships nothing and was only run because arm A left budget):

    score 0.494 = -4.2 Elo, 167 pairs, penta [3, 32, 101, 28, 3], LLR -3.014 through -2.94
    verdict H0: NOT STRONGER than live
    decided in 145.2 min at 69 pairs/hr

So **both** ends of the knob are H0, and neither is monotonically bad (-9.7 at 0.75, -4.2 at 1.00)
-- they are simply not better. `sep`'s perfect monotonicity in share predicted the opposite and was
wrong, exactly as the "compromised on this axis" caveat above warned. The share axis is CLOSED.
Independently, `wdl_calibration.py` shows share 0.75 is no better *calibrated* than 0.5 either
(43-45%, `|score|` identical), having played no games -- see #6 for why a single scalar output makes
this knob a dead end, and what replaces it. rps-w1 and its disk were deleted 2026-09-25 13:50 UTC;
all journals and seat logs are in `sf19_ports/data/w1_match_archive/`.

So **`corr` did not predict Elo either.** `sep` was already known to be circular on this axis; the
reading here is that `corr`'s peak at 0.75 was not a strength peak. Share 0.50 stays live and the
**share axis is closed** -- do not train more arms along it. A second, exploratory arm at share 1.00
was pre-registered in the same file before either test played a game, and its result is reported
whatever it is; it decides nothing.

This is the **third** time a cheap proxy has failed to predict a real SPRT on this project (the
8-sim screen sign flip, `draw|pred|`, and now `corr`). The protocol below survives, with its claim
narrowed:

Protocol, now that both tools are calibrated: train the arms, rank them in `proxy_rank_all.py`
(2 minutes, free), then spend **one** SPRT on the single best. The proxy has ranked correctly 3/3
**on label presence and on capacity** -- it has now failed on share magnitude. It vetoes; it does
not predict.

---

**MEASURED PAYOFF, 2026-09-25** (`book_payoff.py`, 182 booked pairs vs 201 unbooked, same
engine and time control; the recomputed pentanomials reproduce seq_eval's logged ones exactly,
which is what validates the seat mapping):

| | no book | booked |
|---|---|---|
| opening-decided (wasted) | 45.8% | **19.2%** |
| both-drawn (wasted) | 22.4% | **39.0%** |
| **informative** | 31.8% | **41.8%** |

**1.31x, 95% CI [1.01x, 1.61x]** — a real speedup, but below the 1.54x [1.34, 1.77] this idea
promised. The filter did its job on the part it controls (opening-decided pairs fell 58%, z = 5.8);
the *extrapolation* was optimistic, because it assumed a de-lopsided pair becomes informative when
in fact balanced positions draw more and many just land in "both drawn". Caveat in the other
direction: the booked runs used candidates closer in strength to the reference, which also raises
draws, so some of that rise is the nets. **Keep using the book** — 1.31x is free — but estimate
future speedups from played pairs, never from |eval| on openings alone.

---

## #5 — A second seed of the WDL recipe

Turns "this net is stronger" into "this recipe is stronger" with real games. The proxy already gives
this cheap support across two recipes/seeds, so this is confirmation rather than discovery — worth
doing when a cloud host is up for #4 anyway, not worth a dedicated run.

---

## #6 — Give the net somewhere to put "drawn"

`WDL_CALIBRATION_RESULT.md` located where the +33 Elo came from: the WDL net asserts a false win in
a drawn game about half as often. So the axis is **overconfidence in the tail**, not eval scale.
Three facts read out of the model code on 2026-09-25 say what the lever is and is not:

1. **The output is a single scalar** — `models/nnue/py/nnue/model.py:80`, `nn.Linear(2*hidden, 1)`.
   The outcome label was blended into that same scalar `target` by `share`. A scalar cannot say
   "I am ahead and it is still drawn": a sharp drawn position and a balanced one produce one number.
   That is exactly the residue the probe found.
   **This also explains why #4's share sweep returned H0 at −9.7 Elo, and why share 0.75 showed no
   better calibration than 0.5 (43–45%, `|score|` identical): with one output, more outcome signal
   can only drag the number toward 0, trading sharpness for calibration instead of buying both.**
   The fix is a **second output** (a draw-probability / WDL head, as Stockfish does), not a bigger
   share. This is the ranked-first candidate on this axis.

   **#6.1 — CONFIRMED 2026-09-26, before building it. `DRAW_PROBE_RESULT.md`,
   `nnue_train/tools/draw_probe.py`.** The premise above was an assumption; it is now measured. A
   second head would read the same frozen trunk the scalar reads, so probe that trunk directly.
   Frozen live net (`sf2_wdl.nnue`, format 8, H=512, one head), 430 games of the set it was trained
   on, label `outcome == 0`, **held out by game** (signed cp gives AUC 0.494 — no leakage).
   In the defect's own regime, held-out rows the net calls **>= +200 cp** of which 38.6% were
   drawn, separating drawn from won:

   | predictor | whole set | claim >= +200 | claim, >= 100 plies of clock left (3 seeds) |
   |---|---|---|---|
   | the net's own scalar | 0.8124 | 0.7396 | 0.7837  (0.7505 .. 0.8187) |
   | clock-only control | 0.7575 | 0.8407 | 0.7900  (0.7627 .. 0.8108) |
   | linear probe on the trunk | **0.8791** | **0.8966** | **0.8633**  (0.8583 .. 0.8725) |
   | head-shaped probe | 0.8760 | 0.8767 | 0.8639  (0.8526 .. 0.8724) |

   * **The trunk knows and the scalar cannot say it.** +0.08 AUC over the emitted scalar in the
     hardest cut, and the three resamples' spreads do not overlap.
   * **It is not the clock.** The six hand-made columns reach 0.790 on the same rows; the trunk
     probe beats them by ~+0.07 with 100+ plies still on the board. Note this also caps bullet 3:
     widening the clock buckets cannot reach what the probe is reading.
   * **The scalar is a worse draw predictor than six hand-made numbers** (0.740 vs 0.841 across the
     claim regime). That is the bottleneck stated as plainly as the data can state it.
   * **A bare linear draw readout is enough** — `linear` and `head-shaped` tie, and head-shaped
     overfits slightly. So the head costs only a second 2H i16 readout plus an i32 bias:
     `head_size` +`4*hidden+4` bytes, the **existing** SIMD dot product reused, the accumulator and
     its incremental update untouched, and a `convert --to 10` that saturates the shrink LUT at 1.0
     stays byte-verifiably identity — the property format 9 was held to.

   **#6.1 part 2 — the FIX tested, not just the premise. `nnue_train/tools/draw_shrink.py`.**
   Bolt a decisive readout onto the **frozen** live net (trunk and value head frozen, only the new
   readout trained, held out by game) and shrink: `eval = cp * (1 - P(draw)) ** gamma`. Of the
   8,454 held-out rows the live net calls >= +200 cp, 3,260 were drawn, 5,158 won and only **36
   lost** — the defect really is "drawn", not "losing".

   | gamma | drawn claims cut | won claims cut | selectivity | MSE vs outcome |
   |---|---|---|---|---|
   | 0 (live) | — | — | — | 0.1753 |
   | 0.25 | 39.7% | 8.7% | **4.58x** | 0.1406 |
   | 0.50 | 59.8% | 16.1% | 3.71x | **0.1284** |
   | 1.00 | 77.6% | 28.3% | 2.74x | 0.1310 |
   | 2.00 | 89.1% | 45.4% | 1.96x | 0.1578 |

   * **Selective, not timid.** 1.00x would be timidity — every claim shrunk alike, the failure mode
     the WDL work already ruled out. At gamma 0.5 the drawn claims fall 60% and the won claims 16%.
   * **Not a rescale.** Eval scale is a free knob, so the baseline got its best constant too:
     scale-only bottoms at 0.62 -> MSE 0.1628, while gamma 1.00 + constant 1.78 reaches **0.1186**,
     27% below the scale-only floor, at an interior optimum. Note the best constant *with* the head
     is **above 1**: once drawishness is divided out the net should speak MORE loudly about what it
     is right about.
   * **Ranking is untouched.** AUC of the eval picking the winner among decided rows is 0.9881 at
     gamma 0 and 0.9880 at gamma 1.00 — flat through the useful range.

   **Still not Elo.** All of this is offline calibration; the search may not convert it. But unlike
   `tail_weight`, whose table was bad at every setting, this trade is good — which is the
   difference between shelving an idea and building it. Order: (1) the head in the PyTorch model
   and trainer only, flag-gated, trained jointly (the frozen run is a LOWER bound); (2) re-measure
   calibration; (3) only then format 10 + the Rust reader + FORMAT9-grade fixtures; (4) SPRT.

   **#6.1 step 1 — DONE 2026-09-26.** The head is in `model.py` and `train.py`, flag-gated and
   default-off (`--decisive_head`, `--decisive_weight`). `attach_decisive()` lets a `.nnue` start
   gain it without changing what it evaluates: `DECISIVE_INIT_BIAS = 8.0` is p = 0.99966, a 0.2 cp
   shift at +600. `forward()` serves `value * sigmoid(logit)`. `export.py` REFUSES to export a
   model with a head rather than silently dropping it. 101 old tests pass, 9 new ones in
   `py/tests/test_decisive.py`. One real bug was caught in the writing: the trainer's `lean` batch
   path gathers only `("target","weight")`, so the anchor would have trained against a missing
   `outcome` column silently — now `columns` extends and a missing column raises.

   **#6.1 step 2 — the validation set turned out not to be real.** Two defects, both measured:
   every `_wdl` dataset has `split` all-zero (empty validation, `best 0.0`, `patience` dead), and
   `importer.assign_splits` holds out POSITIONS, not games — it splits by game then moves
   orbit-spanning rows back to TRAIN, so **55% of the games with validation rows also have training
   rows** on sf2. For a game-outcome target that is the label leaking. Fixed with
   `nnue_train/tools/make_game_split.py` -> `selfplay_sf2_ex_50k_wdl_gs` (symlinked columns, new
   `split.npy` only, `ids8` cache still bound). Splitting by game-orbit COMPONENTS was tried first
   and rejected on measurement: drawn games are ~2x longer, so they fuse into one 564-game
   component that is 79.1% drawn against 16.4% outside it, which gave a validation draw rate of
   0.189 against training's 0.600. Whole games, stratified, residual orbit overlap 12.9%. See
   [[wdl-split-leak]] and `DRAW_PROBE_RESULT.md`.

   **#6.1 step 2 — NEGATIVE so far: the control beats the head.** On the TEST split (30,169
   rows, 106 whole games) the served MSE is `dec_ctl` **0.1690** (no head), `dec_w025` 0.1719,
   `dec_w1` 0.1740, incumbent 0.2135. The head's *raw* eval is the tell: 0.2586 and 0.2355
   against the control's 0.1690, claiming ~60% more rows at +200 cp, with the shrink clawing it
   back — a net that learned to shout and then apologise. **Cause found:** `forward` returned the
   product, so the value MSE was computed on the SHRUNK value; the factorisation is
   underdetermined and the BCE anchor does not pin it down, so the value head decalibrated and
   let the logit cover for it. Fixed with `apply_shrink` / `Config.decisive_couple`, default
   decoupled. Two things stay true: the head DOES carry signal (draw AUC 0.9065 vs its own
   scalar's 0.8328), and it is not worth the ranking it cost (decided-row AUC 0.9815 live ->
   0.9729 control -> 0.9639 served). Also note the control moved claim-regime draw AUC 0.6598 ->
   **0.8437** with no head at all, so much of what the frozen probe credited to "the scalar
   cannot say it" is really "the incumbent is undertrained on WDL". Step 2b (`dec_dw1`,
   `dec_dw4`, decoupled) is running, and `draw_calib.py` now fits the serving rule
   `cp * (1-P(draw))**gamma * c` on VALIDATION and scores it on TEST — gamma and c were both
   pinned to 1, which is not even the point the frozen probe won at (gamma 1.00, c **1.78**).
   **If the decoupled arms do not clear 0.1690 under the best fitted rule, #6.1 does not survive
   joint training and format 10 should not be built.** Caveat on all of it: `lr 3e-4` overfits
   this fine-tune instantly — every arm, control included, selected epoch 0.

   **#6.1 CLOSED 2026-09-26 — do NOT build format 10.** The deciding test was
   `tools/head_on_control.py`: freeze a value net, train only the readout, fit the serving
   rule `cp * (1-P(draw))**gamma * c` on VALIDATION, score on TEST, whole games held out.
   Against a rule written down BEFORE the run, two of four conditions failed on the retrained
   control: the head bought **+1.3%** where 5% was required (0.1621 vs the 0.1641 rescale
   floor), and its claim-regime draw AUC **0.7722 is WORSE than the scalar's 0.8437** — the
   net already says it. The instrument is trustworthy because the same procedure on the stale
   incumbent reproduces `draw_shrink.py`'s headline almost exactly (**+26.8%** vs the
   published 27%, head AUC 0.9248 vs scalar 0.6598). **Mechanism:** continued WDL training
   does not add draw knowledge, it MOVES it into the scalar — scalar draw AUC 0.6598 -> 0.8437
   while the probe-able residual falls 0.9248 -> 0.7722. The frozen probe measured the gap
   that 500 free steps of ordinary training closes. Joint training never beat the head-free
   control either: best arm `dec_dw1` fitted 0.1724 vs control **0.1641**, at any anchor
   weight, coupled or decoupled. (Decoupling did work — raw MSE 0.2586 -> 0.1840 — it just
   did not matter.) **Recorded but NOT acted on:** live+head reaches 0.1552, the lowest MSE
   anywhere here, with better ranking too (0.9800 vs 0.9729), and it survives a widened grid.
   That is a post-hoc finding on used data and it is offline MSE, not Elo — the user decides.
   All new code is flag-gated and defaults off; 112 tests pass and a head-free model is
   bit-identical under both settings. See `DRAW_PROBE_RESULT.md`.
2. **The loss does go slack in the tail -- but NOT where the defect lives. I overstated this on
   2026-09-25 and the measurement corrects it.** `train.py:100-103` is
   `(raw.tanh() - target)^2 + 0.02 * smooth_l1(raw, atanh(target))`. I claimed the net is "punished
   less for a +400 in a drawn game than for a +40". Measured absolute gradient on a drawn row:

   | claim | +40 | +200 | +400 | +800 | +1600 | +3200 |
   |---|---|---|---|---|---|---|
   | abs gradient | 0.199 | **0.737** | 0.660 | 0.156 | 0.023 | 0.020 |
   | gradient / error | 2.00 | 1.60 | 0.87 | 0.16 | 0.02 | 0.02 |

   The push-back **peaks at +200 and is still strong at +400** -- exactly the range the probe found
   the false claims in. It only collapses above +800, bottoming at the Huber floor `raw_weight`.
   Only the *per-unit-of-error* column falls, and learning follows the absolute gradient, not that
   ratio. **So the loss is not the reason the net still overclaims at +200..+400.**

   The knob exists anyway: `Config.tail_weight` adds `relu(|raw| - |logit|)^2`, defaulting to
   **0.0** so every existing run reproduces byte-identically (verified). But its calibration table
   says not to reach for it -- to buy 1.9x more push-back at +400 you need `tail_weight` 0.3, which
   simultaneously puts a **20x** penalty on a *correct* confident win. The trade is bad at every
   weight, because the term only becomes cheap-and-effective above +1600 where the defect is not.
   **Do not spend a host on this without a reason the table does not show.**

3. **"Train on the clock" is DEAD — it is already there.** `features.py` gives every position 16
   elapsed-clock and 16 remaining-clock buckets, bounds
   `[0,1,2,3,4,6,8,12,16,24,32,48,64,96,128,160]`. Fine resolution near zero (which is why the last
   moves of every clock draw are a hard 0), coarse — three buckets — across the 120–200 plies
   remaining where the false claims actually live. Widening those top bounds is a small, honest
   third option, but the net is not blind here and this is not the main defect.

Rank these against #2's finding that conversion is a planning problem before spending a host: a
tail-calibration fix and a planning fix are not the same repair, and the probe says labels bought
part of the gap and left the rest.

---

## Closed — do not re-run

- **Capacity (hidden 768 vs 512).** SPRT returned H0 at **−46 Elo**. The axis is closed.
- **`cont512` vs live.** It was a cheap-looking lead, but the all-nets proxy sweep puts it clearly
  below the now-live `sf2_wdl`. The audit answered it for free; no match needed.
- **Replaying arms the 8-sim screen rejected.** Swept all 24 nets on 2026-09-25: nothing on disk
  outranks what is live. The bad screen did not bury a winner.
- **Null move ON.** Lost to OFF; it searched deeper and played worse (zugzwang — no legal move
  loses).
- **Clock damping in the eval.** Withdrawn; the defect is planning, not eval.
- **Mutual untouchability** as a signal (gap +0.013, nothing), and comparing final positions of wins
  vs draws (invalid — a won game ends on the goal square by construction).

---

## Standing rules these ideas inherit

- A cheap screen may **veto** a catastrophically bad net. It may never **predict**. An 8-sim screen
  once called a +33 Elo net −15.
- The proxy **ranks**; the SPRT **decides**; a human reads the verdict before anything ships.
- Never extend a match after seeing its score.
- Always run the step-matched control — without `sf2_pass6` the WDL result could not have been
  attributed at all.
