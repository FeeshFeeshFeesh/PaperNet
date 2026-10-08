"""
The user's example, traced: the 09:32:58 LOSS vs Mocca, "reached the corner".

Walks the rendered status trace for that one game and prints, per position, the
ply, our eval, our material and theirs, and the no-capture counter -- so the
question "did we sell material near the capture clock?" is answered from the
record rather than from theory.

No sys.argv.
"""
import re, subprocess
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

STATUS = _ROOT + "/project_files/papernet-status.log"
END_MARK = "Result: LOSS vs Mocca (Elo 273), reached the corner"
SECRET = re.compile(r"token|key|secret", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*m")

PLY    = re.compile(r"ply (\d+) of \d+")
EVAL   = re.compile(r"eval (mate-?\d+|[-+]?\d+)")
PIECES = re.compile(r"pieces: me R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+"
                    r"them R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+no capture for (\d+)/(\d+)")
CLOCKS = re.compile(r"clocks: me (\d+):(\d+), them (\d+):(\d+)")

lines = []
for ln in open(STATUS, errors="replace"):
    if SECRET.search(ln):
        continue
    lines.append(ANSI.sub("", ln.rstrip("\n")))

end = max(i for i, l in enumerate(lines) if END_MARK in l)
start = max(i for i, l in enumerate(lines[:end]) if "New game starting" in l)
print("game spans trace lines %d..%d (%d lines)" % (start, end, end - start))

recs, cur = [], {}
for l in lines[start:end + 1]:
    m = PLY.search(l)
    if m:
        cur = {"ply": int(m.group(1))}
    m = EVAL.search(l)
    if m and cur:
        cur["eval"] = m.group(1)
    m = CLOCKS.search(l)
    if m and cur:
        cur["clk"] = int(m.group(1)) * 60 + int(m.group(2))
    m = PIECES.search(l)
    if m and cur:
        g = [int(x) for x in m.groups()]
        cur.update(me=g[3], them=g[7], nc=g[8],
                   meR=g[0], meP=g[1], meS=g[2],
                   thR=g[4], thP=g[5], thS=g[6])
        recs.append(cur)
        cur = {}

print("parsed %d positions\n" % len(recs))
if not recs:
    raise SystemExit

# Every point where material or the no-capture counter changed.
print("EVENTS -- material changes and capture-clock resets")
print("%6s %10s %5s %5s %8s  %s" % ("ply", "eval", "me", "them", "no-cap", "what"))
prev = None
for r in recs:
    why = []
    if prev:
        if r["me"] != prev["me"]:
            why.append("WE LOST A PIECE (%d->%d)" % (prev["me"], r["me"]))
        if r["them"] != prev["them"]:
            why.append("they lost a piece (%d->%d)" % (prev["them"], r["them"]))
        if r["nc"] < prev["nc"]:
            why.append("CAPTURE CLOCK RESET (was %d)" % prev["nc"])
    if why:
        print("%6d %10s %5d %5d %5d/200  %s"
              % (r["ply"], r.get("eval", "?"), r["me"], r["them"], r["nc"], "; ".join(why)))
    prev = r

# How high did the capture clock actually get, and what was the eval up there?
print("\nHOW CLOSE DID WE GET TO THE 200-PLY DRAW?")
peak = max(recs, key=lambda r: r["nc"])
print("  highest no-capture counter reached: %d/200 at ply %d, eval %s, me %d them %d"
      % (peak["nc"], peak["ply"], peak.get("eval", "?"), peak["me"], peak["them"]))
hi = [r for r in recs if r["nc"] >= 150]
print("  positions at counter >= 150: %d" % len(hi))
if hi:
    print("  %6s %10s %5s %5s %8s" % ("ply", "eval", "me", "them", "no-cap"))
    for r in hi:
        print("  %6d %10s %5d %5d %5d/200"
              % (r["ply"], r.get("eval", "?"), r["me"], r["them"], r["nc"]))

# ---------------------------------------------------------------- the horizon
# Hypothesis: the eval collapses to 0 not because material is damped, but
# because the forced 200-ply draw ENTERS THE SEARCH HORIZON. Once every line
# reaches the drawn terminal node, the root score IS the draw score.
# Prediction: |eval| ~ 0 exactly when (200 - counter) <= depth.
DEPTH = re.compile(r"depth (\d+)")
recs2, cur = [], {}
for l in lines[start:end + 1]:
    m = PLY.search(l)
    if m:
        cur = {"ply": int(m.group(1))}
    if cur:
        m = EVAL.search(l)
        if m:
            cur["eval"] = m.group(1)
        m = DEPTH.search(l)
        if m:
            cur["depth"] = int(m.group(1))
    m = PIECES.search(l)
    if m and cur:
        g = [int(x) for x in m.groups()]
        cur.update(me=g[3], them=g[7], nc=g[8])
        if "eval" in cur and "depth" in cur:
            recs2.append(cur)
        cur = {}

print("\nHORIZON TEST -- does the draw entering the search explain the collapse?")
print("Only positions where we were a piece UP (me > them), eval numeric.")
print("%6s %6s %7s %8s %9s %8s" % ("ply", "no-cap", "to draw", "depth", "in horizon", "eval"))
rows2 = [r for r in recs2 if r["me"] > r["them"] and not r["eval"].startswith("mate")]
inh, outh = [], []
for r in rows2:
    to_draw = 200 - r["nc"]
    ev = abs(int(r["eval"]))
    # depth 245 is the mate-report sentinel, not a real depth
    d = r["depth"] if r["depth"] < 200 else 0
    seen = to_draw <= d
    (inh if seen else outh).append(ev)
for r in rows2:
    if r["nc"] >= 140:
        to_draw = 200 - r["nc"]
        d = r["depth"] if r["depth"] < 200 else 0
        print("%6d %6d %7d %8d %9s %8s"
              % (r["ply"], r["nc"], to_draw, d, "YES" if to_draw <= d else "no", r["eval"]))


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


print("\n  draw INSIDE  the horizon: n=%3d  mean |eval| = %6.1f cp" % (len(inh), mean(inh)))
print("  draw OUTSIDE the horizon: n=%3d  mean |eval| = %6.1f cp" % (len(outh), mean(outh)))
print("  (both groups are positions where WE WERE A PIECE UP)")
