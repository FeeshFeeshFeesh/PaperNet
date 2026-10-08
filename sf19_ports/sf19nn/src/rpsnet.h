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

// rps-v6 port: inference for sploppy's RPSNNUE1 networks (formats 6, 8 and 9).
// The file format and the arithmetic follow models/nnue/src/net.rs of
// https://github.com/Cmiller132/intransitive_bot_research (MIT, Copyright (c)
// 2026 Cmiller132; see LICENSE-sploppy-MIT). This is a C++ re-implementation
// with a Stockfish-style lazy accumulator: do_move records the changed feature
// rows, evaluate applies them from the nearest computed ancestor.

#ifndef RPSNET_H_INCLUDED
#define RPSNET_H_INCLUDED

#include <cstdint>
#include <string>

#include "types.h"

namespace Stockfish {

class Position;
struct StateInfo;

namespace RpsNet {

constexpr int MaxHidden = 1024;
constexpr int MaxRows   = 40;  // changed rows of one kind per perspective per move

// Rows that changed between the previous state and this one, per perspective
struct Dirty {
    uint32_t gen;  // generation of the network it was computed for, 0 = none
    uint8_t  refresh[COLOR_NB];
    uint8_t  nAdd[COLOR_NB], nSub[COLOR_NB];
    uint16_t add[COLOR_NB][MaxRows], sub[COLOR_NB][MaxRows];
};

// One half per perspective (WHITE, BLACK); the side to move reads its own half first
struct Accumulator {
    uint32_t gen[COLOR_NB];
    alignas(64) int32_t v[COLOR_NB][MaxHidden];
};

bool        loaded();
uint32_t    generation();
std::string load(const std::string& path);  // "" on success, else the error
std::string describe();

extern int  scalePercent;  // search value = net value * scalePercent / 100
extern bool verify;        // recompute every evaluation from scratch and compare

// Called by Position::do_move after the board has been updated. attBefore holds
// the attacked pieces of each colour and type before the move.
void record_move(const Position& pos, StateInfo* st, Piece pc, Square from, Square to,
                 Piece captured, const Bitboard attBefore[COLOR_NB][4], int rule50Before);
void attacked(const Position& pos, Bitboard att[COLOR_NB][4]);

// Static evaluation of the side to move in the net's search units (600 = raw 1.0)
int evaluate(const Position& pos);

// Diagnostics for `eval`: hidden size, and dense inputs that quantize to zero
int hidden();
int zero_inputs(const Position& pos);

}  // namespace RpsNet
}  // namespace Stockfish

#endif  // #ifndef RPSNET_H_INCLUDED
