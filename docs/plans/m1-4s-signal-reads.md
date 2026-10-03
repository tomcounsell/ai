---
tracking: none
slug: m1-4s-signal-reads
type: bug
status: critique round 2 of 2 said revise; its findings built in; in build
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
volume, a FIFO, a socket, a directory where a file is expected, and it can
name paths in `plan.json`. The kernel must never read, stat, list, or
create anything outside those directories because of an entry the turn
left, never block on one, and never put the contents of anything but a
regular file with one link into the ledger. An entry it will not read is
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
  O_CLOEXEC`; `fstat` must say `S_ISREG` and `st_nlink == 1`. Anything
  else closes the descriptor and returns `None` and a reason ("is a link,
  not a plain file", "is not a regular file", "has 2 links"); nothing is
  read. The caller owns the descriptor it gets, so a caller can stream,
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
2. `session._plan` opens the workspace and reads `path` with
   `read_turn_file`, then compares. A missing file is "`path` has changes
   not committed", as for a differing one; a refusal is "`path` cannot be
   read: why".
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
    `git.is_repo` is false on a linked workspace.
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
it cannot read, `docs/plans/m1-4-checks.md` lines 331, 624, and 1089,
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
  nothing else (Tom's standing rule on invented caps).

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
6. `m1-4-checks.md:1089` named; `m1-4c-verifier.md:215` edited; the 1.4b
   branch says what this task deletes if 1.4b lands first; `read_verdict`
   holds the `.valor` descriptor itself.
