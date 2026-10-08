"""Read the block-compressed <=5-piece tablebase and answer exact values for a position.

WHAT THIS IS FOR. tb_solve produced 1.008 GiB of exact distance-to-zeroing values and nothing
can read them at a position. This is that reader: FEN or piece list in, exact win/loss/draw
and distance out. It is the offline half -- the engine gets a C++ port of the same arithmetic
once the gate below is answered.

THE INDEX is tb_pack's: dense, big-endian over squares, idx = 2*SUM sqs[i]*81^(n-1-i) + stm,
with identical pieces sorted into increasing square order. See tb_pack.c for why dense.

THE FOLD is tb_solve's probe(), replicated element for element. Only one signature per orbit
of the order-6 group (sigma: R->P->S on types, composed with the colour swap, which also
reflects the board through its centre because that is what exchanges the two goals) was ever
built. So a lookup walks the orbit until it lands on a table that exists. Several elements may
reach it; any of them is a valid answer, and that IS the symmetry.

VALUE ENCODING, from tb_solve unchanged:
    0        draw, dead, or a win/loss clamped out of range
    1..127   the side to move WINS, in that many zeroing moves
    128..255 the side to move LOSES, in (v - 127)

Run from PyCharm. No sys.argv; constants below.
"""
import os
import struct

import zstandard

TBZ_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tbz")

NSQ = 81
FILES = RANKS = 9
GOAL = (80, 0)              # WHITE -> i9, BLACK -> a1
SIGMA = {0: 1, 1: 2, 2: 0}  # R->P, P->S, S->R  (0-based types)
BLOCK_LOG = 16
BLOCK = 1 << BLOCK_LOG
CACHE_BLOCKS = 256

DRAW, WIN, LOSS = "draw", "win", "loss"


def sq_name(s):
    return "abcdefghi"[s % 9] + str(s // 9 + 1)


def name_sq(t):
    return (int(t[1]) - 1) * 9 + "abcdefghi".index(t[0])


class Table:
    """One signature's .tbz, block-cached."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        hdr = self.f.read(48)
        if hdr[:7] != b"RPSTBZ1":
            raise ValueError("%s: not a RPSTBZ1 table" % path)
        self.n = hdr[8]
        self.wc = tuple(hdr[9:12])
        self.bc = tuple(hdr[12:15])
        if hdr[15] != BLOCK_LOG:
            raise ValueError("%s: block log %d, expected %d" % (path, hdr[15], BLOCK_LOG))
        self.dense, self.nblk = struct.unpack("<qq", hdr[16:32])
        self.blkoff = struct.unpack("<%dI" % (self.nblk + 1), self.f.read(4 * (self.nblk + 1)))
        self.payoff = 48 + 4 * (self.nblk + 1)
        self.dc = zstandard.ZstdDecompressor()
        self.cache = {}
        self.order = []
        self.bepw = [81 ** (self.n - 1 - i) for i in range(self.n)]

    def block(self, b):
        hit = self.cache.get(b)
        if hit is not None:
            return hit
        lo, hi = self.blkoff[b], self.blkoff[b + 1]
        self.f.seek(self.payoff + lo)
        raw = self.f.read(hi - lo)
        size = min(BLOCK, self.dense - b * BLOCK)
        m = raw[0]
        if m == 0:                                  # CONST
            out = bytes([raw[1]]) * size
        elif m == 1:                                # ZSTD
            out = self.dc.decompress(raw[1:], max_output_size=size)
        elif m == 2:                                # RAW
            out = raw[1:1 + size]
        else:
            raise ValueError("%s: block %d has unknown method %d" % (self.path, b, m))
        if len(out) != size:
            raise ValueError("%s: block %d gave %d bytes, expected %d" % (self.path, b, len(out), size))
        self.cache[b] = out
        self.order.append(b)
        if len(self.order) > CACHE_BLOCKS:
            del self.cache[self.order.pop(0)]
        return out

    def at(self, idx):
        if not 0 <= idx < self.dense:
            raise IndexError("%s: index %d outside 0..%d" % (self.path, idx, self.dense - 1))
        return self.block(idx >> BLOCK_LOG)[idx & (BLOCK - 1)]

    def value(self, sqs, stm):
        """sqs already in canonical slot order for THIS signature."""
        be = 0
        for i, s in enumerate(sqs):
            be += s * self.bepw[i]
        return self.at(2 * be + stm)


class TB:
    def __init__(self, directory=TBZ_DIR):
        self.dir = directory
        self.tables = {}
        self.tags = set()
        if os.path.isdir(directory):
            for fn in os.listdir(directory):
                if fn.endswith(".tbz"):
                    self.tags.add(fn[:-4])

    def table(self, tag):
        t = self.tables.get(tag)
        if t is None:
            t = self.tables[tag] = Table(os.path.join(self.dir, tag + ".tbz"))
        return t

    @staticmethod
    def tag_of(wc, bc):
        return "%d%d%d_%d%d%d" % (wc[0], wc[1], wc[2], bc[0], bc[1], bc[2])

    def probe(self, pieces, stm):
        """pieces = [(colour, type, square)], colour 0=WHITE/Blue 1=BLACK/Red, type 0=R 1=P 2=S.

        Returns (kind, dtz) for the side to move, or None when no table covers it.
        """
        n = len(pieces)
        if n < 2 or n > 5:
            return None
        for k in range(3):
            for sw in (0, 1):
                wc, bc, slots = [0, 0, 0], [0, 0, 0], []
                for (c, t, q) in pieces:
                    tt = t
                    for _ in range(k):
                        tt = SIGMA[tt]
                    cc, qq = c, q
                    if sw:
                        cc = 1 - cc
                        qq = NSQ - 1 - qq       # centre reflection swaps the goals
                    slots.append((cc, tt, qq))
                    (wc if cc == 0 else bc)[tt] += 1
                tag = self.tag_of(wc, bc)
                if tag not in self.tags:
                    continue
                st2 = 1 - stm if sw else stm
                slots.sort()                     # (colour, type, square): the canonical order
                if any(q == GOAL[c] for (c, t, q) in slots):
                    return None                  # terminal: not stored, the game is already over
                v = self.table(tag).value([q for (_, _, q) in slots], st2)
                if v == 0:
                    return (DRAW, 0)
                if v <= 127:
                    return (WIN, v)
                return (LOSS, v - 127)
        return None

    def probe_fen(self, fen):
        pieces, stm = parse_fen(fen)
        return self.probe(pieces, stm)


def parse_fen(fen):
    """'9/9/.../P8 b - - 165 138' -> ([(colour, type, square)], stm).

    First group is rank 9; uppercase is WHITE/Blue (goal i9), lowercase BLACK/Red (goal a1).
    """
    parts = fen.split()
    rows = parts[0].split("/")
    if len(rows) != RANKS:
        raise ValueError("FEN has %d ranks, expected %d: %s" % (len(rows), RANKS, fen))
    tmap = {"r": 0, "p": 1, "s": 2}
    pieces = []
    for gi, row in enumerate(rows):
        r = RANKS - 1 - gi
        f = 0
        num = ""
        for ch in row:
            if ch.isdigit():
                num += ch
                continue
            if num:
                f += int(num)
                num = ""
            key = ch.lower()
            if key not in tmap:
                raise ValueError("FEN has unknown piece %r: %s" % (ch, fen))
            pieces.append((0 if ch.isupper() else 1, tmap[key], r * 9 + f))
            f += 1
        if num:
            f += int(num)
        if f != FILES:
            raise ValueError("FEN rank %d covers %d files, expected %d: %s" % (r + 1, f, FILES, fen))
    stm = 0 if len(parts) < 2 or parts[1] == "w" else 1
    return pieces, stm


def main():
    tb = TB()
    print("-----")
    print("%d tables in %s" % (len(tb.tags), tb.dir))
    for tag in sorted(tb.tags)[:5]:
        t = tb.table(tag)
        print("  %s  n=%d  dense=%d  blocks=%d" % (tag, t.n, t.dense, t.nblk))


if __name__ == "__main__":
    main()
