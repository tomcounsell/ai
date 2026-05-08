---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-05-08
tracking: https://github.com/tomcounsell/ai/issues/1318
last_comment_id: none
---

# Terminus Classifier: Imperative Fast-Path + Few-Shot Examples

## Problem

The terminus classifier (`classify_conversation_terminus` in `bridge/routing.py:556`) guards against bot reply loops in Telegram. When a human reply to a prior Valor message contains an explicit action directive ("Continue to finish all stage of SDLC", "Go ahead and merge"), the Ollama-backed zero-shot prompt misclassifies it as `SILENT`. The message is dropped with no reply and no indication to the human that anything went wrong.

**Current behavior:**

Tom replied to Valor's status message with:
```
I left a comment on PR 1316

Continue to finish all stage of SDLC
```

The fast-paths fired correctly (the prior Valor message contained `?`, so `valor_asked_question=True` bypassed Fast-Path 2). The message reached the Ollama LLM. Ollama returned `SILENT`. The message was dropped. Tom re-sent follow-ups; all were dropped. No session was spawned for over 2 hours.

A second SILENT cluster on 2026-05-06 05:03-05:13 UTC shows the same pattern: multiple successive human messages with continuation intent dropped by the LLM.

**Desired outcome:**

Explicit action imperatives — "continue", "run", "merge", "proceed", "fix", "retry", "deploy", "go ahead", "ship it" — are classified as `RESPOND` before any LLM call via a new Fast-Path 0. The LLM prompt also receives few-shot examples so ambiguous directives that don't match the fast-path still classify correctly. False-negative rate for human action directives drops to zero.

## Freshness Check

**Baseline commit:** `75d6cdb6a6309c6ee14b10cb0d0497bc33cf7efa`
**Issue filed at:** 2026-05-07T04:46:03Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/routing.py:556` — `classify_conversation_terminus` function definition — still holds at line 556
- `bridge/routing.py:587-611` — fast-path checks 1-3 — still holds at lines 587-611
- `bridge/routing.py:599-607` — `valor_asked_question` guard (Fast-Path 2 skip) — still holds
- `bridge/routing.py:613-631` — zero-shot LLM prompt — still holds, no few-shot examples

**Cited sibling issues/PRs re-checked:**
- #911 — closed/merged via PR #969 (2026-04-14) — initial terminus detection; prior art, not blocked
- #1090 — closed/merged via PR #1108 (2026-04-22) — question-aware Fast-Path 2; ships correctly, confirmed above

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None (searched all active plans; none touch `bridge/routing.py` terminus classifier)

**Notes:** The code is exactly as the issue describes. The zero-shot LLM prompt at lines 613-631 has no examples. The fast-paths are unchanged. No drift.

## Prior Art

- **PR #969** (fix(bridge): conversation terminus detection to break bot reply loops) — Initial RESPOND/REACT/SILENT three-state classifier. Introduced the zero-shot Ollama prompt that is now the root cause. Succeeded for bot loop suppression; did not address human action directives.
- **PR #1108** (fix(#1090): question-aware guard in terminus Fast-Path 2) — Added `valor_asked_question` check so a short human reply to a Valor question reaches the LLM instead of being silenced by Fast-Path 2. This fired correctly in the May 7 incident — the message DID reach the LLM. The LLM is the failure point, not the fast-paths.

## Research

No relevant external findings — this is a purely internal prompt-engineering and fast-path fix. No external libraries or APIs involved.

## Data Flow

1. **Entry point**: Telegram message arrives as reply to a prior Valor message (`replied_msg.out == True`)
2. **`should_respond_async()` in `bridge/routing.py:1073`**: Calls `classify_conversation_terminus(text, thread_messages, sender_is_bot)`
3. **Fast-Path 0 (new)**: Pre-LLM regex check for imperative verbs at start of message — returns `RESPOND` immediately if match found; no network call
4. **Fast-Paths 1-3 (existing)**: Bot sender check, acknowledgment token check, standalone `?` check
5. **LLM classifier (Ollama then Haiku)**: Called only if no fast-path fired; receives enriched few-shot prompt
6. **Decision returned**: `RESPOND` / `REACT` / `SILENT` back to `should_respond_async()`
7. **Session spawn or drop**: `RESPOND` spawns/resumes session; `REACT` sets emoji reaction; `SILENT` discards message

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #969 | Introduced zero-shot terminus classification | Zero-shot prompt too ambiguous for `gemma4:e2b` to distinguish continuation imperatives from conversation closers. No examples provided. |
| PR #1108 | Added `valor_asked_question` guard | Fixed Fast-Path 2 over-silencing for YES/NO answers to Valor questions. Left the LLM prompt unchanged; imperative directives that aren't one-word still reach the LLM and are misclassified. |

**Root cause pattern:** Each fix addressed the most recently observed symptom without hardening the LLM classification for the next failure mode. A fast-path for imperative verbs would have prevented both the May 6 and May 7 incidents at zero cost.

## Architectural Impact

- **New dependencies**: None. Fast-Path 0 uses Python `re` (already imported in `routing.py`).
- **Interface changes**: `classify_conversation_terminus` signature unchanged. New constant `_IMPERATIVE_VERB_RE` added to module scope.
- **Coupling**: No change. Bridge's internal routing logic only.
- **Data ownership**: No change.
- **Reversibility**: High. Both changes (regex + prompt) are self-contained within one function. Revert is a one-file patch.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Fast-Path 0 (`_IMPERATIVE_VERB_RE`)**: A new module-level compiled regex that matches messages beginning with or prominently containing known action imperatives. Returns `RESPOND` before any LLM call. Zero latency cost.
- **Few-shot LLM prompt**: Replace the current zero-shot `classify_conversation_terminus` prompt with a few-shot prompt that includes labeled examples drawn from real misclassified messages. Reduces false-negative rate for imperatives that don't hit Fast-Path 0.
- **DEBUG logging for classified text**: Log `(classified: {result}, text[:80})` at DEBUG level so future misclassifications surface in log tails.

### Flow

Human action directive arrives → **Fast-Path 0 matches imperative verb** → `RESPOND` (no LLM call)

Ambiguous continuation ("proceed on the other one") → Fast-Paths 1-3 don't fire → **Few-shot LLM prompt** → `RESPOND`

Pure acknowledgment ("ok great") → Fast-Path 2 fires → `SILENT` (unchanged)

### Technical Approach

**Fast-Path 0 placement**: Insert between the empty-text guard (line 581) and Fast-Path 1 (line 587). Fast-Path 0 must fire for human senders only — bot senders are handled entirely by Fast-Path 1. Add a `sender_is_bot` guard so the imperative check never interferes with bot loop suppression.

**Imperative verbs to cover (initial set)**:
```
continue, run, merge, proceed, retry, fix, start, deploy, execute,
go ahead, do it, ship it, push, try again, finish, complete, resume,
keep going, move on, send it, do this, handle it
```

Match strategy: `re.compile(r'^\s*(?:' + '|'.join(verbs) + r')\b', re.IGNORECASE)`. Leading-word match (anchored to start) avoids false positives in longer messages where an imperative appears mid-sentence in non-directive context.

**Few-shot examples** (add directly before the `Instructions:` block in the prompt):

```
Examples:
"Continue to finish all stage of SDLC" → RESPOND
"Go ahead and merge" → RESPOND  
"Run it again" → RESPOND
"Proceed with the plan" → RESPOND
"I left a comment on PR 1316\n\nContinue to finish all stage of SDLC" → RESPOND
"ok great" → REACT
"sounds good" → REACT
"👍" → SILENT
"thanks" → SILENT
"got it" → SILENT
```

Mine 2-3 additional examples from bridge logs (SILENT decisions with non-bot sender, multi-line messages containing action verbs).

**DEBUG log**: After `result` is determined (line 674), add:
```python
logger.debug(f"terminus: {result!r} — {text[:80]!r}")
```

## Failure Path Test Strategy

### Exception Handling Coverage
- Fast-Path 0 uses only `re.search()` — no exceptions possible. No new exception handlers introduced.
- LLM paths already have `except Exception` blocks at lines 648 and 668 — both log at DEBUG and fall through to conservative RESPOND default. These are unchanged.

### Empty/Invalid Input Handling
- Empty text: existing guard at line 581 returns `RESPOND` before Fast-Path 0 fires. Unchanged.
- None text: same guard handles it. Unchanged.
- Single imperative word ("continue"): Fast-Path 0 will match. Test that `classify_conversation_terminus("continue", [], sender_is_bot=False)` returns `RESPOND`.

### Error State Rendering
- This classifier runs before session spawn. If it fails (conservative default), the human's message is still handled — it spawns a session. No user-visible error output is changed by this fix.

## Test Impact

- [ ] `tests/unit/test_routing.py` — UPDATE: Add 7 new test cases for Fast-Path 0 imperative detection (see Success Criteria). Existing tests are purely additive — no existing test assertions change since Fast-Paths 1-3 are unmodified.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_acknowledgment_token_returns_silent` — VERIFY (no change needed): "got it" still returns SILENT. Regression guard.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_human_short_reply_no_question_still_silent` — VERIFY (no change needed): "Yes" with non-question thread still returns SILENT.

## Rabbit Holes

- **Swapping Ollama for Haiku as primary classifier**: Adds latency and cost for every reply-to-Valor event. Few-shot + Fast-Path 0 addresses the root cause at zero marginal cost. Out of scope.
- **Building a labeled training dataset / fine-tuned model**: Disproportionate effort for a bug that a 10-line regex and 10 examples fixes.
- **Widening `thread_messages` to include older Valor context**: Noted in the #1090 comment at line 598 as a future revisit. Do not touch — separate concern, different risk surface.
- **Adding RESPOND fast-path for messages with PR/issue number references**: "I left a comment on PR 1316" alone isn't imperative — the imperative was the second line. Don't build a PR-reference heuristic; the few-shot examples handle the combined-message case.

## Risks

### Risk 1: Fast-Path 0 over-fires on human messages that mention imperatives passively
**Impact:** A message like "I wish you would just continue this automatically" would incorrectly return `RESPOND`, spawning a session.
**Mitigation:** Anchoring the regex to the start of the message (`^\s*`) means only messages that *lead* with an imperative match. The example above starts with "I wish", not an imperative — it won't match. Test confirms this.

### Risk 2: Few-shot examples make the LLM prompt token count larger
**Impact:** ~200 extra tokens per LLM call for messages that reach the LLM. Ollama local model; no cost, negligible latency.
**Mitigation:** Accept. Issue explicitly permits ~200-token prompt growth.

## Race Conditions

No race conditions identified — `classify_conversation_terminus` is a pure async function with no shared mutable state. All operations are either regex matches or sequential LLM API calls. No concurrent state modifications.

## No-Gos (Out of Scope)

- Training a custom classifier model
- Swapping Ollama for a cloud model as primary classifier
- Expanding `thread_messages` to include older Valor context
- Handling PR/issue reference heuristics
- Changes to the REACT or bot-sender logic

## Update System

No update system changes required — this is a purely internal bridge routing change. No new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. `classify_conversation_terminus` is called only within `bridge/routing.py`; no CLI entry point or MCP server exposure is needed.

## Documentation

- [ ] Update `docs/features/agent-reply-terminus.md` — add Fast-Path 0 as item `0.` in the "Fast-Path Priority Order" numbered list (human sender only; renumber existing items 1-3 to 2-4)
- [ ] Update `docs/features/agent-reply-terminus.md` — add a subsection under "Fast-Path Priority Order" describing the `_IMPERATIVE_VERB_RE` regex pattern, the covered verb set, and the leading-word anchor strategy
- [ ] Update `docs/features/agent-reply-terminus.md` — document the few-shot prompt addition: explain why zero-shot failed for `gemma4:e2b`, show the labeled examples format, and note that examples are drawn from real misclassified messages
- [ ] Update `docs/features/agent-reply-terminus.md` — document the DEBUG log (`terminus: {result!r} — {text[:80]!r}`) and how to use it to identify future misclassifications for new few-shot examples

## Success Criteria

- [ ] `classify_conversation_terminus("Continue to finish all stage of SDLC", [], sender_is_bot=False)` returns `RESPOND` (unit test, Fast-Path 0)
- [ ] `classify_conversation_terminus("Go ahead and merge", [], sender_is_bot=False)` returns `RESPOND` (unit test, Fast-Path 0)
- [ ] `classify_conversation_terminus("Run it again", [], sender_is_bot=False)` returns `RESPOND` (unit test, Fast-Path 0)
- [ ] `classify_conversation_terminus("Proceed with the plan", [], sender_is_bot=False)` returns `RESPOND` (unit test, Fast-Path 0)
- [ ] `classify_conversation_terminus("continue", [], sender_is_bot=False)` returns `RESPOND` (unit test, single-word imperative)
- [ ] `classify_conversation_terminus("ok great", [], sender_is_bot=False)` returns `SILENT` (regression guard, acknowledgment token unchanged)
- [ ] `classify_conversation_terminus("thanks", [], sender_is_bot=False)` returns `SILENT` (regression guard)
- [ ] `classify_conversation_terminus("Continue with deployment", [], sender_is_bot=True)` returns `SILENT` (Fast-Path 1 still fires first for bots)
- [ ] At least 5 real dropped-message examples from chat history included as labeled few-shot examples in the LLM prompt
- [ ] `docs/features/agent-reply-terminus.md` updated
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (terminus-fast-path)**
  - Name: terminus-builder
  - Role: Implement Fast-Path 0 regex, few-shot prompt update, and DEBUG log in `bridge/routing.py`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (terminus-tests)**
  - Name: terminus-test-engineer
  - Role: Write unit tests for all new fast-path cases and regression guards
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (terminus-docs)**
  - Name: terminus-documentarian
  - Role: Update `docs/features/agent-reply-terminus.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator (terminus-validation)**
  - Name: terminus-validator
  - Role: Run test suite and verify all success criteria pass
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See PLAN_TEMPLATE.md for full list.

## Step by Step Tasks

### 1. Implement Fast-Path 0 and Few-Shot Prompt
- **Task ID**: build-fast-path
- **Depends On**: none
- **Validates**: `tests/unit/test_routing.py`
- **Assigned To**: terminus-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_IMPERATIVE_VERB_RE` module-level compiled regex at `bridge/routing.py` near line 553 (alongside `_STANDALONE_QUESTION_RE`)
- Insert Fast-Path 0 check immediately after the empty-text guard (line 582), before Fast-Path 1 (line 587): `if not sender_is_bot and _IMPERATIVE_VERB_RE.search(text_stripped): return "RESPOND"`
- Replace the zero-shot LLM prompt (lines 615-631) with a few-shot prompt including at least 5 labeled examples mined from bridge logs
- Add `logger.debug(f"terminus: {result!r} — {text[:80]!r}")` after result is determined (before the REACT-collapse block at line 674)
- Run `python -m ruff format bridge/routing.py && python -m ruff check bridge/routing.py`

### 2. Write Unit Tests
- **Task ID**: build-tests
- **Depends On**: build-fast-path
- **Validates**: `tests/unit/test_routing.py`
- **Assigned To**: terminus-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add test group `# Fast-Path 0: imperative verb tests` in `tests/unit/test_routing.py` after the existing Fast-Path tests
- Write 8 tests covering all Success Criteria above (5 RESPOND imperatives, 2 SILENT regression guards, 1 bot-sender SILENT guard for imperatives)
- Run `pytest tests/unit/test_routing.py -v` to confirm all pass

### 3. Update Documentation
- **Task ID**: document-terminus
- **Depends On**: build-tests
- **Assigned To**: terminus-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-reply-terminus.md`: add Fast-Path 0 to the "Fast-Path Priority Order" section (insert as new item 1, renumber existing 1-3 to 2-4); add subsection describing few-shot examples; document DEBUG log
- Update the "Fast-Path Priority Order" table: add row `0. Imperative verb (human sender only) → RESPOND`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-terminus
- **Assigned To**: terminus-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_routing.py -v` — all tests must pass
- Run `python -m ruff check bridge/routing.py docs/features/agent-reply-terminus.md` — lint clean
- Verify `docs/features/agent-reply-terminus.md` documents Fast-Path 0, few-shot strategy, and DEBUG log
- Report pass/fail status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_routing.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/routing.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/routing.py` | exit code 0 |
| Fast-Path 0 present | `grep -n "_IMPERATIVE_VERB_RE" bridge/routing.py` | output > 0 |
| Few-shot examples in prompt | `grep -c "RESPOND" bridge/routing.py` | output > 5 |
| Debug log added | `grep -n "terminus:" bridge/routing.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — this is a self-contained fix with clear scope, verified freshness, and no ambiguous design decisions.
