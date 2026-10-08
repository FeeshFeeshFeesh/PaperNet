"""Rebuild the actual live board positions from the status log.

The status log renders the whole 9x9 board once per OUR move. Every other source
we have -- the seat logs, the result trace -- records only piece COUNTS, so this
is the only place the real fortresses survive.

Output is a request for `bot analyse`: an 81-cell `setup` in the site frame that
`analysis.rs` expects (rank 1 first, a->i, codes 1/2/3 for the side to move and
4/5/6 for the other side), plus to_move / psc / ply.

Two parsing traps, both hit on the first pass:
  * the status line has TWO shapes -- "ply N of N  .  I played X" and
    "PaperNet2  .  I'm Red  .  ply N  .  clocks: ..." -- and matching only the
    first dropped 13,211 of 24,632 boards;
  * "Joined a game in progress" recurs on every reconnect, so segmenting on it
    split 192 real games into 815. Ply numbers are monotone inside a game, so a
    DECREASE is the reliable game boundary.

Nothing here talks to the network and nothing here needs a credential; the
status log is filtered for token-shaped lines anyway.
"""

import json
import re
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

# The tree moved 2026-10: the status log now lives in NeuralNet/project_files/, and the
# old sep2026 path silently yielded zero games rather than an error.
STATUS_LOG = os.path.join(_ROOT, "project_files/papernet-status.log")
OUT_DIR = _WORK

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SECRET = re.compile(r"token|key|secret", re.I)

HEADER = re.compile(r"^\s+a b c d e f g h i\s*$")
ROW = re.compile(r"^\s+([1-9])\s+(.+?)\s*$")
WHO = re.compile(r"I'm (Blue|Red)\s+.\s+ply (\d+)")
PIECES = re.compile(
    r"pieces: me R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+"
    r"them R(\d+) P(\d+) S(\d+) \((\d+)\)\s+.\s+no capture for (\d+)/(\d+)"
)
EVAL = re.compile(r"eval (mate-?\d+|[-+]?\d+)")
DEPTH = re.compile(r"depth (\d+)")
# TWO result formats, and the log is a REDRAWN terminal display so each is
# emitted many times over. Matching only form A left 166 games with no result.
RESULT_A = re.compile(r"Result: (\w+) vs (\S+) \(Elo \d+\), ([^.]+)\.")
RESULT_B = re.compile(r"Result: (\w+) \(([^)]+)\) vs ([^,]+), \d+ plies")


def _result(line):
    """-> (outcome, opponent, reason) or None."""
    m = RESULT_A.search(line)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    m = RESULT_B.search(line)
    if m:
        return m.group(1), m.group(3).strip(), m.group(2).strip()
    return None
THEY = re.compile(r"They played (\S+?)\.")
IPLAY   = re.compile(r"I play (\S+)")       # decision line, BEFORE the board
IPLAYED = re.compile(r"I played (\S+)")     # who-line, shape A only

# The board prints Blue as uppercase and Red as lowercase, a fixed colour
# convention. The setup array is relative to the SIDE TO MOVE, so the mapping
# flips with whose turn it is.
PIECE_OF = {"R": 1, "P": 2, "S": 3}


def to_cells(rows, mover_is_blue):
    """rows[rank] = list of 9 glyphs. Returns the 81-cell site array."""
    cells = []
    for rank in range(1, 10):                       # rank 1 first
        for glyph in rows[rank]:                    # a -> i
            up = glyph.upper()
            if up not in PIECE_OF:                  # '.', '+', the move dot: empty
                cells.append(0)
                continue
            code = PIECE_OF[up]
            cells.append(code if glyph.isupper() == mover_is_blue else code + 3)
    return cells


def parse(path=STATUS_LOG):
    """Yield one dict per game, each holding its board snapshots in order."""
    games = []
    cur = {"plies": [], "result": None, "opponent": None, "reason": None}
    rows, want = {}, 0
    pending_our = None

    def close(keep_open=True):
        nonlocal cur
        if cur["plies"]:
            games.append(cur)
        cur = {"plies": [], "result": None, "opponent": None, "reason": None}

    for raw in open(path, errors="replace"):
        if SECRET.search(raw):
            continue
        line = ANSI.sub("", raw.rstrip("\n"))

        got = _result(line)
        if got:
            # a redraw of the SAME result must not close a second, empty game
            if cur["plies"]:
                cur["result"], cur["opponent"], cur["reason"] = got
                close()
            rows, want = {}, 0
            continue

        if HEADER.match(line):
            rows, want = {}, 9
            continue

        m = ROW.match(line)
        if m and want:
            glyphs = m.group(2).split()
            if len(glyphs) == 9:
                rows[int(m.group(1))] = glyphs
                want -= 1
            continue

        m = WHO.search(line)
        if m and len(rows) == 9:
            ply = int(m.group(2))
            if cur["plies"] and ply < cur["plies"][-1]["ply"]:
                close()                              # ply went backwards: new game
            cur["plies"].append({"rows": dict(rows), "me": m.group(1), "ply": ply})
            rows, want = {}, 0
            rec = cur["plies"][-1]
            mv = IPLAYED.search(line)
            rec["our_move"] = mv.group(1) if mv else pending_our
            pending_our = None
            # this same line can already carry the eval in the second shape
            rec = cur["plies"][-1]
            e = EVAL.search(line)
            if e:
                rec["eval"] = _cp(e.group(1))
                d = DEPTH.search(line)
                rec["depth"] = int(d.group(1)) if d else None
            continue

        if not cur["plies"]:
            continue
        rec = cur["plies"][-1]

        mv = IPLAY.search(line)
        if mv and "I played" not in line:
            pending_our = mv.group(1)               # belongs to the NEXT board
            continue

        m = THEY.search(line)
        if m:
            rec.setdefault("their_reply", m.group(1))

        m = PIECES.search(line)
        if m:
            g = [int(x) for x in m.groups()]
            rec["mine"], rec["theirs"] = g[3], g[7]
            rec["counter"], rec["cap"] = g[8], g[9]

        m = EVAL.search(line)
        if m and "eval" not in rec:
            rec["eval"] = _cp(m.group(1))
            d = DEPTH.search(line)
            rec["depth"] = int(d.group(1)) if d else None

    close()
    return games


def _cp(v):
    if v.startswith("mate"):
        return -30000 if v[4] == "-" else 30000
    return int(v)


def request(rec, rid="q", sims=0, psc=None):
    """A `bot analyse` request for the position AFTER our move: they are to move."""
    mover_is_blue = rec["me"] == "Red"          # we just moved, so it is their turn
    return {
        "id": rid,
        "setup": to_cells(rec["rows"], mover_is_blue),
        "to_move": "blue" if mover_is_blue else "red",
        "psc": rec["counter"] if psc is None else psc,
        "ply": rec["ply"],
        "moves": [],
        "sims": sims,
    }


if __name__ == "__main__":
    games = parse()
    full = [g for g in games if g["result"]]
    snaps = sum(len(g["plies"]) for g in games)
    print("games parsed: %d (%d with a result)" % (len(games), len(full)))
    print("board snapshots: %d" % snaps)
    print("snapshots with a counter: %d"
          % len([r for g in games for r in g["plies"] if "counter" in r]))

    ncdraw = [g for g in full if "no capture" in (g["reason"] or "")]
    print("no-capture draws: %d" % len(ncdraw))

    mocca = [g for g in full if g["opponent"] == "Mocca"]
    print("Mocca games: %d  (%s)"
          % (len(mocca), ", ".join("%s/%d plies" % (g["result"], len(g["plies"]))
                                   for g in mocca)))

    loss = [g for g in mocca if g["result"] == "LOSS"]
    if loss:
        g = loss[-1]
        blind = [r for r in g["plies"]
                 if r.get("mine", 0) > r.get("theirs", 99) and r.get("eval") == 0]
        print("  the traced loss: %d plies, a piece up AND eval 0.00 at %d of them"
              % (len(g["plies"]), len(blind)))
        for r in blind[:5]:
            print("    ply %d  counter %d  %dv%d  depth %s  they reply %s"
                  % (r["ply"], r["counter"], r["mine"], r["theirs"],
                     r["depth"], r.get("their_reply")))
        if blind:
            r = blind[0]
            with open(OUT_DIR + "/blind_req.json", "w") as f:
                f.write(json.dumps(request(r, rid="true_psc")) + "\n")
                f.write(json.dumps(request(r, rid="psc_zero", psc=0)) + "\n")
            print("  wrote blind_req.json (true psc %d, and psc 0) for ply %d"
                  % (r["counter"], r["ply"]))
