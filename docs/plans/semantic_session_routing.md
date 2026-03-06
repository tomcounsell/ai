---
status: Planning
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

## Solution

Two changes, rolled out in phases:

1. **Upgrade the summarizer** to produce structured output via a single Haiku tool_use call: `context_summary`, `response` (Telegram message), and `expectations` (what the agent needs from the human)
2. **Add a semantic session router** that matches unthreaded messages against active sessions using those fields

## Phase 1: Structured Summarizer Output

### 1.1 Add fields to AgentSession

**File:** `models/agent_session.py` (after `pr_url`, ~line 97)

```python
context_summary = Field(null=True, max_length=200)  # "What is this session about"
expectations = Field(null=True, max_length=500)      # "What does the agent need from the human"
```

No migration — Popoto/Redis handles new nullable fields.

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

## No-Gos

- Do NOT change reply-to routing — remains the primary mechanism
- Do NOT route across chats — candidates scoped to same `chat_id` only
- Do NOT block message delivery on routing failures
- Do NOT leave the codebase without a fallback — OpenRouter replaces Ollama as the secondary path

## Success Criteria

- [ ] Haiku produces valid `StructuredSummary` with all three fields on every summarizer call
- [ ] OpenRouter fallback produces valid `StructuredSummary` with all three fields
- [ ] `context_summary` and `expectations` appear on AgentSession in Redis after agent responses
- [ ] Telegram output quality is equivalent or better than current summaries
- [ ] With `SEMANTIC_ROUTING=true`, unthreaded messages matching an active session's expectations route correctly (verified in logs)
- [ ] No Haiku call when zero sessions have expectations (fast path)
- [ ] All failure modes degrade to current behavior (new session)
- [ ] `pytest tests/` passes with no regressions

## Files to Modify

| File | Change |
|------|--------|
| `models/agent_session.py` | Add `context_summary`, `expectations` fields |
| `bridge/summarizer.py` | `StructuredSummary` dataclass, tool_use in Haiku call, updated prompt, updated `SummarizedResponse` |
| `bridge/response.py` | Persist routing fields to session after summarizer call |
| `bridge/session_router.py` | **NEW** — semantic session matching with Haiku classifier |
| `bridge/telegram_bridge.py` | Insert semantic routing in no-reply-to branch |

## Reuse Existing Code

- `tools/classifier.py` — Haiku API call pattern (for the router)
- `bridge/summarizer.py:_compose_structured_summary()` — unchanged mechanical post-processing
- `config/models.py:MODEL_FAST` — Haiku model constant
- `AgentSession.query.filter()` — Popoto ORM session lookup

## Documentation

- [ ] Create `docs/features/semantic-session-routing.md`
- [ ] Update `docs/features/session-isolation.md` to reference semantic routing
- [ ] Add entry to `docs/features/README.md` index table

## Update System

No update system changes required — bridge-internal feature. New AgentSession fields auto-handled by Popoto/Redis. No new dependencies or config file propagation needed.

## Agent Integration

No agent integration required — the structured summarizer and session router operate between agent output and Telegram delivery. The agent itself is unaware of these changes.

## Verification

1. Trigger a summarization, check logs for structured output fields
2. Compare Telegram messages before/after — quality should be equivalent or better
3. Enable `SEMANTIC_ROUTING=true`, send unthreaded message when a session has expectations, verify routing in logs
4. Disable Anthropic API key, verify OpenRouter fallback produces valid structured output
5. Verify no classifier call when zero sessions have expectations
6. `pytest tests/`
