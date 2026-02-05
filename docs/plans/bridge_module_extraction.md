---
status: Planning
type: chore
appetite: "Medium: 3-5 days"
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/59
---

# Refactor telegram_bridge.py into Testable Modules

## Problem

`bridge/telegram_bridge.py` is a 3,335-line monolith with 49 functions handling media processing, routing logic, context building, response formatting, and agent orchestration in a single file. This makes testing painful (tests re-implement bridge functions rather than importing them), merge conflicts frequent, and navigation slow.

**Current behavior:**
- All bridge logic lives in one file that's impossible to unit-test in isolation
- Test files like `test_bridge_logic.py` re-implement routing functions rather than importing them
- Any feature branch touching the bridge conflicts with every other bridge branch
- Finding a function requires scrolling through 3,335 lines of mixed concerns

**Desired outcome:**
- 5 focused modules under `bridge/` with clear ownership of concerns
- `telegram_bridge.py` reduced to ~500 lines of orchestration and the event handler
- Each module independently importable and testable
- Zero behavior changes — the bridge works identically before and after

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`bridge/media.py`**: All media detection, download, transcription, description, and document extraction
- **`bridge/routing.py`**: Config loading, group-to-project mapping, response decision logic, mention detection
- **`bridge/context.py`**: Context prefix building, conversation history, reply chains, link summaries, activity context
- **`bridge/response.py`**: Message cleaning, tool log filtering, file extraction, response sending, reaction management
- **`bridge/agents.py`**: Agent response routing, retry logic, self-healing, failure plans
- **`bridge/telegram_bridge.py`**: Retains `main()`, the Telethon event handler, and top-level orchestration

### Flow

This is a structural refactor — the runtime flow is unchanged:

**Telegram message arrives** → `handler()` in telegram_bridge.py → calls `routing.should_respond_async()` → calls `context.build_context_prefix()` + `context.build_conversation_history()` → calls `agents.get_agent_response_with_retry()` → calls `response.send_response_with_files()` → **response sent**

### Technical Approach

**Extraction strategy: bottom-up by dependency.**
1. Extract leaf modules first (media, routing) — these have minimal internal dependencies
2. Extract context and response next — they depend on routing config but not on each other
3. Extract agents last — it depends on context building
4. Update telegram_bridge.py imports and remove extracted code
5. Re-export key symbols from `bridge/__init__.py` for backward compatibility of external imports

**Constants travel with their functions.** Each module takes the constants, patterns, and config variables used exclusively by its functions. Shared constants (like `MEDIA_DIR`, credential variables) stay in telegram_bridge.py and get imported by sub-modules.

**Import approach:**
- Sub-modules import shared state from `bridge.telegram_bridge` where needed (e.g., `logger`, `CONFIG`, `DEFAULTS`)
- Alternatively, pass config as arguments to avoid circular imports — prefer this where practical
- `bridge/__init__.py` re-exports public symbols so `from bridge.telegram_bridge import X` still works during transition

**Circular import prevention:**
- Module-level globals like `CONFIG`, `GROUP_TO_PROJECT`, `ALL_MONITORED_GROUPS` stay in telegram_bridge.py
- Sub-modules accept these as function parameters or import them lazily
- The Telethon `client` is only in telegram_bridge.py; async functions that need it receive it as a parameter (which they already do)

### Module Breakdown (Detailed)

**`bridge/media.py`** (~350 lines)
- Functions: `get_media_type`, `download_media`, `transcribe_voice`, `describe_image`, `process_incoming_media`, `validate_media_file`, `extract_document_text`, `_extract_pdf_text_stdlib`
- Constants: `MEDIA_DIR`, `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `AUDIO_EXTENSIONS`, `VOICE_EXTENSIONS`, `VISION_EXTENSIONS`, `FILE_MAGIC_BYTES`, `TEXT_DOCUMENT_EXTENSIONS`
- External deps: `httpx`, `telethon`, `pathlib`

**`bridge/routing.py`** (~400 lines)
- Functions: `load_config`, `build_group_to_project_map`, `find_project_for_chat`, `should_respond_sync`, `should_respond_async`, `classify_needs_response`, `classify_needs_response_async`, `is_message_for_valor`, `is_message_for_others`, `extract_at_mentions`, `get_valor_usernames`, `get_user_permissions`
- Constants: `AT_MENTION_PATTERN`, `VALOR_USERNAMES`, `DEFAULT_MENTIONS`
- Config globals: This module defines and owns `load_config()` and `build_group_to_project_map()`, but the actual loaded state (`CONFIG`, `GROUP_TO_PROJECT`, `ALL_MONITORED_GROUPS`, `ACTIVE_PROJECTS`, etc.) stays in telegram_bridge.py which calls these functions at startup

**`bridge/context.py`** (~450 lines)
- Functions: `build_context_prefix`, `build_conversation_history`, `build_activity_context`, `is_status_question`, `fetch_reply_chain`, `format_reply_chain`, `get_link_summaries`, `format_link_summaries`
- Constants: `STATUS_QUESTION_PATTERNS`, `LINK_COLLECTORS`, `MAX_LINKS_PER_MESSAGE`, `LINK_SUMMARY_CACHE_HOURS`

**`bridge/response.py`** (~400 lines)
- Functions: `clean_message`, `filter_tool_logs`, `extract_files_from_response`, `send_response_with_files`, `get_processing_emoji`, `get_processing_emoji_async`, `set_reaction`
- Constants: `FILE_MARKER_PATTERN`, `TOOL_LOG_PATTERNS`, `ABSOLUTE_PATH_PATTERN`, `RELATIVE_PATH_PATTERN`, `VALIDATED_REACTIONS`, `INVALID_REACTIONS`, `REACTION_*` constants, `INTENT_REACTIONS`

**`bridge/agents.py`** (~350 lines)
- Functions: `get_agent_response`, `get_agent_response_clawdbot`, `get_agent_response_with_retry`, `attempt_self_healing`, `create_failure_plan`
- Constants: `ACKNOWLEDGMENT_TIMEOUT_SECONDS`, `ACKNOWLEDGMENT_MESSAGE`, `MAX_RETRIES`, `RETRY_DELAYS`
- Also includes: `_get_running_jobs_info`, `_handle_update_command`, `detect_tracked_work`, `create_workflow_for_tracked_work`, `_get_github_repo_url`, `_match_plan_by_name`, `_detect_issue_number`

**`bridge/telegram_bridge.py`** (remaining ~500 lines)
- `main()`, the event `handler()`, signal handling, `log_event()`
- Startup config loading (calls routing functions, stores results in module globals)
- Telegram credential variables
- Logging setup
- `SHUTTING_DOWN` flag and shutdown logic

## Rabbit Holes

- **Premature abstraction of shared state.** Don't create a `BridgeConfig` class or dependency injection framework. Pass config as function arguments where needed, or use module-level imports. Keep it simple.
- **Refactoring the event handler itself.** The ~500-line `handler()` function is tempting to break up, but that's a separate concern. This plan extracts the helper functions it calls, not the handler's internal flow.
- **Adding type hints or docstrings to extracted code.** This is a move-only refactor. Don't improve the code while moving it — that makes review harder and introduces risk.
- **Changing function signatures.** Every function should have the exact same signature after extraction. If a function currently accesses a module global, pass it as a parameter only if strictly necessary to avoid circular imports.

## Risks

### Risk 1: Circular imports between sub-modules
**Impact:** ImportError at startup, bridge fails to start
**Mitigation:** Extract bottom-up (leaf modules first). Sub-modules import from `bridge.telegram_bridge` only for true shared state. Use lazy imports (inside functions) where circular risk exists. Test import order explicitly.

### Risk 2: Breaking external imports
**Impact:** `agent/sdk_client.py` imports `build_context_prefix` from `bridge.telegram_bridge` — would break if moved without re-export
**Mitigation:** `bridge/__init__.py` re-exports all public symbols. Update `agent/sdk_client.py` to import from new location. Keep backward-compatible re-exports during transition.

### Risk 3: Module-level side effects during import
**Impact:** Config loading, logging setup, and Telegram client creation happen at module level. Importing a sub-module could trigger unexpected initialization.
**Mitigation:** Side effects stay in telegram_bridge.py. Sub-modules are pure — they define functions and constants but don't execute anything at import time.

### Risk 4: Test breakage from changed import paths
**Impact:** Tests that import from `bridge.telegram_bridge` would need updates
**Mitigation:** Update test imports to use new module paths. Verify all tests pass before marking complete.

## No-Gos (Out of Scope)

- No behavior changes — no bug fixes, no new features, no performance improvements
- No refactoring of the event handler's internal logic
- No new abstractions (no config class, no base classes, no protocols)
- No changes to the bridge's public behavior or message handling
- No test coverage expansion beyond updating imports and adding basic smoke tests for new modules
- No changes to `bridge/summarizer.py` or `bridge/dead_letters.py`

## Update System

No update system changes required — this is a purely internal structural refactor. No new dependencies, no new config files, no migration steps. The bridge starts and runs identically.

## Agent Integration

No agent integration required — this is a bridge-internal structural change. No new tools, no MCP server changes, no `.mcp.json` changes. The agent's interface to the bridge is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/README.md` if bridge architecture is documented there
- No new feature doc needed — this is structural, not a feature

### Inline Documentation
- [ ] Add module-level docstrings to each new file explaining its scope
- No other documentation changes — code comments move with their functions

## Success Criteria

- [ ] `bridge/media.py`, `bridge/routing.py`, `bridge/context.py`, `bridge/response.py`, `bridge/agents.py` exist with extracted functions
- [ ] `bridge/telegram_bridge.py` reduced to <600 lines (orchestration + handler only)
- [ ] `models/events.py` deleted; `models/__init__.py` unchanged (it doesn't reference events.py)
- [ ] All existing tests pass: `pytest tests/` green
- [ ] Test imports updated to use new module paths where applicable
- [ ] Bridge starts successfully: `./scripts/start_bridge.sh` → "Connected to Telegram" in logs
- [ ] `from bridge.telegram_bridge import build_context_prefix` still works (backward compat)
- [ ] No circular import errors: `python -c "from bridge import media, routing, context, response, agents"`
- [ ] `ruff check bridge/` and `black --check bridge/` pass

## Team Orchestration

### Team Members

- **Builder (media-routing)**
  - Name: media-routing-builder
  - Role: Extract media.py and routing.py (leaf modules with no internal deps)
  - Agent Type: builder
  - Resume: true

- **Builder (context-response)**
  - Name: context-response-builder
  - Role: Extract context.py and response.py
  - Agent Type: builder
  - Resume: true

- **Builder (agents-orchestration)**
  - Name: agents-orchestration-builder
  - Role: Extract agents.py, trim telegram_bridge.py, wire up __init__.py
  - Agent Type: builder
  - Resume: true

- **Builder (dead-code-cleanup)**
  - Name: dead-code-builder
  - Role: Delete models/events.py and clean up any dangling references
  - Agent Type: builder
  - Resume: true

- **Builder (test-updater)**
  - Name: test-updater
  - Role: Update all test imports to use new module paths, verify tests pass
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria, run full test suite, check imports
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard builder/validator pattern.

## Step by Step Tasks

### 1. Extract bridge/media.py
- **Task ID**: build-media
- **Depends On**: none
- **Assigned To**: media-routing-builder
- **Agent Type**: builder
- **Parallel**: false (start here — baseline extraction)
- Extract all media functions and their constants from telegram_bridge.py into `bridge/media.py`
- Functions: `get_media_type`, `download_media`, `transcribe_voice`, `describe_image`, `process_incoming_media`, `validate_media_file`, `extract_document_text`, `_extract_pdf_text_stdlib`
- Constants: `MEDIA_DIR`, `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `AUDIO_EXTENSIONS`, `VOICE_EXTENSIONS`, `VISION_EXTENSIONS`, `FILE_MAGIC_BYTES`, `TEXT_DOCUMENT_EXTENSIONS`
- Add module-level docstring
- Update telegram_bridge.py to `from bridge.media import ...` for each extracted symbol
- Verify: `python -c "from bridge.media import get_media_type"`

### 2. Extract bridge/routing.py
- **Task ID**: build-routing
- **Depends On**: build-media
- **Assigned To**: media-routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract routing/config functions into `bridge/routing.py`
- Functions: `load_config`, `build_group_to_project_map`, `find_project_for_chat`, `should_respond_sync`, `should_respond_async`, `classify_needs_response`, `classify_needs_response_async`, `is_message_for_valor`, `is_message_for_others`, `extract_at_mentions`, `get_valor_usernames`, `get_user_permissions`
- Constants: `AT_MENTION_PATTERN`, `VALOR_USERNAMES`, `DEFAULT_MENTIONS`
- Module globals that are _produced_ by routing functions (CONFIG, GROUP_TO_PROJECT, etc.) stay in telegram_bridge.py
- Update telegram_bridge.py imports
- Verify: `python -c "from bridge.routing import load_config, find_project_for_chat"`

### 3. Extract bridge/context.py
- **Task ID**: build-context
- **Depends On**: build-routing
- **Assigned To**: context-response-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract context-building functions into `bridge/context.py`
- Functions: `build_context_prefix`, `build_conversation_history`, `build_activity_context`, `is_status_question`, `fetch_reply_chain`, `format_reply_chain`, `get_link_summaries`, `format_link_summaries`
- Constants: `STATUS_QUESTION_PATTERNS`, `LINK_COLLECTORS`, `MAX_LINKS_PER_MESSAGE`, `LINK_SUMMARY_CACHE_HOURS`
- Update telegram_bridge.py imports
- Verify: `python -c "from bridge.context import build_context_prefix"`

### 4. Extract bridge/response.py
- **Task ID**: build-response
- **Depends On**: build-context
- **Assigned To**: context-response-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract response/cleaning functions into `bridge/response.py`
- Functions: `clean_message`, `filter_tool_logs`, `extract_files_from_response`, `send_response_with_files`, `get_processing_emoji`, `get_processing_emoji_async`, `set_reaction`
- Constants: `FILE_MARKER_PATTERN`, `TOOL_LOG_PATTERNS`, `ABSOLUTE_PATH_PATTERN`, `RELATIVE_PATH_PATTERN`, `VALIDATED_REACTIONS`, `INVALID_REACTIONS`, `REACTION_*`, `INTENT_REACTIONS`
- Update telegram_bridge.py imports
- Verify: `python -c "from bridge.response import clean_message, set_reaction"`

### 5. Extract bridge/agents.py
- **Task ID**: build-agents
- **Depends On**: build-response
- **Assigned To**: agents-orchestration-builder
- **Agent Type**: builder
- **Parallel**: false
- Extract agent interaction functions into `bridge/agents.py`
- Functions: `get_agent_response`, `get_agent_response_clawdbot`, `get_agent_response_with_retry`, `attempt_self_healing`, `create_failure_plan`, `_get_running_jobs_info`, `_handle_update_command`, `detect_tracked_work`, `create_workflow_for_tracked_work`, `_get_github_repo_url`, `_match_plan_by_name`, `_detect_issue_number`
- Constants: `ACKNOWLEDGMENT_TIMEOUT_SECONDS`, `ACKNOWLEDGMENT_MESSAGE`, `MAX_RETRIES`, `RETRY_DELAYS`
- Update telegram_bridge.py imports
- Verify: `python -c "from bridge.agents import get_agent_response_with_retry"`

### 6. Wire up bridge/__init__.py and backward compatibility
- **Task ID**: build-init
- **Depends On**: build-agents
- **Assigned To**: agents-orchestration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `bridge/__init__.py` to re-export key public symbols
- Ensure `from bridge.telegram_bridge import build_context_prefix` still works (for agent/sdk_client.py)
- Update `agent/sdk_client.py` import to use `from bridge.context import build_context_prefix`
- Verify telegram_bridge.py is under 600 lines
- Verify: `python -c "from bridge.telegram_bridge import build_context_prefix"` (backward compat)

### 7. Delete dead code (models/events.py)
- **Task ID**: build-dead-code
- **Depends On**: none
- **Assigned To**: dead-code-builder
- **Agent Type**: builder
- **Parallel**: true (independent of extraction work)
- Delete `models/events.py`
- Confirm `models/__init__.py` has no reference to it (it doesn't)
- Grep codebase for any remaining references
- Verify: `python -c "import models"` still works

### 8. Update test imports
- **Task ID**: build-tests
- **Depends On**: build-init, build-dead-code
- **Assigned To**: test-updater
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/unit/test_media_handling.py` imports to use `from bridge.media import ...`
- Update `tests/unit/test_bridge_logic.py` imports if any reference telegram_bridge directly
- Update `tests/integration/test_message_routing.py` imports if any reference telegram_bridge directly
- Run `pytest tests/` and fix any failures
- Run `ruff check bridge/ tests/` and `black --check bridge/ tests/`

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands (see below)
- Verify all success criteria met
- Check that telegram_bridge.py line count is under 600
- Verify no circular imports
- Generate final pass/fail report

## Validation Commands

- `pytest tests/ -x` — all tests pass
- `python -c "from bridge import media, routing, context, response, agents"` — no circular imports
- `python -c "from bridge.telegram_bridge import build_context_prefix"` — backward compat
- `wc -l bridge/telegram_bridge.py` — should be under 600 lines
- `wc -l bridge/media.py bridge/routing.py bridge/context.py bridge/response.py bridge/agents.py` — modules exist and have content
- `ruff check bridge/` — no lint errors
- `black --check bridge/` — formatting OK
- `python -c "import models"` — models package still works after events.py deletion
- `grep -r "from models.events" . --include="*.py"` — no remaining references to dead code

---

## Open Questions

1. **Should `_handle_update_command` go in `agents.py` or stay in `telegram_bridge.py`?** It's a slash command handler that restarts the bridge — feels more like orchestration than agent logic. But it's currently grouped near agent functions. Which module fits best?

2. **Should `detect_tracked_work` / `create_workflow_for_tracked_work` go in `agents.py` or a separate `bridge/workflow.py`?** These are work-detection functions that sit between routing and agent response. They could be their own small module or bundled with agents.

3. **Should `log_event()` stay in telegram_bridge.py or move somewhere?** It's a one-liner that calls `BridgeEvent.log()`. Currently at line 860. It could stay in the orchestration file or move to a shared utilities spot.

4. **Sequencing: should the tasks truly be sequential or can media+routing extract in parallel?** The plan shows sequential extraction to minimize merge complexity, but media.py and routing.py have no overlap — they could extract simultaneously if speed matters.
