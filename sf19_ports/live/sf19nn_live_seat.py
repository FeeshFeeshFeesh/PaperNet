"""PaperNet0's live engine: SF19-NN as an RPSI seat for the site client.

SF19-NN is Stockfish 19 with the rps-v6 rules ported by hand plus a C++
re-implementation of sploppy's RPSNNUE1 inference (see ../README.md). It plays
with sploppy's MIT example.nnue, read in place from sep2026/sploppy_engine/.

A bridge only: the GPL engine binary next to this file runs as a separate
process and is spoken to over UCI, so no GPL code enters sep2026.
connect_bot.py (ENGINE = "sf19nn") starts one of these per game slot.

RPSI in (from the site client) -> UCI out:
  - site FEN rows run rank 1 first, '.' or digits for empty, uppercase = Blue.
    Blue's home corner is the corner nearest Blue's pieces; the board is
    mirrored so that corner is a1 (white = Blue, home a1, goal i9).
  - moves "e2-e3" / "e2xe3" / "Re2-e3" -> "e2e3" in that frame, and back.
  - go movetime N -> go movetime N.
    go btime/rtime/binc/rinc -> go movetime <budget>, where the budget is the
    rule the live bot already used with sploppy's engine (match/src/rpsi.rs,
    MIT): about clock/30 + 0.8 x increment - 150 ms, at least MIN_MOVE_MS and
    at most MAX_MOVE_MS, never closer than 1.5 s to the flag. Under 3 s left
    it plays fast; under 0.8 s it plays a depth-1 move.
One line per move goes to LOG_DIR (budget, depth, nodes, time, score).
After each bestmove is sent, a snapshot (the game in the site's own notation, the
clocks, our move, score and PV) replaces STATUS_DIR/seat_<pid>.json for the status
window, sep2026/papernet_status.py. Best effort: nothing in it can reach play.

No sys.argv. The constants below are the defaults for running this by hand;
connect_bot.py overrides THREADS / HASH_MB / MIN_MOVE_MS / MAX_MOVE_MS through
the environment variables of the same names prefixed with SF19NN_.
"""
import json
import os
import subprocess
import sys
import time

HERE        = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eviction                      # noqa: E402  -- needs HERE on the path first

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
ENGINE      = os.path.join(HERE, "stockfish")
NET         = os.environ.get(
    "SF19NN_NET",
    _ROOT + "/project_files/sploppy_engine/example.nnue",
)
NET_SCALE   = 100          # search value = net value * NET_SCALE / 100
THREADS     = 2            # search threads per game
HASH_MB     = 64           # transposition table per game
MIN_MOVE_MS = 250          # least thinking time per move under a clock
MAX_MOVE_MS = 4000         # most thinking time per move under a clock
DEFAULT_MOVETIME_MS = 500  # when the host gives no time at all
LOG_DIR     = os.path.join(HERE, "logs")
STATUS_DIR  = os.path.join(HERE, "status")   # the status window's snapshots; never read back here
NAME        = "SF19-NN"

# The eviction override, in eviction.py next to this file. A root-level move policy for the
# endgame the net scores +9 and still draws by shuffling. It never enters the search and never
# invents a score: it narrows the ROOT move list and re-runs the real search inside it. The move
# budget is SPLIT, not spent twice -- the ordinary unrestricted search keeps EVICTION_SPLIT of
# it, and if that finds a forced win we play it and the override never speaks. Set False to kill.
EVICTION        = True
EVICTION_SPLIT  = 0.6      # share of the move budget left to the ordinary search
EVICTION_MARGIN = 150      # cp the restricted best may fall short before we keep the plain move

# sploppy's clock constants (match/src/rpsi.rs)
PANIC_CLOCK_MS   = 800
LOW_CLOCK_MS     = 3000
# 2026-09-25: was 150, and that is why we kept losing on time. MEASURED over 21,897 moves in 28
# games from logs/: the site charges us a FIXED ~250 ms per move on top of what the engine reports
# (median add 234-265 ms in every engine-time bucket from <300 ms to >2500 ms, while the RATIO
# swings 5.8x -> 1.06x, so it is a constant cost, not a proportional one). p90 is ~680 ms.
#
# With budget = 0.8*inc + clock/30 - MOVE_OVERHEAD_MS, the clock settles where charged == inc, so
# the equilibrium is clock* = 30 * (MOVE_OVERHEAD_MS - 50) and the equilibrium THINKING time is
# inc - overhead = ~750 ms whatever this constant is. At 150 that put clock* at exactly 3000 ms =
# LOW_CLOCK_MS, i.e. pinned in the panic branch: throttled to 0.5*inc = 500 ms of search and only
# 2.2 s above the hard floor, so any latency spike (p99 ~1.1 s, worst seen 11 s) was a flag fall.
# At 500 the equilibrium is 13.5 s of clock with the SAME 750 ms of thinking, and the 500 ms
# buys nothing away from the search -- it only stops us banking the clock down into the panic zone.
MOVE_OVERHEAD_MS = 500


def _env_int(name, default):
    try:
        return int(os.environ.get("SF19NN_" + name, default))
    except ValueError:
        return default


THREADS     = _env_int("THREADS", THREADS)
HASH_MB     = _env_int("HASH_MB", HASH_MB)
MIN_MOVE_MS = _env_int("MIN_MOVE_MS", MIN_MOVE_MS)
MAX_MOVE_MS = _env_int("MAX_MOVE_MS", MAX_MOVE_MS)

CORNERS = (0, 8, 72, 80)
PID = os.getpid()
os.makedirs(LOG_DIR, exist_ok=True)
_log = open(os.path.join(LOG_DIR, "seat_%d.log" % PID), "a", encoding="utf-8")


def log(msg):
    _log.write(msg + "\n")
    _log.flush()


def say(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def note(msg):
    log("NOTE " + msg)
    say("info string " + msg)


# The current game in the site's notation, for the status window only.
SITE = {}


def status(event, info=None, frame=None, **parts):
    """Replace this seat's snapshot. Called after the reply is out; every failure is swallowed
    here, because an exception reaching main() would send a second bestmove."""
    try:
        snap = dict(SITE, event=event, pid=PID, t=time.time())
        if info is not None:
            def field(k):
                return info[info.index(k) + 1] if k in info else None
            snap.update(depth=field("depth"), seldepth=field("seldepth"), nodes=field("nodes"),
                        ms=field("time"), pv=[])
            if "pv" in info and frame is not None:
                for mv in info[info.index("pv") + 1:][:8]:
                    sq = parse_squares(mv)
                    if sq is None:
                        break
                    snap["pv"].append(sq_name(frame[sq[0]]) + "-" + sq_name(frame[sq[1]]))
        snap.update(parts)
        os.makedirs(STATUS_DIR, exist_ok=True)
        path = os.path.join(STATUS_DIR, "seat_%d.json" % PID)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


# ---------------------------------------------------------------- the engine
class Engine:
    def __init__(self):
        self.p = subprocess.Popen([ENGINE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.send("uci")
        self.wait("uciok")
        self.send("setoption name Threads value %d" % THREADS)
        self.send("setoption name Hash value %d" % HASH_MB)
        self.send("setoption name EvalFile value " + NET)
        self.send("setoption name NetScale value %d" % NET_SCALE)
        self.send("isready")
        _, seen = self.wait("readyok")
        if not any("RPSNNUE1" in x for x in seen):
            raise RuntimeError("net did not load: %s" % [x for x in seen if "EvalFile" in x])
        log("START threads=%d hash=%d min=%d max=%d net=%s" % (
            THREADS, HASH_MB, MIN_MOVE_MS, MAX_MOVE_MS, NET))

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

    def ready(self):
        self.send("isready")
        self.wait("readyok")

    def current_fen(self):
        """Ask the engine where it thinks it is. Cheaper and safer than replaying the moves."""
        self.send("d")
        line, _ = self.wait("Fen:")
        return line.split(None, 1)[1].strip()


# ---------------------------------------------------------------- the frame
def sq_index(name):
    if len(name) != 2 or not ("a" <= name[0] <= "i") or not ("1" <= name[1] <= "9"):
        return None
    return (ord(name[1]) - ord("1")) * 9 + (ord(name[0]) - ord("a"))


def sq_name(i):
    return "abcdefghi"[i % 9] + str(i // 9 + 1)


def parse_squares(token):
    t = token.lstrip("RPSrps")
    if len(t) == 4:
        a, b = t[:2], t[2:]
    elif len(t) == 5 and t[2] in "-x":
        a, b = t[:2], t[3:]
    else:
        return None
    fa, fb = sq_index(a), sq_index(b)
    return None if fa is None or fb is None else (fa, fb)


def parse_board(field):
    rows = field.split("/")
    if len(rows) != 9:
        raise ValueError("FEN has %d rows" % len(rows))
    board = [None] * 81
    for r, row in enumerate(rows):
        f = 0
        for ch in row:
            if ch.isdigit():
                f += int(ch)
            elif ch == ".":
                f += 1
            elif ch.upper() in "RPS":
                if f > 8:
                    raise ValueError("row overflow")
                board[r * 9 + f] = ch
                f += 1
            else:
                raise ValueError("unknown piece %r" % ch)
        if f != 9:
            raise ValueError("row %d covers %d files" % (r + 1, f))
    return board


def nearest_corner(squares):
    best, best_d = CORNERS[0], None
    for c in CORNERS:
        d = sum(max(abs(c // 9 - s // 9), abs(c % 9 - s % 9)) for s in squares)
        if best_d is None or d < best_d:
            best, best_d = c, d
    return best


def make_transform(board):
    blue = [s for s in range(81) if board[s] and board[s].isupper()]
    red = [s for s in range(81) if board[s] and board[s].islower()]
    home = nearest_corner(blue) if blue else 0
    red_home = nearest_corner(red) if red else 80 - home
    if red_home != 80 - home:
        note("home corners disagree; using the identity transform")
        home = 0
    flip_file, flip_rank = home in (8, 80), home in (72, 80)
    t = []
    for i in range(81):
        r, f = i // 9, i % 9
        t.append((8 - r if flip_rank else r) * 9 + (8 - f if flip_file else f))
    return t          # an involution: site -> engine and engine -> site


def engine_fen(board, t, blue_to_move):
    squares = [None] * 81
    for s in range(81):
        squares[t[s]] = board[s]
    rows = []
    for r in range(8, -1, -1):
        out, run = "", 0
        for f in range(9):
            pc = squares[r * 9 + f]
            if pc is None:
                run += 1
            else:
                if run:
                    out += str(run)
                    run = 0
                out += pc
        if run:
            out += str(run)
        rows.append(out)
    return "/".join(rows) + (" w" if blue_to_move else " b") + " - - 0 1"


def normalise(token):
    sq = parse_squares(token)
    return token if sq is None else sq_name(sq[0]) + "-" + sq_name(sq[1])


# ---------------------------------------------------------------- the clock
def move_budget_ms(clock_ms, inc_ms):
    """Wall time for one move under a Fischer clock (sploppy's rule, MIT)."""
    if clock_ms < LOW_CLOCK_MS:
        return int(max(50.0, min(0.5 * inc_ms, clock_ms - PANIC_CLOCK_MS, MAX_MOVE_MS)))
    safe = clock_ms - 1500.0
    target = min(0.8 * inc_ms + clock_ms / 30.0 - MOVE_OVERHEAD_MS, MAX_MOVE_MS, safe)
    return int(max(target, min(MIN_MOVE_MS, safe)))


def go_command(args, blue_to_move):
    p = {}
    for a, b in zip(args, args[1:]):
        try:
            p[a] = int(b)
        except ValueError:
            pass
    side = "b" if blue_to_move else "r"
    if "movetime" in p:
        return "go movetime %d" % max(1, p["movetime"])
    if side + "time" in p:
        clock = p[side + "time"]
        if clock < PANIC_CLOCK_MS:
            return "go depth 1"
        return "go movetime %d" % max(1, move_budget_ms(clock, p.get(side + "inc", 0)))
    return "go movetime %d" % DEFAULT_MOVETIME_MS


# ---------------------------------------------------------------- the session
def main():
    eng = Engine()
    t = None
    fen = None
    uci_moves = []
    blue_to_move = True
    server_moves = []
    for raw in sys.stdin:
        words = raw.split()
        if not words:
            continue
        cmd, args = words[0], words[1:]
        try:
            if cmd == "rpsi":
                say("id name " + NAME)
                say("id author Stockfish 19 + rps-v6 rules port + sploppy MIT net (GPL, separate process)")
                say("protocol 1")
                say("rules 2")
                say("mode V6 Intransitive")
                say("rpsiok")
            elif cmd == "isready":
                eng.ready()
                say("readyok")
            elif cmd == "newgame":
                eng.send("ucinewgame")
                eng.ready()
                t, fen, uci_moves, server_moves = None, None, [], []
                SITE.clear()
                status("newgame")
            elif cmd == "position":
                if not args or args[0] != "fen":
                    note("position without a fen; ignored")
                    continue
                rest = args[1:]
                at = rest.index("moves") if "moves" in rest else len(rest)
                fields, moves = rest[:at], rest[at + 1:]
                board = parse_board(fields[0])
                side = fields[1].lower() if len(fields) > 1 else "b"
                blue_start = side != "r"
                t = make_transform(board)
                fen = engine_fen(board, t, blue_start)
                uci_moves = []
                for tok in moves:
                    sq = parse_squares(tok)
                    if sq is None:
                        raise ValueError("unparsable move %r" % tok)
                    uci_moves.append(sq_name(t[sq[0]]) + sq_name(t[sq[1]]))
                blue_to_move = blue_start if len(moves) % 2 == 0 else not blue_start
                server_moves = []
                SITE.update(start=fields[0], side=side, moves=list(moves))
            elif cmd == "legalmoves":
                server_moves = args
            elif cmd == "go":
                go(eng, t, fen, uci_moves, blue_to_move, server_moves, args)
            elif cmd == "quit":
                break
        except Exception as e:  # keep the seat alive; the host judges legality
            note("error in %s: %s" % (cmd, e))
            if cmd == "go":
                say("bestmove " + (server_moves[0] if server_moves else "a1-a1"))
    try:
        eng.send("quit")
    except Exception:
        pass


def score_of(last):
    """(cp, mate) from an info line's words; either may be None."""
    cp = int(last[last.index("cp") + 1]) if "cp" in last else None
    mate = int(last[last.index("mate") + 1]) if "mate" in last else None
    return cp, mate


def playable_here(moves, t, server_moves):
    """Keep only the engine moves the host has actually offered. The host is the authority on
    legality, so anything it did not list is dropped rather than argued with."""
    if not server_moves:
        return set(moves)
    offered = set(normalise(m) for m in server_moves)
    out = set()
    for mv in moves:
        a, b = sq_index(mv[:2]), sq_index(mv[2:4])
        if a is None or b is None:
            continue
        if sq_name(t[a]) + "-" + sq_name(t[b]) in offered:
            out.add(mv)
    return out


def go(eng, t, fen, uci_moves, blue_to_move, server_moves, args):
    if fen is None:
        note("go without a position")
        say("bestmove " + (server_moves[0] if server_moves else "a1-a1"))
        return
    gocmd = go_command(args, blue_to_move)
    pos = "position fen " + fen + (" moves " + " ".join(uci_moves) if uci_moves else "")
    eng.send(pos)

    mode, second = None, None
    if EVICTION and gocmd.startswith("go movetime "):
        try:
            board, btm = eviction.parse_fen(eng.current_fen())
            allow, mode, _why = eviction.allowed(board, btm)
            allow = playable_here(allow, t, server_moves) if allow else None
        except Exception as e:      # a pattern bug must never cost a game
            note("eviction: %s" % e)
            allow, mode = None, None
        if allow:
            full = int(gocmd.split()[2])
            keep = max(1, int(full * EVICTION_SPLIT))
            gocmd = "go movetime %d" % keep
            second = "go movetime %d searchmoves %s" % (max(1, full - keep), " ".join(sorted(allow)))
        else:
            mode = None

    eng.send(gocmd)
    line, seen = eng.wait("bestmove")
    infos = [s for s in seen if s.startswith("info depth")]
    for s in seen:
        if "illegal" in s.lower() or "invalid" in s.lower():
            note("engine said: " + s[:200])
    best = line.split()[1] if len(line.split()) > 1 else "(none)"
    last = infos[-1].split() if infos else []

    def field(k):
        return last[last.index(k) + 1] if k in last else "-"

    score = (field("cp") if "cp" in last else "mate" + field("mate"))

    tag = ""
    if second is not None:
        cp, mate = score_of(last)
        if mate is not None and mate > 0:
            tag = " evic=%s/won-already" % mode          # a forced win outranks the plan
        else:
            eng.send(pos)
            eng.send(second)
            line2, seen2 = eng.wait("bestmove")
            infos2 = [s for s in seen2 if s.startswith("info depth")]
            last2 = infos2[-1].split() if infos2 else []
            alt = line2.split()[1] if len(line2.split()) > 1 else None
            cp2, mate2 = score_of(last2)
            lost = mate2 is not None and mate2 < 0
            worse = cp is not None and cp2 is not None and cp2 < cp - EVICTION_MARGIN
            if alt and alt not in ("(none)", "0000") and not lost and not worse:
                tag = " evic=%s/%s->%s" % (mode, best, alt)
                best, last = alt, last2
                score = (field("cp") if "cp" in last else "mate" + field("mate"))
            else:
                tag = " evic=%s/declined%s%s" % (mode, " lost" if lost else "",
                                                " worse" if worse else "")
    log("%s | %s ply=%d depth=%s seldepth=%s nodes=%s time=%s score=%s best=%s%s" % (
        " ".join(args), gocmd, len(uci_moves), field("depth"), field("seldepth"),
        field("nodes"), field("time"), score, best, tag))
    sq = parse_squares(best) if best not in ("(none)", "0000") else None
    if sq is None:
        note("engine gave no move (%s); falling back" % best)
        say("bestmove " + (server_moves[0] if server_moves else "a1-a1"))
        return
    want = sq_name(t[sq[0]]) + "-" + sq_name(t[sq[1]])
    token = want
    if server_moves:
        match = [m for m in server_moves if normalise(m) == want]
        if match:
            token = match[0]
        else:
            note("our move %s is not in the host's legalmoves; falling back" % want)
            token = server_moves[0]
    say("bestmove " + token)
    status("move", info=last, frame=t, me="b" if blue_to_move else "r", go=args, budget=gocmd,
           score=score, best=token)


if __name__ == "__main__":
    main()
