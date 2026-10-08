"""Idea #3, step 1: is DECISIVENESS a property of the opening, or just engine noise?

Idea #3 wants to pre-screen an opening book so every future SPRT needs fewer pairs.  That only
works if openings actually differ from one another in a way that repeats.  If a game's result is
engine noise given a random start, then any "good" opening found in a screen is a fluke and the
book is worthless.  This measures that BEFORE anyone writes Rust to load a book.

THE TEST IS FREE, because the harness already ran it 150 times per match: both games of a pair play
THE SAME OPENING with the colours swapped.  Under "openings are interchangeable" the two games of a
pair are independent, so P(both decisive) = p^2.  A positive concordance means openings differ.

AND A TRAP THAT WOULD HAVE MADE THE WHOLE IDEA BACKFIRE.  The SPRT's unit is the PAIR, scored
pentanomially 0 / 0.5 / 1 / 1.5 / 2 for the candidate.  A pair scoring exactly 1.0 sits at the mode
of both hypotheses, so its likelihood ratio is ~1 and it moves the LLR by almost nothing.  There are
TWO ways to score 1.0:

    both games drawn                      -- the waste everyone notices
    the SAME COLOUR wins both games       -- the opening decided it, not the engine

So "maximise decisive rate" is the WRONG objective: it would select lopsided openings where the
first player always wins, which are decisive AND useless.  The objective is openings that are
BALANCED (neither colour forced) yet CONVERTIBLE (skill can break them).  This script reports both
so the difference is visible.

Data: the streams rescued into sf19_ports/data/conversion_archive/ (see its README for what is
lost).  Reads only `start` and `end` events; no engine, no games, no cloud.

No sys.argv: constants below, run straight from PyCharm.
"""
import collections
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ARCHIVE = os.path.join(ROOT, "sf19_ports", "data", "conversion_archive")
STREAMS = ["cloud_sprt_deep_0.jsonl", "cloud_sprt_deep_1.jsonl", "cloud_sprt_deep_2.jsonl",
           "ab_nsdeep_eval.jsonl", "ab_nsdeeper_eval.jsonl"]
ELO1 = 20.0          # the H1 the WDL SPRT used, for the LLR-per-pair weighting below
BOOT = 2000
SEED = 20260925


def read_stream(path):
    """-> {pair: {game: (first_is_cand, winner, plies, opening_key)}}"""
    games = collections.defaultdict(dict)
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            ev = r.get("event")
            if ev == "start":
                games[r["pair"]][r["game"]] = {
                    "cand_first": "cand" in r["first"],
                    "opening": ",".join(r.get("opening") or [])}
            elif ev == "end":
                g = games.get(r["pair"], {}).get(r["game"])
                if g is not None:
                    g["winner"] = r["winner"]
                    g["plies"] = r["plies"]
                    g["end"] = r["end"]
    return games


def pair_rows(games):
    """-> list of (cand_points_out_of_2, n_decisive_in_pair, same_colour_won_both, opening)"""
    out = []
    for pair, gs in sorted(games.items()):
        if set(gs) != {0, 1} or any("winner" not in g for g in gs.values()):
            continue
        pts, dec, colour_wins = 0.0, 0, []
        for gi in (0, 1):
            g = gs[gi]
            w = g["winner"]
            if w is None:
                pts += 0.5
            else:
                dec += 1
                cand_won = (w == 0) == g["cand_first"]
                pts += 1.0 if cand_won else 0.0
                colour_wins.append(w)          # 0 = the side to move first won
        both_same_colour = dec == 2 and len(set(colour_wins)) == 1
        out.append((pts, dec, both_same_colour, gs[0]["opening"]))
    return out


def llr_weight(pts):
    """|log L1/L0| for one pair, normal approximation. Score 1.0 -> ~0: the pair bought nothing."""
    s1 = 1.0 / (1.0 + 10.0 ** (-ELO1 / 400.0))        # H1 per-game score
    return abs((pts / 2.0 - 0.5) * (s1 - 0.5))


def boot_ci(vals, stat, rng):
    import random
    n = len(vals)
    reps = sorted(stat([vals[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT))
    return reps[int(0.025 * BOOT)], reps[int(0.975 * BOOT)]


def main():
    import random
    rng = random.Random(SEED)
    all_rows, per_stream = [], []
    for name in STREAMS:
        path = os.path.join(ARCHIVE, name)
        if not os.path.exists(path):
            print("MISSING %s" % name)
            continue
        rows = pair_rows(read_stream(path))
        per_stream.append((name, rows))
        all_rows += rows

    print("%-26s %6s %8s %8s" % ("stream", "pairs", "decisive", "wasted"))
    for name, rows in per_stream:
        if not rows:
            continue
        dec = sum(r[1] for r in rows) / (2.0 * len(rows))
        wasted = sum(1 for r in rows if r[0] == 1.0) / float(len(rows))
        print("%-26s %6d %7.1f%% %7.1f%%" % (name[:26], len(rows), 100 * dec, 100 * wasted))

    rows = all_rows
    n = len(rows)
    p = sum(r[1] for r in rows) / (2.0 * n)
    print("-----")
    print("POOLED  %d pairs, %d games, per-game decisive rate %.3f" % (n, 2 * n, p))

    # 1. Concordance: do the two games of one opening agree more than chance?
    obs = collections.Counter(r[1] for r in rows)
    exp = {k: n * math.comb(2, k) * p ** k * (1 - p) ** (2 - k) for k in (0, 1, 2)}
    print("-----")
    print("IS DECISIVENESS AN OPENING PROPERTY?  same opening, colours swapped")
    print("%-22s %10s %10s" % ("decisive games in pair", "observed", "if indep"))
    for k in (0, 1, 2):
        print("%-22s %10d %10.1f" % (k, obs[k], exp[k]))
    phi = (obs[2] / float(n) - p * p) / (p * (1 - p)) if 0 < p < 1 else float("nan")
    lo, hi = boot_ci(rows, lambda rs: ((sum(1 for r in rs if r[1] == 2) / float(len(rs))
                                        - (sum(r[1] for r in rs) / (2.0 * len(rs))) ** 2)
                                       / max(1e-9, (sum(r[1] for r in rs) / (2.0 * len(rs)))
                                             * (1 - sum(r[1] for r in rs) / (2.0 * len(rs))))), rng)
    print("correlation between the two games of a pair: phi %+0.4f  95%% CI [%+0.4f, %+0.4f]"
          % (phi, lo, hi))
    print("  phi ~ 0  -> openings are interchangeable; a screened book is a fluke. IDEA #3 IS DEAD.")
    print("  phi > 0  -> openings differ and the difference repeats. A book can be screened.")

    # 2. Where the money actually goes.
    both_draw = sum(1 for r in rows if r[1] == 0)
    colour = sum(1 for r in rows if r[2])
    informative = n - both_draw - colour
    print("-----")
    print("WHERE THE PAIRS GO  (a pair scoring exactly 1.0 moves the LLR by ~nothing)")
    print("  both games drawn            %5d  %5.1f%%   wasted" % (both_draw, 100.0 * both_draw / n))
    print("  same colour won both        %5d  %5.1f%%   wasted -- the OPENING decided it"
          % (colour, 100.0 * colour / n))
    print("  everything else             %5d  %5.1f%%   informative"
          % (informative, 100.0 * informative / n))
    wasted = both_draw + colour
    print("  TOTAL WASTED                %5d  %5.1f%%" % (wasted, 100.0 * wasted / n))
    print("  ceiling if every wasted pair were made informative: %.2fx fewer pairs to a verdict"
          % (1.0 / max(1e-9, informative / float(n))))

    # 3. Does the same opening repeat across streams? (seeds differ, so usually not)
    seen = collections.Counter(r[3] for r in rows)
    rep = sum(1 for k, v in seen.items() if v > 1)
    print("-----")
    print("openings appearing in more than one pair: %d of %d distinct" % (rep, len(seen)))
    if rep:
        agree = [r for r in rows if seen[r[3]] > 1]
        print("  (%d pairs share an opening with another -- a direct repeatability check)" % len(agree))
    print("-----")
    print("Read the concordance FIRST. Everything downstream of idea #3 depends on it.")


if __name__ == "__main__":
    main()
