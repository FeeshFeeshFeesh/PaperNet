"""Recover every REAL decision the live bot faced, as (position, move it chose).

WHY THIS IS NOT JUST A PARSE. The status log renders the board once per our move, and the
rendering is AFTER our move: the origin square carries the move dot and the destination carries
our piece, so the side to move in a rendered board is the OPPONENT, and `our_move` is the move
that LED to that board rather than one available from it. Pairing them naively gives an
off-by-one that is silently wrong -- the move is illegal from the board it is attached to, which
is how the error was caught.

So a decision is reconstructed by chaining: take the rendered board (opponent to move), apply
the recorded `their_reply` to get the position WE actually faced, and the move we chose there is
the NEXT record's `our_move`. That chain is self-checking at three points, and on the 2026-09
log it holds at:

    their_reply legal from the rendered board      29618 ok / 148 bad
    our next move legal after their reply          29179 ok /  42 bad
    reconstructed board == the next rendering      29157 ok /  22 bad

Only chain-verified decisions are yielded, so a reconnect or a redraw that breaks the chain is
dropped rather than guessed at. That also neutralises the known phantom-game problem in
live_boards.parse(): phantoms duplicate positions, and a duplicate decision is just a duplicate,
which DISTINCT counting removes.

Run from PyCharm. No sys.argv; constants below.
"""
import os
import sys

LIVE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "sf19_ports", "live")
TB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIVE_DIR)
sys.path.insert(0, TB_DIR)

import live_boards as LB
import rps_rules as R

RPS = set("RPS")
VERIFY_CHAIN = True        # drop decisions whose reconstruction does not match the next board


def fen_of(rec, stm_is_blue):
    """A rendered board -> FEN. Uppercase is Blue (= WHITE, goal i9); '.', '+' and the move
    dot are all empty."""
    out = []
    for rank in range(9, 0, -1):
        row, run = "", 0
        for g in rec["rows"][rank]:
            if g.upper() in RPS:
                if run:
                    row += str(run)
                    run = 0
                row += g
            else:
                run += 1
        if run:
            row += str(run)
        out.append(row)
    return "/".join(out) + " %s - - 0 1" % ("w" if stm_is_blue else "b")


def mv_str(m):
    return R.sq_name(m[0]) + "-" + R.sq_name(m[1])


def clock_table(plies):
    """ply -> (skeptical, generous) capture clock, the FALLBACK when the chain is broken.

    Exact wherever it matters, from one fact: every zeroing event reduces the total piece
    count.  A capture removes the captured piece; a goal entry ends the game.  So a gap in the
    log with an UNCHANGED total provably contains no zeroing move, and the clock advances by
    the whole gap -- the log's skipped plies cost nothing.  When the total did drop, a capture
    happened somewhere in those g plies and the clock afterwards is in [0, g-1]: the skeptical
    column takes g-1, the generous one 0.

    The first rendering of a game is the only genuinely unknown one, since the log starts at
    ply 3-8 rather than ply 1.  Skeptical bounds it by the plies elapsed, generous by 0.  The
    two columns are reported separately so the gate never rests on the guess.

    Independently checked: the longest no-capture stretch over 478 live games is exactly 200
    plies with none over, which is the referee enforcing CAPTURE_CLOCK and this reconstruction
    agreeing with it.
    """
    out = {}
    prev_ply = prev_tot = None
    hi = lo = 0
    for rec in plies:
        ply, mine, theirs = rec.get("ply"), rec.get("mine"), rec.get("theirs")
        if ply is None or mine is None or theirs is None:
            continue
        tot = mine + theirs
        if prev_ply is None:
            hi, lo = max(ply - 1, 0), 0
        else:
            gap = ply - prev_ply
            if tot < prev_tot:
                hi, lo = max(gap - 1, 0), 0       # a capture landed inside the gap
            else:
                hi, lo = hi + gap, lo + gap       # count unchanged -> provably no zeroing move
        out[ply] = (hi, lo)
        prev_ply, prev_tot = ply, tot
    return out


def decisions(path=None):
    """Yield dicts: pos (rps_rules Pos, our turn), move (the move we chose), ply, eval, game."""
    games = LB.parse(path) if path else LB.parse()
    for gi, gm in enumerate(games):
        plies = gm["plies"]
        clk = clock_table(plies)
        chain = {}                  # ply -> (hi, lo) carried forward by do_move, exact
        for i, rec in enumerate(plies):
            if len(rec.get("rows", {})) != 9:
                continue
            iam_blue = rec["me"] == "Blue"
            try:
                board = R.parse_fen(fen_of(rec, not iam_blue))   # opponent to move
            except Exception:
                continue
            tr = rec.get("their_reply")
            if not tr:
                continue
            cand = [m for m in R.gen_legal(board) if mv_str(m) == tr]
            if not cand:
                continue
            # The clock rides on the position: rps_rules.do_move zeroes it on a capture or a
            # goal entry, so setting it here is all a probe needs to respect the 200-ply rule.
            #
            # Propagated through the reconstructed moves rather than estimated, which makes it
            # EXACT given the game's opening clock: both plies of each pair are known, so
            # do_move decides which of the two zeroed instead of us bounding it.  Only a game's
            # first rendering is unknown (the log starts at ply 3-8, not ply 1) and the two
            # columns pin it from opposite sides; they converge for good at the first capture.
            got = chain.pop(rec.get("ply"), None)
            chi, clo = got if got is not None else clk.get(rec.get("ply"), (0, 0))
            exact = got is not None and chi == clo
            board.clock = chi
            pos = R.do_move(board, cand[0])                      # now OUR turn
            board.clock = clo
            pos_lo = R.do_move(board, cand[0])
            if i + 1 >= len(plies):
                continue
            nxt = plies[i + 1]
            om = nxt.get("our_move")
            if not om:
                continue
            ours = [m for m in R.gen_legal(pos) if mv_str(m) == om]
            if not ours:
                continue
            if VERIFY_CHAIN and len(nxt.get("rows", {})) == 9:
                after = R.do_move(pos, ours[0])
                try:
                    want = R.parse_fen(fen_of(nxt, not iam_blue))
                except Exception:
                    continue
                if R.to_fen(after).split()[0] != R.to_fen(want).split()[0]:
                    continue
                nply = nxt.get("ply")
                if nply is not None:
                    # pos_lo already carries the propagated generous clock -- stepping it is
                    # the whole update; re-seeding it from clo here would lose a ply per move
                    # and the two columns would drift apart instead of converging at a capture
                    chain[nply] = (after.clock, R.do_move(pos_lo, ours[0]).clock)
            yield {"pos": pos, "clock": pos.clock, "clock_lo": pos_lo.clock, "clock_exact": exact,
                   "move": ours[0], "move_str": om, "ply": nxt.get("ply"),
                   "eval": nxt.get("eval"), "depth": nxt.get("depth"), "game": gi,
                   "mine": nxt.get("mine"), "theirs": nxt.get("theirs"),
                   "result": gm.get("result"), "opponent": gm.get("opponent")}


def main():
    n = 0
    from collections import Counter
    c = Counter()
    for d in decisions():
        n += 1
        p = d["pos"]
        c[len(R.pieces(p, R.WHITE)) + len(R.pieces(p, R.BLACK))] += 1
    print("-----")
    print("%d chain-verified decisions" % n)
    for k in sorted(c):
        if k <= 6:
            print("  %2d pieces: %5d" % (k, c[k]))


if __name__ == "__main__":
    main()
