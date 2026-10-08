"""R3 control arm: locked games vs UNLOCKED games, in the same post-ship window.

Written BEFORE the 40th locked game landed, deliberately: every choice below is
fixed in advance so it cannot be motivated by the answer. SHOW_OUTCOMES starts
False so the plumbing can be debugged without seeing the comparison.

WHAT THIS CAN AND CANNOT SAY. The lock is not randomly assigned -- a game locks
because it entered a repetition, and repetition-prone games are already draw-prone.
So a higher draw share in the locked arm is EXPECTED and is NOT evidence that the
lock causes draws. This is observational; it can only VETO (if locked games carry
an excess of LOSSES, the lock is hurting) -- see screen-vs-sprt-harness.

Window: the live engine binary's mtime. Seats BORN at or after it provably ran the
lock-carrying bytes; seats born before it ran the old binary and are excluded.
Do NOT tune MIN_RUN / the instant-reply test: they are lockrecord.py's, unchanged.
"""

import os
import re
import datetime
import lockrecord                      # safe: it has an __main__ guard

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "stockfish")
LOGS = os.path.join(HERE, "logs")
STATUS = lockrecord.STATUS

SHOW_OUTCOMES = False       # flip to True once R3 has its 40 locked games
MIN_RUN = lockrecord.MIN_RUN
NOCAP = "without a capture"

RESULT = re.compile(r"^(\d\d):(\d\d):(\d\d)\s+Result: (WIN|LOSS|DRAW)[^,]*,\s*([^.]*)\.")
CSI = lockrecord.CSI


def results_dated():
    """Every Result line with a REAL datetime, not just HH:MM:SS.

    lockrecord.results() returns only the newest day, but the post-ship window
    spans two dates, and its own docstring warns that matching on HH:MM:SS alone
    pulls in results from other days. Day boundaries come from the clock running
    backwards; the newest day is the status log's own mtime date.
    """
    rows, prev, day, last_t = [], None, 0, -1
    if not os.path.exists(STATUS):
        return []
    for line in open(STATUS, errors="replace"):
        l = CSI.sub("", line).strip()
        m = RESULT.match(l)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        rec = (t, m.group(4), m.group(5).strip())
        if rec == prev:                                   # a display REDRAW
            continue
        if last_t >= 0 and t < last_t - 3600:
            day += 1
        last_t = t
        rows.append([day, t, m.group(4), m.group(5).strip()])
        prev = rec
    if not rows:
        return []
    newest = max(r[0] for r in rows)
    end = datetime.date.fromtimestamp(os.stat(STATUS).st_mtime)
    out = []
    for day, t, outcome, why in rows:
        d = end - datetime.timedelta(days=(newest - day))
        out.append((datetime.datetime.combine(d, datetime.time()) +
                    datetime.timedelta(seconds=t), outcome, why))
    return out


def main():
    ship = datetime.datetime.fromtimestamp(os.stat(ENGINE).st_mtime)
    res = results_dated()
    print("-----")
    print("live engine built (window opens) : %s" % ship)
    print("result lines, deduplicated       : %d" % len(res))
    print("of those, inside the window      : %d" % len([r for r in res if r[0] >= ship]))

    locked, unlocked, unaligned = [], [], 0
    seats = sorted(f for f in os.listdir(LOGS) if f.startswith("seat_") and f.endswith(".log"))
    considered = 0
    for name in seats:
        path = os.path.join(LOGS, name)
        st = os.stat(path)
        born = datetime.datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))
        if born < ship:                       # ran the pre-lock binary
            continue
        segs = lockrecord.segments(path)
        if not segs:
            continue
        considered += 1
        dead = datetime.datetime.fromtimestamp(st.st_mtime)
        window = [r for r in res if born <= r[0] <= dead]
        aligned = window if len(window) in (len(segs), len(segs) - 1) else None
        if aligned is None:
            unaligned += 1
        for i, s in enumerate(segs):
            cand = [r["depth"] == 1 and r["nodes"] < 1000 for r in s]
            run = best = 0
            for c in cand:
                run = run + 1 if c else 0
                best = max(best, run)
            rec = {"log": name, "seg": i, "plies": s[-1]["ply"],
                   "outcome": aligned[i][1] if aligned and i < len(aligned) else None,
                   "why": aligned[i][2] if aligned and i < len(aligned) else None}
            (locked if best >= MIN_RUN else unlocked).append(rec)

    print("-----")
    print("seats born in the window         : %d" % considered)
    print("  of those, results unalignable  : %d" % unaligned)
    print("games LOCKED                     : %d" % len(locked))
    print("games UNLOCKED (control)         : %d" % len(unlocked))
    tot = len(locked) + len(unlocked)
    if tot:
        print("lock rate                        : %.1f%%" % (100.0 * len(locked) / tot))
    for nm, arm in (("locked", locked), ("unlocked", unlocked)):
        known = [r for r in arm if r["outcome"]]
        print("  %-9s attributed %d of %d" % (nm, len(known), len(arm)))

    if not SHOW_OUTCOMES:
        print("-----")
        print("SHOW_OUTCOMES is False: plumbing only, the comparison is withheld")
        print("on purpose until R3 reaches 40 locked games.")
        return

    print("-----")
    for nm, arm in (("locked", locked), ("unlocked", unlocked)):
        known = [r for r in arm if r["outcome"]]
        n = len(known)
        if not n:
            print("%-9s no attributed games" % nm)
            continue
        w = sum(1 for r in known if r["outcome"] == "WIN")
        l = sum(1 for r in known if r["outcome"] == "LOSS")
        d = sum(1 for r in known if r["outcome"] == "DRAW")
        nc = sum(1 for r in known if r["outcome"] == "DRAW" and NOCAP in (r["why"] or ""))
        print("%-9s n=%-4d W %d (%.1f%%)  L %d (%.1f%%)  D %d (%.1f%%)  of which no-capture %d (%.1f%%)"
              % (nm, n, w, 100.0 * w / n, l, 100.0 * l / n, d, 100.0 * d / n, nc, 100.0 * nc / n))
    print("-----")
    print("READ THIS AS A VETO, NOT A GAIN: the arms are self-selected (see the")
    print("module docstring). An excess of LOSSES in the locked arm is the only")
    print("finding that would act against shipping the lock.")


if __name__ == "__main__":
    main()
