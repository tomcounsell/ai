---
tracking: none
slug: m1-4v-binary-paths
type: bug
status: delivered-not-passed
critique_rounds: 1
review_rounds: 1
---

# 1.4v The kernel names its programs by fixed path, and a denied path stays denied

A bug fix in milestone 1 of [valor-rebuild.md](valor-rebuild.md). It fixes
the code and adds no check, gate, hook, or review step.

## The bug

`core/settings.py:39-40` finds Claude Code with `shutil.which("claude")` on
the kernel's PATH, falling back to `~/.local/bin/claude`.
`core/settings.py:26-28` finds Postgres's tools the same way, as the
directory of the first `pg_dump` on PATH. On this Mac the kernel's PATH
(the shell's) begins:

```
~/.opencode/bin
~/.bun/bin
/opt/homebrew/opt/python@3.12/libexec/bin
~/Library/Python/3.12/bin
~/.local/bin
/opt/homebrew/opt/postgresql@18/bin
/opt/homebrew/bin
...
```

The turn profile (`core/workspace.py:92-110`, `HOME_WRITE_DENIED` and
`SYSTEM_WRITE_DENIED`) denies writes to `~/.local/bin` and `/opt/homebrew`
but not to `~/.opencode/bin`, `~/.bun/bin`, or `~/Library/Python/3.12/bin`,
all three the user's own and ahead of both real programs. A turn wrote a
probe file into the first two (check-3b-review2). A `claude` or `pg_dump`
left there is what the next kernel process resolves: `Settings()` is built
once per process, and every `python -m core` command is a new process.

While weighing the fix, a second hole showed up in the deny rules
themselves: a turn can get around a write or read deny by renaming the
denied path's parent directory. sandbox-exec judges a rename by the source
and destination paths, so moving `~/.local` aside is not a write under
`~/.local/bin`; the turn then makes a new `~/.local`, or a link named
`~/.local` pointing at a directory it fills, and `~/.local/bin/claude`
resolves to its program. Reproduced in the scratchpad with
`/usr/bin/sandbox-exec` and a profile of the same shape
(`(deny file-write* (subpath ".../a/b"))`): `mv .../a .../aa`, then
`ln -s .../evil .../a`, both allowed, and `.../a/b/claude` read the planted
file. The read denies fail the same way: with
`(deny file-read* file-write* (subpath ".../t/secret"))`, a direct read and
a hard link of `secret/f` are refused, but `mv .../t .../t7` and then
reading `.../t7/secret/f` printed the secret. On this machine that would
put `~/.config/valor-kernel/pgpass` (the kernel's password file, through
`kernel_paths()`), `~/.config/gh`, and the `~/Library` entries of
`HOME_DENIED` in a turn's reach, by renaming `~/.config` or `~/Library`.
The fixed path below is only as good as the deny around it, so both are
fixed here.

## Threat model

A turn runs as the machine's user under its sandbox profile. It can write
anywhere the user can that the profile does not deny, which includes
directories on the kernel's PATH and the parents of denied paths. What it
leaves there outlives the turn.

What the turn must not be able to do:

1. Choose a program the kernel runs. `settings.claude` is every later
   turn's program, in every task, and the program of every fresh session,
   the Opus critique and review included: a planted one reads each task's
   workspace and gateway grant and writes the verdict the independent
   check is meant to give. It runs inside each later turn's sandbox, so
   its reach is that turn's, but it is a reach across tasks and across the
   builder and reviewer line. `settings.pg_bin` is worse: `core/backup.py`
   runs `pg_dump`, `pg_restore`, `initdb`, and `pg_ctl` from it outside any
   sandbox, as the owner role that reads the machine cluster; the
   workspace's own Postgres runs them under `service.sb`. The launchd
   backup job carries `VALOR_PG_BIN`, but any `python -m core` process, a
   test session, or a resident kernel started from a shell resolves
   `pg_bin` from its PATH. The scratch cluster `restore` makes is the same
   reach by another door: `start_cluster` made it under `/tmp/vk-*`, which
   a turn can write, then ran `initdb`, `pg_ctl` and the server on it
   outside the sandbox, so a `shared_preload_libraries` or
   `archive_command` a turn appended to its `postgresql.conf` ran as the
   user whatever `pg_bin` named.
2. Undo a deny by moving what holds it. A write deny on a path must stop
   the turn from replacing what is at that path by any route, and a read
   deny must stop it from reading what is there by any name. A disk image
   mounted over a denied directory (`hdiutil attach -mountpoint`, which
   diskarbitrationd performs for the user) is another such route; it
   worked over `~/.local/bin` and over the Cellar's `bin`.

Out of reach, unchanged: root-owned directories (the turn cannot rename
`/opt`, `/Users`, or `/Volumes`); the programs `core/binaries.py` already
names by absolute root-owned path (`git`, `sandbox-exec`, `ps`,
`security`); `redis-server`, found with `shutil.which` on a fixed PATH of
`/opt/homebrew/bin:/usr/local/bin:/usr/bin`, where `/opt/homebrew` is
write-denied and `/usr/local/bin` and `/usr/bin` are root's.

## The two options

**A fixed path, resolved from a known install.** `claude` is
`~/.local/bin/claude`, the native installer's link, which points into
`~/.local/share/claude/versions/`; both are in `HOME_WRITE_DENIED`.
Postgres's tools are `/opt/homebrew/opt/postgresql@18/bin`, under
`/opt/homebrew`, which is in `SYSTEM_WRITE_DENIED` and whose parent `/opt`
is root's. Each keeps its environment override (`VALOR_CLAUDE`,
`VALOR_PG_BIN`). The kernel never consults PATH for either, so no PATH
entry, present or later added, matters. Both defaults are what the code
already falls back to today when the lookup finds nothing.

**Deny writes to every PATH directory ahead of the real program.** The
profile would list each entry of the kernel's PATH that comes before the
program `which` finds. It ties the sandbox to the PATH of whichever
process wrote the profile, while the program is resolved by a later
process whose PATH can differ (a launchd job, a shell, a Claude Code
session each have their own), and a profile is written when the task is
provisioned and reused for every later turn. It also has to deny entries
that do not exist yet (a turn can create `~/.cargo/bin` if PATH names it)
and denies writes the turn may need (`pip install --user` and `bun add -g`
write to `~/Library/Python/3.12/bin` and `~/.bun/bin`). It keeps the
lookup and fences around it.

**Chosen: the fixed path.** It removes the lookup instead of guarding it.
Both defaults already sit under denies that exist, so no new protected set
is added; an override must name a program inside a write-denied install,
as the defaults do, and the docs say so. The ancestor rule and the mount
deny make every deny hold against a rename or a mount.

## The fix

1. **`core/settings.py`.** `_claude()` returns
   `str(Path.home() / ".local/bin/claude")`; `_pg_bin()` returns
   `"/opt/homebrew/opt/postgresql@18/bin"`. `shutil` is no longer
   imported. `VALOR_CLAUDE` and `VALOR_PG_BIN` override as today. A
   program missing at that path fails the way today's fallback path fails
   when the lookup finds nothing: `sandbox-exec` cannot exec `claude` and
   the turn ends with that error, and `backup.py`'s Postgres calls report
   the missing file as they do now.
2. **`core/backup.py`, the scratch cluster.** `start_cluster` makes its
   directory under the `pg_scratch` setting, by default `run/` in the
   kernel key directory (`pg_passfile`'s, which `kernel_paths()` read- and
   write-denies to every profile), making `run/` with mode 700 if missing.
   The socket path there is about 80 bytes, under macOS's 103.
   `projects/valor.toml` sets `VALOR_PG_SCRATCH = "/tmp"` for the kernel's
   own repository built as a task, whose suite runs inside a turn where the
   task's key directory is read only.
3. **`core/workspace.py`, ancestors and mounts.** After the kernel's read and write
   deny and before the process and network rules, `profile()` writes one
   more block: `(deny file-write* (literal <a>) ...)` for every proper
   ancestor `a`, except `/`, of every path it denies in any block
   (`HOME_DENIED` and `work`, the fresh profile's denies,
   `HOME_WRITE_DENIED`, `HOME_WRITE_DENIED_FILES`, `SYSTEM_WRITE_DENIED`,
   the kernel's paths), each path taken as written and as
   `os.path.realpath` resolves it. A `literal` covers the directory entry
   itself: renaming, removing, or changing the mode of that directory.
   It does not cover creating, renaming, or removing entries inside it,
   which are judged by their own paths, so `~/.config/foo` or
   `~/Library/Caches/x` stay writable. Checked in the scratchpad: with
   `(deny file-write* (subpath ".../a/b") (literal ".../a") (literal
   "..."))`, renaming `.../a` or its parent and `chmod` on `.../a` are
   refused; creating a file, making and renaming a directory inside
   `.../a`, and creating a sibling of `.../a` are allowed. The block comes
   last among the file rules so no `rw` allow reopens it. Root-owned
   ancestors are listed too; denying what the user cannot write already
   changes nothing, and listing them keeps the rule one rule with no
   ownership read. Beside it, `(deny file-mount)` in every profile:
   diskarbitrationd honours the caller's sandbox, so `hdiutil attach`,
   `mount` and `diskutil mount` are refused, and no turn mounts anything.
4. **`core/workspace.py`, uv's cache.** `~/.cache/uv` joins
   `HOME_WRITE_DENIED`, and a fresh session's environment carries
   `UV_CACHE_DIR` under its own `tmp/`, since its uv otherwise wrote the
   user's cache. See Critique round 1, the two notes.

Nothing else changes: no refusal, no check at spawn, no ownership test for
`claude` (it is the user's, so `binaries.require` would refuse it; it runs
inside the sandbox, which is what bounds it), and no deny of its own for
the configured programs.

## Done, as evidence

1. `tests/test_settings.py`: with PATH starting at a `tmp_path` directory
   holding executable `claude` and `pg_dump` and the overrides unset,
   `Settings()` gives `~/.local/bin/claude`,
   `/opt/homebrew/opt/postgresql@18/bin`, and `pg_scratch` in the kernel
   key directory; with the overrides set, it gives them.
2. `tests/test_workspace.py`, through `probe` under real sandbox-exec with
   a `tmp_path` home and the turn profile:
   - denied: renaming `home/.local` and `home/.local/share`; the planting
     sequence (rename `home/.local`, link a planted directory in its
     place); the `renamex_np(RENAME_SWAP)` swap of `home/.local` with a
     planted directory; the rename through the case alias `home/.LOCAL`;
     renaming `home/.config`, with the kernel path
     `home/.config/valor-kernel` passed as `kernel=`, and reading its
     `pgpass`; renaming `home/Library` and `home` itself; making
     `home/absent`, the missing ancestor of a kernel path; creating a file
     under `home/.cache/uv`;
   - open: creating `home/.local/x`, `home/.config/x`,
     `home/Library/Caches/x`, making a directory under `home/.config`, and
     renaming one there;
   - `home/.local/bin/claude` and the `pgpass` unchanged;
   - `hdiutil attach -mountpoint home/.local/bin` of a `tmp_path` image
     holding a planted `claude` fails, nothing is mounted, and the real
     `claude` is read.
3. `tests/test_workspace.py`: a scratch cluster's directory is in
   `pg_scratch`, inside the kernel key directory, and a turn profile can
   neither list it nor read or append to its `postgresql.conf`.
4. `tests/test_fresh.py`: the critique session's environment is `PATH` and
   a `UV_CACHE_DIR` inside its own `tmp/`.
5. The full suite green, ruff clean.

No test touches the real home's directories; every rename runs against
the `tmp_path` home.

## Files changed

`core/settings.py`, `core/workspace.py`, `core/backup.py`,
`projects/valor.toml`; `tests/test_settings.py`, `tests/test_workspace.py`,
`tests/test_backup.py`, `tests/test_fresh.py`. Docs: `docs/harnesses.md`
(Files gains the ancestor and mount rule; Known openings gains the fixed
paths, the override rule, uv's cache, the scratch cluster, and unmount),
`docs/machine.md` (where `restore`'s scratch cluster sits).

## Absorbs

Nothing.

## Leaves out

- `harnesses/pi.py`'s `_node()`: 3b's. It follows by the fixed path alone:
  `settings.node` is `/opt/homebrew/bin/node`, under `/opt/homebrew`.
- Unmounting: `umount` of a user mount works even with `file-unmount`
  denied, so a turn can unmount the backup disk. That costs availability
  only (the next dump refuses the missing directory) and is named in Known
  openings.
- The other user-writable places a later unsandboxed process of the user
  might run (`/var/folders` caches, PATH directories the kernel no longer
  reads). The kernel runs nothing from them; Tom's own shell still
  resolves by PATH, which is his and already named in Known openings.
- Denying writes to PATH directories as such: the kernel no longer reads
  PATH for its programs.

## Decided by default

- The fixed path over the PATH deny, for the reasons under The two
  options.
- The ancestor rule is part of this fix, not a follow-up: without it the
  fixed path is replaceable by renaming `~/.local`, and the same move
  opens the read denies, `kernel_paths()` included.
- No deny of its own for the configured programs. An override must name a
  program inside a write-denied install, as the defaults do; Known
  openings says so.
- A directory above a denied path that does not exist when the profile is
  written can no longer be made by a turn. On this Mac every one exists
  but the backup disk's mount point while the disk is out, which only
  macOS makes in root's `/Volumes`; nothing a turn needs lives under any
  of them, so nothing legitimate breaks.
- The scratch cluster's directory is a setting (`pg_scratch`) so the
  kernel's own repository, built as a task, can point it at `/tmp` inside
  its turn.
- A missing program fails as today; nothing is refused before the spawn.

## Questions for Tom

None.

## Critique round 1

Verdict revise (`critique-1-4v.md`). Each finding, and how it is built in:

1. A disk image mounted over a denied directory beat every deny, old and
   new. Built in as `(deny file-mount)` in every profile (The fix, step 3)
   and the mount probe (Done, item 2).
2. The scratch cluster's directory under `/tmp` was turn-writable while
   `initdb`, `pg_ctl` and the server ran on it outside the sandbox. Built
   in as The fix, step 2, and Done, item 3.
3. `kernel_programs()` protected one file, not the install, and had no
   incident of its own. Dropped with its `programs=` parameter and its
   test; the override rule is in Decided by default and Known openings.
4. The swap, the case alias and the mount are in Done, item 2.
5. The missing ancestor line is in Decided by default and in harnesses.md
   beside the ancestor rule.

The two notes, judged:

- `~/.cache/uv`: reachable. The kernel never runs uv itself, but the
  kernel's environment is filled by `uv sync` outside any sandbox (the
  handoff's setup and every builder worktree), from `~/.cache/uv`, which a
  turn could write (only the working session's own `UV_CACHE_DIR` was
  redirected; a fresh session's uv wrote the user's cache). Reproduced in
  the scratchpad: a line appended to `six.py` in a cache's `archive-v0`
  entry reached a fresh project's `.venv` through `uv sync --frozen`
  against a lockfile with hashes. Fixed the same way as the rest:
  `~/.cache/uv` write-denied (The fix, step 4). Write-denying it broke
  uv in a fresh session ("Failed to initialize cache"), so a fresh
  session's environment carries its own cache under its `tmp/`.
- The krb5 preferences file: not reachable. The kernel's libpq is
  psycopg's own build, compiled with `gssencmode=disable` as its default
  (read from `Conninfo.get_defaults()`), and the kernel names no
  `gssencmode`. Under a profile that kills the process on any read below
  `/tmp/libpq.build` (the build's compiled `gss/mech` and `krb5.conf`
  location, also turn-writable), connecting over TCP and over the socket
  with the default touched nothing; only an explicit `gssencmode=prefer`
  or `require` did. No server the kernel connects to asks for GSS: the
  machine cluster is reached on its socket, a task's cluster and a scratch
  cluster have the `pg_hba.conf` the kernel wrote. Homebrew's Postgres
  tools reach every cluster on a socket, where libpq skips GSS. Nothing
  changed.

## Checks, round 1 of 1, at 171b32d87

- Docs: updated, e9863c804. Known openings move to `docs/sandbox-openings.md`.
- Test: gaps. Base 507 passed, head 510 passed, 7 skipped each; ruff
  clean. The ancestor rule, mounts onto a denied path, the fixed program
  paths, the scratch cluster and the uv cache held. `VALOR_PG_SCRATCH=/tmp`
  reaches only turn and fresh-session environments, never the kernel's.
- Review: changes. Governance boolean: no. No invented caps.

Findings both checks reproduced:

1. `(deny file-mount)` refuses a mount with a named mount point only.
   `hdiutil attach` with no `-mountpoint`, and `diskutil mount`, still
   mount at `/Volumes/<label>`. A turn that unmounts the backup disk (an
   accepted opening) can mount its own image under the disk's label;
   `backup.dump` checks only that the directory exists on another device,
   so the next ledger dump lands in the turn's image, which the turn then
   reads from its own `.dmg`. "Costs availability, not contents" and "no
   mount is allowed" are therefore untrue. One probe denying mach-lookup of
   `diskarbitrationd` mounted nothing; it needs verifying.
2. `~/.local/share/uv` (`python`, `tools`) is turn-writable, and the
   user's `uv sync` runs a planted managed interpreter from it outside any
   sandbox. Same class as `~/.cache/uv`: write-deny it and give fresh
   sessions their own `UV_PYTHON_INSTALL_DIR`.
3. Docs: the PATH opening also lets a program early on Tom's PATH choose
   the interpreter `uv sync` gives the kernel.

## Delivery: delivered, not passed

Review rounds are spent. Recommendation to Tom: one more patch for the
three findings. Finding 1 leaks ledger contents, so the patch is needed
before 1.4v merges.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild.md, Tom's feedback of 2026-10-03).

Scope: the three findings under Checks (a mount with no mount point can catch the backup dump, with the `diskarbitrationd` deny verified; `~/.local/share/uv` write-denied and fresh sessions given their own `UV_PYTHON_INSTALL_DIR`; the PATH opening in the docs). First in the order.

## Patch round 1 (2026-10-03)

The three findings under Checks, each with its test. Rebased onto
`valor-cori-rebuild` (3a, 3c and 4.2 there; conflicts in `core/settings.py`,
`HOME_WRITE_DENIED`, and `docs/README.md` resolved by keeping both sides).

1. **A mount with no mount point.** Reproduced: under `(deny file-mount)`
   alone, `hdiutil attach` of an image with no `-mountpoint` mounted it at
   `/Volumes/<label>`. Every profile now also denies the mach name
   `com.apple.DiskArbitration.diskarbitrationd`: `hdiutil attach`, with or
   without a mount point, attaches nothing, and `diskutil mount` refuses.
   Verified as asked, and it showed a second route: `open img.dmg` mounts
   through DiskImageMounter, a process Launch Services starts outside the
   sandbox, and `open` of an app bundle the turn wrote runs that app's code
   outside the sandbox entirely (reproduced: it wrote into a denied
   directory). Denying `com.apple.coreservices.launchservicesd` stops `open`
   but makes Claude Code hang (with a real key and with a dummy one), so the
   profile denies instead the Launch Services database (`com.apple.lsd.*`),
   the quarantine resolver, and Apple events (`com.apple.coreservices.
   quarantine-resolver`, `.appleevents`). Under them `open` by path, by
   `-a`, by bundle id, of the image and of DiskImageMounter by path, and
   AppleScript's `run application` and Finder `open` all launch nothing;
   Claude Code ran a turn with a Bash tool call under that profile; uv, git
   and Python worked. `launchservicesd` staying reachable is named in
   `sandbox-openings.md`. Test:
   `test_a_turn_mounts_nothing_and_opens_nothing_outside_its_sandbox`
   (`hdiutil attach` with no mount point, `diskutil mount`, `open -W` of the
   image, DiskImageMounter by path, and a planted app; nothing attached,
   nothing at `/Volumes/<label>`, the app's marker absent). The existing
   mount-point test now asserts on what is attached, not `hdiutil`'s exit
   code, which is 0 when diskarbitrationd is unreachable. Both tests fail
   with the denies removed.
2. **`~/.local/share/uv`.** Joins `HOME_WRITE_DENIED`; a fresh session's
   environment carries `UV_PYTHON_INSTALL_DIR` under its own `tmp/` (both
   the critique and the services branch, which drops the builder's).
   Tests: creating under `home/.local/share/uv/python` is denied in the
   ancestor test; `test_critique_gets_no_database_credential_and_no_service_port`
   checks both uv directories sit in the session's `tmpdir`.
3. **The PATH opening.** `sandbox-openings.md` says that `uv sync` from
   Tom's shell takes the first fitting Python on PATH when no managed one
   fits (none does here: the project wants 3.14, uv holds 3.10 and 3.13),
   so a `python3.14` a turn left ahead of Homebrew becomes the kernel's
   interpreter. Docs only.

Docs: `harnesses.md` Files gains "No mount, no `open`" and the fresh
session's `UV_PYTHON_INSTALL_DIR`; `sandbox-openings.md` gains
`~/.local/share/uv`, the PATH interpreter, the unmount line restated, and
`launchservicesd`.

Suite: 704 passed, 11 skipped (own DB and ports 6430-6439). Ruff check clean; format check clean but for the
known `docs/bridges/telegram.md` and `docs/plans/m2-1-port.md`.
