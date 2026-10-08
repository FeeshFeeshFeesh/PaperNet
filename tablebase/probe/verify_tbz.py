"""Prove the packed table returns the SAME byte the solver wrote, for every live entry.

A tablebase that lies is worse than no tablebase: a probe that is off by one entry returns a
real, plausible value for a DIFFERENT position, and nothing downstream can detect it. So the
packed form is checked against the only ground truth there is -- the original .dtz stream --
by walking pack_rec's order in Python, reading the stream in lockstep, and asking the packed
table for that same placement through the ordinary index path.

MAX_ENTRIES caps the walk so a 5-piece signature can be spot-checked; small signatures are
exhaustive. Set TAGS to the signatures to check.

Run from PyCharm. No sys.argv; constants below.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ZST_DIR = os.path.join(os.path.dirname(HERE), "tb5_zst")
TBZ_DIR = os.path.join(HERE, "tbz")
ZSTD = os.path.expanduser("~/miniconda3/bin/zstd")

TAGS = ['001_013', '001_031', '001_040', '001_103', '001_121', '001_130', '001_202', '001_211', '001_220', '001_301', '001_310', '002_003', '002_012', '002_021', '002_030', '002_102', '002_111', '002_120', '002_201', '002_210', '002_300', '003_011', '003_101', '003_110', '010_040', '011_012', '011_021', '011_102', '011_111', '011_120', '011_201', '011_210', '040_100']
MAX_ENTRIES = 400000     # 0 = exhaustive

sys.path.insert(0, HERE)
import tb_probe
from tb_probe import NSQ, GOAL


def slots_of(wc, bc):
    """fill_pieces order: colour, then type R,P,S."""
    out = []
    for c in (0, 1):
        for t in (0, 1, 2):
            for _ in range((wc, bc)[c][t]):
                out.append((c, t))
    return out


def runs_of(slots):
    runs, i = [], 0
    while i < len(slots):
        j = i
        while j < len(slots) and slots[j] == slots[i]:
            j += 1
        runs.append((i, j))
        i = j
    return runs


def walk(slots, runs):
    """pack_rec's leaf order: lexicographic in (sqs[0..n-1]), distinct, runs increasing,
    skipping any placement with a piece on its own goal."""
    n = len(slots)
    sqs = [0] * n

    def rec(d):
        if d == n:
            for i in range(n):
                if sqs[i] == GOAL[slots[i][0]]:
                    return
            yield tuple(sqs)
            return
        lowest = 0
        for (a, b) in runs:
            if a < d < b:
                lowest = sqs[d - 1] + 1
                break
        for s in range(lowest, NSQ):
            if s in sqs[:d]:
                continue
            sqs[d] = s
            yield from rec(d + 1)

    return rec(0)


def check(tag):
    wc = tuple(int(c) for c in tag[0:3])
    bc = tuple(int(c) for c in tag[4:7])
    slots = slots_of(wc, bc)
    runs = runs_of(slots)
    table = tb_probe.Table(os.path.join(TBZ_DIR, tag + ".tbz"))

    p = subprocess.Popen([ZSTD, "-dc", os.path.join(ZST_DIR, tag + ".dtz.zst")],
                         stdout=subprocess.PIPE)
    seen = bad = 0
    nz = 0
    try:
        for sqs in walk(slots, runs):
            pair = p.stdout.read(2)
            if len(pair) != 2:
                print("  FAIL: stream ended early after %d entries" % seen)
                return False
            for stm in (0, 1):
                got = table.value(sqs, stm)
                want = pair[stm]
                if got != want:
                    bad += 1
                    if bad <= 5:
                        print("  MISMATCH at %s stm=%d: table=%d stream=%d"
                              % (tuple(tb_probe.sq_name(s) for s in sqs), stm, got, want))
                if want:
                    nz += 1
            seen += 2
            if MAX_ENTRIES and seen >= MAX_ENTRIES:
                break
    finally:
        p.stdout.close()
        p.wait()

    tail = p.returncode
    print("  %s: %d entries checked, %d non-draw, %d mismatches%s"
          % (tag, seen, nz, bad, "" if MAX_ENTRIES else " (exhaustive)"))
    return bad == 0


def main():
    print("-----")
    ok = True
    for tag in TAGS:
        ok = check(tag) and ok
    print("-----")
    print("VERDICT: %s" % ("every entry matches" if ok else "MISMATCHES FOUND"))


if __name__ == "__main__":
    main()
