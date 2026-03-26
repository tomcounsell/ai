---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/541
last_comment_id:
---

# Dynamic PM Persona: Conversational Q&A Mode vs Structured Work Mode

## Problem

The PM persona uses the same structured formatting for Q&A answers and SDLC work updates. When a stakeholder asks "what are our open issues?", they get a `✅` prefix and bullet points — the same format as a multi-stage build completion report. Q&A should feel like a knowledgeable teammate chatting, not a CI dashboard reporting status.

Additionally, Q&A answers are often shallow — the agent answers from its system prompt rather than actively researching the codebase, memory system, and knowledge base. And the processing reaction emoji (`👀`/`🤔`) persists on the original message after the Q&A answer arrives.

**Current behavior:**
- Q&A and work-mode responses go through the same `_compose_structured_summary()` path, getting emoji prefix + bullet formatting
- Q&A instructions in `qa_handler.py` list read-only tools but don't prioritize research-first behavior
- Processing reaction stays after Q&A delivery — no cleanup call
- Two Q&A delivery paths exist (`send_telegram.py` vs summarizer fallback) producing inconsistent voice

**Desired outcome:**
- Q&A: conversational prose, no emoji prefix, no bullet template, research-backed answers citing code/memory/docs
- Work mode: structured formatting unchanged (with #540 refinements)
- Processing reaction cleared after Q&A delivery
- One consistent Q&A voice regardless of delivery path

## Prior Art

- **Issue #529 / PR #529**: ChatSession Q&A mode with intent classifier — shipped the `qa_mode` routing path, `qa_handler.py`, and `qa_metrics.py`. Established the branch point at `sdk_client.py:1487`.
- **Issue #497 / PR #527**: PM Telegram tool — enabled PM self-messaging via `send_telegram.py`, introduced `pm_bypass` in `response.py:402`.
- **Issue #540**: PM voice refinement (sibling) — covers work-mode formatting changes. Coordinates on `SUMMARIZER_SYSTEM_PROMPT` and `_compose_structured_summary()`.

## Data Flow

Q&A message lifecycle, highlighting where changes land:

1. **Telegram** → `bridge/telegram_bridge.py:1026` — sets `👀` reaction
2. **Classify** → `agent/intent_classifier.py` — determines `is_qa=True`
3. **Session** → `sdk_client.py:1497` — sets `qa_mode=True` on `AgentSession`
4. **Enrich** → `sdk_client.py:1511` — appends `build_qa_instructions()` **← CHANGE 1: research-first instructions**
5. **Agent runs** → Claude Code answers using read-only tools
6. **Output** → `job_queue.py:2080` — calls `send_cb()` with `agent_session`
7. **Deliver** → `response.py:411` — `should_summarize` check **← CHANGE 2: skip structured summary for Q&A**
8. **Format** → `summarizer.py:1228` — `_compose_structured_summary()` **← CHANGE 3: Q&A bypass**
9. **React** → `job_queue.py:2285` — sets completion reaction **← CHANGE 4: clear reaction for Q&A**

## Architectural Impact

- **No new dependencies**: Uses existing `qa_mode` flag, `set_reaction(None)`, and summarizer infrastructure
- **Interface changes**: `_compose_structured_summary()` gains a Q&A bypass path; `build_qa_instructions()` text updated
- **Coupling**: Slightly reduces coupling — Q&A path becomes simpler (less summarizer involvement)
- **Reversibility**: Fully reversible — remove the `qa_mode` conditionals and behavior returns to current state

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Four surgical changes in four files. No new modules, no new infrastructure.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Q&A summarizer bypass**: Skip `_compose_structured_summary()` for Q&A sessions — return the LLM summary as prose without emoji/bullet/footer wrapping
- **Research-first Q&A instructions**: Rewrite `build_qa_instructions()` to prioritize evidence gathering from code, memory, and docs before forming a response
- **Reaction clearing**: Clear processing emoji after Q&A answer delivery
- **Unified Q&A voice**: Remove the `send_telegram.py` instruction from Q&A handler — Q&A always goes through the summarizer with Q&A-specific formatting

### Flow

**Stakeholder asks question** → Intent classifier routes to Q&A → Agent researches (code, memory, docs) → Agent returns answer → Summarizer formats as prose (no bullets/emoji) → Telegram delivers → Processing reaction cleared

### Technical Approach

- Branch on `session.qa_mode` in `_compose_structured_summary()` to skip emoji prefix and bullet formatting
- Add a Q&A-specific format rule to `SUMMARIZER_SYSTEM_PROMPT` so the LLM produces prose
- Rewrite `build_qa_instructions()` to emphasize: search code first, query memory, consult docs, cite sources
- Remove `send_telegram.py` instruction from Q&A handler — eliminates the dual-path ambiguity
- In `job_queue.py` completion reaction block, call `react_cb(chat_id, msg_id, None)` when `qa_mode=True`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The Q&A bypass in `_compose_structured_summary()` is a simple conditional — no new exception handlers needed
- [ ] Reaction clearing uses existing `set_reaction()` which already has error handling (`try/except` at `response.py:594`)

### Empty/Invalid Input Handling
- [ ] Empty Q&A responses should still produce a fallback message (existing behavior in summarizer)
- [ ] `qa_mode` flag missing/None on session should default to current behavior (structured formatting)

### Error State Rendering
- [ ] Q&A responses that fail should not leave a dangling processing reaction — ensure clearing runs even on error path

## Test Impact

- [ ] `tests/unit/test_qa_handler.py::test_telegram_send_instruction` — UPDATE: assertion will change since `send_telegram.py` instruction is removed from Q&A handler
- [ ] `tests/unit/test_qa_handler.py::test_conversational_tone` — UPDATE: may need new assertions for research-first language
- [ ] `tests/unit/test_summarizer.py::TestComposeStructuredSummary` — UPDATE: add Q&A-mode test cases that verify no emoji prefix and prose format
- [ ] `tests/unit/test_summarizer.py::TestGetStatusEmojiRegression` — no change (Q&A bypass is upstream of emoji selection)
- [ ] `tests/unit/test_summarizer.py::TestComposeStructuredSummaryWithSession` — UPDATE: add qa_mode session variant

## Rabbit Holes

- **Separate Q&A persona overlay file** — The Q&A formatting difference is output-level, not identity-level. Creating a separate persona adds complexity for no gain. The PM is the PM in both modes.
- **Q&A-specific LLM model** — Tempting to use a different model for Q&A vs work, but adds configuration complexity. Same model, different prompt instructions.
- **Smart reaction types for Q&A** — e.g., using different emoji for different Q&A categories. Over-engineering — just clear the reaction.

## Risks

### Risk 1: Q&A answers bypass summarizer entirely and are too long for Telegram
**Impact:** Messages exceed 4096 chars and get raw-truncated mid-sentence
**Mitigation:** Q&A still goes through the summarizer LLM for condensing — only `_compose_structured_summary()` formatting is bypassed. Length control stays intact.

### Risk 2: Removing `send_telegram.py` instruction breaks Q&A that currently works
**Impact:** Q&A responses that previously delivered via PM self-message now go through summarizer
**Mitigation:** The summarizer path with Q&A formatting produces the same conversational tone. Monitor Q&A delivery for one day after shipping.

## Race Conditions

No race conditions identified — all changes are in the synchronous formatting and reaction-setting paths. The `qa_mode` flag is set once during intent classification and only read downstream.

## No-Gos (Out of Scope)

- Work-mode formatting changes (covered by #540)
- Intent classifier accuracy improvements
- New persona overlay files
- Changes to the nudge loop or auto-continue behavior
- Q&A-specific model selection

## Update System

No update system changes required — this feature modifies bridge-internal formatting and Q&A instructions. No new dependencies or config files.

## Agent Integration

No agent integration required — changes are in the bridge summarizer and Q&A handler, which are already in the agent's execution path. No new MCP server or tool registration needed.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` — add Q&A formatting section describing the prose vs structured split
- [ ] Add entry to `docs/features/README.md` index if new feature doc created
- [ ] Update inline comments in `_compose_structured_summary()` and `build_qa_instructions()`

## Success Criteria

- [ ] Q&A responses use conversational prose (no `✅` prefix, no `• ` bullets, no structured template)
- [ ] Q&A answers cite source code, memory, or docs when relevant (research-first behavior)
- [ ] Processing reaction cleared after Q&A answer delivery
- [ ] Work-mode messages unchanged (structured formatting, `🏆` reaction)
- [ ] Q&A voice consistent — no dual-path ambiguity (`send_telegram.py` instruction removed)
- [ ] `qa_mode` flag is the single branch point for all formatting differences
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (qa-formatting)**
  - Name: qa-builder
  - Role: Implement Q&A summarizer bypass, research-first instructions, reaction clearing, and voice unification
  - Agent Type: builder
  - Resume: true

- **Validator (qa-formatting)**
  - Name: qa-validator
  - Role: Verify Q&A formatting, reaction behavior, and test coverage
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: qa-docs
  - Role: Update chat-dev-session architecture doc with Q&A formatting section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Update Q&A handler instructions
- **Task ID**: build-qa-instructions
- **Depends On**: none
- **Validates**: tests/unit/test_qa_handler.py
- **Assigned To**: qa-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `build_qa_instructions()` in `agent/qa_handler.py` to emphasize research-first behavior: search source code (Grep/Glob), query memory system (`python -m tools.memory_search search`), consult knowledge base docs, cite findings in response
- Remove the `send_telegram.py` instruction — Q&A always goes through summarizer
- Update existing tests in `tests/unit/test_qa_handler.py` (remove `send_telegram.py` assertion, add research-first assertions)

### 2. Add Q&A bypass to summarizer
- **Task ID**: build-summarizer-bypass
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py
- **Assigned To**: qa-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Q&A format rule to `SUMMARIZER_SYSTEM_PROMPT` in `bridge/summarizer.py`: "For Q&A sessions, respond in conversational prose — no bullets, no status prefix, no structured template"
- In `_compose_structured_summary()`, check `session.qa_mode` — if True, return summary text directly without emoji prefix, bullet parsing, or link footer
- Pass `qa_mode` context to `_build_summary_prompt()` so the LLM knows to use prose format
- Add test cases in `tests/unit/test_summarizer.py`: Q&A session produces prose without emoji, work-mode session unchanged

### 3. Clear reaction after Q&A delivery
- **Task ID**: build-reaction-clearing
- **Depends On**: none
- **Validates**: tests/unit/test_qa_nudge_cap.py (extend)
- **Assigned To**: qa-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/job_queue.py` completion reaction block (around line 2285), check `agent_session.qa_mode` — if True, call `react_cb(chat_id, msg_id, None)` to clear reaction instead of setting `🏆`/`👍`
- Add test verifying Q&A sessions get `None` reaction (clearing) while work sessions get completion emoji

### 4. Validate all changes
- **Task ID**: validate-qa-formatting
- **Depends On**: build-qa-instructions, build-summarizer-bypass, build-reaction-clearing
- **Assigned To**: qa-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_qa_handler.py tests/unit/test_qa_nudge_cap.py tests/unit/test_summarizer.py -v`
- Verify no regressions in work-mode formatting (structured summary tests still pass)
- Verify Q&A bypass produces prose without emoji/bullets
- Run full lint: `python -m ruff check . && python -m ruff format --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-qa-formatting
- **Assigned To**: qa-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/chat-dev-session-architecture.md` with Q&A formatting section
- Add entry to `docs/features/README.md` index table if new

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: qa-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_qa_handler.py tests/unit/test_qa_nudge_cap.py tests/unit/test_summarizer.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Q&A bypass exists | `grep -n 'qa_mode' bridge/summarizer.py` | output > 0 |
| Research instructions present | `grep -n 'memory_search' agent/qa_handler.py` | output > 0 |
| Reaction clearing exists | `grep -n 'qa_mode' agent/job_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the approach was validated in conversation. All four changes are surgical modifications to existing code paths using the existing `qa_mode` flag.
