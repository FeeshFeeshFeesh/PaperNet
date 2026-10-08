/* tb_scan.c -- answer a BATCH of tablebase queries with ONE sequential pass per .dtz file.
 *
 * WHY THIS EXISTS. The .dtz files that tb_solve.c's pack_rec() writes are a COMPACTED DFS STREAM,
 * not an indexable array: squares ascend, used squares are skipped, identical pieces are forced
 * increasing, and any tuple with a piece on its own goal is omitted entirely. So the dense
 * 2*t+stm index that probe() uses does NOT address the file, and random access would need a
 * combinatorial rank function. A batched scan sidesteps that: sort the queries into enumeration
 * order and merge against the stream.
 *
 * KEY TRICK. pack_rec varies depth 0 in its outermost loop, so the emission order is exactly
 * lexicographic on (sq[0]..sq[n-1]). Give each tuple key = sum sq[i] * 81^(n-1-i) and the key is
 * MONOTONE in emission order, so matching is a single uint64 compare per emitted tuple -- fast
 * enough to stream tens of GiB.
 *
 * Config is at the top, no argv (project convention).
 * Query file lines:   TAG n sq0 .. sq(n-1) stm id
 * Output lines:       id TAG stm value          (value 0=draw, 1..127=win dtz, 128..255=loss dtz-127)
 *                    id TAG stm MISSING        (tuple is never emitted: duplicate square, or a
 *                                               piece standing on its own goal)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define NSQ      81
#define MAXP     5
#define MAXQ     2000000
#define BUFBYTES (1 << 22)

static const char *QUERY_FILE = "/var/tmp/rps/scan/tb_queries.txt";
static const char *TB_DIR     = "/var/tmp/rps/tb5";
static const char *OUT_FILE   = "/var/tmp/rps/scan/tb_answers.txt";

static const int GOAL[2] = { 80, 0 };            /* GOAL[WHITE]=i9, GOAL[BLACK]=a1 at 9x9 */

typedef struct { uint64_t key; int stm; long id; } Q;

static char   TAGS[4096][8];
static int    NTAG;
static Q     *QS[4096];
static int    QN[4096];

static int tag_index(const char *t) {
    for (int i = 0; i < NTAG; i++) if (!strcmp(TAGS[i], t)) return i;
    strncpy(TAGS[NTAG], t, 7); TAGS[NTAG][7] = 0;
    QS[NTAG] = malloc(sizeof(Q) * 64); QN[NTAG] = 0;
    return NTAG++;
}
static int qcmp(const void *a, const void *b) {
    const Q *x = a, *y = b;
    if (x->key != y->key) return x->key < y->key ? -1 : 1;
    return x->stm - y->stm;
}

/* ---- per-signature state for the walk ---- */
static int PCOL[MAXP], N, RUNA[MAXP], RUNB[MAXP], NRUN;
static int SQS[MAXP];
static uint64_t POW[MAXP];
static FILE *DF;
static unsigned char BUF[BUFBYTES];
static size_t BUFN, BUFI;
static Q *CURQ; static int CURN, CURP;
static FILE *OUT; static const char *CURTAG;
static long long EMITTED;

static int nextbyte(void) {
    if (BUFI >= BUFN) { BUFN = fread(BUF, 1, BUFBYTES, DF); BUFI = 0; if (!BUFN) return -1; }
    return BUF[BUFI++];
}

static void walk(int depth, uint64_t key) {
    if (depth == N) {
        for (int i = 0; i < N; i++) if (SQS[i] == GOAL[PCOL[i]]) return;
        int v0 = nextbyte(), v1 = nextbyte();
        EMITTED++;
        while (CURP < CURN && CURQ[CURP].key < key) {          /* never emitted -> report */
            fprintf(OUT, "%ld %s %d MISSING\n", CURQ[CURP].id, CURTAG, CURQ[CURP].stm); CURP++;
        }
        while (CURP < CURN && CURQ[CURP].key == key) {
            fprintf(OUT, "%ld %s %d %d\n", CURQ[CURP].id, CURTAG, CURQ[CURP].stm,
                    CURQ[CURP].stm ? v1 : v0);
            CURP++;
        }
        return;
    }
    int lowest = 0;
    for (int r = 0; r < NRUN; r++)
        if (depth > RUNA[r] && depth < RUNB[r]) { lowest = SQS[depth - 1] + 1; break; }
    for (int s = lowest; s < NSQ; s++) {
        int dup = 0;
        for (int i = 0; i < depth; i++) if (SQS[i] == s) { dup = 1; break; }
        if (dup) continue;
        SQS[depth] = s;
        walk(depth + 1, key + (uint64_t)s * POW[depth]);
        if (CURP >= CURN) return;                              /* all queries answered: stop early */
    }
}

int main(void) {
    FILE *f = fopen(QUERY_FILE, "r");
    if (!f) { fprintf(stderr, "no %s\n", QUERY_FILE); return 1; }
    char tag[16]; int n, stm; long id;
    while (fscanf(f, "%15s %d", tag, &n) == 2) {
        int sq[MAXP];
        for (int i = 0; i < n; i++) if (fscanf(f, "%d", &sq[i]) != 1) return 1;
        if (fscanf(f, "%d %ld", &stm, &id) != 2) return 1;
        int ti = tag_index(tag);
        if (QN[ti] && (QN[ti] & (QN[ti] - 1)) == 0 && QN[ti] >= 64)
            QS[ti] = realloc(QS[ti], sizeof(Q) * QN[ti] * 2);
        uint64_t key = 0;
        for (int i = 0; i < n; i++) key = key * NSQ + (uint64_t)sq[i];
        QS[ti][QN[ti]].key = key; QS[ti][QN[ti]].stm = stm; QS[ti][QN[ti]].id = id; QN[ti]++;
    }
    fclose(f);

    OUT = fopen(OUT_FILE, "w");
    for (int ti = 0; ti < NTAG; ti++) {
        qsort(QS[ti], QN[ti], sizeof(Q), qcmp);
        int wc[3], bc[3];
        for (int i = 0; i < 3; i++) { wc[i] = TAGS[ti][i] - '0'; bc[i] = TAGS[ti][4 + i] - '0'; }
        N = 0;
        for (int c = 0; c < 2; c++)
            for (int pt = 0; pt < 3; pt++)
                for (int j = 0; j < (c ? bc[pt] : wc[pt]); j++) PCOL[N++] = c;
        int TYP[MAXP], k = 0;
        for (int c = 0; c < 2; c++)
            for (int pt = 0; pt < 3; pt++)
                for (int j = 0; j < (c ? bc[pt] : wc[pt]); j++) TYP[k++] = pt;
        NRUN = 0;
        for (int i = 0; i < N; ) {
            int j = i;
            while (j < N && PCOL[j] == PCOL[i] && TYP[j] == TYP[i]) j++;
            RUNA[NRUN] = i; RUNB[NRUN] = j; NRUN++; i = j;
        }
        for (int i = 0; i < N; i++) { POW[i] = 1; for (int j = i + 1; j < N; j++) POW[i] *= NSQ; }

        char path[512];
        snprintf(path, sizeof path, "%s/%s.dtz", TB_DIR, TAGS[ti]);
        DF = fopen(path, "rb");
        if (!DF) { fprintf(stderr, "skip %s (no file)\n", TAGS[ti]); continue; }
        BUFN = BUFI = 0; CURQ = QS[ti]; CURN = QN[ti]; CURP = 0; CURTAG = TAGS[ti]; EMITTED = 0;
        walk(0, 0);
        while (CURP < CURN) { fprintf(OUT, "%ld %s %d MISSING\n", CURQ[CURP].id, CURTAG, CURQ[CURP].stm); CURP++; }
        fprintf(stderr, "%s: %d queries, %lld tuples walked\n", TAGS[ti], QN[ti], EMITTED);
        fclose(DF);
    }
    fclose(OUT);
    return 0;
}
