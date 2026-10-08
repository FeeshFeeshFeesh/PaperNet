"""231ek wiring check: `bot analyse` reports network.value for a board fed as 81 cell codes.
The Python model reports six symmetry views of the same row. If the board is being fed in the
right frame, the engine's value must land on one of those six -- and on the SAME one every time.
If it lands on none, the instrument would be searching a position that is not the row's."""
import json, os, subprocess, sys
import numpy as np

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
PYDIR = os.path.join(REPO, "models", "nnue", "py")
BOT = os.path.join(REPO, "target", "release", "bot")
NET = os.path.join(REPO, "runs", "sf1_ex_50k", "best.nnue")
EVAL_SET = "selfplay_sf2_ex_50k_nopv"
N = 8

sys.path.insert(0, PYDIR)
import pathlib, torch
import nnue.data as data_mod
from nnue import export as export_mod, train as train_mod
from nnue.features import symmetry_table
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

torch.set_num_threads(1)
d = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", EVAL_SET),
                          split=data_mod.VALIDATION)
rows = d.all(N)
model = export_mod.load(pathlib.Path(NET)); model.eval()
table = torch.from_numpy(symmetry_table(model.layout))
ids = train_mod.encode(rows, model.layout)
with torch.no_grad():
    views = torch.stack([model(table[k, ids], qat=True).tanh() for k in range(6)], 1).numpy()

reqs = [{"id": i, "setup": [int(c) for c in np.asarray(rows["board"])[i]], "to_move": "blue",
         "psc": int(np.asarray(rows["since_capture"])[i]),
         "ply": int(np.asarray(rows["ply"])[i]), "moves": [], "sims": 0} for i in range(N)]
p = subprocess.run([BOT, "analyse", "--engine", "nnue:" + NET, "--threads", "1"],
                   input="\n".join(json.dumps(r) for r in reqs) + "\n",
                   capture_output=True, text=True)
print("rc", p.returncode, p.stderr.strip()[:200])
# The engine runs the QUANTISED net; Python runs simulated QAT in float. They agree to about
# the quantisation grid and no closer, so the test is "is the engine within TOL of view 0",
# not "is view 0 the argmin" -- two views can sit 1e-4 apart and the argmin then picks the
# wrong one for reasons that have nothing to do with the wiring. Getting this backwards is
# how the first run of this check reported a wiring fault that was not there.
TOL = 2e-3
ok, worst = True, 0.0
for line in p.stdout.splitlines():
    r = json.loads(line); i = r["id"]
    v = (r.get("network") or {}).get("value")
    if v is None:
        print("id=%d NO NETWORK HEAD err=%s" % (i, r.get("error"))); ok = False; continue
    d0 = abs(views[i][0] - v)
    others = float(np.abs(views[i][1:] - views[i][0]).min())
    worst = max(worst, d0)
    if d0 > TOL:
        ok = False
    print("id=%d engine %+.6f  view0 %+.6f  |d|=%.2e  %s   (nearest other view is %.2e away)"
          % (i, v, views[i][0], d0, "ok" if d0 <= TOL else "OUT OF TOLERANCE", others))
print()
print("WIRED CORRECTLY: every position matches view 0 (the identity), worst |d| = %.2e < %.0e"
      % (worst, TOL) if ok else "WIRING SUSPECT: worst |d| = %.2e" % worst)
