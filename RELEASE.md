# Release and publication record

How this repository is published, what is deliberately left out, and under which
licences. If you are only here to use the code, read `README.md` instead.

---

## 1. The publishing policy: two independent gates

This repository is published out of a **~20 GB working tree** that contains a
live bot token, two bot configuration files, dozens of 13 MB network files,
three virtualenvs and 6 GB of built tablebase. A deny-list would be one
forgotten pattern away from publishing a credential, and the forgotten pattern
is never the one you thought of.

So nothing ships unless a rule names it, and there are **two gates that must
both agree**, written separately so a mistake in one is caught by the other:

| Gate | What it is |
|---|---|
| `.gitignore` | Denies `*` first, then re-includes named paths. A final hard-deny section comes last, and in gitignore the last matching rule wins -- so nothing in the allow section can re-include a secret. |
| `general/publish_check.py` | An allowlist walk with an independent deny list, a credential-shaped-string scan, a 100 MB file-size check and a licence-presence check. Read-only: it creates, deletes and pushes nothing. |

Run the gate before every push:

    python3 general/publish_check.py

Current verdict: **298 files, 2.9 MiB, no denied path in the shipping set, all
required licence files present.**

The two gates have been **diffed against each other** and agree on all 298 files
exactly. That test is worth repeating after any manifest change, because the
gates are only a safety net if they disagree loudly rather than quietly:

    # run git's view of .gitignore without creating a .git in the project
    export GIT_DIR=/tmp/gatetest GIT_WORK_TREE=$PWD
    mkdir -p $GIT_DIR && git init -q
    git add --dry-run . | sed -E "s/^add '//; s/'$//" | sort > /tmp/git_list
    unset GIT_DIR GIT_WORK_TREE

Then compare `/tmp/git_list` against the gate's own manifest. It found two real
disagreements the first time it was run: `.gitignore` was shipping without being
in the allowlist, and `tablebase/tb5_build/` -- 1.4 MB of solver *source* -- was
being denied as if it were build output.

The scan flags five files for long opaque strings. All five are false positives
and have been classified: C++ template identifiers in `numa.h` and `shm.h`, and
a `learn.microsoft.com` documentation URL. Re-classify them if that list ever
changes, rather than ignoring the warning.

If you add a directory to the project it ships **nothing** until you add it to
both gates. That is deliberate.

---

## 2. Licence inventory

### What ships, and under what terms

| Component | Licence | Obligation met how |
|---|---|---|
| `sf19_ports/sf19rps/` | **GPL-3.0-or-later** (modified Stockfish) | `Copying.txt` + `NOTICE-modifications.txt`; complete source ships |
| `sf19_ports/sf19nn/` | **GPL-3.0-or-later** (modified Stockfish) | `Copying.txt` + `NOTICE-modifications.txt`; complete source ships |
| `sf19_ports/sf19nn/src/rpsnet.*` | **MIT** (derived from sploppy's Rust) | `LICENSE-sploppy-MIT` beside it, and in `live/` |
| `tablebase/`, `nnue_train/`, `general/`, `sf19_ports/tools/`, `sf19_ports/live/`, the docs | **MIT** (the author's own work) | `LICENSE` at the repository root |

GPL-3.0 section 5(a) requires a modified work to carry "prominent notices
stating that you modified it, and giving a relevant date." Upstream Stockfish's
own per-file headers do not say that, so each port now carries a
`NOTICE-modifications.txt` naming the base version (Stockfish 19), the
modification date, and the specific files changed or added.

The two ports are **separate programs**, run as separate processes over UCI.
Nothing GPL is linked into, or copied into, the Python tooling.

### Third-party assets deliberately NOT in this repository

These are recorded because the reason for each one's absence matters:

| Asset | Why it is absent |
|---|---|
| `rps-v6.nnue` | (C) Wilson Wang, all rights reserved. Not ours to redistribute. Verified absent from this tree. |
| UZERE's endgame table | Licence unstated. An unstated licence is not permission, so it was never used, not merely not shipped. |
| sploppy's `example.nnue` | MIT, so it *could* ship, but no weights ship at all -- see below. |
| Henhen dataset | CC0, but it is training data: large and regenerable. |
| HushHush, Fairy-Stockfish, Stockfish upstream | GPL-3.0. Used as separate reference programs for cross-checking (perft, eval parity). No code copied. |
| `rpsfish` | LGPL-3.0-or-later. Read for ideas only; no Rust source copied into this tree. |

**No network weights ship, at all.** `*.nnue` is denied by both gates. The
engine takes a net path as a UCI option at runtime. This keeps the repository
small and removes any question about redistributing someone else's weights.

### How the root MIT licence and the GPL ports coexist

`LICENSE` at the root is **MIT**, (c) 2026 Vladimir Loukine. It covers the
author's own work: the tablebase solver and packer, the probe, the training and
SPRT tooling, the live harness, and these documents.

It does **not** cover the two engine ports, and does not claim to. Each port
directory carries its own `Copying.txt` (GPL-3.0) and its own
`NOTICE-modifications.txt`, and a licence file deeper in the tree governs its
own directory. A single repository holding both is mere aggregation: the ports
are separate programs, invoked as separate processes over UCI, and no GPL code
is copied into or linked with anything MIT here. Anyone redistributing a built
engine binary still owes the full GPL-3.0 obligations for that binary.

The MIT text is kept verbatim, with no added preamble, so GitHub's licence
detector recognises it and so the file stays a standard licence rather than a
bespoke one. The scope statement lives here and in `README.md`, which is the
right place for it.

---

## 3. What does not ship, and why

| Excluded | Size | Why |
|---|---|---|
| `connect_bot.py`, `connect_bot2.py` | tiny | **A live bot token is in one of them.** Denied by name in both gates. |
| `rpsbot.conf`, `rpsbot2.conf` | tiny | Bot configuration, mode 0600. Denied by name, and `*.conf` denied as a class. |
| `project_files/` | 2.9 GB | Holds the above, plus live logs and nets. Denied as a directory. |
| `sep2026/` | -- | Off limits by standing instruction. No shipped script references it any more. |
| `nnue_train/sploppy_repo/` | -- | A vendored git checkout. Ship a submodule or a pointer, never a copy. |
| Built tablebase (`tb5_zst/`, `probe/tbz/`) | 1.0 GB + 2.9 GB | Regenerable in ~19 minutes. See the recipe below. Note `tb5_build/` is *source*, and does ship. |
| Nets, datasets, checkpoints | ~10 GB | `*.nnue *.pt *.npy *.npz *.bin` denied as classes. |
| Compiled engines | ~5 MB each | GPL source ships; binaries are built locally. |
| Virtualenvs, `__pycache__`, logs, journals | ~2 GB | Detritus. |

Result: a **2.9 MiB** repository, 298 files, out of a 20 GB tree.

---

## 4. Rebuilding the tablebase

The tables are regenerable, so they are not in the repository. This is the same
arrangement Syzygy uses: ship the generator, not the gigabytes.

    # 1a. the solver: C, no dependencies
    cc -O3 -o tb_solve tablebase/tb_solve.c

    # 1b. the packer: needs libzstd headers and library. On macOS, either of:
    cc -O3 -I$HOME/miniconda3/include -L$HOME/miniconda3/lib -lzstd \
       -o tablebase/probe/tb_pack tablebase/probe/tb_pack.c
    #   or, with Homebrew:
    #   cc -O3 -I/opt/homebrew/include -L/opt/homebrew/lib -lzstd ...

    # 2. solve every signature. Writes tablebase/tb5_zst/*.dtz.zst, 1.008 GiB.
    #    Resumable: a signature already complete is skipped on restart.
    ./tb_solve

    # 3. convert to the seekable prober format. Writes tablebase/probe/tbz/*.tbz,
    #    2.865 GiB, ~19 min on 8 cores. Knobs at the top of the file.
    python3 tablebase/probe/build_tbz.py

    # 4. verify. 0 mismatches expected.
    python3 tablebase/probe/verify_tbz.py      # spot-check vs the solver's prober
    python3 tablebase/probe/oracle_tbz.py      # independent first-principles oracle

`build_tbz.py` also needs the `zstd` **binary** (separate from the library above);
its path is a constant at the top of the file and points at a conda install by
default.

**Disk, not CPU, is the binding constraint.** The dense index is ~230 GiB and is
never written: `tb_pack` streams it through zstd, and `build_tbz.py` halts rather
than filling the disk (`MIN_FREE_GIB`). It was built on a machine with 8.5 GiB
free.

Step 4 is worth running even if you trust the build. `oracle_tbz.py` re-solves
signatures from the rules alone, without consulting the table, and checks both
exact distance and WDL: 1,100,014 positions across 10 signatures, 0 mismatches.
Seven of them are reachable only *through* the orbit fold, so it is the only
check that validates the fold itself. Its default `TAGS` covers the nine
one-piece-each signatures in about a minute; adding a three-piece signature such
as `001_110` costs ~986k states and tens of minutes of pure Python, but it is
the only way to exercise a capture that lands in a *smaller* signature.

---

## 5. Clean-checkout verification

The shipping set was materialised into an empty directory and exercised there,
to prove it does not depend on anything left behind in the working tree:

| Check | Result |
|---|---|
| Files copied | 296, 2.9 MiB (before `.gitignore`, `LICENSE` and this file were added) |
| `tb_solve.c` compiles | yes, no dependencies |
| `tb_pack.c` compiles | yes, against libzstd |
| Every shipped `.py` compiles | 170 of 170 |
| Every shipped `.sh` parses | yes |
| `rps_rules` FEN round-trip | ok |
| `rps_rules` perft | 36 / 1,293 / 52,758 at depths 1-3 |
| `tb_wdl` imports and exposes the clamp | yes |
| `oracle_tbz` imports | yes, 9 default signatures |

Re-run this after any change to the manifest. The reference perft values above
are the cheapest way to tell whether a build of the rules is correct.

---

## 6. Pre-push checklist

1. `python3 general/publish_check.py` -- expect `VERDICT: safe to push`, and
   re-classify any new long-string hit rather than waving it through.
2. `git status --porcelain | wc -l` -- the count should match the gate's file
   count. A large discrepancy means the two gates disagree; find out why before
   pushing.
3. `git ls-files | grep -iE 'conf$|connect_bot|\.nnue$|\.tbz$|\.dtz'` -- must be
   empty.
4. Confirm `LICENSE` (MIT) is still at the root -- `publish_check.py` fails
   without it, so this is belt and braces.
5. Check the identity your commits will carry: `git config user.name` and
   `git config user.email`. Both are already set globally on this machine, and
   the email will be visible on every public commit. GitHub can hide it -- see
   its "Keep my email address private" setting -- but only if you set that up
   before you push.

---

## 7. Setting it up on GitHub, step by step

`gh` is not installed on this machine, so this uses plain `git` and the web UI.
Nothing below pushes anything until the final step.

### Which of these actually need you

Only three things genuinely require the human, and all three are the same kind
of thing: they need either your GitHub account or a decision that is yours.

| Needs you | Why |
|---|---|
| **Step 1**, creating the empty repository | It is an action in your GitHub account, under your name. There is no local command for it without `gh` and a token. |
| **Step 6**, the credential at the push prompt | A personal access token is a credential. It should never be pasted into a terminal that an agent is driving, and it should not exist in this session's history at all. |
| **Step 7**, flipping it public | Publishing is irreversible in practice -- once it is indexed, taking it down does not unpublish it. That is a call for the author, not an automated step. |

Everything else -- steps 2 to 5, which is `git init`, the dry-run checks, the
staging and the commit -- is local, touches nothing outside this directory, and
is fully undoable with `rm -rf .git`. Those can be done for you on request; they
are listed here so that nothing is a black box.

Two smaller decisions are also yours, and both are easy to miss:

- **The name of the repository.** Step 1 asks for it and it ends up in the URL.
- **Whether your email is public.** Your commits will carry
  `vladimir.loukine@gmail.com`, which is visible on every commit of a public
  repository, forever. GitHub's Settings -> Emails -> "Keep my email addresses
  private" plus a `git config user.email <id>+<user>@users.noreply.github.com`
  avoids that, but it has to be set **before** the commit in step 5, because
  changing it afterwards means rewriting history.

**Step 1 -- create the repository on GitHub, empty.** At
<https://github.com/new>: give it a name, and **do not** let GitHub add a
README, a `.gitignore` or a licence. An empty repository avoids a merge on the
first push. Leave it private for now; you can flip it public later, and that
order is much safer than the reverse.

> **Done, 2026-10-08.** <https://github.com/FeeshFeeshFeesh/PaperNet> --
> created **public** and empty (`size: 0`, no commits, default branch `main`),
> so the first push publishes immediately rather than staging privately. Steps
> 2 to 5 are also done: one commit, `0e121bb`, 298 files, 76,793 insertions,
> remote `origin` set to the HTTPS URL. Only the push remains.
>
> The commit author is `the feesh man
> <170285806+FeeshFeeshFeesh@users.noreply.github.com>`. That email was set
> **repo-locally** (`git config user.email`, no `--global`), so the global
> identity still carries the real address for every other project. `170285806`
> is the GitHub account's numeric id, from
> <https://api.github.com/users/FeeshFeeshFeesh>; the `<id>+<login>` form is
> what GitHub requires for accounts made after mid-2017. Commits still link to
> the account -- only the address is hidden.

**Step 2 -- initialise locally.** From the project root:

    cd ~/PycharmProjects/NeuralNet
    git init
    git branch -M main

**Step 3 -- check what git thinks it will add, BEFORE adding it.**

    git add --dry-run . | wc -l
    git add --dry-run . | grep -iE 'conf|token|connect_bot|\.nnue|\.tbz|\.dtz'

The first number should be 298. The second must print nothing. If either
looks wrong, stop: `.gitignore` is not doing its job, and nothing has been
staged yet, so there is nothing to undo.

You may see a warning about `nnue_train/sploppy_repo` containing its own `.git`.
That directory is denied, so it is not being added; the warning is harmless.

**Step 4 -- stage, and verify the staged set.**

    git add .
    git status --short | wc -l
    git ls-files | grep -iE 'conf$|connect_bot|\.nnue$|\.tbz$|\.dtz' || echo "clean"

**Step 5 -- commit.**

    git commit -m "Intransitive engine, exact <=5-piece tablebase, and the experiment log"

**Step 6 -- connect the remote and push.** Use the URL GitHub showed you:

    git remote add origin https://github.com/<you>/<repo>.git
    git push -u origin main

Git will ask for credentials. GitHub removed password authentication for git in
2021, so your account password will be rejected. There are two ways round it:

**(a) HTTPS + a personal access token.** A token is a long random string that
stands in for a password and can be scoped and revoked. Make one at Settings ->
Developer settings -> Personal access tokens -> Tokens (classic) -> Generate
new token (classic), scope `repo`, then paste it where git asks for
*Password* (username: your login). macOS git uses the `osxkeychain` helper by
default, so it is stored after the first use.

**(b) SSH, which is the better fit here.** No secret is ever typed into a
terminal, nothing expires, and the only thing you paste into the browser is a
*public* key, which is not confidential:

    ssh-keygen -t ed25519 -C "FeeshFeeshFeesh"      # Enter three times
    cat ~/.ssh/id_ed25519.pub                        # paste this into GitHub

Add it at Settings -> SSH and GPG keys -> New SSH key. Then point the remote at
SSH and push:

    git remote set-url origin git@github.com:FeeshFeeshFeesh/PaperNet.git
    ssh -T git@github.com                            # expect "successfully authenticated"
    git push -u origin main

Either way, **the credential is the one part of this that must not pass through
an agent-driven terminal.** A token pasted at a prompt lands in this session's
history; a private key generated locally never has to.

**Step 7 -- check the result in the browser.** Confirm the file count, that
`README.md` renders, and that no `.conf`, no `connect_bot*.py` and no `.nnue` is
listed. Then, if you want it public, flip it in Settings -> General -> Danger
Zone -> Change visibility.

### If something sensitive is ever pushed

Do not just delete the file and commit again -- the content stays in history and
on GitHub's servers. Treat the credential as compromised: **rotate the token
first**, then rewrite history (`git filter-repo`) or delete the repository
outright. Rotation is the step that actually matters; history rewriting is
cleanup.

---

## 8. Known warts, recorded rather than hidden

- **`PY` points at the author's virtualenv.** 17 files hardcode an absolute path
  to the one interpreter on that machine that has `numpy` and `torch`. It is a
  live dependency, not a stale path, which is why it was left alone rather than
  rewritten. Repoint it.
- **The `gcloud` binary is called by absolute path** in 3 files in `general/`,
  because it is not on `PATH` on that machine.
- **`nnue_train/.venv` has a broken editable install**: its `nnue` package maps
  to a directory in a previous project tree that no longer exists, so `import
  nnue` from that venv fails. The venv does not ship; recreate it.
- **`nnue_train/cloud/sprt_pool.FROZEN.py` is read-only on purpose** and still
  contains a path from the session that produced it. It is a frozen record of
  what was actually run, so it was not rewritten. It contains no credential.
- **`sf19_ports/README.md` describes itself as a scratchpad backup**, which it
  was. Its content is still accurate.
- Every other path in the tree is now anchored on its own file's location, so a
  fresh checkout runs without editing.
