/*
  Stockfish, a UCI chess playing engine derived from Glaurung 2.1
  Copyright (C) 2004-2026 The Stockfish developers (see AUTHORS file)

  Stockfish is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  Stockfish is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

// rps-v6 port: the handcrafted race evaluation of the rps-v6 variant. Each
// piece is worth a stone plus a bonus for being close to its goal corner,
// and the side's most advanced piece gets an extra bonus.

#include "evaluate.h"

#include <algorithm>
#include <iomanip>
#include <sstream>

#include "bitboard.h"
#include "position.h"
#include "rpsnet.h"
#include "types.h"
#include "uci.h"

namespace Stockfish {

namespace Eval {
int classLoss = 0;
int classDamp = 0;
}

namespace {

// A piece is only ever removed by its predator, so owning no piece of type t is
// not a material deficit that can be repaired -- it is a permanent grant of
// immunity to every enemy prey_of(t) for the rest of the game.
//
// Measured 2026-09-24: at ply 200 a side in that state draws about three times
// as often at matched material, yet a 100k-node search moves by only about -6 cp
// across the capture that causes it (sf19_ports/tools/class_loss_probe.py). The
// net has a context bucket for the enemy's clamped material counts, so it could
// represent this, but it was trained on labels from a search that could not
// convert either and so never learned to charge for it. This charges explicitly.
//
// Counted in classes, not in freed pieces: the damage is the immunity itself,
// and one immune scissors that reaches the corner draws exactly as hard as three.
int closed_classes(const Position& pos, Color c) {

    int n = 0;

    for (PieceType t : {ROCK, PAPER, SCISSORS})
        if (!pos.pieces(c, t) && pos.pieces(~c, prey_of(t)))
            n++;

    return n;
}

// Score of one side in the variant's centipawn-like units (a piece = 100)
int race_score(const Position& pos, Color c) {

    int score = 0, nearest = 8;

    for (Bitboard b = pos.pieces(c); b;)
    {
        int d   = distance(pop_lsb(b), goal_square(c));
        nearest = std::min(nearest, d);
        score += 100 + 4 * (8 - d);
    }

    return score + 20 * (8 - nearest);
}

Value raw_eval(const Position& pos) {

    Color us = pos.side_to_move();
    return (race_score(pos, us) - race_score(pos, ~us)) * StoneValue / 100;
}

}  // namespace


// Evaluate is the evaluator for the outer world. It returns a static evaluation
// of the position from the point of view of the side to move.
Value Eval::evaluate(const Position& pos, int optimism) {

    int v;

    if (RpsNet::loaded())
    {
        v = RpsNet::evaluate(pos) * RpsNet::scalePercent / 100 + optimism * 7675 / 91000;

#ifdef CLOCK_DAMP
        // The net does take the clock as input, which is why damping was left
        // off here -- but its labels come from a search that could not convert
        // either, so it is taught to hold a winning score into a drawn game
        // (HANDOFF 231eo: +800 held for 155 moves, then drawn). Damping makes a
        // shuffling line decay while a capture, which zeroes the clock, does
        // not, so converting lines outrank waiting ones.
        v -= v * pos.rule50_count() / (2 * CAPTURE_CLOCK - 1);
#endif
    }
    else
    {
        v = raw_eval(pos) + optimism * 7675 / 91000;

        // Damp down the evaluation as the capture clock runs out
        v -= v * pos.rule50_count() / (2 * CAPTURE_CLOCK - 1);
    }

    if (Eval::classLoss)
    {
        Color us = pos.side_to_move();
        v -= Eval::classLoss * StoneValue / 100
           * (closed_classes(pos, us) - closed_classes(pos, ~us));
    }

    if (Eval::classDamp)
    {
        // The other shape the same finding can take. A closed class does not
        // simply cost material, it makes the position DRAWISH, and only the side
        // that is ahead is hurt by that -- the side that is behind is helped, and
        // should be willing to head for it. So damp toward zero by the closed
        // classes of whichever side currently has to do the converting.
        //
        // Unlike CLOCK_DAMP this gate cannot be reset by shuffling: a class that
        // has closed stays closed, because nothing on the board can put a piece
        // back. That is why it is worth trying again after damping was withdrawn.
        Color us    = pos.side_to_move();
        Color ahead = v > 0 ? us : ~us;
        int   n     = closed_classes(pos, ahead);

        if (n)
            v -= v * std::min(100, Eval::classDamp * n) / 100;
    }

    return std::clamp(v, VALUE_TB_LOSS_IN_MAX_PLY + 1, VALUE_TB_WIN_IN_MAX_PLY - 1);
}


std::string Eval::trace(Position& pos) {

    std::stringstream ss;
    Value             v = raw_eval(pos);
    v                   = pos.side_to_move() == WHITE ? v : -v;
    ss << std::showpoint << std::showpos << std::fixed << std::setprecision(2);
    ss << "Race evaluation " << 0.01 * UCIEngine::to_cp(v, pos) << " (white side)\n";
    if (RpsNet::loaded())
        ss << std::noshowpoint << std::noshowpos << "Net evaluation " << RpsNet::evaluate(pos)
           << " (side to move, net units)\n"
           << "Net dense inputs zero " << RpsNet::zero_inputs(pos) << " of "
           << 2 * RpsNet::hidden() << "\n";
    return ss.str();
}

}  // namespace Stockfish
