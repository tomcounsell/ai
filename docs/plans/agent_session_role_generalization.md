---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/634
last_comment_id:
---

# Generalize AgentSession parent-child model and add role field

## Problem

The AgentSession model has three naming/design issues left over from #631 that prevent the system from supporting arbitrary session roles beyond "chat" and "dev":

**Current behavior:**
1. `parent_chat_session_id` KeyField implies the parent is always a "PM session", but there is no PM session class — it's all AgentSession with different `session_type` values.
2. No `role` field exists — session specialization is encoded only in `session_type` (a KeyField limited to "chat"/"dev"). Adding new roles would require KeyField changes and Redis key migrations.
3. `create_dev()` factory method hard-codes dev-session creation. Every new role would need its own factory method.

**Desired outcome:**
- `parent_chat_session_id` renamed to `parent_session_id` (accurate, role-neutral name)
- New `role` DataField for flexible role assignment without key-level impact
- `create_child(role=...)` factory replacing the rigid `create_dev()`
- Docstrings updated where they reference "Dev session"/"PM session" as if they were distinct classes

## Prior Art

- **#608 / PR #616**: Renamed all "job" terminology to "agent_session". Left KeyFields unchanged.
- **#609 / PR #628**: AgentSession field cleanup. Added property aliases, kept `job_id` as AutoKeyField.
- **#631 / PR #633**: Renamed `job_id` → `id` and `parent_job_id` → `parent_agent_session_id`. Established the SCAN + RENAME + `rebuild_indexes()` migration pattern. Deliberately scoped out the remaining items that this plan addresses.

## Spike Results

### spike-1: Key position analysis after rename
- **Assumption**: "Renaming `parent_chat_session_id` to `parent_session_id` changes key segment positions, requiring segment swapping like PR #633"
- **Method**: code-read
- **Finding**: **No segment swap needed.** Current alphabetical order: `chat_id`(1), `id`(2), `parent_agent_session_id`(3), `parent_chat_session_id`(4), `project_key`(5), `session_type`(6). After rename: `parent_session_id` still sorts to position 4 (after `parent_agent_session_id`, before `project_key`). The Redis key structure is unchanged — only the hash field name inside each record changes.
- **Confidence**: high
- **Impact on plan**: Migration is simpler than #631. No key restructuring needed — just hash field rename (`HSET` new name, `HDEL` old name) plus `rebuild_indexes()`. No `RENAME` of Redis keys themselves.

### spike-2: session_type vs role design space
- **Assumption**: "role should replace session_type"
- **Method**: code-read
- **Finding**: **No — role supplements session_type.** `session_type` is a KeyField used in 7 critical permission/routing checks across `sdk_client.py`, `pre_tool_use.py`, and `steer_child.py`. It determines the permission model (read-only vs full). `role` is a specialization within a session type — e.g., a dev-type session could have role "builder", "documentarian", "reviewer". Making `role` a DataField (not KeyField) means no key migration impact and roles can be added freely.
- **Confidence**: high
- **Impact on plan**: `role` is a `Field(null=True)` — simple addition. `session_type` stays unchanged. Backfill: `session_type="dev"` → `role="dev"`, `session_type="chat"` → `role="pm"`.

### spike-3: Dev/PM session terminology audit
- **Assumption**: "Hundreds of Dev/PM session references need updating"
- **Method**: code-read
- **Finding**: 733 total references across 70 files. However, these are **intentional architectural terminology** — "PM session" and "Dev session" are the official names for the two session roles. They correctly describe the architecture. The issue requested updating docstrings that treat them as distinct classes, but the actual usage is already correct (they reference roles, not classes).
- **Confidence**: high
- **Impact on plan**: Scope down docstring updates to only the model file's class/module docstrings and the factory method docstrings that literally say "Creates a Dev session" — update those to "Creates a child AgentSession with role=dev". Do NOT bulk-rename 733 references.

## Data Flow

1. **Migration script**: SCAN `AgentSession:*` → for each record: `HGET parent_chat_session_id` → `HSET parent_session_id` → `HDEL parent_chat_session_id` → after all: `rebuild_indexes()`
2. **Model update**: Rename field declaration, add `role = Field(null=True)`, update `create_child()` factory
3. **Caller update**: All files referencing `parent_chat_session_id` switch to `parent_session_id`
4. **Runtime flow**: `pre_tool_use.py` hook calls `AgentSession.create_child(role="dev", ...)` instead of `AgentSession.create_dev(...)`

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `parent_chat_session_id` → `parent_session_id` across all callers; `create_dev()` → `create_child(role=...)` with backward-compat wrapper
- **Coupling**: Decreases — removes PM session-specific naming from a generic parent-child relationship
- **Data ownership**: No change
- **Reversibility**: Medium — another migration script could reverse the hash field rename

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (continuation of established pattern from #631)
- Review rounds: 1 (validate migration safety)

## Prerequisites

No prerequisites — this work uses existing Redis infrastructure and Popoto ORM capabilities.

## Solution

### Key Elements

- **Migration script**: Hash field rename (`parent_chat_session_id` → `parent_session_id`) + backfill `role` from `session_type`
- **Model field rename**: Change KeyField name, add `role` DataField
- **Factory generalization**: `create_dev()` → `create_child(role=...)` with `create_dev()` kept as thin wrapper for backward compat during transition
- **Caller updates**: Mechanical rename across ~12 files (6 source + 6 test files with references)
- **Targeted docstring updates**: Only model and factory docstrings, not bulk rename

### Flow

**Run migration** (dry-run first) → **Update model fields** → **Update callers** → **Run tests** → **Update targeted docstrings**

### Technical Approach

- Write `scripts/migrate_parent_session_field.py` that:
  1. SCANs all `AgentSession:*` keys (excluding index keys)
  2. For each record: renames hash field `parent_chat_session_id` → `parent_session_id`
  3. Backfills `role` field from `session_type` on ALL records (`"chat"` → `"pm"`, `"dev"` → `"dev"`), not just those with `parent_chat_session_id`
  4. Calls `AgentSession.rebuild_indexes()` after all changes
  5. Supports `--dry-run` flag
  6. Idempotent: skips records that already have `parent_session_id`
- **No Redis key RENAME needed** — key structure is unchanged (spike-1 confirmed)
- Add `role = Field(null=True)` to AgentSession model
- Replace `create_dev()` with `create_child(role=...)`, keep `create_dev()` as a thin wrapper that calls `create_child(role="dev", ...)`
- Add backward-compat mapping in `_normalize_kwargs`: `parent_chat_session_id` → `parent_session_id`
- Override `pre_save()` to emit a warning if `role` is None at save time (catches missed factory usage without blocking migration)

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Migration script must handle missing `parent_chat_session_id` field gracefully (skip record)
- [x] Migration must handle partial failures by logging and continuing

### Empty/Invalid Input Handling
- [x] Migration handles empty Redis (zero records) gracefully
- [x] `create_child()` validates `role` parameter is a known value or None
- [x] `role=None` is valid for unspecialized sessions

### Error State Rendering
- [x] Migration reports summary: total records, migrated, skipped, errors

## Test Impact

- [x] `tests/unit/test_agent_session_hierarchy.py` — UPDATE: replace all `parent_chat_session_id` with `parent_session_id`
- [x] `tests/unit/test_agent_session_scheduler_kill.py` — UPDATE: replace `parent_chat_session_id` references
- [x] `tests/unit/test_model_relationships.py` — UPDATE: replace `parent_chat_session_id` references in field presence assertions
- [x] `tests/unit/test_dev_session_registration.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`, update `create_dev` calls to `create_child`
- [x] `tests/unit/test_steer_child.py` — UPDATE: replace `parent_chat_session_id` references
- [x] `tests/unit/test_summarizer.py` — UPDATE: replace `parent_chat_session_id` in docstrings
- [x] `tests/unit/test_chat_session_factory.py` — UPDATE: update factory method references
- [x] `tests/integration/test_agent_session_queue_session_type.py` — UPDATE: replace `parent_chat_session_id`
- [x] `tests/e2e/test_session_spawning.py` — UPDATE: replace `parent_chat_session_id` (10 occurrences)
- [x] `tests/e2e/test_context_propagation.py` — UPDATE: replace `parent_chat_session_id` and `get_parent_chat_session` (10+ occurrences)
- [x] `tests/unit/test_delivery_execution.py` — UPDATE: replace `get_parent_chat_session` mock (line 20)
- [x] `tests/unit/test_pre_tool_use_start_stage.py` — UPDATE: replace `create_dev` references with `create_child` (4 occurrences)

## Rabbit Holes

- **Bulk-renaming 733 Dev/PM session references**: These are intentional architectural terms. Only update model/factory docstrings, not every comment and doc.
- **Replacing `session_type` with `role`**: They serve different purposes (permission model vs specialization). Keep both.
- **Making `role` a KeyField**: This would change Redis key structure. Use DataField (`Field`) instead.
- **Building a generic Popoto migration framework**: Write a standalone script.
- **Adding new roles beyond "pm" and "dev" now**: The `role` field enables future roles, but defining them is follow-up work.

## Risks

### Risk 1: Hash field rename breaks Popoto queries
**Impact:** Queries filtering on `parent_chat_session_id` return empty after migration but before code update.
**Mitigation:** Deploy sequence: stop bridge → run migration → deploy code → restart bridge. Migration and code update happen atomically from the bridge's perspective.

### Risk 2: Backward-compat shim in `_normalize_kwargs` masks bugs
**Impact:** Old callers silently work via shim instead of being updated.
**Mitigation:** Add a deprecation warning log when the shim triggers. Grep to verify zero callers use the old name after the update.

## Race Conditions

### Race 1: Bridge creates session during migration
**Location:** Migration script + bridge session creation
**Trigger:** Bridge creates a new AgentSession while migration is running
**Data prerequisite:** All existing records must be migrated before new model code is deployed
**State prerequisite:** Bridge must be stopped during migration
**Mitigation:** Deployment sequence: stop bridge → run migration → deploy code → start bridge. Eliminates the race.

## No-Gos (Out of Scope)

- Renaming `session_type` or changing its values (stays as KeyField discriminator)
- Defining new role values beyond "pm" and "dev" (follow-up work)
- Updating all 733 Dev/PM session references (only model docstrings)
- Modifying `create_chat()` or `create_local()` factory methods
- Adding role-based permission checks (future work)
- Multi-machine migration coordination (single Redis instance)

## Update System

The migration script must run on the production machine before the code update deploys. Update sequence:
1. Stop bridge (`./scripts/valor-service.sh stop`)
2. Pull new code (`git pull`)
3. Run migration (`python scripts/migrate_parent_session_field.py`)
4. Restart bridge (`./scripts/valor-service.sh restart`)

No changes to the update skill or `scripts/remote-update.sh` needed — the migration is a one-time manual operation.

## Agent Integration

No agent integration required — this is a model-internal rename and field addition. The agent interacts with AgentSession through the queue and scheduler, which will be updated as part of the caller changes. No MCP server or bridge changes needed beyond updating `parent_chat_session_id` references.

## Documentation

### Feature Documentation
- [x] Update `docs/features/agent-session-model.md` — replace `parent_chat_session_id` references, add `role` field documentation
- [x] Update `docs/features/chat-dev-session-architecture.md` — update field names in data model section
- [x] Update `docs/features/redis-models.md` — update field documentation table
- [x] Update `docs/features/session-isolation.md` — if it references `parent_chat_session_id`

### Inline Documentation
- [x] Update module and class docstrings in `models/agent_session.py`
- [x] Update factory method docstrings to reference `create_child(role=...)`
- [x] Update comments in `agent/agent_session_queue.py` field preservation list

## Success Criteria

- [x] `parent_chat_session_id` field renamed to `parent_session_id` (KeyField) in model
- [x] New `role` DataField added to AgentSession with backfill from `session_type`
- [x] `create_child(role=...)` factory method exists and works
- [x] `create_dev()` exists as thin wrapper calling `create_child(role="dev", ...)`
- [x] Zero references to `parent_chat_session_id` in Python files (excluding migration script and `_normalize_kwargs` backward-compat mapping)
- [x] Migration script runs successfully in dry-run mode
- [x] All tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (migration)**
  - Name: migration-builder
  - Role: Write migration script, update model, update callers
  - Agent Type: builder
  - Resume: true

- **Validator (migration)**
  - Name: migration-validator
  - Role: Verify migration correctness and test coverage
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Write migration script
- **Task ID**: build-migration-script
- **Depends On**: none
- **Validates**: `scripts/migrate_parent_session_field.py` runs without error in dry-run mode
- **Informed By**: spike-1 (no key restructuring needed — hash field rename only)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/migrate_parent_session_field.py` with hash field rename + role backfill
- Support `--dry-run` flag
- Idempotent: skip records already migrated
- Report summary statistics

### 2. Update model fields and add role
- **Task ID**: build-model-update
- **Depends On**: build-migration-script
- **Validates**: `tests/unit/test_model_relationships.py`, `tests/unit/test_agent_session_hierarchy.py`
- **Informed By**: spike-2 (role is DataField supplementing session_type)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `parent_chat_session_id` to `parent_session_id` (KeyField) in `models/agent_session.py`
- Add `role = Field(null=True)` to AgentSession
- Replace `create_dev()` with `create_child(role=...)`, keep `create_dev()` as thin wrapper
- Add backward-compat mapping in `_normalize_kwargs`
- Override `pre_save()` to warn if `role` is None (soft validation — doesn't block save)
- Update `get_parent_chat_session()` → `get_parent_session()` and `get_dev_sessions()` → `get_child_sessions()`

### 3. Update callers
- **Task ID**: build-caller-update
- **Depends On**: build-model-update
- **Validates**: `tests/unit/test_dev_session_registration.py`, `tests/unit/test_steer_child.py`
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/hooks/pre_tool_use.py`: `create_dev()` → `create_child(role="dev", ...)`
- Update `agent/hooks/subagent_stop.py`: `parent_chat_session_id` → `parent_session_id`
- Update `agent/hooks/stop.py`: `get_parent_chat_session()` → `get_parent_session()`
- Update `bridge/response.py`: `get_parent_chat_session()` → `get_parent_session()` (4 references)
- Update `scripts/steer_child.py`: same replacements
- Update `agent/agent_session_queue.py`: field preservation list
- Update all test files with new field names (see Test Impact section)
- Grep entire project to catch any missed references

### 4. Update targeted docstrings
- **Task ID**: build-docstring-update
- **Depends On**: build-caller-update
- **Informed By**: spike-3 (only model/factory docstrings, not bulk rename)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update module docstring in `models/agent_session.py`
- Update class docstring in AgentSession
- Update factory method docstrings (`create_child`, `create_chat`)
- Update `get_parent_session()` and `get_child_sessions()` docstrings

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-docstring-update
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify zero `parent_chat_session_id` references in Python files (excluding migration script and `_normalize_kwargs`)
- Verify migration script dry-run completes without error

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: migration-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md`
- Update `docs/features/chat-dev-session-architecture.md`
- Update `docs/features/redis-models.md`

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale parent_chat_session_id refs | `grep -rn 'parent_chat_session_id' --include='*.py' . \| grep -v migrate_parent_session \| grep -v '_normalize_kwargs'` | exit code 1 |
| Migration dry-run | `python scripts/migrate_parent_session_field.py --dry-run` | exit code 0 |
| Role field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'role')"` | exit code 0 |

## Critique Results

**Critiqued**: 2026-04-02
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 8 total (2 blockers, 4 concerns, 2 nits)

### Blockers

#### 1. Missing callers of renamed methods in Task 3
- **Severity**: BLOCKER
- **Critics**: Skeptic, Operator
- **Location**: Task 3 (build-caller-update)
- **Finding**: Task 3 lists specific files to update (`pre_tool_use.py`, `subagent_stop.py`, `steer_child.py`, `agent_session_queue.py`) but omits `bridge/response.py` and `agent/hooks/stop.py`, both of which call `get_parent_chat_session()`. If Task 2 renames this to `get_parent_session()`, these callers will break at runtime.
- **Suggestion**: Add `bridge/response.py` and `agent/hooks/stop.py` to the explicit caller update list in Task 3. The plan says "Grep entire project to catch any missed references" but the explicit list should be correct to avoid relying on a catch-all.

#### 2. Test Impact section missing test files that reference affected methods
- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Test Impact section
- **Finding**: Three test files are missing from the Test Impact section: `tests/unit/test_delivery_execution.py` (uses `get_parent_chat_session`), `tests/unit/test_pre_tool_use_start_stage.py` (uses `create_dev`), and `tests/unit/test_model_relationships.py` is listed but with an incorrect rationale ("`_AGENT_SESSION_FIELDS` assertion" does not exist in that file -- it actually has field presence checks that may need updating for the new `role` field).
- **Suggestion**: Add `test_delivery_execution.py` and `test_pre_tool_use_start_stage.py` to Test Impact with disposition UPDATE. Correct the `test_model_relationships.py` entry rationale to reference field presence assertions for the new `role` field.

### Concerns

#### 3. Plan says "~23 files" but actual count is 12
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Solution > Key Elements
- **Finding**: The plan states "Mechanical rename across ~23 files" but grep shows only 12 files contain `parent_chat_session_id`. Overstating scope can lead to a false sense of thoroughness or hide the fact that the rename is smaller than expected.
- **Suggestion**: Correct the count to ~12 files (or regenerate from grep). The smaller scope is good news for the migration.

#### 4. No backward-compat wrapper for `get_parent_chat_session()` and `get_dev_sessions()`
- **Severity**: CONCERN
- **Critics**: Operator, Archaeologist
- **Location**: Task 2 (build-model-update)
- **Finding**: Task 2 renames `get_parent_chat_session()` to `get_parent_session()` and `get_dev_sessions()` to `get_child_sessions()`. The plan specifies a backward-compat wrapper for `create_dev()` but none for these two methods. If any code outside the repo (scripts, notebooks, manual debugging) calls the old names, it will break silently.
- **Suggestion**: Add thin backward-compat wrappers (like `create_dev` gets) or confirm via grep that no external callers exist. The `_normalize_kwargs` approach does not help here since these are instance methods, not constructor kwargs.

#### 5. Migration script should also backfill `role` for records missing `parent_chat_session_id`
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Data Flow / Technical Approach
- **Finding**: The migration script description focuses on renaming `parent_chat_session_id` and backfilling `role`, but the backfill logic is described as running only on records that have `parent_chat_session_id`. Records that are PM sessions (no `parent_chat_session_id`) still need `role` backfilled to `"pm"` based on `session_type`. The script needs to handle all records, not just those with the parent field.
- **Suggestion**: Clarify the migration script processes ALL `AgentSession:*` records: rename the parent field where present, and backfill `role` from `session_type` on every record regardless.

#### 6. Deployment sequence assumes single-operator execution
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Update System / Race Conditions
- **Finding**: The deployment sequence (stop bridge, run migration, deploy code, start bridge) is documented but not automated. If someone pulls the code without running the migration, the model will reference `parent_session_id` but Redis still has `parent_chat_session_id`. There is no guard or version check.
- **Suggestion**: Add a startup check (e.g., in `AgentSession.__init_subclass__` or a health check) that warns if old field names are detected in Redis. Alternatively, document this risk prominently in the PR description.

### Nits

#### 7. `create_child()` role validation scope unclear
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Failure Path Test Strategy
- **Finding**: The plan says `create_child()` validates `role` parameter is a "known value or None" but does not define the known values. If the only known values are "pm" and "dev", and new roles are explicitly out of scope, the validation is trivially simple but should be documented.
- **Suggestion**: Add a `KNOWN_ROLES` constant (e.g., `{"pm", "dev"}`) referenced by the validation, or accept any string and document that validation is deferred.

#### 8. Task 6 documentation references may not need updates
- **Severity**: NIT
- **Critics**: User
- **Location**: Task 6 (document-feature)
- **Finding**: Task 6 lists `docs/features/session-isolation.md` as a documentation target, and the Documentation section says to update it "if it references `parent_chat_session_id`". This conditional should be resolved before build to avoid wasted effort.
- **Suggestion**: Grep `session-isolation.md` now and either confirm it needs updates or remove it from the task list.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-7, sequential, no gaps |
| Dependencies valid | PASS | All Depends On references point to valid task IDs |
| File paths exist | PASS | All referenced source files exist (12/12 Python files, 4/4 doc files) |
| Prerequisites met | PASS | No prerequisites listed (uses existing infrastructure) |
| Cross-references | PASS | All success criteria map to tasks; no No-Gos appear in Solution; Rabbit Holes are excluded from tasks |

### Verdict

**NEEDS REVISION** -- 2 blockers must be resolved before build:
1. Add missing callers (`bridge/response.py`, `agent/hooks/stop.py`) to Task 3
2. Add missing test files (`test_delivery_execution.py`, `test_pre_tool_use_start_stage.py`) to Test Impact and correct `test_model_relationships.py` rationale

---

## Open Questions

None — all questions resolved via spikes. Key decisions:
- **Resolved:** `parent_session_id` (not `parent_id`) to maintain consistency with `parent_agent_session_id`
- **Resolved:** `role` as DataField (not KeyField) per spike-2 finding
- **Resolved:** Docstring updates scoped to model/factory only per spike-3 finding
