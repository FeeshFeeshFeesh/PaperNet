"""
Section 13 found the horizon collapse in ONE game. How often does it happen?

Walks every game in the rendered status trace and counts positions where we are
materially AHEAD but the forced 200-ply draw is inside the search horizon, so the
root score is the draw score and the material edge is invisible.

That population is what a terminal-draw grade can address on the LIVE bot, as
distinct from the 50.2% of self-match games that end on the no-capture rule.

No sys.argv.
"""
import re
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

STATUS = _ROOT + "/project_files/papernet-status.log"
SECRET = re.compile(r"token|key|secret", re.I)
ANSI   = re.compile(r"\x1b\[[0-9;]*m")
PLY    = re.compile(r"ply (\d+) of \d+")
EVAL   = re.compile(r"eval (mate-?\d+|[-+]?\d+)")
DEPTH  = re.compile(r"depth (\d+)")
PIECES = re.compile(r"pieces: me R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+"
                    r"them R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+no capture for (\d+)/(\d+)")
RESULT = re.compile(r"Result: (\w+) vs (\S+)")

lines = []
for ln in open(STATUS, errors="replace"):
    if SECRET.search(ln):
        continue
    lines.append(ANSI.sub("", ln.rstrip("\n")))

games, cur_game, cur = [], [], {}
for l in lines:
    if "New game starting" in l:
        if cur_game:
            games.append((cur_game, None))
        cur_game, cur = [], {}
        continue
    m = RESULT.search(l)
    if m:
        games.append((cur_game, (m.group(1), m.group(2))))
        cur_game, cur = [], {}
        continue
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
            cur_game.append(cur)
        cur = {}
if cur_game:
    games.append((cur_game, None))

games = [g for g in games if len(g[0]) >= 20]
print("parsed %d games with >=20 recorded positions, %d positions total"
      % (len(games), sum(len(g[0]) for g in games)))

tot = blind = blind_ahead = ahead = 0
games_with = 0
per_result = {}
for recs, res in games:
    hit = 0
    for r in recs:
        if r["eval"].startswith("mate"):
            continue
        tot += 1
        d = r["depth"] if r["depth"] < 200 else 0
        inh = (200 - r["nc"]) <= d
        up = r["me"] > r["them"]
        if up:
            ahead += 1
        if inh:
            blind += 1
        if inh and up:
            blind_ahead += 1
            hit += 1
    if hit:
        games_with += 1
        key = res[0] if res else "unfinished"
        per_result[key] = per_result.get(key, 0) + 1


def pct(a, b):
    return 100.0 * a / b if b else 0.0


print("\nPOPULATION the terminal-draw grade can reach, on live games")
print("  positions scored                      %6d" % tot)
print("  ... where we are materially AHEAD     %6d  (%.1f%%)" % (ahead, pct(ahead, tot)))
print("  ... where the draw is IN the horizon  %6d  (%.1f%%)" % (blind, pct(blind, tot)))
print("  ... BOTH: ahead AND blind             %6d  (%.1f%%)" % (blind_ahead, pct(blind_ahead, tot)))
print("  games containing at least one such position: %d of %d (%.1f%%)"
      % (games_with, len(games), pct(games_with, len(games))))
print("  those games by result: %s" % (per_result or "-"))

print("\nmean abs eval while AHEAD, by whether the draw is in the horizon")
for label, want in (("outside", False), ("inside", True)):
    xs = []
    for recs, _ in games:
        for r in recs:
            if r["eval"].startswith("mate") or r["me"] <= r["them"]:
                continue
            d = r["depth"] if r["depth"] < 200 else 0
            if ((200 - r["nc"]) <= d) == want:
                xs.append(abs(int(r["eval"])))
    print("  draw %-8s n=%5d  mean %6.1f cp  median %5.1f"
          % (label, len(xs), sum(xs) / len(xs) if xs else 0,
             sorted(xs)[len(xs) // 2] if xs else 0))
