"""HANDOFF 231ct: the column that is 1.0 for every row -- per-game weighting.

`train.py` weights the value loss per row:

    weight = torch.from_numpy(rows["weight"]).float()
    value  = ((value_loss(a, ...) + value_loss(b, ...)) * 0.5 * weight).sum() / weight.sum()

and the importer writes 1.0 into every one of the 795,928 entries. So the column is a supported
lever that nobody has ever moved, exactly like `score_scale` was before 231cm.

What uniform weights actually buy, measured on n1_25k: rows per game run from 12 to 797, median
228, mean 248.7. A game therefore contributes gradient in proportion to how long it lasted, and
length is not neutral -- the long games are the ones that ran out the 200-ply capture clock and
ended in a draw. On sf1 that bias is stark (231cr): 39% of the games produce 54% of the rows.
The net is being asked, row by row, to be most accurate in the games that nobody won.

The arm: the same rows, the same labels, the same recipe, the same seed as the scale arm's
incumbent -- only `weight.npy` differs. Each GAME gets equal total weight,

    w_i = clip(mean_len / len(game of i), 1/CLIP, CLIP)   on the TRAIN split only

with the length taken over the game's whole row count, not just its training rows, so that a game
whose rows mostly went to validation is not turned into an outlier. Validation and test rows keep
weight 1.0 on purpose: the val objective then stays numerically comparable to every other arm's,
and the arm can be read both ways -- by the objective and by the match.

Control: `runs/scale_600/best.nnue`, the scale arm's incumbent, trained on exactly these rows
with exactly this recipe. If the scale arm did not get that far, this arm trains its own control
instead, so the comparison is never made against a differently-made net.

It waits for every registered job INCLUDING the leaderboard, so the night's synthesis (231cs)
happens first and this only ever uses time nothing else wanted. Screening, not shipping.
No sys.argv; constants below.
"""
import datetime
import json
import os
import shutil
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
OUT = os.path.join(S, "weight_arm.log")

SRC_SET = "selfplay_n1_sc600"        # the scale arm's incumbent set: n1_25k's rows at scale 600
ALT_SET = "selfplay_n1_25k"          # the same rows under their original name, if the above is absent
NEW_SET = "selfplay_n1_wg"
CTL_RUN = "scale_600"                # the control net, if the scale arm produced it
CLIP = 3.0                           # a game may count at most 3x, and at least 1/3, of an average game

HIDDEN, BATCH, EPOCHS, PASSES, LR = 512, 2048, 20, 4, 0.0001   # the scale arm's recipe, unchanged
SEED_TRAIN = 20260926
THREADS, NICE = 2, 15
EVAL_PAIRS, EVAL_SIMS, EVAL_SEED = 100, 8, 20260927            # the shared leaderboard settings
H2H_PAIRS, H2H_SEED = 300, 20260952                            # a seed no arm and no board used
WAIT_FOR = ("after_gates.py", "mix_train", "[0-9]_gates.py", "sweep_s1.py", "wdl_arm.py",
            "pv_arm.py", "v9_arm.py", "sf1_chain.py", "sf2_chain.py", "sf3_chain.py",
            "nodes_queue.py", "nodes_gate_", "node_scaling.py", "n1n_gate.py", "scale_arm.py",
            "teacher_arm.py", "leaderboard.py")



CODE = """
import json, numpy as np, os
src, dst, clip = __SRC__, __DST__, __CLIP__
g = np.load(os.path.join(src, "game.npy"))
sp = np.load(os.path.join(src, "split.npy"))
ids, counts = np.unique(g, return_counts=True)
length = np.empty(int(ids.max()) + 1, dtype="f8")
length[ids] = counts                       # the game's whole length, not just its training rows
raw = counts.mean() / length[g]
w = np.clip(raw, 1.0 / clip, clip).astype("f4")
w[sp != 0] = 1.0                           # validation and test stay comparable to every other arm
train = sp == 0
w[train] = w[train] / w[train].mean()       # mean 1 over the rows that are actually trained on
np.save(os.path.join(dst, "weight.npy"), w)
print("WEIGHTS rows {} train {} mean {:.4f} min {:.3f} max {:.3f} clipped_hi {:.2f}% clipped_lo {:.2f}%".format(
    len(w), int(train.sum()), float(w[train].mean()), float(w[train].min()), float(w[train].max()),
    100.0 * float((raw[train] > clip).mean()), 100.0 * float((raw[train] < 1.0 / clip).mean())))
prov = json.load(open(os.path.join(src, "provenance.json")))
prov["derived_from"] = {"set": os.path.basename(src), "rule": "arrays symlinked; weight.npy rebuilt",
                        "weighting": "per-game equal on the train split, clipped to [1/{c}, {c}]".format(c=clip),
                        "why": "231ct: the weight column was 1.0 everywhere, so long games counted most"}
json.dump(prov, open(os.path.join(dst, "provenance.json"), "w"), indent=1)
"""



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
    line = "%s WARM %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(OUT, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def rows_of(name):
    path = os.path.join(REPO, "runs", "nnue_data", name, "provenance.json")
    return json.load(open(path))["rows"] if os.path.exists(path) else None


def wait_its_turn():
    said = False
    while True:
        busy = [x for x in WAIT_FOR if alive(x)]
        if not busy:
            return
        if not said:
            say("waiting for %s" % ", ".join(busy))
            said = True
        time.sleep(300)


def build_set(src_name):
    """Every array symlinked from the source set; only weight.npy is new."""
    src = os.path.join(REPO, "runs", "nnue_data", src_name)
    dst = os.path.join(REPO, "runs", "nnue_data", NEW_SET)
    if rows_of(NEW_SET):
        say("%s already built, %d rows" % (NEW_SET, rows_of(NEW_SET)))
        return True
    os.makedirs(dst, exist_ok=True)
    for entry in sorted(os.listdir(src)):
        if entry in ("weight.npy", "provenance.json"):
            continue
        link = os.path.join(dst, entry)
        if not os.path.exists(link):
            os.symlink(os.path.join(src, entry), link)
    code = CODE.replace("__SRC__", repr(src)).replace("__DST__", repr(dst)).replace("__CLIP__", repr(CLIP))
    out = subprocess.run([VPY, "-c", code], cwd=REPO, capture_output=True, text=True)
    for line in (out.stdout + out.stderr).strip().splitlines():
        say(line[:200])
    return rows_of(NEW_SET) is not None


def train(run, set_name, steps):
    run_dir = os.path.join(REPO, "runs", run)
    if os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("%s: already trained" % run)
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
    say("%s: training %d x %d steps on %s" % (run, EPOCHS, steps, set_name))
    rc = subprocess.run(cmd, cwd=REPO, stdout=open(os.path.join(S, "weight_arm_%s.out" % run), "a"),
                        stderr=subprocess.STDOUT).returncode
    if rc != 0 or not os.path.exists(os.path.join(run_dir, "best.nnue")):
        say("%s: train rc %d -- no best.nnue" % (run, rc))
        return None
    return run


def best_objective(path):
    best = None
    for line in open(path, errors="replace") if os.path.exists(path) else []:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "objective" in d and (best is None or d["objective"] < best):
            best = d["objective"]
    return best


def match(name, cand, ref, pairs, seed):
    path = os.path.join(S, "weight_arm_%s.json" % name)
    if not _usable(path):     # a 0-byte OR half-written eval both count as no eval
        cmd = ["nice", "-n", str(NICE), BOT, "eval", "--candidate", cand, "--reference", ref,
               "--sims", str(EVAL_SIMS), "--pairs", str(pairs), "--threads", str(THREADS),
               "--seed", str(seed)]
        rc = subprocess.run(cmd, cwd=REPO, stdout=open(path + ".part", "w"),
                            stderr=open(os.path.join(S, "weight_arm.err"), "a")).returncode
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
    say("started pid %d" % os.getpid())
    wait_its_turn()
    src = SRC_SET if rows_of(SRC_SET) else (ALT_SET if rows_of(ALT_SET) else None)
    if not src:
        say("STOP: neither %s nor %s is imported" % (SRC_SET, ALT_SET))
        return
    say("source set %s, %d rows" % (src, rows_of(src)))
    if not build_set(src):
        say("STOP: could not build %s" % NEW_SET)
        return
    rows = rows_of(NEW_SET)
    steps = max(10, (rows * PASSES + BATCH * EPOCHS - 1) // (BATCH * EPOCHS))
    say("%d rows -> %d x %d steps, the scale arm's recipe exactly" % (rows, EPOCHS, steps))

    ctl = CTL_RUN if os.path.exists(os.path.join(REPO, "runs", CTL_RUN, "best.nnue")) else None
    if ctl:
        say("control: %s, the scale arm's incumbent (same rows, same recipe, same seed)" % ctl)
    else:
        say("control: the scale arm left none, so training one here on %s" % src)
        ctl = train("weight_flat", src, steps)
        if not ctl:
            say("STOP: no control net")
            return
    run = train("weight_game", NEW_SET, steps)
    if not run:
        say("STOP: the per-game arm did not train")
        return

    ev_new = match("wg", "nnue:runs/%s/best.nnue?hash=64" % run, "nnue:%s?hash=64" % INIT,
                   EVAL_PAIRS, EVAL_SEED)
    ctl_json = os.path.join(S, "weight_arm_ctl.json")
    if ctl == CTL_RUN and os.path.exists(os.path.join(S, "scale_arm_s600.json")):
        shutil.copyfile(os.path.join(S, "scale_arm_s600.json"), ctl_json)   # the same match, already played
        say("control's screening match reused from the scale arm (same candidate, same openings)")
    ev_ctl = match("ctl", "nnue:runs/%s/best.nnue?hash=64" % ctl, "nnue:%s?hash=64" % INIT,
                   EVAL_PAIRS, EVAL_SEED)

    say("TABLE  arm | val objective (val rows keep weight 1.0) | score vs the live net")
    say("TABLE  flat(%s) | %s | %s" % (ctl, best_objective(os.path.join(S, "scale_arm_600.out")),
                                       None if not ev_ctl else round(ev_ctl["score"], 3)))
    say("TABLE  per-game | %s | %s" % (best_objective(os.path.join(S, "weight_arm_weight_game.out")),
                                       None if not ev_new else round(ev_new["score"], 3)))

    h2h = match("h2h", "nnue:runs/%s/best.nnue?hash=64" % run,
                "nnue:runs/%s/best.nnue?hash=64" % ctl, H2H_PAIRS, H2H_SEED)
    if h2h:
        lo, hi = (h2h.get("bootstrap_interval") or [None, None])[:2]
        if lo is not None and lo > 0:
            say("RESULT per-game weighting is STRONGER than flat weighting over %d pairs" % H2H_PAIRS)
        elif hi is not None and hi < 0:
            say("RESULT flat weighting is STRONGER; per-game weighting hurt")
        else:
            say("RESULT too close to call over %d pairs: the weight column is not a free win" % H2H_PAIRS)
    say("DONE. Nothing shipped.")


main()
