---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-05-06
revised: 2026-05-08
tracking: https://github.com/tomcounsell/ai/issues/1306
last_comment_id:
---

# Finish pm-briefings cutover: rename package, strip shim, propagate vault changes

## Problem

PR #1295 inlined helpers and deleted `reflections/daily_report.py` + `reflections.auditing.run_log_review`. Commit `bee105d5` then cleaned up vault dead entries and the UI single-source plumbing. The remaining gap is the Python package rename, the vault `callable:` field, the `_load_slots()` shim, and a doc-rewrite cluster.

**Current behavior (after `bee105d5`):**
- Package directory still named `reflections/pm_audio_briefing/`; the new feature name `pm-briefings` is the registry name in vault and the canonical UI prefix, but the import path is unchanged.
- `_load_slots()` shim at `reflections/pm_audio_briefing/__init__.py:125` synthesizes `pm_briefing.slots` from legacy `pm_briefing.angles + pm_briefing.schedule` config. No project carries either legacy shape today (verified: 0 of 13 projects in `~/Desktop/Valor/projects.json`), so the shim is dead weight.
- Vault `~/Desktop/Valor/reflections.yaml` line 327 still has `callable: "reflections.pm_audio_briefing.run"` (the registry `name:` field is already `pm-briefings`).
- `docs/features/pm-audio-briefing.md` still exists as a parallel-run document; `docs/features/pm-briefings.md` and `docs/features/reflections.md` both contain transition-tense narrative and stale `pm_audio_briefing` import path references (6+ live references in `reflections.md` alone, including a registry table row).

**Desired outcome:** Single coherent post-cutover state. Package renamed, shim deleted, vault `callable:` field updated, legacy doc deleted, transition narrative gone, and a re-export shim guards the iCloud/code propagation race during the deploy window.

## Freshness Check

**Baseline commit:** `bee105d5` (main at revision time, 2026-05-08)
**Issue filed at:** 2026-05-06T10:19:48Z
**Revision date:** 2026-05-08
**Disposition:** Updated to current state — `bee105d5` already removed dead vault entries (`daily-log-review`, `daily-report-and-notify`, three other unrelated stale callables) and trimmed `_PREFIX_EXPANDED_REFLECTIONS` in `ui/data/reflections.py` to `("pm-briefings",)`. Tasks 3 and 5 in the original plan are mostly done; only the comment-block cleanup in `ui/data/reflections.py` (lines 66-76) and the vault `callable:` field remain.

**File:line references re-verified (2026-05-08):**
- `reflections/pm_audio_briefing/__init__.py:125` — `_load_slots()` shim — still holds at line 125; called from line 415 in the dispatcher's `run()`.
- `~/Desktop/Valor/reflections.yaml` line 321 — `name: pm-briefings`; line 327 — `callable: "reflections.pm_audio_briefing.run"`. The two dead callable entries (`daily-log-review`, `daily-report-and-notify`) are GONE — already removed by `bee105d5`.
- 7-module `reflections/pm_audio_briefing/` package — directory exists exactly as the issue describes (`__init__.py`, `builder.py`, `collector.py`, `daily_log.py`, `delivery.py`, `log_audit.py`, `morning.py`).
- `ui/data/reflections.py:77` — `_PREFIX_EXPANDED_REFLECTIONS: tuple[str, ...] = ("pm-briefings",)` — single-source already done. Comment block at lines 66-76 still mentions the legacy prefix in commentary; needs final scrub.

**Cited sibling issues/PRs re-checked:**
- #1292 — closed, completed by #1295.
- #1295 — merged 2026-05-06.
- `bee105d5` (2026-05-08) — non-PR commit on main: dead-callable purge + UI single-source.
- #1281 — original consolidation, still merged.
- #1276 — tracking issue for `daily-reflections-unification.md`; still open.

**Commits on main since issue was filed:** `bee105d5` (2026-05-08) is the relevant one; folded into this plan revision.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/daily-reflections-unification.md` (status: Ready, tracking #1276). This plan finishes that one and moves it to `completed/`.

**Notes:** Q1 (orphan-log step) is mooted by critique — dropped from this revision. Q2 (deploy ordering race) resolved by shipping a re-export shim (see Solution / Risk 5). Q3 (no-slots warning surface) is non-blocking — worker log warning sufficient.

## Prior Art

- **PR #1295**: `feat(#1292): pm-briefings cutover — inline helpers, retire legacy modules` (merged 2026-05-06). Deleted `reflections/daily_report.py` and `reflections.auditing.run_log_review`. Inlined helpers from those modules into the new dispatcher. Left the package rename, shim removal, and vault propagation undone — exactly the gap this issue closes.
- **PR #1281**: Original consolidation; settled the user-facing name as `pm-briefings`. Not re-litigating that decision.
- **Issue #1276**: Original tracking issue for `daily-reflections-unification.md`. Closed implicitly when PR #1295 merged but the plan doc remained in the active dir.
- **No prior failed attempts**: this is the second-half cleanup of an in-flight cutover, not a re-do of a failed approach.

## Research

No relevant external findings — proceeding with codebase context. This is purely internal cleanup; no library, API, or ecosystem question is involved.

## Data Flow

The reflection scheduler reads `config/reflections.yaml` (a symlink to `~/Desktop/Valor/reflections.yaml`) and dispatches each registry entry by its `callable:` dotted path. After this work:

1. **Scheduler tick** (`agent.reflection_scheduler`) reads vault yaml.
2. **Registry entry** `pm-briefings` → callable `reflections.pm_briefings.run` (renamed from `reflections.pm_audio_briefing.run`).
3. **Dispatcher** (`reflections/pm_briefings/__init__.py::run`) iterates projects with `pm_briefing.enabled = True`, reads `pm_briefing.slots` directly (no shim), fans out to slot modules.
4. **Output**: voice + text Telegram delivery per slot.

Removed paths: `reflections.auditing.run_log_review` (already deleted by #1295) and `reflections.daily_report.run` (already deleted by #1295) no longer dispatched, because their registry entries are gone from the vault.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: Python import path changes from `reflections.pm_audio_briefing` → `reflections.pm_briefings`. Affects `~30` import sites across `reflections/`, `tests/unit/reflections/`, `tests/integration/reflections/`, and `ui/data/reflections.py`.
- **Coupling**: Decreases. The `_load_slots()` shim coupled the new dispatcher to the legacy `pm_briefing.angles + pm_briefing.schedule` config shape. Removing it leaves a single canonical config path.
- **Data ownership**: Unchanged.
- **Reversibility**: Low cost to revert (single PR + vault edit), but unnecessary — the shim's coupled config shape is unused.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (after vault edit, to confirm slots config strategy is acceptable)
- Review rounds: 1 (CR pass on import-rename mechanical correctness)

This is a mechanical rename + a single-function deletion + a per-machine propagation step. The communication overhead is mainly the vault edit confirmation.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Vault yaml present | `test -f ~/Desktop/Valor/reflections.yaml && echo OK` | Required for registry edit |
| Repo on main, clean | `cd /Users/valorengels/src/ai && git status --porcelain && echo OK` | Avoid mixing in flight work |
| `gh` CLI auth ok | `gh auth status` | Issue / PR operations |

## Solution

### Key Elements

- **Package rename**: `reflections/pm_audio_briefing/` → `reflections/pm_briefings/` via `git mv` so history is preserved.
- **Re-export shim** (deploy-race guard): ship a thin `reflections/pm_audio_briefing.py` module (NOT a package — replaces the directory) that does `from reflections.pm_briefings import *` and re-exports `run`. This makes the vault `callable:` edit independent of code propagation order: regardless of whether the vault edit or the code rename lands first on a given machine, `reflections.pm_audio_briefing.run` and `reflections.pm_briefings.run` both resolve. The shim is removed in a follow-up PR after every machine has pulled this change AND the vault edit (≥1 day window).
- **Shim removal** (`_load_slots()`): Delete `_load_slots()` and call sites that pre-process; `run()` reads `(project.get("pm_briefing") or {}).get("slots") or []` directly. If a project has `pm_briefing.enabled=true` but no `slots`, log a warning and skip — no synthesis.
- **Test cleanup**: Delete `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py`. Update imports in remaining test files to `reflections.pm_briefings`.
- **UI cleanup**: `ui/data/reflections.py` — comment block at lines 66-76 still references the legacy prefix in narrative; rewrite it to describe only the post-cutover state. (`_PREFIX_EXPANDED_REFLECTIONS` and the parent-mapping deletion are already done by `bee105d5`.)
- **Doc cleanup**: Delete `docs/features/pm-audio-briefing.md`. Rewrite `docs/features/pm-briefings.md` and overhaul `docs/features/reflections.md` to describe only the post-cutover state — no "previously", "transitioning", "kept for backward compatibility", or `pm_audio_briefing` import-path mentions. Update `docs/features/README.md` index. Move `docs/plans/daily-reflections-unification.md` to `docs/plans/completed/` with a one-line completion note pointing at this issue + PR #1295.
- **Vault edit**: `~/Desktop/Valor/reflections.yaml` — update line 327 `callable: "reflections.pm_audio_briefing.run"` → `callable: "reflections.pm_briefings.run"`. (Two dead-callable entries already removed by `bee105d5`; registry `name:` already renamed.)
- **Deploy**: run `/update` on every active bridge machine listed in `projects.json`. Re-export shim removes the iCloud-vs-code race; no ordering required.

### Flow

Current (post-`bee105d5`) state → `git mv` package + import sweep → add re-export shim `reflections/pm_audio_briefing.py` → shim removal (`_load_slots`) → test/UI-comment/doc cleanup → vault `callable:` edit (dev machine) → `/update` deploy on every other bridge machine → post-cutover steady state. Re-export shim survives one cycle, then is deleted in a follow-up PR.

### Technical Approach

- **Mechanical sweep**: `git mv reflections/pm_audio_briefing reflections/pm_briefings`, then a `grep -rl pm_audio_briefing` enumeration followed by per-file `sed -i ''` replacing `pm_audio_briefing` → `pm_briefings` across `reflections/`, `tests/`, `ui/data/reflections.py`, `agent/`, `scripts/`, and any docstrings. The string `pm-audio-briefing` (hyphenated) appears mainly in archived plans (`docs/plans/critiques/`, `docs/plans/daily-log-overhaul.md`, completed plan dir) and must NOT be rewritten there — those are historical artifacts. Verify with: `grep -r "pm_audio_briefing\|pm-audio-briefing" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs/plans/completed --exclude-dir=docs/plans/critiques --exclude=docs/plans/daily-log-overhaul.md --exclude=docs/plans/pm-briefings-cutover-finish.md` returns only the re-export shim file.
- **Re-export shim**: create `reflections/pm_audio_briefing.py` (single module, after the `git mv` removed the directory). Body:
  ```python
  """Compat re-export shim for issue #1306 deploy window. Remove after every
  bridge machine has pulled this commit AND the vault has propagated.
  Tracking: follow-up issue created when this PR merges."""
  from reflections.pm_briefings import *  # noqa: F401,F403
  from reflections.pm_briefings import run  # noqa: F401
  ```
- **`_load_slots` shim removal**: Replace `slots = _load_slots(project)` (line 415) with `slots = (project.get("pm_briefing") or {}).get("slots") or []`; if empty, log `"pm-briefings: project %s has no slots configured; skipping"` and continue. Delete `_load_slots()` body. Verify `run()` still passes type checks on the simplified path.
- **UI comment cleanup**: in `ui/data/reflections.py`, the tuple at line 77 is already `("pm-briefings",)` (per `bee105d5`). Rewrite the comment block at lines 66-76 so it describes the post-cutover state without invoking the legacy prefix in commentary.
- **Vault yaml**: edit `~/Desktop/Valor/reflections.yaml` line 327 directly. Change `callable: "reflections.pm_audio_briefing.run"` → `callable: "reflections.pm_briefings.run"`. Validate by running `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` and the existing `bridge.config_validation` reflections check.
- **Doc rewrite (full scope)**: All references must reflect the post-cutover state.
  - `git rm docs/features/pm-audio-briefing.md` (whole file gone).
  - Rewrite `docs/features/pm-briefings.md`:
    - Replace `reflections.pm_audio_briefing.run` (line 4, line 49) with `reflections.pm_briefings.run`.
    - Drop the table row at line 15 referencing the legacy registry name.
    - Update the slot-callable table (lines 42-44) to `reflections.pm_briefings.{morning,daily_log,log_audit}`.
    - Drop `_load_slots()` mention (line 121) — shim deleted.
    - Drop the `pm-audio-briefing` legacy prefix mention (lines 146, 151).
    - Drop the "see pm-audio-briefing.md" cross-link (line 201).
  - Rewrite `docs/features/reflections.md` for ALL six+ live references:
    - Lines 93, 113 — drop the "Retired" rows for `daily-log-review` and `daily-report-and-notify` (already gone from vault); reference is now historical-only and out of date; either remove rows or move into a "Removed reflections" appendix with a one-line note.
    - Line 114 — registry table row: rename to `pm-briefings` callable `reflections.pm_briefings.run`. Drop transition-tense text ("registry-renamed from", "package name preserved", "auto-migrated").
    - Line 146 — `reflections/pm_audio_briefing/{daily_log,log_audit}.py` → `reflections/pm_briefings/{daily_log,log_audit}.py`.
    - Line 168 — `pm_audio_briefing.delivery` → `pm_briefings.delivery`.
    - Line 267 — module reference table row: `reflections.pm_audio_briefing` → `reflections.pm_briefings`. Drop "registry entry" qualifier and slot-driven transition framing.
    - Line 592 — directory tree row: `reflections/pm_audio_briefing/` → `reflections/pm_briefings/`. Drop transition framing.
  - `docs/features/README.md`:
    - Line 96 — delete the `[PM Audio Briefing](pm-audio-briefing.md)` row entirely.
    - Line 97 — rewrite `[PM Briefings (slot-driven)](pm-briefings.md)` to drop the parenthetical transition narrative; describe only the steady-state.
- **Plan move**: `git mv docs/plans/daily-reflections-unification.md docs/plans/completed/` and append a one-line completion note: `> Completed by issue #1306 / PR #<this-PR>. Cutover finished 2026-05-08.`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Touched files (`reflections/pm_briefings/__init__.py`, `ui/data/reflections.py`) have no `except Exception: pass` blocks introduced or removed by this work; only the `_load_slots()` deletion changes behavior, and it's pure deletion.
- [ ] No new exception handlers in scope.

### Empty/Invalid Input Handling
- [ ] Add a unit test asserting that a project with `pm_briefing.enabled=true` but no `slots` key triggers the "no slots configured; skipping" warning and the dispatcher returns cleanly (no crash, no silent loop). This replaces the deleted legacy-shim test.
- [ ] Add a unit test asserting that `pm_briefing.slots = []` (explicit empty list) is also handled as "no slots".
- [ ] No agent output processing involved — Telegram delivery path is untouched.

### Error State Rendering
- [ ] Vault yaml syntax validity test: load `config/reflections.yaml` via `yaml.safe_load`; assert no entries reference `reflections.auditing.run_log_review` or `reflections.daily_report.run`.
- [ ] Worker scheduler tick test: after vault edit, `python -m worker` for one tick produces no `ImportError` in `logs/worker.log` (verified manually post-deploy).

## Test Impact

- [ ] `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py` — DELETE: scenario no longer exists once the shim is gone.
- [ ] `tests/unit/reflections/test_pm_audio_briefing_builder.py` — UPDATE: rename file to `test_pm_briefings_builder.py`; update `from reflections.pm_audio_briefing import builder` → `from reflections.pm_briefings import builder`.
- [ ] `tests/unit/reflections/test_pm_audio_briefing_collector.py` — UPDATE: same rename + import update.
- [ ] `tests/unit/reflections/test_pm_audio_briefing_delivery.py` — UPDATE: same rename + import update.
- [ ] `tests/unit/reflections/test_pm_audio_briefing_init.py` — UPDATE: rename to `test_pm_briefings_init.py` + import update.
- [ ] `tests/unit/reflections/test_pm_briefings_machine_gate.py` — UPDATE: change `from reflections import pm_audio_briefing as briefing` → `from reflections import pm_briefings as briefing`.
- [ ] `tests/unit/reflections/test_pm_briefings_skip_when_empty.py` — UPDATE: same import change.
- [ ] `tests/unit/reflections/test_pm_briefings_slot_match.py` — UPDATE: same import change.
- [ ] `tests/unit/reflections/test_daily_log_renderer.py` — UPDATE: change `import reflections.pm_audio_briefing.daily_log as dr` → `import reflections.pm_briefings.daily_log as dr`. Drop the comment about the inlining transition.
- [ ] `tests/unit/reflections/test_daily_log_audio_guard.py` — UPDATE: change `from reflections.pm_audio_briefing.builder import …` → `from reflections.pm_briefings.builder import …`. Rename test `test_regexes_importable_from_pm_audio_briefing` → `test_regexes_importable_from_pm_briefings`.
- [ ] `tests/unit/reflections/test_log_audit_sentry.py` — UPDATE: import sweep to `reflections.pm_briefings.log_audit`.
- [ ] `tests/unit/reflections/test_daily_log_aggregator.py` — UPDATE: import sweep to `reflections.pm_briefings.daily_log`.
- [ ] `tests/unit/test_ui_reflections_data.py` — UPDATE: any `pm-audio-briefing` literal in test fixtures swapped for `pm-briefings` per ui/data/reflections.py current state; assertions updated.
- [ ] `tests/unit/test_reflections_package.py` — UPDATE: package-discovery assertions now resolve `reflections.pm_briefings` only; legacy module name drops out.
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: any registry-entry fixture using `pm_audio_briefing` callable string updated.
- [ ] `tests/integration/reflections/test_pm_audio_briefing_e2e.py` — UPDATE: rename file to `test_pm_briefings_e2e.py`; update all `reflections.pm_audio_briefing.*` imports → `reflections.pm_briefings.*`. Pre-existing test name like `test_*_pm_audio_briefing_*` renamed to `pm_briefings`.
- [ ] `tests/integration/reflections/test_pm_briefings_dispatch.py` — UPDATE: import sweep to `reflections.pm_briefings`.
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` — REPLACE the deleted legacy-config-migration test with a test asserting the no-slots warning + clean return path.
- [ ] **NEW** `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` — assert that `reflections.pm_audio_briefing.run is reflections.pm_briefings.run` so the deploy-window shim doesn't silently regress.

## Rabbit Holes

- **Adding pm_briefing.slots to existing projects**: out of scope. Verified no project currently has briefings configured at all; opting projects in is a separate decision.
- **Renaming further**: the registry name `pm-briefings` is settled. Don't relitigate.
- **Auditing other reflections** for stale callables: tempting but separate scope. Stick to the three vault entries this issue named.
- **Building generic vault-validation tooling**: tempting given that the `daily-log-review` ImportError went unnoticed for a while, but a separate issue (out of scope here).

## Risks

### Risk 1: Vault yaml typo bricks scheduler
**Impact:** Worker scheduler crashes on yaml load; all reflections stop firing on every machine that pulls the bad vault.
**Mitigation:** Validate with `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` before committing the vault edit. The vault is iCloud-synced; do the edit on the dev machine, validate locally with one scheduler tick, then propagate via /update.

### Risk 2: Import sweep misses a string
**Impact:** Production code still references `reflections.pm_audio_briefing` after rename; runtime ImportError on first scheduler tick.
**Mitigation:** Final acceptance check `grep -r "pm_audio_briefing\|pm-audio-briefing" .` returns zero hits in tracked files (excluding `docs/plans/completed/`). The verification table in this plan includes this grep as a hard gate.

### Risk 3: A project we forgot configures pm_briefing on a non-dev machine
**Impact:** Shim removal breaks briefings on that machine until operator adds explicit slots.
**Mitigation:** Verified zero projects in the iCloud-synced `projects.json` carry `pm_briefing.enabled=true` today, and `projects.json` is shared across machines via iCloud. Even if a divergent local copy exists, the new "no slots configured; skipping" warning is non-fatal — scheduler keeps ticking.

### Risk 4: Deploy-window iCloud-vs-code race ImportError
**Impact:** Vault `~/Desktop/Valor/reflections.yaml` is iCloud-synced; once edited on the dev machine it propagates to every other bridge machine within seconds — possibly before the operator runs `/update` to pull the code rename. During that window, vault says `callable: reflections.pm_briefings.run` but worker code only has `reflections.pm_audio_briefing`. Each scheduler tick logs an ImportError until `/update` lands.
**Mitigation:** Ship a re-export shim — a single-file module `reflections/pm_audio_briefing.py` that re-exports `run` (and everything else) from `reflections.pm_briefings`. After this change merges, BOTH import paths resolve regardless of vault state. The shim is removed in a follow-up PR (≥1 day after this PR merges and every machine has run `/update` at least once). A unit test (`test_pm_audio_briefing_reexport_shim.py`) asserts identity of the two `run` callables so the shim cannot silently rot.

## Race Conditions

No race conditions identified — all changes are static (file rename, code deletion, config edit). The scheduler reads vault yaml at startup or on file change; there is no concurrent write path.

## No-Gos (Out of Scope)

- Renaming the feature beyond `pm-briefings`.
- Changes to slot semantics, briefing content, voice/text rendering.
- Adding `pm_briefing.slots` to any project that doesn't already opt in.
- Generic vault-validation tooling.
- Refactoring `ui/data/reflections.py` beyond removing the dual-name plumbing.

## Update System

The version-controlled update entry points are `scripts/remote-update.sh` (per-machine driver) and `scripts/update/run.py` (the Python pipeline it invokes). There is no `.claude/skills/update/SKILL.md` in this repo — the prior plan revision targeted a non-existent file.

- **Vault propagation**: vault is iCloud-synced; once the dev-machine `~/Desktop/Valor/reflections.yaml` edit propagates, every other machine has it within seconds. The existing `env_sync.sync_reflections_yaml()` step (Step 1.66 in `run.py`) already ensures the symlink resolves, and the worker restart at the end of `run.py` re-reads the vault. **No script change is required** for the yaml itself.
- **Race coverage**: handled in code, not in update — the `reflections/pm_audio_briefing.py` re-export shim ensures the vault edit and the code rename can land in either order on each machine.
- **Orphan-log cleanup**: dropped from this plan (Q1 mooted by critique).
- **Worker restart**: existing `/update` step already restarts the worker after pulling, which picks up the new package name. No change required.

## Agent Integration

No agent integration required — this is a bridge-internal / scheduler-internal cleanup. The `pm-briefings` reflection is invoked by the worker's reflection scheduler via dotted-path callable, not by the agent through MCP. The agent (PM session) is unaffected.

## Documentation

### Feature Documentation
- [ ] Delete `docs/features/pm-audio-briefing.md` (legacy-named, content superseded).
- [ ] Rewrite `docs/features/pm-briefings.md` to describe only the post-cutover state. Remove any "previously", "transitioning", "for backward compatibility" framing. Keep behavior reference (numbers-free audio, written follow-up, slot fan-out).
- [ ] Update `docs/features/reflections.md` to drop transition narrative; describe only the new registry shape.
- [ ] Update `docs/features/README.md` index if it links to `pm-audio-briefing.md`.

### External Documentation Site
- [ ] N/A — this repo's docs are in-tree, no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Update docstrings in `reflections/pm_briefings/__init__.py` and submodules to remove transition-tense comments.
- [ ] Drop the "kept for backward compatibility" comment block in `ui/data/reflections.py` that justified the dual listing.

### Plan archival
- [ ] `git mv docs/plans/daily-reflections-unification.md docs/plans/completed/` with a one-line completion note appended.

## Success Criteria

- [ ] `grep -r "pm_audio_briefing\|pm-audio-briefing" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs/plans/completed --exclude-dir=docs/plans/critiques --exclude=docs/plans/daily-log-overhaul.md --exclude=docs/plans/pm-briefings-cutover-finish.md` matches only `reflections/pm_audio_briefing.py` (the re-export shim).
- [ ] `reflections/pm_audio_briefing/` directory does not exist; `reflections/pm_briefings/` does, with all 7 modules and preserved git history.
- [ ] `reflections/pm_audio_briefing.py` (single file) exists and re-exports `run` from `reflections.pm_briefings`.
- [ ] `_load_slots()` not present in `reflections/pm_briefings/__init__.py`; `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py` deleted.
- [ ] `docs/features/pm-audio-briefing.md` deleted; `docs/features/pm-briefings.md`, `docs/features/reflections.md`, `docs/features/README.md` describe only the post-cutover state (no `pm_audio_briefing` import paths, no "previously", "transitioning", "backward compat" framing).
- [ ] `docs/plans/daily-reflections-unification.md` is in `docs/plans/completed/` with completion note.
- [ ] On the dev machine: `~/Desktop/Valor/reflections.yaml` `pm-briefings` entry has `callable: "reflections.pm_briefings.run"`.
- [ ] `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` exits 0.
- [ ] One worker scheduler tick post-deploy produces no `ImportError` in `logs/worker.log`.
- [ ] `/update` deploy completed on every active bridge machine listed in `projects.json`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] Follow-up issue filed for "remove `reflections/pm_audio_briefing.py` re-export shim" (≥1 day after merge once every machine has updated).

## Team Orchestration

### Team Members

- **Builder (cutover)**
  - Name: cutover-builder
  - Role: rename package, sweep imports, delete shim, clean UI/docs, edit vault yaml, archive plan
  - Agent Type: builder
  - Resume: true

- **Validator (cutover)**
  - Name: cutover-validator
  - Role: verify success criteria — grep clean, yaml valid, scheduler tick clean, test suite green
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder + validator (Tier 1 only — Small appetite, mechanical work).

## Step by Step Tasks

### 1. Rename package + sweep imports + add re-export shim
- **Task ID**: build-rename
- **Depends On**: none
- **Validates**: `tests/unit/reflections/`, `tests/integration/reflections/`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- `git mv reflections/pm_audio_briefing reflections/pm_briefings`.
- Sweep `pm_audio_briefing` → `pm_briefings` across `reflections/`, `tests/`, `ui/data/reflections.py`, `agent/`, `scripts/`, plus any docstrings. Use `grep -rl pm_audio_briefing` to enumerate, then per-file `sed`. **Do not** sweep `docs/plans/critiques/`, `docs/plans/daily-log-overhaul.md`, `docs/plans/completed/`, or this plan file — those are historical.
- Rename test files `test_pm_audio_briefing_*.py` → `test_pm_briefings_*.py` (unit + integration) via `git mv`. Update `tests/integration/reflections/test_pm_audio_briefing_e2e.py` → `test_pm_briefings_e2e.py`.
- Create `reflections/pm_audio_briefing.py` (single-file shim, NOT a package) re-exporting from `reflections.pm_briefings` (see Technical Approach).
- Add `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` asserting `reflections.pm_audio_briefing.run is reflections.pm_briefings.run`.
- Run `pytest tests/unit/reflections/ tests/integration/reflections/ -x -q` to confirm imports resolve.

### 2. Delete `_load_slots` shim and add no-slots warning
- **Task ID**: build-shim-removal
- **Depends On**: build-rename
- **Validates**: `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` (new), existing `test_pm_briefings_skip_when_empty.py`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `_load_slots()` function body in `reflections/pm_briefings/__init__.py`.
- Replace `slots = _load_slots(project)` with `slots = (project.get("pm_briefing") or {}).get("slots") or []`. If empty, log warning and skip the project.
- Delete `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py`.
- Add `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` covering enabled-without-slots warning path.

### 3. UI comment-block cleanup
- **Task ID**: build-ui-comment
- **Depends On**: build-rename
- **Validates**: `grep -n pm-audio-briefing ui/data/reflections.py` returns no matches.
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: true
- In `ui/data/reflections.py` lines 66-76, rewrite the comment block describing `_PREFIX_EXPANDED_REFLECTIONS` so it documents the post-cutover state without invoking the legacy prefix in narrative. (The tuple itself and the parent-mapping deletion are already done by `bee105d5`.)

### 4. Doc cleanup + plan archival
- **Task ID**: build-docs
- **Depends On**: build-rename
- **Validates**: `grep -rn "pm_audio_briefing\|pm-audio-briefing\|previously\|transitioning\|backward compat" docs/features/pm-briefings.md docs/features/reflections.md docs/features/README.md` returns no matches.
- **Assigned To**: cutover-builder
- **Agent Type**: documentarian
- **Parallel**: true
- `git rm docs/features/pm-audio-briefing.md`.
- Rewrite `docs/features/pm-briefings.md` per the Technical Approach line list (lines 4, 15, 42-44, 49, 121, 146, 151, 201).
- Rewrite `docs/features/reflections.md` per the Technical Approach line list (lines 93, 113, 114, 146, 168, 267, 592).
- Update `docs/features/README.md` lines 96-97 (delete legacy row; rewrite slot-driven row).
- `git mv docs/plans/daily-reflections-unification.md docs/plans/completed/` and append the one-line completion note.

### 5. Vault yaml edit (dev machine)
- **Task ID**: build-vault
- **Depends On**: build-shim-removal, build-rename
- **Validates**: `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `~/Desktop/Valor/reflections.yaml` line 327: change `callable: "reflections.pm_audio_briefing.run"` → `callable: "reflections.pm_briefings.run"`. (The dead-callable entries and the `name:` rename were already done by `bee105d5`.)
- Validate yaml parses cleanly via `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"`.
- The re-export shim from Task 1 ensures iCloud propagation to other machines is safe even before they run `/update`.

### 6. Validate cutover
- **Task ID**: validate-cutover
- **Depends On**: build-rename, build-shim-removal, build-ui-comment, build-docs, build-vault
- **Assigned To**: cutover-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "pm_audio_briefing\|pm-audio-briefing" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs/plans/completed --exclude-dir=docs/plans/critiques --exclude=docs/plans/daily-log-overhaul.md --exclude=docs/plans/pm-briefings-cutover-finish.md` — assert only the re-export shim file `reflections/pm_audio_briefing.py` matches.
- Run `pytest tests/unit/reflections/ tests/integration/reflections/ -q` — assert green.
- Run `python -m ruff check . && python -m ruff format --check .` — assert green.
- Validate `config/reflections.yaml` yaml.
- Run worker for one tick locally; tail `logs/worker.log` for ImportError.
- Confirm dashboard `/dashboard.json` shows `pm-briefings` entries (no `pm-audio-briefing` legacy alias).
- Report pass/fail.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-cutover
- **Assigned To**: cutover-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the Verification table commands.
- Confirm all Success Criteria checkboxes are met.
- Report final status to the PM session.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No legacy refs (excl. shim + historical) | `grep -r "pm_audio_briefing\|pm-audio-briefing" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=docs/plans/completed --exclude-dir=docs/plans/critiques --exclude=docs/plans/daily-log-overhaul.md --exclude=docs/plans/pm-briefings-cutover-finish.md \| grep -v '^./reflections/pm_audio_briefing.py:'` | exit code 1 |
| New package present | `test -d reflections/pm_briefings && test -f reflections/pm_briefings/__init__.py` | exit code 0 |
| Old package gone (directory) | `test ! -d reflections/pm_audio_briefing` | exit code 0 |
| Re-export shim present | `test -f reflections/pm_audio_briefing.py` | exit code 0 |
| Shim re-exports run | `python -c "import reflections.pm_audio_briefing as a, reflections.pm_briefings as b; assert a.run is b.run"` | exit code 0 |
| Shim deleted | `grep -n "_load_slots" reflections/pm_briefings/__init__.py` | exit code 1 |
| Legacy doc gone | `test ! -f docs/features/pm-audio-briefing.md` | exit code 0 |
| Plan archived | `test -f docs/plans/completed/daily-reflections-unification.md` | exit code 0 |
| Vault yaml valid | `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` | exit code 0 |
| Vault dead callables gone | `grep "daily-log-review\|daily-report-and-notify" config/reflections.yaml` | exit code 1 |
| Tests pass | `pytest tests/unit/reflections tests/integration/reflections -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

All resolved by critique cycle 2 (2026-05-08):

1. ~~/update orphan-log step persistence~~ — **MOOTED.** Critique dropped the orphan-log task entirely. Bridge machines may or may not have these files; not worth a `/update` step. If they accumulate they can be hand-cleaned per-machine.
2. ~~Worker restart timing / vault-vs-code race~~ — **RESOLVED via re-export shim.** `reflections/pm_audio_briefing.py` re-exports `run` from `reflections.pm_briefings`, so vault and code can land in any order on each machine without ImportError. Shim is removed in a follow-up PR ≥1 day after merge.
3. ~~No-slots warning surface~~ — **NOT BLOCKING.** Worker log warning is sufficient for now; a dashboard / Telegram surface is a separate decision once any project actually opts in.
