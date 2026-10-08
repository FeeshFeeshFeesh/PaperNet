"""The live record, from the status log's Result lines.

THREE traps, all of which corrupted my first pass at this:

  1. The status log is a REDRAWN terminal display. Every Result line is emitted
     many times over (385 raw lines for 156 games), each redraw ending in a
     \\x1b[K erase-to-end-of-line. Counting raw lines inflates the record.
  2. There are TWO result formats, and the older one carries no Elo:
       A  HH:MM:SS  Result: WIN vs hushlite (Elo 281), reached the corner. My Elo 290 (+0).
       B  HH:MM:SS  Result: WIN (resignation) vs Psionic, 143 plies, tc 60+1.
     Matching only A silently dropped 173 lines / a whole era of games.
  3. The ANSI stripper must take every CSI sequence, not just the colour ones
     that end in 'm' -- \\x1b[K survives a `\\x1b\\[[0-9;]*m` regex and breaks
     an anchored match.

Dedup collapses only ADJACENT identical records, which is exactly the redraw
case and cannot merge two genuinely different games.
"""

import math
import re
from collections import Counter, defaultdict
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

STATUS_LOG = _ROOT + "/project_files/papernet-status.log"

CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
SECRET = re.compile(r"token|key|secret", re.I)

FORM_A = re.compile(
    r"^(\d\d:\d\d:\d\d)\s+Result: (\w+) vs (\S+) \(Elo (\d+)\), ([^.]+)\.\s*My Elo (\d+)"
)
FORM_B = re.compile(
    r"^(\d\d:\d\d:\d\d)\s+Result: (\w+) \(([^)]+)\) vs ([^,]+), (\d+) plies"
)

NOCAP = "without a capture"
POINTS = {"WIN": 1.0, "DRAW": 0.5, "LOSS": 0.0}


def games(path=STATUS_LOG):
    seq = []
    for line in open(path, errors="replace"):
        if SECRET.search(line):
            continue
        s = CSI.sub("", line).strip()
        if "Result: " not in s:
            continue
        m = FORM_A.match(s)
        if m:
            seq.append((m.group(1), m.group(2), m.group(3), m.group(5).strip(),
                        int(m.group(6)), None))
            continue
        m = FORM_B.match(s)
        if m:
            seq.append((m.group(1), m.group(2), m.group(4).strip(),
                        m.group(3).strip(), None, int(m.group(5))))
    # collapse redraws
    return [r for i, r in enumerate(seq) if i == 0 or r != seq[i - 1]]


def elo(p):
    if p <= 0 or p >= 1:
        return float("inf") if p >= 1 else float("-inf")
    return -400.0 * math.log10(1.0 / p - 1.0)


if __name__ == "__main__":
    gs = games()
    n = len(gs)
    score = sum(POINTS[g[1]] for g in gs) / n
    print("%d games  %dW %dD %dL   score %.4f  (Elo %+.0f)"
          % (n, sum(g[1] == "WIN" for g in gs), sum(g[1] == "DRAW" for g in gs),
             sum(g[1] == "LOSS" for g in gs), score, elo(score)))
    print("  (raw Result lines in the log: %d -- the rest are terminal redraws)"
          % sum(1 for l in open(STATUS_LOG, errors="replace")
                if "Result: " in CSI.sub("", l)))

    print("\nby reason")
    by = defaultdict(Counter)
    for g in gs:
        by[g[3]][g[1]] += 1
    for reason, c in sorted(by.items(), key=lambda kv: -sum(kv[1].values())):
        t = sum(c.values())
        print("  %-32s %3dW %3dD %3dL   n=%3d  %5.1f%%"
              % (reason, c["WIN"], c["DRAW"], c["LOSS"], t, 100.0 * t / n))

    print("\nby opponent (nc = the 200-ply no-capture draw)")
    opp = defaultdict(Counter)
    for g in gs:
        opp[g[2]][g[1]] += 1
        if NOCAP in g[3]:
            opp[g[2]]["nc"] += 1
    rows = []
    for name, c in opp.items():
        t = c["WIN"] + c["DRAW"] + c["LOSS"]
        s = (c["WIN"] + 0.5 * c["DRAW"]) / t
        rows.append((s, name, c["WIN"], c["DRAW"], c["LOSS"], t, c["nc"]))
    for s, name, w, d, l, t, nc in sorted(rows):
        print("  %-18s %2d-%2d-%2d  n=%3d  score %.3f   nc %2d (%5.1f%%)"
              % (name, w, d, l, t, s, nc, 100.0 * nc / t))

    nc = sum(1 for g in gs if NOCAP in g[3])
    print("\nno-capture draws: %d of %d games = %.1f%%" % (nc, n, 100.0 * nc / n))
    base = sum(POINTS[g[1]] for g in gs)
    for frac in (0.25, 0.5, 1.0):
        s2 = (base + 0.5 * frac * nc) / n
        print("  convert %3d%% of them -> score %.4f  (Elo %+.0f, %+.0f)"
              % (100 * frac, s2, elo(s2), elo(s2) - elo(score)))

    plies = [g[5] for g in gs if g[5] is not None]
    if plies:
        plies.sort()
        print("\nply counts (format-B games only, n=%d): median %d, p90 %d, max %d"
              % (len(plies), plies[len(plies) // 2], plies[int(0.9 * len(plies))],
                 plies[-1]))
