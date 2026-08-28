---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-08-27
tracking: https://github.com/tomcounsell/ai/issues/3008
last_comment_id: none
---

# Add the retired sustainability shim's row to the legacy-artifact guard

## Problem

`tests/unit/test_no_legacy_artifacts.py` is a ratchet: once a compatibility
shim is deleted, a row in its table makes bringing the shim back a test failure
instead of a silent regression. The guard shipped with rows for two of the
three shims that the cleanup batch removed.

The third — the sustainability namespace shim under `agent/` — was deliberately
left out. Its deletion had not landed on `main` when the guard shipped, so a row
naming it would have failed the file-absence check on the default branch for
reasons the guard's own lane did not own. Both the guard's module docstring and
`docs/features/legacy-artifact-guard.md` carry a paragraph recording that
deferral and pointing at #3008.

**Current behavior:** the deleted shim is unratcheted. Recreating its file, or
adding a fresh import of its dotted path from production code, is caught by
nothing at the source-text or git-index level. `tests/unit/test_sustainability_namespace.py`
asserts the module does not *resolve at runtime*, which is a real check but a
different one — it cannot see a tracked file that nothing imports yet, and it
runs against an interpreter's view rather than the index.

**Desired outcome:** the row exists, the three per-row checks are each
demonstrated to fire independently, the genuine retainers are exempted by
repo-relative path, and both prose surfaces stop describing a deferral that has
been discharged.

## Freshness Check

**Baseline commit:** `37e60af6f` — "Retire the agent/sustainability.py shim (PR 2 of 2) (#3007)"
**Issue filed at:** 2026-08-27 (same day as planning)
**Disposition:** Minor drift — the issue's own allow-list is incomplete; the
rest of its claims hold.

**Claims re-verified against `origin/main`:**

- The shim file is absent from the default branch: `git cat-file -e origin/main:agent/sustainability.py`
  exits non-zero. The stated blocker is discharged.
- `tests/unit/test_no_legacy_artifacts.py` is present on `main` with both
  tables, the three module checks, and the one symbol check the issue describes.
- The deferral paragraph naming #3008 is present in two places: the guard's
  module docstring and `docs/features/legacy-artifact-guard.md`.

**Drift found:** the issue names two files as the legitimate retainers. The
actual set on `origin/main`, enumerated with `git grep -l -F <dotted-path> -- '*.py'`,
is nine. Adopting the issue's list verbatim would have failed the new row's
import-absence check on seven files on the first run. The full set and the
justification for each is recorded in the issue's Recon Summary and reproduced
under Technical Approach below.

**Cited sibling issues/PRs re-checked:**

- #2875 / PR #3007 — merged as `37e60af6f`. This is the event the deferral waited on.
- #2880 — the guard's originating issue; the guard is on `main`.
- #3015 — hardening pass that added the path/import-agreement check. On `main`.
- #2805 — the positional-exemption incident. Still the binding constraint: exemptions are paths.
- #2807 — the stale-bytecode incident. Still the binding constraint: git-tracked content only.

**Commits on main since the issue was filed:** `37e60af6f` itself, which is the
enabling commit rather than a competing change.

**Active plans overlapping this area:** `docs/plans/retire-agent-sustainability-shim.md`
is the plan behind PR #3007 and is complete. No open plan touches the guard.

## Prior Art

- **#2880 / `tests/unit/test_no_legacy_artifacts.py`** — created the guard and its
  two tables. Succeeded. This plan adds one row to a table it established; the
  shape is fixed and this plan does not renegotiate it.
- **#3015** — added the path/import-agreement check after review argued that a
  mistyped `file_path` would make the file-absence check permanently green while
  querying a path that never existed. Succeeded. Directly relevant: the new row
  must satisfy that agreement mechanically, and it does.
- **#2805** — a guard in this repo carried a line-number-keyed exemption list;
  unrelated merges shifted the file and the exemptions silently stopped applying.
  Failed in exactly the way this plan must not repeat. The remedy adopted then
  and mandatory now: exemptions are repo-relative paths only.
- **#2807** — a meta-test walked the filesystem and matched string literals baked
  into `__pycache__` bytecode, producing failures unreproducible in a fresh
  checkout. Failed. The guard's git-only design is the fix; nothing here relaxes it.
- **#3007 / #2875** — deleted the shim and left the migration machinery that must
  keep naming it. That machinery is precisely the retainer set this plan exempts.

## Research

No relevant external findings — this is entirely internal repo convention with
no external library, API, or ecosystem surface. Proceeding with codebase context.

## Data Flow

Not applicable in the runtime sense: nothing here executes in the bridge, worker,
or agent path. The relevant flow is the guard's own, which is unchanged and worth
stating because the new row rides it:

1. **Entry point**: pytest collects `tests/unit/test_no_legacy_artifacts.py` and
   parametrizes each check over the rows of its table.
2. **`_is_tracked`**: shells out to `git ls-files -- <file_path>`; non-empty
   stdout means the deleted file is back in the index.
3. **`_tracked_python_matches`**: shells out to `git grep -l -F <dotted_path> -- '*.py'`;
   returns the matching repo-relative paths, branching on exit status rather
   than on a printed count.
4. **Per-row set difference**: matches minus the row's `allowed` frozenset. A
   non-empty remainder is the offender list.
5. **Output**: an `AssertionError` naming the offending paths and the two
   legitimate remedies.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. `BannedModule` gains an instance, not a field.
- **Coupling**: unchanged. The row is data in an existing table.
- **Data ownership**: unchanged.
- **Reversibility**: trivially reversible — delete the row.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The one gating
condition (the shim's deletion landing on `main`) is already satisfied, as
recorded in the Freshness Check.

## Solution

### Key Elements

- **One `BANNED_MODULES` row.** Carries the retired shim's dotted import path,
  the dots-to-slashes translation of that path with a `.py` suffix, and an
  exemption set of repo-relative paths.
- **A ten-path exemption set.** The guard file's own unavoidable self-reference
  plus the nine tracked Python files that legitimately name the retired path.
- **Two prose corrections.** The guard's module docstring and
  `docs/features/legacy-artifact-guard.md` each carry a paragraph describing the
  deferral to #3008. That deferral is discharged; both paragraphs go, replaced
  by a description of what the new row covers and why its exemption set is the
  largest in the table.

### Flow

Someone recreates the deleted shim, or writes a fresh import of its dotted path
→ `/do-test` runs `tests/unit/` → the specific parametrized check for that row
fails, naming the offending path → the failure message states the two legitimate
remedies (remove the reference, or exempt the file by path in the same pull
request) → the regression is resolved deliberately rather than landing silently.

### Technical Approach

**The row's two path fields are determined, not chosen.** The dotted path is the
retired shim's import path. The file path is that path with dots replaced by
slashes and `.py` appended. The path/import-agreement check asserts exactly this
relationship, so any other value fails immediately. There is no judgement call.

**The exemption set is the substantive decision.** Enumerated on `origin/main`
with `git grep -l -F <dotted-path> -- '*.py'`, restricted to the guard's own
scope of tracked Python:

| File | Why it legitimately names the retired path |
|---|---|
| `scripts/migrate_reflections_callables.py` | Holds the callable rename mapping table. The old paths are the *source* side of that table; deleting them disarms the self-heal. |
| `scripts/update/reflections_callables.py` | The `/update` probe that re-runs the migration and reports registry copies still carrying an old path. |
| `scripts/update/run.py` | Explanatory comments on why the migration gate exists and what it matches. |
| `scripts/verify_registry_without_shim.py` | Its `BANNED_MODULE` constant *is* the old path; the script proves every registry callable resolves with that import blocked. |
| `tests/unit/test_migrate_reflections_callables.py` | Exercises the mapping table and asserts rewritten registries no longer carry an old path. |
| `tests/unit/test_reflection_scheduler.py` | One docstring sentence explaining the post-migration callable namespace. |
| `tests/unit/test_sustainability_namespace.py` | The runtime twin of this guard: asserts the module does not resolve and importing it raises. |
| `tests/unit/test_update_reflections_callables.py` | Asserts the `/update` probe detects and rewrites old-path entries. |
| `tests/unit/test_verify_registry_without_shim.py` | Exercises the banned-import verifier, including registry fixtures that carry an old path. |

Plus `GUARD_FILE`, which the table's own text forces onto every row.

**Two near-misses that must not be mishandled.** `reflections/redis_access.py`
names the shim's *file* path in a docstring but never its dotted path. The
import-absence check matches the dotted form only, so that file is not an
offender and must **not** be exempted — an unnecessary exemption is a
permanently granted exception nobody can later distinguish from a real one.
`scripts/update/run.py` names both forms; it earns its exemption on the dotted
occurrences alone.

**Honest accounting of what this row buys.** Its exemption set is the largest in
the table, which weakens the import-absence check specifically: nine of the
files that could name the path already may. That check still catches a fresh
reference from anywhere else — `agent/`, `worker/`, `bridge/`, `reflections/`,
`models/`, `tools/` — which is the reintroduction shape that matters. The other
two checks consult no exemption set at all: file absence queries the git index
directly, and path/import agreement compares two fields. The row is worth its
weight on those two even before counting the third. The feature doc should say
this plainly rather than let a reader assume uniform strength across rows.

**Paraphrase discipline.** The guard's design forbids quoting a banned string
anywhere except the table row itself, because a repo-wide "this string must not
appear" check matches prose that quotes what it bans. Every comment, docstring,
and failure message this plan touches refers to the artifact descriptively. The
feature doc follows the same discipline by convention even though the guard does
not scan `docs/`.

**No derived values in prose.** Neither the feature doc, the `docs/features/README.md`
index row, nor the pull-request body may state a row count, an exemption-set
size, a line total, or a commit SHA. Where a derived value helps, state the
expression that yields it. This is an existing rule in the feature doc; this
plan honors it rather than restating it as new.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No exception handlers in scope. The guard's helpers deliberately raise on
  any git exit status other than "matched" or "clean", and let a subprocess
  timeout propagate. This plan adds no handler and removes none.

### Empty/Invalid Input Handling
- [x] The empty case is the passing case and is already exercised: a clean
  `git grep` exits 1 and `_tracked_python_matches` returns an empty set. The new
  row's import-absence check reaches that branch on every green run, because the
  match set minus the exemption set is empty. No new input surface is added —
  the row is table data consumed by existing parametrized checks.

### Error State Rendering
- [x] The user-visible output is the assertion message. The demonstrated-red
  procedure below inspects it for each of the three checks: the message must name
  the offending path and state the remedies for that check. This is the failure
  path, tested directly rather than by proxy.

## Test Impact

- [ ] `tests/unit/test_no_legacy_artifacts.py` — UPDATE: add one `BANNED_MODULES`
      row and rewrite the module docstring's deferral paragraph. The three module
      checks are parametrized over the table, so they gain a case each with no
      change to their bodies.

No other existing test is affected. The nine retainer files keep their content
verbatim; they are named in the new row's exemption set, which changes nothing
about how they run. `tests/unit/test_sustainability_namespace.py` continues to
assert the runtime proposition and is untouched.

## Rabbit Holes

- **Adding a `BANNED_SYMBOLS` row for the names the shim re-exported.** Those
  names live on under the new namespace, so a fixed-string search for them fires
  on every legitimate live call site. The runtime assertions already cover the
  proposition, and the guard's docstring makes exactly this argument for three
  other names. Do not relitigate it.
- **Widening the guard beyond tracked Python to catch the prose retainers.** The
  scope decision is documented and load-bearing: a guard whose exemption list
  churns on every documentation edit is one people learn to ignore, and none of
  those documents can reintroduce a runtime dependency.
- **Refactoring the exemption sets into a shared constant because this row's set
  is long.** Per-row sets exist so an exception granted for one artifact cannot
  accidentally exempt another. Length is not a reason to collapse them.
- **Trimming the retainer set to make the row look stronger.** Each of the nine
  is load-bearing migration machinery. Removing a string from the mapping table
  in `scripts/migrate_reflections_callables.py` disarms a self-heal that exists
  to repair registries in the field.
- **Extending the row to also ban the file-path (slash) spelling.** That changes
  the check's contract for every row, not just this one, and would drag
  `reflections/redis_access.py` into the exemption set for a docstring. Out of scope.

## Risks

### Risk 1: The exemption set is enumerated against a moving tree
**Impact:** A file that acquires a reference to the retired path between plan
time and merge turns the branch red at an unexpected moment, or worse, a
reviewer reads a stale set as authoritative.
**Mitigation:** Re-run the enumeration against the merge base immediately before
the final push, and treat any delta as a review finding rather than a silent
edit. The check itself is the backstop — a missing exemption is a loud failure,
never a silent pass.

### Risk 2: An over-broad exemption silences a real future regression
**Impact:** If a file is exempted that does not actually need to be, a genuine
reintroduction inside that file would go unnoticed forever.
**Mitigation:** Every entry is justified individually in the table above.
`reflections/redis_access.py` is the worked example of a file that looks like a
retainer and is deliberately excluded.

### Risk 3: The demonstrated-red evidence proves less than it appears to
**Impact:** Reintroducing the artifact in a way that trips all three checks at
once produces `3 failed` and proves only that *something* fires — not that each
check is independently live. A row whose file-absence check was querying a
mistyped path could hide inside that aggregate.
**Mitigation:** Three separate red demonstrations, each with a distinct
perturbation chosen to trip exactly one check, each expected to report one
failure against a green remainder. Spelled out under Verification.

## Race Conditions

No race conditions identified. Every operation is a synchronous subprocess call
to git within a single pytest process; there is no shared mutable state, no
async, and no cross-process handoff.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The rabbit
holes above are rejected on merit rather than postponed, and the one condition
this work ever waited on has been discharged.

## Update System

No update system changes required. This adds a row to a unit test; nothing is
propagated to other machines beyond the ordinary `git pull` that `/update`
already performs, and no new dependency, config file, or migration is involved.

## Agent Integration

No agent integration required. This is test-suite-internal: no CLI entry point,
no MCP surface, and no bridge import. The guard is reached only by pytest at the
`/do-test` stage.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/legacy-artifact-guard.md`: replace the deferral
      paragraph with a description of the new row, name the deleting issue,
      and state plainly that this row's exemption set is the table's largest and
      what that does and does not weaken.
- [ ] Update the `docs/features/README.md` index row so it no longer implies the
      guard covers only the first cleanup batch's issues.

### External Documentation Site
- [x] Not applicable — this repo has no external documentation site build.

### Inline Documentation
- [ ] Rewrite the deferral paragraph in the guard's module docstring. It names
      #3008 as pending; that is now false and a false comment is worse than none.
- [ ] Add a short comment on the new row explaining why its exemption set is long
      — a migration whose machinery must keep recognizing the old path — so the
      next reader does not mistake it for accumulated laxity. Paraphrase only.

## Success Criteria

- [ ] `BANNED_MODULES` carries a row for the retired sustainability shim.
- [ ] The row's `file_path` is the mechanical translation of its `dotted_path`,
      as asserted by the existing path/import-agreement check.
- [ ] Every legitimate retainer is exempted by repo-relative file path. No entry
      is a line number or a range (#2805).
- [ ] `reflections/redis_access.py` is **not** in the exemption set.
- [ ] Each of the row's three checks is independently demonstrated red, each
      reporting a single failure against a green remainder.
- [ ] Neither prose surface nor the guard's docstring still describes the
      deferral as pending.
- [ ] No comment, docstring, or documentation prose added by this change quotes
      a banned string; the table row is the only place any of them appears.
- [ ] No count, size, line total, or SHA appears in the feature doc, the index
      row, or the pull-request body.
- [ ] `tests/unit/test_no_legacy_artifacts.py` passes.
- [ ] Every test file that references a symbol this diff touches passes.
- [ ] Lint and format clean.

## Team Orchestration

Small appetite, single file of production change plus two documentation
surfaces. The work is executed directly rather than fanned out — splitting one
table row across builders would cost more coordination than it saves. A
`code-reviewer` runs at the review gate.

### Team Members

- **Reviewer (guard row)**
  - Name: `guard-row-reviewer`
  - Role: Verify the row, the exemption set's justification, the demonstrated-red
    evidence, and paraphrase/derived-value discipline across all prose surfaces.
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### 1. Add the row and correct the docstring
- **Task ID**: build-guard-row
- **Depends On**: none
- **Validates**: `tests/unit/test_no_legacy_artifacts.py`
- **Assigned To**: lead
- **Agent Type**: builder
- **Parallel**: false
- Re-enumerate the retainer set against the merge base; reconcile against the
  table under Technical Approach and treat any delta as a finding.
- Add the `BANNED_MODULES` row with its exemption set, sorted for readability.
- Replace the module docstring's deferral paragraph.
- Add the row comment explaining the long exemption set, paraphrasing throughout.

### 2. Demonstrate red, three times
- **Task ID**: demo-red
- **Depends On**: build-guard-row
- **Assigned To**: lead
- **Agent Type**: builder
- **Parallel**: false
- Run each perturbation from the Verification section's red-state table in turn.
- Capture the exact pass/fail tally and the assertion message for each.
- Restore after each and confirm green before moving to the next.

### 3. Documentation
- **Task ID**: document-row
- **Depends On**: build-guard-row
- **Assigned To**: lead
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/legacy-artifact-guard.md` and the index row.
- Verify no derived value and no quoted banned string was introduced.

### 4. Review
- **Task ID**: review-all
- **Depends On**: demo-red, document-row
- **Assigned To**: `guard-row-reviewer`
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify every Success Criterion, with particular attention to the exemption
  set's completeness and to the independence of the three red demonstrations.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard passes | `scripts/pytest-clean.sh tests/unit/test_no_legacy_artifacts.py -q -p no:xdist` | exit code 0 |
| Symbol-referencing tests pass | `scripts/pytest-clean.sh tests/unit/test_no_legacy_artifacts.py tests/unit/test_sustainability_namespace.py tests/unit/test_migrate_reflections_callables.py tests/unit/test_update_reflections_callables.py tests/unit/test_verify_registry_without_shim.py tests/unit/test_reflection_scheduler.py -q -p no:xdist` | exit code 0 |
| No exemption is positional | `grep -nE '^\s*"[^"]+:[0-9]+"' tests/unit/test_no_legacy_artifacts.py` | exit code 1 |
| Lint clean | `python -m ruff check tests/unit/test_no_legacy_artifacts.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_no_legacy_artifacts.py` | exit code 0 |

Two rows a first draft of this plan carried are deliberately absent. A
`git ls-files` invocation asserting the shim file is still absent would exit 0
whether or not it matched anything, so it can only ever pass — the file-absence
check inside the guard is the real assertion and it is already covered by the
first row. A standalone exemption-completeness script would restate what the
import-absence check asserts, in a second place that can drift from the first.

The positional-exemption row is an anti-criterion for the #2805 failure mode: it
matches an exemption-set entry that carries a `:` followed by digits, which is
what a line-keyed exemption would look like. It is demonstrated red by
temporarily appending a line suffix to one exemption entry.

**Red-state demonstrations** (this is a non-check table — a record of evidence,
not commands `/do-build` executes):

| Check under test | Perturbation | Expected |
|---|---|---|
| File absence | Create and `git add` an empty file at the deleted shim's path | that check fails, its two siblings for the row stay green |
| Import absence | Add a fresh reference to the dotted path in a tracked Python file that is **not** in the exemption set | that check fails naming that file, siblings green |
| Path/import agreement | Perturb the row's `file_path` field so it is no longer the translation of its `dotted_path` | that check fails, and the file-absence check goes green while checking a path that never existed — the exact silent-pass this check was added to prevent |

Each perturbation is reverted and the suite re-confirmed green before the next.

## Critique Results

War room 2026-08-28, LITE depth (triage: single test-file table row plus prose
sync; no doctrine path, `appetite: Small`). Roster 1/1 — Consolidated Critic,
grounded. Verdict: **READY TO BUILD (no concerns)** — 0 blockers, 0 concerns,
2 nits.

Every factual claim the plan makes was re-verified against `origin/main` before
the critic was dispatched, and all of them hold:

- The shim file is absent from the default branch (`git cat-file -e` on that
  path against `origin/main` exits non-zero). The deferral's gating condition is
  discharged.
- `git grep -l -F <dotted-path> origin/main -- '*.py'` returns exactly the set
  the Technical Approach table enumerates — no more, no fewer, same paths.
- `reflections/redis_access.py` carries the slash spelling in a docstring and
  never the dotted spelling. Its deliberate exclusion from the exemption set is
  correct, and adding it would grant an exception nothing needs.
- `GUARD_FILE` is defined in the guard and is already forced onto every existing
  row's exemption set, so the row's self-reference entry matches the table's
  established shape.
- The deferral paragraph naming this issue exists in both prose surfaces the
  plan commits to correcting, and the feature index row still frames the guard
  as covering only the first cleanup batch.
- Every file path the plan names exists; every prior-art reference resolves.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Consolidated Critic | The Documentation section commits to correcting the feature index row so it no longer implies the guard covers only the first cleanup batch, but no Success Criterion asserts that correction landed. The one Success Criterion touching the index row bans derived values there, which is a different property. Task 3's stated goal is therefore outside the reviewer's checklist. | pending | Cosmetic only — the guard's behavior and tests are unaffected either way. If addressed, add a Success Criterion of the form "the feature index row no longer frames the guard as single-batch coverage", distinct from the existing derived-value ban. |
| NIT | Structural check | Tasks 2, 3, and 4 carry no `Validates:` field; only task 1 does. Task 2 *is* the red-demonstration evidence task and task 4 is the review gate, so the omission is defensible, but a reader scanning for per-task validation finds three blanks. | pending | The Verification table already covers every command that would appear in those fields, and the red-state table is explicitly marked a non-check table `/do-build` does not execute. If addressed, point task 2 at the red-state table and task 3 at the paraphrase/derived-value greps rather than inventing new commands. |

Both nits are documentation-level and neither blocks the build. No revision
pass is required.

---

## Open Questions

None. The one decision that could have gone either way — whether to exempt
`reflections/redis_access.py` — is settled by the check's own contract: it
matches the dotted spelling, that file carries only the slash spelling, so an
exemption there would grant an exception that nothing needs.
