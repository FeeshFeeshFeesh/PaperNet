# Does the cyclic relabelling R -> P -> S -> R really hold on the SOLVED 4-piece tables?
# No argv, per CLAUDE.md.
#
# At 3 pieces the orbit members came out md5-identical.  That is a special case, not the rule:
# the canonical piece list is sorted by (colour, type), and sigma permutes the TYPES, so it can
# reorder the slots -- e.g. (R,S) -> (P,R), whose sorted order is (R,P), swapping slot 0 and 1.
# The index then permutes and the bytes differ even though the game values are identical.
# The invariant that always holds is the VALUE HISTOGRAM, so that is the test; md5 equality is
# reported separately as the stronger, order-preserving case.
import hashlib
import os
from collections import Counter

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

TB = _ROOT + "/tablebase/tb_c"

def sigma(c):
    """counts are (R,P,S); a rock becomes a paper, so new_P = old_R, new_S = old_P, new_R = old_S"""
    return (c[2], c[0], c[1])

def tag(wc, bc):
    return "%d%d%d_%d%d%d" % (wc + bc)

have = {}
for fn in sorted(os.listdir(TB)):
    if not fn.endswith(".dtz"):
        continue
    t = fn[:-4]
    have[t] = os.path.join(TB, fn)

def load(t):
    b = open(have[t], "rb").read()
    return hashlib.md5(b).hexdigest(), Counter(b), len(b)

# group the 4-piece tables we actually have into sigma/colour-swap orbits
sigs = []
for t in have:
    w, b = t.split("_")
    wc, bc = tuple(int(x) for x in w), tuple(int(x) for x in b)
    if sum(wc) + sum(bc) == 4:
        sigs.append((wc, bc))

seen, orbits = set(), []
for s in sorted(sigs):
    if s in seen:
        continue
    orb, cur = set(), s
    for _ in range(3):
        orb.add(cur)
        orb.add((cur[1], cur[0]))
        cur = (sigma(cur[0]), sigma(cur[1]))
    present = {x for x in orb if tag(*x) in have}
    seen |= present
    if len(present) > 1:
        orbits.append(sorted(present))

print("-----  %d 4-piece tables, %d sigma-orbits with more than one member present  -----"
      % (len(sigs), len(orbits)))
bad_hist = bad_len = 0
md5_same = md5_diff = 0
for orb in orbits:
    ref_t = tag(*orb[0])
    rm, rh, rl = load(ref_t)
    line = []
    for s in orb[1:]:
        t = tag(*s)
        m, h, l = load(t)
        if l != rl:
            bad_len += 1
            line.append("%s LENGTH DIFFERS" % t)
        elif h != rh:
            bad_hist += 1
            line.append("%s HISTOGRAM DIFFERS" % t)
        elif m == rm:
            md5_same += 1
            line.append("%s md5-identical" % t)
        else:
            md5_diff += 1
            line.append("%s same histogram, permuted bytes" % t)
    print("  %s  ->  %s" % (ref_t, "; ".join(line)))

print("-----")
print("  histogram mismatches %d   length mismatches %d" % (bad_hist, bad_len))
print("  md5-identical pairs %d   same-histogram-permuted pairs %d" % (md5_same, md5_diff))
print("  VERDICT: %s" % ("relabelling HOLDS on the solved 4-piece tables"
                         if bad_hist == 0 and bad_len == 0 else "RELABELLING BROKEN -- investigate"))
