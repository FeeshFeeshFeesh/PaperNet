# Does the RELABEL-FOLDED builder reproduce the unfolded tables byte for byte?
# No sys.argv: knobs are here, per CLAUDE.md.
#
# This is the regression test that matters for the fold. The unfolded build solved each signature
# by probing capture children found WITHOUT the fold; the folded build reaches the same children
# by walking the order-6 orbit <sigma, colour-swap> to a stored representative. If both produce
# identical bytes for every retained representative, the orbit walk is right end to end -- not
# merely self-consistent, which a folded-vs-folded check would be.
#
# It also reports which reps were only reachable via a NON-TRIVIAL group element, because a fold
# that happened to need k=0 everywhere would pass this test while proving nothing.
import hashlib, os
from itertools import product

OLD = "tb_c"        # colour fold only: 24 small + 51 four-piece
NEW = "tb_c_fold"   # relabel fold: 8 small + 17 four-piece
LIMIT = {0: 3, 1: 4, 2: 3}

def sigma(c): return (c[2], c[0], c[1])
def ok(c):    return all(c[i] <= LIMIT[i] for i in range(3))

def orbit_rep(wc, bc):
    best, a, b = None, wc, bc
    for _ in range(3):
        for cw, cb in ((a, b), (b, a)):
            if ok(cw) and ok(cb) and (best is None or (cw, cb) < best): best = (cw, cb)
        a, b = sigma(a), sigma(b)
    return best

def tag(wc, bc): return "%d%d%d_%d%d%d" % (wc + bc)
def parse(name):
    w, b = name[:-4].split("_")
    return tuple(int(x) for x in w), tuple(int(x) for x in b)

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""): h.update(blk)
    return h.hexdigest()

new = sorted(f for f in os.listdir(NEW) if f.endswith(".dtz"))
old = sorted(f for f in os.listdir(OLD) if f.endswith(".dtz"))
print("-----  %d folded tables vs %d unfolded  -----" % (len(new), len(old)))

bad = missing = 0
for f in new:
    if f not in old:
        print("  %-14s NOT IN THE UNFOLDED SET (nothing to compare)" % f); missing += 1; continue
    a, b = md5(os.path.join(NEW, f)), md5(os.path.join(OLD, f))
    sa, sb = os.path.getsize(os.path.join(NEW, f)), os.path.getsize(os.path.join(OLD, f))
    if a == b:
        print("  %-14s OK  %10d B" % (f, sa))
    else:
        print("  %-14s MISMATCH  %d vs %d B  %s vs %s" % (f, sa, sb, a[:12], b[:12])); bad += 1

# Which unfolded tables did the fold REMOVE, and by which group element are they reached?
print("-----  the %d tables the fold removed, and the element that reaches each  -----" % (len(old) - len(new)))
nontrivial = 0
for f in old:
    if f in new: continue
    wc, bc = parse(f)
    rep = orbit_rep(wc, bc)
    k, sw, a, b = None, None, wc, bc
    for kk in range(3):
        for s_, (cw, cb) in enumerate(((a, b), (b, a))):
            if (cw, cb) == rep and k is None: k, sw = kk, s_
        a, b = sigma(a), sigma(b)
    if k: nontrivial += 1
    print("  %-14s -> %-14s  sigma^%d%s" % (f, tag(*rep) + ".dtz", k, " + colour swap" if sw else ""))
print("-----")
print("  byte mismatches %d   unmatched %d   removals needing sigma^k with k>0: %d"
      % (bad, missing, nontrivial))
print("  VERDICT: %s" % ("the relabelling fold reproduces the unfolded tables EXACTLY"
                         if bad == 0 and missing == 0 else "FOLD IS BROKEN -- do not ship"))
