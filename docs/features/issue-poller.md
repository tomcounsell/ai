# Issue Poller

Automatic SDLC kickoff for new GitHub issues. Polls configured repositories on a 5-minute schedule, detects new issues, runs deduplication checks, and auto-creates draft plans.

## How It Works

1. **Cron fires** every 5 minutes via launchd (`com.valor.issue-poller.plist`)
2. **Polls** `gh issue list` for each project in `~/Desktop/Valor/projects.json` that has a `github` key
3. **Filters** out already-seen issues using the `SeenIssue` Popoto model (`models/seen_issue.py`)
4. **Validates** issue context (title + body length), flagging thin issues as `needs-review`
5. **Dedup check** using Claude Haiku to score semantic similarity against other open issues
6. **Dispatches** plan creation via `claude -p` for valid unique issues
7. **Labels** issues on GitHub: `auto-planned`, `possible-duplicate`, or `needs-review`
8. **Notifies** via Telegram for all automated actions

## Architecture

```
launchd (5 min) → scripts/issue_poller.py
                    ├── Redis lock (prevent concurrent runs)
                    ├── SeenIssue model (Popoto, tracks processed issues per repo)
                    ├── ~/Desktop/Valor/projects.json (multi-project iteration)
                    ├── gh CLI (fetch issues, apply labels, add comments)
                    ├── scripts/issue_dedup.py (Claude Haiku similarity scoring)
                    └── claude -p (dispatch /do-plan for new issues)
```

## State Management

The poller uses a mix of Popoto models and raw Redis keys:

### Popoto Models

| Model | Key | Purpose |
|-------|-----|---------|
| `SeenIssue` (`models/seen_issue.py`) | `repo_key` (org/repo) | Tracks processed issue numbers per repository via a `SetField` |

### Raw Redis Keys

| Redis Key | Type | Purpose |
|-----------|------|---------|
| `issue_poller:lock` | String (TTL) | Distributed lock preventing concurrent runs |
| `issue_poller:consecutive_failures` | Counter | Tracks consecutive cycle failures for alerting |

## Deduplication

The dedup engine (`scripts/issue_dedup.py`) uses Claude Haiku to score semantic similarity between issues:

| Score Range | Classification | Action |
|-------------|----------------|--------|
| >= 0.8 | `duplicate` | Label `possible-duplicate`, comment with suspected original, notify |
| 0.5 - 0.8 | `related` | Note in plan as dependency, proceed with planning |
| < 0.5 | `unique` | Proceed with plan creation |

Dedup is **best-effort**: if the Claude API fails, planning proceeds without dedup rather than blocking.

## Agent D Comment Filtering

The poller filters out automated comments from the `/do-docs` cascade (Agent D) when evaluating issue activity. Comments containing the signature `_Auto-posted by /do-docs cascade_` are excluded from:
- Latest comment ID tracking (for plan freshness gates)
- New activity evaluation (to prevent false re-processing)

## Configuration

Projects are loaded from `~/Desktop/Valor/projects.json`. Only entries with a `github` key are polled:

```json
{
  "projects": {
    "my-project": {
      "github": {"org": "myorg", "repo": "myrepo"},
      "telegram": {"groups": ["Dev: MyProject"]}
    }
  }
}
```

## Installation

```bash
# Install the launchd service
./scripts/install_issue_poller.sh

# Check status
launchctl list | grep issue-poller

# Run manually
python scripts/issue_poller.py

# Unload service
launchctl bootout gui/$(id -u)/com.valor.issue-poller
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Poller not running | launchd not loaded | `./scripts/install_issue_poller.sh` |
| "lock held" in logs | Previous cycle still running or crashed | Lock auto-expires after 5 min; or `redis-cli DEL issue_poller:lock` |
| Issues not detected | Issue already in SeenIssue model | Check via `python -c "from models.seen_issue import SeenIssue; print(SeenIssue.get_or_create('org','repo').issue_numbers)"` |
| Dedup always skipped | `ANTHROPIC_API_KEY` missing | Ensure key is in `.env` |
| No notifications | `valor-telegram` CLI not available | Check bridge is running; falls back to logging |

## Logs

- **Main log**: `logs/issue_poller.log`
- **Error log**: `logs/issue_poller_error.log`
- **Alert threshold**: 3+ consecutive failures triggers a Telegram notification

## Files

| File | Purpose |
|------|---------|
| `scripts/issue_poller.py` | Main polling loop and orchestration |
| `scripts/issue_dedup.py` | LLM-based similarity scoring engine |
| `models/seen_issue.py` | Popoto model tracking processed issues per repo |
| `com.valor.issue-poller.plist` | launchd service definition (5-min interval) |
| `scripts/install_issue_poller.sh` | launchd installation script |
| `tests/test_issue_poller.py` | Integration tests |
| `tests/unit/test_seen_issue.py` | Unit tests for SeenIssue model |
