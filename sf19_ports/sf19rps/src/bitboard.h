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

// rps-v6 port: bitboards for the 9x9 board (bit i = square i, a1 = 0, i9 = 80).

#ifndef BITBOARD_H_INCLUDED
#define BITBOARD_H_INCLUDED

#include <array>
#include <cassert>
#include <string>

#include "types.h"
#include "misc.h"

namespace Stockfish {

namespace Bitboards {

std::string pretty(Bitboard b);

}  // namespace Stockfish::Bitboards

constexpr Bitboard AllSquares = (Bitboard(1) << SQUARE_NB) - 1;

constexpr Bitboard FileABB = []() {
    Bitboard b = 0;
    for (int r = 0; r < 9; ++r)
        b |= Bitboard(1) << (9 * r);
    return b;
}();
constexpr Bitboard FileIBB = FileABB << 8;
constexpr Bitboard Rank1BB = 0x1FF;
constexpr Bitboard Rank9BB = Rank1BB << (9 * 8);

constexpr Bitboard square_bb(Square s) {
    assert(is_ok(s));
    return Bitboard(1) << s;
}

// Overloads of bitwise operators between a Bitboard and a Square for testing
// whether a given bit is set in a bitboard, and for setting and clearing bits.

constexpr Bitboard  operator&(Bitboard b, Square s) { return b & square_bb(s); }
constexpr Bitboard  operator|(Bitboard b, Square s) { return b | square_bb(s); }
constexpr Bitboard  operator^(Bitboard b, Square s) { return b ^ square_bb(s); }
constexpr Bitboard& operator|=(Bitboard& b, Square s) { return b |= square_bb(s); }
constexpr Bitboard& operator^=(Bitboard& b, Square s) { return b ^= square_bb(s); }

constexpr Bitboard operator&(Square s, Bitboard b) { return b & s; }
constexpr Bitboard operator|(Square s, Bitboard b) { return b | s; }
constexpr Bitboard operator^(Square s, Bitboard b) { return b ^ s; }

constexpr Bitboard operator|(Square s1, Square s2) { return square_bb(s1) | s2; }

constexpr bool more_than_one(Bitboard b) { return b & (b - 1); }

constexpr Bitboard rank_bb(Rank r) { return Rank1BB << (9 * r); }
constexpr Bitboard file_bb(File f) { return FileABB << f; }

// Moves a bitboard one step along direction d
constexpr Bitboard shift(Bitboard b, Direction d) {
    switch (d)
    {
    case NORTH :
        return (b << 9) & AllSquares;
    case SOUTH :
        return b >> 9;
    case EAST :
        return (b & ~FileIBB) << 1;
    case WEST :
        return (b & ~FileABB) >> 1;
    case NORTH_EAST :
        return ((b & ~FileIBB) << 10) & AllSquares;
    case NORTH_WEST :
        return ((b & ~FileABB) << 8) & AllSquares;
    case SOUTH_EAST :
        return (b & ~FileIBB) >> 8;
    case SOUTH_WEST :
        return (b & ~FileABB) >> 10;
    }
    return 0;
}

// Every square one king step away from any square of b
constexpr Bitboard king_steps(Bitboard b) {
    Bitboard e = shift(b, EAST), w = shift(b, WEST), ew = b | e | w;
    return e | w | shift(ew, NORTH) | shift(ew, SOUTH);
}

inline constexpr std::array<Bitboard, SQUARE_NB> StepBB = []() {
    std::array<Bitboard, SQUARE_NB> t{};
    for (int s = 0; s < SQUARE_NB; ++s)
    {
        t[s] = king_steps(Bitboard(1) << s);
    }
    return t;
}();

constexpr Bitboard step_bb(Square s) { return StepBB[s]; }

// The squares next to each side's goal corner
constexpr Bitboard goal_ring(Color c) { return StepBB[goal_square(c)]; }

constexpr int distance(Square x, Square y) {
    int df = int(file_of(x)) - int(file_of(y));
    int dr = int(rank_of(x)) - int(rank_of(y));
    df     = df < 0 ? -df : df;
    dr     = dr < 0 ? -dr : dr;
    return df > dr ? df : dr;
}

inline int popcount(Bitboard b) {
    return __builtin_popcountll(u64(b)) + __builtin_popcountll(u64(b >> 64));
}

inline Square lsb(Bitboard b) {
    assert(b);
    u64 lo = u64(b);
    return lo ? Square(__builtin_ctzll(lo)) : Square(64 + __builtin_ctzll(u64(b >> 64)));
}

inline Square msb(Bitboard b) {
    assert(b);
    u64 hi = u64(b >> 64);
    return hi ? Square(127 - __builtin_clzll(hi)) : Square(63 - __builtin_clzll(u64(b)));
}

inline Bitboard least_significant_square_bb(Bitboard b) {
    assert(b);
    return b & (~b + 1);
}

inline Square pop_lsb(Bitboard& b) {
    assert(b);
    const Square s = lsb(b);
    b &= b - 1;
    return s;
}

}  // namespace Stockfish

#endif  // #ifndef BITBOARD_H_INCLUDED
