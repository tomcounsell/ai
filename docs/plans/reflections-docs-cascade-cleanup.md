---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1032
last_comment_id:
---

# Reflections Docs Cascade Cleanup

## Problem

Issue #748 deleted the `scripts/reflections.py` monolith and the `ReflectionRunner` class. The code migration shipped (the current runtime uses the `reflections/` package plus `agent/reflection_scheduler.py` embedded in the worker). But the doc cascade was only partially completed: six feature/guide/research docs outside `docs/plans/completed/` still describe the deleted monolith as the live execution path. A reader finding any of these pages by search will build a wrong mental model of how reflections work today. Sibling issue #1031 covers the `adding-reflection-tasks.md` rewrite separately; this issue cleans up the remaining live-docs cascade.

**Current behavior:**
- `popoto-index-hygiene.md` tells readers that `ReflectionRunner.step_popoto_index_cleanup` runs via launchd as a "standalone safety net" — the runner doesn't exist, and there is no launchd service.
- `bridge-self-healing.md` tells readers that `reflections.log` is configured in `scripts/reflections.py` — the file doesn't exist.
- `telegram-history.md` tells readers Redis TTL is cleaned by `step_redis_cleanup() (step 13)` — step numbers no longer exist; the callable is `reflections.maintenance.run_redis_ttl_cleanup`.
- Both `claude-code-feature-swot.md` variants (`docs/research/` and `docs/guides/`) list `com.valor.reflections` launchd and show `python scripts/reflections.py --dry-run` as a code example — neither exists.
- `valor-name-references.md` maps `data/valor.session` to `scripts/reflections.py` at line 100, and has several other rows referencing `com.valor.reflections`/`com.valor.reflections.plist` as live infrastructure.

**Desired outcome:**
- All six files describe the current architecture accurately: `reflections/` package + `config/reflections.yaml` (scheduler config), `agent/reflection_scheduler.py` (scheduler code), worker-embedded dispatch (no launchd service), and name-based reflection identifiers (no step numbers).
- The acceptance-criteria `grep` for the three monolith strings returns only references inside explicitly historical contexts (completed plans, migration notes, or rows clearly labeled as removed/historical in `valor-name-references.md`).
- Tables of contents and cross-doc links in the edited files still match what each doc now describes.

## Freshness Check

**Baseline commit:** `8a860f08`
**Issue filed at:** 2026-04-17T10:11:23Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `docs/features/popoto-index-hygiene.md:35` — `ReflectionRunner in scripts/reflections.py includes a step_popoto_index_cleanup step` — still holds verbatim.
- `docs/features/popoto-index-hygiene.md:52` — `| ReflectionRunner | \`python scripts/reflections.py\` (launchd) |` — still holds.
- `docs/features/popoto-index-hygiene.md:68` — `| \`scripts/reflections.py\` | \`ReflectionRunner.step_popoto_index_cleanup\` standalone safety net |` — still holds.
- `docs/features/bridge-self-healing.md:11` — `pattern was applied to tools/valor_telegram.py and scripts/reflections.py` — still holds.
- `docs/features/bridge-self-healing.md:112` — `reflections.log — configured in scripts/reflections.py` — still holds.
- `docs/features/telegram-history.md:58` — `cleaned by reflections step_redis_cleanup() (step 13)` — still holds (adjacent lines 47 and 54 also say `cleaned by reflections step 13` — those rows are in scope even though not line-cited in the issue, because the issue acceptance criteria says "Cited step numbers are replaced with callable names").
- `docs/research/claude-code-feature-swot.md:414,420` and `docs/guides/claude-code-feature-swot.md:414,420` — `| Reflections | com.valor.reflections | scripts/reflections.py | Scheduled |` and `python scripts/reflections.py --dry-run` example — both files still hold verbatim.
- `docs/guides/valor-name-references.md:100` — `| data/valor.session | scripts/reflections.py (Telegram session) |` — still holds.

**Cited sibling issues/PRs re-checked:**
- #748 — closed 2026-04-14T18:52:58Z, resolution: "Finish reflections unification: extract monolith units, wire memory reflections, relocate config". The deletion shipped cleanly; nothing in the resolution contradicts this issue's premise.
- #1031 — still open. Its scope (`adding-reflection-tasks.md` rewrite) is disjoint from this issue's six files; no coordination needed beyond the No-Gos below.
- #1134 — merged 2026-04-22T18:01:07Z (chore(reflections): scrub monolith-migration annotations). Explicitly deferred all six of this issue's target files to #1032 — confirming our scope boundary.

**Commits on main since issue was filed (touching referenced files):**
- `db45d622` chore(#1030): replace root-requiring newsyslog with user-space log rotation (#1100) — touched `bridge-self-healing.md`, added the Log Rotation paragraph at line 117. Irrelevant to monolith claims; our line 112 edit is still the correct target.
- `c66b7b1c` Fix agent-session-cleanup phantom-record destruction (#1069) (#1078) — unrelated to our edits.
- `d76232f4`, `350df702`, `f0332258`, `3f97603d` — touched these files in various ways (health-check cascades, cascade updates). Line numbers the issue cites are still valid. Confirmed by re-reading each target section above.

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/adding-reflection-tasks-rewrite.md` would belong to #1031 but does not yet exist; no coordination collision.

**Notes:** Freshness check fully Unchanged. One inline note to surface in Technical Approach: `telegram-history.md` has "step 13" references at lines 47 and 54 in addition to the issue's line 58. All three are symptoms of the same stale convention and must be fixed together, per the issue's acceptance criterion "Cited step numbers are replaced with callable names".

## Prior Art

- **PR #1134** (merged 2026-04-22): `chore(reflections): scrub monolith-migration annotations`. Scrubbed migration annotations from `reflections/*.py` and left a one-line convention note in `docs/features/reflections.md`. Explicitly kept this issue's six docs out of scope — its No-Gos section and plan body repeatedly cite #1032 as the tracking issue for these files. Establishes the scope boundary: our PR touches ONLY the six files the issue lists, and no files inside `reflections/` or any other docs.
- **Issue #748** (closed 2026-04-14): `Finish reflections unification: extract monolith units, wire memory reflections, relocate config`. The code-side deletion. Established the replacement architecture (package + scheduler + YAML registry) that our docs must now describe.
- **Issue #1031** (still open): `Docs: rewrite adding-reflection-tasks.md — describes removed ReflectionRunner architecture`. Complementary to us but disjoint — different file, different shape (full rewrite vs. surgical edits).
- **Sibling plan** `docs/plans/completed/scrub-reflections-migration-annotations.md` — the planning-level precedent for scope discipline. Its Rabbit Holes and No-Gos sections list the exact six files this plan now picks up.

## Research

No relevant external findings — this is a pure internal docs cleanup with a concrete replacement mapping already in the issue body. WebSearch was skipped per the do-plan Phase 0.7 skip clause.

## Data Flow

Not applicable — documentation-only change. No runtime data paths touched.

## Architectural Impact

Not applicable — documentation-only change. No code, no dependencies, no interfaces, no data ownership changes.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully locked by the issue's file list and replacement table)
- Review rounds: 1 (docs-only PR, straightforward review)

## Prerequisites

No prerequisites — this is a pure documentation edit with no external dependencies.

## Solution

### Key Elements

- **Replacement mapping (from issue body):** applied uniformly across the six target files.
  - `scripts/reflections.py` → `reflections/` package + `config/reflections.yaml`
  - `ReflectionRunner` class → `ReflectionScheduler` (`agent/reflection_scheduler.py`)
  - `step_<name>()` methods → callables in `reflections/<module>.py` (registered by name in `config/reflections.yaml`)
  - `com.valor.reflections` launchd service → worker-embedded scheduler (`python -m worker`)
  - `python scripts/reflections.py [--dry-run]` → no direct invocation; reflections run on YAML-declared intervals
  - Numbered step references (e.g. `step 13`) → name-based references (e.g. `redis-ttl-cleanup`)

- **Per-file edit summary:**
  - `docs/features/popoto-index-hygiene.md` — remove the "Cleanup Reflection (Runner)" section entirely and rename "Cleanup Reflection (Scheduler)" to "Cleanup Reflection". The "Three Cleanup Paths" table collapses to two rows (worker startup + scheduler). Update the Key Files row that references `scripts/reflections.py`.
  - `docs/features/bridge-self-healing.md` — line 11: remove `scripts/reflections.py` from the defensive-pattern list (the pattern is no longer in that file). Line 112: replace the `reflections.log` configuration pointer with the current location (configured inside the `reflections/` package — specifically via `RotatingFileHandler` setup in whichever reflection module writes it). If no current module writes `reflections.log`, remove the bullet entirely and note that reflection output is written to worker logs.
  - `docs/features/telegram-history.md` — lines 47, 54, 58: replace every "cleaned by reflections step 13" and "step_redis_cleanup()" with a name-based reference: "cleaned by the `redis-ttl-cleanup` reflection (`reflections.maintenance.run_redis_ttl_cleanup`)".
  - `docs/research/claude-code-feature-swot.md` and `docs/guides/claude-code-feature-swot.md` — line 414: remove the Reflections row from the launchd schedules table (reflections no longer run via launchd) OR rewrite it to describe the worker-embedded scheduler. Lines 417-429: replace the `python scripts/reflections.py --dry-run` code example with a description of the current registry-driven flow (no direct invocation; the numbered step list is gone). Both files must be kept consistent — a post-edit `diff` should only differ in the pre-existing ChatSession/PM session delta, not in the reflections section.
  - `docs/guides/valor-name-references.md` — line 100: remove the `data/valor.session → scripts/reflections.py` row (the path is no longer used by a deleted file). Lines 88, 90, 92, 93, 111 (out of the issue's literal citation but required by the acceptance-criteria `grep`): each row in the launchd/scripts tables references `com.valor.reflections` or `com.valor.reflections.plist`. Triage each row: if the file it points to still exists AND still has the reference, keep the row but add a "removed" annotation explaining that the service is gone. If the file no longer exists or no longer has the reference, remove the row. **This file requires care** — it's a reference grid, not a prose doc; rows document where names were wired, so some rows stay as historical record.

### Flow

Not applicable — documentation edits with no user journey.

### Technical Approach

- Each file edit is mechanical: one or a few targeted replacements per the mapping above, preserving surrounding context (tables, code fences, adjacent paragraphs).
- No new sections created; no cross-doc links added or removed unless an entire section is deleted (e.g., "Cleanup Reflection (Runner)" in `popoto-index-hygiene.md`).
- After all edits, re-run the acceptance-criteria `grep` from the issue body and verify the only hits outside `docs/plans/completed/` are in explicitly historical contexts (or rows in `valor-name-references.md` annotated as removed).
- For `valor-name-references.md`: because this file documents where names were wired (past-tense intent), I will NOT aggressively delete every grep hit. The editorial rule is: if the referenced file still exists AND still contains the name, keep the row and annotate it as "(removed service)" or similar; otherwise drop the row. The issue's acceptance criteria clause "only references inside explicitly historical contexts" supports this — annotated rows in a name-inventory table qualify as historical.
- **Verification on this branch:** after edits, the worktree's `git diff main -- docs/` file list must be exactly the six files listed in the issue (plus the plan file itself, which commits to main separately per /do-plan Phase 2.5).

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a pure documentation edit. No `except` blocks are added, removed, or modified.

### Empty/Invalid Input Handling
Not applicable — no functions are added or modified.

### Error State Rendering
Not applicable — no user-visible output is changed (all edits are within internal documentation pages).

## Test Impact

No existing tests affected — this is a documentation-only change. No test file in the repository references `scripts/reflections.py`, `ReflectionRunner`, or `com.valor.reflections` (verified by grep across `tests/`). Adding a regression test for docs content (e.g., "assert grep returns zero hits") would be fragile over time as new historical contexts accrue; the acceptance-criteria `grep` in the PR description is sufficient evidence.

## Rabbit Holes

- **Don't rewrite `docs/features/adding-reflection-tasks.md`.** That's #1031's scope. If a new reader would benefit from a link to where the registry system is documented today, link to `docs/features/reflections.md` instead.
- **Don't fix `config/reflections.yaml`'s stale comment.** The `redis-index-cleanup` entry in the vault YAML still mentions `step_popoto_index_cleanup` as a parallel wiring path. That's a registry content bug (affects active runtime configuration, not docs), not a doc bug. File a separate issue if this matters; do not mix it into this docs PR.
- **Don't cascade into `docs/features/documentation-audit.md`, `session-lifecycle.md`, `pm-dev-session-architecture.md`, `sustainable-self-healing.md`, `unified-analytics.md`, `bridge-resilience.md`, `session-lifecycle-diagnostics.md`.** Those also contain monolith references (surfaced by the broader grep in the Recon Summary), but the issue body constrains scope to the six files it lists. Those other files are tracked separately or accepted as tangential history.
- **Don't reshape `popoto-index-hygiene.md`.** The Three Cleanup Paths table collapses to two rows — that's the only structural change. Resist refactoring the whole doc.
- **Don't edit the plan PR to close the tracking issue.** `/do-plan` phase 2.5 is explicit: only the implementation PR (from `/do-build`) uses `Closes #1032`. The plan commits to main and does not close #1032.

## Risks

### Risk 1: Editorial over-reach in `valor-name-references.md`
**Impact:** Aggressive row-deletion turns a historical inventory into an incomplete grid, losing information that future migrations will need.
**Mitigation:** Triage row-by-row. Keep rows where the referenced file still exists and still contains the name, annotated as "(removed service)" or similar. Only delete rows where the referenced file or reference is also gone.

### Risk 2: Grep-based acceptance criterion produces false positives on historical rows
**Impact:** After the edits, `grep -rn "scripts/reflections.py\|ReflectionRunner\|com.valor.reflections" docs/features docs/guides docs/research` may still show hits in `valor-name-references.md` rows that are intentionally preserved as historical record. A strict interpretation of the acceptance criterion would flag these as failures.
**Mitigation:** The issue's acceptance criterion reads "returns only references inside explicitly historical contexts". Annotated rows in a name-inventory table qualify. The PR description must explicitly note which remaining grep hits are historical-by-design, so the reviewer agrees on the interpretation before merge. If the reviewer disagrees, collapse to stricter deletion in a follow-up commit.

### Risk 3: Drift between the two `claude-code-feature-swot.md` copies
**Impact:** The research and guides copies are near-duplicates today (differ only in ChatSession↔PM session terminology). Editing one and forgetting the other would re-introduce divergence.
**Mitigation:** Edit both in the same commit. After editing, run `diff docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md` and verify the only differences are the pre-existing ChatSession/PM session ones — no new divergence introduced by this PR.

## Race Conditions

No race conditions identified — all edits are to static documentation files with no concurrency implications.

## No-Gos (Out of Scope)

- `adding-reflection-tasks.md` rewrite (→ #1031).
- Any other docs file outside the six listed in the issue body (even if the broader grep surfaces matches).
- Any code-side change to `reflections/`, `agent/reflection_scheduler.py`, `config/reflections.yaml`, or `scripts/`.
- Adding or re-wiring launchd services (they're gone, and that's correct).
- Creating new docs or new cross-links beyond what the edits logically require (e.g., if a section is removed, a cross-ref pointing to it must be updated).
- Adding regression tests for doc content.
- Touching `docs/plans/completed/*` (their references to the monolith are appropriate).

## Update System

No update system changes required — this PR edits documentation only. `/update` runs `git pull` on target machines and picks up the doc edits automatically. No dependencies, no config files, no migration steps.

## Agent Integration

No agent integration required — this is a pure documentation edit. The agent does not read these docs at runtime; they are human-facing reference material. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Documentation

This PR **is** the documentation work. The edits themselves are the deliverable.

- [ ] Edit `docs/features/popoto-index-hygiene.md` per Solution (remove Cleanup Reflection (Runner) section; collapse Three Cleanup Paths table to two rows; update Key Files table row).
- [ ] Edit `docs/features/bridge-self-healing.md` per Solution (fix line 11 and line 112).
- [ ] Edit `docs/features/telegram-history.md` per Solution (fix lines 47, 54, 58 — all step-13 references).
- [ ] Edit `docs/research/claude-code-feature-swot.md` per Solution (fix line 414 table row; rewrite the lines 417-429 code example and its "Tasks performed" commentary).
- [ ] Edit `docs/guides/claude-code-feature-swot.md` per Solution (same edits as the research copy; keep both in lockstep).
- [ ] Edit `docs/guides/valor-name-references.md` per Solution (remove line 100 row; triage lines 88, 90, 92, 93, 111 per the annotation rule).
- [ ] Verify via `diff` that the two `claude-code-feature-swot.md` copies stay in sync (only pre-existing ChatSession/PM session differences remain).
- [ ] Run the acceptance-criteria `grep` and confirm remaining hits are inside historical contexts or annotated rows. Capture the output in the PR description.

No new feature docs or index entries are needed — every change is to existing pages. `/do-docs` is effectively this PR's build step.

## Success Criteria

- [ ] `popoto-index-hygiene.md`, `bridge-self-healing.md`, `telegram-history.md`, `valor-name-references.md`, and both `claude-code-feature-swot.md` variants no longer describe the monolith as live. (Issue acceptance criterion 1.)
- [ ] Cited step numbers are replaced with callable names (or removed if no longer applicable). (Issue acceptance criterion 2. Covers `telegram-history.md:47,54,58`.)
- [ ] `grep -rn "scripts/reflections.py\|ReflectionRunner\|com.valor.reflections" docs/features docs/guides docs/research` returns only references inside explicitly historical contexts (completed plans, migration notes, annotated historical rows in `valor-name-references.md`). (Issue acceptance criterion 3.)
- [ ] Doc table of contents / README indexes still match what each doc now describes. (Issue acceptance criterion 4. Verified by spot-checking cross-references from `docs/features/README.md` and each edited file's own section structure.)
- [ ] The two `claude-code-feature-swot.md` copies remain semantically identical aside from the pre-existing ChatSession/PM session terminology delta.
- [ ] Lint/format pass (`python -m ruff format .` — no-op on markdown, but runs cleanly).
- [ ] PR description explicitly states scope boundary ("touches only the six files listed in #1032; non-overlapping with #1031 and #1134").

## Team Orchestration

Small docs-only work. Single builder + lightweight validator pass; no parallelism needed.

### Team Members

- **Builder (docs-edits)**
  - Name: docs-cascade-builder
  - Role: Apply the six file edits per the Solution section, preserving surrounding structure and running the acceptance grep.
  - Agent Type: documentarian
  - Resume: true

- **Validator (docs-cascade)**
  - Name: docs-cascade-validator
  - Role: Verify the acceptance criteria grep returns only historical contexts; verify the two SWOT copies stay in lockstep; spot-check cross-references.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply the six file edits
- **Task ID**: build-docs-edits
- **Depends On**: none
- **Validates**: Manual inspection of each edited file against the Solution section; acceptance-criteria grep
- **Informed By**: Recon Summary (confirmed file:line references); Solution section's per-file edit summary
- **Assigned To**: docs-cascade-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Edit `docs/features/popoto-index-hygiene.md`: remove "Cleanup Reflection (Runner)" section (lines 33-35); rename "Cleanup Reflection (Scheduler)" to "Cleanup Reflection"; update the "Three Cleanup Paths" table (remove `ReflectionRunner` row at line 52); update the Key Files table to remove the `scripts/reflections.py` row (line 68).
- Edit `docs/features/bridge-self-healing.md`: line 11, remove `scripts/reflections.py` from the defensive-pattern list; line 112, remove the `reflections.log — configured in scripts/reflections.py` bullet (if no current module writes `reflections.log`, drop the bullet entirely; otherwise replace the pointer with the current location).
- Edit `docs/features/telegram-history.md`: lines 47, 54, 58 — replace every "step 13" / "step_redis_cleanup()" with the name-based form "the `redis-ttl-cleanup` reflection (`reflections.maintenance.run_redis_ttl_cleanup`)".
- Edit `docs/research/claude-code-feature-swot.md`: line 414, rewrite the Reflections launchd row (either remove it because reflections no longer run via launchd, or describe the worker-embedded scheduler briefly); lines 417-429, replace the `python scripts/reflections.py --dry-run` code example and its numbered "Tasks performed" list with a one-paragraph description of the registry-driven flow pointing to `config/reflections.yaml`.
- Edit `docs/guides/claude-code-feature-swot.md`: apply the same edits as the research copy. After edits, run `diff docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md` and verify only the pre-existing ChatSession/PM session differences remain.
- Edit `docs/guides/valor-name-references.md`: remove line 100 row (`data/valor.session → scripts/reflections.py`). Triage lines 88, 90, 92, 93, 111: for each row, check whether the referenced file (`scripts/update/service.py`, `scripts/install_reflections.sh`, `scripts/remote-update.sh`, `scripts/update/run.py`, `com.valor.reflections.plist`) still exists and still contains the reference. If it exists, annotate the row as `(removed service)` keeping it for historical record. If it's gone, delete the row.
- Commit message: `docs(#1032): cascade reflections monolith-deletion cleanup across feature/guide docs`.

### 2. Validate acceptance criteria
- **Task ID**: validate-docs-cascade
- **Depends On**: build-docs-edits
- **Assigned To**: docs-cascade-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands (see next section).
- Verify each Success Criteria checkbox and report status.
- Produce a concise pass/fail report that lists any remaining grep hits and classifies each as "historical context" (pass) or "unexpected live claim" (fail).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Plan's six files edited | `git diff --name-only main -- docs/features docs/guides docs/research` | output contains all six of `docs/features/popoto-index-hygiene.md`, `docs/features/bridge-self-healing.md`, `docs/features/telegram-history.md`, `docs/research/claude-code-feature-swot.md`, `docs/guides/claude-code-feature-swot.md`, `docs/guides/valor-name-references.md` |
| No out-of-scope files edited | `git diff --name-only main -- docs/ \| grep -v "^docs/\\(features/popoto-index-hygiene\\|features/bridge-self-healing\\|features/telegram-history\\|research/claude-code-feature-swot\\|guides/claude-code-feature-swot\\|guides/valor-name-references\\|plans/reflections-docs-cascade-cleanup\\)"` | exit code 1 |
| SWOT copies in lockstep | `diff docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md \| grep -E "reflections\|reflections.py\|com.valor.reflections"` | exit code 1 |
| Acceptance grep (live docs) | `grep -rn "scripts/reflections.py\\\|ReflectionRunner\\\|com.valor.reflections" docs/features docs/guides docs/research` | manually-reviewed output: every hit must be inside an annotated historical row or a migration-note paragraph; validator reports pass/fail |
| Step-number references removed | `grep -rn "step_redis_cleanup\\\|step 13" docs/features/telegram-history.md` | exit code 1 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
