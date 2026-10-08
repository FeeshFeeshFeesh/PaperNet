"""HANDOFF 231gn: pool pair results from several machines and run ONE pentanomial SPRT over them.

WHY THIS EXISTS. `bot eval --sprt` keeps its own journal, and that journal belongs to one process on
one host. Three hosts running three SPRTs is three experiments, and picking whichever one crosses a
boundary first is the oldest way there is to manufacture a significant result. If the work is spread
across machines, the TEST has to be single and central: every host plays whole pairs, all pairs go
into one pool, and one log-likelihood ratio is computed over the pool.

Pooling is legitimate here for a specific, earned reason, and only that reason: phase 1 proved the
engine is bit-identical across these machines (same digest over 258 positions, same bench node
count), and every game is NODE-limited, so a pair's outcome does not depend on which box played it.
That is exactly what a clock-limited match would NOT satisfy -- nps depends on the cache hierarchy --
which is why clock-limited games from different hosts must never be pooled. Distinct SEEDS per host
keep the openings disjoint, so pairs stay independent.

THE STATISTIC. Pairs, not games, are the unit: the two games of a pair share an opening and swap
colours, so they are correlated by construction and treating them as independent would understate
the variance. Each pair scores in {0, .25, .5, .75, 1}; the five counts are the pentanomial. The LLR
is the standard normal-approximation GSPRT used for exactly this:

    LLR = n * (s1 - s0) * (s_hat - (s0 + s1) / 2) / sigma^2

with s_hat the observed mean pair score and sigma^2 the sample variance of the pair scores. The
boundaries are log((1 - beta) / alpha) and log(beta / (1 - alpha)).

WHAT IT REFUSES TO DO. It will not report a verdict it has not earned. If the LLR sits between the
boundaries the answer is "no decision yet", printed with how many more pairs it would plausibly take
-- not a p-value squinted at until it looks convincing. It also prints the interval either way,
because an inconclusive SPRT with a tight interval around zero is a real and useful result: it says
the effect is not there at the size we cared about.

No sys.argv: constants below.
"""
import glob
import json
import math
import os

S = "/private/tmp/claude-501/-Users-vladloukine/39e1c2b8-78ec-42f0-b5db-fed0fedd3d4b/scratchpad"

# Every stream to pool. Globs are fine; each file is one host's share of the SAME experiment.
STREAMS = sorted(glob.glob(os.path.join(S, "cloud_sprt_deep*.jsonl")))

H0 = 0.500                 # the null: the candidate is no better than the reference
# H1 is about +30.1 Elo, and it is FIXED BEFORE THE FIRST GAME -- that is the whole discipline of a
# predefined effect size. Astra's instruction was +20 to +30; the upper end is chosen because the
# lower one is not decidable in a night. Separating 0.500 from 0.530 needs ~522 pairs fixed-sample
# at the observed pair SD of 0.21, against ~254 for 0.543, and three boxes produce roughly 60
# pairs/hour between them. Picking a hypothesis the budget cannot resolve would guarantee an
# inconclusive test and call it a finding. The +20 question is still answered descriptively by the
# confidence interval printed below, which is the honest way to report it.
H1 = 0.543
ALPHA = 0.05
BETA = 0.05
LABEL = "sf2_ex_50k vs example at 250000 fixed nodes"
SHALLOW_REF = (0.590, 100, 20000)   # the A2 column this is testing for survival: score, pairs, nodes


def elo(score):
    score = min(max(score, 1e-6), 1 - 1e-6)
    # `+ 0.0` normalises the negative zero that 0.500 produces, so the null hypothesis prints as
    # "0.0 Elo" and not "-0.0 Elo". Cosmetic, but this text is what a human reads at 4am to decide
    # whether anything ships, and a verdict should not contain anything that invites a second look
    # for the wrong reason.
    return -400 * math.log10(1 / score - 1) + 0.0


def pairs_from(path):
    """Complete pairs only. A half-finished pair is dropped, never counted as a draw: a pair whose
    second game never ran is missing data, and filling it in with 0.5 would invent results.

    THE WINNER CONVENTION IS VALIDATED, not assumed. `winner` is a SEAT index, not a game index, and
    the referee swaps which seat has white between the two games of a pair, so the candidate is seat
    0 in game 0 and seat 1 in game 1 -- hence `w == ev["game"]` means the candidate won. Getting this
    backwards would silently report the reference's score as the candidate's, and every number after
    it would be wrong in a way that still looks plausible.

    Checking it against provision_gcp3.py proves nothing: that script shares this exact expression,
    so the two agree by construction. The real check is `bot`'s own `event: report`, which counts
    results in Rust, in the repo author's code, from the same games. On the phase-3 regression stream
    (cloud_reg20k.jsonl):

        bot's report   wins 33, losses 31, draws 16 over 80 games -> (33 + 16/2)/80 = 0.5125
        this parser    40 pairs, score 0.5125, pentanomial [3, 6, 22, 4, 5]

    Identical, and the SIGN agrees too -- bot has the candidate ahead on wins, and this parser puts
    the score above 0.5 rather than at 0.4875. That rules out an inverted convention, which is the
    failure this check exists for. bot's own margin (0.025) is the same figure in its units:
    (wins - losses)/games, i.e. score = 0.5 + margin/2.
    """
    got = {}
    for line in open(path, errors="replace"):
        if '"event":"end"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        w = ev["winner"]
        pts = 1.0 if w == ev["game"] else 0.5 if w is None else 0.0
        got.setdefault(ev["pair"], {})[ev["game"]] = pts
    return [(v[0] + v[1]) / 2.0 for v in got.values() if 0 in v and 1 in v]


def pentanomial(scores):
    bins = [0] * 5
    for s in scores:
        bins[int(round(s * 4))] += 1
    return bins


def bot_report(path):
    """`bot`'s own tally for this stream, counted in Rust by the repo author's code.

    This is the cross-check described in pairs_from(), run automatically on the real data instead of
    once by hand on an old file. Two independent implementations reading the same games should agree
    on the score; if they do not, something is wrong with THIS script's reading of the stream, and
    the right response is to trust neither number until a human has looked.

    Returns None when a stream has no report -- a host killed mid-run is a normal outcome here (the
    watchdog or the deadman may have stopped it) and is not itself an error. The pairs it did finish
    are still perfectly good data.
    """
    last = None
    for line in open(path, errors="replace"):
        if '"event":"report"' not in line:
            continue
        try:
            last = json.loads(line)
        except ValueError:
            continue
    if not last:
        return None
    games = last.get("wins", 0) + last.get("losses", 0) + last.get("draws", 0)
    if not games:
        return None
    return (last["wins"] + last["draws"] / 2.0) / games, games, last.get("forfeits", 0)


def llr(scores, s0, s1):
    n = len(scores)
    if n < 2:
        return None, None, None
    mean = sum(scores) / n
    var = sum((x - mean) ** 2 for x in scores) / (n - 1)
    if var <= 0:
        # Every pair scored identically. Real only in a degenerate match (a dead seat), so say so
        # rather than dividing by zero and printing an infinite LLR.
        return None, mean, 0.0
    value = n * (s1 - s0) * (mean - (s0 + s1) / 2.0) / var
    return value, mean, var


def main():
    if not STREAMS:
        print("STOP: no streams matched. Nothing to pool.")
        return
    per_host, pool = [], []
    for path in STREAMS:
        sc = pairs_from(path)
        per_host.append((os.path.basename(path), sc, bot_report(path)))
        pool.extend(sc)

    print(LABEL)
    print("-----")
    disagree, forfeits = [], 0
    for name, sc, rep in per_host:
        if not sc:
            print("  %-30s    0 pairs" % name)
            continue
        mine = sum(sc) / len(sc)
        note = ""
        if rep:
            theirs, games, ff = rep
            forfeits += ff
            # Half a pair can sit between the two readings: bot counts every finished GAME, this
            # script counts finished PAIRS and drops a trailing half. One game out of 2n moves the
            # score by at most 1/(2n), so that is the tolerance -- anything larger is a real
            # disagreement, not a boundary effect.
            if abs(theirs - mine) > 1.0 / (2 * len(sc)):
                disagree.append((name, mine, theirs))
                note = "  <-- DISAGREES with bot (%.4f over %d games)" % (theirs, games)
            if ff:
                note += "  %d FORFEIT(S)" % ff
        else:
            # Two very different situations produce no report and the note must not pick one:
            # the batch may still be playing, or the host may have been stopped before it
            # finished. Saying "stopped" while it is merely running would be read at a glance
            # as a fault. Either way the finished pairs are good and the cross-check is simply
            # unavailable for this stream.
            note = "  (no report yet -- still running, or stopped early; pairs still valid)"
        print("  %-30s %4d pairs  score %.4f  %s%s"
              % (name, len(sc), mine, pentanomial(sc), note))

    if forfeits:
        print("-----")
        print("WARNING: %d forfeit(s). A forfeit is a seat that crashed or stopped answering, and" % forfeits)
        print("  it is scored as a loss. At 250k nodes a seat that dies partway would look exactly")
        print("  like a weak net. Check the host logs before believing any verdict below.")
    if disagree:
        print("-----")
        print("STOP: this script and `bot` disagree about the score on %d stream(s)." % len(disagree))
        print("  Two independent readings of the same games must agree. They do not, so neither")
        print("  number is trustworthy and no verdict is printed. A human needs to look at this.")
        for name, mine, theirs in disagree:
            print("    %-30s this script %.4f   bot %.4f" % (name, mine, theirs))
        return
    if not pool:
        print("STOP: no complete pairs in any stream.")
        return

    n = len(pool)
    value, mean, var = llr(pool, H0, H1)
    bins = pentanomial(pool)
    upper = math.log((1 - BETA) / ALPHA)
    lower = math.log(BETA / (1 - ALPHA))

    print("-----")
    print("POOLED  %d pairs (%d games)" % (n, 2 * n))
    print("  score        %.4f   (%+.1f Elo)" % (mean, elo(mean)))
    print("  pentanomial  %s" % bins)
    if var:
        se = math.sqrt(var / n)
        print("  std error    %.4f -> 95%% score [%.4f, %.4f] = Elo [%+.1f, %+.1f]"
              % (se, mean - 1.96 * se, mean + 1.96 * se,
                 elo(mean - 1.96 * se), elo(mean + 1.96 * se)))
    print("-----")
    print("SPRT   H0 %.3f (%+.1f Elo)  vs  H1 %.3f (%+.1f Elo),  alpha %.2f beta %.2f"
          % (H0, elo(H0), H1, elo(H1), ALPHA, BETA))
    if value is None:
        print("  LLR undefined: every pair scored identically (variance 0). That is not a result,")
        print("  it is a broken match -- check whether a seat died.")
        return
    print("  LLR %+.3f   against bounds [%.3f, %.3f]" % (value, lower, upper))
    print("-----")
    if value >= upper:
        print("ACCEPT H1: the candidate is better than %+.1f Elo at this depth." % elo(H1))
    elif value <= lower:
        print("ACCEPT H0: the candidate is NOT better than %+.1f Elo at this depth." % elo(H1))
        print("  Note what this does and does not say. It rejects an improvement of the size we")
        print("  predefined. It does not prove the candidate is exactly equal.")
    else:
        print("NO DECISION YET. The LLR is inside the boundaries; neither hypothesis is accepted.")
        if var:
            # How many pairs would carry the CURRENT trend to a boundary. Honest framing: this is a
            # projection from the observed mean, not a promise, and if the true effect is near the
            # midpoint of H0 and H1 the test can run a very long time without deciding.
            per_pair = (H1 - H0) * (mean - (H0 + H1) / 2.0) / var
            if abs(per_pair) > 1e-12:
                need = (upper if per_pair > 0 else lower) / per_pair
                if need > 0:
                    print("  At the observed rate that would take about %d pairs in total (%+d more)."
                          % (int(need), int(need) - n))
            print("  If the true score sits near %.3f, the midpoint, no practical number of pairs"
                  % ((H0 + H1) / 2.0))
            print("  decides it -- read the interval above instead, which is a real answer.")

    s, p, nodes = SHALLOW_REF
    print("-----")
    print("CONTRAST with the shallow regime this is testing for survival:")
    print("  %d nodes: score %.3f (%+.1f Elo) over %d pairs" % (nodes, s, elo(s), p))
    print("  this run: score %.4f (%+.1f Elo) over %d pairs" % (mean, elo(mean), n))
    print("  The question was never 'is sf2 good' but 'does its shallow advantage survive depth'.")
    print("-----")
    print("A human reads this. Nothing ships on it.")


main()
