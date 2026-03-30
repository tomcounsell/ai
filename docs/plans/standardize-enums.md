---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/562
last_comment_id:
---

# Standardize Session Type and Persona Magic Strings

## Problem

Session types, persona identifiers, and classification types are scattered as magic strings across the codebase. The same concept is represented differently in different places, making refactoring brittle and bugs easy to introduce.

**Current behavior:**
- `session_type` uses plain string constants `SESSION_TYPE_CHAT = "chat"` and `SESSION_TYPE_DEV = "dev"` in `models/agent_session.py`, but most consumers compare against raw strings (`_session_type == "chat"`)
- The dashboard sessions table has a "Type" column that displays "dev", "PM", or "Q&A" -- these are display values derived from `session_type` + `qa_mode`, but the column name ("Type") collides with the machine config table's "Persona" column, which represents the same concept
- `classification_type` uses `"sdlc"` and `"question"` as raw strings across bridge/routing.py, bridge/telegram_bridge.py, and agent/sdk_client.py
- `qa_mode` is a confusingly named boolean -- it does not control whether Q&A is "allowed", it indicates the session IS a Q&A session
- Persona values (`"developer"`, `"project-manager"`, `"teammate"`) appear as raw strings in `bridge/routing.py` and `ui/data/machine.py`

**Desired outcome:**
- All magic strings replaced with proper Python `StrEnum` members in `config/enums.py`
- Dashboard sessions table column renamed from "Type" to "Persona" with unified display values
- `qa_mode` boolean replaced with a clearer mechanism (enum-based session mode)
- Type safety at model save boundaries via enum validation

## Prior Art

No prior issues found related to this work. The existing `SESSION_TYPE_CHAT`/`SESSION_TYPE_DEV` constants in `models/agent_session.py` were an early step toward standardization but stopped short of enums.

## Data Flow

1. **Telegram message arrives** at `bridge/telegram_bridge.py`
2. **Chat mode resolution** in `bridge/routing.py::resolve_chat_mode()` maps persona strings (`"developer"`, `"project-manager"`, `"teammate"`) to mode strings (`"dev"`, `"pm"`, `"qa"`)
3. **Session type assignment** in `bridge/telegram_bridge.py` sets `_session_type = "dev"` or `"chat"` based on resolved mode
4. **Classification** in `bridge/routing.py::classify_intent()` returns `"sdlc"` or `"question"`
5. **Job enqueue** in `agent/job_queue.py::enqueue_job()` passes `session_type` and `classification_type` as strings to `AgentSession` creation
6. **Agent dispatch** in `agent/sdk_client.py::get_agent_response_sdk()` reads `session_type` and `classification_type` from the session, compares against raw strings to set env vars, build prompts, and choose personas
7. **Intent classification** in `agent/sdk_client.py` sets `qa_mode=True` on the session when the Q&A intent is detected
8. **Dashboard display** in `ui/data/sdlc.py::_resolve_session_type()` combines `session_type` + `qa_mode` into display labels ("dev", "PM", "Q&A")

All six string types flow through this pipeline. The enums must be compatible at every boundary.

## Architectural Impact

- **New dependencies**: None -- `StrEnum` is stdlib (Python 3.11+)
- **Interface changes**: `config/enums.py` exports `SessionType`, `PersonaType`, `ClassificationType`, `ChatMode`. Existing string constants in `models/agent_session.py` become aliases to enum values for backward compatibility during transition
- **Coupling**: Decreases coupling -- consumers import from one canonical location instead of hardcoding strings
- **Data ownership**: No change -- enums are value objects, not data owners
- **Reversibility**: Fully reversible -- `StrEnum` members compare equal to their string values, so reverting to raw strings requires no data migration

## Appetite

**Size:** Medium (mechanical changes across many files, but each change is straightforward)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- all changes are internal to existing code. `StrEnum` requires Python 3.11+, which is already the minimum for this project.

## Solution

### Key Elements

- **`config/enums.py`**: Central enum definitions for `SessionType`, `PersonaType`, `ClassificationType`, and `ChatMode`
- **Backward-compatible constants**: `SESSION_TYPE_CHAT` and `SESSION_TYPE_DEV` in `models/agent_session.py` become aliases to `SessionType.CHAT` and `SessionType.DEV`
- **Dashboard column rename**: "Type" to "Persona" in sessions table, with aligned badge values
- **`qa_mode` replacement**: Replace `qa_mode: bool` with `session_mode: str` field that stores a `ChatMode` enum value (`"work"` or `"qa"`), preserving the same branching logic

### Technical Approach

#### 1. Create `config/enums.py`

```python
from enum import StrEnum

class SessionType(StrEnum):
    CHAT = "chat"
    DEV = "dev"

class PersonaType(StrEnum):
    DEVELOPER = "developer"
    PROJECT_MANAGER = "project-manager"
    TEAMMATE = "teammate"

class ClassificationType(StrEnum):
    SDLC = "sdlc"
    QUESTION = "question"

class ChatMode(StrEnum):
    """Resolved chat mode from config/title/DM detection.

    Maps 1:1 with the return values of resolve_chat_mode().
    """
    QA = "qa"
    PM = "pm"
    DEV = "dev"
```

`StrEnum` members compare equal to their string values (`SessionType.CHAT == "chat"` is `True`), so the migration is backward-compatible: existing Redis data with `"chat"` values will match `SessionType.CHAT` without data migration.

#### 2. Replace magic strings in source files

For each file, replace raw string literals and comparisons with enum imports:

**models/agent_session.py:**
- Replace `SESSION_TYPE_CHAT = "chat"` / `SESSION_TYPE_DEV = "dev"` with imports from `config.enums`
- Keep backward-compatible aliases: `SESSION_TYPE_CHAT = SessionType.CHAT`
- No changes to `session_type = KeyField(null=True)` -- Popoto stores the string value, which is identical

**bridge/telegram_bridge.py:**
- Replace `_session_type = "dev"` with `_session_type = SessionType.DEV`
- Replace `_session_type = "chat"` with `_session_type = SessionType.CHAT`
- Replace `classification_result["type"] = "sdlc"` with `ClassificationType.SDLC`

**bridge/routing.py:**
- Replace `PERSONA_TO_MODE` dict keys with `PersonaType` members
- Replace `PERSONA_TO_MODE` dict values with `ChatMode` members
- Replace `return "sdlc"` / `return "question"` with `ClassificationType` members
- Replace mode return values `"qa"`, `"pm"`, `"dev"` with `ChatMode` members

**agent/sdk_client.py:**
- Replace `_session_type == "chat"` comparisons with `SessionType.CHAT`
- Replace `classification == "sdlc"` / `"question"` with `ClassificationType` members
- Replace `qa_mode = True` assignments with `session_mode = ChatMode.QA`

**agent/job_queue.py:**
- Replace `session_type: str = "chat"` defaults with `SessionType.CHAT`
- Replace `qa_mode` checks with `session_mode` checks

**bridge/summarizer.py:**
- Replace `qa_mode` checks with `session_mode == ChatMode.QA`

**ui/data/sdlc.py:**
- Rewrite `_resolve_session_type()` to use enums. Rename to `_resolve_persona_display()`
- Return display labels: `"Dev"`, `"PM"`, `"Q&A"` (capitalized consistently)

**ui/data/machine.py:**
- Replace `persona_order` dict keys with `PersonaType` members

**agent/hooks/pre_tool_use.py:**
- Replace `SESSION_TYPE` env var check against `"chat"` with `SessionType.CHAT`

**tools/job_scheduler.py:**
- Replace `session_type` default `"chat"` with `SessionType.CHAT`

#### 3. Replace `qa_mode` field

- Add new field `session_mode = Field(null=True)` to `AgentSession`
- Deprecate `qa_mode` by adding a property that reads/writes `session_mode`:
  ```python
  @property
  def qa_mode(self) -> bool:
      return self.session_mode == ChatMode.QA
  ```
- Update all writers (sdk_client.py, job_queue.py) to set `session_mode` instead of `qa_mode`
- Update all readers (summarizer.py, job_queue.py, ui/data/sdlc.py) to check `session_mode`
- Keep `qa_mode` Field temporarily for backward compatibility with in-flight sessions in Redis, but mark it deprecated

#### 4. Rename dashboard "Type" column to "Persona"

- Update `ui/templates/_partials/sessions_table.html`: change `<th>Type</th>` to `<th>Persona</th>`
- Update badge color logic to use the new display values from `_resolve_persona_display()`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_resolve_persona_display()` must handle `session_type=None` gracefully (return None, not crash)
- [ ] Enum construction from invalid strings must not crash model loading -- add `try/except` around enum coercion at save boundaries
- [ ] No `except Exception: pass` blocks in scope of this work

### Empty/Invalid Input Handling
- [ ] `SessionType("invalid")` raises `ValueError` -- verify this is caught at model boundaries
- [ ] `_resolve_persona_display()` with `session_type=None` returns `None`
- [ ] `classify_intent()` returning unexpected string falls through to `"question"` default

### Error State Rendering
- [ ] Dashboard sessions table renders correctly when `session_mode` is `None` (pre-migration sessions)
- [ ] Badge displays "-" for sessions with no persona info

## Test Impact

- [ ] `tests/unit/test_summarizer.py::test_qa_mode_returns_prose_without_emoji` -- UPDATE: change `session.qa_mode = True` to `session.session_mode = "qa"`
- [ ] `tests/unit/test_summarizer.py::test_qa_mode_false_still_gets_structured` -- UPDATE: change `session.qa_mode = False` to `session.session_mode = None` or `"work"`
- [ ] `tests/unit/test_summarizer.py::test_qa_mode_session_returns_prose` -- UPDATE: same pattern
- [ ] `tests/unit/test_qa_nudge_cap.py` (3 tests) -- UPDATE: replace `session.qa_mode = True/False` with `session.session_mode`
- [ ] `tests/unit/test_config_driven_routing.py::test_qa_mode_gives_chat_session` -- UPDATE: adjust for session_mode
- [ ] `tests/unit/test_ui_sdlc_data.py` -- UPDATE: adjust for `_resolve_persona_display()` rename and new return values
- [ ] `tests/unit/test_chat_session_factory.py` (5 tests) -- UPDATE: replace `"chat"`/`"dev"` string literals with `SessionType` enum imports
- [ ] `tests/unit/test_pm_session_permissions.py` (12+ tests) -- UPDATE: replace `session_type="chat"` with enum, update `SESSION_TYPE` env var checks
- [ ] `tests/unit/test_sdk_client.py` -- UPDATE: replace string literals with enum imports
- [ ] `tests/unit/test_dev_session_registration.py` -- UPDATE: replace `session.session_type == "dev"` with enum comparison
- [ ] `tests/unit/test_health_check.py` -- UPDATE: replace `mock_session.session_type = "dev"` with enum
- [ ] `tests/unit/test_steer_child.py` -- UPDATE: replace string literals with enums
- [ ] `tests/integration/test_bridge_routing.py` (15+ tests) -- UPDATE: replace string comparisons with enum imports
- [ ] `tests/integration/test_job_queue_session_type.py` (8+ tests) -- UPDATE: replace `SESSION_TYPE_CHAT`/`SESSION_TYPE_DEV` imports and string literals
- [ ] `tests/e2e/test_context_propagation.py` -- UPDATE: replace `SESSION_TYPE_CHAT`/`SESSION_TYPE_DEV` imports

## Rabbit Holes

- **Adding validation at Popoto model save time**: Popoto's Field does not support validators. Do not attempt to hook into Popoto's save mechanism -- instead, validate at the application layer (factory methods and enqueue_job). Trying to monkey-patch Popoto's Field class is a waste of time
- **Migrating existing Redis data**: `StrEnum` members compare equal to their string values, so NO Redis data migration is needed. Do not write migration scripts
- **Creating a DisplayPersona enum for dashboard labels**: The display labels ("Dev", "PM", "Q&A") are presentation concerns. Keep them as plain strings returned by `_resolve_persona_display()`, not as enum members
- **Replacing the `SESSION_TYPE` environment variable**: The env var passes a string to child processes. Keep it as a string -- env vars are inherently string-typed. Just set it from the enum value
- **Expanding ClassificationType beyond sdlc/question**: The issue mentions "bug", "feature", "chore" but the codebase only uses "sdlc" and "question" for classification_type. Do not add unused enum members

## Risks

### Risk 1: Popoto field serialization with StrEnum
**Impact:** If Popoto's `Field` or `KeyField` does not correctly serialize `StrEnum` values to Redis, session creation/lookup could break.
**Mitigation:** `StrEnum` inherits from `str`, so `str(SessionType.CHAT)` returns `"chat"`. Popoto's string-based serialization should work transparently. Add a focused integration test that creates an AgentSession with enum values and verifies round-trip retrieval.

### Risk 2: Env var comparison in child processes
**Impact:** `agent/hooks/pre_tool_use.py` reads `os.environ.get("SESSION_TYPE")` and compares against `"chat"`. If the env var is set from an enum, the comparison must still work.
**Mitigation:** `str(SessionType.CHAT)` produces `"chat"`, and `SessionType.CHAT == "chat"` is `True`. The env var path is inherently string-based and will work. Test this explicitly.

### Risk 3: In-flight sessions during deployment
**Impact:** Sessions created before deployment have `qa_mode=True/False` but no `session_mode` field. If code reads only `session_mode`, these sessions lose their Q&A routing.
**Mitigation:** Keep `qa_mode` Field on the model (deprecated) and add a property that checks both: `session_mode` first, then falls back to `qa_mode`. Remove `qa_mode` in a follow-up cleanup after all in-flight sessions have drained.

## Race Conditions

No race conditions identified -- all changes are to value assignments and comparisons within single-threaded request processing. No shared mutable state is introduced. The `session_mode` field replaces `qa_mode` at the same points in the code path.

## No-Gos (Out of Scope)

- Modifying Popoto's Field/KeyField serialization behavior
- Writing Redis data migration scripts (StrEnum is backward-compatible)
- Adding a third session_type value (e.g., "qa" as a session type -- Q&A remains a mode within ChatSession)
- Expanding ClassificationType beyond the binary sdlc/question (the codebase does not use other values)
- Replacing the SESSION_TYPE environment variable mechanism
- Adding enum validation at the Popoto ORM layer

## Update System

No update system changes required -- this modifies internal Python code only. No new dependencies, no config changes, no new packages. `StrEnum` is stdlib.

## Agent Integration

No agent integration required -- enums are internal to the bridge/agent/model layer. No MCP server changes, no new tools exposed, no `.mcp.json` modifications. The agent interacts with sessions through existing interfaces that will pass enum string values transparently.

## Documentation

- [ ] Create `docs/features/standardized-enums.md` documenting the enum types, import patterns, and backward-compatibility guarantees
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/chat-dev-session-architecture.md` to reference enums instead of string constants
- [ ] Update `docs/features/chatsession-qa-mode.md` to document `session_mode` replacing `qa_mode`
- [ ] Update `docs/features/agent-session-model.md` field table to reflect `session_mode` field

## Success Criteria

- [ ] `config/enums.py` exists with `SessionType`, `PersonaType`, `ClassificationType`, `ChatMode` enums
- [ ] `grep -rn '"chat"\|"dev"' models/agent_session.py bridge/telegram_bridge.py agent/sdk_client.py agent/job_queue.py` returns zero hits outside comments/docstrings
- [ ] `grep -rn '"sdlc"\|"question"' bridge/routing.py agent/sdk_client.py` returns zero hits outside comments/docstrings and LLM prompt strings
- [ ] Dashboard sessions table header shows "Persona" not "Type"
- [ ] `qa_mode` field deprecated with backward-compatible property; `session_mode` field active
- [ ] All 24 affected test files pass
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (enums)**
  - Name: enum-builder
  - Role: Create enum module and replace all magic strings across codebase
  - Agent Type: builder
  - Resume: true

- **Validator (enums)**
  - Name: enum-validator
  - Role: Verify all magic strings replaced and round-trip Redis compatibility
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create config/enums.py
- **Task ID**: create-enums
- **Depends On**: none
- **Validates**: tests/unit/test_enums.py (create)
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `config/enums.py` with `SessionType`, `PersonaType`, `ClassificationType`, `ChatMode` StrEnums
- Create `tests/unit/test_enums.py` with tests verifying string equality, membership, and iteration
- Verify `str(SessionType.CHAT) == "chat"` and `SessionType.CHAT == "chat"`

### 2. Replace session_type magic strings
- **Task ID**: replace-session-type
- **Depends On**: create-enums
- **Validates**: existing tests in test_chat_session_factory.py, test_pm_session_permissions.py, test_sdk_client.py
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `models/agent_session.py`: alias constants to enum values
- Update `bridge/telegram_bridge.py`: use `SessionType.DEV` / `SessionType.CHAT`
- Update `agent/sdk_client.py`: replace all `== "chat"` comparisons
- Update `agent/job_queue.py`: replace default params and comparisons
- Update `agent/hooks/pre_tool_use.py`: compare against `SessionType.CHAT`
- Update `tools/job_scheduler.py`: replace default value

### 3. Replace classification_type magic strings
- **Task ID**: replace-classification
- **Depends On**: create-enums
- **Validates**: existing tests in test_bridge_routing.py, test_config_driven_routing.py
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `bridge/routing.py`: replace return values and dict values with `ClassificationType` members
- Update `bridge/telegram_bridge.py`: replace `classification_result["type"] = "sdlc"`
- Update `agent/sdk_client.py`: replace classification comparisons
- NOTE: Do NOT change the LLM prompt strings in `classify_intent()` -- the LLM returns raw strings that are then compared

### 4. Replace persona magic strings
- **Task ID**: replace-persona
- **Depends On**: create-enums
- **Validates**: existing tests in test_config_driven_routing.py
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `bridge/routing.py`: replace `PERSONA_TO_MODE` dict with `PersonaType` and `ChatMode` members
- Update `ui/data/machine.py`: replace `persona_order` dict keys with `PersonaType` members

### 5. Replace qa_mode with session_mode
- **Task ID**: replace-qa-mode
- **Depends On**: create-enums
- **Validates**: tests/unit/test_summarizer.py, tests/unit/test_qa_nudge_cap.py
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `session_mode = Field(null=True)` to `AgentSession`
- Add backward-compatible `qa_mode` property that reads `session_mode`
- Keep `qa_mode` Field temporarily (renamed to `_qa_mode_legacy`) for Redis backward compatibility
- Update writers: `agent/sdk_client.py`, `agent/job_queue.py`
- Update readers: `bridge/summarizer.py`, `agent/job_queue.py`, `ui/data/sdlc.py`

### 6. Rename dashboard Type column to Persona
- **Task ID**: rename-dashboard-column
- **Depends On**: replace-qa-mode
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `ui/templates/_partials/sessions_table.html`: rename header, update badge logic
- Rename `_resolve_session_type()` to `_resolve_persona_display()` in `ui/data/sdlc.py`
- Update badge color mapping for consistent display values

### 7. Update all test files
- **Task ID**: update-tests
- **Depends On**: replace-session-type, replace-classification, replace-persona, replace-qa-mode, rename-dashboard-column
- **Validates**: pytest tests/unit/ -x -q, pytest tests/integration/ -x -q
- **Assigned To**: enum-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all 24 affected test files to use enum imports instead of string literals
- Replace `session.qa_mode = True/False` with `session.session_mode` assignments
- Replace `SESSION_TYPE_CHAT`/`SESSION_TYPE_DEV` imports to come from `config.enums`

### 8. Validate round-trip Redis compatibility
- **Task ID**: validate-redis
- **Depends On**: update-tests
- **Assigned To**: enum-validator
- **Agent Type**: validator
- **Parallel**: false
- Create an AgentSession with enum-typed fields, save to Redis, re-fetch, verify values match
- Verify `SessionType.CHAT == "chat"` works in Popoto KeyField queries
- Run full test suite: `pytest tests/unit/ -x -q && pytest tests/integration/ -x -q`
- Run lint: `python -m ruff check . && python -m ruff format --check .`

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-redis
- **Assigned To**: enum-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/standardized-enums.md`
- Update `docs/features/README.md` index
- Update `docs/features/chat-dev-session-architecture.md`
- Update `docs/features/chatsession-qa-mode.md`
- Update `docs/features/agent-session-model.md`

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enum-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Run `grep` checks for remaining magic strings
- Verify dashboard column renamed
- Run full test suite

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Enum module exists | `python -c "from config.enums import SessionType, PersonaType, ClassificationType, ChatMode; print('OK')"` | output contains OK |
| No session_type magic strings | `grep -rn '"chat"\|"dev"' models/agent_session.py bridge/telegram_bridge.py agent/sdk_client.py agent/job_queue.py \| grep -v '#\|docstring\|"""' \| wc -l` | output contains 0 |
| Dashboard column renamed | `grep -c 'Persona' ui/templates/_partials/sessions_table.html` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions. The approach is mechanical and well-defined. `StrEnum` backward compatibility with plain strings eliminates the need for data migration, making this a safe incremental refactor.
