# Reflections: Autonomous Maintenance System

The reflections system is a unified framework for all recurring non-issue work. A single lightweight scheduler (`agent/reflection_scheduler.py`) reads from a declarative registry (`config/reflections.yaml`), tracks state in Redis, and executes reflections on schedule. This replaces the previously scattered scheduling mechanisms (launchd plists, asyncio loops, startup hooks).

## Unified Reflection Scheduler

All recurring tasks are declared in `config/reflections.yaml` and managed by a single scheduler that runs as an asyncio task inside the bridge worker loop.

### Architecture

```
Bridge startup
  -> ReflectionScheduler.start()
    -> Tick every 60 seconds
      -> For each reflection in registry:
        -> Check if due (last_run + interval < now)
        -> Check skip-if-running guard
        -> Execute: function (direct callable) or agent (subprocess)
        -> Update state in Redis (Reflection model)
```

### Registry Format (`config/reflections.yaml`)

```yaml
reflections:
  - name: health-check
    description: "Check running jobs for liveness and timeout"
    interval: 300       # 5 minutes
    priority: high
    execution_type: function
    callable: "agent.job_queue._job_health_check"
    enabled: true
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier (used as Redis key) |
| `interval` | int | Seconds between runs |
| `priority` | string | `urgent`, `high`, `normal`, or `low` |
| `execution_type` | string | `function` (direct callable) or `agent` (subprocess) |
| `callable` | string | Dotted Python path (for function type) |
| `command` | string | Shell command (for agent type) |
| `enabled` | bool | Whether this reflection is active (default: true) |

### Registered Reflections

| Name | Interval | Priority | Type | Description |
|------|----------|----------|------|-------------|
| `health-check` | 5 min | high | function | Check running jobs for liveness, recover stuck ones |
| `orphan-recovery` | 30 min | normal | function | Recover stranded AgentSession objects |
| `stale-branch-cleanup` | daily | low | function | Clean up session branches older than 72 hours |
| `daily-maintenance` | daily | low | function | Full 15-step maintenance pipeline |

### State Model (`models/reflection.py`)

Each reflection gets a `Reflection` record in Redis tracking execution state:

| Field | Type | Purpose |
|-------|------|---------|
| `name` | KeyField | Unique identifier matching registry |
| `last_run` | Field(float) | Unix timestamp of last execution start |
| `next_due` | Field(float) | Unix timestamp when next scheduled |
| `run_count` | IntField | Total number of executions |
| `last_status` | Field | `pending`, `running`, `success`, `error`, `skipped` |
| `last_error` | Field | Error message from last failure |
| `last_duration` | Field(float) | Duration of last run in seconds |

### Skip-if-Running Guard

Before enqueuing a reflection, the scheduler checks if it's already running. If a reflection with the same name has `last_status == "running"`, it's skipped. If a reflection has been running for more than 2x its interval, it's considered stuck and reset to `error` status.

### Observability

Reflection status is available via `ReflectionScheduler.format_status()`, showing each reflection's state, time until next run, last duration, and run count. This can be wired to `/queue-status` for Telegram visibility.

### Bridge Watchdog (External)

The bridge watchdog (`com.valor.bridge-watchdog`) is intentionally NOT in the reflection registry. It must run as an external launchd service because it monitors the bridge process itself -- running it inside the process it monitors defeats its purpose.

## Daily Maintenance Pipeline (15-Step)

The `daily-maintenance` reflection runs the full pipeline from `scripts/reflections.py`. The runner loads state from Redis, executes each step in order, and checkpoints after every step. If interrupted, the next run resumes from where it left off. Each step is independently failable — a crash in one step does not block the rest.

### 15-Step Pipeline

| Step | Name | Description | Scope | Failure Mode |
|------|------|-------------|-------|--------------|
| 1 | Clean Up Stale Code | Scans for TODO comments, old-style typing imports | AI repo only | Non-blocking |
| 2 | Review Logs | Extracts structured errors from log files and Redis BridgeEvent records | Per-project | Non-blocking, skips if `logs/bridge.log` missing |
| 3 | Check Error Logs (Sentry) | Queries Sentry for unresolved issues | AI repo only | Non-blocking, skips if MCP unavailable |
| 4 | Clean Up Task Management | Lists open bug issues via `gh issue list` per project | Per-project | Non-blocking, requires `gh` auth |
| 5 | Audit Documentation | Weekly LLM-powered accuracy audit of `docs/` (see [Documentation Audit](documentation-audit.md)) | AI repo only | Non-blocking, requires `ANTHROPIC_API_KEY` |
| 6 | Session Analysis | Queries Redis AgentSession and BridgeEvent; computes thrash ratio, detects user corrections | AI repo only | Non-blocking |
| 7 | LLM Reflection | Claude Haiku categorizes mistakes into 6 categories | AI repo only | Non-blocking, requires `ANTHROPIC_API_KEY` |
| 8 | File Bug Issues | For high-confidence `code_bug` reflections, creates GitHub issues via `gh issue create` | AI repo only | Non-blocking, requires `gh` auth |
| 9 | Report Generation | Writes local markdown report to `logs/reflections/report_YYYY-MM-DD.md` | AI repo only | Non-blocking |
| 10 | GitHub Issue Creation | Posts daily digest issue per project via `gh` CLI; posts summary to Telegram | Per-project | Non-blocking, requires `gh` auth |
| 11 | Skills Audit | Validates all SKILL.md files against template standards (see [Skills Audit](do-skills-audit.md)) | AI repo only | Non-blocking |
| 12 | Redis TTL Cleanup | Prunes expired records across all Redis models | AI repo only | Non-blocking |
| 13 | Redis Data Quality | Surfaces data quality issues: unsummarized links, dead channels, error patterns | AI repo only | Non-blocking |
| 14 | Branch and Plan Cleanup | Deletes merged branches; ensures plans have open issues; flags completed plans for docs migration | AI repo only | Non-blocking, requires `gh` auth |

## State & Persistence

All reflections state lives in Redis via two Popoto models defined in `models/reflections.py`.

### ReflectionRun

One record per calendar date. Acts as the primary state checkpoint for resumability.

| Field | Type | Purpose |
|-------|------|---------|
| `date` | UniqueKeyField | YYYY-MM-DD, one run per day |
| `current_step` | IntField | Next step to execute (1-14) |
| `completed_steps` | ListField | Steps already finished, e.g. `[1, 2, 3]` |
| `daily_report` | ListField | Human-readable log lines per step |
| `findings` | DictField | `{category: [finding_strings]}` |
| `session_analysis` | DictField | Output from session analysis step |
| `reflections` | ListField | LLM reflection outputs |
| `auto_fix_attempts` | ListField | Auto-fix attempt records |
| `step_progress` | DictField | Per-step metrics, e.g. `{"clean_legacy": {"findings": 2}}` |
| `started_at` | SortedField(float) | Unix timestamp, used for cleanup |
| `dry_run` | Field(bool) | True if `--dry-run` mode |

**Checkpoint cycle**: After each step completes (or fails), the runner saves all state to Redis via `ReflectionRun.save_checkpoint()`. This deletes and recreates the record to handle Popoto's KeyField constraints.

**Resume scenario**: If reflections crashes during step 7, the next run loads the ReflectionRun for today, sees `completed_steps = [1,2,3,4,5,6]`, and continues from step 7.

### ReflectionIgnore

Suppresses auto-fix for specific patterns. Each entry has a TTL (default 14 days).

| Field | Type | Purpose |
|-------|------|---------|
| `ignore_id` | AutoKeyField | UUID |
| `pattern` | KeyField | Pattern string to match against reflections |
| `reason` | Field | Why this pattern is ignored |
| `created_at` | SortedField(float) | When the entry was created |
| `expires_at` | SortedField(float) | When it expires (created_at + 14 days) |

**Matching**: Case-insensitive substring match — if either the ignore pattern or the reflection pattern is a substring of the other, it's a match.

**Cleanup**: Expired entries are pruned at the start of each auto-fix step and during Redis TTL cleanup (step 13).


## Session Analysis (Step 6)

Queries Redis for recent sessions and computes quality metrics.

### Data Sources

- **AgentSession** — turn count, tool call count, log file path, session tags
- **BridgeEvent** — error events correlated to sessions

### Thrash Ratio

Measures how much agent effort was wasted:

```
failure_ratio = max(0.0, 1.0 - (turn_count / tool_call_count))
```

Sessions above `THRASH_RATIO_THRESHOLD = 0.5` (50% failure rate) are flagged for LLM reflection. The runner caps analysis at the 20 most interesting sessions (sorted by turn count).

### Correction Detection

Scans session transcripts for patterns indicating the human corrected the agent:

| Pattern | Example |
|---------|---------|
| Explicit correction | "no, I meant...", "that's wrong" |
| Redirection | "actually, ...", "not what I asked" |
| Stop and redirect | "stop... instead" |
| Repeated instruction | "I said..." |

These regex patterns are defined in `CORRECTION_PATTERNS` and applied to user messages extracted from session transcript files.

## LLM Reflection (Step 7)

Flagged sessions are sent to Claude Haiku (`claude-haiku-4-5-20251001`) for categorization:

| Category | Description |
|----------|-------------|
| `misunderstanding` | Misinterpreted the user's intent |
| `code_bug` | Introduced a bug in generated code |
| `poor_planning` | Inadequate planning before implementation |
| `tool_misuse` | Used the wrong tool or used a tool incorrectly |
| `scope_creep` | Built more than was asked for |
| `integration_failure` | Failed to integrate with existing systems |

Each reflection output includes: `category`, `summary`, `pattern`, `prevention`, and `source_session`.

**Skip conditions**: Reflection is skipped if there are no session findings, if `ANTHROPIC_API_KEY` is not set, or if the `anthropic` package is not installed.

## File Bug Issues (Step 8)

When a reflection is categorized as `code_bug` and meets the confidence threshold, reflections creates a GitHub issue via `gh issue create` with the `bug` label. No code changes are made — a human decides whether and how to fix it.

### Confidence Criteria

An issue is filed when a reflection meets **2 of 3** criteria:

| Criterion | Condition |
|-----------|----------|
| Category | `category == "code_bug"` |
| Prevention | `prevention` field is non-empty |
| Pattern length | `pattern` field is at least 10 characters |

If fewer than 2 criteria are met, the reflection is logged but no issue is created.

### Ignore Log

The ignore log (Redis `ReflectionIgnore` model) suppresses issue creation for specific patterns for 14 days:

```bash
python scripts/reflections.py --ignore "pattern text here"
python scripts/reflections.py --ignore "pattern text here" --reason "Intentional design, not a bug"
```

### Safety Properties

- **Issues only** — Creates GitHub issues, never modifies code or opens PRs.
- **Dedup** — If an open issue or PR already exists for the pattern, no duplicate is created.
- **Ignore log** — Patterns can be silenced for 14 days with one CLI command.
- **Dry-run** — All logic is testable without external side effects.
- **Kill switch** — `REFLECTIONS_AUTO_FIX_ENABLED=false` disables the feature entirely.

## Multi-Repo Support

Reflections reads `config/projects.json`, filters to repos present on the current machine via `load_local_projects()`, and runs per-project analysis. A machine with only `ai` checked out analyzes only `ai`; a machine with four repos analyzes all four.

**Per-project steps**: 2 (Log Review), 4 (Task Cleanup), 11 (GitHub Issues + Telegram)
**AI-only steps**: Everything else runs once from the AI repo root

### Configuration

Each project entry in `config/projects.json`:

```json
{
  "working_directory": "/Users/valorengels/src/my-project",
  "github": { "org": "myorg", "repo": "my-project" },
  "telegram": { "groups": ["@my_group"] }
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `working_directory` | Yes | Must exist on disk to be included |
| `github.org` / `github.repo` | For issues/tasks | Steps 4 and 10 skip if absent |
| `telegram.groups` | No | Step 10 skips if absent or empty |

### Subprocess Scoping

Per-project subprocess calls (`gh issue list`, `gh issue create`) use `cwd=project["working_directory"]` rather than `os.chdir`. `gh` auto-detects the GitHub repo from the git remote of the given directory. Both `issue_exists_for_date()` and `create_reflections_issue()` accept and forward this `cwd` parameter to ensure dedup checks target the same repo as issue creation.

### Issue Dedup Guard

Step 10 uses two layers of deduplication to prevent duplicate GitHub issues:

1. **GitHub search** -- `issue_exists_for_date(date, cwd)` queries the target repo for existing issues with the same date title. This catches duplicates across separate reflections runs.
2. **In-memory guard** -- A module-level `_created_this_run` set in `scripts/reflections_report.py` tracks `(date, cwd)` tuples created during the current process. This prevents race condition duplicates when multiple projects are processed rapidly and GitHub's search index hasn't updated yet. The guard is reset via `reset_dedup_guard()` at the start of each `step_create_github_issue()` call.

### Findings Namespacing

`state.findings` keys use `"{slug}:category"` format (e.g., `"ai:log_review"`, `"popoto:tasks"`) to prevent per-project findings from colliding.

### Graceful Fallbacks

- `working_directory` absent from disk — project excluded from `load_local_projects()`
- `github` key missing — steps 4 and 10 log a warning and skip that project
- `telegram.groups` missing or empty — step 10 logs and skips
- `data/valor.session` missing — step 10 skips silently
- `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` not set — step 10 skips silently
- `telethon` not installed — step 10 skips silently

## Findings System

Every step can record findings via `state.add_finding(category, finding_string)`. Findings accumulate in `state.findings` as `{category: [strings]}`.

### Findings Flow

1. **Collected** — Steps append findings throughout execution
2. **Checkpointed** — Saved to Redis ReflectionRun after each step
3. **Reported** — Step 9 writes a local markdown report to `logs/reflections/`
4. **Published** — Step 10 creates per-project GitHub issues and posts to Telegram

## Redis TTL Cleanup (Step 12)

Prunes expired records to keep Redis lean:

| Model | Max Age | Method |
|-------|---------|--------|
| TelegramMessage | 90 days | `cleanup_expired()` |
| Link | 90 days | `cleanup_expired()` |
| Chat | 90 days | `cleanup_expired()` |
| AgentSession | 90 days | `cleanup_expired()` |
| BridgeEvent | 7 days | `cleanup_old()` |
| ReflectionRun | 30 days | `cleanup_expired()` |
| ReflectionIgnore | Per-entry TTL | `cleanup_expired()` |

## Branch and Plan Cleanup (Step 14)

| Action | What It Does |
|--------|--------------|
| Delete merged branches | Removes local branches fully merged into main |
| Orphaned plan check | Ensures every `docs/plans/*.md` file has a matching open GitHub issue |
| Completed plan detection | Flags plans where all checkboxes are checked — needs `/do-docs` then deletion |

## Redis Data Quality (Step 13)

| Check | What It Finds |
|-------|---------------|
| Unsummarized links | Links shared in the last 7 days with no `ai_summary` |
| Dead channels | Chats with no activity in 30+ days |
| Error patterns | Common error keywords recurring across recent session transcripts |
| Message volume | Messages per chat in the last 7 days |

## Operations

### Scheduling

The reflection scheduler starts automatically as part of the bridge worker loop. No separate launchd plist is needed for scheduling -- the scheduler ticks every 60 seconds and checks which reflections are due.

| Component | Detail |
|-----------|--------|
| Scheduler | `agent/reflection_scheduler.py` (asyncio task in bridge) |
| Registry | `config/reflections.yaml` |
| State | Redis via `models/reflection.py` |
| Tick interval | 60 seconds |
| Legacy plist | `com.valor.reflections.plist` (deprecated, bridge scheduler replaces it) |

### Adding a New Reflection

Add an entry to `config/reflections.yaml`:

```yaml
  - name: my-new-task
    description: "What this task does"
    interval: 3600  # every hour
    priority: low
    execution_type: function
    callable: "my_module.my_function"
    enabled: true
```

The scheduler picks it up on the next tick. No code changes or service restarts required (the registry is read at scheduler startup; restart bridge to pick up new entries).

### Quick Commands

| Command | Description |
|---------|-------------|
| `python scripts/reflections.py` | Run daily maintenance manually |
| `python scripts/reflections.py --dry-run` | Run without side effects |
| `python scripts/reflections.py --ignore "pattern"` | Suppress auto-fix for pattern for 14 days |
| `python scripts/reflections.py --ignore "pattern" --reason "why"` | Suppress with reason |
| `tail -f logs/bridge.log` | Stream bridge logs (includes reflection scheduler output) |
| `python -c "from models.reflections import ReflectionIgnore; [print(f'{e.pattern} (expires {e.expires_at})') for e in ReflectionIgnore.get_active()]"` | View active ignore entries |

### Output Locations

| Path | Content |
|------|---------|
| `logs/reflections.log` | Runner stdout/stderr |
| `logs/reflections/` | Generated reports (one per run) |

## Key Files

| File | Purpose |
|------|---------|
| `agent/reflection_scheduler.py` | Unified scheduler: registry loader, schedule evaluator, executor |
| `config/reflections.yaml` | Declarative registry of all reflections |
| `models/reflection.py` | Reflection state model (per-reflection Redis tracking) |
| `models/reflections.py` | Legacy models (ReflectionRun for daily pipeline, ReflectionIgnore) |
| `scripts/reflections.py` | Daily maintenance 15-step runner |
| `scripts/reflections_report.py` | GitHub issue creation module |
| `scripts/install_reflections.sh` | launchd installation script (legacy, kept for migration) |
| `com.valor.reflections.plist` | launchd schedule definition (legacy, kept for migration) |
| `config/projects.json` | Multi-repo project registry |
| `logs/reflections/` | Local report output directory |
| `tests/unit/test_reflection_scheduler.py` | Scheduler and registry tests |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All reflections | Yes — state persistence |
| PyYAML | Registry loader | Yes — reads `config/reflections.yaml` |
| `ANTHROPIC_API_KEY` | Daily maintenance steps 5, 7 | Conditional — LLM reflection and docs audit |
| `gh` CLI (authenticated) | Daily maintenance steps 4, 8, 10, 14 | Conditional — task cleanup, bug issues |
| `telethon` | Daily maintenance step 10 | Conditional — Telegram notifications |
| `config/projects.json` | Multi-repo reflections | Optional — defaults to AI repo only |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| Reflections did not run | `launchctl list \| grep reflections` | `./scripts/install_reflections.sh` |
| No GitHub issue created | No findings, or `gh auth status` failed | Check `tail -20 logs/reflections.log` |
| LLM reflection skipped | `ANTHROPIC_API_KEY` not set | Add to `.env` |
| Telegram post failed | Missing `data/valor.session` | Run `python scripts/telegram_login.py` |
| Auto-fix not triggering | Confidence criteria not met | Check reflection has pattern >=10 chars and non-empty prevention |
| State not resuming | Redis connection issue | Verify Redis is running |
| Step stuck/timing out | Auto-fix subprocess hung | Check for 10-minute timeout; review `logs/reflections.log` |

## See Also

- [Documentation Audit](documentation-audit.md) — step 5 deep dive
- [Skills Audit](do-skills-audit.md) — step 12 deep dive
