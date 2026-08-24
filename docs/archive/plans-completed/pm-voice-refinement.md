---
status: Shipped
type: feature
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/540
last_comment_id:
---

# PM Voice Refinement: Naturalize SDLC Language and Polish Stakeholder-Facing Output

## Problem

The PM persona leaks SDLC implementation details, uses robotic error messages, and has formatting inconsistencies that undermine its north star goal: completely obscuring the development process from stakeholders.

**Current behavior:**
- Messages contain raw SDLC stage labels: "PLAN stage complete", "BUILD blocked on #394"
- Crash fallback is a single hardcoded string at `sdk_client.py:1668`, appearing 6+ times in history
- Questions use a bare `?` prefix that is hard to spot in Telegram
- Links appear inconsistently (inline, footer, or both)
- Messages truncate mid-sentence: "? Sho", "Two things need attent"
- Developer metrics leak into PM channels: "931 additions; 21/21 tests passing"
- Dual messages for the same input (casual reply + summarized message)
- Every message starts with a completion emoji, diluting its signal value

**Desired outcome:**
- SDLC stages referenced in natural language ("planning", "building", "testing")
- Crash messages are varied and contextual, never repeated consecutively
- Questions use a visually distinct prefix
- Links standardized as footer section with short-form inline references only
- No mid-sentence truncation -- sentences complete or full output attached as file
- PM channels report outcomes, not developer metrics
- One coherent voice per response
- Completion emoji reserved for true milestones (PR merged, issue closed)

## Prior Art

- **PR #287**: Fix summarizer question fabrication -- added anti-fabrication guards to SUMMARIZER_SYSTEM_PROMPT. Succeeded. Relevant: same file, same prompt section we are modifying.
- **PR #244/#248**: Improve SDLC summary format -- removed checkboxes, embedded issue number, dropped plan links. Succeeded. Relevant: established the current `_compose_structured_summary()` format.
- **PR #275**: Semantic session routing with structured summarizer -- added `context_summary` and `expectations` fields. Succeeded. Relevant: introduced the structured tool-use summarizer we now modify.
- **PR #228**: SDLC-first architecture -- established thin orchestrator + prompt dominance + summarizer reliability. Succeeded. Relevant: foundational architecture this plan builds on.

No prior fixes found that failed -- this is additive refinement of an evolving prompt system.

## Data Flow

1. **Entry point**: Agent produces raw text output (session completion or mid-session nudge)
2. **`bridge/response.py:send_response_with_files()`**: Filters tool logs, extracts files, decides whether to summarize
3. **`bridge/summarizer.py:summarize_response()`**: Calls Haiku LLM with `SUMMARIZER_SYSTEM_PROMPT` to condense output
4. **`bridge/summarizer.py:_compose_structured_summary()`**: Assembles final message: `_get_status_emoji()` + bullets + questions + `_linkify_references()`
5. **`bridge/response.py:512-513`**: Safety truncation at Telegram's 4096-char limit (raw character slice)
6. **Output**: Message delivered to Telegram via `send_markdown()`

Crash path (separate):
1. **`agent/sdk_client.py:1667-1670`**: Top-level exception handler returns hardcoded crash string
2. **`bridge/response.py:send_response_with_files()`**: Treats crash string as normal text, sends to Telegram

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- all changes are within existing function bodies and prompt text
- **Coupling**: No change -- same components, same interfaces
- **Data ownership**: No change
- **Reversibility**: Trivially reversible -- prompt text changes and small function edits

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Eight targeted changes across three files. The primary lever is prompt text (items 1, 3, 4, 6), with small code changes for items 2, 5, 7, 8. No architectural changes, no new files, no new dependencies.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **SUMMARIZER_SYSTEM_PROMPT updates** (items 1, 3, 4, 6): Add instructions to naturalize SDLC stage names, use distinct question prefix, enforce short-form inline references, suppress developer metrics
- **Crash message pool** (item 2): Replace single hardcoded string with varied pool and consecutive-dedup in `sdk_client.py`
- **Sentence-aware truncation** (item 5): Replace raw character slice in `response.py` with sentence-boundary logic
- **Dual-personality guard** (item 7): Ensure PM bypass path in `response.py` prevents summarized duplicate when PM already self-messaged
- **Milestone-selective emoji** (item 8): Update `_get_status_emoji()` to reserve completion emoji for merge/close events

### Flow

**Agent output** -> `send_response_with_files()` -> [PM bypass check] -> `summarize_response()` (with updated prompt) -> `_compose_structured_summary()` (selective emoji) -> [sentence-aware truncation] -> Telegram

### Technical Approach

**Item 1 -- Naturalize SDLC stage names:**
- Add instruction to `SUMMARIZER_SYSTEM_PROMPT`: "Translate raw SDLC stage labels (PLAN, BUILD, TEST, REVIEW, DOCS, MERGE) to natural language equivalents (planning, building, testing, reviewing, documenting, merging). The term 'SDLC' itself is acceptable as a process reference."
- This is a prompt-only change. The LLM handles the translation.

**Item 2 -- Crash message pool:**
- Define a list of crash message variants in `sdk_client.py` (at least 4)
- Add a module-level variable to track the last crash message sent
- Select randomly from the pool, excluding the last-used message
- Each variant includes what happens next ("will retry", "may need to re-trigger")

**Item 3 -- Question prefix visibility:**
- Update `SUMMARIZER_SYSTEM_PROMPT` format rules: change `? ` prefix to a more visible format like `>> ` or a bold-style prefix
- Update `_parse_summary_and_questions()` to recognize the new prefix pattern
- Keep backward compatibility with existing `? ` prefix in the parser

**Item 4 -- Standardize link footer:**
- Add explicit instruction to `SUMMARIZER_SYSTEM_PROMPT`: "Use short-form references only in bullet text (PR #N, issue #N). Never include full URLs in bullets -- link rendering is handled separately."
- The existing `_linkify_references()` handles footer rendering. This is a prompt reinforcement.

**Item 5 -- Sentence-aware truncation:**
- In `response.py:512-513`, replace `text[:4093] + "..."` with a function that finds the last sentence boundary (`.`, `!`, `?` followed by space or end) within the 4096 limit
- If no sentence boundary found within a reasonable window (last 500 chars), fall back to attaching the full text as a file and sending a short summary note

**Item 6 -- Suppress developer metrics:**
- Add instruction to `SUMMARIZER_SYSTEM_PROMPT`: "Do not include line counts, file counts, or exact test numbers. Use outcome language: 'shipped and tested', 'all tests passing', 'reviewed and approved'."

**Item 7 -- Dual-personality guard:**
- The PM bypass path at `response.py:396-408` already skips the summarizer when `has_pm_messages()` is true. However, the raw agent output still gets sent at line 518 (after the bypass returns True at line 504, this should not happen). Investigate and confirm the guard is complete.
- If a code path exists where both PM self-messages and a summarized version are sent, add an early return or flag to prevent the duplicate.

**Item 8 -- Milestone-selective emoji:**
- Update `_get_status_emoji()` to accept additional context (session metadata: has merged PR, has closed issue)
- Return completion emoji only when session has a merged PR or closed issue
- Return no emoji prefix (empty string) for routine updates
- Return progress emoji only for genuinely in-progress work (active SDLC stage)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The crash message pool selection in `sdk_client.py` has a try/except around the pool logic -- test that it falls back to a default message if the pool is somehow empty
- [ ] The sentence-aware truncation has a fallback to raw slice if sentence detection fails -- test with text containing no sentence boundaries

### Empty/Invalid Input Handling
- [ ] `_get_status_emoji()` with no session and no completion flag returns a sensible default
- [ ] Sentence-aware truncation handles empty string, None, and whitespace-only input
- [ ] Crash message pool handles first call (no previous message to exclude)

### Error State Rendering
- [ ] Crash messages are varied and include next-step language
- [ ] Truncated messages end at a complete sentence, not mid-word

## Test Impact

- [ ] `tests/unit/test_summarizer.py::TestComposeStructuredSummary::test_no_session_returns_emoji_and_bullets` -- UPDATE: adjust expected emoji behavior for routine (non-milestone) completions
- [ ] `tests/unit/test_summarizer.py::TestComposeStructuredSummary::test_questions_appended` -- UPDATE: adjust question prefix from `?` to new format
- [ ] `tests/unit/test_summarizer.py::TestComposeStructuredSummary::test_not_completion_uses_pending_emoji` -- UPDATE: may need adjustment for new emoji logic
- [ ] `tests/unit/test_summarizer.py::TestGetStatusEmoji::*` (6 tests) -- UPDATE: adjust expected return values for the new milestone-selective logic
- [ ] `tests/unit/test_summarizer.py::TestParseSummaryAndQuestions::*` (5 tests) -- UPDATE: adjust for new question prefix format
- [ ] `tests/unit/test_summarizer.py::TestAntiFabricationGuards::test_prompt_contains_negative_examples` -- UPDATE: prompt content has changed
- [ ] `tests/unit/test_summarizer.py::TestSummarizeResponse::test_short_response_still_summarized` -- UPDATE: may need adjustment for new prompt rules

## Rabbit Holes

- Rewriting the entire summarizer to use a different LLM or multi-pass approach -- the current Haiku single-pass is sufficient; prompt changes are the lever
- Building a "personality engine" or persona-specific prompts for each project -- out of scope, the summarizer serves all personas
- Implementing message deduplication across the bridge for the dual-personality issue -- the fix is a simple guard in `response.py`, not a dedup system
- Adding telemetry/analytics on message quality -- useful but a separate project
- Refactoring `_compose_structured_summary()` into a template system -- unnecessary abstraction for this scope

## Risks

### Risk 1: Prompt regression on summarizer quality
**Impact:** New SUMMARIZER_SYSTEM_PROMPT instructions could cause the LLM to over-filter or misinterpret content, degrading summary quality
**Mitigation:** Keep prompt additions minimal and directive. Run existing integration tests (`test_real_haiku_*`) to validate. The anti-fabrication guards from PR #287 remain intact.

### Risk 2: Sentence-aware truncation edge cases
**Impact:** Messages with unusual punctuation (URLs, code blocks, abbreviations like "e.g.") could trigger false sentence boundaries
**Mitigation:** Use a conservative sentence boundary regex that requires punctuation followed by whitespace and a capital letter or end-of-string. Fall back to file attachment for ambiguous cases.

## Race Conditions

No race conditions identified -- all changes are synchronous text transformations within the single-threaded message delivery path. The crash message dedup uses a module-level variable but `sdk_client.query()` is not concurrent within a single process.

## No-Gos (Out of Scope)

- No changes to the persona system or persona overlay files
- No changes to bridge routing, nudge loop, or session management
- No changes to the structured tool schema used by the summarizer LLM
- No new MCP servers or tool registrations
- No changes to the auto-continue system
- No Telegram formatting changes beyond the question prefix

## Update System

No update system changes required -- all changes are within existing Python source files (`bridge/summarizer.py`, `bridge/response.py`, `agent/sdk_client.py`). No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The summarizer, response sender, and crash handler are all internal to the bridge/agent pipeline. No new tools, MCP servers, or bridge imports needed.

## Documentation

- [ ] Create `docs/features/pm-voice-refinement.md` describing the crash message pool, sentence-aware truncation, and emoji selectivity rules
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings for `_get_status_emoji()`, `_compose_structured_summary()`, and `SUMMARIZER_SYSTEM_PROMPT`

## Success Criteria

- [ ] No raw SDLC stage labels (PLAN, BUILD, TEST, REVIEW, DOCS, MERGE) appear in summarizer output -- only natural language equivalents
- [ ] The word "SDLC" may still appear as a high-level process reference
- [ ] Crash fallback message pool has at least 4 variants; consecutive identical crash messages are prevented
- [ ] Questions use a visually distinct prefix in all summarizer output
- [ ] Issue/PR links appear in a dedicated footer section; bullet text uses short-form only
- [ ] Truncation respects sentence boundaries or falls back to file attachment
- [ ] SUMMARIZER_SYSTEM_PROMPT instructs against developer metrics
- [ ] No dual messages for a single agent response in PM channels
- [ ] `_get_status_emoji()` returns completion emoji only for merge/close milestones, not routine updates
- [ ] Existing summarizer tests updated to validate new formatting
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (voice-refinement)**
  - Name: voice-builder
  - Role: Implement all 8 solution items across summarizer.py, response.py, and sdk_client.py
  - Agent Type: builder
  - Resume: true

- **Validator (voice-refinement)**
  - Name: voice-validator
  - Role: Verify formatting changes, run tests, validate no raw SDLC labels leak through
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update SUMMARIZER_SYSTEM_PROMPT (items 1, 3, 4, 6)
- **Task ID**: build-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Add SDLC stage naturalization instruction to SUMMARIZER_SYSTEM_PROMPT
- Change question prefix from `? ` to a more visible format (e.g., `>> `)
- Add short-form-only link instruction to SUMMARIZER_SYSTEM_PROMPT
- Add developer metrics suppression instruction to SUMMARIZER_SYSTEM_PROMPT
- Update `_parse_summary_and_questions()` to recognize the new question prefix while keeping backward compat with `? `

### 2. Implement crash message pool (item 2)
- **Task ID**: build-crash-pool
- **Depends On**: none
- **Validates**: tests/unit/test_sdk_client.py (update)
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Define a list of at least 4 crash message variants in `sdk_client.py`
- Add module-level `_last_crash_message` variable for consecutive-dedup
- Replace the hardcoded string at line 1667-1670 with pool selection logic
- Each variant includes what happens next

### 3. Implement sentence-aware truncation (item 5)
- **Task ID**: build-truncation
- **Depends On**: none
- **Validates**: tests/unit/test_response.py (create or update)
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Create a `_truncate_at_sentence_boundary()` function in `response.py`
- Replace `text[:4093] + "..."` at line 512-513 with the new function
- If no sentence boundary within last 500 chars of the limit, attach full text as file and send a short note

### 4. Fix dual-personality responses (item 7)
- **Task ID**: build-dual-fix
- **Depends On**: none
- **Validates**: tests/unit/test_response.py
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Investigate the `pm_bypass` path in `response.py:396-504` to confirm the guard is complete
- If a code path exists where both PM self-messages and summarized output are sent, add the missing guard
- Add a test case verifying single-message delivery when PM has self-messaged

### 5. Update _get_status_emoji() for milestone selectivity (item 8)
- **Task ID**: build-emoji
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py::TestGetStatusEmoji
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `_get_status_emoji()` to check session metadata for merged PR or closed issue
- Return empty string for routine completions (no emoji prefix)
- Return completion emoji only for milestone events
- Update all affected test assertions

### 6. Update existing tests
- **Task ID**: build-tests
- **Depends On**: build-prompt, build-crash-pool, build-truncation, build-dual-fix, build-emoji
- **Validates**: tests/unit/test_summarizer.py, tests/unit/test_response.py
- **Assigned To**: voice-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all test cases identified in the Test Impact section
- Add new test cases for crash message pool, sentence-aware truncation, and emoji selectivity
- Ensure all tests pass

### 7. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: voice-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify no raw SDLC labels in prompt output examples
- Confirm crash message pool has 4+ variants
- Confirm sentence-aware truncation handles edge cases
- Report pass/fail status

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: voice-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-voice-refinement.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_summarizer.py -x -q` | exit code 0 |
| Response tests pass | `pytest tests/unit/test_response.py -x -q` | exit code 0 |
| All tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/summarizer.py bridge/response.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/summarizer.py bridge/response.py agent/sdk_client.py` | exit code 0 |
| No raw SDLC labels in prompt | `python -c "from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT; assert 'translate' in SUMMARIZER_SYSTEM_PROMPT.lower() or 'natural language' in SUMMARIZER_SYSTEM_PROMPT.lower()"` | exit code 0 |
| Crash pool exists | `python -c "from agent.sdk_client import CRASH_MESSAGE_POOL; assert len(CRASH_MESSAGE_POOL) >= 4"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the issue is extremely detailed with confirmed recon on all 8 items, and the solution stays within the three identified files with no architectural changes needed.
