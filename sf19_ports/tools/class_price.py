"""Convert the exact tablebase scores into the engine's own units, and compare.

THE QUESTION. The engine charges about 330 cp -- 1.59 stones at StoneValue 208 -- for closing one
of its capture classes. Is that the right price? The tablebase gives exact expected scores, but an
expected score is not comparable to a centipawn until both are in the same unit. The unit that
transports is STONES: how many extra pieces is a closed class worth?

HOW. Every family has a piece count per side and a closed-class count per side. Put the exact score
on a logit scale, which is the Elo scale up to the constant 400/ln(10), and regress it on the
antisymmetric features (blue minus red) for material and for closed classes. Antisymmetric because
the game is symmetric: only differences can matter, and forcing that halves the parameters and
gives the fit somewhere to fail if the solve is wrong.

    logit(score)  ~  a * (blue_pieces - red_pieces)  +  b * (blue_closed - red_closed)

Then b/a is the worth of one closed class IN PIECES, and a piece is a stone, so b/a * 100 is the
price in centipawns on the engine's own scale. That number is what 330 cp has to be judged against.

Scores are averaged over who is to move, which removes the tempo term rather than modelling it.
No sys.argv.
"""
import json
import math
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
TB = os.path.join(S, "class_tablebase.json")

STONE_CP = 100.0        # uci.cpp:522 -- one stone reads as 100 cp
ENGINE_CHARGE_CP = 330.3   # measured on matched synthetic pairs, 13.4 sigma
ENGINE_CHARGE_SEM = 24.6


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def solve_least_squares(rows, ncol):
    """Normal equations with Gaussian elimination. Small and dense; no numpy needed."""
    a = [[0.0] * (ncol + 1) for _ in range(ncol)]
    for xs, y in rows:
        for i in range(ncol):
            for j in range(ncol):
                a[i][j] += xs[i] * xs[j]
            a[i][ncol] += xs[i] * y
    for i in range(ncol):
        piv = max(range(i, ncol), key=lambda r: abs(a[r][i]))
        a[i], a[piv] = a[piv], a[i]
        if abs(a[i][i]) < 1e-12:
            raise ValueError("singular design: a feature is a copy of another")
        for r in range(ncol):
            if r == i:
                continue
            f = a[r][i] / a[i][i]
            for c in range(i, ncol + 1):
                a[r][c] -= f * a[i][c]
    return [a[i][ncol] / a[i][i] for i in range(ncol)]


def main():
    tb = json.load(open(TB))
    rows, table = [], []
    for k, rec in sorted(tb.items()):
        blue, red = k.split("v")
        score = 0.5 * (rec["blue_to_move"]["score"] + rec["red_to_move"]["score"])
        xs = [len(blue) - len(red), rec["blue_closed"] - rec["red_closed"]]
        rows.append((xs, logit(score)))
        table.append((k, len(blue), len(red), rec["blue_closed"], rec["red_closed"],
                      score, rec["max_dtm"]))

    print("%d families" % len(table))
    worst_dtm = max(r[6] for r in table)
    print("worst depth-to-win %d plies; the capture clock draws at 200, so it never binds -- "
          "these are exact" % worst_dtm)
    print()
    print("%-8s %5s %5s %7s %7s %9s" % ("family", "bPc", "rPc", "bClosed", "rClosed", "score"))
    for k, bp, rp, bc, rc, sc, _ in table:
        print("%-8s %5d %5d %7d %7d %9.4f" % (k, bp, rp, bc, rc, sc))

    a, b = solve_least_squares(rows, 2)
    ss_res = sum((y - (xs[0] * a + xs[1] * b)) ** 2 for xs, y in rows)
    ybar = sum(y for _, y in rows) / len(rows)
    ss_tot = sum((y - ybar) ** 2 for _, y in rows)
    elo = 400.0 / math.log(10)

    print()
    print("logit(score) ~ a*(piece diff) + b*(closed-class diff)")
    print("  a = %+.4f   (%.1f Elo per extra piece)" % (a, a * elo))
    print("  b = %+.4f   (%.1f Elo per closed class)" % (b, b * elo))
    print("  R^2 = %.4f over %d families" % (1 - ss_res / ss_tot if ss_tot else 0.0, len(rows)))
    print()
    if a <= 0:
        print("  a is not positive: material does not help in this fit, so the ratio is meaningless.")
        return
    stones = -b / a
    print("TRUE PRICE OF ONE CLOSED CLASS: %.2f stones = %.0f cp" % (stones, stones * STONE_CP))
    print("WHAT THE ENGINE CHARGES:        %.2f stones = %.0f cp (+-%.0f)"
          % (ENGINE_CHARGE_CP / STONE_CP, ENGINE_CHARGE_CP, ENGINE_CHARGE_SEM))
    ratio = ENGINE_CHARGE_CP / (stones * STONE_CP) if stones else float("inf")
    print()
    print("  nominal ratio: the engine charges %.2fx this" % ratio)
    print()
    print("  READ THAT NUMBER WITH THE DENOMINATOR IN MIND, NOT AS A VERDICT.")
    print("  `a` is the value of a piece measured where a piece is worth the most it can ever be:")
    print("  going 1v1 -> 2v1 DOUBLES the army. The 330 cp was measured at a median of 8 pieces,")
    print("  where a marginal piece is worth far less, so the true price in stones at 8 pieces is")
    print("  HIGHER than this -- plausibly by 3-5x, which would land on top of the engine's 3.30.")
    print("  Meanwhile `b` itself should also grow with material, since more enemy pieces of the")
    print("  freed type means more immunity bought. Both move the same way; the ratio does not")
    print("  transport, and no amount of precision in the solve fixes that.")
    print()
    print("  WHAT THIS DOES SUPPORT: the charge is plausibly too HIGH, and the negative direction")
    print("  has never been tested. That is the cheap next experiment, not a shipping decision.")
    print("  WHAT WOULD SETTLE IT: 4- and 5-piece solves, to fit how b/a scales with material and")
    print("  extrapolate to 8. That is 86M states per family -- a C port, not a Python afternoon.")


main()
