"""231ek: does sf2's +8.94% held-out value accuracy change what the SEARCH plays?

231eg found a 9% value-error gain worth -4.9 Elo over 600 games; 231ej killed the cheapest
explanation by showing the gain is not parked in positions a search would ignore. What is left is
to look at the decision itself. For each position this scores the root move each net's search
actually returns, plus the raw policy head's top move on the same position, which separates two
very different worlds:

  * the nets already agree on the move   -> a better value head cannot show up as Elo
  * the nets disagree but score the same -> the disagreements are between equal moves
  * the nets disagree and play different games of equal strength -> value error is the wrong ruler

Two facts about this engine, probed before writing any of this and both contradicting what 231eh
assumed when it recommended "visit-weighted error at the root's children":

  * the search is ALPHA-BETA, not MCTS. It returns one move with all 20,000 nodes on it, pi = 1.0
    and every other root line at zero visits. There is no visit distribution to weight anything by,
    so that half of 231eh's recommendation is not implementable and is withdrawn.
  * the policy head is EXACTLY UNIFORM (max prob 0.0196 on 51 legal moves, ratio to uniform 1.00).
    These are pure value nets; the prior carries no information. "Policy agreement" would be the
    argmax of a constant vector, so it is not measured here.

What the alpha-beta search does give, free, is the principal variation, so agreement is measured
at two depths: the move actually played, and how far the two PVs run together.

The engine is sploppy's own `nnue:` player, which is the engine h2h_s1_s2 played its 600 games in
(231eb: the SF19-NN port is a different engine and disagrees with it by about 60 Elo). Measuring
move agreement in a different engine from the one that produced the -4.9 Elo would answer a
question nobody asked.
"""
import json, os, pathlib, subprocess, sys, time
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

S = _WORK
T = _ROOT + "/nnue_train"
REPO = os.path.join(T, "sploppy_repo")
PYDIR = os.path.join(REPO, "models", "nnue", "py")
BOT = os.path.join(REPO, "target", "release", "bot")

NETS = [
    ("base(s1)", "runs/sf1_ex_50k/best.nnue", ["selfplay_sf1_ex_50k", "selfplay_n1_25k"]),
    ("sf2", "runs/sf2_ex_50k/best.nnue",
     ["selfplay_sf2_ex_50k", "selfplay_n1_25k", "selfplay_sf1_ex_50k"]),
]
EVAL_SET = "selfplay_sf2_ex_50k_nopv"
SIMS = 8                 # 8 x 2500 = 20,000 nodes, the number every screen and gate in this
                         # project uses, and the number h2h_s1_s2 played at
PER_GAME = 5             # positions per game, evenly spaced through it -- NOT a prefix
SEED = 20260960
BOOT = 2000
THREADS = 1              # the machine is already carrying a generator and a match
BUSY = ("nodes_gate_h2h_port.py", "h2h_s1_s2.py")   # wait these out before spending any CPU
OUT = os.path.join(S, "move_agree.log")
RESULT = os.path.join(S, "move_agree.json")


def say(msg, fh):
    line = "%s MOVEAGREE %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    fh.write(line + "\n"); fh.flush()


def wait_for_quiet(fh):
    told = False
    while True:
        busy = [p for p in BUSY
                if subprocess.run(["pgrep", "-f", p], capture_output=True).returncode == 0]
        if not busy:
            return
        if not told:
            say("waiting for %s to finish before taking any CPU" % ", ".join(busy), fh)
            told = True
        time.sleep(60)


def clean_rows(data_mod, fh):
    """Every row of the eval set whose orbit appears in NO net's training data. Same rule as
    231dl's offline_score.py, so this lands on the same 20,955 rows and the two instruments are
    talking about the same positions."""
    import numpy as np
    d = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", EVAL_SET),
                              split=data_mod.VALIDATION)
    rows = d.all(None)
    seen = set()
    for _, _, mixture in NETS:
        for name in mixture:
            train = data_mod.Dataset.open(os.path.join(REPO, "runs", "nnue_data", name),
                                          split=data_mod.TRAIN)
            seen.update(int(o) for o in np.asarray(train.all(None)["orbit"]))
    orbit = np.asarray(rows["orbit"])
    keep = np.array([int(o) not in seen for o in orbit])
    say("%d of %d eval rows are clean for every net" % (int(keep.sum()), len(orbit)), fh)
    return {k: np.asarray(v)[keep] for k, v in rows.items()}


def sample(rows, fh):
    """PER_GAME positions from each game, evenly spaced through the game. A prefix would take one
    opening repeated; even spacing takes the opening, the middle and the end of every game."""
    import numpy as np
    game = rows["game"]
    picks = []
    for g in np.unique(game):
        idx = np.flatnonzero(game == g)
        if len(idx) <= PER_GAME:
            picks.extend(idx.tolist())
        else:
            picks.extend(idx[np.linspace(0, len(idx) - 1, PER_GAME).round().astype(int)].tolist())
    picks = np.array(sorted(set(picks)))
    say("sampled %d positions from %d games (%d per game, evenly spaced)"
        % (len(picks), len(np.unique(game)), PER_GAME), fh)
    return picks


def analyse(label, net, rows, picks, fh):
    """One persistent `bot analyse` per net: the net is loaded once, not once per position."""
    import numpy as np
    reqs = []
    for n, i in enumerate(picks):
        reqs.append({"id": n, "setup": [int(c) for c in rows["board"][i]], "to_move": "blue",
                     "psc": int(rows["since_capture"][i]), "ply": int(rows["ply"][i]),
                     "moves": [], "sims": SIMS})
    t0 = time.time()
    p = subprocess.run([BOT, "analyse", "--engine", "nnue:" + os.path.join(REPO, net),
                        "--threads", str(THREADS)],
                       input="\n".join(json.dumps(r) for r in reqs) + "\n",
                       capture_output=True, text=True)
    if p.returncode != 0:
        say("%s FAILED rc %d: %s" % (label, p.returncode, p.stderr.strip()[:300]), fh)
        raise SystemExit(1)
    out = {}
    for line in p.stdout.splitlines():
        r = json.loads(line)
        if r.get("error"):
            say("%s position %s: %s" % (label, r["id"], r["error"]), fh); continue
        srch = r.get("search") or {}
        lines = srch.get("lines", [])
        best = max(lines, key=lambda l: l["visits"], default=None)
        pol = list(((r.get("network") or {}).get("policy") or {}).values())
        flat = (max(pol) * len(pol) if pol else 0.0)   # 1.00 means an exactly uniform prior
        out[r["id"]] = {
            "move": best["move"] if best else None,
            "root_value": srch.get("root_value"),
            "q": best["q"] if best else None,
            "pv": best["pv"] if best else [],
            "net_value": (r.get("network") or {}).get("value"),
            "policy_peak_over_uniform": flat,
        }
    say("%s analysed %d positions in %.0fs" % (label, len(out), time.time() - t0), fh)
    return out


def main():
    sys.path.insert(0, PYDIR)
    import numpy as np
    import nnue.data as data_mod

    fh = open(OUT, "a")
    say("start: %s vs %s at %d nodes" % (NETS[0][0], NETS[1][0], SIMS * 2500), fh)
    wait_for_quiet(fh)
    rows = clean_rows(data_mod, fh)
    picks = sample(rows, fh)
    res = {label: analyse(label, net, rows, picks, fh) for label, net, _ in NETS}

    a, b = res[NETS[0][0]], res[NETS[1][0]]
    ids = sorted(set(a) & set(b))
    game = np.array([int(rows["game"][picks[i]]) for i in ids])
    same = np.array([1.0 if a[i]["move"] == b[i]["move"] else 0.0 for i in ids])

    def shared(x, y):
        """How many leading plies the two principal variations have in common."""
        n = 0
        for u, v in zip(x, y):
            if u != v:
                break
            n += 1
        return n
    depth = np.array([float(shared(a[i]["pv"], b[i]["pv"])) for i in ids])
    peak = max(max(a[i]["policy_peak_over_uniform"] for i in ids),
               max(b[i]["policy_peak_over_uniform"] for i in ids))

    # Bootstrap BY GAME, for the reason 231eg had to learn the hard way: positions inside one
    # game are the same game advanced a few plies and are nowhere near independent.
    rng = np.random.default_rng(SEED)
    games = np.unique(game)
    where = {g: np.flatnonzero(game == g) for g in games}
    def interval(stat):
        draws = []
        for _ in range(BOOT):
            pick = np.concatenate([where[g] for g in rng.choice(games, len(games), replace=True)])
            draws.append(float(stat[pick].mean()))
        return [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]

    search_lo, search_hi = interval(same)
    depth_lo, depth_hi = interval(depth)
    say("", fh)
    say("MOVE agreement      %.4f [%.4f, %.4f] over %d positions in %d games"
        % (same.mean(), search_lo, search_hi, len(ids), len(games)), fh)
    say("shared PV plies     %.3f [%.3f, %.3f] (0 means they part at the move itself)"
        % (depth.mean(), depth_lo, depth_hi), fh)
    say("policy prior peak / uniform: %.3f -- 1.000 is an exactly uniform prior, i.e. no policy "
        "head at all, which is why policy agreement is not reported" % peak, fh)
    disagree = [i for i in ids if a[i]["move"] != b[i]["move"]]
    if disagree:
        gap = np.array([abs((a[i]["q"] or 0) - (b[i]["q"] or 0)) for i in disagree])
        say("on the %d disagreements, |q(a) - q(b)| at the chosen move: mean %.4f, median %.4f"
            % (len(disagree), float(gap.mean()), float(np.median(gap))), fh)
    json.dump({"search_agreement": float(same.mean()),
               "search_interval": [search_lo, search_hi],
               "pv_shared_plies": float(depth.mean()), "pv_interval": [depth_lo, depth_hi],
               "policy_peak_over_uniform": float(peak),
               "positions": len(ids), "games": int(len(games)), "sims": SIMS,
               "per_game": PER_GAME, "seed": SEED, "boot": BOOT},
              open(RESULT, "w"), indent=1)
    say("wrote %s" % RESULT, fh)


main()
