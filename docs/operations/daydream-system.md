# Daydream System Operations

Operational runbook for the daydream autonomous maintenance system.

## Overview

The daydream system runs daily at 6 AM Pacific via macOS launchd. It performs a 10-step maintenance cycle covering cleanup, analysis, reflection, and reporting.

For the full feature description, see [Daydream Reactivation](../features/daydream-reactivation.md).

## Quick Commands

| Command | Description |
|---------|-------------|
| `python scripts/daydream.py` | Run daydream manually |
| `./scripts/install_daydream.sh` | Install/update launchd schedule |
| `tail -f logs/daydream.log` | Stream daydream logs |
| `cat data/lessons_learned.jsonl` | View institutional memory |
| `launchctl list \| grep daydream` | Check launchd status |

## 10-Step Pipeline

| Step | Name | Failure Mode |
|------|------|-------------|
| 1 | File Cleanup | Non-blocking. Logs warning, continues. |
| 2 | Log Review | Non-blocking. Skips if `logs/bridge.log` missing. |
| 3 | Sentry Check | Non-blocking. Skips if MCP unavailable. |
| 4 | Task Cleanup | Non-blocking. Requires `gh` CLI authentication. |
| 5 | Docs Check | Non-blocking. Scans for stale TODOs. |
| 6 | Report Generation | Non-blocking. Writes to `logs/daydream/`. |
| 7 | Session Analysis | Non-blocking. Skips if no session logs exist. |
| 8 | LLM Reflection | Non-blocking. Requires `ANTHROPIC_API_KEY`. |
| 9 | Memory Consolidation | Non-blocking. Appends to `data/lessons_learned.jsonl`. |
| 10 | GitHub Issue Creation | Non-blocking. Skips if no findings. Requires `gh` auth. |

Each step is independently failable. A failure in any step is logged but does not prevent subsequent steps from running.

## Scheduling

**Plist**: `com.valor.daydream.plist`
**Schedule**: Daily at 6:00 AM Pacific
**Location**: `~/Library/LaunchAgents/com.valor.daydream.plist`

### Install or Update

```bash
./scripts/install_daydream.sh
```

### Reload After Changes

```bash
launchctl unload ~/Library/LaunchAgents/com.valor.daydream.plist
launchctl load ~/Library/LaunchAgents/com.valor.daydream.plist
```

### Verify Running

```bash
launchctl list | grep daydream
```

## Output Locations

| Path | Content |
|------|---------|
| `logs/daydream.log` | Runner stdout/stderr |
| `logs/daydream/` | Generated reports (one per run) |
| `data/lessons_learned.jsonl` | Institutional memory (pruned to 90 days) |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| `gh` CLI (authenticated) | Steps 4, 10 | Yes for issue creation |
| `ANTHROPIC_API_KEY` | Step 8 | Yes for LLM reflection |
| Sentry MCP | Step 3 | No (skips gracefully) |
| `logs/bridge.log` | Step 2 | No (skips if missing) |
| `logs/sessions/*/chat.json` | Step 7 | No (skips if empty) |

## Troubleshooting

### Daydream did not run

1. Check launchd: `launchctl list | grep daydream`
2. Check plist installed: `ls ~/Library/LaunchAgents/com.valor.daydream.plist`
3. Reinstall: `./scripts/install_daydream.sh`

### No GitHub issue was created

Expected when no findings were produced. Check `tail -20 logs/daydream.log` and verify `gh auth status`.

### LLM reflection step skipped

Ensure `ANTHROPIC_API_KEY` is set in `.env`.

### Manual full run

```bash
python scripts/daydream.py
```
