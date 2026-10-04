---
tracking: none
slug: m1-4s-signal-reads
type: bug
status: merged
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
`O_NOFOLLOW`, and it does not refuse a hard link or a link at the check
directory above the checkout.

## Threat model

The turn controls everything under its workspace (`<task>/repo`), its
cache, and `state/work`; a fresh session controls its check directory
(`<task>/checks/<stage>-<key>`), the entry itself included, since its
profile's `subpath` rule covers that path. It can leave any entry type
there: a symbolic link to any path, a hard link to a file on the same
volume, a FIFO, a socket, a directory where a file is expected, a sparse
file whose size claims far more than the disk it uses (a 1 PiB file costs
one `truncate`), and it can name paths in `plan.json`. The kernel must never read, stat, list, or
create anything outside those directories because of an entry the turn
left, never block on one, and never put the contents of anything but a
regular file with one link and no holes into the ledger. A file whose
size is backed by disk the turn wrote is only as large as the turn could
make it, and that disk is real and already metered by the machine; a
sparse file is not, so reading it has no bound. An entry it will not read is
recorded as unreadable with a reason, and its contents are never read.

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
| `.valor/screens/` (3c, if merged) | `read_screens` and `_screens_dest` in `core/signals.py` | no; its own descriptor walk, folded into the shared helpers (With 3c) |
| `core/session.py:124-125` | `_plan`: `(workspace/path).is_file()`, `read_bytes()`, `path` taken from `plan.json` | yes, and `..` in `path` is not refused |
| `core/git.py:263` | `is_repo`: `Path(workspace).is_dir()` | yes (stat only) |
| `core/workspace.py:1033-1037` | `fetch_into_mirror`: `.git` `is_symlink()`/`exists()`/`is_dir()`, then `exists()` on `objects/info/alternates`, `objects/info/http-alternates`, `shallow`, `commondir` | the last component of `.git` is checked; the four names are stat'ed through a linked `objects` or `info` directory (existence only) |
| `core/workspace.py:1224-1269` | `read_verdict` on a fresh session's `.valor/verdict.json` | the checkout's last component and below are not followed, but the check directory above it is: a session that swaps `critique-<sha>` for a link makes the kernel read a verdict and create `handled/` wherever it points; and a hard link is read |
| `core/workspace.py:1272-1293` | `_file_away` into `.valor/handled/<turn>/` | no; but an `OSError` from its `os.rename` (a directory planted at the destination name) raises out of the caller |
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
a transcript (1.4d will; it uses `open_turn_file`), `state/work`, or the
cache; the turn's result comes on its stdout.

## The fix

Helpers in `core/workspace.py`, the shape task 1.4b names
(`docs/plans/m1-4b-runners.md`, The test runner, step 4). A relative path
is split on `/`; an empty, `.`, or `..` component, or an absolute path, is
refused. Every directory is opened relative to its parent's descriptor
with `O_RDONLY | O_NOFOLLOW | O_DIRECTORY | O_NONBLOCK | O_CLOEXEC`.

**One rule for a missing entry:** when any component of the path does not
exist, a helper returns `(None, None)`. A reason comes back only for an
entry that exists and is refused. No caller matches reason strings.

- `open_turn_dir(dir_fd, relpath) -> (fd | None, why | None)`: the walk
  for a directory, used for `.valor`, `.valor/effects`, `.git`, and the
  check directory's `<stage>-<key>/repo/.valor`. The turn workspace root
  is opened with `O_NOFOLLOW | O_DIRECTORY`; its parent `<task>/` is the
  kernel's, so the last component is the only one a turn can replace.
- `open_turn_file(dir_fd, relpath) -> (fd | None, why | None)`: the same
  walk, the file opened with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK |
  O_CLOEXEC`; `fstat` must say `S_ISREG`, `st_nlink == 1`, and
  `st_blocks * 512 >= st_size` (no holes). Anything else closes the
  descriptor and returns `None` and a reason ("is a link, not a plain
  file", "is not a regular file", "has 2 links", "is sparse (N bytes
  claimed, M on disk)"); nothing is read. On APFS a file written plainly,
  a file cloned with `cp -c`, and a file with a short seek-made gap all
  report blocks covering their size; `truncate` to a large size, or a
  seek far past the end before a write, reports the holes. The caller owns the descriptor it gets, so a caller can stream,
  hash, or read from an offset.
- `read_turn_file(dir_fd, relpath) -> (bytes | None, why | None)`:
  `open_turn_file`, then the whole file read and the descriptor closed. No
  size cap.
- `_file_away(src_fd, name, valor_fd, turn_id, sub=()) -> (fd | None, why
  | None)`: makes and opens `handled/<turn>/<sub...>` under `valor_fd`
  relative to descriptors, refusing a link at any of them, then
  `os.rename(name, name, src_dir_fd=src_fd, dst_dir_fd=dest)`, which moves
  the entry itself and never what it points at, and returns the open
  destination directory. A missing entry is `(None, None)`. If the
  destination cannot be made or the rename fails, the entry is removed
  with `os.unlink(name, dir_fd=src_fd)` (the link, never its target; an
  empty directory with `os.rmdir`) and a reason is returned, never raised.

**Move first, then read.** Every turn file is filed away into
`handled/<turn>/` before it is read, then read there by descriptor with
`read_turn_file`. A link, FIFO, hard link, or directory is moved like a
file, so it is reported once and never read. When the move is refused, the
entry is removed unread, so nothing is left in `.valor/` for a later turn
to read, and no signal is credited to a turn that did not write it.

**Off the event loop.** The router runs every task on one event loop, so a
slow read of a turn file would stall every task. The kernel's reads of
turn files run in a worker thread: `session.run` calls `signals.collect`
through `asyncio.to_thread`, `session.record` runs `_verdict` (the plan
read and the candidate fetch) the same way, and `fresh.py` calls
`read_verdict` the same way. 1.4b's `read_junit`, 1.4d's transcript
reader, and 3c's `read_screens` (inside `collect`) are called the same
way.

Callers:

1. `signals.collect` opens the workspace, then `.valor` with
   `open_turn_dir`; a missing `.valor` is no signals, a refused one is one
   `unreadable` entry and nothing else is touched. Each text signal and
   `plan.json` is filed away and read. `effects` is opened with
   `open_turn_dir` and listed with `sorted(n for n in os.listdir(fd) if
   n.endswith(".json"))`, so the ledger's order of effects stays
   deterministic; each name is filed away into `handled/<turn>/effects/`
   and read. `Signals` gains `unreadable: list[str]`; a refused effect
   keeps its shape (`{"file", "error"}`); a refused `plan.json` sets
   `plan_error`; a refused text signal is an `unreadable` entry and counts
   as absent. `session.record` adds `found.unreadable` to the `errors` of
   `turn.collected`, so no schema change.
2. `session._plan` reads nothing from the workspace: the payload comes
   from the committed blob (`git.show` at HEAD), and "`path` has changes
   not committed" comes from `git.dirty` naming `path`, as `_candidate`
   uses it.
3. `read_verdict(checks, name, turn_id)` takes the kernel-owned checks
   directory (`lay.checks`) and the check directory's name, opens `checks`
   with `O_NOFOLLOW | O_DIRECTORY`, and holds the `.valor` descriptor
   itself (`open_turn_dir(checks, f"{name}/repo/.valor")`). It files
   `verdict.json` away with `_file_away` and reads it from
   `handled/<turn>/` with `read_turn_file` (gains `st_nlink == 1`). So a
   check directory swapped for a link gives no verdict and a reason, and
   nothing is read or made where it points. `fresh.py` passes `lay.checks`
   and `check_dir.name`. Its size cap goes with the walk, as in 1.4b:
   `settings.verdict_max_bytes` is removed, and a verdict of any size is
   read whole.
4. `fetch_into_mirror` opens the workspace with `O_NOFOLLOW |
   O_DIRECTORY` (a linked workspace refuses the fetch), then `.git` with
   `open_turn_dir`. A missing `.git` keeps the existing fallback: the four
   names are looked up from the workspace descriptor. A refused `.git`
   keeps the existing `FetchRefused` wording, "the clone's .git is not a
   directory (a gitfile moves the real one elsewhere)". For each of the
   four names, a link or a non-directory at an intermediate component
   (`objects`, `info`), or any entry at the last component, refuses the
   fetch; a missing intermediate component means the name is absent.
5. `git.is_repo` uses `os.lstat` and `S_ISDIR`, so a linked workspace is
   "not a git repository".

**Shared with 1.4b, 1.4c, 1.4d, and 3c.** The helpers that survive are
this plan's: `open_turn_dir(dir_fd, relpath)`, `open_turn_file(dir_fd,
relpath)`, and `read_turn_file(dir_fd, relpath)`, each `(value | None, why
| None)` with `(None, None)` for a missing entry, and no `max_bytes`, no
`then=` callback, and no size setting. Whichever task merges second
rebases onto the first's helpers and keeps one set.

- 1.4b lifts `read_verdict`'s walk for `read_junit`. If 1.4b is first, this
  task replaces its `read_turn_file(dir_fd, relpath, max_bytes, *, then=)`
  with the three above, deletes `settings.junit_max_bytes` and the
  `then=` lambda in `read_verdict`, and edits `m1-4b-runners.md`'s step 4
  to the same signature. If this task is first, 1.4b calls these. 1.4b's
  review and docs checks read from the same check-directory layout, so
  `read_junit` walks from `lay.checks` the same way `read_verdict` does.
- 1.4c reads `out/result.json` and `out/junit.xml` with `read_turn_file`;
  `m1-4c-verifier.md` says "1.4b's walk" with no bounds.
- 1.4d streams and hashes each transcript in chunks from the descriptor
  `open_turn_file` gives, from an offset, with no size cap, and lists a
  subagent directory with `open_turn_dir`. Its plan's citation of
  `read_turn_file(dir_fd, relpath, max_bytes)` is stale; the lead tells
  1.4d.

**With 3c.** 3c adds `signals.screens = read_screens(workspace, turn_id)`
to `collect`, with its own descriptor walk and `_screens_dest`, a copy of
`_file_away`'s handled walk. Whichever of the two lands second makes
`collect` keep `screens`, and `read_screens`:

- opens `.valor/screens` with `open_turn_dir` and lists it sorted;
- files each entry away with `_file_away(..., sub=("screens",))`, then
  hashes it from the descriptor `open_turn_file` gives in
  `handled/<turn>/screens/`, recording `{name, bytes, sha256}` and never
  the bytes;
- records a refused entry as `{name, refused: why}`, and an entry whose
  move is refused counts as unreadable like any other signal (removed
  unread, not left for the next turn);
- drops `_screens_dest`.

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
   outside directory, and a non-empty directory planted at
   `.valor/handled/<turn>/question.md`: the move is refused with a reason,
   nothing is read (the question's text in no field of `Signals`),
   `collect` raises nothing, nothing is created outside, and the next
   collect with a new turn id finds no question.
7. The workspace path itself a link: nothing read.
8. A directory named `question.md` and one named `effects/b.json`: refused,
   not read.
9. No `.valor` at all: an empty `Signals` with no `unreadable` entry. Plain
   files: question, done, plan, and effects collected and moved.

Elsewhere:

10. `tests/test_session.py`: an `unreadable` reason appears in
    `turn.collected`'s `errors` (test database), and `_plan` with a
    committed `plan.json` path that is a link to the outside FIFO, and one
    with `..`, returns a reason within the thread's 5 second join.
11. `tests/test_workspace.py`: `read_verdict` refuses a hard-linked
    `verdict.json`; a `critique-<sha>` that is a link to an outside
    directory holding `repo/.valor/verdict.json` gives no verdict, a
    reason, and nothing created outside; `fetch_into_mirror` fetches from a
    plain clone, refuses a clone whose `.git/objects` is a link to a
    directory holding `info/alternates`, refuses a linked workspace, and
    with no `.git` still looks up the four names in the workspace;
    `git.is_repo` is false on a linked workspace; a sparse `verdict.json`
    is refused unread.
11a. `tests/test_signals.py`: a `question.md` truncated to 1 PiB is
    refused as sparse; a 3 MB `done.md` written plainly and an effect file
    cloned with `cp -c` are read.
12. `tests/test_fresh.py`: the `big` case and its act in
    `tests/scripted.py` are deleted (a big verdict is read whole); the
    other cases' reasons match the helpers' wording.
13. The full suite green, ruff clean.

No test opens a real key or password file; the outside files are
`tmp_path` files with a marker.

## Files changed

`core/workspace.py`, `core/signals.py`, `core/session.py`, `core/git.py`,
`core/fresh.py` (the `read_verdict` call), `core/settings.py` (drops
`verdict_max_bytes`); `tests/test_signals.py` (new),
`tests/test_session.py`, `tests/test_workspace.py`, `tests/test_fresh.py`,
`tests/scripted.py`. Docs: the signal channel paragraph in
`docs/architecture.md`, the signal sentence in `core/README.md`,
`skills/sdlc/channel.md` if it describes what the kernel does with a file
it cannot read, `docs/plans/m1-4-checks.md` (now lines 333 and 554, and
`m1-4a-provisioning.md`'s signal-read line),
where "at most 256 KB" and "over 256 KB" become "a regular file with one
link", and `docs/plans/m1-4c-verifier.md` line 215, where "1.4b's walk and
bounds" becomes "1.4b's walk".

## Absorbs

`signals._move` and `read_verdict`'s walk become one walk. `session._plan`
stops accepting `..` in a plan path.

## Leaves out

Git's own reads of the workspace, which `git.py` governs. Service
directories (`pg`, `redis`), written by services under their own profile,
not by a turn.

## Decided by default

- Every turn file is moved to `handled/<turn>/` before it is read. An
  unreadable entry is moved like any signal, so it is reported once, not on
  every later turn. A linked `.valor` or `effects` directory is not moved:
  it is reported and left.
- A turn file whose move is refused is removed unread (`unlink` by
  descriptor, never its target) and reported: nothing is read, and nothing
  is left for a later turn to read.
- A hard link is refused even when it points inside the workspace; a turn
  writing its own signal never makes one.
- An unreadable `question.md` or `done.md` counts as absent: the task does
  not wait on a question it cannot show Tom.
- No size cap, setting, or stop is added: the reads are made safe and
  nothing else (Tom's standing rule on invented caps). A file is read to
  the size `fstat` saw when it was checked, its own size, so a file grown
  after the check is not read past it.
- Text that is not UTF-8 is decoded with replacement characters. At the
  base, `read_text` raised on it out of `collect`; the decode fixes that
  crash.
- `git.dirty` takes no size shortcut: the fresh index caches no sizes, so
  git hashes every tracked file, and a huge or sparse tracked file (20 GiB
  in the review's probe) runs until `git_timeout_s` and is refused with a
  timeout reason instead of "uncommitted changes". Left as is (the lead's
  call): the turn inflicts it on itself, the wait is in a worker thread off
  the loop, and the outcome is still a refusal.

## Questions for Tom

None.

## Critique round 1 (of 2): revise

Each finding is built in.

1. `read_verdict` followed a link at the check directory, which the fresh
   session controls: it walks from the kernel-owned `lay.checks` (caller
   3, test 11).
2. Dropping `verdict_max_bytes` broke `tests/test_fresh.py`'s `big` case
   and left `m1-4-checks.md` describing the cap: both listed (test 12,
   Files changed).
3. `fetch_into_mirror` lost the missing-`.git` fallback and the gitfile
   wording: both kept (caller 4).
4. A failing `os.rename` raised out of `collect` and `read_verdict`:
   `_file_away` returns a reason.
5. A signal whose move was refused was read again: decided by default.
6. Effects listed sorted; test 10's limit named as the 5 second join.

## Critique round 2 (of 2): revise

The rounds are spent; each finding is built in.

1. 3c rewrites `collect` with `read_screens` and was not mentioned: With
   3c, and `.valor/screens` in the sweep.
2. `read_turn_file -> bytes` could not serve 1.4d's streamed transcripts
   or 3c's hashes: `open_turn_file` returns the descriptor and
   `read_turn_file` wraps it (The fix; Shared with 1.4b, 1.4c, 1.4d, and
   3c).
3. The read-then-move order left a refused file for the next turn to read
   and credit to itself: every file is moved first and read in
   `handled/<turn>/`; a refused move removes the entry unread (Move first,
   then read; test 6).
4. The `fetch_into_mirror` wording refused every fetch: an intermediate
   link or non-directory, or any entry at the last component, refuses;
   test 11 fetches from a plain clone.
5. Missing and refused were not told apart: a missing entry is `(None,
   None)` (The fix; test 9).
6. `m1-4-checks.md`'s Decided by default verdict line named; `m1-4c-verifier.md:215` edited; the 1.4b
   branch says what this task deletes if 1.4b lands first; `read_verdict`
   holds the `.valor` descriptor itself.

## Build

Built as planned, with one ordering change: `fetch_into_mirror` checks the
clone's `.git` and the four names before it asks git about the clone's
config, so a linked workspace is refused before git reads anything there.
The suite at the plan commit: 507 passed, 7 skipped. With the build: 527
passed, 7 skipped, 20 tests added (13 in `tests/test_signals.py`, 3 in
`tests/test_session.py`, 5 in `tests/test_workspace.py`, the `big` case
deleted from `tests/test_fresh.py`). Two `tests/test_fresh.py` tests that
start a task's own Postgres or Redis failed once in the full run while
other suites ran on the machine and passed when run again on their own.
Ruff check and format are clean.

### Finding from 3c's review: sparse files

3c's blind review showed a turn can leave a sparse file that uses no disk
but claims 1 PiB, and the kernel was still hashing it after 20 seconds on
the router's event loop, which stalls every task; `read_turn_file` reads
whole files, so verdicts and junit were open to the same attack. Built in:
`open_turn_file` refuses a file whose blocks on disk do not cover its size,
from the `fstat` it already does, as unreadable with the sizes in the
reason. Checked on APFS: an empty file, a 1 byte file, a 3 MB file of
random bytes and one of zeros, `cp -c` clones of both, and a file with a
10 MB seek-made gap all report blocks covering their size; `truncate` to
1 TiB, and a write after a 200 MB or 1 TiB seek, report the holes. A file
APFS stores compressed would also report fewer blocks than its size and be
refused; a turn writing files plainly never makes one. The reads of turn
files moved off the event loop (Off the event loop). Tests: 11a, and a
sparse verdict under 11. The full suite with this: 527 passed, 2 failed, 7 skipped, the
two being tests that start a task's own Postgres, which failed in the full
run and passed on their own; ruff clean.

Compressed files are refused like sparse ones: a file APFS stores
compressed reports fewer blocks than its size, and `UF_COMPRESSED` cannot
tell an honest one apart, since a turn owns its files and can write a
decmpfs header that claims any size and set the flag itself. A turn
writing plainly never makes a compressed file.

## Patch round 1 (review: changes; test: gaps)

1. `session._plan` read the turn's copy of the plan: removed. The payload
   is the committed blob's; "has changes not committed" comes from
   `git.dirty`. Test: a committed link to a FIFO is never opened, a `..`
   path is "not committed at HEAD", and a plan changed after its commit is
   refused.
2. `_is_link` stat'ed an entry after a failed open: removed. The errno
   answers it: `ELOOP` on a file is "is a link, not a plain file"; with
   `O_DIRECTORY`, macOS answers a link with `ENOTDIR` as for a file, so
   both are "is not a plain directory".
3. `read_turn_file` read to EOF after checking the size: it reads to the
   size `fstat` saw at the check and no further. Test: a file grown after
   the check is read only to its checked size.
4. An entry gone between its move and its open was dropped silently: it
   is recorded as unreadable ("was gone from handled/<turn> when it was
   read"), as is an effect listed and gone before its move. Test.
5. The reads off the event loop are tested: `tests/test_fresh.py` drives a
   critique and a build and fails if `read_verdict`, `collect`, or
   `_verdict` runs on the loop (checked by reverting each call).
6. Tests for an entry swapped for a link after its move and for text that
   is not UTF-8.
7. `m1-4-checks.md`'s verdict lines say "and no holes"; Decided by default
   records the UTF-8 decode; the build record records compressed files.

The full suite after patch round 1: 535 passed, 7 skipped; ruff check and format clean.

## Checks after patch round 1, at cbfd5a055 (review round 2 of 2)

- Test: `gaps`. The earlier gaps are closed by probes (growth past the
  checked size, a vanishing entry, reads off the loop, swaps after the
  move, text that is not UTF-8). The suite passed apart from three
  "Postgres did not start" failures, each passing alone.
- Review: `changes`; governance boolean no; no invented caps.
- Docs: `updated`, 0a5a53380 on `m1-4s-docs2`.

The finding both raised: `session._plan` compares `git status --porcelain`
lines with the plan path as plain text. Git quotes a path holding a space
or a character outside ASCII, and names a rename's old path on the left of
` -> `, so a plan with such a path, or a plan staged as renamed away, is
accepted with changes not committed. The ledger still holds the committed
blob, so nothing wrong is recorded; the refusal the plan promises does not
happen. No test covers the three cases.

## Delivery: delivered-not-passed

Review rounds are spent. Recommendation: accept one more patch that runs
`git status --porcelain --untracked-files=all -- ':(literal)<path>'` and
treats any output as changes not committed, with tests for a space, a
character outside ASCII, and a staged rename; rerun the three checks; merge
if they pass. Everything else in the task passed both checks.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild-feedback.md, Tom's feedback of 2026-10-03).

Scope: the Delivery's recommendation (literal-pathspec porcelain check; tests for a space, a character outside ASCII, and a staged rename).

## Patch round 2 (the Delivery's recommendation)

1. `session._plan` matched `git status --porcelain` lines against the plan
   path as text. It now asks git about that path alone: `git.dirty` takes
   a path and runs `git status --porcelain --untracked-files=all -- ':(literal)<path>'`,
   and any output is "has changes not committed". The pathspec is literal,
   so a plan named like a glob is asked about as itself.
2. Tests (`tests/test_session.py`): a plan whose path holds a space, one
   whose path holds a character outside ASCII, each edited after its
   commit, and a plan staged as renamed away, are all refused; each of the
   three failed on the code before the patch. A plan named `docs/plans/*.md`
   is not refused for another plan's change, and a plain plan is not
   refused for a sibling `p.md.bak`; the glob case fails without `:(literal)`.

The full suite after patch round 2: 661 passed, 1 failed, 8 skipped, the
failure being a test that starts a task's own Postgres, which passed on
its own three times; ruff check clean, ruff format clean apart from
`docs/bridges/telegram.md` and `docs/plans/m2-1-port.md`.

## Repair round (a bug fix; after patch round 2's checks: review pass, test gaps)

The test check's gap: after `git update-index --skip-worktree` or
`--assume-unchanged` on the plan file, `git status` reports nothing, so
`session._plan` accepted an edited plan as committed; `session._candidate`
had the same blind spot. A turn runs git in its own worktree, so it can set
these bits.

1. `git.dirty` no longer asks the turn's index. It reads HEAD into a fresh
   index in a temporary directory of the kernel's own (`GIT_INDEX_FILE`;
   `read-tree --empty` when there is no commit) and runs the same
   `git status --porcelain` against it. The kernel reads no byte of the
   turn's files; git compares contents. That index carries no
   assume-unchanged or skip-worktree bit, no cached stat data, and no
   untracked cache, so none of them, all of which the turn can write, can
   hide a change. Repo config the turn can write: `core.fsmonitor` was
   already pinned off for every call; `core.checkStat=minimal` with
   `core.trustctime=false` hid a same-size edit with its mtime restored
   against the turn's index (shown in a scratch repository) and does not
   against the fresh one; `core.fileMode=false` hid a mode change, so the
   status call pins `core.fileMode=true`. `core.ignoreStat` and a sparse
   checkout act only through index bits, which the fresh index lacks. No
   new cap, no new refusal: the existing "has changes not committed" and
   "uncommitted changes" errors now see what was hidden.
2. Tests (`tests/test_session.py`), one per route for `_plan` and for
   `_candidate`: assume-unchanged, skip-worktree, the stat cache
   (`checkStat=minimal`, `trustctime=false`, same size, mtime restored),
   and file mode (`core.fileMode=false`, chmod). Each asserts first that a
   plain `git status` reports nothing; all eight failed on 636f6177a's
   `core/git.py` and pass now.
3. Rebasing onto `valor-cori-rebuild` brought in 3c's `read_screens`, and
   this plan's "With 3c" makes the second to land fold it into the shared
   walk. `collect` keeps `screens`; `_screens` opens `.valor/screens` with
   `open_turn_dir`, files each entry away with `_file_away(...,
   sub=("screens",))`, then sizes it from `fstat` through a new
   `workspace.open_plain_file` (the regular-file and one-link checks of
   `open_turn_file`, without the sparse refusal, which `_open_checked` now
   adds on top). Recorded as `{name, bytes}` with no digest, as 3c's patch
   settled (the kernel never reads a screen), so a sparse screen is still
   sized, not refused. `_screens_dest` and the second walk are gone. A
   screen whose move is refused is removed unread with `_file_away`'s
   reason, so two `tests/test_look.py` assertions now check the reason
   ends in "removed unread" instead of the old "could not be moved aside"
   wording. Docs: `docs/browser.md` and `docs/harnesses.md`.

Not closed, as not hiding a change: line-ending normalization
(`core.autocrlf`, `.git/info/attributes` eol) makes a CRLF-only edit
compare equal by declaration, and `.git/info/exclude` or
`core.excludesFile` makes a file ignored, which is not a change to commit.
Neither alters what the kernel records: the plan's digest and the
candidate both come from the commit.

The full suite after the repair round: 741 passed, 11 skipped; ruff check
clean, ruff format clean apart from `docs/bridges/telegram.md` and
`docs/plans/m2-1-port.md`.

## Patch round 4 (review: changes, finding 1; test: pass)

The review's finding 1: the repair round's text claimed more than the code
does. The fresh index closes index bits, the stat cache, and
`core.fileMode`. It does not close git's content filters, which the turn can
set from config, `.git/info/attributes`, or an untracked `.gitattributes`:
`core.autocrlf` and the `text`/`eol` attributes hide a CRLF-only edit, the
`ident` attribute hides any text written inside `$Id$`, and
`working-tree-encoding` makes other bytes compare equal. The repair round's
"Not closed" paragraph named only line endings and is superseded by this.
None of these changes what the kernel records: the plan's digest is the
committed blob's and the candidate is the commit, so what the filters hide
is a refusal that does not happen, never a false record.

1. Wording only, no code: the `git.dirty` docstring and
   `docs/harnesses.md` (the `plan.json` and `done.md` rows) now say what
   the fresh index closes and that the filters can still make differing
   bytes compare equal, harmlessly. No filtering code is added.
2. Finding 2 (a huge or sparse tracked file runs to `git_timeout_s`) is
   left as is, recorded under "Decided by default".

## Merged

Merged 2026-10-04 by the lead's decision, at `a77688557` on
`valor-cori-rebuild`.

- **Checks that passed** at `3fbc29123`: review pass (review-1-4s-p4), test
  pass (test-1-4s-p4), docs `no_change` (docs-1-4s-p4).
- **Rebase.** One squash of `3fbc29123` onto `07e689fec` (1.4b merged and its
  rollout), since each commit met the same conflicts again. 1.4b's and this
  task's helpers are kept as one set, as "Shared with 1.4b" says: this
  task's walk (`_open_dir`, `open_turn_file`, `read_turn_file`) and
  `_file_away`, and 1.4b's check services, `rmtree`, and `spec_of`. The docs
  runner reads its verdict through `read_verdict`; `read_junit` returns its
  reason when the file is missing.
- **Folds.** The removal of `idle_turns` and the idle stop is kept
  (`core/session.py`, `core/settings.py`). The `git.dirty` docstring says
  that `core.symlinks` set false hides a type change, harmless like the
  filters.
- **Suite** on the rebased head: 877 passed, 13 skipped. `ruff check` clean;
  `ruff format --check` flags only `docs/bridges/telegram.md` and
  `docs/plans/m2-1-port.md`.
- **Backup** before the merge: `valor_rebuild-20261003T195102Z.dump`.
- **Follow-ups.** None.
