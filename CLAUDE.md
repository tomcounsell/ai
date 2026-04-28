# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Google Workspace CLI (`gws`)

Available at `~/src/node_modules/.bin/gws`. Pre-authenticated.

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

## OfficeCLI

CLI for creating, reading, and editing Office documents (.docx, .xlsx, .pptx). Single binary at `~/.local/bin/officecli`, no dependencies.

Usage: `officecli <command> <file> [path] [flags]`

**Commands:** `create`, `view`, `get`, `query`, `set`, `add`, `remove`, `move`, `swap`, `batch`, `validate`, `open`, `close`

**Strategy:** L1 (read) then L2 (DOM edit) then L3 (raw XML). Always prefer higher layers. Add `--json` for structured output.

**Help system:** When unsure about property names or syntax, run help instead of guessing:
```bash
officecli pptx set              # All settable elements and properties
officecli pptx set shape        # Shape properties in detail
officecli pptx set shape.fill   # Specific property format and examples
```

**Common patterns:**
```bash
# Create files
officecli create report.docx
officecli create data.xlsx
officecli create slides.pptx

# Read and inspect
officecli view report.docx outline           # Document structure
officecli view report.docx stats             # Page/word/shape counts
officecli view report.docx issues            # Formatting problems
officecli get report.docx '/body/p[1]' --json
officecli get slides.pptx '/slide[1]' --depth 1

# Edit Word
officecli add report.docx /body --type paragraph --prop text="Summary" --prop style=Heading1
officecli set report.docx '/body/p[1]/r[1]' --prop font=Arial --prop size=12pt

# Edit Excel
officecli set data.xlsx /Sheet1/A1 --prop value="Name" --prop bold=true
officecli set data.xlsx /Sheet1/B2 --prop value=95

# Edit PowerPoint
officecli add slides.pptx / --type slide --prop title="Q4 Report" --prop background=1A1A2E
officecli add slides.pptx /slide[1] --type shape --prop text="Revenue grew 25%" --prop x=2cm --prop y=5cm

# Batch operations (multiple edits in one save)
echo '[{"command":"set","path":"/Sheet1/A1","props":{"value":"Name","bold":"true"}}]' | officecli batch data.xlsx --json

# Resident mode (3+ commands on same file)
officecli open report.docx    # Keep in memory
officecli set report.docx ... # Fast, no file I/O
officecli close report.docx   # Save and release
```

**Output:** Use `--json` for structured output. Use `--depth N` with `get` to expand children. Use `--max-lines N` with `view text` for large documents.

## Reading Telegram Messages

Use `valor-telegram` to read messages from any chat. It checks Redis first, then falls back to the Telegram API automatically. Sending routes through the Redis relay (requires bridge to be running).

```bash
valor-telegram read --chat "Dev: Valor" --limit 10
valor-telegram read --chat "Tom" --search "deployment"
valor-telegram read --chat "Dev: Valor" --since "1 hour ago"
valor-telegram read --chat-id -1001234567 --limit 10       # numeric bypass
valor-telegram read --user tom --limit 10                  # DM whitelist bypass
valor-telegram read --project psyoptimal --limit 20        # union all chats with this project_key
valor-telegram chats --search "psy"                        # discover by fragment
valor-telegram chats --project psyoptimal                  # list every chat tagged with the project
valor-telegram send --chat "Dev: Valor" "Hello world"
valor-telegram send --chat "Forum Group" --reply-to 123 "Message to topic"
valor-telegram send --chat "Tom" --file ./screenshot.png "Caption"
valor-telegram send --chat "Dev: Valor" --voice-note --cleanup-after-send --audio /tmp/out.ogg
```

`--chat`, `--chat-id`, `--user`, and `--project` on `read` are mutually exclusive. Every successful single-chat read prints a freshness header `[chat_name · chat_id=N · last activity: T]` before the messages; cross-chat `--project` reads print `[project=KEY · N chats: name1, name2, ... · last activity: T]` and tag each line with `[chat_name]` so you can see which chat each message came from — trust those headers over your intuition about which chat you asked for. `--project --json` enriches each message dict with `chat_id` and `chat_name`. If a `--chat` name is ambiguous, the **default** is to pick the most recently active candidate, log a warning listing all candidates to stderr, and proceed (exit 0); pass `--strict` to opt into a non-zero exit with a stderr candidate list instead (see [`docs/features/telegram-messaging.md`](docs/features/telegram-messaging.md) for the disambiguation and project-stitching UX).

## Reading Email

Use `valor-email` to read and send email through the bridge. Reads hit the Redis history cache first (populated by the IMAP poll loop), falling back to a read-only IMAP fetch filtered to known senders. Sends always queue via `email:outbox:*` — the email relay (bundled into the email bridge process) drains the queue over SMTP with retry + DLQ.

```bash
valor-email read --limit 5
valor-email read --search "deployment" --since "2 hours ago"
valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
valor-email send --to alice@example.com --to bob@example.com "Message to both"
valor-email send --to alice@example.com --file ./report.pdf "See attached"
valor-email send --to alice@example.com --reply-to "<abc@host>" "Body"
valor-email threads
```

`--to` accepts multiple flags (repeat per recipient) and comma-separated values. To reply to a specific message, first run `valor-email read --json` and copy the `message_id` field — pass it verbatim to `--reply-to` (angle brackets optional; the CLI normalizes). Sends confirm with a queue notice; if delivery seems stuck, check `./scripts/valor-service.sh email-status` (extends to read the relay heartbeat under `email:relay:last_poll_ts`).

## Quick Commands

| Command | Description |
|---------|-------------|
| `./scripts/start_bridge.sh` | Start Telegram bridge |
| `./scripts/valor-service.sh status` | Check bridge status |
| `./scripts/valor-service.sh restart` | Restart bridge, watchdog, and worker after code changes |
| `./scripts/valor-service.sh worker-start` | Start standalone worker service |
| `./scripts/valor-service.sh worker-restart` | Restart standalone worker |
| `./scripts/valor-service.sh worker-status` | Check worker service status |
| `./scripts/valor-service.sh email-start` | Start the email bridge (IMAP polling) |
| `./scripts/valor-service.sh email-stop` | Stop the email bridge |
| `./scripts/valor-service.sh email-restart` | Restart the email bridge |
| `./scripts/valor-service.sh email-status` | Check email bridge status, IMAP last-poll age, and SMTP relay heartbeat |
| `./scripts/valor-service.sh email-dead-letter list` | List failed SMTP sends in dead-letter queue |
| `./scripts/valor-service.sh email-dead-letter replay --all` | Replay all dead-lettered emails |
| `tail -f logs/bridge.log` | Stream bridge logs |
| `pytest tests/` | Run all tests |
| `pytest tests/unit/` | Run unit tests only (fast, ~60s) |
| `pytest tests/unit/ -n auto` | Run unit tests in parallel |
| `pytest tests/integration/` | Run integration tests only |
| `pytest -m sdlc` | Run tests for a specific feature (see `tests/README.md`) |
| `python -m ruff format . && python -m ruff check .` | Format and lint |
| `python -m ui.app` | Start web UI server on localhost:8500 |
| `curl -s localhost:8500/dashboard.json` | Check the dashboard — full system state as JSON (sessions, health, reflections, machine) |
| `tail -f logs/worker.log` | Stream worker logs (includes reflection scheduler) |
| `python scripts/sdlc_reflection.py` | Run SDLC reflection manually |
| `python scripts/sdlc_reflection.py --dry-run` | Preview SDLC reflection without writing |
| `python scripts/sdlc_reflection.py --days 14` | Run with larger lookback window |
| `./scripts/install_sdlc_reflection.sh` | Install SDLC reflection launchd schedule |
| `tail -f logs/sdlc_reflection.log` | Stream SDLC reflection logs |
| `python scripts/autoexperiment.py --target observer --iterations 50` | Run autoexperiment on observer prompt |
| `python scripts/autoexperiment.py --target summarizer --dry-run` | Dry-run autoexperiment on the message drafter (target name is historical) |
| `python scripts/autoexperiment.py --list-targets` | List autoexperiment targets |
| `./scripts/install_autoexperiment.sh` | Install autoexperiment nightly schedule |
| `./scripts/install_nightly_tests.sh` | Install nightly regression test launchd schedule |
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
| `python -m tools.valor_session create --role pm --message "..."` | Create and enqueue a new session. `project_key` determines the repo via `projects.json`; there is no working-directory override flag. Precedence: `--project-key` > `--parent` inheritance > cwd match (raises on no match). Warns to stderr if no worker is running. |
| `python -m tools.valor_session create --role dev --slug {slug} --message "..."` | Create session with worktree isolation under the project's declared repo. Warns to stderr if no worker is running. |
| `python -m tools.valor_session resume --id <ID> --message "..."` | Resume a completed, killed, or failed session (hard-PATCH path; accepts session_id or agent_session_id) |
| `python -m tools.valor_session release --pr <N>` | Clear retain_for_resume after PR merge/close |
| `python -m tools.memory_search search "query"` | Search memories by query |
| `python -m tools.memory_search search "query" --category correction` | Search filtered by category |
| `python -m tools.memory_search search "query" --tag redis` | Search filtered by tag |
| `python -m tools.memory_search save "content"` | Save a new memory |
| `python -m tools.memory_search inspect --id <ID>` | Inspect a specific memory |
| `python -m tools.memory_search inspect --stats` | Show memory statistics |
| `python -m tools.memory_search forget --id <ID> --confirm` | Delete a memory |
| `python -m tools.memory_search status` | Check memory system health (Redis, counts, superseded ratio) |
| `python -m tools.memory_search status --json` | Memory health as machine-readable JSON |
| `python -m tools.memory_search status --deep` | Memory health with orphan index count and per-category confidence |
| `python -m tools.doctor` | Run all environment and health checks |
| `python -m tools.doctor --quick` | Skip slow checks (Telegram session, model verification) |
| `python -m tools.doctor --quality` | Include code quality checks (ruff, pytest) |
| `python -m tools.doctor --json` | Output health check results as JSON |
| `python -m tools.doctor --install-hook` | Install git pre-push hook running doctor --quick |
| `valor-youtube-search "query"` | Search YouTube for videos by query |
| `valor-youtube-search --limit N "query"` | Search YouTube with limited results |
| `valor-tts --text "Hello." --output /tmp/out.ogg` | Synthesize text to OGG/Opus (Kokoro local primary, OpenAI tts-1 fallback). See `docs/features/tts.md`. |
| `valor-tts --text "Hello." --output /tmp/out.ogg --voice af_bella` | Synthesize with a specific voice (catalog in `tools/tts/README.md`) |
| `valor-tts --text "Hello." --output /tmp/out.ogg --force-cloud` | Force the cloud (OpenAI tts-1) backend even if Kokoro is available |
| `valor-ingest <path-or-url>` | Convert a PDF/DOCX/PPTX/XLSX/HTML/image/YouTube URL into a `.md` sidecar the knowledge indexer picks up (see `docs/features/markitdown-ingestion.md`) |
| `valor-ingest --scan ~/work-vault/` | Backfill every convertible binary file in the vault recursively (audio formats deliberately excluded) |

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
- A PM-role AgentSession orchestrates; a Dev-role AgentSession executes
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
- The PM session steers the pipeline, invoking `/sdlc` skills as needed
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
- **SDLC sessions**: the PM session steers pipeline progression between stages
- **The PM session** orchestrates the Dev session's work; all messages route through the PM session
- Auto-continue caps are set to 50 as safety backstops (the PM session manages actual routing)
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
              (worker/__main__.py)         → Startup: index rebuild → recovery → orphan cleanup
                                           → Executes PM session (AgentSession session_type=pm, read-only)
                                               → PM creates Dev session via valor_session CLI
                                                   → Worker executes Dev session via CLI harness (claude -p → Claude API)
                                                   → _handle_dev_session_completion() → steers PM
                                           → Uses OutputHandler protocol (agent/output_handler.py)
                                           → TelegramRelayOutputHandler writes to Redis outbox
                                           → FileOutputHandler fallback for non-Telegram / dev environments
```
See `docs/features/bridge-worker-architecture.md` for the full bridge/worker separation design.

**Session Types** (see `docs/features/pm-dev-session-architecture.md`):
- **PM Session** (`session_type="pm"`) - Orchestrates work, PM persona, read-only
- **Teammate Session** (`session_type="teammate"`) - Conversational, Teammate persona
- **Dev Session** (`session_type="dev"`) - Does coding work, Dev persona, full permissions
- **Nudge loop** - Bridge output routing (deliver or nudge, no SDLC awareness)
- **Session Steering** (see `docs/features/session-steering.md`): `AgentSession.queued_steering_messages` is the steering inbox — any process writes messages, worker injects at turn boundary. `agent/output_router.py` contains routing decision logic extracted from executor. Use `valor-session steer --id <id> --message "..."` to steer externally.

**Subconscious Memory** (see `docs/features/subconscious-memory.md`):
- Human Telegram messages are saved as Memory records on receipt (importance=6.0)
- PostToolUse hook checks ExistenceFilter bloom and injects `<thought>` blocks via additionalContext
- Post-session Haiku extraction saves categorized observations (corrections/decisions at 4.0, patterns/surprises at 1.0)
- Intentional saves via `python -m tools.memory_search save "content"` for project-level learnings (7.0-8.0)
- Post-merge learning extraction distills PR takeaways into memories (importance=7.0)
- Outcome detection (bigram overlap) feeds ObservationProtocol to strengthen/weaken memories, plus dismissal tracking with importance decay
- Multi-query decomposition splits large keyword sets into clusters for broader retrieval coverage
- **Claude Code hooks** extend memory to CLI sessions via `.claude/hooks/hook_utils/memory_bridge.py` (see `docs/features/claude-code-memory.md`): UserPromptSubmit ingests prompts, PostToolUse recalls with file-based sliding window, Stop extracts observations
- All memory operations fail silently -- memory system never crashes the agent or hooks
- **Memory consolidation** (`memory-dedup` nightly reflection): Haiku-based semantic dedup merges near-duplicate records, sets `superseded_by` on originals (never deleted), filters superseded records from recall. Dry-run default — see `docs/features/subconscious-memory.md#memory-consolidation`

**Key Directories:**
- `.claude/commands/` - Slash command skills
- `.claude/agents/` - Subagent definitions (builder, validator, code-reviewer; dev-session removed — dev sessions created via valor_session CLI)
- `bridge/` - Telegram integration, nudge loop
- `worker/` - Standalone worker service (`python -m worker`)
- `agent/` - Session queue, SDK client, output router (`output_router.py`), output handler protocol, constants
- `tools/` - Local Python tools
- `config/` - Configuration files

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

See also: `docs/features/session-lifecycle.md` for the full 13-state reference (including `paused`, `paused_circuit`, `superseded`, `waiting_for_children`, and all terminal states).

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

**Check watchdog**: `python monitoring/bridge_watchdog.py --check-only`
**View crashes**: `python -c "from monitoring.crash_tracker import get_recent_crashes; print(get_recent_crashes(3600))"`
**Enable auto-revert**: `touch data/auto-revert-enabled` (disabled by default)

### Configuration Files

- `.env` - Symlink → `~/Desktop/Valor/.env` (do not write secrets here directly)
- `~/Desktop/Valor/projects.json` - Multi-project configuration (iCloud-synced, private)
- `.claude/settings.local.json` - Claude Code settings

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

## Plan Requirements (This Repo Only)

Plans created with `/do-plan` must include four required sections. These are enforced by hooks that block plan creation if sections are missing or empty.

### ## Documentation (Required)

Every plan must include a **## Documentation** section with actionable tasks specifying which docs to create or update. This is enforced by `.claude/hooks/validators/validate_documentation_section.py`.

The **## Documentation** section must contain:
- At least one checkbox task (`- [ ]`)
- A target documentation path (e.g., `docs/features/my-feature.md`)
- If genuinely no docs needed, explicitly state "No documentation changes needed" with justification

Example:
```markdown
## Documentation
- [ ] Create `docs/features/my-feature.md` describing the new capability
- [ ] Add entry to `docs/features/README.md` index table
```

The `/do-build` workflow validates that these docs were actually created before allowing PR merge.

### ## Update System (Required)

Include an **## Update System** section after **## No-Gos**. This system is deployed across multiple machines via the `/update` skill (`scripts/remote-update.sh`, `.claude/skills/update/`). New features frequently require complementary changes to the update process.

The **## Update System** section should cover:
- Whether the update script or update skill needs changes
- New dependencies or config files that must be propagated
- Migration steps for existing installations
- If no update changes are needed, state that explicitly (e.g., "No update system changes required — this feature is purely internal")

### ## Agent Integration (Required)

Include an **## Agent Integration** section after **## Update System**. The agent receives Telegram messages via the bridge (`bridge/telegram_bridge.py`) and reaches new functionality through one of two surfaces: a CLI entry point declared in `pyproject.toml [project.scripts]` (invoked via the agent's Bash tool), or a direct Python import the bridge calls internally. New Python functions in `tools/` are invisible to the agent until wired into one of those two paths.

The **## Agent Integration** section should cover:
- Whether a new CLI entry point is required in `pyproject.toml [project.scripts]` (e.g. `valor-tts = "tools.tts.cli:main"`)
- Whether the bridge itself needs to import/call the new code directly
- Integration tests that verify the agent can actually invoke the new tools
- If no agent integration is needed, state that explicitly (e.g., "No agent integration required — this is a bridge-internal change")

### ## Test Impact (Required)

Include a **## Test Impact** section after **## Failure Path Test Strategy** and before **## Rabbit Holes**. This section audits existing tests that will break or need changes due to the planned work. It is enforced by `.claude/hooks/validators/validate_test_impact_section.py`.

The **## Test Impact** section must contain:
- Checklist items listing affected test files/cases with dispositions: UPDATE, DELETE, or REPLACE
- If no existing tests are affected, explicitly state "No existing tests affected" with justification (50+ chars)

Example:
```markdown
## Test Impact
- [ ] `tests/unit/test_example.py::test_old_behavior` — UPDATE: assert new return value
- [ ] `tests/integration/test_flow.py::test_end_to_end` — REPLACE: rewrite for new API
```

Or for greenfield work:
```markdown
## Test Impact
No existing tests affected — this is a greenfield feature with no prior test coverage.
```

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

## Business Context

For business context, project notes, and assets see the work vault: `~/src/work-vault/AI Valor Engels System/`
