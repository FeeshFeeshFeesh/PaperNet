"""R1 verdict. Does nc_draw_grade reopen the collapsed root -- and DO THE TWO SIGNS DIFFER?

PRE-REGISTERED READING, fixed before the arms finished:
  `plus` (NC_DRAW_SIGN=+1) already returned H0 at -23 Elo in a real 500 ms SPRT.
  So if plus reopens the spread just as much as minus, then "reopens the spread"
  DOES NOT PREDICT ELO, and R1 must not be given a cloud host on that evidence.
  Only a DIFFERENTIAL between the signs justifies spending one.

The statistic is the baseline's own: on real-clock ("high") positions, how many of
the 254 have spread == 0 across the top-MultiPV root moves -- the flat zone. The
clock-zeroed ("low") rows are the within-position control; if they move too, the
effect is not about the draw at all.
"""
import os
import json
import collections

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [("base",  os.path.join(HERE, "sweep_band.jsonl")),
         ("minus", os.path.join(HERE, "sweep_band_minus.jsonl")),
         ("plus",  os.path.join(HERE, "sweep_band_plus.jsonl"))]
RUNGS = (20000, 50000, 100000, 200000, 400000, 800000)


def load(path):
    """-> {(game,ply,arm,ask_nodes): row}"""
    d = {}
    if not os.path.exists(path):
        return d
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        d[(r["game"], r["ply"], r["arm"], r["ask_nodes"])] = r
    return d


def main():
    data = {n: load(p) for n, p in FILES}
    for n, p in FILES:
        print("  %-6s %5d rows  %s" % (n, len(data[n]), "" if data[n] else "(MISSING)"))
    print()

    have = [n for n, _ in FILES if data[n]]

    print("=== FLAT ZONE: positions with spread == 0 (of those measured)")
    print("  rung      " + "".join("%-18s" % n for n in have))
    for nd in RUNGS:
        cells = []
        for n in have:
            hi = [r for (g, p, a, k), r in data[n].items() if a == "high" and k == nd]
            flat = sum(1 for r in hi if r["spread"] == 0)
            cells.append("%3d/%-3d (%4.1f%%)   " % (flat, len(hi),
                                                   100.0 * flat / len(hi) if hi else 0))
        print("  %7d   %s" % (nd, "".join(cells)))
    print()

    print("=== CONTROL: same boards, capture clock rewritten to 0")
    print("  rung      " + "".join("%-18s" % n for n in have))
    for nd in RUNGS:
        cells = []
        for n in have:
            lo = [r for (g, p, a, k), r in data[n].items() if a == "low" and k == nd]
            flat = sum(1 for r in lo if r["spread"] == 0)
            cells.append("%3d/%-3d (%4.1f%%)   " % (flat, len(lo),
                                                    100.0 * flat / len(lo) if lo else 0))
        print("  %7d   %s" % (nd, "".join(cells)))
    print()

    # paired, position by position, at the deepest rung
    nd = RUNGS[-1]
    print("=== PAIRED at %d nodes (only positions present in BOTH files)" % nd)
    for a, b in (("base", "minus"), ("base", "plus"), ("minus", "plus")):
        if not (data[a] and data[b]):
            continue
        keys = [k for k in data[a] if k in data[b] and k[2] == "high" and k[3] == nd]
        a_flat_only = sum(1 for k in keys
                          if data[a][k]["spread"] == 0 and data[b][k]["spread"] != 0)
        b_flat_only = sum(1 for k in keys
                          if data[b][k]["spread"] == 0 and data[a][k]["spread"] != 0)
        moved = sum(1 for k in keys
                    if data[a][k]["scores"] and data[b][k]["scores"]
                    and max(data[a][k]["scores"], key=data[a][k]["scores"].get)
                    != max(data[b][k]["scores"], key=data[b][k]["scores"].get))
        print("  %-5s vs %-5s  n=%3d | flat only in %s: %3d | flat only in %s: %3d"
              " | BEST MOVE differs: %3d (%.1f%%)"
              % (a, b, len(keys), a, a_flat_only, b, b_flat_only, moved,
                 100.0 * moved / len(keys) if keys else 0))
    print()

    if data["minus"] and data["plus"]:
        nd = RUNGS[-1]
        keys = [k for k in data["minus"] if k in data["plus"]
                and k[2] == "high" and k[3] == nd]
        m_flat = sum(1 for k in keys if data["minus"][k]["spread"] == 0)
        p_flat = sum(1 for k in keys if data["plus"][k]["spread"] == 0)
        base_flat = sum(1 for k in keys
                        if k in data["base"] and data["base"][k]["spread"] == 0)
        print("=== VERDICT (pre-registered)")
        print("  flat at %d nodes -- base %d, minus %d, plus %d (of %d shared)"
              % (nd, base_flat, m_flat, p_flat, len(keys)))
        if abs(m_flat - p_flat) <= max(3, 0.05 * len(keys)):
            print("  -> THE SIGNS DO NOT DIFFER. 'Reopens the spread' cannot predict Elo,")
            print("     because plus ALREADY measured -23 Elo. R1 gets NO cloud host.")
        else:
            print("  -> THE SIGNS DIFFER. The statistic separates an arm that lost Elo")
            print("     from the untested arm, so an SPRT of `minus` is justified --")
            print("     and it must run against the LIVE STICKY-LOCK binary, since the")
            print("     locked zone is exactly where contempt would otherwise bite.")


if __name__ == "__main__":
    main()
