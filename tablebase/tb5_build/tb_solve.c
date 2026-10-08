/* tb_solve.c -- production retrograde DTZ builder for the 9x9 rock/paper/scissors race game.
 *
 * This is the C port of tb_solve.py.  The prototype runs ~80k positions/s, which makes the
 * 4-piece set ~70 minutes and the 5-piece set ~4.5 days: fine as an oracle, hopeless as a
 * builder.  The prototype output in tablebase/tb (the .dtz blobs) is the REFERENCE this must
 * reproduce byte for byte; see tb_solve.py's docstring for the soundness argument and the
 * terminal order (search.cpp:126-137).
 *
 * TWO DELIBERATE DEPARTURES FROM THE PROTOTYPE, both about memory, neither about values.
 *
 * 1. NO INDEX QUEUES.  The prototype keeps one bucket of indices per DTZ level.  Total pushes
 *    equal the number of resolved positions, so at 5 pieces that is 3.07e9 x 4 B = 12.3 GB of
 *    queue on top of the table -- it would dominate the build and break the 2 B/entry budget
 *    the cost model was built on.  Instead each level is a SEQUENTIAL SWEEP of the table.  Max
 *    DTZ over the whole 3-piece set is 36, so the number of sweeps is small, and a sweep is
 *    linear, prefetch-friendly and trivially partitioned.
 *
 * 2. ONE ATOMIC 16-BIT CELL PER ENTRY, hi = state, lo = dtz-or-zdr.  The prototype keeps four
 *    separate arrays (wdl, dtz, safe, zdr = 5 B/entry).  They collapse to 2 B because dtz and
 *    zdr are never live at the same time: zdr is only ever read while a node is UNKNOWN, and
 *    dtz is only ever read once it is resolved.  Packing them into one word is not just a size
 *    win -- it is what makes the threading correct, because every state transition then becomes
 *    a SINGLE compare-and-swap.  Two threads racing to resolve the same predecessor (one via a
 *    LOSS child -> WIN, one via a WIN child -> decrement) cannot interleave into a torn state.
 *
 * Threading is WITHIN a signature over one shared table, never two big signatures at once:
 * peak RAM is the whole build's binding constraint (5.7 GiB for the largest 5-piece signature).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
#include <time.h>

/* ------------------------------------------------------------------ knobs (no argv) */
#ifndef MAX_PIECES
#define MAX_PIECES   4      /* solve every relabel-canonical signature up to this size */
#endif
#ifndef NTHREAD
#define NTHREAD   1         /* 1 while the live bot plays: heavy jobs must not steal its clock */
#endif
#ifndef WRITE_TABLES
#define WRITE_TABLES   1
#endif
#ifndef OUT_DIR
#define OUT_DIR  "/Users/vladloukine/PycharmProjects/RandomShitIWillNeverUseAgain/tablebase/tb_c"
#endif
#define JOURNAL  OUT_DIR "/journal.txt"

/* RESUMABILITY.  This box gets its jobs killed by the host memory guard roughly every 30 minutes
 * and sleeps when the lid closes, so a build that cannot resume is a build that never finishes.
 * A signature is done when its .dtz exists AND the journal names it with the byte count that file
 * actually has.  Smaller signatures are always recomputed (a few seconds) because the bigger ones
 * probe their capture children, and only signatures strictly below MAX_PIECES are retained in RAM
 * -- keeping the 96 four-piece WDL tables would be 8.3 GB on an 8 GiB machine. */

/* FILES/RANKS are overridable so the n=5 path can be exercised on a board small enough to
 * actually run: at 9x9 one 5-piece signature is a 12.99 GiB allocation, so the real build's
 * largest code path can only ever be tested on the cloud unless the board shrinks. */
#ifndef FILES
#define FILES 9
#endif
#ifndef RANKS
#define RANKS 9
#endif
#define NSQ   (FILES * RANKS)
#define ROCK 1
#define PAPER 2
#define SCISSORS 3
#define WHITE 0
#define BLACK 1
#define CAPTURE_CLOCK 200
#define DTZ_MAX CAPTURE_CLOCK
#define MAXP  6
#define MAXMV 64

/* prototype's wdl codes, kept identical so retained tables can be compared directly */
#define UNKNOWN 0
#define WIN     1
#define LOSS    2
#define DEAD    3

/* hi byte of a cell: <= MAXMV means UNKNOWN carrying `safe`; these are the sentinels */
#define ST_LOSS 0xFDu
#define ST_WIN  0xFEu
#define ST_DEAD 0xFFu
#define HI(c) ((unsigned)((c) >> 8))
#define LO(c) ((unsigned)((c) & 0xFF))
#define MKCELL(hi, lo) ((uint16_t)(((hi) << 8) | (lo)))
#define RESOLVED(hi) ((hi) >= ST_LOSS)

/* shipping byte format, see tb_solve.py */
#define CLAMP_WIN  127
#define CLAMP_LOSS 128

static int STEPS[NSQ][8], NSTEP[NSQ];
static int GOAL[2];
static int PREY[4], PRED[4];
static int SIGMA_T[4];      /* type -> relabelled type, the R -> P -> S -> R cycle */

static int sq_of(int f, int r) { return r * FILES + f; }

static void init_tables(void) {
    for (int s = 0; s < NSQ; s++) {
        int f = s % FILES, r = s / FILES, k = 0;
        for (int df = -1; df <= 1; df++)
            for (int dr = -1; dr <= 1; dr++) {
                if (!df && !dr) continue;
                int nf = f + df, nr = r + dr;
                if (nf >= 0 && nf < FILES && nr >= 0 && nr < RANKS) STEPS[s][k++] = sq_of(nf, nr);
            }
        NSTEP[s] = k;
    }
    GOAL[WHITE] = sq_of(FILES - 1, RANKS - 1);   /* i9 at 9x9; parameterised so the
     * n=5 code path can be exercised on a small board, where it is actually runnable */
    GOAL[BLACK] = sq_of(0, 0);
    PREY[ROCK] = SCISSORS; PREY[PAPER] = ROCK;     PREY[SCISSORS] = PAPER;
    PRED[ROCK] = PAPER;    PRED[PAPER] = SCISSORS; PRED[SCISSORS] = ROCK;
    SIGMA_T[ROCK] = PAPER; SIGMA_T[PAPER] = SCISSORS; SIGMA_T[SCISSORS] = ROCK;
}

/* ------------------------------------------------------------------ signatures */
typedef struct {
    int wc[3], bc[3];       /* counts of R,P,S per side */
    int n;                  /* total pieces */
    int pcol[MAXP], ptyp[MAXP];
    long long size;         /* 2 * 81^n */
    long long pw[MAXP];     /* 81^i */
    uint8_t *wdl;           /* retained for probing by bigger signatures */
} Sig;

static Sig sigs[512];
static int nsig = 0;

static int LIMIT[4];

static int sig_key(const int *wc, const int *bc) {
    return (((wc[0] * 5 + wc[1]) * 5 + wc[2]) * 125) + ((bc[0] * 5 + bc[1]) * 5 + bc[2]);
}

static Sig *find_sig(const int *wc, const int *bc) {
    int k = sig_key(wc, bc);
    for (int i = 0; i < nsig; i++)
        if (sig_key(sigs[i].wc, sigs[i].bc) == k) return &sigs[i];
    return NULL;
}

static int lex3(const int *a, const int *b) {   /* -1 / 0 / +1 */
    for (int i = 0; i < 3; i++) { if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1; }
    return 0;
}

/* ------------------------------------------------------------------ the relabelling fold
 * The prey relation is a 3-cycle, so advancing every piece round R -> P -> S -> R is a game
 * automorphism: it leaves the legal move set literally equal because no square moves.  Verified
 * on the solved tables by check_relabel4.py -- 17 orbits, 34 pairs, 0 histogram mismatches.
 * Composed with the colour swap it generates a group of order 6 (sigma and the colour swap
 * commute: one acts on types, the other on colours and squares).  We build only each orbit's
 * representative and reach it from the probe, which removes WHOLE TABLES: 51 four-piece
 * signatures become 17, so disk and CPU both fall ~2.85x.  It cannot shrink peak build RAM,
 * because that is set by the largest single signature, which is itself a representative. */
static void sigma_counts(const int *c, int *o) {
    o[0] = c[2]; o[1] = c[0]; o[2] = c[1];   /* new_R = old_S, new_P = old_R, new_S = old_P */
}

static int counts_ok(const int *c) {
    return c[0] <= LIMIT[ROCK] && c[1] <= LIMIT[PAPER] && c[2] <= LIMIT[SCISSORS];
}

/* Lex-smallest VALID member of the orbit of (wc,bc).  A member can be invalid because there are
 * 4 papers but only 3 rocks and 3 scissors, which is exactly why the fold is 2.98x and not 3x. */
static void orbit_rep(const int *wc, const int *bc, int *rw, int *rb) {
    int aw[3], ab[3], bw[3], bb[3], have = 0;
    memcpy(aw, wc, sizeof aw); memcpy(ab, bc, sizeof ab);
    for (int k = 0; k < 3; k++) {
        for (int sw = 0; sw < 2; sw++) {
            const int *cw = sw ? ab : aw, *cb = sw ? aw : ab;
            if (!counts_ok(cw) || !counts_ok(cb)) continue;
            if (!have || lex3(cw, rw) < 0 || (lex3(cw, rw) == 0 && lex3(cb, rb) < 0)) {
                memcpy(rw, cw, 3 * sizeof(int)); memcpy(rb, cb, 3 * sizeof(int));
                have = 1;
            }
        }
        sigma_counts(aw, bw); sigma_counts(ab, bb);
        memcpy(aw, bw, sizeof aw); memcpy(ab, bb, sizeof ab);
    }
}

static void fill_pieces(Sig *g) {
    int k = 0;
    const int *cnt[2] = { g->wc, g->bc };
    for (int c = 0; c < 2; c++)
        for (int pt = ROCK; pt <= SCISSORS; pt++)
            for (int j = 0; j < cnt[c][pt - 1]; j++) { g->pcol[k] = c; g->ptyp[k] = pt; k++; }
    g->n = k;
    g->size = 2;
    for (int i = 0; i < k; i++) g->size *= NSQ;
    long long p = 1;
    for (int i = 0; i < k; i++) { g->pw[i] = p; p *= NSQ; }
}

/* Colour-canonical signatures (wc <= bc), both sides non-empty, ordered by piece count then
 * lexicographically -- the prototype's signatures() order, so tables are built in the order
 * their children need them. */
static void build_sig_list(int max_pieces) {
    for (int total = 2; total <= max_pieces; total++) {
        for (int nw = 1; nw < total; nw++) {
            int nb = total - nw;
            for (int wr = 0; wr <= LIMIT[ROCK]; wr++)
            for (int wp = 0; wp <= LIMIT[PAPER]; wp++)
            for (int ws = 0; ws <= LIMIT[SCISSORS]; ws++) {
                if (wr + wp + ws != nw) continue;
                for (int br = 0; br <= LIMIT[ROCK]; br++)
                for (int bp = 0; bp <= LIMIT[PAPER]; bp++)
                for (int bs = 0; bs <= LIMIT[SCISSORS]; bs++) {
                    if (br + bp + bs != nb) continue;
                    int wc[3] = { wr, wp, ws }, bc[3] = { br, bp, bs };
                    int rw[3], rb[3];
                    orbit_rep(wc, bc, rw, rb);        /* keep only the orbit representative */
                    if (lex3(wc, rw) || lex3(bc, rb)) continue;
                    Sig *g = &sigs[nsig++];
                    memset(g, 0, sizeof *g);
                    memcpy(g->wc, wc, sizeof wc);
                    memcpy(g->bc, bc, sizeof bc);
                    fill_pieces(g);
                }
            }
        }
    }
    /* stable sort by (total, wc, bc) to match the prototype */
    for (int i = 1; i < nsig; i++)
        for (int j = i; j > 0; j--) {
            Sig *a = &sigs[j - 1], *b = &sigs[j];
            int ka = a->n, kb = b->n, sw = 0;
            if (ka != kb) sw = ka > kb;
            else {
                int c = lex3(a->wc, b->wc);
                if (c) sw = c > 0; else sw = lex3(a->bc, b->bc) > 0;
            }
            if (!sw) break;
            Sig t = *a; *a = *b; *b = t;
        }
}

/* ------------------------------------------------------------------ movegen
 * Slot-indexed mirror of rps_rules.gen_legal, which was differentially validated against the
 * engine (perft 1-4 exact, 150 evasion-restricted move lists).  Returns (frm, to, cap_slot). */
typedef struct { int frm, to, cap; } Move;

static int gen_non_evasions(const Sig *g, const int *sqs, int stm, const signed char *occ, Move *out) {
    int k = 0, n = g->n;
    for (int i = 0; i < n; i++) {
        if (g->pcol[i] != stm) continue;
        int prey = PREY[g->ptyp[i]], frm = sqs[i];
        for (int t = 0; t < NSTEP[frm]; t++) {
            int to = STEPS[frm][t], j = occ[to];
            if (j < 0) { out[k].frm = frm; out[k].to = to; out[k].cap = -1; k++; }
            else if (g->pcol[j] != stm && g->ptyp[j] == prey) {
                out[k].frm = frm; out[k].to = to; out[k].cap = j; k++;
            }
        }
    }
    return k;
}

/* position.h:237 -- pieces of c that could enter c's goal right now */
static int gen_threats(const Sig *g, int c, const signed char *occ, int *out) {
    int gl = GOAL[c], j = occ[gl], k = 0;
    if (j < 0) {
        for (int t = 0; t < NSTEP[gl]; t++) {
            int s = STEPS[gl][t], i = occ[s];
            if (i >= 0 && g->pcol[i] == c) out[k++] = i;
        }
        return k;
    }
    if (g->pcol[j] == c) return 0;
    int want = PRED[g->ptyp[j]];
    for (int t = 0; t < NSTEP[gl]; t++) {
        int s = STEPS[gl][t], i = occ[s];
        if (i >= 0 && g->pcol[i] == c && g->ptyp[i] == want) out[k++] = i;
    }
    return k;
}

/* movegen.cpp:96-128 */
static int gen_evasions(const Sig *g, const int *sqs, int stm, const signed char *occ,
                        const int *thr, int nthr, Move *out) {
    int k = 0, us = stm, them = 1 - stm;
    int mine[MAXP], nm = gen_threats(g, us, occ, mine);
    int gu = GOAL[us], cap_at_gu = occ[gu];
    for (int i = 0; i < nm; i++) { out[k].frm = sqs[mine[i]]; out[k].to = gu; out[k].cap = cap_at_gu; k++; }
    if (nthr == 1) {
        int t = thr[0], ts = sqs[t], want = PRED[g->ptyp[t]];
        for (int q = 0; q < NSTEP[ts]; q++) {
            int s = STEPS[ts][q], j = occ[s];
            if (j >= 0 && g->pcol[j] == us && g->ptyp[j] == want) {
                out[k].frm = s; out[k].to = ts; out[k].cap = t; k++;
            }
        }
    }
    int home = GOAL[them];
    if (occ[home] < 0) {
        int tt[4] = { 0, 0, 0, 0 };
        for (int i = 0; i < nthr; i++) tt[g->ptyp[thr[i]]] = 1;
        for (int pt = ROCK; pt <= SCISSORS; pt++) {
            if (tt[PRED[pt]]) continue;
            for (int q = 0; q < NSTEP[home]; q++) {
                int s = STEPS[home][q], j = occ[s];
                if (j >= 0 && g->pcol[j] == us && g->ptyp[j] == pt) {
                    out[k].frm = s; out[k].to = home; out[k].cap = -1; k++;
                }
            }
        }
    }
    /* The three clauses cannot collide on a (frm,to) pair, but a duplicate would inflate the
     * unresolved-successor counter and silently wedge the BFS, so dedupe (prototype does too). */
    int w = 0;
    for (int i = 0; i < k; i++) {
        int dup = 0;
        for (int j = 0; j < w; j++)
            if (out[j].frm == out[i].frm && out[j].to == out[i].to) { dup = 1; break; }
        if (!dup) out[w++] = out[i];
    }
    return w;
}

static int gen_legal(const Sig *g, const int *sqs, int stm, const signed char *occ, Move *out) {
    int thr[MAXP], nthr = gen_threats(g, 1 - stm, occ, thr);
    if (!nthr) return gen_non_evasions(g, sqs, stm, occ, out);
    int k = gen_evasions(g, sqs, stm, occ, thr, nthr, out);
    if (!k) return gen_non_evasions(g, sqs, stm, occ, out);
    return k;
}

/* ------------------------------------------------------------------ probing children */
/* Clock-aware wdl of an arbitrary position, folded to the canonical colouring. */
static int probe(const int *pcol, const int *ptyp, const int *psq, int n, int stm) {
    int c2[MAXP], t2[MAXP], s2[MAXP];
    Sig *g = NULL;
    /* Walk the order-6 orbit (sigma^k, optionally composed with the colour swap) until we land on
     * a signature that was actually built.  Exactly one signature of the orbit is stored; several
     * group elements may reach it, and any of them is a valid lookup -- that IS the symmetry. */
    for (int k = 0; k < 3 && !g; k++) {
        for (int sw = 0; sw < 2 && !g; sw++) {
            int wc[3] = { 0, 0, 0 }, bc[3] = { 0, 0, 0 }, st2 = stm;
            for (int i = 0; i < n; i++) {
                int t = ptyp[i], c = pcol[i], q = psq[i];
                for (int j = 0; j < k; j++) t = SIGMA_T[t];
                if (sw) { c = 1 - c; q = NSQ - 1 - q; }   /* centre reflection swaps the goals */
                c2[i] = c; t2[i] = t; s2[i] = q;
                if (c == WHITE) wc[t - 1]++; else bc[t - 1]++;
            }
            if (sw) st2 = 1 - stm;
            g = find_sig(wc, bc);
            if (g) stm = st2;
        }
    }
    /* canonical order: by (colour, type, square) -- identical pieces get increasing squares */
    for (int i = 1; i < n; i++)
        for (int j = i; j > 0; j--) {
            int a = j - 1, b = j, sw;
            if (c2[a] != c2[b]) sw = c2[a] > c2[b];
            else if (t2[a] != t2[b]) sw = t2[a] > t2[b];
            else sw = s2[a] > s2[b];
            if (!sw) break;
            int x;
            x = c2[a]; c2[a] = c2[b]; c2[b] = x;
            x = t2[a]; t2[a] = t2[b]; t2[b] = x;
            x = s2[a]; s2[a] = s2[b]; s2[b] = x;
        }
    if (!g || !g->wdl) { fprintf(stderr, "FATAL: probe of unsolved signature\n"); exit(1); }
    long long t = 0;
    for (int i = 0; i < n; i++) t += (long long)s2[i] * g->pw[i];
    return g->wdl[2 * t + stm];
}

/* Value after a zeroing capture, from the CHILD's mover.  -1 when the opponent has no pieces
 * left (we simply win). */
static int capture_child(const Sig *g, const int *sqs, int mover_slot, int cap_slot, int to, int stm) {
    int pc[MAXP], pt[MAXP], ps[MAXP], k = 0, cstm = 1 - stm, alive = 0;
    for (int i = 0; i < g->n; i++) {
        if (i == cap_slot) continue;
        pc[k] = g->pcol[i]; pt[k] = g->ptyp[i];
        ps[k] = (i == mover_slot) ? to : sqs[i];
        if (g->pcol[i] == cstm) alive = 1;
        k++;
    }
    if (!alive) return -1;
    return probe(pc, pt, ps, k, cstm);
}

/* ------------------------------------------------------------------ shared build state */
static _Atomic uint16_t *CELL;
static Sig *CUR;
static int RUNA[MAXP], RUNB[MAXP], NRUN;     /* index ranges of identical pieces */
static int SWEEP_D;
static _Atomic long long RESOLVED_AT_LEVEL;

static void make_runs(const Sig *g) {
    NRUN = 0;
    int i = 0;
    while (i < g->n) {
        int j = i;
        while (j < g->n && g->pcol[j] == g->pcol[i] && g->ptyp[j] == g->ptyp[i]) j++;
        RUNA[NRUN] = i; RUNB[NRUN] = j; NRUN++;
        i = j;
    }
}

/* canonical == distinct squares and identical pieces in increasing square order */
static int is_canonical(const Sig *g, const int *sqs) {
    for (int i = 0; i < g->n; i++)
        for (int j = i + 1; j < g->n; j++)
            if (sqs[i] == sqs[j]) return 0;
    for (int r = 0; r < NRUN; r++)
        for (int k = RUNA[r] + 1; k < RUNB[r]; k++)
            if (sqs[k] < sqs[k - 1]) return 0;
    return 1;
}

static void decode_sqs(const Sig *g, long long base, int *sqs) {
    for (int i = 0; i < g->n; i++) sqs[i] = (int)((base / g->pw[i]) % NSQ);
}

/* ------------------------------------------------------------------ init pass */
typedef struct { int id; long long lo, hi; long long live, dead; } Job;

static void *init_worker(void *arg) {
    Job *jb = (Job *)arg;
    const Sig *g = CUR;
    int n = g->n, sqs[MAXP];
    signed char occ[NSQ];
    Move mv[MAXMV];
    memset(occ, -1, sizeof occ);
    jb->live = jb->dead = 0;
    for (long long idx = jb->lo; idx < jb->hi; idx++) {
        int stm = (int)(idx & 1);
        long long base = idx >> 1;
        decode_sqs(g, base, sqs);
        if (!is_canonical(g, sqs)) continue;              /* a hole in the 81^n index space */
        int over = 0;
        for (int i = 0; i < n; i++) if (sqs[i] == GOAL[g->pcol[i]]) { over = 1; break; }
        if (over) {                                       /* a side already stands on its goal */
            atomic_store_explicit(&CELL[idx], MKCELL(ST_DEAD, 0), memory_order_relaxed);
            if (stm == 0) jb->dead++;
            continue;
        }
        for (int i = 0; i < n; i++) occ[sqs[i]] = (signed char)i;
        int nmv = gen_legal(g, sqs, stm, occ, mv);
        if (!nmv) {                                       /* no move (or no pieces) -> we lose now */
            atomic_store_explicit(&CELL[idx], MKCELL(ST_LOSS, 0), memory_order_relaxed);
            jb->live++;
        } else {
            jb->live++;
            int goal = GOAL[stm], win1 = 0, nz = 0, zd = 0;
            for (int m = 0; m < nmv; m++) {
                if (mv[m].to == goal) { win1 = 1; break; }
                if (mv[m].cap >= 0) {
                    int cv = capture_child(g, sqs, occ[mv[m].frm], mv[m].cap, mv[m].to, stm);
                    if (cv < 0 || cv == LOSS) { win1 = 1; break; }
                    if (cv == UNKNOWN) zd = 1;
                } else nz++;
            }
            if (win1)
                atomic_store_explicit(&CELL[idx], MKCELL(ST_WIN, 1), memory_order_relaxed);
            else if (nz == 0 && !zd)                      /* every move is a losing zeroing move */
                atomic_store_explicit(&CELL[idx], MKCELL(ST_LOSS, 1), memory_order_relaxed);
            else
                atomic_store_explicit(&CELL[idx], MKCELL((unsigned)nz, (unsigned)zd), memory_order_relaxed);
        }
        for (int i = 0; i < n; i++) occ[sqs[i]] = -1;
    }
    return NULL;
}

/* ------------------------------------------------------------------ one CAS per transition */
static void mark_win(long long q, unsigned nd) {
    uint16_t old = atomic_load_explicit(&CELL[q], memory_order_relaxed);
    for (;;) {
        if (RESOLVED(HI(old))) return;
        uint16_t nw = MKCELL(ST_WIN, nd);
        if (atomic_compare_exchange_weak_explicit(&CELL[q], &old, nw,
                memory_order_relaxed, memory_order_relaxed)) {
            atomic_fetch_add_explicit(&RESOLVED_AT_LEVEL, 1, memory_order_relaxed);
            return;
        }
    }
}

static void dec_safe(long long q, unsigned nd) {
    uint16_t old = atomic_load_explicit(&CELL[q], memory_order_relaxed);
    for (;;) {
        unsigned hi = HI(old), lo = LO(old);
        if (RESOLVED(hi)) return;
        if (hi == 0) return;                  /* cannot happen: a node with no non-zeroing move
                                               * has no non-zeroing predecessor either */
        uint16_t nw;
        int became_loss = 0;
        if (hi - 1 == 0 && lo == 0) { nw = MKCELL(ST_LOSS, nd); became_loss = 1; }
        else                          nw = MKCELL(hi - 1, lo);
        if (atomic_compare_exchange_weak_explicit(&CELL[q], &old, nw,
                memory_order_relaxed, memory_order_relaxed)) {
            if (became_loss) atomic_fetch_add_explicit(&RESOLVED_AT_LEVEL, 1, memory_order_relaxed);
            return;
        }
    }
}

/* ------------------------------------------------------------------ sweep pass */
static void *sweep_worker(void *arg) {
    Job *jb = (Job *)arg;
    const Sig *g = CUR;
    int n = g->n, d = SWEEP_D, nd = d + 1;
    int sqs[MAXP], qsqs[MAXP];
    signed char occ[NSQ], qocc[NSQ];
    Move mv[MAXMV];
    memset(occ, -1, sizeof occ);
    memset(qocc, -1, sizeof qocc);
    for (long long idx = jb->lo; idx < jb->hi; idx++) {
        uint16_t c = atomic_load_explicit(&CELL[idx], memory_order_relaxed);
        unsigned hi = HI(c);
        if (hi != ST_WIN && hi != ST_LOSS) continue;
        if (LO(c) != (unsigned)d) continue;
        int stm = (int)(idx & 1);
        decode_sqs(g, idx >> 1, sqs);
        for (int i = 0; i < n; i++) occ[sqs[i]] = (signed char)i;
        int mover = 1 - stm, gm = GOAL[mover];
        for (int i = 0; i < n; i++) {
            if (g->pcol[i] != mover) continue;
            int cs = sqs[i];
            if (cs == gm) continue;                 /* that move was a goal entry: zeroing */
            for (int t = 0; t < NSTEP[cs]; t++) {
                int frm = STEPS[cs][t];
                if (occ[frm] >= 0 || frm == gm) continue;
                for (int k = 0; k < n; k++) qsqs[k] = sqs[k];
                qsqs[i] = frm;
                for (int r = 0; r < NRUN; r++)       /* keep identical pieces increasing */
                    for (int a = RUNA[r] + 1; a < RUNB[r]; a++)
                        for (int b = a; b > RUNA[r] && qsqs[b] < qsqs[b - 1]; b--) {
                            int x = qsqs[b]; qsqs[b] = qsqs[b - 1]; qsqs[b - 1] = x;
                        }
                long long q = 0;
                for (int k = 0; k < n; k++) q += (long long)qsqs[k] * g->pw[k];
                q = 2 * q + mover;
                if (RESOLVED(HI(atomic_load_explicit(&CELL[q], memory_order_relaxed)))) continue;
                for (int k = 0; k < n; k++) qocc[qsqs[k]] = (signed char)k;
                int nmv = gen_legal(g, qsqs, mover, qocc, mv), ok = 0;
                for (int m = 0; m < nmv; m++)
                    if (mv[m].frm == frm && mv[m].to == cs && mv[m].cap < 0) { ok = 1; break; }
                for (int k = 0; k < n; k++) qocc[qsqs[k]] = -1;
                if (!ok) continue;                  /* the forward move is not legal there */
                if (hi == ST_LOSS) mark_win(q, (unsigned)nd);
                else               dec_safe(q, (unsigned)nd);
            }
        }
        for (int i = 0; i < n; i++) occ[sqs[i]] = -1;
    }
    return NULL;
}

static void run_threads(void *(*fn)(void *), Job *jobs) {
    pthread_t th[NTHREAD];
    long long sz = CUR->size, chunk = (sz + NTHREAD - 1) / NTHREAD;
    for (int i = 0; i < NTHREAD; i++) {
        jobs[i].id = i;
        jobs[i].lo = (long long)i * chunk;
        jobs[i].hi = jobs[i].lo + chunk; if (jobs[i].hi > sz) jobs[i].hi = sz;
        if (jobs[i].lo > sz) jobs[i].lo = sz;
        pthread_create(&th[i], NULL, fn, &jobs[i]);
    }
    for (int i = 0; i < NTHREAD; i++) pthread_join(th[i], NULL);
}

/* ------------------------------------------------------------------ pack + write
 * Only LIVE indices, in the prototype's placements() order: lexicographic over
 * (sqs[0], sqs[1], ...), which is itertools.permutations(range(81), n) order once the
 * duplicate-square and twin-increasing filters are applied. */
/* The pack STREAMS to stdio rather than building the blob in RAM.  A buffered pack costs one
 * byte per live entry, which is invisible at 4 pieces (76 MiB) but adds 5.7 GiB on top of the
 * cell array at 5 pieces -- it would be the single largest allocation in the build after the
 * table itself, for no reason at all. */
static FILE *PKF;
static long long PKLEN, PKCLAMP;
static long long HIST[256];

static void emit(long long idx) {
    uint16_t c = atomic_load_explicit(&CELL[idx], memory_order_relaxed);
    unsigned hi = HI(c), lo = LO(c), v = 0;
    if (hi == ST_WIN)       { if (lo <= CLAMP_WIN)  v = lo;       else { v = 0; PKCLAMP++; } }
    else if (hi == ST_LOSS) { if (lo <= CLAMP_LOSS) v = 127 + lo; else { v = 0; PKCLAMP++; } }
    if (PKF) putc((int)v, PKF);
    PKLEN++;
    HIST[v]++;
}

static void pack_rec(const Sig *g, int depth, int *sqs, long long base) {
    if (depth == g->n) {
        for (int i = 0; i < g->n; i++) if (sqs[i] == GOAL[g->pcol[i]]) return;
        emit(2 * base); emit(2 * base + 1);
        return;
    }
    int lowest = 0;
    for (int r = 0; r < NRUN; r++)
        if (depth > RUNA[r] && depth < RUNB[r]) { lowest = sqs[depth - 1] + 1; break; }
    for (int s = lowest; s < NSQ; s++) {
        int dup = 0;
        for (int i = 0; i < depth; i++) if (sqs[i] == s) { dup = 1; break; }
        if (dup) continue;
        sqs[depth] = s;
        pack_rec(g, depth + 1, sqs, base + (long long)s * g->pw[depth]);
    }
}

/* ------------------------------------------------------------------ journal */
static long long journal_bytes(const char *tag) {
    FILE *f = fopen(JOURNAL, "r");
    if (!f) return -1;
    char line[256];
    long long found = -1;
    while (fgets(line, sizeof line, f)) {
        char t[64]; long long b;
        if (sscanf(line, "%63s %lld", t, &b) == 2 && !strcmp(t, tag)) found = b;
    }
    fclose(f);
    return found;
}

static long long file_bytes(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long long n = ftell(f);
    fclose(f);
    return n;
}

static void journal_put(const char *tag, long long bytes, long long w, long long l,
                        long long dr, int maxd, double secs) {
    FILE *f = fopen(JOURNAL, "a");
    if (!f) return;
    fprintf(f, "%s %lld W %lld L %lld D %lld maxdtz %d %.2fs\n", tag, bytes, w, l, dr, maxd, secs);
    fclose(f);
}

/* ------------------------------------------------------------------ driver */
static double now_s(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

int main(void) {
    init_tables();
    LIMIT[ROCK] = 3; LIMIT[PAPER] = 4; LIMIT[SCISSORS] = 3;   /* position.h:19-23 */
    build_sig_list(MAX_PIECES);

    printf("-----\n%d colour-canonical signatures up to %d pieces, %d threads\n",
           nsig, MAX_PIECES, NTHREAD);
    long long tot_live = 0, tot_clamp = 0;
    int gmax = 0;
    double t_all = now_s();

    for (int si = 0; si < nsig; si++) {
        Sig *g = &sigs[si];
        CUR = g;
        make_runs(g);
        double t0 = now_s();

        char tag[64];
        snprintf(tag, sizeof tag, "%d%d%d_%d%d%d",
                 g->wc[0], g->wc[1], g->wc[2], g->bc[0], g->bc[1], g->bc[2]);
        char path[256];
        snprintf(path, sizeof path, "%s/%s.dtz", OUT_DIR, tag);
        if (g->n == MAX_PIECES) {
            long long jb = journal_bytes(tag), fb = file_bytes(path);
            if (jb > 0 && jb == fb) {
                printf("  %s  already done (%lld B) -- skipped\n", tag, fb);
                fflush(stdout);
                tot_live += fb;
                continue;
            }
        }

        CELL = (_Atomic uint16_t *)calloc((size_t)g->size, sizeof(uint16_t));
        if (!CELL) { fprintf(stderr, "FATAL: out of memory for %lld cells\n", g->size); return 1; }

        Job jobs[NTHREAD];
        run_threads(init_worker, jobs);
        long long live = 0;
        for (int i = 0; i < NTHREAD; i++) live += jobs[i].live;

        for (int d = 0; d <= DTZ_MAX; d++) {
            if (d + 1 > DTZ_MAX) break;
            SWEEP_D = d;
            atomic_store(&RESOLVED_AT_LEVEL, 0);
            run_threads(sweep_worker, jobs);
            /* An empty level cannot be followed by a non-empty one, but levels 0 and 1 are
             * BOTH seeded by the init pass, so only start trusting emptiness from d = 2. */
            if (!atomic_load(&RESOLVED_AT_LEVEL) && d > 1) break;
        }

        /* tally -- maxd read off the finished table, not inferred from the sweep loop */
        long long w = 0, l = 0, dr;
        int maxd = 0;
        for (long long i = 0; i < g->size; i++) {
            uint16_t c = atomic_load_explicit(&CELL[i], memory_order_relaxed);
            unsigned hi = HI(c);
            if (hi == ST_WIN) w++;
            else if (hi == ST_LOSS) l++;
            else continue;
            if ((int)LO(c) > maxd) maxd = (int)LO(c);
        }
        dr = live - w - l;
        if (maxd > gmax) gmax = maxd;

        /* pack, streamed straight to the table file */
        PKLEN = PKCLAMP = 0;
        memset(HIST, 0, sizeof HIST);
#if WRITE_TABLES
        PKF = fopen(path, "wb");
        if (!PKF) { fprintf(stderr, "FATAL: cannot write %s\n", path); return 1; }
#else
        PKF = NULL;
#endif
        int sqs[MAXP];
        pack_rec(g, 0, sqs, 0);
        if (PKF) { fclose(PKF); PKF = NULL; }

        int nval = 0;
        for (int v = 0; v < 256; v++) if (HIST[v]) nval++;

        printf("  ((%d, %d, %d), (%d, %d, %d))  live %8lld  W %7lld (%5.1f%%)  L %7lld (%5.1f%%)"
               "  D %7lld (%5.1f%%)  maxDTZ %3d  packed %lld B  vals %d  clamped %lld  %.2fs\n",
               g->wc[0], g->wc[1], g->wc[2], g->bc[0], g->bc[1], g->bc[2],
               live, w, 100.0 * w / live, l, 100.0 * l / live, dr, 100.0 * dr / live,
               maxd, PKLEN, nval, PKCLAMP, now_s() - t0);
        fflush(stdout);

        if (PKLEN != live) {
            fprintf(stderr, "FATAL: packed %lld bytes but %lld live positions\n", PKLEN, live);
            return 1;
        }
        tot_live += live; tot_clamp += PKCLAMP;

        journal_put(tag, PKLEN, w, l, dr, maxd, now_s() - t0);

        /* Retain WDL for the bigger signatures' capture children.  Nothing probes a signature at
         * MAX_PIECES, and holding those tables is what would blow the RAM budget. */
        if (g->n >= MAX_PIECES) { free((void *)CELL); CELL = NULL; continue; }
        g->wdl = (uint8_t *)calloc((size_t)g->size, 1);
        if (!g->wdl) { fprintf(stderr, "FATAL: out of memory retaining wdl\n"); return 1; }
        for (long long i = 0; i < g->size; i++) {
            unsigned hi = HI(atomic_load_explicit(&CELL[i], memory_order_relaxed));
            g->wdl[i] = (hi == ST_WIN) ? WIN : (hi == ST_LOSS) ? LOSS : (hi == ST_DEAD) ? DEAD : UNKNOWN;
        }
        free((void *)CELL);
        CELL = NULL;
    }

    double el = now_s() - t_all;
    printf("-----\nTOTAL live %lld  maxDTZ %d  clamped %lld  %.1fs  %.0f pos/s\n",
           tot_live, gmax, tot_clamp, el, tot_live / el);
    printf("build footprint 2 B/entry (one atomic 16-bit cell: hi state, lo dtz-or-zdr)\n");
    return 0;
}
