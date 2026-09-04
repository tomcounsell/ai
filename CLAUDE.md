# CLAUDE.md

Guidance for Claude Code when working with this repository.

**IMPORTANT CONTEXT**: You ARE this unified conversational development environment. When the user (Valor Engels) talks to you, they are talking TO the codebase itself. Respond as the embodiment of this AI system.

## Commands

Full catalog: [`docs/tools-reference.md`](docs/tools-reference.md). Every `tools.*` CLI, `sdlc-tool`, `valor-*` entrypoint, and `scripts/valor-service.sh` has informative `--help`; use it instead of memorizing invocations.

Non-obvious behavior that `--help` will not tell you:

- Use `scripts/pytest-clean.sh`, never bare `pytest`. The wrapper reaps xdist workers; interrupted bare runs leave orphan workers eating memory. See the reaper note in `pyproject.toml`. Runs are bounded by `--timeout=420 --timeout-method=thread`, so a stuck test becomes a named failure instead of a hang that destroys the run's summary.
- Never clear pytest processes by pattern (`pkill -f pytest`, `kill -9 $(pgrep -f bin/pytest)`). Several agents test on this machine at once and a pattern kill takes out their runs too, leaving a SIGKILLed controller with no summary that reads like a memory ceiling or a poisonous tail test. `scripts/reap-xdist.sh --apply` is the only sanctioned sweep; it spares any run whose parent is still alive. Blocked by `.claude/hooks/validators/validate_no_broad_process_kill.py`. A full `tests/unit/` run legitimately takes about 20 minutes, so a long-running suite is not stuck.
- `.python-version` is the committed, authoritative interpreter pin — it is intentionally NOT gitignored, and `requires-python` in `pyproject.toml` is a dependency floor, not a pin. A bare `uv sync` / `uv venv` in any checkout or worktree therefore lands on the pinned version. `scripts/pytest-clean.sh` aborts on an off-pin venv and `python -m tools.doctor` names every one on the machine. See [`docs/features/worktree-venv-isolation.md`](docs/features/worktree-venv-isolation.md).
- `worker-stop` / `email-stop` are transient (`bootout` only) and launchd's `KeepAlive` may relaunch. Use `worker-disable` / `email-disable` to keep a service down.
- `valor-session resume --id` accepts either a `session_id` or an `agent_session_id`.
- `valor-session create` resolves the repo from `project_key` via `projects.json`; there is no working-directory override. Precedence: `--project-key` > `--parent` inheritance > cwd match.
- After changing bridge, worker, or agent code: `./scripts/valor-service.sh restart` (cycles bridge, watchdog, worker). Verify with `tail -5 logs/bridge.log` showing "Connected to Telegram".
- Hook registration in `.claude/settings.json` and `~/.claude/settings.json` is generated from `.claude/hooks/manifest.toml` — never hand-edit either `hooks` block. See [`docs/features/hook-manifest.md`](docs/features/hook-manifest.md).

## Manual Testing Hygiene

Never use raw Redis on Popoto-managed keys. All reads (`hgetall`, `hget`, `scan_iter`) and writes (`delete`, `srem`, `sadd`, `zrem`) go through the ORM (`Model.query.filter()`, `instance.save()`, `instance.delete()`). Enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`, which stands down only when the Bash call's cwd resolves inside a *different* git checkout (`~/src/popoto`, where raw Redis is legitimate) and only for commands that could actually execute. A cwd belonging to no repo at all, such as `/tmp`, keeps the guard armed: the Redis is machine-global. See [`docs/features/raw-redis-guard.md`](docs/features/raw-redis-guard.md).

When creating AgentSessions manually to test worker or queue behavior, use a recognizable `project_key` prefix (`test-`, `dbg-`) and delete them afterward via the ORM, scoped by that key. Never run bulk operations unscoped.

**This paragraph is about a standalone debug script run from an ambient shell, not from inside a pytest process.** Never point such a script at a test db with `os.environ.setdefault("REDIS_URL", ...)`. `setdefault` is a no-op when the key is already set, and an ambient shell always carries a production `REDIS_URL`, so a script that means to "default" to a test db silently keeps the production one instead. Assign `REDIS_URL` explicitly and assert the resolved db number before any write. Every Python process started inside a repo venv also carries an ambient flush guard (`tools/redis_flush_guard.py`) that raises `RuntimeError` on `.flushdb()` against db 0 or any `.flushall()`; that error means the client resolved to production, not that the guard is malfunctioning. For a script that genuinely needs a test db, follow `tests/db_claim.py`'s `redis_test_url()` / `tests/conftest.py`'s `_redis_test_db_num()` idiom, which matches the per-process claimed db the `redis_test_db` fixture already picked. See [`docs/features/redis-flush-hardening.md`](docs/features/redis-flush-hardening.md). **Inside a pytest process this class of mistake cannot happen**: `pytest_configure` exports the claimed db as `REDIS_URL` process-wide, so any code that resolves `REDIS_URL` — a subprocess spawned with no `env=`, or an in-process module reading it lazily — already sees the claimed db, not production. The distinction is the process boundary: a script invoked by hand from the shell has no `pytest_configure` to run for it.

## Development Principles

1. **NO LEGACY CODE TOLERANCE** — overwrite, replace, delete. No commented-out code, no "temporary" bridges, no half-migrations, no parallel-run migrations, no historical artifacts in docs. Describe only the new status quo.
2. **CRITICAL THINKING MANDATORY** — foolish optimism is not allowed. Question assumptions, anticipate consequences, prefer robust solutions over quick fixes.
3. **INTELLIGENT SYSTEMS OVER RIGID PATTERNS** — LLM intelligence and context-aware decisions, not keyword matching or static rules.
4. **COMMIT AND PUSH** — never leave work uncommitted at the end of a task.
5. **CONTEXT IS THE LIFEBLOOD** — explicitly pass context when spawning sub-agents; track the "why" alongside the "what".
6. **MINIMAL TOOLS** — loading all tools pollutes context and degrades performance. Start minimal, expand only if needed.
7. **DEFINITION OF DONE** — the authoritative list lives in [`.claude/skills-global/do-build/SKILL.md`](.claude/skills-global/do-build/SKILL.md) and is enforced by `/do-build` and the builder agent.
8. **PARALLEL EXECUTION** — spawn parallel sub-agents for genuinely independent tasks; never for sequential or dependent work. Aggregate results before reporting.
9. **SDLC PIPELINE** — an Eng-role AgentSession handles both orchestration and execution. `/sdlc` is a **single-stage router**: assess state, invoke ONE sub-skill, return. Never write code, run tests, or create plans directly; always delegate through sub-skills. Agent gating reads of a PR's head SHA must resolve through `tools/pr_head_resolver.py::resolve_pr_head_sha` (git-first via `git ls-remote refs/pull/N/head`), never a bare `gh` read: a stale `gh` head SHA matches the recorded verdict's trailer and flips the verdict-staleness gate from fail-closed to fail-open (see [`docs/features/gh-stale-state-verdict-gate.md`](docs/features/gh-stale-state-verdict-gate.md)). The same rule now governs a second gate: a plan's `## Verification` rows grade `PASS` / `FAIL` / `UNEVALUATED`, and `tools/merge_predicate.py` refuses to merge on a `FAIL` or `UNEVALUATED` row, reading the aggregate the runner recorded rather than re-executing anything. That aggregate is trusted only while its stamped head SHA matches the PR's current head; a mismatched, absent, or unresolvable SHA refuses. Each consumer owns its own disposition for `UNEVALUATED` rather than reading a per-row marker: the merge predicate may never let one ship, and the build gate blocks on one today (see [`docs/features/machine-readable-dod.md`](docs/features/machine-readable-dod.md)). The aggregate is recorded at REVIEW, the first stage with a PR head to stamp it against; BUILD grades the same table but records nothing. Ground truth on stages: [`.claude/skills-global/do-sdlc/SKILL.md`](.claude/skills-global/do-sdlc/SKILL.md).
10. **RESTART RUNNING SERVICES** — see the restart note under Commands.

## Development Workflow

Conversation first: chat arrives via Telegram or a local Claude Code session, and may be Q&A, exploration, or raising an issue. No branch or slug yet. If it turns out to be real work, create a GitHub issue, then let the Eng session steer the pipeline by invoking `/sdlc` one stage at a time. A 👍 reaction signals "done for now".

**Landing a hotfix on `main`**: a commit that puts code on `main` without a PR must declare what it does to the issue tracker — `Closes #N`, `Refs #N` (touches but does not resolve), or `No-issue: <reason>`. A bare `#N` mention closes nothing and records nothing. Enforced by `.githooks/commit-msg` (authored on `main`) and `.githooks/pre-push` (pushed to `main` from a side branch); `docs/plans/` commits are exempt. See [`docs/features/hotfix-issue-disposition.md`](docs/features/hotfix-issue-disposition.md).

**Auto-continue**: the agent pauses only for a **legitimate open question** requiring human input. Status updates without questions are NOT stopping points; the message drafter auto-sends "continue". Caps are set to 50 purely as safety backstops (the Eng session manages actual routing), and the counter resets when the human sends a new message. Full nudge-loop behavior: [`docs/features/eng-session-architecture.md`](docs/features/eng-session-architecture.md).

## System Architecture

The Telegram bridge (`bridge/telegram_bridge.py`, Telethon) is I/O only: it enqueues AgentSessions to Redis, runs the nudge loop, and registers output callbacks. It has no SDLC awareness. The standalone worker (`python -m worker`) is the sole session execution engine, running one `claude -p` subprocess per turn via the headless session runner. See [`docs/features/bridge-worker-architecture.md`](docs/features/bridge-worker-architecture.md) and [`docs/features/headless-session-runner.md`](docs/features/headless-session-runner.md).

**Session types** (see [`docs/features/eng-session-architecture.md`](docs/features/eng-session-architecture.md)):

| Type | Role |
|------|------|
| `eng` | SDLC work and conversational responses, full permissions, engineer persona |
| `teammate` | Conversational, Teammate persona. Bash open and audit-logged; writes restricted to docs/meta paths, source-code writes redirect to an Eng session. See [`docs/features/teammate-session-permissions.md`](docs/features/teammate-session-permissions.md) |

Steering goes through the Redis steering list (`agent/steering.py`), the sole steering inbox; the worker drains it at turn boundaries. See [`docs/features/session-steering.md`](docs/features/session-steering.md).

**Subconscious memory** ([`docs/features/subconscious-memory.md`](docs/features/subconscious-memory.md)): human messages are saved on receipt; a PostToolUse hook injects compact `<thought>` stubs and the agent pulls full bodies on demand via the `memory_get`/`memory_search` MCP tools; post-session Haiku extraction and post-merge learning extraction save categorized observations. Nightly `memory-dedup` sets `superseded_by` rather than deleting. `memory-decay-prune` tier-1 hard-delete is **off** unless `MEMORY_DECAY_PRUNE_APPLY=true` is set explicitly (it never inherits `params.apply`); tier-2 tombstoning is apply-by-default because it is reversible. All memory operations fail silently and never crash the agent.

**Key directories:** `bridge/` (Telegram, nudge loop), `worker/` (execution engine), `agent/` (queue, output routing, handlers), `tools/`, `config/`, `.claude/commands/`, `.claude/agents/`.

## Global vs. Project-Only Skills

This repo is the canonical source for skills that ship to every machine.

| Directory | Scope | Synced? |
|-----------|-------|---------|
| `.claude/skills-global/` | Global / general-purpose | ✅ Hardlinked to `~/.claude/skills/` by `/update` |
| `.claude/skills/` | Project-only, coupled to this repo's infra | ❌ Never synced |

**Terminology:** "make this a **global skill**" or "**general-purpose skill**" means *put it in `.claude/skills-global/`*. It does not mean editing a CLAUDE.md note. A skill is known to every machine precisely when it lives there.

Sync wiring is `scripts/update/hardlinks.py`. Adding a directory with a `SKILL.md` under `skills-global/` is the entire registration step. **When you move a skill between the two directories, add a `RENAMED_REMOVALS` entry** so the stale hardlink is cleaned up on every machine — the same entry also sweeps repo-local rename residue (a gitignored `__pycache__` keeping the old skill directory alive in this checkout) from both skill roots.

Global skill bodies stay generic; repo-specific behavior layers in via `.claude/skill-context/{skill}.md` (non-SDLC) or `docs/sdlc/{skill}.md` (SDLC stages). Coupled bodies carry the probe sentence "If <context-path> exists, read it and honor its declarations; otherwise use the generic defaults described below." See [`docs/features/skill-context-convention.md`](docs/features/skill-context-convention.md).

Some skills are too coupled to generalize even with a probe: `setup`, `prime`, and `do-deploy` stay project-only. The `sdlc` skill is now a thin `context: fork` router shim over the global `do-sdlc` skill — the substantive SDLC body lives in `.claude/skills-global/do-sdlc/` with this repo's specifics in `docs/sdlc/do-sdlc.md`.

## Testing Philosophy

- **Real integration testing** — no mocks, use actual APIs.
- **Intelligence validation** — use AI judges, not keyword matching.

## Work Completion Criteria

Work is DONE when:
1. ✅ Deliverable exists and works
2. ✅ Code quality standards met (`python -m ruff check`, `python -m ruff format`)
3. ✅ Changes committed and pushed to git
4. ✅ Original request fulfilled

## Session Management

Fresh messages create new sessions scoped by Telegram thread ID or local session ID; reply-to messages resume the original session and its context. Sessions pause only for genuine open questions. The full 14-state lifecycle is in [`docs/features/session-lifecycle.md`](docs/features/session-lifecycle.md).

Task lists are isolated automatically via `CLAUDE_CODE_TASK_LIST_ID`. Ad-hoc conversations get ephemeral thread-scoped lists; planned work created via `/do-plan {slug}` gets a durable slug-scoped list, and the lane slug ties together the task list, branch, and worktree. The plan doc is linked by `tracking:` frontmatter, not by name, and may carry a different filename than the lane's recorded slug — see [`docs/features/sdlc-lane-identity.md`](docs/features/sdlc-lane-identity.md). Filesystem isolation for planned work lives in `agent/worktree_manager.py` (`.worktrees/{slug}/`, branch `session/{slug}`). See [`docs/features/session-isolation.md`](docs/features/session-isolation.md).

## Self-Healing

The bridge auto-recovers from crashes: startup lock cleanup, a separate watchdog service, a Redis crash tracker with git-commit correlation, 4-level escalation, and an update-loop-wedged detector that restarts with `catch_up=True` for lossless backfill. A wedge verdict needs positive evidence that something was missed (`bridge:last_missed_recovery`, stamped by the reconciler), not just an absence of inbound messages, and its silence clock runs from the bridge process's start time so a restart clears the verdict that caused it. The crash-storm signal and the restart throttle are computed independently of the escalation level, so a recurring wedge's capped restart is never silently overridden. The watchdog records to `logs/watchdog.log`; it pushes no notification anywhere. Auto-revert is disabled unless `data/auto-revert-enabled` exists. See [`docs/features/bridge-self-healing.md`](docs/features/bridge-self-healing.md).

Recovery entry points: `./scripts/valor-service.sh restart`, `worker-restart`, and `python scripts/telegram_login.py` for Telegram auth.

## Configuration Files

- `.env` — symlink to `~/Desktop/Valor/.env`. Do not write secrets here directly.
- `~/Desktop/Valor/projects.json` — multi-project configuration (iCloud-synced, private).
- `.claude/settings.local.json` — Claude Code settings.

Tunable timing, retry, and TTL values live in `config/settings.py`'s `TimeoutSettings`, overridable via `TIMEOUTS__*` env keys. See [`docs/features/config-timeout-catalog.md`](docs/features/config-timeout-catalog.md) for the field catalog and the promote-vs-name-locally criterion.

## Single-Machine Ownership (Strict)

Every bridge-contact identifier in `projects.json` is owned by exactly **one** machine. Two machines must never both pick up the same incoming message. This covers Telegram DM contact ids, Telegram group names, email contacts, and email domain wildcards.

`projects.<key>.machine` is the source of truth; every other identifier inherits ownership from its project, so adding a machine costs zero edits to existing entries. Enforced by `bridge/config_validation.py::validate_projects_config` and gated by `scripts/update/run.py` Step 4.6: the update script blocks the bridge restart on a malformed config and the running bridge keeps serving the last-known-good one. See [`docs/features/single-machine-ownership.md`](docs/features/single-machine-ownership.md).

## Secrets

All secrets go in **`~/Desktop/Valor/.env`**; never write them to `repo/.env`. To add one: add it to the vault `.env`, add a placeholder to `.env.example` with a comment line above the `KEY=` (required by the completeness check), and add a field to `config/settings.py`. No sync step needed.

**Required by default.** `check_env_completeness` (`scripts/update/verify.py`) treats every `.env.example` declaration as required unless its comment block carries a bare `# @optional` line — a traced read site with an in-code default, and never a credential. Unmarked is the fail-closed default: a forgotten marker costs one spurious warning, a wrong one silences a real secret forever. Never derive the marker from running the check on your own machine and annotating whatever it flags; that set is per-machine and its wider form includes genuine credentials. See [`docs/features/env-completeness-validation.md`](docs/features/env-completeness-validation.md).

**No declaration without a reader.** A key with no reader in tracked non-markdown code needs a `# @passthrough <binary>` sigil naming the external binary that reads it straight out of the environment — `OP_SERVICE_ACCOUNT_TOKEN` below is exactly this shape, since `op` reads it, not any tracked Python. A passthrough key is still required; the axis is orthogonal to `@optional`. `tests/unit/test_env_declaration_readers.py` enforces this for every declaration.

### 1Password (`op`) — always non-interactive

`op` authenticates via the `valor-local` service account using `OP_SERVICE_ACCOUNT_TOKEN` from the vault `.env`. Vault of record: `m-valor`.

**Never write a skill, runbook, script, or `/update` step that depends on a human approving a 1Password prompt.** The desktop-app integration is not an execution path: its session expires in about a minute and needs someone at the keyboard, while the worker runs headless under launchd with nobody to prompt. Do not use `op signin` or `op account add` in automation.

```bash
# The sanctioned shape — token from env, cache off, batched resolve.
OP_CACHE=false op run --env-file=<template> --no-masking -- <command>
OP_CACHE=false op read "op://m-valor/<item>/credential"
```

`OP_CACHE=false` is required globally: the cache daemon trips TCC dialogs under launchd. If an `op` call cannot authenticate non-interactively, fail closed and report; do not fall back to a prompt.

When reconciling a secret between `.env` and the vault, verify the credential against its own API before adopting either side. "Newer wins" is not safe and has already destroyed a working key here. Never echo a secret value, or any prefix of one, to stdout: Bash output persists to session transcripts. Compare by SHA-256 fingerprint instead.

## GitHub Issue Labels

| Label | When to use |
|-------|-------------|
| `bug` | Something is broken or not working as expected |
| `reflections` | The reflections maintenance system |
| `memory` | The subconscious memory system |
| `skills` | Skills (`/do-*`), tools (MCP/Python), or the SDLC pipeline |
| `dashboard` | The web UI dashboard (`ui/`) |
| `bridge` | The Telegram bridge |
| `testing` | The test suite |
| `upvote` | Pre-approved for autonomous SDLC pickup — a scheduled reflection may start a lane on this issue without further human input. |

Do NOT use a `feature` label; it adds no signal.

## Knowledge Base (KB)

Pull from both sources before answering substantive questions.

**Vault** (curated, what humans wrote): `~/work-vault/AI Valor Engels System/`, indexed by that directory's `README.md`. Source of truth for business context, project notes, decisions, and assets. Ingest binaries with `valor-ingest <path>`.

**Memory** (Redis, what the agent learned): project key `valor`. Search via `python -m tools.memory_search search "<query>" --project valor` or the `mcp__memory__*` tools.

Both partition by project; don't leak cross-project context. This is a convention every project should follow, see [`docs/conventions/knowledge-base-section.md`](docs/conventions/knowledge-base-section.md).

## See Also

| Resource | Purpose |
|----------|---------|
| `/prime` | Architecture deep dive and codebase onboarding |
| `/setup` | New machine configuration |
| `/sdlc` | Single-stage router: assess state, invoke one sub-skill, return |
| [`docs/features/README.md`](docs/features/README.md) | Feature index — look up how things work |
| [`docs/sdlc/`](docs/sdlc/) | Per-stage repo-specific addenda, read by SDLC skills at runtime |
| [`tests/README.md`](tests/README.md) | Test suite index — feature markers, blind spots, contribution guide |
| [`docs/tools-reference.md`](docs/tools-reference.md) | Complete tool documentation |
| `config/identity.json` | Structured identity data |
