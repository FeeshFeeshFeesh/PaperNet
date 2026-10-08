"""Run the no-capture-draw SPRT on a fresh GCP c4a host, because the live bot is
playing and a local match steals its clock.

Staged and idempotent: set the STAGE flags and re-run. Each stage checks for its
own output first, so a re-run after a failure resumes rather than repeats.

What is tested: one net (the live `sf2_wdl/best.nnue`) on BOTH sides, two builds
of the SAME `search.cpp`. `-DNC_DRAW_GRADE` replaces the flat VALUE_DRAW the
200-ply no-capture terminal returns with a signed, hard-capped score, so the
search can rank lines that all end in the draw. NC_DRAW_SIGN picks which way:
  plus  (+1) -- the draw is credited to whoever is ahead: protect the drawn
                position, prefer a certain draw to a fresh equal-material game.
  minus (-1) -- the draw is charged against whoever is ahead: push for the
                conversion.
The base arm is built from the identical source with the flag OFF and must relink
bit-identical to the tree's unpatched build, which is what proves the live bot's
binary is untouched.

PRE-REGISTERED, written down before the run: H0 = 0 Elo, H1 = +20, alpha = beta
= 0.05, cap 1000 pairs, 500 ms/move, 24 opening plies. H0 CLOSES the arm. It does
not license a re-run at another cap, move time or contempt constant.

Nothing with a credential is uploaded: the bundle is engine source, sploppy
source, the net and the seat scripts. connect_bot*.py and rpsbot*.conf are not in
it and must never be.
"""
import datetime
import os
import subprocess
import sys
import time

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

# ---- local paths ----------------------------------------------------------
ROOT    = _ROOT
SPLOPPY = os.path.join(ROOT, "nnue_train", "sploppy_repo")
ENGSRC  = os.path.join(ROOT, "sf19_ports", "sf19nn")
MATCH   = os.path.join(ROOT, "sf19_ports", "data", "ncdraw_match")
VENDOR  = os.path.join(ROOT, "nnue_train", "cloud", "vendor")
NET     = os.path.join(SPLOPPY, "runs", "sf2_wdl", "best.nnue")
LOG     = os.path.join(ROOT, "nnue_train", "cloud", "cloud_ncdraw.log")

# ---- host ----------------------------------------------------------------
GCLOUD  = "/Users/vladloukine/google-cloud-sdk/bin/gcloud"
HOST    = "rps-ncdraw"
ZONE    = "asia-southeast1-b"
MACHINE = "c4a-standard-16"
IMAGE_FAMILY  = "ubuntu-2404-lts-arm64"
IMAGE_PROJECT = "ubuntu-os-cloud"
DISK_GB = 50
R       = "/var/tmp/rps"          # host work root
# IAP needs its own firewall rule (35.235.240.0/20 -> :22) that the default VPC
# does not have; the default VPC DOES allow direct ssh, so tunnel off by default.
TUNNEL  = False

# ---- the match -----------------------------------------------------------
ARM        = "minus"              # "plus" or "minus". PLUS IS CLOSED: SPRT H0 at -23 Elo
                                  # (46 pairs, 2026-09-26). MINUS is the arm the R1 ladder
                                  # gate cleared: flat zone 80/254 -> 44/254 at 800k nodes,
                                  # while plus stayed at 77/254 (= base). Signs differ.
MOVE_MS    = 500
OPEN_PLIES = 24
IN_FLIGHT  = 8                   # MEASURED 2026-09-26: 8 in flight spawns 16 engine
                                 # processes but only ~8 are SEARCHING -- in a pair the
                                 # two sides alternate. So IN_FLIGHT should equal vCPU
                                 # (16), not half it. 8 leaves half the host idle.
                                 # Movetime is wall clock, so a busy host shallows the
                                 # search rather than costing wall time; symmetric
                                 # across arms, so it biases nothing.
ELO0, ELO1 = 0.0, 20.0
CAP        = 1000
SEED       = 20260926
DEADLINE_H = 5.0                  # was 9.0; the comparable plus arm verdicted in 34 min
DEADMAN_MIN = 360                 # sudo shutdown -h +N, in case the driver dies.
                                  # Was 660. This is the ONLY real backstop: DO_SHUTDOWN
                                  # below is a DEAD FLAG, never read by main(). Halting
                                  # stops compute but NOT the 50 GB disk -- still delete.

# ---- stages --------------------------------------------------------------
DO_CREATE   = True    # GCP is empty (verified 2026-09-26), so cold start
DO_SETUP    = True
DO_UPLOAD   = True
DO_BUILD    = True
DO_RUN      = True    # NB: ARM is a top-level constant, NOT an argument (the old
                      # comment here said "chosen on argument" -- it never was).
DO_SHUTDOWN = False


def say(text):
    line = "%s NCDRAW %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), text)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def gc(args, capture=True, timeout=1800):
    cmd = [GCLOUD] + args
    r = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
    return r


def ssh_args():
    a = [GCLOUD, "compute", "ssh", HOST, "--zone", ZONE]
    if TUNNEL:
        a.append("--tunnel-through-iap")
    return a


def ssh(script, timeout=3600, quiet=False):
    """Run a /bin/sh script on the host. Returns (rc, stdout+stderr)."""
    r = subprocess.run(ssh_args() + ["--command", script],
                       capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if not quiet and out.strip():
        for ln in out.strip().split("\n")[-25:]:
            say("  | " + ln)
    return r.returncode, out


def host_exists():
    r = gc(["compute", "instances", "list", "--filter=name=" + HOST, "--format=value(name)"])
    return HOST in (r.stdout or "")


# ---- stage 1: create -----------------------------------------------------
def create():
    if host_exists():
        say("host %s already exists" % HOST)
        return True
    say("creating %s (%s, %s, %d GB) in %s" % (HOST, MACHINE, IMAGE_FAMILY, DISK_GB, ZONE))
    r = gc(["compute", "instances", "create", HOST,
            "--zone", ZONE, "--machine-type", MACHINE,
            "--image-family", IMAGE_FAMILY, "--image-project", IMAGE_PROJECT,
            "--boot-disk-size", "%dGB" % DISK_GB, "--boot-disk-type", "hyperdisk-balanced",
            "--metadata", "serial-port-logging-enable=true"])
    if r.returncode != 0:
        say("CREATE FAILED: " + (r.stderr or "")[-1200:])
        return False
    say("created; waiting for ssh")
    for i in range(40):
        rc, _ = ssh("echo up", timeout=120, quiet=True)
        if rc == 0:
            say("ssh up after %d tries" % (i + 1))
            return True
        time.sleep(15)
    say("ssh never came up")
    return False


# ---- stage 2: setup ------------------------------------------------------
SETUP = r"""
set -e
if [ -f /var/tmp/rps/.setup_done ]; then echo "setup already done"; exit 0; fi
sudo mkdir -p %(R)s && sudo chown -R $(id -u):$(id -g) %(R)s
sudo shutdown -h +%(DEADMAN)d || true
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential g++ curl tar python3 libssl-dev pkg-config >/dev/null
if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
  curl -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path --default-toolchain none
fi
. "$HOME/.cargo/env"
rustup toolchain install 1.95.0 -c rustfmt -c clippy --profile minimal
nproc; g++ --version | head -1; cargo --version
touch %(R)s/.setup_done
echo SETUP_OK
"""


def setup():
    say("setup: apt + rustup 1.95.0, deadman shutdown at +%d min" % DEADMAN_MIN)
    rc, out = ssh(SETUP % {"R": R, "DEADMAN": DEADMAN_MIN}, timeout=2400)
    ok = rc == 0 and ("SETUP_OK" in out or "already done" in out)
    say("setup %s" % ("OK" if ok else "FAILED"))
    return ok


# ---- stage 3: upload -----------------------------------------------------
def upload():
    say("uploading bundle (engine source, sploppy source, net, seats, seq_eval)")
    rc, _ = ssh("mkdir -p %s/bin %s/seq %s/seats" % (R, R, R), timeout=300, quiet=True)
    if rc != 0:
        return False

    def send(cwd, items, dest, label):
        tar = subprocess.Popen(
            ["tar", "czf", "-", "--exclude=target", "--exclude=runs", "--exclude=docs",
             "--exclude=.git", "--exclude=build"] + items,
            cwd=cwd, stdout=subprocess.PIPE)
        r = subprocess.run(ssh_args() + ["--command",
                            "mkdir -p %s && tar xzf - -C %s" % (dest, dest)],
                           stdin=tar.stdout, capture_output=True, text=True, timeout=3600)
        tar.stdout.close(); tar.wait()
        say("  %s -> %s rc=%d" % (label, dest, r.returncode))
        return r.returncode == 0

    ok = send(SPLOPPY,
              ["Cargo.toml", "Cargo.lock", "rust-toolchain.toml", "LICENSE", "Project.md",
               "AGENTS.md", "README.md", "arena", "cli", "engine", "match", "models",
               "search", "xtask", "weights"],
              R + "/sploppy", "sploppy source (MIT, LICENSE kept)")
    ok = ok and send(ENGSRC, ["src", "build.sh"], R + "/sf19nn", "sf19nn source")
    ok = ok and send(os.path.dirname(NET), [os.path.basename(NET)], R + "/nets", "net")
    ok = ok and send(MATCH, ["ncdraw_ref_seat.py", "ncdraw_plus_seat.py", "ncdraw_minus_seat.py"],
                     R + "/seats", "seats")
    ok = ok and send(VENDOR, ["seq_eval.py"], R + "/vendor", "seq_eval")
    say("upload %s" % ("OK" if ok else "FAILED"))
    return ok


# ---- stage 4: build ------------------------------------------------------
BUILD = r"""
set -e
. "$HOME/.cargo/env"
cd %(R)s/sf19nn
cat > linux_build.sh <<'SH'
#!/bin/bash
# bash, not sh: Ubuntu's /bin/sh is dash and `jobs -r` is a bashism.
# Linux aarch64 build of SF19-NN. The Mac build.sh is Apple-specific (xcrun,
# -isysroot, -nostdinc++, -mcpu=apple-m1); everything else is the same.
cd "$(dirname "$0")/src" || exit 1
OPT=${OPT:--O2}
FLAGS="-std=c++17 $OPT -fno-exceptions -DNDEBUG -DIS_64BIT -DUSE_POPCNT -mcpu=native -ffp-contract=off -pthread -Wall"
mkdir -p ../build && rm -f ../build/*.o ../build/*.log
for f in *.cpp; do
  ( g++ $FLAGS -c $f -o ../build/$f.o > ../build/$f.log 2>&1 || echo "FAILED $f" ) &
  while [ $(jobs -r | wc -l) -ge 8 ]; do sleep 0.2; done
done
wait
grep -h -E " error|FAILED" ../build/*.log | sort -u | head -20
if g++ $OPT -pthread -o ../stockfish ../build/*.o > ../build/link.log 2>&1; then echo LINKED; else head -20 ../build/link.log; fi
SH
chmod +x linux_build.sh
for spec in "base:-O2" "minus:-O2 -DNC_DRAW_GRADE -DNC_DRAW_SIGN=-1" "plus:-O2 -DNC_DRAW_GRADE -DNC_DRAW_SIGN=1"; do
  name=${spec%%%%:*}; opt=${spec#*:}
  echo "--- $name ($opt)"
  OPT="$opt" ./linux_build.sh
  cp stockfish %(R)s/bin/sf_$name
done
sha256sum %(R)s/bin/sf_* | cut -c1-16,65-
cd %(R)s/sploppy && cargo build --release -p cli 2>&1 | tail -5 || cargo build --release 2>&1 | tail -5
ls -l %(R)s/sploppy/target/release/bot
echo BUILD_OK
"""


def build():
    say("building 3 engine arms + sploppy bot on the host")
    rc, out = ssh(BUILD % {"R": R}, timeout=3600)
    ok = rc == 0 and "BUILD_OK" in out
    say("build %s" % ("OK" if ok else "FAILED"))
    return ok


# ---- stage 5: run --------------------------------------------------------
HOSTRUN = r'''
import datetime, os, sys, time
R = "%(R)s"
sys.path.insert(0, R + "/vendor")
import seq_eval
NAME = "ncdraw_%(ARM)s_vs_base_%(MS)dms"
LOG  = R + "/ncdraw_sprt.log"
def say(t):
    line = "%%s NCDRAW %%s" %% (datetime.datetime.now().strftime("%%m-%%d %%H:%%M:%%S"), t)
    open(LOG, "a").write(line + "\n"); print(line, flush=True)
WORK = R + "/seq"
os.makedirs(WORK, exist_ok=True)
REPO = R + "/sploppy"
BOT  = REPO + "/target/release/bot"
seq_eval.S, seq_eval.T, seq_eval.REPO, seq_eval.BOT = WORK, REPO, REPO, BOT
budget = ["--move-ms", "%(MS)d", "--player-threads", "1", "--opening-plies", "%(OP)d"]
cand = "rpsi:/usr/bin/python3 -u " + R + "/seats/ncdraw_%(ARM)s_seat.py"
ref  = "rpsi:/usr/bin/python3 -u " + R + "/seats/ncdraw_ref_seat.py"
say("START " + NAME + " seed %(SEED)d " + " ".join(budget))
j = seq_eval.sprt_h2h(NAME, cand, ref, %(SEED)d, say, elo0=%(E0).1f, elo1=%(E1).1f,
                      cap=%(CAP)d, threads=%(IF)d, nice=0, budget=budget)
if not j:
    say("NO VERDICT -- match could not be played"); sys.exit(1)
say("VERDICT %%s: %%s" %% (NAME, j.get("verdict")))
say("WORDS %%s" %% (j.get("words") or seq_eval.words(j, %(E0).1f, %(E1).1f)))
say("pairs %%s penta %%s score %%s elo %%s llr %%s" %% (j.get("pairs"), j.get("penta"),
    j.get("score"), j.get("elo"), j.get("llr")))
'''


def run():
    say("rewriting seats for the host and launching the SPRT (%s arm)" % ARM)
    fix = r"""
set -e
for s in ref plus minus; do
  f=%(R)s/seats/ncdraw_${s}_seat.py
  eng=%(R)s/bin/sf_base
  [ "$s" = plus ]  && eng=%(R)s/bin/sf_plus
  [ "$s" = minus ] && eng=%(R)s/bin/sf_minus
  mkdir -p %(R)s/logs/$s
  sed -i \
    -e "s|^ENGINE    = .*|ENGINE    = \"$eng\"|" \
    -e "s|^NET       = .*|NET       = \"%(R)s/nets/best.nnue\"|" \
    -e "s|^LOG_DIR   = .*|LOG_DIR   = \"%(R)s/logs/$s\"|" "$f"
  grep -E "^ENGINE|^NET |^LOG_DIR|^NAME" "$f"
done
printf 'rpsi\nquit\n' | /usr/bin/python3 -u %(R)s/seats/ncdraw_%(ARM)s_seat.py | head -2
echo SEATS_OK
""" % {"R": R, "ARM": ARM}
    rc, out = ssh(fix, timeout=600)
    if rc != 0 or "SEATS_OK" not in out:
        say("seat rewrite FAILED")
        return False

    body = HOSTRUN % {"R": R, "ARM": ARM, "MS": MOVE_MS, "OP": OPEN_PLIES, "SEED": SEED,
                      "E0": ELO0, "E1": ELO1, "CAP": CAP, "IF": IN_FLIGHT}
    enc = body.encode().hex()
    launch = ("printf '%%s' %s | python3 -c \"import sys,binascii;"
              "open('%s/hostrun.py','wb').write(binascii.unhexlify(sys.stdin.read()))\" && "
              "cd %s && { nohup setsid python3 -u hostrun.py > hostrun.out 2>&1 < /dev/null & } >/dev/null 2>&1; "
              "sleep 3; echo LAUNCHED; tail -5 %s/hostrun.out") % (enc, R, R, R)
    rc, out = ssh(launch, timeout=600)
    say("launch rc=%d" % rc)
    return "LAUNCHED" in out


def main():
    if not os.path.exists(GCLOUD):
        say("gcloud not at %s" % GCLOUD)
        return
    say("=== stages: create=%s setup=%s upload=%s build=%s run=%s"
        % (DO_CREATE, DO_SETUP, DO_UPLOAD, DO_BUILD, DO_RUN))
    if DO_CREATE and not create():
        return
    if DO_SETUP and not setup():
        return
    if DO_UPLOAD and not upload():
        return
    if DO_BUILD and not build():
        return
    if DO_RUN and not run():
        return
    say("launched. poll with:  %s compute ssh %s --zone %s --command "
        "'tail -20 %s/ncdraw_sprt.log'" % (GCLOUD, HOST, ZONE, R))
    say("REMEMBER: delete the host when the verdict is in --  %s compute instances delete %s "
        "--zone %s --quiet" % (GCLOUD, HOST, ZONE))


main()
