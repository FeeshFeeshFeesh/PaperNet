"""HANDOFF 231cs: one table for every net the night produced, and one independent match to settle it.

Six arms, three chains and a node-SPRT queue each answer their own question, and each writes its
own eval JSON next to its net. They were all built to the SAME measurement: candidate against
`models/nnue/examples/example.nnue` -- which is the net the live bot is carrying tonight -- at
20,000 nodes a move, 100 paired openings, hash 64. Two opening sets are in play and only two:

    runs/<run>/eval_sweep.json   the arms          seed 20260927 (fixed for every arm)
    runs/<run>/eval.json         the chain's own   the RUN's own seed, read from its config.json

so within one seed the scores are directly comparable, and across seeds they are not: HANDOFF
231dq measured the same net on two opening sets at 0.585 and 0.510, 53 Elo apart. The eval JSON
does not record its seed (231dq), which is why `eval.json`'s is taken from the run's config --
mix_train passes one SEED to both training and eval -- instead of being assumed to be 20260926.
That assumption was true until sf2_ex_50k, which ran on 20260929. Nothing here runs a training or a generator; it reads JSON that already
exists, so it can print the standings at any time and does, every POLL seconds, into its log.

When every registered job is gone it does the one thing the night cannot do for itself: it takes
the two best DISTINCT nets and plays them against each other for H2H_PAIRS pairs on a seed no arm
used. That matters because ranking N candidates on one 100-opening set rewards whoever was
luckiest on those openings; the independent match is the only line in this log that is not
contaminated by that selection, and it is the line to read first in the morning.

Screening, not shipping: nothing is copied anywhere, no bot is touched, and the live net is the
reference, not the candidate. No sys.argv; constants below.
"""
import datetime
import glob
import hashlib
import json
import math
import os
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
BOT = os.path.join(REPO, "target", "release", "bot")
INIT = "models/nnue/examples/example.nnue"
REF = "nnue:%s?hash=64" % INIT
OUT = os.path.join(S, "leaderboard.log")

WANT_PAIRS, WANT_SIMS = 100, 8          # the shared measurement: 100 openings, 20,000 nodes a move
SEED_OF = {"eval_sweep.json": 20260927, "eval.json": 20260926}
H2H_PAIRS, H2H_SIMS, H2H_SEED = 300, 8, 20260951   # a seed no arm used, so the openings are fresh
DEADLINE = "04:30"      # the night's synthesis must exist by morning: stop queueing at this clock
GATE_PATTERN = "[0-9]_gates.py"   # ...unless a move-time gate is running, which contention would ruin
BX_PAIR = ("sf1_ex_50k", "n1_25k")   # 231bx's own question, played whatever the standings say
THREADS, NICE = 2, 15
POLL = 600
WAIT_FOR = ("after_gates.py", "mix_train", "[0-9]_gates.py", "sweep_s1.py", "wdl_arm.py",
            "pv_arm.py", "v9_arm.py", "sf1_chain.py", "sf2_chain.py", "sf3_chain.py",
            "nodes_queue.py", "nodes_gate_", "node_scaling.py", "n1n_gate.py", "scale_arm.py",
            "teacher_arm.py")

_sha_cache = {}



def _usable(path):
    """A cached eval counts only if it parses. `stdout=open(path, "w")` creates the file the
    instant the eval starts, so a killed eval leaves a 0-byte file that looks like a finished
    one; the next run would then skip the eval and die on json.load. Checked, not assumed."""
    try:
        json.load(open(path))
        return True
    except Exception:
        return False

def say(text):
    line = "%s BOARD %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def sha16(path):
    if not os.path.exists(path):
        return "-"
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()[:16]
    return _sha_cache[key]


def elo(score):
    if score is None or score <= 0.0 or score >= 1.0:
        return None
    return -400.0 * math.log10(1.0 / score - 1.0)


def past_deadline():
    now = datetime.datetime.now()
    hh, mm = (int(x) for x in DEADLINE.split(":"))
    return now.hour * 60 + now.minute >= hh * 60 + mm and now.hour < 12


def seed_of(run, name):
    """Which openings this file was played on. `eval_sweep.json` is the sweep's fixed eval seed;
    `eval.json` was played on whatever seed the chain that produced it used, which mix_train
    writes into the run's config as the one seed for both training and eval. The eval JSON itself
    does not carry it -- see HANDOFF 231dq."""
    if name != "eval.json":
        return SEED_OF[name]
    try:
        cfg = json.load(open(os.path.join(REPO, "runs", run, "config.json")))
        return int(cfg["config"]["seed"])
    except Exception:
        return SEED_OF[name]


def scan():
    """Every eval JSON that measures a net against the live net on the shared settings."""
    rows, skipped = [], 0
    for path in sorted(glob.glob(os.path.join(REPO, "runs", "*", "eval*.json"))):
        name = os.path.basename(path)
        if name not in SEED_OF:
            continue                       # eval_h2h.json and friends: a different reference
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if d.get("reference") != REF or d.get("pairs") != WANT_PAIRS or d.get("sims") != WANT_SIMS:
            skipped += 1
            continue
        w, dr, l = d.get("wins", 0), d.get("draws", 0), d.get("losses", 0)
        games = w + dr + l
        if not games:
            continue
        run = os.path.basename(os.path.dirname(path))
        rows.append({"run": run, "kind": name, "seed": seed_of(run, name), "games": games,
                     "w": w, "d": dr, "l": l, "score": (w + 0.5 * dr) / games,
                     "boot": d.get("bootstrap_interval"), "plies": d.get("mean_plies"),
                     "partial": d.get("complete_pairs") != d.get("pairs"),
                     "net": os.path.join(REPO, "runs", run, "best.nnue")})
    for r in rows:
        r["sha"] = sha16(r["net"])
        r["elo"] = elo(r["score"])
    rows.sort(key=lambda r: -r["score"])
    return rows, skipped


def table(rows, skipped):
    say("TABLE  %d nets measured against the LIVE net (example.nnue) at %d nodes, %d openings"
        % (len(rows), WANT_SIMS * 2500, WANT_PAIRS))
    say("TABLE  run | openings | score | Elo | W/D/L | bootstrap | net sha")
    for r in rows:
        say("TABLE  %-22s | seed %d | %.3f | %s | %3d/%3d/%3d | %s | %s%s"
            % (r["run"], r["seed"], r["score"],
               "%+6.1f" % r["elo"] if r["elo"] is not None else "   ?  ",
               r["w"], r["d"], r["l"], r["boot"], r["sha"],
               "  (INCOMPLETE)" if r["partial"] else ""))
    if skipped:
        say("TABLE  (%d eval files ignored: different reference, pairs or node budget)" % skipped)
    # measured 09-19 03:09: sf1_ex_50k scored .585 on seed 20260926 and .510 on seed 20260927 --
    # same net, same reference, same 20000 nodes, only the openings differ. 1.6 SE apart, so not a
    # contradiction; it is what one 200-game screen's error actually looks like.
    say("TABLE  READ THIS AS A SHORTLIST, NOT A RANKING: one 200-game screen carries +-45 Elo of")
    say("TABLE  95% error, so any two rows inside ~0.06 of each other are tied. That is why the")
    say("TABLE  match below is played on a seed nothing here was selected on.")


def pick(rows):
    """The two best DISTINCT nets. A net that appears twice keeps its better score."""
    best = []
    for r in rows:
        if r["sha"] == "-" or any(x["sha"] == r["sha"] for x in best):
            continue
        best.append(r)
        if len(best) == 2:
            break
    return best


def head_to_head(a, b, tag="board"):
    path = os.path.join(REPO, "runs", a["run"], "h2h_%s.json" % tag)
    if not _usable(path):
        cmd = ["nice", "-n", str(NICE), BOT, "eval",
               "--candidate", "nnue:runs/%s/best.nnue?hash=64" % a["run"],
               "--reference", "nnue:runs/%s/best.nnue?hash=64" % b["run"],
               "--sims", str(H2H_SIMS), "--pairs", str(H2H_PAIRS),
               "--threads", str(THREADS), "--seed", str(H2H_SEED)]
        say("H2H %s (candidate) vs %s (reference), %d pairs at %d nodes, seed %d"
            % (a["run"], b["run"], H2H_PAIRS, H2H_SIMS * 2500, H2H_SEED))
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                            stderr=open(os.path.join(S, "leaderboard.err"), "a")).returncode
        if rc == 0:
            os.replace(path + ".part", path)  # never leave a half-written eval that looks cached
        if rc != 0:
            say("H2H rc %d -- no independent match this run" % rc)
            try:
                os.remove(path)
            except OSError:
                pass
            return
    d = json.load(open(path))
    games = d["wins"] + d["draws"] + d["losses"]
    score = (d["wins"] + 0.5 * d["draws"]) / games if games else None
    e = elo(score)
    say("H2H RESULT %s vs %s: score %.3f (Elo %s) over %d games, W/D/L %d/%d/%d, bootstrap %s"
        % (a["run"], b["run"], score, "%+.1f" % e if e is not None else "?", games,
           d["wins"], d["draws"], d["losses"], d.get("bootstrap_interval")))
    lo, hi = (d.get("bootstrap_interval") or [None, None])[:2]
    if lo is not None and lo > 0:
        say("FINAL the strongest net the night produced is %s (it beat %s on openings no arm "
            "selected on). Nothing has shipped: a human reads this before any net goes near the bot."
            % (a["run"], b["run"]))
    elif hi is not None and hi < 0:
        say("FINAL the leaderboard's winner did NOT survive a fresh opening set: %s beat %s. "
            "Read that as selection on the shared 100 openings, and trust %s."
            % (b["run"], a["run"], b["run"]))
    else:
        say("FINAL %s and %s are within noise of each other over %d games; the leaderboard's order "
            "between them is opening luck, not strength." % (a["run"], b["run"], games))


def main():
    say("started pid %d; %d jobs to wait for before the independent match" % (os.getpid(), len(WAIT_FOR)))
    rows, skipped = scan()
    table(rows, skipped)                       # the smoke test: it can read what exists right now
    said = None
    while True:
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            break
        if past_deadline() and not alive(GATE_PATTERN):
            say("DEADLINE %s passed and no move-time gate is running: playing the independent "
                "match now, beside %s. A node-limited match is fair under contention -- it is the "
                "same %d nodes a move however busy the machine is -- so this costs time, not validity."
                % (DEADLINE, ", ".join(busy), H2H_SIMS * 2500))
            break
        if busy != said:
            say("waiting for %s" % ", ".join(busy))
            said = busy
        time.sleep(POLL)
        rows, skipped = scan()
        table(rows, skipped)
    rows, skipped = scan()
    table(rows, skipped)
    if not os.path.exists(BOT):
        say("STOP: %s is missing, so there is no independent match" % BOT)
        return
    best = pick(rows)
    if len(best) < 2:
        say("STOP: only %d distinct net(s) measured, so there is nothing to settle" % len(best))
        return
    # 231bx first: it is a registered question, and the standings must not be able to displace it
    have = {r["run"] for r in rows}
    bx = [{"run": x} for x in BX_PAIR]
    pair = {best[0]["run"], best[1]["run"]}
    if set(BX_PAIR) <= have and pair != set(BX_PAIR):
        say("231bx: %s against %s, the comparison the gate could not make (both were measured only "
            "against the live net)" % BX_PAIR)
        head_to_head(bx[0], bx[1], tag="bx")
    elif set(BX_PAIR) <= have:
        say("231bx: the two best nets ARE %s and %s, so the match below answers it too" % BX_PAIR)
    else:
        say("231bx: %s not both measured, so no 231bx match" % ", ".join(BX_PAIR))
    head_to_head(best[0], best[1])
    say("exit")


main()
