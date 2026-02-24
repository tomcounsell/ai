# Plan: Fix Research Pipeline Issues #89 and #90

**Issues:** [#89](https://github.com/yudame/cuttlefish/issues/89) (Claude Deep Research validation failures), [#90](https://github.com/yudame/cuttlefish/issues/90) (Gemini Deep Research empty content)

## Investigation Findings

### Issue #89: Claude Deep Research — RESOLVED

**Original symptom:** `pydantic_ai.exceptions.UnexpectedModelBehavior: Exceeded maximum retries for output validation` on all 5 subagents.

**Testing (2026-02-24):** Full pipeline works end-to-end. Planner (Opus), researcher (Sonnet), and synthesizer (Opus) all produce valid `SubagentFindings` and `DeepResearchReport` outputs without validation errors.

**Root cause:** Transient issue, likely fixed by dependency updates. Current versions: `pydantic-ai==1.56.0`, `anthropic==0.79.0`. The original failure was probably caused by a PydanticAI/Anthropic SDK incompatibility or a temporary API response format change that has since been resolved.

**Remaining work:** Remove the "optional/skip" workaround or keep it as defensive coding (recommended to keep — it's good practice for any external API call). Close the issue.

### Issue #90: Gemini Deep Research — QUOTA ISSUE

**Original symptom:** Gemini API returns 0 characters, creating empty `p2-gemini` artifacts.

**Testing (2026-02-24):** Confirmed. API returns HTTP 429:
```
"You do not have enough quota to make this request."
quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier
```

The `GEMINI_API_KEY` is on the **free tier** with a limit of **0 RPM/RPD** for Deep Research. Regular Gemini Pro calls also fail with the same quota error. This is not a code bug — it's a billing/plan issue.

**Root cause:** Free-tier API key has exhausted all quota. Gemini Deep Research requires a paid tier ([Google AI pricing](https://ai.google.dev/gemini-api/docs/pricing) states Deep Research uses standard Gemini 3 Pro rates, paid tier only).

## Plan

### Task 1: Improve Gemini error detection (code change)

Currently `gemini_deep_research.py` returns `None` for both quota errors and genuine empty results. Improve error handling to distinguish between:
- **Quota/billing errors** (429) → clear error message + specific skip reason
- **API errors** (4xx/5xx) → generic error skip
- **Empty content** (200 but no text) → different skip reason

**Files to change:**
- `apps/podcast/tools/gemini_deep_research.py` — `submit_research()` and `run_gemini_research()`: return structured error info instead of bare `None`
- `apps/podcast/services/research.py` — `run_gemini_research()`: use structured error to create more specific skip artifact metadata

**Approach:**
- Make `submit_research()` raise a custom `GeminiQuotaError` on 429 responses instead of returning `None`
- Make `run_gemini_research()` (the tool function) catch `GeminiQuotaError` separately from other errors
- In the service layer, create skip artifacts with specific `reason` metadata: `"quota_exceeded"` vs `"api_error"` vs `"empty_response"`
- Log a clear actionable message: `"Gemini API quota exceeded. Upgrade billing at https://aistudio.google.com/apikey"`

### Task 2: Upgrade Gemini API billing (requires human action)

The `GEMINI_API_KEY` needs to be upgraded to a paid tier. This is a **human action** — Tom needs to:

1. Go to https://aistudio.google.com/apikey
2. Enable billing on the Google Cloud project associated with the API key
3. Optionally generate a new key if the current one is permanently rate-limited

**No code change needed.** The existing graceful degradation handles this correctly.

### Task 3: Close issue #89

Claude Deep Research works. The defensive error handling already in place is correct. Close with a comment explaining the resolution.

### Task 4: Update issue #90

Update the issue with investigation findings and the billing action required. Keep open until billing is resolved.

## Success Criteria

1. `gemini_deep_research.py` produces actionable log messages distinguishing quota errors from other failures
2. Skip artifacts contain specific `reason` metadata (`quota_exceeded` vs `api_error` vs `empty_response`)
3. Issue #89 closed with resolution notes
4. Issue #90 updated with findings and next steps
5. All existing tests pass
