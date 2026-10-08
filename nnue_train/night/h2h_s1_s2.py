"""HANDOFF 231dt: the one match that settles what 231do/231dp/231dq could not.

231dp's clean-row rescoring puts sf2_ex_50k 10.7% ahead of sf1_ex_50k on held-out sf-flavour
value error (95% [9.5%, 11.9%], leaked rows removed) and exactly level on n1-flavour error --
a strict improvement in value prediction. Its 200-game screen came back 0.497, a dead heat, but
on a different opening seed from sf1's, so the two screens cannot be subtracted (231dq).

This plays them against each other directly: one shared opening set, 300 pairs = 600 games, on a
seed neither net has ever been measured on. It waits for the leaderboard's own H2H to finish
first so the two matches never share the machine. Constants at the top; run it from PyCharm.
"""
import datetime, json, os, subprocess, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
BOT = os.path.join(REPO, "target/release/bot")
LOG = os.path.join(S, "h2h_s1_s2.log")

CANDIDATE, REFERENCE = "sf2_ex_50k", "sf1_ex_50k"   # score > .500 means the 3x-data net is stronger
PAIRS, SIMS, THREADS, SEED = 300, 8, 2, 20260952    # a seed neither net has been measured on
NICE = 10
WAIT_FOR = os.path.join(REPO, "runs/n1_25k/h2h_board.json")   # the leaderboard's own match
WAIT_PROC = "leaderboard.py"
POLL = 60
OUT = os.path.join(REPO, "runs", CANDIDATE, "h2h_s1_s2.json")


def say(msg):
    line = "%s H2HSS %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def up(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def usable(path):
    try:
        d = json.load(open(path))
        return (d.get("wins", 0) + d.get("draws", 0) + d.get("losses", 0)) > 0
    except Exception:
        return False


def main():
    say("started pid %d" % os.getpid())
    if usable(OUT):
        say("a result already exists at %s -- nothing to do" % OUT)
        return
    for net in (CANDIDATE, REFERENCE):
        p = os.path.join(REPO, "runs", net, "best.nnue")
        if not os.path.exists(p):
            say("MISSING %s -- not starting" % p)
            return
    while not usable(WAIT_FOR) and up(WAIT_PROC):
        say("waiting for the leaderboard's own match (%s)" % os.path.basename(WAIT_FOR))
        time.sleep(POLL)
    say("the leaderboard's match is %s" % ("done" if usable(WAIT_FOR) else "gone without a result"))

    cmd = ["nice", "-n", str(NICE), BOT, "eval",
           "--candidate", "nnue:runs/%s/best.nnue?hash=64" % CANDIDATE,
           "--reference", "nnue:runs/%s/best.nnue?hash=64" % REFERENCE,
           "--sims", str(SIMS), "--pairs", str(PAIRS),
           "--threads", str(THREADS), "--seed", str(SEED)]
    say("MATCH %s (candidate) vs %s (reference), %d pairs at %d nodes, seed %d"
        % (CANDIDATE, REFERENCE, PAIRS, SIMS * 2500, SEED))
    say("PRE-REGISTERED READING: a score ABOVE .500 means %s -- the net trained on 2.5x the rows "
        "with pv rows -- is the stronger one. .480-.520 is the tie band at 600 games (+-35 Elo)." % CANDIDATE)
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(OUT + ".part", "w"),
                        stderr=open(os.path.join(S, "h2h_s1_s2.err"), "a")).returncode
    if rc != 0:
        say("rc %d -- no match" % rc)
        return
    os.replace(OUT + ".part", OUT)
    d = json.load(open(OUT))
    w, dr, l = d.get("wins", 0), d.get("draws", 0), d.get("losses", 0)
    games = w + dr + l
    score = (w + 0.5 * dr) / games if games else float("nan")
    say("H2H RESULT %s vs %s: score %.3f over %d games, W/D/L %d/%d/%d, bootstrap %s"
        % (CANDIDATE, REFERENCE, score, games, w, dr, l, d.get("bootstrap_interval")))
    if 0.480 <= score <= 0.520:
        say("VERDICT: TIE inside the pre-registered band. A 10.7%% cut in held-out value error "
            "bought no measurable Elo.")
    else:
        say("VERDICT: %s is ahead on openings neither net was selected on."
            % (CANDIDATE if score > 0.5 else REFERENCE))
    say("Nothing ships on this; a human reads it.")
    say("exit")


main()
