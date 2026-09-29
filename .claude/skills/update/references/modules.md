# Update Orchestrator Module Details

Python API reference for the `scripts/update/` modules. Load this when debugging the orchestrator internals or calling a module directly — the normal `/update` run needs none of it.

## Git Operations (`git.py`)

```python
from scripts.update import git

# Pull with automatic stash handling
result = git.git_pull(project_dir)
# result.success, result.commit_count, result.commits

# Check pending upgrades
pending = git.check_upgrade_pending(project_dir)
# pending.pending, pending.timestamp, pending.reason
```

## Dependency Management (`deps.py`)

```python
from scripts.update import deps

# Sync dependencies
result = deps.sync_dependencies(project_dir, reinstall=False)
# result.success, result.method ("uv" or "pip")

# Verify versions
versions = deps.verify_critical_versions(project_dir)
# [VersionInfo(package, version, expected, matches), ...]
```

## Environment Verification (`verify.py`)

```python
from scripts.update import verify

result = verify.verify_environment(project_dir)
# result.system_tools, result.python_deps, result.dev_tools
# result.valor_tools, result.ollama, result.sdk_auth, result.mcp_servers
```

## Google Workspace CLI auth (`gws_auth.py`)

```python
from scripts.update import gws_auth

result = gws_auth.configure_gws_auth(project_dir)
# result.action: "already_ok" | "needs_auth" | "skipped" | "failed"
```

Runs right after the `gh` auth step. `gws` (the `@googleworkspace/cli` binary)
is installed automatically by the npm prereq step, but first use needs a
one-time **human** OAuth step — `gws auth login` opens a browser for Google
consent and `gws auth setup` requires `gcloud` + a GCP project. Those are
human-gated and `/update` also runs non-interactively (launchd polling), so this
step is **detection only** — it never opens a browser or blocks:

- `gws` not on PATH → `skipped` (nothing to authenticate yet).
- authenticated (`gws auth status` reports `auth_method != "none"`) → `already_ok`, silent and idempotent.
- installed but unauthenticated → `needs_auth`: surfaces an actionable warning with the exact command (`gws auth setup --login`) and appends it to `result.warnings` so it shows at the end of the run. The human completes it once, at their next interactive moment.

## Calendar Integration (`calendar.py`)

```python
from scripts.update import calendar

# Ensure global hook is configured
hook = calendar.ensure_global_hook(project_dir)
# hook.configured, hook.created, hook.error

# Generate calendar config
config = calendar.generate_calendar_config(project_dir)
# config.success, config.mappings, config.error
```

## MCP Server Registration (`mcp_memory.py`, `mcp_byob.py`)

Both modules idempotently verify/repair their entry in `~/.claude.json`
`mcpServers` under `fcntl.flock(LOCK_EX | LOCK_NB)` on
`~/.claude.json.lock` with the same 3-attempt backoff (50/200/800ms).
`run.py` calls both on every invocation so drift is healed automatically.

```python
from scripts.update import mcp_memory, mcp_byob

# Memory MCP -- python3 -m mcp_servers.memory_server
r1 = mcp_memory.verify_memory_mcp(write=True)
# r1.ok, r1.action ("ok"|"installed"|"repaired"|...)

# BYOB MCP -- tsx ~/.byob/packages/mcp-server/bin/byob-mcp.ts, BYOB_ALLOW_EVAL=1
r2 = mcp_byob.verify_byob_mcp(write=True)
# r2.ok, r2.action
```

`write=False` runs in verify-only mode (LOCK_SH, no rename) -- used by
`/update --verify`.

Binary updates for BYOB (`config/byob_pin.json`) and bcu (`config/bcu_pin.json`) are designed but not implemented in `scripts/update/`; see `docs/features/byob-browser-control.md` and `docs/features/computer-use.md`.

## Auto-bump of critical dependencies (`deps.py`)

`AUTO_BUMP_SETS` declares coupled sets (packages that move together or not at all). Only the lockfile-maintainer machine auto-bumps. Per set: skip if it carries a `hold` (recorded as `held: <reason>`) or if any member's latest PyPI version is unresolvable or no pin changed; otherwise snapshot `pyproject.toml`, rewrite the changed pins, `uv sync`, and run the set's gate phases in order (`llm`: `python -m agent.llm.compat --json --allow-network`, one real billed call; `import`; `pytest tests/unit/test_docs_auditor_substrate.py -x -q`). All pass: pins are committed and pushed. Any failure: that set's snapshot is restored and re-synced, `rolled_back` names the failed phase; a failed restore sets `restore_failed` and nothing is committed that run.

Every run on every machine also reports `llm-stack-compat` (`python -m agent.llm.compat --json`), which covers hand-staged pins and followers. See `docs/features/llm-stack-compat-gate.md`.

## Launchd sweep and session cleanup

Step 1.56 boots out LaunchAgents whose features were removed, listed in `scripts/update/service.py::OBSOLETE_SERVICE_SUFFIXES`. When you delete a launchd-backed feature, add its label suffix there. Step 5.5 deletes corrupted sessions, rebuilds indexes, and moves running/pending sessions older than 120 min with no live process to `killed`.

## Service Management (`service.py`)

```python
from scripts.update import service

# Get bridge status
status = service.get_service_status(project_dir)
# status.running, status.pid, status.uptime, status.memory_mb

# Install/restart bridge
service.install_service(project_dir)  # Installs bridge + update cron
service.restart_service(project_dir)

# Get worker status
worker = service.get_worker_status(project_dir)
# worker.running, worker.pid, worker.uptime, worker.memory_mb

# Install/restart worker
service.install_worker(project_dir)   # Installs standalone worker service
service.restart_worker(project_dir)

# Install/reload the reflection-scheduler subprocess (issue #1828)
service.install_reflection_worker(project_dir)
# Delegates to scripts/install_reflection_worker.sh (has_worker_role()
# self-gate, fail-open). Returns True on rc=0. Called by run.py right
# after the worker install, guarded only on plist existence — NOT under
# `if has_bridge:` (the subprocess must run everywhere the worker does).
```
