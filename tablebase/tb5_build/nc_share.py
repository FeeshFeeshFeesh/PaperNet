# Re-derive the no-capture draw share WITHOUT trusting per-game record segmentation.
# The status log's own Result line carries the game's TOTAL ply count, so a
# "200 plies without a capture" claim is self-certifying: it needs >= 200 plies.
import re, collections
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
LOG = _ROOT + "/project_files/papernet-status.log"
ESC = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Format B carries the ply count; format A does not.
B = re.compile(r"Result: (\w+) \(([^)]+)\) vs ([^,]+), (\d+) plies")
A = re.compile(r"Result: (\w+) vs (\S+) \(Elo \d+\), ([^.]+)\.")

raw = []
for line in open(LOG, errors="replace"):
    if "Result:" not in line: continue
    s = ESC.sub("", line[line.index("Result:"):]).strip()
    raw.append(s)
ded = [s for i, s in enumerate(raw) if i == 0 or s != raw[i-1]]   # collapse consecutive repeats

nb = na = 0
res = collections.Counter(); nc = []; bad = []
for s in ded:
    m = B.search(s)
    if m:
        nb += 1; out, reason, plies = m.group(1), m.group(2), int(m.group(4))
        res[(out, reason)] += 1
        if "without a capture" in reason:
            (nc if plies >= 200 else bad).append((plies, m.group(3)))
        continue
    m = A.search(s)
    if m:
        na += 1; res[(m.group(1), m.group(3))] += 1

print("raw Result lines %d -> deduplicated games %d" % (len(raw), len(ded)))
print("  format B (carries ply count, self-certifying) : %d" % nb)
print("  format A (no ply count)                       : %d" % na)
print("-----  outcome x reason over the %d dedup'd games" % (nb + na))
for k, v in res.most_common(10):
    print("   %-6s %-34s %4d  (%5.1f%%)" % (k[0], k[1][:34], v, 100.0 * v / (nb + na)))
print("-----")
tot_nc = len(nc) + len(bad)
print("no-capture draws claimed (format B)     : %d" % tot_nc)
print("  self-certifying, total plies >= 200   : %d" % len(nc))
print("  IMPOSSIBLE, total plies < 200         : %d %s" % (len(bad), bad[:6]))
if nb:
    print("-----")
    print("SHARE among format-B games = %d / %d = %.1f%%" % (len(nc), nb, 100.0 * len(nc) / nb))
if nc:
    ps = sorted(p for p, _ in nc)
    print("their total plies: min %d  median %d  max %d" % (ps[0], ps[len(ps)//2], ps[-1]))
