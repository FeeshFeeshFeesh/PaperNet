"""Is the OV=150 vs OV=500 comparison confounded by the ENGINE the seat ran? Zero games.

R5 wants time management measured. Internally impossible (bot eval/bot play take only --sims and
--move-ms), and a clock-limited SPRT would destroy the node-limited property that earns cross-host
pooling in sprt_pool.FROZEN.py. So the only route is observational: compare the OV=150 and OV=500
seats that already played. This asks whether that comparison can mean anything, BEFORE building the
outcome join -- by cross-tabulating each seat's regime against the net and thread count it logged in
its own START line.
No sys.argv -- constants here, per CLAUDE.md.
"""
import glob, os, re, collections

LOG_GLOB = _ROOT + "/sf19_ports/live/logs/seat_*.log"
SECRET = re.compile(r"token|key|secret", re.I)
LOW_CLOCK_MS, PANIC_CLOCK_MS, MAX_MOVE_MS, MIN_MOVE_MS = 3000, 800, 4000, 250
CANDS = (150, 500)
LINE  = re.compile(r"rtime (\d+) btime (\d+) rinc (\d+) binc (\d+) \| go movetime (\d+) ply=(\d+)")
START = re.compile(r"START threads=(\d+) hash=\d+ min=(\d+) max=(\d+) net=(\S+)")

def budget(clock_ms, inc_ms, ov):
    if clock_ms < LOW_CLOCK_MS:
        return int(max(50.0, min(0.5 * inc_ms, clock_ms - PANIC_CLOCK_MS, MAX_MOVE_MS)))
    safe = clock_ms - 1500.0
    return int(max(MIN_MOVE_MS, min(0.8 * inc_ms + clock_ms / 30.0 - ov, MAX_MOVE_MS, safe)))

info = {}
for path in sorted(glob.glob(LOG_GLOB)):
    seat, net, thr, votes, games, last = os.path.basename(path), None, None, collections.Counter(), 1, -1
    for ln in open(path, errors="replace"):
        if SECRET.search(ln): continue
        m = START.search(ln)
        if m: thr, net = int(m.group(1)), os.path.basename(os.path.dirname(m.group(4))) + "/" + os.path.basename(m.group(4))
        m = LINE.search(ln)
        if not m: continue
        rt, bt, ri, bi, mv, ply = (int(g) for g in m.groups())
        if ply < last: games += 1
        last = ply
        hit = set()
        for clk, inc in ((rt, ri), (bt, bi)):
            b = {ov: budget(clk, inc, ov) for ov in CANDS}
            if b[150] == b[500]: continue
            for ov in CANDS:
                if abs(b[ov] - mv) <= 1: hit.add(ov)
        if len(hit) == 1: votes[hit.pop()] += 1
    if votes:
        info[seat] = (votes.most_common(1)[0][0], net, thr, games, os.path.getmtime(path))

print("-----  regime x engine, one row per assigned seat (%d seats)  -----" % len(info))
cross = collections.defaultdict(lambda: [0, 0])
for seat, (ov, net, thr, games, mt) in info.items():
    e = cross[(net, thr)]
    e[0 if ov == 150 else 1] += games
print("  %-34s %5s %9s %9s" % ("net / threads", "", "OV=150 g", "OV=500 g"))
for (net, thr), (a, b) in sorted(cross.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
    print("  %-34s thr=%-2s %9d %9d" % (net, thr, a, b))
print("-----  does OV separate cleanly in TIME as well?  -----")
import datetime
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
for ov in CANDS:
    ts = sorted(mt for _, (o, _, _, _, mt) in info.items() if o == ov)
    if ts:
        f = lambda t: datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")
        print("  OV=%-4d %2d seats   last-write span %s .. %s" % (ov, len(ts), f(ts[0]), f(ts[-1])))
