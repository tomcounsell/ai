---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-05-06
tracking: https://github.com/tomcounsell/ai/issues/1306
last_comment_id:
---

# Finish pm-briefings cutover: rename package, strip shim, propagate vault changes

## Problem

PR #1295 inlined helpers and deleted `reflections/daily_report.py` + `reflections.auditing.run_log_review`, but it left the rest of the daily-reflections cutover in a parallel-run state.

**Current behavior:**
- Package directory still named `reflections/pm_audio_briefing/`; the new feature name `pm-briefings` only appears in docs and in `ui/data/reflections.py` aliasing.
- `_load_slots()` shim at `reflections/pm_audio_briefing/__init__.py:125` synthesizes `pm_briefing.slots` from legacy `pm_briefing.angles + pm_briefing.schedule` config. No project actually uses the legacy keys today (verified: 0 of 13 projects in `~/Desktop/Valor/projects.json` carry either shape), so the shim is dead weight.
- Vault `~/Desktop/Valor/reflections.yaml` (lines 196, 280, 340) still has registry entries `daily-log-review` (calls deleted `reflections.auditing.run_log_review`), `daily-report-and-notify` (calls deleted `reflections.daily_report.run`), and `pm-audio-briefing` (legacy registry name). Every scheduler tick logs ImportError on the first two until the operator removes them.
- `docs/features/pm-audio-briefing.md` and `docs/plans/daily-reflections-unification.md` describe the migration in transition tense rather than the post-cutover status quo.
- Orphan `logs/reflections/report_2026-*.md` files may remain on bridge machines other than the dev machine (zero on dev today).

**Desired outcome:** Single coherent post-cutover state. Package renamed, shim deleted, vault registry cleaned, legacy doc deleted, transition narrative gone, and propagation routed through `/update` for all bridge machines.

## Freshness Check

**Baseline commit:** `75e9d3c6` (main at plan time)
**Issue filed at:** 2026-05-06T10:19:48Z (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `reflections/pm_audio_briefing/__init__.py:125` — `_load_slots()` shim — still holds at line 125; called from line 415 in the dispatcher's `run()`.
- `~/Desktop/Valor/reflections.yaml` lines 196 / 280 / 340 — `daily-log-review`, `daily-report-and-notify`, `pm-audio-briefing` registry entries — all three still present.
- 7-module `reflections/pm_audio_briefing/` package — directory exists exactly as the issue describes (`__init__.py`, `builder.py`, `collector.py`, `daily_log.py`, `delivery.py`, `log_audit.py`, `morning.py`).

**Cited sibling issues/PRs re-checked:**
- #1292 — closed, completed by #1295 (today's PR being followed up).
- #1295 — merged 2026-05-06 (`feat(#1292): pm-briefings cutover — inline helpers, retire legacy modules`); confirmed it deleted the two callables and left the rest.
- #1281 — original consolidation, still merged.
- #1276 — tracking issue for `daily-reflections-unification.md`; still open.

**Commits on main since issue was filed:** None (plan written same day as issue file).

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/daily-reflections-unification.md` (status: Ready, tracking #1276). This plan finishes that one and moves it to `completed/`.

**Notes:** Issue accurately reports the orphan-log situation as "may differ on other machines" — verified zero `report_2026-*.md` files on the dev machine today. Cleanup step is a no-op on the dev machine but still needed for any other active bridge machine.

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
- **Shim removal**: Delete `_load_slots()` and call sites that pre-process; `run()` reads `(project.get("pm_briefing") or {}).get("slots") or []` directly. If a project has `pm_briefing.enabled=true` but no `slots`, log a warning and skip — no synthesis.
- **Test cleanup**: Delete `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py`. Update imports in remaining test files to `reflections.pm_briefings`.
- **UI cleanup**: `ui/data/reflections.py` — drop the `_PREFIX_EXPANDED_REFLECTIONS` dual-listing of legacy name, drop the `"pm-briefings": "pm-audio-briefing"` parent-mapping. Single-source `pm-briefings` only.
- **Doc cleanup**: Delete `docs/features/pm-audio-briefing.md`. Rewrite `docs/features/pm-briefings.md` and `docs/features/reflections.md` to describe only the post-cutover state — no "previously", no "transitioning", no "kept for backward compatibility" framing. Move `docs/plans/daily-reflections-unification.md` to `docs/plans/completed/` with a one-line completion note pointing at this issue + PR #1295.
- **Vault edit**: `~/Desktop/Valor/reflections.yaml` — REMOVE `daily-log-review` and `daily-report-and-notify` blocks; RENAME `pm-audio-briefing` → `pm-briefings` and update its `callable:` to `reflections.pm_briefings.run`.
- **Per-machine cleanup**: orphan `logs/reflections/report_2026-*.md` removal added to `/update` skill (one-shot step).
- **Deploy**: run `/update` on every active bridge machine listed in `projects.json`.

### Flow

Pre-cutover state → `git mv` package + import sweep → shim deletion → test/UI/doc cleanup → vault edit (dev machine) → /update skill deployment to every other bridge machine → post-cutover steady state.

### Technical Approach

- **Mechanical sweep**: `git mv reflections/pm_audio_briefing reflections/pm_briefings`, then a single `find … -exec sed -i ''` sweep replacing `pm_audio_briefing` → `pm_briefings` and `pm-audio-briefing` → `pm-briefings` across `reflections/`, `tests/`, `ui/data/reflections.py`, `agent/`, `scripts/`, and any docstrings. Runs in `tests/unit/reflections/` to verify no string survived in test fixture data.
- **Shim removal**: Replace `slots = _load_slots(project)` (line 415) with `slots = (project.get("pm_briefing") or {}).get("slots") or []`; if empty, log `"pm-briefings: project %s has no slots configured; skipping"` and continue. Delete `_load_slots()` body. Verify `run()` still passes type checks on the simplified path.
- **Single-source UI**: in `ui/data/reflections.py`, replace `_PREFIX_EXPANDED_REFLECTIONS = ("pm-briefings", "pm-audio-briefing")` with `("pm-briefings",)`. Delete the `"pm-briefings": "pm-audio-briefing"` mapping entry. Delete the comment block discussing the dual-listing rationale (lines 77-86 area).
- **Vault yaml**: edit `~/Desktop/Valor/reflections.yaml` directly (the symlink target). Remove the two legacy blocks; rename the third block's `name:` field and `callable:` field. Validate by running `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"`.
- **Doc rewrite**: pm-briefings.md and reflections.md drop transition framing. pm-audio-briefing.md is `git rm`-ed entirely.
- **Plan move**: `git mv docs/plans/daily-reflections-unification.md docs/plans/completed/` and append a one-line completion note: `> Completed by issue #1306 / PR #<this-PR>. Cutover finished 2026-05-06.`
- **/update skill addition**: Add a one-shot `rm -f logs/reflections/report_2026-*.md` step gated on `[ -d logs/reflections ]` to `.claude/skills/update/SKILL.md` (or equivalent step file). Idempotent on machines where files don't exist.

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
- [ ] `tests/integration/reflections/` — UPDATE: any file importing `reflections.pm_audio_briefing.*` updated; mechanical sweep covers them.
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` — REPLACE the deleted legacy-config-migration test with a test asserting the no-slots warning + clean return path.

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

### Risk 4: /update skill orphan-log-cleanup step accidentally deletes user data
**Impact:** A wrong glob deletes legitimate logs.
**Mitigation:** Pin the glob exactly to `logs/reflections/report_2026-*.md` (literal year prefix). Run `ls` first, then `rm -f`. Idempotent on machines where files don't exist.

## Race Conditions

No race conditions identified — all changes are static (file rename, code deletion, config edit). The scheduler reads vault yaml at startup or on file change; there is no concurrent write path.

## No-Gos (Out of Scope)

- Renaming the feature beyond `pm-briefings`.
- Changes to slot semantics, briefing content, voice/text rendering.
- Adding `pm_briefing.slots` to any project that doesn't already opt in.
- Generic vault-validation tooling.
- Refactoring `ui/data/reflections.py` beyond removing the dual-name plumbing.

## Update System

This is half the work. The `/update` skill at `.claude/skills/update/SKILL.md` runs on every bridge machine via `scripts/remote-update.sh`.

- **Vault propagation**: vault is iCloud-synced; once the dev-machine `~/Desktop/Valor/reflections.yaml` edit propagates, every other machine has it after iCloud sync. /update should re-read the vault yaml after pull. No script change required for the yaml itself.
- **Orphan-log cleanup**: add a one-shot step to `/update` that runs `find logs/reflections -maxdepth 1 -name 'report_2026-*.md' -delete` (or equivalent `rm -f`). Idempotent. Document inline that this is a one-time post-cutover step that can stay in /update permanently as a no-op afterward.
- **Worker restart**: existing /update step already restarts the worker after pulling, which picks up the new package name. No change required.

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

- [ ] `grep -r "pm_audio_briefing\|pm-audio-briefing" .` returns zero hits in tracked files (excluding `docs/plans/completed/` and commit-message references).
- [ ] `reflections/pm_audio_briefing/` directory does not exist; `reflections/pm_briefings/` does, with all 7 modules and preserved git history.
- [ ] `_load_slots()` not present in `reflections/pm_briefings/__init__.py`; `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py` deleted.
- [ ] `docs/features/pm-audio-briefing.md` deleted; `docs/features/pm-briefings.md` and `docs/features/reflections.md` describe only the post-cutover state (no "legacy", "previously", "transition" tokens).
- [ ] `docs/plans/daily-reflections-unification.md` is in `docs/plans/completed/` with completion note.
- [ ] On the dev machine: `~/Desktop/Valor/reflections.yaml` has neither `daily-log-review` nor `daily-report-and-notify`; the third entry's `name:` is `pm-briefings` and `callable:` is `reflections.pm_briefings.run`.
- [ ] `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"` exits 0.
- [ ] `ls logs/reflections/report_2026-*.md 2>/dev/null` returns nothing on the dev machine.
- [ ] One worker scheduler tick post-deploy produces no `ImportError` in `logs/worker.log`.
- [ ] `/update` deploy completed on every active bridge machine listed in `projects.json`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

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

### 1. Rename package + sweep imports
- **Task ID**: build-rename
- **Depends On**: none
- **Validates**: `tests/unit/reflections/`, `tests/integration/reflections/`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- `git mv reflections/pm_audio_briefing reflections/pm_briefings`
- Sweep `pm_audio_briefing` → `pm_briefings` and `pm-audio-briefing` → `pm-briefings` across `reflections/`, `tests/`, `ui/data/reflections.py`, any other tracked file. Use `grep -rl pm_audio_briefing` to enumerate, then per-file `sed`.
- Rename test files `test_pm_audio_briefing_*.py` → `test_pm_briefings_*.py` via `git mv`.
- Run `pytest tests/unit/reflections/ -x -q` to confirm imports resolve.

### 2. Delete shim and add no-slots warning
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

### 3. Single-source UI plumbing
- **Task ID**: build-ui-cleanup
- **Depends On**: build-rename
- **Validates**: dashboard renders pm-briefings entries (manual: `curl -s localhost:8500/dashboard.json | jq .reflections`)
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: true
- In `ui/data/reflections.py`: replace `_PREFIX_EXPANDED_REFLECTIONS = ("pm-briefings", "pm-audio-briefing")` with `("pm-briefings",)`.
- Delete the `"pm-briefings": "pm-audio-briefing"` parent-mapping entry.
- Delete the comment block (~lines 77-86) explaining dual-listing.

### 4. Doc cleanup + plan archival
- **Task ID**: build-docs
- **Depends On**: build-rename
- **Validates**: `grep -rn "previously\|transitioning\|backward compat" docs/features/pm-briefings.md docs/features/reflections.md` returns no matches (excluding code blocks)
- **Assigned To**: cutover-builder
- **Agent Type**: documentarian
- **Parallel**: true
- `git rm docs/features/pm-audio-briefing.md`.
- Rewrite `docs/features/pm-briefings.md` to describe only the post-cutover state.
- Update `docs/features/reflections.md` to drop transition narrative.
- Update `docs/features/README.md` index entries if needed.
- `git mv docs/plans/daily-reflections-unification.md docs/plans/completed/` and append the one-line completion note.

### 5. Vault yaml edit (dev machine)
- **Task ID**: build-vault
- **Depends On**: build-shim-removal
- **Validates**: `python -c "import yaml; yaml.safe_load(open('config/reflections.yaml'))"`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `~/Desktop/Valor/reflections.yaml` directly. Remove `daily-log-review` block. Remove `daily-report-and-notify` block. Rename `pm-audio-briefing` → `pm-briefings`. Update its `callable:` to `reflections.pm_briefings.run`.
- Validate yaml parses cleanly.
- This change is on the dev machine only; iCloud sync + /update propagates it to other machines.

### 6. /update skill orphan-log step
- **Task ID**: build-update-step
- **Depends On**: build-vault
- **Validates**: re-running /update is a no-op on a clean machine
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a one-shot orphan-log cleanup step to `.claude/skills/update/SKILL.md` (or its step files): `rm -f logs/reflections/report_2026-*.md` gated on directory existence. Idempotent.

### 7. Validate cutover
- **Task ID**: validate-cutover
- **Depends On**: build-rename, build-shim-removal, build-ui-cleanup, build-docs, build-vault, build-update-step
- **Assigned To**: cutover-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "pm_audio_briefing\|pm-audio-briefing" .` excluding `docs/plans/completed/` and `.git/` — assert zero hits.
- Run `pytest tests/unit/reflections/ tests/integration/reflections/ -q` — assert green.
- Run `python -m ruff check . && python -m ruff format --check .` — assert green.
- Validate `config/reflections.yaml` yaml.
- Run worker for one tick locally; tail `logs/worker.log` for ImportError.
- Confirm dashboard `/dashboard.json` shows `pm-briefings` entries (no `pm-audio-briefing` legacy alias).
- Report pass/fail.

### 8. Final validation
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
| No legacy refs | `grep -r "pm_audio_briefing\|pm-audio-briefing" . --exclude-dir=.git --exclude-dir=docs/plans/completed --exclude-dir=.venv` | exit code 1 |
| New package present | `test -d reflections/pm_briefings && test -f reflections/pm_briefings/__init__.py` | exit code 0 |
| Old package gone | `test ! -d reflections/pm_audio_briefing` | exit code 0 |
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

1. **/update orphan-log step persistence**: should the `rm -f logs/reflections/report_2026-*.md` step stay in `/update` permanently as a defensive no-op, or be a one-time cleanup we delete after every machine has been updated once? My default is "keep permanently as no-op" because it's safer than tracking which machines ran it; confirm.
2. **Worker restart timing**: vault edit + iCloud sync + /update on each machine is not atomic. During the propagation window, some machines run new vault yaml against old code (pre-rename) and vice versa. Acceptable to accept a few-minute window of ImportError on machines that pull the vault edit before they pull the code, or do we coordinate via deploy ordering (code first via git, vault edit second)?
3. **`pm_briefing.enabled=true` projects**: confirmed zero today. If you intend to opt projects in soon, do you want the no-slots warning surfaced in the dashboard / Telegram alert, or is a worker log warning sufficient?
