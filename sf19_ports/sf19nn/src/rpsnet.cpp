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

// rps-v6 port: RPSNNUE1 inference, re-implemented from sploppy's net.rs (MIT,
// Copyright (c) 2026 Cmiller132, see LICENSE-sploppy-MIT).
//
// Each perspective (WHITE, BLACK) sees the board in its own frame: its goal is
// square 80, its pieces are codes 1-3 and the enemy's 4-6. White's frame is the
// board itself; Black's frame reflects squares across the anti-diagonal. Active
// rows of a perspective: a piece-square row per piece (in a context chosen by
// the other colour's material from format 8 on), a row per attacked piece, two
// capture-clock rows and, in format 9, two goal-corner rows.

#include "rpsnet.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <sstream>
#include <vector>

#if defined(__aarch64__)
    #include <arm_neon.h>
#endif

#include "bitboard.h"
#include "position.h"

namespace Stockfish::RpsNet {

int  scalePercent = 100;
bool verify       = false;

namespace {

constexpr int NSq          = 81;
constexpr int Features     = 6 * NSq;
constexpr int ClockBuckets = 16;
constexpr int GoalStates   = 4;
constexpr int Contexts     = 27;
constexpr int DenseWidth   = 32;
constexpr int V6Features   = 1004;
constexpr int V8Features   = Contexts * Features + Features + 2 * ClockBuckets;
constexpr int V9Features   = V8Features + 2 * GoalStates;
constexpr int QA           = 255;
constexpr int QB           = 64;
constexpr int ClockLimit   = 200;
constexpr int RefreshCost  = 30;  // walk back at most this many changed rows
constexpr int ClockBounds[ClockBuckets] = {0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 160};

struct Head {
    std::vector<int16_t> output;  // 2H, own half first
    int32_t              bias;
    std::vector<int8_t>  denseWeights;  // DenseWidth x 2H
    int32_t              denseBias[DenseWidth];
    int16_t              denseOutput[DenseWidth];
    int32_t              rowSum[DenseWidth];  // sum of each dense weight row
    int                  active[DenseWidth];
    int                  activeCount;
};

struct Network {
    int                  version, features, hidden;
    bool                 contexts, goals;
    int                  attackBase, clockBase, goalBase;
    std::vector<int16_t> bias;
    std::vector<int16_t> weights;
    std::vector<Head>    heads;
    std::string          path;
};

Network  net;
bool     isLoaded = false;
uint32_t gen      = 0;

inline int mirror_anti(int s) { return (8 - s % 9) * 9 + (8 - s / 9); }

inline int frame_sq(Color p, Square s) { return p == WHITE ? int(s) : mirror_anti(int(s)); }

// Piece code minus one in the frame of p: 0-2 own rock/paper/scissors, 3-5 enemy
inline int frame_code(Color p, Piece pc) { return int(type_of(pc)) - 1 + (color_of(pc) == p ? 0 : 3); }

inline int clock_bucket(int x) {
    return int(std::upper_bound(ClockBounds, ClockBounds + ClockBuckets, x) - ClockBounds) - 1;
}

inline int context_of(int rocks, int papers, int scissors) {
    return std::min(rocks, 2) + 3 * std::min(papers, 2) + 9 * std::min(scissors, 2);
}

// Context of perspective p: the material of the other colour
int context(const Position& pos, Color p) {
    if (!net.contexts)
        return 0;
    Color o = ~p;
    return context_of(popcount(pos.pieces(o, ROCK)), popcount(pos.pieces(o, PAPER)),
                      popcount(pos.pieces(o, SCISSORS)));
}

inline int piece_row(Color p, Piece pc, Square s, int ctx) {
    return ctx * Features + frame_code(p, pc) * NSq + frame_sq(p, s);
}

inline int attack_row(Color p, Piece pc, Square s) {
    return net.attackBase + frame_code(p, pc) * NSq + frame_sq(p, s);
}

inline int since_row(int since) { return net.clockBase + clock_bucket(since); }

inline int left_row(int since) {
    return net.clockBase + ClockBuckets + clock_bucket(std::max(0, ClockLimit - since));
}

// Goal-corner rows of p: an enemy blocker on p's goal, one of p's pieces on p's home
inline int blocker_row(Color p, Piece onGoal) {
    return net.goalBase + (onGoal != NO_PIECE && color_of(onGoal) != p ? int(type_of(onGoal)) : 0);
}

inline int invader_row(Color p, Piece onHome) {
    return net.goalBase + GoalStates
         + (onHome != NO_PIECE && color_of(onHome) == p ? int(type_of(onHome)) : 0);
}

int active_rows(const Position& pos, Color p, int rows[]) {
    int      n   = 0;
    int      ctx = context(pos, p);
    Bitboard att[COLOR_NB][4];
    attacked(pos, att);

    for (Bitboard b = pos.pieces(); b;)
    {
        Square s = pop_lsb(b);
        rows[n++] = piece_row(p, pos.piece_on(s), s, ctx);
    }
    for (Color c : {WHITE, BLACK})
        for (PieceType t : {ROCK, PAPER, SCISSORS})
            for (Bitboard b = att[c][t]; b;)
                rows[n++] = attack_row(p, make_piece(c, t), pop_lsb(b));

    rows[n++] = since_row(pos.rule50_count());
    rows[n++] = left_row(pos.rule50_count());

    if (net.goals)
    {
        rows[n++] = blocker_row(p, pos.piece_on(goal_square(p)));
        rows[n++] = invader_row(p, pos.piece_on(goal_square(~p)));
    }
    return n;
}

// The hidden size is copied to a local: acc is an int32_t pointer, which could
// alias net.hidden and would otherwise block vectorization. H is a multiple of 32.
inline void add_row(int32_t* acc, int row) {
    const int      H = net.hidden;
    const int16_t* w = net.weights.data() + std::size_t(row) * H;
#if defined(__aarch64__)
    for (int j = 0; j < H; j += 4)
        vst1q_s32(acc + j, vaddw_s16(vld1q_s32(acc + j), vld1_s16(w + j)));
#else
    for (int j = 0; j < H; ++j)
        acc[j] += w[j];
#endif
}

inline void sub_row(int32_t* acc, int row) {
    const int      H = net.hidden;
    const int16_t* w = net.weights.data() + std::size_t(row) * H;
#if defined(__aarch64__)
    for (int j = 0; j < H; j += 4)
        vst1q_s32(acc + j, vsubw_s16(vld1q_s32(acc + j), vld1_s16(w + j)));
#else
    for (int j = 0; j < H; ++j)
        acc[j] -= w[j];
#endif
}

void refresh(const Position& pos, Color p, int32_t* acc) {
    int rows[2 * 44];
    int       n = active_rows(pos, p, rows);
    const int H = net.hidden;
    for (int j = 0; j < H; ++j)
        acc[j] = net.bias[j];
    for (int i = 0; i < n; ++i)
        add_row(acc, rows[i]);
}

// Brings the accumulator half of perspective p of the current state up to date
void ensure(const Position& pos, StateInfo* st, Color p) {

    if (st->accumulator.gen[p] == gen)
        return;

    StateInfo* chain[32];
    int        n = 0, cost = 0;
    StateInfo* s = st;

    while (true)
    {
        const Dirty& d = s->dirty;
        cost += d.nAdd[p] + d.nSub[p];
        if (d.gen != gen || d.refresh[p] || !s->previous || n == 32 || cost > RefreshCost)
        {
            refresh(pos, p, st->accumulator.v[p]);
            st->accumulator.gen[p] = gen;
            return;
        }
        chain[n++] = s;
        s          = s->previous;
        if (s->accumulator.gen[p] == gen)
            break;
    }

    for (int i = n - 1; i >= 0; --i)
    {
        StateInfo*   c   = chain[i];
        const Dirty& d   = c->dirty;
        int32_t*     dst = c->accumulator.v[p];
        std::memcpy(dst, c->previous->accumulator.v[p], sizeof(int32_t) * net.hidden);
        for (int k = 0; k < d.nSub[p]; ++k)
            sub_row(dst, d.sub[p][k]);
        for (int k = 0; k < d.nAdd[p]; ++k)
            add_row(dst, d.add[p][k]);
        c->accumulator.gen[p] = gen;
    }
}

int64_t readout(const int32_t* acc, const int16_t* w) {
    const int H = net.hidden;
#if defined(__aarch64__)
    // x*x <= 65025 times |w| <= 32768 fits in int32 exactly
    const int32x4_t zero = vdupq_n_s32(0), top = vdupq_n_s32(QA);
    int64x2_t       sum  = vdupq_n_s64(0);
    for (int j = 0; j < H; j += 4)
    {
        int32x4_t x = vminq_s32(vmaxq_s32(vld1q_s32(acc + j), zero), top);
        sum = vpadalq_s32(sum, vmulq_s32(vmulq_s32(x, x), vmovl_s16(vld1_s16(w + j))));
    }
    return vgetq_lane_s64(sum, 0) + vgetq_lane_s64(sum, 1);
#else
    int64_t sum = 0;
    for (int j = 0; j < H; ++j)
    {
        int32_t x = std::clamp(acc[j], 0, QA);
        sum += int64_t(x * x) * w[j];
    }
    return sum;
#endif
}

// Dense input (x*x + 127) / 255 for x = clamp(acc, 0, 255), stored minus 128 so it
// fits int8 (the dot product adds 128 * row sum back). The denominator is odd, so
// an exact half tie is impossible; (t + (t >> 8)) >> 8 with t = x*x + 128 equals
// the division for every x in 0..255 (checked exhaustively).
void quantize(const int32_t* acc, int8_t* out) {
    const int H = net.hidden;
#if defined(__aarch64__)
    const uint16x8_t top = vdupq_n_u16(QA), half = vdupq_n_u16(128);
    const uint8x8_t  bias = vdup_n_u8(0x80);
    for (int j = 0; j < H; j += 8)
    {
        uint16x8_t x = vminq_u16(
          vcombine_u16(vqmovun_s32(vld1q_s32(acc + j)), vqmovun_s32(vld1q_s32(acc + j + 4))), top);
        uint16x8_t t = vaddq_u16(vmulq_u16(x, x), half);
        uint16x8_t q = vshrq_n_u16(vaddq_u16(t, vshrq_n_u16(t, 8)), 8);
        vst1_s8(out + j, vreinterpret_s8_u8(veor_u8(vmovn_u16(q), bias)));
    }
#else
    for (int j = 0; j < H; ++j)
    {
        int32_t x = std::clamp(acc[j], 0, QA);
        out[j]    = int8_t(int((x * x + 127) / 255) - 128);
    }
#endif
}

// Sum of in[i] * w[i]; n is a multiple of 64
int32_t dot(const int8_t* in, const int8_t* w, int n) {
#if defined(__aarch64__) && defined(__ARM_FEATURE_DOTPROD)
    int32x4_t s = vdupq_n_s32(0);
    for (int i = 0; i < n; i += 16)
        s = vdotq_s32(s, vld1q_s8(in + i), vld1q_s8(w + i));
    return vaddvq_s32(s);
#else
    int32_t s = 0;
    for (int i = 0; i < n; ++i)
        s += int32_t(in[i]) * int32_t(w[i]);
    return s;
#endif
}

inline int round_div64_clipped(int64_t value) {
    if (value <= 0)
        return 0;
    if (value >= 255 * 64)
        return 255;
    int64_t q = value >> 6, r = value & 63;
    return int(q + int64_t(r > 32 || (r == 32 && (q & 1))));
}

int output(const Position& pos, const Accumulator& acc) {

    const int H   = net.hidden;
    std::size_t b = 0;
    if (net.heads.size() > 1)
        b = std::min<std::size_t>(7, std::size_t(std::max(0, popcount(pos.pieces()) - 2)) * 8 / 19);
    const Head& h = net.heads[b];

    Color          us  = pos.side_to_move();
    const int32_t* own = acc.v[us];
    const int32_t* opp = acc.v[~us];

    int64_t sum = readout(own, h.output.data()) + readout(opp, h.output.data() + H);

    int64_t dense = 0;
    if (h.activeCount)
    {
        alignas(64) int8_t in[2 * MaxHidden];
        quantize(own, in);
        quantize(opp, in + H);
        for (int i = 0; i < h.activeCount; ++i)
        {
            int     r = h.active[i];
            int64_t v = int64_t(dot(in, h.denseWeights.data() + std::size_t(r) * 2 * H, 2 * H))
                      + 128 * int64_t(h.rowSum[r]) + h.denseBias[r];
            dense += int64_t(round_div64_clipped(v)) * h.denseOutput[r];
        }
    }

    const double qa = QA, qb = QB;
    double       raw =
      double(sum) / (qa * qa * qb) + double(h.bias) / qb + double(dense) / (qa * qb);
    double e = std::nearbyint(raw * 600.0);  // ties to even, as round_ties_even
    return int(std::clamp(e, -28000.0, 28000.0));
}

inline uint32_t u32_at(const std::vector<char>& b, std::size_t at) {
    uint32_t v;
    std::memcpy(&v, b.data() + at, 4);
    return v;
}

}  // namespace


bool        loaded() { return isLoaded; }
uint32_t    generation() { return gen; }


void attacked(const Position& pos, Bitboard att[COLOR_NB][4]) {
    for (Color c : {WHITE, BLACK})
    {
        att[c][0] = 0;
        for (PieceType t : {ROCK, PAPER, SCISSORS})
            att[c][t] = pos.pieces(c, t) & king_steps(pos.pieces(~c, predator_of(t)));
    }
}


std::string load(const std::string& path) {

    std::ifstream f(path, std::ios::binary);
    if (!f)
        return "cannot open " + path;
    std::vector<char> b((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());

    if (b.size() < 36 || std::memcmp(b.data(), "RPSNNUE1", 8) != 0)
        return "invalid NNUE header or magic";

    Network n;
    n.version        = int(u32_at(b, 8));
    n.features       = int(u32_at(b, 12));
    n.hidden         = int(u32_at(b, 16));
    uint32_t buckets = u32_at(b, 32);
    float    scale;
    std::memcpy(&scale, b.data() + 28, 4);

    bool okFormat = (n.version == 6 && n.features == V6Features && buckets == 1)
                 || (n.version == 8 && n.features == V8Features && buckets == 1)
                 || (n.version == 9 && n.features == V9Features && buckets == 8);
    if (!okFormat || u32_at(b, 20) != QA || u32_at(b, 24) != QB || scale != 600.0f)
        return "unsupported NNUE format, dimensions, scales or buckets";
    if (n.hidden < 32 || n.hidden > MaxHidden || n.hidden % 32)
        return "hidden width must be a multiple of 32 in 32..1024";

    const std::size_t H          = std::size_t(n.hidden);
    const std::size_t F          = std::size_t(n.features);
    const std::size_t widthField = n.features != V9Features;
    const std::size_t headSize   = 68 * H + 196 + 4 * widthField;
    if (b.size() != H * (2 * F + 2) + 36 + buckets * headSize)
        return "invalid NNUE payload length";

    n.contexts   = n.features >= V8Features;
    n.goals      = n.features == V9Features;
    n.attackBase = n.contexts ? Contexts * Features : Features;
    n.clockBase  = n.attackBase + Features;
    n.goalBase   = n.clockBase + 2 * ClockBuckets;

    n.bias.resize(H);
    std::memcpy(n.bias.data(), b.data() + 36, 2 * H);
    n.weights.resize(F * H);
    std::memcpy(n.weights.data(), b.data() + 36 + 2 * H, 2 * F * H);

    for (std::size_t k = 0; k < buckets; ++k)
    {
        std::size_t at          = 36 + 2 * H + 2 * F * H + k * headSize;
        std::size_t biasAt      = at + 4 * H;
        std::size_t denseAt     = biasAt + 4 + 4 * widthField;
        std::size_t denseBiasAt = denseAt + DenseWidth * 2 * H;
        std::size_t residualAt  = denseBiasAt + DenseWidth * 4;
        if (widthField && u32_at(b, biasAt + 4) != DenseWidth)
            return "unsupported dense width";

        Head h;
        h.output.resize(2 * H);
        std::memcpy(h.output.data(), b.data() + at, 4 * H);
        std::memcpy(&h.bias, b.data() + biasAt, 4);
        h.denseWeights.resize(DenseWidth * 2 * H);
        std::memcpy(h.denseWeights.data(), b.data() + denseAt, DenseWidth * 2 * H);
        std::memcpy(h.denseBias, b.data() + denseBiasAt, 4 * DenseWidth);
        std::memcpy(h.denseOutput, b.data() + residualAt, 2 * DenseWidth);
        h.activeCount = 0;
        for (int r = 0; r < DenseWidth; ++r)
        {
            h.rowSum[r] = 0;
            for (int j = 0; j < 2 * H; ++j)
                h.rowSum[r] += h.denseWeights[std::size_t(r) * 2 * H + j];
            if (h.denseOutput[r])
                h.active[h.activeCount++] = r;
        }
        n.heads.push_back(std::move(h));
    }

    n.path   = path;
    net      = std::move(n);
    isLoaded = true;
    ++gen;
    return "";
}


std::string describe() {
    if (!isLoaded)
        return "no network";
    std::ostringstream ss;
    ss << "RPSNNUE1 format " << net.version << " features " << net.features << " hidden "
       << net.hidden << " heads " << net.heads.size() << " from " << net.path;
    return ss.str();
}


void record_move(const Position& pos, StateInfo* st, Piece pc, Square from, Square to,
                 Piece captured, const Bitboard attBefore[COLOR_NB][4], int rule50Before) {

    Dirty& d = st->dirty;
    st->accumulator.gen[WHITE] = st->accumulator.gen[BLACK] = 0;
    if (!isLoaded)
    {
        d.gen = 0;
        return;
    }
    d.gen = gen;

    Bitboard attAfter[COLOR_NB][4];
    attacked(pos, attAfter);
    int since = pos.rule50_count();

    for (Color p : {WHITE, BLACK})
    {
        d.nAdd[p] = d.nSub[p] = d.refresh[p] = 0;

        int ctx = context(pos, p);
        if (net.contexts && captured != NO_PIECE && color_of(captured) != p)
        {
            Color o      = ~p;
            int   r      = popcount(pos.pieces(o, ROCK)) + (type_of(captured) == ROCK);
            int   pa     = popcount(pos.pieces(o, PAPER)) + (type_of(captured) == PAPER);
            int   sc     = popcount(pos.pieces(o, SCISSORS)) + (type_of(captured) == SCISSORS);
            if (context_of(r, pa, sc) != ctx)
            {
                d.refresh[p] = 1;
                continue;
            }
        }

        auto sub = [&](int row) {
            if (d.nSub[p] < MaxRows)
                d.sub[p][d.nSub[p]++] = uint16_t(row);
            else
                d.refresh[p] = 1;
        };
        auto add = [&](int row) {
            if (d.nAdd[p] < MaxRows)
                d.add[p][d.nAdd[p]++] = uint16_t(row);
            else
                d.refresh[p] = 1;
        };

        sub(piece_row(p, pc, from, ctx));
        add(piece_row(p, pc, to, ctx));
        if (captured != NO_PIECE)
            sub(piece_row(p, captured, to, ctx));

        for (Color c : {WHITE, BLACK})
            for (PieceType t : {ROCK, PAPER, SCISSORS})
            {
                for (Bitboard b = attBefore[c][t] & ~attAfter[c][t]; b;)
                    sub(attack_row(p, make_piece(c, t), pop_lsb(b)));
                for (Bitboard b = attAfter[c][t] & ~attBefore[c][t]; b;)
                    add(attack_row(p, make_piece(c, t), pop_lsb(b)));
            }

        if (since_row(rule50Before) != since_row(since))
        {
            sub(since_row(rule50Before));
            add(since_row(since));
        }
        if (left_row(rule50Before) != left_row(since))
        {
            sub(left_row(rule50Before));
            add(left_row(since));
        }

        if (net.goals)
        {
            auto before = [&](Square s) {
                return s == from ? pc : s == to ? captured : pos.piece_on(s);
            };
            Square g = goal_square(p), h = goal_square(~p);
            if (blocker_row(p, before(g)) != blocker_row(p, pos.piece_on(g)))
            {
                sub(blocker_row(p, before(g)));
                add(blocker_row(p, pos.piece_on(g)));
            }
            if (invader_row(p, before(h)) != invader_row(p, pos.piece_on(h)))
            {
                sub(invader_row(p, before(h)));
                add(invader_row(p, pos.piece_on(h)));
            }
        }
    }
}


int evaluate(const Position& pos) {

    StateInfo* st = pos.state();
    ensure(pos, st, WHITE);
    ensure(pos, st, BLACK);

    if (verify)
    {
        alignas(64) int32_t fresh[MaxHidden];
        for (Color p : {WHITE, BLACK})
        {
            refresh(pos, p, fresh);
            if (std::memcmp(fresh, st->accumulator.v[p], sizeof(int32_t) * net.hidden))
            {
                std::cout << "info string NETVERIFY MISMATCH perspective " << int(p) << " fen "
                          << pos.fen() << std::endl;
                std::abort();
            }
        }
    }

    return output(pos, st->accumulator);
}

// Diagnostic: dense-layer inputs of the side to move that quantize to zero
int zero_inputs(const Position& pos) {
    if (!isLoaded || net.heads[0].activeCount == 0)
        return 0;
    StateInfo* st = pos.state();
    ensure(pos, st, WHITE);
    ensure(pos, st, BLACK);
    const int H = net.hidden;
    alignas(64) int8_t in[2 * MaxHidden];
    quantize(st->accumulator.v[pos.side_to_move()], in);
    quantize(st->accumulator.v[~pos.side_to_move()], in + H);
    return int(std::count(in, in + 2 * H, int8_t(-128)));
}

int hidden() { return isLoaded ? net.hidden : 0; }

}  // namespace Stockfish::RpsNet
