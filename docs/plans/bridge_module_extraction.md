---
status: In Progress
type: chore
appetite: "Medium: 3-5 days"
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/25
---

# Refactor telegram_bridge.py into Testable Modules

## Problem

`bridge/telegram_bridge.py` is a 3,608-line monolith with 51 functions. Although extraction modules (`media.py`, `routing.py`, `context.py`, `response.py`, `agents.py`) were created, the functions were **duplicated** rather than moved — `telegram_bridge.py` still contains all original code.

**Current state (verified 2026-02-10):**
- ✅ Modules exist: `media.py` (461 lines), `routing.py` (434 lines), `context.py` (562 lines), `response.py` (510 lines), `agents.py` (760 lines)
- ✅ No circular imports: `from bridge import media, routing, context, response, agents` works
- ❌ `telegram_bridge.py` still 3,608 lines (goal: <600)
- ❌ Functions duplicated in both locations
- ❌ `models/events.py` no longer exists (already deleted)

**Remaining work:** Remove duplicated functions from `telegram_bridge.py` and update imports.

## Appetite

**Time budget:** 1 day (most module extraction complete)

## Solution

### What's Done
- All 5 extraction modules created with functions and constants
- Module-level docstrings added
- No circular import issues

### What Remains

1. **Remove duplicated functions from `telegram_bridge.py`** — Functions that now live in sub-modules must be deleted from the main file
2. **Update imports in `telegram_bridge.py`** — Replace removed functions with imports from sub-modules
3. **Update external imports** — `agent/sdk_client.py` and tests should import from new locations
4. **Verify tests pass** — Run full test suite
5. **Verify bridge starts** — Confirm runtime works

### Functions to Remove from telegram_bridge.py

Based on what exists in sub-modules:

**From `bridge/media.py`:**
- `get_media_type`, `download_media`, `transcribe_voice`, `describe_image`, `process_incoming_media`, `validate_media_file`, `extract_document_text`, `_extract_pdf_text_stdlib`

**From `bridge/routing.py`:**
- `load_config`, `build_group_to_project_map`, `find_project_for_chat`, `should_respond_sync`, `should_respond_async`, `classify_needs_response`, `classify_needs_response_async`, `is_message_for_valor`, `is_message_for_others`, `extract_at_mentions`, `get_valor_usernames`, `get_user_permissions`

**From `bridge/context.py`:**
- `build_context_prefix`, `build_conversation_history`, `build_activity_context`, `is_status_question`, `fetch_reply_chain`, `format_reply_chain`, `get_link_summaries`, `format_link_summaries`

**From `bridge/response.py`:**
- `clean_message`, `filter_tool_logs`, `extract_files_from_response`, `send_response_with_files`, `get_processing_emoji`, `get_processing_emoji_async`, `set_reaction`

**From `bridge/agents.py`:**
- `get_agent_response`, `get_agent_response_clawdbot`, `get_agent_response_with_retry`, `attempt_self_healing`, `create_failure_plan`, `_get_running_jobs_info`, `_handle_update_command`, `detect_tracked_work`, `create_workflow_for_tracked_work`, `_get_github_repo_url`, `_match_plan_by_name`, `_detect_issue_number`

## Rabbit Holes

- Don't refactor the handler() function itself — just remove duplicated helpers
- Don't add type hints or improve code while removing duplicates
- Don't change function signatures — exact match required

## Risks

### Risk: Import cycles when wiring up
**Impact:** ImportError at startup
**Mitigation:** Sub-modules already work. Just need to add imports to `telegram_bridge.py`.

### Risk: Runtime breakage after removing code
**Impact:** Bridge fails to start or process messages
**Mitigation:** Run bridge after each batch of removals. Test incrementally.

## No-Gos (Out of Scope)

- No behavior changes
- No new abstractions
- No changes to sub-module implementations
- Don't touch `bridge/summarizer.py` or `bridge/dead_letters.py`

## Update System

No update system changes required.

## Agent Integration

No agent integration required.

## Documentation

- [ ] Update module-level docstring in `telegram_bridge.py` to reflect new structure
- [ ] Verify `docs/features/README.md` doesn't need updates

## Success Criteria

- [ ] `bridge/telegram_bridge.py` under 600 lines
- [ ] All functions removed from `telegram_bridge.py` that exist in sub-modules
- [ ] `from bridge.telegram_bridge import build_context_prefix` still works (backward compat)
- [ ] `pytest tests/` passes
- [ ] Bridge starts: `./scripts/start_bridge.sh` → "Connected to Telegram"
- [ ] `ruff check bridge/` and `black --check bridge/` pass

## Team Orchestration

### Team Members

- **Builder (dedup)**
  - Name: dedup-builder
  - Role: Remove duplicated functions from telegram_bridge.py, add imports
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria, run tests, check line count
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove media functions from telegram_bridge.py
- **Task ID**: dedup-media
- **Depends On**: none
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove: `get_media_type`, `download_media`, `transcribe_voice`, `describe_image`, `process_incoming_media`, `validate_media_file`, `extract_document_text`, `_extract_pdf_text_stdlib`
- Remove associated constants: `MEDIA_DIR`, `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `AUDIO_EXTENSIONS`, `VOICE_EXTENSIONS`, `VISION_EXTENSIONS`, `FILE_MAGIC_BYTES`, `TEXT_DOCUMENT_EXTENSIONS`
- Add: `from bridge.media import get_media_type, download_media, transcribe_voice, describe_image, process_incoming_media, validate_media_file, extract_document_text`
- Verify: `python -c "from bridge.telegram_bridge import get_media_type"`

### 2. Remove routing functions from telegram_bridge.py
- **Task ID**: dedup-routing
- **Depends On**: dedup-media
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove: `load_config`, `build_group_to_project_map`, `find_project_for_chat`, `should_respond_sync`, `should_respond_async`, `classify_needs_response`, `classify_needs_response_async`, `is_message_for_valor`, `is_message_for_others`, `extract_at_mentions`, `get_valor_usernames`, `get_user_permissions`
- Remove: `AT_MENTION_PATTERN`, `VALOR_USERNAMES`, `DEFAULT_MENTIONS`
- Add imports from `bridge.routing`
- Verify: `python -c "from bridge.telegram_bridge import load_config"`

### 3. Remove context functions from telegram_bridge.py
- **Task ID**: dedup-context
- **Depends On**: dedup-routing
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove: `build_context_prefix`, `build_conversation_history`, `build_activity_context`, `is_status_question`, `fetch_reply_chain`, `format_reply_chain`, `get_link_summaries`, `format_link_summaries`
- Remove: `STATUS_QUESTION_PATTERNS`, `LINK_COLLECTORS`, `MAX_LINKS_PER_MESSAGE`, `LINK_SUMMARY_CACHE_HOURS`
- Add imports from `bridge.context`
- Verify: `python -c "from bridge.telegram_bridge import build_context_prefix"`

### 4. Remove response functions from telegram_bridge.py
- **Task ID**: dedup-response
- **Depends On**: dedup-context
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove: `clean_message`, `filter_tool_logs`, `extract_files_from_response`, `send_response_with_files`, `get_processing_emoji`, `get_processing_emoji_async`, `set_reaction`
- Remove: `FILE_MARKER_PATTERN`, `TOOL_LOG_PATTERNS`, `ABSOLUTE_PATH_PATTERN`, `RELATIVE_PATH_PATTERN`, reaction constants, `INTENT_REACTIONS`
- Add imports from `bridge.response`
- Verify: `python -c "from bridge.telegram_bridge import clean_message"`

### 5. Remove agent functions from telegram_bridge.py
- **Task ID**: dedup-agents
- **Depends On**: dedup-response
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove: `get_agent_response`, `get_agent_response_clawdbot`, `get_agent_response_with_retry`, `attempt_self_healing`, `create_failure_plan`, `_get_running_jobs_info`, `_handle_update_command`, `detect_tracked_work`, `create_workflow_for_tracked_work`, `_get_github_repo_url`, `_match_plan_by_name`, `_detect_issue_number`
- Remove: `ACKNOWLEDGMENT_TIMEOUT_SECONDS`, `ACKNOWLEDGMENT_MESSAGE`, `MAX_RETRIES`, `RETRY_DELAYS`
- Add imports from `bridge.agents`
- Verify: `python -c "from bridge.telegram_bridge import get_agent_response_with_retry"`

### 6. Update external imports
- **Task ID**: update-external
- **Depends On**: dedup-agents
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/sdk_client.py` to import from `bridge.context` instead of `bridge.telegram_bridge`
- Update any test files that import directly from `bridge.telegram_bridge`
- Add backward-compatible re-exports to `bridge/__init__.py` if needed

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: update-external
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `telegram_bridge.py` is under 600 lines: `wc -l bridge/telegram_bridge.py`
- Run tests: `pytest tests/ -x`
- Check imports: `python -c "from bridge.telegram_bridge import build_context_prefix, get_media_type, load_config"`
- Check linting: `ruff check bridge/ && black --check bridge/`
- Start bridge and verify logs

## Validation Commands

```bash
wc -l bridge/telegram_bridge.py  # Should be under 600
pytest tests/ -x
python -c "from bridge.telegram_bridge import build_context_prefix, get_media_type, load_config"
python -c "from bridge import media, routing, context, response, agents"
ruff check bridge/
black --check bridge/
./scripts/start_bridge.sh  # Verify "Connected to Telegram"
```

## Open Questions

None — the path is clear. Remove duplicates, add imports, verify.
