# R4 verdict, PRE/POST aware.  No sys.argv per CLAUDE.md.
#
# PERSPECTIVE, the thing that is easy to get backwards:
#   PRE  = position we chose from, US to move.    W = we win,  L = we are lost.
#   POST = position after our move, THEM to move. L = THEY are lost, i.e. WE still win.
#                                                 W = they win.
# So "we had a win and kept it" is PRE=W and POST=L.  "we threw it" is PRE=W and POST!=L.
#
# THE CLOCK, the other thing that is easy to get backwards.  Reads tb_meta3.txt, whose
# `counter` field is PER PHASE.  A ply record's own `counter` describes the board in `rows`,
# i.e. the POST position after our move -- proved on 26376 exact reconstructions, where
# counter == 0 in 483 of 483 records whose move captured and 0 of 25893 whose move did not.
# Using it for PRE understates the PRE clock by one when our move was quiet (conservative)
# and, when our move captured, replaces a clock as high as 199 with 0 (NOT conservative:
# it would call a dtz=150 win reachable inside the 200-ply no-capture limit when it is not).
# tb_genq3.py emits the PRE value directly; tb_queries3.txt came out byte-identical to the
# uploaded tb_queries2.txt, so the nid -> position mapping is proven unchanged.
import os, collections

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S       = _ROOT + "/tablebase/tb5_build"
ANSWERS = os.path.join(S, "tb_answers_host.txt")
META    = os.path.join(S, "tb_meta3.txt")
NOCAP   = 200
MATE    = 10000
QUIET   = 50

def decode(v):
    v = int(v)
    if v == 0:   return "D", 0
    if v <= 127: return "W", v
    return "L", v - 127
def num(x):
    try: return int(x)
    except (TypeError, ValueError): return None

meta = {}
for line in open(META):
    p = line.rstrip("\n").split("\t")
    if len(p) < 12: continue
    n = num(p[0])
    if n is None: continue
    meta[n] = dict(phase=p[1], gi=num(p[2]), opp=p[3], res=p[4], reason=p[5], me=p[6],
                   ply=num(p[7]), counter=num(p[8]), ev=num(p[9]), depth=num(p[10]), mv=p[11],
                   pre_ok=(len(p) > 12 and p[12] == "1"))

ans = {}; missing = 0
for line in open(ANSWERS):
    p = line.split()
    if len(p) != 4: continue
    if p[3] == "MISSING": missing += 1; continue
    ans[int(p[0])] = (p[1],) + decode(p[3])

rec = collections.defaultdict(dict)      # (gi, ply) -> {phase: (tag, wdl, dtz, meta)}
dropped = 0
for nid, (tag, wdl, dtz) in ans.items():
    m = meta.get(nid)
    if not m or m["gi"] is None or m["ply"] is None: continue
    # DROP unreconciled PRE positions.  A PRE that is not exactly one recorded move before its
    # POST is not the position we chose from, so no claim about "we had a win and threw it" can
    # rest on it.  All three tablebase-logic violations found on this data were in this set and
    # there were none outside it, so this filter is the boundary of what the data supports.
    if m["phase"] == "PRE" and not m["pre_ok"]: dropped += 1; continue
    rec[(m["gi"], m["ply"])][m["phase"]] = (tag, wdl, dtz, m)
print("-----  %d answers (%d MISSING = piece on its own goal), %d unreconciled PRE dropped,"
      " %d distinct ply records" % (len(ans), missing, dropped, len(rec)))
both = sum(1 for v in rec.values() if "PRE" in v and "POST" in v)
print("  %d records have BOTH phases answered" % both)

def stalled(m): return m["res"] == "DRAW" and "without a capture" in str(m["reason"])

# ---------- 1. did we ever stand in a winning position in a game we drew? ----------
win_games = collections.defaultdict(list)
for (gi, ply), v in rec.items():
    if "PRE" not in v: continue
    tag, wdl, dtz, m = v["PRE"]
    if not stalled(m): continue
    if wdl != "W": continue
    # clock infeasible: no room to reach the zeroing event before the 200-ply draw.
    # m["counter"] is the PRE clock here, by tb_meta3's per-phase emission.
    if m["counter"] is None or dtz > NOCAP - m["counter"]: continue
    kept = ("POST" in v and v["POST"][1] == "L")
    win_games[gi].append((ply, dtz, m, kept, v.get("POST")))
print("-----  STALLED GAMES (drew by the 200-ply no-capture rule)  -----")
sg = set(gi for (gi, _p), v in rec.items()
         for ph in ("PRE", "POST") if ph in v and stalled(v[ph][3]))
print("  %d stalled games have tablebase coverage" % len(sg))
print("  %d of them contained at least one CLOCK-FEASIBLE WIN with us to move" % len(win_games))
for gi in sorted(win_games):
    rows = sorted(win_games[gi])
    opp = rows[0][2]["opp"]
    threw = [r for r in rows if not r[3]]
    plies = [r[0] for r in rows]
    runs = 1; 
    for a, b in zip(plies, plies[1:]):
        if b - a > 2: runs += 1
    print("  game %-4d vs %-14s %3d winning positions over plies %d-%d in %d stretch(es);"
          " %d of them our move did NOT keep the win" % (gi, opp, len(rows), plies[0], plies[-1], runs, len(threw)))
    for ply, dtz, m, kept, post in rows[:3]:
        pw = post[1] if post else "?"
        print("      ply %-4d counter %-3s eval %+6s depth %-4s dtz %-3d our_move %-8s POST=%s %s"
              % (ply, m["counter"], m["ev"], m["depth"], dtz, m["mv"], pw,
                 "KEPT" if kept else "LOST THE WIN"))

# ---------- 2. what the table knows that the eval does not (PRE only: eval aligns with PRE) ----------
pre = [(w, d, m) for v in rec.values() if "PRE" in v for _t, w, d, m in [v["PRE"]] if m["ev"] is not None]
grid = collections.Counter()
def evclass(ev):
    if ev >= MATE:  return "claims WIN"
    if ev <= -MATE: return "claims LOSS"
    if ev > QUIET:  return "+"
    if ev < -QUIET: return "-"
    return "~0"
for w, d, m in pre: grid[(w, evclass(m["ev"]))] += 1
cols = ["claims WIN", "+", "~0", "-", "claims LOSS"]
print("-----  PRE positions (us to move): tablebase truth vs the engine's own eval  -----")
print("  %-4s" % "TB" + "".join("%13s" % c for c in cols) + "%9s" % "total")
for t in ("W", "D", "L"):
    tot = sum(grid[(t, c)] for c in cols)
    if tot: print("  %-4s" % t + "".join("%13s" % (grid[(t, c)] or "-") for c in cols) + "%9d" % tot)
blind = [(w, d, m) for w, d, m in pre
         if w == "W" and m["ev"] is not None and m["ev"] <= QUIET
         and m["counter"] is not None and d <= NOCAP - m["counter"]]
print("  wins the eval does not see (TB=W, clock-feasible, eval <= %+d): %d over %d games"
      % (QUIET, len(blind), len(set(m["gi"] for _w, _d, m in blind))))
agree = sum(1 for w, _d, m in pre
            if (w == "W" and m["ev"] > QUIET) or (w == "D" and abs(m["ev"]) <= QUIET) or (w == "L" and m["ev"] < -QUIET))
if pre: print("  sign agreement: %d / %d = %.1f%%" % (agree, len(pre), 100.0*agree/len(pre)))
