# CORRECTED query generator, v3.  QUERIES ARE UNCHANGED FROM v2 (proved byte-identical);
# only the METADATA gains a per-phase no-capture clock.
#
# WHY v3 EXISTS.  `counter` on a ply record describes the board in `rows`, i.e. the POST
# position AFTER our move -- established on 26376 exact reconstructions (0 inexact) by the
# fact that counter == 0 in 483 of 483 records where our move captured and in 0 of 25893
# where it did not.  A perfect bijection.  So using `counter` for the PRE position is wrong:
#   * our move did NOT capture -> PRE clock = counter - 1.
#   * our move DID capture     -> counter == 0 and the PRE clock is NOT recoverable from it;
#     it is the previous POST clock + 1, which can be as high as 199.  Deriving it as
#     max(0, counter-1) = 0 would call a dtz=150 win "clock-feasible" when only dtz<=1 is.
# The clock test `dtz <= 200 - clock` is what turns "the tablebase says WIN" into "a win we
# could actually reach", so this is the difference between a real result and a wrong one.  Two facts established mechanically (tb_phase.py, 25812 vs 0):
#   * `rows` is the position AFTER our move -> the OPPONENT is to move in it.
#   * therefore the position we actually CHOSE from at record k is
#         apply(board(k-1), their_reply(k-1))          with US to move,
#     and that is also the position the engine's eval(k) refers to.
# We emit BOTH phases so a thrown win is identifiable as "PRE says win, POST says draw".
import os, sys, io, re, contextlib, collections
S    = _ROOT + "/tablebase/tb5_build"
LIVE = _ROOT + "/sf19_ports/live"
sys.path.insert(0, LIVE); sys.path.insert(0, S)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import tb_expect
    from tb_query import probe_key
import live_boards

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once
ALL = set("%d%d%d_%d%d%d" % (w[0],w[1],w[2],b[0],b[1],b[2]) for _n, w, b, _bd in tb_expect.stored)

def sq(a):
    m = re.match(r"^([a-i])([1-9])$", str(a).strip())
    return None if not m else (int(m.group(2)) - 1) * 9 + (ord(m.group(1)) - ord('a'))
def parse_mv(mv):
    if not mv or "-" not in str(mv): return None
    a, b = str(mv).split("-")[:2]
    s, d = sq(a), sq(b)
    return None if s is None or d is None else (s, d)
def apply(cells, mv):
    s, d = mv
    c = list(cells)
    if c[s] == 0: return None
    c[d] = c[s]; c[s] = 0
    return c

def emit(qf, mf, cells, stm_is_us, us, nid, gi, rec, g, phase, clock, pre_ok):
    mine   = [(q, c)     for q, c in enumerate(cells) if 1 <= c <= 3]
    theirs = [(q, c - 3) for q, c in enumerate(cells) if 4 <= c <= 6]
    n = len(mine) + len(theirs)
    if not (2 <= n <= 5): return 0
    pcol = [us]*len(mine) + [1-us]*len(theirs)
    ptyp = [c for _q, c in mine] + [c for _q, c in theirs]
    psq  = [q for q, _c in mine] + [q for q, _c in theirs]
    stm  = us if stm_is_us else 1 - us
    r = probe_key(pcol, ptyp, psq, stm, ALL)
    if not r: return 0
    tag, tup, stm2 = r
    qf.write("%s %d %s %d %d\n" % (tag, len(tup), " ".join(str(x) for x in tup), stm2, nid))
    mf.write("%d\t%s\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" %
             (nid, phase, gi, g.get("opponent"), g.get("result"), str(g.get("reason"))[:28],
              rec.get("me"), rec.get("ply"), clock, rec.get("eval"),
              rec.get("depth"), rec.get("our_move")))
    # 13th column: does PRE + our_move == POST exactly?  Written for both phases so the verdict
    # can filter on one field.  POST needs no reconstruction (it IS `rows`), so it is always 1.
    mf.write("\t%d\n" % (1 if pre_ok else 0))
    return 1

qf = open(os.path.join(S, "tb_queries3.txt"), "w")
mf = open(os.path.join(S, "tb_meta3.txt"), "w")
nid = 0; cnt = collections.Counter()
for gi, g in enumerate(live_boards.parse()):
    recs = [r for r in g.get("plies", []) if r.get("rows") and len(r["rows"]) == 9 and r.get("me") is not None]
    for k, rec in enumerate(recs):
        me = rec["me"]; us = 0 if me == "Blue" else 1
        post = live_boards.to_cells(rec["rows"], me == "Blue")
        # POST clock is the record's own counter, by definition of the field.
        nid += 1; cnt["POST"] += emit(qf, mf, post, False, us, nid, gi, rec, g, "POST",
                                      rec.get("counter"), True)
        if k > 0:
            prev = recs[k-1]
            tr = parse_mv(prev.get("their_reply"))
            if tr and prev["me"] == me:
                pre = apply(live_boards.to_cells(prev["rows"], me == "Blue"), tr)
                if pre is not None:
                    # PRE clock: one less than POST unless our move zeroed it, in which case
                    # POST's 0 carries no information and the previous POST clock does.
                    c = rec.get("counter")
                    if c is None:
                        pc = None
                    elif c > 0:
                        pc = c - 1
                    else:
                        p = prev.get("counter")
                        pc = None if p is None else p + 1
                    # RECONCILIATION, the gate on every PRE claim.  PRE is built from the
                    # PREVIOUS record plus their reply, so it is only the position we actually
                    # chose from if PRE + our_move == POST exactly.  It fails for 457 of 26833
                    # pairs (1.7%): 70 have a ply gap of 4 or 6, i.e. records are missing from the
                    # log, and 387 have the right gap but still do not reconcile.  Measured
                    # 2026-09-27: ALL THREE positions that violated the tablebase's own WDL/DTZ
                    # logic were in this set, and there were ZERO violations among reconciled
                    # pairs -- so this flag is exactly the line between real data and noise.
                    om = parse_mv(rec.get("our_move"))
                    ps = frozenset(i for i, x in enumerate(pre) if x)
                    qs = frozenset(i for i, x in enumerate(post) if x)
                    ok = bool(om) and qs == (ps - {om[0]}) | {om[1]}
                    nid += 1; cnt["PRE"] += emit(qf, mf, pre, True, us, nid, gi, rec, g, "PRE", pc, ok)
                    cnt["PRE_ok" if ok else "PRE_broken"] += 1
qf.close(); mf.close()
print("-----  corrected query set  -----")
print("  PRE  (us to move, the position we chose from) : %d" % cnt["PRE"])
print("  POST (them to move, after our move)          : %d" % cnt["POST"])
print("  total queries: %d" % (cnt["PRE"] + cnt["POST"]))
print("  clocks: PRE derived per-record, POST = the record's counter")
print("  PRE reconciled %d, NOT reconciled %d -- the verdict must drop the latter"
      % (cnt["PRE_ok"], cnt["PRE_broken"]))
