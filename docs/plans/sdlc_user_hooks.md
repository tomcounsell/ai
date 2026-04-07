---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/183
---

# SDLC User-Level Hooks + PR #185 Tech Debt

## Problem

After running the update script on another machine, the SDLC pipeline doesn't enforce its rules. The agent commits directly to main despite being mid-pipeline.

**Current behavior:**
SDLC enforcement hooks live in `.claude/hooks/sdlc/` at project level. The update script (`scripts/update/hardlinks.py`) syncs skills, commands, and agents to `~/.claude/` but not hooks. Other repos on other machines have no mechanical enforcement.

**Desired outcome:**
SDLC enforcement hooks fire in every repo on every machine via user-level `~/.claude/settings.json`. Additionally, PR #185 tech debt items are cleaned up in the same pass since they touch the same auto-continue code path.

## Appetite

**Size:** Small

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 0 (scope is clear from prior discussion)
- Review rounds: 1

## Prerequisites

No prerequisites. PR #185 (stage-aware auto-continue) is already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| AgentSession helpers exist | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'is_sdlc_job')"` | Shared SDLC detection |

## Solution

### Key Elements

- **Shared `is_sdlc_context()` module**: Single utility in `.claude/hooks/sdlc/sdlc_context.py` that all 3 hooks import, replacing the 3x duplication from PR #184
- **Settings merger in `hardlinks.py`**: New `sync_user_hooks()` function copies hook scripts to `~/.claude/hooks/sdlc/` and merges entries into `~/.claude/settings.json`
- **PR #185 tech debt fixes**: Fix hardcoded `MAX_AUTO_CONTINUES` in log guards, complete `_enqueue_continuation()` refactor, fix test typo

### Flow

**Update script runs** → `sync_user_hooks()` copies hooks to `~/.claude/hooks/sdlc/` → merges hook entries into `~/.claude/settings.json` → next Claude Code session in any repo fires hooks → hooks detect SDLC context via branch name + AgentSession → block/warn as appropriate

### Technical Approach

1. **Extract shared `sdlc_context.py`**

   Create `.claude/hooks/sdlc/sdlc_context.py` with `is_sdlc_context()` — the single source of truth. All 3 hook scripts import from it instead of each containing their own copy. The function uses the same two-tier check: branch name `session/*` + AgentSession model query.

2. **Refactor existing hook scripts**

   The 3 hook scripts already exist at `.claude/hooks/sdlc/` (from PR #184's branch, committed to project level). Refactor them to:
   - Import `is_sdlc_context` from `sdlc_context.py` (relative import or sys.path)
   - Remove their duplicated `is_sdlc_context()` definitions
   - Keep all other logic intact — they already work correctly

3. **Add `sync_user_hooks()` to `hardlinks.py`**

   - Copy `.claude/hooks/sdlc/*.py` to `~/.claude/hooks/sdlc/`
   - Read `~/.claude/settings.json`, merge SDLC hook entries (PreToolUse, PostToolUse, Stop)
   - Deduplicate by command string — don't add entries that already exist
   - Write back. Never clobber non-SDLC user hooks.

4. **Fix PR #185 tech debt in `agent/job_queue.py`**

   - Lines ~1208, ~1213, ~1408: Replace `MAX_AUTO_CONTINUES` with `effective_max` in log guards and messages
   - Lines ~1152-1200: Refactor classifier path to call `_enqueue_continuation()` instead of duplicating enqueue logic inline
   - `tests/test_stage_aware_auto_continue.py` line 304: Fix `TestMaxAutoContiuesConstants` → `TestMaxAutoContinuesConstants`

## Rabbit Holes

- **Syncing ALL project-level hooks to user level**: Only the SDLC enforcement hooks belong at user level. Validators, calendar hooks, etc. are project-specific.
- **Making hooks importable as a Python package**: Don't add `__init__.py` or make this a proper package. Keep it as standalone scripts with one shared utility.
- **Redesigning `_enqueue_continuation()` signature**: Just make the classifier path call it. Don't redesign the function's interface.

## Risks

### Risk 1: Settings merge corrupts user config
**Impact:** User's existing Claude Code hooks stop working
**Mitigation:** Read-modify-write with JSON validation. Only append to hook arrays, never replace. Test with existing user hooks present.

### Risk 2: Hook import fails on machines without AI repo
**Impact:** `is_sdlc_context()` can't import AgentSession, falls back to branch-only check
**Mitigation:** Already handled — the function has a try/except around the AgentSession import and falls back gracefully. Branch name check (`session/*`) works standalone.

## No-Gos (Out of Scope)

- Syncing project-level validators or calendar hooks to user level
- Changing SDLC detection logic (already done in PR #185)
- Modifying the classifier or coaching system
- Adding new SDLC stages or changing pipeline order
- Creating backup of `~/.claude/settings.json` (Tom said no)

## Update System

This IS an update system change. `scripts/update/hardlinks.py` gets a new `sync_user_hooks()` function called from `sync_claude_dirs()`. After this lands, running the update script on any machine will deploy SDLC hooks to user level.

## Agent Integration

No agent integration required — hooks fire automatically via Claude Code's hook system. The agent doesn't know about them.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` with user-level hooks section
- [ ] Update `docs/features/README.md` index entry for sdlc-enforcement
- [ ] Code comments on `sync_user_hooks()` in hardlinks.py

## Success Criteria

- [ ] `is_sdlc_context()` defined once in `sdlc_context.py`, imported by all 3 hooks
- [ ] `sync_user_hooks()` copies hooks to `~/.claude/hooks/sdlc/` and merges settings
- [ ] Running update script on fresh machine installs hooks at user level
- [ ] Hooks fire in non-AI repos when on a `session/` branch
- [ ] Hooks silently pass through for non-SDLC work
- [ ] `effective_max` used in job_queue.py log guards (lines ~1208, ~1213, ~1408)
- [ ] Classifier path calls `_enqueue_continuation()` (no duplicated enqueue logic)
- [ ] Test class typo fixed
- [ ] Existing tests pass without modification
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hooks-refactor)**
  - Name: hooks-builder
  - Role: Extract shared sdlc_context.py, refactor hooks, build settings merger
  - Agent Type: builder
  - Resume: true

- **Builder (tech-debt)**
  - Name: debt-builder
  - Role: Fix job_queue.py tech debt and test typo
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: hooks-validator
  - Role: Verify hooks work, settings merge, tech debt fixes
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs)**
  - Name: docs-writer
  - Role: Update sdlc-enforcement docs and README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extract shared sdlc_context.py and refactor hooks
- **Task ID**: build-shared-context
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/sdlc/sdlc_context.py` with `is_sdlc_context()` and `read_stdin()`, `block()`, `allow()` utilities
- Refactor `validate_commit_message.py`, `sdlc_reminder.py`, `validate_sdlc_on_stop.py` to import from sdlc_context
- Remove duplicated function definitions from each hook

### 2. Fix PR #185 tech debt
- **Task ID**: fix-tech-debt
- **Depends On**: none
- **Assigned To**: debt-builder
- **Agent Type**: builder
- **Parallel**: true
- Fix `MAX_AUTO_CONTINUES` → `effective_max` in job_queue.py log guards (~lines 1208, 1213, 1408)
- Refactor classifier path (~lines 1152-1200) to call `_enqueue_continuation()`
- Fix `TestMaxAutoContiuesConstants` → `TestMaxAutoContinuesConstants` in test file

### 3. Build settings merger in hardlinks.py
- **Task ID**: build-merger
- **Depends On**: build-shared-context
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `sync_user_hooks()` to `scripts/update/hardlinks.py`
- Copy hook scripts to `~/.claude/hooks/sdlc/`
- Read/merge/write `~/.claude/settings.json` with deduplication
- Integrate into `sync_claude_dirs()`

### 4. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-shared-context, fix-tech-debt, build-merger
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run existing tests: `pytest tests/test_auto_continue.py tests/test_stage_aware_auto_continue.py -v`
- Verify shared import works in all 3 hooks
- Verify settings merge handles existing hooks correctly
- Verify `effective_max` in log messages

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-enforcement.md` with user-level hooks
- Update `docs/features/README.md` index

### 6. Final Validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/test_auto_continue.py -v` — Existing auto-continue tests
- `pytest tests/test_stage_aware_auto_continue.py -v` — Stage-aware tests (including typo fix)
- `python -c "from pathlib import Path; exec(open('.claude/hooks/sdlc/sdlc_context.py').read()); print('shared module OK')"` — Shared module loads
- `black --check agent/job_queue.py .claude/hooks/sdlc/` — Code formatting
- `ruff check agent/job_queue.py .claude/hooks/sdlc/` — Linting
