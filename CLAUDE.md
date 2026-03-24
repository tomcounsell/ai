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

## Reading Telegram Messages

Use Telethon directly to read messages from any chat. The bridge session file provides access.

```python
python -c "
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os
load_dotenv()
client = TelegramClient('data/valor_bridge', int(os.getenv('TELEGRAM_API_ID')), os.getenv('TELEGRAM_API_HASH'))
async def main():
    await client.start(phone=os.getenv('TELEGRAM_PHONE'))
    async for d in client.iter_dialogs():
        if 'CHAT_NAME' in (d.name or ''):
            for m in reversed(await client.get_messages(d.id, limit=10)):
                sender = 'Valor' if m.out else (getattr(m.sender, 'first_name', None) or 'Unknown')
                print(f'[{m.date}] {sender}: {(m.text or \"\")[:300]}')
            break
    await client.disconnect()
asyncio.run(main())
"
```

Replace `CHAT_NAME` with the group name (e.g. `PM: PsyOptimal`, `Dev: Valor`). This reads real messages from Telegram — the CLI history tool (`valor-history`) only shows messages stored in Redis, which may be incomplete.

## Quick Commands

| Command | Description |
|---------|-------------|
| `./scripts/start_bridge.sh` | Start Telegram bridge |
| `./scripts/valor-service.sh status` | Check bridge status |
| `./scripts/valor-service.sh restart` | Restart after code changes |
| `tail -f logs/bridge.log` | Stream bridge logs |
| `pytest tests/` | Run all tests |
| `pytest tests/unit/` | Run unit tests only (fast, ~60s) |
| `pytest tests/unit/ -n auto` | Run unit tests in parallel |
| `pytest tests/integration/` | Run integration tests only |
| `pytest -m sdlc` | Run tests for a specific feature (see `tests/README.md`) |
| `python -m ruff format . && python -m ruff check .` | Format and lint |
| `python -m ui.app` | Start web UI server on localhost:8500 |
| `python scripts/reflections.py` | Run reflections maintenance manually |
| `python scripts/reflections.py --dry-run` | Test reflections without side effects |
| `python scripts/reflections.py --ignore "pattern"` | Silence a bug pattern for 14 days |
| `./scripts/install_reflections.sh` | Install reflections launchd schedule |
| `tail -f logs/reflections.log` | Stream reflections logs |
| `python scripts/issue_poller.py` | Run issue poller manually (polls GitHub for new issues) |
| `./scripts/install_issue_poller.sh` | Install issue poller launchd schedule (5-min interval) |
| `tail -f logs/issue_poller.log` | Stream issue poller logs |
| `python scripts/autoexperiment.py --target observer --iterations 50` | Run autoexperiment on observer prompt |
| `python scripts/autoexperiment.py --target summarizer --dry-run` | Dry-run autoexperiment on summarizer |
| `python scripts/autoexperiment.py --list-targets` | List autoexperiment targets |
| `./scripts/install_autoexperiment.sh` | Install autoexperiment nightly schedule |
| `python -m tools.job_scheduler kill --job-id <ID>` | Kill a running or pending job by ID |
| `python -m tools.job_scheduler kill --session-id <ID>` | Kill a job by session ID |
| `python -m tools.job_scheduler kill --all` | Kill all running and pending jobs |

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
- ChatSession (PM persona) orchestrates; DevSession (Dev persona) executes
- Bridge uses nudge loop for output routing (no SDLC awareness in bridge)
- `/sdlc` is a **single-stage router**: it assesses state, invokes ONE sub-skill, and returns
- NEVER write code, run tests, or create plans directly -- always delegate through sub-skills
- See `.claude/skills/sdlc/SKILL.md` for the ground truth on pipeline stages

### 10. ALWAYS RESTART RUNNING SERVICES
- If bridge is running and you modify bridge/agent code, restart immediately after committing
- Restart: `./scripts/valor-service.sh restart`
- Verify: `tail -5 logs/bridge.log` shows "Connected to Telegram"

## Development Workflow

The standard flow from conversation to shipped feature:

### Phase 1: Conversation
- Chat arrives via Telegram (or local Claude Code session)
- Could be Q&A, exploring an idea, or raising an issue
- No branch, no task list, no slug yet — just conversation
- If it's a real piece of work: create a GitHub issue

### Phase 2: SDLC (triggered by work request)
- ChatSession steers the pipeline, invoking `/sdlc` skills as needed
- `/sdlc` assesses current state, invokes ONE sub-skill, and returns
- Stages: Plan -> Build -> Test -> Patch -> Review -> Patch -> Docs -> Merge
- See `.claude/skills/sdlc/SKILL.md` for the ground truth on stage definitions

### Phase 3: Review & Merge
- Valor may or may not be asked to merge the PR after human review
- Thumbs-up emoji reaction (👍) signals "done for now" / final completion

### Auto-Continue Rules
- The agent should only pause if there is a **legitimate open question** requiring human input
- If there is no question -- just a status update -- the summarizer auto-sends "continue"
- Status updates without questions or signs of completion are NOT stopping points
- The agent keeps working until the phase is complete or it's genuinely blocked
- **SDLC jobs**: ChatSession steers pipeline progression between stages
- **ChatSession** orchestrates DevSession work; all messages route through ChatSession
- Auto-continue caps are set to 50 as safety backstops (ChatSession manages actual routing)
- The auto-continue counter resets when the human sends a new message

### Session Continuity
- Full session logs are saved at all breakpoints for later analysis
- Telegram chat history is stored in Redis via Popoto ORM for fast review anytime
- Reply-to messages in Telegram resume the original session context

## System Architecture

```
Telegram → Python Bridge (Telethon) → ChatSession (read-only, PM persona)
              (bridge/telegram_bridge.py)     → Nudge loop (bridge has no SDLC awareness)
                                              → Spawns DevSession (full permissions, Dev persona)
                                                    → Claude Agent SDK → Claude API
                                                        (agent/sdk_client.py)
```

**Session Types** (see `docs/features/chat-dev-session-architecture.md`):
- **ChatSession** (`session_type="chat"`) - Orchestrates work, PM persona, read-only
- **DevSession** (`session_type="dev"`) - Does coding work, Dev persona, full permissions
- **Nudge loop** - Bridge output routing (deliver or nudge, no SDLC awareness)

**Subconscious Memory** (see `docs/features/subconscious-memory.md`):
- Human Telegram messages are saved as Memory records on receipt (importance=6.0)
- PostToolUse hook checks ExistenceFilter bloom and injects `<thought>` blocks via additionalContext
- Post-session Haiku extraction saves agent observations (importance=1.0)
- Outcome detection (bigram overlap) feeds ObservationProtocol to strengthen/weaken memories
- All memory operations fail silently -- memory system never crashes the agent

**Key Directories:**
- `.claude/commands/` - Slash command skills
- `.claude/agents/` - Subagent definitions (including `dev-session`)
- `bridge/` - Telegram integration, nudge loop
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

- `.env` - Environment variables and API keys
- `~/Desktop/Valor/projects.json` - Multi-project configuration (iCloud-synced, private)
- `.claude/settings.local.json` - Claude Code settings

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

Include an **## Agent Integration** section after **## Update System**. The agent receives Telegram messages via the bridge (`bridge/telegram_bridge.py`) and can only use tools exposed through MCP servers registered in `.mcp.json`. New Python functions in `tools/` are invisible to the agent unless wrapped.

The **## Agent Integration** section should cover:
- Whether a new or existing MCP server needs to expose the functionality
- Changes to `.mcp.json` or `mcp_servers/` directory
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
| `config/SOUL.md` | Valor persona and philosophy |
| `docs/features/README.md` | Feature index — look up how things work |
| `tests/README.md` | Test suite index — feature markers, blind spots, contribution guide |

## Business Context

For business context, project notes, and assets see the work vault: `~/src/work-vault/AI Valor Engels System/`
