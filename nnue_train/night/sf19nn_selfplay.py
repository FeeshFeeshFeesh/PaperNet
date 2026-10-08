"""Self-play training data from SF19-NN, written as sploppy's `bot selfplay` records.

SF19-NN (our Stockfish 19 rps port with sploppy's net, GPL, run as a separate process) plays itself
at a fixed node budget. Every searched root becomes a labelled position in the record format that
sploppy's importer reads (`python -m nnue.importer selfplay --records <OUT>`, then `nnue.data encode`
and `nnue.train`, as in models/nnue/quickstart.sh). SF19-NN searches ~3x more nodes per second than
sploppy's engine on this machine, so the same hours give either ~3x the positions or deeper labels.

Game plan per game (the same shape as `bot selfplay --opening-plies 8 --random-moves 2 --random-from 8
--random-to 40`): 8 uniformly random plies, then the engine plays, except at 2 random plies in 8..39
where a random legal move is played. Plies with a random move have no root. The outcome counts for
roots after the last random ply (outcome_after_ply). The game rules come from sploppy's `engine`
module (apply / legal_mask), not from the searcher.

Labels: root_score = the final UCI score of the search in net units. The port prints
cp = round(100 * v / 208) (StoneValue 208), so v = cp * 208 / 100; a mate is +-28000.
sploppy's importer uses tanh(root_score / 600).

Resumable: every finished game is appended to parts/w<k>.jsonl; a restart skips the game ids already
there. When all games exist the parts are packed into gzip shards plus manifest.json ("complete").

No sys.argv: edit the constants and run with nnue_train/.venv/bin/python (it has the engine module).
"""
import datetime
import gzip
import hashlib
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from multiprocessing import Process

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "sploppy_repo")
sys.path.insert(0, os.path.join(REPO, "models", "nnue", "py"))

ENGINE = _ROOT + "/sf19_ports/live/stockfish"
NET = os.path.join(REPO, "models", "nnue", "examples", "example.nnue")
NAME = "sf1_ex_50k"
GAMES = 1600                # measured 220 games/h at 50k nodes: 3200 would be 14 h of awake time
NODES = 50000
WORKERS = 6                 # one engine process (1 thread) per worker
HASH_MB = 64
SEED = 20260925             # change for every batch: games are deterministic given the seed and the net
OPENING_PLIES = 8
RANDOM_MOVES = 2
RANDOM_FROM = 8
RANDOM_TO = 40              # exclusive
MAX_PLIES = 1024
CAPTURE_CLOCK = 200
PV_PLIES    = 8            # line positions kept per root, for nnue.importer --pv-rows
SHARD_GAMES = 256
NICE = 10
STONE_VALUE = 208           # the port's UCI cp scale: cp = 100 * v / STONE_VALUE
MATE_SCORE = 28000
OUT = os.path.join(REPO, "runs", "nnue_selfplay", NAME)
LOG = os.path.join(HERE, "logs", NAME + ".log")

DIRS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(cfg, text):
    with open(cfg["log"], "a") as fh:
        fh.write("%s %s\n" % (stamp(), text))


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def sq_name(s):
    return "abcdefghi"[s % 9] + str(s // 9 + 1)


def token_of(action, red_to_move, anti):
    """The absolute move token of a canonical action (the inverse of nnue.games.parse_action)."""
    start = action % 81
    dr, df = DIRS[action // 81]
    end = start + 9 * dr + df
    if red_to_move:
        start, end = int(anti[start]), int(anti[end])
    return sq_name(start) + "-" + sq_name(end)


def initial_fen(board):
    """The UCI FEN of the initial position. Blue's home is a1 there, which is already the port's frame
    (white = Blue, home a1, goal i9), so no transform is needed; checked by the caller."""
    rows = []
    for rank in range(8, -1, -1):
        out, run = "", 0
        for f in range(9):
            code = board[rank * 9 + f]
            if code == 0:
                run += 1
                continue
            if run:
                out += str(run)
                run = 0
            out += "RPS"[code - 1] if code <= 3 else "rps"[code - 4]
        rows.append(out + (str(run) if run else ""))
    return "/".join(rows) + " w - - 0 1"


class Searcher:
    def __init__(self, cfg):
        self.cfg = cfg
        self.p = subprocess.Popen([cfg["engine"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.send("uci")
        self.wait("uciok")
        for name, value in (("UCI_Variant", "rps-v6"), ("Threads", "1"), ("Hash", str(cfg["hash_mb"])),
                            ("EvalFile", cfg["net"]), ("NetScale", "100")):
            self.send("setoption name %s value %s" % (name, value))
        self.send("isready")
        _, seen = self.wait("readyok")
        if not any("RPSNNUE1" in x for x in seen):
            raise RuntimeError("net did not load: %s" % seen[-5:])

    def send(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def wait(self, prefix):
        seen = []
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("engine exited")
            line = line.strip()
            if line.startswith(prefix):
                return line, seen
            seen.append(line)

    def new_game(self):
        self.send("ucinewgame")
        self.send("isready")
        self.wait("readyok")

    def search(self, fen, uci_moves):
        self.send("position fen " + fen + (" moves " + " ".join(uci_moves) if uci_moves else ""))
        self.send("go nodes %d" % self.cfg["nodes"])
        line, seen = self.wait("bestmove")
        infos = [x.split() for x in seen if x.startswith("info depth") and " score " in x]
        exact = [x for x in infos if "lowerbound" not in x and "upperbound" not in x]
        last = (exact or infos or [[]])[-1]
        words = line.split()
        best = words[1] if len(words) > 1 else "(none)"

        def field(key):
            return last[last.index(key) + 1] if key in last else None

        if field("mate") is not None:
            score = MATE_SCORE if int(field("mate")) > 0 else -MATE_SCORE
        elif field("cp") is not None:
            score = int(round(int(field("cp")) * self.cfg["stone_value"] / 100.0))
            score = max(-MATE_SCORE, min(MATE_SCORE, score))
        else:
            score = None
        depth = field("depth")
        nodes = field("nodes")
        # the line the score belongs to; the importer can make a row of each position on it
        pv = last[last.index("pv") + 1:] if "pv" in last else []
        return best, score, (int(depth) if depth else None), (int(nodes) if nodes else None), pv

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def play_game(cfg, searcher, game_id, engine, anti):
    from nnue.games import parse_action
    game_seed = (cfg["seed"] * 1_000_003 + game_id * 7919) & ((1 << 63) - 1)
    rng = random.Random(game_seed)
    initial = [int(x) for x in engine.initial_board()]
    fen = initial_fen(initial)
    plan = sorted(rng.sample(range(cfg["random_from"], cfg["random_to"]), cfg["random_moves"]))
    board, since, ply, outcome = list(initial), 0, 0, 0
    moves, uci, roots, random_plies = [], [], [], []
    last_action = None
    searcher.new_game()
    while ply < cfg["max_plies"]:
        mask = engine.legal_mask(board)
        legal = [a for a in range(648) if mask[a]]
        if not legal:
            raise RuntimeError("game %d ply %d: no legal move but the game is not over" % (game_id, ply))
        red = ply % 2 == 1
        if ply < cfg["opening_plies"] or ply in plan:
            action = rng.choice(legal)
            random_plies.append(ply)
        else:
            t0 = time.monotonic_ns()
            best, score, depth, nodes, pv = searcher.search(fen, uci)
            if len(best) != 4:
                raise RuntimeError("game %d ply %d: engine gave %r" % (game_id, ply, best))
            token = best[:2] + "-" + best[2:]
            action = parse_action(token, red_to_move=red)
            if not (0 <= action < 648) or not mask[action]:
                raise RuntimeError("game %d ply %d: illegal engine move %s" % (game_id, ply, best))
            roots.append({"ply": ply, "mover": ply % 2, "since_capture": since, "capture_clock": cfg["clock"],
                          "searched_best": action, "played_action": action, "root_score": score,
                          "score_kind": "search" if score is not None else None,
                          "completed_depth": depth, "nodes": nodes, "elapsed_ns": time.monotonic_ns() - t0})
            # The line as canonical actions, the side to move alternating from this root.
            # nnue.importer --pv-rows K walks it from the root board, so it has to start
            # with the move that was searched, and one bad id would cost the whole line.
            line, side = [], red
            for step in pv[:cfg["pv_plies"]]:
                if len(step) != 4:
                    break
                line.append(parse_action(step[:2] + "-" + step[2:], red_to_move=side))
                side = not side
            if line:
                roots[-1]["pv"] = line
        token = token_of(action, red, anti)
        moves.append(token)
        uci.append(token.replace("-", ""))
        last_action = action
        child, since, ply, outcome = engine.apply(board, since, ply, action, cfg["clock"])
        board = [int(x) for x in child]
        if outcome != 0:
            break
    record = {"schema": 3, "producer": "sf19nn_selfplay.py", "first": cfg["player"], "second": cfg["player"],
              "plies": len(moves), "moves": moves, "random_plies": random_plies, "game_id": game_id,
              "seed": game_seed, "capture_clock": cfg["clock"], "roots": roots,
              "opening_plies": cfg["opening_plies"], "random_plan": plan, "random_skipped": [],
              "initial": {"board": initial, "ply": 0, "mover": 0, "since_capture": 0, "blue_home": 0},
              "outcome_after_ply": (random_plies[-1] + 1) if random_plies else 0}
    if outcome == 0:
        record.update(winner=None, end="MaxPlies", censored=True, outcome=None)
    elif outcome == 2:
        record.update(winner=None, end="CaptureClock", censored=False, outcome=0)
    else:
        winner = (ply - 1) % 2
        goal = last_action % 81 + 9 * DIRS[last_action // 81][0] + DIRS[last_action // 81][1] == 80
        record.update(winner=winner, end="Goal" if goal else "NoMoves", censored=False,
                      outcome=1 if winner == 0 else -1)
    return record


def done_ids(path):
    ids = set()
    if os.path.exists(path):
        good = 0
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    ids.add(json.loads(raw)["game_id"])
                    good += len(raw)
                except (ValueError, KeyError):
                    break
        if good != os.path.getsize(path):  # a torn last line from a kill: cut it off
            with open(path, "rb+") as fh:
                fh.truncate(good)
    return ids


def worker(cfg, k, have):
    """Plays the game ids that are k modulo the worker count and not yet in any part file."""
    import engine
    from nnue import features
    anti = features.ANTI
    part = os.path.join(cfg["out"], "parts", "w%d.jsonl" % k)
    todo = [g for g in range(cfg["games"]) if g % cfg["workers"] == k and g not in have]
    searcher = Searcher(cfg)
    failures = 0
    for g in todo:
        try:
            record = play_game(cfg, searcher, g, engine, anti)
        except Exception as e:
            failures += 1
            log(cfg, "worker %d game %d FAILED (%s); engine restarted" % (k, g, e))
            searcher.close()
            if failures > 20:
                log(cfg, "worker %d gave up after %d failures" % (k, failures))
                return
            searcher = Searcher(cfg)
            continue
        with open(part, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    searcher.close()


def pack(cfg):
    records = {}
    for name in sorted(os.listdir(os.path.join(cfg["out"], "parts"))):
        with open(os.path.join(cfg["out"], "parts", name)) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    records[r["game_id"]] = r
    ids = sorted(records)
    shards = []
    for i in range(0, len(ids), cfg["shard_games"]):
        chunk = ids[i:i + cfg["shard_games"]]
        name = "shard-%06d.jsonl.gz" % (i // cfg["shard_games"])
        path = os.path.join(cfg["out"], name)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=1) as fh:
            for g in chunk:
                fh.write(json.dumps(records[g], separators=(",", ":")) + "\n")
        roots = sum(len(records[g]["roots"]) for g in chunk)
        labelled = sum(1 for g in chunk for r in records[g]["roots"] if r["root_score"] is not None)
        shards.append({"file": name, "first_game": chunk[0], "sha256": sha256(path), "bytes": os.path.getsize(path),
                       "counts": {"games": len(chunk), "roots": roots, "labelled_roots": labelled}})
    manifest = {"schema": 3, "producer": "nnue_train/sf19nn_selfplay.py (SF19-NN, not sploppy's bot)",
                "model_sha256": sha256(cfg["net"]), "binary_sha256": sha256(cfg["engine"]),
                "capture_clock": cfg["clock"], "repetition_draw": False, "score_adjudication": False,
                "score_scale": 600, "label": "final UCI score of `go nodes` in net units (cp * 208 / 100), mate +-28000",
                "settings": {k: v for k, v in cfg.items() if k not in ("log", "out")},
                "shards": shards, "complete": len(ids) == cfg["games"]}
    with open(os.path.join(cfg["out"], "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    return len(ids), sum(s["counts"]["roots"] for s in shards)


def config():
    return {"engine": ENGINE, "net": NET, "games": GAMES, "nodes": NODES, "workers": WORKERS, "hash_mb": HASH_MB,
            "seed": SEED, "opening_plies": OPENING_PLIES, "random_moves": RANDOM_MOVES,
            "random_from": RANDOM_FROM, "random_to": RANDOM_TO, "max_plies": MAX_PLIES, "clock": CAPTURE_CLOCK,
            "shard_games": SHARD_GAMES, "stone_value": STONE_VALUE, "out": OUT, "log": LOG,
            "pv_plies": PV_PLIES,
            "player": "sf19nn:%s?nodes=%d&hash=%d" % (NET, NODES, HASH_MB)}


def main(cfg=None):
    cfg = cfg or config()
    import engine
    initial = list(engine.initial_board())
    home = [s for s in range(81) if 1 <= initial[s] <= 3]
    if not all(s // 9 + s % 9 < 8 for s in home):  # Blue's pieces must sit around a1 for the identity frame
        raise SystemExit("the initial position is not in the port's frame")
    os.makedirs(os.path.join(cfg["out"], "parts"), exist_ok=True)
    os.makedirs(os.path.dirname(cfg["log"]), exist_ok=True)
    os.nice(NICE)
    log(cfg, "START %s games=%d nodes=%d workers=%d seed=%d engine sha %s net sha %s"
        % (os.path.basename(cfg["out"]), cfg["games"], cfg["nodes"], cfg["workers"], cfg["seed"],
           sha256(cfg["engine"])[:16], sha256(cfg["net"])[:16]))
    # ids already played by any earlier session (its worker count may differ); torn last lines are cut here
    have = set()
    for name in os.listdir(os.path.join(cfg["out"], "parts")):
        have |= done_ids(os.path.join(cfg["out"], "parts", name))
    log(cfg, "resuming with %d games already recorded" % len(have))
    procs = [Process(target=worker, args=(cfg, k, have)) for k in range(cfg["workers"])]
    for p in procs:
        p.start()

    def stop(signum, frame):
        # without this the workers survive a kill of this process, keep playing the same ids as the next
        # run and append to the same part files (09-18: half a day of duplicated games)
        log(cfg, "signal %d: stopping %d workers" % (signum, len(procs)))
        for child in procs:
            child.terminate()
        for child in procs:
            child.join(timeout=10)
        raise SystemExit(1)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    t0, last = time.time(), -1
    while any(p.is_alive() for p in procs):
        time.sleep(60)
        n = len(set().union(*(part_ids(os.path.join(cfg["out"], "parts", name))
                             for name in os.listdir(os.path.join(cfg["out"], "parts")))))
        if n // 50 != last // 50:
            log(cfg, "progress %d/%d games, %.0f games/h this session" % (n, cfg["games"], (n - len(have)) / max(1e-9, time.time() - t0) * 3600))
            last = n
    games, roots = pack(cfg)
    log(cfg, "%s %d games, %d roots -> %s" % ("COMPLETE" if games == cfg["games"] else "PARTIAL", games, roots, cfg["out"]))


GAME_ID = re.compile(rb'"game_id":\s*(\d+)')


def part_ids(path):
    """The game ids in a part file, read without parsing the JSON.

    Progress counts games, not lines: after the 09-18 orphan incident the same
    game can sit in two part files, and counting lines said "1664/1600 done"
    while a third of the batch had never been played.
    """
    ids = set()
    with open(path, "rb") as fh:
        for raw in fh:
            found = GAME_ID.search(raw)
            if found:
                ids.add(int(found.group(1)))
    return ids


if __name__ == "__main__":
    main()
