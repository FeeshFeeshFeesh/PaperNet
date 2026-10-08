# Idea #3 — make every future test cheaper — MEASURED, 2026-09-25

**Result: a free filter on the opening cuts pairs-to-verdict by 1.54x [1.34, 1.77].**
Tools: `sf19_ports/tools/opening_power.py`, `sf19_ports/tools/opening_filter.py`. No games, no cloud.

## The objective was wrong, and fixing it changes the answer

Idea #3 was written as "select openings by measured decisive rate". That is the wrong target, and
chasing it would have made things worse.

The SPRT's unit is the **pair**: two games, one opening, colours swapped, scored 0/0.5/1/1.5/2 for
the candidate. A pair scoring exactly **1.0** sits at the mode of both hypotheses, so its likelihood
ratio is ~1 and it moves the LLR by almost nothing. **There are two ways to score 1.0:**

- both games drawn — the waste everyone notices;
- **the same colour wins both games** — the *opening* decided it and both engines converted.

So "more decisive games" can be bought by using lopsided openings, which are decisive *and useless*.

## What the harness has actually been doing

Every pair of the WDL SPRT and of the archived deep runs, re-scored:

| | 8-ply, 60 s (570 pairs) | **24-ply, 500 ms — the WDL SPRT** (201 pairs) |
|---|---|---|
| decisive per game | 47.5% | 62.2% |
| both games drawn — wasted | 30.5% | 22.4% |
| **same colour won both — wasted** | **18.6%** | **45.8%** |
| **informative** | **50.9%** | **31.8%** |

**`--opening-plies 24` did not make games decisive by making them sharp. It made them decisive by
handing one side a won position.** Nearly half of the WDL SPRT's pairs were settled by the opening
before either net moved. Pairs-to-verdict is inversely proportional to the informative fraction, so
that setting is paying roughly 1.6x for its verdicts.

**And the premise behind it does not hold up.** `class_ab_match.py` justifies 24 plies with "at 8
plies only about 10% of games are decisive". 570 archived pairs at 8 plies are **47.5%** decisive.
Those runs are 60 s/move and the WDL SPRT was 500 ms, so this is not a flat contradiction — but the
10% figure was never re-measured at the budget the choice was actually applied to.

## The filter, and why it is free

Screening a book by *playing* it is hopeless: every opening occurs exactly once (570 distinct, zero
repeats), so ranking openings needs tens of games each and costs far more than it saves.

A **filter** needs no games. Replay the opening, evaluate the position with the net, discard the
opening if it is already lopsided. One forward pass per opening.

Tested on openings that have already been played out:

> mean |eval| after the opening — **opening-decided 0.4580, everything else 0.0893**
> when the opening decided it, the eval's **sign named the winning colour 89.1%** of the time

| cut on \|eval\| | kept | decided | both drawn | informative | speed |
|---|---|---|---|---|---|
| none | 100% | 45.8% | 22.4% | 31.8% | 1.00x |
| **0.10** | **49.8%** | **16.0%** | 35.0% | **49.0%** | **1.54x** |
| 0.20 | 64.2% | 24.8% | 33.3% | 41.9% | 1.31x |
| 0.30 | 70.6% | 29.6% | 31.0% | 39.4% | 1.24x |

Balanced openings are more drawish (22.4% -> 35.0%), but the colour-decided waste falls three times
as fast, so the informative fraction rises. Rejecting half the openings costs nothing: they are
generated, not scarce.

**The cut was not fitted to the data it is scored on.** Two independent streams, each used to pick
the cut and then scored on the other: both independently chose 0.10, giving **1.65x** and **1.41x**
out of sample. Pooled bootstrap over 201 pairs: **1.54x, 95% CI [1.34x, 1.77x]**.

At cut 0.10 the informative fraction (49.0%) lands back on what the 8-ply runs got naturally
(50.9%) — the filter recovers what 24 plies gave away, while keeping the opening variety that makes
deterministic engines play different games.

## Implementing it without putting a hand on the rules

The referee has no book flag; `match/src/eval.rs:217` calls `Opening::random` per pair. **Do not
re-implement opening generation in Python** — a subtly different legal-move generator would corrupt
every future verdict silently. Generate in the trusted Rust generator, subset in Python:

1. **Rust, opt-in:** `--dump-openings N` writes N openings from the existing `Opening::random` as
   token lines; `--openings <file>` plays a book instead of generating. `EvalConfig.openings_file`
   defaults to `None`, so with neither flag the referee is byte-identical to today.
   `Frame::parse_move` (`match/src/rpsi.rs:178`) already parses the tokens `Opening::tokens` writes,
   so no new parsing rules are needed.
2. **Python:** replay each dumped opening, evaluate, keep `|eval| <= 0.10`, write the book.
   `opening_filter.py` already does every step of this.
3. The journal's `openings_sha256` (`eval.rs:236`) already digests the openings, so a book run stays
   as auditable and reproducible as a seeded one — and **the same book across experiments removes
   opening variance between them too**, which a fresh seed never has.

**Two things to weigh before doing it.** The referee is the one component that produces every
verdict, and it is deliberately frozen (`sprt_pool.FROZEN.py`). The change is opt-in and inert
unless a flag is passed, but it still wants a parity check: one seeded run before and after, same
seed, identical journal digest. And `cargo build` on this Mac competes with the live bot.

## Honest limits

- **201 pairs**, from two runs of one engine family at one budget. The CI is the bootstrap's, and it
  does not cover "a different engine at a different time control".
- The filter is applied with `sf2_wdl`, which also played those games. It evaluates the position
  *before* either engine moves, so this is not outcome leakage, but a filter and a player sharing a
  net could in principle agree on which positions are dull.
- Filtering to balanced openings makes the test measure strength from balanced positions. That is
  arguably *more* relevant — the live bot always starts from the balanced initial position — but it
  is a change in what is being measured, not only in how fast.
