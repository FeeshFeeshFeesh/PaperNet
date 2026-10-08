"""IDEA #2: does the NET already know about untouchable trades, or is it blind?

THE QUESTION AND WHY IT DECIDES WHERE A ~190 ELO FIX GOES.
Won endgames get drawn.  The cause is measured and it is not the endgame: a piece can only ever be
removed by the one type that beats it, so the instant a side's count of type `u` reaches zero, every
enemy piece of type BEATS[u] is UNTOUCHABLE to it for the rest of the game.  At matched material in
the 6-7 piece band that roughly TRIPLES the draw rate (66.5% vs 20.6%, `untouchable-trades`).  That
is a trade-selection defect fifty plies before the endgame.

But knowing the defect does not say where the fix belongs, and the two answers need opposite work:

    net KNOWS (it scores the crippled side clearly worse) -> the eval is fine, SEARCH is trading
        into these positions anyway.  Fix belongs in search / trade ordering.
    net is BLIND (no reaction) -> the eval cannot see it, so search is not overriding anything.
        Fix belongs in the features or the labels -- which is idea #1's shape, and idea #1 worked.

Guessing wrong costs a cloud week, and this probe PLAYS NO GAMES.  It costs an afternoon and A$0.

THE DESIGN -- a matched single-piece mutation, so the untouchable property is the ONLY thing that
moves.  Take a real held-out position in the mover's frame (codes: 0 empty, 1-3 own R/P/S, 4-6
enemy).  Change exactly one own piece from type u to type v, leaving every square occupied by the
same side and the piece COUNT identical:

    TREATMENT  the mover holds exactly ONE piece of type u AND the enemy holds at least one of the
               type u captures, so the mutation zeroes u and those enemy pieces become untouchable
               forever.
    COUNT CTRL the mover holds TWO OR MORE of type u, so the same u->v mutation zeroes nothing and
               no untouchable is created.
    BOUNDARY CTRL -- the one that matters.  The mover again holds exactly ONE piece of type u, so
               the count still goes 1 -> 0, but the enemy holds NONE of the type u captures, so
               NOTHING becomes untouchable.

The boundary control exists because of a confound that would otherwise fake the whole result:
format 8's feature context is `min(r,2) + 3 min(p,2) + 9 min(s,2)` over the opponent's counts, so a
1 -> 0 transition CROSSES A CONTEXT BUCKET and a 3 -> 2 transition does not.  The count control
alone therefore compares "crossed a representational boundary" against "did not", and would show a
large gap even if the net knew nothing about untouchability.  The boundary control crosses the very
same bucket with the semantics removed:

    treatment - boundary control  =  the effect of the RELATION, with representation held fixed.
    boundary control - count control  =  what the bucket boundary is worth on its own.

Treatment and control share the same from-type, the same to-type and the same piece count, and both
start from positions with NO pre-existing untouchable.  So the difference between their eval shifts
is attributable to the property and not to "a piece changed type".

THE NOISE FLOOR, which is what makes the number readable.  `features.transform_board(b, k)` with
`k % 3` renames the piece types CYCLICALLY, and the docstring is explicit that such a board "is
played exactly like the original".  The true value is therefore unchanged, so whatever the eval does
under it is pure net noise.  Any treatment effect must clear that floor to mean anything.  (The net
was trained with `--symmetry_weight 0.2`, so the floor should be small -- which is what makes this
test sensitive rather than what makes it pass.)

Reads only.  No games, no training, no cloud.  No sys.argv: constants below, run from PyCharm.
"""
import os
import sys

import numpy as np

T = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.join(T, "sploppy_repo")
sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))

DATA = os.path.join(REPO, "runs", "nnue_data", "selfplay_sf2_ex_50k")
NET = os.path.join(REPO, "runs", "sf2_wdl", "best.nnue")      # the live net
SPLIT = 2                 # held out from every arm
POOL = 120000             # rows to scan for candidates
PER_CELL = 1500           # cap per (u,v) per group, keeps the 8 GB Mac comfortable
SEED = 20260925
BOOT = 2000               # bootstrap resamples for the CIs

# Own codes 1,2,3 = rock, paper, scissors; enemy 4,5,6 likewise.
# Rock beats scissors, paper beats rock, scissors beats paper, so own type u can capture only:
CAPTURES = {1: 6, 2: 4, 3: 5}
NAME = {1: "rock", 2: "paper", 3: "scissors"}


def load_pool():
    split = np.load(os.path.join(DATA, "split.npy"), mmap_mode="r")
    pool = np.flatnonzero(np.asarray(split) == SPLIT)
    rng = np.random.default_rng(SEED)
    idx = np.sort(rng.choice(pool, size=min(POOL, len(pool)), replace=False))
    b = np.asarray(np.load(os.path.join(DATA, "board.npy"), mmap_mode="r")[idx])
    s = np.asarray(np.load(os.path.join(DATA, "since_capture.npy"), mmap_mode="r")[idx])
    c = np.asarray(np.load(os.path.join(DATA, "capture_clock.npy"), mmap_mode="r")[idx])
    return b, s, c


def counts(board):
    return {code: (board == code).sum(axis=1) for code in (1, 2, 3, 4, 5, 6)}


def has_untouchable(cnt):
    """True where the MOVER already cannot touch something: it owns no u while the enemy holds
    a piece of the type u captures."""
    out = np.zeros(len(cnt[1]), dtype=bool)
    for u, prey in CAPTURES.items():
        out |= (cnt[u] == 0) & (cnt[prey] > 0)
    return out


def mutate(board, rows, u, v):
    """Change the FIRST own piece of type u into type v, on a copy. Squares and counts per side
    are untouched, so only the type composition moves."""
    b = board[rows].copy()
    sq = np.argmax(b == u, axis=1)
    b[np.arange(len(rows)), sq] = v
    return b


def ev(net, export, board, since, clock):
    return export.integer_eval(net, board, since, clock)


def boot_ci(x, rng):
    if len(x) == 0:
        return float("nan"), float("nan")
    draw = rng.integers(0, len(x), size=(BOOT, len(x)))
    means = x[draw].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    from nnue import export, features

    board, since, clock = load_pool()
    cnt = counts(board)
    clean = ~has_untouchable(cnt)          # no pre-existing untouchable, so the effect is isolated
    pieces = sum(cnt[c] for c in (1, 2, 3, 4, 5, 6))
    print("pool %d rows, %d (%.1f%%) with no pre-existing untouchable"
          % (len(board), clean.sum(), 100.0 * clean.mean()))

    net = export.read(NET)
    net["weights_padded"] = export.pad_rows(net)
    rng = np.random.default_rng(SEED)

    # ---- noise floor: a cyclic type renaming leaves the game identical, so this is pure net noise
    sample = np.flatnonzero(clean)[:4000]
    base = ev(net, export, board[sample], since[sample], clock[sample])
    floor = []
    for k in (1, 2):
        alt = features.transform_board(board[sample], k)
        floor.append(np.abs(ev(net, export, alt, since[sample], clock[sample]) - base))
    floor = np.concatenate(floor)
    print("noise floor (cyclic type renaming, true value UNCHANGED): mean |delta| %.5f  "
          "median %.5f  p95 %.5f  over %d" % (floor.mean(), np.median(floor), np.percentile(floor, 95), len(floor)))
    print("-----")

    print("%-22s %6s %9s %9s %-22s" % ("mutation", "n", "mean d", "median", "95% CI of the mean"))
    agg = {"treat": [], "bound": [], "count": []}
    for u in (1, 2, 3):
        for v in (1, 2, 3):
            if u == v:
                continue
            live = cnt[CAPTURES[u]] > 0        # the enemy actually holds what u captures
            dead = cnt[CAPTURES[u]] == 0       # nothing for u to capture, so zeroing u costs nothing
            have_v = cnt[v] > 0                # the target type already exists, matched across arms
            for label, pick, prey in (("treat", cnt[u] == 1, live),
                                      ("bound", cnt[u] == 1, dead),
                                      ("count", cnt[u] >= 2, live)):
                rows = np.flatnonzero(clean & prey & have_v & pick)
                if len(rows) == 0:
                    continue
                rows = rng.permutation(rows)[:PER_CELL]
                mb = mutate(board, rows, u, v)
                d = ev(net, export, mb, since[rows], clock[rows]) - \
                    ev(net, export, board[rows], since[rows], clock[rows])
                agg[label].append(d)
                lo, hi = boot_ci(d, rng)
                print("%-22s %6d %+9.5f %+9.5f  [%+.5f, %+.5f]"
                      % ("%s %s->%s" % (label, NAME[u][:4], NAME[v][:4]), len(d),
                         d.mean(), np.median(d), lo, hi))

    print("-----")
    tr = np.concatenate(agg["treat"])
    bd = np.concatenate(agg["bound"])
    ct = np.concatenate(agg["count"])
    for label, arr, note in (("TREATMENT  ", tr, "1->0, enemy HAS prey  -> untouchable created"),
                             ("BOUNDARY   ", bd, "1->0, enemy has NO prey -> same bucket, no relation"),
                             ("COUNT      ", ct, "n->n-1, nothing zeroed  -> no bucket change")):
        lo, hi = boot_ci(arr, rng)
        print("%s n %5d  mean %+0.5f  95%% CI [%+0.5f, %+0.5f]   %s"
              % (label, len(arr), arr.mean(), lo, hi, note))

    def gap(a, b):
        d = a.mean() - b.mean()
        bs = np.array([a[rng.integers(0, len(a), len(a))].mean() -
                       b[rng.integers(0, len(b), len(b))].mean() for _ in range(BOOT)])
        return d, np.percentile(bs, 2.5), np.percentile(bs, 97.5)

    print("-----")
    d, lo, hi = gap(tr, bd)
    print("EFFECT OF THE RELATION   (treatment - boundary)  %+0.5f  CI [%+0.5f, %+0.5f]  = %.1fx floor"
          % (d, lo, hi, abs(d) / floor.mean()))
    print("   ^ representation held fixed: this is the untouchable property itself.")
    d2, lo2, hi2 = gap(bd, ct)
    print("EFFECT OF THE BUCKET     (boundary - count)      %+0.5f  CI [%+0.5f, %+0.5f]  = %.1fx floor"
          % (d2, lo2, hi2, abs(d2) / floor.mean()))
    print("   ^ what merely crossing the min(count,2) context boundary is worth.")
    print("   in centipawns (x600): relation %+.0f cp, bucket %+.0f cp" % (d * 600, d2 * 600))
    print("-----")
    print("Eval is in tanh space (x600 = centipawns), mover's view, so a NEGATIVE effect means the")
    print("net scores the side that just lost its last piece of a type WORSE -- i.e. it reacts.")
    print("Reading: effect clearly negative and above the noise floor -> the net KNOWS, and the fix")
    print("belongs in SEARCH. Effect indistinguishable from the control -> the net is BLIND, and the")
    print("fix belongs in the features/labels.")


if __name__ == "__main__":
    main()
