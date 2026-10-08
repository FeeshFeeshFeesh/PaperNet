import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

"""Who leaves the repetition?

The user's definition: a loop is the board returning to the same state over and
over; the side that BREAKS it is the one whose move first departs from the
cycle -- not the side that later makes the capture.

Snapshots exist only after OUR move, so one snapshot = 2 plies and a shuffle
shows up as period p >= 2 in snapshot space.  After the last on-cycle board the
order of events is THEIR move, then OURS, so their deviation is checked first.
"""
import sys
sys.path.insert(0, os.path.dirname(HERE))
import live_boards as lb

MIN_VISITS = 3          # the cycle must be seen at least 3 times to count
MAX_P      = 6          # periods to look for, in snapshots
HIGH       = 100        # "heading for the draw": counter at least this

def key(rec):
    return tuple(tuple(rec["rows"][r]) for r in range(1, 10))

def loops(snaps):
    """Yield (start, last_on_cycle, p) for maximal repetitions."""
    n = len(snaps)
    ks = [key(s) for s in snaps]
    i = 0
    out = []
    while i < n:
        best = None
        for p in range(1, MAX_P + 1):
            j = i + p
            while j < n and ks[j] == ks[j - p]:
                j += 1
            reps = (j - i) // p                     # visits to each cycle state
            if reps >= MIN_VISITS and (best is None or j - 1 > best[1]):
                best = (i, j - 1, p)
        if best:
            out.append(best)
            i = best[1] + 1
        else:
            i += 1
    return out

def main():
    games = lb.parse()
    rows = []
    for gi, g in enumerate(games):
        sn = g["plies"]
        if len(sn) < 10:
            continue
        for (i, j, p) in loops(sn):
            peak = max((sn[k].get("counter") or 0) for k in range(i, j + 1))
            if peak < HIGH:
                continue
            b = j + 1                               # the off-cycle board
            if b >= len(sn) or j - p < 0 or b - p < 0:
                continue
            their  = sn[j].get("their_reply")
            theirc = sn[j - p].get("their_reply")
            our    = sn[b].get("our_move")
            ourc   = sn[b - p].get("our_move")
            if their and theirc and their != theirc:
                who = "them"
            elif our and ourc and our != ourc:
                who = "us"
            elif not (their and theirc and our and ourc):
                who = "unknown"
            else:
                who = "neither?"
            # who makes the next capture, from the counter reset
            nxt = None
            for k in range(b, min(b + 12, len(sn))):
                c = sn[k].get("counter")
                if c is not None and c <= 1:
                    nxt = "us" if c == 0 else "them"
                    break
            after = sn[min(b + 5, len(sn) - 1)]
            rows.append(dict(
                game=gi, opp=g["opponent"], result=g["result"], reason=g["reason"],
                p=p, visits=(j - i + p) // p, ply=sn[b]["ply"], counter=sn[b].get("counter"),
                peak=peak, who=who, capturer=nxt,
                mine=sn[j].get("mine"), theirs=sn[j].get("theirs"),
                mine_after=after.get("mine"), theirs_after=after.get("theirs"),
                ev=sn[j].get("eval"), depth=sn[j].get("depth"),
                their_mv=their, their_cyc=theirc, our_mv=our, our_cyc=ourc,
            ))
    return rows

if __name__ == "__main__":
    rows = main()
    print("long loops reaching counter >= %d: %d" % (HIGH, len(rows)))
    from collections import Counter
    print("-----who leaves the cycle-----")
    for w, n in Counter(r["who"] for r in rows).most_common():
        print("  %-9s %d" % (w, n))
    print("-----periods-----")
    for p, n in sorted(Counter(r["p"] for r in rows).items()):
        print("  p=%d  %d" % (p, n))
    import json, io
    io.open(os.path.join(HERE, "breaks.json"), "w").write(json.dumps(rows))
    print("confirm")
