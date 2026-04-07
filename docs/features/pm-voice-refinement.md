# PM Voice Refinement

Naturalizes SDLC language, adds crash message variety, sentence-aware truncation, and milestone-selective emoji to make the PM persona's Telegram output sound human rather than robotic.

## Problem Addressed

The PM persona leaked implementation details into stakeholder-facing messages: raw SDLC stage labels ("PLAN stage complete"), a single hardcoded crash string repeated verbatim, mid-sentence truncation, developer metrics in output, and completion emoji on every message regardless of significance.

## Changes

### SDLC Stage Naturalization

The `SUMMARIZER_SYSTEM_PROMPT` in `bridge/summarizer.py` instructs the LLM to translate raw SDLC stage labels to natural language: PLAN becomes "planning", BUILD becomes "building", TEST becomes "testing", REVIEW becomes "reviewing", DOCS becomes "documenting", MERGE becomes "merging". The term "SDLC" itself remains acceptable as a process reference. This is a prompt-only change -- the LLM handles the translation at summarization time.

### Crash Message Pool

`agent/sdk_client.py` defines `CRASH_MESSAGE_POOL`, a list of five varied crash fallback messages. Each includes next-step language ("retry", "try again", "re-trigger", "re-send"). The `_get_crash_message()` function selects randomly from the pool while tracking `_last_crash_message` at module level to prevent consecutive repeats. If the pool is somehow empty, a hardcoded default is returned.

### Question Prefix

The question prefix changed from `? ` to `>> ` for better visual distinction in Telegram. The `_normalize_question_prefix()` function provides backward compatibility by converting any legacy `? ` prefixes to `>> `. Both `SUMMARIZER_SYSTEM_PROMPT` and `_parse_summary_and_questions()` use the new format.

### Link Footer Standardization

The summarizer prompt now explicitly instructs the LLM to use short-form references only in bullet text (e.g., "PR #N", "issue #N") and never include full URLs in bullets. Full URL rendering is handled by the existing `_linkify_references()` post-processor.

### Sentence-Aware Truncation

`bridge/response.py` adds `_truncate_at_sentence_boundary()` to replace the raw `text[:4093] + "..."` slice at Telegram's 4096-character limit. The function searches the last 500 characters of the allowed window for sentence-ending punctuation (`.`, `!`, `?`) followed by whitespace or end-of-string, and cuts there. If no sentence boundary is found, it falls back to the ellipsis truncation.

### Developer Metrics Suppression

The summarizer prompt instructs the LLM to avoid line counts, file counts, addition/deletion counts, and exact test pass/fail numbers. Instead it should use outcome language: "shipped and tested", "all tests passing", "reviewed and approved".

### Dual-Personality Guard

The `pm_bypass` path in `response.py` has clarified documentation confirming it prevents sending both PM self-messages and a summarized version of the same content. The guard blocks both summarization and text sending when the PM has already delivered its own messages.

### Milestone-Selective Emoji

`_get_status_emoji()` in `bridge/summarizer.py` now reserves the completion emoji for true milestones. The logic:

| Condition | Emoji |
|-----------|-------|
| Session failed | `"X"` |
| Completed with PR link (milestone) | checkmark |
| Completed without PR (routine) | empty string |
| Routine completion (running session) | empty string |
| In-progress work | hourglass |
| No session context | checkmark if completion, hourglass otherwise |

Routine completions produce no emoji prefix, reducing noise. Only merged PRs and closed issues get the completion checkmark.

## Files Modified

- `bridge/summarizer.py` -- Prompt updates (naturalization, question prefix, link format, metrics suppression), `_get_status_emoji()` milestone logic, `_normalize_question_prefix()`, `_parse_summary_and_questions()` updates
- `bridge/response.py` -- `_truncate_at_sentence_boundary()`, dual-personality guard documentation
- `agent/sdk_client.py` -- `CRASH_MESSAGE_POOL`, `_get_crash_message()`
- `tests/unit/test_summarizer.py` -- Updated and new tests for all changes

## Related

- [Summarizer Format](summarizer-format.md) -- Output format specification (updated by this feature)
- [Bridge Response Improvements](bridge-response-improvements.md) -- Response pipeline
- [Chat Dev Session Architecture](pm-dev-session-architecture.md) -- PM/Dev session split
- Issue [#540](https://github.com/tomcounsell/ai/issues/540) -- Tracking issue
- PR [#548](https://github.com/tomcounsell/ai/pull/548) -- Implementation
