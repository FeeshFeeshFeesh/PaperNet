"""Dense-input sparsity of sploppy's net on real game positions (every 6th ply of the SF19 match)."""
import json, subprocess, collections
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
S = _WORK
NET = _ROOT + "/project_files/sploppy_engine/example.nnue"
games = collections.defaultdict(list)
for l in open(S + "/sf19_eval.jsonl"):
    r = json.loads(l)
    if r.get("event") == "start":
        games[(r["pair"], r["game"])] = [m.replace("-", "") for m in r["opening"]]
    elif r.get("event") == "move":
        games[(r["pair"], r["game"])].append(r["move"].replace("-", ""))
cmds = ["setoption name EvalFile value " + NET]
for mv in games.values():
    for k in range(8, len(mv), 6):
        cmds += ["position startpos moves " + " ".join(mv[:k]), "eval"]
cmds.append("quit")
out = subprocess.run([S + "/sf19nn/stockfish"], input="\n".join(cmds) + "\n", capture_output=True, text=True).stdout
z = [int(x.split()[4]) for x in out.splitlines() if x.startswith("Net dense inputs zero")]
tot = int([x for x in out.splitlines() if x.startswith("Net dense inputs zero")][0].split()[-1])
z.sort()
print("positions", len(z), "of", tot, "inputs: zero fraction mean %.3f  p10 %.3f  median %.3f  p90 %.3f"
      % (sum(z) / len(z) / tot, z[len(z) // 10] / tot, z[len(z) // 2] / tot, z[9 * len(z) // 10] / tot))
