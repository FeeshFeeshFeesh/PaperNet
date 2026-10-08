"""HANDOFF 231dw: do the 20,000-node screen and the 500 ms gate rank the same nets in opposite orders?

231dw found an exact reversal over three nets:

    net           200-game screen, 20,000 nodes     round 3, 500 ms/move
    n1_25k                0.595                            0.505
    sf1_ex_50k            0.585                            0.528
    sf2_ex_50k            0.497                            0.583

Three confounds make that uninterpretable as it stands: the two columns were played on different
opening seeds (231cz measured one net 53 Elo apart on seed alone), the round-3 numbers come from
a SEQUENTIAL test that stopped at the boundary, so each is a stopped estimate and biased away from
the null, and the pair counts differ (100 / 63 / 24). This script removes all three: the same three
nets, the same reference (example.nnue, the net the live bot carries -- sha 0abdf9aa in both trees,
checked), the SAME opening seed in both regimes, and a FIXED 100 pairs every time.

PRE-REGISTERED READING, fixed before anything runs (231dw): if the two orderings are negatively
correlated AND the three intervals are separated within each regime, then the screen measures
something the gate does not, and every "no effect" tonight has to be re-read at the gate's time
control. If the orderings agree, 231dw is noise and says so. Anything in between -- a reversal
with overlapping intervals -- is reported as "not resolved at 100 pairs", not as either verdict.

THREE COLUMNS, because 231dw's two differ in TWO things at once (231eb). The screens are played
by sploppy's own `nnue:` player; the gates are played by the SF19-NN port through an rpsi seat.
So "screen 0.497 vs gate 0.583" mixes an engine change with a time-control change and cannot be
attributed to either. The middle column holds the engine fixed at the port and the budget fixed at
20,000 nodes, which splits the two apart:

  A.  sploppy `nnue:` at 20,000 nodes   -- the screens' regime
  A2. SF19-NN port at 20,000 nodes      -- 231cf's regime, same budget as A, different engine
  B.  SF19-NN port at 500 ms a move     -- round 3's regime, same engine as A2, different budget

A vs A2 is the engine axis. A2 vs B is the budget axis. A and A2 are node-limited, so contention
costs them time and not validity (231cu) and they run as soon as the matches in flight are done.
B is time-limited -- a net measured while six generator workers hold the cores is a net playing
worse than it can -- so it refuses to start until nothing is generating, training, gating or
matching and the one-minute load average is under LOAD_MAX for QUIET_POLLS consecutive polls. If
that never happens it never runs and says so every half hour, and A vs A2 still stands on its own.

Nothing here ships anything; both phases only measure. No sys.argv; constants below.
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
ENGINE = os.path.join(S, "ab_bu_bin", "base", "stockfish")       # the live binary, sha 4b7fabf0...
REF_MS = _ROOT + "/project_files/sploppy_engine/example.nnue"
REF_NODE = "models/nnue/examples/example.nnue"                   # same file, repo-relative

NETS = ["n1_25k", "sf1_ex_50k", "sf2_ex_50k"]
SEED = 20260955          # ONE opening seed for both regimes; no net has been measured on it
PAIRS = 100
SIMS = 8                 # 8 * 2500 = 20,000 nodes, the screens' budget
THREADS = 2
MOVE_MS = 500
IN_FLIGHT = 3
NICE = 10
BOOT = 2000
BOOT_SEED = 20260956

# Frozen copies, so a retrain cannot move the target under a running match. They live INSIDE the
# repo because the node regime takes a repo-relative path and the ms seat takes an absolute one;
# one file serves both, so the two regimes cannot drift apart.
FROZEN = os.path.join(REPO, "runs", "regime_frozen")
OUT = os.path.join(S, "regime_test.log")
RESULT = os.path.join(S, "regime_test.json")
BASE_SEAT = os.path.join(S, "ab_base_seat.py")     # clock-limited seat (round 3's)
NODES_SEAT = os.path.join(S, "ab_nodes_seat.py")   # node-limited seat (231cf's)
PORT_NODES = 20000                                 # same budget as the screens, in the port

POLL_S = 120
BUSY_NODE = ("h2h_s1_s2.py", "nodes_gate_", "leaderboard.py")
BUSY_MS = ("sf19nn_selfplay.py", "mix_train", "[0-9]_gates.py", "nodes_gate_", "h2h_s1_s2.py",
           "leaderboard.py", "node_scaling.py", "n1n_gate.py")
LOAD_MAX = 1.5           # 1-minute load average; the arms park at ~0 and do not count against this
QUIET_POLLS = 3
SAY_EVERY = 15           # while holding, say something every SAY_EVERY polls (30 min)


def say(text):
    line = "%s REGIME %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
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


def freeze():
    os.makedirs(FROZEN, exist_ok=True)
    frozen = {}
    for run in NETS:
        src = os.path.join(REPO, "runs", run, "best.nnue")
        if not os.path.exists(src):
            say("MISSING %s -- not starting" % src)
            return None
        dst = os.path.join(FROZEN, run + ".nnue")
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        frozen[run] = dst
        h = subprocess.run(["shasum", "-a", "256", dst], capture_output=True, text=True).stdout
        say("frozen %-12s %s" % (run, h.split()[0][:16]))
    return frozen


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


def phase_a(frozen):
    """20,000 nodes, the screens' regime, on the shared seed."""
    wait_for(BUSY_NODE, "phase A")
    out = {}
    for run in NETS:
        path = os.path.join(REPO, "runs", run, "eval_regime_node.json")
        if not os.path.exists(path):
            cmd = ["nice", "-n", str(NICE), BOT, "eval",
                   "--candidate", "nnue:runs/regime_frozen/%s.nnue?hash=64" % run,
                   "--reference", "nnue:%s?hash=64" % REF_NODE,
                   "--sims", str(SIMS), "--pairs", str(PAIRS),
                   "--threads", str(THREADS), "--seed", str(SEED)]
            say("phase A %s vs example.nnue, %d pairs at %d nodes, seed %d"
                % (run, PAIRS, SIMS * 2500, SEED))
            rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                                stderr=open(os.path.join(S, "regime_test.err"), "a")).returncode
            if rc != 0:
                say("phase A %s: rc %d -- skipped" % (run, rc))
                continue
            os.replace(path + ".part", path)      # never leave a half-written file that looks cached
        d = json.load(open(path))
        games = d["wins"] + d["draws"] + d["losses"]
        score = (d["wins"] + 0.5 * d["draws"]) / games if games else None
        out[run] = {"score": score, "games": games, "boot": d.get("bootstrap_interval"),
                    "wdl": [d["wins"], d["draws"], d["losses"]]}
        say("phase A %-12s score %.3f (%+.0f Elo) over %d games, W/D/L %d/%d/%d, bootstrap %s"
            % (run, score, elo(score) or 0, games, d["wins"], d["draws"], d["losses"],
               d.get("bootstrap_interval")))
    return out


def machine_quiet():
    busy = [x for x in BUSY_MS if alive(x)]
    load = os.getloadavg()[0]
    return (not busy and load < LOAD_MAX), busy, load


def wait_quiet():
    """Phase B is time-limited, so it may only run on a quiet machine (231cu)."""
    clear = 0
    polls = 0
    while clear < QUIET_POLLS:
        ok, busy, load = machine_quiet()
        clear = clear + 1 if ok else 0
        if polls % SAY_EVERY == 0 or (clear and clear < QUIET_POLLS):
            say("phase B HOLDING: load %.2f (need < %.1f for %d polls, have %d)%s"
                % (load, LOAD_MAX, QUIET_POLLS, clear,
                   "; busy: " + ", ".join(busy) if busy else ""))
        polls += 1
        if clear < QUIET_POLLS:
            time.sleep(POLL_S)
    say("phase B: machine quiet for %d consecutive polls -- starting" % QUIET_POLLS)


def run_port(run, net, stream, err, nodes):
    """The SF19-NN port playing this net against example.nnue. nodes=None means the 500 ms clock."""
    cand_seat = os.path.join(S, "regime_cand_seat.py")
    ref_seat = os.path.join(S, "regime_ref_seat.py")
    cand_logs, ref_logs = os.path.join(S, "regime_cand_logs"), os.path.join(S, "regime_ref_logs")
    base = NODES_SEAT if nodes else BASE_SEAT
    extra = [("FIXED_NODES =", "FIXED_NODES = %d" % nodes)] if nodes else []
    rewrite(base, cand_seat, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                              ("NET       =", 'NET       = "%s"' % net),
                              ("LOG_DIR   =", 'LOG_DIR   = "%s"' % cand_logs),
                              ("NAME      =", 'NAME      = "SF19-NN-%s"' % run)] + extra)
    rewrite(base, ref_seat, [("ENGINE    =", 'ENGINE    = "%s"' % ENGINE),
                             ("NET       =", 'NET       = "%s"' % REF_MS),
                             ("LOG_DIR   =", 'LOG_DIR   = "%s"' % ref_logs),
                             ("NAME      =", 'NAME      = "SF19-NN-ex"')] + extra)
    for d in (cand_logs, ref_logs):
        shutil.rmtree(d, ignore_errors=True)
    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, cand_seat),
           "--reference", "rpsi:%s -u %s" % (PY, ref_seat),
           "--move-ms", str(60000 if nodes else MOVE_MS),   # a ceiling the node seats never reach
           "--pairs", str(PAIRS), "--threads", str(IN_FLIGHT),
           "--player-threads", "1", "--opening-plies", "8", "--seed", str(SEED), "--stream"]
    # Quiet at the start is not quiet throughout, and this match is time-limited, so sample the
    # load while it plays and hand the caller enough to call the result contaminated.
    proc = subprocess.Popen(cmd, stdout=open(stream, "w"), stderr=open(err, "w"), cwd=REPO)
    loads, intruders = [], set()
    while proc.poll() is None:
        loads.append(os.getloadavg()[0])
        intruders.update(x for x in BUSY_MS if alive(x))
        time.sleep(30)
    for d in (cand_logs, ref_logs):
        shutil.rmtree(d, ignore_errors=True)
    watch = {"load_mean": sum(loads) / len(loads) if loads else None,
             "load_max": max(loads) if loads else None,
             "intruders": sorted(intruders)}
    return proc.returncode, watch


def phase_port(frozen, tag, nodes):
    """The SF19-NN port, same seed and same 100 pairs as phase A.

    nodes=PORT_NODES is node-limited, so it runs whenever the other matches are done. nodes=None
    is the 500 ms clock and must wait for a quiet machine -- that is the only difference in how
    the two are scheduled, and it is the whole reason they are separate phases."""
    rng = random.Random(BOOT_SEED)
    out, watches = {}, {}
    budget = "%d nodes" % nodes if nodes else "%d ms" % MOVE_MS
    for run in NETS:
        stream = os.path.join(S, "regime_%s_%s.jsonl" % (tag, run))
        err = os.path.join(S, "regime_%s_%s.err" % (tag, run))
        if not os.path.exists(stream) or len(pair_scores(stream)) < PAIRS:
            if nodes:
                wait_for(BUSY_NODE, "phase %s" % tag)
            else:
                wait_quiet()
            say("phase %s %s vs example.nnue, %d pairs at %s, %d in flight, seed %d"
                % (tag, run, PAIRS, budget, IN_FLIGHT, SEED))
            rc, watch = run_port(run, frozen[run], stream, err, nodes)
            watches[run] = watch
            if rc != 0:
                say("phase %s %s: rc %d -- skipped" % (tag, run, rc))
                continue
            dirty = (not nodes) and (watch["intruders"] or (watch["load_max"] or 0) > LOAD_MAX * 2)
            say("phase %s %s: load mean %.2f max %.2f%s%s"
                % (tag, run, watch["load_mean"] or 0, watch["load_max"] or 0,
                   "; alongside: " + ", ".join(watch["intruders"]) if watch["intruders"] else "",
                   "  <-- CONTAMINATED, a time-limited match shared the machine" if dirty else ""))
        done = pair_scores(stream)
        if not done:
            say("phase %s %s: no complete pairs" % (tag, run))
            continue
        score = sum(done) / len(done)
        penta = [0] * 5
        for x in done:
            penta[int(round(x * 4))] += 1
        boot = boot_interval(done, rng)
        out[run] = {"score": score, "pairs": len(done), "penta": penta, "boot": boot,
                    "watch": watches.get(run)}
        say("phase %s %-12s score %.3f (%+.0f Elo) over %d pairs, penta %s, bootstrap [%.3f, %.3f]"
            % (tag, run, score, elo(score) or 0, len(done), penta, boot[0], boot[1]))
    return out


def spearman(a, b):
    """n = 3, so this is one of {+1, +0.5, -0.5, -1}; report it rather than a p-value."""
    ra = {r: i for i, r in enumerate(sorted(a, key=lambda x: -a[x]["score"]))}
    rb = {r: i for i, r in enumerate(sorted(b, key=lambda x: -b[x]["score"]))}
    shared = [r for r in NETS if r in ra and r in rb]
    if len(shared) < 3:
        return None, ra, rb
    n = len(shared)
    d2 = sum((ra[r] - rb[r]) ** 2 for r in shared)
    return 1 - 6.0 * d2 / (n * (n * n - 1)), ra, rb


def separated(res):
    """Do all three intervals fail to overlap, pairwise, inside one regime?"""
    runs = [r for r in NETS if r in res and res[r].get("boot")]
    if len(runs) < 3:
        return False
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            x, y = res[runs[i]]["boot"], res[runs[j]]["boot"]
            if x[0] <= y[1] and y[0] <= x[1]:
                return False
    return True


def axis(name, a, b, la, lb):
    """One comparison: two columns that differ in exactly one thing."""
    rho, ra, rb = spearman(a, b)
    if rho is None:
        say("%s: not enough finished matches to compare" % name)
        return
    sa, sb = separated(a), separated(b)
    say("%s: %s order %s; %s order %s; Spearman %+.1f; intervals separated: %s %s, %s %s"
        % (name, la, sorted(ra, key=ra.get), lb, sorted(rb, key=rb.get), rho, la, sa, lb, sb))
    if rho < 0 and sa and sb:
        say("  -> REVERSED with separated intervals. %s and %s do not rank these nets the same "
            "way, and every conclusion drawn in one of them has to be re-read in the other."
            % (la, lb))
    elif rho > 0:
        say("  -> the orderings AGREE; this axis is not where 231dw's reversal came from.")
    else:
        say("  -> orderings differ but intervals overlap: NOT RESOLVED at %d pairs. Not a finding."
            % PAIRS)


def report(sploppy, port_nodes, port_ms):
    say("TABLE  net | sploppy nnue @%d nodes | SF19-NN port @%d nodes | SF19-NN port @%d ms"
        % (SIMS * 2500, PORT_NODES, MOVE_MS))
    say("TABLE  all three on opening seed %d, %d pairs each, same reference (example.nnue)"
        % (SEED, PAIRS))
    for run in NETS:
        say("TABLE  %-12s | %s | %s | %s"
            % (run,
               "%.3f" % sploppy[run]["score"] if run in sploppy else "  ?  ",
               "%.3f" % port_nodes[run]["score"] if run in port_nodes else "  ?  ",
               "%.3f" % port_ms[run]["score"] if run in port_ms else "  ?  "))
    dirty = [r for r in port_ms if (port_ms[r].get("watch") or {}).get("intruders")]
    if dirty:
        say("CAUTION: the 500 ms matches for %s shared the machine with another job. A time-limited "
            "match measures how well a net plays with the cores it got, so those scores are a "
            "floor, not a measurement, and anything below that uses them is provisional."
            % ", ".join(dirty))
    say("231dw compared column 1 against a 500 ms column and called the difference a time-control "
        "effect. It cannot be read that way: those two columns differ in the ENGINE as well as the "
        "budget. The two comparisons below separate them, which is the point of column 2.")
    axis("ENGINE (budget held at %d nodes)" % PORT_NODES, sploppy, port_nodes,
         "sploppy", "port")
    axis("BUDGET (engine held at the port)", port_nodes, port_ms, "nodes", "500ms")


def main():
    say("started pid %d: %s, seed %d, %d pairs, three columns (%d nodes sploppy, %d nodes port, "
        "%d ms port)" % (os.getpid(), ", ".join(NETS), SEED, PAIRS, SIMS * 2500, PORT_NODES,
                         MOVE_MS))
    frozen = freeze()
    if not frozen:
        return
    sploppy = phase_a(frozen)
    json.dump({"sploppy": sploppy}, open(RESULT, "w"), indent=1)
    port_nodes = phase_port(frozen, "A2", PORT_NODES)
    json.dump({"sploppy": sploppy, "port_nodes": port_nodes}, open(RESULT, "w"), indent=1)
    say("both node-limited columns done. Phase B is time-limited and holds until the machine is "
        "quiet; if it never is, the two columns above still separate the engine axis on their own.")
    port_ms = phase_port(frozen, "B", None)
    json.dump({"sploppy": sploppy, "port_nodes": port_nodes, "port_ms": port_ms},
              open(RESULT, "w"), indent=1)
    report(sploppy, port_nodes, port_ms)
    say("DONE. Nothing shipped; a human reads this.")


main()
