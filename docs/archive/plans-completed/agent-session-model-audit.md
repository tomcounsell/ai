---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/592
last_comment_id:
---

# AgentSession Model Audit: Fix Status KeyField Duplicates, Prune Dead Fields

## Problem

**Current behavior:**
AgentSession's `status` field is a `KeyField`, meaning every lifecycle transition (pending -> running -> completed) creates a new Redis record instead of updating in place. A single local Claude Code session produces 3 separate Redis records. The dashboard shows duplicate session cards for the same work.

The bridge's `job_queue.py` works around this with a delete-and-recreate pattern (~100 lines of complexity), but 7 other call sites mutate `status` directly via `.status = X; .save()`, silently creating orphan records:
- `.claude/hooks/user_prompt_submit.py:60-63` -- local session resume
- `.claude/hooks/stop.py:155-160` -- session completion
- `agent/job_queue.py:327-329` -- superseding old sessions
- `monitoring/session_watchdog.py:173` -- marking stale sessions failed
- `monitoring/session_watchdog.py:566` -- marking sessions abandoned
- `bridge/session_transcript.py:290+` -- transcript completion (uses delete-and-recreate)
- `bridge/session_transcript.py:92` -- mutates `chat_id` (also a KeyField)

Additionally, 3 fields are confirmed dead: `retry_count`, `last_stall_reason`, `artifacts`.

**Desired outcome:**
- `status` is a regular `Field`, not a `KeyField` -- status transitions update in place
- Dead fields removed, reducing model surface area
- No remaining call sites that mutate a KeyField and call `.save()` -- all KeyFields are immutable after creation
- Dashboard shows exactly one record per session
- Delete-and-recreate complexity in `job_queue.py` reduced where `status` was the only reason

## Prior Art

- **[PR #505](https://github.com/tomcounsell/ai/pull/505)**: AgentSession field cleanup Phase 1 -- removed dead fields, renamed for clarity. Successfully merged 2026-03-24. This plan is the continuation (Phase 2+).
- **[PR #490](https://github.com/tomcounsell/ai/pull/490)**: Consolidated SDLC stage tracking, removed legacy fields. Merged 2026-03-24. Established the pattern of field consolidation.
- **[Issue #210](https://github.com/tomcounsell/ai/issues/210)**: `complete_transcript()` drops fields on status change (delete-and-recreate). Closed 2026-02-28. First identification of the KeyField mutation problem.
- **[Issue #543](https://github.com/tomcounsell/ai/issues/543)**: Worker loop exits without picking up pending jobs for same chat_id. Closed 2026-03-26. Related to KeyField index corruption from status mutations.
- **[PR #316](https://github.com/tomcounsell/ai/pull/316)**: Stall detection and automatic retry. Merged 2026-03-09. Added `retry_count` and `last_stall_reason` fields that are now dead.

## Architectural Impact

- **Interface changes**: `status` changes from `KeyField` to `Field`. All `.filter(status=X)` queries will stop working because Popoto only supports `.filter()` on KeyField/IndexField. These queries must be rewritten as `.filter()` + list comprehension, or `status` must become an `IndexField` instead.
- **Coupling**: Decreases coupling -- removing the delete-and-recreate pattern eliminates the tight coupling between `_JOB_FIELDS` list and the model definition.
- **Data ownership**: No change.
- **Reversibility**: Low risk -- field type changes in Popoto take effect immediately. Existing records with old key structures become orphans and should be flushed (short-lived data).

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by audit)
- Review rounds: 1 (final validation)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto >= 1.4.3 | `python -c "import popoto; v = getattr(popoto, '__version__', '0'); parts = v.split('.'); assert int(parts[0]) >= 1 and int(parts[1]) >= 4 and int(parts[2]) >= 3, f'Need 1.4.3+, got {v}'"` | Latest KeyField/IndexField updates |

## Solution

### Key Elements

- **Part 0: Popoto upgrade** -- Upgrade popoto from 1.4.2 to 1.4.3 to get latest KeyField/IndexField improvements before changing field types
- **Part 1: Fix status field** -- Change `status` from `KeyField` to `Field` (or `IndexField` if filter queries need it), simplify delete-and-recreate where status was the only reason
- **Part 2: Remove dead fields** -- Delete `retry_count`, `last_stall_reason`, `artifacts` from model and `_JOB_FIELDS`
- **Part 3: KeyField mutation audit** -- Ensure no remaining code mutates a KeyField after record creation

### Flow

**Part 0** (prerequisite upgrade) -> **Part 1** (status field fix + simplification) -> **Part 2** (dead field removal) -> **Part 3** (KeyField audit + remaining mutations)

Each part is a separate commit. Part 1 requires flushing existing AgentSession records. Parts are sequential because Part 1 determines whether delete-and-recreate is still needed for Part 3.

### Technical Approach

- **Part 0**: Bump `popoto>=1.4.3` in `pyproject.toml`, run `pip install -e .`, verify import works
- **Part 1**: Change `status = KeyField(default="pending")` to `status = Field(default="pending")`. This means `.filter(status=X)` calls will no longer work -- audit all filter calls and decide: use `IndexField` (if popoto 1.4.3 supports it for this use case) or rewrite as post-filter. Simplify `_pop_job()` and other delete-and-recreate sites where status was the only KeyField being changed.
- **Part 2**: Remove 3 dead fields from `models/agent_session.py` and their entries in `_JOB_FIELDS`. Remove `revival_context` from `_JOB_FIELDS` comment about "Stall retry fields" since it belongs with the queue fields.
- **Part 3**: Audit remaining KeyField mutations: `chat_id` mutation in `session_transcript.py:92`, and verify `parent_chat_session_id`, `parent_job_id`, `stable_job_id` KeyFields are never mutated after creation.

**Important correction from recon**: The issue lists `revival_context` as dead, but it is actively used by the revival system (`job_queue.py:2606` writes it via `enqueue_job`, and `RedisJob.revival_context` property reads it). It must NOT be removed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_pop_job()` delete-and-recreate has try/except around delete -- if simplified, verify the new simpler path still handles save failures gracefully
- [ ] `session_watchdog.py:173` catches `ModelException` after `.save()` -- verify this still works after status becomes a regular Field
- [ ] `stop.py:155-160` has `except Exception: pass` -- no change needed (already silent)

### Empty/Invalid Input Handling
- [ ] Verify `status=None` does not break queries or dashboard rendering after field type change
- [ ] Verify `AgentSession.query.filter()` without status still returns results correctly

### Error State Rendering
- [ ] Dashboard must show exactly one card per session after the fix (visual verification)
- [ ] No duplicate entries in `job_scheduler list` output

## Test Impact

- [ ] `tests/e2e/test_session_lifecycle.py` -- UPDATE: tests mutate `session.status` and call `.save()`, then reload via `.filter()`. After status becomes a non-KeyField, the `.filter(status=X)` calls may need updating depending on whether IndexField is used.
- [ ] `tests/e2e/test_error_boundaries.py` -- UPDATE: same pattern -- mutates status, saves, filters by status. Multiple test cases affected.
- [ ] `tests/integration/test_job_queue_race.py` -- UPDATE: references `revival_context` in `_JOB_FIELDS` assertions and creates jobs with `revival_context`. Remove references to `retry_count`/`last_stall_reason` from any field list assertions.
- [ ] `tests/integration/test_agent_session_lifecycle.py` -- UPDATE: likely references dead fields or status filter patterns.
- [ ] `tests/unit/test_stop_hook.py` -- UPDATE: tests the stop hook which mutates status.
- [ ] `tests/unit/test_summarizer.py` -- REVIEW: check if it references `artifacts` field on AgentSession.
- [ ] `tests/unit/test_context_modes.py` -- NO CHANGE: `artifacts` references are on `ContextRequest`, not `AgentSession`.

## Rabbit Holes

- **Making ALL KeyFields into regular Fields** -- Only `status` is confirmed problematic. Other KeyFields (`session_type`, `project_key`, `chat_id`) are legitimately used for compound key construction and `.filter()` queries. Do not change them.
- **Building a generic migration framework for Popoto** -- Popoto does not support schema migrations by design. A flush-and-recreate approach is appropriate for short-lived session data.
- **Refactoring the entire delete-and-recreate pattern** -- Some uses of delete-and-recreate are needed for other KeyField changes (e.g., `chat_id` mutation). Only simplify where `status` was the sole reason.
- **Removing `revival_context`** -- The issue incorrectly flags this as dead. It is actively used by the revival system. Do not remove.

## Risks

### Risk 1: `.filter(status=X)` queries break silently
**Impact:** Queries that filter by status return empty results, causing jobs to never be picked up or dashboard to show no sessions.
**Mitigation:** Audit every `.filter()` call that includes `status=`. If popoto 1.4.3 supports `IndexField` for non-key filtered queries, use that. Otherwise, rewrite as `.filter(project_key=X)` + list comprehension filter on status. Test each query path.

### Risk 2: Orphaned Redis records from old key structure
**Impact:** Old records with status in the compound key persist in Redis, wasting memory and confusing dashboard.
**Mitigation:** Flush all AgentSession records after deploying the change. Session data is short-lived and reconstructed on next use. Document the flush step in deployment instructions.

### Risk 3: Delete-and-recreate still needed for non-status KeyField changes
**Impact:** Over-simplifying delete-and-recreate removes protection for `chat_id`, `parent_job_id`, or other KeyField mutations.
**Mitigation:** Only remove delete-and-recreate logic at sites where status was the sole KeyField being changed. Keep the pattern intact for sites that change other KeyFields (e.g., `session_transcript.py` chat_id mutation). Add code comments documenting WHY delete-and-recreate is still needed at each remaining site.

## Race Conditions

### Race 1: Concurrent status transitions during simplification
**Location:** `agent/job_queue.py:668-704` (_pop_job)
**Trigger:** Two workers pop the same job simultaneously. With delete-and-recreate, the second delete fails (record already gone). With simple field mutation, both could succeed and create conflicting states.
**Data prerequisite:** Job must exist in pending state.
**State prerequisite:** Only one worker should process a given chat_id at a time.
**Mitigation:** The existing chat_id-scoped worker loop already serializes job processing per chat. The `_pop_job` function is called from a single async context per chat_id. After simplification, the race window shrinks (no gap between delete and create), making this safer.

## No-Gos (Out of Scope)

- Changing `session_type`, `project_key`, or `chat_id` KeyField types -- these are legitimate compound key components
- Renaming any fields (covered by the superseded Phase 1 plan)
- Building a Popoto migration framework
- Removing `revival_context` (actively used despite issue claiming it is dead)
- Addressing `classification_confidence` deprecation (tracked in issue #562)
- Restructuring AgentSession into separate PM/Dev session Popoto models

## Update System

The `/update` skill must include a popoto upgrade step and a Redis flush step for one release cycle:

```bash
git pull && pip install -e .                    # 1. Pull code + upgrade popoto to 1.4.3
python -c "
from models.agent_session import AgentSession
deleted = 0
for s in AgentSession.query.all():
    s.delete()
    deleted += 1
print(f'Flushed {deleted} AgentSession records')
"                                                # 2. Flush old records with stale key structure
./scripts/valor-service.sh restart              # 3. Restart bridge with new field types
```

No bridge stop required before pull -- the field type change is backward-compatible (old code can still write, new code reads correctly). The flush clears orphaned records from the old compound key structure.

## Agent Integration

No agent integration required -- AgentSession is not exposed through any MCP server. The agent interacts with sessions through the bridge and job queue, which will use the updated field types after code deployment.

## Documentation

- [ ] Update `docs/features/redis-models.md` -- document that `status` is now a regular Field, not a KeyField
- [ ] Update inline comments in `models/agent_session.py` -- remove stall retry field group header after dead field removal
- [ ] Update inline comments in `agent/job_queue.py` -- simplify delete-and-recreate documentation to note which sites still need it and why
- [ ] Add entry to `docs/features/README.md` index if `redis-models.md` is new

## Success Criteria

- [ ] `status` is a `Field` (not `KeyField`) on AgentSession
- [ ] Zero duplicate AgentSession records after a full session lifecycle (pending -> running -> completed)
- [ ] Dead fields removed: `retry_count`, `last_stall_reason`, `artifacts`
- [ ] No code path mutates a KeyField value after record creation
- [ ] Delete-and-recreate pattern in `job_queue.py` simplified where `status` was the only reason for the workaround
- [ ] Dashboard shows exactly one card per session
- [ ] Popoto version >= 1.4.3 in `pyproject.toml`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (popoto-upgrade)**
  - Name: popoto-upgrader
  - Role: Upgrade popoto dependency to 1.4.3, verify compatibility
  - Agent Type: builder
  - Resume: true

- **Builder (status-field-fix)**
  - Name: status-field-builder
  - Role: Change status from KeyField to Field, simplify delete-and-recreate, fix all mutation sites
  - Agent Type: builder
  - Resume: true

- **Builder (dead-field-removal)**
  - Name: dead-field-builder
  - Role: Remove retry_count, last_stall_reason, artifacts from model and _JOB_FIELDS
  - Agent Type: builder
  - Resume: true

- **Builder (keyfield-audit)**
  - Name: keyfield-auditor
  - Role: Audit all remaining KeyField mutation sites, fix chat_id mutation in session_transcript.py
  - Agent Type: builder
  - Resume: true

- **Validator (all-parts)**
  - Name: field-validator
  - Role: Verify no stale references, all tests pass, no duplicate records
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update redis-models.md and inline comments
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Part 0 -- Upgrade Popoto to 1.4.3
- **Task ID**: build-popoto-upgrade
- **Depends On**: none
- **Validates**: `python -c "import popoto; print(popoto.__version__)"` shows >= 1.4.3
- **Assigned To**: popoto-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Bump `popoto>=1.4.3` in `pyproject.toml`
- Run `pip install -e .` to install the upgrade
- Run `pytest tests/unit/ -x -q` to verify no regressions from the upgrade
- Commit: "Upgrade popoto to 1.4.3 for latest KeyField/IndexField updates"

### 2. Part 1 -- Fix Status KeyField
- **Task ID**: build-status-field
- **Depends On**: build-popoto-upgrade
- **Validates**: `pytest tests/e2e/test_session_lifecycle.py tests/e2e/test_error_boundaries.py tests/unit/test_stop_hook.py -x`
- **Assigned To**: status-field-builder
- **Agent Type**: builder
- **Parallel**: false
- Change `status = KeyField(default="pending")` to `status = Field(default="pending")` in `models/agent_session.py`
- Audit all `.filter(status=X)` calls across the codebase -- rewrite as post-filter or use IndexField if supported
- Simplify `_pop_job()` in `job_queue.py`: replace delete-and-recreate with direct field mutation + `.save()` for status changes
- Simplify other delete-and-recreate sites in `job_queue.py` where status was the only KeyField being changed (lines ~800, ~842, ~1139, ~1193, ~1252, ~1383, ~1893)
- Remove `status` from `_JOB_FIELDS` list (no longer needed for delete-and-recreate of status)
- Fix direct mutation sites: `user_prompt_submit.py`, `stop.py`, `session_watchdog.py` (these now work correctly without delete-and-recreate)
- Fix `session_transcript.py:290+` -- simplify the status change to direct mutation
- Update affected tests to use new query patterns
- Commit: "Change AgentSession status from KeyField to Field, simplify delete-and-recreate"

### 3. Validate Part 1
- **Task ID**: validate-status-field
- **Depends On**: build-status-field
- **Assigned To**: field-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -n 'KeyField' models/agent_session.py` does not show `status`
- No `.filter(status=` calls remain that would fail on a non-KeyField (unless using IndexField)
- `pytest tests/ -x -q` passes
- `python -m ruff check .` passes

### 4. Part 2 -- Remove Dead Fields
- **Task ID**: build-dead-fields
- **Depends On**: validate-status-field
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: dead-field-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `retry_count` field from `models/agent_session.py`
- Remove `last_stall_reason` field from `models/agent_session.py`
- Remove `artifacts` field from `models/agent_session.py`
- Remove `retry_count`, `last_stall_reason`, `artifacts` from `_JOB_FIELDS` in `agent/job_queue.py`
- Remove the "Stall retry fields" comment group from `_JOB_FIELDS` (but keep `revival_context` -- move it to queue fields group)
- Update any tests that reference these dead fields
- Commit: "Remove dead fields: retry_count, last_stall_reason, artifacts"

### 5. Part 3 -- KeyField Mutation Audit
- **Task ID**: build-keyfield-audit
- **Depends On**: build-dead-fields
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: keyfield-auditor
- **Agent Type**: builder
- **Parallel**: false
- Audit `session_transcript.py:92` -- `chat_id` is a KeyField being mutated via `.save()`. Convert to delete-and-recreate or determine if chat_id should become a regular Field
- Verify `parent_chat_session_id`, `parent_job_id`, `stable_job_id` KeyFields are never mutated after creation (read-only after construction)
- Verify `session_type` and `project_key` KeyFields are never mutated after creation
- Add code comments to remaining delete-and-recreate sites documenting which KeyField change necessitates the pattern
- Commit: "Audit and fix remaining KeyField mutations"

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-keyfield-audit
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/redis-models.md` with status field type change
- Update inline comments in `models/agent_session.py`
- Update inline comments in `agent/job_queue.py`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: field-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands from Verification table
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Status is not KeyField | `grep -n 'status.*=.*KeyField' models/agent_session.py` | exit code 1 |
| No retry_count | `grep -rn 'retry_count' models/agent_session.py agent/job_queue.py` | exit code 1 |
| No last_stall_reason | `grep -rn 'last_stall_reason' models/agent_session.py agent/job_queue.py` | exit code 1 |
| No artifacts on model | `grep -n 'artifacts.*=.*Field' models/agent_session.py` | exit code 1 |
| Popoto version | `python -c "import popoto; v=popoto.__version__.split('.'); assert int(v[2])>=3"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. Should `status` become an `IndexField` (to preserve `.filter(status=X)` queries) or a plain `Field` (requiring post-filter list comprehensions)? This depends on whether popoto 1.4.3's IndexField supports the query patterns used in `_pop_job()` and dashboard queries. The builder should investigate during Part 0.
2. The `chat_id` mutation in `session_transcript.py:92` -- should `chat_id` remain a KeyField (requiring delete-and-recreate for mutations) or become a regular Field? This affects compound key structure and all `.filter(chat_id=X)` queries.
