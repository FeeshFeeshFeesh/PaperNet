"""Sequential head-to-head for the arms (HANDOFF 231fq): stop the match when the answer is known.

The arms used to decide with a fixed 300-pair match (about 4 h at 8 sims under the queue's load)
whatever the gap. This plays the same `bot eval` match with `--stream` and re-scores it after
every finished opening pair with the pentanomial GSPRT that ab_nmp_tally.py and nodes_gate use:
    H0: the candidate is ELO0 Elo (no gain)      H1: the candidate is ELO1 Elo
    LLR = N * (s1 - s0) * (2*mean - s0 - s1) / (2 * var)   over pair scores {0,.25,.5,.75,1}
    LLR >= ln((1-BETA)/ALPHA) -> STRONGER;  LLR <= ln(BETA/(1-ALPHA)) -> NOT STRONGER;
    CAP_PAIRS without either -> too close to call.
The false-verdict rate is at most ALPHA (or BETA). SPRT allows checking after every pair.
A +50 Elo gap then ends in about 50 pairs rather than 300. A gap near the middle of [ELO0, ELO1] runs
to the cap, and a fixed match could not have called that gap either. When H1 is accepted early the
score is biased upward (the test stopped on a lucky streak), so the Elo printed is an upper-leaning estimate.

Not sploppy's native `--sprt`: that one checks first at 128 pairs, in 16-pair batches. Here the
first check comes at MIN_PAIRS, which is where the big gaps (the common case for a new net) save most.

Resumable: the host memory guard kills long jobs. Each run of the match is a SEGMENT
(seq_<name>.<k>.jsonl, seed SEED + 7919*k, so no opening is replayed) and completed pairs from
every segment count. A match left running by a killed arm (its own session, so it outlives the arm)
is ADOPTED through its pid file, never duplicated. The finished verdict is cached in seq_<name>.json.
In-process `nnue:` players have no wall-clock deadline, so the battery guard may pause these safely.
No sys.argv: an import for the arms, constants below.
"""
import json
import math
import os
import signal
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
BOT = os.path.join(REPO, "target", "release", "bot")
ELO0, ELO1 = 0.0, 20.0
ALPHA = BETA = 0.05
MIN_PAIRS = 16
CAP_PAIRS = 400
PSEUDO = 0.5
SIMS, THREADS, NICE = 8, 2, 15
POLL_S = 30
STATUS_EVERY_S = 900
ATTEMPTS = 4


def expected(elo):
    return 1.0 / (1.0 + 10 ** (-elo / 400.0))


def paths(name):
    base = os.path.join(S, "seq_%s" % name)
    return base + ".json", base + ".pid", base + ".err"


def segment(name, k):
    return os.path.join(S, "seq_%s.%d.jsonl" % (name, k))


def segments(name):
    k = 0
    while os.path.exists(segment(name, k)):
        k += 1
    return k


def pair_scores(name):
    """Candidate score of every finished pair across all segments, and odd end events seen."""
    out, odd = [], []
    for k in range(segments(name)):
        pairs = {}
        with open(segment(name, k), errors="replace") as fh:
            for line in fh:
                if '"event":"end"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("end") not in ("Goal", "CaptureClock", "Elimination", "NoMoves"):
                    odd.append(str(ev.get("end")))
                w = ev.get("winner")
                pairs.setdefault(ev["pair"], {})[ev["game"]] = 1.0 if w == ev["game"] else (0.5 if w is None else 0.0)
        out += [(v[0] + v[1]) / 2.0 for v in pairs.values() if 0 in v and 1 in v]
    return out, odd


def judge(done, elo0=ELO0, elo1=ELO1, cap=CAP_PAIRS):
    penta = [0] * 5
    for x in done:
        penta[int(round(x * 4))] += 1
    counts = [c + PSEUDO for c in penta]
    n = sum(counts)
    mean = sum(counts[i] * i / 4.0 for i in range(5)) / n
    var = sum(counts[i] * (i / 4.0) ** 2 for i in range(5)) / n - mean ** 2
    s0, s1 = expected(elo0), expected(elo1)
    llr = n * (s1 - s0) * (2 * mean - s0 - s1) / (2 * max(var, 1e-9))
    lower, upper = math.log(BETA / (1 - ALPHA)), math.log((1 - BETA) / ALPHA)
    real = len(done)
    score = sum(done) / real if real else float("nan")
    elo = -400 * math.log10(1 / min(max(score, 1e-6), 0.999999) - 1) if real else float("nan")
    if real >= MIN_PAIRS and llr >= upper:
        verdict = "H1"
    elif real >= MIN_PAIRS and llr <= lower:
        verdict = "H0"
    elif real >= cap:
        verdict = "cap"
    else:
        verdict = None
    return {"pairs": real, "penta": penta, "score": score, "elo": elo, "llr": llr,
            "bounds": [lower, upper], "verdict": verdict}


def words(j, elo0=ELO0, elo1=ELO1):
    if j["verdict"] == "H1":
        return "STRONGER (H1 %+.0f Elo accepted, error <= %.0f%%; an early stop overstates the gap)" % (elo1, 100 * ALPHA)
    if j["verdict"] == "H0":
        return ("NOT STRONGER (H0 %+.0f Elo accepted over %+.0f, error <= %.0f%%; estimate %+.0f)"
                % (elo0, elo1, 100 * BETA, j["elo"]))
    if j["verdict"] == "cap":
        return ("too close to call: %d pairs (the cap) could not separate %+.0f from %+.0f Elo; estimate %+.0f"
                % (j["pairs"], elo0, elo1, j["elo"]))
    return "undecided"


def status(j):
    return ("pairs %d  penta(0,.25,.5,.75,1)=%s  score %.3f (%+.0f Elo)  LLR %.2f  bounds [%.2f, %.2f]"
            % (j["pairs"], j["penta"], j["score"], j["elo"], j["llr"], j["bounds"][0], j["bounds"][1]))


def running(pid):
    """The pid still runs a `bot eval` (it may be a stranger if the pid was reused)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return "target/release/bot" in cmd and " eval " in cmd


def stop(pid):
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
        try:
            os.killpg(os.getpgid(pid), sig)
        except OSError:
            return
        for _ in range(wait):
            if not running(pid):
                return
            time.sleep(1)


def reported(name, k):
    """Did segment k end with sploppy's own report line (it played every pair it was asked for)?"""
    try:
        return '"event":"report"' in open(segment(name, k), errors="replace").read()[-4000:]
    except OSError:
        return False


def sprt_h2h(name, cand, ref, seed, say, elo0=ELO0, elo1=ELO1, cap=CAP_PAIRS,
             sims=SIMS, threads=THREADS, nice=NICE, budget=None):
    """Play cand vs ref until the SPRT decides. Returns the verdict dict (cached), or None if the
    match could not be played. cand/ref are `bot eval` player specs, relative to REPO. `budget`, if
    given, replaces ["--sims", sims] (e.g. a move-time budget for rpsi: players, 231fr)."""
    budget = list(budget) if budget else ["--sims", str(sims)]
    result, pidfile, err = paths(name)
    try:
        return json.load(open(result))
    except (OSError, ValueError):
        pass
    say("SEQ %s: SPRT H0 %+.0f / H1 %+.0f Elo, alpha=beta=%.2f, first look at %d pairs, cap %d, %s, %d in flight, seed %d"
        % (name, elo0, elo1, ALPHA, MIN_PAIRS, cap, " ".join(budget), threads, seed))
    say("SEQ %s: candidate %s | reference %s" % (name, cand, ref))
    failures, last_status = 0, 0.0
    proc, pid = None, None
    try:
        pid = int(open(pidfile).read().split()[0])
        if running(pid):
            say("SEQ %s: adopting the match still running as pid %d (left by a killed arm)" % (name, pid))
        else:
            pid = None
    except (OSError, ValueError, IndexError):
        pid = None
    while True:
        done, odd = pair_scores(name)
        j = judge(done, elo0, elo1, cap)
        if j["verdict"] is None and pid is None:
            if failures >= ATTEMPTS:
                say("SEQ %s: GAVE UP -- %d segments died; %s" % (name, failures, status(j)))
                return None
            k = segments(name)
            if k and reported(name, k - 1) and j["pairs"] >= cap:
                j["verdict"] = "cap"
            else:
                cmd = (["nice", "-n", str(nice), BOT, "eval", "--candidate", cand, "--reference", ref]
                       + budget + ["--pairs", str(max(1, cap - j["pairs"])),
                                   "--threads", str(threads), "--seed", str(seed + 7919 * k), "--stream"])
                proc = subprocess.Popen(cmd, cwd=REPO, stdout=open(segment(name, k), "w"),
                                        stderr=open(err, "a"), start_new_session=True)
                pid = proc.pid
                with open(pidfile, "w") as fh:
                    fh.write("%d\n" % pid)
                say("SEQ %s: segment %d started, pid %d, %d pairs to go, seed %d"
                    % (name, k, pid, cap - j["pairs"], seed + 7919 * k))
        if j["verdict"] is not None:
            if pid is not None and running(pid):
                stop(pid)
            j.update({"name": name, "candidate": cand, "reference": ref, "elo0": elo0, "elo1": elo1,
                      "alpha": ALPHA, "beta": BETA, "cap": cap, "budget": budget, "seed": seed,
                      "segments": segments(name), "odd_ends": odd[:20], "words": words(j, elo0, elo1),
                      "games": 2 * j["pairs"], "finished": time.strftime("%Y-%m-%d %H:%M:%S")})
            with open(result + ".tmp", "w") as fh:
                json.dump(j, fh, indent=1)
            os.replace(result + ".tmp", result)
            say("SEQ %s: %s" % (name, status(j)))
            say("SEQ %s: VERDICT candidate is %s" % (name, j["words"]))
            if odd:
                say("SEQ %s: NOTE %d games ended oddly: %s" % (name, len(odd), sorted(set(odd))))
            return j
        if time.time() - last_status >= STATUS_EVERY_S:
            say("SEQ %s: %s" % (name, status(j)))
            last_status = time.time()
        time.sleep(POLL_S)
        rc = proc.poll() if proc is not None else (None if running(pid) else -1)
        if rc is not None:
            k = segments(name) - 1
            if rc == 0 or reported(name, k):
                say("SEQ %s: segment %d finished all its pairs" % (name, k))
            else:
                failures += 1
                say("SEQ %s: segment %d died (rc %s); %d of %d failures, resuming in a new segment"
                    % (name, k, rc, failures, ATTEMPTS))
            proc, pid = None, None
