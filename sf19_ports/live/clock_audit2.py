"""CORRECTION to clock_audit.py's regime split. Zero games; reads only seat logs.

THE BUG. clock_audit.py decides which MOVE_OVERHEAD_MS a move played under by trying the candidates
in order and breaking on the first match:

    for cand_ov in (150, 500):
        if abs(budget(clk, i, cand_ov) - mv) <= 1: ...; break

budget() is INSENSITIVE to ov on three kinds of row: when the target is capped at MAX_MOVE_MS, when
the clock is under LOW_CLOCK_MS (the panic branch never reads ov), and when `safe` clamps it. On
every such row BOTH candidates match, so the row is labelled 150 whatever it really was. The 150
bucket absorbs the ambiguous rows of BOTH regimes; the 500 bucket keeps only rows where the formula
discriminates, which are by construction the LOW-clock ones. That inverts the very comparison the
table was making.

THE FIX. MOVE_OVERHEAD_MS is a module constant read at seat start, so it cannot change mid-seat
(see memory live-clock-overhead: a seat edit lands only when the next SEAT spawns). So assign it PER
SEAT from the discriminating rows alone, then apply it to that seat's every row. Seats with no
discriminating row stay UNASSIGNED instead of being guessed.

WHICH CLOCK IS OURS has the same ambiguity -- on a capped row both sides' clocks reproduce the
budget -- so our colour is voted PER GAME (games are segmented on a drop in ply=, per lockrecord.py)
and then applied to all of that game's rows, instead of per row where the capped rows would again
pick arbitrarily.

MIN/MAX_MOVE_MS are env-overridable in sf19nn_live_seat.py, so they are NOT assumed: every one of
the 98 seat logs records `START ... min=250 max=4000`, checked before this ran.
No sys.argv -- constants here, per CLAUDE.md.
"""
import glob, os, re, collections
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

LOG_GLOB = _ROOT + "/sf19_ports/live/logs/seat_*.log"
SECRET   = re.compile(r"token|key|secret", re.I)
LOW_CLOCK_MS, PANIC_CLOCK_MS, MAX_MOVE_MS, MIN_MOVE_MS = 3000, 800, 4000, 250
CANDS    = (150, 500)
LINE = re.compile(r"rtime (\d+) btime (\d+) rinc (\d+) binc (\d+) \| go movetime (\d+) ply=(\d+)")

def budget(clock_ms, inc_ms, ov):
    if clock_ms < LOW_CLOCK_MS:
        return int(max(50.0, min(0.5 * inc_ms, clock_ms - PANIC_CLOCK_MS, MAX_MOVE_MS)))
    safe = clock_ms - 1500.0
    return int(max(MIN_MOVE_MS, min(0.8 * inc_ms + clock_ms / 30.0 - ov, MAX_MOVE_MS, safe)))

def pct(a, b): return "%.1f%%" % (100.0 * a / b) if b else "-"
def q(v, p):
    if not v: return 0
    s = sorted(v); return s[min(len(s) - 1, int(p * len(s)))]

seats, unparsed = {}, 0
for path in sorted(glob.glob(LOG_GLOB)):
    rows = []
    for ln in open(path, errors="replace"):
        if SECRET.search(ln): continue
        m = LINE.search(ln)
        if not m: continue
        rt, bt, ri, bi, mv, ply = (int(g) for g in m.groups())
        rows.append((rt, bt, ri, bi, mv, ply))
    if rows: seats[os.path.basename(path)] = rows
print("-----  %d seats, %d parsed move rows  -----" % (len(seats), sum(len(r) for r in seats.values())))

# ---- pass A: per-seat OV from rows where the formula can actually tell 150 from 500 apart
seat_ov, minority, disc_tot, amb_tot = {}, 0, 0, 0
for seat, rows in seats.items():
    votes = collections.Counter()
    for rt, bt, ri, bi, mv, ply in rows:
        hit = set()
        for clk, inc in ((rt, ri), (bt, bi)):
            b = {ov: budget(clk, inc, ov) for ov in CANDS}
            if b[150] == b[500]: continue          # degenerate: cannot discriminate
            for ov in CANDS:
                if abs(b[ov] - mv) <= 1: hit.add(ov)
        if len(hit) == 1: votes[hit.pop()] += 1; disc_tot += 1
        else: amb_tot += 1
    if votes:
        seat_ov[seat] = votes.most_common(1)[0][0]
        minority += sum(votes.values()) - votes.most_common(1)[0][1]
tot = disc_tot + amb_tot
print("  discriminating rows %6d (%s)   AMBIGUOUS %6d (%s)  <- all booked OV=150 before"
      % (disc_tot, pct(disc_tot, tot), amb_tot, pct(amb_tot, tot)))
print("  minority votes within a seat: %d of %d discriminating rows (%s) -- noise floor of the vote"
      % (minority, disc_tot, pct(minority, disc_tot)))

# ---- pass B+C: per-game colour, then the clock table over EVERY row of an assigned seat
def table(label, bucket_of):
    print("-----  %s  -----" % label)
    print("  %-5s %6s %6s %8s %8s %8s %8s %8s %8s"
          % ("OV", "seats", "games", "moves", "clk p05", "median", "min", "<3000", "maxed"))
    agg = collections.defaultdict(lambda: [set(), 0, [], 0, 0])
    for seat, rows in seats.items():
        ov = seat_ov.get(seat)
        if ov is None: continue
        games, cur, last = [], [], -1
        for r in rows:
            if r[5] < last: games.append(cur); cur = []
            cur.append(r); last = r[5]
        games.append(cur)
        for g in games:
            if not g: continue
            side = collections.Counter()
            for rt, bt, ri, bi, mv, ply in g:
                ok = [k for k, (clk, inc) in enumerate(((rt, ri), (bt, bi)))
                      if abs(budget(clk, inc, ov) - mv) <= 1]
                if len(ok) == 1: side[ok[0]] += 1
            if not side: continue
            s = side.most_common(1)[0][0]
            for r in g:
                b = bucket_of(seat, r, ov)
                if b is None: continue
                e = agg[b]; e[0].add(seat); e[2].append(r[0] if s == 0 else r[1])
                e[3] += 1 if r[4] >= MAX_MOVE_MS else 0
            agg[bucket_of(seat, g[0], ov)][1] += 1
    for ov in CANDS:
        if ov not in agg: continue
        sts, ng, clks, maxed, _ = agg[ov]
        print("  %-5d %6d %6d %8d %8d %8d %8d %8s %8s"
              % (ov, len(sts), ng, len(clks), q(clks, 0.05), q(clks, 0.5), min(clks),
                 pct(sum(1 for c in clks if c < LOW_CLOCK_MS), len(clks)), pct(maxed, len(clks))))

table("CORRECTED: OV per SEAT, all rows counted", lambda seat, r, ov: ov)

def old(seat, r, ov):
    for clk, inc in ((r[0], r[2]), (r[1], r[3])):
        for c in CANDS:
            if abs(budget(clk, inc, c) - r[4]) <= 1: return c
    return None
table("REPRODUCING THE BUG: first-match-wins, as clock_audit.py did", old)
