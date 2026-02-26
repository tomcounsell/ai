# Summarizer: Bullet-Point Format

Structured output format for Telegram delivery of agent work summaries. Replaces dense paragraph summaries with scannable, emoji-prefixed bullet points.

## Output Format

### SDLC Completions
```
✅ Infinite false-positive loop after squash merge
☑ ISSUE → ☑ PLAN → ☑ BUILD → ☑ TEST → ☑ REVIEW → ☑ DOCS
• Two-layer defense: branch tracking + merge detection
• 10 new tests, 63 passing
Issue #168 | Plan | PR #176
```

### Mid-Pipeline
```
⏳ Plan picker for free teams
☑ ISSUE → ☑ PLAN → ☑ BUILD → ▶ TEST → ☐ REVIEW → ☐ DOCS
• Replaced upgrade flow with plan picker UI
• Running test suite...
Issue #273 | Plan
```

### Conversational
Simple prose format, no stage line or link footer.

## Emoji Vocabulary

| Emoji | Meaning |
|-------|---------|
| ✅ | Completed |
| ⏳ | In progress |
| ❌ | Failed |
| ⚠️ | External blocker |

## Stage Progress Symbols

| Symbol | Meaning |
|--------|---------|
| ☑ | Stage completed |
| ▶ | Stage in progress |
| ☐ | Stage pending |

## Implementation

- `bridge/summarizer.py`: Adaptive system prompt, `_render_stage_progress()`, `_render_link_footer()`, `_compose_structured_summary()`
- `bridge/markdown.py`: `send_markdown()` with plain-text fallback
- `bridge/response.py`: Passes `AgentSession` to summarizer for context enrichment

## Adaptive Format Rules

1. **Simple completions**: "Done ✅"
2. **Conversational**: Prose, preserving tone
3. **Questions**: Preserved exactly
4. **SDLC work**: Emoji + stage line + bullets + link footer
5. **Status updates**: 2-4 bullet points

## Telegram Markdown

Basic `md` parse mode (not MarkdownV2). Supports bold, inline code, and `[text](url)` links. Falls back to plain text on parse errors.

## Related

- [AgentSession Model](agent-session-model.md) - Unified lifecycle model
- [Bridge Response Improvements](bridge-response-improvements.md) - Response pipeline
