"""Emit one line per newly finished PaperNet2 game, with its verdict, plus liveness alarms.

The seat logs are the only local record of results (connect2.out never prints them). A game
boundary is a ply that is lower than the previous one; the final score classifies it:
  score=mateN  -> we walked in a forced win        score=-mateN -> we were mated
  last ply>=190 and score 0 -> capture-clock draw  anything else -> abort/loss
State lives in watch_games.pos so a re-armed watcher never re-emits a game it already reported.
No sys.argv: edit the constants and run it.
"""
import json, os, re, subprocess, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

LOGDIR = _ROOT + "/sf19_ports/live/logs"
POS    = os.path.join(_WORK, "watch_games.pos")
PERIOD = 60
STALE_S = 900          # ignore seat logs untouched for this long: they are finished sessions
LINE   = re.compile(r" ply=(\d+) .*score=(-?[a-z0-9]+) ")

def games(path):
    out, cur = [], []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                m = LINE.search(line)
                if not m:
                    continue
                p, s = int(m.group(1)), m.group(2)
                if cur and p < cur[-1][0]:
                    out.append(cur); cur = []
                cur.append((p, s))
    except OSError:
        return []
    return out                       # finished games only; the trailing `cur` is still in play

def verdict(g):
    lp, ls = g[-1]
    peak = 0
    for _, s in g:
        v = 3000 if s.startswith("mate") else (-3000 if s.startswith("-mate") else 0)
        if not s.startswith("mate") and not s.startswith("-mate"):
            try: v = int(s)
            except ValueError: v = 0
        peak = max(peak, v)
    if ls.startswith("mate"):  return "WIN  in %d plies" % lp
    if ls.startswith("-mate"): return "LOSS mated at ply %d" % lp
    if lp >= 190:
        tag = "  <-- THREW AWAY A WIN (peak %+d)" % peak if peak > 200 else ""
        return "DRAW on the capture clock, %d plies, peak eval %+d%s" % (lp, peak, tag)
    return "LOSS/abort at ply %d" % lp

state = json.load(open(POS)) if os.path.exists(POS) else {"seen": {}, "alive": True}
seen  = state["seen"]
while True:
    for name in sorted(os.listdir(LOGDIR)):
        if not name.startswith("seat_") or not name.endswith(".log"):
            continue
        # A live seat writes constantly. Stale logs from earlier sessions are still on disk and
        # would otherwise be reported as if their games had just been played.
        if time.time() - os.path.getmtime(os.path.join(LOGDIR, name)) > STALE_S:
            continue
        gs = games(os.path.join(LOGDIR, name))
        done = len(gs)
        prev = seen.get(name, 0)
        if done > prev:
            for g in gs[prev:]:
                print("%s game %d: %s" % (name[5:-4], gs.index(g) + 1, verdict(g)), flush=True)
            seen[name] = done
    up = subprocess.run(["pgrep", "-f", "connect_bot2.py"], capture_output=True).returncode == 0
    if state["alive"] and not up:
        print("PaperNet2 IS OFFLINE: connect_bot2.py exited", flush=True)
    state["alive"] = up
    json.dump(state, open(POS, "w"))
    time.sleep(PERIOD)
