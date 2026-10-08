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

#ifndef TYPES_H_INCLUDED
    #define TYPES_H_INCLUDED

// When compiling with provided Makefile (e.g. for Linux and OSX), configuration
// is done automatically. To get started type 'make help'.
//
// When Makefile is not used (e.g. with Microsoft Visual Studio) some switches
// need to be set manually:
//
// -DNDEBUG      | Disable debugging mode. Always use this for release.
//
// -DNO_PREFETCH | Disable use of prefetch asm-instruction. You may need this to
//               | run on some very old machines.
//
// -DUSE_POPCNT  | Add runtime support for use of popcnt asm-instruction. Works
//               | only in 64-bit mode and requires hardware with popcnt support.
//
// -DUSE_PEXT    | Add runtime support for use of pext asm-instruction. Works
//               | only in 64-bit mode and requires hardware with pext support.

    #include <cassert>
    #include <cstddef>
    #include <cstdint>
    #include "misc.h"

    #if defined(_MSC_VER)
        // Disable some silly and noisy warnings from MSVC compiler
        #pragma warning(disable: 4127)  // Conditional expression is constant
        #pragma warning(disable: 4146)  // Unary minus operator applied to unsigned type
        #pragma warning(disable: 4800)  // Forcing value to bool 'true' or 'false'
    #endif

// Predefined macros hell:
//
// __GNUC__                Compiler is GCC, Clang or ICX
// __clang__               Compiler is Clang or ICX
// __INTEL_LLVM_COMPILER   Compiler is ICX
// _MSC_VER                Compiler is MSVC
// _WIN32                  Building on Windows (any)
// _WIN64                  Building on Windows 64 bit

    // Enforce minimum GCC version
    #if defined(__GNUC__) && !defined(__clang__) \
      && (__GNUC__ < 9 || (__GNUC__ == 9 && __GNUC_MINOR__ < 3))
        #error "Stockfish requires GCC 9.3 or later for correct compilation"
    #endif

    // Enforce minimum Clang version
    #if defined(__clang__) && (__clang_major__ < 11)
        #error "Stockfish requires Clang 11.0 or later for correct compilation"
    #endif

    #define ASSERT_ALIGNED(ptr, alignment) assert(reinterpret_cast<uintptr_t>(ptr) % alignment == 0)

    #if defined(_WIN64) && defined(_MSC_VER)  // No Makefile used
        #include <intrin.h>                   // Microsoft header for _BitScanForward64()
        #define IS_64BIT
    #endif

    #if defined(_MSC_VER)
        #include <nmmintrin.h>  // Microsoft header for _mm_popcnt_u64()
    #endif

    #if !defined(NO_PREFETCH) && defined(_MSC_VER)
        #include <xmmintrin.h>  // Microsoft header for _mm_prefetch()
    #endif

    #if defined(USE_PEXT)
        #include <immintrin.h>  // Header for _pext_u64() intrinsic
        #define pext(b, m) _pext_u64(b, m)
        #define pdep(b, m) _pdep_u64(b, m)
    #endif

namespace Stockfish {

    #ifdef USE_POPCNT
constexpr bool HasPopCnt = true;
    #else
constexpr bool HasPopCnt = false;
    #endif

    #ifdef USE_PEXT
constexpr bool HasPext = true;
    #else
constexpr bool HasPext = false;
    #endif

    #ifdef IS_64BIT
constexpr bool Is64Bit = true;
    #else
constexpr bool Is64Bit = false;
    #endif

// rps-v6 port: a 9x9 board of 81 squares needs a 128-bit bitboard.
using Key      = u64;
using Bitboard = u128;

constexpr int MAX_MOVES = 256;
constexpr int MAX_PLY   = 246;

enum Color : u8 {
    WHITE,  // Blue: moves first, home a1, goal i9
    BLACK,  // Red: home i9, goal a1
    COLOR_NB = 2
};

enum Bound : u8 {
    BOUND_NONE,
    BOUND_UPPER,
    BOUND_LOWER,
    BOUND_EXACT = BOUND_UPPER | BOUND_LOWER
};

// Value is used as an alias for int, this is done to differentiate between a search
// value and any other integer value. The values used in search are always supposed
// to be in the range (-VALUE_NONE, VALUE_NONE] and should not exceed this range.
using Value = int;

constexpr Value VALUE_ZERO     = 0;
constexpr Value VALUE_DRAW     = 0;
constexpr Value VALUE_NONE     = 32002;
constexpr Value VALUE_INFINITE = 32001;

constexpr Value VALUE_MATE             = 32000;
constexpr Value VALUE_MATE_IN_MAX_PLY  = VALUE_MATE - MAX_PLY;
constexpr Value VALUE_MATED_IN_MAX_PLY = -VALUE_MATE_IN_MAX_PLY;

constexpr Value VALUE_TB                 = VALUE_MATE_IN_MAX_PLY - 1;
constexpr Value VALUE_TB_WIN_IN_MAX_PLY  = VALUE_TB - MAX_PLY;
constexpr Value VALUE_TB_LOSS_IN_MAX_PLY = -VALUE_TB_WIN_IN_MAX_PLY;


constexpr bool is_valid(Value value) { return value != VALUE_NONE; }

constexpr bool is_win(Value value) {
    assert(is_valid(value));
    return value >= VALUE_TB_WIN_IN_MAX_PLY;
}

constexpr bool is_loss(Value value) {
    assert(is_valid(value));
    return value <= VALUE_TB_LOSS_IN_MAX_PLY;
}

constexpr bool is_decisive(Value value) { return is_win(value) || is_loss(value); }

constexpr bool is_mate(Value value) {
    assert(is_valid(value));
    return value >= VALUE_MATE_IN_MAX_PLY;
}

constexpr bool is_mated(Value value) {
    assert(is_valid(value));
    return value <= VALUE_MATED_IN_MAX_PLY;
}

constexpr bool is_mate_or_mated(Value value) { return is_mate(value) || is_mated(value); }

constexpr Value mate_in(int ply) { return VALUE_MATE - ply; }

constexpr Value mated_in(int ply) { return -VALUE_MATE + ply; }

// All three piece types are worth the same; the scale matches chess pawn
// units so that search margins keep their meaning.
constexpr Value StoneValue = 208;
constexpr Value PawnValue  = StoneValue;

// clang-format off
enum PieceType : u8 {
    NO_PIECE_TYPE, ROCK, PAPER, SCISSORS,
    ALL_PIECES = 0,
    PIECE_TYPE_NB = 8
};

enum Piece : u8 {
    NO_PIECE,
    W_ROCK = ROCK,     W_PAPER, W_SCISSORS,
    B_ROCK = ROCK + 8, B_PAPER, B_SCISSORS,
    PIECE_NB = 16
};

constexpr Value PieceValue[PIECE_NB] = {
  VALUE_ZERO, StoneValue, StoneValue, StoneValue, VALUE_ZERO, VALUE_ZERO, VALUE_ZERO, VALUE_ZERO,
  VALUE_ZERO, StoneValue, StoneValue, StoneValue, VALUE_ZERO, VALUE_ZERO, VALUE_ZERO, VALUE_ZERO};
// clang-format on

// Rock beats scissors, scissors beat paper, paper beats rock.
constexpr PieceType prey_of(PieceType pt) { return pt == ROCK ? SCISSORS : PieceType(pt - 1); }
constexpr PieceType predator_of(PieceType pt) { return pt == SCISSORS ? ROCK : PieceType(pt + 1); }

using Depth = int;

// The following DEPTH_ constants are used for transposition table entries
// and quiescence search move generation stages. In regular search, the
// depth stored in the transposition table is literal: the search depth
// (effort) used to make the corresponding transposition table value. In
// quiescence search, however, the transposition table entries only store
// the current quiescence move generation stage (which should thus compare
// lower than any regular search depth).
constexpr Depth DEPTH_QS = 0;
// For transposition table entries where no searching at all was done
// (whether regular or qsearch) we use DEPTH_UNSEARCHED, which should thus
// compare lower than any quiescence or regular depth. DEPTH_ENTRY_OFFSET
// is used only for the transposition table entry occupancy check (see tt.cpp),
// and should thus be lower than DEPTH_UNSEARCHED.
constexpr Depth DEPTH_UNSEARCHED = -2;
constexpr Depth DEPTH_NONE       = -3;

enum Square : u8 {
    SQ_A1 = 0,
    SQ_B1 = 1,
    SQ_I1 = 8,
    SQ_A2 = 9,
    SQ_A9 = 72,
    SQ_I9 = 80,
    SQ_NONE,

    SQUARE_ZERO = 0,
    SQUARE_NB   = 81
};

enum Direction : i8 {
    NORTH = 9,
    EAST  = 1,
    SOUTH = -NORTH,
    WEST  = -EAST,

    NORTH_EAST = NORTH + EAST,
    SOUTH_EAST = SOUTH + EAST,
    SOUTH_WEST = SOUTH + WEST,
    NORTH_WEST = NORTH + WEST
};

enum File : u8 {
    FILE_A,
    FILE_B,
    FILE_C,
    FILE_D,
    FILE_E,
    FILE_F,
    FILE_G,
    FILE_H,
    FILE_I,
    FILE_NB
};

enum Rank : u8 {
    RANK_1,
    RANK_2,
    RANK_3,
    RANK_4,
    RANK_5,
    RANK_6,
    RANK_7,
    RANK_8,
    RANK_9,
    RANK_NB
};

    #define ENABLE_INCR_OPERATORS_ON(T) \
        constexpr T& operator++(T& d) { return d = T(int(d) + 1); } \
        constexpr T& operator--(T& d) { return d = T(int(d) - 1); }

ENABLE_INCR_OPERATORS_ON(PieceType)
ENABLE_INCR_OPERATORS_ON(Square)
ENABLE_INCR_OPERATORS_ON(File)
ENABLE_INCR_OPERATORS_ON(Rank)

    #undef ENABLE_INCR_OPERATORS_ON

constexpr Direction operator+(Direction d1, Direction d2) { return Direction(int(d1) + int(d2)); }
constexpr Direction operator*(int i, Direction d) { return Direction(i * int(d)); }

// Additional operators to add a Direction to a Square
constexpr Square  operator+(Square s, Direction d) { return Square(int(s) + int(d)); }
constexpr Square  operator-(Square s, Direction d) { return Square(int(s) - int(d)); }
constexpr Square& operator+=(Square& s, Direction d) { return s = s + d; }
constexpr Square& operator-=(Square& s, Direction d) { return s = s - d; }

// Toggle color
constexpr Color operator~(Color c) { return Color(c ^ BLACK); }

// Swap color of piece B_ROCK <-> W_ROCK
constexpr Piece operator~(Piece pc) { return Piece(pc ^ 8); }

constexpr Square make_square(File f, Rank r) { return Square(r * 9 + f); }

constexpr Piece make_piece(Color c, PieceType pt) { return Piece((c << 3) + pt); }

constexpr PieceType type_of(Piece pc) { return PieceType(pc & 7); }

constexpr Color color_of(Piece pc) {
    assert(pc != NO_PIECE);
    return Color(pc >> 3);
}

constexpr bool is_ok(Square s) { return s >= SQ_A1 && s <= SQ_I9; }

constexpr File file_of(Square s) { return File(s % 9); }

constexpr Rank rank_of(Square s) { return Rank(s / 9); }

// The corner each side must reach, and the one it starts next to.
constexpr Square goal_square(Color c) { return c == WHITE ? SQ_I9 : SQ_A1; }
constexpr Square home_square(Color c) { return goal_square(~c); }

// Based on a congruential pseudo-random number generator
constexpr Key make_key(u64 seed) { return seed * 6364136223846793005ULL + 1442695040888963407ULL; }

// A move needs 14 bits to be stored
//
// bit  0- 6: destination square (from 0 to 80)
// bit  7-13: origin square (from 0 to 80)
//
// Special cases are Move::none() and Move::null(). We can sneak these in because
// in any normal move the destination square and origin square are always different.
class Move {
   public:
    Move() = default;
    constexpr explicit Move(u16 d) :
        data(d) {}

    constexpr Move(Square from, Square to) :
        data((from << 7) + to) {}

    constexpr Square from_sq() const {
        assert(is_ok());
        return Square((data >> 7) & 0x7F);
    }

    constexpr Square to_sq() const {
        assert(is_ok());
        return Square(data & 0x7F);
    }

    constexpr bool is_ok() const { return none().data != data && null().data != data; }

    static constexpr Move null() { return Move(129); }
    static constexpr Move none() { return Move(0); }

    constexpr bool operator==(const Move& m) const { return data == m.data; }
    constexpr bool operator!=(const Move& m) const { return data != m.data; }

    constexpr explicit operator bool() const { return data != 0; }

    constexpr u16 raw() const { return data; }

    struct MoveHash {
        usize operator()(const Move& m) const { return make_key(m.data); }
    };

    static constexpr int FromSqShift = 7;
    static constexpr int ToSqShift   = 0;

   protected:
    u16 data;
};

}  // namespace Stockfish

#endif  // #ifndef TYPES_H_INCLUDED

#include "tune.h"  // Global visibility to tuning setup
