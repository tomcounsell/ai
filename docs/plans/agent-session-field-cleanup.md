---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/473
last_comment_id:
---

# AgentSession Field Naming Cleanup

## Problem

**Current behavior:**
AgentSession has ~40 fields accumulated over months of development. Three message-related fields (`message_id`, `reply_to_msg_id`, `trigger_message_id`) sound interchangeable but serve distinct purposes. A field marked "deprecated" (`last_transition_at`) is actively used in 6+ files. `claude_code_session_id` is defined and preserved in delete-and-recreate but never read. Two fields (`sdlc_stages` and `stage_states`) track the same SDLC stage progress with an implicit fallback chain.

**Desired outcome:**
Every field name clearly communicates its purpose. No false deprecation markers. No vestigial fields. No duplicate fields with fallback logic. A developer reading AgentSession for the first time can understand each field without consulting git blame.

## Prior Art

- **[Issue #295](https://github.com/tomcounsell/ai/issues/295)**: "Strengthen Popoto model relationships and naming" — Introduced TelegramMessage model, trigger_message_id cross-reference, and migration script. Successfully merged via [PR #392](https://github.com/tomcounsell/ai/pull/392). Created the dual-model pattern this cleanup continues.
- **[Issue #436](https://github.com/tomcounsell/ai/issues/436)**: "Make is_sdlc_job a derived property from stage progress" — Removed stored classification flag in favor of derived property. Precedent for removing redundant fields.
- **[PR #464](https://github.com/tomcounsell/ai/pull/464)**: "SDLC Redesign: ChatSession/DevSession split" — Added `sdlc_stages` field to DevSession, creating the duplication with `stage_states` that this plan resolves.
- **[PR #180](https://github.com/tomcounsell/ai/pull/180)**: "Unified AgentSession model" — Original model creation that accumulated all fields.

## Architectural Impact

- **Interface changes**: Field renames in Phase 2 change the model API. All callers must update. Property aliases can provide backward compatibility during transition.
- **Coupling**: No change — same fields, better names.
- **Data ownership**: No change.
- **Reversibility**: Phase 1 (removals) is low-risk since removed fields are unused. Phase 2 (renames) can be reversed by keeping old fields as aliases temporarily. Phase 3 (deprecated field removal) is higher-risk — requires migration verification first.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by audit)
- Review rounds: 1 (final validation)

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are internal to the Redis-backed Popoto models.

## Solution

### Key Elements

- **Phase 1 — Dead weight removal**: Delete `claude_code_session_id`, consolidate `sdlc_stages` into `stage_states`, fix `last_transition_at` deprecation comment
- **Phase 2 — Field renames**: Rename message ID triad and `chat_id_for_enrichment` with Popoto-safe migration (add new → backfill → update refs → remove old)
- **Phase 3 — Deprecated field removal**: Verify/run TelegramMessage migration, then remove 6 deprecated media fields

### Flow

**Phase 1** (safe removals) → **Phase 2** (renames with migration script) → **Phase 3** (deprecated field cleanup)

Each phase is a separate commit. Phase 2 requires a migration script. Phase 3 requires verifying the existing migration script has been run.

### Technical Approach

- Popoto derives Redis keys from field names, so renames require: add new field alongside old, write a migration script to copy data, update all code references, remove old field
- Phase 1 fields are safe to remove directly (no reads, or consolidation)
- No backward-compat aliases — we own all consumers, so rename completely in one pass
- Phase 3 runs `scripts/migrate_model_relationships.py` during the build if not already run

**Rename mapping (Phase 2):**

| Current | New | Rationale |
|---------|-----|-----------|
| `message_id` | `telegram_message_id` | Clarifies it's a Telegram message ID, not a generic one |
| `reply_to_msg_id` | `telegram_reply_to_message_id` | Full spelling, consistent prefix, clarifies Telegram origin |
| `trigger_message_id` | `telegram_message_key` | Clarifies it's a Popoto key to TelegramMessage, not an integer ID |
| `chat_id_for_enrichment` | `media_source_chat_id` | Describes what the field actually represents |

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is a rename/removal refactor

### Empty/Invalid Input Handling
- Migration script must handle records where old field is None (skip, don't error)
- `_get_sdlc_stages_dict()` fallback removal must not break when `stage_states` is None

### Error State Rendering
- No user-visible output changes

## Test Impact

- [ ] `tests/unit/test_model_relationships.py::test_claude_code_session_id_field_exists` — DELETE: field is being removed
- [ ] `tests/e2e/test_context_propagation.py::test_sdlc_stages_persist_as_json` — UPDATE: change to use `stage_states` instead of `sdlc_stages`
- [ ] `tests/e2e/test_context_propagation.py::test_chat_session_without_sdlc_stages` — UPDATE: rename field reference
- [ ] `tests/unit/test_pipeline_integrity.py::test_merge_in_sdlc_stages` — UPDATE: rename field reference
- [ ] `tests/integration/test_stage_aware_auto_continue.py::test_sdlc_stages_remaining_auto_continues` — UPDATE: rename field reference
- [ ] `tests/e2e/test_nudge_loop.py` — UPDATE: `chat_id_for_enrichment` → `media_source_chat_id`
- [ ] `tests/integration/test_job_queue_race.py` — UPDATE: `chat_id_for_enrichment` → `media_source_chat_id` (2 references)
- [ ] 24 test files reference `message_id`, `reply_to_msg_id`, or `trigger_message_id` — UPDATE: rename to new field names in Phase 2

## Rabbit Holes

- **Renaming `session_id` itself** — The Telegram-derived session identifier format (`tg_project_chatid_msgid`) is confusing but deeply embedded. Not worth touching in this cleanup.
- **Renaming `job_id` to `id`** — Already has a property alias, and AutoKeyField Redis keys depend on field name. Leave as-is.
- **Removing `sender` property alias** — Backward compat alias that's low-cost to keep. Not worth the churn.
- **Restructuring the model into separate ChatSession/DevSession classes** — Popoto doesn't support inheritance. Would require a different ORM or manual key management. Out of scope.

## Risks

### Risk 1: Migration script misses records
**Impact:** Old field has data, new field is empty. Code reads new field, gets None.
**Mitigation:** Migration script logs counts (migrated/skipped/failed). Run with `--dry-run` first. Add assertion check after migration: count records where old field is set but new field is None.

### Risk 2: Missed code references after rename
**Impact:** Runtime AttributeError on first access to old field name.
**Mitigation:** Complete rename in one pass — grep for old field names across entire codebase and update all references before committing. Run full test suite. No aliases needed since we own all consumers.

### Risk 3: Bridge downtime during migration
**Impact:** Active sessions could fail if model changes while bridge is running.
**Mitigation:** Strict deploy sequencing for Phase 2 renames:
1. Stop bridge (`./scripts/valor-service.sh stop`)
2. Deploy new code (with both old and new field definitions + property aliases)
3. Run migration script (`python scripts/migrate_agent_session_fields.py`)
4. Start bridge (`./scripts/valor-service.sh start`)

Phase 1 (removals of unused fields) is safe to deploy without stopping the bridge.

### Risk 4: Orphaned Redis hash fields after rename
**Impact:** Old field names remain in Redis hashes, wasting memory and causing confusion during debugging.
**Mitigation:** Migration script includes a cleanup step that `HDEL`s old field names from each session hash after copying values to new field names. Cleanup runs only after verification that new fields are populated.

## Race Conditions

No race conditions identified — field renames are schema-level changes deployed during maintenance windows. The migration script operates on stored data, not live sessions. Bridge restart ensures all running code uses the new field names.

## No-Gos (Out of Scope)

- Renaming `session_id`, `job_id`, or `sender_name` — low confusion, high churn
- Restructuring AgentSession into separate models per session_type
- Changing Popoto to support field name aliases at the ORM level
- Removing the `sender` property alias (low-cost backward compat)
- Cleaning up `_JOB_FIELDS` tuple ordering or comments (separate chore)

## Update System

The `/update` skill must include migration commands in its post-pull sequence for one release cycle. Deploy sequencing for Phase 2:

```bash
./scripts/valor-service.sh stop                        # 1. Stop bridge
git pull && pip install -r requirements.txt             # 2. Deploy new code
python scripts/migrate_agent_session_fields.py          # 3. Run field rename migration
python scripts/migrate_model_relationships.py           # 4. Phase 3 prerequisite (if not already run)
./scripts/valor-service.sh start                        # 5. Restart bridge
```

Phase 1 (dead weight removal) does not require bridge stop — removed fields are unused.

## Agent Integration

No agent integration required — AgentSession is not exposed through any MCP server. The agent interacts with sessions through the bridge and job queue, which will use the new field names after code update.

## Documentation

- [ ] Update `docs/features/redis-models.md` — field name references throughout
- [ ] Update `docs/features/session-isolation.md` — references `trigger_message_id`
- [ ] Update `docs/features/steering-queue.md` — references `reply_to_msg_id`
- [ ] Update inline comments on `models/agent_session.py` — field grouping comments

## Success Criteria

- [ ] `claude_code_session_id` field does not exist in `models/agent_session.py`
- [ ] `sdlc_stages` field does not exist; `create_dev()` writes to `stage_states`
- [ ] `last_transition_at` has no "Deprecated" comment
- [ ] `grep -rn 'message_id' models/agent_session.py` shows only `telegram_message_id`, `telegram_reply_to_message_id`, and `telegram_message_key`
- [ ] `grep -rn 'chat_id_for_enrichment' .` returns zero results
- [ ] `grep -rn 'has_media\|media_type\|youtube_urls\|non_youtube_urls' models/agent_session.py` returns zero results (Phase 3)
- [ ] New test verifies all `_JOB_FIELDS` entries are valid AgentSession field names (prevents silent drift)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (phase-1)**
  - Name: dead-weight-builder
  - Role: Remove unused fields, consolidate sdlc_stages, fix deprecation comment
  - Agent Type: builder
  - Resume: true

- **Builder (phase-2)**
  - Name: rename-builder
  - Role: Create migration script, rename fields, update all references
  - Agent Type: builder
  - Resume: true

- **Builder (phase-3)**
  - Name: deprecation-builder
  - Role: Verify migration, remove deprecated media fields
  - Agent Type: builder
  - Resume: true

- **Validator (all-phases)**
  - Name: field-validator
  - Role: Verify no stale references, all tests pass, migration data integrity
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update all docs referencing renamed fields
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Phase 1 — Remove Dead Weight
- **Task ID**: build-phase-1
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_model_relationships.py tests/e2e/test_context_propagation.py tests/unit/test_pipeline_integrity.py tests/integration/test_stage_aware_auto_continue.py -x`
- **Assigned To**: dead-weight-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `claude_code_session_id` field from `models/agent_session.py`
- Remove `claude_code_session_id` from `_JOB_FIELDS` in `agent/job_queue.py`
- Delete `test_claude_code_session_id_field_exists` test
- Remove `sdlc_stages` field from `models/agent_session.py`
- Update `create_dev()` to write `stage_states` instead of `sdlc_stages`
- Simplify `_get_sdlc_stages_dict()` to only read `stage_states`
- Update `is_sdlc` property to remove `sdlc_stages` fallback path
- Remove `sdlc_stages` from `_JOB_FIELDS` in `agent/job_queue.py`
- Update all tests referencing `sdlc_stages` to use `stage_states`
- Remove "Deprecated:" comment from `last_transition_at` field
- Commit: "Remove unused claude_code_session_id, consolidate sdlc_stages into stage_states"

### 2. Validate Phase 1
- **Task ID**: validate-phase-1
- **Depends On**: build-phase-1
- **Assigned To**: field-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -rn 'claude_code_session_id' . --include='*.py'` returns zero results
- `grep -rn 'sdlc_stages' . --include='*.py'` returns zero results (excluding git history)
- `grep -n 'Deprecated' models/agent_session.py` does not match `last_transition_at`
- Full test suite passes: `pytest tests/ -x -q`

### 3. Phase 2 — Rename Fields
- **Task ID**: build-phase-2
- **Depends On**: validate-phase-1
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- Add new fields alongside old: `telegram_message_id`, `telegram_reply_to_message_id`, `telegram_message_key`, `media_source_chat_id`
- Write migration script `scripts/migrate_agent_session_fields.py` to copy old field values to new fields and `HDEL` old field names from Redis hashes
- Update all code references in `agent/`, `bridge/`, `tools/`, `models/`, `scripts/`, `monitoring/` to use new field names
- Update all test files (24 files) to use new field names
- Remove old field definitions entirely — no aliases, no backward compat (we own all consumers)
- Commit: "Rename AgentSession message ID fields and chat_id_for_enrichment for clarity"

### 4. Validate Phase 2
- **Task ID**: validate-phase-2
- **Depends On**: build-phase-2
- **Assigned To**: field-validator
- **Agent Type**: validator
- **Parallel**: false
- Zero references to old field names (`message_id`, `reply_to_msg_id`, `trigger_message_id`, `chat_id_for_enrichment`) in any `.py` file
- Migration script handles None values, logs counts, and `HDEL`s old hash field names
- Full test suite passes: `pytest tests/ -x -q`
- Lint passes: `python -m ruff check .`

### 5. Phase 3 — Remove Deprecated Media Fields
- **Task ID**: build-phase-3
- **Depends On**: validate-phase-2
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: deprecation-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `scripts/migrate_model_relationships.py --dry-run` to check migration status
- If not yet run, execute migration: `scripts/migrate_model_relationships.py`
- Remove deprecated fields from `models/agent_session.py`: `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `classification_type`, `classification_confidence`
- Remove fallback paths in `agent/job_queue.py` enrichment logic — read only from TelegramMessage
- Remove deprecated field entries from `_JOB_FIELDS`
- Update tests that reference these deprecated fields
- Commit: "Remove deprecated media fields from AgentSession after TelegramMessage migration"

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-phase-3
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/redis-models.md` with new field names
- Update `docs/features/session-isolation.md`
- Update `docs/features/steering-queue.md`
- Update field comments in `models/agent_session.py`

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
| No old message_id | `grep -rn '\.message_id\b' models/agent_session.py \| grep -v telegram_message_id \| grep -v telegram_reply_to_message_id \| grep -v telegram_message_key` | exit code 1 |
| No claude_code_session_id | `grep -rn 'claude_code_session_id' . --include='*.py'` | exit code 1 |
| No sdlc_stages | `grep -rn 'sdlc_stages' . --include='*.py'` | exit code 1 |
| No chat_id_for_enrichment | `grep -rn 'chat_id_for_enrichment' . --include='*.py'` | exit code 1 |
| No deprecated media fields | `grep -rn 'has_media\|media_type\|youtube_urls\|non_youtube_urls' models/agent_session.py` | exit code 1 |

---

## Open Questions

1. **Should Phase 2 renames keep the old Popoto fields alive permanently as read-only aliases?** The plan proposes temporary `@property` aliases removed in Phase 2 cleanup. But if external tools or Redis inspection scripts reference old field names, they'd silently break. Is there anything outside this codebase that reads AgentSession Redis keys directly?

2. **Should we run `scripts/migrate_model_relationships.py` as a pre-requisite before starting this work?** The recon found it was never executed. Running it now would let Phase 3 proceed immediately. Or should Phase 3 be deferred to a follow-up issue?

3. **Are the proposed new names correct?** The rename mapping is:
   - `message_id` → `tg_message_id`
   - `reply_to_msg_id` → `tg_reply_to_msg_id`
   - `trigger_message_id` → `telegram_message_key`
   - `chat_id_for_enrichment` → `media_source_chat_id`

   Do any of these feel wrong or could be clearer?
