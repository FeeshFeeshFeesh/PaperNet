"""HANDOFF 231ed: the night's decisive match, replayed in the engine the live bot actually runs.

231eb found that every screen tonight was played by sploppy's own `nnue:` player and every gate
by the SF19-NN port, and that the two disagree by about 60 Elo on sf2_ex_50k at the SAME 20,000
node budget. h2h_s1_s2.py is the match the whole s1-vs-s2 question turns on -- and it is a sploppy
match. This is the same match in the port.

Everything is held fixed except the engine:

    same two nets (the frozen copies 231ea made, so a retrain cannot move them),
    same reference roles (sf2_ex_50k candidate, sf1_ex_50k reference),
    same 20,000 node budget,
    same 300 pairs,
    SAME opening seed 20260952 -- `bot eval` defaults to --opening-plies 8, which is what the
    port matches pass explicitly, so the two matches play the SAME openings.

That last point is the reason this is worth an hour of machine time: the sploppy result and this
result are paired game for game, so the difference between them is the engine and nothing else.

PRE-REGISTERED READING, fixed before the match starts:
  * above .500 means sf2_ex_50k is the stronger net IN THE PORT; .480-.520 is the tie band at
    600 games (+-35 Elo), the same band h2h_s1_s2.py pre-registered.
  * the comparison that matters is this score against the sploppy score. If both land in the tie
    band, the engine does not change the s1-vs-s2 answer and 231eb's 60 Elo is about the level of
    play, not the ranking. If they land on OPPOSITE sides with non-overlapping bootstrap
    intervals, then the engine changes which net is better, and every net chosen on a screen
    tonight was chosen in an engine the bot does not run.
  * a difference between the two matches that is inside either interval is NOT a finding.

Nothing here ships anything. No sys.argv; constants below.

The file name starts with `nodes_gate_` on purpose. That prefix is the token this tree uses to
mean "a node-limited match is holding the cores": regime_test.py's already-running copy watches
for it, so while this plays, its 500 ms phase B correctly counts the machine as busy. It is a
scheduling name, not a claim that this is one of 231cf's SPRT gates -- it is a fixed 300-pair
match with no stopping rule.
"""
import datetime
import json
import math
import os
import random
import shutil
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
PY = "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/.venv/bin/python"
REPO = os.path.join(T, "sploppy_repo")
BOT = os.path.join(REPO, "target", "release", "bot")
ENGINE = os.path.join(S, "ab_bu_bin", "base", "stockfish")     # the live binary, sha 4b7fabf0...
NODES_SEAT = os.path.join(S, "ab_nodes_seat.py")
FROZEN = os.path.join(REPO, "runs", "regime_frozen")           # 231ea's frozen copies

CANDIDATE = "sf2_ex_50k"
REFERENCE = "sf1_ex_50k"
PAIRS = 300
SEED = 20260952          # THE SAME SEED as h2h_s1_s2.py: same openings, so only the engine differs
NODES = 20000
IN_FLIGHT = 3
BOOT = 2000
BOOT_SEED = 20260957

OUT = os.path.join(S, "h2h_port.log")
RESULT = os.path.join(S, "h2h_port.json")
STREAM = os.path.join(S, "h2h_port.jsonl")
ERR = os.path.join(S, "h2h_port.err")
SPLOPPY_RESULT = os.path.join(REPO, "runs", CANDIDATE, "h2h_s1_s2.json")   # h2h_s1_s2.py writes
                                                       # `bot eval`'s own json here

POLL_S = 120
BUSY = ("h2h_s1_s2.py", "leaderboard.py")   # regime_test is handled by the queue script, not here


def say(text):
    line = "%s H2HPORT %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def elo(score):
    if score is None or not 0 < score < 1:
        return None
    return -400 * math.log10(1 / score - 1)


def rewrite(src, dst, replacements):
    """Copy src to dst replacing whole constant lines; each prefix must match exactly one line."""
    lines = open(src).read().split("\n")
    for prefix, new in replacements:
        hits = [i for i, x in enumerate(lines) if x.startswith(prefix)]
        if len(hits) != 1:
            raise RuntimeError("%s: %d lines start with %r" % (src, len(hits), prefix))
        lines[hits[0]] = new
    open(dst, "w").write("\n".join(lines))


def pair_scores(stream):
    """Per-pair scores in {0, .25, .5, .75, 1} from a `bot eval --stream` log (231bu's reader)."""
    pairs = {}
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
            pairs.setdefault(ev["pair"], {})[ev["game"]] = pts
    return [(v[0] + v[1]) / 2.0 for v in pairs.values() if 0 in v and 1 in v]


def boot_interval(done, rng):
    """Percentile bootstrap over PAIRS, which is the unit that was randomised."""
    if not done:
        return None
    n = len(done)
    means = sorted(sum(rng.choice(done) for _ in range(n)) / n for _ in range(BOOT))
    return [means[int(0.025 * BOOT)], means[int(0.975 * BOOT) - 1]]


def frozen_net(run):
    """231ea's frozen copy if it exists, else freeze one now and say so."""
    dst = os.path.join(FROZEN, run + ".nnue")
    if not os.path.exists(dst):
        src = os.path.join(REPO, "runs", run, "best.nnue")
        if not os.path.exists(src):
            return None
        os.makedirs(FROZEN, exist_ok=True)
        shutil.copy2(src, dst)
    h = subprocess.run(["shasum", "-a", "256", dst], capture_output=True, text=True).stdout
    say("net %-12s %s" % (run, h.split()[0][:16]))
    return dst


def wait_for(patterns, what):
    said = False
    while True:
        busy = [x for x in patterns if alive(x)]
        if not busy:
            if said:
                say("%s: clear" % what)
            return
        if not said:
            say("%s: waiting for %s" % (what, ", ".join(busy)))
            said = True
        time.sleep(POLL_S)


def play(cand_net, ref_net):
    cand_seat = os.path.join(S, "h2hport_cand_seat.py")
    ref_seat = os.path.join(S, "h2hport_ref_seat.py")
    cand_logs, ref_logs = os.path.join(S, "h2hport_cand_logs"), os.path.join(S, "h2hport_ref_logs")
    common = [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
              ("FIXED_NODES =", "FIXED_NODES = %d" % NODES)]
    rewrite(NODES_SEAT, cand_seat, common + [("NET       =", 'NET       = "%s"' % cand_net),
                                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % cand_logs),
                                             ("NAME      =", 'NAME      = "SF19-NN-%s"' % CANDIDATE)])
    rewrite(NODES_SEAT, ref_seat, common + [("NET       =", 'NET       = "%s"' % ref_net),
                                            ("LOG_DIR   =", 'LOG_DIR   = "%s"' % ref_logs),
                                            ("NAME      =", 'NAME      = "SF19-NN-%s"' % REFERENCE)])
    for d in (cand_logs, ref_logs):
        shutil.rmtree(d, ignore_errors=True)
    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, cand_seat),
           "--reference", "rpsi:%s -u %s" % (PY, ref_seat),
           "--move-ms", "60000",                     # a ceiling the node seats never reach
           "--pairs", str(PAIRS), "--threads", str(IN_FLIGHT),
           "--player-threads", "1", "--opening-plies", "8", "--seed", str(SEED), "--stream"]
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(STREAM, "w"), stderr=open(ERR, "w")).returncode
    for d in (cand_logs, ref_logs):
        shutil.rmtree(d, ignore_errors=True)
    return rc, (time.time() - t0) / 60.0


def sploppy_side():
    """The same match in sploppy's engine, if h2h_s1_s2.py has written it.

    `bot eval`'s json reports `bootstrap_interval` on the MARGIN scale (margin = 2 * (score - .5);
    checked against the leaderboard's own h2h: score 0.518, margin 0.036, interval [-0.032, 0.107]
    whose midpoint is the margin). This file's own interval is on the SCORE scale, so convert here
    or the two get compared in different units and "separated intervals" means nothing."""
    if not os.path.exists(SPLOPPY_RESULT):
        return None
    try:
        d = json.load(open(SPLOPPY_RESULT))
    except ValueError:
        return None
    games = d.get("wins", 0) + d.get("draws", 0) + d.get("losses", 0)
    if not games:
        return None
    margin_boot = d.get("bootstrap_interval")
    return {"score": (d["wins"] + 0.5 * d["draws"]) / games, "games": games,
            "boot": [0.5 + x / 2.0 for x in margin_boot] if margin_boot else None,
            "margin_boot": margin_boot}


say("started pid %d: %s (candidate) vs %s (reference) in the SF19-NN port, %d pairs at %d nodes, "
    "seed %d -- the same seed h2h_s1_s2.py used, so the openings are the same"
    % (os.getpid(), CANDIDATE, REFERENCE, PAIRS, NODES, SEED))
say("PRE-REGISTERED READING: above .500 means %s is stronger IN THE PORT; .480-.520 is the tie "
    "band at 600 games (+-35 Elo). The finding is this score against the sploppy score: opposite "
    "sides with separated intervals means the ENGINE decides which net is better." % CANDIDATE)

cand_net, ref_net = frozen_net(CANDIDATE), frozen_net(REFERENCE)
if not cand_net or not ref_net:
    say("MISSING a frozen net -- not starting")
    raise SystemExit(1)

done = pair_scores(STREAM)
if len(done) < PAIRS:
    wait_for(BUSY, "match")
    say("playing %d pairs (%d already on disk)" % (PAIRS, len(done)))
    rc, mins = play(cand_net, ref_net)
    say("match rc %d after %.1f min" % (rc, mins))
    done = pair_scores(STREAM)
else:
    say("%d pairs already on disk -- reporting without replaying" % len(done))

if not done:
    say("no complete pairs -- nothing to report")
    raise SystemExit(1)

score = sum(done) / len(done)
boot = boot_interval(done, random.Random(BOOT_SEED))
penta = [0, 0, 0, 0, 0]
for x in done:
    penta[int(round(x * 4))] += 1
e = elo(score)
say("RESULT %s vs %s in the port: score %.3f (%s Elo) over %d pairs (%d games), "
    "penta(0,.25,.5,.75,1)=%s, bootstrap [%.3f, %.3f]"
    % (CANDIDATE, REFERENCE, score, "%+.0f" % e if e is not None else "n/a", len(done),
       2 * len(done), penta, boot[0], boot[1]))

verdict = ("%s is stronger in the port" % CANDIDATE if score > 0.520 else
           "%s is stronger in the port" % REFERENCE if score < 0.480 else
           "a tie in the port: inside the .480-.520 band")
say("VERDICT %s" % verdict)

sp = sploppy_side()
if sp:
    same_side = (score - 0.5) * (sp["score"] - 0.5) >= 0
    sep = boot and sp["boot"] and (boot[0] > sp["boot"][1] or sp["boot"][0] > boot[1])
    sp_iv = "[%.3f, %.3f]" % (sp["boot"][0], sp["boot"][1]) if sp["boot"] else "(no interval)"
    say("SIDE BY SIDE (both intervals on the SCORE scale)  sploppy %.3f %s   |   "
        "port %.3f [%.3f, %.3f]" % (sp["score"], sp_iv, score, boot[0], boot[1]))
    if not same_side and sep:
        say("  -> THE ENGINE DECIDES. The two matches put different nets ahead and their intervals "
            "do not overlap. A net chosen on a sploppy screen is not a net chosen for the bot.")
    elif same_side:
        say("  -> the two engines agree on which net is ahead; 231eb's 60 Elo is about the level "
            "of play, not the ranking.")
    else:
        say("  -> they differ but the intervals overlap: NOT RESOLVED at 300 pairs. Not a finding.")
else:
    say("SIDE BY SIDE: the sploppy match has not written its result yet; re-read this log later.")

json.dump({"candidate": CANDIDATE, "reference": REFERENCE, "engine": "SF19-NN port",
           "nodes": NODES, "seed": SEED, "pairs": len(done), "score": score,
           "elo": elo(score), "penta": penta, "bootstrap": boot,
           "sploppy": sp}, open(RESULT, "w"), indent=1)
say("DONE. Nothing shipped; a human reads this.")
