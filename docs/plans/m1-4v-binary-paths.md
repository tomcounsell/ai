---
tracking: none
slug: m1-4v-binary-paths
type: bug
status: planned
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
   `pg_bin` from its PATH.
2. Undo a deny by moving what holds it. A write deny on a path must stop
   the turn from replacing what is at that path by any route, and a read
   deny must stop it from reading what is there by any name.

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
To make it hold for an override as well as the default, the profile
denies writes to the configured program wherever it is, so the protection
follows the setting and not a list of directories; and the ancestor fix
makes every deny, old and new, hold against a rename.

## The fix

1. **`core/settings.py`.** `_claude()` returns
   `str(Path.home() / ".local/bin/claude")`; `_pg_bin()` returns
   `"/opt/homebrew/opt/postgresql@18/bin"`. `shutil` is no longer
   imported. `VALOR_CLAUDE` and `VALOR_PG_BIN` override as today. A
   program missing at that path fails the way today's fallback path fails
   when the lookup finds nothing: `sandbox-exec` cannot exec `claude` and
   the turn ends with that error, and `backup.py`'s Postgres calls report
   the missing file as they do now.
2. **`core/workspace.py`, the programs the kernel names.** A function
   `kernel_programs() -> list[Path]` returns `settings.claude` and
   `settings.pg_bin`, each as configured and as `os.path.realpath`
   resolves it (for the native install, `~/.local/bin/claude` and
   `~/.local/share/claude/versions/<v>`). `profile()` takes
   `programs: list[Path] | None = None` beside `kernel`, defaulting to
   `kernel_programs()`, and adds them to its `(deny file-write* ...)`
   block as `subpath` entries (a `subpath` of a file is that file). 3b
   adds `settings.node` to `kernel_programs()` and drops `_node()`'s PATH
   lookup the same way.
3. **`core/workspace.py`, ancestors.** After the kernel's read and write
   deny and before the process and network rules, `profile()` writes one
   more block: `(deny file-write* (literal <a>) ...)` for every proper
   ancestor `a`, except `/`, of every path it denies in any block
   (`HOME_DENIED` and `work`, the fresh profile's denies,
   `HOME_WRITE_DENIED`, `HOME_WRITE_DENIED_FILES`, `SYSTEM_WRITE_DENIED`,
   the programs, the kernel's paths), each path taken as written and as
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
   ownership read.

Nothing else changes: no refusal, no check at spawn, no ownership test for
`claude` (it is the user's, so `binaries.require` would refuse it; it runs
inside the sandbox, which is what bounds it).

## Done, as evidence

1. `tests/test_settings.py`: with
   PATH set to a `tmp_path` directory holding executable `claude` and
   `pg_dump` ahead of the real ones and `VALOR_CLAUDE` and `VALOR_PG_BIN`
   unset, `Settings()` gives `~/.local/bin/claude` and
   `/opt/homebrew/opt/postgresql@18/bin`; with the overrides set, it gives
   them.
2. `tests/test_workspace.py`, through `probe` under real sandbox-exec with
   a `tmp_path` home, as the existing profile tests do:
   - the parent rename: with `home/.local/bin/claude` present, renaming
     `home/.local` and `home/.local/share` is denied, and so is the whole
     planting sequence (rename `home/.local`, link a new one to a
     directory holding a `claude`), leaving `home/.local/bin/claude`
     unchanged;
   - the read through a renamed parent: with a kernel path at
     `home/.config/valor-kernel/pgpass` (passed as `kernel=`), renaming
     `home/.config` is denied and the file is not read;
   - `home/Library` cannot be renamed;
   - still open: creating `home/.local/x`, `home/.config/x`, and
     `home/Library/Caches/x`, and making and renaming a directory under
     `home/.config`;
   - a program configured outside every listed directory (passed as
     `programs=[tmp_path / "elsewhere" / "claude"]`): writing it, and
     renaming `elsewhere`, are denied.
   The existing probe tests keep their expectations.
3. The full suite green, ruff clean.

No test touches the real home's directories; every rename runs against
the `tmp_path` home.

## Files changed

`core/settings.py`, `core/workspace.py`; `tests/test_settings.py`,
`tests/test_workspace.py`. Docs:
`docs/harnesses.md`, Known openings, which says `claude` lives where a turn
can replace it and the profiles deny writes to named places: it gains the
fixed path, the configured programs in the deny, and the ancestor rule;
the `core/settings.py` module docstring if it describes the lookup.

## Absorbs

Nothing.

## Leaves out

- `harnesses/pi.py`'s `_node()`: 3b's, following step 2.
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
- The configured programs are write-denied wherever an override points,
  so `VALOR_CLAUDE` naming another install is as protected as the
  default.
- A missing program fails as today; nothing is refused before the spawn.

## Questions for Tom

None.
