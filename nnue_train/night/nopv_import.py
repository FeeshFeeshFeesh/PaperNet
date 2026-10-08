"""The control dataset 231dk asks for: the SAME 1600 sf2 games, imported with pv_rows = 0.

231dk cannot separate three things the sf2 run changes at once -- more rows, a new KIND of row
(principal-variation positions at half weight), and more training steps. This import makes the
separation possible later: `selfplay_sf2_ex_50k_nopv` is the identical games with the roots only,
so a future run can train on n1 + sf1 + nopv and be compared row-for-row against the pv version.

It imports; it does not train and does not evaluate. Cheap (the pv import of the same games took
73 seconds) and it leaves a dataset, not a claim."""
import datetime, os, subprocess
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
VPY = os.path.join(T, ".venv", "bin", "python")
S = _WORK
LOG = os.path.join(S, "nopv_import.log")
RECORDS, OUT, MIN_PLY, NICE = "sf2_ex_50k", "selfplay_sf2_ex_50k_nopv", 16, 19


def say(text):
    with open(LOG, "a") as fh:
        fh.write("%s NOPV %s\n" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text))


target = os.path.join(REPO, "runs", "nnue_data", OUT)
if os.path.exists(os.path.join(target, "provenance.json")):
    say("already imported, nothing to do: %s" % OUT)
    raise SystemExit(0)

cmd = ["nice", "-n", str(NICE), VPY, "-u", "-m", "nnue.importer", "selfplay",
       "--records", os.path.join("runs", "nnue_selfplay", RECORDS),
       "--out", OUT, "--min-ply", str(MIN_PLY)]
say("import start: %s -> %s (no --pv-rows, so roots only)" % (RECORDS, OUT))
rc = subprocess.run(cmd, cwd=REPO, stdout=open(LOG, "a"), stderr=subprocess.STDOUT).returncode
say("import exited rc %d" % rc)
if rc == 0:
    import json
    p = json.load(open(os.path.join(target, "provenance.json")))
    say("DONE %s has %s rows; counts %s" % (OUT, p.get("rows"), p.get("counts")))
    say("it is a dataset, not a result: nothing has trained on it")
