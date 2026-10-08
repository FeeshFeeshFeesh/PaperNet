"""Check the reconstructed FENs against the engine's own move generator.

A reconstruction is only trustworthy if (a) the engine accepts the FEN, (b) it
reports the same board back, (c) the capture clock survives the round trip, and
(d) the move we actually played in the live game is LEGAL there. (d) is the real
test: it is what proves the undo put the right piece back on the right square.

The `d` command prints "Fen: ..." so one engine process can do the whole batch --
those lines are the only reliable separator between positions in a UCI stream.
"""

import os
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "stockfish")
POS = os.path.join(HERE, "positions.jsonl")


def main():
    rows = [json.loads(l) for l in open(POS)]
    script = ["uci"]
    for r in rows:
        script += ["position fen " + r["fen"], "d", "go perft 1"]
    script.append("quit")

    out = subprocess.run([ENGINE], input="\n".join(script) + "\n",
                         capture_output=True, text=True).stdout.splitlines()

    # walk the stream: each "Fen:" opens a position, moves follow until the next
    blocks, cur = [], None
    for line in out:
        if line.startswith("Fen: "):
            cur = {"fen": line[5:].strip(), "moves": []}
            blocks.append(cur)
        elif cur is not None and len(line) > 4 and line[4] == ":" and line[0].isalpha():
            cur["moves"].append(line[:4])

    ok = bad_fen = bad_clock = illegal = 0
    problems = []
    for r, b in zip(rows, blocks):
        want_board = r["fen"].split()[0]
        got_board = b["fen"].split()[0]
        got_clock = int(b["fen"].split()[4])
        if want_board != got_board or r["fen"].split()[1] != b["fen"].split()[1]:
            bad_fen += 1
            problems.append(("board differs", r["fen"], b["fen"]))
        elif got_clock != r["clock"]:
            bad_clock += 1
            problems.append(("clock %d != %d" % (got_clock, r["clock"]), r["fen"], b["fen"]))
        elif r["our_move"] not in b["moves"]:
            illegal += 1
            problems.append(("played move %s not legal (%d legal)"
                             % (r["our_move"], len(b["moves"])), r["fen"], ""))
        else:
            ok += 1

    print("positions:            %d" % len(rows))
    print("blocks read back:     %d" % len(blocks))
    print("VALID (move legal):   %d" % ok)
    print("board round-trip bad: %d" % bad_fen)
    print("clock round-trip bad: %d" % bad_clock)
    print("played move illegal:  %d" % illegal)
    if problems:
        print("\nfirst 5 problems:")
        for p in problems[:5]:
            print("  %s\n    ours: %s\n    engine: %s" % p)
    legal = [len(b["moves"]) for b in blocks]
    if legal:
        legal.sort()
        print("\nlegal moves per position: min %d  median %d  max %d"
              % (legal[0], legal[len(legal) // 2], legal[-1]))


if __name__ == "__main__":
    main()
