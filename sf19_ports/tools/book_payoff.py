"""What the balanced opening book actually bought, measured on played games.

The claim being checked was made BEFORE the book existed: 45.8% of SPRT pairs were decided by the
opening alone (each engine won when it held the favoured colour), 22.4% were both-drawn, so only
31.8% of pairs carried information. Filtering openings to |eval| <= 0.10 was predicted to raise that
to 49.0%, i.e. 1.54x fewer pairs to a verdict, CI [1.34x, 1.77x].

A pair's two games share an opening and swap colours. Its score in {0,.25,.5,.75,1} is the
candidate's points over the two games, halved. The MIDDLE bucket (.5) is the uninformative one and
it has two very different causes, which this splits apart:
    SPLIT   -- each side won its own colour: the opening decided it. The book should kill these.
    DD      -- both games drawn. The book may well CREATE these, since balanced positions draw more.
Anything else (.25/.75 and the extremes) moved the LLR.

VERIFICATION: the pentanomial computed here must equal the one seq_eval logged for the same run.
If the winner->seat mapping below were wrong, it would not.

No sys.argv: constants below.
"""
import json
import os

D = "/var/tmp/rps/seq"
RUNS = [
    ("09-24 test A  sf2_wdl vs live      ", "seq_wdl_sf2wdl_vs_live_500ms.0.jsonl",  "NO BOOK"),
    ("09-24 test B  sf2_wdl vs pass6     ", "seq_wdl_sf2wdl_vs_pass6_500ms.0.jsonl", "NO BOOK"),
    ("09-25 test A  sf2_wdl075 vs live   ", "seq_share075_vs_live_500ms.0.jsonl",    "BOOKED"),
    ("09-25 test B  sf2_wdl100 vs live   ", "seq_share100_vs_live_500ms.0.jsonl",    "BOOKED"),
]


def pairs_of(path):
    """(pair, game) -> (candidate points, was_draw). Incomplete pairs are dropped, never halved."""
    first, pts, drew = {}, {}, {}
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue                      # a half-written last line while the match is live
            if r.get("event") == "start":
                first[(r["pair"], r["game"])] = r["first"].startswith("cand")
            elif r.get("event") == "end":
                k = (r["pair"], r["game"])
                if k not in first:
                    continue
                w = r.get("winner")
                if w is None:
                    pts[k], drew[k] = 0.5, True
                else:
                    cand_first = first[k]
                    cand_won = (w == 0) == cand_first
                    pts[k], drew[k] = (1.0 if cand_won else 0.0), False
    out = {}
    for (p, g), v in pts.items():
        out.setdefault(p, {})[g] = (v, drew[(p, g)])
    return {p: g for p, g in out.items() if len(g) == 2}


def report(label, path, kind):
    if not os.path.exists(path):
        print("%s  %-8s  (no file)" % (label, kind))
        return
    pr = pairs_of(path)
    penta = [0] * 5
    split = dd = 0
    for p, g in pr.items():
        (a, da), (b, db) = g[0], g[1]
        s = (a + b) / 2.0
        penta[int(round(s * 4))] += 1
        if abs(s - 0.5) < 1e-9:
            if da and db:
                dd += 1
            elif not da and not db:
                split += 1               # each side won one -- with colours swapped, the opening
    n = len(pr)
    if not n:
        print("%s  %-8s  (no complete pairs yet)" % (label, kind))
        return
    mid = penta[2]
    info = n - mid
    print("%s  %-8s  %4d pairs  penta %-22s" % (label, kind, n, penta))
    print("      informative %5.1f%%   middle %5.1f%%  = opening-decided %5.1f%%  + both-drawn %5.1f%%"
          % (100.0 * info / n, 100.0 * mid / n, 100.0 * split / n, 100.0 * dd / n))
    return n, info, split, dd, penta


def main():
    print("run                                  book      pairs  pentanomial")
    print("-" * 100)
    tot = {}
    for label, fn, kind in RUNS:
        r = report(label, os.path.join(D, fn), kind)
        if r:
            n, info, split, dd, _ = r
            t = tot.setdefault(kind, [0, 0, 0, 0])
            t[0] += n; t[1] += info; t[2] += split; t[3] += dd
    print("-" * 100)
    for kind in ("NO BOOK", "BOOKED"):
        if kind in tot:
            n, info, split, dd = tot[kind]
            print("POOLED %-8s %4d pairs   informative %5.1f%%   opening-decided %5.1f%%   both-drawn %5.1f%%"
                  % (kind, n, 100.0 * info / n, 100.0 * split / n, 100.0 * dd / n))
    if "NO BOOK" in tot and "BOOKED" in tot:
        a = tot["NO BOOK"][1] / float(tot["NO BOOK"][0])
        b = tot["BOOKED"][1] / float(tot["BOOKED"][0])
        print()
        print("SPEEDUP: informative share %.1f%% -> %.1f%% = %.2fx fewer pairs to the same verdict"
              % (100 * a, 100 * b, b / a))
        print("PREDICTED BEFORE THE BOOK EXISTED: 31.8%% -> 49.0%% = 1.54x, CI [1.34x, 1.77x]")


main()
