"""R4's free gate: on the band positions a <=5-piece table covers, did the engine's move
actually throw the result?

THE QUESTION THIS DECIDES. We own 1.008 GiB of exact values and nothing reads them. Wiring a
probe into search is days of work, and a 6-piece table on top is a new out-of-core solver plus
A$300-600. Both are only worth it if exact values would have CHANGED A MOVE. So before any of
that: take the 254-position near-draw band, keep the ones the table covers, and ask whether the
move actually played preserved the game-theoretic result.

WDL, NOT DISTANCE, IS THE GATE. tb_solve stores distance-to-terminal in plies and clamps wins
past 127 to 0, and the 200-ply no-capture rule interacts with that in ways a single number
cannot express. Throwing a WIN into a DRAW, or a DRAW into a LOSS, needs none of that detail --
and it is the only part that is worth Elo.

WHAT A CLEAN RESULT MEANS. If the engine already preserves the result everywhere the table can
see, then probing <=5 buys nothing, and 6 pieces -- which only widens coverage from 62.2% to
82.3% of the same band -- cannot be rescued by more of the same. That closes R4 honestly and
for free. A material throw rate is the opposite verdict and justifies the probe.

Run from PyCharm. No sys.argv; constants below.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BAND = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                    "sf19_ports", "live", "flatten", "positions_band.jsonl")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import rps_rules as R
import tb_probe
import tb_wdl
from tb_probe import DRAW, WIN, LOSS
from tb_wdl import ORDER, MAX_PIECES, npieces, to_probe_pieces, verdict

SHOW_EXAMPLES = 12


def main():
    tb = tb_probe.TB()
    rows = [json.loads(l) for l in open(BAND) if l.strip()]
    print("-----")
    print("%d band positions, %d tables loaded" % (len(rows), len(tb.tags)))

    covered = uncovered = 0
    partial = 0
    throws = []
    agree = 0
    byres = {}
    for i, row in enumerate(rows):
        p = R.parse_fen(row["fen"])
        n = npieces(p)
        if n > MAX_PIECES:
            uncovered += 1
            continue
        root = verdict(tb, p)
        if root is None:
            uncovered += 1
            continue
        moves = R.gen_legal(p)
        kids = [(m, tb_wdl.move_value(tb, p, m)) for m in moves]
        if any(v is None for _, v in kids):
            partial += 1
            continue
        covered += 1
        best = max(ORDER[v[0]] for _, v in kids)
        byres[root[0]] = byres.get(root[0], 0) + 1

        played = row.get("our_move")
        pm = [(m, v) for m, v in kids if R.sq_name(m[0]) + R.sq_name(m[1]) == played]
        if not pm:
            continue
        got = ORDER[pm[0][1][0]]
        if got == best:
            agree += 1
        else:
            throws.append((i, row, root, pm[0][1], best, kids))

    print("-----")
    print("covered by the <=%d table : %d" % (MAX_PIECES, covered))
    print("not covered              : %d" % uncovered)
    print("covered root, uncovered child: %d" % partial)
    if covered:
        print("root verdict mix         : %s" % ", ".join("%s %d" % kv for kv in sorted(byres.items())))
        judged = agree + len(throws)
        print("-----")
        print("moves judged             : %d" % judged)
        print("preserved the result     : %d" % agree)
        print("THREW the result         : %d  (%.1f%%)"
              % (len(throws), 100.0 * len(throws) / judged if judged else 0.0))

    names = {2: "WIN", 1: "DRAW", 0: "LOSS"}
    for (i, row, root, pv, best, kids) in throws[:SHOW_EXAMPLES]:
        bestmoves = [R.sq_name(m[0]) + R.sq_name(m[1]) for m, v in kids if ORDER[v[0]] == best]
        print("  game %s ply %s clock %s: played %s -> %s, but %s -> %s   (root %s in %s)"
              % (row.get("game"), row.get("ply"), row.get("clock"), row.get("our_move"),
                 names[ORDER[pv[0]]], "/".join(bestmoves[:4]), names[best], root[0], root[1]))


if __name__ == "__main__":
    main()
