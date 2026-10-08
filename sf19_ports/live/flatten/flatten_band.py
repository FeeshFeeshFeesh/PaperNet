"""Extract REAL positions where WE are to move, we are ahead, and the capture
clock is high -- the exact shape the conversion defect lives in -- and emit them
as engine FENs.

Why it can be done at all: a status-log snapshot is the board AFTER our move, so
THEY are to move in it. The position with US to move is snapshot k+1 with its
`our_move` UNDONE. Undoing is only safe when that move captured nothing, which is
precisely the case we want: high clock means no captures are happening. We check
it by requiring both piece counts to be unchanged across the pair.

Everything here is offline: no engine, no network. `validate.py` next door checks
the FENs against the engine's own move generator.
"""

import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # sf19_ports/live

import live_boards

OUT = os.path.join(HERE, "positions_band.jsonl")

MIN_CLOCK = 165        # BAND VARIANT: the transition zone only. The main sample
MAX_CLOCK = 194        # had 44 positions at 119-129 and 40 at 195-199 but only
                       # 2 in 170-194 -- exactly where "depth >= plies left" first
                       # bites, i.e. where "deeper search blinds earlier" lives.
MIN_AHEAD = 1          # pieces up; the side to move has something to convert
MAX_PER_GAME = 6       # don't let one long shuffle dominate the sample
                       # ...and spread those 3 across the game's clock RANGE, not
                       # its first 3 qualifying plies: taking the first 3 pinned
                       # every sample to clock 119-124, right at the threshold.


def fen(rows, we_are_blue, clock, ply):
    """rows[rank] = 9 glyphs, uppercase Blue. Engine FEN: ranks 9..1, uppercase
    is Blue(white). We are to move."""
    out = []
    for rank in range(9, 0, -1):
        run, cells = 0, ""
        for glyph in rows[rank]:
            if glyph.upper() in ("R", "P", "S"):
                if run:
                    cells += str(run)
                    run = 0
                cells += glyph
            else:
                run += 1
        if run:
            cells += str(run)
        out.append(cells)
    stm = "w" if we_are_blue else "b"
    return "%s %s - - %d %d" % ("/".join(out), stm, clock, ply // 2 + 1)


def undo(rows, move):
    """move is 'd5-e6' or 'd5e6'. Put the glyph on `to` back on `from`."""
    m = move.replace("-", "")
    if len(m) != 4:
        return None
    frm, to = m[:2], m[2:]
    fr, tr = int(frm[1]), int(to[1])
    ff, tf = ord(frm[0]) - 97, ord(to[0]) - 97
    if not (0 <= ff < 9 and 0 <= tf < 9 and 1 <= fr <= 9 and 1 <= tr <= 9):
        return None
    new = {r: list(rows[r]) for r in rows}
    glyph = new[tr][tf]
    if glyph.upper() not in ("R", "P", "S"):
        return None                                  # nothing there: not our move
    if new[fr][ff].upper() in ("R", "P", "S"):
        return None                                  # origin occupied: not reversible
    new[tr][tf] = "."
    new[fr][ff] = glyph
    return new


def main():
    games = live_boards.parse()
    rows_out = []
    for gi, g in enumerate(games):
        plies = g["plies"]
        cand = []
        for k in range(1, len(plies)):
            a, b = plies[k - 1], plies[k]
            need = ("counter", "mine", "theirs")
            if any(f not in a or f not in b for f in need):
                continue
            if not b.get("our_move"):
                continue
            if a["mine"] != b["mine"] or a["theirs"] != b["theirs"]:
                continue                             # a capture happened: can't undo
            if not (MIN_CLOCK <= b["counter"] <= MAX_CLOCK):
                continue
            if b["mine"] - b["theirs"] < MIN_AHEAD:
                continue
            r = undo(b["rows"], b["our_move"])
            if r is None:
                continue
            we_are_blue = b["me"] == "Blue"
            cand.append({
                "game": gi,
                "ply": b["ply"] - 1,
                "clock": b["counter"] - 1,
                "mine": b["mine"],
                "theirs": b["theirs"],
                "our_move": b["our_move"].replace("-", ""),
                "eval": b.get("eval"),
                "depth": b.get("depth"),
                "opponent": g["opponent"],
                "reason": g["reason"],
                "fen": fen(r, we_are_blue, b["counter"] - 1, b["ply"] - 1),
            })

        # keep the lowest, middle and highest clock this game offers
        cand.sort(key=lambda r: r["clock"])
        if len(cand) <= MAX_PER_GAME:
            rows_out.extend(cand)
        else:
            idx = {0, len(cand) // 2, len(cand) - 1}
            while len(idx) < MAX_PER_GAME:
                idx.add(len(cand) * (len(idx) + 1) // (MAX_PER_GAME + 1))
            rows_out.extend(cand[i] for i in sorted(idx))

    with open(OUT, "w") as f:
        for r in rows_out:
            f.write(json.dumps(r) + "\n")

    print("games: %d" % len(games))
    print("candidate positions (we to move, ahead >=%d, clock >=%d): %d"
          % (MIN_AHEAD, MIN_CLOCK, len(rows_out)))
    print("distinct games represented: %d" % len({r["game"] for r in rows_out}))
    if rows_out:
        cl = sorted(r["clock"] for r in rows_out)
        ah = sorted(r["mine"] - r["theirs"] for r in rows_out)
        print("clock   min/med/max: %d / %d / %d" % (cl[0], cl[len(cl) // 2], cl[-1]))
        print("ahead   min/med/max: %d / %d / %d" % (ah[0], ah[len(ah) // 2], ah[-1]))
        print("\nfirst 3:")
        for r in rows_out[:3]:
            print("  g%-4d ply %-4d clock %-4d %dv%d  move %s  eval %s"
                  % (r["game"], r["ply"], r["clock"], r["mine"], r["theirs"],
                     r["our_move"], r["eval"]))
            print("        %s" % r["fen"])
    print("\nwrote %s" % OUT)


if __name__ == "__main__":
    main()
