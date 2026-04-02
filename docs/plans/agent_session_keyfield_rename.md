---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/631
last_comment_id:
---

# Rename AgentSession KeyFields and Generalize Parent-Child Model

## Problem

Three naming issues on the AgentSession model need to be resolved together:

1. **Legacy "job" vocabulary**: The `job_id` AutoKeyField and `parent_job_id` KeyField are the last remnants of "job" vocabulary after #608. Property aliases (`agent_session_id`, `id`) paper over the inconsistency.

2. **Misleading `parent_chat_session_id`**: This field implies the parent is always a ChatSession, but there is no `ChatSession` class — it's all `AgentSession` with different `session_type` values. With role-based session spawning (see below), the parent can be any role.

3. **No role field**: Sessions are spawned with a `session_type` (chat/dev) but no explicit `role`. We need a `role` field to support general role-based spawning (dev, pm, documentarian, scrum-master, etc.) and a `create_child()` factory that accepts role as a parameter.

**Current behavior:**
Redis key pattern (fields sorted alphabetically by name):
```
AgentSession:{chat_id}:{job_id}:{parent_chat_session_id}:{parent_job_id}:{project_key}:{session_type}
```

Callers use `session.agent_session_id` or `session.id` which are property aliases for `job_id`. The model has backward-compat shims in `__init__` and `_normalize_kwargs` to translate between names. `create_dev()` hardcodes `session_type=SESSION_TYPE_DEV`. `parent_chat_session_id` implies a ChatSession parent.

**Desired outcome:**
- `job_id` renamed to `id` as the actual AutoKeyField
- `parent_job_id` renamed to `parent_agent_session_id` as the actual KeyField
- `parent_chat_session_id` renamed to `parent_session_id` as the actual KeyField
- New `role` hash field added (not a KeyField — no key structure impact)
- `create_dev()` generalized to `create_child(role="dev", ...)` accepting any role
- Redis keys migrated in-place without data loss
- Property aliases and backward-compat shims removed (no longer needed)
- All "DevSession"/"ChatSession" references in docstrings updated to "AgentSession with role=X"
- All callers updated to use the new field names directly

## Prior Art

- **#608** (closed, PR #616 merged 2026-03-31): Renamed all "job" terminology to "agent_session" across codebase. Deliberately left `job_id` and `parent_job_id` unchanged due to Redis key migration complexity.
- **#609** (closed, PR #628 merged 2026-04-01): AgentSession field cleanup. Added `id` and `agent_session_id` property aliases but kept `job_id` as the AutoKeyField. Created `scripts/migrate_agent_session_fields.py` for hash field renames (not key renames).
- **#295** (closed, PR #392 merged 2026-03-13): Strengthened Popoto model relationships and naming. Earlier cleanup pass.

## Spike Results

### spike-1: Key position analysis after rename
- **Assumption**: "Renaming job_id to id, parent_job_id to parent_agent_session_id, and parent_chat_session_id to parent_session_id changes key segment positions"
- **Method**: code-read
- **Finding**: Current alphabetical order: `chat_id`(1), `job_id`(2), `parent_chat_session_id`(3), `parent_job_id`(4), `project_key`(5), `session_type`(6). New order: `chat_id`(1), `id`(2), `parent_agent_session_id`(3), `parent_session_id`(4), `project_key`(5), `session_type`(6). Positions 2-4 are all renamed but stay in the same positions (alphabetical order is preserved: `id` < `parent_agent_session_id` < `parent_session_id`).
- **Confidence**: high
- **Impact on plan**: The migration script renames all three segments in-place. No position swaps needed — the alphabetical order is preserved.

### spike-2: Popoto RENAME + rebuild_indexes pattern
- **Assumption**: "Popoto's Recipe 7/8 SCAN+RENAME pattern works for KeyField renames"
- **Method**: code-read of `popoto/models/migrations.py`
- **Finding**: Recipe 7 (Add a KeyField) provides exact pattern: SCAN old keys, construct new key, `pipeline.rename(old, new)`, update `$Class:AgentSession` set, then `rebuild_indexes()`. No dedicated "rename KeyField" recipe exists, but the mechanics are identical. The `rebuild_indexes()` method clears all secondary indexes and rebuilds from a SCAN of `AgentSession:*`, so it handles index drift automatically.
- **Confidence**: high
- **Impact on plan**: Use Recipe 7 pattern directly. The existing `scripts/migrate_agent_session_fields.py` can be extended or replaced.

### spike-3: Cross-reference safety
- **Assumption**: "Other models storing job_id UUIDs as foreign keys don't need migration"
- **Method**: code-read
- **Finding**: Confirmed. Fields like `TelegramMessage.agent_session_id` and `parent_chat_session_id` store the UUID value, not the field name. The UUID values are preserved during key rename -- only the Redis key structure changes. No cross-reference migration needed.
- **Confidence**: high
- **Impact on plan**: No additional model migrations required.

### spike-4: parent_chat_session_id usage scope
- **Assumption**: "parent_chat_session_id is used widely and renaming it has significant blast radius"
- **Method**: grep across codebase
- **Finding**: 64 occurrences across 22 files (7 in model, 5 in agent/hooks, 12 in tests, rest in docs/plans). All usages store UUID values. No external systems depend on the field name.
- **Confidence**: high
- **Impact on plan**: Same migration pattern as the other renames. Docs need updating but are mechanical.

## Data Flow

1. **Migration script**: SCAN `AgentSession:*` Redis keys -> parse 6 colon-separated segments -> rename segments 2-4 in place -> `RENAME` old key to new key -> update `$Class:AgentSession` set
2. **Hash field rename**: For each renamed key, `HSET` new field names (`id`, `parent_agent_session_id`, `parent_session_id`) with old values, `HDEL` old field names (`job_id`, `parent_job_id`, `parent_chat_session_id`). Add `role` hash field (default `"dev"` for existing sessions with `session_type=dev`, `"pm"` for `session_type=chat`).
3. **Index rebuild**: `AgentSession.rebuild_indexes()` clears all sorted sets, field indexes, and class set, then re-scans and re-indexes
4. **Model update**: Change field declarations, add `role` DataField, remove property aliases, remove `_normalize_kwargs` shims. Rename `create_dev()` to `create_child(role=...)`.
5. **Caller update**: Replace all `job_id` with `id`, `parent_job_id` with `parent_agent_session_id`, `parent_chat_session_id` with `parent_session_id`, `create_dev()` with `create_child()` across ~22 files

## Architectural Impact

- **New dependencies**: None
- **Interface changes**:
  - `AgentSession.job_id` -> `AgentSession.id`
  - `AgentSession.parent_job_id` -> `AgentSession.parent_agent_session_id`
  - `AgentSession.parent_chat_session_id` -> `AgentSession.parent_session_id`
  - `AgentSession.create_dev()` -> `AgentSession.create_child(role=...)`
  - New `AgentSession.role` DataField (not a KeyField)
  - Property aliases removed
- **Coupling**: Decreases -- removes indirection layer of property aliases, removes false implication that parent is always a ChatSession
- **Data ownership**: No change -- AgentSession continues to own its identity
- **Reversibility**: Medium -- would require another migration script to reverse the key rename, but the pattern is symmetric

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (straightforward migration)
- Review rounds: 1 (validate migration safety)

## Prerequisites

No prerequisites -- this work uses existing Redis infrastructure and Popoto ORM capabilities.

## Solution

### Key Elements

- **Migration script**: SCAN + RENAME pattern from Popoto Recipe 7, handles key restructuring and hash field renames in a single pass
- **Model field renames**: `job_id` → `id` (AutoKeyField), `parent_job_id` → `parent_agent_session_id` (KeyField), `parent_chat_session_id` → `parent_session_id` (KeyField)
- **New role field**: `role` as a DataField (not KeyField) — stores the spawning role (dev, pm, documentarian, etc.)
- **Factory generalization**: `create_dev()` → `create_child(role=...)` accepting any role string
- **Alias cleanup**: Remove property aliases (`agent_session_id`, `id` properties) and `_normalize_kwargs` backward-compat shims
- **Caller updates**: Mechanical find-and-replace across ~22 files

### Flow

**Run migration script** (dry-run first) → **Validate key structure** → **Update model fields** → **Update callers** → **Run tests** → **Deploy with migration**

### Technical Approach

- Write a new migration script `scripts/migrate_agent_session_keyfield_rename.py` that:
  1. SCANs all `AgentSession:*` keys (excluding index keys)
  2. Parses each key into segments
  3. Renames segments 2-4 in place (`job_id` → `id`, `parent_chat_session_id` → `parent_session_id`, `parent_job_id` → `parent_agent_session_id`)
  4. Uses `pipeline.rename()` for atomic key rename
  5. Updates `$Class:AgentSession` set membership
  6. Renames hash fields inside each record (`job_id` → `id`, `parent_job_id` → `parent_agent_session_id`, `parent_chat_session_id` → `parent_session_id`)
  7. Adds `role` hash field: `"pm"` if `session_type=chat`, `"dev"` otherwise
  8. Calls `AgentSession.rebuild_indexes()` after all renames
- The migration is idempotent: re-running on already-migrated data is a no-op (keys already match new pattern). Detection: if the key already contains `parent_session_id` in position 4, skip it.
- Dry-run mode logs what would change without modifying Redis
- Supports `--reverse` flag to undo the migration for rollback scenarios
- Tracks progress: logs each key as it's processed, so partial failures can be diagnosed and resumed

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Migration script must handle partial failures (some keys renamed, others not) by tracking progress and supporting resume
- [ ] If `RENAME` fails for a key (e.g., target already exists), log and continue rather than aborting

### Empty/Invalid Input Handling
- [ ] Migration handles empty Redis (zero AgentSession records) gracefully
- [ ] Migration handles keys with None/empty segments (null KeyField values)

### Error State Rendering
- [ ] Migration reports clear summary: total records, migrated, skipped, errors

## Test Impact

- [ ] `tests/unit/test_agent_session_hierarchy.py` — UPDATE: replace `parent_job_id` with `parent_agent_session_id`, `job_id` with `id`
- [ ] `tests/unit/test_agent_session_scheduler_kill.py` — UPDATE: replace `parent_job_id` with `parent_agent_session_id`
- [ ] `tests/unit/test_model_relationships.py` — UPDATE: replace `job_id` with `id`, update `_AGENT_SESSION_FIELDS` for all three renames
- [ ] `tests/integration/test_agent_session_scheduler.py` — UPDATE: replace `job_id` and `parent_job_id`
- [ ] `tests/unit/test_dev_session_registration.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`, `create_dev` with `create_child`
- [ ] `tests/unit/test_steer_child.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`
- [ ] `tests/unit/test_summarizer.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`
- [ ] `tests/unit/test_chat_session_factory.py` — UPDATE: replace `create_dev` with `create_child`
- [ ] `tests/e2e/test_session_spawning.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`, `create_dev` with `create_child`
- [ ] `tests/e2e/test_context_propagation.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`
- [ ] `tests/integration/test_agent_session_queue_session_type.py` — UPDATE: replace `parent_chat_session_id` with `parent_session_id`, `create_dev` with `create_child`

## Rabbit Holes

- **Renaming the model class**: `AgentSession` is the correct name. Don't rename it.
- **Adding a Popoto migration framework**: Just write a standalone script. Don't build a generic migration system.
- **Updating the existing `scripts/migrate_agent_session_fields.py`**: That script served a different purpose (hash field renames for #609). Write a new script for the structural key migration.
- **Creating DevSession/PMSession subclasses or aliases**: There is one model — `AgentSession`. Role is a field, not a class. No sugar.
- **Making `role` a KeyField**: Role is observability metadata, not identity. Keep it as a DataField to avoid key structure changes when adding new roles.

## Risks

### Risk 1: Partial migration leaves Redis in inconsistent state
**Impact:** Some keys use old pattern, some use new. Popoto can't find records using old keys after model is updated.
**Mitigation:** Migration script uses Redis pipeline for atomicity within batches. Dry-run mode validates before applying. Model update is deployed only after migration completes successfully.

### Risk 2: Running bridge during migration causes key conflicts
**Impact:** New sessions created during migration use new pattern, old sessions still have old pattern.
**Mitigation:** Stop the bridge before running migration. Migration is fast (pipeline batches of 500). Restart bridge after migration + model update.

## Race Conditions

### Race 1: Bridge creates new session during migration
**Location:** Migration script + bridge session creation
**Trigger:** Bridge creates a new AgentSession while migration is renaming keys
**Data prerequisite:** Migration must complete before model code is updated
**State prerequisite:** Bridge must be stopped during migration
**Mitigation:** Deployment sequence: stop bridge -> run migration -> deploy code with new field names -> start bridge. This eliminates the race entirely.

## No-Gos (Out of Scope)

- Building a generic Popoto migration framework
- Modifying the existing `migrate_agent_session_fields.py` script
- Changing any UUID values (only field names and key structure change)
- Multi-machine migration coordination (single Redis instance, single migration run)
- Building agent definitions for new roles (pm-session, etc.) — that's a follow-up; this plan only adds the model foundation
- Hook changes to support role-based spawning — follow-up work once model is clean

## Update System

The migration script must run on the production machine before the code update deploys. Update sequence:
1. Stop bridge (`./scripts/valor-service.sh stop`)
2. Pull new code (`git pull`)
3. Run migration (`python scripts/migrate_agent_session_keyfield_rename.py`)
4. Restart bridge (`./scripts/valor-service.sh restart`)

The update skill (`scripts/remote-update.sh`) should be checked to see if it needs a post-pull migration hook. If not, the migration can be run manually as a one-time operation.

## Agent Integration

No agent integration required -- this is a model-internal rename. The agent interacts with AgentSession through the queue and scheduler, which will be updated as part of the caller changes. No MCP server or bridge changes needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/README.md` if it references `job_id` field names
- [ ] No new feature doc needed -- this is a rename, not a new feature

### Inline Documentation
- [ ] Update docstrings in `models/agent_session.py` that reference `job_id`
- [ ] Update comments in `agent/agent_session_queue.py` and `tools/agent_session_scheduler.py`

## Success Criteria

- [ ] `job_id` field renamed to `id` (AutoKeyField) in model
- [ ] `parent_job_id` field renamed to `parent_agent_session_id` (KeyField) in model
- [ ] `parent_chat_session_id` field renamed to `parent_session_id` (KeyField) in model
- [ ] New `role` DataField added to model
- [ ] `create_dev()` replaced with `create_child(role=...)` factory method
- [ ] All property aliases (`agent_session_id`, `id` properties) removed
- [ ] Migration script successfully renames all existing Redis keys and backfills `role` field (dry-run validates)
- [ ] Zero references to `job_id`, `parent_job_id`, or `parent_chat_session_id` remain in Python files (excluding migration script)
- [ ] Zero references to `create_dev` remain in Python files (excluding migration script)
- [ ] All "DevSession"/"ChatSession" references in docstrings say "AgentSession with role=X" instead
- [ ] All tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (migration)**
  - Name: migration-builder
  - Role: Write migration script and update model + callers
  - Agent Type: builder
  - Resume: true

- **Validator (migration)**
  - Name: migration-validator
  - Role: Verify migration script correctness and test coverage
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Write migration script
- **Task ID**: build-migration-script
- **Depends On**: none
- **Validates**: `scripts/migrate_agent_session_keyfield_rename.py` runs without error in dry-run mode
- **Informed By**: spike-1 (key position swap), spike-2 (Popoto RENAME pattern)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/migrate_agent_session_keyfield_rename.py` with SCAN + RENAME + rebuild_indexes pattern
- Support `--dry-run` flag
- Handle idempotency (skip keys already in new format)
- Handle null KeyField segments

### 2. Update model fields and remove aliases
- **Task ID**: build-model-update
- **Depends On**: build-migration-script
- **Validates**: `tests/unit/test_model_relationships.py`, `tests/unit/test_agent_session_hierarchy.py`
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename `job_id` to `id` (AutoKeyField) in `models/agent_session.py`
- Rename `parent_job_id` to `parent_agent_session_id` (KeyField) in `models/agent_session.py`
- Rename `parent_chat_session_id` to `parent_session_id` (KeyField) in `models/agent_session.py`
- Add `role` as a DataField (string, default `"dev"`)
- Rename `create_dev()` to `create_child(role="dev", ...)` — accept role as parameter, map role to session_type internally
- Remove `agent_session_id` property and `id` property aliases
- Remove `_normalize_kwargs` shims for `parent_agent_session_id`
- Update all "DevSession"/"ChatSession" references in model docstrings to "AgentSession with role=X"
- Update all internal references within the model file

### 3. Update callers
- **Task ID**: build-caller-update
- **Depends On**: build-model-update
- **Validates**: `tests/unit/test_agent_session_scheduler_kill.py`, `tests/integration/test_agent_session_scheduler.py`
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/agent_session_queue.py`: replace `job_id` with `id`, `parent_job_id` with `parent_agent_session_id`, `parent_chat_session_id` with `parent_session_id`
- Update `agent/hooks/pre_tool_use.py`: replace `parent_chat_session_id` with `parent_session_id`, rename `_maybe_register_dev_session` to `_maybe_register_child_session`, replace `create_dev` with `create_child`
- Update `agent/hooks/subagent_stop.py`: replace `parent_chat_session_id` with `parent_session_id`
- Update `scripts/steer_child.py`: replace `parent_chat_session_id` with `parent_session_id`
- Update `tools/agent_session_scheduler.py`: replace `job_id` → `id`, `parent_job_id` → `parent_agent_session_id` (including function parameters)
- Update `_AGENT_SESSION_FIELDS` list if it references old names
- Update all local variable names that refer to the AgentSession identifier (e.g., `job_id = ...` → `agent_session_id = ...`)
- Update all 11 test files with new field names (see Test Impact section)
- Grep entire project for `job_id`, `parent_job_id`, `parent_chat_session_id`, `create_dev` to catch straggling references

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-caller-update
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` to verify all unit tests pass
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify zero remaining `job_id` or `parent_job_id` references in Python files (excluding migration script and git history)
- Verify migration script dry-run completes without error

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale job_id refs | `grep -rn 'job_id' --include='*.py' . \| grep -v migrate_agent_session \| grep -v '# legacy'` | exit code 1 |
| No stale parent_chat refs | `grep -rn 'parent_chat_session_id' --include='*.py' . \| grep -v migrate_agent_session` | exit code 1 |
| No stale create_dev refs | `grep -rn 'create_dev' --include='*.py' . \| grep -v migrate_agent_session` | exit code 1 |
| Migration dry-run | `python scripts/migrate_agent_session_keyfield_rename.py --dry-run` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — all questions resolved.

- **Resolved:** Field named `id` (not `agent_session_id`) per owner decision on 2026-04-02.
- **Resolved:** `parent_chat_session_id` → `parent_session_id` folded into this plan per owner decision on 2026-04-02. Parent can be any role, not just "chat".
- **Resolved:** `role` is a DataField (not KeyField) — adding new roles should never require key migration.
- **Resolved:** No DevSession/PMSession sugar classes — one model (`AgentSession`), role is a field.
