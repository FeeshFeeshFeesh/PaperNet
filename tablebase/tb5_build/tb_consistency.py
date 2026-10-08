# Does the live->tablebase mapping survive the tablebase's OWN logic?
#
# This is the credibility gate for every R4 claim.  A wrong colour mapping or a wrong PRE/POST
# phase would still produce a plausible-looking pile of verdicts (see tb-live-colour-mapping:
# a flipped goal gives bulk phantom LOSSes).  But PRE and POST of the same ply record are one
# move apart, and WDL here is CLOCK-BLIND (0 = unlimited-clock draw), so three implications
# must hold with ZERO exceptions:
#
#   PRE=L  =>  POST=W        a lost position cannot be saved by our own move
#   PRE=D  =>  POST in {D,W} our own move cannot turn a draw into a win for us
#   PRE=W  =>  anything      we may keep it or throw it
#
# and one on the distances, since a winning side that moves optimally shortens the distance to
# the next zeroing event by exactly one, and a suboptimal winning move can only lengthen it:
#
#   PRE=W and POST=L  =>  PRE_dtz - POST_dtz <= 1
#
# Any violation is a bug in the mapping, not a fact about the games.
import os, collections

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S       = _ROOT + "/tablebase/tb5_build"
ANSWERS = os.path.join(S, "tb_answers_host.txt")
META    = os.path.join(S, "tb_meta3.txt")

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
    meta[n] = dict(phase=p[1], gi=num(p[2]), ply=num(p[7]), clock=num(p[8]), mv=p[11], tag=None)

rec = collections.defaultdict(dict)
nans = nmiss = 0
for line in open(ANSWERS):
    p = line.split()
    if len(p) != 4: continue
    nid = num(p[0])
    if nid is None: continue
    if p[3] == "MISSING": nmiss += 1; continue
    nans += 1
    m = meta.get(nid)
    if not m or m["gi"] is None or m["ply"] is None: continue
    w, d = decode(p[3])
    rec[(m["gi"], m["ply"])][m["phase"]] = (w, d, p[1], m)

both = {k: v for k, v in rec.items() if "PRE" in v and "POST" in v}
print("-----  %d answered, %d MISSING; %d ply records, %d with BOTH phases" % (nans, nmiss, len(rec), len(both)))

# A move is QUIET iff the two phases sit in the SAME signature; a capture changes it.
pairs = collections.Counter(); viol = collections.defaultdict(list); quiet = 0
for (gi, ply), v in both.items():
    (pw, pd, ptag, pm), (qw, qd, qtag, qm) = v["PRE"], v["POST"]
    same = (ptag == qtag)
    quiet += same
    pairs[(pw, qw, "quiet" if same else "capture")] += 1
    if pw == "L" and qw != "W":            viol["PRE=L but POST!=W"].append((gi, ply, pw, pd, qw, qd, pm["mv"]))
    if pw == "D" and qw == "L":            viol["PRE=D but POST=L (we won a drawn position)"].append((gi, ply, pw, pd, qw, qd, pm["mv"]))
    if pw == "W" and qw == "L" and pd - qd > 1:
        viol["PRE=W,POST=L but dtz fell by >1"].append((gi, ply, pw, pd, qw, qd, pm["mv"]))
print("  %d of those %d pairs are QUIET moves (same signature); %d are captures" % (quiet, len(both), len(both)-quiet))
print("-----  PRE -> POST transition table  -----")
print("  %-6s %-6s %-9s %8s" % ("PRE", "POST", "move", "n"))
for (a, b, kind), n in sorted(pairs.items(), key=lambda x: -x[1]):
    print("  %-6s %-6s %-9s %8d" % (a, b, kind, n))
print("-----  LOGICAL VIOLATIONS (any nonzero = the mapping is wrong)  -----")
tot = 0
for k in ("PRE=L but POST!=W", "PRE=D but POST=L (we won a drawn position)", "PRE=W,POST=L but dtz fell by >1"):
    rows = viol.get(k, []); tot += len(rows)
    print("  %-46s %5d" % (k, len(rows)))
    for r in rows[:4]:
        print("       gi %-4d ply %-4d PRE=%s/%-3d POST=%s/%-3d move %s" % r)
print("  TOTAL VIOLATIONS %d of %d testable pairs" % (tot, len(both)))
# the dtz step distribution on kept wins: 1 = we played a distance-optimal move
step = collections.Counter(pd - qd for v in both.values()
                           for (pw, pd, _a, _m), (qw, qd, _b, _n) in [(v["PRE"], v["POST"])]
                           if pw == "W" and qw == "L")
print("-----  PRE_dtz - POST_dtz on kept wins (1 = distance-optimal move)  -----")
print("  " + "  ".join("%+d:%d" % (k, step[k]) for k in sorted(step)))
