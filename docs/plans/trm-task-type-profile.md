---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/906
last_comment_id:
---

# TRM Registry: TaskTypeProfile for Grove-Style Delegation Maturity

## Problem

The PM session spawns dev sessions with uniform prompt granularity regardless of whether the task type is well-practiced or novel. A bug we've fixed dozens of times gets the same step-by-step scaffolding as a greenfield feature the system has never attempted.

This wastes PM context on over-specification for proven tasks and under-specifies truly new territory where structured guidance would reduce rework. Andy Grove's **Task-Relevant Maturity (TRM)** principle — from *High Output Management* — says the right supervision style depends on the agent's demonstrated familiarity with a *specific task type*, not just global skill level.

**Current behavior:** `load_pm_system_prompt()` builds the same SDLC orchestration instructions for every dev session spawn. No per-task-type history is consulted.

**Desired outcome:** PM consults a `TaskTypeProfile` before spawning a dev session, adjusting prompt granularity based on historical success rate, rework rate, and avg turn count for that task type. Proven task types get objective-only handoff; new or error-prone ones get structured step-by-step guidance.

## Freshness Check

**Baseline commit:** `86ce1ff9620f3dc8367d6195fde1dfdbe9dd7179`
**Issue filed at:** 2026-04-11T13:30:05Z (filed minutes before this plan — same day)
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py` — `extra_context = DictField(null=True)` and `classification_type` property — confirmed, still present as described. No `task_type` IndexedField exists yet.
- `models/session_lifecycle.py` — `finalize_session()` calls `auto_tag_session()` at step 2 — confirmed, still at that location. The natural hook for profile updates.
- `tools/session_tags.py` — `auto_tag_session()` is pattern-based with 6 rules — confirmed. No `task_type` derivation exists yet.
- `agent/sdk_client.py` — PM dev session spawning via `load_pm_system_prompt()` — confirmed. No `TaskTypeProfile` lookup occurs today.

**Cited sibling issues/PRs re-checked:** None cited.

**Commits on main since issue was filed (touching referenced files):**
- `57b16132` fix: close nudge-stomp append_event save bypass (#905) — irrelevant to task_type/profile work
- `30242bc3` feat: PM session child fan-out for multi-issue SDLC (#903) — modifies `sdk_client.py` PM prompt injection; worth verifying no overlap. It adds fan-out instructions to enriched_message, does not change `load_pm_system_prompt()`. No conflict.

**Active plans in `docs/plans/` overlapping this area:** None found.

**Notes:** Issue was filed the same day as this plan. All referenced call sites verified against current main.

## Prior Art

No prior issues or PRs found related to TRM, TaskTypeProfile, or task_type field on AgentSession. Greenfield work.

## Data Flow

Session completion → profile update → PM spawn consultation:

1. **Session completion trigger**: `finalize_session(session, "completed")` is called in `models/session_lifecycle.py`
2. **Auto-tag side effect**: `auto_tag_session(session_id)` derives tags from metadata and transcript — already runs at completion
3. **New: task_type derivation**: `auto_tag_session()` (extended) derives and sets `session.task_type` from `classification_type`, `branch_name`, `slug`, tags
4. **New: profile update**: `update_task_type_profile(session_id)` called after `auto_tag_session()` in `finalize_session()` — reads the session, aggregates metrics into `TaskTypeProfile` for `project_key + task_type`
5. **PM pre-spawn consultation**: Before spawning a dev session, PM system prompt (or enriched message injection in `sdk_client.py`) calls `TaskTypeProfile.get_recommendation(project_key, task_type)` to retrieve `delegation_recommendation`
6. **Prompt adjustment**: If `structured` → include step-by-step SDLC instructions; if `autonomous` → include objective + constraints only

## Architectural Impact

- **New model**: `models/task_type_profile.py` — new Popoto model, keyed by `project_key + task_type`
- **New field on AgentSession**: `task_type = IndexedField(null=True)` — additive, no existing behavior changes
- **New optional field**: `rework_triggered = Field(null=True)` — boolean flag, additive
- **New side effect in `finalize_session()`**: profile update wrapped in try/except, non-blocking
- **PM prompt injection**: Minimal change to `sdk_client.py` enriched message — conditional text based on profile lookup
- **Coupling**: `finalize_session()` gains a soft dependency on `TaskTypeProfile` (lazy import, fail-safe)
- **Reversibility**: All additive. Removing task_type field and profile model leaves system fully functional at prior behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on `task_type` vocabulary)
- Review rounds: 1 (code review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto `FloatField` available | `python -c "from popoto import FloatField; print('ok')"` | `TaskTypeProfile.avg_turns`, `rework_rate` fields |
| Redis accessible | `python -c "from models.agent_session import AgentSession; print('ok')"` | Popoto model operations |

Run all checks: `python scripts/check_prerequisites.py docs/plans/trm-task-type-profile.md`

## Solution

### Key Elements

- **`task_type` IndexedField on `AgentSession`**: Classifies the work category with a defined vocabulary (`sdlc-build`, `sdlc-test`, `sdlc-patch`, `sdlc-plan`, `bug-fix`, `greenfield-feature`, `rework-triggered`). Set by extended `auto_tag_session()` at completion.
- **`rework_triggered` field on `AgentSession`**: Boolean flag set when a session retries prior output. Feeds `rework_rate` in the profile.
- **`TaskTypeProfile` Popoto model**: Aggregates per-task-type performance metrics across sessions. Keyed by `project_key + task_type`. Derives `delegation_recommendation` from `rework_rate` and `session_count`.
- **Profile update hook in `finalize_session()`**: Calls `update_task_type_profile()` after `auto_tag_session()`, wrapped in try/except (never blocks finalization).
- **PM consultation in `sdk_client.py`**: Before the dev session prompt is built, looks up `TaskTypeProfile.delegation_recommendation` for the inferred `task_type` and adjusts the handoff instruction granularity.

### Flow

Session completes → `finalize_session()` → `auto_tag_session()` (sets `task_type`) → `update_task_type_profile()` (aggregates metrics)

PM spawns dev session → `get_delegation_recommendation(project_key, task_type)` → inject structured or autonomous handoff into enriched_message

### Technical Approach

**Part 1 — `AgentSession` extension** (`models/agent_session.py`):
- Add `task_type = IndexedField(null=True)` after the `tags` field
- Add `rework_triggered = Field(null=True)` (boolean stored as string "true"/"false" per Popoto convention)
- Defined vocabulary as a module-level constant: `TASK_TYPE_VOCABULARY = {"sdlc-build", "sdlc-test", "sdlc-patch", "sdlc-plan", "bug-fix", "greenfield-feature", "rework-triggered"}`

**Part 2 — `auto_tag_session()` extension** (`tools/session_tags.py`):
- Add Rule 7: derive `task_type` from existing session fields in priority order:
  1. If `classification_type == "sdlc"` and `branch_name.startswith("session/")` → check tags for stage markers (`pr-created`, `tested`, etc.) to infer specific SDLC stage type
  2. If `classification_type == "bug"` → `"bug-fix"`
  3. If `slug` set and `"pr-created"` not in tags → `"greenfield-feature"` (new work without PR yet)
  4. If `classification_type == "sdlc"` and stage detected from events → `"sdlc-{stage.lower()}"`
  5. Fallback: None (leave unset, don't force classification)
- Set `session.task_type = derived_type` via `session.task_type` direct assignment, then save
- Only set if not already set (idempotent)

**Part 3 — `TaskTypeProfile` model** (`models/task_type_profile.py`):
```python
class TaskTypeProfile(Model):
    id = AutoKeyField()
    project_key = KeyField()
    task_type = KeyField()           # composite key: project_key + task_type
    session_count = IntField(default=0)
    avg_turns = FloatField(default=0.0)
    rework_rate = FloatField(default=0.0)     # fraction of sessions with rework_triggered=True
    failure_stage_distribution = Field(null=True)  # JSON: {"sdlc-build": 2, "sdlc-test": 1}
    delegation_recommendation = IndexedField(default="structured")  # "structured" | "autonomous"
    last_updated = SortedField(type=float, partition_by="project_key")
```
- `delegation_recommendation` is a derived field, re-computed on each update:
  - `structured` if `rework_rate > 0.3 OR session_count < 5`
  - `autonomous` otherwise
- `update_task_type_profile(session_id)`: reads session, fetches/creates profile for `project_key + task_type`, re-aggregates all metrics incrementally, saves. Wrapped in try/except in caller.

**Part 4 — `finalize_session()` hook** (`models/session_lifecycle.py`):
- After the existing `auto_tag_session()` call (step 2), add step 2.5:
```python
# 2.5. Update TaskTypeProfile (after auto_tag so task_type is set)
if not skip_auto_tag:  # reuse same guard — skip together
    try:
        from models.task_type_profile import update_task_type_profile
        if session_id:
            update_task_type_profile(session_id)
    except Exception as e:
        logger.debug(f"[lifecycle] TaskTypeProfile update failed (non-fatal): {e}")
```

**Part 5 — PM consultation** (`agent/sdk_client.py`):
- In the PM dispatch branch of enriched_message construction, after determining `project_key` and before building the SDLC orchestration text, infer `task_type` from the incoming message/classification and look up `TaskTypeProfile`:
```python
from models.task_type_profile import get_delegation_recommendation
_task_type = _infer_task_type_from_message(message, classification)
_delegation = get_delegation_recommendation(project_key, _task_type)
```
- If `_delegation == "structured"`: current step-by-step SDLC instructions (unchanged)
- If `_delegation == "autonomous"`: abbreviated handoff — objective + constraints, no step-by-step
- `_infer_task_type_from_message()`: simple rule-based function, no LLM — checks for SDLC stage keywords in message, classification_type, presence of issue URL

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `update_task_type_profile()` in `finalize_session()` is wrapped in `try/except Exception` — test that a deliberate exception in the profile update does not prevent the session from reaching terminal status
- [ ] `get_delegation_recommendation()` must never raise — test with missing profile (returns `"structured"` as safe default)
- [ ] Popoto save failure in `update_task_type_profile()` — log debug, do not re-raise

### Empty/Invalid Input Handling
- [ ] `update_task_type_profile(session_id)` with session where `task_type` is None → no-op, no profile created for `None` type
- [ ] `get_delegation_recommendation(project_key=None, task_type=None)` → returns `"structured"` (safe default)
- [ ] `_infer_task_type_from_message("")` → returns None without error

### Error State Rendering
- [ ] If profile lookup fails in PM dispatch, SDLC handoff falls back to `"structured"` (current behavior preserved) — no user-visible error

## Test Impact

- [ ] `tests/unit/test_session_tags.py` — UPDATE: add test cases for Rule 7 (task_type derivation) and verify idempotency
- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: verify `update_task_type_profile` is called after `auto_tag_session` in finalize path; verify skip when `skip_auto_tag=True`
- [ ] `tests/integration/test_session_finalize.py` (create if absent) — CREATE: end-to-end: complete a dev session, verify `TaskTypeProfile` is updated with correct metrics
- [ ] `tests/unit/test_agent_session.py` — UPDATE: verify `task_type` IndexedField is indexable and queryable

## Rabbit Holes

- **LLM-based task classification**: The issue explicitly says no LLM calls in the tagging/profile path. Pattern-based only.
- **Backfilling historical sessions**: Existing sessions have no `task_type`. Do not attempt retroactive tagging — profiles will bootstrap from zero and accumulate over time.
- **Fine-grained SDLC stage tracking**: The vocabulary is intentionally coarse. Do not try to track `sdlc-build-phase-2` vs `sdlc-build-phase-3`.
- **Per-session recommendation explanations**: `delegation_recommendation` is a two-value enum. Do not add rationale text or confidence scores — keep it binary.
- **Real-time profile updates during session**: Profile updates happen at completion only. Do not add in-flight tracking.

## Risks

### Risk 1: Popoto `IndexedField` on `task_type` with null values
**Impact:** Popoto's index may behave unexpectedly when `task_type=None` — could pollute the index or cause query errors.
**Mitigation:** Use `IndexedField(null=True)` which is already the pattern for `status`. Test `AgentSession.query.filter(task_type="sdlc-build")` returns only sessions where `task_type` is explicitly set.

### Risk 2: Profile not updated for sessions that fail or are killed
**Impact:** `finalize_session()` is called for all terminal statuses including `failed` and `killed`. Profile metrics would include failed sessions in avg_turns/rework calculations.
**Mitigation:** In `update_task_type_profile()`, only update profile when `session.status == "completed"`. Log but skip for other terminal statuses. This keeps profiles focused on completion data.

### Risk 3: `_infer_task_type_from_message()` mis-classifies the task
**Impact:** PM requests `autonomous` for a novel task type that happens to pattern-match as a known type.
**Mitigation:** Conservative defaults. Any task type with `session_count < 5` always returns `structured` regardless of pattern match. Mis-inference just delays the `autonomous` recommendation.

## Race Conditions

### Race 1: Concurrent profile updates for same project_key + task_type
**Location:** `models/task_type_profile.py` — `update_task_type_profile()`
**Trigger:** Two dev sessions of the same `task_type` completing simultaneously for the same project
**Data prerequisite:** Profile's `session_count` and aggregates must be consistent
**State prerequisite:** No concurrent writers on the same profile key
**Mitigation:** Popoto saves are not transactional. Use **incremental update** (re-read profile → update fields → save) rather than computed-from-scratch aggregation. Worst case: one concurrent update is lost, session_count is off by 1. Acceptable — profiles are advisory, not authoritative. Add a comment in code noting this is eventually-consistent.

## No-Gos (Out of Scope)

- Do not add LLM calls to the tagging or profile update path — keep it synchronous and pattern-based
- Do not denormalize aggregate metrics into `AgentSession` — keep it a pure operational record
- Do not block session finalization if profile update fails — always wrap in try/except
- Do not implement retroactive backfill of `task_type` for historical sessions
- Do not expose `TaskTypeProfile` data in the dashboard or Telegram UI (separate concern)
- Do not add per-failure-stage analytics beyond `failure_stage_distribution` JSON field

## Update System

No update system changes required — this feature is purely internal to the AI system. No new environment variables, external services, or deployment dependencies. The new Popoto model auto-creates its Redis keys on first write.

## Agent Integration

No agent integration required — `TaskTypeProfile` is consulted within the PM session execution path (`sdk_client.py`), which is bridge-internal. No new MCP server tools needed. The PM session already has read access to the Redis data via Popoto models imported in `sdk_client.py`.

## Documentation

- [ ] Create `docs/features/trm-task-type-profile.md` describing the TRM registry, `TaskTypeProfile` model fields, `task_type` vocabulary, and how `delegation_recommendation` is derived
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `AgentSession` has `task_type = IndexedField(null=True)` and `rework_triggered = Field(null=True)`
- [ ] `auto_tag_session()` derives and sets `task_type` at completion using the defined vocabulary
- [ ] `TaskTypeProfile` Popoto model exists with `avg_turns`, `rework_rate`, `failure_stage_distribution`, `delegation_recommendation`, `session_count`, `last_updated` fields
- [ ] `TaskTypeProfile` is updated post-session via `finalize_session()` hook (after `auto_tag_session`), wrapped in try/except
- [ ] `delegation_recommendation` logic is tested: `structured` when `rework_rate > 0.3` or `session_count < 5`, `autonomous` otherwise
- [ ] PM spawn logic consults `TaskTypeProfile.delegation_recommendation` before building dev session prompt
- [ ] Unit tests for profile update logic and recommendation derivation
- [ ] Integration test: complete a session, verify `TaskTypeProfile` is updated
- [ ] A profile update failure never prevents session finalization (failure injection test)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (data-model)**
  - Name: model-builder
  - Role: Add `task_type` and `rework_triggered` to `AgentSession`, create `TaskTypeProfile` model, extend `auto_tag_session()`, add `finalize_session()` hook
  - Agent Type: builder
  - Resume: true

- **Builder (pm-integration)**
  - Name: pm-builder
  - Role: Add `TaskTypeProfile` consultation to `sdk_client.py` PM dispatch path
  - Agent Type: builder
  - Resume: true

- **Validator (data-model)**
  - Name: model-validator
  - Role: Verify model fields, tagging rules, profile update logic, and failure path safety
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write unit and integration tests for profile update, recommendation derivation, and PM consultation
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: documentarian
  - Role: Create `docs/features/trm-task-type-profile.md` and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. AgentSession field additions + TaskTypeProfile model
- **Task ID**: build-data-model
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py`, `tests/unit/test_task_type_profile.py` (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `task_type = IndexedField(null=True)` and `rework_triggered = Field(null=True)` to `AgentSession` after `tags` field in `models/agent_session.py`
- Add `TASK_TYPE_VOCABULARY` constant to `models/agent_session.py`
- Create `models/task_type_profile.py` with `TaskTypeProfile` Popoto model (fields: `id`, `project_key`, `task_type`, `session_count`, `avg_turns`, `rework_rate`, `failure_stage_distribution`, `delegation_recommendation`, `last_updated`)
- Add `update_task_type_profile(session_id)` function to `models/task_type_profile.py`
- Add `get_delegation_recommendation(project_key, task_type)` function returning `"structured"` or `"autonomous"`

### 2. Extend auto_tag_session() with task_type derivation
- **Task ID**: build-tagging
- **Depends On**: build-data-model
- **Validates**: `tests/unit/test_session_tags.py`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Add Rule 7 to `auto_tag_session()` in `tools/session_tags.py`: derive `task_type` from `classification_type`, `branch_name`, `slug`, existing tags
- Set `session.task_type` only if not already set (idempotent)
- Keep it pattern-based — no LLM calls

### 3. Add profile update hook to finalize_session()
- **Task ID**: build-lifecycle-hook
- **Depends On**: build-tagging
- **Validates**: `tests/unit/test_session_lifecycle.py`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Add step 2.5 to `finalize_session()` in `models/session_lifecycle.py`: call `update_task_type_profile(session_id)` after `auto_tag_session()`, wrapped in try/except, guarded by `not skip_auto_tag`
- Only update profile when `session.status == "completed"` (skip for `failed`, `killed`, etc.)

### 4. PM consultation in sdk_client.py
- **Task ID**: build-pm-integration
- **Depends On**: build-data-model
- **Validates**: No existing tests cover this path; new test in `tests/unit/test_sdk_client_pm.py`
- **Assigned To**: pm-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_infer_task_type_from_message(message, classification)` utility in `agent/sdk_client.py`
- In PM dispatch branch, look up `get_delegation_recommendation(project_key, task_type)` before building enriched_message SDLC instructions
- If `"autonomous"`: use abbreviated handoff (objective + constraints only)
- If `"structured"`: current step-by-step instructions (existing behavior, unchanged)
- Default to `"structured"` on any lookup failure

### 5. Validate data model
- **Task ID**: validate-data-model
- **Depends On**: build-lifecycle-hook
- **Assigned To**: model-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `AgentSession.task_type` is queryable via `AgentSession.query.filter(task_type="sdlc-build")`
- Verify `TaskTypeProfile` saves and loads correctly from Redis
- Verify `update_task_type_profile()` is a no-op when `task_type=None`
- Verify failure in profile update does not block session finalization

### 6. Write tests
- **Task ID**: build-tests
- **Depends On**: validate-data-model, build-pm-integration
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for `TaskTypeProfile.delegation_recommendation` derivation logic (structured threshold)
- Unit tests for `auto_tag_session()` Rule 7 task_type derivation
- Unit tests for `get_delegation_recommendation()` with missing profile (returns `"structured"`)
- Integration test: complete a session and verify `TaskTypeProfile` is updated
- Failure injection test: exception in `update_task_type_profile()` does not prevent finalization

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/trm-task-type-profile.md`
- Add entry to `docs/features/README.md`

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: model-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| TaskTypeProfile importable | `python -c "from models.task_type_profile import TaskTypeProfile; print('ok')"` | output contains ok |
| task_type field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'task_type'); print('ok')"` | output contains ok |
| auto_tag derives task_type | `pytest tests/unit/test_session_tags.py -k task_type -v` | exit code 0 |
| Profile update non-blocking | `pytest tests/unit/test_session_lifecycle.py -k profile -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`rework_triggered` flag setter**: Should `rework_triggered` be set by the PM when spawning a retry dev session, or auto-detected by comparing the session's issue/slug against prior sessions? The issue sketch says "set when a session retries a prior session's output" but doesn't specify the setter. Recommendation: PM explicitly sets it via `--rework` flag in `valor_session create` for now; auto-detection is a rabbit hole.
