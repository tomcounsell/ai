# Daydream System Operations

Operational runbook for the daydream autonomous maintenance system.

## Overview

The daydream system runs daily at 6 AM Pacific via macOS launchd. It performs an 11-step maintenance cycle covering cleanup, analysis, reflection, reporting, and per-project Telegram notifications.

For the full feature description, see [Daydream Reactivation](../features/daydream-reactivation.md).

## Quick Commands

| Command | Description |
|---------|-------------|
| `python scripts/daydream.py` | Run daydream manually |
| `./scripts/install_daydream.sh` | Install/update launchd schedule |
| `tail -f logs/daydream.log` | Stream daydream logs |
| `cat data/lessons_learned.jsonl` | View institutional memory |
| `launchctl list \| grep daydream` | Check launchd status |

## 11-Step Pipeline

| Step | Name | Failure Mode |
|------|------|-------------|
| 1 | File Cleanup | Non-blocking. Logs warning, continues. |
| 2 | Log Review | Non-blocking. Per-project. Skips if `logs/bridge.log` missing. |
| 3 | Sentry Check | Non-blocking. Skips if MCP unavailable. |
| 4 | Task Cleanup | Non-blocking. Per-project via `gh` CLI. Requires `gh` auth. |
| 5 | Audit Documentation | Non-blocking. Weekly gate: skips if run within last 7 days. Requires `ANTHROPIC_API_KEY`. |
| 6 | Report Generation | Non-blocking. Writes to `logs/daydream/`. |
| 7 | Session Analysis | Non-blocking. Skips if no session logs exist. |
| 8 | LLM Reflection | Non-blocking. Requires `ANTHROPIC_API_KEY`. |
| 9 | Memory Consolidation | Non-blocking. Appends to `data/lessons_learned.jsonl`. |
| 10 | GitHub Issue Creation | Non-blocking. Per-project. Skips if no findings. Requires `gh` auth. |
| 11 | Telegram Post | Non-blocking. Per-project. Skips if `telegram.groups` absent or Telethon credentials missing. |

### Step 5: Documentation Audit

Step 5 (`step_audit_docs`) replaced the older `step_update_docs` function, which used a 30-day timestamp check to flag stale documentation. The new step performs content-aware verification using an LLM.

**What it does.** For each `.md` file in `docs/` (excluding `plans/`), the auditor:
1. Extracts verifiable references: file paths, env vars, Python imports, class/function names, CLI commands, package names
2. Checks each reference against the actual codebase (Glob, Grep, `pyproject.toml`)
3. Calls `claude-haiku-4-5-20251001` (with Sonnet escalation for uncertain cases) for a final verdict
4. Applies the verdict: KEEP (no changes), UPDATE (targeted corrections), DELETE (>60% unverifiable)
5. Sweeps `docs/README.md` and `docs/features/README.md` for broken links after any deletions
6. Normalizes filenames to lowercase-with-hyphens and relocates misplaced docs

**Frequency.** Reads `last_audit_date` from `data/daydream_state.json`. Skips if fewer than 7 days have passed since the last run. Updates `last_audit_date` on completion.

**Findings recorded.**

```json
{
  "audit_docs": {
    "kept": 12,
    "updated": 3,
    "deleted": 1,
    "skipped": false
  }
}
```

For full details including the `DocsAuditor` class API and manual invocation instructions, see [Documentation Audit](../features/documentation-audit.md).

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
| `ANTHROPIC_API_KEY` | Steps 5, 8 | Yes for docs audit and LLM reflection |
| Sentry MCP | Step 3 | No (skips gracefully) |
| `logs/bridge.log` | Step 2 | No (skips if missing) |
| `logs/sessions/*/chat.json` | Step 7 | No (skips if empty) |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Step 11 | No (skips if missing) |
| `data/valor.session` | Step 11 | No (skips if missing) |
| `config/projects.json` `telegram.groups` | Step 11 | No (skips per-project if absent) |

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
