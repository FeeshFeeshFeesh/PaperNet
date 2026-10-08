"""R3: the passive firing record for the sticky loop lock. No games played.

WHY THIS IS NOT A ONE-LINE GREP. A seat log does NOT hold one game -- seat_78913
holds THREE, appended back to back. Any "count depth=1 lines in the newest log"
check therefore mixes games, and the 62-firing game and a -1009 LOSS live in the
same file. Games are separated here by a DROP in ply (a reset to single digits).

A lock reply is identifiable without touching the engine: the seat asks for a
normal movetime, and the reply comes back `depth=1` with a handful of nodes in
~1 ms, where the same position took ~5.2M nodes a moment earlier.

Plan (sticky-loop-lock): accumulate ~40 games before touching the threshold. This
prints the record so far and says how far off that is. Nothing is tuned here.
"""
import os
import re
import glob
import json
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs", "seat_*.log")
STATUS = os.path.join(os.path.dirname(os.path.dirname(HERE)),
                      "project_files", "papernet-status.log")
OUT = os.path.join(HERE, "lockrecord.jsonl")

TARGET_GAMES = 40
MIN_RUN = 3                  # consecutive instant replies before it counts as a lock
MOVE = re.compile(r"ply=(\d+) depth=(\d+) seldepth=(\d+) nodes=(\d+) time=(\d+) score=(-?\d+)")
BTIME = re.compile(r"btime (\d+)")
CSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
RESULT = re.compile(r"^(\d\d):(\d\d):(\d\d)\s+Result: (WIN|LOSS|DRAW)[^,]*,\s*([^.]*)\.")
SECRET = ("token", "key", "secret")


def results(today_only=True):
    """chronological, adjacent duplicates collapsed (the log is a REDRAWN display,
    so every Result line is emitted many times -- see nocapture-draw-prize).

    ! The Result lines carry a TIME OF DAY AND NO DATE, and this log spans many
    days. Matching a seat log's window on HH:MM:SS alone pulled in 25 candidate
    results from other days, out of order and at time controls we never played.
    Day boundaries are recovered by watching the clock go BACKWARDS, and only the
    newest day is returned."""
    out, prev, day = [], None, 0
    last_t = -1
    if not os.path.exists(STATUS):
        return out
    for line in open(STATUS, errors="replace"):
        l = CSI.sub("", line).strip()
        m = RESULT.match(l)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        rec = (t, m.group(4), m.group(5).strip())
        if rec == prev:
            continue
        if last_t >= 0 and t < last_t - 3600:      # >1h backwards = a new day
            day += 1
        last_t = t
        out.append((day, t, m.group(4), m.group(5).strip()))
        prev = rec
    if today_only and out:
        newest = max(r[0] for r in out)
        out = [r for r in out if r[0] == newest]
    return [(t, o, w) for _, t, o, w in out]


def segments(path):
    rows = []
    for line in open(path, errors="replace"):
        low = line.lower()
        if any(k in low for k in SECRET):
            continue
        m = MOVE.search(line)
        if not m:
            continue
        b = BTIME.search(line)
        rows.append({"ply": int(m.group(1)), "depth": int(m.group(2)),
                     "nodes": int(m.group(4)), "time": int(m.group(5)),
                     "score": int(m.group(6)),
                     "btime": int(b.group(1)) if b else None})
    segs, cur = [], []
    for r in rows:
        if cur and r["ply"] < cur[-1]["ply"]:
            segs.append(cur)
            cur = []
        cur.append(r)
    if cur:
        segs.append(cur)
    return segs


def main():
    res = results()
    logs = sorted(glob.glob(LOGS), key=lambda p: os.stat(p).st_mtime)
    records, multi, total = [], 0, 0

    for path in logs:
        segs = segments(path)
        if not segs:
            continue
        total += len(segs)
        if len(segs) > 1:
            multi += 1
        st = os.stat(path)
        t0 = getattr(st, "st_birthtime", st.st_mtime)
        day = datetime.datetime.fromtimestamp(t0)
        lo = day.hour * 3600 + day.minute * 60 + day.second
        hi = datetime.datetime.fromtimestamp(st.st_mtime)
        hi = hi.hour * 3600 + hi.minute * 60 + hi.second
        window = [r for r in res if lo <= r[0] <= hi] if hi >= lo else []
        # segments are consecutive games, so the results that follow this log's birth
        # map onto segments 1..k in order. The final segment may still be IN PROGRESS,
        # in which case there is one fewer result than segments -- still alignable.
        aligned = window if len(window) in (len(segs), len(segs) - 1) else None

        for i, s in enumerate(segs):
            cand = [r["depth"] == 1 and r["nodes"] < 1000 for r in s]
            # A REAL lock is a RUN of consecutive instant replies -- the board has to
            # recur 3x before it engages. A lone depth=1 move is something else: at
            # ply 1 the seat produced one 128-node reply, which is not a loop lock.
            run = best = 0
            for c in cand:
                run = run + 1 if c else 0
                best = max(best, run)
            if best < MIN_RUN:
                continue
            lock = [r for r, c in zip(s, cand) if c]
            outcome = aligned[i][1] if aligned and i < len(aligned) else None
            reason = aligned[i][2] if aligned and i < len(aligned) else None
            ended_locked = s[-1]["ply"] == lock[-1]["ply"]
            # DEDUCED FROM THE RULES, not from any log: the lock only ever replays a
            # board that is already repeating, so no capture can occur while it is
            # engaged. If the game ENDED during the lock it can only have ended by the
            # 200-ply no-capture rule -- not by the corner, not by resignation.
            # (position.h: there is no repetition rule; is_draw is rule50 >= 200.)
            inferred = "DRAW (200 plies, no capture -- deduced)" if ended_locked else None
            bt = [r["btime"] for r in lock if r["btime"] is not None]
            records.append({
                "log": os.path.basename(path), "seg": i + 1,
                "plies": [s[0]["ply"], s[-1]["ply"]],
                "our_moves": len(s),
                "lock_replies": len(lock),
                "lock_plies": [lock[0]["ply"], lock[-1]["ply"]],
                "clock_at_engage": bt[0] if bt else None,
                "clock_at_end": bt[-1] if bt else None,
                "score_range": [min(r["score"] for r in lock),
                                max(r["score"] for r in lock)],
                "max_nodes_in_lock": max(r["nodes"] for r in lock),
                "max_ms_in_lock": max(r["time"] for r in lock),
                "ended_during_lock": ended_locked,
                "result": outcome, "reason": reason,
                "result_confident": aligned is not None,
                "result_inferred": inferred,
            })

    with open(OUT, "w") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")

    print("seat logs scanned      : %d" % len(logs))
    print("GAMES found            : %d  (logs holding >1 game: %d)" % (total, multi))
    print("  -> 'one log = one game' is FALSE. Any per-log stat mixes games.")
    print("games where the lock FIRED: %d   (plan wants %d before tuning)"
          % (len(records), TARGET_GAMES))
    print()
    if not records:
        print("no firings recorded yet")
        return
    for r in records:
        print("  %s seg%d  plies %d-%d | %d lock replies over plies %d-%d"
              % (r["log"], r["seg"], r["plies"][0], r["plies"][1],
                 r["lock_replies"], r["lock_plies"][0], r["lock_plies"][1]))
        print("      clock %s -> %s ms | eval %+d..%+d | worst lock cost %d nodes / %d ms"
              % (r["clock_at_engage"], r["clock_at_end"],
                 r["score_range"][0], r["score_range"][1],
                 r["max_nodes_in_lock"], r["max_ms_in_lock"]))
        print("      ended during the lock: %s | result from log: %s%s"
              % (r["ended_during_lock"], r["result"] or "unmatched",
                 " (%s)" % r["reason"] if r["reason"] else ""))
        if r["result_inferred"]:
            print("      result DEDUCED FROM RULES: %s%s" % (
                r["result_inferred"],
                "   <-- CONTRADICTS the log line above; the deduction is the proof"
                if r["result"] and r["result"] != r["result_inferred"].split()[0] else ""))
    # ! THE DEDUCTION OUTRANKS THE LOG, and this is not a preference -- it is a
    # proof. The lock replays a board that has ALREADY occurred, so no piece can be
    # entering a goal square on a locked move; a game ending during the lock cannot
    # have ended by us reaching the corner. Meanwhile `aligned` maps the today-window
    # result lines onto segments POSITIONALLY, so one missing or extra line shifts
    # every attribution after it -- observed 2026-09-27, the final segment was handed
    # "WIN (reached the corner)" while the status line written at that game's end read
    # "DRAW vs RPSFish, 200 plies without a capture". See nocapture-draw-prize.
    agg, contra = {}, []
    for r in records:
        if r["result_inferred"]:
            k = r["result_inferred"].split()[0]
            if r["result"] and r["result"] != k:
                contra.append((r["log"], r["seg"], r["result"], k))
        else:
            k = r["result"]
        r["result_final"] = k
        agg[k] = agg.get(k, 0) + 1
    print()
    print("  outcomes of locked games: %s   (deduction preferred where it exists)" % agg)
    if contra:
        print("  ! %d log attributions OVERRIDDEN by the rules deduction:" % len(contra))
        for lg, sg, was, now in contra:
            print("      %s seg%d: log said %s, rules say %s" % (lg, sg, was, now))
    print("  total lock replies: %d | worst single reply: %d nodes / %d ms"
          % (sum(r["lock_replies"] for r in records),
             max(r["max_nodes_in_lock"] for r in records),
             max(r["max_ms_in_lock"] for r in records)))
    print("  -> %d of %d games needed before the threshold is touched"
          % (max(0, TARGET_GAMES - len(records)), TARGET_GAMES))


if __name__ == "__main__":
    main()
