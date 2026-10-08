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

// rps-v6 port of position.cpp. The rules follow the site's rps-v6 variant.

#include "position.h"

#include <cassert>
#include <cctype>
#include <cstddef>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string_view>

#include "bitboard.h"
#include "history.h"
#include "misc.h"
#include "tt.h"
#include "uci.h"

using std::string;

namespace Stockfish {

namespace Zobrist {

Key psq[PIECE_NB][SQUARE_NB];
Key side;
}

namespace {

constexpr std::string_view PieceToChar(" RPS     rps");

constexpr PieceType Types[] = {ROCK, PAPER, SCISSORS};

}  // namespace


// Returns an ASCII representation of the position
std::ostream& operator<<(std::ostream& os, const Position& pos) {

    const char* line = "\n +---+---+---+---+---+---+---+---+---+";
    os << line << "\n";

    for (int r = RANK_9; r >= RANK_1; --r)
    {
        for (int f = FILE_A; f <= FILE_I; ++f)
            os << " | " << PieceToChar[pos.piece_on(make_square(File(f), Rank(r)))];

        os << " | " << (1 + r) << line << "\n";
    }

    os << "   a   b   c   d   e   f   g   h   i\n"
       << "\nFen: " << pos.fen() << "\nKey: " << std::hex << std::uppercase << std::setfill('0')
       << std::setw(16) << pos.key() << std::setfill(' ') << std::dec << "\nThreats against side to move: ";

    for (Bitboard b = pos.checkers(); b;)
        os << UCIEngine::square(pop_lsb(b)) << " ";

    return os;
}


// Initializes at startup the various arrays used to compute hash keys
void Position::init() {

    PRNG rng(1070372);

    for (Piece pc : {W_ROCK, W_PAPER, W_SCISSORS, B_ROCK, B_PAPER, B_SCISSORS})
        for (int s = 0; s < SQUARE_NB; ++s)
            Zobrist::psq[pc][s] = rng.rand<Key>();

    Zobrist::side = rng.rand<Key>();
}


// Initializes the position object with the given FEN string. Ranks are
// listed from 9 down to 1; uppercase is Blue (white). After the side to move,
// an optional castling and en passant field ('-') is skipped, then the
// capture clock (plies since the last capture) and the move number.
std::optional<PositionSetError>
Position::set(const string& fenStr, bool isChess960, StateInfo* si) {

    (void) isChess960;

    std::memset(reinterpret_cast<char*>(this), 0, sizeof(Position));
    std::memset(si, 0, sizeof(StateInfo));
    st = si;

    std::istringstream ss(fenStr);
    ss >> std::noskipws;

    unsigned char token;
    int           r = RANK_9, f = FILE_A;

    while ((ss >> token) && !isspace(token))
    {
        if (isdigit(token))
            f += token - '0';
        else if (token == '/')
        {
            if (f != FILE_NB)
                return PositionSetError("rank " + std::to_string(r + 1) + " does not cover 9 files");
            --r;
            f = FILE_A;
        }
        else
        {
            size_t idx = PieceToChar.find(token);
            if (idx == string::npos || token == ' ')
                return PositionSetError(string("unknown piece '") + char(token) + "'");
            if (r < 0 || f >= FILE_NB)
                return PositionSetError("piece outside the board");
            put_piece(Piece(idx), make_square(File(f), Rank(r)));
            ++f;
        }
        if (f > FILE_NB)
            return PositionSetError("rank overflow");
    }

    if (r != RANK_1 || f != FILE_NB)
        return PositionSetError("board field does not cover 9x9");

    ss >> token;
    sideToMove = (token == 'b' ? BLACK : WHITE);

    ss >> std::skipws;
    string castle, ep;
    int    clock = 0, moveNo = 1;
    ss >> castle >> ep >> clock >> moveNo;

    st->rule50 = std::max(0, clock);
    gamePly    = std::max(2 * (moveNo - 1), 0) + (sideToMove == BLACK);

    set_state();

    assert(pos_is_ok());

    return {};
}


// Sets the goal-threat information for the side to move: which enemy pieces
// could enter our home corner next move, and which of our piece types would
// threaten our goal from its ring.
void Position::set_check_info() const {

    Color us = sideToMove;

    st->checkersBB = threats(~us);

    for (int pt = 0; pt < PIECE_TYPE_NB; ++pt)
        st->checkSquares[pt] = 0;

    Piece occ = board[goal_square(us)];
    if (occ == NO_PIECE)
        for (PieceType pt : Types)
            st->checkSquares[pt] = goal_ring(us);
    else if (color_of(occ) != us)
        st->checkSquares[predator_of(type_of(occ))] = goal_ring(us);
}


// Computes the hash keys of the position, and other data that once computed
// is updated incrementally as moves are made.
void Position::set_state() const {

    st->key = st->pawnKey = st->minorPieceKey = 0;
    st->nonPawnKey[WHITE] = st->nonPawnKey[BLACK] = 0;
    st->nonPawnMaterial[WHITE] = st->nonPawnMaterial[BLACK] = VALUE_ZERO;
    st->capturedPiece                                      = NO_PIECE;
    st->repetition                                         = 0;
    st->previous                                           = nullptr;

    for (Bitboard b = pieces(); b;)
    {
        Square s  = pop_lsb(b);
        Piece  pc = piece_on(s);
        st->key ^= Zobrist::psq[pc][s];
        st->nonPawnKey[color_of(pc)] ^= Zobrist::psq[pc][s];
        st->nonPawnMaterial[color_of(pc)] += PieceValue[pc];

        if (type_of(pc) == ROCK)
            st->pawnKey ^= Zobrist::psq[pc][s];
        else if (type_of(pc) == PAPER)
            st->minorPieceKey ^= Zobrist::psq[pc][s];
    }

    if (sideToMove == BLACK)
        st->key ^= Zobrist::side;

    set_check_info();
}


// Returns a FEN representation of the position
string Position::fen() const {

    int                emptyCnt;
    std::ostringstream ss;

    for (int r = RANK_9; r >= RANK_1; --r)
    {
        for (int f = FILE_A; f <= FILE_I; ++f)
        {
            for (emptyCnt = 0; f <= FILE_I && empty(make_square(File(f), Rank(r))); ++f)
                ++emptyCnt;

            if (emptyCnt)
                ss << emptyCnt;

            if (f <= FILE_I)
                ss << PieceToChar[piece_on(make_square(File(f), Rank(r)))];
        }

        if (r > RANK_1)
            ss << '/';
    }

    ss << (sideToMove == WHITE ? " w " : " b ") << "- - " << st->rule50 << " "
       << 1 + (gamePly - (sideToMove == BLACK)) / 2;

    return ss.str();
}


// True if c has at least one move: a king step onto an empty square or onto
// a piece of the type its own pieces beat.
bool Position::has_move(Color c) const {

    Bitboard empties = AllSquares & ~pieces();

    for (PieceType pt : Types)
        if (king_steps(pieces(c, pt)) & (empties | pieces(~c, prey_of(pt))))
            return true;

    return false;
}


// Every move is legal once it is pseudo-legal: there is no king to expose.
bool Position::legal(Move m) const {

    assert(m.is_ok());
    assert(pseudo_legal(m));
    (void) m;
    return true;
}


// Takes a random move and tests whether the move is pseudo-legal. It is used
// to validate moves from TT that can be corrupted due to SMP concurrent access
// or hash position key aliasing.
bool Position::pseudo_legal(const Move m) const {

    Color  us   = sideToMove;
    Square from = m.from_sq();
    Square to   = m.to_sq();

    if (from >= SQUARE_NB || to >= SQUARE_NB)
        return false;

    Piece pc = moved_piece(m);

    if (pc == NO_PIECE || color_of(pc) != us)
        return false;

    if (!(step_bb(from) & to))
        return false;

    Piece target = piece_on(to);
    if (target != NO_PIECE && (color_of(target) == us || type_of(target) != prey_of(type_of(pc))))
        return false;

    return true;
}


// Tests whether a pseudo-legal move leaves one of our pieces on the ring of
// our goal able to enter it next move (the rps-v6 notion of "check").
bool Position::gives_check(Move m) const {

    assert(m.is_ok());

    Color  us   = sideToMove;
    Square from = m.from_sq();
    Square to   = m.to_sq();
    Square goal = goal_square(us);
    Piece  occ  = board[goal];

    if (to == goal || (occ != NO_PIECE && color_of(occ) == us))
        return false;

    PieceType hunter = occ != NO_PIECE ? predator_of(type_of(occ)) : NO_PIECE_TYPE;
    Bitboard  ours   = hunter ? pieces(us, hunter) : pieces(us);

    if (!hunter || type_of(piece_on(from)) == hunter)
        ours ^= square_bb(from) ^ square_bb(to);

    return ours & goal_ring(us);
}


// Makes a move, and saves all information necessary to a StateInfo object.
// If a pointer to the TT table is passed, the entry for the new position will
// be prefetched, and likewise for shared history.
void Position::do_move(Move                      m,
                       StateInfo&                newSt,
                       bool                      givesCheck,
                       const TranspositionTable* tt      = nullptr,
                       const SharedHistories*    history = nullptr) {

    assert(m.is_ok());
    assert(&newSt != st);
    (void) givesCheck;

    Key k = st->key ^ Zobrist::side;

    // Copy some fields of the old state to our new StateInfo object except the
    // ones which are going to be recalculated from scratch anyway and then switch
    // our state pointer to point to the new (ready to be updated) state.
    std::memcpy(&newSt, st, offsetof(StateInfo, key));
    newSt.previous = st;
    st             = &newSt;

    ++gamePly;
    ++st->rule50;
    ++st->pliesFromNull;

    Color  us       = sideToMove;
    Color  them     = ~us;
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Piece  captured = piece_on(to);

    assert(color_of(pc) == us);
    assert(captured == NO_PIECE || color_of(captured) == them);

    if (captured)
    {
        k ^= Zobrist::psq[captured][to];
        st->nonPawnKey[them] ^= Zobrist::psq[captured][to];
        st->nonPawnMaterial[them] -= PieceValue[captured];

        if (type_of(captured) == ROCK)
            st->pawnKey ^= Zobrist::psq[captured][to];
        else if (type_of(captured) == PAPER)
            st->minorPieceKey ^= Zobrist::psq[captured][to];

        remove_piece(to);

        // Reset rule 50 counter
        st->rule50 = 0;
    }

    // Update hash keys
    Key fromTo = Zobrist::psq[pc][from] ^ Zobrist::psq[pc][to];
    k ^= fromTo;
    st->nonPawnKey[us] ^= fromTo;

    if (type_of(pc) == ROCK)
        st->pawnKey ^= fromTo;
    else if (type_of(pc) == PAPER)
        st->minorPieceKey ^= fromTo;

    if (tt)
        prefetch(tt->first_entry(adjust_key50(k)));

    // Update the key with the final value
    st->key = k;

    if (history)
    {
        prefetch(&history->pawn_entry(*this)[pc][to]);
        prefetch(&history->pawn_correction_entry(*this));
        prefetch(&history->minor_piece_correction_entry(*this));
        prefetch(&history->nonpawn_correction_entry<WHITE>(*this));
        prefetch(&history->nonpawn_correction_entry<BLACK>(*this));
    }

    move_piece(from, to);

    st->capturedPiece = captured;

    sideToMove = them;

    set_check_info();

    st->repetition = 0;

    assert(pos_is_ok());
}


// Unmakes a move. When it returns, the position should
// be restored to exactly the same state as before the move was made.
void Position::undo_move(Move m) {

    assert(m.is_ok());

    sideToMove = ~sideToMove;

    Square from = m.from_sq();
    Square to   = m.to_sq();

    assert(empty(from));

    move_piece(to, from);

    if (st->capturedPiece)
        put_piece(st->capturedPiece, to);

    // Finally point our state pointer back to the previous state
    st = st->previous;
    --gamePly;

    assert(pos_is_ok());
}


// Used to do a "null move": it flips
// the side to move without executing any move on the board.
void Position::do_null_move(StateInfo& newSt) {

    assert(!checkers());
    assert(&newSt != st);

    std::memcpy(&newSt, st, sizeof(StateInfo));

    newSt.previous = st;
    st             = &newSt;

    st->key ^= Zobrist::side;

    st->pliesFromNull = 0;

    st->capturedPiece = NO_PIECE;

    sideToMove = ~sideToMove;

    set_check_info();

    st->repetition = 0;

    assert(pos_is_ok());
}


// Must be used to undo a "null move"
void Position::undo_null_move() {

    assert(!checkers());

    st         = st->previous;
    sideToMove = ~sideToMove;
}


// Computes the new hash key after the given move. Needed for speculative
// prefetch. It doesn't recognize special moves like castling, en passant and
// promotions.
Key Position::prefetch_key(Move m) const {
    Square from     = m.from_sq();
    Square to       = m.to_sq();
    Piece  pc       = piece_on(from);
    Piece  captured = piece_on(to);
    Key    k        = st->key ^ Zobrist::side;

    k ^= Zobrist::psq[captured][to] ^ Zobrist::psq[pc][to] ^ Zobrist::psq[pc][from];

    if (captured)
        return k;

    return adjust_key50<true>(k);
}


// Tests if the SEE (Static Exchange Evaluation) value of move is greater or
// equal to the given threshold. Captures on a square can only be answered by
// the predator of whatever now stands there, stepping in from an adjacent square.
bool Position::see_ge(Move m, int threshold) const {

    assert(m.is_ok());

    Square from = m.from_sq(), to = m.to_sq();

    int swap = PieceValue[piece_on(to)] - threshold;
    if (swap < 0)
        return false;

    swap = PieceValue[piece_on(from)] - swap;
    if (swap <= 0)
        return true;

    Bitboard  around    = step_bb(to);
    Bitboard  available = pieces() ^ from;
    PieceType occupant  = type_of(piece_on(from));
    Color     stm       = color_of(piece_on(from));
    int       res       = 1;

    while (true)
    {
        stm              = ~stm;
        PieceType hunter = predator_of(occupant);
        Bitboard  att    = around & pieces(stm, hunter) & available;

        if (!att)
            break;

        res ^= 1;

        if ((swap = StoneValue - swap) < res)
            break;

        available ^= lsb(att);
        occupant = hunter;
    }

    return bool(res);
}


// Performs some consistency checks for the position object
// and raise an assert if something wrong is detected.
// This is meant to be helpful when debugging.
bool Position::pos_is_ok() const {

    constexpr bool Fast = true;  // Quick (default) or full check?

    if (sideToMove != WHITE && sideToMove != BLACK)
        assert(0 && "pos_is_ok: Default");

    if (Fast)
        return true;

    if ((pieces(WHITE) & pieces(BLACK)) || (pieces(WHITE) | pieces(BLACK)) != pieces())
        assert(0 && "pos_is_ok: Bitboards");

    for (PieceType p1 : Types)
        for (PieceType p2 : Types)
            if (p1 != p2 && (pieces(p1) & pieces(p2)))
                assert(0 && "pos_is_ok: Bitboards");

    for (Piece pc : {W_ROCK, W_PAPER, W_SCISSORS, B_ROCK, B_PAPER, B_SCISSORS})
        if (pieceCount[pc] != popcount(pieces(color_of(pc), type_of(pc))))
            assert(0 && "pos_is_ok: Pieces");

    return true;
}

}  // namespace Stockfish
