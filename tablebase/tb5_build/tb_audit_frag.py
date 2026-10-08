# Does the mis-tagged fragment change R4's verdict?  A game only ended by the 200-ply
# no-capture rule if its own no-capture clock actually reached it, so require final
# counter >= 195 in the SOURCE record, not just the reason string.
import os, sys, collections
sys.path.insert(0, _ROOT + "/sf19_ports/live")
import live_boards

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S       = _ROOT + "/tablebase/tb5_build"
ANSWERS = os.path.join(S, "tb_answers_host.txt")
META    = os.path.join(S, "tb_meta3.txt")
NOCAP   = 200

def decode(v):
    v = int(v)
    if v == 0:   return "D", 0
    if v <= 127: return "W", v
    return "L", v - 127
def num(x):
    try: return int(x)
    except (TypeError, ValueError): return None

gs = live_boards.parse()
genuine = set()
for gi, g in enumerate(gs):
    pl = g.get("plies", [])
    if not pl: continue
    c = pl[-1].get("counter")
    if c is not None and c >= 195: genuine.add(gi)

meta = {}
for line in open(META):
    p = line.rstrip("\n").split("\t")
    if len(p) < 12: continue
    n = num(p[0])
    if n is None: continue
    meta[n] = dict(phase=p[1], gi=num(p[2]), res=p[4], reason=p[5], ply=num(p[7]),
                   counter=num(p[8]), ev=num(p[9]), pre_ok=(len(p) > 12 and p[12] == "1"))
ans = {}
for line in open(ANSWERS):
    p = line.split()
    if len(p) != 4 or p[3] == "MISSING": continue
    ans[int(p[0])] = (p[1],) + decode(p[3])

rec = collections.defaultdict(dict)
for nid, (tag, wdl, dtz) in ans.items():
    m = meta.get(nid)
    if not m or m["gi"] is None or m["ply"] is None: continue
    if m["phase"] == "PRE" and not m["pre_ok"]: continue
    rec[(m["gi"], m["ply"])][m["phase"]] = (tag, wdl, dtz, m)

def report(label, gate):
    sg = set(gi for (gi, _p), v in rec.items()
             for ph in ("PRE", "POST") if ph in v and gate(v[ph][3]))
    n = collections.Counter(); feas = collections.Counter()
    for (gi, ply), v in rec.items():
        if gi not in sg: continue
        for ph in ("PRE", "POST"):
            if ph not in v: continue
            tag, wdl, dtz, m = v[ph]
            if not gate(m): continue
            n[wdl] += 1
            if wdl == "W" and m["counter"] is not None:
                feas["feasible" if dtz <= NOCAP - m["counter"] else "infeasible"] += 1
    print("  %-22s games %2d | positions %4d = D %4d  W %3d  L %3d | wins clock-feasible %d, infeasible %d"
          % (label, len(sg), sum(n.values()), n["D"], n["W"], n["L"], feas["feasible"], feas["infeasible"]))
    return sg

def stalled_old(m): return m["res"] == "DRAW" and "without a capture" in str(m["reason"])
def stalled_new(m): return stalled_old(m) and m["gi"] in genuine

print("-----  R4 stalled-game tallies, reason-string gate vs counter-verified gate  -----")
a = report("reason string only", stalled_old)
b = report("counter-verified", stalled_new)
print("-----")
print("  games dropped by the gate: %s" % sorted(a - b))
