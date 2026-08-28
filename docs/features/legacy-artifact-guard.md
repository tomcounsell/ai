# Legacy-Artifact Guard

`tests/unit/test_no_legacy_artifacts.py` pins deleted modules and removed
symbols as *absent*. It is a ratchet: once the cleanup batch behind
#2872 / #2873 / #2874 removed a shim module or a deprecated attribute — or the
sibling #2875 rename retired one more shim — this guard makes bringing it back a
test failure rather than a silent regression.

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

Two tables. Values a reader could instead derive from the tree are deliberately
absent from every prose surface that describes this guard — this document, the
`docs/features/README.md` index row, and the pull-request body that ships a
change to it. That covers row counts and exemption-set sizes, and equally file
line counts and commit SHAs: the axis is not "is it a count" but "does anything
update this when the tree moves". Nothing does, because prose has no build step.
Do not reintroduce such a value anywhere. A number or SHA a reader can get from
the code is one that will eventually be wrong, and the stale copy is the one
people trust, because it reads as though someone checked.

Where a derived value genuinely helps, state the expression rather than its
result — `len(BANNED_MODULES)` explains itself and cannot drift, while `2`
silently stops being true.

**`BANNED_MODULES`.** The bridge-side session-log shim and the reflections model
shim, both deleted by #2872, and the sustainability-namespace shim under
`agent/`, deleted by #2875. Each row carries the module's import path, its file
path, and its own exemption set. Each row drives three independent checks:

1. **File absence.** The path is not present in `git ls-files`. This catches a
   reintroduced file that nothing imports yet.
2. **Import absence.** No tracked `.py` outside the row's exemption set
   contains the module's fully-qualified import path as a literal. This catches
   a reintroduced caller.
3. **Path/import agreement.** A row's file path must be the mechanical
   translation of its import path (dots to slashes, plus the `.py` suffix).
   The file-absence check only ever queries the path field, and a path that
   was mistyped in a row reads as "absent" exactly like a path that is
   genuinely gone — there is no way to tell those apart from the check's
   result alone. This third check locks the two fields together so a typo in
   one can't make check 1 permanently green while checking nothing.

All three are needed. Dropping any one would pass on some reintroduction
shape, or on a row that was miscopied in the first place.

**`BANNED_SYMBOLS`.** A set of `AgentSession` attributes removed by #2873, plus
the retired session-type member and the retired settings helper from #2874. Each
row carries the symbol name and its exemption set, and drives one check: no
tracked `.py` outside that set names it.

Three further `AgentSession` attributes removed by the same batch are
deliberately **not** in the symbol table. Two are ordinary English words that
also occur as live identifiers and keyword arguments throughout the repo, and
the third is a strict substring of a name already in the table, so a row for it
would fire on every legitimate mention of its sibling and add no coverage. All
three stay covered by the runtime attribute assertions, which have no
false-positive surface at all. Grep is the right tool only for names
distinctive enough to survive it.

## The sustainability-shim row and what its exemption set costs

The row for the shim retired by #2875 arrived after the others, via #3008. That
deletion had not landed on `main` when the guard first shipped, so a row naming
it then would have failed the file-absence check on the default branch for
reasons the guard's own lane did not own. The deletion has since landed and the
row is in place; nothing about it is still pending.

Its exemption set is long, and honesty about what that means matters more than
the row looking strong. The rename left behind a migration script, an
update-time probe, a standalone verifier, and their tests. They are **not** all
exempt for the same reason, and the row groups them accordingly — one blanket
rationale would read well and be false for most of the set:

1. **The pre-rename path is data the code acts on.** The migration script's
   rename mapping is keyed by it — that path is the *source* side of the table —
   and the standalone verifier names it as the import it blocks. Rewrite either
   and the self-heal that repairs registry copies still in the field stops
   working. These cannot be paraphrased away at any price.
2. **The path is fixture input or an assertion.** Each of these passes the
   pre-rename path to code under test — registry fixtures that still carry it,
   and assertions that it was rewritten away — and one of them passes it to the
   import machinery to prove the module no longer resolves. Also unavoidable,
   but note that last one is an absence guard, not a self-heal; it is exempt
   because it must *name* the module to assert the module is gone.
3. **The path appears only in comment or docstring prose.** In the update-time
   probe, the update script, and one scheduler-test docstring, nothing executes
   it and stripping it would disarm nothing. These are exempt only so the guard
   does not force explanatory prose to be mangled.

That third group is worth flagging rather than burying: the update script is
permanent production code, unlike the transitional migration machinery around
it, so it will still be carrying a standing exemption long after groups 1 and 2
have been deleted. Those three entries are the ones that could later be retired
by paraphrasing the prose instead of exempting the file — the one direction in
which an exemption set here can honestly shrink.

What that costs is specific and confined to one of the three checks:

- **Import absence is weakened for this row.** The files most likely to name the
  old path are already permitted to. The check still catches a fresh reference
  from any other tracked Python file — exemptions are keyed per file, so no
  directory is exempt wholesale, and a *new* file under `scripts/` or `tests/`
  is caught exactly like one under `agent/` or `bridge/`. That is the
  reintroduction shape that actually matters.
- **The other two checks are untouched.** File absence consults no exemption set
  at all; it queries the git index directly. Path/import agreement compares two
  fields of the row against each other. Neither weakens by one byte as the
  exemption set grows, so the row earns its place on those two alone.

Do not read uniform strength across rows: a row's import-absence coverage is
exactly the complement of its exemption set.

One file is a deliberate near-miss. `reflections/redis_access.py` names the
retired shim's *file* path in a docstring but never its dotted import path.
The import-absence check matches the dotted spelling only, so that file is not
an offender and is **not** exempted. Adding it would grant a permanent exception
that nothing needs — and one nobody could later distinguish from a real one.

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
check. For the module table's file-path field specifically, the third check
described above (path/import agreement) closes half of that gap mechanically
instead of relying on care alone; nothing plays the same role for a mistyped
import path or a mistyped symbol name, so those still depend on getting the
table right.

## Scope: tracked Python only

Every search is restricted to tracked `*.py`. This is the choice that makes the
guard tractable rather than a churn machine.

A whole-tree search for these strings also hits a large and growing set of
documents under `docs/` that legitimately discuss the migration in prose —
including a completed plan that quotes a literal import line inside a
verification row. None of those can reintroduce a runtime dependency.
Restricting to tracked Python drops the exemption list from unbounded and
growing to a short list of source files plus the guard's own unavoidable
self-reference.

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
meant to grow when growth is justified. The two content-search-backed checks —
import absence and symbol absence — fail with a message that states exactly
two legitimate resolutions:

1. **Remove the reference.** This is the default and is correct almost every
   time. The artifact was deleted on purpose.
2. **Exempt the file**, if the reference is genuinely warranted — a new runtime
   guard that must name the symbol, or a migration helper that must recognize
   the old import path. Add the offending **repo-relative file path** to that
   row's exemption set, **in the same pull request** as the reference itself.

The file-absence check is different: it ignores the row's exemption set
entirely, so "add an exemption" is never one of its options. Its failure
message states its own pair instead — delete the reintroduced file, or, if it
is genuinely a new module that happens to share the path, revisit that row's
entry deliberately, in the same pull request.

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
runs `.githooks/pre-commit`, `.githooks/commit-msg` and `.githooks/pre-push`,
none of which invokes pytest, so it never runs this guard. Closing that
residual gap requires a GitHub Actions workflow with a Redis service
container, which is out of scope for the guard itself.

## Related

- [`tests/unit/test_no_legacy_paths.py`](../../tests/unit/test_no_legacy_paths.py) —
  the direct precedent. Same shape, different subject: it pins a filesystem
  *path* migration across all tracked files, where this guard pins deleted code
  artifacts across tracked Python. They stay separate files rather than one
  merged table, because conflating two propositions under one table is a
  recurring source of review churn here.
- `tests/unit/test_agent_session_legacy_surface.py`, `tests/unit/test_enums.py` —
  the runtime layer this guard sits above.
