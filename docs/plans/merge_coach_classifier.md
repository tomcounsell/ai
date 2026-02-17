---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-02-17
tracking: https://github.com/tomcounsell/ai/issues/130
---

# Merge Coach and Classifier into a Single LLM Pass

## Problem

The classifier (`bridge/summarizer.py`) and coach (`bridge/coach.py`) are two halves of the same decision split across two systems with a lossy free-text interface between them.

**Current behavior:**
1. Classifier makes an LLM call to classify output as question/status/completion/blocker/error
2. Classifier returns a free-text `reason` field
3. `_parse_classification_response()` tries to detect rejected completions by pattern-matching on the reason text (lines 421-432 in `summarizer.py`) — looking for strings like "hedg", "no evidence", "no proof"
4. Coach checks `was_rejected_completion` and emits a static template message — always the same text regardless of what specifically was wrong

**What breaks:**
- If the classifier LLM rephrases its reasoning (e.g. "lacks substantiation" instead of "no evidence"), the hedging detection misses it, `was_rejected_completion` stays False, and the agent gets a skill-aware coaching message or plain "continue" instead of rejection coaching
- The rejection coaching template is always identical — it can't say "you claimed tests pass but didn't show output" vs "you said 'should work' which is hedging"
- Two prompt engineering surfaces (classifier system prompt + coach templates) that must agree on what "done" means

**Desired outcome:**
A single Haiku call that classifies the output AND — when rejecting — generates the coaching message in the same pass. Most of the time (~60-70%) the classifier approves the output (completion, question, blocker, error) and it flows straight through to the summarizer with no coaching needed. Only when the classifier rejects (downgrading a completion to status, or flagging a status update for auto-continue) does it also produce a coaching message. One LLM call, two possible outcomes: pass (no coaching) or fail (coaching included).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (validate the combined prompt design)
- Review rounds: 1

## Prerequisites

No prerequisites — uses the same Anthropic API key and MODEL_FAST already in use.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | LLM classification calls |

## Solution

### Key Elements

- **Single Haiku call with pass/fail branching**: One prompt, one call. The LLM classifies AND coaches in the same response. `coaching_message` is null on pass (majority case — output goes to summarizer), populated on fail (auto-continue with guidance).
- **Structured output schema**: JSON response with `type`, `confidence`, `reason`, and `coaching_message` fields — no string matching needed. The `coaching_message` field being null vs populated IS the pass/fail signal.
- **Coach simplification**: `build_coaching_message()` becomes a thin dispatcher that uses the LLM-generated coaching when available and keeps existing skill/plan-aware coaching as enrichment for the heuristic fallback path.
- **Heuristic fallback preserved**: The existing `_classify_with_heuristics()` stays as fallback when LLM is unavailable, paired with existing static coach templates.

### Flow

**Agent output arrives** → `classify_output(text)` → single Haiku call → returns `ClassificationResult`:
- **Pass (~60-70%):** `type` is completion/question/blocker/error → `coaching_message` is null → output flows to summarizer → delivered to Telegram
- **Fail (~30-40%):** `type` is status (auto-continue needed) → `coaching_message` is populated with specific guidance → bridge uses it directly as the continuation prompt

Same Haiku call, same JSON response. The `coaching_message` field is simply null when the output is approved and populated when it's rejected. No second LLM call, no string matching.

### Technical Approach

1. **Extend `ClassificationResult`** — add `coaching_message: str | None` field. Null on pass-through types (completion, question, blocker, error). Populated on status/auto-continue.
2. **Merge the classifier prompt** — expand `CLASSIFIER_SYSTEM_PROMPT` to: "When classifying as `status`, also return a `coaching_message` explaining what the agent should do or fix. When classifying as any other type, set `coaching_message` to null." This is the key design: one prompt, two branches.
3. **Update JSON schema** — response becomes `{"type": "...", "confidence": 0.95, "reason": "...", "coaching_message": "..."|null}`. The majority of responses will have `coaching_message: null` since most output is approved.
4. **Remove `was_rejected_completion` detection** — the hedging pattern matching in `_parse_classification_response()` (lines 418-432) is deleted. The LLM now explicitly returns coaching when it rejects — no need to reverse-engineer intent from the reason field.
5. **Simplify `build_coaching_message()`** — check if `classification.coaching_message` exists first; if so, prefix with `[System Coach]` and return. Fall through to existing skill/plan-aware coaching only when no LLM coaching was provided (heuristic fallback path).
6. **Pass context to classifier** — optionally pass plan file path and active skill info so the LLM can reference success criteria in its coaching when rejecting.

## Rabbit Holes

- **Multi-turn coaching history** — Issue mentions seeing prior coaching messages. Defer this — it requires passing conversation history into the classifier, which is a separate concern. The single-pass merge is valuable on its own.
- **Replacing the heuristic fallback** — Keep `_classify_with_heuristics()` as-is. It's the offline safety net and doesn't need to generate coaching messages (the static templates in `coach.py` cover that path).
- **Changing the coach's skill-aware tiers** — Tier 2 (plan/skill coaching for status updates) works fine. Only Tier 1 (rejection coaching) needs to use LLM-generated messages. Don't refactor the whole coach.

## Risks

### Risk 1: LLM generates poor coaching messages
**Impact:** Agent gets unhelpful or confusing auto-continue messages
**Mitigation:** Include few-shot examples in the prompt. Keep the static template as fallback — if `coaching_message` is empty/missing, fall back to existing behavior.

### Risk 2: Increased token usage from longer prompt
**Impact:** Slightly higher cost per classification call
**Mitigation:** The prompt grows by ~200 tokens (coaching instructions + examples). At Haiku pricing this is negligible. The coaching message output adds ~50 tokens. We're saving the information loss that causes wasted auto-continue cycles, which cost far more.

### Risk 3: Structured output parsing becomes more complex
**Impact:** More fields to validate in `_parse_classification_response()`
**Mitigation:** `coaching_message` is optional — if missing or empty, fall through to existing coach logic. The parser already handles missing fields gracefully.

## No-Gos (Out of Scope)

- Multi-turn coaching memory (seeing prior coaching messages) — separate concern
- Changing the summarizer (response summarization is unrelated to classification)
- Modifying the heuristic fallback classifier beyond adding `coaching_message=None` to its returns
- Changing how the bridge calls `classify_output()` or `build_coaching_message()` — the external API stays the same
- Replacing Tier 2/3 coaching (skill-aware and plain continue) — only Tier 1 rejection coaching changes

## Update System

No update system changes required — this is a bridge-internal refactor. No new dependencies, no new config files, no migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The classifier and coach are called by `agent/job_queue.py` (lines 1005-1061), and the external interface (`classify_output()` returns `ClassificationResult`, `build_coaching_message()` returns `str`) stays the same. The only change is that `ClassificationResult` gains a `coaching_message` field.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` to reflect the merged architecture
- [ ] Update `docs/features/README.md` index if the coaching loop entry needs revision
- [ ] Code comments on the combined prompt explaining the coaching message contract

## Success Criteria

- [ ] Single LLM call produces both classification and coaching message
- [ ] `was_rejected_completion` flag and hedging pattern matching removed from `_parse_classification_response()`
- [ ] `build_coaching_message()` uses LLM-generated coaching for rejection cases
- [ ] Heuristic fallback path still works (no LLM → heuristics + static templates)
- [ ] Existing tests updated and passing (`pytest tests/test_summarizer.py tests/test_coach.py tests/test_auto_continue.py`)
- [ ] New test: classifier returns specific coaching for hedging language (not a static template)
- [ ] New test: classifier returns specific coaching for missing evidence (not a static template)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (merge-classifier-coach)**
  - Name: classifier-coach-builder
  - Role: Merge the classifier prompt, extend ClassificationResult, update coach fallback logic
  - Agent Type: builder
  - Resume: true

- **Validator (merge-classifier-coach)**
  - Name: classifier-coach-validator
  - Role: Verify classification+coaching works end-to-end, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update coaching-loop docs to reflect merged architecture
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extend ClassificationResult and update classifier prompt
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: classifier-coach-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `coaching_message: str | None = None` field to `ClassificationResult` dataclass
- Expand `CLASSIFIER_SYSTEM_PROMPT` to instruct the LLM to return a `coaching_message` field when classifying as `status` (especially when downgrading a completion)
- Include 2-3 few-shot examples in the prompt showing good coaching messages for hedging, missing evidence, and missing test output
- Update `_parse_classification_response()` to extract `coaching_message` from the JSON response
- Remove the `was_rejected_completion` hedging pattern matching block (lines 418-432)
- Update `_classify_with_heuristics()` to return `coaching_message=None` on all paths

### 2. Update coach to use LLM-generated coaching
- **Task ID**: build-coach
- **Depends On**: build-classifier
- **Assigned To**: classifier-coach-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `build_coaching_message()` to check `classification.coaching_message` first
- If `coaching_message` is present and non-empty, prefix with `[System Coach]` and return it
- If not, fall through to existing Tier 2/3 logic (skill-aware coaching, plain continue)
- Remove `was_rejected_completion` check (Tier 1) since LLM coaching replaces it
- Keep `_build_rejection_coaching()` as fallback for heuristic classifier path (rename to clarify it's the static fallback)

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-coach
- **Assigned To**: classifier-coach-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/test_summarizer.py`: remove tests for `was_rejected_completion` hedging detection, add tests for `coaching_message` field in parsed responses
- Update `tests/test_coach.py`: update rejection coaching tests to use `coaching_message` field, verify fallback to static template when `coaching_message` is None
- Add new tests: LLM returns specific coaching for hedging vs missing evidence (mock the API response with different coaching messages)
- Run `pytest tests/test_summarizer.py tests/test_coach.py tests/test_auto_continue.py` and fix any failures

### 4. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: classifier-coach-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `was_rejected_completion` and hedging patterns are fully removed
- Verify `ClassificationResult` has `coaching_message` field
- Verify `build_coaching_message()` uses LLM coaching when available
- Verify heuristic fallback path still works
- Run full test suite: `pytest tests/`
- Check no references to old hedging patterns remain

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/coaching-loop.md` to describe the merged architecture
- Update `docs/features/README.md` index entry if needed

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: classifier-coach-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/test_summarizer.py -v` — classifier tests pass
- `pytest tests/test_coach.py -v` — coach tests pass
- `pytest tests/test_auto_continue.py -v` — auto-continue integration tests pass
- `pytest tests/ -v` — full test suite
- `ruff check bridge/summarizer.py bridge/coach.py` — lint clean
- `black --check bridge/summarizer.py bridge/coach.py` — format clean
- `grep -r "was_rejected_completion" bridge/` — should only appear in ClassificationResult dataclass (field preserved for backward compat) or be fully removed
- `grep -r "hedg.*pattern\|hedging_patterns" bridge/` — no hedging pattern matching remains
