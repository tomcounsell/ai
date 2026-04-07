---
status: Ready
type: chore
appetite: Large
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/609
last_comment_id:
---

# AgentSession Field Cleanup

## Problem

The [`AgentSession`](../../models/agent_session.py) model has grown to ~50 fields over successive iterations. It accumulates field-level debt across several categories: wrong types (float timestamps instead of Popoto's `DatetimeField`), vague names (`last_activity`, `scheduled_after`, `revival_context`), duplicate fields (`work_item_slug` duplicates `slug`), per-interaction values that get overwritten on session resumption (`summary`, `result_text`, `stage_states`, `commit_sha`), dead infrastructure (`depends_on`, `stable_job_id`, `_qa_mode_legacy`), scattered origin fields (6 fields for one Telegram message), and trivial wrapper methods.

**Current behavior:**
- Timestamp fields use `Field(type=float)` with `time.time()`, losing type safety and requiring manual arithmetic
- `summary` and `result_text` are single strings overwritten each time a session resumes, losing earlier interaction data
- `work_item_slug` exists alongside `slug` with every caller doing `self.slug or self.work_item_slug`
- `depends_on` and `stable_job_id` are wired up but no callers actually set dependencies
- Six separate fields describe one Telegram message origin
- History entries are flat strings like `"[lifecycle] pending→running"` with no structure

**Desired outcome:**
- Proper datetime types with `auto_now`/`auto_now_add` support
- Clear, unambiguous field names
- Structured `SessionEvent` Pydantic model as single source of truth for per-interaction data
- ~50 fields reduced to ~30 fields
- Trivial wrapper methods removed in favor of direct Popoto queries

## Prior Art

- **[#592](https://github.com/tomcounsell/ai/issues/592)**: Audit AgentSession model — Fixed `status` KeyField duplicates, pruned 3 dead fields (`retry_count`, `last_stall_reason`, `artifacts`). This issue continues that cleanup.
- **[PR #607](https://github.com/tomcounsell/ai/pull/607)**: Implementation of #592 — Changed `status` to `IndexedField`, removed dead fields, upgraded Popoto to 1.4.3.
- **[#473](https://github.com/tomcounsell/ai/issues/473)**: AgentSession field naming cleanup — Earlier pass at naming issues, now closed.
- **[PR #505](https://github.com/tomcounsell/ai/pull/505)**: Earlier field cleanup pass — Removed some dead fields, renamed for clarity.
- **[#530](https://github.com/tomcounsell/ai/issues/530)**: OOP audit of AgentSession god-object — Identified structural debt, now closed.
- **[#608](https://github.com/tomcounsell/ai/issues/608)**: Rename "job" terminology to "agent_session" — Open, complementary but independent scope.

## Spike Results

### spike-1: Popoto DatetimeField backward compatibility with float data
- **Assumption**: "DatetimeField can gracefully read existing float timestamp data from Redis"
- **Method**: code-read
- **Finding**: Popoto's `decode_custom_types` returns raw floats unchanged (they're not tagged dicts), and the model constructor accepts them without type-checking. So existing float data won't crash on read — it passes through as a raw float. However, DatetimeField methods (like `auto_now`) expect `datetime` objects. **Migration is required**: read the float, convert with `datetime.fromtimestamp()`, re-save. This is a standard schema migration, not a Popoto limitation.
- **Confidence**: high (confirmed by Popoto maintainer)
- **Impact on plan**: A migration script is needed as a build task. Property-level fallback is NOT sufficient — the data must be converted. Added as Step 1.5 in tasks.

### spike-2: SortedField score compatibility
- **Assumption**: "Changing SortedField from type=float to type=datetime breaks existing sorted set scores"
- **Method**: code-read
- **Finding**: Safe. SortedField's `convert_to_numeric()` converts datetime via `.timestamp()` which produces the same Unix float. Existing float scores in the sorted set remain valid. No migration needed for sorted set indexes — only the hash field values need conversion.
- **Confidence**: high
- **Impact on plan**: No additional work for SortedField scores.

## Data Flow

AgentSession is created and mutated across the full message lifecycle:

1. **Entry point**: Telegram message arrives → `bridge/telegram_bridge.py` creates session via `enqueue_job()` in `agent/job_queue.py`
2. **Job queue**: `_pop_job()` picks session, sets `started_at`, filters by `scheduled_after` and `depends_on`
3. **Agent execution**: `agent/sdk_client.py` reads/writes `classification_type`, `stage_states`, `claude_session_uuid`, `commit_sha`
4. **Bridge hooks**: `bridge/session_transcript.py` sets `summary`, `log_path`, `branch_name`; `bridge/response.py` sets `result_text`, `expectations`
5. **Pipeline state**: `bridge/pipeline_state.py` reads/writes `stage_states` via `PipelineStateMachine`
6. **Monitoring**: `monitoring/session_watchdog.py` reads `last_activity`, `started_at`; writes `watchdog_unhealthy`, `summary`
7. **Dashboard**: `ui/data/sdlc.py` reads all fields for display; templates format timestamps with `| timestamp` filter
8. **Cleanup**: `cleanup_expired()` scans `started_at`/`created_at` for age-based deletion

## Architectural Impact

- **New dependencies**: `pydantic` (already in deps) for `SessionEvent` model
- **Interface changes**: Property accessors preserve backward compatibility for `sender_name`, `sender_id`, `message_text`, `summary`, `result_text`, `stage_states`, `last_commit_sha`, `classification_type`, `scheduling_depth`
- **Coupling**: Reduces coupling — consolidated DictFields replace scattered related fields
- **Data ownership**: `session_events` becomes single source of truth for per-interaction data (summary, delivery, stages, checkpoints)
- **Reversibility**: Medium — field renames change Redis hash keys for existing data. New sessions work immediately; old sessions need migration or will appear empty for renamed fields

## Appetite

**Size:** Large

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (scope alignment on migration strategy)
- Review rounds: 1 (code review for field rename safety)

## Prerequisites

No prerequisites — this work modifies only internal model fields with no external dependencies.

## Solution

### Key Elements

- **DatetimeField migration**: Replace `Field(type=float)` with `DatetimeField` and `SortedField(type=datetime)` for all timestamp fields
- **Structured event log**: Replace flat string `history` with `session_events` ListField containing serialized `SessionEvent` Pydantic model dicts; derive `summary`, `result_text`, `stage_states`, `last_commit_sha` as `@property` accessors with **setters** that append events (so existing code like `s.summary = "..."` continues to work)
- **Field consolidation**: Merge 6 Telegram origin fields into `initial_telegram_message` DictField; merge `revival_context` + `classification_type` + `classification_confidence` into `extra_context` DictField
- **Dead field removal**: Remove `depends_on`, `stable_job_id`, `_qa_mode_legacy`, `work_item_slug`, `scheduling_depth` (replaced with derived property)
- **Method pruning**: Remove 3 factory methods and 7 query wrappers that are trivial Popoto one-liners

### Flow

**Model change** → Update all callers (bridge, agent, hooks, tools, monitoring) → Update tests → Update UI templates → Update docs → Verify with migration script for existing Redis data

### Technical Approach

- Changes are ordered to minimize intermediate breakage: field additions first, then caller updates, then field removals
- Property accessors on renamed/removed fields provide backward compatibility during migration
- Existing Redis sessions with float timestamps may need a one-time migration script
- `SessionEvent` is serialized as dicts in the `ListField` (Popoto serializes via msgpack), with Pydantic used for validation at write time

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `append_event()` (renamed from `append_history`) has try/except on save — test that save failures are logged but don't crash
- [ ] `set_link()`, `record_pm_message()`, `push_steering_message()` all have try/except — verify logging on failure
- [ ] `cleanup_expired()` datetime comparison — test with sessions that have `None` timestamps

### Empty/Invalid Input Handling
- [ ] `initial_telegram_message` is `None` for local CLI sessions — verify property accessors return `None` gracefully
- [ ] `extra_context` is `None` — verify `classification_type` property returns `None`
- [ ] `session_events` is `None` or empty — verify `summary`, `result_text`, `stage_states`, `last_commit_sha` all return `None`
- [ ] `scheduling_depth` property with broken parent chain (parent deleted) — verify it stops and returns partial count

### Error State Rendering
- [ ] UI templates handle `None` datetime fields (sessions created before migration)
- [ ] Dashboard session detail renders correctly with new field structure

## Test Impact

**Note:** This list is a starting point. Builders must grep-audit every renamed field and update ALL callers, including files not listed here. The `initial_telegram_message` consolidation alone touches 48+ files.

- [ ] `tests/unit/test_job_hierarchy.py` — UPDATE: datetime objects, remove `depends_on`/`stable_job_id`, remove `commit_sha` field usage
- [ ] `tests/unit/test_job_dependencies.py` — DELETE: `depends_on` and `stable_job_id` removed entirely
- [ ] `tests/unit/test_session_status.py` — UPDATE: `updated_at` rename, datetime objects
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: `updated_at` rename, datetime objects, `work_item_slug` → `slug`
- [ ] `tests/unit/test_job_queue_async.py` — UPDATE: datetime objects, remove `scheduling_depth`
- [ ] `tests/unit/test_job_scheduler_kill.py` — UPDATE: datetime objects
- [ ] `tests/unit/test_summarizer.py` — UPDATE: remove `_qa_mode_legacy` references
- [ ] `tests/unit/test_qa_nudge_cap.py` — UPDATE: remove `_qa_mode_legacy` references
- [ ] `tests/unit/test_session_tags.py` — UPDATE: `work_item_slug` → `slug`, `sender` property removal
- [ ] `tests/unit/test_sdlc_env_vars.py` — UPDATE: `work_item_slug` → `slug`
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: remove `work_item_slug` from field lists
- [ ] `tests/unit/test_model_relationships.py` — UPDATE: `sender` property, datetime objects
- [ ] `tests/unit/test_config_driven_routing.py` — UPDATE: remove `_qa_mode_legacy` references
- [ ] `tests/integration/test_agent_session_lifecycle.py` — UPDATE: datetime objects, `updated_at` rename, `session_events` rename, factory method removal
- [ ] `tests/integration/test_job_scheduler.py` — UPDATE: datetime objects, `scheduling_depth` removal, `scheduled_at` rename
- [ ] `tests/integration/test_job_health_monitor.py` — UPDATE: datetime objects
- [ ] `tests/integration/test_connectivity_gaps.py` — UPDATE: datetime objects, `work_item_slug` → `slug`
- [ ] `tests/integration/test_job_queue_race.py` — UPDATE: `work_item_slug` → `slug`, `revival_context` → `extra_context`
- [ ] `tests/integration/test_redis_models.py` — UPDATE: field renames, `sender` property
- [ ] `tests/e2e/test_session_continuity.py` — UPDATE: datetime objects
- [ ] `tests/e2e/test_session_lifecycle.py` — UPDATE: datetime objects, factory methods
- [ ] `tests/e2e/test_context_propagation.py` — UPDATE: `work_item_slug` → `slug`
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: `work_item_slug` → `slug`

## Rabbit Holes

- **Over-engineering the migration script**: The migration is a simple loop — read float, convert, save. Don't add rollback logic, progress tracking, or batch processing. Sessions are small, the loop is fast.
- **Caching derived properties**: `session_events` scan is O(n) but capped at 20 entries. Adding `@functools.lru_cache` or `__slots__` optimization is premature.
- **Refactoring PipelineStateMachine**: The state machine reads `stage_states` which becomes a derived property. Tempting to refactor the state machine itself, but it should just call the property — separate concern.

## Risks

### Risk 1: Redis data format incompatibility
**Impact:** Existing sessions with float timestamps won't work correctly with DatetimeField methods (e.g., `auto_now` comparison)
**Mitigation:** One-time migration script runs before deployment. Popoto reads raw floats without crashing — the migration loop reads the float, converts with `datetime.fromtimestamp()`, and re-saves in proper format. SortedField scores need no migration (same numeric scale).

### Risk 2: Breakage scope
**Impact:** ~161 timestamp occurrences across ~44 files means high chance of missed callers
**Mitigation:** One PR with three sequential migration phases (timestamps → event log → field consolidation). Each phase updates model + all callers before moving to the next. Grep verification at each step. Full test suite must pass after each phase.

### Risk 3: Conflict with parallel issues
**Impact:** #599 (qa→teammate), #600 (MSG_MAX_CHARS), #608 (job→session) touch overlapping code
**Mitigation:** This issue does NOT touch `session_mode`, `MSG_MAX_CHARS`, or job/session naming. Coordinate merge order if concurrent.

## Race Conditions

No race conditions identified — all changes are to field types and names, not to concurrency patterns. The existing save/read patterns remain unchanged.

## No-Gos (Out of Scope)

- Do NOT rename any `KeyField` or `AutoKeyField` — changing those changes Redis keys and breaks existing records
- Do NOT touch `session_mode` or the qa→teammate rename (issue #599)
- Do NOT touch `MSG_MAX_CHARS` removal (issue #600)
- Do NOT rename job→session terminology (issue #608)
- Do NOT refactor `PipelineStateMachine` — it just needs to call the new `stage_states` property
- Do NOT add new fields — this is a cleanup issue, not a feature issue

## Update System

No update system changes required — this is a model-internal refactor. The update script does not reference timestamp field types or field names. Popoto 1.4.3 (already deployed via PR #607) supports all required field types.

## Agent Integration

No agent integration required — AgentSession is used internally by the bridge, hooks, and job queue. No MCP server exposes AgentSession fields directly.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` — new field types, names, and `SessionEvent` model
- [ ] Update `docs/features/redis-models.md` — field type audit table

### Related Documentation
- [ ] Update `docs/features/job-queue.md` — remove `depends_on`, `scheduling_depth` references
- [ ] Update `docs/features/job-scheduling.md` — `scheduling_depth` removal, `scheduled_at` rename
- [ ] Update `docs/features/session-watchdog.md` — datetime field references
- [ ] Update `docs/features/chat-dev-session-architecture.md` — factory method removal, field renames

### Inline Documentation
- [ ] Docstrings on `SessionEvent` model and all new `@property` accessors

## Success Criteria

- [ ] All timestamp fields use `DatetimeField` or `SortedField(type=datetime)` instead of `Field(type=float)`
- [ ] `last_activity` renamed to `updated_at` with `auto_now=True`
- [ ] `scheduled_after` renamed to `scheduled_at`
- [ ] `_qa_mode_legacy` field and raw Redis `hget` fallback removed
- [ ] `scheduling_depth` field replaced with derived `@property` walking `parent_job_id` chain
- [ ] `sender_name`, `sender_id`, `telegram_message_id`, `message_text`, `chat_title`, `telegram_message_key` consolidated into `initial_telegram_message` DictField
- [ ] `revival_context` renamed to `extra_context` as general-purpose DictField
- [ ] `classification_type` + `classification_confidence` folded into `extra_context`
- [ ] `history` renamed to `session_events` with `SessionEvent` Pydantic model
- [ ] `summary`, `result_text`, `stage_states`, `commit_sha` removed as fields, replaced with `@property` reading from `session_events`
- [ ] `depends_on` and `stable_job_id` removed
- [ ] Factory methods and trivial query wrappers removed
- [ ] `work_item_slug` removed, all callers use `slug`
- [ ] All callers use `datetime.now(tz=timezone.utc)` instead of `time.time()`
- [ ] All existing tests pass
- [ ] UI templates render datetime fields correctly
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model-core)**
  - Name: model-builder
  - Role: Update AgentSession model fields, add SessionEvent, add property accessors
  - Agent Type: builder
  - Resume: true

- **Builder (callers-bridge)**
  - Name: bridge-builder
  - Role: Update all bridge/ callers (telegram_bridge, session_transcript, response, summarizer, pipeline_state, routing)
  - Agent Type: builder
  - Resume: true

- **Builder (callers-agent)**
  - Name: agent-builder
  - Role: Update agent/ callers (job_queue, sdk_client, hooks, health_check, completion)
  - Agent Type: builder
  - Resume: true

- **Builder (callers-tools-ui)**
  - Name: tools-ui-builder
  - Role: Update tools/ (job_scheduler, session_tags), monitoring/, ui/, and scripts/
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Update all test files for new field types, names, and removed methods
  - Agent Type: builder
  - Resume: true

- **Validator (full)**
  - Name: full-validator
  - Role: Run full test suite, verify lint/format, check UI rendering
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs for field changes
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Model core changes
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `tests/integration/test_redis_models.py`, `tests/integration/test_agent_session_lifecycle.py`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `SessionEvent` Pydantic model in `models/session_event.py`
- Update `models/agent_session.py`: change field types, rename fields, add DictFields, add property accessors, remove dead fields and trivial methods
- Update `_JOB_FIELDS` in `agent/job_queue.py` to match new field names

### 1.5. Migration script
- **Task ID**: build-migration
- **Depends On**: build-model
- **Validates**: manual run against test Redis
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true (with build-bridge, build-agent, build-tools-ui)
- Create `scripts/migrate_datetime_fields.py` — loop all AgentSessions, convert float timestamps to datetime, convert flat history strings to SessionEvent dicts
- Recipe: `for s in AgentSession.query.all(): if isinstance(s.created_at, float): s.created_at = datetime.fromtimestamp(s.created_at, tz=timezone.utc); s.save()`
- Run as part of deployment before restarting bridge

### 2. Update bridge callers
- **Task ID**: build-bridge
- **Depends On**: build-model
- **Validates**: `tests/unit/test_summarizer.py`, `tests/unit/test_qa_nudge_cap.py`
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true (with build-agent, build-tools-ui)
- Update `bridge/telegram_bridge.py` — `initial_telegram_message` dict construction, `extra_context`, datetime usage
- Update `bridge/session_transcript.py` — datetime fields, `session_events` append, `updated_at`
- Update `bridge/response.py` — `result_text` via `append_event()`, datetime usage
- Update `bridge/summarizer.py` — remove `_qa_mode_legacy`, `work_item_slug` references
- Update `bridge/pipeline_state.py` — `stage_states` via `session_events`
- Update `bridge/routing.py` — `classification_type` property, datetime usage
- Update `bridge/reconciler.py` — sender fields via `initial_telegram_message`

### 3. Update agent callers
- **Task ID**: build-agent
- **Depends On**: build-model
- **Validates**: `tests/unit/test_job_queue_async.py`, `tests/unit/test_sdlc_env_vars.py`
- **Assigned To**: agent-builder
- **Agent Type**: builder
- **Parallel**: true (with build-bridge, build-tools-ui)
- Update `agent/job_queue.py` — datetime fields, remove `depends_on`/`stable_job_id` logic, `_dependencies_met()`, `scheduled_at` rename, `extra_context`
- Update `agent/sdk_client.py` — `classification_type` property, `slug` instead of `work_item_slug`, datetime usage
- Update `agent/hooks/` — factory method removal, datetime usage
- Update `agent/health_check.py` — `updated_at` rename, datetime comparison
- Update `agent/completion.py` — `summary` property

### 4. Update tools, UI, monitoring, scripts
- **Task ID**: build-tools-ui
- **Depends On**: build-model
- **Validates**: `tests/unit/test_session_tags.py`, `tests/unit/test_ui_sdlc_data.py`
- **Assigned To**: tools-ui-builder
- **Agent Type**: builder
- **Parallel**: true (with build-bridge, build-agent)
- Update `tools/job_scheduler.py` — remove `scheduling_depth`/`_get_scheduling_depth()`, datetime fields, `scheduled_at`, `extra_context`
- Update `tools/session_tags.py` — `slug` instead of `work_item_slug`, `sender` via property
- Update `monitoring/` — datetime comparisons, `updated_at` rename
- Update `ui/data/sdlc.py` — datetime fields, `work_item_slug` → `slug`, field renames
- Update `ui/templates/` — Jinja `| timestamp` filter for datetime objects
- Update `scripts/reflections.py` — datetime comparisons

### 5. Update all tests
- **Task ID**: build-tests
- **Depends On**: build-bridge, build-agent, build-tools-ui
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: test-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all 23 test files listed in Test Impact section
- Delete `tests/unit/test_job_dependencies.py`
- Add new tests for `SessionEvent` model serialization round-trip
- Add new tests for derived properties (`summary`, `result_text`, `stage_states`, `last_commit_sha`)

### 6. Validate full suite
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: full-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all tests pass
- Run `python -m ruff check .` — no lint errors
- Run `python -m ruff format --check .` — no format issues
- Verify UI templates render with `python -m ui.app` smoke test

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update 6 docs files listed in Documentation section
- Add docstrings on `SessionEvent` and property accessors

### 8. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: full-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No float timestamps in model | `grep -c 'type=float' models/agent_session.py` | output contains 0 |
| No time.time in model | `grep -c 'time.time' models/agent_session.py` | output contains 0 |
| No work_item_slug in model | `grep -c 'work_item_slug' models/agent_session.py` | output contains 0 |
| No depends_on in model | `grep -c 'depends_on' models/agent_session.py` | output contains 0 |
| SessionEvent model exists | `python -c "from models.session_event import SessionEvent"` | exit code 0 |

## Critique Results

| # | CONCERN | Critic | Issue | Resolution |
|---|---------|--------|-------|------------|
| 1 | BLOCKER | Operator | Test Impact undercounts — `sender_name` alone appears in 144 files, real count is 40+ test files | Plan updated: builders must grep-audit all renamed fields and update every caller, not just the listed files. Test Impact list is a starting point, not exhaustive. |
| 2 | BLOCKER | Operator | `summary`/`stage_states` writers break — code like `s.summary = "..."` raises AttributeError on read-only @property | Plan updated: add property setters that append to `session_events`. E.g., `summary.setter` creates a completion event. |
| 3 | BLOCKER | Archaeologist | `depends_on`/`stable_job_id` have 49 lines of wired logic, not truly "dead" | Acknowledged: the infrastructure exists but no callers set dependencies. Removal requires careful extraction of `_dependencies_met()`, `_pop_job()` filtering, and `_update_depends_on_references()`. |
| 4 | CONCERN | Adversary | `scheduling_depth` parent-chain walk has no cycle protection | Already has max-depth 5 safety cap in the property. Made explicit in plan. |
| 5 | CONCERN | Simplifier | Phasing unresolved — atomic PR vs multiple | Resolved: one PR, three sequential migration phases internally (timestamps → event log → field consolidation). |
| 6 | CONCERN | Operator | Missing caller files in task lists (`bridge/enrichment.py`, `bridge/catchup.py`, `bridge/context.py`) | Plan updated: task descriptions say "all callers" not just named files. Builders grep-audit each renamed field. |
| 7 | NIT | Simplifier | SessionEvent cap contradicts Rabbit Holes | Resolved: cap stays at 20. |

---

## Open Questions

1. ~~**Migration strategy for existing Redis data**~~ — **Resolved:** One-time migration script (spike-1 confirmed).

2. ~~**SessionEvent cap**~~ — **Resolved:** Keep at 20. Richer data per entry doesn't require more entries.

3. ~~**Phasing**~~ — **Resolved:** One PR with three sequential migration phases: timestamps → event log → field consolidation.
