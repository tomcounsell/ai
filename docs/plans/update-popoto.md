---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-02-12
tracking: https://github.com/yudame/valor-agent/issues/90
---

# Update popoto to v1.0.0b2

## Problem

The codebase uses popoto v0.9.0 for Redis ORM functionality. The new v1.0.0b2 release includes a **breaking change**: the `sort_by` parameter on `SortedField` has been renamed to `partition_by`.

**Current behavior:**
Three models use the deprecated `sort_by` parameter:
- `AgentSession.started_at` - `sort_by="project_key"`
- `TelegramMessage.timestamp` - `sort_by="chat_id"`
- `RedisJob.created_at` - `sort_by="project_key"`

**Desired outcome:**
- Upgrade to popoto v1.0.0b2
- Migrate all `sort_by` → `partition_by` usages
- Leverage new features if beneficial (e.g., `delete_all()`, `to_dict()`, `auto_now`)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (straightforward dependency upgrade)
- Review rounds: 0 (ship it)

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Version bump**: Update `pyproject.toml` from `popoto>=0.9.0` to `popoto>=1.0.0b2`
- **Parameter rename**: Change `sort_by` to `partition_by` in 3 model definitions
- **Validation**: Run existing tests to ensure no regressions

### Flow

**Update dependency** → Rename parameters → Run tests → Commit and push

### Technical Approach

1. Update `pyproject.toml` dependency version
2. Search-and-replace `sort_by=` → `partition_by=` in:
   - `models/sessions.py` (line 20)
   - `models/telegram.py` (line 21)
   - `agent/job_queue.py` (line 45)
3. Run `uv sync` to update the dependency
4. Run tests to verify functionality

### Optional Enhancements (Low Priority)

Review new v1.0.0b2 features for potential improvements:
- `Model.delete_all()` - Could simplify `BridgeEvent.cleanup_old()`
- `to_dict()` - May simplify serialization in some places
- `auto_now_add`/`auto_now` on timestamps - Could remove manual `time.time()` calls

These are deferred to future work unless trivially applicable.

## Rabbit Holes

- **Don't adopt all new features at once** - The `delete_all()`, `to_dict()`, `get_or_create()` etc. are nice but not required for this upgrade. Focus on the breaking change only.
- **Don't refactor Redis usage patterns** - This is a dependency upgrade, not an architecture change.

## Risks

### Risk 1: Breaking change missed somewhere
**Impact:** Runtime errors on popoto model operations
**Mitigation:** Comprehensive grep for `sort_by`, run full test suite

### Risk 2: Behavior differences in new version
**Impact:** Subtle bugs in query ordering or filtering
**Mitigation:** Test manually with bridge after upgrade, monitor logs

## No-Gos (Out of Scope)

- Adopting new popoto features (e.g., `get_or_create`, `auto_now`)
- Refactoring model definitions beyond the parameter rename
- Adding new Redis models

## Update System

No update system changes required — this is a standard dependency upgrade. The update script already handles `uv sync` for dependency updates.

## Agent Integration

No agent integration required — this is a model layer change with no new tools or bridge modifications.

## Documentation

No documentation changes needed — this is an internal dependency upgrade with no user-facing changes.

## Success Criteria

- [ ] `pyproject.toml` updated to `popoto>=1.0.0b2`
- [ ] All `sort_by` parameters renamed to `partition_by`
- [ ] `uv sync` completes successfully
- [ ] `pytest tests/` passes
- [ ] Bridge starts and processes a message successfully

## Team Orchestration

### Team Members

- **Builder (upgrade)**
  - Name: upgrade-builder
  - Role: Update dependency and migrate parameters
  - Agent Type: builder
  - Resume: true

- **Validator (upgrade)**
  - Name: upgrade-validator
  - Role: Verify tests pass and bridge functions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update dependency and migrate parameters
- **Task ID**: build-upgrade
- **Depends On**: none
- **Assigned To**: upgrade-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `pyproject.toml`: change `popoto>=0.9.0` to `popoto>=1.0.0b2`
- Edit `models/sessions.py` line 20: `sort_by=` → `partition_by=`
- Edit `models/telegram.py` line 21: `sort_by=` → `partition_by=`
- Edit `agent/job_queue.py` line 45: `sort_by=` → `partition_by=`
- Run `uv sync`

### 2. Validate upgrade
- **Task ID**: validate-upgrade
- **Depends On**: build-upgrade
- **Assigned To**: upgrade-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/`
- Run `ruff check .`
- Verify no remaining `sort_by=` in popoto field definitions
- Confirm popoto version is 1.0.0b2

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-upgrade
- **Assigned To**: upgrade-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -r "sort_by=" models/ agent/ | grep -v partition` - Should return empty (no old usages)
- `uv pip show popoto | grep Version` - Should show 1.0.0b2
- `pytest tests/test_redis_models.py` - Redis model tests pass
- `pytest tests/` - Full test suite passes

---

## Open Questions

None — this is a straightforward dependency upgrade with clear migration path.
