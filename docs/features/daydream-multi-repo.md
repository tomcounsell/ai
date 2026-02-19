# Daydream Multi-Repo Support

## Problem

The original daydream script was hard-coded to the `ai` repo. Running on multiple machines caused duplicate GitHub issues — every machine analyzed the same repo and filed the same daily digest independently. Other configured projects (popoto, django-template, etc.) were never analyzed at all.

## Solution

Daydream now reads `config/projects.json`, filters to repos present on the current machine, and runs per-project analysis for each one. AI-only housekeeping steps continue to run once. GitHub issue creation, task cleanup, and Telegram posting are all scoped per project.

## Architecture

### `load_local_projects()`

Called at `DaydreamRunner.__init__`. Reads `config/projects.json` and returns only projects whose `working_directory` exists on the current filesystem. A machine with only `ai` checked out analyzes only `ai`. A machine with four repos checked out analyzes all four. Absent repos are silently excluded.

### `AI_ROOT` constant

Set to `PROJECT_ROOT` at module import time (before any `chdir`). Steps that must stay anchored to the `ai` repo — state file, lessons file — use `AI_ROOT` explicitly so they work correctly regardless of which project is currently being processed.

### Step classification

| Step | Type | Behavior |
|------|------|----------|
| 1 — Prioritize and clean code | AI-only | Runs once from `AI_ROOT` |
| 2 — Review logs | Per-project | Iterates each project's `logs/` dir |
| 3 — Sentry check | AI-only | Runs once (MCP skipped in standalone) |
| 4 — Clean tasks | Per-project | `gh issue list` per project via `cwd=` |
| 5 — Update docs | AI-only | Runs once from `AI_ROOT` |
| 6 — Produce report | AI-only | Single report for all findings |
| 7 — Session analysis | AI-only | Analyzes `AI_ROOT/logs/sessions/` |
| 8 — LLM reflection | AI-only | Reflects on combined session findings |
| 9 — Memory consolidation | AI-only | Appends to `AI_ROOT/data/lessons_learned.jsonl` |
| 10 — GitHub issue creation | Per-project | Issue in each project's own repo |
| 11 — Telegram post | Per-project | Posts summary to `telegram.groups[0]` |

### `subprocess` `cwd=` vs `os.chdir`

Per-project subprocess calls (`gh issue list`, `gh issue create`) use `cwd=project["working_directory"]` rather than `os.chdir`. This is safe in async code because it doesn't mutate the process working directory. `gh` auto-detects the GitHub repo from the git remote of the given directory.

### Findings namespacing

`state.findings` keys use `"{slug}:category"` format (e.g., `"ai:log_review"`, `"popoto:tasks"`). This prevents per-project findings from colliding when multiple projects share a category name, and lets the report section identify which project each finding came from.

### Telegram posting (`step_post_to_telegram`)

After creating a GitHub issue for a project, daydream posts a short summary to `project["telegram"]["groups"][0]` via Telethon, reusing the existing `data/valor.session` file. The message includes: project name, date, finding count, and the GitHub issue URL.

## Configuration

Each project entry in `config/projects.json`:

```json
{
  "working_directory": "/Users/valorengels/src/my-project",
  "github": {
    "org": "myorg",
    "repo": "my-project"
  },
  "telegram": {
    "groups": ["@my_group"]
  }
}
```

| Field | Required for daydream | Notes |
|-------|----------------------|-------|
| `working_directory` | Yes | Must exist on disk to be included |
| `github.org` | For issues/tasks | Steps 4 and 10 skip if absent |
| `github.repo` | For issues/tasks | Steps 4 and 10 skip if absent |
| `telegram.groups` | No | Step 11 skips if absent or empty |

## Graceful fallbacks

- `working_directory` absent from disk → project excluded from `load_local_projects()`
- `github` key missing → steps 4 and 10 log a warning and skip that project
- `telegram.groups` missing or empty → step 11 logs and skips
- `data/valor.session` missing → step 11 skips silently
- `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` not set → step 11 skips silently
- `telethon` not installed → step 11 skips silently

## See Also

- `docs/features/daydream-reactivation.md` — core 11-step process and architecture
- `config/projects.json` — live project registry
- `scripts/daydream.py` — implementation
- `scripts/daydream_report.py` — `create_daydream_issue(findings, date, cwd=None)`
