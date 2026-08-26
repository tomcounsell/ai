# Legacy-Artifact Guard

`tests/unit/test_no_legacy_artifacts.py` pins deleted modules and removed
symbols as *absent*. It is a ratchet: once the cleanup batch behind
#2872 / #2873 / #2874 removed a shim module or a deprecated attribute, this
guard makes bringing it back a test failure rather than a silent regression.

## Why it exists alongside the runtime guards

Two sibling tests already cover part of this ground, and neither subsumes this
one:

| Test | What it checks | Blind spot |
|------|----------------|------------|
| `tests/unit/test_agent_session_legacy_surface.py` | The removed `AgentSession` aliases are absent from the live class | A brand-new module that defines the same name itself |
| `tests/unit/test_enums.py` | The retired session-type member and the retired settings helper are absent from their live objects | A deleted module *file* reappearing |

Both inspect the live object graph. If someone recreates one of the deleted
shim module files and imports it, every existing assertion still passes: the
import resolves, and the attribute checks are about a different object
entirely. The legacy-artifact guard closes that gap by checking two things the
object graph cannot show — the **git index** and the **tracked source text**.

## What it pins

Seven artifacts, in two tables.

**`BANNED_MODULES` — two rows.** The bridge-side session-log shim and the
reflections model shim, both deleted by #2872. Each row carries the module's
import path, its file path, and its own exemption set. Each row drives two
independent checks:

1. **File absence.** The path is not present in `git ls-files`. This catches a
   reintroduced file that nothing imports yet.
2. **Import absence.** No tracked `.py` outside the row's exemption set
   contains the module's fully-qualified import path as a literal. This catches
   a reintroduced caller.

Both checks are needed. Either one alone would pass on half the reintroduction
shapes.

**`BANNED_SYMBOLS` — five rows.** Three `AgentSession` attributes removed by
#2873, plus the retired session-type member and the retired settings helper
from #2874. Each row carries the symbol name and its exemption set, and drives
one check: no tracked `.py` outside that set names it.

Three further `AgentSession` attributes removed by the same batch are
deliberately **not** in the symbol table. Two are ordinary English words that
also occur as live identifiers and keyword arguments throughout the repo, and
the third is a strict substring of a name already in the table, so a row for it
would fire on every legitimate mention of its sibling and add no coverage. All
three stay covered by the runtime attribute assertions, which have no
false-positive surface at all. Grep is the right tool only for names
distinctive enough to survive it.

One artifact from the same batch is deferred to **#3008**: its removal had not
landed on `main` when this guard shipped, so its row would have turned the
default branch red for reasons this lane did not own. #3008 names the exact row
and its legitimate retainers.

## Two hard constraints

Both are load-bearing and both were learned expensively. Do not relax either
without reading the issue behind it.

**1. Tracked content only.** Every check shells out to git, so it sees the
index and tracked worktree files and nothing else. It must never walk the
filesystem recursively. Compiled bytecode caches embed their module's string
literals verbatim, so a stale cache next to a source file produces a phantom
match — a failure that is unreproducible in a fresh checkout and near-impossible
to debug. That is the documented root cause of #2807, and the reason a whole
plan exists to sweep the repo's other meta-tests for the same defect.

**2. Exemptions are keyed by repo-relative file path, never by a position
within a file.** A guard in this repo once carried a positional exemption list.
Unrelated merges shifted the file around and the exemptions silently stopped
applying to the call sites they were written for — surviving in form while
doing nothing. See #2805.

A third rule falls out of how git reports a clean result: an empty match set is
signalled by an **exit status**, not by a printed count. There is no zero on
stdout to compare against, so no check here compares a printed tally against a
literal zero. Every helper branches on the exit status and treats anything
other than "matched" or "clean" as a hard failure — a broken invocation (a bad
flag, or a call made outside a repository) must never read as "nothing found".
That third branch catches a broken invocation specifically; it does not, and
cannot, catch a search that runs cleanly but was typed wrong. A mistyped
pathspec or a mistyped pattern still exits "clean" — git has no way to
distinguish "found nothing because the artifact is gone" from "found nothing
because the argument doesn't match anything real" — so that residual gap is
closed only by keeping the tables and paths correct, not by the exit-status
check.

## Scope: tracked Python only

Every search is restricted to tracked `*.py`. This is the choice that makes the
guard tractable rather than a churn machine.

A whole-tree search for these strings also hits roughly fifteen documents
under `docs/` that legitimately discuss the migration in prose — including a
completed plan that quotes a literal import line inside a verification row.
None of those can reintroduce a runtime dependency. Restricting to tracked
Python drops the exemption list from unbounded and growing to four distinct
files plus the guard's own unavoidable self-reference.

A guard whose exemption list churns on every documentation edit is a guard
people learn to ignore, and policing prose blocks writing honestly about the
migration.

## Self-reference and paraphrase discipline

The guard file necessarily spells every banned string in its own tables, so it
appears on every row's exemption set by explicit path. That is the only
self-reference. Everywhere else in the file — docstrings, comments, failure
messages — the banned strings are **paraphrased** rather than quoted, so no
comment edit can widen what the guard tolerates. This document follows the same
discipline: the guard does not scan `docs/`, so a quotation here would not fail
anything, but a doc that models quoting invites the next author to quote inside
a `.py` file.

## Adding an exemption

The exemption list is a record of deliberate exceptions, not a wall. It is
meant to grow when growth is justified. When the guard fails, exactly two
resolutions are legitimate, and the failure message states both:

1. **Remove the reference.** This is the default and is correct almost every
   time. The artifact was deleted on purpose.
2. **Exempt the file**, if the reference is genuinely warranted — a new runtime
   guard that must name the symbol, or a migration helper that must recognize
   the old import path. Add the offending **repo-relative file path** to that
   row's exemption set, **in the same pull request** as the reference itself.

Rules for a new exemption:

- A path and nothing else. Never a position within a file.
- Add it to the specific row it belongs to. Exemption sets are per-row precisely
  so an exception granted for one artifact cannot accidentally exempt another.
- Say why in the pull request. An unexplained exemption is indistinguishable
  from a regression that someone silenced.

Never resolve a failure by widening a pattern, deleting a row, or exempting the
guard file's siblings wholesale. Each of those converts a real signal into a
permanently green test that checks nothing.

## Where it runs

`tests/unit/` runs at the `/do-test` stage of the SDLC pipeline, which is the
gate every pull request passes through in this repo. Stated plainly, because
the qualifier matters: this repo has **no pytest CI workflow**
(`.github/workflows/` holds only an `@claude` mention responder), and
`/do-test` is a pipeline convention rather than a mechanical gate. A commit
landed through the sanctioned hotfix path — direct to `main`, no pull request —
runs `.githooks/commit-msg` and `.githooks/pre-push`, neither of which invokes
pytest, so it never runs this guard. Closing that residual gap requires a
GitHub Actions workflow with a Redis service container, which is out of scope
for the guard itself.

## Related

- [`tests/unit/test_no_legacy_paths.py`](../../tests/unit/test_no_legacy_paths.py) —
  the direct precedent. Same shape, different subject: it pins a filesystem
  *path* migration across all tracked files, where this guard pins deleted code
  artifacts across tracked Python. They stay separate files rather than one
  merged table, because conflating two propositions under one table is a
  recurring source of review churn here.
- `tests/unit/test_agent_session_legacy_surface.py`, `tests/unit/test_enums.py` —
  the runtime layer this guard sits above.
