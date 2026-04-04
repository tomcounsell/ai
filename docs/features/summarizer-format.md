# Summarizer: Always-Summarize with SDLC Templates

Structured output format for Telegram delivery of agent work summaries. Every response is summarized via Haiku (no character threshold). SDLC messages use template-rendered stage progress and link footers.

## Key Behaviors

1. **SDLC: always summarize (even empty responses). Non-SDLC: summarize if >= 200 chars.** SDLC sessions always go through Haiku (stage lines + link footers needed). Even empty SDK responses render SDLC stage progress if session data is available. Non-SDLC short responses (< 200 chars) pass through raw -- this preserves programmatic skill output like `/update` that's already formatted.
2. **SDLC template rendering**: Stage progress lines and link footers are rendered in Python code, not by the LLM. The LLM only generates bullet summaries and questions.
3. **Question extraction (anti-fabrication)**: The LLM can surface questions using a `---` separator and `>> ` prefix, but ONLY questions that are **verbatim present** in the raw agent output. Declarative statements and plans must never be reframed as questions. The `expectations` field is set only when explicit questions exist. Legacy `? ` prefix is accepted and normalized to `>> ` by `_normalize_question_prefix()`.

## Output Format

### SDLC Completions
```
✅
ISSUE 168 → PLAN → BUILD → TEST → REVIEW → DOCS
• Two-layer defense: branch tracking + merge detection
• 10 new tests, 63 passing
Issue #168 | PR #176
```

### SDLC Mid-Pipeline with Questions
```
⏳
ISSUE 273 → PLAN → ▶ BUILD → TEST → REVIEW → DOCS
• Replaced upgrade flow with plan picker UI
• Running test suite...

>> Should we use modal or inline picker?
>> 2 nits found in review — skip or patch?
Issue #273
```

### Conversational (Chat)
```
✅
• Summary bullet 1
• Summary bullet 2

>> Question needing input
```

Simple emoji + bullets format, no stage line or link footer. Still summarized via Haiku. No message echo -- Telegram's reply-to feature provides context about what the response is answering.

### Teammate Mode (Prose)
```
The bridge connects Telegram to Claude via Telethon. See bridge/telegram_bridge.py
for the main entry point. The nudge loop in agent/agent_session_queue.py handles delivery
routing based on stop_reason classification.
```

When `session.session_mode == PersonaType.TEAMMATE`, `_compose_structured_summary()` bypasses all structured formatting — no emoji prefix, no bullet parsing, no template. The LLM summary (already in conversational prose via the `persona=teammate` context injected into the prompt) is returned directly. The processing reaction is cleared instead of setting a completion emoji.

## Emoji Vocabulary

| Emoji | Meaning |
|-------|---------|
| ✅ | Milestone completion (merged PR, closed issue) |
| ⏳ | In progress |
| ❌ | Failed |
| ⚠️ | External blocker |
| _(empty)_ | Routine completion (no emoji prefix) |

## Stage Progress Format

Stages are rendered as plain text names joined with ` → `. Position relative to the `▶` marker indicates completion state:

| Format | Meaning |
|--------|---------|
| `STAGE` | Completed (before ▶) or pending (after ▶) |
| `▶ STAGE` | Currently in progress |
| `ISSUE NNN` | ISSUE stage with issue number embedded |

No checkbox icons are used. The ISSUE stage label includes the issue number when available from session links (e.g., `ISSUE 243`). Plan links are excluded from the link footer — only issue and PR links are rendered.

## Auto-Linkification of PR/Issue References

`_linkify_references(text, session)` converts plain `PR #N` and `Issue #N` patterns in the composed summary into clickable markdown links. This acts as a safety net that ensures links always appear, regardless of whether session progress tracking stored the URLs first.

**How it works:**
1. Reads `project_key` from the session object
2. Looks up the GitHub org/repo from the registered project config via `get_project_config()`
3. Applies regex replacement: `PR #123` becomes `[PR #123](https://github.com/org/repo/pull/123)`
4. Similarly for `Issue #N` patterns

**Safeguards:**
- Negative lookbehind `(?<!\[)` prevents double-linking text already inside markdown `[...]` syntax
- Graceful fallback: missing project_key, missing GitHub config, or no session returns text unchanged
- This is additive — the link footer rendered in `_compose_structured_summary()` continues to work as the canonical link source for SDLC jobs

**Called from:** `_compose_structured_summary()`, applied to the final joined text just before return.

## Implementation

- `bridge/summarizer.py`: `summarize_response()` (always-summarize entry point), `_strip_process_narration()` (pre-summarization cleanup), `_compose_structured_summary()` (template renderer with inline stage progress and link footer rendering), `_parse_summary_and_questions()` (question extractor), `_normalize_question_prefix()` (legacy `?` to `>>` conversion), `_linkify_references()` (auto-link PR/Issue refs)
- `bridge/response.py`: Always calls summarizer for non-empty text, passes `AgentSession` via `session=` kwarg. `_truncate_at_sentence_boundary()` ensures clean truncation at Telegram's 4096-char limit.
- `bridge/telegram_bridge.py`: `_send` callback accepts and forwards `session` parameter
- `agent/agent_session_queue.py`: `SendCallback` type includes session parameter, `send_to_chat()` uses `classify_nudge_action()` for routing decisions and passes `agent_session`
- `bridge/markdown.py`: `send_markdown()` with plain-text fallback

## Session Freshness

Stage data (via `stage_states` JSON field managed by `PipelineStateMachine` in `bridge/pipeline_state.py`) and links (`issue_url`, `pr_url`) are written to Redis during agent execution. By the time the summarizer runs, the session object passed through the callback chain may be stale (loaded before stages were recorded).

Both `response.py` and `summarizer.py` re-read the session from Redis before composing structured output:

```python
# Re-read for fresh stage/link data
fresh = list(AgentSession.query.filter(session_id=session.session_id))
if fresh:
    session = fresh[0]
```

Diagnostic logging in `_compose_structured_summary()` confirms when stage progress is rendered vs missing:
- `INFO "Rendered stage progress for session ..."` — template applied successfully
- `WARNING "SDLC session ... has no stage progress to render"` — stage data not found

## Callback Chain

```
agent/agent_session_queue.py send_to_chat()
  → send_cb(chat_id, msg, message_id, agent_session)
  → bridge/telegram_bridge.py _send(chat_id, text, reply_to, session)
  → bridge/response.py send_response_with_files(..., session=session)
      ↳ re-reads session from Redis for fresh stage/link data
  → bridge/summarizer.py summarize_response(text, session=session)
  → _compose_structured_summary(summary, session=session)
      ↳ re-reads session from Redis for fresh stage/link data
```

## Process Narration Stripping

Before summarization, `_strip_process_narration()` removes meta-action lines that describe the agent's internal process rather than meaningful outcomes. This addresses the most common audit finding — verbose "Let me check...", "Now reading..." lines passing through to Telegram.

**Stripped patterns** (lines starting with):
- "Let me check/look/read/examine/review..."
- "I'll check/look/read/examine/review..."
- "Now let me check/look/read/examine/review..."
- "Checking/Looking/Reading/Examining/Reviewing..."

**Preserved**: Lines containing substantive content (e.g., "I'll document the API changes" or "Let me explain the architecture") are NOT stripped — only meta-actions that describe tool invocations.

The stripping runs inside `summarize_response()` before the text is passed to `_build_summary_prompt()`, reducing token usage and improving summary quality.

## Anti-Fabrication Rules (Issue #280)

The summarizer must NEVER fabricate questions that are not verbatim in the raw agent output. This was added after Haiku reframed declarative statements ("I will add sdlc to classifier categories") as questions (">> Should classifier be updated to output 'sdlc'?"), causing false-dormant sessions.

**Rules enforced in `SUMMARIZER_SYSTEM_PROMPT`:**

1. Only surface questions that are **explicit** in the raw output (sentences ending in `?` directed at the human)
2. Declarative statements ("I will do X") are plans, not questions
3. Future-tense work descriptions are NOT questions
4. The `expectations` field is null unless an explicit question exists
5. Negative examples in the prompt show Haiku what NOT to do

**Rules enforced in `STRUCTURED_SUMMARY_TOOL` schema:**

The `expectations` field description explicitly states it should only be set when the raw output contains an explicit question directed at the human.

**Test coverage:** `TestQuestionFabricationPrevention` in `tests/test_summarizer.py` covers 10 scenarios including declarative statements, real questions, mixed content, future-tense plans, rhetorical questions, code snippets with `?`, and conditional statements. Integration tests with real Haiku API validate end-to-end behavior.

## Adaptive Format Rules

1. **Simple completions**: "Done ✅" (still summarized for consistency)
2. **Conversational**: Prose, preserving tone
3. **Questions**: Preserved exactly, surfaced after bullets via `---` separator with `>> ` prefix
4. **SDLC work**: Emoji + stage line + bullets + questions + link footer (no message echo)
5. **Status updates**: 2-4 bullet points
6. **Teammate sessions** (`PersonaType.TEAMMATE`): Conversational prose — no bullets, no emoji prefix, no structured template. Bypasses `_compose_structured_summary()` formatting entirely.

## Telegram Markdown

Basic `md` parse mode (not MarkdownV2). Supports bold, inline code, and `[text](url)` links. Falls back to plain text on parse errors.

## Related

- [AgentSession Model](agent-session-model.md) - Unified lifecycle model with stage progress helpers
- [Bridge Response Improvements](bridge-response-improvements.md) - Response pipeline
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) - Output classification and auto-continue
- [PM Voice Refinement](pm-voice-refinement.md) - Naturalized SDLC language, crash pool, sentence truncation, milestone-selective emoji
