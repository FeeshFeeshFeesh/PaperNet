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

// rps-v6 port: the board, rules and incremental state for the 9x9
// rock-paper-scissors race game. Each side has 3 rocks, 4 papers and 3
// scissors that make king steps onto an empty square or onto their prey.
// Entering your goal corner wins; a side with no move (or no pieces) loses;
// 200 plies without a capture draw. There is no repetition rule.

#ifndef POSITION_H_INCLUDED
#define POSITION_H_INCLUDED

#include <array>
#include <cassert>
#include <deque>
#include <iosfwd>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

#include "bitboard.h"
#include "types.h"

namespace Stockfish {

class TranspositionTable;
struct SharedHistories;

// Plies without a capture that draw the game
constexpr int CAPTURE_CLOCK = 200;

// StateInfo struct stores information needed to restore a Position object to
// its previous state when we retract a move. Whenever a move is made on the
// board (by calling Position::do_move), a StateInfo object must be passed.

struct StateInfo {

    // Copied when making a move
    Key   pawnKey;        // rocks
    Key   minorPieceKey;  // papers
    Key   nonPawnKey[COLOR_NB];
    Value nonPawnMaterial[COLOR_NB];
    int   rule50;
    int   pliesFromNull;

    // Not copied when making a move (will be recomputed anyhow)
    Key        key;
    Bitboard   checkersBB;
    StateInfo* previous;
    Bitboard   checkSquares[PIECE_TYPE_NB];
    Piece      capturedPiece;
    int        repetition;
};


// A list to keep track of the position states along the setup moves (from the
// start position to the position just before the search starts). Needed by
// 'draw by repetition' detection. Use a std::deque because pointers to
// elements are not invalidated upon list resizing.
using StateListPtr = std::unique_ptr<std::deque<StateInfo>>;

struct PositionSetError: std::runtime_error {
    using std::runtime_error::runtime_error;
};

class Position {
   public:
    static void init();

    Position()                           = default;
    Position(const Position&)            = delete;
    Position& operator=(const Position&) = delete;

    // FEN string input/output
    std::optional<PositionSetError> set(const std::string& fenStr, bool isChess960, StateInfo* si);
    std::string                     fen() const;

    // Position representation
    Bitboard pieces() const;  // All pieces
    template<typename... PieceTypes>
    Bitboard pieces(PieceTypes... pts) const;
    Bitboard pieces(Color c) const;
    template<typename... PieceTypes>
    Bitboard                            pieces(Color c, PieceTypes... pts) const;
    Piece                               piece_on(Square s) const;
    const std::array<Piece, SQUARE_NB>& piece_array() const;
    bool                                empty(Square s) const;
    template<PieceType Pt>
    int count(Color c) const;
    template<PieceType Pt>
    int count() const;

    // Goal threats ("checks"): pieces of c next to c's goal that can enter it
    Bitboard threats(Color c) const;
    Bitboard checkers() const;
    Bitboard check_squares(PieceType pt) const;
    Bitboard attacks_by(Color c, PieceType pt) const;

    // Rules
    bool has_move(Color c) const;
    bool reached_goal(Color c) const;

    // Properties of moves
    bool  legal(Move m) const;
    bool  pseudo_legal(const Move m) const;
    bool  capture(Move m) const;
    bool  capture_stage(Move m) const;
    bool  gives_check(Move m) const;
    Piece moved_piece(Move m) const;
    Piece captured_piece() const;

    // Doing and undoing moves
    void do_move(Move m, StateInfo& newSt, const TranspositionTable* tt = nullptr);
    void do_move(Move                      m,
                 StateInfo&                newSt,
                 bool                      givesCheck,
                 const TranspositionTable* tt,
                 const SharedHistories*    history);
    void undo_move(Move m);
    void do_null_move(StateInfo& newSt);
    void undo_null_move();

    // Static Exchange Evaluation
    bool see_ge(Move m, int threshold = 0) const;

    // Accessing hash keys
    Key key() const;
    Key prefetch_key(Move m) const;
    Key pawn_key() const;
    Key minor_piece_key() const;
    Key non_pawn_key(Color c) const;

    // Other properties of the position
    Color side_to_move() const;
    int   game_ply() const;
    bool  is_chess960() const { return false; }
    bool  is_draw(int ply) const;
    bool  is_repetition(int) const { return false; }
    bool  upcoming_repetition(int) const { return false; }
    int   rule50_count() const;
    Value non_pawn_material(Color c) const;
    Value non_pawn_material() const;

    // Position consistency check, for debugging
    bool pos_is_ok() const;

    StateInfo* state() const;

    void put_piece(Piece pc, Square s);
    void remove_piece(Square s);

   private:
    // Initialization helpers (used while setting up a position)
    void set_state() const;
    void set_check_info() const;

    // Other helpers
    void move_piece(Square from, Square to);
    template<bool AfterMove = false>
    Key adjust_key50(Key k) const;

    // Data members
    std::array<Piece, SQUARE_NB>        board;
    std::array<Bitboard, PIECE_TYPE_NB> byTypeBB;
    std::array<Bitboard, COLOR_NB>      byColorBB;
    int                                 pieceCount[PIECE_NB];
    StateInfo*                          st;
    int                                 gamePly;
    Color                               sideToMove;
};

std::ostream& operator<<(std::ostream& os, const Position& pos);

inline Color Position::side_to_move() const { return sideToMove; }

inline Piece Position::piece_on(Square s) const {
    assert(is_ok(s));
    return board[s];
}

inline const std::array<Piece, SQUARE_NB>& Position::piece_array() const { return board; }

inline bool Position::empty(Square s) const { return piece_on(s) == NO_PIECE; }

inline Piece Position::moved_piece(Move m) const { return piece_on(m.from_sq()); }

inline Bitboard Position::pieces() const { return byTypeBB[ALL_PIECES]; }

template<typename... PieceTypes>
inline Bitboard Position::pieces(PieceTypes... pts) const {
    return (byTypeBB[pts] | ...);
}

inline Bitboard Position::pieces(Color c) const { return byColorBB[c]; }

template<typename... PieceTypes>
inline Bitboard Position::pieces(Color c, PieceTypes... pts) const {
    return pieces(c) & pieces(pts...);
}

template<PieceType Pt>
inline int Position::count(Color c) const {
    return pieceCount[make_piece(c, Pt)];
}

template<PieceType Pt>
inline int Position::count() const {
    return count<Pt>(WHITE) + count<Pt>(BLACK);
}

inline bool Position::reached_goal(Color c) const { return pieces(c) & goal_square(c); }

// Pieces of c that could enter c's goal right now. None if c already stands
// on it; if the opponent blocks it, only the blocker's predators qualify.
inline Bitboard Position::threats(Color c) const {
    Piece occ = board[goal_square(c)];
    if (occ == NO_PIECE)
        return goal_ring(c) & pieces(c);
    if (color_of(occ) == c)
        return 0;
    return goal_ring(c) & pieces(c, predator_of(type_of(occ)));
}

inline Bitboard Position::attacks_by(Color c, PieceType pt) const {
    return king_steps(pieces(c, pt));
}

inline Bitboard Position::checkers() const { return st->checkersBB; }

inline Bitboard Position::check_squares(PieceType pt) const { return st->checkSquares[pt]; }

inline Key Position::key() const { return adjust_key50(st->key); }

template<bool AfterMove>
inline Key Position::adjust_key50(Key k) const {
    return st->rule50 < 28 - AfterMove ? k : k ^ make_key((st->rule50 - (28 - AfterMove)) / 16);
}

inline Key Position::pawn_key() const { return st->pawnKey; }

inline Key Position::minor_piece_key() const { return st->minorPieceKey; }

inline Key Position::non_pawn_key(Color c) const { return st->nonPawnKey[c]; }

inline Value Position::non_pawn_material(Color c) const { return st->nonPawnMaterial[c]; }

inline Value Position::non_pawn_material() const {
    return non_pawn_material(WHITE) + non_pawn_material(BLACK);
}

inline int Position::game_ply() const { return gamePly; }

inline int Position::rule50_count() const { return st->rule50; }

inline bool Position::is_draw(int) const { return st->rule50 >= CAPTURE_CLOCK; }

inline bool Position::capture(Move m) const {
    assert(m.is_ok());
    return !empty(m.to_sq());
}

// Captures and goal entries are the tactical moves searched by qsearch
inline bool Position::capture_stage(Move m) const {
    assert(m.is_ok());
    return !empty(m.to_sq()) || m.to_sq() == goal_square(sideToMove);
}

inline Piece Position::captured_piece() const { return st->capturedPiece; }

inline void Position::put_piece(Piece pc, Square s) {

    board[s] = pc;
    byTypeBB[ALL_PIECES] |= byTypeBB[type_of(pc)] |= s;
    byColorBB[color_of(pc)] |= s;
    pieceCount[pc]++;
    pieceCount[make_piece(color_of(pc), ALL_PIECES)]++;
}

inline void Position::remove_piece(Square s) {

    Piece pc = board[s];
    byTypeBB[ALL_PIECES] ^= s;
    byTypeBB[type_of(pc)] ^= s;
    byColorBB[color_of(pc)] ^= s;
    board[s] = NO_PIECE;
    pieceCount[pc]--;
    pieceCount[make_piece(color_of(pc), ALL_PIECES)]--;
}

inline void Position::move_piece(Square from, Square to) {

    Piece    pc     = board[from];
    Bitboard fromTo = from | to;
    byTypeBB[ALL_PIECES] ^= fromTo;
    byTypeBB[type_of(pc)] ^= fromTo;
    byColorBB[color_of(pc)] ^= fromTo;
    board[from] = NO_PIECE;
    board[to]   = pc;
}

inline void Position::do_move(Move m, StateInfo& newSt, const TranspositionTable* tt) {
    do_move(m, newSt, gives_check(m), tt, nullptr);
}

inline StateInfo* Position::state() const { return st; }

}  // namespace Stockfish

#endif  // #ifndef POSITION_H_INCLUDED
