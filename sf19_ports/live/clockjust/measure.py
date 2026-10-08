"""R2 steps 2-3 -- is the net's capture-clock discount JUSTIFIED?

For every sampled position, read the static net eval twice: once at the real
clock, once with the clock zeroed. The difference is the discount.

Then the actual test: which of the two numbers predicts the game's real result
better? AUC, clustered by game, bootstrapped over games.

  discounted eval wins  -> the discount is real information, the axis is CLOSED
                           for free (and IDEAS #6 bullet 3 closes with it)
  zeroed eval wins      -> the discount is destroying information -> retrain one
                           arm with the top clock bounds widened or ablated

No games are played. One engine process, Threads=1, `eval` only.
"""
import os
import re
import json
import random
import subprocess

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = _ROOT
ENGINE = os.path.join(ROOT, "sf19_ports/sf19nn/stockfish")   # plain build
NET = os.path.join(ROOT, "nnue_train/sploppy_repo/runs/sf2_wdl/best.nnue")
POS = os.path.join(HERE, "positions_all.jsonl")
OUT = os.path.join(HERE, "evals.jsonl")

NET_EVAL = re.compile(r"^Net evaluation (-?\d+)")
BOOTSTRAP = 2000
SEED = 20260926


def relabel(fen, clock):
    f = fen.split()
    f[4] = str(clock)
    return " ".join(f)


def read_evals(rows):
    script = ["uci", "setoption name Threads value 1",
              "setoption name Hash value 16",
              "setoption name EvalFile value " + NET]
    for r in rows:
        for clock in (r["clock"], 0):
            script += ["ucinewgame", "position fen " + relabel(r["fen"], clock), "eval"]
    script += ["ucinewgame", "position fen " + rows[0]["fen"], "go depth 1", "quit"]
    out = subprocess.run([ENGINE], input="\n".join(script) + "\n",
                         capture_output=True, text=True).stdout.splitlines()
    vals = [int(m.group(1)) for m in (NET_EVAL.match(l) for l in out) if m]
    if len(vals) != 2 * len(rows):
        raise SystemExit("!! %d evals for %d expected -- attribution unsafe"
                         % (len(vals), 2 * len(rows)))
    for i, r in enumerate(rows):
        r["ev_real"], r["ev_zero"] = vals[2 * i], vals[2 * i + 1]
    return rows


def auc(pairs):
    """pairs = [(score, label)]; label 1 = positive. Ties count a half."""
    pos = [s for s, y in pairs if y]
    neg = [s for s, y in pairs if not y]
    if not pos or not neg:
        return None
    neg = sorted(neg)
    import bisect
    tot = 0.0
    for s in pos:
        lo = bisect.bisect_left(neg, s)
        hi = bisect.bisect_right(neg, s)
        tot += lo + 0.5 * (hi - lo)
    return tot / (len(pos) * len(neg))


def boot_gap(rows, label, key_a="ev_real", key_b="ev_zero"):
    """bootstrap the AUC gap (a - b), resampling whole GAMES."""
    bygame = {}
    for r in rows:
        bygame.setdefault(r["game"], []).append(r)
    keys = list(bygame)
    rnd = random.Random(SEED)
    gaps = []
    for _ in range(BOOTSTRAP):
        samp = []
        for _ in keys:
            samp.extend(bygame[keys[rnd.randrange(len(keys))]])
        a = auc([(r[key_a], label(r)) for r in samp])
        b = auc([(r[key_b], label(r)) for r in samp])
        if a is not None and b is not None:
            gaps.append(a - b)
    gaps.sort()
    if not gaps:
        return None
    return gaps[int(0.025 * len(gaps))], gaps[len(gaps) // 2], gaps[int(0.975 * len(gaps))]


def table(rows, title):
    print("  %s  (n=%d, %d games)" % (title, len(rows), len({r["game"] for r in rows})))
    if len(rows) < 30:
        print("     too few rows")
        return
    won = lambda r: r["result"] == "WIN"
    drew = lambda r: r["result"] == "DRAW"
    for name, label in (("WIN vs not", won), ("DRAW vs not", drew)):
        a = auc([(r["ev_real"], label(r)) for r in rows])
        b = auc([(r["ev_zero"], label(r)) for r in rows])
        if a is None or b is None:
            continue
        # for DRAW the eval should point DOWN, so report the informative direction
        print("     %-12s AUC  discounted %.4f   zeroed %.4f   gap %+.4f"
              % (name, a, b, a - b))
    g = boot_gap(rows, won)
    if g:
        print("     bootstrap over games, WIN-AUC gap (discounted - zeroed):")
        print("        median %+.4f   95%% CI [%+.4f, %+.4f]   %s"
              % (g[1], g[0], g[2],
                 "DISCOUNT HELPS" if g[0] > 0 else
                 "DISCOUNT HURTS" if g[2] < 0 else "no difference"))
    print()


def main():
    rows = [json.loads(l) for l in open(POS)]
    rows = read_evals(rows)
    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    print("=== the discount itself (median ev_real - ev_zero by clock band)")
    bands = {}
    for r in rows:
        bands.setdefault(min(r["clock"] // 20 * 20, 180), []).append(r)
    print("  band      n   med ev_zero   med ev_real   med discount")
    for b in sorted(bands):
        v = bands[b]
        z = sorted(r["ev_zero"] for r in v)
        e = sorted(r["ev_real"] for r in v)
        d = sorted(r["ev_real"] - r["ev_zero"] for r in v)
        print("  %3d-%3d %4d %11d %13d %14d"
              % (b, b + 19, len(v), z[len(z) // 2], e[len(e) // 2], d[len(d) // 2]))
    print()

    print("=== THE TEST: which eval predicts the real result better?")
    table(rows, "ALL positions")
    table([r for r in rows if r["clock"] >= 60], "clock >= 60 (the discount bites)")
    table([r for r in rows if r["clock"] >= 100], "clock >= 100")
    table([r for r in rows if 100 <= r["clock"] <= 160],
          "clock 100-160 (draw NOT yet in any horizon)")


if __name__ == "__main__":
    main()
