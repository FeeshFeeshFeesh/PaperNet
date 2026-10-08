"""R2 step 1 -- sample live positions across the WHOLE clock range, with the
game's real result attached.

FLATTEN_RESULT.md section 5 measured that the net discounts the eval by the
capture clock long before any search horizon effect: -136 cp at clock 119-129,
where the draw is 81 plies away and depth >= plies_left in 0 of 44 positions.
That is the static eval alone.

A discount is only a defect if it is WRONG. So the question here is not "how big
is it" but "does the discounted eval predict the actual game result better or
worse than the clock-zeroed one". That needs positions at every clock, each
carrying the outcome of the game it came from -- which is what this builds.

Reuses flatten_positions.fen/undo unchanged (validated 254/254 by
validate_band.py) and live_boards.parse for the boards and the results.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.dirname(HERE)
sys.path.insert(0, LIVE)
sys.path.insert(0, os.path.join(LIVE, "flatten"))

import live_boards
import flatten_positions as fp

OUT = os.path.join(HERE, "positions_all.jsonl")

MAX_PER_GAME = 12      # spread across the game's clock RANGE, not its first 12
MIN_CLOCK = 0          # the whole range this time, low clock included

SCORE = {"WIN": 1.0, "DRAW": 0.5, "LOSS": 0.0}


def main():
    games = live_boards.parse()
    kept, n_games, no_result = [], 0, 0
    for gi, g in enumerate(games):
        if g["result"] not in SCORE:
            no_result += 1
            continue
        plies = g["plies"]
        cand = []
        for k in range(1, len(plies)):
            a, b = plies[k - 1], plies[k]
            if any(f not in a or f not in b for f in ("counter", "mine", "theirs")):
                continue
            if not b.get("our_move"):
                continue
            if a["mine"] != b["mine"] or a["theirs"] != b["theirs"]:
                continue                      # a capture happened: cannot undo
            if b["counter"] < MIN_CLOCK:
                continue
            r = fp.undo(b["rows"], b["our_move"])
            if r is None:
                continue
            cand.append({
                "game": gi,
                "ply": b["ply"] - 1,
                "clock": b["counter"] - 1,
                "mine": b["mine"],
                "theirs": b["theirs"],
                "ahead": b["mine"] - b["theirs"],
                "live_eval": b.get("eval"),
                "depth": b.get("depth"),
                "opponent": g["opponent"],
                "reason": g["reason"],
                "result": g["result"],
                "score": SCORE[g["result"]],
                "fen": fp.fen(r, b["me"] == "Blue", b["counter"] - 1, b["ply"] - 1),
            })
        if not cand:
            continue
        n_games += 1
        cand.sort(key=lambda r: r["clock"])
        if len(cand) <= MAX_PER_GAME:
            kept.extend(cand)
        else:
            idx = sorted({len(cand) * i // MAX_PER_GAME for i in range(MAX_PER_GAME)}
                         | {len(cand) - 1})
            kept.extend(cand[i] for i in idx)

    with open(OUT, "w") as f:
        for r in kept:
            f.write(json.dumps(r) + "\n")

    print("games parsed        : %d" % len(games))
    print("games with a result : %d  (skipped %d without one)"
          % (len(games) - no_result, no_result))
    print("games contributing  : %d" % n_games)
    print("positions kept      : %d -> %s" % (len(kept), OUT))
    if kept:
        cl = sorted(r["clock"] for r in kept)
        print("clock  min %d  p25 %d  median %d  p75 %d  max %d"
              % (cl[0], cl[len(cl) // 4], cl[len(cl) // 2], cl[3 * len(cl) // 4], cl[-1]))
        res = {}
        for r in kept:
            res[r["result"]] = res.get(r["result"], 0) + 1
        print("positions by game result: %s" % res)
        bands = {}
        for r in kept:
            b = min(r["clock"] // 20 * 20, 180)
            bands.setdefault(b, []).append(r["score"])
        print("clock band -> n, mean game score:")
        for b in sorted(bands):
            v = bands[b]
            print("   %3d-%3d  n=%4d  score %.3f" % (b, b + 19, len(v), sum(v) / len(v)))


if __name__ == "__main__":
    main()
