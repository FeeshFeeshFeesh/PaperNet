"""HANDOFF 231cm: the label's squashing constant -- 600 was never chosen, it was inherited.

Every row this tree has ever trained on is `target = tanh(root_score / 600)`, where the score is
in net units (208 = one stone). Nobody picked 600: it is the importer's default, read from each
records manifest. It decides how the search's opinion is compressed into [-1, 1], and therefore
how much of the training signal goes to close positions versus won ones. Measured on
`selfplay_n1_25k` (795,928 rows):

    mean |target| 0.461, median 0.398
    |t| > 0.5  45.3%   |t| > 0.9  23.6%   |t| > 0.95  18.6%   |t| > 0.99  6.7%   |t| = 1  2.8%
    mean (1 - t^2) = 0.640          (the share of the tanh gradient that survives)

That is not broken -- the loss pairs the squashed MSE with a bounded logit term whose target is
clamped to +/-0.98, so even mate rows (2.8% at exactly +/-1) pull towards a finite value. But a
quarter of the rows sit where tanh is nearly flat, and their differences barely reach the
weights. A smaller scale sharpens the focus on close positions and throws away more of the
difference between "won" and "winning"; a larger one spends capacity ranking positions that are
already decided. There is no reason to believe 600 is the best point on that curve for THIS game,
where a stone is 208 units and games are decided by goal races, not by material.

The arm: the SAME records, the SAME rows, the SAME split -- only the squashing constant changes.
A scaled set is built by symlinking n1_25k's shards into a new records dir whose manifest carries
a different `score_scale` (the importer reads the scale from the manifest, so this costs no disk
and no re-generation), importing with identical flags, and training an identical recipe from
`example.nnue` with one seed. Four nets, one number different.

READ THE EVAL SCORE, NOT THE OBJECTIVE. Each arm's validation MSE is measured against ITS OWN
targets, so the objectives are four different quantities and cannot be compared. Only play can
rank them: each net plays `example.nnue` on the same openings, and the best non-600 arm then
plays the 600 arm directly, which is the only number that answers the question.

No sys.argv; constants below.
"""
import datetime
import json
import os
import subprocess
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
VPY = os.path.join(T, ".venv", "bin", "python")
BOT = os.path.join(REPO, "target", "release", "bot")
INIT = "models/nnue/examples/example.nnue"
SRC_RECORDS = "n1_25k"                 # 3200 games, the only set that exists tonight for certain
SCALES = [400, 600, 700, 1100]         # 600 the incumbent (rebuilt, so every arm is identical work),
                                       # 700 the outcome-calibrated optimum, 400 and 1100 the two directions
MIN_PLY, IMPORT_SEED = 16, 20260909    # the defaults the incumbent set was built with
HIDDEN, BATCH, EPOCHS, PASSES, LR = 512, 2048, 20, 4, 0.0001
SEED_TRAIN = 20260926
THREADS, NICE = 2, 15
EVAL_PAIRS, EVAL_SIMS, EVAL_SEED = 100, 8, 20260927
H2H_PAIRS, H2H_SEED = 300, 20260946
POLL_S = 120
OUT = os.path.join(S, "scale_arm.log")
WAIT_FOR = ("after_gates.py", "mix_train", "[0-9]_gates.py", "sweep_s1.py", "wdl_arm.py",
            "pv_arm.py", "v9_arm.py", "nodes_queue.py", "nodes_gate_", "node_scaling.py",
            "n1n_gate.py")



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
    line = "%s SCALE %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def wait_its_turn():
    said = False
    while True:
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(POLL_S)


def rows_of(name):
    path = os.path.join(REPO, "runs", "nnue_data", name, "provenance.json")
    return json.load(open(path))["rows"] if os.path.exists(path) else None


def records_for(scale):
    """A records dir that is n1_25k's shards by symlink, with one number changed in the manifest."""
    name = "%s_sc%d" % (SRC_RECORDS, scale)
    src = os.path.join(REPO, "runs", "nnue_selfplay", SRC_RECORDS)
    dst = os.path.join(REPO, "runs", "nnue_selfplay", name)
    man = json.load(open(os.path.join(src, "manifest.json")))
    os.makedirs(dst, exist_ok=True)
    for shard in man["shards"]:
        link = os.path.join(dst, shard["file"])
        if not os.path.exists(link):
            os.symlink(os.path.join(src, shard["file"]), link)
    man["score_scale"] = scale
    man["derived_from"] = {"records": SRC_RECORDS, "rule": "same shards by symlink",
                           "why": "231cm: only the label's squashing constant changes"}
    json.dump(man, open(os.path.join(dst, "manifest.json"), "w"), indent=1)
    return name


def imported(scale):
    set_name = "selfplay_n1_sc%d" % scale
    if rows_of(set_name):
        say("scale %d: %s already imported, %d rows" % (scale, set_name, rows_of(set_name)))
        return set_name
    records = records_for(scale)
    cmd = [VPY, "-u", "-m", "nnue.importer", "selfplay",
           "--records", os.path.join("runs", "nnue_selfplay", records),
           "--out", set_name, "--min-ply", str(MIN_PLY), "--seed", str(IMPORT_SEED)]
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(os.path.join(S, "scale_arm.import"), "a"),
                        stderr=subprocess.STDOUT).returncode
    if rc != 0 or not rows_of(set_name):
        say("scale %d: import rc %d" % (scale, rc))
        return None
    rc = subprocess.run([VPY, "-u", "-m", "nnue.data", "encode", set_name], cwd=REPO,
                        stdout=open(os.path.join(S, "scale_arm.import"), "a"),
                        stderr=subprocess.STDOUT).returncode
    if rc != 0:
        say("scale %d: encode rc %d" % (scale, rc))
        return None
    say("scale %d: %s, %d rows" % (scale, set_name, rows_of(set_name)))
    return set_name


def saturation(set_name):
    """How much of the tanh gradient survives at this scale -- the reason the arm exists."""
    code = ("import numpy as np,os;t=np.load(os.path.join('runs','nnue_data','%s','target.npy'))"
            ";a=np.abs(t);print('%%.4f %%.4f %%.2f %%.2f'%%(a.mean(),(1-t.astype('f8')**2).mean(),"
            "100*(a>0.95).mean(),100*(a>0.99).mean()))" % set_name)
    out = subprocess.run([VPY, "-c", code], cwd=REPO, capture_output=True, text=True)
    return out.stdout.strip() or "?"


def train(scale, set_name, steps):
    run = "scale_%d" % scale
    run_dir = os.path.join(REPO, "runs", run)
    if os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("scale %d: already trained" % scale)
        return run
    warmup = min(200, max(1, steps * EPOCHS // 10))
    cmd = ["nice", "-n", str(NICE), VPY, "-u", "-m", "nnue.train", "--run", run, "--init", INIT,
           "--hidden", str(HIDDEN), "--version", "8", "--batch", str(BATCH),
           "--steps_per_epoch", str(steps), "--epochs", str(EPOCHS), "--lr", str(LR),
           "--weight_decay", "1e-5", "--warmup_steps", str(warmup), "--lr_floor", "0.15",
           "--qat_start_epoch", "1", "--raw_weight", "0.02", "--symmetry_weight", "0.2",
           "--grad_clip", "5", "--patience", str(EPOCHS), "--val_rows", "8192",
           "--threads", str(THREADS), "--device", "cpu", "--seed", str(SEED_TRAIN),
           "--data", "%s:1.0" % set_name]
    say("scale %d: training %d x %d steps on %s" % (scale, EPOCHS, steps, set_name))
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(os.path.join(S, "scale_arm_%d.out" % scale), "a"),
                        stderr=subprocess.STDOUT).returncode
    if rc != 0 or not os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("scale %d: train rc %d -- no best.nnue" % (scale, rc))
        return None
    return run


def match(name, cand, ref, pairs, seed):
    path = os.path.join(S, "scale_arm_%s.json" % name)
    if not _usable(path):     # a 0-byte OR half-written eval both count as no eval
        cmd = ["nice", "-n", str(NICE), BOT, "eval", "--candidate", cand, "--reference", ref,
               "--sims", str(EVAL_SIMS), "--pairs", str(pairs), "--threads", str(THREADS),
               "--seed", str(seed)]
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                            stderr=open(os.path.join(S, "scale_arm.err"), "a")).returncode
        if rc == 0:
            os.replace(path + ".part", path)  # never leave a half-written eval that looks cached
        if rc != 0:
            say("%s: eval rc %d" % (name, rc))
            return None
    ev = json.load(open(path))
    games = ev["wins"] + ev["draws"] + ev["losses"]
    ev["score"] = (ev["wins"] + 0.5 * ev["draws"]) / games if games else None
    say("%s: score %.3f over %d games (W/D/L %d/%d/%d), bootstrap %s"
        % (name, ev["score"], games, ev["wins"], ev["draws"], ev["losses"], ev.get("bootstrap_interval")))
    return ev


def main():
    say("started pid %d: scales %s on %s" % (os.getpid(), SCALES, SRC_RECORDS))
    wait_its_turn()
    if not os.path.exists(os.path.join(REPO, "runs", "nnue_selfplay", SRC_RECORDS, "manifest.json")):
        say("STOP: %s has no manifest" % SRC_RECORDS)
        return
    sets = {}
    for scale in SCALES:
        name = imported(scale)
        if not name:
            say("STOP: could not build the set for scale %d" % scale)
            return
        sets[scale] = name
        say("scale %d: mean|t| / mean(1-t^2) / %%>0.95 / %%>0.99 = %s" % (scale, saturation(name)))
    rows = min(rows_of(n) for n in sets.values())
    steps = max(10, (rows * PASSES + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))
    say("%d rows a set -> every arm trains %d x %d steps" % (rows, EPOCHS, steps))
    results = []
    for scale in SCALES:
        run = train(scale, sets[scale], steps)
        if not run:
            continue
        ev = match("s%d" % scale, "nnue:runs/%s/best.nnue?hash=64" % run, "nnue:%s?hash=64" % INIT,
                   EVAL_PAIRS, EVAL_SEED)
        results.append((scale, run, ev["score"] if ev else None))
    say("TABLE  scale | score vs example.nnue (same openings, same seed, %d pairs)" % EVAL_PAIRS)
    for scale, _, score in results:
        say("TABLE  %d | %s" % (scale, "%.3f" % score if score is not None else "?"))
    rest = [(sc, r, s) for sc, r, s in results if sc != 600 and s is not None]
    base = [(sc, r, s) for sc, r, s in results if sc == 600 and s is not None]
    if rest and base:
        best = max(rest, key=lambda x: x[2])
        say("head to head: scale %d vs the incumbent 600, %d pairs" % (best[0], H2H_PAIRS))
        match("h2h_%d_600" % best[0], "nnue:runs/%s/best.nnue?hash=64" % best[1],
              "nnue:runs/%s/best.nnue?hash=64" % base[0][1], H2H_PAIRS, H2H_SEED)
    say("READ: the eval scores rank the arms; the objectives do NOT (each is measured against its "
        "own targets). The head to head is the only number with enough pairs to act on.")
    say("DONE. Nothing shipped")


main()
