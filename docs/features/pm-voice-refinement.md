# PM Voice Refinement

The PM persona's Telegram output reads as natural human prose: SDLC stage labels are naturalized, crash messages vary, truncation lands on sentence boundaries, developer metrics stay out of stakeholder-facing text, and the completion emoji is reserved for milestones.

## Behavior

### SDLC Stage Naturalization

The `DRAFTER_SYSTEM_PROMPT` in `bridge/message_drafter.py` instructs the LLM to translate raw SDLC stage labels to natural language: PLAN becomes "planning", BUILD becomes "building", TEST becomes "testing", REVIEW becomes "reviewing", DOCS becomes "documenting", MERGE becomes "merging". The term "SDLC" itself remains acceptable as a process reference. This is a prompt-only behavior — the LLM handles the translation at draft time.

### Crash Message Pool

`agent/sdk_client.py` defines `CRASH_MESSAGE_POOL`, a list of five varied crash fallback messages. Each includes next-step language ("retry", "try again", "re-trigger", "re-send"). The `_get_crash_message()` function selects randomly from the pool while tracking `_last_crash_message` at module level to prevent consecutive repeats. If the pool is empty, a hardcoded default is returned.

### Question Prefix

The question prefix is `>> ` for better visual distinction in Telegram. The `_normalize_question_prefix()` function converts any legacy `? ` prefixes to `>> `. Both `DRAFTER_SYSTEM_PROMPT` and `_parse_summary_and_questions()` use the current format.

### Link Footer Standardization

The drafter prompt instructs the LLM to use short-form references only in bullet text (e.g., "PR #N", "issue #N") and never include full URLs in bullets. Full URL rendering is handled by the `_linkify_references()` post-processor.

### Sentence-Aware Truncation

`bridge/response.py` provides `_truncate_at_sentence_boundary()` to replace a raw `text[:4093] + "..."` slice at Telegram's 4096-character limit. The function searches the last 500 characters of the allowed window for sentence-ending punctuation (`.`, `!`, `?`) followed by whitespace or end-of-string, and cuts there. If no sentence boundary is found, it falls back to the ellipsis truncation.

### Developer Metrics Suppression

The drafter prompt instructs the LLM to avoid line counts, file counts, addition/deletion counts, and exact test pass/fail numbers. Instead it uses outcome language: "shipped and tested", "all tests passing", "reviewed and approved".

### Dual-Personality Guard

The `pm_bypass` path in `response.py` prevents sending both PM self-messages and a drafted version of the same content. The guard blocks both drafting and text sending when the PM has already delivered its own messages.

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

- `bridge/message_drafter.py` — prompt updates (naturalization, question prefix, link format, metrics suppression), `_get_status_emoji()` milestone logic, `_normalize_question_prefix()`, `_parse_summary_and_questions()`
- `bridge/response.py` — `_truncate_at_sentence_boundary()`, dual-personality guard
- `agent/sdk_client.py` — `CRASH_MESSAGE_POOL`, `_get_crash_message()`

## Related

- [Message Drafter](message-drafter.md) — output format specification
- [Bridge Response Improvements](bridge-response-improvements.md) — response pipeline
- [Eng Session Architecture](eng-session-architecture.md) — PM/Dev session split
