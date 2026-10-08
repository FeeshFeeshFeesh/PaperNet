"""R4 post-build: verify, measure compression, upload to GCS. No sys.argv -- knobs at the top.

Runs AFTER cloud_tb5.py's build finishes. Order matters: verify first (a bad set must not be
archived), then measure compression on a SAMPLE (deciding local-vs-cloud storage), then upload
per-file .zst -- per-file, not a tarball, so a single signature can be fetched without pulling
29 GiB, and so a later local copy can hold a subset.

In-region upload (host and bucket are both asia-southeast1) is not charged as egress; a later
download to the Mac is, which is exactly why we compress BEFORE uploading.
"""
import subprocess, time, re
import os

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

GCLOUD  = "/Users/vladloukine/google-cloud-sdk/bin/gcloud"
HOST    = "rps-tb5"
ZONE    = "asia-southeast1-b"
R       = "/var/tmp/rps"
BUCKET  = "gs://papernet-701052690089-results"
PREFIX  = "tb5"
JOURNAL = _ROOT + "/tablebase/cloud_tb5.journal"
# ⚠ NOT tb_cost4.py's 3.1472e10. That model folds DIAGONALLY; tb_solve.c's probe() walks only
# sigma^k x colour swap, so no diagonal fold exists on disk. Independently re-derived by mirroring
# build_sig_list/orbit_rep in scratchpad/tb_expect.py, which reproduces the solver's 60 signatures.
EXPECT_TABLES  = 60                   # stored orbit representatives for <=5 pieces
UPPER_BOUND    = 62943920000          # 6.294392e10 = sum 2*perm(81,n)/div over stored sigs
LIVE_FLOOR     = 0.85                 # live/bound observed ~0.94; illegal + already-decided removed
SAMPLE_N       = 3                    # tables to measure compression on
BLOCK_KIB      = 64                   # probe-friendly independent block size

def say(m):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), m)
    print(line, flush=True)
    open(JOURNAL, "a").write(line + "\n")

def ssh(cmd, timeout=3600):
    p = subprocess.run([GCLOUD, "compute", "ssh", HOST, "--zone", ZONE, "--command", cmd],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def verify():
    say("verifying the finished set")
    rc, out = ssh("cd %s && grep -c DONE_RC tb5.log; grep DONE_RC tb5.log; "
                  "ls tb5/*.dtz | wc -l; du -cb tb5/*.dtz | tail -1 | cut -f1; "
                  "find tb5 -name '*.dtz' -size 0 | wc -l; "
                  # ⚠ tb5.log prints `live <n>` on every SIGNATURE line AND once more on the
                  # final `TOTAL live <n>` line, so a blind grep-and-sum double counts it exactly
                  # (observed 2026-09-27: 118237187232 = 2 x 59118593616, which tripped the
                  # nbytes != live check on a set that was in fact byte-perfect). Sum the
                  # signature lines and read the TOTAL separately, then require the two to agree
                  # -- that turns the bug into an extra independent check.
                  "grep -v '^TOTAL' tb5.log | grep -oE 'live +[0-9]+' | awk '{s+=$2} END{print s+0}'; "
                  "grep -E '^TOTAL live' tb5.log | grep -oE 'live +[0-9]+' | awk '{print $2+0}'; "
                  "grep -oE 'clamped +[0-9]+' tb5.log | awk '{s+=$2} END{print s+0}'; "
                  "grep -oE 'maxDTZ +[0-9]+' tb5.log | awk '{if($2>m)m=$2} END{print m+0}'; "
                  "df -B1 --output=avail %s | tail -1" % (R, R))
    if rc != 0:
        say("verify ssh failed: " + out[-300:]); return False
    ls = [x for x in out.split("\n") if x.strip()]
    say("raw verify output: " + " | ".join(ls)[:400])
    try:
        done_n = int(ls[0]); done_ln = ls[1]
        ntab = int(ls[2]); nbytes = int(ls[3])
        empties = int(ls[4]); live = int(ls[5]); live_total = int(ls[6])
        clamped = int(ls[7]); maxdtz = int(ls[8]); avail = int(ls[9])
    except (IndexError, ValueError):
        say("could not parse the verify output -- build probably still running"); return False

    if done_n == 0 or "DONE_RC=0" not in done_ln:
        say("BUILD NOT CLEANLY FINISHED (%s) -- refusing to archive" % done_ln); return False
    if empties:
        say("FAIL: %d zero-length tables" % empties); return False

    ratio = live / float(UPPER_BOUND)
    say("tables %d/%d  bytes %d (%.2f GiB)  live %d (log TOTAL %d)  bound %d  live/bound %.4f"
        "  disk free %.1f GiB"
        % (ntab, EXPECT_TABLES, nbytes, nbytes / 1024.0**3, live, live_total, UPPER_BOUND, ratio,
           avail / 1024.0**3))

    # Four independent checks. Each one alone is weak; together they pin the set down.
    if ntab != EXPECT_TABLES:
        say("FAIL: %d tables, expected %d stored orbit representatives" % (ntab, EXPECT_TABLES))
        return False
    if live_total != live:
        say("FAIL: the log's own TOTAL live %d disagrees with the sum over signature lines %d"
            % (live_total, live)); return False
    if nbytes != live:
        say("FAIL: on-disk bytes %d != summed live %d -- a table is truncated or padded"
            % (nbytes, live)); return False
    # A clamped entry is a win too long for the 1-byte DTZ encoding, and emit() writes it as
    # v=0 -- INDISTINGUISHABLE FROM A DRAW. That silently corrupts exactly the signal we build
    # this table to measure, so refuse rather than archive a table that lies about wins.
    say("max DTZ over all tables %d (encoding limit: WIN %d / LOSS %d), clamped entries %d"
        % (maxdtz, 127, 128, clamped))
    if clamped:
        say("FAIL: %d clamped entries -- those wins were written as v=0 and are now"
            " indistinguishable from draws. Refusing to archive a table that understates wins."
            % clamped); return False
    if maxdtz > 127:
        say("FAIL: maxDTZ %d exceeds the 1-byte encoding limit" % maxdtz); return False
    if live > UPPER_BOUND:
        say("FAIL: live %d EXCEEDS the legal-position bound %d -- indexing bug" % (live, UPPER_BOUND))
        return False
    if ratio < LIVE_FLOOR:
        say("FAIL: live/bound %.4f below the %.2f floor -- signatures are missing entries"
            % (ratio, LIVE_FLOOR)); return False

    # Compression doubles the footprint transiently (raws are kept on purpose).
    if avail < nbytes // 4:
        say("WARNING: only %.1f GiB free; zstd output needs roughly %.1f GiB"
            % (avail / 1024.0**3, nbytes / 4 / 1024.0**3))
    say("VERIFIED: clean exit, %d tables, bytes==live, live/bound %.4f within envelope"
        % (ntab, ratio))
    return True

def measure():
    # Two different questions: whole-file ratio decides STORAGE and EGRESS; independent-block
    # ratio decides whether a probe can decompress one block instead of a whole table.
    say("installing zstd/xz and measuring compression on the %d largest tables" % SAMPLE_N)
    rc, out = ssh(
        "sudo apt-get -qq -y install zstd xz-utils >/dev/null 2>&1; cd %s/tb5 && "
        "for f in $(ls -S *.dtz | head -%d); do "
        "  raw=$(stat -c %%s $f); "
        "  z3=$(zstd -3 -c -T8 $f 2>/dev/null | wc -c); "
        "  z12=$(zstd -12 -c -T8 $f 2>/dev/null | wc -c); "
        "  x6=$(head -c 200000000 $f | xz -6 -T8 -c 2>/dev/null | wc -c); "
        "  xr=$(head -c 200000000 $f | wc -c); "
        "  head -c 200000000 $f > /tmp/bkin; rm -f /tmp/bk_*; split -b %dK /tmp/bkin /tmp/bk_; "
        "  blk=$(for b in /tmp/bk_*; do zstd -12 -c $b 2>/dev/null | wc -c; done | awk '{s+=$1} END{print s}'); "
        "  blkraw=$(stat -c %%s /tmp/bkin); rm -f /tmp/bk_* /tmp/bkin; "
        "  echo \"$f raw=$raw zstd3=$z3 zstd12=$z12 xz6_on=$xr xz6=$x6 blkraw=$blkraw blk%dk_zstd12=$blk\"; "
        "done" % (R, SAMPLE_N, BLOCK_KIB, BLOCK_KIB), timeout=5400)
    say("compression sample rc=%d" % rc)
    for ln in out.split("\n"):
        if ".dtz raw=" in ln:
            d = dict(kv.split("=") for kv in ln.split() if "=" in kv)
            raw = float(d["raw"])
            say("  %s  raw %.2f GiB | zstd-3 %.2fx | zstd-12 %.2fx | %dKiB-block zstd-12 %.2fx | xz-6 %.2fx (on a %.0f MB head)"
                % (ln.split()[0], raw/1024**3, raw/float(d["zstd3"]), raw/float(d["zstd12"]),
                   BLOCK_KIB, float(d["blkraw"])/float(d["blk%dk_zstd12" % BLOCK_KIB]),
                   float(d["xz6_on"])/float(d["xz6"]), float(d["xz6_on"])/1e6))
    return rc == 0

def upload():
    say("compressing per-file with zstd-12 and uploading to %s/%s (in-region, not egress)" % (BUCKET, PREFIX))
    # `du -sb .` measured the WHOLE directory, so it reported .dtz + .zst (60.2 GB) and looked
    # like zstd had made the set bigger. Measure the .zst files themselves.
    rc, out = ssh("cd %s/tb5 && md5sum *.dtz | sed 's/  */ /' | sort -k2 > tb5_raw.md5 && "
                  "wc -l < tb5_raw.md5 && zstd -12 -T8 -q -f *.dtz && ls *.zst | wc -l && "
                  "du -cb *.zst | tail -1 | cut -f1" % R, timeout=7200)
    say("compress rc=%d  %s" % (rc, " | ".join(x for x in out.split("\n") if x.strip())[:200]))
    if rc != 0:
        return False

    # ⚠ THE BUG THIS REPLACES, and it is the worst kind: it reported success on a failed upload.
    # The old command ended in `| tail -3`, so the pipeline's status was tail's (0) and ssh's rc
    # was 0 no matter what gcloud did. On 2026-09-27 the VM's access scope was
    # devstorage.read_only and the service account held only roles/storage.objectCreator, so
    # every object was refused with `storage.objects.get denied` -- and the script printed
    # "RESCUE COMPLETE -- safe to delete the instance and its disk" over an EMPTY bucket. An
    # exit status that travels through a pipe is not evidence, and absence of an error message
    # is not evidence either. So: carry gcloud's own rc out in a sentinel line, then PROVE the
    # upload by listing the bucket and matching the object count and the byte total.
    rc, out = ssh("cd %s/tb5 && gcloud storage cp *.zst tb5_raw.md5 %s/%s/ 2>&1 | tail -3; "
                  "echo CP_RC=${PIPESTATUS[0]}" % (R, BUCKET, PREFIX), timeout=7200)
    say("upload ssh rc=%d  %s" % (rc, " | ".join(x for x in out.split("\n") if x.strip())[-300:]))
    m = re.search(r"CP_RC=(\d+)", out)
    if rc != 0 or not m or m.group(1) != "0":
        say("FAIL: the copy itself did not succeed (CP_RC=%s) -- nothing may be deleted"
            % (m.group(1) if m else "unreported")); return False

    rc, out = ssh("gcloud storage ls -l %s/%s/ | tail -3; echo LS_RC=${PIPESTATUS[0]}"
                  % (BUCKET, PREFIX), timeout=1800)
    nobj = nbytes = None
    mm = re.search(r"TOTAL:\s+(\d+) objects,\s+(\d+) bytes", out)
    if mm: nobj, nbytes = int(mm.group(1)), int(mm.group(2))
    say("bucket now holds %s objects / %s bytes" % (nobj, nbytes))
    if nobj is None or nobj < EXPECT_TABLES + 1:
        say("FAIL: expected at least %d objects (%d tables + tb5_raw.md5), the bucket reports %s"
            " -- NOT safe to delete anything" % (EXPECT_TABLES + 1, EXPECT_TABLES, nobj))
        return False
    say("UPLOAD PROVEN by listing the bucket: %d objects, %.2f GiB" % (nobj, nbytes / 1024.0**3))
    return True

if __name__ == "__main__":
    say("---- R4 rescue start ----")
    for step in (verify, measure, upload):
        if not step():
            say("STOPPED at %s -- instance left UP on purpose so nothing is lost" % step.__name__)
            break
    else:
        say("RESCUE COMPLETE -- safe to delete the instance and its disk")
