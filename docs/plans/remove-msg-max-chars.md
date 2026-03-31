---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/600
last_comment_id:
---

# Remove MSG_MAX_CHARS Constants and max_length Constraints

## Problem

Three separate `MSG_MAX_CHARS` constants define arbitrary character limits on Redis-backed text fields. These limits were introduced reactively (commit `8982e582` raised default from Popoto's 1,024 to 20,000 after a crash), not from any measured requirement. When text exceeds the limit, Popoto raises `ModelException` -- or the `_truncate_to_limit()` helper silently chops data before save, losing information with only a log warning as evidence.

**Current behavior:**
- `models/agent_session.py` defines `MSG_MAX_CHARS = 20_000` applied to `message_text`, `revival_context`, `result_text`
- `agent/job_queue.py` duplicates `MSG_MAX_CHARS = 20_000` used by `_truncate_to_limit()` which silently chops input
- `models/telegram.py` defines `MSG_MAX_CHARS = 50_000` applied to `content`
- Additional hardcoded `max_length` values on fields across `dead_letter`, `link`, `telemetry`, `reflection`, `reflections` models
- `context[:500]` in `ObserverTelemetry.record_decision()` manually truncates to match the `max_length=500` constraint

**Desired outcome:**
- No artificial `max_length` constraints on text fields (Redis strings have no inherent size limit)
- No silent data truncation at storage boundaries
- Observability logging when unusually large values are stored, so problems can be detected rather than silently hidden
- `_truncate_to_limit()` helper removed

## Prior Art

No prior issues found related to this work. The `MSG_MAX_CHARS` constants were introduced ad-hoc and have not been the subject of any previous cleanup effort.

## Data Flow

1. **Entry point**: Human sends Telegram message (any length)
2. **Bridge** (`bridge/telegram_bridge.py`): Message received, TelegramMessage created with `content` field
3. **Job queue** (`agent/job_queue.py`): `enqueue_job()` calls `_truncate_to_limit()` on `message_text` and `revival_context` before creating AgentSession
4. **AgentSession** (`models/agent_session.py`): Fields saved to Redis via Popoto; `max_length` on Field causes `ModelException` if exceeded
5. **Output**: Agent response delivered to Telegram (subject to real 4,096-char API limit in `bridge/response.py` -- NOT in scope)

The truncation in step 3 is the only proactive data loss. All other `max_length` constraints are passive bombs that crash on save if exceeded.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- removing constraints is backward-compatible; all existing data remains valid
- **Coupling**: Decreases coupling -- removes the dependency between job_queue.py and agent_session.py's arbitrary constant
- **Data ownership**: No change
- **Reversibility**: Trivially reversible -- re-add `max_length` if needed

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Straightforward removal of constants and constraints across known files. No new features, no new APIs, no architectural changes.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Constant removal**: Delete all three `MSG_MAX_CHARS` constants and their usages
- **Field cleanup**: Remove `max_length` from all text/content Popoto Field definitions
- **Truncation removal**: Delete `_truncate_to_limit()` and its call sites
- **Application-level truncation removal**: Remove `context[:500]` in `ObserverTelemetry.record_decision()`
- **Observability utility**: Add a lightweight `log_large_field()` helper that warns when field values exceed a soft threshold (e.g., 50k chars) -- observation only, never truncates or rejects

### Flow

**Text arrives** -> stored in Popoto Field (no max_length) -> `log_large_field()` emits warning if over soft threshold -> saved to Redis without truncation

### Technical Approach

- Remove `MSG_MAX_CHARS` from `models/agent_session.py`, `models/telegram.py`, `agent/job_queue.py`
- Remove `max_length=...` from every text Field across all models (see full inventory below)
- Remove `_truncate_to_limit()` function and its two call sites in `enqueue_job()`
- Remove `context[:500]` slice in `ObserverTelemetry.record_decision()`
- Add a `log_large_field(field_name, value, threshold=50_000)` utility in a shared location (e.g., `tools/field_utils.py` or inline in models) that logs a warning when `len(value) > threshold`
- Call `log_large_field()` at save points where large values are plausible (enqueue_job, TelegramMessage creation)

### Full Field Inventory

| Model | File | Field | Current max_length | Action |
|-------|------|-------|--------------------|--------|
| AgentSession | models/agent_session.py | message_text | 20,000 | Remove |
| AgentSession | models/agent_session.py | revival_context | 20,000 | Remove |
| AgentSession | models/agent_session.py | result_text | 20,000 | Remove |
| AgentSession | models/agent_session.py | log_path | 1,000 | Remove |
| AgentSession | models/agent_session.py | summary | 50,000 | Remove |
| AgentSession | models/agent_session.py | context_summary | 200 | Remove |
| AgentSession | models/agent_session.py | expectations | 500 | Remove |
| TelegramMessage | models/telegram.py | content | 50,000 | Remove |
| DeadLetter | models/dead_letter.py | text | 20,000 | Remove |
| Link | models/link.py | final_url | 2,000 | Remove (URLs can exceed 2k in practice) |
| Link | models/link.py | title | 1,000 | Remove |
| Link | models/link.py | description | 2,000 | Remove |
| Link | models/link.py | notes | 5,000 | Remove |
| Link | models/link.py | ai_summary | 50,000 | Remove |
| ObserverTelemetry | models/telemetry.py | last_decision_context | 500 | Remove |
| Reflection | models/reflection.py | last_error | 1,000 | Remove |
| ReflectionIgnore | models/reflections.py | reason | 500 | Remove |

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this work removes constraints rather than adding error handling

### Empty/Invalid Input Handling
- [ ] `log_large_field()` must handle None and empty string inputs gracefully (no-op)
- [ ] Verify that removing `max_length` does not change Popoto's behavior for None/empty values (it should not -- `null=True` fields already accept None)

### Error State Rendering
- Not applicable -- no user-visible output changes

## Test Impact

- [ ] `tests/unit/test_observer_telemetry.py::test_record_decision_truncates_context` -- UPDATE: remove this test (truncation no longer happens; the `context[:500]` slice is removed along with `max_length`)
- [ ] `tests/unit/test_observer_telemetry.py::test_record_decision_with_context` -- no change needed (stores short context, still works)
- [ ] `tests/unit/test_monitoring_telemetry.py` -- no change needed (stores short context values)

No other existing tests reference `MSG_MAX_CHARS`, `_truncate_to_limit`, or `max_length` -- these constants were never directly tested.

## Rabbit Holes

- **Popoto Field internals**: Do not attempt to patch Popoto itself to add soft-limit support. The observability logging belongs in application code, not the ORM.
- **Comprehensive field-level logging**: Do not add `log_large_field()` calls to every single model save path. Focus on the high-traffic entry points (job enqueue, TelegramMessage creation). Other models rarely encounter large values.
- **URL field validation**: Do not add URL format validation to `final_url` as a replacement for `max_length`. That is a separate concern.

## Risks

### Risk 1: Unbounded Redis memory growth
**Impact:** If an unusually large value is stored (e.g., a 10MB paste), Redis memory could grow unexpectedly.
**Mitigation:** The `log_large_field()` observability logging will surface these cases. Redis itself handles large strings fine; the real risk is sustained accumulation, which existing TTL and cleanup mechanisms already address.

### Risk 2: Popoto behavior without max_length
**Impact:** Unknown if Popoto's Field() behaves differently when max_length is omitted vs. set to a large value.
**Mitigation:** Popoto's Field treats max_length as optional validation -- omitting it simply skips the length check. No behavioral change expected. Verify during implementation.

## Race Conditions

No race conditions identified -- all changes are to field definitions and a synchronous truncation helper. No concurrent access patterns are affected.

## No-Gos (Out of Scope)

- Telegram's real 4,096-char send limit in `bridge/response.py` and `tools/send_telegram.py` -- real API constraint
- `HISTORY_MAX_ENTRIES` and `STEERING_QUEUE_MAX` -- these cap list lengths, not text field sizes
- Adding Redis memory monitoring or alerting -- separate infrastructure concern
- Changing Popoto's Field behavior or contributing upstream patches

## Update System

No update system changes required -- this is a pure code cleanup with no new dependencies, config files, or migration steps. Existing Redis data remains valid (removing constraints does not invalidate stored values).

## Agent Integration

No agent integration required -- this is an internal model cleanup. No new tools, no MCP server changes, no bridge import changes. The agent's behavior is unchanged; it simply will no longer hit `ModelException` crashes on large text values.

## Documentation

### Inline Documentation
- [ ] Update docstrings on modified model classes to remove references to character limits
- [ ] Add docstring to `log_large_field()` utility explaining its observability-only purpose
- [ ] Remove the `MSG_MAX_CHARS` comment in `agent/job_queue.py` ("~5k tokens -- reasonable context limit")

No feature documentation file is needed -- this is a chore that removes constraints rather than adding a user-facing feature. The models themselves are the documentation.

## Success Criteria

- [ ] No `MSG_MAX_CHARS` constant exists anywhere in the codebase
- [ ] No Popoto `Field()` definition uses `max_length` for text content fields
- [ ] `_truncate_to_limit()` function is removed from `agent/job_queue.py`
- [ ] `context[:500]` truncation removed from `ObserverTelemetry.record_decision()`
- [ ] A `log_large_field()` utility warns when text values exceed a soft observation threshold
- [ ] `test_record_decision_truncates_context` test updated or removed
- [ ] Storing a message longer than 20,000 chars no longer crashes or truncates
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (field-cleanup)**
  - Name: field-cleanup-builder
  - Role: Remove all max_length constraints, MSG_MAX_CHARS constants, truncation helpers, and add observability logging
  - Agent Type: builder
  - Resume: true

- **Validator (field-cleanup)**
  - Name: field-cleanup-validator
  - Role: Verify no max_length or MSG_MAX_CHARS remain, tests pass, observability logging works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove constants and constraints
- **Task ID**: build-remove-constraints
- **Depends On**: none
- **Validates**: `grep -rn 'MSG_MAX_CHARS\|_truncate_to_limit' --include='*.py' . | grep -v docs/ | grep -v .md` returns empty
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `MSG_MAX_CHARS` constant from `models/agent_session.py`, `models/telegram.py`, `agent/job_queue.py`
- Remove `max_length=...` from all 17 Field definitions listed in the inventory table
- Delete `_truncate_to_limit()` function and its two call sites in `enqueue_job()`
- Remove `context[:500]` slice in `ObserverTelemetry.record_decision()`
- Clean up any now-unused imports

### 2. Add observability logging
- **Task ID**: build-observability
- **Depends On**: build-remove-constraints
- **Validates**: `tests/unit/test_log_large_field.py` (create)
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `log_large_field(field_name: str, value: str | None, threshold: int = 50_000)` utility
- Add calls at `enqueue_job()` for `message_text` and `revival_context`
- Add call at `TelegramMessage` creation for `content`
- Write unit test for `log_large_field()` covering: None input, short string, over-threshold string

### 3. Update tests
- **Task ID**: build-update-tests
- **Depends On**: build-remove-constraints
- **Validates**: `pytest tests/unit/test_observer_telemetry.py -x -q`
- **Assigned To**: field-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove or update `test_record_decision_truncates_context` in `tests/unit/test_observer_telemetry.py`

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-remove-constraints, build-observability, build-update-tests
- **Assigned To**: field-cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn 'MSG_MAX_CHARS' --include='*.py' . | grep -v docs/` and confirm empty
- Run `grep -rn 'max_length' models/ --include='*.py'` and confirm empty
- Run `grep -rn '_truncate_to_limit' --include='*.py' .` and confirm empty
- Run `pytest tests/ -x -q` and confirm all pass
- Run `python -m ruff check .` and confirm clean

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: field-cleanup-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update docstrings on modified model classes
- Add docstring to `log_large_field()` utility

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No MSG_MAX_CHARS | `grep -rn 'MSG_MAX_CHARS' --include='*.py' . \| grep -v docs/` | exit code 1 |
| No max_length in models | `grep -rn 'max_length' models/ --include='*.py'` | exit code 1 |
| No _truncate_to_limit | `grep -rn '_truncate_to_limit' --include='*.py' .` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the issue is well-scoped with clear acceptance criteria and all assumptions have been validated in the issue recon.
