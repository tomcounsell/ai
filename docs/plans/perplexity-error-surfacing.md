---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-07
tracking: https://github.com/yudame/cuttlefish/issues/225
last_comment_id:
---

# Perplexity Error Surfacing

## Problem

When the Perplexity API returns a non-200 status (401, 429, 500), the failure is silently collapsed into a generic `[SKIPPED: Perplexity API returned no content]` artifact message. The HTTP status code and API error message are logged only to the server log — they never reach the artifact, the UI, or the database.

**Current behavior:**
- `_handle_error_response()` in `perplexity_deep_research.py:437` logs the error but returns `None, {}` — the caller always gets `(None, {})`
- `research.py:96` treats `content_text is None` identically regardless of cause (API error vs empty response)
- A 401 `insufficient_quota` and a missing API key write the same artifact: `[SKIPPED: Perplexity API returned no content]`
- The workflow status resolver already distinguishes `[SKIPPED: ...]` from `[FAILED: ...]` — but the wrong prefix is being written

**Desired outcome:**
- API errors write `[FAILED: Perplexity API {status_code} - {error_type}]` to the artifact
- Missing API key continues to write `[SKIPPED: PERPLEXITY_API_KEY not configured]` (intentional graceful degradation — unchanged)
- Empty response with 200 status writes `[FAILED: Perplexity API returned empty content]`
- Raw API error message stored in `artifact.metadata["error"]`

## Prior Art

- **PR #157** (merged 2026-03-10): "Fix empty Perplexity research breaking question discovery" — Added auto-retry logic in `tasks.py` when Phase 3 detects no usable p2-* artifacts. Did not address error visibility in artifacts; it treated the symptom (missing research) without surfacing the cause (API failure reason).

No prior attempts to fix the error surfacing problem directly.

## Data Flow

Current failure data flow (broken):

1. **Entry**: `research.py` calls `run_perplexity_research(prompt=...)`
2. **Tool layer** (`perplexity_deep_research.py`): receives non-200 response → calls `_handle_error_response(response)` which logs status+body but returns `None` → `run_perplexity_research` returns `(None, {})`
3. **Service layer** (`research.py`): receives `(None, {})` → checks `if content_text is None` → writes `[SKIPPED: Perplexity API returned no content]` to artifact with `metadata={"skipped": True, "reason": "API returned no content"}`
4. **Status resolver** (`workflow_progress.py`): sees `[SKIPPED: ...]` prefix → returns `("skipped", "")` — error is invisible

Fixed data flow:

1. **Entry**: `research.py` calls `run_perplexity_research(prompt=...)`
2. **Tool layer**: `_handle_error_response()` extracts status code + error type/message from response body → returns dict with `_error_status` and `_error_message` keys → `run_perplexity_research` returns `(None, {"_error_status": 401, "_error_message": "insufficient_quota", ...})`
3. **Service layer**: receives `(None, response_data)` → inspects `response_data` for `_error_status` → writes `[FAILED: Perplexity API 401 - insufficient_quota]` with `metadata={"error": "<raw api message>"}`
4. **Status resolver**: sees `[FAILED: ...]` prefix → returns `("failed", "Perplexity API 401 - insufficient_quota")` — error visible in UI

## Architectural Impact

- **Interface changes**: `_handle_error_response()` return type changes from `None` to `dict` with error keys. Callers inside `perplexity_deep_research.py` must be updated.
- **New dependencies**: None — error details extracted from the existing `response.json()` call already in `_handle_error_response()`
- **Coupling**: No change — both files already coupled; this adds a richer data contract over the same interface
- **Reversibility**: Easy — return type change is backward compatible if callers check for key existence

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies beyond the existing Perplexity API integration.

## Solution

### Key Elements

- **`_handle_error_response()` return value**: Instead of returning `None` (implicitly), return a dict with `_error_status` (int) and `_error_message` (str) extracted from the response body
- **`run_perplexity_research()` pass-through**: When calling `_handle_error_response()`, capture its return value and include it in the `(None, error_data)` tuple instead of `(None, {})`
- **`research.py` error branching**: When `content_text is None`, inspect `response_data` for `_error_status`; write `[FAILED: ...]` for API errors, keep `[SKIPPED: ...]` only for missing API key and no-content 200s; store raw error in `metadata["error"]`

### Flow

API call → non-200 response → `_handle_error_response()` returns `{_error_status, _error_message}` → `run_perplexity_research` returns `(None, error_data)` → `research.py` writes `[FAILED: Perplexity API {status} - {reason}]` → `_resolve_substep_status()` returns `("failed", reason)` → UI shows failure

### Technical Approach

**`perplexity_deep_research.py`:**
```python
def _handle_error_response(response) -> dict:
    try:
        error_body = response.json()
    except Exception:
        error_body = {"message": response.text[:500]}

    # Extract error type from common Perplexity error shapes
    error_message = (
        error_body.get("error", {}).get("type")
        or error_body.get("error", {}).get("message")
        or error_body.get("detail")
        or str(response.status_code)
    )
    # ... existing logging unchanged ...
    return {"_error_status": response.status_code, "_error_message": error_message, "_error_body": error_body}
```

At call site (line 433):
```python
    else:
        error_data = _handle_error_response(response)
        return None, error_data
```

**`research.py`:**
```python
if content_text is None or content_text == "":
    error_status = response_data.get("_error_status") if response_data else None
    error_message = response_data.get("_error_message") if response_data else None
    error_body = response_data.get("_error_body") if response_data else None

    if error_status:
        content = f"[FAILED: Perplexity API {error_status} - {error_message}]"
        metadata = {"error": str(error_body or error_message)}
        description = f"Perplexity Deep Research (failed - API returned {error_status})."
    else:
        content = "[FAILED: Perplexity API returned empty content]"
        metadata = {"error": "API returned no content"}
        description = "Perplexity Deep Research (failed - empty content)."

    artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-perplexity",
        defaults={"content": content, "description": description,
                  "workflow_context": "Research Gathering", "metadata": metadata},
    )
    return artifact
```

Note: The `[SKIPPED: PERPLEXITY_API_KEY not configured]` path is earlier in `research.py` (before the API call) and is NOT touched by this fix.

## Failure Path Test Strategy

### Exception Handling Coverage
- `_handle_error_response()` has a `try/except Exception` around `response.json()` — the fix preserves this and still falls back to `response.text[:500]` on parse failure. A test should assert the dict is returned even when JSON parsing fails.

### Empty/Invalid Input Handling
- `response_data` may be `{}` (empty dict) if coming from the JSON parse error path — `response_data.get("_error_status")` returns `None` safely, triggering the empty-content branch
- `error_message` extraction uses `.get()` chains with fallback to `str(response.status_code)` — never raises

### Error State Rendering
- `_resolve_substep_status()` already handles `[FAILED: ...]` prefix → `("failed", error_msg)` — UI rendering is covered by existing tests in `test_workflow_progress.py`
- New tests must verify the artifact content string written for 401 and 429 cases

## Test Impact

- `apps/podcast/tools/tests/test_research_tools.py` — UPDATE: The existing `TestPerplexityDeepResearch` class tests `run_perplexity_research` but mocks it entirely. Add new test cases for `_handle_error_response()` directly asserting it returns a dict with `_error_status` and `_error_message` keys.
- `apps/podcast/tests/test_task_steps.py` — UPDATE: `test_reruns_perplexity_when_only_skipped_research` checks for `[SKIPPED: ...]` content — verify this test still passes because it mocks at the `run_perplexity_research` level (not affected). If the mock returns `MagicMock()` artifact, it is fine; inspect carefully to ensure no breakage.

New tests to create:
- `apps/podcast/tests/test_perplexity_research_service.py` (new) — test `run_perplexity_research` service function in `research.py` with mocked `run_perplexity_research` tool; assert 401 → `[FAILED: Perplexity API 401 - ...]` artifact, 429 → `[FAILED: Perplexity API 429 - ...]`, empty 200 → `[FAILED: Perplexity API returned empty content]`, missing key → `[SKIPPED: PERPLEXITY_API_KEY not configured]` (unchanged)

## Rabbit Holes

- **Normalizing all error types across research tools** (Grok, Gemini, GPT-Researcher): Each has its own error handling. Scope is Perplexity only.
- **Retry logic on 429**: Rate limit retry/backoff is out of scope — this is only about surfacing the error, not handling it differently.
- **UI-level error formatting**: `_resolve_substep_status()` already renders `[FAILED: ...]` correctly. No UI changes needed.
- **Schema migrations for metadata**: `metadata` is a JSONField — no migration needed for new keys.

## Risks

### Risk 1: `_error_message` extraction varies by Perplexity error shape
**Impact:** Error message logged as raw status code string instead of human-readable type
**Mitigation:** Use `.get()` chain covering the most common Perplexity error shapes (`error.type`, `error.message`, `detail`); fallback to `str(status_code)` ensures the `[FAILED: ...]` prefix is always written with at least a numeric code

### Risk 2: Existing test assertions on `[SKIPPED: ...]` artifact content break
**Impact:** `test_reruns_perplexity_when_only_skipped_research` fails if it hard-codes the old SKIPPED message that this fix would change
**Mitigation:** That test mocks at the service level (`run_perplexity_research`), not the HTTP level, so it is not affected. Verify during build.

## Race Conditions

No race conditions identified — all operations are synchronous Django ORM calls and HTTP requests; no shared mutable state between calls.

## No-Gos (Out of Scope)

- Retry logic or backoff on 429 responses
- Error surfacing for Grok, Gemini, or GPT-Researcher tools
- Changing the `[SKIPPED: PERPLEXITY_API_KEY not configured]` message or behavior
- UI changes beyond what `_resolve_substep_status()` already provides
- Alerting or notification on API errors

## Update System

No update system changes required — this is a purely internal bug fix with no new dependencies, config files, or deployment changes.

## Agent Integration

No agent integration required — this is an internal service fix. The Perplexity research tool is invoked by the podcast workflow tasks, not directly by the agent via MCP.

## Documentation

- [ ] Update `docs/ERROR_HANDLING.md` to note that Perplexity API errors now surface as `[FAILED: ...]` artifact prefixes with HTTP status and error type

No new feature doc needed — this is a bug fix restoring expected behavior, not a new capability.

## Success Criteria

- [ ] A 401 response writes `[FAILED: Perplexity API 401 - insufficient_quota]` (or equivalent type) to the artifact
- [ ] A 429 response writes `[FAILED: Perplexity API 429 - rate_limit_exceeded]`
- [ ] A missing API key still writes `[SKIPPED: PERPLEXITY_API_KEY not configured]` (unchanged)
- [ ] A 200 response with empty content writes `[FAILED: Perplexity API returned empty content]`
- [ ] Raw API error message stored in `artifact.metadata["error"]`
- [ ] Tests cover 401 and 429 cases
- [ ] All existing tests pass (`/do-test`)
- [ ] Lint and format clean (`python -m ruff check .` and `python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (perplexity-error)**
  - Name: perplexity-builder
  - Role: Implement error surfacing changes in `perplexity_deep_research.py` and `research.py`, write new tests
  - Agent Type: builder
  - Resume: true

- **Validator (perplexity-error)**
  - Name: perplexity-validator
  - Role: Verify artifact content, test coverage, and no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement error surfacing in tool and service layers
- **Task ID**: build-perplexity-error
- **Depends On**: none
- **Validates**: `apps/podcast/tools/tests/test_research_tools.py`, `apps/podcast/tests/test_perplexity_research_service.py` (create)
- **Assigned To**: perplexity-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_handle_error_response()` in `apps/podcast/tools/perplexity_deep_research.py` to return dict with `_error_status`, `_error_message`, `_error_body` keys
- Update the `else` branch at line 433 to capture and forward the returned error dict: `return None, _handle_error_response(response)`
- Update `research.py` lines 96-112: inspect `response_data` for `_error_status`; write `[FAILED: ...]` for API errors, `[FAILED: Perplexity API returned empty content]` for empty 200s; store raw error in `metadata["error"]`
- Create `apps/podcast/tests/test_perplexity_research_service.py` with tests for 401, 429, empty 200, and missing API key cases

### 2. Update existing tool tests
- **Task ID**: build-tool-tests
- **Depends On**: build-perplexity-error
- **Validates**: `apps/podcast/tools/tests/test_research_tools.py`
- **Assigned To**: perplexity-builder
- **Agent Type**: builder
- **Parallel**: false
- Add tests to `TestPerplexityDeepResearch` covering `_handle_error_response()` directly: assert it returns dict with expected keys for 401, 429, 500 responses
- Assert fallback when `response.json()` raises (malformed JSON body)

### 3. Validate all success criteria
- **Task ID**: validate-all
- **Depends On**: build-perplexity-error, build-tool-tests
- **Assigned To**: perplexity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m pytest apps/podcast/tests/test_perplexity_research_service.py apps/podcast/tools/tests/test_research_tools.py apps/podcast/tests/test_task_steps.py -v`
- Run `python -m ruff check apps/podcast/tools/perplexity_deep_research.py apps/podcast/services/research.py`
- Verify artifact content strings match acceptance criteria exactly
- Confirm `[SKIPPED: PERPLEXITY_API_KEY not configured]` path is unchanged

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New service tests pass | `python -m pytest apps/podcast/tests/test_perplexity_research_service.py -v` | exit code 0 |
| Tool tests pass | `python -m pytest apps/podcast/tools/tests/test_research_tools.py -v` | exit code 0 |
| Task step tests pass | `python -m pytest apps/podcast/tests/test_task_steps.py -v` | exit code 0 |
| Lint clean | `python -m ruff check apps/podcast/tools/perplexity_deep_research.py apps/podcast/services/research.py` | exit code 0 |
| Format clean | `python -m ruff format --check apps/podcast/tools/perplexity_deep_research.py apps/podcast/services/research.py` | exit code 0 |
| FAILED prefix written for 401 | `python -m pytest apps/podcast/tests/test_perplexity_research_service.py::test_401_writes_failed_artifact -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

No open questions — the issue recon is complete and the solution approach is fully specified.
