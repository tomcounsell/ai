# Integration Audit Report: Summarizer

**Date:** 2026-04-03
**Auditor:** DevSession (automated via `/do-integration-audit summarizer`)
**Target:** `bridge/summarizer.py` (1,481 lines) and its integration surfaces
**Issue:** #676

---

## Discovery

Found 28 files across 6 surfaces:

- **Implementation:** 4 files (`bridge/summarizer.py`, `bridge/formatting.py`, `bridge/message_quality.py`, `bridge/response.py`)
- **Entry points:** 3 (`bridge/response.py:send_response_with_files`, `agent/hooks/stop.py`, `agent/agent_session_queue.py:classify_nudge_action`)
- **Tests:** 5 files (unit: 3 — `test_summarizer.py` 2,160 lines, `test_cross_wire_fixes.py`, `test_work_request_classifier.py`; integration: 2 — `test_agent_session_lifecycle.py`, `test_connectivity_gaps.py`)
- **Documentation:** 4 files (`docs/features/summarizer-format.md`, `docs/guides/summarizer-output-audit.md`, `docs/features/link-summarization.md`, `docs/features/agent-message-delivery.md`)
- **Configuration:** 5 keys (`OPENROUTER_API_KEY`, `FILE_ATTACH_THRESHOLD`, `SAFETY_TRUNCATE`, `OPENROUTER_URL`, `CLASSIFICATION_CONFIDENCE_THRESHOLD`)
- **Migrations:** 1 file (`scripts/migrate_persona_values.py` — removes legacy `qa_mode` field)

External references found in 12 additional files (docs, plans, config, CLAUDE.md).

## Integration Map

| Surface | File | Status |
|---------|------|--------|
| entry point | `bridge/response.py:512` — `summarize_response()` | connected |
| entry point | `agent/hooks/stop.py:93` — `summarize_response()` | connected |
| entry point | `agent/agent_session_queue.py:57` — `classify_nudge_action()` | connected (separate classifier, not using `classify_output`) |
| implementation | `bridge/summarizer.py` | connected — core module |
| implementation | `bridge/formatting.py` | connected — `linkify_references_from_session` imported by summarizer |
| implementation | `bridge/message_quality.py` | connected — `PROCESS_NARRATION_PATTERNS` imported by summarizer |
| test | `tests/unit/test_summarizer.py` | connected — ~130 tests, 2,160 lines |
| test | `tests/unit/test_cross_wire_fixes.py` | connected — exercises `_classify_with_heuristics` |
| test | `tests/unit/test_work_request_classifier.py` | connected — exercises `_strip_process_narration` |
| test | `tests/integration/test_agent_session_lifecycle.py` | connected — exercises `_compose_structured_summary`, `_get_status_emoji`, `_build_summary_prompt`, `summarize_response` |
| test | `tests/integration/test_connectivity_gaps.py` | connected — exercises `_compose_structured_summary` |
| documentation | `docs/features/summarizer-format.md` | connected — comprehensive, mostly accurate |
| documentation | `docs/guides/summarizer-output-audit.md` | connected — prior manual audit (2026-02-26) |
| config | `OPENROUTER_API_KEY` via `os.environ.get()` | present in `.env.example` |
| config | `FILE_ATTACH_THRESHOLD` (hardcoded 3000) | no env override |
| config | `SAFETY_TRUNCATE` (hardcoded 4096) | no env override |
| config | `OPENROUTER_URL` (hardcoded) | no env override, duplicated in 4 other modules |
| config | `CLASSIFICATION_CONFIDENCE_THRESHOLD` (hardcoded 0.80) | no env override |

---

## Findings

### CRITICAL

(none)

No orphan code or dead entry points found. All implementation files are imported and exercised. All entry points are reachable from the running application.

### WARNING

#### [stale-reference] `docs/features/summarizer-format.md:95` references non-existent functions

The Implementation section at line 95 references `_render_stage_progress()` and `_render_link_footer()` as functions in `bridge/summarizer.py`. Neither function exists in the current codebase. These were likely removed or renamed during a refactor, but the documentation was not updated.

**File:** `docs/features/summarizer-format.md:95`
**Recommendation:** Remove references to `_render_stage_progress()` and `_render_link_footer()` from the Implementation section. The stage progress and link rendering are now handled within `_compose_structured_summary()` or have been moved elsewhere.

#### [stale-reference] `docs/features/summarizer-format.md:52` references `qa_mode` which no longer exists

Line 52 and line 170 reference `qa_mode=True` as the trigger for Q&A prose formatting. The `qa_mode` field has been migrated away (see `scripts/migrate_persona_values.py` which deletes `qa_mode` from all sessions, and `models/agent_session.py:346` which lists it as a legacy field). The actual mechanism now is `session_mode == PersonaType.TEAMMATE`.

**File:** `docs/features/summarizer-format.md:52,170`
**Recommendation:** Replace all `qa_mode=True` references with `session_mode=PersonaType.TEAMMATE` (or "Teammate persona") in the documentation.

#### [internal-naming-drift] "coaching_message" vs "nudge_feedback" terminology

The `ClassificationResult.coaching_message` field in the summarizer (line 164) uses "coaching" terminology. Issue #674 is renaming vestigial "coach" terminology to "nudge_feedback" across the codebase. The summarizer's `coaching_message` field, the `CLASSIFIER_SYSTEM_PROMPT` coaching instructions, and the heuristic coaching logic will need updating once #674 completes. Currently ~30 references to "coaching" exist in `bridge/summarizer.py`.

**File:** `bridge/summarizer.py:164,280-437,562,831-852`
**Recommendation:** Track as dependency on #674. Once the rename lands, update `coaching_message` field and all prompt references to use the new terminology.

#### [missing-integration-test] No integration test exercises the full `classify_output()` LLM path

`classify_output()` is the async LLM-based classifier that determines routing decisions (question/status/completion/blocker/error). Unit tests mock the Anthropic client. No integration test calls `classify_output()` with a real API to verify the LLM classification path works end-to-end. The heuristic fallback is well-tested, but the primary LLM path is only tested with mocks.

**File:** `tests/unit/test_summarizer.py:836-1015` (all mocked)
**Recommendation:** Add an integration test that calls `classify_output()` with a real Anthropic API key and verifies the response is parseable and has reasonable confidence. Similar to the existing `TestSummarizeResponseIntegration` (line 231) which tests real Haiku summarization.

#### [missing-integration-test] No integration test for the `response.py -> summarizer` callback chain

The callback chain `agent_session_queue.py -> telegram_bridge.py -> response.py -> summarizer.py` is documented in `summarizer-format.md` but no integration test exercises it end-to-end. Each component is tested in isolation. A wiring failure between `response.py` and `summarizer.py` (e.g., wrong kwargs, changed return type) would not be caught.

**File:** `bridge/response.py:510-540`
**Recommendation:** Add an integration test that calls `send_response_with_files()` (or a test harness of it) with a mock Telegram client but real summarizer, verifying the full chain from raw text to structured output.

#### [config-gap] `OPENROUTER_URL` hardcoded in 5 modules with no shared constant

The OpenRouter API URL `https://openrouter.ai/api/v1/chat/completions` is hardcoded identically in:
- `bridge/summarizer.py:45`
- `scripts/autoexperiment.py:40`
- `tools/doc_summary/__init__.py:16`
- `tools/image_analysis/__init__.py:18`
- `tools/knowledge_search/__init__.py:16`

If the URL changes, all 5 files must be updated independently. No env var override exists.

**File:** Multiple (see above)
**Recommendation:** Extract to a shared config constant (e.g., `config/models.py:OPENROUTER_URL`) or read from `OPENROUTER_URL` env var with the current URL as default.

#### [config-gap] `FILE_ATTACH_THRESHOLD`, `SAFETY_TRUNCATE`, `CLASSIFICATION_CONFIDENCE_THRESHOLD` not configurable

These three constants control summarizer behavior but cannot be overridden via environment variables. While they rarely need changing in production, they cannot be tuned for testing or debugging without code changes.

**File:** `bridge/summarizer.py:41-49`
**Recommendation:** Low priority. Consider reading from env with defaults: `int(os.environ.get("FILE_ATTACH_THRESHOLD", 3000))`. Not urgent since the current values are well-tuned.

#### [inconsistent-interface] Two parallel classifiers with different interfaces

`bridge/summarizer.py:classify_output()` is an async LLM-based classifier returning `ClassificationResult` with `OutputType` enum values. `agent/agent_session_queue.py:classify_nudge_action()` is a sync pure function returning string action names. Both classify agent output for routing, but they use completely different type systems and are called at different points in the pipeline.

This is intentional by design (one classifies content type, the other decides delivery action), but the naming `classify_*` for both can be confusing. The nudge loop does not consume `ClassificationResult` at all -- it has its own independent routing logic.

**File:** `bridge/summarizer.py:702`, `agent/agent_session_queue.py:57`
**Recommendation:** Informational. The separation is correct (content classification vs. delivery routing). Consider renaming `classify_nudge_action` to `determine_delivery_action` or similar to reduce naming confusion.

### INFO

#### [missing-error-boundary] `_summarize_with_openrouter()` propagates JSON parse errors

At line 1226, `json.loads(tc["function"]["arguments"])` can raise `json.JSONDecodeError` if the OpenRouter API returns malformed tool call arguments. This is inside a try/except block, so it falls through to text-only fallback, but the error message logged is generic ("OpenRouter tool_use failed") without indicating the parse failure specifically.

**File:** `bridge/summarizer.py:1226`
**Recommendation:** Low priority. The fallback chain handles this gracefully. Could improve debug logging by catching `JSONDecodeError` separately.

#### [missing-error-boundary] `_write_full_output_file()` uses `tempfile.mkstemp` without cleanup

At line 975, temp files are created for long responses but no cleanup mechanism exists. The caller (`summarize_response`) returns the path in `SummarizedResponse.full_output_file`, and it is the responsibility of `response.py` to send and then clean up the file. However, if the send fails or the response object is dropped, the temp file leaks.

**File:** `bridge/summarizer.py:973-978`, `bridge/response.py:543-600`
**Recommendation:** Low priority. Temp files in `/tmp` are cleaned by the OS. Could add explicit cleanup in `response.py` after send.

#### [non-reusable-interface] `summarize_response()` and `classify_output()` tightly coupled to session model

Both functions accept an optional `session` parameter that is expected to be an `AgentSession` with specific attributes (`session_mode`, `message_text`, `classification_type`, `branch_name`, `slug`, `get_links()`, `_get_history_list()`, `session_id`, `status`). This coupling means the summarizer cannot easily be reused outside the bridge context (e.g., in a standalone CLI tool or notebook).

**File:** `bridge/summarizer.py:918-965,1379-1381`
**Recommendation:** Low priority. The summarizer is a bridge-internal module by design. If reuse becomes needed, extract a protocol/interface for the session dependency.

#### [partial-wiring] `_get_status_emoji` line 907 has a bare `pass` in exception handler

At line 907, a bare `pass` in the exception handler silently swallows errors when calling `session.get_links()`. While this is intentional (non-fatal), it means link-related bugs in the session model could go unnoticed during emoji selection.

**File:** `bridge/summarizer.py:907`
**Recommendation:** Add `logger.debug()` to the exception handler for observability.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| WARNING | 8 |
| INFO | 4 |

**Overall assessment: B+**

The summarizer is well-integrated. All code paths are reachable, entry points are wired, and test coverage is extensive (~130 tests). The main gaps are:

1. **Documentation staleness** (2 findings) -- `summarizer-format.md` references removed functions and deprecated `qa_mode` field
2. **Naming drift** (1 finding) -- "coaching" terminology pending #674 rename
3. **Missing integration tests** (2 findings) -- the LLM classify path and the response->summarizer callback chain lack end-to-end tests
4. **Config duplication** (2 findings) -- `OPENROUTER_URL` duplicated across 5 files, thresholds not env-configurable

No critical issues found. The summarizer is actively maintained and functional.

## Recommended Follow-Up

| Priority | Action | Effort |
|----------|--------|--------|
| High | Fix stale docs in `summarizer-format.md` (remove `_render_stage_progress`, `_render_link_footer`, replace `qa_mode` with Teammate persona) | 15 min |
| Medium | Add integration test for `classify_output()` with real API | 30 min |
| Medium | Extract `OPENROUTER_URL` to shared config constant | 15 min |
| Low | Track coaching -> nudge_feedback rename as part of #674 | Covered by #674 |
| Low | Add integration test for response->summarizer chain | 45 min |
| Low | Make thresholds env-configurable | 10 min |
