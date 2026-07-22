# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Google Workspace CLI (`gws`)

On PATH after `npm install -g @googleworkspace/cli` (installed automatically on every machine by `/update`). **Not pre-authenticated** — first use requires a one-time human OAuth step: `gws auth setup` then `gws auth login`. If `gws` is present but unauthenticated, fall through to the next tool in the ladder (Gmail/Calendar/Drive MCP, then BYOB) rather than stalling.

Usage: `gws <service> <resource> [sub-resource] <method> [flags]`

**Services:** drive, sheets, gmail, calendar, docs, slides, people, chat, forms, keep, meet

**Key flags:**
- `--params '<JSON>'` — URL/query parameters
- `--json '<JSON>'` — request body (POST/PATCH/PUT)
- `--format table|csv|yaml` — output format (default: json)
- `--page-all` — auto-paginate (NDJSON, max 10 pages)
- `--upload <PATH>` — upload file
- `--output <PATH>` — save binary response to file
- `gws schema <service.resource.method>` — discover params for any method

**Common patterns:**
```
gws gmail users messages list --params '{"userId": "me", "maxResults": 5}'
gws gmail users messages get --params '{"userId": "me", "id": "MSG_ID"}'
gws drive files list --params '{"q": "name contains '\''report'\''", "pageSize": 10}'
gws calendar events list --params '{"calendarId": "primary", "timeMin": "2026-03-06T00:00:00Z"}'
gws sheets spreadsheets values get --params '{"spreadsheetId": "ID", "range": "Sheet1!A1:D10"}'
```

**Workflows:** `gws workflow +standup-report`, `+meeting-prep`, `+email-to-task`, `+weekly-digest`

## Quick Commands

| Command | Description |
|---------|-------------|
| `./scripts/start_bridge.sh` | Start Telegram bridge |
| `./scripts/valor-service.sh status` | Check bridge status |
| `./scripts/valor-service.sh restart` | Restart bridge, watchdog, and worker after code changes |
| `./scripts/valor-service.sh worker-start` | Start standalone worker service (also re-enables launchd auto-respawn) |
| `./scripts/valor-service.sh worker-stop` | Transient stop — `bootout` only; launchd's `KeepAlive=true` may relaunch |
| `./scripts/valor-service.sh worker-restart` | Restart standalone worker |
| `./scripts/valor-service.sh worker-status` | Check worker service status |
| `./scripts/valor-service.sh worker-disable` | Stop the worker **and** disable launchd auto-respawn (stays down until `worker-enable`/`worker-start`) |
| `./scripts/valor-service.sh worker-enable` | Re-enable launchd auto-respawn (does NOT start the worker; pair with `worker-start`) |
| `./scripts/valor-service.sh email-start` | Start the email bridge (IMAP polling) |
| `./scripts/valor-service.sh email-stop` | Stop the email bridge |
| `./scripts/valor-service.sh email-restart` | Restart the email bridge |
| `./scripts/valor-service.sh email-status` | Check email bridge status, IMAP last-poll age, and SMTP relay heartbeat |
| `./scripts/valor-service.sh email-dead-letter list` | List failed SMTP sends in dead-letter queue |
| `./scripts/valor-service.sh email-dead-letter replay --all` | Replay all dead-lettered emails |
| `./scripts/install_email_bridge.sh` | Install launchd plist for boot-time email bridge (machine-gated, idempotent; opt-in) |
| `tail -f logs/bridge.log` | Stream bridge logs |
| `pytest tests/` | Run all tests (parallel by default — `-n auto --dist=loadfile` from `pyproject.toml`). **Prefer `scripts/pytest-clean.sh` over bare `pytest`** — the wrapper reaps xdist workers on exit; without it, interrupted runs leave orphan workers consuming memory (see xdist reaper note in `pyproject.toml`). |
| `pytest tests/unit/` | Run unit tests only (~40s parallel) |
| `pytest tests/unit/ -n0` | Force serial unit run (e.g. for debugging) |
| `pytest tests/integration/` | Run integration tests only (~125s parallel) |
| `scripts/pytest-clean.sh <pytest-args>` | Run pytest with automatic xdist worker reaping (drop-in for `pytest`). Full-suite runs also take a machine-global advisory lock (a `/tmp` path keyed to the repo's git common dir, shared across all worktrees) so a second concurrent full-suite run — including one from another worktree — waits instead of oversubscribing cores — see `docs/features/full-suite-pytest-lock.md`. Disable with `PYTEST_SUITE_LOCK=0`. |
| `scripts/reap-xdist.sh` | Kill any orphan xdist workers on the system (one-shot reaper, idempotent) |
| `pytest -m sdlc` | Run tests for a specific feature (see `tests/README.md`) |
| `python -m ruff format . && python -m ruff check .` | Format and lint |
| `python -m ui.app` | Start web UI server on localhost:8500 |
| `curl -s localhost:8500/dashboard.json` | Check the dashboard — full system state as JSON (sessions, health, reflections, machine) |
| `curl -s localhost:8500/memories/metrics.json` | Corpus-wide memory ingest-quality metrics as JSON (act rate, junk rate, ingest volume, histograms); optional `?project_key=`/`?min_evidence=`. See `docs/features/memory-telemetry.md`. |
| `python -m tools.memory_eval.snapshot` | Snapshot current memory-corpus telemetry to `docs/baselines/memory-telemetry-baseline.{json,md}`; refuses to overwrite existing artifacts unless `--force` is passed |
| `tail -f logs/worker.log` | Stream worker logs |
| `python -m reflections --dry-run` | Load the reflection registry, print status, exit 0 (validates the out-of-process scheduler entry) |
| `./scripts/install_reflection_worker.sh` | Install/reload the reflection-scheduler subprocess (`com.valor.reflection-worker`; worker-role gated, self-skips + removes stale plist elsewhere) |
| `tail -f logs/reflection_worker.log` | Stream reflection-scheduler subprocess logs (`python -m reflections`) |
| `sdlc-tool stage-query --issue-number {N}` | Query SDLC pipeline state for an issue (cwd-independent — see `docs/features/sdlc-tool-resolver.md`) |
| `sdlc-tool verdict get --stage CRITIQUE --issue-number {N}` | Read the recorded critique verdict for an issue (also: `--stage REVIEW`) |
| `python scripts/sdlc_reflection.py` | Run SDLC reflection manually |
| `python scripts/sdlc_reflection.py --dry-run` | Preview SDLC reflection without writing |
| `python scripts/sdlc_reflection.py --days 14` | Run with larger lookback window |
| `./scripts/install_sdlc_reflection.sh` | Install SDLC reflection launchd schedule |
| `tail -f logs/sdlc_reflection.log` | Stream SDLC reflection logs |
| `python scripts/autoexperiment.py --target observer --iterations 50` | Run autoexperiment on observer prompt |
| `python scripts/autoexperiment.py --target summarizer --dry-run` | Dry-run autoexperiment on the message drafter (target name is historical) |
| `python scripts/autoexperiment.py --list-targets` | List autoexperiment targets |
| `./scripts/install_autoexperiment.sh` | Install autoexperiment nightly schedule |
| `./scripts/install_nightly_tests.sh` | Install nightly regression test launchd schedule (bridge-role gated; auto-installed by `/update` on bridge machines, self-skips + removes stale plist elsewhere) |
| `python scripts/nightly_regression_tests.py --dry-run` | Preview nightly test run without Telegram |
| `tail -f logs/nightly_tests.log` | Stream nightly test logs |
| `tail -f logs/nightly_tests_error.log` | Stream nightly test error log (startup crashes) |
| `python -m tools.analytics export --days 30` | Export analytics metrics as JSON |
| `python -m tools.analytics summary` | Print human-readable analytics summary |
| `python -m tools.analytics rollup` | Run analytics daily rollup manually |
| `python -m tools.agent_session_scheduler status` | Show queue status (pending, running, killed counts) |
| `python -m tools.agent_session_scheduler list --status killed,abandoned` | List sessions filtered by status |
| `python -m tools.agent_session_scheduler kill --agent-session-id <ID>` | Kill a running or pending session by ID |
| `python -m tools.agent_session_scheduler kill --session-id <ID>` | Kill a session by session ID |
| `python -m tools.agent_session_scheduler kill --all` | Kill all running and pending sessions |
| `python -m tools.agent_session_scheduler cleanup --age 30 --dry-run` | Preview stale session cleanup |
| `python -m tools.agent_session_scheduler cleanup --age 30` | Delete stale killed/abandoned/failed sessions |
| `python -m tools.valor_session list` | List all sessions |
| `python -m tools.valor_session status --id <ID>` | Show session status and pending steering messages |
| `python -m tools.valor_session status --full-message --id <ID>` | Show full initial prompt (no 100-char truncation) |
| `python -m tools.valor_session inspect --id <ID>` | Dump all raw Popoto fields for a session (debugging) |
| `python -m tools.valor_session children --id <ID>` | List all child sessions spawned by a parent session |
| `python -m tools.valor_session steer --id <ID> --message "..."` | Inject a steering message into a running session |
| `python -m tools.valor_session kill --id <ID>` | Kill a session |
| `python -m tools.valor_session kill --all` | Kill all running sessions |
| `python -m tools.valor_session create --role eng --message "..."` | Create and enqueue a new Eng session. `project_key` determines the repo via `projects.json`; there is no working-directory override flag. Precedence: `--project-key` > `--parent` inheritance > cwd match (raises on no match). Warns to stderr if no worker is running. |
| `python -m tools.valor_session resume --id <ID> --message "..."` | Resume a completed, killed, or failed session (hard-PATCH path; accepts session_id or agent_session_id) |
| `python -m tools.valor_session release --pr <N>` | Clear retain_for_resume after PR merge/close |
| `python -m tools.valor_session telemetry --id <ID>` | Show session telemetry timeline (turn events, token usage, status transitions) |
| `valor-session crash-signatures` | Show crash signatures in the library (project-scoped) |
| `valor-session crash-policy list` | Show derived auto-resume policy entries |
| `valor-session-archive status` | Show the SQLite secondary-store (`data/session_archive.db`) freshness: row count, last export age, health |
| `valor-session-archive restore --dry-run` | Report the empty-Redis restore guard decision (would it restore/skip/resume, and how many rows) without writing anything — read-only; export and live restore run automatically via the worker |
| `python -m tools.memory_search search "query"` | Search memories by query |
| `python -m tools.memory_search search "query" --category correction` | Search filtered by category |
| `python -m tools.memory_search search "query" --tag redis` | Search filtered by tag |
| `python -m tools.memory_search save "content"` | Save a new memory |
| `python -m tools.memory_search inspect --id <ID>` | Inspect a specific memory |
| `python -m tools.memory_search inspect --stats` | Show memory statistics |
| `python -m tools.memory_search forget --id <ID> --confirm` | Delete a memory |
| `python -m tools.memory_search status` | Check memory system health (Redis, counts, superseded ratio) |
| `python -m tools.memory_search status --json` | Memory health as machine-readable JSON |
| `python -m tools.memory_search status --deep` | Memory health with Redis-side `orphan_index_count`, disk-side `disk_orphan_count`, and per-category confidence |
| `python -m tools.doctor` | Run all environment and health checks |
| `python -m tools.doctor --quick` | Skip slow checks (Telegram session, model verification) |
| `python -m tools.doctor --quality` | Include code quality checks (ruff, pytest) |
| `python -m tools.doctor --json` | Output health check results as JSON |
| `python -m tools.doctor --install-hook` | Install git pre-push hook running doctor --quick |
| `valor-youtube-search "query"` | Search YouTube for videos by query |
| `valor-youtube-search --limit N "query"` | Search YouTube with limited results |
| `valor-youtube-transcribe <url>` | Transcribe a YouTube video (captions-first, Whisper fallback). Prefer this over `WebFetch` for YouTube URLs — YouTube serves anti-bot HTML to non-browser fetchers. |
| `valor-youtube-transcribe --json <url>` | Same as above, emit raw `process_youtube_url` dict as JSON |
| `valor-youtube-transcribe --summary-only <url>` | Emit only the GPT-4o-mini summary (or full transcript with a note if none) |
| `valor-video-watch <url> ["question"]` | Visual grounding for a YouTube or X/Twitter video: yt-dlp download, ffmpeg scene-change frame extraction (deduped), Whisper transcript, and Grok X-native context/fallback for X. Prints frame JPEG paths (`t=MM:SS`) to `Read` image-by-image. Use when the answer is on-screen, not in the audio. See `docs/features/video-watch-visual-grounding.md`. |
| `valor-video-watch --json <url>` | Same, emitting the raw `watch_video` result dict as JSON |
| `valor-tts --text "Hello." --output /tmp/out.ogg` | Synthesize text to OGG/Opus (Kokoro local primary, OpenAI tts-1 fallback). See `docs/features/tts.md`. |
| `valor-tts --text "Hello." --output /tmp/out.ogg --voice af_bella` | Synthesize with a specific voice (catalog in `tools/tts/README.md`) |
| `valor-tts --text "Hello." --output /tmp/out.ogg --force-cloud` | Force the cloud (OpenAI tts-1) backend even if Kokoro is available |
| `valor-deck-video deck.md` | Render a narrated MP4 of a Marp deck (per-slide `<!-- narration: ... -->`, voiceover via valor-tts, slides held for each clip's duration). See `docs/features/narrated-deck-video.md`. |
| `valor-ingest <path-or-url>` | Convert a PDF/DOCX/PPTX/XLSX/HTML/image/YouTube URL into a `.md` sidecar the knowledge indexer picks up (see `docs/features/markitdown-ingestion.md`) |
| `valor-ingest --scan ~/work-vault/` | Backfill every convertible binary file in the vault recursively (audio formats deliberately excluded) |
| `valor-computer bootstrap` | Readiness preflight (`GET /v1/bootstrap`); run once per session before the first action. Exit 0 when ready, 78 when permissions ungranted (`instructions.ready == false`) or bcu unavailable — relay `instructions.user` and stop |
| `valor-computer list_apps` | List all visible macOS apps (requires bcu opt-in via `/setup`; macOS-only — exits 78 on other OSes) |
| `valor-computer list_windows <app>` | List open windows for an app (name, bundle ID, or query); window IDs are strings |
| `valor-computer click <window> --x N --y N` | Click coordinates in a native window without moving the user's cursor |
| `valor-computer type_text <window> "text"` | Type text into a native app window via Accessibility API |
| `valor-computer screenshot <window> --output /tmp/out.png` | Capture a native window screenshot via get_window_state imageMode (see `docs/features/computer-use.md`) |

## Manual Testing Hygiene

When creating AgentSessions manually (debug scripts, one-off Python invocations) to test worker or queue behavior, **always clean up afterward**:

```python
# Clean up test sessions by project_key using Popoto only — never raw Redis
from models.agent_session import AgentSession
stale = [s for s in AgentSession.query.all() if s.project_key == "my-test-proj"]
for s in stale: s.delete()
```

- Use a recognizable `project_key` prefix (e.g. `test-`, `dbg-`) so test sessions are easy to identify
- Never use raw Redis on Popoto-managed keys — all reads (`r.hgetall`, `r.hget`, `r.scan_iter`) and writes (`r.delete`, `r.srem`, `r.sadd`, `r.zrem`) must go through the ORM (`Model.query.filter()`, `instance.save()`, `instance.delete()`). Enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`.
- Check the dashboard after any manual test session run: `curl -s localhost:8500/dashboard.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['sessions']), 'sessions')"`

## Development Principles

### 1. NO LEGACY CODE TOLERANCE
- Never leave traces of legacy code or systems
- Always overwrite, replace, and delete obsolete code completely
- No commented-out code, no "temporary" bridges, no half-migrations

### 2. CRITICAL THINKING MANDATORY
- Foolish optimism is not allowed - always think deeply
- Question assumptions, validate decisions, anticipate consequences
- Prioritize robust solutions over quick fixes

### 3. INTELLIGENT SYSTEMS OVER RIGID PATTERNS
- Use LLM intelligence instead of keyword matching
- Context-aware decision making over static rule systems

### 4. MANDATORY COMMIT AND PUSH WORKFLOW
- ALWAYS commit and push changes at the end of every task
- Never leave work uncommitted in the repository
- Use `git add . && git commit -m "Description" && git push`

### 5. CONTEXT COLLECTION AND MANAGEMENT
- Context is the lifeblood of agentic systems
- Explicitly pass context when spawning sub-agents
- Track the "why" alongside the "what"

### 6. TOOL AND MCP SELECTION
- Loading all tools pollutes context and degrades performance
- Start with minimal tools, expand only if needed

### 7. DEFINITION OF DONE
- Enforced by the `/do-build` command and builder agent — see `.claude/commands/do-build.md`
- Not complete until: tests pass, docs created, PR opened, plan migrated

### 8. PARALLEL EXECUTION (P-Thread Pattern)
- When facing independent tasks, spawn parallel sub-agents using Task tool
- Do NOT parallelize sequential/dependent work
- Always aggregate results before reporting

### 9. SDLC PIPELINE
- An Eng-role AgentSession handles both orchestration and execution
- Bridge uses nudge loop for output routing (no SDLC awareness in bridge)
- `/sdlc` is a **single-stage router**: it assesses state, invokes ONE sub-skill, and returns
- NEVER write code, run tests, or create plans directly -- always delegate through sub-skills
- See `.claude/skills/sdlc/SKILL.md` for the ground truth on pipeline stages

### 10. ALWAYS RESTART RUNNING SERVICES
- If bridge or worker is running and you modify bridge/agent code, restart immediately after committing
- Restart: `./scripts/valor-service.sh restart` (cycles bridge, watchdog, and worker)
- Verify: `tail -5 logs/bridge.log` shows "Connected to Telegram"

## Development Workflow

The standard flow from conversation to shipped feature:

### Phase 1: Conversation
- Chat arrives via Telegram (or local Claude Code session)
- Could be Q&A, exploring an idea, or raising an issue
- No branch, no task list, no slug yet — just conversation
- If it's a real piece of work: create a GitHub issue

### Phase 2: SDLC (triggered by work request)
- The Eng session steers the pipeline, invoking `/sdlc` skills as needed
- `/sdlc` assesses current state, invokes ONE sub-skill, and returns
- Stages: Plan -> Critique -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge
- See `.claude/skills/sdlc/SKILL.md` for the ground truth on stage definitions

### Phase 3: Review & Merge
- Valor may or may not be asked to merge the PR after human review
- Thumbs-up emoji reaction (👍) signals "done for now" / final completion

### Auto-Continue Rules
- The agent should only pause if there is a **legitimate open question** requiring human input
- If there is no question -- just a status update -- the message drafter auto-sends "continue"
- Status updates without questions or signs of completion are NOT stopping points
- The agent keeps working until the phase is complete or it's genuinely blocked
- **SDLC sessions**: the Eng session handles pipeline progression
- **The Eng session** handles both orchestration and execution; all messages route through the Eng session
- Auto-continue caps are set to 50 as safety backstops (the Eng session manages actual routing)
- The auto-continue counter resets when the human sends a new message

### Session Continuity
- Full session logs are saved at all breakpoints for later analysis
- Telegram chat history is stored in Redis via Popoto ORM for fast review anytime
- Reply-to messages in Telegram resume the original session context

## System Architecture

```
Telegram → Python Bridge (Telethon) → Enqueues AgentSession to Redis (I/O only)
              (bridge/telegram_bridge.py)     → Nudge loop (bridge has no SDLC awareness)
                                              → Registers output callbacks for delivery

Standalone Worker (python -m worker) → Sole session execution engine
              (worker/__main__.py)         → Startup: index rebuild → corrupted+orphan cleanup → dead-worker sweep (Step 3a, issue #1767) → recovery (Step 3b) → register_worker_pid (self-suicide guard)
                                           → Hourly `agent-session-cleanup` reflection: corrupted records + cross-process orphan reap (claude/MCP, PPID==1, heartbeat-gated; issue #1271)
                                           → Executes Eng session (AgentSession session_type=eng)
                                               → Eng session handles SDLC work via the headless session runner (one `claude -p` subprocess per turn; PM spawns/continues a resumable `dev` subagent inline)
                                                 (agent/session_runner/; bridge-originated sessions; see docs/features/headless-session-runner.md)
                                           → Uses OutputHandler protocol (agent/output_handler.py)
                                           → TelegramRelayOutputHandler writes to Redis outbox
                                           → FileOutputHandler fallback for non-Telegram / dev environments
```
See `docs/features/bridge-worker-architecture.md` for the full bridge/worker separation design.

**Session Types** (see `docs/features/eng-session-architecture.md`):
- **Eng Session** (`session_type="eng"`) - Handles both SDLC work and conversational responses, full permissions, engineer persona
- **Teammate Session** (`session_type="teammate"`) - Conversational, Teammate persona. Bash is open, audit-logged with `[teammate-audit]`. Writes restricted in code to `docs/`, `.claude/`, `.github/`, `wiki/`, `skills/`, top-level meta files, and `~/work-vault/`; source-code writes get a redirect to spawn an Eng session. See [`docs/features/teammate-session-permissions.md`](docs/features/teammate-session-permissions.md).
- **Nudge loop** - Bridge output routing (deliver or nudge, no SDLC awareness)
- **Session Steering** (see `docs/features/session-steering.md`): the Redis steering list (`agent/steering.py`) is the sole steering inbox — any process writes messages via `push_steering_message()`, the worker drains them at the turn boundary. `agent/output_router.py` contains routing decision logic extracted from executor. Use `valor-session steer --id <id> --message "..."` to steer externally.

**Subconscious Memory** (see `docs/features/subconscious-memory.md`):
- Human Telegram messages are saved as Memory records on receipt (importance=6.0)
- PostToolUse hook checks ExistenceFilter bloom and injects compact `<thought id="...">[category] title</thought>` stubs via additionalContext (≥5× smaller than full bodies); the agent pulls full content on demand via the `memory_get` / `memory_search` MCP tools (`mcp_servers/memory_server.py`)
- Post-session Haiku extraction saves categorized observations (corrections/decisions at 4.0, patterns/surprises at 1.0)
- Intentional saves via `python -m tools.memory_search save "content"` for project-level learnings (7.0-8.0)
- Post-merge learning extraction distills PR takeaways into memories (importance=7.0)
- Outcome detection (bigram overlap) feeds ObservationProtocol to strengthen/weaken memories, plus dismissal tracking with importance decay
- Multi-query decomposition splits large keyword sets into clusters for broader retrieval coverage
- **Claude Code hooks** extend memory to CLI sessions via `.claude/hooks/hook_utils/memory_bridge.py` (see `docs/features/claude-code-memory.md`): UserPromptSubmit ingests prompts, PostToolUse recalls with file-based sliding window, Stop extracts observations
- All memory operations fail silently -- memory system never crashes the agent or hooks
- **Memory consolidation** (`memory-dedup` nightly reflection): Haiku-based semantic dedup merges near-duplicate records, sets `superseded_by` on originals (never deleted), filters superseded records from recall. Dry-run default — see `docs/features/subconscious-memory.md#memory-consolidation`

**Key Directories:**
- `.claude/skills-global/` - **Global skills** — synced to every machine (see below)
- `.claude/skills/` - **Project-only skills** — work only in this repo's context, NOT synced
- `.claude/commands/` - Slash command skills
- `.claude/agents/` - Subagent definitions (builder, validator, code-reviewer; eng sessions created via valor_session CLI)
- `bridge/` - Telegram integration, nudge loop
- `worker/` - Standalone worker service (`python -m worker`)
- `agent/` - Session queue, SDK client, output router (`output_router.py`), output handler protocol, constants
- `tools/` - Local Python tools
- `config/` - Configuration files

## Global vs. Project-Only Skills

This repo is the canonical source for skills that ship to **every machine**. There are two skill directories, and the distinction matters:

| Directory | Scope | Synced? |
|-----------|-------|---------|
| `.claude/skills-global/` | **Global / general-purpose skills** | ✅ Hardlinked to `~/.claude/skills/` on every machine by `/update` |
| `.claude/skills/` | **Project-only skills** — tightly coupled to this repo's infra (Telegram bridge, macOS Messages, system logs) | ❌ Never synced; only work in this repo's context |

**Terminology:** When someone says "make this a **global skill**" or "**general-purpose skill**," they mean: *put it in `.claude/skills-global/` so the `/update` wiring propagates it to `~/.claude/skills/` on every machine.* It does NOT mean editing a `CLAUDE.md` note. A skill is "known to every machine" precisely when it lives in `skills-global/`.

**The sync wiring** lives in `scripts/update/hardlinks.py`:
- `sync_claude_dirs()` hardlinks every skill dir under `.claude/skills-global/` into `~/.claude/skills/`. Adding a directory with a `SKILL.md` there is all that's required — no registration step.
- `PROJECT_ONLY_SKILLS` and the project-only `.claude/skills/` set are explicitly excluded from the sync.
- `RENAMED_REMOVALS` removes stale user-level copies when a skill is renamed or moved between the two dirs. **When you move a skill between `skills/` and `skills-global/`, add a `RENAMED_REMOVALS` entry** so the old hardlink is cleaned up on every machine.

Example: `/do-debrief` (the TTS composite that wraps `valor-tts`) lives in `.claude/skills-global/do-debrief/` — that's why every machine already knows it. The client-facing CMA skills `/imagine-agent` and `/build-agent` follow the same pattern. A skill that only ever runs against the local bridge (e.g. `telegram`, `checking-system-logs`) stays in `.claude/skills/`.

**Repo-specific behavior via the skill-context seam:** Global skill bodies stay generic. Repo-specific behavior is layered in via `.claude/skill-context/{skill}.md` (non-SDLC skills) or `docs/sdlc/{skill}.md` (SDLC pipeline skills). If the file is absent — the common case in any foreign repo — the skill runs its generic baseline. If the file is present, the skill reads it and honors its declarations. Every coupled skill body carries the canonical probe sentence: `"If <context-path> exists, read it and honor its declarations; otherwise use the generic defaults described below."` The `rule_13_coupling_signals` guard in `do-skills-audit` enforces probe presence for any body that references ai-repo executables (`sdlc-tool`, `valor-*`, `python -m tools.*`, etc.). See [`docs/features/skill-context-convention.md`](docs/features/skill-context-convention.md) for the full reference.

**Bucket C (project-only infrastructure skills):** Some skills are too tightly coupled to this repo's infrastructure to generalize even with a probe step. `setup`, `prime`, `sdlc`, and `do-deploy` live in `.claude/skills/` (project-only) rather than `.claude/skills-global/`. They are never synced to `~/.claude/skills/` on other machines. If you move a skill into this category, add a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py` to remove the stale hardlink on every machine.

## Testing Philosophy

- **Real integration testing** - No mocks, use actual APIs
- **Intelligence validation** - Use AI judges, not keyword matching
- **Quality gates**: Unit 100%, Integration 95%, E2E 90%

## Work Completion Criteria

Work is DONE when:
1. ✅ Deliverable exists and works
2. ✅ Code quality standards met (`python -m ruff check`, `python -m ruff format`)
3. ✅ Changes committed and pushed to git
4. ✅ Original request fulfilled

## Session Management

| State | Description |
|-------|-------------|
| **Active** | Currently processing message |
| **Dormant** | Paused on a legitimate open question, waiting for human reply |
| **Abandoned** | Unfinished work, auto-revived |
| **Complete** | Work done, signaled by 👍 reaction or `mark_work_done()` |

See also: `docs/features/session-lifecycle.md` for the full 14-state reference (including `paused`, `paused_circuit`, `paused_budget`, `superseded`, `waiting_for_children`, and all terminal states).

- Fresh messages create new sessions (scoped by Telegram thread ID or local session ID)
- Reply-to messages resume the original session and its context
- Sessions only pause for **genuine open questions** — not status updates
- Each session gets an isolated task list automatically (see issue #62 for two-tier scoping)

### Task List Isolation

Sessions get automatic task list isolation via the `CLAUDE_CODE_TASK_LIST_ID` environment variable, injected by the SDK client when spawning Claude Code.

- **Tier 1 (thread-scoped):** Ad-hoc conversations get ephemeral, disposable task lists keyed by `thread-{chat_id}-{root_message_id}`. No configuration needed -- the bridge derives the ID from the Telegram thread automatically.
- **Tier 2 (slug-scoped):** Planned work items (created via `/do-plan {slug}`) get durable, named task lists keyed by the slug. The slug ties together the task list, branch, worktree, plan doc, and GitHub issue.
- **Git worktrees:** Filesystem isolation is available for tier 2 work via `agent/worktree_manager.py`. Each work item gets its own worktree under `.worktrees/{slug}/` with branch `session/{slug}`.

See `docs/features/session-isolation.md` for the full technical design.

## Quick Reference

### Critical Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| Memory | 600MB | 800MB |
| CPU | 80% | 95% |

### Emergency Recovery

- **Bridge Issues**: `./scripts/valor-service.sh restart`
- **Worker Issues**: `./scripts/valor-service.sh worker-restart`
- **Telegram Auth**: `python scripts/telegram_login.py`
- **SDK Issues**: Check SDK configuration in `.env`

### Self-Healing System

The bridge includes automatic crash recovery (see `docs/features/bridge-self-healing.md`):

- **Session lock cleanup**: Kills stale processes holding session-related files on startup
- **Bridge watchdog**: Separate launchd service (`com.valor.bridge-watchdog`) monitors health every 60s
- **Crash tracker**: Logs start/crash events to Redis via `monitoring/crash_tracker.py` with git commit correlation
- **5-level escalation**: restart → kill stale → clear locks → revert commit → alert human
- **Update-loop wedged detector** (#1712): detects when the bridge is process-alive but Telethon's `NewMessage` handler has silently stopped firing — auto-restarts with `catch_up=True` for lossless backfill

**Check watchdog**: `python monitoring/bridge_watchdog.py --check-only`
**View crashes**: `python -c "from monitoring.crash_tracker import get_recent_crashes; print(get_recent_crashes(3600))"`
**Enable auto-revert**: `touch data/auto-revert-enabled` (disabled by default)

### Configuration Files

- `.env` - Symlink → `~/Desktop/Valor/.env` (do not write secrets here directly)
- `~/Desktop/Valor/projects.json` - Multi-project configuration (iCloud-synced, private)
- `.claude/settings.local.json` - Claude Code settings

Tunable timing/retry/TTL values (subprocess timeouts, HTTP/SMTP/Redis timeouts,
session TTLs) live in `config/settings.py`'s `TimeoutSettings` group, overridable
via `TIMEOUTS__*` env keys — see [`docs/features/config-timeout-catalog.md`](docs/features/config-timeout-catalog.md)
for the full field catalog and the promote-vs-name-locally criterion for adding new knobs.

### Single-Machine Ownership (Strict)

Every bridge-contact identifier in `projects.json` is owned by exactly **one** machine. Two machines must never both pick up the same incoming bridge message. Applies to all bridge-contact shapes:

- Telegram DM contact id (`dms.whitelist[].id`)
- Telegram group name (`projects.<key>.telegram.groups.<name>`)
- Email contact (`projects.<key>.email.contacts[]`)
- Email domain wildcard (`projects.<key>.email.domains[]`)

`projects.<key>.machine` is the source of truth — every other identifier inherits ownership from its project. Adding a new machine costs zero edits to existing whitelist entries, group declarations, or email patterns.

Enforced by `bridge/config_validation.py::validate_projects_config` and gated by `scripts/update/run.py` Step 4.6 — the update script blocks the bridge restart on a malformed config and the running bridge keeps serving on the previously-validated config. Full reference: [docs/features/single-machine-ownership.md](docs/features/single-machine-ownership.md).

## Secrets

All secrets go in **`~/Desktop/Valor/.env`**. Never write secrets to `repo/.env`.

The repo `.env` is a symlink — writing to it writes to the vault, but the canonical workflow is to edit `~/Desktop/Valor/.env` directly. The symlink is created automatically by `scripts/remote-update.sh` and `scripts/update/env_sync.py` on each machine after iCloud syncs.

**Adding a new secret:** add it to `~/Desktop/Valor/.env`, add a placeholder to `.env.example` (with a comment line above the `KEY=` — required by the completeness check), add a field to `config/settings.py`. That's it — no sync step needed.

## See Also

| Resource | Purpose |
|----------|---------|
| `/prime` | Full architecture deep dive and codebase onboarding |
| `/setup` | New machine configuration |
| `/do-pr-review` | PR review with implementation validation and screenshots |
| `/add-feature` | How to extend the system |
| `/sdlc` | Single-stage router: assess state, invoke one sub-skill, return |
| `docs/deployment.md` | Multi-instance deployment |
| `docs/tools-reference.md` | Complete tool documentation |
| `config/identity.json` | Structured identity data (name, email, timezone, org) |
| `config/personas/segments/` | Composable persona segments (identity, work-patterns, tools) |
| `docs/features/README.md` | Feature index — look up how things work |
| `docs/sdlc/` | Per-stage repo-specific addenda — read by SDLC skills at runtime |
| `tests/README.md` | Test suite index — feature markers, blind spots, contribution guide |
| `docs/conventions/knowledge-base-section.md` | KB-section convention every project's `CLAUDE.md`/`README.md` should follow |

## GitHub Issue Labels

Use these labels consistently when creating or editing issues:

| Label | When to use |
|-------|-------------|
| `bug` | Something is broken or not working as expected |
| `reflections` | Related to the reflections maintenance system (`reflections/` package, `agent/reflection_scheduler.py`) |
| `memory` | Related to the subconscious memory system (memory search, bloom filter, recall/extract) |
| `skills` | Related to skills (`/do-*` commands), tools (MCP/Python), or the SDLC pipeline |
| `dashboard` | Related to the web UI dashboard (`ui/`) |
| `bridge` | Related to the Telegram bridge (`bridge/`) |
| `testing` | Related to the test suite (`tests/`) |

Do NOT use a `feature` label — it adds no signal.

## Knowledge Base (KB)

This project's knowledge has two sources. Pull from both before answering substantive questions.

**1. Vault (curated docs, iCloud-synced)**
- Location: `~/work-vault/AI Valor Engels System/`
- Index: see that directory's `README.md` for the file index
- Source of truth for business context, project notes, decisions, and assets
- Ingest binaries into the indexer with `valor-ingest <path>` (creates `.md` sidecars; `--scan` for backfill)

**2. Memory system (Redis, agent-learned observations)**
- Project key: `valor` (partitions memories for this project — see `config/projects.json`)
- Search: `python -m tools.memory_search search "<query>" --project valor`
- Save: `python -m tools.memory_search save "<content>" --project valor`
- Status: `python -m tools.memory_search status --project valor`
- MCP recall: `mcp__memory__memory_search`, `mcp__memory__memory_get`
- See `docs/features/subconscious-memory.md` for ingestion, scoring, and consolidation

Curated vault = what humans wrote. Memory = what the agent learned (corrections, decisions, patterns, surprises). Both partition by project — don't leak cross-project context.

This is a convention every project should follow — see [`docs/conventions/knowledge-base-section.md`](docs/conventions/knowledge-base-section.md).
