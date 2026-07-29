# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Google Workspace CLI (`gws`)

On PATH after `npm install -g @googleworkspace/cli` (installed automatically on every machine by `/update`). **Not pre-authenticated** — first use requires a one-time human OAuth step: `gws auth setup` then `gws auth login`. If `gws` is present but unauthenticated, fall through to the next tool in the ladder (Gmail/Calendar/Drive MCP, then BYOB) rather than stalling.

Command reference: `gws --help`, and `gws schema <service.resource.method>` to discover params for any method. The full usage/flags/patterns reference lives in `~/.claude/CLAUDE.md`, which loads on every machine and in every project.

## Quick Commands

Everyday commands:

| Command | Description |
|---------|-------------|
| `./scripts/valor-service.sh restart` | Restart bridge, watchdog, and worker after code changes |
| `./scripts/valor-service.sh status` | Check bridge status |
| `scripts/pytest-clean.sh tests/unit/` | Run tests via the wrapper that reaps xdist workers (prefer over bare `pytest`) |
| `python -m ruff format . && python -m ruff check .` | Format and lint |
| `curl -s localhost:8500/dashboard.json` | Full system state as JSON |
| `python -m tools.doctor --quick` | Environment and health checks |
| `tail -f logs/bridge.log` | Stream bridge logs |

**The full command table** (valor-session, sdlc-tool, memory, reflections, analytics,
TTS/video/ingest, computer-use, email bridge, and every flag) lives in the `repo-commands`
skill. Invoke it when you need an exact invocation instead of guessing.

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
- Agent gating reads of a PR's head SHA must resolve through `tools/pr_head_resolver.py::resolve_pr_head_sha` (git-first via `git ls-remote refs/pull/N/head`), never a bare `gh` read — a stale `gh` head SHA matches the recorded verdict's trailer and flips the verdict-staleness gate from fail-closed to fail-open (#2404; see `docs/features/gh-stale-state-verdict-gate.md`)
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

Telegram → bridge (I/O only, no SDLC awareness) → AgentSession in Redis → standalone
worker → Eng session via the headless session runner (one `claude -p` per turn).

Read these before changing anything in `bridge/`, `worker/`, or `agent/`:
- `docs/features/bridge-worker-architecture.md` — bridge/worker separation, startup and recovery order
- `docs/features/headless-session-runner.md` — the `claude -p` per-turn model, PM/dev subagent continuation
- `docs/features/eng-session-architecture.md` — session types and permissions

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

## Global vs. Project-Only Skills

This repo is the canonical source for skills that ship to **every machine**. There are two skill directories, and the distinction matters:

| Directory | Scope | Synced? |
|-----------|-------|---------|
| `.claude/skills-global/` | **Global / general-purpose skills** | ✅ Hardlinked to `~/.claude/skills/` on every machine by `/update` |
| `.claude/skills/` | **Project-only skills** — tightly coupled to this repo's infra (Telegram bridge, macOS Messages, system logs) | ❌ Never synced; only work in this repo's context |

**Terminology:** When someone says "make this a **global skill**" or "**general-purpose skill**," they mean: *put it in `.claude/skills-global/` so the `/update` wiring propagates it to `~/.claude/skills/` on every machine.* It does NOT mean editing a `CLAUDE.md` note. A skill is "known to every machine" precisely when it lives in `skills-global/`.

**The sync wiring** lives in `scripts/update/hardlinks.py`:
- `sync_claude_dirs()` hardlinks every skill dir under `.claude/skills-global/` into `~/.claude/skills/`. Adding a directory with a `SKILL.md` there is all that's required — no registration step.
- Project-only skills under `.claude/skills/` are excluded *structurally*: that directory is never a sync source. The `test_no_project_only_skill_is_a_sync_destination` unit test asserts the invariant against the live filesystem.
- `RENAMED_REMOVALS` removes stale user-level copies when a skill is renamed or moved between the two dirs. **When you move a skill between `skills/` and `skills-global/`, add a `RENAMED_REMOVALS` entry** so the old hardlink is cleaned up on every machine.

Example: `/do-debrief` (the TTS composite that wraps `valor-tts`) lives in `.claude/skills-global/do-debrief/` — that's why every machine already knows it. The client-facing CMA skills `/imagine-agent` and `/build-agent` follow the same pattern. A skill that only ever runs against the local bridge (e.g. `telegram`, `checking-system-logs`) stays in `.claude/skills/`.

**Repo-specific behavior via the skill-context seam:** Global skill bodies stay generic. Repo-specific behavior is layered in via `.claude/skill-context/{skill}.md` (non-SDLC skills) or `docs/sdlc/{skill}.md` (SDLC pipeline skills). If the file is absent — the common case in any foreign repo — the skill runs its generic baseline. If the file is present, the skill reads it and honors its declarations. Every coupled skill body carries the canonical probe sentence: `"If <context-path> exists, read it and honor its declarations; otherwise use the generic defaults described below."` The `rule_13_coupling_signals` guard in `audit-skills` enforces probe presence for any body that references ai-repo executables (`sdlc-tool`, `valor-*`, `python -m tools.*`, etc.). See [`docs/features/skill-context-convention.md`](docs/features/skill-context-convention.md) for the full reference.

**Bucket C (project-only infrastructure skills):** Some skills are too tightly coupled to this repo's infrastructure to generalize even with a probe step. `setup`, `prime`, `sdlc`, and `do-deploy` live in `.claude/skills/` (project-only) rather than `.claude/skills-global/`. They are never synced to `~/.claude/skills/` on other machines. If you move a skill into this category, add a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py` to remove the stale hardlink on every machine.

## Testing Philosophy

- **Real integration testing** - No mocks, use actual APIs
- **Intelligence validation** - Use AI judges, not keyword matching
- **Quality gates**: Unit 100%, Integration 95%, E2E 90%

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
- **4-level escalation, plus a decoupled human-alert signal** (#2396): restart → kill stale → clear locks → revert commit; a crash-storm alert (`human_alert_needed`) and a reason-aware restart throttle (`restart_circuit_open`) fire independently of `recovery_level` so a recurring wedge's capped restart is never silently overridden
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

### 1Password (`op`) — always non-interactive

The `op` CLI authenticates via the **`valor-local` service account**, using
`OP_SERVICE_ACCOUNT_TOKEN` from `~/Desktop/Valor/.env`. Vault of record:
`m-valor` (`r2f54evinfnkzr6vgqlo3x5rry`).

**Never write a skill, runbook, script, or `/update` step that depends on a human
approving a 1Password prompt.** The desktop-app integration (Touch ID / biometric
unlock) is not an execution path: its session expires in about a minute and needs
someone at the keyboard, while the worker runs headless under launchd with nobody
to prompt. Do not use `op signin` or `op account add` in automation.

```bash
# The sanctioned shape — token from env, cache off, batched resolve.
OP_CACHE=false op run --env-file=<template> --no-masking -- <command>
OP_CACHE=false op read "op://m-valor/<item>/credential"
```

`OP_CACHE=false` is required globally: the cache daemon trips TCC dialogs under
launchd. If an `op` call cannot authenticate non-interactively, fail closed and
report — do not fall back to a prompt.

**Reconciling a secret between `.env` and the vault:** verify the credential
against its own API before adopting either side. "Newer wins" is not safe — it
has already destroyed a working key here. And never echo a secret value, or any
prefix of one, to stdout; Bash output persists to session transcripts. Compare
by SHA-256 fingerprint instead.

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
