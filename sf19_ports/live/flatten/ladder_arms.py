"""R1 gate: run the contempt (-1) and bonus (+1) arms of nc_draw_grade down the
same node ladder as the baseline sweep_band.jsonl, and ask whether the two SIGNS
differ. Pre-registered reading: `plus` already returned -23 Elo in a real SPRT,
so if plus reopens the spread as much as minus, "reopens the spread" does not
predict Elo and R1 must NOT be given a cloud host. Only a DIFFERENTIAL justifies one.

WHY THIS FILE EXISTS -- the first attempt was SIGSTOP'd by the host after 15
minutes with zero output. Cause: one engine process for all 3,048 searches, with
subprocess.run(capture_output) holding the ENTIRE stdout (MultiPV 8 info lines)
in RAM until the end. The machine had 95 MB free and the live bot was mid-game.
Fixes here, and none of them touch the experiment:
  * CHUNKED   -- one engine per CHUNK positions, so peak RAM is flat
  * RESUMABLE -- appends, and skips positions already in the output file
  * RAM-GATED -- waits if free RAM drops under FREE_FLOOR_MB; the live bot's
                 clock outranks this job (a heavy local job once cost the bot
                 7.3 s on a single move)
  * HEARTBEAT -- one flushed line per chunk, so a stall is VISIBLE to a monitor.
                 pgrep matches a stopped process, so "process exists" is not life.

The constants below are byte-identical to flatten_sweep_band.py on purpose: same
NODES, MULTIPV, HASH_MB, LOW_CLOCK. Change one and the baseline comparison dies.
"""

import os
import json
import re
import subprocess
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

HERE = os.path.dirname(os.path.abspath(__file__))
SF = os.path.join(os.path.dirname(os.path.dirname(HERE)), "sf19nn")
NET = _ROOT + "/nnue_train/sploppy_repo/runs/sf2_wdl/best.nnue"
POS = os.path.join(HERE, "positions_band.jsonl")

ARMS = [("minus", os.path.join(SF, "stockfish_minus"), os.path.join(HERE, "sweep_band_minus.jsonl")),
        ("plus",  os.path.join(SF, "stockfish_plus"),  os.path.join(HERE, "sweep_band_plus.jsonl"))]

# --- identical to the baseline sweep. do not touch. ---
NODES = (20000, 50000, 100000, 200000, 400000, 800000)
MULTIPV = 8
LOW_CLOCK = 0
HASH_MB = 64
# --- runner-only knobs ---
CHUNK = 16                  # positions per engine process
FREE_FLOOR_PCT = 20         # pause below this; the live bot gets the RAM
WAIT_SLICE_S = 20
MAX_WAIT_S = 1800

FEN_LINE = re.compile(r"^Fen: (.+)$")
NET_EVAL = re.compile(r"^Net evaluation (-?\d+)")
INFO = re.compile(r"^info depth (\d+) seldepth \d+ multipv (\d+) score (cp|mate) (-?\d+)"
                  r"(?: (?:upper|lower)bound)? nodes (\d+).*?\bpv (\S+)")


def say(msg):
    print("%s  %s" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def free_pct():
    """`memory_pressure`, NOT vm_stat's "Pages free".

    Pages free read 58 MB on this machine while memory_pressure reported 52%
    free: ~1.9 GB sat in reclaimable INACTIVE pages. Gating on Pages free would
    stall this job forever on a healthy machine."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            if "free percentage" in line:
                return int(line.rsplit(":", 1)[1].strip().rstrip("%"))
    except Exception:
        pass
    return 100              # unknown -> don't block on it


def wait_for_ram():
    waited = 0
    while free_pct() < FREE_FLOOR_PCT and waited < MAX_WAIT_S:
        if waited == 0:
            say("   RAM %d%% free < floor %d%% -- yielding to the live bot"
                % (free_pct(), FREE_FLOOR_PCT))
        time.sleep(WAIT_SLICE_S)
        waited += WAIT_SLICE_S
    return waited < MAX_WAIT_S


def relabel(fen, clock):
    f = fen.split()
    f[4] = str(clock)
    return " ".join(f)


def run_chunk(engine, rows):
    """One engine process, one chunk. Returns rows ready to append."""
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
    # the LAST go before quit is aborted in flight and reports depth 1 / 0 nodes.
    # burn one search so no real row is truncated. (this trap has bitten twice.)
    script += ["ucinewgame", "position fen " + rows[0]["fen"], "go depth 1", "quit"]

    out = subprocess.run([engine], input="\n".join(script) + "\n",
                         capture_output=True, text=True).stdout.splitlines()

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
        raise RuntimeError("!! %d blocks for %d planned searches -- attribution unsafe"
                           % (len(blocks), len(plan)))

    st_idx, i = {}, 0
    for r in rows:
        for arm in ("high", "low"):
            st_idx[(r["game"], r["ply"], arm)] = statics[i] if i < len(statics) else None
            i += 1

    made = []
    for (r, arm, clock, fen, nodes), b in zip(plan, blocks):
        if (b["fen"].split()[0] != fen.split()[0]
                or b["fen"].split()[4] != fen.split()[4]):
            raise RuntimeError("!! block/plan mismatch at %s" % fen)
        deepest = max((x[0] for x in b["rows"]), default=0)
        scores, used = {}, 0
        for d, pv_i, kind, val, nd, move in b["rows"]:
            if d != deepest:
                continue
            used = max(used, nd)
            scores[move] = (30000 if val > 0 else -30000) if kind == "mate" else val
        vals = sorted(scores.values(), reverse=True)
        made.append({
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
        })
    return made


def done_keys(path):
    """(game, ply) pairs already fully written -- 2 arms x len(NODES) rows each."""
    if not os.path.exists(path):
        return set()
    seen = {}
    for line in open(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        seen[(d["game"], d["ply"])] = seen.get((d["game"], d["ply"]), 0) + 1
    need = 2 * len(NODES)
    return {k for k, v in seen.items() if v >= need}


def main():
    allrows = [json.loads(l) for l in open(POS)]
    say("positions available: %d" % len(allrows))

    for name, engine, out in ARMS:
        if not os.path.exists(engine):
            say("ARM %s SKIPPED -- no binary at %s" % (name, engine))
            continue
        have = done_keys(out)
        todo = [r for r in allrows if (r["game"], r["ply"]) not in have]
        say("ARM %s: %d already done, %d to go -> %s"
            % (name, len(have), len(todo), os.path.basename(out)))
        t0 = time.time()
        for i in range(0, len(todo), CHUNK):
            batch = todo[i:i + CHUNK]
            if not wait_for_ram():
                say("ARM %s ABORTED -- RAM stayed below floor for %ds" % (name, MAX_WAIT_S))
                return
            try:
                made = run_chunk(engine, batch)
            except RuntimeError as e:
                say("ARM %s %s" % (name, e))
                return
            with open(out, "a") as fh:
                for row in made:
                    fh.write(json.dumps(row) + "\n")
            say("   %s %d/%d positions, +%d rows, RAM %d%% free, %.0fs elapsed"
                % (name, min(i + CHUNK, len(todo)), len(todo), len(made),
                   free_pct(), time.time() - t0))
        say("ARM %s COMPLETE in %.0fs" % (name, time.time() - t0))
    say("BOTH ARMS DONE")


if __name__ == "__main__":
    main()
