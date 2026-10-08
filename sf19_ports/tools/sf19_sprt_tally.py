"""Sequential test (SPRT) on a `bot eval --stream` log: stop as soon as the answer is known.

Instead of a fixed number of games, the log-likelihood ratio (LLR) of two
hypotheses is updated after every complete opening pair:
    H0: candidate is ELO_BAD  Elo  (i.e. WORSE by |ELO_BAD|)
    H1: candidate is ELO_GOOD Elo  (i.e. BETTER by ELO_GOOD)
  LLR >= upper -> "candidate is stronger";  LLR <= lower -> "reference is stronger".
Error guarantee (Wald): if the candidate is really worse by >= |ELO_BAD|, the
chance of calling it stronger is <= ALPHA; if really better by >= ELO_GOOD, the
chance of calling it weaker is <= BETA. A true gap inside the zone can go either
way (the engines are then close). Checking after every pair is what SPRT is for,
so peeking does not inflate these error rates.

LLR is the pentanomial GSPRT approximation used by Fishtest:
    LLR = N * (s1 - s0) * (2*mean - s0 - s1) / (2 * var)
over per-pair scores {0, .25, .5, .75, 1}. A pseudo-count of PSEUDO per bin keeps
the variance sane on tiny samples (it pulls toward a draw: conservative), and no
verdict is given before MIN_PAIRS. At MAX_PAIRS without a verdict: too close to call.

No sys.argv; constants below. Prints one status line (last line = verdict).
"""
import json
import math
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
STREAM    = os.path.join(S, "sf19_eval.jsonl")
CAND_NAME = "SF19-RPS"
ELO_BAD   = -30.0
ELO_GOOD  = +30.0
ALPHA     = 0.05
BETA      = 0.05
MIN_PAIRS = 10
MAX_PAIRS = 200
PSEUDO    = 0.5


def expected(elo):
    return 1.0 / (1.0 + 10 ** (-elo / 400.0))


def main():
    pairs = {}
    with open(STREAM) as fh:
        for line in fh:
            if '"event":"end"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            w = ev["winner"]
            pts = 1.0 if w == ev["game"] else 0.5 if w is None else 0.0
            pairs.setdefault(ev["pair"], {})[ev["game"]] = pts
    done = [(v[0] + v[1]) / 2.0 for v in pairs.values() if 0 in v and 1 in v]
    penta = [0] * 5
    for x in done:
        penta[int(round(x * 4))] += 1
    n_real = len(done)
    counts = [c + PSEUDO for c in penta]
    n = sum(counts)
    mean = sum(counts[i] * i / 4.0 for i in range(5)) / n
    var = sum(counts[i] * (i / 4.0) ** 2 for i in range(5)) / n - mean ** 2
    s0, s1 = expected(ELO_BAD), expected(ELO_GOOD)
    llr = n * (s1 - s0) * (2 * mean - s0 - s1) / (2 * max(var, 1e-9))
    lower = math.log(BETA / (1 - ALPHA))
    upper = math.log((1 - BETA) / ALPHA)
    raw = sum(done) / n_real if n_real else float("nan")
    elo = (-400 * math.log10(1 / min(max(raw, 1e-6), 1 - 1e-6) - 1)) if n_real else float("nan")
    status = ("pairs %d  penta(0,.25,.5,.75,1)=%s  score %.3f (%+.0f Elo)  LLR %.2f  bounds [%.2f, %.2f]"
              % (n_real, penta, raw, elo, llr, lower, upper))
    print(status)
    if n_real >= MIN_PAIRS and llr >= upper:
        print("SPRT VERDICT: %s is STRONGER (H1 %+.0f accepted, error <= %.0f%%)" % (CAND_NAME, ELO_GOOD, 100 * ALPHA))
    elif n_real >= MIN_PAIRS and llr <= lower:
        print("SPRT VERDICT: the reference is STRONGER than %s (H0 %+.0f accepted, error <= %.0f%%)" % (CAND_NAME, ELO_BAD, 100 * BETA))
    elif n_real >= MAX_PAIRS:
        print("SPRT VERDICT: too close to call after %d pairs (within about +/-%.0f Elo)" % (n_real, ELO_GOOD))
    else:
        print("SPRT: continue")


if __name__ == "__main__":
    main()
