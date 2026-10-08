"""Idea #2's free follow-up: when the untouchable relation is CREATED, what did the engine think?

The probe (UNTOUCHABLE_PROBE_RESULT.md) settled that the net knows: making one of your piece types
hit zero while the enemy still holds its prey is worth -854 cp to it, 16x the noise floor, and the
control with no prey on the board comes out slightly POSITIVE.  That closed "add untouchability as
a feature or a label".  But it opened a sharper question, because the two readings need opposite
fixes and cost different amounts:

  SEARCH TARGET.  The engine walks into the trade anyway -- it sits beyond the horizon, or the
                  search overrides an eval that was screaming.  Then the victim's own score should
                  FALL SHARPLY across the transition.  Worth real work on trade ordering.
  SYMPTOM.        The transition happens in games that were already going nowhere: by the time it
                  is reachable it is forced, and the census's own caveat ("observational, not
                  causal") was the right reading.  Then the score is ALREADY NEAR ZERO before it,
                  and a conversion fix aimed here buys nothing.

This plays no games and spends nothing.  It reads games we already have.

THE JOIN, which is the only fiddly part.  The referee streams record every move but their `info`
field is null throughout -- no engine scores.  The seat logs record `ply=... score=... best=...`
but not the board.  Neither alone is enough.  They join on the move: a seat plays ONE colour, so
its plies are single-parity, and its `best=` at each ply equals the move the stream recorded there.
Matching on the first MATCH_PLIES moves of the right parity identifies the game and the colour.

Score sign: `score=` is UCI convention, from the side to move.  A victim's score read at the
victim's own plies is therefore already in the victim's frame and needs no flip.

No sys.argv: constants below, run straight from PyCharm.
"""
import collections
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
A = os.path.join(ROOT, "sf19_ports", "data", "conversion_archive")

# (stream, seat-log dirs).  These two local A/Bs kept their seat logs.  The cloud SPRT runs kept
# theirs too -- they were pulled off rps-w1 before it was deleted and live in
# sf19_ports/data/w1_match_archive/ ; wdl_calibration.py reads them.
SETS = [("ab_nsdeep_eval.jsonl", ["ab_nsdeep_fast_logs", "ab_nsdeep_slow_logs"]),
        ("ab_nsdeeper_eval.jsonl", ["ab_nsdeeper_fast_logs", "ab_nsdeeper_slow_logs"])]
MATCH_PLIES = 4          # moves of one parity needed to identify a game; 3 already unique, 4 is slack
WINDOW = 3               # plies of the victim's own either side of the transition to average
CP = 1                   # seat scores are already centipawns

ROCK, PAPER, SCISSORS = 0, 1, 2
BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}
NAME = {ROCK: "rock", PAPER: "paper", SCISSORS: "scissors"}
RX = re.compile(r"ply=(\d+).*?score=(-?\d+)\s+best=(\S+)")


def mirror_anti(sq):
    rank, file = divmod(sq, 9)
    return (8 - file) * 9 + (8 - rank)


def initial():
    board = [None] * 81
    for piece, squares in ((ROCK, (28, 20, 12)), (PAPER, (37, 29, 21, 13)),
                           (SCISSORS, (38, 30, 22))):
        for sq in squares:
            board[sq] = (0, piece)
            board[mirror_anti(sq)] = (1, piece)
    return board


def parse(tok):
    t = tok.strip()
    if t and t[0] in "RPSrps" and len(t) > 5:
        t = t[1:]
    sep = "x" if "x" in t else "-"
    if sep not in t:
        return None
    a, b = t.split(sep, 1)
    try:
        return ("abcdefghi".index(a[0]) + (int(a[1:]) - 1) * 9,
                "abcdefghi".index(b[0]) + (int(b[1:]) - 1) * 9)
    except (ValueError, IndexError):
        return None


def read_stream(path):
    games = {}
    for line in open(path):
        d = json.loads(line)
        k = (d.get("pair"), d.get("game"))
        e = d.get("event")
        if e == "start":
            games[k] = {"opening": d["opening"], "moves": {}}
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


def transitions(game):
    """Replay; yield (ply, victim_side, type) where victim's count of `type` hits 0 with prey up."""
    board = initial()
    for i, tok in enumerate(game["opening"]):
        mv = parse(tok)
        if mv is None:
            return None
        board[mv[1]], board[mv[0]] = board[mv[0]], None
    events = []
    for ply in sorted(game["moves"]):
        mv = parse(game["moves"][ply])
        if mv is None:
            return None
        frm, to = mv
        src = board[frm]
        if src is None or src[0] != ply % 2:
            return None                       # replay disagrees with the stream: drop the game
        victim = board[to]
        board[to], board[frm] = src, None
        if victim is None or victim[0] == src[0]:
            continue
        vside, vtype = victim
        if any(c and c[0] == vside and c[1] == vtype for c in board):
            continue                          # not their last one
        # their last `vtype` just died.  Is the prey it used to hunt still on the board?
        if any(c and c[0] != vside and c[1] == BEATS[vtype] for c in board):
            events.append((ply, vside, vtype))
    return events


def main():
    norm = lambda s: s.replace("-", "")
    rows, joined, replayed, total = [], 0, 0, 0
    for stream, dirs in SETS:
        spath = os.path.join(A, stream)
        if not os.path.exists(spath):
            print("skip %s (absent)" % stream)
            continue
        games = read_stream(spath)
        total += len(games)
        index = {}
        for k, g in games.items():
            for par in (0, 1):
                ps = [p for p in sorted(g["moves"]) if p % 2 == par][:MATCH_PLIES]
                if len(ps) == MATCH_PLIES:
                    index[tuple((p, norm(g["moves"][p])) for p in ps)] = (k, par)
        scores = {}                            # (game key, side) -> {ply: score}
        for d in dirs:
            for path in sorted(glob.glob(os.path.join(A, d, "*"))):
                for seg in seat_segments(path):
                    if len(seg) < MATCH_PLIES:
                        continue
                    hit = index.get(tuple((p, b) for p, _, b in seg[:MATCH_PLIES]))
                    if hit is None:
                        continue
                    joined += 1
                    scores[(hit[0], hit[1])] = {p: s for p, s, _ in seg}
        for k, g in games.items():
            ev = transitions(g)
            if ev is None:
                continue
            replayed += 1
            winner, end = g.get("end", (None, None))
            for ply, vside, vtype in ev:
                own = scores.get((k, vside))
                if not own:
                    continue
                before = [own[p] for p in sorted(own) if p < ply][-WINDOW:]
                after = [own[p] for p in sorted(own) if p > ply][:WINDOW]
                if not before or not after:
                    continue
                rows.append({"stream": stream, "ply": ply, "type": vtype,
                             "before": sum(before) / len(before), "after": sum(after) / len(after),
                             "drawn": winner is None, "end": end})
    print("games %d, replayed cleanly %d, seat segments joined %d" % (total, replayed, joined))
    print("transition events with scores on both sides: %d" % len(rows))
    if not rows:
        return
    print("-----")
    print("%-22s %6s %9s %9s %9s" % ("bucket", "n", "before", "after", "drop"))

    def show(label, sel):
        s = [r for r in rows if sel(r)]
        if not s:
            return
        b = sum(r["before"] for r in s) / len(s)
        a = sum(r["after"] for r in s) / len(s)
        print("%-22s %6d %9.1f %9.1f %9.1f" % (label, len(s), b * CP, a * CP, (b - a) * CP))

    show("ALL", lambda r: True)
    show("  game drawn", lambda r: r["drawn"])
    show("  game decisive", lambda r: not r["drawn"])
    for t in (ROCK, PAPER, SCISSORS):
        show("  lost last " + NAME[t], lambda r, t=t: r["type"] == t)
    print("-----")
    mag = [abs(r["before"]) for r in rows]
    mag.sort()
    med = mag[len(mag) // 2]
    near = sum(1 for m in mag if m <= 50)
    print("|score| just BEFORE the transition: median %.0f cp, %.1f%% within +-50 cp"
          % (med * CP, 100.0 * near / len(mag)))
    drops = sorted(r["before"] - r["after"] for r in rows)
    print("drop across it: median %.0f cp, p90 %.0f cp, share >200 cp %.1f%%"
          % (drops[len(drops) // 2] * CP, drops[int(0.9 * (len(drops) - 1))] * CP,
             100.0 * sum(1 for d in drops if d * CP > 200) / len(drops)))
    print("-----")
    print("Already near zero before it => SYMPTOM, and a conversion fix aimed here buys nothing.")
    print("Sharp drop across it        => SEARCH walked into what its own eval hates.")
    print("The -854 cp the probe measured is what a net that HATED it in advance would show.")


if __name__ == "__main__":
    main()
