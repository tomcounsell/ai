---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-10
tracking: https://github.com/tomcounsell/ai/issues/70
---

# Bridge Message Handling Delays

## Problem

After running `/update`, the bridge takes ~5 minutes before it sees and handles new messages. Users perceive the system as dead during this window, lowering reliability across all deployed machines.

**Current behavior:**
- Heavy pre-processing (media/Ollama, YouTube transcription, Perplexity link summaries, reply chain fetching) runs synchronously inside the `@client.on(events.NewMessage)` handler before the job is enqueued
- With `sequential_updates=False` already shipped, concurrent handlers can still bottleneck on shared resources (Ollama model loading, Perplexity API)
- Restart scripts leave a 3-16 second gap where no process listens for messages, and `catch_up=False` means those messages are permanently lost
- Session lock cleanup uses SIGKILL directly, risking SQLite corruption

**Desired outcome:**
- Messages are enqueued within milliseconds of arrival; heavy enrichment happens later
- Zero messages lost during restarts
- Lock cleanup degrades gracefully (SIGTERM → wait → SIGKILL)

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on what "fast enough" means)
- Review rounds: 1 (code review of event handler restructure)

The core work is restructuring the event handler pipeline and fixing the restart gap — both well-understood patterns. The bottleneck is verifying the fix works across deployed machines.

## Prerequisites

No prerequisites — this work uses existing dependencies and infrastructure.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Bridge running | `./scripts/valor-service.sh status` | Need running bridge to test changes |

## Solution

### Key Elements

- **Deferred enrichment pipeline**: Move YouTube, Perplexity, media, and reply chain processing out of the event handler and into the job's execution phase
- **Catch-up on restart**: Enable Telethon's `catch_up=True` so messages sent during downtime are replayed
- **Per-chat message dedup**: Redis/Popoto-backed dedup to prevent duplicate processing from catch_up replays
- **Graceful lock cleanup**: SIGTERM first, wait, then SIGKILL as fallback

### Flow

**Message arrives** → handler extracts raw text + metadata (< 50ms) → enqueue to job queue → **👀 reaction sent** → job worker picks up → enrichment (media, YouTube, links, reply chain) → agent invocation → **response sent**

Today: Message arrives → handler enriches (seconds to minutes) → enqueue → agent → response

### Technical Approach

#### Fix 2: Deferred enrichment (Critical)

Move four blocking operations out of the event handler (`bridge/telegram_bridge.py:632-722`) into the job execution phase:

1. **Media processing** (lines 632-640) — `process_incoming_media()` calls Ollama vision model. On cold start, this loads 11B parameters from disk. Move to job worker.
2. **YouTube transcription** (lines 655-684) — Network-dependent, variable latency. Move to job worker.
3. **Link summarization** (lines 687-701) — Perplexity API call per URL. Move to job worker.
4. **Reply chain fetching** (lines 703-722) — Up to 20 Telegram API calls. Move to job worker.

The handler should only: extract raw text, determine routing, set 👀 reaction, and enqueue. All enrichment metadata (URLs found, media present, reply-to ID) gets passed as job payload for the worker to process.

This means the job payload needs new fields: `raw_media` (media reference for deferred processing), `youtube_urls` (list of URLs to transcribe), `non_youtube_urls` (list for Perplexity), `reply_to_msg_id` (for deferred chain fetch). The worker enriches the message text before passing it to the agent.

Enrichment logic lives in a new `bridge/enrichment.py` module (not `bridge/context.py`) since this will grow as new skills add more enrichment types.

#### Fix 3: Catchup hold-off

Catchup already runs after handler registration (confirmed at line 1162-1179). However, catchup-queued messages use `priority="low"` which is correct but the catchup scan itself (`get_dialogs()` + iterating messages) still blocks the startup sequence. Wrap the catchup scan in `asyncio.create_task()` so it runs concurrently with real-time message processing.

#### Fix 4: Restart gap

Two options (pick one):
- **Option A**: Set `catch_up=True` on the TelegramClient so Telethon replays missed updates after reconnection. Simple, leverages Telethon's built-in mechanism. Risk: may replay already-handled messages, but the catchup module's `_check_if_handled` provides deduplication.
- **Option B**: Overlap processes — start new bridge, wait for connection, then kill old. More complex, risk of dual-processing.

**Recommendation: Option A** — simpler and sufficient. Dedup via Redis/Popoto (per-chat, ~50 message IDs) prevents double-processing.

#### Fix 5: Graceful lock cleanup

In `_cleanup_session_locks()` (line 200), replace `os.kill(pid, 9)` with:
1. `os.kill(pid, signal.SIGTERM)` — request graceful shutdown
2. Wait up to 5 seconds for process to exit
3. `os.kill(pid, signal.SIGKILL)` — force kill if still running

Also replace the synchronous `time.sleep(jitter)` (line 223) with a comment noting this only runs at startup where blocking is acceptable.

**Note:** Fix 6 (acknowledgment timeout) is dropped from scope. Emoji reactions already acknowledge receipt and processing status. The "working on this" message at 180s is acceptable — the system communicates via reactions, and PMs don't need play-by-play updates.

## Rabbit Holes

- **Parallel job processing per project** — The sequential-per-project job queue is by design (prevents race conditions on shared project state). Don't try to parallelize it.
- **Ollama model preloading** — Tempting to pre-warm Ollama models on startup, but this is an Ollama configuration concern, not a bridge concern.
- **WebSocket/streaming acknowledgments** — Telegram doesn't support typing indicators for bots in groups. Reactions are the right mechanism.
- **Full catchup rewrite** — The existing catchup module is functional. Just fix the blocking startup scan; don't redesign the deduplication logic.

## Risks

### Risk 1: Deferred enrichment changes job payload contract
**Impact:** Existing queued jobs (from before the change) may lack new fields, causing worker crashes.
**Mitigation:** Not a real risk — `/update` waits for the queue to drain before restarting, so no old-format jobs will exist when the new code starts. Still use `.get()` with defaults as defensive coding.

### Risk 2: catch_up=True replays already-handled messages
**Impact:** Duplicate responses to users after restart.
**Mitigation:** Per-chat message dedup via Redis/Popoto (~50 message IDs per chat). This follows the existing pattern of caching message history to local Redis. The catchup module's `_check_if_handled` and `should_respond_async` routing provide additional layers.

## No-Gos (Out of Scope)

- Not parallelizing the per-project job queue
- Not rewriting the catchup deduplication logic
- Not adding Ollama model preloading or caching
- Not changing the bridge module structure (already refactored in #25)
- Not addressing the sequential job starvation issue (medium priority, separate work item)

## Update System

No update system changes required — all changes are to bridge/agent Python code that propagates via normal `git pull`. The `/update` skill already restarts the bridge after pulling, which is sufficient.

## Agent Integration

No agent integration required — these are bridge-internal changes to the message handling pipeline. The agent's interface (receiving enriched text via job queue) remains the same; only the timing of when enrichment happens changes.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` to document the SIGTERM→SIGKILL escalation fix
- [ ] Add entry to `docs/features/README.md` for message pipeline architecture if not already present
- [ ] Add inline comments in the event handler explaining the "enqueue fast, enrich later" pattern

## Success Criteria

- [ ] Event handler enqueues messages in < 100ms (no network calls before enqueue)
- [ ] Messages sent during bridge restart are processed after reconnection
- [ ] Per-chat Redis dedup prevents duplicate responses from catch_up replay
- [ ] Lock cleanup uses SIGTERM→SIGKILL escalation (no direct SIGKILL)
- [ ] Catchup scan runs concurrently with real-time message processing
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (pipeline)**
  - Name: pipeline-builder
  - Role: Restructure event handler to defer enrichment, update job payload, implement worker-side enrichment
  - Agent Type: builder
  - Resume: true

- **Builder (restart)**
  - Name: restart-builder
  - Role: Enable catch_up=True, implement Redis/Popoto per-chat dedup, fix SIGKILL→SIGTERM escalation
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline)**
  - Name: pipeline-validator
  - Role: Verify event handler is non-blocking, enrichment happens in worker, no regressions
  - Agent Type: validator
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: End-to-end validation — bridge starts, handles messages, no duplicates after restart
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update self-healing docs and add pipeline architecture documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Restructure event handler — defer enrichment to job worker
- **Task ID**: build-pipeline
- **Depends On**: none
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract media references, YouTube URLs, non-YouTube URLs, and reply_to_msg_id in the handler without processing them
- Add new fields to job payload: `raw_media`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `chat_id_for_enrichment`
- Create an `enrich_message()` function in new `bridge/enrichment.py` that the job worker calls before passing text to the agent
- Update job worker in `agent/job_queue.py` to call enrichment before agent invocation
- Ensure backward compatibility: old jobs without enrichment fields just skip enrichment

### 2. Enable catch_up and fix restart gap
- **Task ID**: build-restart
- **Depends On**: none
- **Assigned To**: restart-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `catch_up=True` to `TelegramClient()` constructor in `bridge/telegram_bridge.py:495`
- Implement per-chat message dedup using Redis/Popoto (~50 message IDs per chat, following existing message cache pattern). Check dedup in event handler before processing.
- Fix `_cleanup_session_locks()` to use SIGTERM→wait(5s)→SIGKILL escalation
- Wrap catchup scan in `asyncio.create_task()` at line 1169 so it doesn't block startup

### 3. Validate pipeline restructure
- **Task ID**: validate-pipeline
- **Depends On**: build-pipeline
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no network calls (Ollama, Perplexity, YouTube, Telegram get_messages) happen in the event handler
- Verify job payload includes enrichment metadata fields
- Verify worker calls enrichment before agent invocation
- Verify old-format jobs (without enrichment fields) don't crash
- Run `black . && ruff check .`

### 4. Validate restart and timing fixes
- **Task ID**: validate-restart
- **Depends On**: build-restart
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `catch_up=True` is set on TelegramClient
- Verify per-chat Redis dedup exists and is checked in handler
- Verify `_cleanup_session_locks` uses SIGTERM before SIGKILL
- Verify catchup runs as `asyncio.create_task()`, not blocking
- Run `pytest tests/` to check for regressions

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline, validate-restart
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with SIGTERM→SIGKILL escalation
- Document the "enqueue fast, enrich later" pipeline pattern
- Add/update entry in `docs/features/README.md`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `black . && ruff check .`
- Run `pytest tests/`
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `black --check . && ruff check .` — Code formatting and linting
- `pytest tests/` — All tests pass
- `grep -n "sequential_updates\|flood_sleep_threshold\|catch_up" bridge/telegram_bridge.py` — Verify Telethon client config
- `grep -n "SIGTERM\|signal.SIGTERM" bridge/telegram_bridge.py` — Verify graceful kill
- `grep -n "process_incoming_media\|process_youtube_urls\|get_link_summaries\|fetch_reply_chain" bridge/telegram_bridge.py` — Verify these are NOT called in handler

