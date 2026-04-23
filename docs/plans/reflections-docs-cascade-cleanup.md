---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1032
last_comment_id:
revision_applied: true
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
- `docs/features/telegram-history.md:35,47,54,58` — FOUR hits of the step-13 pattern (not three — the Recon Summary originally missed line 35). Line 35 is the `TelegramMessage` TTL bullet; lines 47 and 54 are the `Link` and `Chat` TTL bullets; line 58 is the `## Data Retention` summary which also includes the callable name `step_redis_cleanup()`. All four hold verbatim today (`grep -n "step 13" docs/features/telegram-history.md` returns exactly lines 35, 47, 54, 58). All four are in scope per the issue's acceptance criteria clause "Cited step numbers are replaced with callable names".
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

**Notes:** Freshness check fully Unchanged. One inline note to surface in Technical Approach: `telegram-history.md` has "step 13" references at lines 35, 47, and 54 in addition to the issue's line 58 — FOUR total. All four are symptoms of the same stale convention and must be fixed together, per the issue's acceptance criterion "Cited step numbers are replaced with callable names". (The Recon Summary initially listed only 47/54/58 — revised during plan critique.)

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
  - "bridge-hosted" / "Bridge tick" scheduler phrasing → "worker-embedded" / "Worker scheduler tick" (the scheduler runs in the worker process, not the bridge — see blocker resolution below).

- **Canonical replacement target**: where a target file needs more than a one-line replacement, link to `docs/features/reflections.md` rather than duplicating architecture description. That doc is the single authoritative live source (lines 1-8 describe the worker-embedded scheduler + YAML registry architecture).

- **Per-file edit summary:**
  - `docs/features/popoto-index-hygiene.md`:
    1. Remove the "Cleanup Reflection (Runner)" section entirely and rename "Cleanup Reflection (Scheduler)" to "Cleanup Reflection".
    2. **(Blocker resolution)** Edit line 31: replace "`ReflectionScheduler` (bridge-hosted) dispatches this daily when the bridge is running" with "`ReflectionScheduler` (worker-embedded in `python -m worker`) dispatches this daily while the worker process runs". The scheduler is imported/started in `worker/__main__.py:351-355`, not anywhere in `bridge/`.
    3. **(Blocker resolution)** In the "Three Cleanup Paths" table, update the row 2 Trigger column from "Bridge tick (daily)" to "Worker scheduler tick (daily)". The `ReflectionScheduler` row (line 51 today) stays after the Runner row is removed above, so the table collapses to two rows (worker startup + worker scheduler).
    4. Update the Key Files row that references `scripts/reflections.py` (line 69 today: `config/reflections.yaml` row description should stay; the separate `scripts/reflections.py` row referenced in the recon needs removal — see Step 1 task for exact target).
    *Implementation Note:* `grep -rn 'reflection_scheduler\|ReflectionScheduler' bridge/` returns zero matches, confirming nothing in `bridge/` hosts the scheduler. `docs/features/reflections.md:6` uses the phrasing "runs as an asyncio task inside the standalone worker process" — match that phrasing for consistency.
  - `docs/features/bridge-self-healing.md`:
    1. Line 11: remove `scripts/reflections.py` from the defensive-pattern list (the file no longer exists).
    2. **(Concern resolution)** Line 112: REMOVE the `reflections.log — configured in scripts/reflections.py` bullet entirely. Rationale: line 109's bucket header says "Python-managed logs (auto-rotate on write via `RotatingFileHandler`, 10MB max, 5 backups)" — but `reflections.log` is NOT `RotatingFileHandler`-managed. `scripts/sdlc_reflection.py:25,48-49` writes it via a raw `open(LOG_FILE, "a")` append; shell rotation at `scripts/valor-service.sh:197` is the only rotation path. The log belongs in a different bucket (or should be dropped entirely). Option (b) — drop the bullet — is cleanest because (i) it avoids introducing a new claim about `sdlc_reflection.py` (which is a parallel path under `com.valor.sdlc-reflection`, easily conflated with the deleted `com.valor.reflections`), and (ii) line 115 already lists `reflections_error.log` in the Shell-rotated bucket, which covers the reflection output readers would look for. Do NOT add a new claim that `reflections.log` is configured somewhere in `reflections/` — that is factually wrong (`grep -rn "RotatingFileHandler" reflections/ agent/reflection_scheduler.py` returns zero).
  - `docs/features/telegram-history.md`:
    - **(Concern resolution)** Edit all FOUR lines — 35, 47, 54, 58 (not just 47/54/58). Lines 35, 47, 54 each say "TTL: 90 days (cleaned by reflections step 13)" and become "TTL: 90 days (cleaned by the `redis-ttl-cleanup` reflection)". Line 58 says "Redis models: 90-day TTL, cleaned by reflections `step_redis_cleanup()` (step 13)" and becomes "Redis models: 90-day TTL, cleaned by the `redis-ttl-cleanup` reflection (`reflections.maintenance.run_redis_ttl_cleanup`)". *Implementation Note:* `grep -n "step 13" docs/features/telegram-history.md` returns exactly these four lines; verify there are no others before committing. The Verification row 5 grep (`step_redis_cleanup\|step 13`) is the trip-wire — after edits it must return exit 1 with zero hits.
  - `docs/research/claude-code-feature-swot.md` and `docs/guides/claude-code-feature-swot.md`:
    1. **(NIT resolution)** Line 414: REMOVE the Reflections row from the launchd "Current schedules" table (the table is launchd-focused — a plist + script per row — and the worker-embedded scheduler is neither a plist nor a standalone script, so a rewrite would break the table's columns).
    2. **(NIT resolution)** Immediately after the table, add one sentence: "Reflections no longer run via launchd; the scheduler is embedded in the worker (`python -m worker`). See [Reflections](../features/reflections.md) for details." (Use `../features/reflections.md` from `docs/guides/` and `../features/reflections.md` from `docs/research/` — the relative path is identical.)
    3. Lines 417-429: replace the `python scripts/reflections.py --dry-run` code example and its numbered "Tasks performed" list with a one-paragraph description of the registry-driven flow pointing to `config/reflections.yaml`. Example replacement text: "Reflections are declared in `config/reflections.yaml` (a vault-symlinked registry). Each entry maps to a callable in the `reflections/` package (e.g., `reflections.maintenance.run_redis_ttl_cleanup`). The `ReflectionScheduler` in `agent/reflection_scheduler.py` — run as an asyncio task inside the standalone worker — enqueues due reflections at their declared intervals. There is no direct CLI invocation."
    4. Apply identically to BOTH copies. After edits, run `diff docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md` and verify only the pre-existing ChatSession/PM session differences remain.
  - `docs/guides/valor-name-references.md` — pre-computed per-row dispositions:
    - **(Concern resolution)** Row at line 88 (`com.valor.reflections.plist → com.valor.reflections`): **DELETE**. The plist file does not exist anywhere in the repo or in `~/Library/LaunchAgents/` (verified: `find . -name "com.valor.reflections.plist"` returns nothing; `ls ~/Library/LaunchAgents/com.valor.reflections.plist` → no such file). The triage rule "if the file no longer exists, delete the row" applies directly.
    - Row at line 90 (`scripts/update/service.py`): **ANNOTATE** as `(removed service)`. The file exists and still mentions `com.valor.reflections` as a historical wiring point.
    - **(Concern resolution)** Row at line 92 (`scripts/install_reflections.sh`): **DELETE**. The script does not exist in the repo (`ls scripts/install_reflections.sh` → no such file). Triage rule applies.
    - Row at line 93 (`scripts/remote-update.sh`): **ANNOTATE** as `(removed service)`. File exists and still mentions `com.valor.reflections`.
    - Row at line 100 (`data/valor.session → scripts/reflections.py`): **DELETE**. Referenced script file is gone.
    - Row at line 111 (`scripts/update/run.py`): **ANNOTATE** as `(removed service)`. File exists and still mentions `com.valor.reflections`.
    - **(NIT resolution)** Standard annotation text: `(removed service)` (no variation — use this exact string for all three annotated rows, no "(removed)", no "(historical)", no "(removed 2026-04)").
    *Implementation Note:* File-existence verified from `/Users/tomcounsell/src/ai/.worktrees/reflections-docs-cascade-cleanup` as of baseline commit `8a860f08`. The pre-computed dispositions remove all ambiguity — the builder does NOT need to re-run the triage rule per row.

### Flow

Not applicable — documentation edits with no user journey.

### Technical Approach

- Each file edit is mechanical: one or a few targeted replacements per the mapping above, preserving surrounding context (tables, code fences, adjacent paragraphs).
- No new sections created; no cross-doc links added or removed unless an entire section is deleted (e.g., "Cleanup Reflection (Runner)" in `popoto-index-hygiene.md`) or explicitly added (e.g., the one-sentence note below the SWOT table).
- After all edits, re-run the acceptance-criteria `grep` from the issue body and verify the only hits outside `docs/plans/completed/` are in explicitly historical contexts (annotated rows in `valor-name-references.md` using the standard `(removed service)` annotation).
- For `valor-name-references.md`: dispositions are pre-computed in the per-file edit summary above. The builder does NOT apply the triage rule case-by-case — the per-row decisions (88=DELETE, 90=ANNOTATE, 92=DELETE, 93=ANNOTATE, 100=DELETE, 111=ANNOTATE) are final. The annotation text is `(removed service)` — exact string, no variation.
- **Verification on this branch:** after edits, the worktree's `git diff main -- docs/` file list must be exactly the six files listed in the issue. The plan file itself commits to main directly (per /do-plan Phase 2.5) — it is NOT part of the session branch's diff.

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
- **Don't cascade into `docs/features/documentation-audit.md`, `session-lifecycle.md`, `pm-dev-session-architecture.md`, `sustainable-self-healing.md`, `unified-analytics.md`, `bridge-resilience.md`, `session-lifecycle-diagnostics.md`.** Those also contain monolith references (surfaced by the broader grep in the Recon Summary), but the issue body constrains scope to the six files it lists. **(Concern resolution)** Per-file disposition for the seven deferred files: **accepted as tangential history, no follow-up issue tracked.** Each of these files mentions the monolith in an incidental, historical, or contextual way (e.g., `documentation-audit.md` describes what the audit originally scanned; `session-lifecycle.md` references the runner as one example in a pattern discussion). None of them claim the monolith is live in a way a reader would treat as current architecture — they are tangential context, not live-doc errors. This is an explicit, intentional scope boundary, not forgotten work. If a future review disagrees with this call, file a follow-up issue citing the specific misleading claim; do NOT bundle it into this PR. The acceptance-criteria `grep` in the PR description will call out which file each remaining hit is in, so reviewers have full visibility into the deferral.
- **Don't reshape `popoto-index-hygiene.md`.** The Three Cleanup Paths table collapses to two rows — that's the only structural change. Resist refactoring the whole doc.
- **Don't edit the plan PR to close the tracking issue.** `/do-plan` phase 2.5 is explicit: only the implementation PR (from `/do-build`) uses `Closes #1032`. The plan commits to main and does not close #1032.

## Risks

### Risk 1: Editorial over-reach in `valor-name-references.md`
**Impact:** Aggressive row-deletion turns a historical inventory into an incomplete grid, losing information that future migrations will need.
**Mitigation:** Per-row dispositions are pre-computed in the Solution's per-file edit summary (88=DELETE, 90=ANNOTATE, 92=DELETE, 93=ANNOTATE, 100=DELETE, 111=ANNOTATE). Annotated rows use the exact string `(removed service)` — no builder discretion. DELETE decisions are backed by file-existence checks (verified during plan revision): rows 88, 92, 100 cite files that no longer exist; rows 90, 93, 111 cite scripts that exist and still contain `com.valor.reflections` strings.

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

- [ ] Edit `docs/features/popoto-index-hygiene.md` per Solution (remove Cleanup Reflection (Runner) section; **fix line 31 "bridge-hosted" → "worker-embedded"**; **fix Three Cleanup Paths table row 2 Trigger "Bridge tick (daily)" → "Worker scheduler tick (daily)"**; collapse table to two rows; update Key Files table row).
- [ ] Edit `docs/features/bridge-self-healing.md` per Solution (fix line 11; **drop line-112 `reflections.log` bullet entirely**).
- [ ] Edit `docs/features/telegram-history.md` per Solution (fix ALL FOUR step-13 references at lines **35, 47, 54, 58**).
- [ ] Edit `docs/research/claude-code-feature-swot.md` per Solution (REMOVE line-414 Reflections row; add explanatory sentence below the table; rewrite the lines 417-429 code example and its "Tasks performed" commentary).
- [ ] Edit `docs/guides/claude-code-feature-swot.md` per Solution (same edits as the research copy; keep both in lockstep).
- [ ] Edit `docs/guides/valor-name-references.md` per Solution (apply pre-computed dispositions: DELETE rows 88, 92, 100; ANNOTATE rows 90, 93, 111 with exact string `(removed service)`).
- [ ] Verify via `diff` that the two `claude-code-feature-swot.md` copies stay in sync (only pre-existing ChatSession/PM session differences remain).
- [ ] Run the acceptance-criteria `grep` and confirm remaining hits are only the three annotated rows in `valor-name-references.md` (or in `docs/plans/completed/`). Capture the output in the PR description.

No new feature docs or index entries are needed — every change is to existing pages. `/do-docs` is effectively this PR's build step.

## Success Criteria

- [ ] `popoto-index-hygiene.md`, `bridge-self-healing.md`, `telegram-history.md`, `valor-name-references.md`, and both `claude-code-feature-swot.md` variants no longer describe the monolith as live. (Issue acceptance criterion 1.) Specifically: `popoto-index-hygiene.md` no longer says "bridge-hosted" or "Bridge tick" (scheduler is worker-embedded).
- [ ] Cited step numbers are replaced with callable names (or removed if no longer applicable). (Issue acceptance criterion 2. Covers `telegram-history.md:35, 47, 54, 58` — all four, not just the three originally in the recon.)
- [ ] `grep -rn "scripts/reflections.py\|ReflectionRunner\|com.valor.reflections" docs/features docs/guides docs/research` returns only references inside explicitly historical contexts: (a) `docs/plans/completed/*`, (b) the three annotated `(removed service)` rows in `valor-name-references.md` (90, 93, 111), and (c) the seven deferred out-of-scope files explicitly called out in Rabbit Holes (documentation-audit.md, session-lifecycle.md, pm-dev-session-architecture.md, sustainable-self-healing.md, unified-analytics.md, bridge-resilience.md, session-lifecycle-diagnostics.md). (Issue acceptance criterion 3.)
- [ ] Doc table of contents / README indexes still match what each doc now describes. (Issue acceptance criterion 4. Verified by spot-checking cross-references from `docs/features/README.md` and each edited file's own section structure.)
- [ ] The two `claude-code-feature-swot.md` copies remain semantically identical aside from the pre-existing ChatSession/PM session terminology delta.
- [ ] Lint/format pass (`python -m ruff format .` — no-op on markdown, but runs cleanly).
- [ ] PR description explicitly states scope boundary ("touches only the six files listed in #1032; non-overlapping with #1031 and #1134") AND enumerates the deferred seven files with their disposition ("accepted as tangential history, no follow-up issue tracked").

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
- **Informed By**: Recon Summary (confirmed file:line references); Solution section's per-file edit summary with pre-computed dispositions
- **Assigned To**: docs-cascade-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Edit `docs/features/popoto-index-hygiene.md`:
    - Remove the "Cleanup Reflection (Runner)" section (today at lines 33-35); rename "Cleanup Reflection (Scheduler)" → "Cleanup Reflection".
    - **(Blocker)** Line 31 as it stands today: replace "`ReflectionScheduler` (bridge-hosted) dispatches this daily when the bridge is running." with "`ReflectionScheduler` (worker-embedded in `python -m worker`) dispatches this daily while the worker process runs."
    - **(Blocker)** In the "Three Cleanup Paths" table (today line 51), update the row 2 Trigger column from "Bridge tick (daily)" to "Worker scheduler tick (daily)".
    - Update the Key Files table to remove the `scripts/reflections.py` row (today line 68-ish).
- Edit `docs/features/bridge-self-healing.md`:
    - Line 11: remove `scripts/reflections.py` from the defensive-pattern list.
    - **(Concern)** Line 112: REMOVE the `reflections.log — configured in scripts/reflections.py` bullet entirely. Do NOT try to repoint it to `reflections/` — no module in that package writes `reflections.log`. Do NOT move it to `scripts/sdlc_reflection.py` either (that's a parallel path and introduces its own confusion). Dropping the bullet is the chosen outcome; `reflections_error.log` (line 115 in the Shell-rotated bucket) already provides the reflection-output reference readers would look for.
- Edit `docs/features/telegram-history.md`:
    - **(Concern)** Edit ALL FOUR lines — 35, 47, 54, AND 58. Lines 35/47/54 each have the phrasing "TTL: 90 days (cleaned by reflections step 13)" → replace with "TTL: 90 days (cleaned by the `redis-ttl-cleanup` reflection)". Line 58 has "Redis models: 90-day TTL, cleaned by reflections `step_redis_cleanup()` (step 13)" → replace with "Redis models: 90-day TTL, cleaned by the `redis-ttl-cleanup` reflection (`reflections.maintenance.run_redis_ttl_cleanup`)". Before committing, run `grep -n "step 13\|step_redis_cleanup" docs/features/telegram-history.md` and confirm zero hits.
- Edit `docs/research/claude-code-feature-swot.md`:
    - **(NIT)** Line 414: REMOVE the Reflections row from the launchd "Current schedules" table (not rewrite — rewriting would break the table's Plist/Script columns).
    - **(NIT)** Immediately after the table, add the sentence: "Reflections no longer run via launchd; the scheduler is embedded in the worker (`python -m worker`). See [Reflections](../features/reflections.md) for details."
    - Lines 417-429: replace the `python scripts/reflections.py --dry-run` code example and numbered "Tasks performed" list with a one-paragraph description of the registry-driven flow (see Solution for the exact paragraph template).
- Edit `docs/guides/claude-code-feature-swot.md`: apply the same three edits as the research copy above. Path for the cross-link is `../features/reflections.md` (same relative path from both `docs/guides/` and `docs/research/`). After edits, run `diff docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md` and verify only the pre-existing ChatSession/PM session differences remain.
- Edit `docs/guides/valor-name-references.md` using pre-computed dispositions (do NOT re-run the triage rule):
    - Row at line 88 (`com.valor.reflections.plist`): **DELETE** (plist file does not exist).
    - Row at line 90 (`scripts/update/service.py`): **ANNOTATE** — append ` (removed service)` to the row's reflections reference cell.
    - Row at line 92 (`scripts/install_reflections.sh`): **DELETE** (script does not exist).
    - Row at line 93 (`scripts/remote-update.sh`): **ANNOTATE** — append ` (removed service)`.
    - Row at line 100 (`data/valor.session → scripts/reflections.py`): **DELETE** (referenced script gone).
    - Row at line 111 (`scripts/update/run.py`): **ANNOTATE** — append ` (removed service)`.
    - Standard annotation string: `(removed service)` — exact, no variation.
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

Critique run: 2026-04-23 (war room: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User).
Verdict: **NEEDS REVISION** — 1 blocker, 5 concerns, 3 nits.
Revision applied: 2026-04-23 (this commit). All findings embedded as Implementation Notes in the sections below. See `revision_applied: true` in the frontmatter.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic (+ User) | `popoto-index-hygiene.md:31` claims "`ReflectionScheduler` (bridge-hosted)" and Three Cleanup Paths table row 2 says "Bridge tick (daily)". Scheduler actually runs in worker (`worker/__main__.py:351-355`), not bridge. Plan missed these edits. | Solution → per-file edit summary for `popoto-index-hygiene.md` (items 2 and 3); Step 1 task bullets with line-31 and row-2 fixes; Success Criteria row 1 updated. | `grep -rn 'reflection_scheduler\|ReflectionScheduler' bridge/` returns zero matches. `docs/features/reflections.md:6` uses "runs as an asyncio task inside the standalone worker process" — match that phrasing. |
| CONCERN | Skeptic | `telegram-history.md` has FOUR step-13 references (lines 35, 47, 54, 58), not three. Plan originally listed only 47/54/58 — line 35 was missed. | Freshness Check re-stated with all four lines; Solution → per-file edit summary for `telegram-history.md`; Step 1 task; Documentation checklist; Success Criteria row 2. | `grep -n "step 13" docs/features/telegram-history.md` returns lines 35, 47, 54, 58. Lines 35/47/54 have "step 13" only; line 58 also has `step_redis_cleanup()`. Verification row 5 grep (`step_redis_cleanup\|step 13`) catches all four — expected exit 1 post-edit. |
| CONCERN | Skeptic (+ Simplifier) | `bridge-self-healing.md:112` plan rationale was wrong. `scripts/sdlc_reflection.py` DOES write `reflections.log` via `open(...,"a")`; real problem is it's NOT `RotatingFileHandler`-managed (wrong bucket). | Solution → per-file edit summary for `bridge-self-healing.md` (pick option b — drop bullet entirely); Step 1 task with explicit "do NOT repoint, do NOT move to sdlc_reflection.py" guidance. | `scripts/sdlc_reflection.py:25` defines `LOG_FILE`; line 49 uses `open(LOG_FILE, "a")`. `grep -rn "RotatingFileHandler" reflections/ agent/reflection_scheduler.py scripts/sdlc_reflection.py` returns zero. Line 115's existing `reflections_error.log` entry in the Shell-rotated bucket covers the reader's need. |
| CONCERN | Operator | `valor-name-references.md:88` (`com.valor.reflections.plist`) references a file that does NOT exist. Plan should DELETE this row, not annotate. | Solution → per-file edit summary for `valor-name-references.md` with pre-computed dispositions; Step 1 task with per-row DELETE/ANNOTATE list; Risk 1 mitigation updated. | `find . -name "com.valor.reflections.plist"` returns nothing; `ls ~/Library/LaunchAgents/com.valor.reflections.plist` returns "no such file". |
| CONCERN | Operator | `valor-name-references.md:92` (`scripts/install_reflections.sh`) references a file that does NOT exist. Plan should DELETE row 92 (and pre-compute ALL row dispositions, not leave case-by-case to builder). | Solution → per-file edit summary for `valor-name-references.md` with full pre-computed dispositions (88=DELETE, 90=ANNOTATE, 92=DELETE, 93=ANNOTATE, 100=DELETE, 111=ANNOTATE); Step 1 task list; Technical Approach note that triage rule is NOT re-run by builder. | `ls scripts/install_reflections.sh` → no such file. Other scripts (`update/service.py`, `remote-update.sh`, `update/run.py`) all exist and still contain `com.valor.reflections` strings. |
| CONCERN | Adversary | Seven deferred out-of-scope files have no follow-up tracking citation. Reviewer can't tell if deferral is intentional or forgotten. | Rabbit Hole #3 expanded with explicit disposition statement ("accepted as tangential history, no follow-up issue tracked") and justification; Success Criteria row 7 now requires PR description to enumerate the deferred files. | `grep -rn "scripts/reflections.py\|ReflectionRunner\|com.valor.reflections" docs/features docs/guides docs/research` minus the six in-scope files surfaces 7 files (documentation-audit.md, session-lifecycle.md, pm-dev-session-architecture.md, sustainable-self-healing.md, unified-analytics.md, bridge-resilience.md, session-lifecycle-diagnostics.md). All are incidental/contextual references, not live-doc errors. |
| NIT | Archaeologist | Replacement mapping doesn't cite `docs/features/reflections.md` as the canonical live doc. | Solution → Key Elements: new bullet points to `docs/features/reflections.md` as the canonical target for multi-line replacements. | `docs/features/reflections.md:1-8` is the current authoritative description of the reflections architecture. |
| NIT | User | `claude-code-feature-swot.md:414` left as EITHER/OR (remove OR rewrite). Table is launchd-focused; worker-embedded scheduler is neither plist nor script — rewrite breaks columns. | Solution → per-file edit summary pre-decides REMOVE + one-sentence note below table; Step 1 task reflects this. | Table columns are Plist/Script/Schedule — all launchd-specific. A standalone note cleanly communicates the architecture change without breaking table structure. |
| NIT | User | "(removed service) or similar" leaves annotation text to builder → risks inconsistency. | Solution → per-file edit summary specifies exact annotation string `(removed service)`; Step 1 task reiterates "exact, no variation"; Risk 1 mitigation updated. | Consistent annotation text across the three annotated rows (90, 93, 111) keeps the table readable as a uniform historical record. |
