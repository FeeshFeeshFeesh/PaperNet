"""R4: build the <=5-piece RPS tablebase on a GCP c4a host. No argv, per CLAUDE.md.

WHY THE CLOUD. Peak RAM for one 5-piece signature is 14.36 GiB (tb_solve.c retains one byte per
DENSE slot, 2*81^5), which an 8 GB Mac cannot hold, and the live bot must keep its clock anyway.
Measured cost model (validated out of sample to 0.4% on the 4-piece build): 60 signatures,
6.2944e10 entries, 58.6 GiB, 6.78 core-h -> ~0.85 h wall on 8 vCPU, about A$0.36 of CPU.

WHY THERE IS A VALIDATION PHASE. Two code paths have never executed anywhere:
  (a) the CLOUD COMPILER's output -- every verified table so far came from Apple clang on arm64;
  (b) NTHREAD > 1 -- every local build ran single-threaded, because the live bot needed the cores.
Running the real build first would make its 0.85 h the first execution of both. So the host first
rebuilds the 4x4 / 5-piece set, which is verified locally against an INDEPENDENT Python reference
(check_fold44.py: 0 byte mismatches, all 114 removals needing sigma^k>0), at NTHREAD=1 AND NTHREAD=8,
and must reproduce all 60 md5s exactly, twice. A mismatch aborts before the real build spends.

SAFETY. A `shutdown -h` deadman is armed on the host at creation, so the box cannot run away even if
this script dies -- halting stops CPU billing. The build is journaled on the host, so a resume picks
up whole signatures rather than restarting. Teardown is a separate, deliberate step (rescue_and_teardown.py)
because the 58.6 GiB of tables ARE the deliverable and must not be deleted with the instance.
"""
import os, subprocess, sys, time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT    = _ROOT
GCLOUD  = "/Users/vladloukine/google-cloud-sdk/bin/gcloud"
HOST    = "rps-tb5"
ZONE    = "asia-southeast1-b"
MACHINE = "c4a-standard-8"          # 8 vCPU / 32 GiB: the 32 GiB is for the 14.36 GiB peak
IMAGE_FAMILY, IMAGE_PROJECT = "ubuntu-2404-lts-arm64", "ubuntu-os-cloud"
DISK_GB = 120                        # 58.6 GiB of tables + OS + headroom
R       = "/var/tmp/rps"
DEADMAN_MIN = 240                    # hard backstop; expected wall is ~51 min
LOCAL_SOLVER   = os.path.join(ROOT, "tablebase", "tb_solve.c")
LOCAL_MANIFEST = os.path.join(_WORK, "tb44_c.md5")
JOURNAL = os.path.join(ROOT, "tablebase", "cloud_tb5.journal")

def say(m):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), m)
    print(line, flush=True)
    with open(JOURNAL, "a") as f:
        f.write(line + "\n")

def gc(args, timeout=900):
    return subprocess.run([GCLOUD] + args, capture_output=True, text=True, timeout=timeout)

def ssh(cmd, timeout=900):
    r = gc(["compute", "ssh", HOST, "--zone", ZONE, "--quiet", "--command", cmd], timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def scp(local, remote):
    return gc(["compute", "scp", "--quiet", "--zone", ZONE, local, "%s:%s" % (HOST, remote)]).returncode

def host_exists():
    return gc(["compute", "instances", "describe", HOST, "--zone", ZONE]).returncode == 0

# ---------------------------------------------------------------- 1. create
def create():
    if host_exists():
        say("host exists, reusing")
        return True
    say("creating %s (%s, %d GB) in %s" % (HOST, MACHINE, DISK_GB, ZONE))
    r = gc(["compute", "instances", "create", HOST, "--zone", ZONE, "--machine-type", MACHINE,
            "--image-family", IMAGE_FAMILY, "--image-project", IMAGE_PROJECT,
            "--boot-disk-size", "%dGB" % DISK_GB, "--boot-disk-type", "hyperdisk-balanced",
            "--metadata", "serial-port-logging-enable=true"])
    if r.returncode != 0:
        say("CREATE FAILED: " + (r.stderr or "")[-1500:])
        return False
    for i in range(40):
        if ssh("echo up", timeout=120)[0] == 0:
            say("ssh up after %d tries" % (i + 1))
            return True
        time.sleep(15)
    say("ssh never came up")
    return False

# ---------------------------------------------------------------- 2. setup + deadman
def setup():
    # clang, not gcc's cc: every verified table so far came from Apple clang, so matching the
    # toolchain family keeps a validation failure interpretable as THREADING rather than compiler drift.
    say("arming the %d-minute shutdown deadman and installing clang" % DEADMAN_MIN)
    rc, out = ssh("sudo shutdown -h +%d 'tb5 deadman' >/dev/null 2>&1; "
                  "sudo apt-get -qq update && sudo apt-get -qq -y install clang >/dev/null 2>&1; "
                  "mkdir -p %s/tb44 %s/tb5 && clang --version | head -1 && nproc && "
                  "free -g | awk '/Mem:/{print \"mem_gib \" $2}'"
                  % (DEADMAN_MIN, R, R))   # the % binds the WHOLE concatenation: 3 slots, not 2
    say("host: " + " | ".join(x for x in out.split("\n") if x.strip())[:300])
    return rc == 0

# ---------------------------------------------------------------- 3. validate
def validate():
    if scp(LOCAL_SOLVER, R + "/tb_solve.c") != 0: say("scp solver failed"); return False
    if scp(LOCAL_MANIFEST, R + "/tb44_c.md5") != 0: say("scp manifest failed"); return False
    for nthread in (1, 8):
        say("validating: 4x4 / 5 pieces at NTHREAD=%d, must reproduce all 60 md5s" % nthread)
        rc, out = ssh(
            "cd %s && rm -f tb44/*.dtz tb44/journal.txt && "
            "clang -O2 -pthread -DFILES=4 -DRANKS=4 -DMAX_PIECES=5 -DNTHREAD=%d "
            "-DOUT_DIR='\"%s/tb44\"' -o tb44solve tb_solve.c 2>&1 | tail -5 && "
            "./tb44solve > tb44.log 2>&1; tail -2 tb44.log; "
            "cd tb44 && md5sum *.dtz | sed 's/  */ /' | sort -k2 > /tmp/got.md5 && "
            "sed 's/  */ /' %s/tb44_c.md5 | sort -k2 > /tmp/want.md5 && "
            "echo \"tables $(ls *.dtz | wc -l)\" && "
            "if diff -q /tmp/got.md5 /tmp/want.md5 >/dev/null; then echo MD5_ALL_MATCH; "
            "else echo MD5_MISMATCH; diff /tmp/got.md5 /tmp/want.md5 | head -8; fi"
            % (R, nthread, R, R), timeout=1800)
        tail = " | ".join(x for x in out.split("\n") if x.strip())[-400:]
        say("  NTHREAD=%d -> %s" % (nthread, tail))
        if "MD5_ALL_MATCH" not in out:
            say("VALIDATION FAILED at NTHREAD=%d -- NOT starting the real build" % nthread)
            return False
    say("VALIDATED: cloud compiler and the threaded path both reproduce the verified 4x4 set exactly")
    return True

# ---------------------------------------------------------------- 4. launch the real build
def launch():
    say("launching the real 9x9 <=5 build at NTHREAD=8, nohup + journal")
    rc, out = ssh(
        "cd %s && clang -O2 -pthread -DMAX_PIECES=5 -DNTHREAD=8 -DOUT_DIR='\"%s/tb5\"' "
        "-o tb5solve tb_solve.c 2>&1 | tail -5 && "
        "nohup sh -c './tb5solve > tb5.log 2>&1; echo DONE_RC=$? >> tb5.log' "
        ">/dev/null 2>&1 & sleep 5; echo launched; tail -3 tb5.log" % (R, R))
    say("  " + " | ".join(x for x in out.split("\n") if x.strip())[:300])
    return rc == 0

if __name__ == "__main__":
    say("---- R4 cloud build start ----")
    for step in (create, setup, validate, launch):
        if not step():
            say("STOPPED at %s -- host left up for inspection, deadman armed" % step.__name__)
            sys.exit(1)
    say("BUILD RUNNING. poll: %s compute ssh %s --zone %s --command 'tail -5 %s/tb5.log'"
        % (GCLOUD, HOST, ZONE, R))
