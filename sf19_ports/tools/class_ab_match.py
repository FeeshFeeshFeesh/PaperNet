"""A/B match for one eval option, at an opening width that can actually decide anything.

Builds two copies of the match seat that differ in exactly ONE UCI option, runs sploppy's referee
between them, and leaves behind both a game stream and per-seat score logs stamped with a game key.
The stream feeds the SPRT tally; the seat logs feed the conversion census. Both matter here, and
for opposite reasons:

  - an SPRT measures Elo, which is what ships;
  - the conversion census measures the throw rate, which is the number this whole line of work is
    trying to move. A term that bites mainly in won endgames can move the throw rate a long way
    while barely touching Elo, and it can also turn the engine passive -- hoarding pieces, reaching
    fewer won positions at all -- which Elo alone would hide. So read both.

`--opening-plies 24`, not the referee's default of 8: at 8 plies only about 10% of games are
decisive, every pair scores 0.5, and the LLR falls monotonically until the test returns H0 at the
first look -- for a candidate 200 Elo stronger just as readily as for a dead-equal one. That is not
a slow test, it is an empty one.

The SAME binary serves both arms; it benches identically with the option at 0. No sys.argv.
"""
import os
import subprocess
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT = _ROOT
S = _WORK

BOT    = os.path.join(ROOT, "nnue_train", "sploppy_repo", "target", "release", "bot")
ENGINE = os.path.join(ROOT, "sf19_ports", "sf19nn", "stockfish")     # has ClassLoss / ClassDamp
NET    = os.path.join(ROOT, "nnue_train", "sploppy_repo", "runs", "sf2_ex_50k", "best.nnue")
PY     = sys.executable

# ---- what is under test: one option, one value. The reference always leaves it at 0.
OPTION   = "ClassLoss"
VALUE    = 60
TAG      = "classloss60"

NODES         = 100000     # node-limited: deterministic, and immune to CPU contention
OPENING_PLIES = 24         # 8 makes the test powerless; see the docstring
PAIRS         = 32
THREADS       = 3          # in-flight pairs; one pair costs one core, leave the rest alone
SEED          = 20260924

OUTDIR = os.path.join(S, "ab_" + TAG)
STREAM = os.path.join(S, "ab_%s.jsonl" % TAG)


def make_seat(name, options):
    """A copy of the match seat with its own log directory and its own option setting."""
    src = open(os.path.join(ROOT, "nnue_train", "night", "ab_nodes_seat.py")).read()
    logdir = os.path.join(OUTDIR, name + "_logs")
    os.makedirs(logdir, exist_ok=True)
    for prefix, repl in (("ENGINE    =", "ENGINE    = %r" % ENGINE),
                         ("NET       =", "NET       = %r" % NET),
                         ("THREADS   =", "THREADS   = 1"),
                         ("FIXED_NODES =", "FIXED_NODES = %d" % NODES),
                         ("EXTRA_OPTIONS =", "EXTRA_OPTIONS = %r" % (options,)),
                         ("LOG_DIR   =", "LOG_DIR   = %r" % logdir),
                         ("NAME      =", "NAME      = %r" % ("SF19-NN-" + name))):
        src = "\n".join(repl if ln.startswith(prefix) else ln for ln in src.split("\n"))
    path = os.path.join(OUTDIR, name + "_seat.py")
    open(path, "w").write(src)
    return path


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    cand = make_seat("cand", [(OPTION, VALUE)])
    ref  = make_seat("ref",  [])

    cmd = [BOT, "eval",
           "--candidate", "rpsi:%s -u %s" % (PY, cand),
           "--reference", "rpsi:%s -u %s" % (PY, ref),
           "--candidate-network", NET,
           "--reference-network", NET,
           "--pairs", str(PAIRS),
           "--opening-plies", str(OPENING_PLIES),
           "--threads", str(THREADS),
           "--player-threads", "1",
           "--seed", str(SEED),
           "--stream"]

    print("candidate  %s = %s" % (OPTION, VALUE))
    print("reference  %s = 0" % OPTION)
    print("%d pairs, %d nodes/move, %d opening plies, %d in flight"
          % (PAIRS, NODES, OPENING_PLIES, THREADS))
    print("stream -> %s" % STREAM)
    print("seat logs -> %s/{cand,ref}_logs" % OUTDIR)
    print()
    sys.stdout.flush()

    t0 = time.time()
    with open(STREAM, "w") as out:
        rc = subprocess.run(cmd, cwd=os.path.join(ROOT, "nnue_train", "sploppy_repo"),
                            stdout=out, stderr=subprocess.STDOUT).returncode
    print("referee exited %d after %.0f min" % (rc, (time.time() - t0) / 60.0))

    # The referee's own tally, straight off the stream, so a crash still leaves a readable result.
    wins = draws = losses = 0
    for line in open(STREAM, errors="replace"):
        if '"event":"end"' not in line:
            continue
        import json
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        w = ev.get("winner")
        if w is None:
            draws += 1
        elif w == ev.get("game"):
            wins += 1
        else:
            losses += 1
    n = wins + draws + losses
    if n:
        print("candidate %d-%d-%d (W-D-L) over %d games, %.1f%% decisive"
              % (wins, draws, losses, n, 100.0 * (wins + losses) / n))
        print("Run the SPRT tally and the conversion census on this before reading anything into it.")


main()
