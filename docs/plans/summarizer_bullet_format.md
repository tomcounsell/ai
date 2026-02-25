---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-02-25
tracking: https://github.com/tomcounsell/ai/issues/177
---

# Summarizer: Bullet-Point Format with Markdown Links

## Problem

SDLC completion summaries are dense single-paragraph walls of text. Hard to scan in Telegram, especially when multiple jobs complete back-to-back. All Telegram messages are sent as plain text — no bold, italic, inline code, or clickable links. The summarizer operates blind — no idea what was originally requested. And there's no visibility into which pipeline stages actually ran.

Additionally, `RedisJob` and `SessionLog` represent the same logical thing — a unit of work triggered by a message — but are split across two models with duplicated fields (`session_id`, `project_key`, `status`, `chat_id`, `sender`, `started_at`, `work_item_slug`, `classification_type`). RedisJob is ephemeral (deleted after completion), SessionLog is the historical record. This split creates a handoff gap and makes it impossible for the summarizer to access lifecycle data cleanly.

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

**Size:** Large

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (scope alignment on model merge + format spec)
- Review rounds: 1-2

The model unification is the heavy lift. The summarizer rewrite and markdown enablement are straightforward once the single model exists.

## Prerequisites

No prerequisites — this work builds on existing RedisJob model, SessionLog model, and summarizer infrastructure. Note: PR #179 (session tagging) has been merged — `SessionLog.tags` and `tools/session_tags.py` are now active. The unified model must carry forward the tags field and tagging integration.

## Solution

### Key Elements

- **Unified AgentSession model**: Merge `RedisJob` and `SessionLog` into a single `AgentSession` model that tracks the full lifecycle from enqueue through completion. Queue-specific fields (`priority`, `message_text`, `auto_continue_count`) and session-specific fields (`turn_count`, `tool_call_count`, `log_path`, `summary`, `tags`) live together. `status` field expands to cover both: `pending → running → active → dormant → completed → failed`.
- **Job history tracking**: `history` ListField on AgentSession — append-only log of lifecycle events (user input, classification, stage transitions, summary) capped at 20 entries
- **Link accumulator**: `issue_url`, `plan_url`, `pr_url` fields on AgentSession, set as each SDLC stage completes
- **Summarizer context enrichment**: Summarizer reads from the single model — original message, history, and links — to produce structured output
- **Adaptive format**: Prose for casual/conversational replies; bullet points whenever there's a list of things (changes, test results, steps taken); full structured format (emoji + stage line + bullets + links) for SDLC completions
- **Telegram markdown**: Basic `parse_mode='md'` on all `send_message` calls — bold, inline code, `[text](url)` links only. No MarkdownV2. Plain-text fallback on parse errors.

### Flow

**Message arrives** → AgentSession created (status=`pending`) → history records `[user]` → enqueued → worker picks up (status=`running`) → classification adds `[classify]` → SDLC stages append `[stage]` entries → links accumulate → agent completes → summarizer reads AgentSession → renders structured format → Telegram sends with markdown → AgentSession transitions to `completed` → auto-tagger applies tags

### Technical Approach

**Phase A: Model Unification**
- Create `models/agent_session.py` with unified `AgentSession` model containing all fields from both `RedisJob` and `SessionLog`, plus new `history`, `issue_url`, `plan_url`, `pr_url` fields
- Status values: `pending` | `running` | `active` | `dormant` | `completed` | `failed`
- Add helper methods: `append_history(role, text)`, `set_link(kind, url)`, `get_stage_progress()`, `get_links()`
- Migrate `agent/job_queue.py` to use AgentSession instead of RedisJob — `enqueue_job()`, `dequeue_job()`, worker loop, health checks all operate on AgentSession
- Migrate `bridge/session_transcript.py` to use AgentSession instead of SessionLog — `start_transcript()`, `complete_transcript()`, `save_session_snapshot()`
- Migrate `tools/session_tags.py` to reference AgentSession (preserving the tagging system from PR #179)
- Update all imports and call sites across the codebase
- Delete `RedisJob` from `agent/job_queue.py` and `SessionLog` from `models/session_log.py`
- Keep the `Job` wrapper class for the worker interface, backed by AgentSession

**Phase B: Summarizer Rewrite**
- Rewrite `SUMMARIZER_SYSTEM_PROMPT` with adaptive format: prose for casual replies, bullets for any list of things, full structured format for SDLC (emoji + stage line + bullets + links)
- Change `summarize_response()` to accept AgentSession for context enrichment
- Build `_render_stage_progress(history)` and `_render_link_footer(links)` renderers
- Remove `_ensure_github_link_footer()` URL parsing

**Phase C: Telegram Markdown**
- Create `bridge/markdown.py` with `escape_markdown(text)` utility
- Enable `parse_mode='md'` on all `send_message` calls
- Add try/except fallback to plain text on parse errors

## Rabbit Holes

- **Rich markdown formatting beyond basics**: Stick to bold, inline code, and links. No tables or headers — Telegram renders them poorly.
- **Retroactive history for in-flight sessions**: Don't backfill. New sessions only.
- **MarkdownV2 parse mode**: Too aggressive escaping. Stick with basic `md`.
- **Real-time stage progress updates**: Don't edit sent messages. Format the final summary correctly.
- **Redis data migration**: Don't try to migrate existing RedisJob/SessionLog data. Old sessions stay as-is in Redis until they age out (90-day TTL). New sessions use AgentSession from day one.

## Risks

### Risk 1: Model merge breaks the job queue
**Impact:** Jobs stop processing, bridge goes down
**Mitigation:** Phase A is the critical path — do it first with comprehensive tests. The `Job` wrapper class isolates the worker loop from the underlying model. Keep the same queue semantics (FILO, per-project sequential). Test enqueue/dequeue/health-check before touching anything else.

### Risk 2: Markdown escaping breaks message delivery
**Impact:** Messages fail to send on unescaped special characters
**Mitigation:** Try/except with plain text fallback on every send. `escape_markdown()` utility for known edge cases.

### Risk 3: Popoto ListField serialization
**Impact:** History entries lost or corrupted
**Mitigation:** Sequential per-project processing (no concurrent writes). Cap at 20 entries. Graceful degradation — if history is empty/broken, omit stage line.

### Risk 4: Import graph changes break startup
**Impact:** Bridge fails to start after refactor
**Mitigation:** Map all imports before starting. Use the `Job` wrapper as the stable interface. Test bridge startup as part of validation.

### Risk 5: Session tagging integration breaks
**Impact:** Auto-tagging from PR #179 stops working after model rename
**Mitigation:** `tools/session_tags.py` references `SessionLog` — update all references to `AgentSession`. The `tags` field carries over directly. Run tagging tests as part of validation.

## No-Gos (Out of Scope)

- Editing sent messages to update progress in real-time
- MarkdownV2 parse mode (too fragile)
- Structured formatting for non-SDLC conversational replies (keep those simple)
- Custom formatting per project or per chat
- Inline images or media in summaries
- Redis data migration for existing sessions/jobs
- **Session tagging logic** (#162, PR #179): The tagging system is already merged and working. This plan carries the `tags` field forward into the unified model and updates `tools/session_tags.py` imports, but does NOT modify tagging logic itself.

## Update System

No update system changes required — this feature modifies bridge-internal models and behavior. The update script pulls code and restarts the bridge, which is sufficient. Existing Redis keys for old `RedisJob` and `SessionLog` instances will be orphaned but harmless — they'll age out via TTL or can be cleaned up manually.

## Agent Integration

The agent needs a tool to record SDLC stage transitions and set links on the AgentSession. This is a simple CLI tool the agent calls via Bash — one line per stage update.

- Create `tools/session_progress.py` — CLI tool that updates AgentSession fields in Redis via Popoto
- Usage: `python -m tools.session_progress --session-id $CLAUDE_CODE_TASK_LIST_ID --stage BUILD --status completed`
- Link setting: `python -m tools.session_progress --session-id $CLAUDE_CODE_TASK_LIST_ID --pr-url https://github.com/...`
- The tool looks up the AgentSession by session_id, calls `append_history("stage", "BUILD ☑")` and/or `set_link("pr", url)`, then saves
- SDLC sub-skills (`.claude/commands/do-build.md`, etc.) should include a call to this tool at each stage boundary
- No MCP server needed — plain Bash invocation like all other tools in `tools/`

## Documentation

- [ ] Create `docs/features/summarizer-format.md` describing the bullet-point format, emoji vocabulary, stage progress rendering, and link accumulation
- [ ] Create `docs/features/agent-session-model.md` describing the unified model, its fields, status lifecycle, and relationship to the job queue and session transcripts
- [ ] Update `docs/features/README.md` index table with new entries
- [ ] Update `docs/features/session-transcripts.md` to reference AgentSession instead of SessionLog
- [ ] Update `docs/features/session-tagging.md` to reference AgentSession instead of SessionLog
- [ ] Update inline docstrings in `models/agent_session.py`, `bridge/summarizer.py`, `agent/job_queue.py`

## Success Criteria

- [ ] `AgentSession` model exists with all fields from RedisJob + SessionLog + new history/link fields
- [ ] `RedisJob` class removed from `agent/job_queue.py`
- [ ] `SessionLog` class removed from `models/session_log.py`
- [ ] All imports updated — no references to `RedisJob` or `SessionLog` in Python code
- [ ] Job queue (enqueue/dequeue/worker/health-check) works on AgentSession
- [ ] Session transcripts (start/complete/snapshot) work on AgentSession
- [ ] Session tagging (`tools/session_tags.py`) works on AgentSession
- [ ] `append_history()` and `set_link()` helpers work, cap history at 20 entries
- [ ] Summarizer accepts AgentSession context and produces bullet-point format for SDLC
- [ ] Stage progress line renders correctly from history
- [ ] Link footer renders progressively from session fields
- [ ] `_ensure_github_link_footer()` removed
- [ ] All Telegram `send_message` calls use `parse_mode='md'`
- [ ] Markdown escape fallback prevents send failures
- [ ] Non-SDLC summaries still work (prose for conversational, bullets for lists)
- [ ] `tools/session_progress.py` CLI tool updates AgentSession stage/links from Bash
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model-unification)**
  - Name: model-builder
  - Role: Create unified AgentSession model, migrate job queue, session transcripts, and session tags
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
  - Role: Verify end-to-end flow from session creation through formatted Telegram output
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

### 1. Create unified AgentSession model
- **Task ID**: build-model
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `models/agent_session.py` with `AgentSession` combining all fields from RedisJob and SessionLog
- Fields from RedisJob: `job_id` (AutoKeyField), `project_key`, `status`, `priority`, `created_at`, `session_id`, `working_dir`, `message_text`, `sender_name`, `sender_id`, `chat_id`, `message_id`, `chat_title`, `revival_context`, `workflow_id`, `work_item_slug`, `task_list_id`, `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `chat_id_for_enrichment`, `classification_type`, `auto_continue_count`, `started_at`
- Fields from SessionLog: `completed_at`, `turn_count`, `tool_call_count`, `log_path`, `summary`, `branch_name`, `tags`, `classification_confidence`, `last_activity`
- New fields: `history` (ListField), `issue_url` (Field), `plan_url` (Field), `pr_url` (Field)
- Status values: `pending` | `running` | `active` | `dormant` | `completed` | `failed`
- Add `append_history(role, text)`, `set_link(kind, url)`, `get_stage_progress()`, `get_links()`, `cleanup_expired()` methods
- Add `sender` property that returns `sender_name` (reconcile the naming difference)

### 2. Migrate job queue to AgentSession
- **Task ID**: build-job-migration
- **Depends On**: build-model
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/job_queue.py`: replace all `RedisJob` references with `AgentSession`
- Update `enqueue_job()`, `dequeue_job()`, `_process_job()`, health check functions
- Keep `Job` wrapper class, backed by AgentSession instead of RedisJob
- Update all imports across the codebase that reference `RedisJob`
- Delete `RedisJob` class

### 3. Migrate session transcripts and tags to AgentSession
- **Task ID**: build-session-migration
- **Depends On**: build-model
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with task 2 — both depend on model but touch different files)
- Update `bridge/session_transcript.py`: replace `SessionLog` references with `AgentSession`
- Update `models/__init__.py` exports
- Update `monitoring/session_watchdog.py`
- Update `tools/session_tags.py` to reference AgentSession (carrying forward PR #179 tagging)
- Update test files that reference SessionLog
- Delete `SessionLog` from `models/session_log.py`

### 4. Rewrite summarizer with bullet-point format
- **Task ID**: build-summarizer
- **Depends On**: build-job-migration
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `SUMMARIZER_SYSTEM_PROMPT`: prose for casual, bullets for lists, full structured format for SDLC. Emoji vocabulary: ✅ ⏳ ❓ ⚠️ ❌
- Change `summarize_response()` to accept optional `session: AgentSession = None`
- Build `_render_stage_progress(history)` producing `☑ ISSUE → ☑ PLAN → ...` line
- Build `_render_link_footer(links)` producing `Issue #N | Plan | PR #N` with markdown links
- Remove `_ensure_github_link_footer()`
- Update `_build_summary_prompt()` to include session context

### 5. Enable Telegram markdown parse mode
- **Task ID**: build-telegram-md
- **Depends On**: none
- **Assigned To**: telegram-md-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/markdown.py` with `escape_markdown(text)` utility
- Update `send_response_with_files()` in `bridge/response.py` to use `parse_mode='md'`
- Add try/except fallback to plain text on parse errors
- Update other `send_message` calls in `bridge/telegram_bridge.py`

### 6. Create session progress CLI tool
- **Task ID**: build-progress-tool
- **Depends On**: build-model
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/session_progress.py` — CLI tool that updates AgentSession via Popoto
- Accept `--session-id`, `--stage` (with `--status completed|failed|in_progress`), `--issue-url`, `--plan-url`, `--pr-url`
- Look up AgentSession by session_id, call `append_history()` and/or `set_link()`, save
- Print confirmation line to stdout so the agent sees it worked
- Handle missing session gracefully (print warning, exit 0)

### 7. Wire session context through response path
- **Task ID**: build-wiring
- **Depends On**: build-job-migration, build-summarizer, build-telegram-md, build-progress-tool
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Pass AgentSession to `summarize_response()` from the worker loop
- Append history at key points: enqueue (`[user]`), classification (`[classify]`), summary (`[summary]`)
- Add `session_progress.py` calls to SDLC sub-skill docs (`.claude/commands/do-build.md`, etc.)

### 8. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-wiring, build-session-migration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify AgentSession fields serialize/deserialize correctly
- Verify job queue enqueue/dequeue/worker works
- Verify session transcript start/complete works
- Verify session tagging still works
- Verify `append_history()` caps at 20 entries
- Verify stage progress and link footer renderers
- Verify markdown escape and fallback
- Verify `tools/session_progress.py` updates session from CLI
- Verify no remaining references to `RedisJob` or `SessionLog` in Python code
- Run full test suite

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/summarizer-format.md`
- Create `docs/features/agent-session-model.md`
- Update `docs/features/session-transcripts.md`
- Update `docs/features/session-tagging.md`
- Add entries to `docs/features/README.md` index table

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -c "from models.agent_session import AgentSession; print('model loads')"` - Unified model exists
- `python -c "from agent.job_queue import RedisJob"` - Should FAIL (RedisJob removed)
- `python -c "from models.session_log import SessionLog"` - Should FAIL (SessionLog removed)
- `python -m tools.session_progress --session-id test --stage BUILD --status completed` - CLI tool works
- `ruff check models/ bridge/ agent/ tools/` - No lint errors
- `black --check models/ bridge/ agent/ tools/` - Formatting OK
- `pytest tests/ -x` - All tests pass
