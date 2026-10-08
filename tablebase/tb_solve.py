"""
tb_solve.py -- retrograde DTZ solver for the 9x9 rock/paper/scissors king game.

WHAT IT PRODUCES, and why this encoding is SOUND under the capture clock.

The clock matters: 22.6% of our live games end by the 200-ply no-capture rule, so a
clock-blind table would claim wins it cannot deliver -- exactly the bug we are fixing.
Two facts make one signed number per position enough:

  1. Every zeroing event (capture, or entering the goal) CHANGES THE MATERIAL SIGNATURE
     or ends the game.  So inside one signature the clock only ever counts UP.
  2. Therefore (signature, clock) is acyclic, and the value at clock c is a monotone
     step function of c: win/loss while c is small, draw once the budget runs out.

So we store, per position, the value assuming a FRESH clock (c = 0) plus DTZ = the number
of plies to the next zeroing event under optimal play (winner minimises, loser maximises).
The probe rule is then exact: at a real clock c, use the stored value iff DTZ <= 200 - c,
otherwise treat the position as unknown and search.  Conservative at c > 0, never wrong.

Because a win needing more than 200 plies of clock is a DRAW at a fresh clock, the
retrograde BFS is BOUNDED at DTZ_MAX = 200.  Anything unresolved at that depth is a draw,
and every value used to resolve a node was itself resolved at a smaller DTZ -- so the
bounded BFS is not an approximation of the clock-aware value, it IS the clock-aware value.

Terminal order is taken verbatim from the engine's own comment at search.cpp:126-137:
"the side that just moved standing on its goal wins, then the side to move with no move
(including no pieces) loses, then the capture clock draws.  A side to move with a piece
that can enter its goal wins on this move."  So a stalemate at clock 200 is a LOSS, and a
goal threat at clock 200 is a DRAW.

Rules come from rps_rules.py, which was differentially validated against the engine
(perft depths 1-4 exact, 120 sparse positions at depth 3, 150 evasion-restricted legal
move lists).  The fast slot-indexed movegen here is checked against it on startup.
"""

import itertools
import lzma
import os
import random
import time
import zlib
from array import array

import rps_rules as R

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

# ----------------------------------------------------------------- knobs (no argv)
MAX_PIECES    = 3          # solve every colour-canonical signature up to this size
ONLY_SIGS     = None       # e.g. [((1,0,0),(1,0,0))] to restrict; None = all
DTZ_MAX       = R.CAPTURE_CLOCK      # 200
CROSSCHECK_MOVEGEN = 3000  # random positions comparing fast movegen vs rps_rules
FIXPOINT_N    = 4000       # random positions per signature for the fixpoint self-check
CLOCK_DP_MAX_PIECES = 2    # run the independent clock DP on signatures this small
SEED          = 20260927
WRITE_TABLES  = True       # dump the packed .dtz blobs to OUT_DIR
OUT_DIR       = _ROOT + "/tablebase/tb"

LIMITS = {R.ROCK: 3, R.PAPER: 4, R.SCISSORS: 3}     # per side, position.h:19-23

UNKNOWN, WIN, LOSS, DEAD = 0, 1, 2, 3

# ----------------------------------------------------------------- signatures
def pieces_of(sig):
    """Canonical piece list for a signature: sorted by (colour, type), so identical
    pieces are contiguous and can be folded by keeping their squares increasing."""
    wc, bc = sig
    out = []
    for c, cnt in ((R.WHITE, wc), (R.BLACK, bc)):
        for pt in (R.ROCK, R.PAPER, R.SCISSORS):
            out += [(c, pt)] * cnt[pt - 1]
    return out

def runs_of(pcs):
    """Index ranges of identical pieces."""
    out, i = [], 0
    while i < len(pcs):
        j = i
        while j < len(pcs) and pcs[j] == pcs[i]:
            j += 1
        out.append((i, j))
        i = j
    return out

def all_counts(n):
    for r in range(min(n, LIMITS[R.ROCK]) + 1):
        for p in range(min(n - r, LIMITS[R.PAPER]) + 1):
            s = n - r - p
            if s <= LIMITS[R.SCISSORS]:
                yield (r, p, s)

def signatures(max_pieces):
    """Colour-canonical signatures (wc <= bc), both sides non-empty.
    The other colouring is obtained by the centre reflection s -> 80 - s, which maps
    i9 <-> a1 and hence GOAL[WHITE] <-> GOAL[BLACK]."""
    out = []
    for total in range(2, max_pieces + 1):
        for nw in range(1, total):
            nb = total - nw
            for wc in all_counts(nw):
                for bc in all_counts(nb):
                    if sum(wc) == nw and sum(bc) == nb and wc <= bc:
                        out.append((wc, bc))
    out.sort(key=lambda s: (sum(s[0]) + sum(s[1]), s))
    return out

# ----------------------------------------------------------------- indexing
def encode(sqs, stm, pw):
    t = 0
    for i, s in enumerate(sqs):
        t += s * pw[i]
    return 2 * t + stm

def decode(base, n, pw):
    return tuple((base // pw[i]) % R.NSQ for i in range(n))

def canon(sqs, runs):
    out = list(sqs)
    for a, b in runs:
        if b - a > 1:
            out[a:b] = sorted(out[a:b])
    return out

# ----------------------------------------------------------------- fast movegen
# Slot-indexed mirror of rps_rules.gen_legal.  Returns (from_sq, to_sq, cap_slot).
def _non_evasions(pcs, sqs, stm, occ):
    out = []
    for i in range(len(pcs)):
        c, pt = pcs[i]
        if c != stm:
            continue
        prey = R.PREY[pt]
        frm = sqs[i]
        for to in R.STEPS[frm]:
            j = occ.get(to)
            if j is None:
                out.append((frm, to, -1))
            elif pcs[j][0] != stm and pcs[j][1] == prey:
                out.append((frm, to, j))
    return out

def _threats(pcs, sqs, c, occ):
    """position.h:237, as slots: pieces of c that could enter c's goal right now."""
    g = R.GOAL[c]
    j = occ.get(g)
    if j is None:
        return [occ[s] for s in R.STEPS[g] if s in occ and pcs[occ[s]][0] == c]
    if pcs[j][0] == c:
        return []
    want = R.PREDATOR[pcs[j][1]]
    return [occ[s] for s in R.STEPS[g] if s in occ and pcs[occ[s]] == (c, want)]

def _evasions(pcs, sqs, stm, occ, thr):
    """movegen.cpp:96-128."""
    us, them = stm, 1 - stm
    out = []
    gu = R.GOAL[us]
    cap_at_gu = occ.get(gu, -1)
    for i in _threats(pcs, sqs, us, occ):
        out.append((sqs[i], gu, cap_at_gu))
    if len(thr) == 1:
        t = thr[0]
        ts = sqs[t]
        want = R.PREDATOR[pcs[t][1]]
        for s in R.STEPS[ts]:
            j = occ.get(s)
            if j is not None and pcs[j] == (us, want):
                out.append((s, ts, t))
    home = R.GOAL[them]
    if home not in occ:
        thr_types = {pcs[t][1] for t in thr}
        for pt in (R.ROCK, R.PAPER, R.SCISSORS):
            if R.PREDATOR[pt] not in thr_types:
                for s in R.STEPS[home]:
                    j = occ.get(s)
                    if j is not None and pcs[j] == (us, pt):
                        out.append((s, home, -1))
    return out

def legal(pcs, sqs, stm, occ):
    thr = _threats(pcs, sqs, 1 - stm, occ)
    if not thr:
        return _non_evasions(pcs, sqs, stm, occ)
    ev = _evasions(pcs, sqs, stm, occ, thr)
    if not ev:
        return _non_evasions(pcs, sqs, stm, occ)
    # The three evasion clauses cannot collide on a (from,to) pair, but a duplicate would
    # inflate the unresolved-successor counter and silently wedge the BFS, so dedupe.
    return list(dict.fromkeys(ev))

# ----------------------------------------------------------------- probing children
def probe(pcs, sqs, stm, tables):
    """Clock-aware wdl of an arbitrary position, folding to the canonical colouring.
    Returns WIN/LOSS/UNKNOWN from the point of view of `stm`."""
    wc, bc = [0, 0, 0], [0, 0, 0]
    for c, pt in pcs:
        (wc if c == R.WHITE else bc)[pt - 1] += 1
    if tuple(wc) > tuple(bc):
        pcs = [(1 - c, pt) for c, pt in pcs]
        sqs = [R.NSQ - 1 - s for s in sqs]        # (f,r) -> (8-f,8-r)
        stm = 1 - stm
        wc, bc = bc, wc
    sig = (tuple(wc), tuple(bc))
    pairs = sorted(zip(pcs, sqs))                  # canonical order, incl. folded twins
    wdl, dtz, pw = tables[sig]
    idx = encode([s for _, s in pairs], stm, pw)
    return wdl[idx], dtz[idx]

def capture_child(pcs, sqs, mover, cap_slot, to, stm, tables):
    """Value of the position after a zeroing capture, from the CHILD's mover (= the
    opponent).  Returns None when the opponent has no pieces left: we simply win."""
    npcs, nsqs = [], []
    cstm = 1 - stm
    alive = False
    for k in range(len(pcs)):
        if k == cap_slot:
            continue
        npcs.append(pcs[k])
        nsqs.append(to if k == mover else sqs[k])
        if pcs[k][0] == cstm:
            alive = True
    if not alive:
        return None
    return probe(npcs, nsqs, cstm, tables)[0]

# ----------------------------------------------------------------- enumeration
def placements(pcs, runs):
    """Every canonical square assignment: distinct squares, identical pieces increasing."""
    n = len(pcs)
    if all(b - a == 1 for a, b in runs):
        for sqs in itertools.permutations(range(R.NSQ), n):
            yield sqs
    else:
        for sqs in itertools.permutations(range(R.NSQ), n):
            ok = True
            for a, b in runs:
                for k in range(a + 1, b):
                    if sqs[k] < sqs[k - 1]:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                yield sqs

# ----------------------------------------------------------------- the solver
def solve(sig, tables):
    pcs  = pieces_of(sig)
    n    = len(pcs)
    runs = runs_of(pcs)
    pw   = [R.NSQ ** i for i in range(n)]
    size = 2 * R.NSQ ** n
    own_goal = [R.GOAL[c] for c, _ in pcs]

    wdl  = bytearray(size)
    dtz  = array('h', bytes(2 * size))
    safe = bytearray(size)
    zdr  = bytearray(size)
    buckets = [array('i') for _ in range(DTZ_MAX + 2)]   # 4 B/entry, not a 32 B python int
    live = 0

    for sqs in placements(pcs, runs):
        base = 0
        for i in range(n):
            base += sqs[i] * pw[i]
        base *= 2
        over = False
        for i in range(n):
            if sqs[i] == own_goal[i]:
                over = True
                break
        if over:                                  # a side already stands on its goal
            wdl[base] = DEAD
            wdl[base + 1] = DEAD
            continue
        occ = {}
        for i in range(n):
            occ[sqs[i]] = i
        for stm in (0, 1):
            idx = base + stm
            mv = legal(pcs, sqs, stm, occ)
            if not mv:                            # no move (or no pieces) -> we lose, now
                wdl[idx] = LOSS
                dtz[idx] = 0
                buckets[0].append(idx)
                live += 1
                continue
            live += 1
            goal = R.GOAL[stm]
            win1 = False
            nz = 0
            zd = False
            for frm, to, cap in mv:
                if to == goal:
                    win1 = True
                    break
                if cap >= 0:
                    cv = capture_child(pcs, sqs, occ[frm], cap, to, stm, tables)
                    if cv is None or cv == LOSS:
                        win1 = True
                        break
                    if cv == UNKNOWN:
                        zd = True
                else:
                    nz += 1
            if win1:
                wdl[idx] = WIN
                dtz[idx] = 1
                buckets[1].append(idx)
            elif nz == 0 and not zd:               # every move is a losing zeroing move
                wdl[idx] = LOSS
                dtz[idx] = 1
                buckets[1].append(idx)
            else:
                safe[idx] = nz
                zdr[idx] = 1 if zd else 0

    # ---- bounded retrograde BFS.  Reverse moves are non-zeroing by construction.
    for d in range(DTZ_MAX + 1):
        b = buckets[d]
        if not b:
            continue
        nd = d + 1
        if nd > DTZ_MAX:
            break
        nb = buckets[nd]
        for idx in b:
            stm = idx & 1
            v = wdl[idx]
            sqs = decode(idx >> 1, n, pw)
            occ = {}
            for i in range(n):
                occ[sqs[i]] = i
            mover = 1 - stm
            gm = R.GOAL[mover]
            for i in range(n):
                if pcs[i][0] != mover:
                    continue
                cs = sqs[i]
                if cs == gm:                       # that move was a goal entry: zeroing
                    continue
                for frm in R.STEPS[cs]:
                    if frm in occ or frm == gm:
                        continue
                    qsqs = list(sqs)
                    qsqs[i] = frm
                    qsqs = canon(qsqs, runs)
                    qidx = encode(qsqs, mover, pw)
                    if wdl[qidx] != UNKNOWN:
                        continue
                    qocc = {}
                    for k in range(n):
                        qocc[qsqs[k]] = k
                    if (frm, cs, -1) not in legal(pcs, qsqs, mover, qocc):
                        continue                   # the forward move is not legal there
                    if v == LOSS:
                        wdl[qidx] = WIN
                        dtz[qidx] = nd
                        nb.append(qidx)
                    else:
                        safe[qidx] -= 1
                        if safe[qidx] == 0 and not zdr[qidx]:
                            wdl[qidx] = LOSS
                            dtz[qidx] = nd
                            nb.append(qidx)
    return wdl, dtz, pw, live

# ----------------------------------------------------------------- shipping format
# One byte per LIVE position:
#     0            draw (or unusable -- see the clamp)
#     1..127       win,  DTZ = v
#     128..255     loss, DTZ = v - 127        (so loss DTZ 1..128)
# A win with DTZ > 127 or a loss with DTZ > 128 is written as 0.  That is CONSERVATIVE:
# the probe then just searches instead of trusting the table, so the clamp can cost
# coverage but can never make us claim a win we cannot deliver.
CLAMP_WIN, CLAMP_LOSS = 127, 128

def pack(sig, tables, runs=None):
    """Returns (blob, n_live, n_clamped).  The blob holds only live indices, in index
    order -- the 81^n index space has holes (duplicate squares, non-canonical twins) and
    leaving those zeros in would make the compression ratio look far better than the real
    ranked format ever could."""
    pcs = pieces_of(sig)
    n = len(pcs)
    if runs is None:
        runs = runs_of(pcs)
    pw = [R.NSQ ** i for i in range(n)]
    wdl, dtz, _ = tables[sig]
    own_goal = [R.GOAL[c] for c, _ in pcs]
    out = bytearray()
    clamped = 0
    for sqs in placements(pcs, runs):
        if any(sqs[i] == own_goal[i] for i in range(n)):
            continue
        base = 2 * sum(sqs[i] * pw[i] for i in range(n))
        for stm in (0, 1):
            i = base + stm
            v, d = wdl[i], dtz[i]
            if v == WIN:
                if d <= CLAMP_WIN:
                    out.append(d)
                else:
                    out.append(0)
                    clamped += 1
            elif v == LOSS:
                if d <= CLAMP_LOSS:
                    out.append(127 + d)
                else:
                    out.append(0)
                    clamped += 1
            else:
                out.append(0)
    return bytes(out), len(out), clamped

# ----------------------------------------------------------------- checks
def crosscheck_movegen(rng, n_pos):
    """The fast slot movegen must agree with the engine-validated rps_rules."""
    bad = 0
    for _ in range(n_pos):
        npc = rng.randint(2, 5)
        sqs = rng.sample(range(R.NSQ), npc)
        pcs = []
        for k in range(npc):
            pcs.append((R.WHITE if k % 2 == 0 else R.BLACK,
                        rng.choice([R.ROCK, R.PAPER, R.SCISSORS])))
        p = R.Pos()
        p.bd = [None] * R.NSQ
        for k in range(npc):
            p.bd[sqs[k]] = pcs[k]
        p.stm = rng.randint(0, 1)
        p.clock = 0
        if R.reached_goal(p, R.WHITE) or R.reached_goal(p, R.BLACK):
            continue
        occ = {s: i for i, s in enumerate(sqs)}
        mine = {(f, t) for f, t, _ in legal(pcs, sqs, p.stm, occ)}
        theirs = set(R.gen_legal(p))
        if mine != theirs:
            bad += 1
            if bad <= 3:
                print("   MOVEGEN MISMATCH", R.to_fen(p), sorted(mine ^ theirs))
    return bad

def fixpoint_check(sig, tables, rng, n_pos):
    """Re-derive each value from its children and demand the fixpoint equations hold."""
    pcs = pieces_of(sig)
    n = len(pcs)
    runs = runs_of(pcs)
    wdl, dtz, pw = tables[sig]
    own_goal = [R.GOAL[c] for c, _ in pcs]
    bad = tested = 0
    while tested < n_pos:
        sqs = canon(rng.sample(range(R.NSQ), n), runs)
        if any(sqs[i] == own_goal[i] for i in range(n)):
            continue
        stm = rng.randint(0, 1)
        idx = encode(sqs, stm, pw)
        if wdl[idx] == DEAD:
            continue
        occ = {s: i for i, s in enumerate(sqs)}
        mv = legal(pcs, sqs, stm, occ)
        tested += 1
        goal = R.GOAL[stm]
        best = LOSS          # best outcome for stm over all moves
        bd = None            # dtz that goes with `best`
        if not mv:
            got, gd = LOSS, 0
        else:
            for frm, to, cap in mv:
                if to == goal:
                    o, od = WIN, 1
                elif cap >= 0:
                    cv = capture_child(pcs, sqs, occ[frm], cap, to, stm, tables)
                    if cv is None or cv == LOSS:
                        o, od = WIN, 1
                    elif cv == UNKNOWN:
                        o, od = UNKNOWN, 0
                    else:
                        o, od = LOSS, 1
                else:
                    qs = canon([(to if k == occ[frm] else sqs[k]) for k in range(n)], runs)
                    j = encode(qs, 1 - stm, pw)
                    cv, cd = wdl[j], dtz[j]
                    if cv == LOSS:
                        o, od = WIN, cd + 1
                    elif cv == WIN:
                        o, od = LOSS, cd + 1
                    else:
                        o, od = UNKNOWN, 0
                if o == WIN and od > DTZ_MAX:      # a win past the clock is a draw
                    o, od = UNKNOWN, 0
                rank = {LOSS: 0, UNKNOWN: 1, WIN: 2}
                if rank[o] > rank[best]:
                    best, bd = o, od
                elif o == best == WIN and od < bd:
                    bd = od                        # winner minimises dtz
                elif o == best == LOSS and (bd is None or od > bd):
                    bd = od                        # loser maximises dtz
            got, gd = best, (bd or 0)
            if got == LOSS and gd > DTZ_MAX:
                got, gd = UNKNOWN, 0
        if wdl[idx] != got or (got != UNKNOWN and dtz[idx] != gd):
            bad += 1
            if bad <= 4:
                print("   FIXPOINT MISMATCH sqs=%s stm=%d table=(%d,%d) recomputed=(%d,%d)"
                      % (sqs, stm, wdl[idx], dtz[idx], got, gd))
    return bad, tested

def clock_dp(sig, tables):
    """Fully independent check: backward induction on the clock.  No reverse moves, no
    counters.  (signature, clock) is acyclic because every zeroing move leaves the
    signature, so V[c] depends only on V[c+1] and on already-solved smaller signatures."""
    pcs = pieces_of(sig)
    n = len(pcs)
    runs = runs_of(pcs)
    pw = [R.NSQ ** i for i in range(n)]
    size = 2 * R.NSQ ** n
    own_goal = [R.GOAL[c] for c, _ in pcs]

    live = []
    moves = {}
    for sqs in placements(pcs, runs):
        if any(sqs[i] == own_goal[i] for i in range(n)):
            continue
        occ = {s: i for i, s in enumerate(sqs)}
        for stm in (0, 1):
            idx = encode(sqs, stm, pw)
            live.append(idx)
            moves[idx] = (sqs, occ, legal(pcs, sqs, stm, occ))

    nxt = bytearray(size)            # V[c+1]; at c = 200 the clock draws (terminal first)
    for idx in live:
        if not moves[idx][2]:
            nxt[idx] = LOSS          # search.cpp:131 -- stalemate is tested before the clock
    cmax = array('h', bytes(2 * size))
    for idx in live:
        cmax[idx] = -1
    v200 = bytes(nxt)
    for idx in live:
        if nxt[idx] != UNKNOWN:
            cmax[idx] = DTZ_MAX

    for c in range(DTZ_MAX - 1, -1, -1):
        cur = bytearray(size)
        for idx in live:
            sqs, occ, mv = moves[idx]
            stm = idx & 1
            if not mv:
                cur[idx] = LOSS
                continue
            goal = R.GOAL[stm]
            best = LOSS
            for frm, to, cap in mv:
                if to == goal:
                    best = WIN
                    break
                if cap >= 0:
                    cv = capture_child(pcs, sqs, occ[frm], cap, to, stm, tables)
                    o = WIN if (cv is None or cv == LOSS) else (UNKNOWN if cv == UNKNOWN else LOSS)
                else:
                    qs = canon([(to if k == occ[frm] else sqs[k]) for k in range(n)], runs)
                    cv = nxt[encode(qs, 1 - stm, pw)]
                    o = WIN if cv == LOSS else (LOSS if cv == WIN else UNKNOWN)
                if o == WIN:
                    best = WIN
                    break
                if o == UNKNOWN:
                    best = UNKNOWN
            cur[idx] = best
        for idx in live:
            if cur[idx] != UNKNOWN and cmax[idx] < c:
                cmax[idx] = c
        nxt = cur
    return nxt, cmax, pw, live, v200

# ----------------------------------------------------------------- main
def main():
    rng = random.Random(SEED)
    print("-----")
    print("movegen cross-check vs rps_rules on %d random positions ..." % CROSSCHECK_MOVEGEN)
    bad = crosscheck_movegen(rng, CROSSCHECK_MOVEGEN)
    print("   %s (%d mismatches)" % ("OK" if bad == 0 else "FAILED", bad))
    if bad:
        return

    sigs = ONLY_SIGS if ONLY_SIGS else signatures(MAX_PIECES)
    print("-----")
    print("%d colour-canonical signatures up to %d pieces" % (len(sigs), MAX_PIECES))
    tables = {}
    tot_bytes, tot_z, tot_x, tot_clamp = [0], [0], [0], [0]
    tot_live = tot_win = tot_loss = tot_draw = 0
    for k, sig in enumerate(sigs):
        t0 = time.time()
        wdl, dtz, pw, live = solve(sig, tables)
        tables[sig] = (wdl, dtz, pw)
        w = l = 0
        mx = 0
        for i in range(len(wdl)):
            v = wdl[i]
            if v == WIN:
                w += 1
                if dtz[i] > mx:
                    mx = dtz[i]
            elif v == LOSS:
                l += 1
                if dtz[i] > mx:
                    mx = dtz[i]
        dr = live - w - l
        tot_live += live
        tot_win += w
        tot_loss += l
        tot_draw += dr
        print("  %-24s live %8d  W %7d (%5.1f%%)  L %7d (%5.1f%%)  D %7d (%5.1f%%)"
              "  maxDTZ %3d  %5.1fs"
              % (str(sig), live, w, 100.0 * w / live, l, 100.0 * l / live,
                 dr, 100.0 * dr / live, mx, time.time() - t0))
        b, t = fixpoint_check(sig, tables, rng, FIXPOINT_N)
        print("     fixpoint self-check: %s (%d/%d bad)" % ("OK" if b == 0 else "FAILED", b, t))
        blob, nlive, clamped = pack(sig, tables)
        z = len(zlib.compress(blob, 9))
        x = len(lzma.compress(blob, preset=6))
        tot_bytes[0] += nlive
        tot_z[0] += z
        tot_x[0] += x
        tot_clamp[0] += clamped
        if WRITE_TABLES:
            os.makedirs(OUT_DIR, exist_ok=True)
            name = "%d%d%d_%d%d%d" % (sig[0] + sig[1])
            with open(os.path.join(OUT_DIR, name + ".dtz"), "wb") as f:
                f.write(blob)
        print("     packed %8d B  zlib %8d B (%4.2fx)  lzma %8d B (%5.2fx)  clamped %d"
              % (nlive, z, nlive / z, x, nlive / x, clamped))
        if sum(sig[0]) + sum(sig[1]) <= CLOCK_DP_MAX_PIECES:
            t0 = time.time()
            v0, cmax, pw2, live2, _ = clock_dp(sig, tables)
            nb = nd = 0
            for i in live2:
                if v0[i] != wdl[i]:
                    nb += 1
                    if nb <= 3:
                        print("     DP WDL MISMATCH idx=%d dp=%d tb=%d" % (i, v0[i], wdl[i]))
                elif v0[i] != UNKNOWN and (DTZ_MAX - cmax[i]) != dtz[i]:
                    nd += 1
                    if nd <= 3:
                        print("     DP DTZ MISMATCH idx=%d dp=%d tb=%d"
                              % (i, DTZ_MAX - cmax[i], dtz[i]))
            print("     independent clock DP: %s (%d wdl, %d dtz bad of %d)  %5.1fs"
                  % ("OK" if nb + nd == 0 else "FAILED", nb, nd, len(live2), time.time() - t0))
    print("-----")
    print("TOTAL live %d  W %.1f%%  L %.1f%%  D %.1f%%"
          % (tot_live, 100.0 * tot_win / tot_live, 100.0 * tot_loss / tot_live,
             100.0 * tot_draw / tot_live))
    print("TOTAL packed %d B  zlib %.2fx  lzma %.2fx  clamped %d (%.4f%%)"
          % (tot_bytes[0], tot_bytes[0] / max(tot_z[0], 1), tot_bytes[0] / max(tot_x[0], 1),
             tot_clamp[0], 100.0 * tot_clamp[0] / max(tot_bytes[0], 1)))
    print("prototype build footprint: wdl 1 + dtz 2 + safe 1 + zdr 1 = 5 B/entry unpacked;"
          " a C build packs dtz 1 B + (wdl 2 bits | safe 6 bits) 1 B = 2 B/entry")

main()
