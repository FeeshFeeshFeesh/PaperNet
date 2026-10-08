"""SF19-NN (Stockfish 19 rps port + sploppy's MIT example.nnue, C++ inference) as an RPSI seat.

Copy of sf19_seat.py with ENGINE/NAME/LOG_DIR changed and EvalFile/NetScale set;
the UCI frame is the same (white = Blue, home a1, goal i9). UCI_Variant and
"Use NNUE" are accepted as no-ops; the net is chosen by EvalFile.

Original notes:

A bridge only: it runs the GPL engine binary as a separate process and speaks
UCI to it, so no GPL code enters this file or the sep2026 tree. No network is
loaded ("Use NNUE" false), so the engine plays on the patch's four-line
handcrafted eval. Hushhush's own net is all-rights-reserved and is not used.

RPSI in (from sploppy's `bot eval` host or the site client) -> UCI out:
  - site FEN rows run rank 1 first, '.' or digits for empty, uppercase = Blue.
    Blue's home corner is found as the corner nearest Blue's pieces, and the
    board is mirrored so that corner is a1. That frame IS Fairy's rps-v6 frame
    (white = Blue, home a1, goal i9), so no colour flip is needed.
  - moves "e2-e3" / "e2xe3" / "Re2-e3" -> "e2e3" in that frame, and back.
  - go movetime N -> go movetime N;  go btime/rtime/binc/rinc -> wtime/btime/winc/binc.
One line per move goes to LOG_DIR (depth, seldepth, nodes, time, score).
No sys.argv; constants below.
"""
import os
import subprocess
import sys

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ENGINE    = os.path.join(_ROOT, "sf19_ports/sf19nn/stockfish")
NET       = _ROOT + "/project_files/sploppy_engine/example.nnue"
NET_SCALE = 100          # search value = net value * NET_SCALE / 100
THREADS   = 1
HASH_MB   = 64
USE_NNUE  = False
DEFAULT_MOVETIME_MS = 500      # when the host gives no time at all
LOG_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sf19nn_seat_logs")
NAME      = "SF19-NN-example"

CORNERS = (0, 8, 72, 80)
PID = os.getpid()
os.makedirs(LOG_DIR, exist_ok=True)
_log = open(os.path.join(LOG_DIR, "fsf_seat_%d.log" % PID), "a", encoding="utf-8")


def log(msg):
    _log.write(msg + "\n")
    _log.flush()


def say(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def note(msg):
    log("NOTE " + msg)
    say("info string " + msg)


# ---------------------------------------------------------------- the engine
class Engine:
    def __init__(self):
        self.p = subprocess.Popen([ENGINE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.send("uci")
        self.wait("uciok")
        self.send("setoption name UCI_Variant value rps-v6")
        self.send("setoption name Use NNUE value %s" % ("true" if USE_NNUE else "false"))
        self.send("setoption name Threads value %d" % THREADS)
        self.send("setoption name Hash value %d" % HASH_MB)
        self.send("setoption name EvalFile value " + NET)
        self.send("setoption name NetScale value %d" % NET_SCALE)
        self.send("isready")
        _, seen = self.wait("readyok")
        if not any("RPSNNUE1" in x for x in seen):
            raise RuntimeError("net did not load: %s" % [x for x in seen if "EvalFile" in x])

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
    return t          # an involution: site -> fairy and fairy -> site


def fairy_fen(board, t, blue_to_move):
    fairy = [None] * 81
    for s in range(81):
        fairy[t[s]] = board[s]
    rows = []
    for r in range(8, -1, -1):
        out, run = "", 0
        for f in range(9):
            pc = fairy[r * 9 + f]
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
                fen = fairy_fen(board, t, blue_start)
                uci_moves = []
                for tok in moves:
                    sq = parse_squares(tok)
                    if sq is None:
                        raise ValueError("unparsable move %r" % tok)
                    uci_moves.append(sq_name(t[sq[0]]) + sq_name(t[sq[1]]))
                blue_to_move = blue_start if len(moves) % 2 == 0 else not blue_start
                server_moves = []
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


def go(eng, t, fen, uci_moves, blue_to_move, server_moves, args):
    if fen is None:
        note("go without a position")
        say("bestmove " + (server_moves[0] if server_moves else "a1-a1"))
        return
    p = {}
    for a, b in zip(args, args[1:]):
        try:
            p[a] = int(b)
        except ValueError:
            pass
    if "movetime" in p:
        gocmd = "go movetime %d" % max(1, p["movetime"])
    elif "btime" in p or "rtime" in p:
        gocmd = "go wtime %d btime %d winc %d binc %d" % (
            p.get("btime", 1000), p.get("rtime", 1000), p.get("binc", 0), p.get("rinc", 0))
    else:
        gocmd = "go movetime %d" % DEFAULT_MOVETIME_MS
    pos = "position fen " + fen + (" moves " + " ".join(uci_moves) if uci_moves else "")
    eng.send(pos)
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
    log("%s ply=%d depth=%s seldepth=%s nodes=%s time=%s score=%s best=%s" % (
        gocmd, len(uci_moves), field("depth"), field("seldepth"), field("nodes"),
        field("time"), score, best))
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


if __name__ == "__main__":
    main()
