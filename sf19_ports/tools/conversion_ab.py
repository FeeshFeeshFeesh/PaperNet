"""Did the candidate throw fewer won games than the reference?

This is the gate for the conversion work, and it is not an SPRT. An SPRT answers "is it stronger",
which is the right question for shipping and the wrong one for steering: a term that bites only in
won endgames can cut the throw rate a long way while moving Elo by less than a 32-pair match can
resolve. Read this first, and the SPRT second.

It also watches the obvious way this family of terms goes wrong. A penalty for giving up a piece
class can simply make the engine passive -- refusing trades, hoarding, and reaching fewer winning
positions at all. That failure looks fine on throw rate (you cannot throw what you never had), so
the reached-a-won-position rate is reported beside it and both have to hold.

Joins on the `gamekey` line the seat now stamps at each newgame. The older archived runs have no such
line and cannot be joined at all -- the referee does not hand games to workers round-robin, so a
worker's Nth game is not the stream's Nth game. Runs without it fall back to join-free totals and
say so.

No sys.argv.
"""
import collections
import glob
import json
import os
import re

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK

TAG    = "classloss60"
OUTDIR = os.path.join(S, "ab_" + TAG)
STREAM = os.path.join(S, "ab_%s.jsonl" % TAG)
ARMS   = [("candidate", os.path.join(OUTDIR, "cand_logs")),
          ("reference", os.path.join(OUTDIR, "ref_logs"))]

WIN_CP     = 400   # "our own search says this is won"
HOLD_PLIES = 6     # and it says so for this many of its OWN moves in a row
GONE_CP    = 50    # "the advantage is gone"

SCORE = re.compile(r"\bply=(\d+)\b.*?\bscore=(-?\d+|mate-?\d+)\b")
KEY   = re.compile(r"^gamekey board=(\S+) side=(\S+) opening=(\S+)")


def cp(tok):
    if tok.startswith("mate"):
        return 30000 if int(tok[4:]) >= 0 else -30000
    return int(tok)


def seat_games(path):
    """Split one worker's log into games on the gamekey stamps."""
    games, key, scores = [], None, []
    for line in open(path, errors="replace"):
        m = KEY.match(line)
        if m:
            if key is not None:
                games.append((key, scores))
            key, scores = m.group(3), []
            continue
        m = SCORE.search(line)
        if m:
            scores.append(cp(m.group(2)))
    if key is not None:
        games.append((key, scores))
    return games


def stream_outcomes(path):
    """opening-key -> (winner, which side each game was, end reason)."""
    out = {}
    opening = {}
    for line in open(path, errors="replace"):
        if '"event"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        k = (ev.get("pair"), ev.get("game"))
        if ev.get("event") == "start":
            opening[k] = ",".join(ev.get("opening") or []) or "-"
        elif ev.get("event") == "end":
            key = opening.get(k)
            if key is not None:
                out.setdefault(key, []).append((ev.get("winner"), ev.get("game"),
                                                ev.get("end"), ev.get("plies")))
    return out


def trajectory(scores):
    """Did this game ever look won to us, and if so was it thrown?"""
    run = 0
    won = False
    for v in scores:
        run = run + 1 if v >= WIN_CP else 0
        if run >= HOLD_PLIES:
            won = True
    if not won:
        return None
    return scores[-1] <= GONE_CP


def main():
    outcomes = stream_outcomes(STREAM) if os.path.exists(STREAM) else {}
    print("stream: %s   %d distinct openings" % (os.path.basename(STREAM), len(outcomes)))
    print()
    print("  %-11s %7s %10s %16s" % ("arm", "games", "won pos", "of those thrown"))

    rows = {}
    for label, logdir in ARMS:
        games, stamped = [], 0
        for f in sorted(glob.glob(os.path.join(logdir, "*.log"))):
            g = seat_games(f)
            stamped += sum(1 for k, _ in g if k)
            games.extend(g)
        if not games:
            print("  %-11s (no seat logs under %s)" % (label, logdir))
            continue
        n_won = n_thrown = 0
        for key, scores in games:
            t = trajectory(scores)
            if t is None:
                continue
            n_won += 1
            n_thrown += 1 if t else 0
        rows[label] = (len(games), n_won, n_thrown)
        print("  %-11s %7d %9.1f%% %15.1f%%"
              % (label, len(games),
                 100.0 * n_won / len(games) if games else 0.0,
                 100.0 * n_thrown / n_won if n_won else 0.0))

    if len(rows) == 2:
        (gc, wc, tc), (gr, wr, tr) = rows["candidate"], rows["reference"]
        dthrow = (100.0 * tc / wc if wc else 0) - (100.0 * tr / wr if wr else 0)
        dwon   = (100.0 * wc / gc if gc else 0) - (100.0 * wr / gr if gr else 0)
        print()
        print("  throw rate   candidate minus reference: %+.1f points   (want NEGATIVE)" % dthrow)
        print("  won-position candidate minus reference: %+.1f points   (must NOT fall: a term"
              % dwon)
        print("               that just makes the engine passive shows up here, not above)")
        if not outcomes:
            print()
            print("  No stream found, so nothing is joined to real outcomes -- these are the")
            print("  engine's own opinions of its own games, which is what the throw rate is.")


main()
