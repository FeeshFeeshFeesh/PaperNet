"""One page for 8am: what every night job decided, with the pre-registered rule for reading it.

Run it from PyCharm. It reads logs only -- it starts nothing, kills nothing, ships nothing.
Each block prints the job's state (running / done / never started), the lines that carry a
verdict (TABLE, VERDICT, RESULT, score, DONE), and the HANDOFF section that says in advance
how the number was to be read. No sys.argv; constants below.
"""
import datetime
import os
import re
import time
import subprocess

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
HANDOFF = _ROOT + "/project_files/HANDOFF.md"
KEYS = ("TABLE", "VERDICT", "RESULT", "FINAL", "DONE", "STOP", "GAVE UP", "READ:", "score ",
        "H2H", "head to head", "Elo", "STRONGER", "too close",
        "objective",            # 231cd's primary metric: same rows, same split, no seed noise
        "COMPLETE", "PARTIAL")  # a generator's own last word, not left to the 2-line fallback
NOISE = ("waiting for", "pairs/h]", "progress ")
TAIL = 14

# label, log file, process to look for, HANDOFF section that pre-registered the reading
BOARD = S + "/leaderboard.log"      # printed whole, not tailed: it is the summary of everything

JOBS = [
    ("LEADERBOARD", BOARD, "leaderboard.py", "231cs"),
    ("s1 vs s2, direct", S + "/h2h_s1_s2.log", "h2h_s1_s2.py", "231dt"),
    ("  same, in the port", S + "/h2h_port.log", "h2h_port", "231ed"),
    ("batch 1 (sf1) generator", T + "/logs/sf1_ex_50k.log", "sf19nn_selfplay.py", "231bx"),
    ("batch 1 training", T + "/logs/sf1_ex_50k.train.log", "mix_train.py", "231bx"),
    ("batch 1 gates", S + "/sf1_gates.log", "sf1_gates.py", "231bx"),
    ("batch 2 (sf2)", S + "/sf2_chain.log", "sf2_chain.py", "231ca"),
    ("batch 2 gates", S + "/sf2_gates.log", "sf2_gates.py", "231ca"),
    ("batch 3 (sf3)", S + "/sf3_chain.log", "sf3_chain.py", "231cc"),
    ("batch 3 gates", S + "/sf3_gates.log", "sf3_gates.py", "231cc"),
    ("recipe sweep", S + "/sweep_s1.log", "sweep_s1.py", "231cd"),
    ("node SPRT queue", S + "/nodes_queue.log", "nodes_queue.py", "231cf"),
    ("  node SPRT s1", S + "/nodes_gate_s1n.log", "nodes_gate_s1n.py", "231cf"),
    ("  node SPRT s2", S + "/nodes_gate_s2n.log", "nodes_gate_s2n.py", "231cf"),
    ("  node SPRT s3", S + "/nodes_gate_s3n.log", "nodes_gate_s3n.py", "231cf"),
    ("  node SPRT n1", S + "/nodes_gate_n1n.log", "nodes_gate_n1n.py", "231cl"),
    ("outcome-label arm", S + "/wdl_arm.log", "wdl_arm.py", "231dx"),
    ("  same, passes restored", S + "/wdl_full.log", "wdl_full.py", "231dy"),
    ("screen vs gate", S + "/regime_test.log", "regime_test.py", "231ea"),
    ("determinism replay", S + "/pv_arm.log", "pv_arm.py", "231ef"),   # was the pv arm; see 231ef
    ("format-9 arm", S + "/v9_arm.log", "v9_arm.py", "231ci"),
    ("node scaling", S + "/node_scaling.log", "node_scaling.py", "231cj"),
    ("label-scale arm", S + "/scale_arm.log", "scale_arm.py", "231cm"),
    ("teacher arm", S + "/teacher_arm.log", "teacher_arm.py", "231ck"),
    ("per-game weight arm", S + "/weight_arm.log", "weight_arm.py", "231ct"),
    ("watchdog (restarts)", S + "/watchdog.log", "watchdog.py", "231cp"),
]


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def text_skew(proc):
    """231dz: a job runs the file as it was at LAUNCH. Say so when the file has changed since.

    scale_arm read [RUNNING] all night while its log could not have come from the file on disk;
    the file was three hours younger than the process. That is cheap to check, so check it."""
    path = os.path.join(S, proc)
    if not os.path.exists(path):
        return ""
    out = subprocess.run(["pgrep", "-f", proc], capture_output=True, text=True).stdout.split()
    if not out:
        return ""
    lstart = subprocess.run(["ps", "-o", "lstart=", "-p", out[0]],
                            capture_output=True, text=True).stdout.strip()
    started = None
    for fmt in ("%a %d %b %H:%M:%S %Y", "%a %b %d %H:%M:%S %Y"):   # ps follows the locale
        try:
            started = time.mktime(time.strptime(lstart, fmt))
            break
        except ValueError:
            pass
    if started is None:
        return ""
    return ", TEXT EDITED SINCE LAUNCH" if os.path.getmtime(path) > started + 5 else ""


def heading(section):
    for line in open(HANDOFF, errors="replace"):
        if line.startswith("### " + section):
            return line.strip()
    return "### " + section


def interesting(path):
    if not os.path.exists(path):
        return []
    lines = [x.strip() for x in open(path, errors="replace").read().splitlines()]
    if path == BOARD:
        # the board reprints its whole table every 10 minutes; show only the last one, and
        # everything after it, because the independent match is written below the final table
        starts = [i for i, x in enumerate(lines) if "nets measured against the LIVE net" in x]
        if starts:
            return lines[starts[-1]:][:30]
    # an SPRT narrates itself once a minute; keep only the last distribution, not the running one
    hits = [x for x in lines if any(k in x for k in KEYS) and not any(n in x for n in NOISE)
            and not ("penta(" in x and "FINAL" not in x)]
    return hits[-TAIL:] if hits else lines[-2:]


print("=" * 100)
print("THE NIGHT, IN ONE PAGE.   Nothing below has shipped: every verdict here is a measurement.")
print("=" * 100)


def headline():
    """The four answers, before the evidence. Every one of them is a measurement with a rule that
    was written down before the number landed; the section numbers are where to check that."""
    pending = ("still playing.\n  It prints nothing at all until it is done -- the engine writes its JSON only at the end -- so a\n  silent log is not a hung job. The identical 300-pair match on the leaderboard took 2h17m; this\n  one started 06:48:30 under a heavier load, so expect it between 09:30 and 10:20. Re-run this page.")
    try:
        if "H2H RESULT" in open(S + "/h2h_s1_s2.log", errors="replace").read():
            pending = "IT LANDED -- read it at the bottom of this page"
    except OSError:
        pass
    # This block ends in `% pending`, so it is a %-formatted string: every literal percent sign
    # inside it must be written `%%` or the whole page dies with "unsupported format character".
    print("""
WHAT THE NIGHT ANSWERED

  1. The 50,000-node teacher pipeline bought nothing.  n1_25k vs sf1_ex_50k, 600 games on
     openings nothing was selected on: 0.518, inside the .480-.520 tie band.        [231du]
  2. More data of this kind is saturated.  n1's own doubling bought <= 0, and "3x the data"
     turned out to be 2x the passes plus a re-weighting.                       [231do, 231dt]
  3. The recipe sweep found nothing.  Its best arm is exactly the maximum four coin flips
     would produce under the null.                                                  [231dn]
  4. Lower validation loss did NOT mean a better net, and this is now MEASURED, not inferred.
     sf2 had 2.16x the data and 3x the compute. Scored against sf1 on one row set clean for
     both nets, it predicts held-out positions 8.94%% better (interval +3.95%% to +13.34%%,
     bootstrapped by game) and is better calibrated. It then played sf1 600 games and scored
     0.493: -4.9 Elo, interval -28 to +19. A real 9%% cut in value error, worth nothing.
     The objective this project optimises and the strength it wants have come apart.
                                                                      [231dt, 231eg, 231eh]

  5. The biggest thing on the machine, found at 08:15 and NOT a result yet: every screen
     tonight was played by sploppy's own nnue player, and every gate by the SF19-NN port --
     a different search. Same net, same 20,000 nodes, same reference: sf2 scores 0.497 in
     one and 0.584 in the other, about 60 Elo apart. Inside the port, nodes and 500 ms agree
     with each other. So 231dw's "screens disagree with gates" is an ENGINE difference, not
     a time-control one -- and the live bot runs the port, not the screens' engine.  [231eb]

  What 231eb costs the rest of the page, audited: headline 1 is one sploppy match, and the net
  it is about -- n1_25k, which leads every screen tonight -- has never played a game in the
  port. Headline 3 is four sploppy screens, and a null stays a null in any engine, but it means
  "nothing the screens' engine can see". Headline 4 rests on exactly the cell the two engines
  disagree about: sf2 screens 0.497 and gates 0.584. Headline 2 is mostly row-level work and no
  engine touches it. And sf1 and sf2 have never played each other in the port at all -- their
  0.576 and 0.584 are two separate stopped SPRTs against a third net, which is not a comparison.
  So the same 300-pair match was played IN THE PORT, on the same seed, paired game for game.
  IT LANDED at 10:26:42: 0.508 (+5.6 Elo) over 600 games, bootstrap [0.476, 0.539], penta
  [38, 40, 136, 46, 40] -- inside the .480-.520 tie band, so the port says tie as well.
  Side by side on the score scale: sploppy 0.493 [0.460, 0.527] | port 0.508 [0.476, 0.539].
  Opposite sides of .500, but 231ed required SEPARATED intervals before calling it an engine
  effect and these overlap almost entirely, so that reading does NOT fire. What it does kill is
  the objection that sploppy's search was too weak to exploit a better net: a second engine, 60
  Elo away, cannot tell them apart either.                                       [231ed, 231el]

  The outcome label (231cg) screened at 08:53 and it is a sixth kind of nothing: 0.550. 231dx
  fixed the bands before it landed, and 0.535-0.58 is the "record it and leave it" band -- the
  fifth arm on that seed, 0.005 above the fourth, with a bootstrap interval that still contains
  zero. Its objective went 0.56%% the wrong way, which 231dx had already predicted would cost
  1.3%% from the lost teacher passes alone, so the label paid back about three fifths of what it
  cost. The control that gives those passes back (wdl_full) is queued and decides it.   [231ee]
  CORRECTED at 09:28: that 0.56%% was measured on the arm's own validation split. On a row set
  clean for every net it is 2.85%% worse (interval -6.46%% to -0.06%%, clear of zero), so the
  label repaid much less than three fifths -- it is the worst of the five sweep arms on value
  error, and it has the best screen of them. Both facts are in the table below.        [231eh]

  The pv-rows arm (231ch) produced no experiment at all. Its import found no `pv` field on the
  sf1 records, so `--pv-rows 8` expanded nothing and the arm trained the baseline a second time;
  its net is byte-identical to base(s1)'s, which is how it was caught. Read no k8 row as evidence
  either way: 231ch is simply unanswered. Do not re-run the arm to fix it -- 231dl already
  pre-registered the better version, offline and game-free, on a pair of datasets that exists.
  What ran in its place is worth more than the arm was: an exact replay of the sweep's base(s1)
  cell -- same net, same 100 openings, same seed, same everything. IT LANDED at 10:31:05 and it
  came back 0.510 with W/D/L 81/42/77, identical to the last game, forfeits 0. So the screen is
  EXACTLY DETERMINISTIC and has no run-to-run noise at all. The 0.505-to-0.550 spread across the
  sweep is therefore the sampling noise of 200 games, which the bootstrap already reports and
  which is +-35 Elo wide -- the screens resolve nothing because 200 games cannot resolve that,
  not because the harness is flaky. Three controls tonight have now returned exactly zero where
  zero was right: the sha check, the k8 row, and this replay.                  [231ef, 231en]

  The one axis nothing tonight varied is the net and the search themselves (width, format,
  buckets, the goal-threat extension). 231ci, 231cj and 231cb are all still queued or unrun.

  The s2 gate finished at 07:19: STRONGER, in 44 minutes rather than the four hours 231dr
  predicted -- 231dw scores that prediction as wrong and says what it got wrong. Its queue
  then released three jobs at once, so the machine is four times oversubscribed and the last
  match is slower than planned; the sf3 generator was reniced to yield to it.

  Two defects in the reporting itself were found and fixed at 07:50 and they are worth one line,
  because both made this page lie. The watchdog was labelled QUEUED because its success message
  quotes another job's "waiting for" (231cx amendment). And five arms had been running code
  OLDER than their own files since 02:35, so a log line and the script that supposedly wrote it
  did not match; four were parked, so they were relaunched on the current text at no cost, and
  this page now prints TEXT EDITED SINCE LAUNCH when it happens again. The fifth, the outcome-
  label arm below, still carries the flag on purpose: it was in the middle of its eval, and a
  restart would have thrown that away to fix code it has already run past.          [231dz]

  THE MATCH LANDED at 09:20:43, 2h32m in: sf2_ex_50k vs sf1_ex_50k, 600 games, score 0.493,
  W/D/L 238/116/246, bootstrap [-0.08, +0.055] on the margin scale. A TIE inside the
  pre-registered .480-.520 band, so 231dv's prediction of a tie was right. The port's copy of
  the same match, same openings, started 3 seconds later and reads the same headline in the
  engine the live bot actually runs; it is the one number still missing.        [231eg, 231ed]

  Open on the machine right now: sf2_ex_50k vs sf1_ex_50k head to head -- %s""" % pending)


headline()
STALE_MIN = 25          # a job that has not said anything for this long is not "running"


def log_age_min(line):
    """Minutes since a log line was written, from its own MM-DD HH:MM:SS stamp. None if unparsable."""
    m = re.match(r"(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", line)
    if not m:
        return None
    now = datetime.datetime.now()
    when = datetime.datetime(now.year, int(m.group(1)), int(m.group(2)),
                             int(m.group(3)), int(m.group(4)), int(m.group(5)))
    return (now - when).total_seconds() / 60.0


def gen3_eta():
    """The sf3 generator's own progress line, turned into a finish time. It prints games done and
    games/h every ten minutes; if it has not printed yet, say so rather than guess."""
    path = T + "/logs/sf3_ex_50k.log"
    done = rate = None
    for line in (open(path).read().splitlines() if os.path.exists(path) else []):
        if " progress " in line and " games/h" in line:
            try:
                done = int(line.split(" progress ")[1].split("/")[0])
                total = int(line.split("/")[1].split(" ")[0])
                rate = float(line.split("games, ")[1].split(" games/h")[0])
            except (IndexError, ValueError):
                pass
    if not rate or not done:
        return "at a rate it has not printed yet"
    left = (total - done) / rate
    return "about %s (%d games/h, %d of %d done)" % (
        time.strftime("%H:%M", time.localtime(time.time() + left * 3600)), rate, done, total)


def queue_state(path):
    """(is it queued, the line that says what it waits for, its own last line).

    Queued means one thing only: its own last word was "waiting for". Staleness cannot stand in
    for it -- sweep_s1 and nodes_queue go quiet for an hour at a time because the work is in a
    child process, and calling those QUEUED would be as wrong as calling scale_arm RUNNING. The
    age below the heading carries that instead, as a fact rather than a guess."""
    lines = [x.strip() for x in open(path, errors="replace").read().splitlines() if x.strip()] \
        if os.path.exists(path) else []
    if not lines:
        return False, "", ""
    # "waiting for" must be the line's OWN verb, not a quotation: the watchdog logs
    # "RESTARTED pv_arm.py; its last log line was: ... waiting for ...", which is the opposite
    # of queued. A job's own wait line carries it right after the timestamp and tag.
    def own_wait(x):
        return 0 <= x.find("waiting for") <= 40
    waits = [x for x in lines if own_wait(x)]
    return own_wait(lines[-1]), (waits[-1] if waits else ""), lines[-1]


def quiet_for(line):
    age = log_age_min(line)
    if age is None or age < STALE_MIN:
        return ""
    return ", quiet %dh%02dm" % (age // 60, age % 60) if age >= 60 else ", quiet %dm" % age


for label, path, proc, section in JOBS:
    queued, wait_line, tail_line = queue_state(path)
    if alive(proc):
        state = ("QUEUED" if queued else "RUNNING" + quiet_for(tail_line)) + text_skew(proc)
    else:
        state = "done/exited" if os.path.exists(path) else "never started"
    print("\n%-26s [%s]   %s" % (label, state, heading(section)))
    if state == "QUEUED":
        print("    " + wait_line[:200])
        if tail_line != wait_line:
            print("    last thing it did: " + tail_line[:180])
        continue
    for line in interesting(path):
        print("    " + line[:200])
def h2h_reading():
    """Say in words which net won the independent match.

    The board logs `H2H <a> (candidate) vs <b> (reference)` and then a score. A score above .500
    means the CANDIDATE is stronger -- and tonight the candidate is n1_25k, so a score above .500
    means the 50,000-node teacher data did NOT help. That is one inversion too many to leave to a
    reader at 8am, so it is spelled out here."""
    if not os.path.exists(BOARD):
        return
    text = open(BOARD, errors="replace").read()
    pair = re.findall(r"H2H (\S+) \(candidate\) vs (\S+) \(reference\)", text)
    res = re.findall(r"H2H RESULT \S+ vs \S+: score ([0-9.]+)", text)
    if not pair:
        return
    cand, ref = pair[-1]
    print("\n%-26s [the only match tonight on openings nothing was selected on]" % "READING THE H2H")
    # HANDOFF 231ds: the board labelled every eval.json "seed 20260926" from a constant keyed on the
    # FILENAME. n1_25k's was really 20260918 and sf2_ex_50k's is 20260929, so the table's top two rows
    # were printed as one opening set and are two. leaderboard.py is fixed; its log above predates it.
    print("    CAUTION on the LEADERBOARD table above (231ds): its `seed` column is wrong for")
    print("    n1_25k, whose screen was played on 20260918, not 20260926. The table's top two rows")
    print("    are therefore NOT the same opening set, and 231dq measured one net 53 Elo apart on")
    print("    two sets. This match is the comparison that does not depend on any of that.")
    print("    UNITS: every `bootstrap` interval on this page -- the leaderboard's, the sweep's,")
    print("    the arms' -- is on the MARGIN scale, 2 x (score - 0.5), not the score scale. So an")
    print("    interval containing 0 means the net is not distinguishable from its reference, and")
    print("    a score of 0.550 with [-0.02, 0.215] is a net that might be even or might be ahead.")
    if not res:
        print("    %s (candidate) vs %s (reference) is still playing." % (cand, ref))
        print("    When it lands: score above .500 means %s is the stronger net." % cand)
        return
    score = float(res[-1])
    # 600 games resolves about +-35 Elo. Naming a winner inside that band and then printing the
    # caveat underneath is how a report contradicts itself; the band is checked first instead.
    tied = 0.480 <= score <= 0.520
    winner = cand if score > 0.5 else ref
    print("    %s (candidate) vs %s (reference) scored %.3f over 600 games." % (cand, ref, score))
    if tied:
        print("    TOO CLOSE TO CALL. 600 games resolves about +-35 Elo, and .480-.520 is inside that.")
        print("    231bx asked whether the 50,000-node teacher data helped over n1_25k: this says")
        print("    neither net is clearly ahead, which is itself the answer to a question that")
        print("    assumed one of them would be.")
        print("    CAUTION (231eb, 08:15): this match, like every screen tonight, is played by")
        print("    sploppy's own nnue player. The live bot runs the SF19-NN port, and the two")
        print("    disagree by about 60 Elo on sf2 at the same node budget. Whatever this says,")
        print("    it says it about the screens' engine. 231ea is measuring the gap now.")
        return
    print("    %s is ahead on openings neither net was selected on." % winner)
    print("    231bx asked whether the 50,000-node teacher data helped over n1_25k:")
    print("    %s" % ("NO -- n1_25k, trained without it, is ahead." if winner == "n1_25k" else
                      "YES -- the mixed 50k-node net is ahead." if winner.startswith("sf")
                      else "see the score above; neither net is n1_25k."))
    print("    Still a screen, not a gate: nothing ships on it.")



def h2h_ss_reading():
    """The second match: sf2_ex_50k against sf1_ex_50k, on a seed neither was measured on.

    This one is not about which net ships. It is about whether the objective the trainer minimises
    predicts the thing the engine does. HANDOFF 231dt measured sf2 at 10.7% lower held-out value
    error on identical clean rows, with a 95% interval nowhere near zero, and its 200-game screen
    was 0.497. If this match is also a tie, the objective is a poor target for this engine; if sf2
    wins, 200 games simply could not see it. Both readings are pre-registered in 231dt."""
    log = S + "/h2h_s1_s2.log"
    if not os.path.exists(log):
        return
    text = open(log, errors="replace").read()
    res = re.findall(r"H2H RESULT (\S+) vs (\S+): score ([0-9.]+)", text)
    print("\n%-26s [231dt: does 10.7%% less value error buy any Elo at all?]" % "READING s1 vs s2")
    print("    On clean held-out rows sf2_ex_50k scores 10-11% better than sf1_ex_50k on one data")
    print("    flavour and ties on the other -- but 231dv split that tie and it is NOT a tie: sf2 is")
    print("    3.7% WORSE at value there [1.2%, 6.6%] and 11.5% better at agreeing with itself, and")
    print("    evaluate() sums the two. sf2 specialised toward the flavour it saw more of. Its")
    print("    200-game screen was 0.497, and 231dv predicts this match ties too.")
    if not res:
        state = "still playing" if "H2HSS started" in text and "waiting for" not in text.splitlines()[-1] else "queued behind the leaderboard match"
        print("    sf2_ex_50k (candidate) vs sf1_ex_50k (reference) is %s." % state)
        print("    When it lands: score above .500 means sf2_ex_50k -- the 2.5x-data net -- is stronger.")
        print("    CAUTION (231eb, 08:15): this match, like every screen tonight, is played by")
        print("    sploppy's own nnue player. The live bot runs the SF19-NN port, and the two")
        print("    disagree by about 60 Elo on sf2 at the same node budget. Whatever this says,")
        print("    it says it about the screens' engine. 231ea is measuring the gap now.")
        return
    cand, ref, score = res[-1][0], res[-1][1], float(res[-1][2])
    print("    %s (candidate) vs %s (reference) scored %.3f over 600 games." % (cand, ref, score))
    if 0.480 <= score <= 0.520:
        print("    TOO CLOSE TO CALL, and that is the informative outcome: -4.9 Elo, interval -28 to")
        print("    +19. The loss function and the scoreboard are measuring different things.")
        print("    SETTLED AT 09:27, because the 10.7% could have been an artifact of sf2 validating")
        print("    on its own split (which holds PV rows). Both nets were scored offline on ONE set:")
        print("    the 20,955 validation roots of selfplay_sf2_ex_50k_nopv that are orbit-clean for")
        print("    both. sf2 is 8.94% better there [+3.95%, +13.34%, bootstrapped by game] and better")
        print("    calibrated (bias +0.0019 vs +0.0075). The gain is real; it bought nothing. [231eg]")
        print("    Reconciling 231dv's 'sf2 is 3.7% WORSE at value [1.2%, 6.6%]': that was sf1's own")
        print("    val rows, split by flavour, with a ROW-level interval. Row-level intervals here")
        print("    are about 4.6x too narrow (231eg), so widened it does not clear zero. Different")
        print("    row set, different statistic, and the narrower claim does not survive its own")
        print("    error bar. The number to quote is +8.94% on rows clean for both.")
        return
    if score > 0.5:
        print("    sf2_ex_50k is ahead. The value error DID convert: 2.5x the rows bought Elo that")
        print("    a 200-game screen (0.497) could not see. 231dh's decoupling is weaker than it looked.")
    else:
        print("    sf1_ex_50k is ahead -- the net with MORE value error won. That is the strongest")
        print("    form of the decoupling: minimising the objective further made the engine worse.")
    print("    Still a screen, not a gate: nothing ships on it.")


BOT_LOG = _ROOT + "/project_files/bot.log"
print("\n%-26s [read-only: nothing here was started, stopped or reconnected]" % "the live bot")
if os.path.exists(BOT_LOG):
    when = datetime.datetime.fromtimestamp(os.path.getmtime(BOT_LOG)).strftime("%m-%d %H:%M")
    stale = (time.time() - os.path.getmtime(BOT_LOG)) / 3600.0
    print("    bot.log last written %s (%.1f hours ago)" % (when, stale))
    safe = [x.strip() for x in open(BOT_LOG, errors="replace").read().splitlines()
            if not any(k in x.lower() for k in ("token", "key", "secret"))]
    for line in safe[-3:]:
        print("    " + re.sub(r"[A-Za-z0-9_-]{32,}", "<REDACTED>", line)[:160])
else:
    print("    no bot.log")

def offline_table():
    """231eh: every net of the night scored on ONE row set, paired against base(s1), with the
    bootstrap clustered BY GAME. This is the only comparison tonight with enough resolution to
    separate anything, and it is game-free -- three minutes of CPU for seven nets."""
    import json
    path = S + "/offline_sweep.json"
    if not os.path.exists(path):
        return
    try:
        d = json.load(open(path))
    except ValueError:
        return
    screens = {"base(s1)": "0.510", "p8": "0.517", "lr2": "0.505", "ema": "0.545",
               "wdl": "0.550", "k8": "pending", "sf2": "-4.9 Elo h2h"}
    print("\n%-26s [231eh: %s clean rows from %d games, orbit-clean for every net]"
          % ("VALUE ERROR, ONE RULER", "20,955", d["paired"][0]["games"] if d.get("paired") else 0))
    print("    %-10s %12s  %-26s %s" % ("arm", "clean mse", "vs base(s1), by game", "its screen"))
    for label in screens:
        if label not in d:
            continue
        pair = next((x for x in d.get("paired", []) if x["b"] == label), None)
        cell = "--"
        if pair:
            lo, hi = pair["rel_interval"]
            cell = "%+6.2f%% [%+6.2f, %+6.2f]%s" % (pair["rel_percent"], lo, hi,
                                                   "" if lo <= 0 <= hi else "  *")
        print("    %-10s %12.8f  %-26s %s" % (label, d[label]["mse"], cell, screens[label]))
    print("    * = the interval clears zero. Only p8, wdl and sf2 do; the rest are unrankable,")
    print("      which puts the resolution limit of this row set at about 3%.")
    print("    k8 returning exactly 0.00% is the control: 231ef showed its net is byte-identical")
    print("      to base(s1)'s, and the scorer, told nothing of that, agrees to the last digit.")
    print("    Read down the last two columns: the two arms with the WORST value error have the")
    print("      two BEST screens. Rank correlation -0.70 on five arms -- not evidence by itself,")
    print("      but it points the same way as the one match that had the games to resolve")
    print("      anything (sf2: +8.94% value error, -4.9 Elo over 600 games).")


offline_table()


def strata_table():
    """231ej: the same clean rows split by |target|, to answer whether sf2's 8.94% is parked in
    positions a search would not care about. It is not. Read the LAST column, not the percentages:
    the strata have very different base errors, so a relative gain is not comparable across rows."""
    import json
    path = S + "/offline_strata.json"
    if not os.path.exists(path):
        return
    try:
        d = json.load(open(path))
    except ValueError:
        return
    total = sum(x["rows"] * x["base_mse"] * x["nets"]["sf2"]["rel"] / 100.0 for x in d["strata"])
    print("\n%-26s [231ej: where sf2's +8.94%% actually is]" % "VALUE ERROR BY |TARGET|")
    print("    %-22s %6s %6s %12s  %-24s %s"
          % ("stratum", "rows", "games", "base mse", "sf2 vs base, by game", "share of gain"))
    for x in d["strata"]:
        n = x["nets"]["sf2"]
        lo, hi = n["rel_interval"]
        share = 100.0 * x["rows"] * x["base_mse"] * n["rel"] / 100.0 / total
        print("    %-22s %6d %6d %12.8f  %+6.2f%% [%+6.2f, %+6.2f]%-1s %11.1f%%"
              % (x["stratum"], x["rows"], x["games"], x["base_mse"], n["rel"], lo, hi,
                 "" if lo <= 0 <= hi else "*", share))
    print("    * = the interval clears zero.")
    print("    The biggest RELATIVE gain is at |t| >= .8, which would have explained the tie --")
    print("      except decided positions already have the lowest base error, so 17% of it is")
    print("      only a quarter of the total. The single largest SHARE is the undecided half.")
    print("    So sf2 is resolvably better exactly where the game is still open, and still")
    print("      -4.9 Elo over 600 games. 231ei pre-registered this as the worst case.")
    sign = d.get("sign", {})
    if sign:
        print("    sign agreement (right side ahead, |target| > .05, 15302 rows): "
              + "  ".join("%s %.4f" % (k, v) for k, v in sign.items()))
        print("      +0.74 points for sf2 over base -- the first thing measured tonight that")
        print("      agrees with value accuracy instead of against it, but small, and not")
        print("      interval-estimated. A hint about the next instrument, not a result.")


strata_table()


def move_agreement():
    """231ek: the same two nets, the same clean rows, but the question is what the SEARCH plays
    rather than what the net predicts. Queued behind the port match; prints nothing until it lands."""
    import json
    path = S + "/move_agree.json"
    if not os.path.exists(path):
        print("\n%-26s [231ek: queued behind the port match, ~5 min of one thread once it starts]"
              % "MOVE AGREEMENT")
        return
    try:
        d = json.load(open(path))
    except ValueError:
        return
    lo, hi = d["search_interval"]
    plo, phi = d["pv_interval"]
    print("\n%-26s [231ek: %d positions from %d games, by-game interval]"
          % ("MOVE AGREEMENT", d["positions"], d["games"]))
    print("    base(s1) and sf2 play the SAME move in  %.1f%% [%.1f, %.1f] of positions"
          % (100 * d["search_agreement"], 100 * lo, 100 * hi))
    print("    they share the first %.2f [%.2f, %.2f] plies of the principal variation"
          % (d["pv_shared_plies"], plo, phi))
    print("    policy prior peak / uniform = %.3f (1.000 = no policy head; see 231ek fact 2)"
          % d["policy_peak_over_uniform"])
    a = d["search_agreement"]
    if a >= 0.95:
        print("    READING (231ek, pre-registered): >= 95%. The tie is explained mechanically --")
        print("      a 9% better value head changes at most one move in twenty and 600 games")
        print("      cannot resolve that. The fix is MORE GAMES, not a new instrument.")
    elif a >= 0.80:
        print("    READING (231ek, pre-registered): 80-95%. Partial. They differ often enough to")
        print("      see and still draw, so the disagreements are between near-equal moves. Next")
        print("      measure the SIZE of the mistakes, not their count.")
    else:
        print("    READING (231ek, pre-registered): < 80%. They play substantially different games")
        print("      and are equally strong. Held-out value error is then close to an irrelevant")
        print("      ruler, and every arm ranked by objective tonight was ranked by a number that")
        print("      does not predict play. This is the reading 231ei called the worst case.")


move_agreement()


def written_up_tonight():
    """Every HANDOFF section stamped with today's date, newest last. This is the index into the
    long record: the one page tells you the state, these tell you what was concluded and why."""
    today = datetime.date.today().strftime("%m-%d")   # headings are stamped (MM-DD HH:MM)
    found = []
    for line in open(HANDOFF, errors="replace"):
        m = re.match(r"^###\s+(\S+)\s+(.*?)\s*\((?:PRE-REGISTERED )?%s[^)]*\)\s*$"
                     % re.escape(today), line)
        if m:
            found.append((m.group(1), m.group(2)))
    print("\n" + "=" * 100)
    if not found:
        print("Written up in HANDOFF today: nothing yet.")
        return
    print("Written up in HANDOFF today -- %d sections, read them in this order:" % len(found))
    for num, title in found:
        print("  %-8s %s" % (num, title))

written_up_tonight()

h2h_reading()
h2h_ss_reading()

print("\n" + "=" * 100)
print("Open decisions waiting on a human:")
print("  * anything a gate or an SPRT called STRONGER still needs a human to read it before it ships")
print("  * promoting a generator net (the teacher, 231ck) is a human decision, same as shipping")
print("  * 231cb, the goal-threat extension A/B, is pre-registered and still unrun (it must not run")
print("    beside a generator; it was queued for after the s2 gates)")
print("  * 231ef/231eh: the pv-rows question (231ch) was never actually tested -- the sf1 records")
print("    have no pv field, so the arm retrained the baseline (its net is byte-identical, and the")
print("    scorer returns exactly 0.00% for it, which is a nice control). The scorer 231dl asked")
print("    for now EXISTS and has run on every other net. What is still missing is the one net it")
print("    needs: an sf2 trained on selfplay_sf2_ex_50k_nopv at sf2's own 20 x 368 steps, so the")
print("    only difference is the PV rows. ~45 minutes of training, then minutes to score. It is")
print("    NOT queued -- it wants a quiet machine and the sf3 chain owns the day. Your call.")
print("  * 231ej/231ek: the value-error ruler is now exhausted as an explanation. sf2 is better")
print("    everywhere, better by the most where the game is still OPEN (37% of the total gain,")
print("    interval [+6.8, +12.8]), and still -4.9 Elo. 231ek is queued to ask whether it even")
print("    changes the move. Two things it already found change the plan: this search is")
print("    ALPHA-BETA (no visit distribution to weight by, so half of 231eh's recommendation is")
print("    withdrawn) and the policy head is EXACTLY UNIFORM (these are pure value nets). If")
print("    agreement comes back under 80%, stop ranking arms by objective entirely. Your call")
print("    on whether that changes what the sf3 chain is allowed to conclude.")
print("  * 231ch left open: whether to shorten gate round 3. The recommendation CHANGED at 07:20")
print("    with a third data point (231dw): round 3 cost 4h36m / 2h22m / 44m to measure +3 / +19")
print("    / +58 Elo. Its cost falls as the effect grows -- that is what a sequential test is for")
print("    -- and the slow case is the one that returns 'no effect as large as +30 Elo'. Do NOT")
print("    cut it to a screen; if it must be cheaper, cut 100 pairs to 60. Still your call.")
print("  * 231eb is the thing most worth deciding, and it is queued (231ea, running now): the")
print("    screens and the gates use DIFFERENT ENGINES, so 231dw's reversal was never a")
print("    time-control finding. regime_test.py plays all three nets in all three regimes on")
print("    one seed at a fixed 100 pairs. Its first two columns are node-limited and run")
print("    today, behind the port head-to-head 231ed puts first. The third is 500 ms and it")
print("    needs an IDLE machine: a time-limited match on a busy one measures the machine.")
print("    Your call, and it is NOT 'wait a few hours'. The generator ends")
print("    " + gen3_eta() + ", but the sf3 chain then goes on to training, its gates")
print("    and its node SPRT, and the eight arms queued behind it each train and then play a")
print("    200-game screen. On that schedule the 500 ms column does not run today at all --")
print("    it waits for the whole queue to drain. To get it today something has to be paused,")
print("    and the generator is the cheapest: about 2.5 hours of lost generation, resumable.")
print("  * 231cl, the node SPRT for n1_25k in the port, was left out of the queue and 231ed makes")
print("    it the control for headline 1: the net that leads every screen tonight is the only one")
print("    with no verdict in the engine the bot runs. regime_test's A2 column gives it 100 fixed")
print("    pairs there today (+-45 Elo, a measurement, not a verdict); the SPRT is about an hour.")
print("  * PaperNet0 has been offline since its own clean shutdown on 09-17 23:48; reconnecting it")
print("    is a human decision and nothing overnight touched it")
print("=" * 100)
