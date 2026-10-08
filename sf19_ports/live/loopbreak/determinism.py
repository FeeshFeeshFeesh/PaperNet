import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # for live_boards
sys.path.insert(0, HERE)

"""From the SAME position, how often does each side repeat its move?

Their position = the board right after our move (a snapshot, exactly).
Our  position = that board plus their reply -- also exact, so the pair
(board, their_reply) identifies the position we then moved from.

A side that always answers a given position the same way can hold a loop
forever.  A side that does not, leaves the loop by accident.
"""
import sys
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(HERE))
import live_boards as lb

def key(rec):
    return tuple(tuple(rec["rows"][r]) for r in range(1, 10))

for label, lo in (("near-draw (counter >= 100)", 100), ("all plies", 0)):
    theirs = defaultdict(Counter)      # board            -> their moves
    ours   = defaultdict(Counter)      # (board, reply)   -> our moves
    for g in lb.parse():
        sn = g["plies"]
        for i, s in enumerate(sn):
            c = s.get("counter")
            if c is None or c < lo:
                continue
            k = key(s)
            if s.get("their_reply"):
                theirs[k][s["their_reply"]] += 1
            if i + 1 < len(sn) and s.get("their_reply") and sn[i + 1].get("our_move"):
                ours[(k, s["their_reply"])][sn[i + 1]["our_move"]] += 1

    print("-----%s-----" % label)
    print("%-6s %-10s %-10s %-9s %-9s %s" %
          ("side", "positions", "revisits", "1 move", ">1 move", "P(same move again)"))
    for who, d in (("them", theirs), ("us", ours)):
        rep = {k: c for k, c in d.items() if sum(c.values()) >= 3}
        if not rep:
            continue
        one  = sum(1 for c in rep.values() if len(c) == 1)
        many = len(rep) - one
        # probability that a repeat visit reproduces the earlier move
        agree = tot = 0
        for c in rep.values():
            n = sum(c.values())
            agree += sum(v * (v - 1) for v in c.values())
            tot   += n * (n - 1)
        print("%-6s %-10d %-10d %-9d %-9d %.3f" %
              (who, len(rep), sum(sum(c.values()) for c in rep.values()), one, many,
               agree / tot if tot else 0))
    print()
print("confirm")
