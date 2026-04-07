---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-06
tracking: https://github.com/tomcounsell/ai/issues/274
---

# Unified Structured Summarizer + Semantic Session Routing

## Problem

When the agent finishes work and posts to Telegram, PM feedback often arrives without reply-to threading: image + separate comment, next-day replies, multi-message feedback. Today every non-reply message creates a brand new session, losing context and fragmenting conversations.

The root cause: session routing is purely mechanical (reply-to message ID) when it should be semantic (what is this message about?).

**Current behavior:**
Session routing is purely mechanical -- only `reply_to_msg_id` links a message to an existing session. If the PM sends a new message (no reply-to) about the same topic, a fresh session is created with no context from the previous conversation.

**Desired outcome:**
Non-reply messages are semantically matched to active/dormant sessions when those sessions have declared expectations. High-confidence matches auto-route to the existing session. Failures degrade gracefully to current behavior (new session).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on confidence thresholds and UX)
- Review rounds: 1 (code review)

Solo dev work is fast -- the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites -- this work has no external dependencies. Uses existing Anthropic API key and Haiku model already in use by the summarizer.

## Solution

Two changes, rolled out in phases:

1. **Upgrade the summarizer** to produce structured output via a single Haiku tool_use call: `context_summary`, `response` (Telegram message), and `expectations` (what the agent needs from the human)
2. **Add a semantic session router** that matches unthreaded messages against active sessions using those fields

### Key Elements

- **Structured summarizer output**: Upgrade `_summarize_with_haiku()` to use `tool_use` for structured extraction of `context_summary`, `response`, and `expectations` fields
- **Session model fields**: Add `context_summary` and `expectations` fields to `AgentSession` for routing data persistence
- **Summarizer fallback chain**: Anthropic API -> OpenRouter (same Haiku model) -> raw truncation. Remove Ollama dependency
- **Semantic session router**: New classifier that matches non-reply messages to sessions with active expectations

### Flow

**New message (no reply-to)** -> Check for sessions with non-null `expectations` in this chat -> If candidates exist, Haiku classifies match -> High confidence (>=0.80) -> auto-route to session -> Otherwise, create new session as today

**Agent response** -> Summarizer produces structured output -> `context_summary` and `expectations` persisted on `AgentSession` -> `response` field replaces current summary text

## Phase 1: Structured Summarizer Output

### 1.1 Add fields to AgentSession

**File:** `models/agent_session.py` (after `pr_url`, ~line 97)

```python
context_summary = Field(null=True, max_length=200)  # "What is this session about"
expectations = Field(null=True, max_length=500)      # "What does the agent need from the human"
```

No migration -- Popoto/Redis handles new nullable fields.

### 1.2 Modify Haiku call to use tool_use

**File:** `bridge/summarizer.py`

Replace plain-text Haiku output with structured `tool_use`. Define a tool schema:

```python
STRUCTURED_SUMMARY_TOOL = {
    "name": "structured_summary",
    "description": "Produce a structured summary of the developer session output.",
    "input_schema": {
        "type": "object",
        "properties": {
            "context_summary": {
                "type": "string",
                "description": "One sentence: what this session is about (for routing)"
            },
            "response": {
                "type": "string",
                "description": "The Telegram message. Follow format rules from system prompt."
            },
            "expectations": {
                "type": ["string", "null"],
                "description": "What specific input/decision/approval needed from human, or null"
            }
        },
        "required": ["context_summary", "response", "expectations"]
    }
}
```

**Modify `_summarize_with_haiku()`:**
- Add `tools=[STRUCTURED_SUMMARY_TOOL]` and `tool_choice={"type": "tool", "name": "structured_summary"}`
- Return new `StructuredSummary` dataclass instead of `str | None`
- Parse `response.content[0].input` directly (tool_use returns clean dict)

**New internal dataclass:**
```python
@dataclass
class StructuredSummary:
    context_summary: str
    response: str
    expectations: str | None
```

**Remove Ollama fallback, use OpenRouter instead.** If the Anthropic SDK is working for the agent, Haiku will work too. Fallback chain:
- **Primary:** Anthropic API with `tool_use` (direct Haiku)
- **Fallback:** OpenRouter API with `tool_use` (Haiku via `OPENROUTER_API_KEY` + `OPENROUTER_HAIKU` from `config/models.py`)
- **Last resort:** Raw truncation

Delete `_summarize_with_ollama()`. Add `_summarize_with_openrouter()` mirroring the Haiku call via OpenRouter.

### 1.3 Update SummarizedResponse

**File:** `bridge/summarizer.py` (~line 119)

Add fields to the dataclass:
```python
context_summary: str | None = None
expectations: str | None = None
```

In `summarize_response()`: extract `.response` for `_compose_structured_summary()` (unchanged mechanical post-processing), carry routing fields through.

### 1.4 Persist routing fields

**File:** `bridge/response.py` (~line 406, after summarizer call)

Save `context_summary` and `expectations` to session after `summarize_response()` succeeds. Non-fatal on failure.

### 1.5 Update system prompt

**File:** `bridge/summarizer.py` (SUMMARIZER_SYSTEM_PROMPT)

Add preamble explaining the three structured fields. Existing format rules (bullets, questions after "---", SDLC handling) apply to the `response` field. Guide quality:
- `context_summary`: specific topic + scope, not vague
- `response`: senior dev reporting to PM, direct and professional
- `expectations`: specific input needed, or null when work is self-contained

Pass richer session context into `_build_summary_prompt()`: branch_name, work_item_slug, issue_url, plan_url, pr_url, history entries.

## Phase 2: Semantic Session Router

### 2.1 New router module

**New file:** `bridge/session_router.py`

```python
async def find_matching_session(
    chat_id: str, message_text: str, project_key: str,
) -> tuple[str | None, float]:
```

1. Query AgentSession for `dormant`/`active` sessions in this chat with non-null `expectations`
2. Zero candidates -> return `(None, 0.0)` immediately (no LLM cost)
3. Cap at 5 most recent by `last_activity`
4. Build multiple-choice prompt, call Haiku
5. Return `(session_id, confidence)` or `(None, 0.0)`

**Confidence thresholds:**
- >= 0.80: auto-route to matched session
- < 0.80: new session (medium-confidence disambiguation deferred to Phase 3)

**Feature flag:** `SEMANTIC_ROUTING` env var, default `false`.

### 2.2 Wire into message handler

**File:** `bridge/telegram_bridge.py` (~line 709-712)

In the `else` branch (no reply-to), before creating a new session_id:
- If `SEMANTIC_ROUTING` enabled, call `find_matching_session()`
- High confidence match -> use matched session_id
- Otherwise -> fall through to current behavior (new session)
- All errors fall through to new session (non-fatal)

## Rabbit Holes

- **Medium-confidence UX (0.50-0.80 range)**: Phase 3 in the issue. "Is this about X?" disambiguation messages would be nice but add significant complexity. Explicitly deferred.
- **Cross-chat routing**: Never route across different Telegram chats. Scoped strictly to same `chat_id`.
- **Historical session matching**: Only match against sessions with non-null `expectations`. Don't build a general-purpose session search.
- **Custom embedding/vector search**: Haiku classification is sufficient. No need for embedding-based similarity.

## Risks

### Risk 1: False positive routing
**Impact:** Message gets routed to wrong session, confusing both agent and PM.
**Mitigation:** High confidence threshold (>=0.80), feature flag for Phase 2, reply-to routing is unchanged (semantic is additive).

### Risk 2: Summarizer regression
**Impact:** Summary quality degrades when switching to `tool_use` output format.
**Mitigation:** Fallback to current text-only behavior if `tool_use` response is malformed. Existing `_compose_structured_summary()` logic remains intact.

### Risk 3: Ollama removal breaks installations without Anthropic key
**Impact:** Summarization fails completely on machines with only Ollama.
**Mitigation:** OpenRouter fallback provides a second API path. Raw truncation as final fallback ensures messages are always delivered.

## No-Gos (Out of Scope)

- Do NOT change reply-to routing -- remains the primary mechanism
- Do NOT route across chats -- candidates scoped to same `chat_id` only
- Do NOT block message delivery on routing failures
- Do NOT leave the codebase without a fallback -- OpenRouter replaces Ollama as the secondary path
- Phase 3 medium-confidence disambiguation UX
- Embedding-based or vector similarity matching
- Session merge/split capabilities

## Update System

No update system changes required -- bridge-internal feature. New AgentSession fields auto-handled by Popoto/Redis. No new dependencies or config file propagation needed. `SEMANTIC_ROUTING` env var defaults to disabled.

## Agent Integration

No agent integration required -- the structured summarizer and session router operate between agent output and Telegram delivery. The agent itself is unaware of these changes. No new MCP servers or tools needed.

## Documentation

- [ ] Create `docs/features/semantic-session-routing.md` describing the feature
- [ ] Update `docs/features/session-isolation.md` to reference semantic routing
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Inline code comments on confidence threshold logic and fallback chains

## Success Criteria

- [ ] `AgentSession` model has `context_summary` and `expectations` fields
- [ ] Haiku produces valid `StructuredSummary` with all three fields on every summarizer call
- [ ] OpenRouter fallback produces valid `StructuredSummary` with all three fields
- [ ] `context_summary` and `expectations` are persisted on session after summarization
- [ ] Ollama summarizer dependency removed, replaced with OpenRouter fallback
- [ ] `bridge/session_router.py` exists with semantic matching logic
- [ ] Semantic router is feature-flagged via `SEMANTIC_ROUTING` env var
- [ ] Router only queries sessions in the same chat with non-null expectations
- [ ] Confidence threshold >= 0.80 for auto-routing
- [ ] No Haiku call when zero sessions have expectations (fast path)
- [ ] All failures degrade to current behavior (new session created)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Files to Modify

| File | Change |
|------|--------|
| `models/agent_session.py` | Add `context_summary`, `expectations` fields |
| `bridge/summarizer.py` | `StructuredSummary` dataclass, tool_use in Haiku call, updated prompt, updated `SummarizedResponse` |
| `bridge/response.py` | Persist routing fields to session after summarizer call |
| `bridge/session_router.py` | **NEW** -- semantic session matching with Haiku classifier |
| `bridge/telegram_bridge.py` | Insert semantic routing in no-reply-to branch |

## Reuse Existing Code

- `tools/classifier.py` -- Haiku API call pattern (for the router)
- `bridge/summarizer.py:_compose_structured_summary()` -- unchanged mechanical post-processing
- `config/models.py:MODEL_FAST` -- Haiku model constant
- `AgentSession.query.filter()` -- Popoto ORM session lookup

## Team Orchestration

### Team Members

- **Builder (summarizer)**
  - Name: summarizer-builder
  - Role: Upgrade summarizer to structured tool_use output, add OpenRouter fallback, remove Ollama
  - Agent Type: builder
  - Resume: true

- **Builder (session-model)**
  - Name: model-builder
  - Role: Add context_summary and expectations fields to AgentSession
  - Agent Type: builder
  - Resume: true

- **Builder (router)**
  - Name: router-builder
  - Role: Create session_router.py and integrate into telegram_bridge.py
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end routing and summarizer behavior
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using: builder, validator, documentarian

## Step by Step Tasks

### 1. Add AgentSession fields
- **Task ID**: build-model
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `context_summary = Field(null=True, max_length=200)` to AgentSession
- Add `expectations = Field(null=True, max_length=500)` to AgentSession

### 2. Upgrade summarizer to structured output
- **Task ID**: build-summarizer
- **Depends On**: none
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: true
- Define tool schema with `context_summary`, `response`, `expectations` fields
- Modify `_summarize_with_haiku()` to pass tool and parse `tool_use` response
- Add `_summarize_with_openrouter()` function following `tools/doc_summary` pattern
- Replace `_summarize_with_ollama()` with OpenRouter fallback
- Remove `import ollama as ollama_pkg` and related code
- Ensure fallback chain: Haiku tool_use -> Haiku text-only -> OpenRouter -> truncation

### 3. Persist routing fields after summarization
- **Task ID**: build-persistence
- **Depends On**: build-model, build-summarizer
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `send_response_with_files()` (response.py), persist `context_summary` and `expectations` to session after summarization
- Return structured fields from `summarize_response()` so caller can persist

### 4. Create semantic session router
- **Task ID**: build-router
- **Depends On**: build-model
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/session_router.py` with `find_matching_session(chat_id, message_text, project_key)` function
- Query AgentSession for candidates (same chat, non-null expectations, active/dormant)
- If no candidates, return `(None, 0.0)` (zero-cost path)
- If candidates, single Haiku call to classify match
- Return `(session_id, confidence)` if confidence >= 0.80, else `(None, 0.0)`
- Feature-flag via `SEMANTIC_ROUTING` env var (default: disabled)

### 5. Integrate router into telegram_bridge.py
- **Task ID**: build-integration
- **Depends On**: build-router
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- In the handler's non-reply-to branch (~line 709), before creating a fresh session_id:
  - If `SEMANTIC_ROUTING` env var is truthy, call `find_matching_session()`
  - If a match is returned, use that session_id instead of generating a new one
  - Log the routing decision

### 6. Validate integration
- **Task ID**: validate-all
- **Depends On**: build-integration, build-persistence
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify AgentSession model has new fields
- Verify summarizer uses tool_use and falls back correctly
- Verify Ollama dependency is removed
- Verify session_router.py exists and is feature-flagged
- Verify telegram_bridge.py calls the router
- Run `python -m ruff check` and `python -m ruff format --check`
- Run `pytest tests/`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/semantic-session-routing.md`
- Update `docs/features/session-isolation.md` to reference semantic routing
- Add entry to `docs/features/README.md` index table
- Add inline comments on confidence thresholds

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -m ruff check bridge/ models/ tests/` - Lint check
- `python -m ruff format --check bridge/ models/ tests/` - Format check
- `pytest tests/test_summarizer.py -v` - Summarizer tests
- `pytest tests/ -v` - All tests
- `grep -r "context_summary" models/agent_session.py` - Verify field exists
- `grep -r "expectations" models/agent_session.py` - Verify field exists
- `grep -r "SEMANTIC_ROUTING" bridge/session_router.py` - Verify feature flag
- `grep -r "ollama" bridge/summarizer.py` - Should return no matches (removed)
- `python -c "from bridge.session_router import find_matching_session"` - Import check

## Verification

1. Trigger a summarization, check logs for structured output fields
2. Compare Telegram messages before/after -- quality should be equivalent or better
3. Enable `SEMANTIC_ROUTING=true`, send unthreaded message when a session has expectations, verify routing in logs
4. Disable Anthropic API key, verify OpenRouter fallback produces valid structured output
5. Verify no classifier call when zero sessions have expectations
6. `pytest tests/`
