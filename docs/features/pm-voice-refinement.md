# PM Voice Refinement

The PM persona's Telegram output reads as natural human prose: the agent's own text reaches the human verbatim, truncation lands on sentence boundaries, question prefixes are normalized, issue and PR references are linkified, and the completion emoji is reserved for milestones.

## Behavior

### Verbatim Pass-Through

`bridge/message_drafter.py` performs validation and structural composition, not summarization. There is no LLM rewriting step: the agent's text is stripped of process narration (`_strip_process_narration()`), validated against the per-medium wire format, and composed with the emoji prefix, SDLC stage line, and link footer by `_compose_structured_draft()`. A response too long for the medium is written out and attached as a `.txt` file by `_write_full_output_file()`, never shortened.

### Question Prefix

The question prefix is `>> ` for better visual distinction in Telegram. `_normalize_question_prefix()` converts any legacy `? ` prefixes to `>> `, and `_parse_draft_and_questions()` splits the composed draft into bullet text and a questions block.

### Link Footer Standardization

Bullet text carries short-form references only (e.g. "PR #N", "issue #N"). Full URL rendering is handled by the `_linkify_references()` post-processor, which resolves those references against the session's project before the draft is sent.

### Sentence-Aware Truncation

`bridge/message_drafter.py` provides `_truncate_at_sentence_boundary()` instead of a raw slice at Telegram's 4096-character limit. The function searches the last 500 characters of the allowed window for sentence-ending punctuation (`.`, `!`, `?`) followed by whitespace or end-of-string, and cuts there. If no sentence boundary is found, it falls back to ellipsis truncation.

### Dual-Personality Guard

The `pm_bypass` path in `bridge/telegram_bridge.py` prevents sending both PM self-messages and a drafted version of the same content. When the PM session (or its parent PM session in SDLC flows) has already delivered messages via `tools/send_message.py`, the drafter is skipped entirely. File attachments still send — they would otherwise be lost.

### Milestone-Selective Emoji

`_get_status_emoji()` in `bridge/message_drafter.py` reserves the completion emoji for true milestones. The logic:

| Condition | Emoji |
|-----------|-------|
| Session failed | `"X"` |
| Completed with PR link (milestone) | checkmark |
| Completed without PR (routine) | empty string |
| Routine completion (running session) | empty string |
| In-progress work | hourglass |
| No session context | checkmark if completion, hourglass otherwise |

Routine completions produce no emoji prefix. Only merged PRs and closed issues get the completion checkmark.

## Key Files

- `bridge/message_drafter.py` — `_strip_process_narration()`, `_truncate_at_sentence_boundary()`, `_normalize_question_prefix()`, `_parse_draft_and_questions()`, `_linkify_references()`, `_get_status_emoji()`, `_write_full_output_file()`, `_compose_structured_draft()`
- `bridge/telegram_bridge.py` — `pm_bypass` dual-personality guard

## Related

- [Message Drafter](message-drafter.md) — output format specification
- [Bridge Response Improvements](bridge-response-improvements.md) — response pipeline
- [Eng Session Architecture](eng-session-architecture.md) — PM/Dev session split
