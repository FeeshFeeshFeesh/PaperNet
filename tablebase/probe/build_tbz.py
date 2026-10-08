"""Convert every solved .dtz into a seekable .tbz, in parallel, resumably.

The 5-piece signatures are 6.97 GiB of dense index each and there are 35 of them, so this is
~244 GiB streamed through zstd. That is tens of minutes, which on this machine means it MUST
survive being killed: the host memory guard reaps long jobs and a closed lid sleeps the box.
So every signature is independent, the journal records byte counts, and a restart skips any
signature whose .tbz is already complete. Nothing is ever half-written in place -- each table
is built under a .part name and renamed only once tb_pack exits 0.

DISK IS THE REAL CONSTRAINT: 8.5 GiB free against 228 GiB of dense index, which is why
tb_pack streams and why nothing uncompressed is ever written. The guard below stops the build
rather than filling the disk.

Run from PyCharm. No sys.argv; constants below.
"""
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ZST_DIR = os.path.join(os.path.dirname(HERE), "tb5_zst")
TBZ_DIR = os.path.join(HERE, "tbz")
PACK = os.path.join(HERE, "tb_pack")
ZSTD = os.path.expanduser("~/miniconda3/bin/zstd")
JOURNAL = os.path.join(HERE, "build_tbz.journal")

ZLEVEL = 12             # measured 54.6x on the live stream; holes compress on top of that
JOBS = 5                # 8 cores; leave headroom, each job is one zstd + one tb_pack
MIN_FREE_GIB = 2.0      # stop rather than fill the disk
ONLY_N = 0              # 0 = all piece counts, else restrict (5 to finish the big ones)


def tags():
    out = []
    for fn in sorted(os.listdir(ZST_DIR)):
        if not fn.endswith(".dtz.zst"):
            continue
        t = fn[:-len(".dtz.zst")]
        n = sum(int(c) for c in t[0:3] + t[4:7])
        if ONLY_N and n != ONLY_N:
            continue
        out.append((n, t))
    return sorted(out)


def done_tags():
    d = {}
    if os.path.exists(JOURNAL):
        for line in open(JOURNAL):
            p = line.split()
            if len(p) >= 2:
                d[p[0]] = int(p[1])
    good = set()
    for t, nbytes in d.items():
        path = os.path.join(TBZ_DIR, t + ".tbz")
        if os.path.exists(path) and os.path.getsize(path) == nbytes:
            good.add(t)
    return good


def free_gib():
    return shutil.disk_usage(TBZ_DIR).free / (1 << 30)


def launch(tag):
    part = os.path.join(TBZ_DIR, tag + ".part")
    src = os.path.join(ZST_DIR, tag + ".dtz.zst")
    dec = subprocess.Popen([ZSTD, "-dc", src], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
    pk = subprocess.Popen([PACK, tag, part, str(ZLEVEL)], stdin=dec.stdout,
                          stderr=subprocess.PIPE)
    dec.stdout.close()
    return {"tag": tag, "dec": dec, "pk": pk, "part": part, "t0": time.time()}


def reap(job):
    err = job["pk"].stderr.read().decode("utf-8", "replace").strip()
    job["pk"].wait()
    job["dec"].wait()
    tag, part = job["tag"], job["part"]
    final = os.path.join(TBZ_DIR, tag + ".tbz")
    dt = time.time() - job["t0"]
    if job["pk"].returncode != 0:
        print("  %s FAILED rc=%d  %s" % (tag, job["pk"].returncode, err))
        if os.path.exists(part):
            os.remove(part)
        return False
    os.rename(part, final)
    nbytes = os.path.getsize(final)
    with open(JOURNAL, "a") as f:
        f.write("%s %d %.1f\n" % (tag, nbytes, dt))
    print("  %s  %.1f MiB in %.0f s   %s" % (tag, nbytes / (1 << 20), dt, err))
    return True


def main():
    os.makedirs(TBZ_DIR, exist_ok=True)
    have = done_tags()
    todo = [(n, t) for (n, t) in tags() if t not in have]
    print("-----")
    print("%d signatures already complete, %d to build, %d jobs, zstd-%d"
          % (len(have), len(todo), JOBS, ZLEVEL))
    print("free disk %.1f GiB" % free_gib())
    if not todo:
        print("nothing to do")
        return

    running, ok, bad, t0 = [], 0, 0, time.time()
    todo = sorted(todo, reverse=True)          # biggest first: better tail packing
    while todo or running:
        while todo and len(running) < JOBS:
            if free_gib() < MIN_FREE_GIB:
                print("STOP: free disk %.1f GiB below the %.1f GiB guard"
                      % (free_gib(), MIN_FREE_GIB))
                todo = []
                break
            running.append(launch(todo.pop(0)[1]))
        time.sleep(2)
        for job in list(running):
            if job["pk"].poll() is not None:
                running.remove(job)
                if reap(job):
                    ok += 1
                else:
                    bad += 1
    print("-----")
    print("built %d, failed %d, %.0f s, free disk now %.1f GiB"
          % (ok, bad, time.time() - t0, free_gib()))
    total = sum(os.path.getsize(os.path.join(TBZ_DIR, f))
                for f in os.listdir(TBZ_DIR) if f.endswith(".tbz"))
    print("tbz total %.3f GiB across %d tables"
          % (total / (1 << 30), len([f for f in os.listdir(TBZ_DIR) if f.endswith('.tbz')])))


if __name__ == "__main__":
    main()
