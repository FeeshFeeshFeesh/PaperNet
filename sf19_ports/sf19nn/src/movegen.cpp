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

// rps-v6 port of movegen.cpp. Captures and goal entries form the CAPTURES
// stage; every other step onto an empty square is a QUIET.

#include "movegen.h"

#include <cassert>

#include "bitboard.h"
#include "position.h"

namespace Stockfish {

namespace {

constexpr PieceType Types[] = {ROCK, PAPER, SCISSORS};

inline Move* splat_steps(Square from, Bitboard to, Move* moveList) {

    while (to)
        *moveList++ = Move(from, pop_lsb(to));
    return moveList;
}

inline Move* splat_steps_into(Bitboard from, Square to, Move* moveList) {

    while (from)
        *moveList++ = Move(pop_lsb(from), to);
    return moveList;
}

template<GenType Type>
Move* generate_all(const Position& pos, Move* moveList) {

    Color    us      = pos.side_to_move();
    Color    them    = ~us;
    Bitboard empties = AllSquares & ~pos.pieces();
    Bitboard goal    = square_bb(goal_square(us));

    for (PieceType pt : Types)
    {
        Bitboard prey   = pos.pieces(them, prey_of(pt));
        Bitboard target = Type == CAPTURES ? prey | (empties & goal)
                        : Type == QUIETS   ? empties & ~goal
                                           : empties | prey;

        for (Bitboard b = pos.pieces(us, pt); b;)
        {
            Square from = pop_lsb(b);
            moveList    = splat_steps(from, step_bb(from) & target, moveList);
        }
    }

    return moveList;
}

}  // namespace


// <CAPTURES>     Generates all captures and goal entries
// <QUIETS>       Generates all other steps onto empty squares
// <NON_EVASIONS> Generates all pseudo-legal captures and non-captures
template<GenType Type>
Move* generate(const Position& pos, Move* moveList) {

    assert(Type != LEGAL && Type != EVASIONS);

    return generate_all<Type>(pos, moveList);
}

// Explicit template instantiations
template Move* generate<CAPTURES>(const Position&, Move*);
template Move* generate<QUIETS>(const Position&, Move*);
template Move* generate<NON_EVASIONS>(const Position&, Move*);


// <EVASIONS> The opponent threatens to enter our home corner next move. The
// only moves that do not lose at once: enter our own goal first, capture the
// threatening piece when there is only one, or step into the empty home
// corner with a piece that none of the threatening pieces can capture. If
// there is none, every move loses and all of them are generated.
template<>
Move* generate<EVASIONS>(const Position& pos, Move* moveList) {

    assert(pos.checkers());

    Color    us      = pos.side_to_move();
    Color    them    = ~us;
    Bitboard threats = pos.checkers();
    Move*    start   = moveList;

    // Win first
    moveList = splat_steps_into(pos.threats(us), goal_square(us), moveList);

    // Capture a lone threat
    if (!more_than_one(threats))
    {
        Square   t       = lsb(threats);
        Bitboard hunters = step_bb(t) & pos.pieces(us, predator_of(type_of(pos.piece_on(t))));
        moveList         = splat_steps_into(hunters, t, moveList);
    }

    // Block our home corner
    Square home = goal_square(them);
    if (pos.empty(home))
        for (PieceType pt : Types)
            if (!(threats & pos.pieces(them, predator_of(pt))))
                moveList = splat_steps_into(step_bb(home) & pos.pieces(us, pt), home, moveList);

    if (moveList == start)
        return generate<NON_EVASIONS>(pos, moveList);

    return moveList;
}


// generate<LEGAL> generates all the legal moves in the given position. Every
// pseudo-legal move is legal; when a goal threat is pending only the evasions
// are listed, as in chess.
template<>
Move* generate<LEGAL>(const Position& pos, Move* moveList) {

    return pos.checkers() ? generate<EVASIONS>(pos, moveList)
                          : generate<NON_EVASIONS>(pos, moveList);
}

}  // namespace Stockfish
