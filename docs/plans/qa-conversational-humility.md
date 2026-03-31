---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/589
last_comment_id: 4154388316
---

# Agent-Controlled Message Delivery

## Problem

The agent has no control over its own output. It generates raw text, the summarizer rewrites it, and whatever comes out gets sent to Telegram. The agent never sees the final message, can't override bad classification, can't choose to react with an emoji instead, and can't catch its own mistakes (like echoing CLI syntax). This affects both PM and Teammate personas.

**Current behavior:**

1. **Agent is blind to its output**: Raw text → Haiku summarizer rewrites → Telegram. The agent never sees or approves the final message.
2. **Classification is hard truth**: Ollama says "respond" or "ignore" — the agent can't disagree. A message classified as needing response always gets a full session, even if it's just banter.
3. **CLI syntax leaking**: `valor-telegram send --chat` examples in the persona prompt get echoed into responses. No system in the pipeline catches this because they're all disconnected.
4. **No react-only path**: Every classified message spawns a full session. "lol" gets a multi-paragraph earnest response.
5. **Authoritative monologue**: Teammate responses lecture instead of conversing. No clarification, no humility, single-perspective walls of text.
6. **False stops**: Agent says "I started the PR review" and stops. The summarizer treats this as a deliverable. The agent should have continued working.

**Desired outcome:**

On session stop, the agent gets a **review gate**: the summarizer produces a draft, and the agent chooses to (a) send it as-is, (b) edit it, (c) replace with emoji reaction, (d) send nothing, or (e) continue working. All personas (PM, Teammate, Dev) get this capability when the session was triggered by a Telegram message. Classification is advisory and mutable — the agent can override it at any point.

## Prior Art

- **Issue #497**: PM should compose Telegram messages via tool, not through summarizer — **Merged**. Established `pm_bypass` pattern, `tools/send_telegram.py`, `pm_sent_message_ids` on AgentSession. Proved the agent CAN own its output.
- **Issue #541**: Dynamic PM persona — **Merged**. Created teammate/QA mode routing. Built `teammate_handler.py`.
- **Issue #571**: PM voice gaps — **Merged**. Fixed dual messages (PM self-message + summarizer). Proved the summarizer bypass works.
- **Issue #556**: Config-driven chat mode — **Merged**. Added `resolve_chat_mode()` returning `"qa"` for teammate groups.

## Why Previous Fixes Failed

The v1 plan for this issue (branch `session/qa-conversational-humility`) attempted 4 independent layers: prompt overhaul, summarizer tone rules, CLI stripping, and 3-way classifier. Each operated in isolation.

| Prior Fix | What It Did | Why It Failed |
|-----------|-------------|---------------|
| v1 Layer 1 | Rewrote QA prompt with humility rules | Prompt can't control what the summarizer does to the output |
| v1 Layer 2 | Added QA tone rules to summarizer | Two LLMs both trying to enforce tone = conflicting signals |
| v1 Layer 3 | CLI syntax regex stripping | Band-aid on symptom; agent still doesn't know what it's sending |
| v1 Layer 4 | 3-way classifier (respond/react/ignore) | Changed bool→str return, silently broke callers (non-empty string is truthy) |

**Root cause pattern:** All fixes applied external controls around the agent instead of giving the agent control over its own output pipeline.

## Data Flow

### Current flow

```
Message → Ollama classifier (bool: respond/ignore)
  → Agent session → raw text output
  → Summarizer (Haiku) rewrites blindly
  → Telegram
Agent never sees final output. No feedback loop.
```

### Proposed flow

```
Message → Ollama classifier (respond/ignore) → classification passed as context
  → Agent session does work
  → Agent tries to stop
  → STOP HOOK fires:
    1. Runs summarizer on agent's raw output → draft message
    2. Injects draft + choices back to agent:
       (a) SEND as-is
       (b) EDIT: "revised message here"
       (c) REACT: 😁 (emoji reaction only)
       (d) SILENT (no response)
       (e) CONTINUE (resume working — false stop detected)
    3. Agent makes a choice
    4. Bridge executes the choice
```

The Stop hook uses `decision: "block"` + `reason` to prevent premature stops, presenting the draft and options. The agent's next output is its delivery decision.

## Architectural Impact

- **New dependencies**: None. Uses existing Stop hook infrastructure (`agent/hooks/stop.py`), existing summarizer, existing `set_reaction()`.
- **Interface changes**: Stop hook return type gains new fields for message delivery control. `classify_needs_response()` stays as `bool` — no breaking change.
- **Coupling**: Reduces coupling. Instead of 4 disconnected systems, the agent is the single decision-maker for its output. Summarizer becomes a draft generator, not the final author.
- **Data ownership**: Agent gains agency over its AgentSession. The stop hook reads the session and presents relevant context.
- **Reversibility**: High. Stop hook enhancement is additive. If it breaks, remove the hook logic and the old flow resumes (summarizer as final author).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (tone calibration review)
- Review rounds: 1

## Prerequisites

No prerequisites — builds on existing Stop hook, summarizer, and `set_reaction()` infrastructure.

## Solution

### Key Elements

- **Stop hook review gate**: When an agent session stops, the hook runs the summarizer to generate a draft, then blocks the stop and presents the draft + choices to the agent. The agent makes a final delivery decision.
- **Teammate persona**: Rename QA/teammate mode to a proper Teammate persona with conversational guidelines (clarify, multi-perspective, humble framing).
- **Classification as context**: Ollama classification result passed as advisory text in the session prompt, not a hard gate.
- **React-only + silence paths**: Agent can choose emoji reaction or no response — handled by the bridge based on the agent's stop-hook decision.
- **Shared infrastructure**: All personas (PM, Teammate, Dev) use the same stop-hook review gate **when the session was created by a Telegram message**. Subagent sessions and programmatically-spawned sessions skip the gate — they don't have a conversation to respond to.

### Flow

**Normal response path:**
1. Agent session runs, does research/reasoning
2. Agent tries to stop (end_turn)
3. Stop hook fires → runs summarizer → gets draft message
4. Hook returns `decision: "block"` with `reason` containing draft + choices
5. Agent sees: "Here's what would be sent: '{draft}'. Reply with: SEND / EDIT: {new text} / REACT: {emoji} / SILENT / CONTINUE"
6. Agent replies with its choice
7. Stop hook fires again → parses choice → writes delivery instruction to AgentSession
8. Hook returns `{}` (allow stop)
9. Bridge reads delivery instruction from session → executes (send text, set reaction, or nothing)

**False-stop detection:**
- Agent output contains promise-like patterns ("I started...", "Let me check...") without substantive content
- Stop hook detects this via simple heuristics and suggests CONTINUE in the review prompt
- Agent decides whether to continue or deliver — no cap, no counter, just a suggestion

### Technical Approach

**Component 1 — Stop hook review gate** (`agent/hooks/stop.py`):
- **Activation rule**: Only fires when the session has an originating Telegram message (i.e., `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` env vars are set). Subagent sessions, programmatic sessions, and local Claude Code sessions skip the gate entirely — they have no conversation to respond to.
- **How it works mechanically**:
  1. First stop: hook reads agent's raw output from transcript file (`input_data["transcript_path"]`, same pattern as `subagent_stop.py`). Calls summarizer via lazy import (`from bridge.summarizer import summarize_response` — hooks run in the parent bridge process, not a subprocess, so all bridge imports are available). Returns `{"decision": "block", "reason": "<review prompt with draft + choices>"}`. The SDK injects the `reason` text as feedback to the agent.
  2. Agent sees the draft and choices, responds with its delivery decision (e.g., "SEND", "EDIT: revised text", "REACT: 😁", "SILENT", "CONTINUE"), then tries to stop again.
  3. Second stop: hook reads the agent's latest output from the transcript tail (last ~500 chars, same `_extract_output_tail` pattern from `subagent_stop.py`). Parses the delivery choice. Writes to AgentSession delivery fields. Returns `{}` (allow stop).
- Use an in-memory dict keyed by session_id to track review state (first stop vs. second stop). Module-level `_review_state: dict[str, bool] = {}` — cleared when session completes.
- Skip gate when `has_pm_messages()` is true (agent already handled delivery mid-session)
- **Observability**: Log review gate activation, draft length, agent's delivery choice, and elapsed time at INFO level

**Component 2 — Delivery execution** (`bridge/response.py`):
- After session completes, check AgentSession for delivery instruction field
- `delivery_action: "send"` + `delivery_text: "..."` → send via `send_response_with_files()`
- `delivery_action: "react"` + `delivery_emoji: "😁"` → call `set_reaction()`, no text
- `delivery_action: "silent"` → truly nothing sent, no emoji, no text
- `delivery_action: None` (no review gate ran, e.g. subagent/programmatic session) → fall through to existing summarizer path (backward compat)
- This field is checked BEFORE the summarizer runs, so the summarizer only fires as safety net when no delivery instruction exists

**Component 3 — Teammate persona prompt** (`agent/teammate_handler.py` → rename to `agent/qa_handler.py`):
- Replace "knowledgeable teammate who knows the codebase well" with conversational guidelines:
  - Clarify before answering when ambiguous
  - Cover 2-3 angles briefly, not one exhaustive explanation
  - Use "I think" / "from what I've seen" framing
  - End with follow-up question when uncertain about the ask
  - Answer their situation first, reference internals only when relevant
- Add instruction: "The routing classifier categorized this as: {classification}. This is an initial guess. Use your judgment."
- Remove: "Your return text will be automatically summarized and sent via Telegram"
- Add: "When you stop, you'll review a draft of your response before it's sent. You can edit, replace with an emoji reaction, or choose silence."

**Component 4 — Classification as context** (`agent/sdk_client.py`):
- Pass Ollama classification result as advisory text in the enriched message for both PM and Teammate sessions
- No changes to `classify_needs_response()` return type or callers

**Component 5 — CLI leak fix** (root cause):
- Remove `valor-telegram send --chat "Dev: Valor" "Hello"` example from `config/personas/_base.md` (line 233)
- This is the root cause of CLI syntax leaking — the agent echoes persona prompt examples
- The agent gets delivery instructions per-session; no need for CLI examples in the persona

**Component 6 — AgentSession delivery fields** (`models/agent_session.py`):
- Add fields: `delivery_action` (str, null), `delivery_text` (str, null), `delivery_emoji` (str, null)
- Stop hook writes these; bridge reads them post-session
- Lightweight — just 3 nullable string fields on an existing model

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] If summarizer fails during stop hook, the hook should still present a "raw output" fallback draft (not crash)
- [ ] If stop hook crashes entirely, session completes normally and existing summarizer path fires (fail-open)
- [ ] `classify_needs_response()` already defaults to `True` on Ollama failure — no change needed

### Empty/Invalid Input Handling
- [ ] Agent raw output is empty → stop hook skips review gate, session ends with no delivery
- [ ] Agent response to review gate is unparseable → treat as SEND (conservative: deliver the draft)
- [ ] REACT with invalid emoji → fall back to SEND (don't lose the message)

### Error State Rendering
- [ ] If delivery_action is set but bridge can't execute (Telegram API error) → dead-letter queue (existing path)
- [ ] If agent keeps choosing CONTINUE → existing nudge cap (50) is the safety backstop; no additional cap on review gate

## Test Impact

- [ ] `tests/unit/test_qa_handler.py` — UPDATE: imports reference `teammate_handler`, update to `qa_handler`. Assertions update for new prompt content.
- [ ] `tests/unit/test_qa_nudge_cap.py` — UPDATE: references `teammate_handler` imports, update to `qa_handler`.
- [ ] `tests/unit/test_config_driven_routing.py` — NO CHANGE: `classify_needs_response` interface unchanged.
- [ ] `tests/e2e/test_message_pipeline.py` — NO CHANGE: classifier interface unchanged.
- [ ] `tests/unit/test_summarizer.py` — NO CHANGE: summarizer still works the same, just called from stop hook instead of bridge.
- [ ] `tests/unit/test_stop_hook.py` — UPDATE: existing tests for SDLC branch enforcement must still pass; add new tests for review gate (draft generation, choice parsing, delivery field writes, activation rule).

## Rabbit Holes

- **Replacing the summarizer entirely**: The summarizer is still useful as a draft generator. Don't remove it — change who calls it (stop hook) and who has final say (agent).
- **Complex NLU for false-stop detection**: Simple heuristics ("I started", "Let me check" without substantive content) are enough. Don't build a classifier for this.
- **Making the review gate async/streaming**: The stop hook is synchronous in the SDK. Don't try to stream the draft. The agent sees the whole thing at once and decides.
- **Per-persona review gate behavior**: All personas use the same mechanism when Telegram-triggered. Don't special-case. The persona prompt shapes what the agent decides, not the gate itself.
- **3-way classifier changes**: Don't change `classify_needs_response()`. The agent overrides classification from inside the session.

## Risks

### Risk 1: Stop hook adds latency to every session
**Impact:** Every session gets one extra stop-resume cycle (summarizer call + agent decision).
**Mitigation:** The summarizer call is fast (~500ms with Haiku). The agent decision is typically one short line ("SEND" or "EDIT: ..."). Net add is <2s. Skip the gate entirely when `has_pm_messages()` is true (agent already handled delivery mid-session).

### Risk 2: Agent gets confused by the review gate prompt
**Impact:** Agent doesn't understand the choices, outputs garbage, delivery fails.
**Mitigation:** Parse conservatively — if the agent's response doesn't match any known pattern, treat as SEND (deliver the draft). The draft was already good enough for the old pipeline. Also: the prompt format is simple and tested.

### Risk 3: CONTINUE loop — agent never actually stops
**Impact:** Session runs forever, burning tokens.
**Mitigation:** CONTINUE is a suggestion, not a forced loop. The agent decides whether to continue — it has full context of what it's done and what remains. The existing nudge cap (50) is the safety backstop for runaway sessions. No additional cap needed on the review gate itself.

## Race Conditions

### Race 1: Stop hook reads transcript while agent is still writing
**Location:** `agent/hooks/stop.py` reading transcript file
**Trigger:** SDK fires Stop hook slightly before transcript is fully flushed
**Data prerequisite:** Transcript file must be fully written before hook reads it
**Mitigation:** The SDK guarantees the transcript is flushed before Stop fires. If partial reads occur, the summarizer handles truncated input gracefully (it already handles arbitrary-length input).

## No-Gos (Out of Scope)

- Changing `classify_needs_response()` return type or interface
- CLI syntax stripping/sanitizing in response.py (fix root cause instead)
- Bookend subagents (intake/review) — the stop hook IS the review step
- New MCP server for Telegram
- Adding the review gate to subagent or programmatically-spawned sessions (only Telegram-triggered sessions get it)
- Modifying the summarizer itself (it stays the same, just called from a new location)

## Update System

No update system changes required — code changes propagate via `git pull`. No new dependencies, config files, or migration steps. The new AgentSession fields (`delivery_action`, `delivery_text`, `delivery_emoji`) are nullable and backward-compatible.

## Agent Integration

No new MCP server needed. Changes are internal to the agent hooks and bridge response pipeline. **Note**: hooks run in the parent bridge process (not the Claude Code subprocess), so all bridge/model imports are available via lazy import.

- `agent/hooks/stop.py` — enhanced with review gate logic (lazy-imports summarizer, presents choices)
- `agent/sdk_client.py` — passes classification context to agent, injects Telegram env vars for teammate sessions
- `agent/teammate_handler.py` → `agent/qa_handler.py` — renamed, prompt rewritten with conversational guidelines
- `models/agent_session.py` — 3 new nullable fields for delivery instructions
- `bridge/response.py` or `agent/job_queue.py` — reads delivery instruction before running summarizer
- `config/personas/_base.md` — removes CLI syntax example

All changes use existing infrastructure. No `.mcp.json` changes.

## Documentation

- [ ] Create `docs/features/agent-message-delivery.md` describing the stop-hook review gate, delivery choices, and how PM/Teammate personas use it
- [ ] Update `docs/features/README.md` index table
- [ ] Rename `docs/features/chatsession-teammate-mode.md` → `chatsession-qa-mode.md` and document new Teammate persona
- [ ] Update `docs/features/chat-dev-session-architecture.md` to mention delivery instruction fields on AgentSession

## Success Criteria

- [ ] Stop hook presents draft + choices to agent before delivery
- [ ] Agent can SEND, EDIT, REACT, SILENT, or CONTINUE from the review gate
- [ ] Summarizer output is used as draft, not as final message (agent has last word)
- [ ] All personas (PM, Teammate, Dev) get the review gate when Telegram-triggered; subagent/programmatic sessions skip it
- [ ] Teammate prompt enforces clarification-first, multi-perspective, conversational tone
- [ ] Classification result passed as context to agent (advisory, not hard gate)
- [ ] `valor-telegram send` CLI example removed from persona prompt
- [ ] React-only path works: agent chooses REACT → bridge sets emoji, no text sent
- [ ] False-stop detection: CONTINUE suggested when output looks like a promise, not a deliverable
- [ ] Review gate skipped when agent already sent messages mid-session (`has_pm_messages()`)
- [ ] SILENT means truly silent — no emoji, no text
- [ ] Existing tests updated and passing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (stop-hook)**
  - Name: stop-hook-builder
  - Role: Implement stop hook review gate, delivery instruction fields on AgentSession, bridge delivery execution
  - Agent Type: builder
  - Resume: true

- **Builder (persona-and-prompt)**
  - Name: persona-builder
  - Role: Rename teammate_handler → qa_handler, rewrite Teammate prompt, remove CLI examples from persona, wire classification context
  - Agent Type: builder
  - Resume: true

- **Validator (delivery-flow)**
  - Name: delivery-validator
  - Role: Verify end-to-end delivery flow: review gate fires, choices work, bridge executes correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add delivery instruction fields to AgentSession
- **Task ID**: build-session-fields
- **Depends On**: none
- **Validates**: `grep -c "delivery_action\|delivery_text\|delivery_emoji" models/agent_session.py`
- **Assigned To**: stop-hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `delivery_action = Field(null=True)` — "send" / "react" / "silent" / None
- Add `delivery_text = Field(null=True)` — final message text (for send/edit)
- Add `delivery_emoji = Field(null=True)` — emoji for react-only
- These are nullable string fields, backward-compatible

### 2. Implement stop hook review gate
- **Task ID**: build-stop-hook
- **Depends On**: build-session-fields
- **Validates**: tests/unit/test_stop_hook_review.py (create)
- **Assigned To**: stop-hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Enhance `agent/hooks/stop.py`:
  - Activation rule: only fire when `TELEGRAM_CHAT_ID` and `TELEGRAM_REPLY_TO` env vars are set (Telegram-triggered session)
  - On first stop: read transcript, run summarizer for draft, return `{"decision": "block", "reason": "<review prompt with draft + choices>"}`
  - Track review state (in-memory dict keyed by session_id, or flag on AgentSession)
  - On second stop: parse agent's delivery choice, write to AgentSession delivery fields, return `{}`
  - Skip gate when `has_pm_messages()` is true
- Add false-stop heuristics: detect "I started...", "Let me check..." without substance → suggest CONTINUE (no cap, just a suggestion)
- Create unit tests for review gate logic (parsing choices, edge cases)

### 3. Wire delivery execution in bridge
- **Task ID**: build-delivery-exec
- **Depends On**: build-session-fields
- **Validates**: tests/unit/test_delivery_execution.py (create)
- **Assigned To**: stop-hook-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-stop-hook)
- In the post-session response path (where `send_response_with_files` is called):
  - Check `session.delivery_action` before running summarizer
  - If `"send"`: use `session.delivery_text` as the final message (skip summarizer)
  - If `"react"`: call `set_reaction()` with `session.delivery_emoji`, send no text
  - If `"silent"`: truly nothing — no emoji, no text, session just ends
  - If `None`: fall through to existing summarizer path (backward compat)

### 4. Rename teammate_handler → qa_handler and rewrite prompt
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: tests/unit/test_qa_handler.py
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `agent/teammate_handler.py` → `agent/qa_handler.py`
- Rename `agent/teammate_metrics.py` → `agent/qa_metrics.py` (if exists)
- Update all imports across codebase
- Rewrite `build_qa_instructions()` with conversational guidelines
- Add review gate awareness: "When you stop, you'll review a draft before delivery"
- Add classification context instruction
- Remove CLI examples from `config/personas/_base.md`

### 5. Wire classification context into sessions
- **Task ID**: build-classification-context
- **Depends On**: build-persona
- **Validates**: `grep -c "classification" agent/sdk_client.py`
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/sdk_client.py`: pass Ollama classification result as advisory text in enriched message
- Format: "[Routing context: classifier said '{result}'. This is a guess — use your judgment.]"
- Apply to both PM and Teammate code paths
- No changes to `classify_needs_response()` interface

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-stop-hook, build-delivery-exec, build-persona, build-classification-context
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify stop hook review gate logic (parse SEND/EDIT/REACT/SILENT/CONTINUE)
- Verify delivery execution reads AgentSession fields correctly
- Verify QA prompt includes conversational guidelines
- Verify no `valor-telegram send` in persona files
- Verify `classify_needs_response` still returns bool

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: persona-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/agent-message-delivery.md`
- Update `docs/features/README.md` index
- Rename `docs/features/chatsession-teammate-mode.md` → `chatsession-qa-mode.md`

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met including documentation
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Delivery fields exist | `grep -c "delivery_action" models/agent_session.py` | output > 0 |
| Stop hook has review gate | `grep -c "DELIVERY REVIEW\|delivery_action" agent/hooks/stop.py` | output > 0 |
| QA prompt has humility | `grep -c "I think\|clarif\|from what" agent/qa_handler.py` | output > 0 |
| No CLI in persona | `grep -c "valor-telegram send" config/personas/_base.md` | exit code 1 |
| No teammate_handler refs | `grep -rl "teammate_handler" agent/ tests/` | exit code 1 |
| classify still returns bool | `grep "-> bool" bridge/routing.py \| grep classify` | output > 0 |

## Critique Results

| # | CONCERN | CRITIC | STATUS |
|---|---------|--------|--------|
| 1 | Stop hook can't read agent's delivery choice — StopHookInput has no raw output field | Archaeologist | RESOLVED — hook reads transcript tail via `transcript_path` (same pattern as `subagent_stop.py`) |
| 2 | Stop hook can't import summarizer (subprocess isolation) | Skeptic | RESOLVED — hooks run in parent bridge process (confirmed by `session_registry.py` docs), lazy import works |
| 3 | Delivery execution location unspecified (response.py vs job_queue.py) | Operator | RESOLVED — committed to `bridge/response.py` |
| 4 | `test_stop_hook.py` missing from Test Impact | Operator | RESOLVED — added to Test Impact section |
| 5 | Review gate adds latency to every session | Skeptic | ACCEPTED — ~500ms Haiku call + 1 agent turn. Skipped when `has_pm_messages()` true. |
| 6 | `qa_handler` rename may not be needed if Teammate is a proper persona | Simplifier | NOTED — rename still useful for clarity (teammate_handler → qa_handler) but open to keeping `teammate_handler` if preferred |

---

## Open Questions

None — all questions resolved during spike and critique phases.
