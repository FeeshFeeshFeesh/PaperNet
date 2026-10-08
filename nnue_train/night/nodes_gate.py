"""HANDOFF 231cf. A node-limited A/B gate: the same SPRT, ~15x more pairs per hour.

Why: the 500 ms gate (231bu/231bv) caps at 100 pairs because a pair costs ~2.5
minutes of wall clock, and 100 pairs cannot resolve anything smaller than about
+/-30 Elo -- n1's net came out "+3 Elo, too close to call", which is not a verdict.
Here both seats search a FIXED NODE BUDGET instead of a clock, so
  - a game costs ~20k nodes/move * ~250 plies = seconds, not minutes,
  - CPU contention cannot bias the result (no clock to lose), so every core is
    usable and the gate may run beside other work,
and the pair count an SPRT needs becomes affordable: SPRT[0, +10] to a 1500-pair cap.

What it does NOT tell you: how the net behaves under the live 500 ms clock (time
management, move overhead). A node-limited win is evidence about the net, not a
shipping decision -- the 500 ms gate still decides, and a human still reads it.

No sys.argv; constants below. Progress lines go to OUT (append), prefixed "NGATE".
"""
import datetime
import hashlib
import json
import os
import re
import shutil
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
ENGINE = os.path.join(S, "ab_bu_bin", "base", "stockfish")   # sha256 4b7fabf0..., the live binary

TAG = "nsmoke"                       # names the seats, the logs and the stream
CAND_NET = os.path.join(S, "ab_n1_bin", "n1_25k.nnue")       # frozen candidate
REF_NET = _ROOT + "/project_files/sploppy_engine/example.nnue"
CAND_LABEL = "SF19-NN + n1_25k.nnue"

NODES = 20000            # both seats, every move
MOVE_MS = 60000          # a ceiling the seats never reach; the host still wants a clock
PAIRS = 1500             # cap; the SPRT usually stops long before
THREADS = 4              # games in flight (node-limited: contention costs time, not truth)
SEED = 20260934
ELO_BAD = 0.0            # SPRT[0, +10]: "is it any gain at all?"
ELO_GOOD = 10.0
SMOKE_PAIRS = 2
SMOKE_ONLY = True        # True: stop after the smoke (plumbing + pace only)
PAUSE_WHEN = "[0-9]_gates.py"   # a move-time SPRT: SIGSTOP this match so it cannot steal its CPU
                         # (not "_gates.py": after_gates.py is a queue that waits, not a match)
POLL_S = 60
OUT = os.path.join(S, "nodes_gate.log")

CAND_SEAT = os.path.join(S, "ab_%s_cand_seat.py" % TAG)
REF_SEAT = os.path.join(S, "ab_%s_ref_seat.py" % TAG)
CAND_LOGS = os.path.join(S, "ab_%s_cand_logs" % TAG)
REF_LOGS = os.path.join(S, "ab_%s_ref_logs" % TAG)
TALLY = os.path.join(S, "ab_%s_tally.py" % TAG)
STREAM = os.path.join(S, "ab_%s_eval.jsonl" % TAG)
ERR = os.path.join(S, "ab_%s_eval.err" % TAG)


def say(msg):
    line = "%s NGATE %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rewrite(src, dst, pairs):
    """Copy src to dst replacing whole lines by prefix; each prefix must hit exactly once."""
    lines = open(src).read().splitlines(True)
    out, hits = [], [0] * len(pairs)
    for line in lines:
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


def run_eval(pairs, threads, seed, stream, err):
    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, CAND_SEAT),
           "--reference", "rpsi:%s -u %s" % (PY, REF_SEAT),
           "--move-ms", str(MOVE_MS), "--pairs", str(pairs), "--threads", str(threads),
           "--player-threads", "1", "--opening-plies", "8", "--seed", str(seed), "--stream"]
    return subprocess.Popen(cmd, stdout=open(stream, "w"), stderr=open(err, "w"),
                            cwd=os.path.join(S, "sploppy_repo"), start_new_session=True)


def tally():
    p = subprocess.run([PY, "-u", TALLY], capture_output=True, text=True)
    out = [x for x in p.stdout.splitlines() if x.strip()]
    return (out[-2] if len(out) >= 2 else ""), (out[-1] if out else "tally printed nothing: " + p.stderr[-300:])


def v_checks():
    """The verdict is only readable if nothing went wrong in the plumbing."""
    bad = []
    ends = [json.loads(x) for x in open(STREAM) if '"event":"end"' in x] if os.path.exists(STREAM) else []
    forfeits = [e for e in ends if "forfeit" in json.dumps(e).lower()]
    if forfeits:
        bad.append("%d forfeits" % len(forfeits))
    if os.path.exists(ERR):
        hits = [x for x in open(ERR) if re.search("forfeit|panic|error", x, re.I)]
        if hits:
            bad.append("stderr: %s" % hits[0][:120])
    for d in (CAND_LOGS, REF_LOGS):
        notes = subprocess.run("grep -h NOTE %s/*.log 2>/dev/null | head -3" % d,
                               shell=True, capture_output=True, text=True).stdout.strip()
        if notes:
            bad.append("%s NOTE: %s" % (os.path.basename(d), notes.splitlines()[0][:120]))
    return bad


def seats_used_nodes():
    """Proof that the games really were node-limited: every go line must be 'go nodes'."""
    got = subprocess.run("grep -h '^.*go ' %s/*.log %s/*.log 2>/dev/null | grep -o 'go [a-z]*' | sort | uniq -c"
                         % (CAND_LOGS, REF_LOGS), shell=True, capture_output=True, text=True).stdout.strip()
    return got.replace("\n", " | ")


def main():
    say("START tag %s: %s vs %s" % (TAG, os.path.basename(CAND_NET), os.path.basename(REF_NET)))
    for path in (BOT, ENGINE, CAND_NET, REF_NET, os.path.join(S, "ab_nodes_seat.py")):
        if not os.path.exists(path):
            say("MISSING %s -- nothing run" % path)
            return
    say("engine %s; cand net %s; ref net %s" % (sha(ENGINE)[:16], sha(CAND_NET)[:16], sha(REF_NET)[:16]))

    base = os.path.join(S, "ab_nodes_seat.py")
    rewrite(base, CAND_SEAT, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                              ("NET       =", 'NET       = "%s"' % CAND_NET),
                              ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                              ("LOG_DIR   =", 'LOG_DIR   = "%s"' % CAND_LOGS),
                              ("NAME      =", 'NAME      = "SF19-NN-%s"' % TAG)])
    rewrite(base, REF_SEAT, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                             ("NET       =", 'NET       = "%s"' % REF_NET),
                             ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % REF_LOGS),
                             ("NAME      =", 'NAME      = "SF19-NN-ref"')])
    rewrite(os.path.join(S, "ab_nmp_tally.py"), TALLY,
            [("STREAM    =", 'STREAM    = "%s"' % STREAM),
             ("CAND_NAME =", 'CAND_NAME = "%s"' % CAND_LABEL),
             ("ELO_BAD   =", "ELO_BAD   = %.1f" % ELO_BAD),
             ("ELO_GOOD  =", "ELO_GOOD  = %.1f" % ELO_GOOD),
             ("MAX_PAIRS =", "MAX_PAIRS = %d" % PAIRS)])

    for d in (CAND_LOGS, REF_LOGS):
        shutil.rmtree(d, ignore_errors=True)
    t0 = time.time()
    p = run_eval(SMOKE_PAIRS, 1, SEED, STREAM + ".smoke", ERR + ".smoke")
    rc = p.wait()
    ends = [json.loads(x) for x in open(STREAM + ".smoke") if '"event":"end"' in x]
    bad = [x for x in open(ERR + ".smoke") if re.search("forfeit|panic|error", x, re.I)]
    gos = seats_used_nodes()
    if rc != 0 or len(ends) != 2 * SMOKE_PAIRS or bad or "go movetime" in gos or "go nodes" not in gos:
        say("SMOKE FAIL rc %s ends %d go-lines [%s] stderr %s" % (rc, len(ends), gos, bad[:2]))
        return
    say("smoke clean in %.0fs: %d games, go-lines [%s]" % (time.time() - t0, len(ends), gos))
    say("smoke pace: %.1f s per pair at 1 in flight" % ((time.time() - t0) / SMOKE_PAIRS))
    if SMOKE_ONLY:
        say("SMOKE_ONLY: stopping here, nothing else run.")
        return
    for d in (CAND_LOGS, REF_LOGS):
        shutil.rmtree(d, ignore_errors=True)

    say("RUN: %d nodes/move, cap %d pairs, %d in flight, seed %d, SPRT[%.0f, %.0f]"
        % (NODES, PAIRS, THREADS, SEED, ELO_BAD, ELO_GOOD))
    t0 = time.time()
    p = run_eval(PAIRS, THREADS, SEED, STREAM, ERR)
    last, paused, paused_s = None, False, 0.0
    while True:
        time.sleep(POLL_S)
        if PAUSE_WHEN:
            timed = subprocess.run(["pgrep", "-f", PAUSE_WHEN], capture_output=True).returncode == 0
            if timed and not paused and p.poll() is None:
                os.killpg(p.pid, signal.SIGSTOP); paused = True; t_pause = time.time()
                say("PAUSED: a move-time gate is running, this match must not steal its CPU")
            elif not timed and paused:
                os.killpg(p.pid, signal.SIGCONT); paused = False; paused_s += time.time() - t_pause
                say("RESUMED after %.0f min paused" % ((time.time() - t_pause) / 60.0))
            if paused:
                continue
        status, verdict = tally()
        n = status.split("  ")[0]
        if n != last:
            done = int(n.split()[-1]) if n.startswith("pairs") else 0
            rate = 3600.0 * done / max(1e-9, time.time() - t0)
            say("%s  [%.0f pairs/h]" % (status, rate))
            last = n
        finished = p.poll() is not None
        if verdict.startswith("SPRT VERDICT") or finished:
            if not finished:
                os.killpg(p.pid, signal.SIGTERM)
                time.sleep(5)
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
            status, verdict = tally()
            say("FINAL " + status)
            say(verdict + ("" if verdict.startswith("SPRT VERDICT") else "  (eval exited rc %s first)" % p.returncode))
            say("go lines: %s" % seats_used_nodes())
            bad = v_checks()
            say("V: %s" % ("PASS (0 forfeits, clean stderr, 0 NOTE lines)" if not bad
                           else "FAIL, the verdict is not to be read: %s" % bad[:5]))
            say("DONE in %.1f min wall (%.1f min of it paused). Nothing shipped; a human reads this."
            % ((time.time() - t0) / 60.0, paused_s / 60.0))
            return


main()
