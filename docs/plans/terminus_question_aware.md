---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-22
tracking: https://github.com/tomcounsell/ai/issues/1090
last_comment_id:
---

# Terminus Question-Aware Short-Reply Fix

## Problem

When Valor asks a question mid-session — e.g. "Should I select the Yudame workspace?" — and the human replies with a short answer like "Yes", the bridge drops the message with `terminus=SILENT` before any steering or session-resume logic is reached. The session stalls indefinitely, waiting for an answer that was never received.

**Real incident:** 2026-04-21 04:30:53 UTC, session `tg_cuttlefish_-5295380350_9046`. Valor sent msg 9047 ("Should I select the Yudame workspace (`tea-cldfmjeg1b2c73f6rrug`)?"). Tom replied "Yes" (1 word). Bridge logged `Reply to Valor: terminus=SILENT, not responding` and discarded the message. The session had already completed, so it was never re-enqueued for resume either.

**Current behavior:** Any reply of ≤1 word to a Valor message returns `SILENT` unconditionally — including human answers to questions Valor explicitly asked.

**Desired outcome:** When the Valor message being replied to contains a question mark, a short human reply ("Yes" / "No" / "Ok" / etc.) returns `RESPOND` so it can be routed to the steering queue or session-resume path. Bot reply-loop suppression is preserved unchanged.

## Freshness Check

**Baseline commit:** `94ae0a9a5883c1edb3ce5b70d2fc668eff25090b` (main HEAD at plan time)
**Issue filed at:** 2026-04-21T05:04:18Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/routing.py:554` — issue claims the ≤1-word guard fires on humans → still holds. Code: `if token_normalized in _ACKNOWLEDGMENT_TOKENS or word_count <= 1: return "SILENT"`.
- `bridge/routing.py:519` — `classify_conversation_terminus()` signature → still holds.
- `bridge/routing.py:1021-1025` — call site passes `thread_messages=[replied_msg.message or ""]` → still holds.
- `bridge/routing.py:516` — `_STANDALONE_QUESTION_RE` regex → still holds, available for reuse.

**Cited sibling issues/PRs re-checked:**
- #911 (origin issue) — closed 2026-04-14T19:31:03Z, resolved by PR #969. Provides direct context for why Fast-Path 2 exists. Fix in this plan augments rather than replaces that work.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-21T05:04:18Z" -- bridge/routing.py tests/unit/test_routing.py` returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** None. No other plan touches `bridge/routing.py` or terminus detection.

**Notes:** All issue claims verified verbatim against current code. No drift.

## Prior Art

- **PR #969** (merged 2026-04-14): "fix(bridge): conversation terminus detection to break bot reply loops" — introduced `classify_conversation_terminus()` with the three fast-paths (bot+no-question → SILENT; acknowledgment token or ≤1 word → SILENT; standalone `?` → RESPOND) and the LLM fallback. Closed #911. Solved bot reply loops correctly but under-scoped Fast-Path 2: assumed all 1-word replies were either bot loops or human conversation closers, not anticipating that a human might give a 1-word ("Yes"/"No") *answer* to a question Valor explicitly asked. The current bug is the gap left by that fix.
- **Issue #911**: Original RESPOND/REACT/SILENT spec. Did not call out the human-short-answer-to-Valor-question case explicitly, which is why PR #969 missed it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #969 | Added Fast-Path 2 (`token in _ACKNOWLEDGMENT_TOKENS or word_count <= 1 → SILENT`) at `bridge/routing.py:551-555` to catch short bot acknowledgments and short human "thanks"/"got it" closers. | The check correctly fires after the bot-sender check, but it does not consult `thread_messages` for context. When Valor's prior message in the thread was a question, a 1-word *answer* from a human is functionally equivalent to a continuation — yet Fast-Path 2 silences it identically to a thread closer. The replied-to message text is already passed in as `thread_messages`, but Fast-Path 2 ignored that argument entirely. |

**Root cause pattern:** Fast-Path 2 conflated "short reply" with "thread closer" without inspecting the conversational context. The fix in this plan teaches Fast-Path 2 to look at one piece of context that is already in scope — whether the replied-to message was a question.

## Architectural Impact

- **New dependencies:** None. Uses an already-existing module-level regex (`_STANDALONE_QUESTION_RE`).
- **Interface changes:** None. `classify_conversation_terminus()` signature, return values, and call site are unchanged.
- **Coupling:** Slightly *decreases* coupling between Fast-Path 2 and the LLM fallback by giving Fast-Path 2 access to a context signal it should always have considered.
- **Data ownership:** Unchanged. The `thread_messages` argument is already populated by `should_respond_async()`.
- **Reversibility:** Trivial — the fix is a 3-line `if not <condition>:` wrapper. Reverting is one Edit.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully captured in the issue and plan; no ambiguity remains)
- Review rounds: 1 (standard `/do-pr-review` after build)

Solo dev work, ~30 minutes of code time. Bottleneck is review and CI, not implementation.

## Prerequisites

No prerequisites — this work has no external dependencies, no new env vars, no new secrets.

## Solution

### Key Elements

- **Question-aware guard in Fast-Path 2**: Before the existing `if token_normalized in _ACKNOWLEDGMENT_TOKENS or word_count <= 1: return "SILENT"` check, add a pre-check: if any message in `thread_messages` contains a standalone `?`, skip Fast-Path 2 entirely (let the message fall through to Fast-Path 3 / LLM / RESPOND default).
- **Reuse of `_STANDALONE_QUESTION_RE`**: Use the existing module-level regex for question detection, not a literal `"?" in msg` substring check. This keeps the URL-query-param exclusion (e.g., `https://example.com?q=1` is not a question) consistent across all three fast-paths.
- **Bot-loop suppression preserved**: The change lives only in Fast-Path 2. Fast-Path 1 (`sender_is_bot and not _STANDALONE_QUESTION_RE.search(text_stripped) → SILENT`) fires *before* Fast-Path 2 and is untouched, so a bot replying "yes" to a Valor question still returns SILENT via Fast-Path 1.

### Flow

Bridge receives reply-to-Valor msg → `should_respond_async()` → `classify_conversation_terminus(text, thread_messages=[replied_valor_msg], sender_is_bot)` → 

1. Fast-Path 1 (bot + no question in *reply text*) → SILENT (unchanged)
2. **NEW**: check `_STANDALONE_QUESTION_RE.search(any thread_message)` → if true, skip Fast-Path 2
3. Fast-Path 2 (token/word-count) → SILENT (only if step 2 didn't skip)
4. Fast-Path 3 (standalone `?` in reply text) → RESPOND (unchanged)
5. LLM fallback (unchanged)

Result for the bug: human "Yes" reply to "Should I select the Yudame workspace?" → step 2 detects `?` in the Valor message → step 3 is skipped → step 4 doesn't fire (no `?` in "Yes") → falls through to LLM → LLM returns RESPOND for an answer to a question → bridge routes to steering/session-resume. (Even if the LLM is offline, the conservative default of RESPOND still solves the bug.)

### Technical Approach

Single-file change in `bridge/routing.py`, function `classify_conversation_terminus()`, between current lines 551 and 555:

```python
# Fast-path 2: acknowledgment token or very short — but skip if the
# replied-to context contained a question (human answering Valor).
# Fast-Path 1 above already handled the bot-sender case.
valor_asked_question = any(
    _STANDALONE_QUESTION_RE.search(msg) for msg in thread_messages if msg
)
if not valor_asked_question:
    token_normalized = text_lower.rstrip("!.,").strip()
    word_count = len(text_stripped.split())
    if token_normalized in _ACKNOWLEDGMENT_TOKENS or word_count <= 1:
        return "SILENT"
```

Notes:
- The `if msg` filter inside the generator avoids `re.search(None)` if `thread_messages` ever contains `None` defensively. Current callers pass strings, but the cost of the guard is zero and it future-proofs the call site.
- `token_normalized` and `word_count` move *inside* the `if not valor_asked_question:` block since they are only consumed there. (Functional equivalent if left at module scope; this is a micro-clarity choice. Builder may keep them outside if they prefer to minimize the diff.)
- Fast-Path 1 (bot + no question in *reply text*) is unchanged and continues to fire first. A bot replying "yes" still gets caught by Fast-Path 1 before Fast-Path 2 is reached.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except` blocks are introduced. The function already has try/except around Ollama and Haiku calls (lines ~585+). Those are unchanged.
- [ ] If `_STANDALONE_QUESTION_RE.search()` were to raise (it won't on a string input), the exception would propagate up — but `re.search` on a string is total. No new silent failure surface.

### Empty/Invalid Input Handling
- [ ] Empty `thread_messages` list: `any(...)` over empty iterable returns `False` → `valor_asked_question = False` → original Fast-Path 2 behavior preserved. (Test case: `test_classify_terminus_acknowledgment_token_returns_silent` already covers this and must continue to pass.)
- [ ] `thread_messages` containing empty string: `if msg` filter skips it → same as empty list above.
- [ ] `thread_messages` containing `None` (defensive): `if msg` filter skips it → no `re.search(None)` crash.
- [ ] Empty reply `text`: handled by the existing guard at line 541-542, returns RESPOND. Unchanged.

### Error State Rendering
- [ ] No user-visible output. The function returns a string consumed by `should_respond_async()`. Bridge logs the terminus result at line 1038. No change to logging.

## Test Impact

- [ ] `tests/unit/test_routing.py::test_classify_terminus_human_short_reply_to_valor_question_returns_respond` — ADD: new test verifying that `text="Yes"`, `thread_messages=["Should I select the Yudame workspace?"]`, `sender_is_bot=False` returns `"RESPOND"` (not `"SILENT"`).
- [ ] `tests/unit/test_routing.py::test_classify_terminus_human_short_reply_no_question_still_silent` — ADD: regression test verifying that `text="Yes"`, `thread_messages=["Here is the report you asked for."]`, `sender_is_bot=False` returns `"SILENT"` (existing Fast-Path 2 behavior preserved).
- [ ] `tests/unit/test_routing.py::test_classify_terminus_bot_short_reply_to_valor_question_still_silent` — ADD: regression test verifying that `text="Yes"`, `thread_messages=["Should I do X?"]`, `sender_is_bot=True` returns `"SILENT"` via Fast-Path 1 (the bot loop break must still fire even when Valor asked a question — bots don't answer questions, they loop).
- [ ] `tests/unit/test_routing.py::test_classify_terminus_url_query_in_thread_not_treated_as_question` — ADD: edge case verifying that `text="Yes"`, `thread_messages=["See https://example.com?q=1"]`, `sender_is_bot=False` returns `"SILENT"` (URL query string `?q=1` must not count as a question, so Fast-Path 2 still fires).
- [ ] `tests/unit/test_routing.py::test_classify_terminus_acknowledgment_token_returns_silent` (line ~113) — UNCHANGED: must continue to pass. Calls with `thread_messages=[]` so the new guard is a no-op.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_acknowledgment_fires_after_bot_check` (line ~124) — UNCHANGED: must continue to pass. Bot sender, `thread_messages=[]`, fires via Fast-Path 1.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_bot_no_question_returns_silent` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_human_question_returns_respond` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_url_with_query_param_not_respond` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_ollama_failure_defaults_to_respond` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_empty_text_returns_respond` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_routing.py::test_classify_terminus_bot_react_collapses_to_silent` — UNCHANGED: must continue to pass.
- [ ] `tests/unit/test_config_driven_routing.py` (terminus mock at line 397) — UNCHANGED: mocks the classifier entirely so it is unaffected.

## Rabbit Holes

- **Do NOT rewrite the LLM fallback prompt.** It already handles "answer to a question" correctly when invoked. The bug is purely that Fast-Path 2 short-circuits before the LLM is consulted.
- **Do NOT change the `_ACKNOWLEDGMENT_TOKENS` set.** Adding/removing tokens is a separate scope and would risk regressions in classification of true thread closers.
- **Do NOT plumb richer context into `thread_messages`.** The call site currently passes only the immediate replied-to message, not full thread history. Expanding that is a separate, larger change with its own risks (token budget, LLM context size). The current single-message context is sufficient to fix this bug.
- **Do NOT add a new fast-path layer.** The fix belongs *inside* Fast-Path 2 as a guard, not as a separate Fast-Path 1.5. Adding new layers makes the priority order harder to reason about.
- **Do NOT introduce sender-history lookups.** Tempting to also check "did this human reply quickly?" or "did Valor recently ask a question elsewhere?" — out of scope. The `?` in `thread_messages` signal is sufficient and self-contained.

## Risks

### Risk 1: Question mark in non-question Valor messages

**Impact:** A Valor message like "I tried `git status?` and got nothing back." contains a `?` but is not a question to the user. A 1-word reply ("Yes") would now route to RESPOND instead of being silenced. This produces a slightly noisier bridge but does not lose information — RESPOND just means the bridge processes the reply normally.

**Mitigation:** This is the desired conservative behavior. The classifier's design principle (line 538) is "Conservative default: any classifier error → RESPOND." Treating ambiguous `?` content as RESPOND aligns with that. Worst case is a small uptick in messages routed to the agent, which is preferable to silently dropping legitimate human answers.

### Risk 2: Multi-message `thread_messages` widening (hypothetical)

**Impact:** Today `thread_messages` carries one message. If a future change widens it to include older Valor messages, the `?` heuristic could fire on a stale question and route a "Yes" to a *different* unrelated thread. This is a future-coupling risk, not a present bug.

**Mitigation:** Document the assumption inline ("the replied-to message is the relevant context"). Anyone widening `thread_messages` will need to revisit this guard. Add a code comment pointing to issue #1090 so the rationale is discoverable.

### Risk 3: Bot replying "Yes" to a question

**Impact:** If another bot replied "Yes" to a Valor question, Fast-Path 2 with the new guard would NOT silence it — but Fast-Path 1 fires first (`sender_is_bot and not _STANDALONE_QUESTION_RE.search(text_stripped)`) and would send it to SILENT because "Yes" has no `?`. Verified by the new `test_classify_terminus_bot_short_reply_to_valor_question_still_silent` test case.

**Mitigation:** Test coverage as listed in Test Impact. No code change beyond the guard is needed; the fast-path priority order already protects this case.

## Race Conditions

No race conditions identified — `classify_conversation_terminus()` is async but does not share mutable state. `thread_messages` is constructed per-call by the caller. The `_STANDALONE_QUESTION_RE` regex is a module-level immutable `re.Pattern` (compiled once, thread-safe by Python's regex semantics). No file I/O, no Redis writes, no shared globals touched.

## No-Gos (Out of Scope)

- Widening `thread_messages` to include richer thread context (more than the immediate replied-to message). Separate, larger work item.
- Refactoring the fast-path priority order or splitting Fast-Path 2 into multiple paths.
- Modifying `_ACKNOWLEDGMENT_TOKENS` set membership.
- Changing the LLM fallback prompt or model selection.
- Session-resume logic itself (issue #1090 is about getting the message *to* the session-resume path; what happens after RESPOND is already handled by existing code at `bridge/routing.py:1026-1028` and downstream).
- Reactions: the `REACT` path (line 1029-1037) is unchanged.

## Update System

No update system changes required — this is a single-file Python bug fix in `bridge/routing.py`. No new dependencies, no new env vars, no migration steps. Existing installations get the fix via standard `git pull && service restart`.

## Agent Integration

No agent integration required — this is a bridge-internal change to the routing layer (`bridge/routing.py`). The function is not exposed to the agent via MCP and is invoked only by `should_respond_async()` inside the bridge. The agent's tool surface is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-reply-terminus.md` Fast-Path 2 description (currently says "Fires after the bot check — never before — to avoid silencing human short replies"). Add a sub-bullet noting that Fast-Path 2 is also skipped when the replied-to message contains a question (issue #1090). Link the issue inline.
- [ ] Verify `docs/features/README.md` index entry for "Agent Reply Terminus Detection" is still accurate (it should be — title and scope are unchanged).

### External Documentation Site
This repo does not use Sphinx/ReadTheDocs/MkDocs. Skip.

### Inline Documentation
- [ ] Inline comment on the new guard explaining *why* it exists (1-line reference to issue #1090) — see Solution > Technical Approach for the proposed comment text.
- [ ] Update the docstring of `classify_conversation_terminus()` (lines 524-539) to add Fast-Path 2 to the fast-path order list with the question-aware caveat. Current docstring lists "2. acknowledgment token or very short (≤1 word) → SILENT" — append "(unless thread_messages contains a question — then fall through)".

## Success Criteria

- [ ] `classify_conversation_terminus(text="Yes", thread_messages=["Should I select the workspace?"], sender_is_bot=False)` returns `"RESPOND"` (covered by new test).
- [ ] `classify_conversation_terminus(text="Yes", thread_messages=["Here is the file."], sender_is_bot=False)` returns `"SILENT"` (covered by new test, existing behavior preserved).
- [ ] `classify_conversation_terminus(text="Yes", thread_messages=["Should I do X?"], sender_is_bot=True)` returns `"SILENT"` (covered by new test — bot loop break still wins via Fast-Path 1).
- [ ] All 7 existing terminus tests in `tests/unit/test_routing.py` continue to pass.
- [ ] `docs/features/agent-reply-terminus.md` updated to describe the question-aware guard.
- [ ] Tests pass (`/do-test` → `pytest tests/unit/test_routing.py -q`).
- [ ] Lint and format clean (`python -m ruff check bridge/routing.py tests/unit/test_routing.py` and `python -m ruff format bridge/routing.py tests/unit/test_routing.py`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (terminus-fix)**
  - Name: terminus-fix-builder
  - Role: Apply the question-aware guard in `classify_conversation_terminus()`, add the new tests, update the feature doc, update the docstring.
  - Agent Type: builder
  - Resume: true

- **Validator (terminus-fix)**
  - Name: terminus-fix-validator
  - Role: Run `pytest tests/unit/test_routing.py -v`, confirm all 11 tests pass (7 existing + 4 new). Run ruff. Diff the feature doc to confirm the Fast-Path 2 update is present and accurate. Report pass/fail.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tier-1 agents (`builder`, `validator`) are sufficient. No specialists required.

## Step by Step Tasks

### 1. Apply the question-aware guard

- **Task ID**: build-terminus-guard
- **Depends On**: none
- **Validates**: `tests/unit/test_routing.py` (existing tests must still pass; new tests added in next task)
- **Informed By**: Solution > Technical Approach in this plan
- **Assigned To**: terminus-fix-builder
- **Agent Type**: builder
- **Parallel**: false

Edit `bridge/routing.py`:
- Around lines 551-555, replace the current Fast-Path 2 block with the question-aware version from Solution > Technical Approach. Use `_STANDALONE_QUESTION_RE.search(msg)` (not literal `"?" in msg`).
- Add an inline comment referencing issue #1090.
- Update the `classify_conversation_terminus` docstring (lines 531-534) to note that Fast-Path 2 is skipped when `thread_messages` contains a question.
- Do NOT modify Fast-Path 1, Fast-Path 3, the LLM call, or any other function in the file.

### 2. Add the four new tests

- **Task ID**: build-terminus-tests
- **Depends On**: build-terminus-guard
- **Validates**: `tests/unit/test_routing.py` (all 11 tests pass, including 4 new)
- **Assigned To**: terminus-fix-builder
- **Agent Type**: builder
- **Parallel**: false

Edit `tests/unit/test_routing.py`. Append the four new tests immediately after the existing terminus test block (after line 193, before the next test section if any):

1. `test_classify_terminus_human_short_reply_to_valor_question_returns_respond` — text="Yes", thread_messages=["Should I select the Yudame workspace?"], sender_is_bot=False → "RESPOND"
2. `test_classify_terminus_human_short_reply_no_question_still_silent` — text="Yes", thread_messages=["Here is the report you asked for."], sender_is_bot=False → "SILENT"
3. `test_classify_terminus_bot_short_reply_to_valor_question_still_silent` — text="Yes", thread_messages=["Should I do X?"], sender_is_bot=True → "SILENT"
4. `test_classify_terminus_url_query_in_thread_not_treated_as_question` — text="Yes", thread_messages=["See https://example.com?q=1"], sender_is_bot=False → "SILENT"

For test 1 and 2, the function will fall through the fast-paths and into the LLM call. To keep tests deterministic and offline, mock both Ollama and Haiku to be unavailable (same pattern as `test_classify_terminus_ollama_failure_defaults_to_respond` at line 135) so the conservative default `"RESPOND"` is returned for test 1. For test 2 the function still hits SILENT at Fast-Path 2 (no LLM call needed). For test 3, Fast-Path 1 fires (no LLM call needed). For test 4, Fast-Path 2 fires (no LLM call needed).

### 3. Update feature documentation

- **Task ID**: build-terminus-docs
- **Depends On**: build-terminus-guard
- **Validates**: `docs/features/agent-reply-terminus.md` reflects the question-aware guard
- **Assigned To**: terminus-fix-builder
- **Agent Type**: builder
- **Parallel**: true (with build-terminus-tests)

Edit `docs/features/agent-reply-terminus.md`:
- Locate the "Fast-Path Priority Order" subsection (around line 50).
- Update the bullet for Fast-Path 2 to note: "Fires after the bot check (never before) AND only when `thread_messages` does not contain a question. If Valor's prior message in the thread contained a standalone `?` (per `_STANDALONE_QUESTION_RE`), the ≤1-word check is skipped so a human short answer like 'Yes' / 'No' falls through to the LLM (or RESPOND default). See [#1090](https://github.com/tomcounsell/ai/issues/1090)."
- Do not restructure the rest of the document.

### 4. Validation pass

- **Task ID**: validate-terminus-fix
- **Depends On**: build-terminus-guard, build-terminus-tests, build-terminus-docs
- **Assigned To**: terminus-fix-validator
- **Agent Type**: validator
- **Parallel**: false

- Run `pytest tests/unit/test_routing.py -v`. Confirm 11 tests pass (4 new + 7 existing).
- Run `python -m ruff check bridge/routing.py tests/unit/test_routing.py`. Exit code must be 0.
- Run `python -m ruff format --check bridge/routing.py tests/unit/test_routing.py`. Exit code must be 0.
- `grep -n "1090" docs/features/agent-reply-terminus.md` — must return at least one match.
- `grep -n "valor_asked_question\|thread_messages" bridge/routing.py` — must show the new guard inside `classify_conversation_terminus`.
- Report pass/fail with concrete evidence (test names that passed, ruff output).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Routing unit tests pass | `pytest tests/unit/test_routing.py -q` | exit code 0 |
| Lint clean (touched files) | `python -m ruff check bridge/routing.py tests/unit/test_routing.py` | exit code 0 |
| Format clean (touched files) | `python -m ruff format --check bridge/routing.py tests/unit/test_routing.py` | exit code 0 |
| Feature doc references issue | `grep -c "1090" docs/features/agent-reply-terminus.md` | output > 0 |
| New guard wired in | `grep -c "valor_asked_question" bridge/routing.py` | output > 0 |
| Full unit test suite still green | `pytest tests/unit/ -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

None. The issue's Solution Sketch is precise, the recon is fully verified against current code, the test surface is well-understood, and the only design choice (literal `"?" in msg` vs. `_STANDALONE_QUESTION_RE.search`) is resolved in favor of the regex for consistency with Fast-Paths 1 and 3.
