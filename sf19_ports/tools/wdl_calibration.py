"""Did the WDL labels fix the defect they were aimed at, or just happen to win?

`sf2_wdl` shipped on 2026-09-25 for one reason: +33 Elo vs live and +23 vs the step-matched
control, both H1. That says it is stronger. It does not say WHY, and the "why" on record is a
mechanism claim: the teacher could not convert its own won endgames, so the net was being taught
to hold a winning score into a drawn game (the `#ifdef CLOCK_DAMP` note in evaluate.cpp), and
outcome labels should teach it that the shuffles are draws.

That claim is testable for free, and it has never been tested. The match that shipped the net is
178 games of `sf2_wdl` against the very net it replaced, and 80 of them ended on the capture
clock. Both seats logged `score=` on every move. So for each clock draw we can read what each of
the two nets believed while the game ran out -- in the SAME game, from both sides, which controls
for the position, the opening, the clock and the host.

  MECHANISM CONFIRMED.  In clock draws the WDL seat's own score decays toward zero and the live
                        seat's does not. The +33 Elo is the calibration fix doing its job, and
                        more of this axis is worth buying (though the share sweep says not by
                        turning the share knob -- 0.75 came back H0 at -9.7 Elo).
  MECHANISM DENIED.     Both seats hold the same inflated belief. The net got stronger for some
                        other reason, the 190 Elo conversion target is untouched, and it still
                        needs an attack that is not "label the outcome".

What this is NOT: a strength test. It ships nothing and decides nothing. It reads games already
played and asks what the engines believed while playing them.

THE JOIN is `untouchable_score_trace.py`'s, which verified itself on 240 games: a seat plays one
colour, so its plies are single-parity and its `best=` at each ply is the move the referee stream
recorded there. Matching the first MATCH_PLIES moves of the right parity identifies the game and
the colour. Here the stream's `first`/`second` fields then say which of the two is the candidate,
so no seat-log directory has to be trusted for identity.

Score sign: `score=` is UCI, from the side to move, so read at the seat's own plies it is already
in that seat's frame and needs no flip.

No sys.argv: constants below, run straight from PyCharm.
"""
import collections
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
A = os.path.join(ROOT, "sf19_ports", "data", "w1_match_archive")

# (label, stream, [seat-log dirs]).  The candidate is named in each game's own start event.
SETS = [
    ("sf2_wdl vs live   (the match that shipped it)", "seq/seq_wdl_sf2wdl_vs_live_500ms.0.jsonl",
     ["wdl_sf2wdl_vs_live_500ms_cand_logs", "wdl_sf2wdl_vs_live_500ms_ref_logs"]),
    ("sf2_wdl vs pass6  (step-matched control)", "seq/seq_wdl_sf2wdl_vs_pass6_500ms.0.jsonl",
     ["wdl_sf2wdl_vs_pass6_500ms_cand_logs", "wdl_sf2wdl_vs_pass6_500ms_ref_logs"]),
    ("share 0.75 vs sf2_wdl (both WDL nets -- sf2_wdl is the CURRENT live, not row 1's)", "seq/seq_share075_vs_live_500ms.0.jsonl",
     ["share075_vs_live_500ms_cand_logs", "share075_vs_live_500ms_ref_logs"]),
]

MATCH_PLIES = 4      # moves of one parity needed to identify a game; 3 is already unique
TAIL = 6             # the seat's own last N moves -- saturated at 0, see the note in main()
BANDS = [(0, 10), (10, 20), (20, 40), (40, 60), (60, 100), (100, 10 ** 6)]
BELIEF_CP = 200      # "still claiming an advantage"; where the belief is declared to have ended
WIN_CP = 400         # "my search says this is won", the conversion census's threshold
HOLD = 6             # sustained for this many of the seat's OWN moves in a row, not one spike
RX = re.compile(r"ply=(\d+).*?score=(-?\d+)\s+best=(\S+)")


def read_stream(path):
    games = {}
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        k = (d.get("pair"), d.get("game"))
        e = d.get("event")
        if e == "start":
            games[k] = {"moves": {}, "first": d.get("first", ""), "second": d.get("second", "")}
        elif e == "move" and k in games:
            games[k]["moves"][d["ply"]] = d["move"]
        elif e == "end" and k in games:
            games[k]["end"] = (d.get("winner"), d.get("end"))
    return games


def seat_segments(path):
    """A seat process serves many games in sequence; a ply going backwards starts a new one."""
    out, cur, prev = [], [], None
    for line in open(path):
        m = RX.search(line)
        if not m:
            continue
        ply, sc, best = int(m.group(1)), int(m.group(2)), m.group(3)
        if prev is not None and ply < prev:
            out.append(cur)
            cur = []
        cur.append((ply, sc, best))
        prev = ply
    if cur:
        out.append(cur)
    return out


def sustained(scores, thresh, hold):
    """Did this seat's own score hold >= thresh for `hold` of its own moves in a row?"""
    run = 0
    for p in sorted(scores):
        run = run + 1 if scores[p] >= thresh else 0
        if run >= hold:
            return True
    return False


def collect(stream, dirs):
    """-> rows, one per (game, seat), and the join's own diagnostics."""
    games = read_stream(os.path.join(A, stream))
    index, dup = {}, set()
    for k, g in games.items():
        for par in (0, 1):
            ps = [p for p in sorted(g["moves"]) if p % 2 == par][:MATCH_PLIES]
            if len(ps) < MATCH_PLIES:
                continue
            key = tuple((p, g["moves"][p].replace("-", "")) for p in ps)
            if key in index:
                dup.add(key)          # two games share an opening prefix: trust neither
            index[key] = (k, par)
    for key in dup:
        index.pop(key, None)

    scores, joined = {}, 0
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(A, d, "*"))):
            for seg in seat_segments(path):
                if len(seg) < MATCH_PLIES:
                    continue
                hit = index.get(tuple((p, b) for p, _, b in seg[:MATCH_PLIES]))
                if hit is None:
                    continue
                joined += 1
                scores[hit] = {p: s for p, s, _ in seg}

    rows = []
    for k, g in games.items():
        winner, end = g.get("end", (None, None))
        if end is None:
            continue
        for par in (0, 1):
            own = scores.get((k, par))
            if not own or len(own) < TAIL:
                continue
            who = g["first"] if par == 0 else g["second"]
            plies = sorted(own)
            rows.append({
                "game": k, "side": par,
                "role": "cand" if who.startswith("cand") else "ref",
                "name": who,
                "drawn": winner is None,
                "clock": winner is None and end == "CaptureClock",
                "won": winner == par,
                "final": own[plies[-1]],
                "tail": sum(own[p] for p in plies[-TAIL:]) / float(TAIL),
                "peak": max(own.values()),
                "held": sustained(own, WIN_CP, HOLD),
                "own": own,
            })
    return rows, len(games), joined, len(dup)


def band_scores(r):
    """The seat's own score keyed by how many of ITS OWN moves remained before the game ended."""
    plies = sorted(r["own"])
    return [(len(plies) - 1 - i, r["own"][p]) for i, p in enumerate(plies)]


def belief_end(r, thresh):
    """How many of its own moves before the end did this seat last claim >= thresh? None if never."""
    bs = band_scores(r)
    hits = [d for d, sc in bs if sc >= thresh]
    return max(hits) if hits else None


def report(label, rows, ngames, joined, dup):
    seats = collections.Counter(r["name"] for r in rows)
    print("=" * 78)
    print(label)
    print("  %d games in the stream, %d seat segments joined, %d ambiguous keys dropped"
          % (ngames, joined, dup))
    print("  seats recovered: %s" % ", ".join("%s x%d" % (n, c) for n, c in sorted(seats.items())))
    if not rows:
        return
    names = sorted(seats)
    draws = [r for r in rows if r["clock"]]
    print()
    print("  CLOCK DRAWS (%d seats). The seat's own score, by how many of its own moves were left:"
          % len(draws))
    print("    %-22s %s" % ("moves left", "".join("%12s" % ("%d-%d" % b if b[1] < 10 ** 5 else "%d+" % b[0])
                                                  for b in BANDS)))
    for n in names:
        cells = []
        for lo, hi in BANDS:
            vals = [sc for r in draws if r["name"] == n for d, sc in band_scores(r) if lo <= d < hi]
            cells.append("%12s" % ("%.0f (%d)" % (sum(vals) / len(vals), len(vals)) if vals else "--"))
        print("    %-22s %s" % (n, "".join(cells)))

    # Signed means cancel: the two seats are OPPONENTS, so a shared belief that one side stands
    # better shows up as +x and -x and averages away. In a game that was in fact drawn, the honest
    # miscalibration of a seat is its DISTANCE FROM ZERO, which cannot cancel.
    print()
    print("  Same clock draws, mean |score| -- how far from the truth (a draw) each seat was:")
    for n in names:
        cells = []
        for lo, hi in BANDS:
            vals = [abs(sc) for r in draws if r["name"] == n for d, sc in band_scores(r) if lo <= d < hi]
            cells.append("%12s" % ("%.0f" % (sum(vals) / len(vals)) if vals else "--"))
        print("    %-22s %s" % (n, "".join(cells)))
    # Paired, inside the same game: the two seats of one clock draw, at the same distance from the end.
    by_game = collections.defaultdict(dict)
    for r in draws:
        by_game[r["game"]][r["role"]] = r
    both = [g for g in by_game.values() if "cand" in g and "ref" in g]
    if both:
        print()
        print("  PAIRED inside the same %d clock-drawn games, candidate minus reference:" % len(both))
        for lo, hi in BANDS:
            ds = []
            for g in both:
                c = [sc for d, sc in band_scores(g["cand"]) if lo <= d < hi]
                f = [sc for d, sc in band_scores(g["ref"]) if lo <= d < hi]
                if c and f:
                    ds.append(sum(c) / len(c) - sum(f) / len(f))
            if ds:
                lower = sum(1 for d in ds if d < 0)
                print("    %-12s n=%-4d mean %+7.0f cp   candidate believed less in %3d of %3d (%2.0f%%)"
                      % ("%d-%d left" % (lo, hi) if hi < 10 ** 5 else "%d+ left" % lo,
                         len(ds), sum(ds) / len(ds), lower, len(ds), 100.0 * lower / len(ds)))
        print()
        print("  Same pairs, |score| -- candidate closer to zero is the calibration claim:")
        for lo, hi in BANDS:
            ds, sums = [], []
            for g in both:
                c = [abs(sc) for d, sc in band_scores(g["cand"]) if lo <= d < hi]
                f = [abs(sc) for d, sc in band_scores(g["ref"]) if lo <= d < hi]
                cs = [sc for d, sc in band_scores(g["cand"]) if lo <= d < hi]
                fs = [sc for d, sc in band_scores(g["ref"]) if lo <= d < hi]
                if c and f:
                    ds.append(sum(c) / len(c) - sum(f) / len(f))
                    sums.append(sum(cs) / len(cs) + sum(fs) / len(fs))
            if ds:
                closer = sum(1 for d in ds if d < 0)
                print("    %-12s n=%-4d mean %+7.0f cp   candidate closer to 0 in %3d of %3d (%2.0f%%)"
                      "   [they agree to %+.0f cp]"
                      % ("%d-%d left" % (lo, hi) if hi < 10 ** 5 else "%d+ left" % lo,
                         len(ds), sum(ds) / len(ds), closer, len(ds), 100.0 * closer / len(ds),
                         sum(sums) / len(sums)))

    # When does the belief end?  Only games where the seat ever claimed it.
    print()
    print("  In clock draws where the seat ever claimed >= +%d, how many of its own moves" % BELIEF_CP)
    print("  remained when it last claimed it (higher = gave up the fantasy earlier):")
    for n in names:
        ends = [belief_end(r, BELIEF_CP) for r in draws if r["name"] == n]
        ends = [e for e in ends if e is not None]
        if not ends:
            print("    %-24s never claimed it in a clock draw" % n)
            continue
        ends.sort()
        print("    %-24s %3d of %3d games   median %3d moves left,  p90 %3d,  max %3d"
              % (n, len(ends), sum(1 for r in draws if r["name"] == n),
                 ends[len(ends) // 2], ends[int(0.9 * (len(ends) - 1))], ends[-1]))

    # Conversion: of the games a seat thought it had won, how many did it win?
    print()
    print("  sustained >= +%d for %d of its own moves -> what actually happened:" % (WIN_CP, HOLD))
    for n in names:
        s = [r for r in rows if r["name"] == n and r["held"]]
        if not s:
            continue
        print("    %-24s %4d games  won %4.1f%%  clock-drew %4.1f%%"
              % (n, len(s), 100.0 * sum(1 for r in s if r["won"]) / len(s),
                 100.0 * sum(1 for r in s if r["clock"]) / len(s)))
    print()


def main():
    print("WDL calibration -- what each net BELIEVED while a game ran out on the capture clock")
    print()
    print("NOTE, and it is the first result: in EVERY clock draw, in every match, both seats")
    print("score the position EXACTLY 0 over their last several moves -- 135 of 135 seats in the")
    print("shipping match, final score 0 in all of them. The engines do not drift to zero, they")
    print("SEE the clock once it enters the search horizon and return a hard draw score. So the")
    print("end of the game says nothing about eval calibration, and the question worth asking is")
    print("how far from the end the belief collapses, which is what follows.")
    print()
    for label, stream, dirs in SETS:
        path = os.path.join(A, stream)
        if not os.path.exists(path):
            print("=" * 78)
            print(label)
            print("  no stream at %s" % path)
            continue
        rows, ngames, joined, dup = collect(stream, dirs)
        report(label, rows, ngames, joined, dup)
    print("=" * 78)
    print("Read it this way: if outcome labels taught the net that the shuffles are draws, the")
    print("WDL seat should claim LESS than the net it replaced at the same distance from the end")
    print("of the same drawn game, and should give up the claim with more moves still to play.")
    print("If the two curves sit on top of each other, the +33 Elo came from somewhere other than")
    print("draw calibration and the 190 Elo conversion target is still open.")
    print("Observational: these games were played for another purpose and nothing here is a test.")


main()
