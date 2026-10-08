"""Exact game values for small material families on the real 9x9 board.

WHY THIS EXISTS
---------------
Two probes established that the engine ALREADY charges about 330 cp (1.6 stones) for closing one
of its own capture classes -- 13.4 sigma, on matched synthetic pairs -- and that adding a second,
explicit charge on top changes nothing that matters (net +2 and +3 of ~10 discordant move flips,
p = 0.75 and 0.51). So the conversion defect is not "the engine does not know". What is still open
is whether 330 cp is the RIGHT price, and nothing measured against the engine can answer that: the
engine's own opinion is the thing under test. Only an exact solve gives a ground truth.

The comparison this makes possible is direct. Two families with the same piece COUNTS that differ
in one piece's TYPE differ only in the capture graph -- type affects nothing else, because every
piece moves as a king and any piece reaching the goal corner wins. The gap in exact expected score
between such a pair is the true worth of that class.

RULES, read off the referee itself (sploppy engine/src/rules.rs, the authority for our matches):
  * king steps; a move lands on an empty square or on an enemy this piece beats, nothing else
  * rock beats scissors, paper beats rock, scissors beats paper -- so a piece is only ever removed
    by the single type that beats it, which is what makes a class CLOSED once it is emptied
  * a move wins if it reaches the mover's goal, OR takes the opponent's last piece, OR leaves the
    opponent with no legal move (rules.rs:312, in that precedence)
  * 200 plies without a capture draws

Blue's goal is square 80, Red's is square 0.

SCOPE. 1v1 and 2v1 families, solved exactly. A 2v1 family's captures lead only to 1v1 (Blue taking
Red's only piece ends the game outright), so this set is closed under capture and needs nothing
larger. 2v2 would need 80M states per family, which is past what Python can carry here.

WHAT IT DOES NOT CLAIM. It averages uniformly over all legal placements, which is not the
distribution real games reach, so it is a structural measurement rather than a frequency claim
about our games. That is the point: sampling the positions our engine actually reaches would
condition on the very behaviour under test.

Resumable: each family is written to the JSON as it finishes and skipped on a restart. No sys.argv.
"""
import array
import itertools
import json
import os
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK

NSQ = 81
SIDE = 9
BLUE_GOAL = 80
RED_GOAL = 0
OUT = os.path.join(S, "class_tablebase.json")

R, P, Sc = 0, 1, 2
NAME = {R: "R", P: "P", Sc: "S"}
PREY = {R: Sc, P: R, Sc: P}          # what each type captures
WIN, LOSS, DRAW = 1, 2, 0
NEVER = 1 << 30


def build_adj():
    adj = []
    for sq in range(NSQ):
        r, f = divmod(sq, SIDE)
        out = []
        for dr in (-1, 0, 1):
            for df in (-1, 0, 1):
                if dr or df:
                    nr, nf = r + dr, f + df
                    if 0 <= nr < SIDE and 0 <= nf < SIDE:
                        out.append(nr * SIDE + nf)
        adj.append(tuple(out))
    return adj


ADJ = build_adj()


class Family(object):
    """One material class. Pieces are held in a fixed order: Blue's types sorted, then Red's.

    Identical pieces are interchangeable, so a position is canonicalised by sorting the squares
    within each run of equal (owner, type). Without that the same position would be solved twice
    under two names and the averages would be silently double-weighted.
    """

    def __init__(self, blue, red):
        self.blue = tuple(sorted(blue))
        self.red = tuple(sorted(red))
        self.n = len(self.blue) + len(self.red)
        self.owner = (0,) * len(self.blue) + (1,) * len(self.red)
        self.types = self.blue + self.red
        self.goal = (BLUE_GOAL, RED_GOAL)
        self.runs = []
        i = 0
        while i < self.n:
            j = i
            while j + 1 < self.n and (self.owner[j + 1], self.types[j + 1]) == (self.owner[i], self.types[i]):
                j += 1
            if j > i:
                self.runs.append((i, j + 1))
            i = j + 1

    def key(self):
        return "".join(NAME[t] for t in self.blue) + "v" + "".join(NAME[t] for t in self.red)

    def closed_classes(self, side):
        """Types this side holds none of, whose prey the other side actually has on the board.

        Holding no rocks only matters when the enemy has scissors to be made immune. This mirrors
        closed_classes() in the engine's evaluate.cpp so the two numbers are comparable.
        """
        mine = self.blue if side == 0 else self.red
        theirs = self.red if side == 0 else self.blue
        return sum(1 for t in (R, P, Sc) if t not in mine and PREY[t] in theirs)


def canon(fam, sq):
    if not fam.runs:
        return sq
    out = list(sq)
    for a, b in fam.runs:
        out[a:b] = sorted(out[a:b])
    return tuple(out)


def index_of(sq):
    i = 0
    for s in sq:
        i = i * NSQ + s
    return i


def decode(i, n):
    out = [0] * n
    for k in range(n - 1, -1, -1):
        out[k] = i % NSQ
        i //= NSQ
    return tuple(out)


def legal_placement(fam, sq):
    if len(set(sq)) != fam.n:
        return False
    for i, s in enumerate(sq):
        if s == fam.goal[fam.owner[i]]:
            return False          # that side is already home: the game ended before this position
    return True


def child_of(fam, sq, i, j, to):
    """Family and canonical position after piece i captures piece j and lands on `to`."""
    rem = [k for k in range(fam.n) if k != j]
    rem.sort(key=lambda k: (fam.owner[k], fam.types[k]))
    blue = [fam.types[k] for k in rem if fam.owner[k] == 0]
    red = [fam.types[k] for k in rem if fam.owner[k] == 1]
    sub = Family(blue, red)
    return sub, canon(sub, tuple(to if k == i else sq[k] for k in rem))


def classify(fam, sq, stm, values):
    """(wins_now, within, cross) for the side to move.

    `cross` holds the values of capture moves as seen by the OPPONENT, who is to move in the child.
    """
    goal = fam.goal[stm]
    occ = {}
    for i, s in enumerate(sq):
        occ[s] = i
    within, cross = [], []
    wins_now = False
    n_enemy = sum(1 for i in range(fam.n) if fam.owner[i] != stm)
    for i in range(fam.n):
        if fam.owner[i] != stm:
            continue
        frm = sq[i]
        for to in ADJ[frm]:
            j = occ.get(to)
            if j is None:
                if to == goal:
                    wins_now = True
                    continue
                nxt = list(sq)
                nxt[i] = to
                within.append(index_of(canon(fam, tuple(nxt))))
                continue
            if fam.owner[j] == stm or fam.types[j] != PREY[fam.types[i]]:
                continue
            if to == goal or n_enemy == 1:
                wins_now = True               # goal entry, or the opponent's last piece
                continue
            sub, keep = child_of(fam, sq, i, j, to)
            cross.append(values[sub.key()][index_of(keep) * 2 + (1 - stm)])
    return wins_now, within, cross


def enumerate_states(fam):
    for sq in itertools.product(range(NSQ), repeat=fam.n):
        if legal_placement(fam, sq) and canon(fam, sq) == sq:
            yield sq


def solve(fam, values, log=None):
    """Exact retrograde solve of one family, with every smaller family already in `values`."""
    size = NSQ ** fam.n
    val = bytearray(size * 2)
    dtm = array.array("h", bytes(4 * size))
    degree = array.array("i", bytes(8 * size))
    queue = array.array("i")
    t0 = time.time()

    seen = 0
    for sq in enumerate_states(fam):
        base = index_of(sq) * 2
        for stm in (0, 1):
            idx = base + stm
            wins_now, within, cross = classify(fam, sq, stm, values)
            if wins_now or LOSS in cross:
                val[idx] = WIN
                dtm[idx] = 1
                queue.append(idx)
            elif DRAW in cross:
                degree[idx] = NEVER           # a drawing option exists, so this can never be lost
            else:
                degree[idx] = len(within)
                if not within:
                    # Either stalemated, or every capture available loses. Both are a loss.
                    val[idx] = LOSS
                    queue.append(idx)
        seen += 1
        if log and seen % 50000 == 0:
            log("    %s scan %d  %.0fs" % (fam.key(), seen, time.time() - t0))

    head = 0
    while head < len(queue):
        idx = queue[head]
        head += 1
        cur = val[idx]
        sq = decode(idx >> 1, fam.n)
        stm = idx & 1
        mover = 1 - stm                        # whoever moved INTO this state
        occ = set(sq)
        for i in range(fam.n):
            if fam.owner[i] != mover:
                continue
            for frm in ADJ[sq[i]]:
                if frm in occ:
                    continue
                prev = list(sq)
                prev[i] = frm
                prev = canon(fam, tuple(prev))
                if not legal_placement(fam, prev):
                    continue
                p = index_of(prev) * 2 + mover
                if val[p]:
                    continue
                if cur == LOSS:
                    val[p] = WIN
                    dtm[p] = dtm[idx] + 1
                    queue.append(p)
                elif degree[p] != NEVER:
                    degree[p] -= 1
                    if degree[p] == 0:
                        val[p] = LOSS
                        dtm[p] = dtm[idx] + 1
                        queue.append(p)
        if log and head % 200000 == 0:
            log("    %s back %d/%d  %.0fs" % (fam.key(), head, len(queue), time.time() - t0))
    return val, dtm


def tally(fam, val, dtm):
    """Expected score for BLUE, reported separately by who is to move."""
    out = {}
    maxdtm = 0
    for stm in (0, 1):
        w = d = l = 0
        for sq in enumerate_states(fam):
            idx = index_of(sq) * 2 + stm
            v = val[idx]
            if v:
                maxdtm = max(maxdtm, dtm[idx])
            blue_wins = (v == WIN) if stm == 0 else (v == LOSS)
            blue_loses = (v == LOSS) if stm == 0 else (v == WIN)
            if v == DRAW:
                d += 1
            elif blue_wins:
                w += 1
            elif blue_loses:
                l += 1
        n = w + d + l
        out["blue_to_move" if stm == 0 else "red_to_move"] = {
            "n": n, "win": w, "draw": d, "loss": l,
            "score": (w + 0.5 * d) / n if n else 0.0}
    out["max_dtm"] = maxdtm
    return out


def log(msg):
    print(msg)
    sys.stdout.flush()


def families():
    """1v1 first, then 2v1 and 1v2. Captures from a 2v1 family land only in 1v1, so this set is
    closed: nothing here ever needs a family that is not solved before it."""
    ones = [Family([a], [b]) for a in (R, P, Sc) for b in (R, P, Sc)]
    twos = []
    pairs = [c for c in itertools.combinations_with_replacement((R, P, Sc), 2)]
    for pair in pairs:
        for b in (R, P, Sc):
            twos.append(Family(list(pair), [b]))
            twos.append(Family([b], list(pair)))
    return ones + twos


def main():
    done = {}
    if os.path.exists(OUT):
        done = json.load(open(OUT))
        log("resuming: %d families already solved" % len(done))

    values = {}
    for fam in families():
        k = fam.key()
        size = NSQ ** fam.n
        if k in done and os.path.exists(os.path.join(S, "tb_%s.bin" % k)):
            values[k] = bytearray(open(os.path.join(S, "tb_%s.bin" % k), "rb").read())
            log("skip %-8s (already solved)" % k)
            continue
        t0 = time.time()
        val, dtm = solve(fam, values, log if fam.n > 2 else None)
        values[k] = val
        open(os.path.join(S, "tb_%s.bin" % k), "wb").write(bytes(val))
        rec = tally(fam, val, dtm)
        rec["blue_closed"] = fam.closed_classes(0)
        rec["red_closed"] = fam.closed_classes(1)
        rec["seconds"] = round(time.time() - t0, 1)
        done[k] = rec
        json.dump(done, open(OUT, "w"), indent=1)
        log("%-8s blue_to_move %.4f   red_to_move %.4f   closed b=%d r=%d   maxDTM %d   %.0fs"
            % (k, rec["blue_to_move"]["score"], rec["red_to_move"]["score"],
               rec["blue_closed"], rec["red_closed"], rec["max_dtm"], rec["seconds"]))

    log("")
    log("SYMMETRY CHECK -- the anti-diagonal mirror swaps the goal corners, so family AvB with")
    log("Blue to move is the same game as BvA with Red to move, seen from the other side.")
    worst = 0.0
    for k, rec in sorted(done.items()):
        a, b = k.split("v")
        mate = done.get(b + "v" + a)
        if not mate:
            continue
        gap = abs(rec["blue_to_move"]["score"] - (1.0 - mate["red_to_move"]["score"]))
        worst = max(worst, gap)
        if gap > 1e-9:
            log("  MISMATCH %-8s %.6f vs %.6f" % (k, rec["blue_to_move"]["score"],
                                                  1.0 - mate["red_to_move"]["score"]))
    log("  worst mismatch %.2e  %s" % (worst, "OK" if worst < 1e-9 else "SOLVER IS WRONG"))
    log("")
    log("results -> %s" % OUT)


main()
