---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-03-07
tracking: https://github.com/valorengels/ai/issues/294
---

# Surface Plan Open Questions

## Problem

When the `/do-plan` skill completes and produces a plan document containing an `## Open Questions` section, the summarizer does not surface those questions to the user. The SDLC pipeline auto-continues past the plan stage without pausing for human input on design decisions.

**Current behavior:**
1. `/sdlc` runs the PLAN stage, which creates a plan with `## Open Questions`
2. The agent output mentions the open questions
3. The stage-aware auto-continue path sees "stages remaining" and auto-continues immediately
4. The summarizer's anti-fabrication rules filter out questions that aren't direct `?`-terminated sentences aimed at the user
5. Open questions go unanswered; BUILD proceeds with unresolved design decisions

**Desired outcome:**
When a plan contains open questions, the pipeline pauses for human input. The questions are surfaced in the Telegram summary so the human can answer them before BUILD begins.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on question detection heuristics)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Open question detector**: A heuristic function that scans agent output for `## Open Questions` sections and extracts questions from them
- **Stage-aware question gate**: A check in the stage-aware auto-continue path that pauses when open questions are detected, regardless of remaining stages
- **Summarizer expectations passthrough**: Ensures detected open questions flow into the `expectations` field of the structured summary

### Flow

Agent completes PLAN stage -> send_to_chat receives output -> open question detector scans output -> if questions found, deliver to user with expectations set -> session pauses for human input -> human answers -> SDLC resumes with BUILD

### Technical Approach

**Bug 1 fix (summarizer)**: Add a pre-processing step in `summarize_response()` that detects `## Open Questions` sections in the raw agent output. When found, extract the questions and inject them into the structured summary's `expectations` field. This bypasses the anti-fabrication filter because the questions are extracted verbatim from structured document sections, not fabricated by the LLM.

**Bug 2 fix (stage-aware auto-continue)**: In the stage-aware auto-continue block of `send_to_chat()` (job_queue.py ~line 1298), add a question detection check before deciding to auto-continue. If the output contains an `## Open Questions` section (or the summarizer sets `expectations`), route to deliver instead of auto-continue.

The approach is to add the detection at two levels for defense in depth:
1. **job_queue.py**: Before auto-continuing, check if the output contains `## Open Questions` -- if so, fall through to classifier/deliver path
2. **summarizer.py**: When summarizing, detect `## Open Questions` sections and populate the `expectations` field with the extracted questions

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_extract_open_questions()` function must handle malformed markdown gracefully (return empty list, not crash)
- [ ] If question extraction fails, the pipeline should continue normally (auto-continue as before) rather than crashing

### Empty/Invalid Input Handling
- [ ] Empty `## Open Questions` section (heading with no content below it) should not trigger a pause
- [ ] `## Open Questions` followed only by whitespace should not trigger a pause
- [ ] Questions that are just numbered placeholders like "1. TBD" should not trigger a pause

### Error State Rendering
- [ ] When open questions are surfaced, they must appear in the Telegram message with `?` prefix
- [ ] The `expectations` field must contain the verbatim questions so the session pauses correctly

## Rabbit Holes

- **Rewriting the classifier**: The issue mentions classifier hardening for Q&A completion flakiness. That's a separate concern (already tracked as flaky test). Don't fix the classifier in this PR.
- **NLP-based question detection**: Don't build sophisticated question detection beyond the `## Open Questions` section pattern. The structured markdown heading is a reliable signal; trying to detect implicit questions in prose is a rabbit hole.
- **Changing the anti-fabrication rules**: The summarizer's anti-fabrication constraints are correct and important. Don't weaken them. Instead, extract questions from structured sections and pass them through a separate channel.

## Risks

### Risk 1: False positives from `## Open Questions` in quoted/referenced content
**Impact:** Agent output that quotes a plan's open questions section (e.g., in a status report) could trigger an unwanted pause.
**Mitigation:** Only trigger when the output is from a PLAN stage (check session history for current stage being PLAN). Additionally, require the section to contain at least one substantive line (not just the heading).

### Risk 2: Over-aggressive pausing breaks SDLC flow
**Impact:** If the detection is too sensitive, the pipeline pauses unnecessarily on every plan stage output.
**Mitigation:** Only pause when the `## Open Questions` section actually contains question-like content (lines ending in `?` or numbered items with substantive text). Empty or placeholder sections don't trigger.

## Race Conditions

No race conditions identified. The question detection and routing decision happen synchronously within `send_to_chat()`, which is single-threaded per project worker. The `expectations` field is set on the `StructuredSummary` before delivery, not written asynchronously.

## No-Gos (Out of Scope)

- Classifier hardening for Q&A completion flakiness (separate issue)
- Detecting implicit questions in prose (only structured `## Open Questions` sections)
- Changing how the summarizer's anti-fabrication rules work for other question types
- Reformatting how `/do-plan` outputs open questions (the plan skill is fine; the consumer needs fixing)

## Update System

No update system changes required -- this is a bridge-internal change affecting only `bridge/summarizer.py` and `agent/job_queue.py`.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The fix modifies how the bridge processes agent output, not how the agent produces it. No MCP server changes or `.mcp.json` updates needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/coaching-loop.md` with the open question gate behavior
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is created

### Inline Documentation
- [ ] Code comments on the `_extract_open_questions()` function explaining the extraction pattern
- [ ] Updated docstring for `send_to_chat` documenting the open question gate

## Success Criteria

- [ ] When `/do-plan` produces a plan with `## Open Questions`, the pipeline pauses and surfaces questions in Telegram
- [ ] When `/do-plan` produces a plan with no open questions (or empty section), the pipeline auto-continues normally
- [ ] The `expectations` field is populated with verbatim open questions when they exist
- [ ] Existing anti-fabrication behavior is preserved -- no questions are fabricated by the summarizer
- [ ] Unit tests cover: question extraction from markdown, empty section handling, stage-aware gate behavior
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (question-gate)**
  - Name: question-gate-builder
  - Role: Implement open question extraction and stage-aware gate
  - Agent Type: builder
  - Resume: true

- **Validator (question-gate)**
  - Name: question-gate-validator
  - Role: Verify question gate behavior and anti-fabrication preservation
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using standard Tier 1 agents: builder and validator.

## Step by Step Tasks

### 1. Add `_extract_open_questions()` to summarizer
- **Task ID**: build-question-extractor
- **Depends On**: none
- **Assigned To**: question-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_extract_open_questions(text: str) -> list[str]` function to `bridge/summarizer.py`
- Function extracts questions from `## Open Questions` sections in agent output
- Returns list of verbatim question strings, or empty list if no substantive questions found
- Handle edge cases: empty sections, placeholder text, malformed markdown

### 2. Wire question extraction into `summarize_response()`
- **Task ID**: build-summarizer-wire
- **Depends On**: build-question-extractor
- **Assigned To**: question-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- In `summarize_response()`, after getting the structured summary, check if raw output contains open questions
- If questions found and structured summary has no expectations set, populate expectations with extracted questions
- Preserve existing expectations behavior (LLM-detected questions take priority)

### 3. Add open question gate to stage-aware auto-continue
- **Task ID**: build-autocontinue-gate
- **Depends On**: build-question-extractor
- **Assigned To**: question-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- In `send_to_chat()` at the stage-aware auto-continue decision point (~line 1298), add a check
- Before auto-continuing, call `_extract_open_questions()` on the output
- If questions are found, fall through to classifier/deliver path instead of auto-continuing
- Log the decision: "Open questions detected in output, pausing for human input"

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-question-extractor, build-summarizer-wire, build-autocontinue-gate
- **Assigned To**: question-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Test `_extract_open_questions()` with: real open questions, empty section, no section, malformed markdown
- Test that `summarize_response()` populates expectations when open questions exist
- Test that stage-aware auto-continue falls through when open questions detected
- Test that existing behavior is preserved when no open questions exist

### 5. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: question-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests: `pytest tests/`
- Verify anti-fabrication tests still pass
- Verify cross-wire fix tests still pass
- Check that the summarizer's existing question detection is not broken
- Generate final report

## Validation Commands

- `pytest tests/test_open_question_gate.py -v` - validates question extraction and gate behavior
- `pytest tests/test_cross_wire_fixes.py -v` - validates existing classifier behavior preserved
- `pytest tests/ -v` - full test suite
- `python -m ruff check bridge/summarizer.py agent/job_queue.py` - lint check on modified files

---

## Open Questions

1. Should the open question gate apply only to PLAN stage outputs, or to all SDLC stage outputs? The issue specifically mentions plan open questions, but other stages could theoretically produce questions too. Restricting to PLAN is simpler and avoids false positives.

2. When open questions are surfaced and the human answers them, how should the answers flow back into the plan document? Currently the SDLC dispatcher would just re-invoke the next stage. Should the plan be updated with answers before proceeding, or is that the human's responsibility?

3. The `_extract_open_questions()` function needs to decide what counts as a "substantive question." Should it require lines ending in `?`, or should numbered list items under `## Open Questions` be treated as questions regardless of punctuation? Plan open questions are often phrased as statements with context (e.g., "Whether the extraction scope should include counter logic").
