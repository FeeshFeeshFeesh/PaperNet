"""Idea #1's stage-2 SPRTs, run overnight on rps-w1 while Vladimir sleeps.

Structure and every convention here are wide_cloud.py's; only the constants and the two-test loop
differ, so the wide match stays reproducible from its own file.

WHAT THE SCREENS SAID (300 pairs, 8 sims, sploppy's `bot`, vs the live net runs/sf2_ex_50k):
    sf2_wdl    (teacher + WDL, 552 steps)  margin -0.0417  CI [-0.1121, +0.0287]  ~ -14.5 Elo
    sf2_pass6  (teacher only,  552 steps)  margin -0.0500  CI [-0.1195, +0.0195]  ~ -17.4 Elo
Both arms are BELOW the live net by about the same amount, so the outcome labels did not cause the
regression -- the retrain at 552 steps did. Against its own step-matched control the WDL arm is
+0.008 of margin, which is noise, but it is not negative.

TEST A -- PRE-REGISTERED in HANDOFF_wdl_match.md before any arm was trained. Unchanged.
    sf2_wdl vs the live net. Stage-1 gate was "GO if the CI upper bound >= 0"; it is +0.0287, so
    this runs. It is expected to return H0 and that is a real answer: it is the SHIP question, and
    the screen is weak evidence about it -- the screen ran inside sploppy's `bot` at 8 sims while
    this runs inside the SF19 port at 500 ms, a different engine at ~100x the search.

TEST B -- EXPLORATORY, and pre-registered HERE, before either test has played a game.
    sf2_wdl vs sf2_pass6. Same steps, same seed, same init, same mixture except the outcome rows,
    so this is the clean label A/B at the real time control -- the thing test A cannot isolate
    because test A's candidate differs from the live net in BOTH labels and step count.
    It is exploratory because it was chosen after seeing the screens. It decides no shipping. It
    answers only "do outcome labels help, at matched steps", which is idea #1's actual claim.
    Reported as exploratory whatever it returns.

NOTHING SHIPS from either test. A human reads the verdict.

IN_FLIGHT 14, not 15. wide_cloud.py measured that a pair costs ONE core, not two: the game is
turn-based so the two seats alternate. 3-in-flight on 4 vCPU left 1 core spare for the referee and
the OS; 14 on 16 vCPU leaves 2, because the referee's own work scales with the number of concurrent
games and a seat that does not get a whole core searches fewer nodes in its 500 ms. Contention here
is symmetric (both seats are the same engine at the same width, unlike the width match, where it
changed the quantity under test), so it would bias the absolute Elo rather than the comparison --
but headroom is still cheap and the effect is still real.

WALL-CLOCK BUDGET. Two caps of 1000 pairs could run ~19 h unattended, about A$23. DEADLINE_H caps
the pair at 9 h total (~A$11). A test cut short by the deadline reports exactly as a capped test
does: "not separable". That is an honest outcome, not a missing one.

The host powers itself off when both tests finish, and a `shutdown -h +600` deadman was armed
separately in case this script dies. The journals live on the disk, which survives the shutdown, so
nothing is lost either way, and re-running resumes from them.
"""
import datetime
import os
import subprocess
import sys
import time

R          = "/var/tmp/rps"
PY         = "/usr/bin/python3"
SEQ_DIR    = R
ENGINE     = os.path.join(R, "bin", "stockfish")
BOT        = os.path.join(R, "sploppy", "target", "release", "bot")
BASE_SEAT  = os.path.join(R, "deep_cand_seat.py")

MOVE_MS    = 500
OPEN_PLIES = 24
IN_FLIGHT  = 14
MATCH_NICE = 0
ELO0, ELO1 = 0.0, 20.0
CAP        = 1000
DEADLINE_H = 9.0
SHUTDOWN   = True

NETS = os.path.join(R, "nets")
TESTS = [
    # (name, candidate net, reference net, seed, what it is)
    ("wdl_sf2wdl_vs_live_500ms",  "sf2_wdl.nnue", "sf2_ex_50k.nnue", 20260996, "PRE-REGISTERED"),
    ("wdl_sf2wdl_vs_pass6_500ms", "sf2_wdl.nnue", "sf2_pass6.nnue",  20260997, "EXPLORATORY"),
]

WORK   = os.path.join(R, "seq")
LOG    = os.path.join(R, "wdl_cloud.log")
BUDGET = ["--move-ms", str(MOVE_MS), "--player-threads", "1",
          "--opening-plies", str(OPEN_PLIES)]


def say(text):
    line = "%s WDL %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def rewrite(src, dst, replacements):
    """wide_cloud.py's: exactly one line per prefix, or refuse to write the seat."""
    lines = open(src).read().split("\n")
    for prefix, new in replacements:
        hits = [i for i, x in enumerate(lines) if x.startswith(prefix)]
        if len(hits) != 1:
            raise RuntimeError("%s: %d lines start with %r" % (src, len(hits), prefix))
        lines[hits[0]] = new
    open(dst, "w").write("\n".join(lines))


def seat(net, dst, logs, label):
    rewrite(BASE_SEAT, dst, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                             ("NET       =", 'NET       = "%s"' % net),
                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % logs),
                             ("NAME      =", 'NAME      = "%s"' % label),
                             ("FIXED_NODES =", "FIXED_NODES = 0")])
    return "rpsi:%s -u %s" % (PY, dst)


def loaded(net):
    """What the port says it loaded. The log must record the net that actually played."""
    script = ("uci\nsetoption name Use NNUE value true\nsetoption name EvalFile value %s\n"
              "isready\nquit\n" % net)
    out = subprocess.run([ENGINE], input=script, capture_output=True, text=True)
    for line in (out.stdout + out.stderr).splitlines():
        if "RPSNNUE" in line:
            return line.strip()[:160]
    return "?"


def power_off(why):
    say("SHUTTING DOWN: %s" % why)
    if SHUTDOWN:
        subprocess.run(["sudo", "shutdown", "-h", "+2", "wdl_cloud finished"], check=False)


def main():
    say("started pid %d -- two SPRTs, %d in flight, deadline %.1f h" % (os.getpid(), IN_FLIGHT, DEADLINE_H))
    for d in (WORK,):
        if not os.path.isdir(d):
            os.makedirs(d)

    sys.path.insert(0, SEQ_DIR)
    import seq_eval
    # vendor/seq_eval.py (recovered 09-25, verified byte-identical to the old .pyc) still
    # points at Mac paths; nothing in it is machine-specific except these four.
    seq_eval.S, seq_eval.T, seq_eval.REPO, seq_eval.BOT = WORK, R, R, BOT
    say("seq_eval repointed: S=%s BOT=%s" % (seq_eval.S, seq_eval.BOT))

    for p in (ENGINE, BOT, BASE_SEAT):
        if not os.path.exists(p):
            say("STOP: missing %s" % p)
            return power_off("missing %s" % p)

    t_start = time.time()
    for name, cnet, rnet, seed, kind in TESTS:
        left = DEADLINE_H - (time.time() - t_start) / 3600.0
        if left <= 0.25:
            say("SKIPPED %s (%s): only %.2f h of the %.1f h budget left" % (name, kind, left, DEADLINE_H))
            continue
        cand_net, ref_net = os.path.join(NETS, cnet), os.path.join(NETS, rnet)
        for p in (cand_net, ref_net):
            if not os.path.exists(p):
                say("STOP %s: missing %s" % (name, p))
                continue
        say("---- %s  [%s]" % (name, kind))
        say("candidate: %s" % loaded(cand_net))
        say("reference: %s" % loaded(ref_net))
        cand = seat(cand_net, os.path.join(R, name + "_cand_seat.py"),
                    os.path.join(R, name + "_cand_logs"), "cand-" + cnet[:-5])
        ref = seat(ref_net, os.path.join(R, name + "_ref_seat.py"),
                   os.path.join(R, name + "_ref_logs"), "ref-" + rnet[:-5])
        for d in (os.path.join(R, name + "_cand_logs"), os.path.join(R, name + "_ref_logs")):
            if not os.path.isdir(d):
                os.makedirs(d)
        say("%s: %s vs %s at %d ms, SPRT H0 %+.0f / H1 %+.0f Elo, 5%%/5%%, cap %d, seed %d, "
            "%d in flight, opening-plies %d, %.2f h of budget left"
            % (name, cnet, rnet, MOVE_MS, ELO0, ELO1, CAP, seed, IN_FLIGHT, OPEN_PLIES, left))

        t0 = time.time()
        try:
            j = seq_eval.sprt_h2h(name, cand, ref, seed, say, elo0=ELO0, elo1=ELO1, cap=CAP,
                                  threads=IN_FLIGHT, nice=MATCH_NICE, budget=BUDGET)
        except Exception as exc:                      # a crashed test must not cost the other one
            say("RESULT %s: FAILED %r" % (name, exc))
            continue
        dt = time.time() - t0
        if not j:
            say("RESULT %s: none, the match could not be played" % name)
            continue
        pairs = j.get("pairs", 0)
        rate = (pairs / (dt / 3600.0)) if dt > 0 else 0.0
        say("RESULT %s [%s]: score %.3f (%+.1f Elo) over %d pairs, penta %s, LLR %.3f, "
            "verdict %s -- %.1f min at %.0f pairs/hr"
            % (name, kind, j["score"], j["elo"], pairs, j["penta"], j["llr"], j["verdict"],
               dt / 60.0, rate))
        say("WORDS %s: %s" % (name, j.get("words") or seq_eval.words(j, ELO0, ELO1)))

    say("ALL TESTS DONE after %.2f h -- NOTHING SHIPS, a human reads this" % ((time.time() - t_start) / 3600.0))
    power_off("all tests done")


if __name__ == "__main__":
    main()
