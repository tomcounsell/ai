---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/233
---

# Refactor "Daydream" into "Reflections"

## Problem

The "Daydream" system currently handles daily maintenance tasks -- log review, error checking, session analysis, auto-fix, memory consolidation, and more. The name "daydream" undersells the scope and makes it sound whimsical rather than functional. The system should be called "Reflections" to better communicate that it encompasses all self-directed maintenance work: scheduled reviews, audits, research tasks, and similar exercises a senior dev would do between sprints.

**Current behavior:**
Every reference -- file names, class names, CLI commands, launchd labels, log paths, env vars, docs -- uses "daydream" terminology.

**Desired outcome:**
All references renamed to "reflections" (or "reflection" where singular is appropriate). The system's conceptual scope is broadened to include any self-directed maintenance task, not just the current daily run.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a mechanical rename with broad blast radius (42 files reference "daydream"). The risk is not complexity but thoroughness -- missing a reference breaks something silently.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **File renames**: All `daydream`-named files become `reflections`-named equivalents
- **Class/function renames**: `DaydreamRun` -> `ReflectionRun`, `DaydreamIgnore` -> `ReflectionIgnore`, etc.
- **Service identity**: launchd label `com.valor.daydream` -> `com.valor.reflections`, plist filename, log filenames
- **Documentation**: All docs updated to use "Reflections" terminology
- **Backward compatibility**: Redis model data uses class-name-based keys in Popoto, so existing Redis data for `DaydreamRun` etc. will NOT be accessible under the new class names. Since this data is ephemeral (daily runs, 30-day TTL), this is acceptable -- old data ages out naturally.

### Flow

**Developer runs reflections** -> `python scripts/reflections.py` -> Steps 1-14 execute -> Report written to `logs/reflections.log`

**Scheduled execution** -> `com.valor.reflections` launchd -> same script -> same steps

### Technical Approach

1. **Phase 1: Rename source files** (models, scripts, tests)
2. **Phase 2: Update all internal references** (imports, class names, function names, logger names)
3. **Phase 3: Rename infrastructure** (plist, install script, env vars, log paths)
4. **Phase 4: Update documentation and CLAUDE.md** references
5. **Phase 5: Update the update system** (scripts/update/ references)
6. **Phase 6: Delete legacy files** -- no commented-out code, no aliases

The launchd service must be unloaded under the old label and reloaded under the new label. The install script handles this.

## Rabbit Holes

- **Migrating existing Redis data**: Popoto keys are class-name-based. Migrating `DaydreamRun` records to `ReflectionRun` keys is not worth the effort -- the data is ephemeral with 30-day TTL. Let it age out.
- **Adding new "reflection" task types in this PR**: The issue mentions "scheduled reviews, audits, research tasks" but this rename PR should NOT implement new task types. That is separate work.
- **Renaming the `data/daydream_state.json` file**: This is a legacy artifact already superseded by Redis. Just delete it if it still exists.

## Risks

### Risk 1: Missed references cause runtime errors
**Impact:** Import errors, broken launchd scheduling, missing logs
**Mitigation:** Grep-based validation after rename. Run full test suite. Verify launchd loads cleanly.

### Risk 2: Worktree files contain old references
**Impact:** Stale worktrees could confuse future work
**Mitigation:** Worktrees are isolated copies. They will pick up changes on next checkout. Do NOT modify worktree contents directly.

## No-Gos (Out of Scope)

- Adding new reflection task types (scheduled reviews, audits, research)
- Changing the system's behavior or step ordering
- Migrating existing Redis data between key namespaces
- Modifying files inside `.worktrees/` directories (these are isolated git worktrees)
- Renaming the `LessonLearned` model (it already has a generic name)

## Update System

The update system (`scripts/update/`) has direct references to daydream:

- `scripts/update/run.py`: calls `service.install_daydream()` and references `com.valor.daydream.plist`
- `scripts/update/service.py`: defines `install_daydream()` and `is_daydream_installed()` functions
- `scripts/remote-update.sh`: may reference daydream

All of these must be renamed to `install_reflections()`, `is_reflections_installed()`, `com.valor.reflections.plist`, etc. The update skill docs (`.claude/skills/update/SKILL.md`) must also be updated.

After merging, existing deployments will need one manual update cycle to transition the launchd label from `com.valor.daydream` to `com.valor.reflections`. The update script should handle unloading the old label.

## Agent Integration

No agent integration required -- this is a rename of internal tooling. The daydream/reflections system is not exposed through MCP servers or the bridge. The CLAUDE.md quick commands section references daydream commands and must be updated, but that is documentation, not integration.

## Documentation

- [ ] Rename `docs/features/daydream.md` to `docs/features/reflections.md` and update content
- [ ] Update `docs/features/README.md` index table entry
- [ ] Update `CLAUDE.md` quick commands section (daydream -> reflections)
- [ ] Update `README.md` references
- [ ] Update `config/SOUL.md` references
- [ ] Update `.claude/skills/setup/SKILL.md` references
- [ ] Update `.claude/skills/do-docs-audit/SKILL.md` references

## Success Criteria

- [ ] Zero references to "daydream" remain in source code (excluding docs/plans/ historical records and .worktrees/)
- [ ] `python scripts/reflections.py --dry-run` runs successfully
- [ ] All tests pass after rename (`pytest tests/`)
- [ ] `com.valor.reflections` plist loads via launchd without errors
- [ ] `scripts/install_reflections.sh` works correctly
- [ ] Update system functions renamed and working
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (rename-core)**
  - Name: rename-builder
  - Role: Execute all file renames, class renames, import updates, and infrastructure changes
  - Agent Type: builder
  - Resume: true

- **Validator (rename-verify)**
  - Name: rename-validator
  - Role: Verify zero remaining daydream references in active source, all tests pass
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs-update)**
  - Name: docs-updater
  - Role: Update all documentation files
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Rename model file and classes
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `models/daydream.py` -> `models/reflections.py`
- Rename classes: `DaydreamRun` -> `ReflectionRun`, `DaydreamIgnore` -> `ReflectionIgnore`
- Update `models/__init__.py` imports and docstring
- Keep `LessonLearned` name unchanged

### 2. Rename script files
- **Task ID**: build-scripts
- **Depends On**: none
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `scripts/daydream.py` -> `scripts/reflections.py`
- Rename `scripts/daydream_report.py` -> `scripts/reflections_report.py`
- Rename `scripts/install_daydream.sh` -> `scripts/install_reflections.sh`
- Update all internal references, imports, logger names

### 3. Rename infrastructure files
- **Task ID**: build-infra
- **Depends On**: none
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `com.valor.daydream.plist` -> `com.valor.reflections.plist`
- Update plist contents (label, script paths, log paths)
- Update `.env.example` variable name: `DAYDREAM_AUTO_FIX_ENABLED` -> `REFLECTIONS_AUTO_FIX_ENABLED`
- Delete `data/daydream_state.json` if it exists

### 4. Rename test files
- **Task ID**: build-tests
- **Depends On**: build-models, build-scripts
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `tests/test_daydream.py` -> `tests/test_reflections.py`
- Rename `tests/test_daydream_report.py` -> `tests/test_reflections_report.py`
- Rename `tests/test_daydream_redis.py` -> `tests/test_reflections_redis.py`
- Rename `tests/test_daydream_multi_repo.py` -> `tests/test_reflections_multi_repo.py`
- Rename `tests/test_daydream_scheduling.py` -> `tests/test_reflections_scheduling.py`
- Update all imports and references within test files

### 5. Update update system
- **Task ID**: build-update-system
- **Depends On**: build-infra
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename functions in `scripts/update/service.py`: `install_daydream` -> `install_reflections`, `is_daydream_installed` -> `is_reflections_installed`
- Update `scripts/update/run.py` references
- Update `scripts/remote-update.sh` references

### 6. Update all documentation
- **Task ID**: build-docs
- **Depends On**: build-scripts, build-infra
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Rename `docs/features/daydream.md` -> `docs/features/reflections.md`
- Update `CLAUDE.md`, `README.md`, `config/SOUL.md`
- Update all skill files referencing daydream
- Update `docs/features/README.md` index

### 7. Validate zero remaining references
- **Task ID**: validate-all
- **Depends On**: build-models, build-scripts, build-infra, build-tests, build-update-system, build-docs
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -ri "daydream" --include="*.py" --include="*.md" --include="*.sh" --include="*.plist" --include="*.json" . | grep -v ".worktrees/" | grep -v "docs/plans/" | grep -v "__pycache__"` returns zero results
- `pytest tests/` passes
- `python scripts/reflections.py --dry-run` runs without import errors

## Validation Commands

- `grep -ri "daydream" --include="*.py" --include="*.md" --include="*.sh" --include="*.plist" . | grep -v ".worktrees/" | grep -v "docs/plans/" | grep -v "__pycache__" | grep -v "logs/"` - Verify zero remaining references
- `python -c "from models.reflections import ReflectionRun, ReflectionIgnore, LessonLearned"` - Verify model imports
- `python -c "from scripts.reflections_report import create_reflections_issue"` - Verify report imports
- `pytest tests/test_reflections.py tests/test_reflections_report.py tests/test_reflections_redis.py tests/test_reflections_multi_repo.py tests/test_reflections_scheduling.py` - All renamed tests pass

---

## Open Questions

1. **Should the `LessonLearned` model be renamed?** It already has a generic name not tied to "daydream". Recommend keeping it as-is.
2. **Should existing `data/daydream_state.json` be deleted or renamed?** It is a legacy artifact superseded by Redis. Recommend deleting it.
3. **Should the launchd migration (unload old label, load new) be handled automatically by the install script?** Recommend yes -- the install script should unload `com.valor.daydream` if present before loading `com.valor.reflections`.
