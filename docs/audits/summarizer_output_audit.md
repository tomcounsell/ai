# Summarizer Output Audit: Instructions vs Actual Telegram Messages

**Date:** 2026-02-26
**Source data:** 50 most recent messages from Dev: Valor chat
**Compared against:** `bridge/summarizer.py` (SUMMARIZER_SYSTEM_PROMPT), `docs/features/summarizer-format.md`

---

## Executive Summary

The summarizer is **partially working**. The structured bullet-point format with stage progress lines is being generated correctly for some SDLC work, but most messages bypass the summarizer entirely — either because they're under the 500-char threshold or because the raw Claude output is sent without summarization. The result: a mix of clean formatted summaries and verbose, process-heavy dumps that violate nearly every rule in the prompt.

**Grade: C+**

---

## What the Instructions Say

From `SUMMARIZER_SYSTEM_PROMPT`:
1. Simple completions → "Done ✅" or "Yes"/"No"
2. Conversational replies → condense while preserving tone, prose format
3. Questions → preserve exactly
4. SDLC work → 2-4 bullet points ONLY, no emoji prefix (added separately), no URLs (added from session data)
5. Status updates → 2-4 bullet points
6. General: lead with outcome not process, direct, no preamble, no filler
7. ⚠️ only for genuinely external blockers

From `config/SOUL.md`:
- Direct, concise, professional
- State what I did, what I need, or what I found. No preamble.
- Short messages. Longer explanations only when requested.

---

## Actual Message Analysis

### Category 1: VERBOSE PROCESS DUMPS (Primary Issue)

These messages expose intermediate steps, tool invocations, and reasoning — exactly what SOUL.md says to never do.

**Example 1** (10:44, PR 180 review):
```
Let me check what the review produced:
The skill ran but didn't post a review. Let me run the review manually with full analysis.
26 files changed. Let me get the actual diff to review properly:
Now let me read the remaining key files - the new model and the summarizer diff:
I've now read the full diff. Let me post the review to GitHub:
Can't self-approve. Let me post as a comment instead:
PR #180 review posted.
```

**Violation:** Shows 6 intermediate steps before the actual outcome. Should be:
```
PR #180 review posted — ready to merge ✅
```

**Example 2** (10:49, test writing):
```
Let me read the model and summarizer to build precise tests against the actual implementation:
Now let me read the `_compose_structured_summary` function:
Now I have everything. Let me write the comprehensive test file:
55/55 passing. Now commit and push:
**55 lifecycle tests added** — all passing. Committed `b6e4ba63`.
```

**Violation:** 4 lines of process before the outcome. Should be just the last 2 lines.

**Example 3** (10:52, fixing RedisJob refs):
```
Two tasks. Let me handle both.
First, let me find the exact RedisJob reference and send the test message in parallel:
Good. Now let me send a live markdown test message and fix the RedisJob references:
Now fix the RedisJob references. Line 411 is the actual code issue...
Now I can see the remaining RedisJob references. Line 411 is passing `RedisJob`...
```

**Violation:** Entire message is process narration.

**Count in sample:** ~15 of 50 messages (30%) are verbose process dumps

### Category 2: CORRECTLY FORMATTED SDLC SUMMARIES

Some messages follow the format well.

**Example 1** (11:04, doc cascade):
```
## Documentation Cascade Complete
**Change**: PR #180 — Unified AgentSession model + bullet-point summarizer
**Documents updated** (13 files, commit `835e897c`):
- `docs/features/session-transcripts.md` — SessionLog → AgentSession...
```

**Assessment:** Clean, outcome-first, with artifacts preserved. ✅
**Minor issue:** Uses markdown headers (##) which don't render in Telegram basic markdown.

**Example 2** (11:44, issue creation):
```
Issue created: https://github.com/tomcounsell/ai/issues/183
```

**Assessment:** Perfect conciseness. ✅

**Example 3** (16:05, close PR):
```
Done. PR #184 closed, branch `session/sdlc_user_hooks` deleted (local + remote), issue #183 reopened for rebuild after #178.
```

**Assessment:** Direct, outcome-first, one sentence. ✅

**Count in sample:** ~10 of 50 messages (20%) are well-formatted

### Category 3: MIXED — GOOD CONTENT, BAD FORMAT

**Example** (16:36, tech debt answer):
```
No, they weren't patched. The review found 2 tech debt items and 1 nit, but since none were blockers...

The items that were left unaddressed:
1. **Tech Debt**: Hardcoded `MAX_AUTO_CONTINUES` (3)...
2. **Tech Debt**: `_enqueue_continuation()` only used by...
3. **Nit**: `TestMaxAutoContiuesConstants` typo...
```

**Assessment:** Content is good and direct. Format is clean. Answers the question well. ✅ (conversational reply done right)

**Count in sample:** ~8 of 50 messages (16%)

### Category 4: UNSUMMARIZED RAW OUTPUT

Several messages appear to be the raw Claude output sent directly, not through the summarizer at all. These tend to start with "Let me..." or contain full reasoning chains.

**Example** (15:57, scope overlap):
```
Let me check issue 178 to understand the scope overlap concern.
Now I see the overlap. Let me also quickly check what PR 184 actually implements vs what 178 covers.
Now I can see the overlap clearly. Let me send a response to Tom.
Yes, there's meaningful overlap. Here's my assessment:
**Issue 178** (stage-aware auto-continue) owns the core question...
```

**Diagnosis:** The 500-char summarize threshold may have been exceeded but the message starts with process steps, suggesting it was either:
- Not passed through the summarizer, or
- The summarizer received the full output including process steps and faithfully preserved them

**Count in sample:** ~12 of 50 messages (24%)

### Category 5: MISSING STRUCTURED FORMAT

SDLC work that should have stage progress lines and link footers but doesn't.

**Example** (15:55, BUILD complete):
```
The BUILD stage completed successfully. PR #184 is open and ready for human review/merge...
**What was built:**
- 3 user-level hook scripts...
- Settings merger in `hardlinks.py`...
- 42 unit tests passing
- Feature documentation created
```

**Expected format:**
```
✅ SDLC user-level hooks
☑ ISSUE → ☑ PLAN → ☑ BUILD → ☑ TEST → ☑ REVIEW → ☑ DOCS
• 3 hook scripts, settings merger, 42 tests passing
• Feature docs created
Issue #183 | Plan | PR #184
```

**Diagnosis:** The `_compose_structured_summary()` function exists and works, but the session context may not be getting passed correctly, or the AgentSession doesn't have stage progress populated at the time of summarization.

**Count in sample:** ~5 of 50 messages (10%)

---

## Root Cause Analysis

### 1. Process narration comes from Claude, not the summarizer

The verbose "Let me check...", "Now let me read..." messages are Claude's raw output. The summarizer prompt says "lead with outcome not process" — but if the raw output is under 500 chars or if the bridge sends it without summarization, those process steps pass through verbatim.

**Hypothesis:** Many messages are being sent as `send_to_chat` callbacks during intermediate steps (not just the final output). Each callback is short enough to skip summarization.

### 2. Stage progress lines are missing from most SDLC outputs

The `_render_stage_progress()` function requires the `AgentSession` to be passed to `summarize_response()`. If the session isn't passed or doesn't have history populated yet, no stage line is rendered.

**Hypothesis:** The `session` parameter may be None or stale when `summarize_response()` is called.

### 3. Link footer is rarely visible

Same root cause as #2 — requires `session.get_links()` to return data, which depends on `set_link()` having been called during the SDLC pipeline.

### 4. The 500-char threshold may be too high

Short but verbose messages (like "Let me check... Now let me read... Done.") slip under 500 chars and bypass summarization entirely.

---

## Discrepancy Summary Table

| Summarizer Instruction | Compliance | Issue |
|---|---|---|
| "Lead with outcome, not process" | ~30% | Most messages start with "Let me..." process narration |
| "2-4 bullet points for SDLC work" | ~20% | Bullets appear sometimes, but often prose dumps instead |
| "Simple completions → Done ✅" | ~50% | Works when it triggers, but many completions get verbose treatment |
| "No preamble, no filler" | ~25% | "Let me check...", "Now let me read..." is preamble |
| "Stage progress line" | ~10% | Rarely rendered — session context likely not passed |
| "Link footer" | ~10% | Same issue as stage progress |
| "⚠️ only for external blockers" | ✅ 100% | No misuse observed |
| "Preserve commit hashes" | ✅ 90% | Usually preserved when present |
| "Preserve URLs" | ✅ 90% | GitHub URLs consistently preserved |
| "Direct, concise tone" | ~40% | Good when summarizer runs, poor when bypassed |

---

## Recommendations

### Quick Wins
1. **Lower SUMMARIZE_THRESHOLD** from 500 to 200 — catch more verbose short messages
2. **Pass `session` to ALL `summarize_response()` calls** — ensure stage progress and links are always available
3. **Verify `append_history("stage", ...)` timing** — stage data must be written before the summarizer reads it

### Medium Effort
4. **Add a "process stripping" pre-pass** — before summarization, strip lines starting with "Let me...", "Now let me...", "Good.", "I'll...", etc. These are Claude's thinking-out-loud and should never reach Telegram
5. **Audit the bridge's send_to_chat callback** — determine if intermediate outputs are being sent or only final outputs
6. **Add integration test** — feed known verbose output through summarizer and assert the output matches format rules

### Larger Improvements
7. **Structured output from Claude** — instead of summarizing prose, have Claude output JSON with {outcome, bullets, artifacts} and render it in the bridge. This eliminates the summarizer entirely for the happy path.
8. **Two-phase delivery** — send a quick "⏳ Working on it..." acknowledgment, then replace with the final structured summary when done. This prevents intermediate verbose outputs from reaching the chat.

---

## Conclusion

The summarizer code and instructions are well-designed. The gap is in **when** and **how** they're invoked. Most discrepancies stem from:
1. Raw Claude output bypassing the summarizer (threshold too high or session callback structure)
2. Missing session context preventing structured format rendering
3. No pre-processing to strip Claude's process narration before summarization

The fixes are tractable — the infrastructure exists, it just needs tighter integration with the bridge's message delivery path.
