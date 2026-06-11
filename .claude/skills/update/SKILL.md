---
name: update
description: "Use when deploying updates to this machine. Pulls latest changes, syncs dependencies, verifies environment, and restarts the bridge service. Triggered by 'update', 'deploy', 'pull and restart', or after git pull."
---

# Update & Restart

Pull the latest changes from the remote repository, sync dependencies, and restart the bridge service.

## Instructions

**PREREQUISITE: Must be on latest main branch before running.**

```bash
cd ~/src/ai && git checkout main && git pull
```

If there are local changes, stash them first: `git stash`. The update orchestrator also handles this, but being on main is required.

Run the full update orchestrator and report the results:

```bash
cd ~/src/ai && .venv/bin/python scripts/update/run.py --full
```

The orchestrator will:
- Pull latest changes (with automatic stash/unstash)
- Sync `.claude` hardlinks and audit skill hooks
- Check for pending critical dependency upgrades
- **Verify machine identity** — reads `scutil --get ComputerName`, matches against `machine` field in `~/Desktop/Valor/projects.json`, reports which projects this machine handles
- Sync dependencies if pyproject.toml changed
- Verify critical dependency versions
- Check/pull Ollama summarizer model
- Verify CLI tools and SDK authentication
- **Validate `projects.json` (green-light gate)** — runs `bridge/config_validation.py::validate_projects_config` over the full config (Step 4.6). Enforces that every bridge-contact identifier (DM contact id, Telegram group, email contact, email domain wildcard) resolves to exactly one machine. On failure: log the error, skip the service restart, leave the running bridge serving on the previously-validated config. See [Single-Machine Ownership](../../../docs/features/single-machine-ownership.md).
- Install/restart bridge, worker, caffeinate, and reflections services (skipped if the validation gate failed)
- Set up global calendar hook and generate config
- Check MCP server configuration

After running, report the result. If there are warnings or errors, list each one clearly.

**First-install backfill reminder (markitdown):** When the update run is the first to install the `[knowledge]` extra on this machine (detected by `scripts/update/deps.py`'s lockfile-diff check), the Telegram summary appends a one-line tip: `run 'valor-ingest --scan ~/work-vault/' to backfill existing binary files into sidecars.` The reminder is gated by `~/.cache/valor/markitdown-backfill-reminded` and fires only once per machine. If the user asks why existing PDFs/docs in the vault are not yet indexed after update, point them at this command — the watcher only picks up files modified after it starts.

**Log rotation:** The orchestrator installs the user-space log-rotate LaunchAgent on every `--full` run (`com.valor.log-rotate.plist` → `~/Library/LaunchAgents/`). No root/sudo needed; the LaunchAgent runs `scripts/log_rotate.py` every 30 minutes to rotate any `logs/*.log` file over 10 MB. The installer is content-idempotent — if the rendered plist matches the installed file, the bootout/bootstrap cycle is skipped entirely. If a stale `/etc/newsyslog.d/valor.conf` exists from prior releases, the orchestrator attempts `sudo -n rm` (non-interactive) to remove it; if sudo requires a password, the cleanup is skipped with a warning and retried next run.

The orchestrator automatically cleans up sessions as part of Step 5.5:
- Corrupted sessions (invalid IDs, unsaveable records) are deleted and indexes rebuilt
- Running/pending sessions older than 120 min with no live process are transitioned to `killed`
- Terminal sessions (killed/abandoned/failed/completed) are preserved for reflections to analyze
- Reflections handles its own 90-day expiry of old session records

### Auto-Bump Critical Dependencies

The update system automatically checks PyPI for newer versions of `anthropic` and `claude-agent-sdk` on every run. When a newer version is available:

1. Bumps the pin in `pyproject.toml`
2. Runs `uv sync` to install the new version
3. Runs a smoke test (import check + `pytest tests/unit/test_docs_auditor_substrate.py -x -q`)
4. If smoke test passes: commits and pushes the bump
5. If smoke test fails: rolls back `pyproject.toml` and re-syncs old versions

This means SDK upgrades happen automatically and safely — no manual intervention needed unless a breaking change causes test failures.

### Critical Dependency Handling (git-driven changes)

When `pyproject.toml` changes via git pull with critical dep version changes (telethon, anthropic, claude-agent-sdk):

- The cron job (`remote-update.sh`) detects the change and writes `data/upgrade-pending`
- Running `/update` manually will apply the upgrade with proper verification

If `data/upgrade-pending` exists:
```bash
# Check what's pending
cat ~/src/ai/data/upgrade-pending

# After /update applies the upgrade and verifies the bridge starts:
rm ~/src/ai/data/upgrade-pending
```

### Verification Only

To check the environment without making changes:
```bash
cd ~/src/ai
.venv/bin/python scripts/update/run.py --verify
```

### Reinstall Launchd Services

After update, reinstall launchd plists to pick up any template changes:

```bash
cd ~/src/ai
./scripts/install_reflections.sh
./scripts/install_worker.sh
```

The install script substitutes `__PROJECT_DIR__` and `__HOME_DIR__` placeholders with the current machine's paths. This ensures plists work on any machine without hardcoded usernames.

## Troubleshooting

### Virtual environment issues
```bash
cd ~/src/ai
rm -rf .venv
uv venv
uv sync --all-extras
```

### Missing dependencies after update
```bash
cd ~/src/ai
uv sync --all-extras --reinstall
```

### Calendar integration not working
1. Check OAuth token: `ls ~/Desktop/Valor/google_token.json`
2. Re-run OAuth: `valor-calendar test`
3. Check deps: `.venv/bin/python -c "import google_auth_oauthlib; print('OK')"`

### Wrong projects active (machine identity mismatch)

The bridge derives active projects from `scutil --get ComputerName` matched against the `machine` field in `~/Desktop/Valor/projects.json`. If the wrong projects are active:

1. Check the machine name: `scutil --get ComputerName`
2. Check the config: `python -c "import json; [print(f'{k}: {v.get(\"machine\")}') for k,v in json.load(open('$HOME/Desktop/Valor/projects.json')).get('projects',{}).items()]"`
3. Fix: ensure the `machine` value in projects.json matches the ComputerName exactly (case-insensitive)

### Bridge won't start
```bash
# Check logs
tail -50 ~/src/ai/logs/bridge.error.log

# Manual restart
~/src/ai/scripts/valor-service.sh restart

# Check status
~/src/ai/scripts/valor-service.sh status
```

### Worker won't start
```bash
# Check logs
tail -50 ~/src/ai/logs/worker_error.log

# Manual restart
~/src/ai/scripts/valor-service.sh worker-restart

# Check status
~/src/ai/scripts/valor-service.sh worker-status

# Reinstall plist
~/src/ai/scripts/install_worker.sh
```

## Module Details

### Git Operations (`git.py`)

```python
from scripts.update import git

# Pull with automatic stash handling
result = git.git_pull(project_dir)
# result.success, result.commit_count, result.commits

# Check pending upgrades
pending = git.check_upgrade_pending(project_dir)
# pending.pending, pending.timestamp, pending.reason
```

### Dependency Management (`deps.py`)

```python
from scripts.update import deps

# Sync dependencies
result = deps.sync_dependencies(project_dir, reinstall=False)
# result.success, result.method ("uv" or "pip")

# Verify versions
versions = deps.verify_critical_versions(project_dir)
# [VersionInfo(package, version, expected, matches), ...]
```

### Environment Verification (`verify.py`)

```python
from scripts.update import verify

result = verify.verify_environment(project_dir)
# result.system_tools, result.python_deps, result.dev_tools
# result.valor_tools, result.ollama, result.sdk_auth, result.mcp_servers
```

### Google Workspace CLI auth (`gws_auth.py`)

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

### Calendar Integration (`calendar.py`)

```python
from scripts.update import calendar

# Ensure global hook is configured
hook = calendar.ensure_global_hook(project_dir)
# hook.configured, hook.created, hook.error

# Generate calendar config
config = calendar.generate_calendar_config(project_dir)
# config.success, config.mappings, config.error
```

### MCP Server Registration (`mcp_memory.py`, `mcp_byob.py`)

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

### BYOB + Computer-Use Update Steps

`run.py` wires:

- **Step 4.8**: `mcp_memory.verify_memory_mcp()` -- runs every invocation.
- **Step 4.9**: `mcp_byob.verify_byob_mcp()` -- runs every invocation.

For BYOB binary updates (rebuild ~/.byob/ when the pinned commit changes
in `config/byob_pin.json`) and bcu binary updates (re-download + SHA verify
against `config/bcu_pin.json` when the opt-in sentinel
`~/.config/valor/computer-use-enabled` is present), see the upcoming
implementation in `scripts/update/run.py` and the post-install canary at
`scripts/update/byob_canary.js`. Pins are bumped only via:

- `/update --bump-byob` -- next BYOB upstream commit
- `/update --bump-bcu` -- next bcu release tag

Rollback paths:
- BYOB: snapshot the entire `~/.byob/` tree to `~/.byob.prev/` before
  `git pull && bun install && bun run setup`. BYOB v0.3+ is a workspace
  monorepo with build artifacts under `packages/*/output/` and
  `packages/*/dist/` — there is no single top-level `dist/` to copy.
  Restore by `rm -rf ~/.byob && mv ~/.byob.prev ~/.byob` on canary
  failure (defined as `cd ~/.byob && bun run doctor` reporting any red
  status, or the post-install end-to-end probe — once
  `byob_canary.js` is built — failing within 30s).
- bcu: `~/.local/bin/background-computer-use.prev` symlink, restored on
  `/v1/list_apps` canary failure.

### Service Management (`service.py`)

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
```

## Node toolchain (soft prerequisite)

Machines that run the `do-design-system` skill also need Node + npm (for
`npx @google/design.md`). `remote-update.sh` runs `npm ci --only=prod`
guarded by:

```bash
if [ -f "$PROJECT_DIR/package.json" ] && command -v npm >/dev/null 2>&1; then
    ( set +o pipefail; cd "$PROJECT_DIR" && npm ci --only=prod ) \
        || echo "[update] npm ci failed (non-fatal); continuing"
fi
```

The non-pipefail subshell + `|| echo` trailer guarantee a missing `npm`
or a transient install failure never aborts the parent update. Machines
without Node simply skip the block silently; design-system tooling then
falls back to Python-only emission (`--generate --no-node`) for
`design-system.md` / `brand.css` / `source.css`. Lint and DTCG / Tailwind
exports still require Node and are only produced on Node-equipped
machines. See `docs/features/design-system-tooling.md` for the full
fallback semantics.
