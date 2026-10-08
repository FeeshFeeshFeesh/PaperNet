"""Screen the DANGLING 09-19 WDL arm that was trained and never evaluated.

Why this is worth a screen and costs nothing.  runs/sweep_wdl_full was trained on 2026-09-19 on the
sf1+n1 recipe with the outcome rows added, and its step count was already compensated for the batch-
share trap: 188 / 125 = 1.504 = (2 teacher x 1.0 + 2 wdl x 0.5) / (2 x 1.0).  So against
runs/sf1_ex_50k -- same seed 20260926, same init, same hidden/batch/lr/schedule -- the teacher sees
the SAME number of passes in both arms and the only deliberate difference is the presence of the
outcome-labelled rows.  That is the cleanest label A/B already sitting on this disk, and nobody ever
played a game with it.

NOT PRE-REGISTERED, and NOT the experiment.  Written after the arms were chosen but before any game
of this screen was played.  At 300 pairs / 8 sims the bootstrap half-width is about +-0.07 of margin
while a shippable gain is ~0.029, so this CANNOT confirm the idea.  It is a prior, in one direction
only: the labels demonstrably not hurting on the old recipe raises confidence in spending the cloud
day, and the labels clearly hurting lowers it.  The sf2 arms and their SPRT decide.

The residual confound is the extra steps themselves (188 vs 125), which is precisely what sf2_pass6
controls for in the real experiment.  Do not attribute a win here to the labels alone.

No sys.argv: constants below, run straight from PyCharm.
"""
import datetime
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
T = os.path.dirname(HERE)
REPO = os.path.join(T, "sploppy_repo")
BOT = os.path.join(REPO, "target", "release", "bot")

CANDIDATE = "runs/sweep_wdl_full/best.nnue"   # teacher + outcome rows, 188 steps
REFERENCE = "runs/sf1_ex_50k/best.nnue"       # same recipe, teacher only, 125 steps
OUT = os.path.join(REPO, "runs", "sweep_wdl_full", "h2h_vs_sf1.json")

PAIRS, SIMS, SEED = 300, 8, 20260927          # the protocol of runs/sf2_ex_50k/h2h_s1_s2.json
THREADS, NICE = 3, 15                         # the user is working on this machine
LOG = os.path.join(T, "logs", "wdl1_screen.log")


def log(fh, text):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text)
    fh.write(line + "\n")
    fh.flush()
    print(line, flush=True)


def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as fh:
        for rel in (CANDIDATE, REFERENCE):
            if not os.path.exists(os.path.join(REPO, rel)):
                log(fh, "GAVE UP: %s is missing" % rel)
                return
        cmd = ["nice", "-n", str(NICE), BOT, "eval",
               "--candidate", "nnue:%s?hash=64" % CANDIDATE,
               "--reference", "nnue:%s?hash=64" % REFERENCE,
               "--sims", str(SIMS), "--pairs", str(PAIRS),
               "--threads", str(THREADS), "--seed", str(SEED)]
        log(fh, "SCREEN START %s vs %s: %d pairs at %d sims" % (CANDIDATE, REFERENCE, PAIRS, SIMS))
        # .part then rename: stdout= creates the file the instant the eval starts, so a killed eval
        # would otherwise leave a 0-byte file that looks like a finished result.
        with open(OUT + ".part", "w") as jf:
            rc = subprocess.run(cmd, cwd=REPO, stdout=jf, stderr=fh).returncode
        if rc != 0:
            log(fh, "SCREEN FAILED rc=%d" % rc)
            return
        try:
            r = json.load(open(OUT + ".part"))
        except ValueError:
            log(fh, "SCREEN UNPARSEABLE")
            return
        os.replace(OUT + ".part", OUT)
        games = r["wins"] + r["draws"] + r["losses"]
        score = (r["wins"] + 0.5 * r["draws"]) / games if games else float("nan")
        log(fh, "SCREEN RESULT score %.4f over %d games (W %d D %d L %d) margin %+0.4f CI [%+0.4f, %+0.4f]"
            % (score, games, r["wins"], r["draws"], r["losses"], r.get("margin", 0.0),
               r["interval"][0], r["interval"][1]))
        log(fh, "SCREEN COMPLETE")


if __name__ == "__main__":
    main()
