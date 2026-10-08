# seq_eval -- the SPRT harness, recovered from sourceless bytecode

`seq_eval` is the sequential head-to-head harness every cloud SPRT imports
(`sprt_h2h`). Its source **was** lost: the .pyc records its origin as
`work/seq_eval.py`,
a per-session scratchpad that was wiped when that session ended. The only
surviving copy of anything was a single .pyc on rps-w1's boot disk -- one
`gcloud compute instances delete` away from gone for good. That is why both
files now live in the repo.

**`seq_eval.py` is the canonical file. Import it, edit it, and compile it if
you need a .pyc.** The .pyc is kept only as provenance for the reconstruction.

## How the source was recovered, and why you can trust it (2026-09-25)

It was decompiled by hand from the 3.9 bytecode, then checked two ways that are
independent of each other:

1. **Byte-identical recompile.** Compiling `seq_eval.py` under the Mac's 3.9.6,
   with the original `co_filename`, yields a code object equal to the one in the
   .pyc in every field that is compared recursively through nested code objects:
   `co_code`, `co_consts`, `co_names`, `co_varnames`, `co_freevars`,
   `co_cellvars`, `co_flags`, `co_stacksize`, `co_firstlineno` and `co_lnotab`.
   Line numbers and docstrings included. There is no behavioural room left
   between the two.
2. **Replay of the archived verdicts.** Re-scoring rps-w1's four finished
   journals with the rebuilt module reproduces every logged field -- `pairs`,
   `penta`, `score`, `elo`, `llr`, `verdict` and the `words` sentence:

   | journal | pairs | penta | score | LLR | verdict |
   |---|---|---|---|---|---|
   | wdl_sf2wdl_vs_live_500ms  |  89 | [0, 10, 53, 25, 1] | 0.548 | +3.024 | H1 |
   | wdl_sf2wdl_vs_pass6_500ms | 112 | [0, 7, 84, 20, 1]  | 0.533 | +3.256 | H1 |
   | share075_vs_live_500ms    | 126 | [2, 28, 74, 19, 3] | 0.486 | -2.969 | H0 |
   | wide_768_512_500ms        |  34 | [2, 5, 27, 0, 0]   | 0.434 | -2.952 | H0 |

   4 matched, 0 mismatched.

## Python version

The .pyc is Python 3.9 bytecode (magic 3425), compiled by the Mac's
`/usr/bin/python3` (3.9.6). Ubuntu 24.04 ships 3.12, which refuses it with
`ImportError: bad magic number`. **The recovered .py has no version
constraint** -- it runs on 3.12 -- so a cloud host no longer needs a 3.9 to run
a launcher, as long as the launcher imports the .py and not the .pyc.
(`uv python install 3.9` still provides one in about 20 seconds if some other
sourceless artifact ever needs it.)

## What it does

Module constants:
    ELO0, ELO1 = 0.0, 20.0      ALPHA = BETA = 0.05
    MIN_PAIRS  = 16             CAP_PAIRS = 400        PSEUDO = 0.5
    SIMS, THREADS, NICE = 8, 2, 15
    POLL_S = 30                 STATUS_EVERY_S = 900   ATTEMPTS = 4
Callables: expected, paths, segment, segments, pair_scores, judge, words,
status, running, stop, reported, sprt_h2h.

Two constants near the top, `S` (where journals are written) and `T` (which
gives `REPO` and `BOT`), still hold the dead Mac scratchpad path and the local
tree. Every cloud launcher already overrides them after importing
(`seq_eval.S = ...`), which is why the stale defaults never mattered; keep
doing that rather than editing them, so the file stays byte-comparable to the
.pyc.

Two behaviours worth knowing before you change anything:

* **A pair is scored from `winner == game`**, not from a seat index -- the
  colours swap between the two games of a pair, so the candidate is `first` in
  game 0 and second in game 1. `book_payoff.py` derives the same mapping from
  the `start` events independently and agrees.
* **`judge` adds `PSEUDO = 0.5` to each of the five buckets** before taking the
  mean and variance. The pentanomial it *reports* is the raw count; the LLR is
  computed from the padded one, which is what keeps the variance finite at the
  first look.
