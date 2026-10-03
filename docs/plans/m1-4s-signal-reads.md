---
tracking: none
slug: m1-4s-signal-reads
type: bug
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.4s The kernel reads turn files without following links

A bug fix in milestone 1 of [valor-rebuild.md](valor-rebuild.md). It fixes
the code and adds no check, gate, hook, or review step.

## The bug

`signals.collect` (`core/signals.py:44-87`) reads `.valor/*.md`,
`.valor/plan.json`, and `.valor/effects/*.json` with `Path.is_file()`,
`Path.glob()`, and `Path.read_text()`, all of which follow links. The
kernel runs outside the sandbox, as Tom's user. A turn that links
`.valor/question.md` to any file Tom's user can read gets that file's text
written into the ledger as its question; a FIFO at that name hangs the
kernel; a link at `.valor/handled` makes the kernel create directories and
move files wherever it points. `session._plan` reads a path the turn names
the same way. Only `workspace.read_verdict` walks by descriptor with
`O_NOFOLLOW`, and it does not refuse a hard link.

## Threat model

The turn controls everything under its workspace (`<task>/repo`), its
cache, and `state/work`; a fresh session controls its check directory. It
can leave any entry type there: a symbolic link to any path, a hard link to
a file on the same volume, a FIFO, a socket, a directory where a file is
expected, and it can name paths in `plan.json`. The kernel must never read,
stat, list, or create anything outside those directories because of an
entry the turn left, never block on one, and never put the contents of
anything but a regular file with one link into the ledger. An entry it
will not read is recorded as unreadable with a reason, and its contents
are never read.

## The sweep

Every place code outside a sandboxed turn (`core/`, `tools/`,
`harnesses/`) touches a path under a turn-owned directory, line numbers at
`8601ad7e6`:

| Where | What | Follows links |
|---|---|---|
| `core/signals.py:48-49` | `root = workspace/.valor`, `handled = root/handled/<turn>` built as paths | yes: every later call resolves through a linked `.valor` or `handled` |
| `core/signals.py:53-54` | `is_file()`, `read_text()` on `question.md`, `no_question.md`, `done.md` | yes; a FIFO hangs the read |
| `core/signals.py:57-59` | `is_file()`, `read_text()` on `plan.json` | yes; same |
| `core/signals.py:66-70` | `effects.is_dir()`, `effects.glob("*.json")`, `read_text()` on each | yes: lists and reads a linked `effects` directory and linked files |
| `core/signals.py:85-87` | `_move`: `to.parent.mkdir(parents=True)`, `path.replace(to)` | the source name is renamed, not followed; the destination's directories are created and resolved through any link at `.valor`, `handled`, or `handled/<turn>`, so the kernel writes outside the workspace |
| `core/session.py:124-125` | `_plan`: `(workspace/path).is_file()`, `read_bytes()`, `path` taken from `plan.json` | yes, and `..` in `path` is not refused |
| `core/git.py:263` | `is_repo`: `Path(workspace).is_dir()` | yes (stat only) |
| `core/workspace.py:1033-1037` | `fetch_into_mirror`: `.git` `is_symlink()`/`exists()`/`is_dir()`, then `exists()` on `objects/info/alternates`, `objects/info/http-alternates`, `shallow`, `commondir` | the last component of `.git` is checked; the four names are stat'ed through a linked `objects` or `info` directory (existence only) |
| `core/workspace.py:1224-1269` | `read_verdict` on a fresh session's `.valor/verdict.json` | no; but a hard link is read |
| `core/workspace.py:1272-1293` | `_file_away` into `.valor/handled/<turn>/` | no |
| `core/workspace.py:1114-1122` | `fresh_dir`: `is_symlink()`, `rmtree` of a check directory a fresh session wrote | no: `shutil.rmtree.avoids_symlink_attacks` is true on this Python |
| `core/workspace.py:1179-1199` | `write_inputs` into a checkout | no |

Not turn-owned, so left as they are: `reserved_ports` and `sweep` read
`<work>/<task>/ports.json` and list `<work>` (the task directory is not in
the turn's writable set); `_alive` reads `redis.pid` and `_stop_postgres`
stats `postmaster.pid` (written by the service under the service profile);
`clean_partial` and every git call on `kernel.git` (the kernel's mirror).
Git run on the workspace itself (`git.py`, `tools/push_branch.py`,
`broker._git_facts`, `tree_has_valor`, `upload-pack`) is the pinned git
under `git.py`'s rules, not a file read by the kernel. No kernel code reads
a transcript, `state/work`, or the cache; the turn's result comes on its
stdout.

## The fix

One helper in `core/workspace.py`, the shape task 1.4b names
(`docs/plans/m1-4b-runners.md`, The test runner, step 4):

- `read_turn_file(dir_fd, relpath) -> (bytes | None, why | None)`:
  `relpath` split on `/`; an empty, `.`, or `..` component, or an absolute
  path, is refused. Each directory is opened relative to its parent's
  descriptor with `O_RDONLY | O_NOFOLLOW | O_DIRECTORY | O_NONBLOCK |
  O_CLOEXEC`, the file with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK |
  O_CLOEXEC`. `fstat` must say `S_ISREG` and `st_nlink == 1`, and the whole file is
  read. Anything else returns `None` and a reason ("is a link", "is not a
  regular file", "has 2 links"), and nothing is read. No size cap.
- `open_turn_dir(dir_fd, relpath) -> (fd | None, why | None)`: the same
  walk for a directory, used for the workspace root, `.valor`, and
  `.valor/effects`. The workspace root itself is opened with `O_NOFOLLOW`
  too, as `read_verdict` opens its checkout.
- `_file_away(src_fd, name, valor_fd, turn_id, sub=())` (the existing one,
  generalized): makes and opens `handled/<turn>/<sub...>` relative to
  descriptors, refusing a link at any of them, then
  `os.rename(name, name, src_dir_fd=..., dst_dir_fd=...)`, which moves the
  entry itself and never what it points at. A link, FIFO, or hard link is
  moved like a file so it is reported once, never read.

Callers:

1. `signals.collect` opens the workspace, then `.valor`; a missing `.valor`
   is no signals, any other refusal is one `unreadable` entry and nothing
   else is touched. Each text signal and `plan.json` goes through
   `read_turn_file`. `effects` is opened with `open_turn_dir` and
   listed with `os.listdir(fd)`; each `*.json` name is read with
   `read_turn_file`. `Signals` gains `unreadable: list[str]`; a refused
   effect keeps its shape (`{"file", "error"}`); a refused `plan.json`
   sets `plan_error`. `session.record` adds `found.unreadable` to the
   `errors` of `turn.collected`, so no schema change.
2. `session._plan` reads `path` with `read_turn_file` relative to the
   workspace descriptor and compares; a
   refusal is "`path` cannot be read: why".
3. `read_verdict` uses `read_turn_file` (gains `st_nlink == 1`), and its
   size cap goes with the walk, as in 1.4b: `settings.verdict_max_bytes`
   is removed.
4. `fetch_into_mirror` opens `.git` with `open_turn_dir` and looks up each
   of the four names with an `lstat` walk relative to descriptors; an entry
   or a link at any component counts as present and refuses the fetch.
5. `git.is_repo` uses `os.lstat` and `S_ISDIR`, so a linked workspace is
   "not a git repository".

**With 1.4b.** 1.4b lifts `read_verdict`'s walk into `read_turn_file` for
`read_junit`. Whichever task merges second rebases onto the first's helper
and keeps one: if 1.4b is first, this task adds `st_nlink == 1`, the
component refusals, and `open_turn_dir` to its `read_turn_file`; if this
task is first, 1.4b calls this one for `read_junit`. The signature,
`read_turn_file(dir_fd, relpath)` with no size cap, is the same in both.

## Done, as evidence

`tests/test_signals.py` (new, no database), each against a workspace in
`tmp_path` and an "outside" directory beside it holding a regular file with
a marker string and a FIFO. Every collect runs in a thread joined with a
5 second limit, so a hang fails the test instead of stalling the suite.
Following any link to the outside FIFO would block, so a test that returns
proves the link was not opened.

1. `.valor/question.md` linked to the outside marker file, and to the
   outside FIFO: no question, an `unreadable` reason, the marker in no
   field of `Signals`, the link moved to `handled/<turn>/`, the outside
   file unchanged.
2. `.valor/done.md` a hard link to the outside marker file: refused as two
   links, marker absent.
3. A FIFO at `.valor/question.md`, `.valor/plan.json`, and
   `.valor/effects/a.json`: returns, each with a reason, each moved.
4. `.valor` linked to an outside directory holding `question.md` and
   `effects/a.json`: nothing read, the outside directory's listing and
   contents unchanged, no `handled` made there.
5. `.valor/effects` linked to an outside directory holding `a.json`: no
   effect, one reason, the outside directory unchanged.
6. `.valor/handled` and, separately, `.valor/handled/<turn>` linked to an
   outside directory: the signal is read, the move is refused with a
   reason, nothing is created outside.
7. The workspace path itself a link: nothing read.
8. A directory named `question.md` and one named `effects/b.json`: refused,
   not read.
9. Plain files: question, done, plan, and effects collected and moved, unchanged.

Elsewhere:

10. `tests/test_session.py`: an `unreadable` reason appears in
    `turn.collected`'s `errors` (test database), and `_plan` with a
    committed `plan.json` path that is a link to the outside FIFO, and one
    with `..`, returns a reason within the limit.
11. `tests/test_workspace.py`: `read_verdict` refuses a hard-linked
    `verdict.json`; `fetch_into_mirror` refuses a clone whose
    `.git/objects` is a link to a directory holding `info/alternates`;
    `git.is_repo` is false on a linked workspace.
12. The full suite green, ruff clean.

No test opens a real key or password file; the outside files are
`tmp_path` files with a marker.

## Files changed

`core/workspace.py`, `core/signals.py`, `core/session.py`, `core/git.py`,
`core/settings.py` (drops `verdict_max_bytes`);
`tests/test_signals.py` (new), `tests/test_session.py`,
`tests/test_workspace.py`. Docs: the signal channel paragraph in
`docs/architecture.md`, the signal sentence in `core/README.md`, and
`skills/sdlc/channel.md` if it describes what the kernel does with a file
it cannot read.

## Absorbs

`signals._move` and `read_verdict`'s walk become one walk. `session._plan`
stops accepting `..` in a plan path.

## Leaves out

Git's own reads of the workspace, which `git.py` governs. Service
directories (`pg`, `redis`), written by services under their own profile,
not by a turn.

## Decided by default

- An unreadable entry is moved to `handled/<turn>/` like any signal, so it
  is reported once, not on every later turn. A linked `.valor` or
  `effects` directory is not moved: it is reported and left.
- A hard link is refused even when it points inside the workspace; a turn
  writing its own signal never makes one.
- An unreadable `question.md` or `done.md` counts as absent: the task does
  not wait on a question it cannot show Tom.
- No size cap, setting, or stop is added: the reads are made safe and
  nothing else (Tom's standing rule on invented caps).

## Questions for Tom

None.
