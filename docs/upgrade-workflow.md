# Dependency Upgrade Workflow

## Dependency Tiers

**Critical (pinned with `==`)** -- upgrades require manual `/update` on each machine:
- `telethon` -- Telegram protocol client, bridge infrastructure
- `anthropic` -- Claude API client
- `claude-agent-sdk` -- Agent backend

**Normal (pinned with `>=`)** -- auto-synced by cron when lock file changes:
- Everything else (httpx, dotenv, ollama, yt-dlp, popoto, google-*)

## Checking for Updates

```bash
cd /Users/valorengels/src/ai
uv pip list --outdated
```

## Upgrading a Critical Dependency

### 1. Edit the pin in pyproject.toml

```bash
# Example: telethon==1.40.0 -> telethon==1.41.0
```

### 2. Regenerate the lock file

```bash
uv lock
```

### 3. Install and test locally

```bash
uv sync --all-extras

# Start bridge, send test messages, check logs
./scripts/start_bridge.sh
tail -f logs/bridge.log
# Verify "Connected to Telegram" appears
# Verify message handling works
```

### 4. Commit and push

```bash
git add pyproject.toml uv.lock
git commit -m "Upgrade telethon 1.40.0 -> 1.41.0

Tested locally: bridge connects, messages handled correctly."
git push
```

### 5. Other machines

When the cron (`remote-update.sh`) pulls this commit on other machines:
- It detects a critical dep version changed
- Writes `data/upgrade-pending` flag
- Does NOT auto-sync -- bridge keeps running on old version
- User runs `/update` on that machine to apply the upgrade with verification

## Upgrading Normal Dependencies

Normal deps auto-sync. To bump them intentionally:

```bash
# Update minimum version if needed
# e.g., httpx>=0.27.0 -> httpx>=0.28.0

uv lock          # Regenerate lock with latest compatible versions
uv sync --all-extras
# Test, commit pyproject.toml + uv.lock, push
```

The cron will auto-sync the new lock file on all machines.

## Rollback

If an upgrade breaks things:

```bash
# Revert the upgrade commit
git revert <commit-hash>
git push

# The cron will auto-sync the revert on other machines
# For critical deps: cron sees the pin changed back, writes upgrade-pending
# Run /update on each machine to apply the rollback
```

Manual emergency rollback:

```bash
git checkout HEAD~1 -- pyproject.toml uv.lock
uv sync --all-extras
# Test, then commit and push
```

## How the Two-Speed System Works

```
Developer upgrades critical dep on Machine A
    |
    v
Commits pyproject.toml + uv.lock, pushes
    |
    +---> Machine B cron pulls
    |         |
    |         v
    |     Detects critical dep change
    |         |
    |         v
    |     Writes data/upgrade-pending
    |     Does NOT sync (bridge stays on old version)
    |         |
    |         v
    |     User runs /update on Machine B
    |         |
    |         v
    |     Syncs deps, verifies bridge health
    |     Clears upgrade-pending flag
    |
    +---> Machine C cron pulls
          (same flow as Machine B)
```

Non-critical dep changes flow through the cron automatically on all machines.
