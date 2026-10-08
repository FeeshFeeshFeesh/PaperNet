"""Does the search go BLIND when the no-capture draw enters the horizon?

Two measured results collide and predict it. `conversion-measured`: the rate at
which we throw a won endgame RISES 10% -> 45% along a node ladder.
`nocapture-draw-flat`: once the 200-ply draw is inside the horizon, the search
returns one flat root score. If they are the same defect then on a real won
position the SPREAD of scores across root moves should collapse toward zero as the
search grows -- but only while the capture clock is high. Reset the clock field on
the same board and the collapse should not happen.

That is a within-position control: board, material, side to move and net are all
held fixed, and only the clock moves.

Two facts about this engine make the experiment exact, and both contradict the old
note that `go nodes` / `go depth` "don't search":
  * `go depth 99 nodes N` IS honoured and is bit-exact reproducible -- three runs
    of the same position return identical node counts and scores.
  * determinism needs `ucinewgame` before every search; without it the TT carries
    over and five runs of `go depth 14` gave three different bestmoves.
  * the trap that produced the old note: the LAST `go` before `quit` is aborted in
    flight and reports depth 1 / 0 nodes. Always send one search to discard.

A node ladder, not a depth ladder, because (a) it is what `conversion-measured`
already measured the throw rate along, and (b) it is bounded -- an uncapped
`go depth 14` on one of these endgames ran for 415 seconds.

Nothing here plays a game and nothing needs a credential.
"""

import os
import json
import re
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "stockfish")
NET = _ROOT + "/nnue_train/sploppy_repo/runs/sf2_wdl/best.nnue"
POS = os.path.join(HERE, "positions_band.jsonl")
OUT = os.path.join(HERE, "sweep_band.jsonl")

N_POSITIONS = 0              # 0 = all
NODES = (20000, 50000, 100000, 200000, 400000, 800000)
MULTIPV = 8
LOW_CLOCK = 0                # control arm: same board, capture clock reset
HASH_MB = 64

FEN_LINE = re.compile(r"^Fen: (.+)$")
NET_EVAL = re.compile(r"^Net evaluation (-?\d+)")
INFO = re.compile(r"^info depth (\d+) seldepth \d+ multipv (\d+) score (cp|mate) (-?\d+)"
                  r"(?: (?:upper|lower)bound)? nodes (\d+).*?\bpv (\S+)")


def relabel(fen, clock):
    f = fen.split()
    f[4] = str(clock)
    return " ".join(f)


def main():
    rows = [json.loads(l) for l in open(POS)]
    if N_POSITIONS:
        rows = rows[:N_POSITIONS]

    # One engine process for the whole batch. `d` prints "Fen: ..." which is the
    # only reliable separator inside a UCI stream, and `eval` is tagged by its own
    # line, so every search can be attributed without launching 1,584 processes.
    plan, script = [], ["uci",
                        "setoption name Threads value 1",
                        "setoption name Hash value %d" % HASH_MB,
                        "setoption name EvalFile value " + NET,
                        "setoption name MultiPV value %d" % MULTIPV]
    for r in rows:
        for arm, clock in (("high", r["clock"]), ("low", LOW_CLOCK)):
            fen = relabel(r["fen"], clock)
            script += ["ucinewgame", "position fen " + fen, "eval"]
            for n in NODES:
                script += ["ucinewgame", "position fen " + fen, "d",
                           "go depth 99 nodes %d" % n]
                plan.append((r, arm, clock, fen, n))
    script += ["ucinewgame", "position fen " + rows[0]["fen"], "go depth 1", "quit"]

    t0 = time.time()
    out = subprocess.run([ENGINE], input="\n".join(script) + "\n",
                         capture_output=True, text=True).stdout.splitlines()
    print("engine ran %d searches in %.1fs" % (len(plan), time.time() - t0))

    # walk the stream in order
    blocks, cur, statics = [], None, []
    for line in out:
        m = NET_EVAL.match(line)
        if m:
            statics.append(int(m.group(1)))
            continue
        m = FEN_LINE.match(line)
        if m:
            cur = {"fen": m.group(1).strip(), "rows": []}
            blocks.append(cur)
            continue
        if cur is None:
            continue
        m = INFO.match(line)
        if m:
            cur["rows"].append((int(m.group(1)), int(m.group(2)), m.group(3),
                                int(m.group(4)), int(m.group(5)), m.group(6)))

    if len(blocks) != len(plan):
        print("!! %d blocks for %d planned searches -- attribution unsafe, stopping"
              % (len(blocks), len(plan)))
        return

    # statics come one per (position, arm), in the same order
    st_idx = {}
    i = 0
    for r in rows:
        for arm in ("high", "low"):
            st_idx[(r["game"], r["ply"], arm)] = statics[i] if i < len(statics) else None
            i += 1

    n_written = 0
    with open(OUT, "w") as fh:
        for (r, arm, clock, fen, nodes), b in zip(plan, blocks):
            if b["fen"].split()[0] != fen.split()[0] or b["fen"].split()[4] != fen.split()[4]:
                print("!! block/plan mismatch at %s" % fen)
                return
            deepest = max((x[0] for x in b["rows"]), default=0)
            scores, used = {}, 0
            for d, pv_i, kind, val, nd, move in b["rows"]:
                if d != deepest:
                    continue
                used = max(used, nd)
                scores[move] = (30000 if val > 0 else -30000) if kind == "mate" else val
            vals = sorted(scores.values(), reverse=True)
            fh.write(json.dumps({
                "game": r["game"], "ply": r["ply"], "arm": arm,
                "clock": clock, "real_clock": r["clock"],
                "mine": r["mine"], "theirs": r["theirs"],
                "ask_nodes": nodes, "used_nodes": used, "depth": deepest,
                "static": st_idx.get((r["game"], r["ply"], arm)),
                "n": len(vals),
                "best": vals[0] if vals else None,
                "worst": vals[-1] if vals else None,
                "spread": (vals[0] - vals[-1]) if len(vals) > 1 else None,
                "scores": scores,
            }) + "\n")
            n_written += 1
    print("wrote %d rows to %s" % (n_written, OUT))


if __name__ == "__main__":
    main()
