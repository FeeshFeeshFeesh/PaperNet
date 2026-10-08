"""R5 IS CLOSED AS UNMEASURABLE (2026-09-27). This file's parser was never fixed, on purpose.

R5 asked: make time management measurable. Both routes fail, and the second one fails on data, not
on opinion.

1. INTERNALLY. `bot eval` / `bot play` accept only --sims and --move-ms: no clock, no increment, no
   forfeit. Adding a clock would also destroy the property that makes a verdict affordable --
   sprt_pool.FROZEN.py pools pairs across hosts ONLY because every game is node-limited, so a pair's
   outcome cannot depend on which box played it. nps depends on the cache hierarchy, so clock-limited
   games from different hosts must never be pooled, and un-pooled verdicts cost several times more.

2. OBSERVATIONALLY, comparing the OV=150 and OV=500 seats that already played. r5_confound.py kills
   this: the regime is perfectly collinear with the date AND nearly perfectly with the engine.
   OV=500 is 81 of 81 games on sf2_wdl/best.nnue thr=6, all 09-26..09-27; OV=150 is 199 games spread
   over three nets, all 09-17..09-25, with ZERO temporal overlap. sf2_wdl is the WDL-labels net that
   shipped 09-25 at +33 Elo, so the comparison would mostly recover that net change and attribute it
   to the clock constant. The one overlapping stratum has 11 games against 81 -- a 95% interval near
   +-270 Elo -- and it straddles the sticky lock shipping on 09-26 as well.

So the regime-vs-outcome join is not worth building, and the broken regex below is moot. What DID
come out of this line of work is real and is kept: clock_audit2.py, which fixes a labelling bug in
clock_audit.py (see memory live-clock-overhead).
"""

"""Can we score game OUTCOMES by clock regime, offline, with zero games?

WHY THIS ASKS INSTEAD OF ANSWERING. R5 was framed as "make time management measurable in the
referee". That is not possible: sprt_pool.FROZEN.py earns the right to pool pairs across hosts
ONLY because every game is node-limited, so a pair's outcome is host-independent. A clock-limited
match breaks that (nps tracks the cache hierarchy), and pooling is what makes a cloud verdict cost
~A$1. So the referee stays node-limited, and the only honest route is observational: take the two
MOVE_OVERHEAD_MS regimes that already played on the live site and compare their results.

That comparison is CONFOUNDED and this script's job is to say by how much before anyone builds on
it. The regimes are separated in time, so they differ in opponent mix and in engine version -- the
sticky loop lock shipped 2026-09-26, AFTER the OV 150 -> 500 change on 09-25. If the lock and the
regime boundary coincide, no outcome difference can be attributed to either.

No sys.argv -- constants here, per CLAUDE.md.
"""
import glob, os, re
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

LOG_GLOB = _ROOT + "/sf19_ports/live/logs/seat_*.log"
SECRET   = re.compile(r"token|key|secret", re.I)      # never read a credential line
OV_SPLIT = 325          # ms; the two regimes are 150 and 500, so anything between separates them
MIN_MOVES = 20          # a seat with fewer scored moves cannot be assigned a regime

ROW = re.compile(r"ply=(\d+).*?clock=(\d+).*?budget=(\d+)")

def implied_ov(clock, budget, inc=1000):
    # budget = 0.8*inc + clock/30 - OV   ->   OV = 0.8*inc + clock/30 - budget
    return 0.8 * inc + clock / 30.0 - budget

seats = {}
for path in sorted(glob.glob(LOG_GLOB)):
    ovs, plies = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            if SECRET.search(line):
                continue
            m = ROW.search(line)
            if not m:
                continue
            ply, clock, budget = int(m.group(1)), int(m.group(2)), int(m.group(3))
            ov = implied_ov(clock, budget)
            if 0 <= ov <= 1200:            # drop capped/panic rows, where the formula does not apply
                ovs.append(ov)
            plies.append(ply)
    if len(ovs) >= MIN_MOVES:
        ovs.sort()
        med = ovs[len(ovs) // 2]
        games = 1 + sum(1 for a, b in zip(plies, plies[1:]) if b < a)
        seats[os.path.basename(path)] = (med, len(ovs), games, os.path.getmtime(path))

print("-----  seats assignable to a clock regime  -----")
lo = {k: v for k, v in seats.items() if v[0] < OV_SPLIT}
hi = {k: v for k, v in seats.items() if v[0] >= OV_SPLIT}
for tag, d in (("OV~150", lo), ("OV~500", hi)):
    g = sum(v[2] for v in d.values())
    mv = sum(v[1] for v in d.values())
    print("  %s : %3d seats  %4d games  %6d scored moves" % (tag, len(d), g, mv))
print("  unassignable seats (too few scored moves): %d" % (len(glob.glob(LOG_GLOB)) - len(seats)))

if lo and hi:
    newest_lo = max(v[3] for v in lo.values())
    oldest_hi = min(v[3] for v in hi.values())
    import datetime
    f = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
    print("-----  is the regime boundary separable from the sticky-lock ship date?  -----")
    print("  newest OV~150 seat : %s" % f(newest_lo))
    print("  oldest OV~500 seat : %s" % f(oldest_hi))
    print("  sticky loop lock shipped 2026-09-26 (see memory sticky-loop-lock)")
    print("  -> OV~500 seats that also postdate the lock are CONFOUNDED: two changes, one boundary.")
    lockday = datetime.datetime(2026, 9, 26).timestamp()
    clean = sum(v[2] for v in hi.values() if v[3] < lockday)
    print("  OV~500 games BEFORE the lock shipped (the only clean comparison): %d" % clean)
