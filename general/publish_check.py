"""Decide, mechanically, whether this tree is safe to push -- and print what would go.

WHY THIS IS AN ALLOWLIST. The working tree is ~20 GB and holds a live bot token, two bot
configs, dozens of 13 MB nets, three virtualenvs and 6 GB of tablebase. A deny-list release
is one forgotten pattern away from publishing a credential, and the forgotten pattern is
never the one you thought of. So nothing ships unless a rule here names it, and the deny
list is a second, independent gate on top -- a file matching DENY fails the run even if an
allow rule picked it up.

WHAT IT CHECKS
  1. every denied path is absent from the shipping set, by name and by directory
  2. no shipping file contains a credential-shaped string (and any hit is printed REDACTED)
  3. no shipping file exceeds GitHub's 100 MB hard limit, and the total is sane
  4. the licence files that must accompany the GPL and MIT components are present

It is read-only. It creates nothing, deletes nothing and pushes nothing; it prints a verdict
and a manifest. Run it again after any change to the manifest, and before any push.

Run from PyCharm. No sys.argv; constants below.
"""
import os
import re

# --- paths, anchored on this file so a fresh checkout runs unchanged
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_WORK = os.path.join(_ROOT, "work")   # intermediate outputs; mkdir it once

ROOT = _ROOT

# ---------------------------------------------------------------- what ships
# Directory prefixes, with the extensions allowed under each. "*" means any file.
ALLOW = [
    ("sf19_ports/sf19nn/src", ("*",)),
    ("sf19_ports/sf19nn/Copying.txt", ("*",)),
    ("sf19_ports/sf19nn/LICENSE-sploppy-MIT", ("*",)),
    ("sf19_ports/sf19nn/build.sh", ("*",)),
    ("sf19_ports/sf19nn/NOTICE-modifications.txt", ("*",)),
    ("sf19_ports/sf19rps/src", ("*",)),
    ("sf19_ports/sf19rps/Copying.txt", ("*",)),
    ("sf19_ports/sf19rps/NOTICE-modifications.txt", ("*",)),
    ("sf19_ports/tools", (".py", ".md", ".sh")),
    ("sf19_ports/live", (".py", ".md", "LICENSE-sploppy-MIT")),
    ("sf19_ports/README.md", ("*",)),
    ("tablebase", (".py", ".c", ".md")),
    (".gitignore", ("*",)),
    ("LICENSE", ("*",)),
    ("README.md", ("*",)),
    ("RELEASE.md", ("*",)),
    ("nnue_train", (".py", ".md")),
    ("general", (".py", ".md", ".json")),
]

# ---------------------------------------------------------------- what must never ship
DENY_NAMES = {
    "connect_bot.py", "connect_bot2.py",          # a LIVE bot token lives in one of these
    "rpsbot.conf", "rpsbot2.conf",                # the bot configs, chmod 0600
    ".env", ".DS_Store", "id_rsa", "id_ed25519",
}
DENY_DIRS = (
    "project_files/",        # the scratch tree: holds connect_bot*.py and the configs
    "sep2026/",              # off limits by standing instruction
    ".venv/", "venv/", "site-packages/", "__pycache__/", ".git/",
    "sploppy_repo/",         # a vendored git checkout; ship the submodule or a pointer instead
)
DENY_EXT = (".nnue", ".dtz", ".zst", ".tbz", ".pyc", ".bak", ".log", ".journal",
            ".pt", ".npy", ".bin")

# a credential looks like a long opaque run of base64-ish characters; comment rules and
# session uuids match too, so hits are classified by context rather than counted blindly
CRED = re.compile(r"[A-Za-z0-9_\-]{40,}")
BORING = re.compile(r"^-+$|^=+$|^_+$|^[0-9a-f]{8}-[0-9a-f]{4}")
GITHUB_FILE_LIMIT = 100 * 1024 * 1024

MUST_EXIST = [
    "sf19_ports/sf19nn/Copying.txt",                 # GPL-3.0, Stockfish derivative
    "sf19_ports/sf19nn/LICENSE-sploppy-MIT",         # sploppy's net and trainer
    "sf19_ports/sf19rps/Copying.txt",                # GPL-3.0, Stockfish derivative
    "sf19_ports/sf19nn/NOTICE-modifications.txt",    # GPL-3.0 s.5(a) change notice
    "sf19_ports/sf19rps/NOTICE-modifications.txt",   # GPL-3.0 s.5(a) change notice
    "README.md",
    "RELEASE.md",
    "LICENSE",                                       # MIT, our own code
]


def denied(rel):
    if os.path.basename(rel) in DENY_NAMES:
        return "denied name"
    for d in DENY_DIRS:
        if rel.startswith(d) or ("/" + d) in ("/" + rel):
            return "denied dir %s" % d
    if rel.endswith(DENY_EXT):
        return "denied extension"
    return None


def allowed(rel):
    for prefix, exts in ALLOW:
        if rel == prefix:
            return True
        if rel.startswith(prefix.rstrip("/") + "/"):
            if "*" in exts or rel.endswith(exts):
                return True
    return False


def main():
    ship, blocked, leaks, huge = [], [], [], []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if not denied(os.path.relpath(os.path.join(dirpath, d), ROOT) + "/")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if not allowed(rel):
                continue
            why = denied(rel)
            if why:
                blocked.append((rel, why))       # an allow rule picked up something denied
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            ship.append((rel, size))
            if size > GITHUB_FILE_LIMIT:
                huge.append((rel, size))
            if size < 4 * 1024 * 1024:
                try:
                    with open(full, "r", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            for m in CRED.findall(line):
                                if BORING.match(m):
                                    continue
                                leaks.append((rel, i, CRED.sub("<REDACTED>", line.strip()[:90])))
                                break
                except OSError:
                    pass

    total = sum(s for _, s in ship)
    print("-----")
    print("shipping %d files, %.1f MiB" % (len(ship), total / 1048576.0))
    for rel in MUST_EXIST:
        print("  licence %-44s %s" % (rel, "present" if os.path.exists(
            os.path.join(ROOT, rel)) else "MISSING"))
    print("-----")
    big = sorted(ship, key=lambda kv: -kv[1])[:8]
    print("largest files:")
    for rel, s in big:
        print("  %8.2f MiB  %s" % (s / 1048576.0, rel))

    print("-----")
    if blocked:
        print("BLOCKED -- an allow rule matched a denied path (%d):" % len(blocked))
        for rel, why in blocked[:20]:
            print("  %-60s %s" % (rel, why))
    else:
        print("no denied path is inside the shipping set")

    if leaks:
        print("-----")
        print("%d shipping files contain a long opaque string; each shown REDACTED, "
              "classify before pushing:" % len({r for r, _, _ in leaks}))
        for rel, ln, ctx in leaks[:25]:
            print("  %s:%d  %s" % (rel, ln, ctx))
    else:
        print("no credential-shaped strings in the shipping set")

    if huge:
        print("-----")
        print("OVER GITHUB'S 100 MB FILE LIMIT:")
        for rel, s in huge:
            print("  %8.2f MiB  %s" % (s / 1048576.0, rel))

    ok = not blocked and not huge
    print("-----")
    print("VERDICT: %s" % ("safe to push once the redacted strings above are classified"
                           if ok else "DO NOT PUSH -- see BLOCKED/OVER-LIMIT above"))


if __name__ == "__main__":
    main()
