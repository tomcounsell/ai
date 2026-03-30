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
- Install/restart bridge, caffeinate, and reflections services
- Set up global calendar hook and generate config
- Check MCP server configuration

After running, report the result. If there are warnings or errors, list each one clearly.

### Stale Session & Job Audit

After the orchestrator completes, audit Redis for stale or abandoned sessions/jobs. An `/update` implies a soft reset — anything stuck should be surfaced.

```bash
# Preview stale sessions (killed/abandoned/failed older than 30 min)
cd ~/src/ai && python -m tools.job_scheduler cleanup --age 30 --dry-run

# List any non-terminal sessions that might be stuck
python -m tools.job_scheduler list --status running,pending
```

Report findings:
- **Clean**: "No stale sessions or jobs"
- **Stale found**: Show the dry-run output and ask whether to clean up. If approved: `python -m tools.job_scheduler cleanup --age 30`

**Important**: Never change session status via `s.status = ...; s.save()` — Popoto's KeyField creates a new record and orphans the old one. Use `s.delete()` for cleanup, or use the `cleanup` command which does this correctly.

### Auto-Bump Critical Dependencies

The update system automatically checks PyPI for newer versions of `anthropic` and `claude-agent-sdk` on every run. When a newer version is available:

1. Bumps the pin in `pyproject.toml`
2. Runs `uv sync` to install the new version
3. Runs a smoke test (import check + `pytest tests/test_docs_auditor.py -x -q`)
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

### Service Management (`service.py`)

```python
from scripts.update import service

# Get status
status = service.get_service_status(project_dir)
# status.running, status.pid, status.uptime, status.memory_mb

# Install/restart
service.install_service(project_dir)  # Installs bridge + update cron
service.restart_service(project_dir)
```
