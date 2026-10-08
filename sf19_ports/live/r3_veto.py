"""R3, closed on frozen data: does the sticky loop lock ever HURT?

DEVIATION FROM THE PRE-REGISTRATION, STATED UP FRONT. r3_control.py was written
to compare locked games against UNLOCKED games in the same window. The bot stopped
playing on 2026-09-28 00:09 with R3 at 26 of 40 locked games, and in the frozen data
only 6 unlocked games carry an attributable result -- the control arm is empty, so
that comparison cannot be run at any power. This asks the narrower question the veto
actually needs, using a denominator that needs no per-seat alignment: the locked
games' outcomes against EVERY game played in the same post-ship window, taken from
the status log's own deduplicated Result lines.

Still observational, still veto-only: locked games are self-selected into repetition,
so a draw excess is expected. An excess of LOSSES is the only adverse finding.
"""

import os
import datetime
import lockrecord
import r3_control

LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stockfish")
NOCAP = "without a capture"


def main():
    ship = datetime.datetime.fromtimestamp(os.stat(ENGINE).st_mtime)
    res = [r for r in r3_control.results_dated() if r[0] >= ship]

    w = sum(1 for r in res if r[1] == "WIN")
    l = sum(1 for r in res if r[1] == "LOSS")
    d = sum(1 for r in res if r[1] == "DRAW")
    n = len(res)
    print("-----")
    print("BASELINE -- every game in the post-ship window (%s onward)" % ship.strftime("%m-%d %H:%M"))
    print("  n=%d   W %d (%.1f%%)   L %d (%.1f%%)   D %d (%.1f%%)"
          % (n, w, 100.0 * w / n, l, 100.0 * l / n, d, 100.0 * d / n))

    # locked games: detected from seat logs, attributed where a result aligns,
    # plus the rules deduction (a game that ENDS inside the lock can only have
    # ended by the 200-ply no-capture rule -- no capture is possible in a lock).
    locked, deduced, unknown = [], 0, 0
    for name in sorted(f for f in os.listdir(LOGS) if f.startswith("seat_") and f.endswith(".log")):
        path = os.path.join(LOGS, name)
        st = os.stat(path)
        born = datetime.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))
        if born < ship:
            continue
        segs = lockrecord.segments(path)
        if not segs:
            continue
        dead = datetime.datetime.fromtimestamp(st.st_mtime)
        window = [r for r in res if born <= r[0] <= dead]
        aligned = window if len(window) in (len(segs), len(segs) - 1) else None
        for i, s in enumerate(segs):
            cand = [r["depth"] == 1 and r["nodes"] < 1000 for r in s]
            run = best = 0
            for c in cand:
                run = run + 1 if c else 0
                best = max(best, run)
            if best < lockrecord.MIN_RUN:
                continue
            lock = [r for r, c in zip(s, cand) if c]
            ended_in_lock = s[-1]["ply"] == lock[-1]["ply"]
            out = aligned[i][1] if aligned and i < len(aligned) else None
            if out is None and ended_in_lock:
                out, _ = "DRAW", 0
                deduced += 1
            if out is None:
                unknown += 1
                continue
            locked.append(out)

    ln = len(locked)
    print("-----")
    print("LOCKED games")
    print("  detected %d | attributed %d (of which %d by the rules deduction) | unknown %d"
          % (ln + unknown, ln, deduced, unknown))
    if ln:
        lw = locked.count("WIN")
        ll = locked.count("LOSS")
        ld = locked.count("DRAW")
        print("  n=%d   W %d (%.1f%%)   L %d (%.1f%%)   D %d (%.1f%%)"
              % (ln, lw, 100.0 * lw / ln, ll, 100.0 * ll / ln, ld, 100.0 * ld / ln))
        print("-----")
        base_l = 100.0 * l / n
        got_l = 100.0 * ll / ln
        print("THE VETO TEST: loss share, locked %.1f%% vs baseline %.1f%%" % (got_l, base_l))
        if got_l > base_l:
            print("  ADVERSE: locked games lose MORE often than the bot's average.")
            print("  (check n before believing it -- %d locked games)" % ln)
        else:
            print("  NO VETO: locked games do not lose more often than average.")
        print("  Draws are EXPECTED to be high here (selection), so the draw share")
        print("  is not evidence either way.")
    print("-----")
    print("UNDERPOWERED BY DESIGN STOP: R3 wanted 40 locked games and the bot stopped")
    print("at 26. Treat this as 'no red flag found', never as a measured gain.")


if __name__ == "__main__":
    main()
