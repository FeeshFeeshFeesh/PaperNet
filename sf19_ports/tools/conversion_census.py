"""How often does SF19-NN reach a won position and then fail to convert it?

This is the INSTRUMENT for the conversion axis, not a fix. The largest known defect in the
project is worth ~190 Elo and is invisible to an ordinary SPRT: a term that only matters once
the game is already won moves almost no games from an opening book, so plain Elo cannot see it.
The number to move is the CONVERSION RATE -- of the games where our own search said "winning",
what fraction actually finished.

It costs no compute. Every A/B match the project has ever run already wrote what is needed:

  - the seat (`ab_nodes_seat.py` and its siblings) logs one line per move with the SEARCH score,
    `... ply=N depth=D seldepth=S nodes=X time=T score=C best=M`;
  - sploppy's referee wrote a jsonl stream beside it with `start` / `move` / `end` events, and
    `end` carries the winner and the ply count.

The two are joined here WITHOUT trusting any assumption for free. The referee runs one worker
process per `--threads`, each playing its share of the games sequentially, so a worker's log is
its games end to end and `ply=` running backwards marks a boundary. Which game went to which
worker is then a guess -- round robin -- and that guess is CHECKED: a segment's line count is
determined by the game's total plies, so if the assignment is right every one of the hundreds of
segments matches its game's length exactly, and if it is wrong the mismatches are immediate. A
join that verifies itself on 800 games is a join; an unverified one is a story. When the check
fails the script says so and falls back to the trajectory census, which needs no join at all.

Reads only. No games are played and nothing is written.
"""
import collections
import glob
import json
import os
import re

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

# ---------------------------------------------------------------- what to measure
# The raw runs were archived into the repo on 2026-09-25. They used to be read straight out of
# a Claude session scratchpad, which is wiped when the session ends -- and two of them were
# already gone by the time the archive was made: the 20k-nodes row and the 1000 ms row lost
# both their seat logs and their referee stream. Those rows now print "no logs"/"no referee
# stream" and are skipped; the ladder still runs 100k -> 400k, which is where the trend lives.
# See the archive's README.md. Do not point this back at /private/tmp.
S = os.path.join(_ROOT, "sf19_ports", "data", "conversion_archive")

RUNS = [
    # (label, glob of ONE side's seat logs, the referee stream)
    # A node ladder. The point of running all of them is that the defect is expected to GROW
    # with search: a weak search blunders games away decisively, a strong one holds everything
    # and draws. If conversion failure rises as the engine gets stronger, it is a planning
    # problem and no amount of eval or net training touches it.
    ("20k nodes   cand", S + "/h2h32_cand_logs/*.log",        S + "/seq_port_s3_s2_20k.0.jsonl"),
    ("20k nodes   ref",  S + "/h2h32_ref_logs/*.log",         S + "/seq_port_s3_s2_20k.0.jsonl"),
    ("100k nodes",       S + "/ab_nsdeep_slow_logs/*.log",    S + "/ab_nsdeep_eval.jsonl"),
    ("200k nodes  (a)",  S + "/ab_nsdeep_fast_logs/*.log",    S + "/ab_nsdeep_eval.jsonl"),
    ("200k nodes  (b)",  S + "/ab_nsdeeper_slow_logs/*.log",  S + "/ab_nsdeeper_eval.jsonl"),
    ("400k nodes",       S + "/ab_nsdeeper_fast_logs/*.log",  S + "/ab_nsdeeper_eval.jsonl"),
    ("1000 ms    cand",  S + "/ab_p2_logs/*.log",             S + "/seq_port_sf2_ex_1s.3.jsonl"),
    ("1000 ms    ref",   S + "/ab_pex_logs/*.log",            S + "/seq_port_sf2_ex_1s.3.jsonl"),
]

WIN_CP      = 400    # "our search says this is won" -- the draw census used +200, this is stricter
HOLD_PLIES  = 6      # it must say so for this many of ITS OWN moves in a row, not one spike
GONE_CP     = 50     # "the advantage is gone" -- what the score decayed to by the end
OPEN_PLIES  = 8      # the referee's --opening-plies for these runs; the first logged ply


# ---------------------------------------------------------------- seat logs
LINE = re.compile(r"\bply=(\d+)\b.*?\bscore=(-?\d+|mate-?\d+)\b")


def score_cp(tok):
    """A mate score is not a centipawn count; clamp it to something comparable and huge."""
    if tok.startswith("mate"):
        n = int(tok[4:])
        return 30000 if n >= 0 else -30000
    return int(tok)


def segments(path):
    """Split one worker's log into its games. A ply number that does not advance starts a new one."""
    out, cur, prev = [], [], None
    for line in open(path, errors="replace"):
        m = LINE.search(line)
        if not m:
            continue
        ply, cp = int(m.group(1)), score_cp(m.group(2))
        if prev is not None and ply <= prev:
            out.append(cur)
            cur = []
        cur.append((ply, cp))
        prev = ply
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- referee stream
def games(stream):
    """The referee's games in the order they were STARTED, which is the order workers took them."""
    started, ended = [], {}
    for line in open(stream, errors="replace"):
        if '"event"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        k = (ev.get("pair"), ev.get("game"))
        if ev.get("event") == "start":
            started.append(k)
        elif ev.get("event") == "end":
            ended[k] = (ev.get("winner"), ev.get("plies"), ev.get("end"))
    return [(k, ended[k]) for k in started if k in ended]


def expected_lines(plies, moves_first):
    """How many moves a seat made in a game of `plies` plies, given whether it moved first.

    Plies are counted from 0, the first OPEN_PLIES are the book, so this seat's moves are the
    plies of its own parity at or after OPEN_PLIES and below `plies`.
    """
    start = OPEN_PLIES + (0 if moves_first else 1)
    return max(0, (plies - start + 1) // 2)


def join(segs, gs):
    """Round-robin assignment of games to workers, verified against every segment's length.

    Returns (pairs, complaint). `pairs` is [(segment, (winner, plies, end), moves_first)] and is
    empty when the verification fails -- a join that does not check out is worth nothing.
    """
    nw = len(segs)
    if not nw or not gs:
        return [], "no segments or no games"
    total = sum(len(s) for s in segs)
    if total != len(gs) and len(gs) % nw:
        pass  # not fatal by itself; the length check below is the real test
    pairs, bad = [], 0
    for w, worker in enumerate(segs):
        mine = gs[w::nw]
        if len(mine) != len(worker):
            return [], ("worker %d holds %d segments but round robin gives it %d games"
                        % (w, len(worker), len(mine)))
        for seg, (key, res) in zip(worker, mine):
            plies = res[1] or 0
            first = expected_lines(plies, True)
            second = expected_lines(plies, False)
            if len(seg) == first:
                pairs.append((seg, res, True))
            elif len(seg) == second:
                pairs.append((seg, res, False))
            else:
                bad += 1
    if bad:
        return [], ("%d of %d segments do not match their game's ply count" % (bad, len(gs)))
    return pairs, ""


# ---------------------------------------------------------------- the census
def trajectory(seg):
    """peak score, the ply it peaked at, the last score, and whether the peak was SUSTAINED."""
    peak, at = -10 ** 9, 0
    run, sustained = 0, False
    for ply, cp in seg:
        if cp > peak:
            peak, at = cp, ply
        run = run + 1 if cp >= WIN_CP else 0
        if run >= HOLD_PLIES:
            sustained = True
    return peak, at, (seg[-1][1] if seg else 0), sustained


def stall(seg):
    """The longest run of the seat's own moves spent at >= WIN_CP, and the score's drift over it.

    This is the shape the diagnosis turns on. A won position that the engine LOSES its grip on
    shows a decaying score. A won position it cannot find the conversion for shows a score that
    sits flat and high for a hundred plies and then the capture clock ends the game with the
    engine still saying +800. Those are different bugs and they want different fixes, so the
    census has to tell them apart rather than count them both as "failed to convert".
    """
    best, run_start = 0, None
    span, first_cp, last_cp = 0, 0, 0
    for i, (ply, cp) in enumerate(seg):
        if cp >= WIN_CP:
            if run_start is None:
                run_start = i
            if i - run_start + 1 > best:
                best = i - run_start + 1
                span = seg[i][0] - seg[run_start][0]
                first_cp, last_cp = seg[run_start][1], cp
        else:
            run_start = None
    return best, span, first_cp, last_cp


def buckets(vals, edges, fmt="%d"):
    out = collections.Counter()
    for v in vals:
        for e in edges:
            if v < e:
                out[e] = out[e] + 1
                break
        else:
            out[None] = out[None] + 1
    lines = []
    prev = None
    for e in edges:
        lo = fmt % prev if prev is not None else "-inf"
        lines.append(("%s..%s" % (lo, fmt % e), out[e]))
        prev = e
    lines.append((("%s.." % (fmt % edges[-1])) + "inf", out[None]))
    return [(k, c) for k, c in lines if c]


def report(label, segs, pairs, complaint):
    print("-----", label)
    flat = [s for w in segs for s in w]
    print("  %d worker logs, %d game segments, %d move records"
          % (len(segs), len(flat), sum(len(s) for s in flat)))

    won = [s for s in flat if trajectory(s)[3]]
    thrown = [s for s in won if trajectory(s)[2] <= GONE_CP]
    print("  reached a SUSTAINED +%d (>= %d of its own moves in a row): %d of %d  (%.1f%%)"
          % (WIN_CP, HOLD_PLIES, len(won), len(flat), 100.0 * len(won) / max(1, len(flat))))
    if won:
        print("  ...of those, the score had decayed to <= +%d by the last move: %d  (%.1f%%)"
              % (GONE_CP, len(thrown), 100.0 * len(thrown) / len(won)))

    if won:
        finals = [trajectory(s)[2] for s in won]
        print("  where the score STOOD on its last move in those %d games:" % len(won))
        for k, c in buckets(finals, [-400, 0, 50, 200, 400, 800, 1600]):
            print("    %14s cp  %5d   %5.1f%%" % (k, c, 100.0 * c / len(won)))
        runs = [stall(s) for s in won]
        print("  longest unbroken stretch AT >= +%d, in plies of the whole game:" % WIN_CP)
        for k, c in buckets([sp for _, sp, _, _ in runs], [20, 50, 100, 200, 400]):
            print("    %14s plies %5d   %5.1f%%" % (k, c, 100.0 * c / len(runs)))
        held = [(sp, a2 - a1) for _, sp, a1, a2 in runs if sp >= 100]
        if held:
            drift = sorted(d for _, d in held)
            mid = drift[len(drift) // 2]
            print("  in the %d games that held >= +%d for 100+ plies, the median score DRIFT"
                  % (len(held), WIN_CP))
            print("  from the start to the end of that stretch was %+d cp." % mid)

    if not pairs:
        print("  NO OUTCOME JOIN: %s" % complaint)
        print("  The trajectory numbers above stand on their own; the win/draw split does not.")
        return

    # The seat's own result. winner is the referee's game index, and `moves_first` tells us
    # whether this seat was that index -- game 0 is the first-named engine, which moves first.
    tally = collections.Counter()
    for seg, (winner, plies, end), first in pairs:
        peak, at, last, sustained = trajectory(seg)
        if not sustained:
            continue
        me = 0 if first else 1
        got = "win" if winner == me else "draw" if winner is None else "loss"
        tally[(got, end or "?")] += 1
    n = sum(tally.values())
    if not n:
        print("  no game in this run ever reached a sustained +%d" % WIN_CP)
        return
    print("  OUTCOME of the %d games it said were won:" % n)
    for (got, end), c in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("    %-5s %-16s %5d   %5.1f%%" % (got, end, c, 100.0 * c / n))
    wins = sum(c for (g, _), c in tally.items() if g == "win")
    print("  CONVERSION RATE  %.1f%%   (%d of %d)" % (100.0 * wins / n, wins, n))


def main():
    print("conversion census -- won positions reached, and what happened to them")
    print("thresholds: sustained +%d for %d of the seat's own moves; 'gone' is <= +%d"
          % (WIN_CP, HOLD_PLIES, GONE_CP))
    print()
    for label, logs, stream in RUNS:
        paths = sorted(glob.glob(logs))
        if not paths:
            print("-----", label)
            print("  no logs at %s" % logs)
            continue
        if not os.path.exists(stream):
            print("-----", label)
            print("  no referee stream at %s" % stream)
            continue
        segs = [segments(p) for p in paths]
        gs = games(stream)
        pairs, complaint = join(segs, gs)
        report(label, segs, pairs, complaint)
        print()


main()
