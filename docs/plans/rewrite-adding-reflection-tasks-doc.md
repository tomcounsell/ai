---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/1031
last_comment_id: 4786392409
revision_applied: true
revision_applied_at: 2026-07-24T09:09:27Z
---

# Rewrite `adding-reflection-tasks.md` for the current callable + per-file architecture

## Problem

`docs/features/adding-reflection-tasks.md` is a developer guide for adding a new
reflection. Every code snippet, file reference, import, checklist item, and test
template in it points at an architecture that was deleted in the reflections
monolith migration (#748) and further restructured by #1028. A contributor who
follows the doc literally cannot land a working reflection — the classes,
scripts, and methods it names do not exist.

**Current behavior:**
The doc instructs the reader to:
- Edit `scripts/reflections.py` (absent from the repo).
- Add an `async def step_<key>(self) -> None` method to a `ReflectionRunner`
  class (zero matches repo-wide).
- Call `self.state.add_finding(...)` and set `self.state.step_progress[...]`
  (protocol that no longer exists).
- Register the step as a numbered tuple in `self.steps`.
- Add a test that imports `from scripts.reflections import ReflectionRunner`.

None of these map to how reflections actually work today.

**Desired outcome:**
The doc accurately describes the current architecture so a new contributor can
follow it end-to-end and land a working reflection on `main`:
1. The callable contract: no args, returns `{"status", "findings", "summary"}`,
   may be sync `def` or `async def`.
2. The one-file-per-reflection layout from #1028
   (`reflections/{group}/<name>.py` exposing `run()`), plus the compatibility
   re-export shim (`reflections/{group}.py`) that keeps historical registry
   dotted paths resolving.
3. YAML registration in `config/reflections.yaml` (`name` / `every` / `priority`
   / `execution_type` / `callable` or `command`).
4. Async-safety (wrap blocking I/O in `await asyncio.to_thread(...)`, or write a
   plain `def` that the scheduler dispatches via `run_in_executor`).
5. Testing via the `reflections/` package smoke-test pattern
   (`tests/unit/test_reflections_package.py`, `assert_valid_result`).
6. Both `execution_type: function` and `execution_type: agent` reflection types.
7. A checklist that targets real files.

The canonical example is `reflections/housekeeping/disk_space_check.py::run`.

## Freshness Check

**Baseline commit:** `19baaa330190eab98881674d6b8b480f0846fd7b`
**Issue filed at:** 2026-04-17T10:11:01Z (re-scope comment: 2026-06-24T06:10:31Z)
**Disposition:** Minor drift

**File:line references re-verified (against current main):**
- `scripts/reflections.py` — issue claims absent — **still absent** (`ls` → No such file).
- `class ReflectionRunner` — issue claims zero matches — **still zero** repo-wide.
- `reflections/__init__.py:1-9` — documents the callable contract (no args →
  `{status, findings, summary}` dict) — **holds**.
- `reflections/housekeeping/disk_space_check.py::run` — canonical example —
  **present**, async `run()` returning the dict with the standardized docstring
  header (What it does / Cadence / Failure modes / Related reflections / See also).
- `reflections/maintenance.py:1-27` — **now a compatibility re-export shim**
  (`from reflections.housekeeping.disk_space_check import run as run_disk_space_check`,
  etc.). The issue named `reflections/maintenance.py::run_disk_space_check` as the
  canonical example; that symbol is now a re-export of the per-file `run`. The
  rewrite should point at the per-file module as canonical and explain the shim.
- `config/reflections.yaml` — uses mixed dotted paths: legacy re-export paths
  (`reflections.maintenance.run_disk_space_check`, line ~193) and new per-file
  paths (`reflections.housekeeping.test_baseline_refresh_check.run`, line ~348).
- Schedule grammar is `every: 300s` (suffixed duration), **not** `interval:`
  (the yaml header comment is stale; `docs/features/reflections.md:74,86` document
  the `every:` grammar and the `interval:` migration).

**Cited sibling issues/PRs re-checked:**
- #1028 — **CLOSED 2026-06-24** ("one file per reflection under
  `reflections/{group}/`"). The issue's "architecture may shift again" heads-up is
  now reality; the re-scope comment mandates documenting the new layout.
- #748 — reflections monolith deletion (plan
  `docs/plans/completed/reflections-monolith-deletion.md`) — the origin of the
  staleness.

**Commits on main since issue was filed (touching the doc):**
- `f80f9894a` docs: fix stale test refs (#1294) — **partially** corrected the doc's
  Test Pattern section to reference the split `tests/unit/test_reflections_*.py`
  files. The template, `ReflectionRunner`, `scripts/reflections.py`, and Reference
  Implementation sections remain stale — the core rewrite the issue describes is
  still needed.
- `2a8f7b790`, `5f4429455` — Cowork migration commits touching the doc's Cowork
  cross-reference paragraph. Preserve/refresh that cross-reference in the rewrite.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The re-scope comment's mandate (document the #1028 per-file layout, not
just the YAML+callable contract) is fully realized on main. Proceeding on the
re-scoped premise: Option 1 (rewrite), targeting the per-file structure.

## Prior Art

- **Issue #1028** (CLOSED, merged): restructured `reflections/` into one file per
  reflection under `reflections/{group}/`, with `reflections/{group}.py`
  compatibility shims. This is the architecture the rewrite must document.
- **Issue #748** (reflections monolith deletion): removed `ReflectionRunner` and
  `scripts/reflections.py`. Root cause of the doc's staleness.
- **PR #1294** ("docs: fix stale test refs"): a prior, partial touch-up of this
  same doc — corrected only the test-file references, not the architecture. Shows
  the doc has been patched piecemeal without a full rewrite.
- **Issues #1261, #1480** ("Docs auditor: docs_features_adding-reflection-tasks_md"):
  the docs-auditor reflection has repeatedly flagged this file. This issue (#1031)
  is the consolidated fix.
- No prior *failed* rewrite attempt exists — the doc has only been partially
  patched, never fully rewritten. No "Why Previous Fixes Failed" section needed.

## Research

No relevant external findings — proceeding with codebase context. This is a
purely internal documentation change describing this repo's reflection
architecture; no external libraries, APIs, or ecosystem patterns are involved.

## Data Flow

Not applicable — this is a documentation-only change with no runtime data flow.
The *subject* the doc describes (scheduler reads `config/reflections.yaml` →
resolves the `callable` dotted path via importlib → invokes `run()` → collects
the returned dict) is covered in `docs/features/reflections.md` and is
cross-referenced, not duplicated, by the rewritten doc.

## Appetite

**Size:** Small

**Team:** Solo documentarian

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue + re-scope comment)
- Review rounds: 1 (accuracy review against the live code)

The work is a single-file rewrite plus a one-line README index update. The
bottleneck is accuracy against current source, not volume.

## Prerequisites

No prerequisites — this is a documentation-only change with no external
dependencies. The verification greps run against files already in the repo.

## Solution

### Key Elements

- **Rewritten `docs/features/adding-reflection-tasks.md`**: describes the current
  callable + per-file architecture, structured as the seven sections the issue's
  Option 1 enumerates, using `reflections/housekeeping/disk_space_check.py::run`
  as the canonical worked example.
- **Updated `docs/features/README.md` index row**: the one-line description must
  match what the doc becomes (drop "steps" / "reflection steps" language that
  encodes the deleted numbered-step model).

### Flow

Contributor opens `docs/features/adding-reflection-tasks.md` → reads the callable
contract → copies the canonical `run()` example into a new
`reflections/{group}/<name>.py` → registers it in `config/reflections.yaml` →
adds a smoke test → runs the test → lands the reflection.

### Technical Approach

The rewrite covers these topics, each grounded in a live file:

1. **Callable contract.** No args; returns `{"status", "findings", "summary"}`.
   `status` ∈ {ok, error, skipped, disabled}; `findings` is a list; `summary` is
   a str. Callable may be **sync `def` or `async def`** — the scheduler dispatches
   sync callables via `run_in_executor`. Ground: `reflections/__init__.py:1-9`,
   `tests/unit/test_reflections_package.py` (`run_async`, `assert_valid_result`).

2. **File layout (#1028).** One file per reflection at
   `reflections/{group}/<name>.py`, each exposing `run()`. Existing groups:
   `housekeeping/`, `memory/`, `agents/`, `audits/`, `pm_briefings/`. Include the
   standardized module-docstring header convention observed in the canonical
   example (What it does / Cadence / Failure modes / Related reflections / See
   also). Explain the **compatibility re-export shim**: `reflections/{group}.py`
   (e.g. `reflections/maintenance.py:1-27`) re-imports each per-file `run` under
   its historical name so registry dotted paths keep resolving without a yaml edit
   — new reflections should register the per-file dotted path directly.

3. **YAML registration.** A `config/reflections.yaml` entry with `name`,
   `description`, `every: <N>s` (the suffixed-duration grammar — NOT `interval:`),
   `priority` (urgent/high/normal/low), `execution_type: function`, `callable`
   (dotted path to the per-file `run`), `enabled`. Cross-reference
   `docs/features/reflections.md` Schedule Grammar / Registry Format sections
   rather than duplicating them.

4. **Async-safety.** Wrap blocking I/O in `await asyncio.to_thread(...)` inside an
   `async def run()`, or write a plain `def run()` that the scheduler runs in an
   executor. Never block the scheduler event loop. Ground:
   `docs/features/reflections.md` async-safety note.

5. **Testing.** Add a smoke test to `tests/unit/test_reflections_package.py` (or a
   sibling `test_reflections_<topic>.py`) using the `assert_valid_result` helper —
   verify the callable imports, runs with mocked Redis/filesystem, and returns a
   valid dict.

6. **Agent-type reflections.** `execution_type: agent` uses a `command:`
   natural-language PM prompt instead of `callable:`. Ground: yaml
   `system-health-digest`, `sentry-issue-triage`; `agent/reflection_scheduler.py`.

7. **Checklist** targeting real files: create `reflections/{group}/<name>.py`,
   register in `config/reflections.yaml`, add the smoke test, run
   `pytest tests/unit/test_reflections_package.py -x -q`, update
   `docs/features/reflections.md` if the reflection changes the registered set.

Preserve the doc's opening "is this a reflection or a Cowork routine?" decision
paragraph (it cross-references `cowork-tasks.md` and is still accurate), refreshed
for the current architecture.

### Implementation Notes (critique concerns)

These three notes were raised by the plan critique (READY TO BUILD WITH CONCERNS)
and are embedded here as build-time requirements. They are non-blocking but must
be honored by the doc-rewrite builder and confirmed by the validator.

1. **Assert `every: <N>s` grammar at write-time.** The rewritten YAML example must
   use the suffixed-duration `every:` grammar (e.g. `every: 300s`), never the
   legacy `interval:` key. The stale header comment in `config/reflections.yaml`
   still shows `interval:` — do NOT copy it. A mechanical grep check is added to the
   Verification table below (`every:` present, `interval:` absent) so a regression
   to the old grammar fails the accuracy gate rather than shipping silently.

2. **Show async-safety in the worked example, and grep for it.** The canonical
   `run()` example (or its surrounding prose) must contain a concrete async-safety
   construct — `await asyncio.to_thread(...)` in an `async def`, or a plain sync
   `def` dispatched via `run_in_executor`. This is added as a Verification row
   (`asyncio.to_thread|run_in_executor`, expect > 0) so the doc cannot describe
   async-safety abstractly without demonstrating it.

3. **State the `redis.exceptions.ConnectionError` handling requirement as prose.**
   The doc must explicitly tell contributors that a reflection's `run()` should
   handle `redis.exceptions.ConnectionError` (return `status: "error"` or
   `"skipped"` rather than propagating), because reflections routinely touch Redis
   and the scheduler runs them unattended. Note in the prose that the canonical
   `disk_space_check.py` example satisfies this only *incidentally* via a broad
   `except Exception` — a new reflection author should treat Redis-connection
   failure as a named, expected failure mode, not rely on a catch-all. This is
   primarily a prose requirement, tied to the module-docstring "Failure modes"
   header convention the doc already teaches; a light presence-check grep
   (`ConnectionError` appears) is added to the Verification table to confirm the
   prose lands.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this change edits Markdown files only; there is
  no executable code path to test.

### Empty/Invalid Input Handling
- Not applicable — no functions are added or modified.

### Error State Rendering
- Not applicable — no user-visible runtime output. The "output" is documentation
  prose, verified by the doc-accuracy grep checks in Verification.

## Test Impact

No existing tests affected — this is a documentation-only rewrite of a single
Markdown file plus a one-line README index edit, and no test asserts on the
contents of `adding-reflection-tasks.md` (verified: `grep -rn
"adding-reflection-tasks" tests/` returns no matches). The
`tests/unit/test_reflections_package.py` file the rewritten doc points at as the
copy-from pattern is referenced, not modified, by this work.

## Rabbit Holes

- **Do not restructure `docs/features/reflections.md`.** That file already
  documents the scheduler, registry format, schedule grammar, state model, and
  package layout. The rewrite cross-references it; it does not duplicate or
  reorganize it.
- **Do not re-verify or "clean up" the mixed dotted-path state in
  `config/reflections.yaml`.** Both the legacy re-export paths and the new
  per-file paths resolve correctly. Migrating existing entries to per-file paths
  is out of scope (that is registry churn, not doc work).
- **Do not add a new reflection** as a "worked example." Reference the existing
  `disk_space_check` reflection; adding real registry entries is not doc work.
- **Do not document every group or every reflection.** One canonical example plus
  the general pattern is the target; an exhaustive catalog belongs in
  `reflections.md` (which already has one).

## Risks

### Risk 1: Doc drifts again if #1028-style restructuring continues
**Impact:** A future per-file or per-group change re-stales the doc.
**Mitigation:** Anchor the doc to the *contract* (no-arg callable → dict) and the
*current* `reflections/{group}/<name>.py` convention, and cross-reference
`reflections.md` for the authoritative registry/scheduler detail rather than
restating it. Point at `disk_space_check.py` by path so a rename is a one-line
fix. The docs-auditor reflection will re-flag the file if it drifts.

### Risk 2: Canonical-example choice is ambiguous (shim vs per-file)
**Impact:** The reader copies the re-export shim instead of a real reflection.
**Mitigation:** Explicitly name `reflections/housekeeping/disk_space_check.py::run`
as the file to copy, and describe `reflections/maintenance.py` as a shim the
reader should NOT edit (it is generated-by-convention, not a place to add code).

## Race Conditions

No race conditions identified — this is a synchronous, single-file documentation
edit with no concurrent access patterns.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1028] Migrating the remaining legacy `reflections.<group>.<fn>`
  registry dotted paths in `config/reflections.yaml` to per-file paths. #1028 is
  the restructuring effort; the compat shims make this optional and it is registry
  churn, not doc work.
- Rewriting or reorganizing `docs/features/reflections.md`. It is accurate and
  authoritative; this plan only cross-references it. (Not deferred work — it is
  simply not part of fixing the stale developer guide.)

## Update System

No update system changes required — this is a documentation-only change. `/update`
propagates the repo as-is; no new dependencies, config files, or migration steps
are introduced.

## Agent Integration

No agent integration required — this edits Markdown docs only. No CLI entry point,
MCP surface, or bridge import is added or changed.

## Documentation

### Feature Documentation
- [ ] Rewrite `docs/features/adding-reflection-tasks.md` to describe the current
  callable + per-file (#1028) architecture, per the seven-section structure in the
  Solution above, using `reflections/housekeeping/disk_space_check.py::run` as the
  canonical example.
- [ ] Update the `docs/features/README.md` index row (line 10) so the description
  matches the rewritten doc (drop the deleted "reflection steps" / numbered-step
  language).

### External Documentation Site
- Not applicable — this repo has no Sphinx/MkDocs/RTD site for these docs.

### Inline Documentation
- Not applicable — no code changes.

## Success Criteria

- [ ] `docs/features/adding-reflection-tasks.md` accurately describes the callable
  contract (no args → `{status, findings, summary}`) and the #1028 per-file layout.
- [ ] No references to `scripts/reflections.py`, `ReflectionRunner`,
  `self.state.add_finding`, `self.state.step_progress`, `step_<key>` method naming,
  or `tests/test_reflections.py` remain anywhere in the new doc.
- [ ] The doc covers async-safety, `config/reflections.yaml` registration, and both
  `function` and `agent` execution types.
- [ ] The doc uses `reflections/housekeeping/disk_space_check.py::run` as the
  canonical example and names files that exist on `main`.
- [ ] `docs/features/README.md` index description matches the rewritten doc.
- [ ] Documentation reviewed for accuracy against live source (`/do-docs` / manual).

## Team Orchestration

The lead agent dispatches a single documentarian, then a validator confirms the
doc is accurate and the forbidden references are gone.

### Team Members

- **Builder (doc-rewrite)**
  - Name: `reflection-doc-writer`
  - Role: Rewrite `adding-reflection-tasks.md` and update the README index row.
  - Agent Type: documentarian
  - Resume: true

- **Validator (doc-accuracy)**
  - Name: `reflection-doc-validator`
  - Role: Verify the rewritten doc names only real files/symbols, that the
    forbidden-reference greps return zero, and that the README row matches.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Uses `documentarian` (rewrite) and `validator` (accuracy check) — both Tier 1.

## Step by Step Tasks

### 1. Rewrite the developer guide
- **Task ID**: build-doc-rewrite
- **Depends On**: none
- **Validates**: the Verification greps below (forbidden references absent; real
  symbols present)
- **Assigned To**: reflection-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite `docs/features/adding-reflection-tasks.md` per the seven-section Solution
  structure, grounded in: `reflections/__init__.py`,
  `reflections/housekeeping/disk_space_check.py`, `reflections/maintenance.py`
  (shim), `config/reflections.yaml`, `tests/unit/test_reflections_package.py`.
- Use `reflections/housekeeping/disk_space_check.py::run` as the canonical worked
  example; describe `reflections/maintenance.py` as a shim not to edit.
- Cover async-safety (`await asyncio.to_thread(...)` or sync `def`), `every:`
  schedule grammar, and both `function` and `agent` execution types.
- Preserve/refresh the opening reflection-vs-Cowork-routine decision paragraph.
- Cross-reference `docs/features/reflections.md` for registry/scheduler detail
  rather than duplicating it.

### 2. Update the README index row
- **Task ID**: build-readme-index
- **Depends On**: build-doc-rewrite
- **Assigned To**: reflection-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Edit `docs/features/README.md` line 10 so the description reflects the rewritten
  doc (remove deleted "reflection steps"/numbered-step language).

### 3. Validate accuracy
- **Task ID**: validate-doc
- **Depends On**: build-doc-rewrite, build-readme-index
- **Assigned To**: reflection-doc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the forbidden-reference greps (Verification table) and confirm zero matches.
- Confirm every file/symbol the doc names exists on `main`.
- Confirm the README row matches the rewritten doc.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No `scripts/reflections.py` ref | `grep -c "scripts/reflections.py" docs/features/adding-reflection-tasks.md` | match count == 0 |
| No `ReflectionRunner` ref | `grep -c "ReflectionRunner" docs/features/adding-reflection-tasks.md` | match count == 0 |
| No `step_progress`/`add_finding` ref | `grep -cE "step_progress\|add_finding" docs/features/adding-reflection-tasks.md` | match count == 0 |
| No `step_<key>` method ref | `grep -cE "step_<key>\|async def step_" docs/features/adding-reflection-tasks.md` | match count == 0 |
| No `tests/test_reflections.py` ref | `grep -c "tests/test_reflections.py" docs/features/adding-reflection-tasks.md` | match count == 0 |
| Canonical example present | `grep -c "disk_space_check" docs/features/adding-reflection-tasks.md` | output > 0 |
| Callable contract present | `grep -c "status.*findings.*summary\|findings.*summary" docs/features/adding-reflection-tasks.md` | output > 0 |
| YAML registration covered | `grep -c "config/reflections.yaml" docs/features/adding-reflection-tasks.md` | output > 0 |
| Agent execution type covered | `grep -cE "execution_type|command:" docs/features/adding-reflection-tasks.md` | output > 0 |
| `every:` grammar used (concern 1) | `grep -c "every:" docs/features/adding-reflection-tasks.md` | output > 0 |
| No legacy `interval:` grammar (concern 1) | `grep -c "interval:" docs/features/adding-reflection-tasks.md` | match count == 0 |
| Async-safety demonstrated (concern 2) | `grep -cE "asyncio.to_thread|run_in_executor" docs/features/adding-reflection-tasks.md` | output > 0 |
| Redis ConnectionError handling in prose (concern 3) | `grep -c "ConnectionError" docs/features/adding-reflection-tasks.md` | output > 0 |
| Canonical file exists on main | `test -f reflections/housekeeping/disk_space_check.py` | exit code 0 |

## Open Questions

None. The issue plus the #1028 re-scope comment fully specify the scope (Option 1
rewrite, targeting the per-file layout). The canonical example, section structure,
and acceptance criteria are all pinned down by the issue and verified against
current `main`.
