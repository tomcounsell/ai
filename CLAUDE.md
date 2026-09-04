# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Commands

Full catalog: [`docs/tools-reference.md`](docs/tools-reference.md). Every `tools.*` CLI, `sdlc-tool`, `valor-*` entrypoint, and `scripts/valor-service.sh` has informative `--help`; use it instead of memorizing invocations.

Non-obvious behavior that `--help` will not tell you:

- Use `scripts/pytest-clean.sh`, never bare `pytest`. The wrapper reaps xdist workers, bounds runs with `--timeout=420 --timeout-method=thread`, aborts on an off-pin venv or a worktree `.venv` lacking `bin/pytest`, and pins `PYTHONPATH` to the invoking checkout so worktree tests exercise worktree code. A full `tests/unit/` run legitimately takes about 20 minutes. See [`docs/features/worktree-venv-isolation.md`](docs/features/worktree-venv-isolation.md).
- Never clear pytest processes by pattern (`pkill -f pytest`). Several agents test on this machine at once; a pattern kill takes out their runs too. `scripts/reap-xdist.sh --apply` is the only sanctioned sweep. Blocked by `.claude/hooks/validators/validate_no_broad_process_kill.py`.
- `.python-version` is the committed, authoritative interpreter pin (intentionally NOT gitignored); `requires-python` in `pyproject.toml` is a floor, not a pin. `python -m tools.doctor` names every off-pin venv on the machine.
- `worker-stop` / `email-stop` are transient (launchd `KeepAlive` may relaunch). Use `worker-disable` / `email-disable` to keep a service down.
- `valor-session resume --id` accepts either a `session_id` or an `agent_session_id`. `valor-session create` resolves the repo from `project_key` via `projects.json`; precedence: `--project-key` > `--parent` inheritance > cwd match.
- After changing bridge, worker, or agent code: `./scripts/valor-service.sh restart`. Verify with `tail -5 logs/bridge.log` showing "Connected to Telegram".
- Hook registration in `.claude/settings.json` and `~/.claude/settings.json` is generated from `.claude/hooks/manifest.toml`; never hand-edit either `hooks` block. See [`docs/features/hook-manifest.md`](docs/features/hook-manifest.md).

## Manual Testing Hygiene

Never use raw Redis on Popoto-managed keys. All reads and writes go through the ORM (`Model.query.filter()`, `instance.save()`, `instance.delete()`). Enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`; the Redis is machine-global, so the guard stays armed everywhere except inside a different git checkout such as `~/src/popoto`. See [`docs/features/raw-redis-guard.md`](docs/features/raw-redis-guard.md).

When creating AgentSessions manually for testing, use a recognizable `project_key` prefix (`test-`, `dbg-`) and delete them afterward via the ORM, scoped by that key. Never run bulk operations unscoped.

For a standalone debug script run from an ambient shell: never point it at a test db with `os.environ.setdefault("REDIS_URL", ...)`. The ambient shell already carries the production `REDIS_URL`, so the setdefault is a no-op and the script silently writes to production. Assign `REDIS_URL` explicitly and assert the resolved db number before any write; follow `tests/db_claim.py::redis_test_url()`. Every repo-venv Python process carries a flush guard (`tools/redis_flush_guard.py`) that raises on `.flushdb()` against db 0 or any `.flushall()`; that error means the client resolved to production. Inside a pytest process this mistake cannot happen: `pytest_configure` exports the claimed db as `REDIS_URL` process-wide. See [`docs/features/redis-flush-hardening.md`](docs/features/redis-flush-hardening.md).

## Development Principles

1. **NO LEGACY CODE TOLERANCE**: overwrite, replace, delete. No commented-out code, no temporary bridges, no half-migrations, no historical artifacts in docs. Describe only the new status quo.
2. **CRITICAL THINKING MANDATORY**: question assumptions, anticipate consequences, prefer robust solutions over quick fixes.
3. **INTELLIGENT SYSTEMS OVER RIGID PATTERNS**: LLM intelligence and context-aware decisions, not keyword matching or static rules.
4. **COMMIT AND PUSH**: never leave work uncommitted at the end of a task.
5. **CONTEXT IS THE LIFEBLOOD**: explicitly pass context when spawning sub-agents; track the "why" alongside the "what".
6. **MINIMAL TOOLS**: loading all tools pollutes context and degrades performance. Start minimal, expand only if needed.
7. **DEFINITION OF DONE**: the authoritative list lives in [`.claude/skills-global/do-build/SKILL.md`](.claude/skills-global/do-build/SKILL.md).
8. **PARALLEL EXECUTION**: spawn parallel sub-agents for genuinely independent tasks only; aggregate results before reporting.
9. **SDLC PIPELINE**: an Eng-role AgentSession handles both orchestration and execution. `/sdlc` is a single-stage router: assess state, invoke ONE sub-skill, return. Gating reads of a PR's head SHA must resolve through `tools/pr_head_resolver.py::resolve_pr_head_sha`, never a bare `gh` read (see [`docs/features/gh-stale-state-verdict-gate.md`](docs/features/gh-stale-state-verdict-gate.md)). Ground truth on stages: [`.claude/skills-global/do-sdlc/SKILL.md`](.claude/skills-global/do-sdlc/SKILL.md).
10. **RESTART RUNNING SERVICES**: see the restart note under Commands.

## Development Workflow

Conversation first: chat arrives via Telegram or a local session. If it becomes real work, create a GitHub issue, then let the Eng session steer the pipeline via `/sdlc` one stage at a time. A 👍 reaction signals "done for now".

**Landing a hotfix on `main`**: a commit that puts code on `main` without a PR must declare its issue disposition: `Closes #N`, `Refs #N`, or `No-issue: <reason>`. Enforced by `.githooks/commit-msg` and `.githooks/pre-push`; `docs/plans/` commits are exempt. See [`docs/features/hotfix-issue-disposition.md`](docs/features/hotfix-issue-disposition.md).

**Auto-continue**: the agent pauses only for a legitimate open question requiring human input; status updates auto-continue. See [`docs/features/eng-session-architecture.md`](docs/features/eng-session-architecture.md).

## System Architecture

The Telegram bridge (`bridge/telegram_bridge.py`, Telethon) is I/O only: it enqueues AgentSessions to Redis, runs the nudge loop, and registers output callbacks. The standalone worker (`python -m worker`) is the sole session execution engine, one `claude -p` subprocess per turn. See [`docs/features/bridge-worker-architecture.md`](docs/features/bridge-worker-architecture.md) and [`docs/features/headless-session-runner.md`](docs/features/headless-session-runner.md).

**Session types** (see [`docs/features/eng-session-architecture.md`](docs/features/eng-session-architecture.md)):

| Type | Role |
|------|------|
| `eng` | SDLC work and conversational responses, full permissions, engineer persona |
| `teammate` | Conversational, Teammate persona; writes restricted to docs/meta paths. See [`docs/features/teammate-session-permissions.md`](docs/features/teammate-session-permissions.md) |

Steering goes through the Redis steering list (`agent/steering.py`); the worker drains it at turn boundaries. See [`docs/features/session-steering.md`](docs/features/session-steering.md).

**Subconscious memory** ([`docs/features/subconscious-memory.md`](docs/features/subconscious-memory.md)): messages are saved on receipt; a hook injects `<thought>` stubs and the agent pulls full bodies via `memory_get`/`memory_search`; post-session extraction saves categorized observations. `memory-decay-prune` tier-1 hard-delete stays off unless `MEMORY_DECAY_PRUNE_APPLY=true`. All memory operations fail silently.

**Key directories:** `bridge/`, `worker/`, `agent/`, `tools/`, `config/`, `.claude/commands/`, `.claude/agents/`.

## Global vs. Project-Only Skills

This repo is the canonical source for skills that ship to every machine.

| Directory | Scope | Synced? |
|-----------|-------|---------|
| `.claude/skills-global/` | Global / general-purpose | ✅ Hardlinked to `~/.claude/skills/` by `/update` |
| `.claude/skills/` | Project-only | ❌ Never synced |

"Make this a global skill" means put it in `.claude/skills-global/`; adding a directory with a `SKILL.md` there is the entire registration step. When moving a skill between the two directories, add a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py` so stale hardlinks are cleaned fleet-wide. Global bodies stay generic; repo-specific behavior layers in via `.claude/skill-context/{skill}.md` or `docs/sdlc/{skill}.md`. See [`docs/features/skill-context-convention.md`](docs/features/skill-context-convention.md). `setup`, `prime`, and `do-deploy` stay project-only; `sdlc` is a thin router shim over the global `do-sdlc`.

## Testing Philosophy

- **Real integration testing**: no mocks, use actual APIs.
- **Intelligence validation**: use AI judges, not keyword matching.

## Work Completion Criteria

Work is DONE when:
1. ✅ Deliverable exists and works
2. ✅ Code quality standards met (`python -m ruff check`, `python -m ruff format`)
3. ✅ Changes committed and pushed to git
4. ✅ Original request fulfilled

## Session Management

Fresh messages create new sessions scoped by thread ID or local session ID; reply-to resumes the original session. Full lifecycle: [`docs/features/session-lifecycle.md`](docs/features/session-lifecycle.md). Task lists are isolated via `CLAUDE_CODE_TASK_LIST_ID`; planned work gets a durable slug-scoped list, and the lane slug ties together task list, branch, and worktree (`.worktrees/{slug}/`, branch `session/{slug}`, `agent/worktree_manager.py`). The plan doc is linked by `tracking:` frontmatter, not filename. See [`docs/features/sdlc-lane-identity.md`](docs/features/sdlc-lane-identity.md) and [`docs/features/session-isolation.md`](docs/features/session-isolation.md).

## Self-Healing

The bridge auto-recovers from crashes: startup lock cleanup, watchdog service, crash tracker with git correlation, escalation, and an update-loop-wedged detector. A wedge verdict needs positive missed-message evidence, never mere silence. Auto-revert is disabled unless `data/auto-revert-enabled` exists. See [`docs/features/bridge-self-healing.md`](docs/features/bridge-self-healing.md). Recovery entry points: `./scripts/valor-service.sh restart`, `worker-restart`, `python scripts/telegram_login.py`.

## Configuration Files

- `.env`: symlink to `~/Desktop/Valor/.env`. Do not write secrets here directly.
- `~/Desktop/Valor/projects.json`: multi-project configuration (iCloud-synced, private).
- `.claude/settings.local.json`: Claude Code settings.

Tunable timing, retry, and TTL values live in `config/settings.py` (`TimeoutSettings`, `TIMEOUTS__*` env keys). See [`docs/features/config-timeout-catalog.md`](docs/features/config-timeout-catalog.md).

## Single-Machine Ownership (Strict)

Every bridge-contact identifier in `projects.json` is owned by exactly one machine; `projects.<key>.machine` is the source of truth and every other identifier inherits from its project. Enforced by `bridge/config_validation.py::validate_projects_config`; the update script blocks the bridge restart on a malformed config. See [`docs/features/single-machine-ownership.md`](docs/features/single-machine-ownership.md).

## Secrets

All secrets go in `~/Desktop/Valor/.env`, never `repo/.env`. To add one: vault `.env`, a commented placeholder in `.env.example`, and a field in `config/settings.py`. Every `.env.example` declaration is required unless its comment block carries a bare `# @optional` line (a traced read site with an in-code default, never a credential); never derive markers from what the check flags on your own machine. A key read only by an external binary needs `# @passthrough <binary>`. See [`docs/features/env-completeness-validation.md`](docs/features/env-completeness-validation.md).

**1Password (`op`) is always non-interactive**: the `valor-local` service account authenticates via `OP_SERVICE_ACCOUNT_TOKEN` from the vault `.env` (vault of record: `m-valor`), with `OP_CACHE=false` globally. Never write automation that depends on a human approving a 1Password prompt, and never use `op signin` in automation; if `op` cannot authenticate non-interactively, fail closed and report. When reconciling a secret between `.env` and the vault, verify the credential against its own API first ("newer wins" has destroyed a working key here), and never echo a secret or any prefix of one to stdout; compare by SHA-256 fingerprint.

## GitHub Issue Labels

`bug` (broken behavior), `reflections`, `memory`, `skills` (skills/tools/SDLC), `dashboard`, `bridge`, `testing`, `upvote` (pre-approved for autonomous SDLC pickup). No `feature` label; it adds no signal.

## Knowledge Base (KB)

Pull from both sources before answering substantive questions.

**Vault** (curated, what humans wrote): `~/work-vault/AI Valor Engels System/`, indexed by that directory's `README.md`. Ingest binaries with `valor-ingest <path>`.

**Memory** (Redis, what the agent learned): project key `valor`. Search via `python -m tools.memory_search search "<query>" --project valor` or the `mcp__memory__*` tools.

Both partition by project; don't leak cross-project context. See [`docs/conventions/knowledge-base-section.md`](docs/conventions/knowledge-base-section.md).

## See Also

| Resource | Purpose |
|----------|---------|
| `/prime` | Architecture deep dive and onboarding |
| `/setup` | New machine configuration |
| [`docs/features/README.md`](docs/features/README.md) | Feature index |
| [`docs/sdlc/`](docs/sdlc/) | Per-stage repo-specific addenda |
| [`docs/tools-reference.md`](docs/tools-reference.md) | Complete tool documentation |
| [`tests/README.md`](tests/README.md) | Test suite index |
| `config/identity.json` | Structured identity data |
