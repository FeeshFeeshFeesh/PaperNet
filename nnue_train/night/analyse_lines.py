"""Two things 231ek depends on and neither is safe to assume:
  1. is `visits` discriminative at the root, or does every line report the same number?
  2. is the policy head informative, or is it flat -- in which case 'policy agreement' is the
     argmax of noise and must not be reported as a measurement."""
import json, os, subprocess, sys
import numpy as np
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))
import nnue.data as data_mod
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
d = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", "selfplay_sf2_ex_50k_nopv"),
                          split=data_mod.VALIDATION)
rows = d.all(2)
reqs = [{"id": i, "setup": [int(c) for c in np.asarray(rows["board"])[i]], "to_move": "blue",
         "psc": int(np.asarray(rows["since_capture"])[i]),
         "ply": int(np.asarray(rows["ply"])[i]), "moves": [], "sims": 8} for i in range(2)]
p = subprocess.run([os.path.join(REPO, "target", "release", "bot"), "analyse",
                    "--engine", "nnue:" + os.path.join(REPO, "runs/sf1_ex_50k/best.nnue"),
                    "--threads", "1"],
                   input="\n".join(json.dumps(r) for r in reqs) + "\n",
                   capture_output=True, text=True)
for line in p.stdout.splitlines():
    r = json.loads(line)
    lines = (r.get("search") or {})["lines"]
    v = np.array([l["visits"] for l in lines]); pi = np.array([l["pi"] for l in lines])
    pol = np.array(list((r["network"]["policy"]).values()))
    print("id=%s  %d root lines" % (r["id"], len(lines)))
    print("  visits: n_distinct=%d  max=%d  sum=%d  top5=%s"
          % (len(set(v.tolist())), v.max(), v.sum(), sorted(v.tolist(), reverse=True)[:5]))
    print("  pi    : max=%.4f  sum=%.3f  top5=%s"
          % (pi.max(), pi.sum(), [round(x, 4) for x in sorted(pi.tolist(), reverse=True)[:5]]))
    print("  policy: max=%.4f  uniform would be %.4f  ratio %.2f  sum=%.3f"
          % (pol.max(), 1.0 / len(pol), pol.max() * len(pol), pol.sum()))
    top = sorted(lines, key=lambda l: l["visits"], reverse=True)[:3]
    for l in top:
        print("    %-8s visits=%-6d q=%+.4f pi=%.4f pv=%s"
              % (l["move"], l["visits"], l["q"], l["pi"], " ".join(l["pv"][:4])))
