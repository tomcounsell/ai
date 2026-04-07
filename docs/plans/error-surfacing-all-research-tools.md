---
status: Planning
type: refactor
appetite: Medium
owner: Valor
created: 2026-04-07
tracking: TBD
plan_url: https://github.com/yudame/cuttlefish/blob/main/docs/plans/error-surfacing-all-research-tools.md
last_comment_id:
---

# Error Surfacing: All Research Tools

## Problem

PR #228 fixed error surfacing for the Perplexity research tool. The other five tools
(Grok, Gemini, GPT-Researcher, Together, Claude) still silently swallow API errors.

When Grok returns a 429, or Gemini raises a non-quota exception, or GPT-Researcher
returns `None` after a network failure, the artifact content is either an unspecific
`[SKIPPED: ... returned no content]` or, in Claude's case, a `[SKIPPED: ... failed - {e}]`
string that happens to include the exception message but uses the wrong prefix. The
pipeline's fan-in resolver distinguishes `[FAILED: ...]` from `[SKIPPED: ...]` to
determine whether research actually ran and encountered an error versus was intentionally
skipped. Using `[SKIPPED: ...]` for API errors hides failures from operators and prevents
the pipeline from correctly identifying which sources need manual retry.

**Root cause summary:**

| Tool | Tool layer return | Service layer writes on error |
|------|------------------|-------------------------------|
| Perplexity | `(None, {_error_status, _error_message, _error_body})` | `[FAILED: Perplexity API {status} - {reason}]` (fixed in PR #228) |
| Grok | `(None, {})` — empty dict on error | `[SKIPPED: Grok research returned no content]` (gap: no service function exists yet) |
| Gemini | raises `GeminiQuotaError` for 429, `return None` for other errors | `[SKIPPED: Gemini API quota exceeded]` or `[SKIPPED: Gemini API error]` |
| GPT-Researcher | `None` or `""` | `[SKIPPED: GPT-Researcher returned no content]` |
| Together | `(None, metadata_with_error_key)` on failure | `[SKIPPED: Together research returned no content]` |
| Claude | raises `RuntimeError` or other exceptions | `[SKIPPED: Claude research failed - {str(e)}]` (wrong prefix) |

## Reference Pattern (Perplexity — PR #228)

### Tool layer contract

```python
def run_perplexity_research(...) -> tuple[str | None, dict]:
    # On success:  return content_text, response_dict
    # On API error: return None, {
    #     "_error_status": 429,
    #     "_error_message": "rate_limit_exceeded",
    #     "_error_body": {...raw api body...},
    # }
    # On no-key:   return None, {}  (caller checks env var first)
```

### Service layer pattern

```python
content_text, response_data = _tool(prompt=full_prompt, verbose=False)

if content_text is None or content_text == "":
    error_status = response_data.get("_error_status") if response_data else None
    error_message = response_data.get("_error_message") if response_data else None
    error_body = response_data.get("_error_body") if response_data else None

    if error_status:
        content = f"[FAILED: ToolName API {error_status} - {error_message}]"
        metadata = {"error": str(error_body or error_message)}
        description = f"ToolName (failed - API returned {error_status})."
    else:
        content = "[FAILED: ToolName API returned empty content]"
        metadata = {"error": "API returned no content"}
        description = "ToolName (failed - empty content)."

    artifact, _ = EpisodeArtifact.objects.update_or_create(...)
    return artifact
```

**Key invariant:** `[SKIPPED: ...]` is reserved for intentional skips (missing API key,
unconfigured service). `[FAILED: ...]` is for all unexpected failures — API errors,
empty 200 responses, exceptions during the call.

## Per-Tool Analysis and Required Changes

### 1. Grok (`apps/podcast/tools/grok_deep_research.py`)

**Current state:**
- `run_grok_research()` returns `(str | None, dict)` — same signature as Perplexity
- `_handle_error_response()` logs error but returns `None` implicitly
- Call site: `return None, {}` on non-200 response — error info lost

**No service layer function exists.** `run_grok_research` is not called from
`research.py` and is not wired into `tasks.py`. This refactor includes adding it.

**Tool layer changes (`grok_deep_research.py`):**
- `_handle_error_response(response) -> dict`: extract `_error_status`, `_error_message`
  from response body using the same `.get()` chain as Perplexity; return the dict
- At the `else` branch (line 283): `return None, _handle_error_response(response)`

**New service function (`research.py`):**

```python
def run_grok_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    import os
    episode = Episode.objects.get(pk=episode_id)

    if not os.getenv("GROK_API_KEY"):
        # write [SKIPPED: GROK_API_KEY not configured]
        ...
        return artifact

    context = _get_episode_context(episode)
    full_prompt = f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"

    from apps.podcast.tools.grok_deep_research import run_grok_research as _grok
    content_text, response_data = _grok(prompt=full_prompt, verbose=False)

    if content_text is None or content_text == "":
        error_status = response_data.get("_error_status") if response_data else None
        ...  # write [FAILED: Grok API {status} - {reason}] or [FAILED: Grok API returned empty content]
        return artifact

    # success path
    from apps.podcast.tools.grok_deep_research import extract_metadata
    metadata = extract_metadata(response_data) if response_data else {}
    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-grok",
        defaults={"content": content_text, "description": "Grok deep research output.",
                  "workflow_context": "Research Gathering", "metadata": metadata},
    )
    return artifact
```

**Tasks integration:** Wire `run_grok_research` into `tasks.py` as `step_grok_research`
and add it to the fan-in signal in `signals.py`. (This is a new pipeline step — confirm
with Tom before merging since it changes workflow phase counts.)

---

### 2. Gemini (`apps/podcast/tools/gemini_deep_research.py`)

**Current state:**
- `run_gemini_research()` returns `str | None` — no error dict, no structured error info
- 429 specifically raises `GeminiQuotaError` (caught in `research.py`)
- All other errors cause `return None` from the tool
- Service layer catches `GeminiQuotaError` → `[SKIPPED: Gemini API quota exceeded]`
- `None` return → `[SKIPPED: Gemini API error or empty response]`

**Tool layer changes (`gemini_deep_research.py`):**
- Change signature: `run_gemini_research(...) -> tuple[str | None, dict]`
- On 429: instead of raising `GeminiQuotaError`, return `(None, {"_error_status": 429, "_error_message": "quota_exceeded"})`
- On other non-200: return `(None, {"_error_status": status_code, "_error_message": reason})`
- On success: return `(content, {})` or `(content, metadata_dict)`

Note: `GeminiQuotaError` was the previous escape hatch. After this change it can be
deprecated, but keep it defined (and not raised) to avoid breaking any other callers.

**Service layer changes (`research.py` — `run_gemini_research`):**
- Remove `try/except GeminiQuotaError` block
- Apply the standard error-dict pattern
- Remove the `if content_text is None` path that writes `[SKIPPED: ...]`; replace with
  the `[FAILED: Gemini API {status} - {reason}]` / `[FAILED: Gemini API returned empty content]` pattern
- Keep `[SKIPPED: GEMINI_API_KEY not configured]` unchanged (happens before the API call)

---

### 3. GPT-Researcher (`apps/podcast/tools/gpt_researcher_run.py`)

**Current state:**
- `run_research(prompt)` is async, returns `str | None`
- No error dict, no structured error info
- The service bridges async→sync with `async_to_sync`
- Service writes `[SKIPPED: GPT-Researcher returned no content]` on `None` or `""`

**Tool layer changes (`gpt_researcher_run.py`):**

GPT-Researcher is a framework (not a raw HTTP client), so error surfacing is different:
the library's own exceptions propagate rather than HTTP status codes. The approach:

- Change `run_research()` return type to `tuple[str | None, dict]`
- Wrap the library call in `try/except`; on exception, return
  `(None, {"_error_status": None, "_error_message": str(e), "_error_type": type(e).__name__})`
- On success: return `(content, {})`

**Service layer changes (`research.py` — `run_gpt_researcher`):**
- Apply error-dict pattern
- On `_error_message` present (no `_error_status`): write
  `[FAILED: GPT-Researcher {error_type} - {error_message}]`
- On `None` with empty dict: write `[FAILED: GPT-Researcher returned empty content]`
- Keep `[SKIPPED: GPT-Researcher returned no content]` → replace with `[FAILED: ...]`
  (the current SKIPPED is incorrect — an empty return is unexpected, not intentional)

---

### 4. Together (`apps/podcast/tools/together_deep_research/runner.py`)

**Current state:**
- `run_together_research()` already returns `tuple[str | None, dict]`
- On `TimeoutError`: returns `(None, {"error": "Timed out after {elapsed}s", ...})`
- On other exceptions: returns `(None, {"error": str(e), ...})`
- Uses `"error"` key (not `"_error_status"`) — inconsistent with Perplexity pattern
- Service layer: checks `content_text is None` but only reads `metadata` for success path;
  on failure writes `[SKIPPED: Together research returned no content]`

**Tool layer changes (`runner.py`):**
- No structural changes needed — the tool already returns `(None, dict)` on failure
- Enrich the error dict to include `_error_status` and `_error_message` keys in addition
  to existing `error` key for consistency:
  - `TimeoutError` → `_error_status: "TIMEOUT"`, `_error_message: f"timed out after {elapsed}s"`
  - `Exception` → `_error_status: type(e).__name__`, `_error_message: str(e)`
  - Missing keys (no LLM key, no Tavily key) already return `(None, {})` — service
    layer handles those via the pre-call env var checks

**Service layer changes (`research.py` — `run_together_research`):**
- In the `content_text is None or content_text == ""` branch, read `_error_status` and
  `_error_message` from `metadata` (the dict returned by the tool)
- Write `[FAILED: Together {error_status} - {error_message}]` when error info present
- Write `[FAILED: Together returned empty content]` when error info absent
- Replace the existing `[SKIPPED: Together research returned no content]` with the above

---

### 5. Claude (`apps/podcast/services/claude_deep_research/orchestrate.py`)

**Current state:**
- `deep_research(command)` raises `RuntimeError` when all subagents fail
- Other exceptions (API errors in planner/synthesizer) propagate uncaught
- Service layer: broad `except Exception as e` → `[SKIPPED: Claude research failed - {str(e)}]`
  — wrong prefix (should be `[FAILED: ...]`)

**Orchestrator changes (no tool layer change needed):**
- `deep_research()` already raises on failure — the interface is exception-based, not
  tuple-based. This is acceptable for Claude because it's a multi-agent pipeline with
  no single HTTP status code to surface.

**Service layer changes (`research.py` — `run_claude_research`):**
- The existing `except Exception` catch block writes `[SKIPPED: ...]` — change prefix
  to `[FAILED: Claude {error_type} - {error_message}]`
- Keep `[SKIPPED: Claude research returned no report]` for the `report is None` case
  (that's the normal "no output" path, distinct from an exception)
- Store `metadata["error"]` and `metadata["error_type"]` — already done, just correct prefix

Specifically, change:
```python
# Before
"content": f"[SKIPPED: Claude research failed - {str(e)}]",
```
to:
```python
# After
"content": f"[FAILED: Claude {type(e).__name__} - {str(e)}]",
```

---

## Service Layer Summary

Changes to `apps/podcast/services/research.py`:

| Function | Change |
|----------|--------|
| `run_grok_research` | New function (does not exist yet) |
| `run_gemini_research` | Remove `GeminiQuotaError` catch; apply error-dict pattern |
| `run_gpt_researcher` | Apply error-dict pattern from tool return value |
| `run_together_research` | Read `_error_status`/`_error_message` from metadata dict |
| `run_claude_research` | Change `[SKIPPED: ...]` to `[FAILED: ...]` in exception branch |

## Test Strategy

### New test file: `apps/podcast/tests/test_research_error_surfacing.py`

One test class per tool. All tests mock at the tool layer boundary (not the HTTP
boundary) so they work without real API credentials.

**Grok:**
```python
class TestGrokErrorSurfacing:
    def test_grok_api_401_writes_failed_artifact(self): ...
    def test_grok_api_429_writes_failed_artifact(self): ...
    def test_grok_empty_content_writes_failed_artifact(self): ...
    def test_grok_missing_api_key_writes_skipped_artifact(self): ...
```

**Gemini:**
```python
class TestGeminiErrorSurfacing:
    def test_gemini_quota_429_writes_failed_artifact(self): ...
    def test_gemini_api_500_writes_failed_artifact(self): ...
    def test_gemini_empty_content_writes_failed_artifact(self): ...
    def test_gemini_missing_api_key_writes_skipped_artifact(self): ...
```

**GPT-Researcher:**
```python
class TestGptResearcherErrorSurfacing:
    def test_gpt_exception_writes_failed_artifact(self): ...
    def test_gpt_empty_content_writes_failed_artifact(self): ...
```

**Together:**
```python
class TestTogetherErrorSurfacing:
    def test_together_timeout_writes_failed_artifact(self): ...
    def test_together_exception_writes_failed_artifact(self): ...
    def test_together_empty_content_writes_failed_artifact(self): ...
    def test_together_missing_keys_writes_skipped_artifact(self): ...
```

**Claude:**
```python
class TestClaudeErrorSurfacing:
    def test_claude_runtime_error_writes_failed_artifact(self): ...
    def test_claude_api_error_writes_failed_artifact(self): ...
    def test_claude_none_report_writes_skipped_artifact(self): ...
```

### Existing tests to verify (not modify)

- `apps/podcast/tests/test_task_steps.py` — verify `[SKIPPED: ...]` assertions still
  pass for missing-key paths (those paths are unchanged)
- `apps/podcast/tools/tests/test_research_tools.py` — verify Grok and Gemini tool-level
  tests still pass after signature changes

### Tool-layer unit tests

Add to `apps/podcast/tools/tests/test_research_tools.py`:
- Grok: `_handle_error_response()` returns dict with `_error_status` and `_error_message`
- Gemini: non-200 returns `(None, {_error_status, _error_message})` instead of raising

## Success Criteria

Each tool satisfies all of the following:

- [ ] A 429 or 401 API response writes `[FAILED: <ToolName> API {status} - {reason}]`
- [ ] An empty content response (200 with no body) writes `[FAILED: <ToolName> API returned empty content]` (or `[FAILED: <ToolName> returned empty content]` for non-HTTP tools)
- [ ] A missing API key still writes `[SKIPPED: <ENV_VAR> not configured]` (unchanged)
- [ ] Raw error info stored in `artifact.metadata["error"]`
- [ ] All artifact prefixes follow the `[FAILED: ...]` / `[SKIPPED: ...]` convention so `_resolve_substep_status()` classifies them correctly

**Per-tool expected artifact content strings:**

| Tool | Error condition | Artifact content |
|------|----------------|-----------------|
| Grok | 401 | `[FAILED: Grok API 401 - {reason}]` |
| Grok | 429 | `[FAILED: Grok API 429 - {reason}]` |
| Grok | Empty 200 | `[FAILED: Grok API returned empty content]` |
| Grok | No API key | `[SKIPPED: GROK_API_KEY not configured]` |
| Gemini | 429 | `[FAILED: Gemini API 429 - quota_exceeded]` |
| Gemini | 500 | `[FAILED: Gemini API 500 - {reason}]` |
| Gemini | Empty | `[FAILED: Gemini API returned empty content]` |
| Gemini | No API key | `[SKIPPED: GEMINI_API_KEY not configured]` |
| GPT-Researcher | Exception | `[FAILED: GPT-Researcher {ExcType} - {message}]` |
| GPT-Researcher | Empty | `[FAILED: GPT-Researcher returned empty content]` |
| Together | Timeout | `[FAILED: Together TIMEOUT - timed out after {n}s]` |
| Together | Exception | `[FAILED: Together {ExcType} - {message}]` |
| Together | Empty | `[FAILED: Together returned empty content]` |
| Together | No keys | `[SKIPPED: Missing API keys - ...]` (unchanged) |
| Claude | RuntimeError | `[FAILED: Claude RuntimeError - {message}]` |
| Claude | Any exception | `[FAILED: Claude {ExcType} - {message}]` |
| Claude | None report | `[SKIPPED: Claude research returned no report]` (unchanged) |

## Appetite

**Size:** Medium (5 files across tool and service layers; one new service function; one
new test module)

**Team:** Solo dev

## Prerequisites

- PR #228 merged (Perplexity reference pattern in production) ✓
- Confirm with Tom before wiring Grok into `tasks.py` / `signals.py` (new pipeline step)

## Rabbit Holes

- **Retry logic on 429:** Out of scope. This plan surfaces errors; it does not handle
  them differently. Retry belongs in a separate issue.
- **Grok pipeline wiring:** The `run_grok_research` service function is new but wiring
  it as a live pipeline step changes workflow phase counts and fan-in thresholds. Confirm
  before `tasks.py` changes.
- **Gemini `GeminiQuotaError` removal:** Keep the class definition — removing it is a
  breaking change if any external caller imports it. Just stop raising it.
- **GPT-Researcher library internals:** Do not try to extract HTTP status from GPT-
  Researcher exceptions — the library abstracts HTTP away. Use exception type + message.

## Risks

### Risk 1: Gemini signature change breaks CLI usage
**Impact:** If `run_gemini_research()` is called directly from CLI scripts, changing
return type from `str | None` to `tuple` breaks those callers.
**Mitigation:** Search for all callers before changing signature. CLI scripts in
`apps/podcast/tools/` that call `run_gemini_research()` directly must be updated.

### Risk 2: Together error dict key collision
**Impact:** Together's existing metadata dict uses `"error"` key; adding `"_error_status"`
and `"_error_message"` must not overwrite useful existing keys.
**Mitigation:** Use underscore-prefixed keys to avoid collision with Together's own
`"error"` key.

### Risk 3: Grok not in pipeline — new step affects fan-in thresholds
**Impact:** Adding Grok as a live pipeline step changes how many p2-* artifacts are
required before Phase 3 can advance.
**Mitigation:** Confirm pipeline wiring with Tom before merging. The error surfacing
changes to the tool and service layer can ship independently of the pipeline wiring.

## No-Gos (Out of Scope)

- Retry or backoff logic on rate limit errors
- UI-level formatting changes (the resolver already handles `[FAILED: ...]`)
- MiroFish error surfacing (MiroFish is already handled by a broad `except Exception`
  block that writes `[SKIPPED: MiroFish research failed - {exc}]` — its SKIPPED prefix
  is also incorrect but MiroFish is an optional sidecar, lower priority)
- Schema migrations (JSONField `metadata` requires no migration)

## Step-by-Step Tasks

### 1. Fix Grok tool layer (`grok_deep_research.py`)
- Change `_handle_error_response()` to return dict with `_error_status`, `_error_message`, `_error_body`
- Update call site to `return None, _handle_error_response(response)`
- Add `TestGrokHandleErrorResponse` to `test_research_tools.py`

### 2. Add Grok service function (`research.py`)
- Implement `run_grok_research(episode_id, prompt)` following reference pattern
- Include missing-key skip, error-dict FAILED, and empty-content FAILED paths
- Add `TestGrokErrorSurfacing` to new test file

### 3. Fix Gemini tool layer (`gemini_deep_research.py`)
- Change return type to `tuple[str | None, dict]`
- Replace `raise GeminiQuotaError(...)` with `return None, {"_error_status": 429, ...}`
- Handle other non-200 status codes with structured error dict
- Add `TestGeminiHandleErrorResponse` to `test_research_tools.py`

### 4. Fix Gemini service layer (`research.py` — `run_gemini_research`)
- Remove `try/except GeminiQuotaError` block
- Apply error-dict pattern using `_error_status` from returned dict
- Preserve `[SKIPPED: GEMINI_API_KEY not configured]` path

### 5. Fix GPT-Researcher tool layer (`gpt_researcher_run.py`)
- Wrap library call in `try/except`
- Return `(None, {"_error_message": str(e), "_error_type": type(e).__name__})` on exception
- Return `(content, {})` on success

### 6. Fix GPT-Researcher service layer (`research.py` — `run_gpt_researcher`)
- Apply error-dict pattern for exception case
- Write `[FAILED: GPT-Researcher returned empty content]` for empty-content case

### 7. Fix Together tool layer (`runner.py`)
- Add `_error_status` and `_error_message` keys to existing error metadata dicts
- Timeout: `_error_status: "TIMEOUT"`, `_error_message: f"timed out after {elapsed}s"`
- Exception: `_error_status: type(e).__name__`, `_error_message: str(e)`

### 8. Fix Together service layer (`research.py` — `run_together_research`)
- Read `_error_status`/`_error_message` from metadata dict on `content_text is None`
- Write `[FAILED: Together {status} - {message}]` or `[FAILED: Together returned empty content]`

### 9. Fix Claude service layer (`research.py` — `run_claude_research`)
- Change `[SKIPPED: Claude research failed - {str(e)}]` to `[FAILED: Claude {type(e).__name__} - {str(e)}]`
- No orchestrator changes needed

### 10. Write and run all new tests
- Create `apps/podcast/tests/test_research_error_surfacing.py`
- Run full test suite; confirm all existing tests pass
- Lint and format clean

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New error surfacing tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_research_error_surfacing.py -v` | exit code 0 |
| Tool-level tests | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tools/tests/test_research_tools.py -v` | exit code 0 |
| Task step tests (regression) | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_task_steps.py -v` | exit code 0 |
| Lint | `uv run flake8 apps/podcast/tools/ apps/podcast/services/research.py` | exit code 0 |
| Format | `uv run black --check apps/podcast/tools/ apps/podcast/services/research.py` | exit code 0 |

## Open Questions

1. **Grok pipeline wiring:** Should `step_grok_research` be added to `tasks.py` and
   `signals.py` as part of this refactor, or deferred to a separate issue? The error
   surfacing changes to the tool/service layer are independent and can ship without it.

2. **MiroFish:** Should `run_mirofish_research`'s exception branch also be changed from
   `[SKIPPED: ...]` to `[FAILED: ...]`? It uses the same incorrect prefix but is lower
   priority (optional sidecar). Include in this issue or defer?
