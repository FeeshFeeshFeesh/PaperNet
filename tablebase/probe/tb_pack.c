/* tb_pack -- turn a solved .dtz stream into a seekable, block-compressed table.
 *
 * WHY THIS EXISTS.  tb_solve writes one byte per LIVE entry in pack_rec's DFS order: a
 * compacted stream with every illegal placement squeezed out.  That is the right shape for
 * a one-shot offline scan and the wrong shape for a search probe, because finding entry k
 * means walking the first k entries.  A probe needs O(1) addressing.
 *
 * THE INDEX.  Dense, big-endian over the squares:
 *
 *     idx = 2 * SUM_i sqs[i] * 81^(n-1-i)  +  stm
 *
 * Big-endian is the whole trick.  pack_rec iterates slot 0 in its OUTERMOST loop, so its
 * output is lexicographically ascending in (sqs[0], ..., sqs[n-1]) -- which is exactly
 * ascending in a big-endian index and NOT in the solver's own little-endian pw[i] = 81^i.
 * So the conversion is a single sequential pass: every live entry lands at a strictly
 * greater output offset than the one before it, and the gaps are holes.
 *
 * WHY DENSE AND NOT A RANK FUNCTION.  A rank function over the live set is ~4x denser --
 * the live stream folds identical pieces by k!, which 81^n does not (001_040, with four
 * identical papers, is 2799% holes).  It was rejected anyway, for two reasons.  It needs a
 * per-probe DP over run-structured disjoint subsets, ~30us, and in a <=5-piece endgame every
 * node probes, so that is a ~30x search slowdown.  And it is the exact species of
 * combinatorial code that silently returns a NEIGHBOURING entry when it is subtly wrong --
 * a tablebase that lies is worse than no tablebase.  Dense costs disk, which compresses
 * away, and buys an index that cannot really be got wrong.
 *
 * VALUE ENCODING is tb_solve's, unchanged: 0 = draw/dead/clamped, 1..127 = win in that many
 * zeroing moves, 128.. = loss in (v - 127).
 *
 * Reads the RAW stream on stdin (pipe `zstd -dc x.dtz.zst`), so the 55 GiB of decompressed
 * entries never touch a disk that has 8.5 GiB free.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <zstd.h>

#define NSQ 81
#define MAXP 6
#define BLOCK_LOG 16
#define BLOCK (1 << BLOCK_LOG)

/* block methods */
#define M_CONST 0
#define M_ZSTD  1
#define M_RAW   2

static int N, WC[3], BC[3], PCOL[MAXP], ZLEVEL = 12;
static int GOALSQ[2];
static int RUNA[MAXP], RUNB[MAXP], NRUN;
static long long BEPW[MAXP];          /* 81^(n-1-i) */

static FILE *OUT;
static unsigned char BUF[BLOCK];
static size_t BUFN;
static long long NEXT_OUT, DENSE;
static uint32_t *BLKOFF;
static long long NBLK, BLKI;
static long long PAYOFF, WROTE;
static unsigned char CMP[BLOCK + 4096];
static long long LIVE_IN, HOLES, NCONST, NZSTD, NRAW;

static void fill_pieces(void) {
    int k = 0; const int *cnt[2] = { WC, BC };
    for (int c = 0; c < 2; c++)
        for (int pt = 0; pt < 3; pt++)
            for (int j = 0; j < cnt[c][pt]; j++) PCOL[k++] = c;
    N = k;
    for (int i = 0; i < N; i++) {
        long long p = 1;
        for (int e = 0; e < N - 1 - i; e++) p *= NSQ;
        BEPW[i] = p;
    }
    /* runs of identical (colour, type) -- recomputed the same way, over the same order */
    NRUN = 0; k = 0;
    for (int c = 0; c < 2; c++)
        for (int pt = 0; pt < 3; pt++) {
            int m = cnt[c][pt];
            if (!m) continue;
            RUNA[NRUN] = k; RUNB[NRUN] = k + m; NRUN++; k += m;
        }
}

static void flush_block(void) {
    if (!BUFN) return;
    BLKOFF[BLKI] = (uint32_t)WROTE;
    /* CONST first: the holes make whole blocks of one value, and they are the common case */
    int uniform = 1;
    for (size_t i = 1; i < BUFN; i++) if (BUF[i] != BUF[0]) { uniform = 0; break; }
    if (uniform) {
        unsigned char h[2] = { M_CONST, BUF[0] };
        fwrite(h, 1, 2, OUT); WROTE += 2; NCONST++;
    } else {
        size_t z = ZSTD_compress(CMP, sizeof CMP, BUF, BUFN, ZLEVEL);
        if (!ZSTD_isError(z) && z < BUFN) {
            unsigned char h = M_ZSTD;
            fwrite(&h, 1, 1, OUT); fwrite(CMP, 1, z, OUT);
            WROTE += 1 + (long long)z; NZSTD++;
        } else {
            unsigned char h = M_RAW;
            fwrite(&h, 1, 1, OUT); fwrite(BUF, 1, BUFN, OUT);
            WROTE += 1 + (long long)BUFN; NRAW++;
        }
    }
    BLKI++; BUFN = 0;
}

static void put(unsigned char v) {
    BUF[BUFN++] = v;
    if (BUFN == BLOCK) flush_block();
}

/* advance the dense cursor to `upto`, writing holes as 0 */
static void pad_to(long long upto) {
    while (NEXT_OUT < upto) { put(0); NEXT_OUT++; HOLES++; }
}

static void leaf(const int *sqs) {
    for (int i = 0; i < N; i++) if (sqs[i] == GOALSQ[PCOL[i]]) return;   /* not live */
    long long be = 0;
    for (int i = 0; i < N; i++) be += (long long)sqs[i] * BEPW[i];
    pad_to(2 * be);
    int a = getchar(), b = getchar();
    if (a == EOF || b == EOF) {
        fprintf(stderr, "FATAL: input stream ended early at live entry %lld\n", LIVE_IN);
        exit(1);
    }
    put((unsigned char)a); NEXT_OUT++;
    put((unsigned char)b); NEXT_OUT++;
    LIVE_IN += 2;
}

static int SQS[MAXP];

static void walk(int depth) {
    if (depth == N) { leaf(SQS); return; }
    int lowest = 0;
    for (int r = 0; r < NRUN; r++)
        if (depth > RUNA[r] && depth < RUNB[r]) { lowest = SQS[depth - 1] + 1; break; }
    for (int s = lowest; s < NSQ; s++) {
        int dup = 0;
        for (int i = 0; i < depth; i++) if (SQS[i] == s) { dup = 1; break; }
        if (dup) continue;
        SQS[depth] = s;
        walk(depth + 1);
    }
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: tb_pack <tag WWW_BBB> <out.tbz> [zlevel]  (raw .dtz on stdin)\n"); return 2; }
    const char *tag = argv[1];
    if (strlen(tag) != 7 || tag[3] != '_') { fprintf(stderr, "FATAL: bad tag %s\n", tag); return 2; }
    for (int i = 0; i < 3; i++) { WC[i] = tag[i] - '0'; BC[i] = tag[4 + i] - '0'; }
    if (argc > 3) ZLEVEL = atoi(argv[3]);

    GOALSQ[0] = NSQ - 1;    /* WHITE -> i9 */
    GOALSQ[1] = 0;          /* BLACK -> a1 */
    fill_pieces();

    DENSE = 2;
    for (int i = 0; i < N; i++) DENSE *= NSQ;
    NBLK = (DENSE + BLOCK - 1) / BLOCK;

    OUT = fopen(argv[2], "wb");
    if (!OUT) { perror("fopen out"); return 1; }
    BLKOFF = (uint32_t *)malloc(sizeof(uint32_t) * (size_t)(NBLK + 1));
    if (!BLKOFF) { fprintf(stderr, "FATAL: no memory for %lld block offsets\n", NBLK); return 1; }

    /* header, then a placeholder offset table rewritten at the end */
    unsigned char hdr[48]; memset(hdr, 0, sizeof hdr);
    memcpy(hdr, "RPSTBZ1", 8);
    hdr[8] = (unsigned char)N;
    for (int i = 0; i < 3; i++) { hdr[9 + i] = (unsigned char)WC[i]; hdr[12 + i] = (unsigned char)BC[i]; }
    hdr[15] = BLOCK_LOG;
    memcpy(hdr + 16, &DENSE, 8);
    memcpy(hdr + 24, &NBLK, 8);
    fwrite(hdr, 1, sizeof hdr, OUT);
    PAYOFF = (long long)sizeof hdr + (long long)sizeof(uint32_t) * (NBLK + 1);
    fseek(OUT, (long)PAYOFF, SEEK_SET);

    walk(0);
    pad_to(DENSE);
    flush_block();
    BLKOFF[BLKI] = (uint32_t)WROTE;

    if (getchar() != EOF) fprintf(stderr, "WARNING: input stream had trailing bytes\n");
    if (BLKI != NBLK) { fprintf(stderr, "FATAL: wrote %lld blocks, expected %lld\n", BLKI, NBLK); return 1; }

    fseek(OUT, (long)sizeof hdr, SEEK_SET);
    fwrite(BLKOFF, sizeof(uint32_t), (size_t)(NBLK + 1), OUT);
    fclose(OUT);

    fprintf(stderr, "%s n=%d live=%lld holes=%lld dense=%lld blocks=%lld (const=%lld zstd=%lld raw=%lld) payload=%lld B  ratio=%.1fx\n",
            tag, N, LIVE_IN, HOLES, DENSE, NBLK, NCONST, NZSTD, NRAW, WROTE,
            WROTE ? (double)DENSE / (double)WROTE : 0.0);
    return 0;
}
