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
#include "types.h"
#include "uci.h"

namespace Stockfish {

namespace {

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

    int v = raw_eval(pos) + optimism * 7675 / 91000;

    // Damp down the evaluation as the capture clock runs out
    v -= v * pos.rule50_count() / (2 * CAPTURE_CLOCK - 1);

    return std::clamp(v, VALUE_TB_LOSS_IN_MAX_PLY + 1, VALUE_TB_WIN_IN_MAX_PLY - 1);
}


std::string Eval::trace(Position& pos) {

    std::stringstream ss;
    Value             v = raw_eval(pos);
    v                   = pos.side_to_move() == WHITE ? v : -v;
    ss << std::showpoint << std::showpos << std::fixed << std::setprecision(2);
    ss << "Race evaluation " << 0.01 * UCIEngine::to_cp(v, pos) << " (white side)\n";
    return ss.str();
}

}  // namespace Stockfish
