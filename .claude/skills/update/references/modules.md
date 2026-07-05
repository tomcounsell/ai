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

## BYOB + Computer-Use Update Steps

`run.py` wires:

- **Step 4.8**: `mcp_memory.verify_memory_mcp()` -- runs every invocation.
- **Step 4.9**: `mcp_byob.verify_byob_mcp()` -- runs every invocation.

Binary updates (rebuilding `~/.byob/` when the pin in `config/byob_pin.json`
changes; re-downloading bcu against `config/bcu_pin.json` when the opt-in
sentinel `~/.config/valor/computer-use-enabled` is present) are designed but
not yet implemented in `scripts/update/` — see
`docs/features/byob-browser-control.md` and `docs/features/computer-use.md`
for the design (including `--bump-byob` / `--bump-bcu` and rollback paths).

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
