---
name: update
description: Pull latest changes, sync dependencies, verify environment, and restart the bridge service. Use when deploying updates, after git pull, or when the bridge needs a fresh start.
---

# Update & Restart

Pull the latest changes from the remote repository, sync dependencies, and restart the bridge service.

## Quick Reference

```bash
# Run full update
cd /Users/valorengels/src/ai && .venv/bin/python scripts/update/run.py --full

# Run verification only (no changes)
cd /Users/valorengels/src/ai && .venv/bin/python scripts/update/run.py --verify
```

## Steps

The update system is modular. Each step is implemented in `scripts/update/`:

| Module | Purpose |
|--------|---------|
| `git.py` | Git pull, stash handling, upgrade detection |
| `deps.py` | Dependency sync (uv/pip), version verification |
| `verify.py` | Environment checks (tools, SDK auth, MCP) |
| `calendar.py` | Global hook setup, calendar config generation |
| `service.py` | Bridge restart, caffeinate service |
| `run.py` | Orchestrator (calls all modules) |

### Running the Update

1. **Run the full update orchestrator**:
   ```bash
   cd /Users/valorengels/src/ai
   .venv/bin/python scripts/update/run.py --full
   ```

2. **Review the output** - the orchestrator will:
   - Pull latest changes (with automatic stash/unstash)
   - Check for pending critical dependency upgrades
   - Sync dependencies if pyproject.toml changed
   - Verify critical dependency versions
   - Check/pull Ollama summarizer model
   - Verify SDK authentication status
   - Install/restart bridge and update cron services
   - Install caffeinate service (prevents sleep)
   - Verify CLI tools are available
   - Set up global calendar hook if missing
   - Generate Google Calendar config
   - Check MCP server configuration

3. **Handle any warnings or errors** reported by the orchestrator.

### Critical Dependency Handling

Critical dependencies (telethon, anthropic, claude-agent-sdk) are pinned with `==` in pyproject.toml. When these change:

- The cron job (`remote-update.sh`) detects the change and writes `data/upgrade-pending`
- The cron job does NOT auto-sync critical deps
- Running `/update` manually will apply the upgrade with proper verification

If `data/upgrade-pending` exists:
```bash
# Check what's pending
cat /Users/valorengels/src/ai/data/upgrade-pending

# After /update applies the upgrade and verifies the bridge starts:
rm /Users/valorengels/src/ai/data/upgrade-pending
```

### Verification Only

To check the environment without making changes:
```bash
cd /Users/valorengels/src/ai
.venv/bin/python scripts/update/run.py --verify
```

## Troubleshooting

### Virtual environment issues
```bash
cd /Users/valorengels/src/ai
rm -rf .venv
uv venv
uv sync --all-extras
```

### Missing dependencies after update
```bash
cd /Users/valorengels/src/ai
uv sync --all-extras --reinstall
```

### Calendar integration not working
1. Check OAuth token: `ls ~/Desktop/claude_code/google_token.json`
2. Re-run OAuth: `valor-calendar test`
3. Check deps: `.venv/bin/python -c "import google_auth_oauthlib; print('OK')"`

### Bridge won't start
```bash
# Check logs
tail -50 /Users/valorengels/src/ai/logs/bridge.error.log

# Manual restart
/Users/valorengels/src/ai/scripts/valor-service.sh restart

# Check status
/Users/valorengels/src/ai/scripts/valor-service.sh status
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
