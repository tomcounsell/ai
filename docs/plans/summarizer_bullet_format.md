---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-02-25
tracking:
---

# Summarizer: Bullet-Point Format with Markdown Links

## Problem

SDLC completion summaries are dense single-paragraph walls of text. Hard to scan in Telegram, especially when multiple jobs complete back-to-back. All Telegram messages are sent as plain text — no bold, italic, inline code, or clickable links. The summarizer operates blind — no idea what was originally requested. And there's no visibility into which pipeline stages actually ran.

**Current behavior:**
> Done ✅ PR #176 fixes infinite false-positive loop after gh pr merge --squash with two-layer defense: tracks modified_on_branch at write time to distinguish merged session code from main edits, plus bash detection of gh pr merge resets code_modified=false. 10 new tests, all 63 passing. Commit 8b60821c.
> https://github.com/tomcounsell/ai/pull/176

**Desired outcome:**
```
✅ Infinite false-positive loop after squash merge
☑ ISSUE → ☑ PLAN → ☑ BUILD → ☑ TEST → ☑ REVIEW → ☑ DOCS
• Two-layer defense: branch tracking + merge detection
• 10 new tests, 63 passing
Issue #168 | Plan | PR #176
```

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on format spec)
- Review rounds: 1

## Prerequisites

No prerequisites — this work builds on existing RedisJob model and summarizer infrastructure.

## Solution

### Key Elements

- **RedisJob history tracking**: Append-only log of job lifecycle events (user input, classification, stage transitions, summary) capped at 20 entries
- **RedisJob link accumulator**: Store `issue_url`, `plan_url`, `pr_url` on the job as each SDLC stage completes, eliminating URL parsing from agent output
- **Summarizer context enrichment**: Pass original message text, job links, and history to the summarizer so it can name the task, build the stage progress line, and format links
- **Bullet-point format**: Replace dense paragraphs with emoji-first, stage-progress, bullet-point summaries
- **Telegram markdown**: Enable `parse_mode='md'` on all `send_message` calls with escape handling for raw agent output

### Flow

**Job enqueued** → history records `[user]` entry → classification adds `[classify]` entry → SDLC stages append `[stage]` entries → links accumulate on job fields → summarizer reads history + links + original message → renders structured bullet-point format → Telegram sends with markdown parse mode

### Technical Approach

- Add `history` (ListField), `issue_url`, `plan_url`, `pr_url` (Field) to RedisJob model
- Add helper methods on RedisJob/Job: `append_history(role, text)` and `set_link(kind, url)`
- Modify SDLC sub-skills to call `set_link()` and `append_history("stage", ...)` as they complete
- Rewrite `SUMMARIZER_SYSTEM_PROMPT` with bullet-point format guidance, emoji vocabulary, and SDLC-aware patterns
- Change `summarize_response()` signature to accept optional `job` parameter for context enrichment
- Build stage progress line renderer from history (last occurrence of each stage type)
- Build link footer renderer from job fields (progressive accumulation)
- Remove `_ensure_github_link_footer()` URL parsing — links come from job fields
- Enable markdown parse mode in `bridge/response.py` `send_response_with_files()` and other send points
- Add markdown escape utility for raw agent output that bypasses summarizer

## Rabbit Holes

- **Rich markdown formatting beyond basics**: Stick to bold, inline code, and links. Don't attempt tables, headers, or complex formatting that Telegram renders poorly.
- **Retroactive history for in-flight jobs**: Don't try to backfill history for jobs that started before this feature. New jobs only.
- **MarkdownV2 parse mode**: Telegram's MarkdownV2 has extremely aggressive escaping requirements. Stick with basic `md` (Markdown) parse mode which is simpler and sufficient.
- **Real-time stage progress updates**: Don't try to edit existing messages to show live progress. Just format the final summary correctly.

## Risks

### Risk 1: Markdown escaping breaks message delivery
**Impact:** Messages fail to send if they contain unescaped markdown special characters (e.g., `_`, `*`, `` ` ``, `[`)
**Mitigation:** Wrap all `send_message` calls with a try/except that falls back to plain text on markdown parse errors. Add an `escape_markdown()` utility that handles common characters.

### Risk 2: Popoto ListField serialization edge cases
**Impact:** History entries could be lost or corrupted if concurrent writes happen
**Mitigation:** RedisJob is processed sequentially per project (one worker per project_key). No concurrent write risk. Cap list at 20 entries with truncation on append.

### Risk 3: SDLC skills fail to record stage transitions
**Impact:** Stage progress line shows incomplete data
**Mitigation:** Graceful degradation — if no history exists, omit the stage progress line entirely. The summary still works without it.

## No-Gos (Out of Scope)

- Editing sent messages to update progress in real-time
- MarkdownV2 parse mode (too fragile)
- Structured formatting for non-SDLC conversational replies (keep those simple)
- Custom formatting per project or per chat
- Inline images or media in summaries
- **RedisJob/SessionLog unification**: These models share many fields and represent the same logical thing (a unit of work) at different lifecycle phases. Merging them is worthwhile but is a separate refactor — it touches the entire job queue, worker loop, session transcript system, and every call site. This plan adds `history`/links to RedisJob as-is. A future issue should unify the two models (RedisJob becomes a status on SessionLog, queue-specific fields like `priority`, `message_text`, `auto_continue_count` move there).

## Update System

No update system changes required — this feature modifies bridge-internal behavior (summarizer, response sending, job model). The update script pulls code and restarts the bridge, which is sufficient.

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent's tools and MCP servers are unaffected. The changes happen in the summarizer (post-processing agent output) and the Telegram send path (formatting). SDLC sub-skills are invoked by the agent but the stage recording happens in the bridge/job infrastructure that wraps them.

## Documentation

- [ ] Create `docs/features/summarizer-format.md` describing the bullet-point format, emoji vocabulary, stage progress rendering, and link accumulation
- [ ] Update `docs/features/README.md` index table with new entry
- [ ] Update inline docstrings in `bridge/summarizer.py` and `agent/job_queue.py`

## Success Criteria

- [ ] RedisJob has `history`, `issue_url`, `plan_url`, `pr_url` fields
- [ ] `append_history()` and `set_link()` helpers work and cap history at 20 entries
- [ ] Summarizer accepts job context and produces bullet-point format for SDLC completions
- [ ] Stage progress line renders correctly from history (last occurrence of each stage)
- [ ] Link footer renders progressively from job fields
- [ ] `_ensure_github_link_footer()` removed, links come from job fields
- [ ] All Telegram `send_message` calls use `parse_mode='md'`
- [ ] Markdown escape fallback prevents send failures on unescaped characters
- [ ] Non-SDLC summaries still work (conversational, Q&A, simple completions)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (job-model)**
  - Name: job-model-builder
  - Role: Add history/link fields to RedisJob, implement helper methods
  - Agent Type: builder
  - Resume: true

- **Builder (summarizer)**
  - Name: summarizer-builder
  - Role: Rewrite summarizer prompt, add context enrichment, build stage/link renderers
  - Agent Type: builder
  - Resume: true

- **Builder (telegram-markdown)**
  - Name: telegram-md-builder
  - Role: Enable markdown parse mode, add escape utility, update send paths
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow from job creation through formatted Telegram output
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update index
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation
- `validator` - Read-only verification
- `documentarian` - Documentation updates

## Step by Step Tasks

### 1. Add history and link fields to RedisJob
- **Task ID**: build-job-model
- **Depends On**: none
- **Assigned To**: job-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `history = ListField(null=True)` to RedisJob
- Add `issue_url = Field(null=True)`, `plan_url = Field(null=True)`, `pr_url = Field(null=True)` to RedisJob
- Add `append_history(role, text)` method to Job wrapper that appends `{"role": role, "text": text, "ts": time.time()}` and caps at 20 entries
- Add `set_link(kind, url)` method to Job wrapper that sets the appropriate `*_url` field
- Add `get_stage_progress()` method that reads history and returns last occurrence of each stage type
- Add `get_links()` method that returns dict of non-null link fields

### 2. Rewrite summarizer with bullet-point format
- **Task ID**: build-summarizer
- **Depends On**: build-job-model
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `SUMMARIZER_SYSTEM_PROMPT` with bullet-point format, emoji vocabulary (✅ ⏳ ❓ ⚠️ ❌), and SDLC-aware patterns
- Change `summarize_response()` to accept optional `job: Job = None` parameter
- When job is provided: pass original `message_text`, history, and links to the summarizer prompt
- Build `_render_stage_progress(history)` function that produces the `☑ ISSUE → ☑ PLAN → ...` line
- Build `_render_link_footer(links)` function that produces `Issue #N | Plan | PR #N` with markdown links
- Remove `_ensure_github_link_footer()` function
- Update `_build_summary_prompt()` to include job context when available

### 3. Enable Telegram markdown parse mode
- **Task ID**: build-telegram-md
- **Depends On**: none
- **Assigned To**: telegram-md-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/markdown.py` with `escape_markdown(text)` utility that escapes `_`, `*`, `` ` ``, `[` characters in raw text
- Update `send_response_with_files()` in `bridge/response.py` to use `parse_mode='md'` on `client.send_message()`
- Add try/except fallback: if markdown send fails, retry with plain text (no parse_mode)
- Update other `send_message` calls in `bridge/telegram_bridge.py` (queue position, revival prompts) to use `parse_mode='md'`

### 4. Wire job context through response path
- **Task ID**: build-wiring
- **Depends On**: build-job-model, build-summarizer, build-telegram-md
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Update the call site in `bridge/response.py` where `summarize_response()` is called to pass the job object
- Update `agent/job_queue.py` worker loop to pass job to response handler
- Ensure history entries are appended at key points: job creation (`[user]`), classification (`[classify]`), and summary (`[summary]`)
- Add stage recording hooks that SDLC skills can call (this may be a follow-up if skills need deeper integration)

### 5. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-wiring
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify RedisJob fields serialize/deserialize correctly
- Verify `append_history()` caps at 20 entries
- Verify `_render_stage_progress()` handles empty, partial, and full stage histories
- Verify `_render_link_footer()` handles progressive link accumulation
- Verify markdown escape handles common edge cases
- Verify markdown send fallback works on parse errors
- Run full test suite

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/summarizer-format.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -c "from agent.job_queue import RedisJob; j = RedisJob(project_key='test', message_text='test', chat_id='1', message_id=1); print('history' in dir(j))"` - RedisJob has history field
- `pytest tests/ -x` - All tests pass
- `ruff check bridge/summarizer.py bridge/response.py agent/job_queue.py` - No lint errors
- `black --check bridge/summarizer.py bridge/response.py agent/job_queue.py` - Formatting OK
- `python -c "from bridge.summarizer import _render_stage_progress; print('stage renderer exists')"` - Stage progress renderer exists

---

## Open Questions

1. **Stage recording depth**: The issue says SDLC sub-skills should write stage entries to job history. These skills run inside Claude Code as slash commands — they don't have direct access to the RedisJob. Should we (a) have the bridge record stages based on parsing skill invocations from agent output, (b) expose a tool/MCP endpoint for the agent to call, or (c) defer stage recording to a follow-up and ship the summarizer format improvements first?

2. **Markdown parse mode**: Telethon supports `'md'` (basic Markdown) and `'html'`. Basic Markdown in Telegram is limited — no nested formatting, links are `[text](url)` style. Is basic Markdown sufficient, or should we use HTML (`<b>`, `<a href>`, `<code>`) which is more reliable for complex formatting?

3. **Non-SDLC summary format**: For casual Q&A and conversational replies, should the summarizer keep the current prose style, or also switch to bullet points? The issue focuses on SDLC completions but mentions enabling markdown for "all messages."
