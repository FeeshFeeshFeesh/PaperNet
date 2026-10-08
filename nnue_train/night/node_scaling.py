"""HANDOFF 231cj: what is a node worth in Elo? The exchange rate this engine actually has.

Every architecture decision that changes search speed needs this number and we have never
measured it. A wider net is the case in hand: benched in the gate binary at 100,000 nodes a
move, hidden 768 runs at 0.781 of hidden 512's nodes per second and hidden 1024 at 0.582
(bench_hidden.json). Under the live 500 ms clock that is a straight node handicap of 0.36
and 0.78 of a doubling -- so "is a wider net worth it?" is unanswerable until a doubling has
a price in Elo. The same number sizes a search change (231cb's goal-threat extension buys
knowledge and costs nodes) and says how much of a node-limited gate's verdict survives the
clock.

Method: the SAME net plays itself at N and at N/2 nodes a move, which is the cleanest possible
measurement -- no net difference, no clock, no contention effect (a node-limited seat cannot be
slowed into a loss), so the whole score gap is the budget. Two budgets are run because the
curve flattens: `lo` at the cheap end, `hi` at the gate's own 20,000 nodes.

Not a gate and not a ship decision: this only calibrates. It waits for the node-SPRT queue,
never runs beside a move-time SPRT (it SIGSTOPs itself if one appears), and touches nothing live.

No sys.argv; constants below. Progress lines go to OUT (append), prefixed "NSCALE".
"""
import datetime
import json
import math
import os
import random
import re
import signal
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
BOT = os.path.join(S, "sploppy_repo", "target", "release", "bot")
ENGINE = os.path.join(S, "ab_bu_bin", "base", "stockfish")
NET = os.path.join(S, "ab_n1_bin", "n1_25k.nnue")      # one trained net, both seats
MOVE_MS = 60000                                        # a ceiling the seats never reach
THREADS = 2                                            # games in flight
PAIRS = 120                                            # 240 games a job: +-0.065 on the score
SMOKE_PAIRS = 2
POLL_S = 60
WAIT_FOR = ("nodes_queue.py", "nodes_gate_", "[0-9]_gates.py")   # queue behind the SPRTs
PAUSE_WHEN = "[0-9]_gates.py"                          # a move-time SPRT: stop, do not steal its CPU
OUT = os.path.join(S, "node_scaling.log")
RESULT = os.path.join(S, "node_scaling.json")
# tag -> (fast seat nodes, slow seat nodes, seed); the pair is one doubling
JOBS = [("lo", 8000, 4000, 20260961), ("hi", 20000, 10000, 20260962)]


def say(msg):
    line = "%s NSCALE %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def rewrite(src, dst, pairs):
    """Copy src to dst replacing whole lines by prefix; each prefix must hit exactly once."""
    out, hits = [], [0] * len(pairs)
    for line in open(src).read().splitlines(True):
        for i, (prefix, new) in enumerate(pairs):
            if line.startswith(prefix):
                hits[i] += 1
                out.append(new + "\n")
                break
        else:
            out.append(line)
    for i, (prefix, _) in enumerate(pairs):
        assert hits[i] == 1, "%s: %d lines start with %r" % (dst, hits[i], prefix)
    open(dst, "w").write("".join(out))


def seats(tag, fast, slow):
    base = os.path.join(S, "ab_nodes_seat.py")
    made = []
    for role, nodes in (("fast", fast), ("slow", slow)):
        seat = os.path.join(S, "ab_ns%s_%s_seat.py" % (tag, role))
        logs = os.path.join(S, "ab_ns%s_%s_logs" % (tag, role))
        rewrite(base, seat, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                             ("NET       =", 'NET       = "%s"' % NET),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % nodes),
                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % logs),
                             ("NAME      =", 'NAME      = "SF19-NN-ns%s-%s"' % (tag, role))])
        made.append((seat, logs, nodes))
    return made


def run(tag, seat_fast, seat_slow, pairs, seed, stream, err):
    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, seat_fast),
           "--reference", "rpsi:%s -u %s" % (PY, seat_slow),
           "--move-ms", str(MOVE_MS), "--pairs", str(pairs), "--threads", str(THREADS),
           "--player-threads", "1", "--opening-plies", "8", "--seed", str(seed), "--stream"]
    return subprocess.Popen(cmd, stdout=open(stream, "w"), stderr=open(err, "w"),
                            cwd=os.path.join(S, "sploppy_repo"), start_new_session=True)


def pair_scores(stream):
    """Per-pair score of the FAST seat, in {0, .25, .5, .75, 1} (both games of the pair)."""
    got = {}
    if not os.path.exists(stream):
        return []
    with open(stream) as fh:
        for line in fh:
            if '"event":"end"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            w = ev["winner"]
            pts = 1.0 if w == ev["game"] else 0.5 if w is None else 0.0
            got.setdefault(ev["pair"], {})[ev["game"]] = pts
    return [(v[0] + v[1]) / 2.0 for v in got.values() if 0 in v and 1 in v]


def elo(score):
    score = min(max(score, 1e-6), 1 - 1e-6)
    return -400 * math.log10(1 / score - 1)


def interval(scores, draws=2000, seed=7):
    """Bootstrap over PAIRS (the unit that was randomised), 95%, in Elo."""
    rng = random.Random(seed)
    n = len(scores)
    if n < 5:
        return None
    out = []
    for _ in range(draws):
        out.append(elo(sum(scores[rng.randrange(n)] for _ in range(n)) / n))
    out.sort()
    return [round(out[int(0.025 * draws)], 1), round(out[int(0.975 * draws)], 1)]


def budgets_used(logs):
    got = subprocess.run("grep -ho 'go nodes [0-9]*' %s/*.log 2>/dev/null | sort | uniq -c" % logs,
                         shell=True, capture_output=True, text=True).stdout.strip()
    return got.replace("\n", " | ")


def smoke(tag, made, seed):
    stream = os.path.join(S, "ab_ns%s_smoke.jsonl" % tag)
    err = os.path.join(S, "ab_ns%s_smoke.err" % tag)
    p = run(tag, made[0][0], made[1][0], SMOKE_PAIRS, seed + 1, stream, err)
    rc = p.wait()
    ends = pair_scores(stream)
    gos = " ; ".join(budgets_used(logs) for _, logs, _ in made)
    bad = [x for x in open(err) if re.search("forfeit|panic|error", x, re.I)] if os.path.exists(err) else []
    ok = (rc == 0 and len(ends) == SMOKE_PAIRS and not bad
          and ("go nodes %d" % made[0][2]) in gos and ("go nodes %d" % made[1][2]) in gos
          and "go movetime" not in gos)
    say("%s smoke: rc %d, %d pairs, budgets [%s]%s" % (tag, rc, len(ends), gos,
                                                       "" if not bad else ", stderr " + bad[0][:100]))
    return ok


def wait_its_turn():
    said = False
    while True:
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)


def job(tag, fast, slow, seed):
    stream = os.path.join(S, "ab_ns%s_eval.jsonl" % tag)
    err = os.path.join(S, "ab_ns%s_eval.err" % tag)
    made = seats(tag, fast, slow)
    for _, logs, _ in made:
        os.makedirs(logs, exist_ok=True)
    if not smoke(tag, made, seed):
        say("%s SMOKE FAILED -- job skipped" % tag)
        return None
    say("%s: %d vs %d nodes, %d pairs, %d in flight" % (tag, fast, slow, PAIRS, THREADS))
    p = run(tag, made[0][0], made[1][0], PAIRS, seed, stream, err)
    paused, t0, said = False, time.time(), 0
    while True:
        time.sleep(POLL_S)
        if PAUSE_WHEN:
            timed = alive(PAUSE_WHEN)
            if timed and not paused and p.poll() is None:
                os.killpg(p.pid, signal.SIGSTOP)
                paused = True
                say("PAUSED: a move-time gate is running, this match must not steal its CPU")
            elif not timed and paused:
                os.killpg(p.pid, signal.SIGCONT)
                paused = False
                say("RESUMED")
        done = pair_scores(stream)
        if p.poll() is not None:
            break
        if len(done) >= said + 20:
            said = len(done)
            say("%s: %d/%d pairs, score %.3f [%.1f pairs/h]"
                % (tag, len(done), PAIRS, sum(done) / len(done), 3600 * len(done) / max(1, time.time() - t0)))
    scores = pair_scores(stream)
    if not scores:
        say("%s: no finished pairs" % tag)
        return None
    score = sum(scores) / len(scores)
    ci = interval(scores)
    rec = {"tag": tag, "fast_nodes": fast, "slow_nodes": slow, "pairs": len(scores),
           "score": round(score, 4), "elo": round(elo(score), 1), "elo_ci95": ci,
           "hours": round((time.time() - t0) / 3600, 2),
           "budgets": [budgets_used(logs) for _, logs, _ in made]}
    say("%s RESULT: %d pairs, score %.3f -> %+.0f Elo per doubling, 95%% %s (%.2f h)"
        % (tag, len(scores), score, rec["elo"], ci, rec["hours"]))
    return rec


def main():
    say("START: one net at two budgets, %d pairs a job" % PAIRS)
    for path in (BOT, ENGINE, NET, os.path.join(S, "ab_nodes_seat.py")):
        if not os.path.exists(path):
            say("MISSING %s -- nothing run" % path)
            return
    wait_its_turn()
    out = []
    for tag, fast, slow, seed in JOBS:
        rec = job(tag, fast, slow, seed)
        if rec:
            out.append(rec)
            json.dump(out, open(RESULT, "w"), indent=1)
    if out:
        say("TABLE  budget pair | score of the faster seat | Elo per doubling | 95%")
        for rec in out:
            say("TABLE  %d vs %d | %.3f | %+.0f | %s"
                % (rec["fast_nodes"], rec["slow_nodes"], rec["score"], rec["elo"], rec["elo_ci95"]))
        say("USE: hidden 768 costs 0.356 of a doubling, hidden 1024 costs 0.781 (bench_hidden.json)")
    say("DONE. Nothing shipped")


main()
