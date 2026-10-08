"""HANDOFF §231ca (= the §231bv reading rule) for the batch-2 net sf2_ex_50k: wait for mix_train_s2.py, then R1 -> R2 -> R3.

R0  nothing touches the live bot; this script only writes files in the scratchpad.
R1  sploppy's own quick eval (runs/<NAME>/eval.json) is a screen: bootstrap interval upper bound < 0 -> stop.
R2  the C++ reader must match sploppy's Rust eval exactly on the new format-8 net (nn_parity).
R3  SPRT: SF19-NN + the new net vs SF19-NN + example.nnue, same frozen binary (the live one),
    500 ms, 3 in flight, new seed, the §231bu tally (MIN_PAIRS 6, cap 100) and V checks.
Nothing is shipped: every verdict is written to OUT for a human to read.

No sys.argv; constants below. Progress lines go to OUT (append), prefixed "GATES".
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
NAME = "sf2_ex_50k"
LAUNCH_LOG = os.path.join(T, "logs", NAME + ".train.log")
RUN_DIR = os.path.join(T, "sploppy_repo", "runs", NAME)
REF_NET = _ROOT + "/project_files/sploppy_engine/example.nnue"
ENGINE = os.path.join(S, "ab_bu_bin", "base", "stockfish")   # sha256 4b7fabf0..., the live binary
BOT = os.path.join(S, "sploppy_repo", "target", "release", "bot")
TAG = "s2"
SEED = 20260930
MOVE_MS = 500
PAIRS = 100
IN_FLIGHT = 3
POLL_WAIT_S = 300       # while training runs
POLL_SPRT_S = 60
OUT = os.path.join(S, "sf2_gates.log")

FROZEN_NET = os.path.join(S, "ab_%s_bin" % TAG, NAME + ".nnue")
CAND_SEAT = os.path.join(S, "ab_%s_seat.py" % TAG)
REF_SEAT = os.path.join(S, "ab_ex_seat.py")
TALLY = os.path.join(S, "ab_%s_tally.py" % TAG)
PARITY = os.path.join(S, "nn_parity_%s.py" % TAG)
STREAM = os.path.join(S, "ab_%s_eval.jsonl" % TAG)
ERR = os.path.join(S, "ab_%s_eval.err" % TAG)
CAND_LOGS = os.path.join(S, "ab_%s_logs" % TAG)
REF_LOGS = os.path.join(S, "ab_ex_logs")


def say(text):
    with open(OUT, "a") as fh:
        fh.write("%s GATES %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def rewrite(src, dst, replacements):
    """Copy src to dst replacing whole constant lines; each prefix must match exactly one line."""
    lines = open(src).read().split("\n")
    for prefix, new in replacements:
        hits = [i for i, x in enumerate(lines) if x.startswith(prefix)]
        if len(hits) != 1:
            raise RuntimeError("%s: %d lines start with %r" % (src, len(hits), prefix))
        lines[hits[0]] = new
    open(dst, "w").write("\n".join(lines))


def launcher_alive():
    return subprocess.run(["pgrep", "-f", "mix_train_s2.py"], capture_output=True).returncode == 0


def wait_for_training():
    say("waiting for %s (log %s)" % (NAME, LAUNCH_LOG))
    while True:
        text = open(LAUNCH_LOG).read() if os.path.exists(LAUNCH_LOG) else ""
        if "RUN COMPLETE %s" % NAME in text:
            return True
        if "GAVE UP" in text:
            say("STOP: the launcher gave up; no gates run")
            return False
        if not launcher_alive():
            say("STOP: LAUNCHER GONE without RUN COMPLETE; no gates run")
            return False
        time.sleep(POLL_WAIT_S)


def r1():
    ev = json.load(open(os.path.join(RUN_DIR, "eval.json")))
    score = [x for x in open(LAUNCH_LOG).read().splitlines() if "candidate score" in x]
    say("R1 eval.json: W/D/L %s/%s/%s over %s pairs, margin %s, interval %s, bootstrap %s"
        % (ev.get("wins"), ev.get("draws"), ev.get("losses"), ev.get("complete_pairs"),
           ev.get("margin"), ev.get("interval"), ev.get("bootstrap_interval")))
    if score:
        say("R1 " + score[-1].strip())
    upper = ev["bootstrap_interval"][1]
    if upper < 0:
        say("R1 STOP: bootstrap interval upper bound %s < 0 (the new net is clearly weaker in sploppy's engine). Data kept." % upper)
        return False
    say("R1 PASS (bootstrap upper bound %s >= 0) -> R2" % upper)
    return True


def r2():
    os.makedirs(os.path.dirname(FROZEN_NET), exist_ok=True)
    shutil.copyfile(os.path.join(RUN_DIR, "best.nnue"), FROZEN_NET)
    head = open(FROZEN_NET, "rb").read(36)
    say("R2 frozen net %s sha256 %s format %d features %d hidden %d"
        % (FROZEN_NET, sha(FROZEN_NET)[:16], int.from_bytes(head[8:12], "little"),
           int.from_bytes(head[12:16], "little"), int.from_bytes(head[16:20], "little")))
    rewrite(os.path.join(S, "nn_parity_v8.py"), PARITY, [("NET = ", 'NET = "%s"' % FROZEN_NET)])
    p = subprocess.run(["nice", "-n", "5", PY, "-u", PARITY], capture_output=True, text=True, cwd=S)
    lines = [x for x in (p.stdout + p.stderr).splitlines() if "token" not in x.lower() and "secret" not in x.lower()]
    for x in lines:
        if x.startswith("GATE"):
            say("R2 " + x)
    m = [re.search(r"GATE A positions (\d+) mismatches (\d+)", x) for x in lines]
    m = [x for x in m if x]
    gate_b = any(x.startswith("GATE B positions") for x in lines)
    if p.returncode != 0 or not m or int(m[-1].group(2)) != 0 or int(m[-1].group(1)) < 1000 or not gate_b:
        say("R2 FAIL (rc %d): fix the reader before any game. Last output: %s" % (p.returncode, lines[-5:]))
        return False
    say("R2 PASS -> R3")
    return True


def v_checks():
    bad = []
    with open(STREAM) as fh:
        for line in fh:
            if '"event":"end"' in line and "forfeit" in line.lower():
                bad.append("stream: " + line.strip()[:200])
    if os.path.exists(ERR):
        for line in open(ERR):
            if re.search("forfeit|panic|error", line, re.I):
                bad.append("stderr: " + line.strip()[:200])
    for d in (CAND_LOGS, REF_LOGS):
        if os.path.isdir(d):
            for f in os.listdir(d):
                for line in open(os.path.join(d, f)):
                    if line.startswith("NOTE"):
                        bad.append("%s: %s" % (f, line.strip()[:200]))
    return bad


def tally():
    p = subprocess.run([PY, "-u", TALLY], capture_output=True, text=True)
    out = [x for x in p.stdout.splitlines() if x.strip()]
    return (out[-2] if len(out) >= 2 else ""), (out[-1] if out else "tally printed nothing: " + p.stderr[-300:])


def run_eval(pairs, move_ms, threads, seed, stream, err):
    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, CAND_SEAT),
           "--reference", "rpsi:%s -u %s" % (PY, REF_SEAT),
           "--move-ms", str(move_ms), "--pairs", str(pairs), "--threads", str(threads),
           "--player-threads", "1", "--opening-plies", "8", "--seed", str(seed), "--stream"]
    return subprocess.Popen(cmd, stdout=open(stream, "w"), stderr=open(err, "w"),
                            cwd=os.path.join(S, "sploppy_repo"), start_new_session=True)


def r3():
    say("R3 engine %s sha256 %s; reference net sha256 %s" % (ENGINE, sha(ENGINE)[:16], sha(REF_NET)[:16]))
    base = os.path.join(S, "ab_base_seat.py")
    rewrite(base, CAND_SEAT, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                              ("NET       =", 'NET       = "%s"' % FROZEN_NET),
                              ("LOG_DIR   =", 'LOG_DIR   = "%s"' % CAND_LOGS),
                              ("NAME      =", 'NAME      = "SF19-NN-%s"' % TAG)])
    rewrite(base, REF_SEAT, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                             ("NET       =", 'NET       = "%s"' % REF_NET),
                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % REF_LOGS),
                             ("NAME      =", 'NAME      = "SF19-NN-ex"')])
    rewrite(os.path.join(S, "ab_nmp_tally.py"), TALLY,
            [("STREAM    =", 'STREAM    = "%s"' % STREAM),
             ("CAND_NAME =", 'CAND_NAME = "SF19-NN + %s.nnue"' % NAME)])
    for d in (CAND_LOGS, REF_LOGS):
        shutil.rmtree(d, ignore_errors=True)

    smoke_stream, smoke_err = STREAM + ".smoke", ERR + ".smoke"
    p = run_eval(1, 200, 2, 1, smoke_stream, smoke_err)
    rc = p.wait()
    ends = [json.loads(x) for x in open(smoke_stream) if '"event":"end"' in x]
    smoke_bad = [x for x in open(smoke_err) if re.search("forfeit|panic|error", x, re.I)]
    if rc != 0 or len(ends) != 2 or smoke_bad or any("forfeit" in str(e).lower() for e in ends):
        say("R3 SMOKE FAIL rc %d ends %s stderr %s" % (rc, ends, smoke_bad[:3]))
        return
    say("R3 smoke clean: %s" % ends)
    for d in (CAND_LOGS, REF_LOGS):
        shutil.rmtree(d, ignore_errors=True)

    say("R3 START: %d pairs max, %d ms, %d in flight, seed %d -> %s" % (PAIRS, MOVE_MS, IN_FLIGHT, SEED, STREAM))
    p = run_eval(PAIRS, MOVE_MS, IN_FLIGHT, SEED, STREAM, ERR)
    last = None
    while True:
        time.sleep(POLL_SPRT_S)
        status, verdict = tally()
        n = status.split("  ")[0]
        if n != last:
            say("R3 " + status)
            last = n
        finished = p.poll() is not None
        if verdict.startswith("SPRT VERDICT") or finished:
            if not finished:
                os.killpg(p.pid, signal.SIGTERM)
                time.sleep(5)
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGKILL)
            status, verdict = tally()
            say("R3 FINAL " + status)
            say("R3 " + verdict + ("" if verdict.startswith("SPRT VERDICT") else "  (eval exited rc %s before a verdict)" % p.returncode))
            bad = v_checks()
            say("R3 V: %s" % ("PASS (0 forfeits, 0 forfeit|panic|error in stderr, 0 NOTE lines)" if not bad
                             else "FAIL, the verdict is not to be read: %s" % bad[:5]))
            say("DONE. Nothing shipped; a human reads this.")
            return


def main():
    say("started pid %d" % os.getpid())
    if wait_for_training() and r1() and r2():
        r3()
    say("exit")


if __name__ == "__main__":
    main()
