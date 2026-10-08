"""⚠ THE REGIME SPLIT IN THIS FILE IS BUGGY -- USE clock_audit2.py INSTEAD (found 2026-09-27).

The `for cand_ov in (150, 500): ... break` assignment below is wrong on any row where budget() does
not depend on ov: capped at MAX_MOVE_MS, below LOW_CLOCK_MS (the panic branch ignores ov), or clamped
by `safe`. Both candidates match such a row, so it is labelled 150 whatever it was -- 21.8% of all
rows. The 150 bucket absorbs both regimes' ambiguous rows; the 500 bucket keeps only discriminating,
i.e. low-clock, rows. Its "budget maxed 0.0%" was therefore true by construction (really 17.1%).
The fixed-point result at OV=150 (p05 3,017 vs predicted 3,000) SURVIVES -- corrected it is 3,009.
clock_audit2.py assigns ov per SEAT from discriminating rows only, which is sound because
MOVE_OVERHEAD_MS is read once at seat start. Kept unchanged as the provenance of the original table.
"""

"""
Did the live clock ever actually run us out of time, and did the 2026-09-25
MOVE_OVERHEAD_MS 150 -> 500 change move us off the panic boundary?

Zero games. Reads the seat logs, which carry both clocks, the increment, the
budget we chose and the time we really spent.

The clock is a FISCHER clock (inc 1000 ms), so game LENGTH cannot drain it on
its own: budget = 0.8*inc + clock/30 - OV has a fixed point at
clock* = 30*(OV - site_overhead + 0.2*inc). The question is only where that
fixed point sits relative to LOW_CLOCK_MS = 3000, below which the seat panics
and throttles itself to 0.5*inc.

No sys.argv -- everything is set here.
"""
import glob, os, re, datetime
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

LOG_GLOB = _ROOT + "/sf19_ports/live/logs/seat_*.log"
SECRET   = re.compile(r"token|key|secret", re.I)   # never read a credential line
LOW_CLOCK_MS   = 3000
PANIC_CLOCK_MS = 800
MAX_MOVE_MS    = 4000
MIN_MOVE_MS    = 250

LINE = re.compile(
    r"rtime (\d+) btime (\d+) rinc (\d+) binc (\d+) \| go movetime (\d+)"
    r".*?ply=(\d+).*?time=(\d+)")


def budget(clock_ms, inc_ms, ov):
    if clock_ms < LOW_CLOCK_MS:
        return int(max(50.0, min(0.5 * inc_ms, clock_ms - PANIC_CLOCK_MS, MAX_MOVE_MS)))
    safe = clock_ms - 1500.0
    target = min(0.8 * inc_ms + clock_ms / 30.0 - ov, MAX_MOVE_MS, safe)
    return int(max(MIN_MOVE_MS, target))


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def quant(xs, q):
    if not xs:
        return 0
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


rows = []
for path in sorted(glob.glob(LOG_GLOB)):
    day = datetime.date.fromtimestamp(os.path.getmtime(path))
    for ln in open(path, errors="replace"):
        if SECRET.search(ln):
            continue
        m = LINE.search(ln)
        if not m:
            continue
        rt, bt, ri, bi, mv, ply, spent = (int(g) for g in m.groups())
        # Which clock is ours? The one that reproduces the budget we chose.
        ours, inc, ov = None, ri, None
        for clk, i in ((rt, ri), (bt, bi)):
            for cand_ov in (150, 500):
                if abs(budget(clk, i, cand_ov) - mv) <= 1:
                    ours, inc, ov = clk, i, cand_ov
                    break
            if ours is not None:
                break
        if ours is None:
            continue
        rows.append((day, path, ours, inc, mv, ply, spent, ov))

print("parsed %d moves from %d logs" % (len(rows), len(set(r[1] for r in rows))))
if not rows:
    raise SystemExit

# group by the overhead constant actually in force -- that is the regime
print("\nregime split by the MOVE_OVERHEAD_MS the budget implies")
hdr = ("%-6s %7s %8s %8s %8s %8s %8s %8s %8s"
       % ("OV", "moves", "clk p05", "clk med", "clk p95", "clk min",
          "<3000", "<800", "maxed"))
print(hdr)
for ov in (150, 500):
    sub = [r for r in rows if r[7] == ov]
    if not sub:
        continue
    clks = [r[2] for r in sub]
    print("%-6d %7d %8d %8d %8d %8d %7.1f%% %7.1f%% %7.1f%%"
          % (ov, len(sub), quant(clks, 0.05), quant(clks, 0.50), quant(clks, 0.95),
             min(clks), pct(sum(1 for c in clks if c < LOW_CLOCK_MS), len(sub)),
             pct(sum(1 for c in clks if c < PANIC_CLOCK_MS), len(sub)),
             pct(sum(1 for r in sub if r[4] >= MAX_MOVE_MS), len(sub))))

print("\nby day (all regimes)")
print("%-12s %7s %8s %8s %8s %8s %9s %9s"
      % ("day", "moves", "clk med", "clk min", "bud med", "spent med", "<3000", "overshoot"))
for day in sorted(set(r[0] for r in rows)):
    sub = [r for r in rows if r[0] == day]
    clks = [r[2] for r in sub]
    print("%-12s %7d %8d %8d %8d %8d %8.1f%% %8.1f%%"
          % (day, len(sub), quant(clks, 0.50), min(clks),
             quant([r[4] for r in sub], 0.50), quant([r[6] for r in sub], 0.50),
             pct(sum(1 for c in clks if c < LOW_CLOCK_MS), len(sub)),
             pct(sum(1 for r in sub if r[6] > r[4] + 50), len(sub))))

# Does game length correlate with clock at all? That is the user's mechanism.
print("\nclock vs ply -- the 'dragged out, lost on time' mechanism")
print("%-12s %7s %8s %8s %8s" % ("ply band", "moves", "clk med", "clk min", "<3000"))
for lo, hi in ((0, 100), (100, 200), (200, 300), (300, 500), (500, 10000)):
    sub = [r for r in rows if lo <= r[5] < hi]
    if not sub:
        continue
    clks = [r[2] for r in sub]
    print("%-12s %7d %8d %8d %7.1f%%"
          % ("%d-%d" % (lo, hi - 1 if hi < 10000 else 9999), len(sub),
             quant(clks, 0.50), min(clks),
             pct(sum(1 for c in clks if c < LOW_CLOCK_MS), len(sub))))

print("\nfixed point check: clock* = 30*(OV - 250 + 0.2*inc), inc=1000")
for ov in (150, 500):
    print("  OV=%-4d -> clock* = %6d ms   (LOW_CLOCK_MS = %d)"
          % (ov, 30 * (ov - 250 + 200), LOW_CLOCK_MS))

# ------------------------------------------------------------------ the split
# The pooled ply table mixes regimes and 86% of it predates the fix, so it
# cannot say whether long games STILL drain the clock. Split it.
print("\nply band x regime -- is the mechanism closed or only mitigated?")
print("%-12s %6s %8s %8s %8s   %6s %8s %8s %8s"
      % ("ply band", "n@150", "med", "min", "<3000", "n@500", "med", "min", "<3000"))
for lo, hi in ((0, 100), (100, 200), (200, 300), (300, 500), (500, 10000)):
    cells = []
    for ov in (150, 500):
        sub = [r for r in rows if lo <= r[5] < hi and r[7] == ov]
        if sub:
            c = [r[2] for r in sub]
            cells.append("%6d %8d %8d %7.1f%%" % (len(sub), quant(c, 0.50), min(c),
                         pct(sum(1 for x in c if x < LOW_CLOCK_MS), len(sub))))
        else:
            cells.append("%6s %8s %8s %8s" % ("-", "-", "-", "-"))
    print("%-12s %s   %s" % ("%d-%d" % (lo, hi - 1 if hi < 10000 else 9999),
                             cells[0], cells[1]))

print("\nhow long were the games in each regime? (max ply seen per log)")
for ov in (150, 500):
    per = {}
    for r in rows:
        if r[7] == ov:
            per[r[1]] = max(per.get(r[1], 0), r[5])
    if per:
        v = sorted(per.values())
        print("  OV=%-4d %2d logs, max ply per log: med %d, p90 %d, longest %d"
              % (ov, len(v), quant(v, 0.5), quant(v, 0.9), v[-1]))

print("\ndid we ever actually flag? lowest clocks seen")
low = sorted(rows, key=lambda r: r[2])[:6]
for r in low:
    print("  %s OV=%-4d ply %4d  clock %6d ms  budget %5d  spent %5d"
          % (r[0], r[7], r[5], r[2], r[4], r[6]))
