"""Idea #4's SPRT: does the WDL share matter, at the real time control?

Structure, conventions and every constant name here are wdl_cloud.py's (which shipped idea #1),
so the two runs stay comparable line for line. Only the tests and the opening source differ.

WHAT WE ARE TESTING. Idea #1 established that outcome (WDL) labels help and shipped sf2_wdl
(share 0.50, +33 Elo SPRT). The share sweep then trained 0.25 / 0.75 / 1.00 and ranked them with a
zero-game proxy. That proxy is NOT evidence about Elo:
  - `sep` (decisive |raw| over drawn |raw|) is monotone in share, but it is CIRCULAR on this axis:
    its denominator is the very thing WDL training is minimising. Higher share must raise it.
  - `corr` (raw eval against the true outcome) is not circular, and it peaks at share 0.75.
A screen has already flipped sign against a real SPRT on this project once (8-sim screen said
-15 Elo, the 500 ms SPRT said +33). Screens veto; they never predict. So the share axis is decided
here or not at all.

BOTH TESTS ARE PRE-REGISTERED, NOW, BEFORE ANY GAME IS PLAYED, in this fixed order.

TEST A -- the ship question. sf2_wdl075 vs sf2_wdl (LIVE). If `corr` is telling the truth, 0.75 is
    the best net we have. H0 is "no better than live", H1 is +20 Elo, 5%/5%.

TEST B -- EXPLORATORY, and fixed here before A plays a game so that it cannot be chosen after
    seeing A's score. sf2_wdl100 vs sf2_wdl (LIVE). This asks the shape of the axis, not what to
    ship: if A wins, B says whether more WDL keeps helping; if A loses, B says whether the axis is
    monotonically bad. It runs ONLY if A leaves at least MIN_H hours, and its verdict ships nothing.

NOTHING SHIPS from either test. A human reads the verdict.

THE OPENING BOOK, and the one caveat it carries. Both tests play
/var/tmp/rps/openings/balanced_24ply.txt: 5,603 24-ply openings kept from 12,000 dumped at seed
20260925 because the live net scores them |eval| <= 0.10. Measured on played games, 45.8% of pairs
in the last WDL SPRT were decided by the opening alone -- both engines converted the same won
position, the pair scored 1.0, and the LLR barely moved. Filtering raises informative pairs from
31.8% to 49.0%, i.e. ~1.54x fewer pairs to the same verdict, CI [1.34x, 1.77x].
  CAVEAT, stated before the result: this narrows both tests to BALANCED positions. A net that only
  helps when already winning or already lost would show up smaller here than in a real ladder game.
  That is the price, and it is the honest reason the Elo numbers below are "Elo on balanced
  openings", not "Elo".
  The book was filtered with the REFERENCE net (sf2_wdl, the live one), never the candidate, so the
  selection cannot favour the thing under test.
  5,603 > CAP, so the book never cycles and every pair gets its own opening.

WALL-CLOCK BUDGET. DEADLINE_H caps both tests at 9 h total on a c4a-standard-16, about A$11 at
A$1.23/hr -- inside the A$50/day ceiling with room to spare. A test cut short by the deadline
reports exactly as a capped test does: "not separable". That is an honest outcome, not a missing
one. The host powers itself off when the tests finish, and a `shutdown -h` deadman is armed
separately in case this script dies. The journals live on the disk, which survives the shutdown, so
re-running resumes from them.

IN_FLIGHT 14 on 16 vCPU, for wide_cloud.py's measured reason: a pair costs ONE core (the game is
turn-based, so the two seats alternate), and the referee's own work scales with concurrent games.

No sys.argv: constants below.
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
BOOK       = os.path.join(R, "openings", "balanced_24ply.txt")

MOVE_MS    = 500
OPEN_PLIES = 24
IN_FLIGHT  = 14
MATCH_NICE = 0
ELO0, ELO1 = 0.0, 20.0
CAP        = 1000
DEADLINE_H = 9.0
MIN_H      = 3.0          # test B needs at least this much left, or it is skipped, not squeezed
SHUTDOWN   = True

NETS = os.path.join(R, "nets")
TESTS = [
    # (name, candidate net, reference net, seed, what it is)
    ("share075_vs_live_500ms", "sf2_wdl075.nnue", "sf2_wdl.nnue", 20260998, "PRE-REGISTERED"),
    ("share100_vs_live_500ms", "sf2_wdl100.nnue", "sf2_wdl.nnue", 20260999, "EXPLORATORY"),
]

WORK   = os.path.join(R, "seq")
LOG    = os.path.join(R, "share_cloud.log")
BUDGET = ["--move-ms", str(MOVE_MS), "--player-threads", "1",
          "--opening-plies", str(OPEN_PLIES), "--openings", BOOK]


def say(text):
    line = "%s SHARE %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
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
        subprocess.run(["sudo", "shutdown", "-h", "+2", "share_cloud finished"], check=False)


def main():
    say("started pid %d -- %d in flight, deadline %.1f h, book %s" % (os.getpid(), IN_FLIGHT,
                                                                      DEADLINE_H, BOOK))
    if not os.path.isdir(WORK):
        os.makedirs(WORK)

    sys.path.insert(0, SEQ_DIR)
    import seq_eval
    seq_eval.S, seq_eval.T, seq_eval.REPO, seq_eval.BOT = WORK, R, R, BOT
    say("seq_eval repointed: S=%s BOT=%s" % (seq_eval.S, seq_eval.BOT))

    for p in (ENGINE, BOT, BASE_SEAT, BOOK):
        if not os.path.exists(p):
            say("STOP: missing %s" % p)
            return power_off("missing %s" % p)
    n_open = sum(1 for x in open(BOOK) if x.strip() and not x.lstrip().startswith("#"))
    say("book holds %d openings; CAP is %d, so the book never cycles" % (n_open, CAP))
    if n_open < CAP:
        say("STOP: the book would cycle (%d openings < cap %d)" % (n_open, CAP))
        return power_off("book too short")

    t_start = time.time()
    for name, cnet, rnet, seed, kind in TESTS:
        left = DEADLINE_H - (time.time() - t_start) / 3600.0
        need = MIN_H if kind == "EXPLORATORY" else 0.25
        if left <= need:
            say("SKIPPED %s (%s): only %.2f h of the %.1f h budget left, needs %.2f h"
                % (name, kind, left, DEADLINE_H, need))
            continue
        cand_net, ref_net = os.path.join(NETS, cnet), os.path.join(NETS, rnet)
        missing = [p for p in (cand_net, ref_net) if not os.path.exists(p)]
        if missing:
            say("STOP %s: missing %s" % (name, missing))
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
            "%d in flight, BOOKED openings, %.2f h of budget left"
            % (name, cnet, rnet, MOVE_MS, ELO0, ELO1, CAP, seed, IN_FLIGHT, left))

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
        say("RESULT %s [%s]: score %.3f (%+.1f Elo on balanced openings) over %d pairs, penta %s, "
            "LLR %.3f, verdict %s -- %.1f min at %.0f pairs/hr"
            % (name, kind, j["score"], j["elo"], pairs, j["penta"], j["llr"], j["verdict"],
               dt / 60.0, rate))
        say("WORDS %s: %s" % (name, j.get("words") or seq_eval.words(j, ELO0, ELO1)))

    say("ALL TESTS DONE after %.2f h -- NOTHING SHIPS, a human reads this" % ((time.time() - t_start) / 3600.0))
    power_off("all tests done")


if __name__ == "__main__":
    main()
