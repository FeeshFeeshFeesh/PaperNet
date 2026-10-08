"""Idea #3, step 3: BUILD the opening book, now that the referee accepts one.

WHY.  `opening_power.py` (step 1) found where the WDL SPRT's money went: a pair whose two games
are both drawn, or whose two games are won by the SAME COLOUR, scores exactly 1.0 for the candidate
and moves the LLR by ~nothing.  At 24 plies and 500 ms that was 22.4% + 45.8% = 68.2% of all pairs.
Only 31.8% said anything about which engine is better.

`opening_filter.py` (step 2) showed the waste is PREDICTABLE for free: mean |eval| after the opening
is 0.458 when the opening decided the pair and 0.089 when it did not.  Keeping only |eval| <= 0.10
takes the informative fraction 31.8% -> 49.0%, i.e. 1.54x fewer pairs to a verdict, bootstrap CI
[1.34x, 1.77x], with both held-out directions independently picking the same cut.

THIS SCRIPT is step 3 and it plays no games either:

    1. ask the referee for openings from its own generator  (`bot eval --dump-openings`)
    2. replay each one and evaluate the position it reaches  (one forward pass)
    3. keep the ones with |eval| <= CUT
    4. write them as a book the referee reads with `--openings <file>`

WHAT THIS DOES NOT DO.  It does not change which net is better.  Both arms of an SPRT play the same
book, so the comparison is untouched; only the number of pairs needed to see the difference falls.
The honest cost is that the test now measures the engines over BALANCED positions specifically.  A
net whose edge lives in lopsided positions would be under-measured by this book, and that is a real
narrowing of the question, not a free lunch.  It is the right trade here because a pair the opening
already decided measures nothing at all.

PROVENANCE.  The header written into the book records the net, cut, seed and plies.  The referee's
protocol line carries `openings_sha256` over the plies it actually played, so a run that used a book
is already distinguishable from one that did not, and a journal cannot resume across the two.

No sys.argv: constants below, run straight from PyCharm.
Needs numpy -> run with nnue_train/.venv/bin/python.
"""
import datetime
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

# opening_filter guards its main(), so importing it only brings in the board primitives and
# puts the nnue package on sys.path.  replay_opening/to_format8 are reused verbatim so the book
# is filtered by exactly the eval the measurement was made with.
import opening_filter as F  # noqa: E402

BOT = os.path.join(ROOT, "nnue_train", "sploppy_repo", "target", "release", "bot")
NET = F.NET                        # runs/sf2_wdl/best.nnue -- the live net, as step 2 used
CLOCK = F.CLOCK                    # 200, the corpus constant

DUMP = 12000                       # openings to generate before filtering
SEED = 20260925                    # the book's own seed, independent of any SPRT's --seed
OPENING_PLIES = 24                 # matches the SPRT that was measured
CUT = 0.10                         # step 2's measured cut

OUT = os.path.join(ROOT, "nnue_train", "openings", "balanced_%dply.txt" % OPENING_PLIES)


def dump_openings(count):
    """The referee's OWN generator, so the book is drawn from the distribution it would play."""
    cmd = [BOT, "eval", "--dump-openings", str(count),
           "--seed", str(SEED), "--opening-plies", str(OPENING_PLIES)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [line.split() for line in out.splitlines() if line.strip()]


def main():
    import numpy as np
    from nnue import export

    if not os.path.exists(BOT):
        print("MISSING %s -- cargo build --release first" % BOT)
        return

    print("dumping %d openings at %d plies (seed %d) ..." % (DUMP, OPENING_PLIES, SEED))
    openings = dump_openings(DUMP)
    print("  got %d" % len(openings))

    rows, kept_tokens = [], []
    for tokens in openings:
        got = F.replay_opening(tokens)
        if got is None:                      # the referee cannot emit one of these; belt and braces
            continue
        board, mover, since = got
        rows.append((F.to_format8(board, mover), mover, since))
        kept_tokens.append(tokens)
    print("  %d replayed cleanly" % len(rows))
    if not rows:
        return

    net = export.read(NET)
    net["weights_padded"] = export.pad_rows(net)
    boards = np.array([r[0] for r in rows], dtype=np.uint8)
    since = np.array([r[2] for r in rows], dtype=np.int32)
    clock = np.full(len(rows), CLOCK, dtype=np.int32)
    raw = export.integer_eval(net, boards, since, clock)
    mover = np.array([r[1] for r in rows])
    ev = np.where(mover == 0, raw, -raw)      # side 0's frame, so the sign names a colour

    keep = np.abs(ev) <= CUT
    book, seen = [], set()
    for i, ok in enumerate(keep):
        if not ok:
            continue
        line = " ".join(kept_tokens[i])
        if line in seen:                      # 24 random plies never collide, but be sure
            continue
        seen.add(line)
        book.append(line)

    print("-----")
    print("mean |eval| over all %d: %.4f      kept |eval| <= %.2f: %d (%.1f%%)"
          % (len(ev), np.abs(ev).mean(), CUT, len(book), 100.0 * len(book) / len(ev)))
    print("kept mean |eval| %.4f   dropped mean |eval| %.4f"
          % (np.abs(ev[keep]).mean(), np.abs(ev[~keep]).mean() if (~keep).any() else float("nan")))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write("# balanced opening book -- built by sf19_ports/tools/opening_book.py\n")
        fh.write("# %s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        fh.write("# net %s\n" % os.path.relpath(NET, ROOT))
        fh.write("# %d plies, dumped %d at seed %d, kept |eval| <= %.2f -> %d openings\n"
                 % (OPENING_PLIES, DUMP, SEED, CUT, len(book)))
        fh.write("# play with:  bot eval ... --openings %s\n" % os.path.relpath(OUT, ROOT))
        for line in book:
            fh.write(line + "\n")
    print("-----")
    print("wrote %d openings to %s" % (len(book), os.path.relpath(OUT, ROOT)))
    print("A 1000-pair SPRT needs 1000; the referee cycles a shorter book, which would make pairs")
    print("share openings -- raise DUMP if the count above is under the pair cap you intend to use.")


if __name__ == "__main__":
    main()
