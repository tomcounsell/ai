---
status: Draft
type: refactor
appetite: Medium
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/599
---

# Unify Persona Vocabulary: Eliminate ChatMode and Q&A Naming

## Problem

The persona system uses two incompatible vocabularies for the same concept and carries legacy "Q&A" naming that obscures intent.

**Redundant enum layer:** `config/enums.py` defines both `PersonaType` (DEVELOPER, PROJECT_MANAGER, TEAMMATE) and `ChatMode` (QA, PM, DEV). `ChatMode` is a pure alias -- `PERSONA_TO_MODE` in `bridge/routing.py` maps between them 1:1. This indirection adds complexity with zero value.

**Legacy "Q&A" naming throughout:** The Teammate persona is still called "Q&A mode" across ~42 files: field names (`qa_mode`, `session_mode` storing `"qa"`), file names (`qa_handler.py`, `qa_metrics.py`), constants (`QA_MAX_NUDGE_COUNT`, `QA_CONFIDENCE_THRESHOLD`), display labels (`"Q&A"`), intent classifier labels (`"qa"` vs `"work"`), and documentation.

**Dashboard inconsistency:** The "Active Groups" table shows raw config values (`developer`, `project-manager`, `teammate`). The "Sessions" table shows derived labels (`Dev`, `PM`, `Q&A`). Both columns are labeled "Persona" but display different values.

**Heavyweight backward-compat layer:** `models/agent_session.py` has a 3-tier fallback in the `qa_mode` property: check `session_mode`, check `_qa_mode_legacy` Popoto field, then raw Redis `hget` for the original `qa_mode` key. This was a migration bridge from PR #594 that now adds permanent maintenance burden.

## Scope

| Area | Files affected | Change type |
|------|---------------|-------------|
| Enum deletion | `config/enums.py` | Delete `ChatMode`, keep `PersonaType` |
| Model cleanup | `models/agent_session.py` | Remove `qa_mode` property/setter, `_qa_mode_legacy` field, `session_mode` stores `PersonaType` values |
| Routing | `bridge/routing.py` | Delete `PERSONA_TO_MODE`, rename `resolve_chat_mode` to `resolve_persona`, return `PersonaType` |
| Bridge | `bridge/telegram_bridge.py` | Replace `ChatMode` imports/usage with `PersonaType` |
| SDK client | `agent/sdk_client.py` | Replace all `ChatMode` references with `PersonaType` |
| File renames | `agent/qa_handler.py`, `agent/qa_metrics.py` | Rename to `teammate_handler.py`, `teammate_metrics.py` |
| Constants | `agent/qa_handler.py`, `agent/intent_classifier.py` | Rename `QA_MAX_NUDGE_COUNT`, `QA_CONFIDENCE_THRESHOLD` |
| Intent classifier | `agent/intent_classifier.py` | Change labels from `"qa"`/`"work"` to `"teammate"`/`"work"` |
| Summarizer | `bridge/summarizer.py` | Replace `ChatMode.QA` checks with `PersonaType.TEAMMATE` |
| Job queue | `agent/job_queue.py` | Replace `ChatMode` imports/usage |
| Context builder | `bridge/context.py` | Update Q&A access restriction strings |
| Dashboard | `ui/data/sdlc.py`, `ui/templates/` | Unify persona display labels and badge colors |
| Redis migration | `scripts/migrate_persona_values.py` | Migrate `session_mode` values: `"qa"` to `"teammate"`, `"pm"` to `"project-manager"`, `"dev"` to `"developer"` |
| Tests | 6 test files | Rename and update assertions |
| Docs | 4 doc files | Rename and update references |

## Prior Art

- **#562**: Original issue defining vision for unified persona vocabulary. Partially implemented.
- **#594**: PR that created `config/enums.py` with both PersonaType and ChatMode, replaced `qa_mode` bool with `session_mode` string field. Did not unify the two enum types or eliminate "Q&A" naming.
- **#596**: Follow-up PR replacing remaining string comparisons with ChatMode enum members. Did not address the vocabulary mismatch.
- **`scripts/migrate_agent_session_fields.py`**: Existing migration script pattern for Redis field renames.

## Architectural Impact

- **Interface changes**: `resolve_chat_mode()` renamed to `resolve_persona()`, returns `PersonaType` instead of `ChatMode`. All callers (3 production sites) updated.
- **Storage change**: `session_mode` field stores `PersonaType` values (`"teammate"`, `"project-manager"`, `"developer"`) instead of `ChatMode` values (`"qa"`, `"pm"`, `"dev"`). Migration script handles existing sessions.
- **Import changes**: All `from config.enums import ChatMode` become `from config.enums import PersonaType` where not already imported.
- **Coupling**: Low. Most changes are mechanical replacements. The `PERSONA_TO_MODE` indirection is the only cross-cutting concern, and it is being deleted.
- **Reversibility**: Low risk. If needed, `ChatMode` could be re-added, but the migration script is one-way for Redis values.

## Appetite

**Size:** Medium (42 files touched, but changes are mechanical; one migration script)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a vocabulary unification -- conceptually simple but wide-reaching. The migration script and dashboard changes are the only non-mechanical parts.

## Prerequisites

No prerequisites. All changes are internal refactoring with no external dependencies.

## Solution

### Key Elements

1. **Delete `ChatMode` enum** from `config/enums.py`. `PersonaType` becomes the only persona enum.
2. **Remove backward-compat layers** from `agent_session.py`: delete `qa_mode` property/setter, `_qa_mode_legacy` field, Redis `hget` fallback.
3. **Rename `resolve_chat_mode()` to `resolve_persona()`** in `bridge/routing.py`, returning `PersonaType` directly. Delete `PERSONA_TO_MODE` mapping.
4. **Rename files**: `qa_handler.py` to `teammate_handler.py`, `qa_metrics.py` to `teammate_metrics.py`.
5. **Rename constants**: `QA_MAX_NUDGE_COUNT` to `TEAMMATE_MAX_NUDGE_COUNT`, `QA_CONFIDENCE_THRESHOLD` to `TEAMMATE_CONFIDENCE_THRESHOLD`.
6. **Update intent classifier** labels from `"qa"`/`"work"` to `"teammate"`/`"work"`.
7. **Unify dashboard display** so both tables show "Developer", "Project Manager", "Teammate" with matching badge colors.
8. **Redis migration script** to convert existing `session_mode` values.
9. **Rename Redis metrics keys** from `qa_metrics:*` to `teammate_metrics:*`.

### Technical Approach

#### 1. Delete ChatMode and update enums.py

Remove the `ChatMode` class entirely. Update the module docstring to remove `ChatMode` from the import example. `PersonaType` is already defined with the correct values.

#### 2. Update agent_session.py

- Change import from `ChatMode` to only use `PersonaType`
- Change `session_mode` field comment to reference `PersonaType` values
- Delete `_qa_mode_legacy` field entirely
- Delete `qa_mode` property (getter and setter)
- All code that checked `session_mode == ChatMode.QA` now checks `session_mode == PersonaType.TEAMMATE`

#### 3. Rename resolve_chat_mode to resolve_persona in routing.py

- Delete `PERSONA_TO_MODE` dict
- Rename `resolve_chat_mode()` to `resolve_persona()`
- Return `PersonaType` values directly instead of `ChatMode` values
- DMs return `PersonaType.TEAMMATE` (was `ChatMode.QA`)
- Title prefix `"Dev:"` returns `PersonaType.DEVELOPER` (was `ChatMode.DEV`)
- Title prefix `"PM:"` returns `PersonaType.PROJECT_MANAGER` (was `ChatMode.PM`)
- Persona config lookup returns `PersonaType` member directly (no mapping needed)

#### 4. Update all callers of resolve_chat_mode

Three production call sites:

**`bridge/telegram_bridge.py`** (line ~1410-1414):
```python
# Before:
_chat_mode = resolve_chat_mode(project, chat_title, is_dm=is_dm)
if _chat_mode == ChatMode.DEV:
# After:
_persona = resolve_persona(project, chat_title, is_dm=is_dm)
if _persona == PersonaType.DEVELOPER:
```

**`agent/sdk_client.py`** (~20 references to ChatMode):
- Replace all `ChatMode.QA` with `PersonaType.TEAMMATE`
- Replace all `ChatMode.PM` with `PersonaType.PROJECT_MANAGER`
- Replace all `ChatMode.DEV` with `PersonaType.DEVELOPER`
- Rename local variables from `_chat_mode`/`project_mode` where they store ChatMode values

**`bridge/routing.py`** (line ~753-754 in `should_respond_async`):
```python
# Before:
chat_mode = resolve_chat_mode(project, chat_title, is_dm=False)
if chat_mode == ChatMode.QA:
# After:
persona = resolve_persona(project, chat_title, is_dm=False)
if persona == PersonaType.TEAMMATE:
```

#### 5. Rename qa_handler.py to teammate_handler.py

- `git mv agent/qa_handler.py agent/teammate_handler.py`
- Rename `build_qa_instructions()` to `build_teammate_instructions()`
- Rename `QA_MAX_NUDGE_COUNT` to `TEAMMATE_MAX_NUDGE_COUNT`
- Update module docstring to use "Teammate" instead of "Q&A"
- Update all importers: `agent/job_queue.py`, `agent/sdk_client.py`

#### 6. Rename qa_metrics.py to teammate_metrics.py

- `git mv agent/qa_metrics.py agent/teammate_metrics.py`
- Rename Redis key prefix from `qa_metrics` to `teammate_metrics`
- Update all internal `"qa"` string references to `"teammate"`
- Update all importers: `agent/sdk_client.py`

#### 7. Update intent_classifier.py

- Rename `QA_CONFIDENCE_THRESHOLD` to `TEAMMATE_CONFIDENCE_THRESHOLD`
- Change classifier prompt: replace all `"qa"` labels with `"teammate"` in examples, rules, and format description
- Update `_parse_classifier_response()` to accept `"teammate"` instead of `"qa"`
- Update `IntentResult.is_qa` property to `is_teammate` (checks `intent == "teammate"`)
- Update `IntentResult.is_work` to reference `TEAMMATE_CONFIDENCE_THRESHOLD`

#### 8. Update bridge/summarizer.py

Four locations checking for Q&A mode:
- Replace `ChatMode.QA` with `PersonaType.TEAMMATE`
- Replace `qa_mode` attribute checks with `session_mode == PersonaType.TEAMMATE`
- Update string literals: `"qa_mode=True"` context signal becomes `"teammate_mode=True"` or just `"persona=teammate"`
- Update natural-language references from "Q&A" to "Teammate" in prompts

#### 9. Update agent/job_queue.py

- Replace `ChatMode` import with `PersonaType`
- Replace all `ChatMode.QA` checks with `PersonaType.TEAMMATE`
- Replace `qa_mode` attribute access with `session_mode` comparison
- Update `qa_handler` import to `teammate_handler`

#### 10. Update bridge/context.py

- Replace "Q&A-only access" string with "Teammate-only access" (or "read-only Teammate access")
- Replace "can only help with Q&A" with "can only help with informational queries"

#### 11. Unify dashboard display

**`ui/data/sdlc.py`** (`_resolve_persona_display`):
```python
# Before: returns "Q&A", "Dev", "PM" based on ChatMode
# After: returns "Developer", "Project Manager", "Teammate" based on PersonaType
def _resolve_persona_display(session) -> str | None:
    mode = getattr(session, "session_mode", None)
    if mode == PersonaType.TEAMMATE:
        return "Teammate"
    raw = getattr(session, "session_type", None)
    if raw == "dev":
        return "Developer"
    if raw == "chat":
        return "Project Manager"
    return raw
```

**`ui/templates/_partials/sessions_table.html`** (line 56):
```html
<!-- Before: badge-blue for Dev, badge-green for Q&A, badge-purple for PM -->
<!-- After: badge-blue for Developer, badge-green for Teammate, badge-purple for Project Manager -->
```

**`ui/templates/index.html`** (lines 57-59):
Already shows raw PersonaType values (`developer`, `project-manager`, `teammate`). Update badge colors to match:
- `developer` -> `badge-blue`
- `project-manager` -> `badge-purple`
- `teammate` -> `badge-green`

#### 12. Redis migration script

Create `scripts/migrate_persona_values.py` following the pattern of `scripts/migrate_agent_session_fields.py`:

```python
VALUE_MIGRATIONS = {
    "session_mode": {
        "qa": "teammate",
        "pm": "project-manager",
        "dev": "developer",
    }
}
```

For each `AgentSession` in Redis:
1. Read `session_mode` value
2. If it matches an old value, write the new value
3. Delete `_qa_mode_legacy` field from the Redis hash (HDEL)
4. Delete legacy `qa_mode` field from the Redis hash (HDEL)

Also delete old `qa_metrics:*` Redis keys (ephemeral counters, not worth preserving).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `resolve_persona()` returns `None` for unconfigured groups (same as before, no behavior change)
- [ ] Migration script handles sessions with `None` session_mode gracefully (skip, don't crash)
- [ ] Migration script handles sessions that already have new-format values (idempotent)

### Empty/Invalid Input Handling
- [ ] Intent classifier handles unparseable response by defaulting to "work" (existing behavior preserved)
- [ ] Dashboard display handles unknown session_mode values (show raw value)

### Error State Rendering
- [ ] Migration script `--dry-run` mode logs what would change without modifying Redis

## Test Impact

- [ ] `tests/unit/test_qa_handler.py` -- REPLACE: rename to `tests/unit/test_teammate_handler.py`, update function names and imports
- [ ] `tests/unit/test_qa_metrics.py` -- REPLACE: rename to `tests/unit/test_teammate_metrics.py`, update Redis key prefixes and string assertions
- [ ] `tests/unit/test_qa_nudge_cap.py` -- REPLACE: rename to `tests/unit/test_teammate_nudge_cap.py`, update constant names and imports
- [ ] `tests/unit/test_intent_classifier.py` -- UPDATE: change `"qa"` assertions to `"teammate"`, rename `is_qa` to `is_teammate`
- [ ] `tests/unit/test_routing_mode.py` -- UPDATE: replace `ChatMode` references with `PersonaType`, rename `resolve_chat_mode` to `resolve_persona`
- [ ] `tests/unit/test_config_driven_routing.py` -- UPDATE: replace `ChatMode` references with `PersonaType`, rename function under test
- [ ] `tests/unit/test_enums.py` -- UPDATE: remove `ChatMode` tests, verify `PersonaType` members unchanged
- [ ] `tests/unit/test_summarizer.py` -- UPDATE: replace `ChatMode.QA` with `PersonaType.TEAMMATE`, update `qa_mode` references
- [ ] `tests/unit/test_nudge_loop.py` -- UPDATE: replace `ChatMode` references
- [ ] `tests/unit/test_cross_wire_fixes.py` -- UPDATE: replace `ChatMode`/`qa_mode` references
- [ ] `tests/unit/test_work_request_classifier.py` -- UPDATE: verify no `ChatMode` usage remains
- [ ] `tests/integration/test_agent_session_lifecycle.py` -- UPDATE: remove `qa_mode` property tests, add `session_mode` PersonaType tests

## Rabbit Holes

- **Renaming `intent_classifier.py` to `teammate_classifier.py`**: The file classifies intent (teammate vs work), not teammates. The current file name accurately describes its purpose. Do not rename.
- **Phased migration with dual-read**: The backward-compat code is exactly what we are eliminating. Adding more compat layers contradicts the goal. Clean cut with migration script.
- **Renaming `ClassificationType` enum**: `ClassificationType.QUESTION` is fine -- it classifies the work-request routing decision, not the persona. Leave it alone.
- **Changing PersonaType string values**: `PersonaType.TEAMMATE = "teammate"` is already correct. Do not change enum values.
- **Modifying projects.json schema**: The persona field in projects.json already uses PersonaType values. No config changes needed.

## Risks

### Risk 1: In-flight sessions with old session_mode values
**Impact:** Sessions created before migration but still active would have `session_mode="qa"` which no longer matches any comparison.
**Mitigation:** Run migration script before deploying new code. Sessions have 90-day TTL so stragglers expire naturally. The migration script explicitly updates all existing records.

### Risk 2: Redis metrics key rename loses counters
**Impact:** Dashboard or monitoring that reads `qa_metrics:*` keys would see zeros.
**Mitigation:** Metrics are ephemeral counters for operational monitoring, not historical data. Acceptable to reset. Migration script deletes old keys explicitly.

### Risk 3: Intent classifier prompt change affects classification accuracy
**Impact:** Changing prompt labels from "qa" to "teammate" could shift classification behavior.
**Mitigation:** The label name is arbitrary for the LLM -- "teammate" is equally clear as "qa" for the classification task. Examples in the prompt are updated consistently.

## Race Conditions

No race conditions. The migration script runs once before deployment. All code changes are deployed atomically in one PR. There is no concurrent reader/writer concern because the old code (reading `ChatMode.QA`) and new code (reading `PersonaType.TEAMMATE`) are never running simultaneously.

## No-Gos (Out of Scope)

- Modifying `PersonaType` enum values (they are already correct)
- Renaming `ClassificationType` or its members
- Changing `projects.json` schema or persona field values
- Renaming `intent_classifier.py` file (name is accurate)
- Adding new persona types
- Changing session_type enum (CHAT/DEV is a separate concept from persona)

## Update System

No update system changes required. This is an internal vocabulary refactoring. The migration script runs once manually after deployment. No new dependencies, no config file changes, no new packages.

## Agent Integration

No agent integration required. No MCP server changes, no `.mcp.json` modifications, no new tools exposed. The agent's behavior is unchanged -- only internal naming is unified.

## Documentation

- [ ] Rename `docs/features/chatsession-qa-mode.md` to `docs/features/chatsession-teammate-mode.md` and update all content
- [ ] Update `docs/features/standardized-enums.md` to remove ChatMode references, document PersonaType as single source of truth
- [ ] Update `docs/features/config-driven-chat-mode.md` to reference `resolve_persona()` instead of `resolve_chat_mode()`
- [ ] Update `docs/features/chat-dev-session-architecture.md` to replace Q&A references with Teammate
- [ ] Update `docs/features/agent-session-model.md` to remove `qa_mode` property documentation
- [ ] Update `docs/features/personas.md` to reflect unified vocabulary
- [ ] Update `docs/features/job-scheduling.md` to remove Q&A references
- [ ] Update `docs/features/summarizer-format.md` to replace Q&A references
- [ ] Update `docs/features/README.md` index table (renamed doc)
- [ ] Update `CLAUDE.md` if any Q&A references remain

## Step by Step Tasks

### 1. Delete ChatMode enum and update enums.py
- **Task ID**: delete-chatmode
- **Depends On**: none
- **Validates**: `python -c "from config.enums import PersonaType; assert not hasattr(__import__('config.enums'), 'ChatMode')"`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Remove `ChatMode` class from `config/enums.py`
- Update module docstring to remove `ChatMode` from import example

### 2. Clean up agent_session.py
- **Task ID**: clean-agent-session
- **Depends On**: delete-chatmode
- **Validates**: `grep -c "qa_mode\|_qa_mode_legacy\|ChatMode" models/agent_session.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `ChatMode` from imports
- Delete `_qa_mode_legacy` field
- Delete `qa_mode` property (getter) and `qa_mode` setter
- Update `session_mode` field comment to reference PersonaType values
- Replace any remaining `ChatMode` references with `PersonaType`

### 3. Rename resolve_chat_mode to resolve_persona in routing.py
- **Task ID**: rename-resolve-function
- **Depends On**: delete-chatmode
- **Validates**: `grep -c "resolve_chat_mode\|PERSONA_TO_MODE\|ChatMode" bridge/routing.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `PERSONA_TO_MODE` mapping dict
- Rename `resolve_chat_mode()` to `resolve_persona()`
- Return `PersonaType` values directly: `PersonaType.TEAMMATE` for DMs, `PersonaType.DEVELOPER` for "Dev:" prefix, `PersonaType.PROJECT_MANAGER` for "PM:" prefix
- Update persona config lookup to return the PersonaType member directly (no mapping needed -- the config already stores PersonaType values)
- Update `should_respond_async()` to use `resolve_persona()` and `PersonaType.TEAMMATE`

### 4. Rename qa_handler.py to teammate_handler.py
- **Task ID**: rename-handler
- **Depends On**: delete-chatmode
- **Validates**: `test -f agent/teammate_handler.py && ! test -f agent/qa_handler.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- `git mv agent/qa_handler.py agent/teammate_handler.py`
- Rename `build_qa_instructions()` to `build_teammate_instructions()`
- Rename `QA_MAX_NUDGE_COUNT` to `TEAMMATE_MAX_NUDGE_COUNT`
- Update module docstring: "Teammate handler" instead of "Q&A handler"
- Replace "Q&A" in instruction text with "Teammate"/"informational query"

### 5. Rename qa_metrics.py to teammate_metrics.py
- **Task ID**: rename-metrics
- **Depends On**: delete-chatmode
- **Validates**: `test -f agent/teammate_metrics.py && ! test -f agent/qa_metrics.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- `git mv agent/qa_metrics.py agent/teammate_metrics.py`
- Rename Redis key prefix from `"qa_metrics"` to `"teammate_metrics"`
- Update all internal string references from `"qa"` to `"teammate"` in function parameters and log messages
- Update module docstring

### 6. Update intent_classifier.py
- **Task ID**: update-classifier
- **Depends On**: delete-chatmode
- **Validates**: `grep -c '"qa"' agent/intent_classifier.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `QA_CONFIDENCE_THRESHOLD` to `TEAMMATE_CONFIDENCE_THRESHOLD`
- Replace all `"qa"` labels with `"teammate"` in `CLASSIFIER_PROMPT` (rules, examples, format)
- Update `_parse_classifier_response()`: accept `"teammate"` instead of `"qa"`
- Rename `IntentResult.is_qa` to `IntentResult.is_teammate`
- Update `IntentResult.is_work` to use `TEAMMATE_CONFIDENCE_THRESHOLD`
- Update module docstring

### 7. Update bridge callers (telegram_bridge.py, summarizer.py, context.py)
- **Task ID**: update-bridge-callers
- **Depends On**: rename-resolve-function, rename-handler
- **Validates**: `grep -c "ChatMode\|qa_mode\|resolve_chat_mode" bridge/telegram_bridge.py bridge/summarizer.py bridge/context.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- **telegram_bridge.py**: Replace `ChatMode` import with `PersonaType`, replace `resolve_chat_mode` with `resolve_persona`, replace all `ChatMode.DEV`/`ChatMode.PM`/`ChatMode.QA` with PersonaType equivalents
- **summarizer.py**: Replace `ChatMode.QA` checks with `PersonaType.TEAMMATE`, replace `qa_mode` attribute checks with `session_mode == PersonaType.TEAMMATE`, update context signal string from `"qa_mode=True"` to `"persona=teammate"`
- **context.py**: Replace "Q&A-only access" and "can only help with Q&A" strings with Teammate-oriented language

### 8. Update agent callers (sdk_client.py, job_queue.py)
- **Task ID**: update-agent-callers
- **Depends On**: rename-resolve-function, rename-handler, rename-metrics, update-classifier
- **Validates**: `grep -c "ChatMode\|qa_mode\|qa_handler\|qa_metrics\|QA_" agent/sdk_client.py agent/job_queue.py` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- **sdk_client.py**: Replace all ~20 `ChatMode` references with `PersonaType`, update `resolve_chat_mode` calls to `resolve_persona`, replace `qa_handler`/`qa_metrics` imports
- **job_queue.py**: Replace `ChatMode` import, update `qa_mode`/`ChatMode.QA` checks, update `qa_handler` import

### 9. Unify dashboard display
- **Task ID**: unify-dashboard
- **Depends On**: delete-chatmode
- **Validates**: `grep -c 'Q&A' ui/data/sdlc.py ui/templates/_partials/sessions_table.html ui/templates/index.html` returns 0
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Update `_resolve_persona_display()` in `ui/data/sdlc.py` to return "Developer", "Project Manager", "Teammate"
- Update `sessions_table.html` badge logic: badge-blue for Developer, badge-purple for Project Manager, badge-green for Teammate
- Update `index.html` Active Groups table: ensure badge-green for `teammate` persona (already nearly correct, just verify color consistency)

### 10. Create Redis migration script
- **Task ID**: create-migration
- **Depends On**: none
- **Validates**: `python scripts/migrate_persona_values.py --dry-run` runs successfully
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/migrate_persona_values.py` following `scripts/migrate_agent_session_fields.py` pattern
- Migrate `session_mode` values: `"qa"` to `"teammate"`, `"pm"` to `"project-manager"`, `"dev"` to `"developer"`
- HDEL `_qa_mode_legacy` field from all session hashes
- HDEL legacy `qa_mode` field from all session hashes
- Delete old `qa_metrics:*` Redis keys
- Support `--dry-run` flag

### 11. Update and rename test files
- **Task ID**: update-tests
- **Depends On**: update-bridge-callers, update-agent-callers, update-classifier, unify-dashboard
- **Validates**: `pytest tests/unit/ -x -q`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- `git mv tests/unit/test_qa_handler.py tests/unit/test_teammate_handler.py` and update contents
- `git mv tests/unit/test_qa_metrics.py tests/unit/test_teammate_metrics.py` and update contents
- `git mv tests/unit/test_qa_nudge_cap.py tests/unit/test_teammate_nudge_cap.py` and update contents
- Update `test_intent_classifier.py`: change `"qa"` assertions to `"teammate"`, `is_qa` to `is_teammate`
- Update `test_routing_mode.py`: replace `ChatMode` with `PersonaType`, `resolve_chat_mode` with `resolve_persona`
- Update `test_config_driven_routing.py`: replace `ChatMode` with `PersonaType`
- Update `test_enums.py`: remove `ChatMode` tests
- Update `test_summarizer.py`: replace `ChatMode.QA` with `PersonaType.TEAMMATE`
- Update `test_nudge_loop.py`, `test_cross_wire_fixes.py`, `test_work_request_classifier.py` as needed
- Update `tests/integration/test_agent_session_lifecycle.py`

### 12. Validate full test suite and lint
- **Task ID**: validate-all
- **Depends On**: update-tests, create-migration
- **Validates**: all verification checks pass
- **Assigned To**: builder
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`
- Run `grep -r "ChatMode" --include="*.py"` and verify zero results
- Run `grep -r "qa_mode" --include="*.py"` and verify zero results (excluding migration script)
- Run `grep -r "Q&A" --include="*.py" --include="*.html"` and verify zero results
- Verify all acceptance criteria from issue #599

### 13. Update documentation
- **Task ID**: update-docs
- **Depends On**: validate-all
- **Validates**: `grep -r "Q&A\|ChatMode\|qa_mode\|resolve_chat_mode" docs/features/` returns zero results
- **Assigned To**: builder
- **Agent Type**: documentarian
- **Parallel**: false
- Rename and update all documentation files listed in the Documentation section
- Update `docs/features/README.md` index table

## Success Criteria

- [ ] `ChatMode` enum does not exist in `config/enums.py`
- [ ] `grep -r "ChatMode" --include="*.py"` returns zero results
- [ ] `grep -r "qa_mode" --include="*.py"` returns zero results (excluding migration script)
- [ ] `grep -r "Q&A" --include="*.py" --include="*.html"` returns zero results
- [ ] `session_mode` field stores PersonaType string values ("teammate", "project-manager", "developer")
- [ ] No `_qa_mode_legacy` field on AgentSession
- [ ] No raw Redis `hget` fallback in AgentSession
- [ ] Files `qa_handler.py` and `qa_metrics.py` do not exist; `teammate_handler.py` and `teammate_metrics.py` do
- [ ] Dashboard "Active Groups" and "Sessions" tables both display "Developer", "Project Manager", "Teammate" as persona labels
- [ ] Same badge color scheme used in both dashboard tables for each persona
- [ ] `resolve_chat_mode()` does not exist; `resolve_persona()` returns `PersonaType` members
- [ ] Intent classifier uses "teammate"/"work" labels (not "qa"/"work")
- [ ] All tests pass (`pytest tests/unit/ -n auto && pytest tests/integration/`)
- [ ] `python -m ruff check . && python -m ruff format --check .` passes

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No ChatMode anywhere | `grep -r "ChatMode" --include="*.py"` | zero matches |
| No qa_mode anywhere | `grep -r "qa_mode" --include="*.py"` | zero matches (excl. migration) |
| No Q&A in code/templates | `grep -r "Q&A" --include="*.py" --include="*.html"` | zero matches |
| No old files exist | `test ! -f agent/qa_handler.py && test ! -f agent/qa_metrics.py` | exit code 0 |
| Migration runs clean | `python scripts/migrate_persona_values.py --dry-run` | exit code 0 |
| PersonaType is importable | `python -c "from config.enums import PersonaType; print(list(PersonaType))"` | three members |
| resolve_persona exists | `python -c "from bridge.routing import resolve_persona"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions. The issue is well-specified with explicit constraints (no phased migration, PersonaType as single source of truth, one atomic PR). All affected files have been audited and the changes are mechanical.
