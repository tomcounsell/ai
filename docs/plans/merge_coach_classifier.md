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

- **Classification drives the decision, coaching is a secondary output**: Haiku's primary job is classifying into 5 types with a confidence score — same as today. The bridge applies the existing confidence threshold (0.80) to determine pass/fail. Coaching is just an extra field Haiku populates when it classifies as `status`. This prevents bias — Haiku isn't making a separate "should I coach?" judgment.
- **Confidence threshold remains the safety net**: The existing threshold logic stays. Below 0.80 confidence → default to QUESTION (pause for human). This is tunable without touching the prompt. If Haiku starts over-rejecting, adjust the threshold — don't rewrite prompt engineering.
- **Structured output with optional coaching**: JSON response adds a `coaching_message` field. Null on non-status types (the majority). Populated with specific guidance on status types. The bridge only reads `coaching_message` when it's already decided to auto-continue.
- **Heuristic fallback preserved**: The existing `_classify_with_heuristics()` stays as the offline fallback. When active, the static coach templates in `coach.py` provide coaching — no LLM coaching available on this path.

### Flow

**Agent output arrives** → `classify_output(text)` → single Haiku call → returns `ClassificationResult` with `type`, `confidence`, `reason`, `coaching_message`:

1. **Confidence check**: If confidence < 0.80 → default to QUESTION (pause for human). Same as today.
2. **Pass (~60-70%):** `type` is completion/question/blocker/error → `coaching_message` is null → output flows to summarizer → delivered to Telegram. Haiku didn't generate coaching because it wasn't asked to for non-status types.
3. **Fail (~30-40%):** `type` is status → bridge decides to auto-continue → `coaching_message` contains specific guidance → bridge uses it as the continuation prompt.

The pass/fail decision is made by the classification + threshold — not by whether coaching exists. Coaching is a consequence of the decision, not the driver.

### Technical Approach

1. **Extend `ClassificationResult`** — add `coaching_message: str | None` field. Null on pass-through types (completion, question, blocker, error). Populated on status/auto-continue.
2. **Expand classifier prompt** — add to `CLASSIFIER_SYSTEM_PROMPT`: "When classifying as `status`, also return a `coaching_message` explaining what the agent should do next or what evidence was missing. For all other types, set `coaching_message` to null." Haiku's primary task is still classification with confidence — coaching is a secondary output only on the status path.
3. **Update JSON schema** — response becomes `{"type": "...", "confidence": 0.95, "reason": "...", "coaching_message": "..."|null}`. The majority of responses will have `coaching_message: null` since most output is approved.
4. **Remove `was_rejected_completion` detection** — the hedging pattern matching in `_parse_classification_response()` (lines 418-432) is deleted. The LLM now explicitly returns coaching when it classifies as status — no need to reverse-engineer intent from the reason field.
5. **Simplify `build_coaching_message()`** — check if `classification.coaching_message` exists first; if so, prefix with `[System Coach]` and return. Fall through to existing skill/plan-aware coaching only when no LLM coaching was provided (heuristic fallback path).
6. **Pass context to classifier** — optionally pass plan file path and active skill info so the LLM can reference success criteria in its coaching when classifying as status.

## Rabbit Holes

- **Separate pass/fail score** — Don't ask Haiku for a second judgment axis (e.g. `"pass": true/false`). This creates conflicting signals ("type: completion, pass: false" — what does that mean?). The classification type + confidence threshold IS the pass/fail mechanism. One axis of judgment, not two.
- **Multi-turn coaching history** — Issue mentions seeing prior coaching messages. Defer this — it requires passing conversation history into the classifier, which is a separate concern. The single-pass merge is valuable on its own.
- **Replacing the heuristic fallback** — Keep `_classify_with_heuristics()` as-is. It's the offline safety net and doesn't need to generate coaching messages (the static templates in `coach.py` cover that path).
- **Changing the coach's skill-aware tiers** — Tier 2 (plan/skill coaching for status updates) works fine. Only Tier 1 (rejection coaching) needs to use LLM-generated messages. Don't refactor the whole coach.

## Risks

### Risk 1: Haiku develops bias toward rejecting (over-coaching)
**Impact:** If Haiku finds generating coaching messages "interesting," it may drift toward classifying more output as `status` to get the chance to coach, reducing pass-through rate.
**Mitigation:** Classification with confidence score is the decision mechanism — coaching is a secondary output. The confidence threshold (0.80) catches drift. If pass-through rate drops, tune the threshold up — no prompt changes needed. Monitor the status-vs-completion ratio in logs.

### Risk 2: LLM generates poor coaching messages
**Impact:** Agent gets unhelpful or confusing auto-continue messages
**Mitigation:** Include few-shot examples in the prompt. Keep the static template as fallback — if `coaching_message` is empty/missing, fall back to existing behavior.

### Risk 3: Increased token usage from longer prompt
**Impact:** Slightly higher cost per classification call
**Mitigation:** The prompt grows by ~200 tokens (coaching instructions + examples). At Haiku pricing this is negligible. The coaching message output adds ~50 tokens. We're saving the information loss that causes wasted auto-continue cycles, which cost far more.

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
