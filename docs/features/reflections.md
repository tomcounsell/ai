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
    callable: "agent.agent_session_queue._job_health_check"
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
| `timeout` | int | Optional per-reflection timeout in seconds. Defaults: 1800 (30 min) for function, 3600 (60 min) for agent |

### Registered Reflections

| Name | Interval | Priority | Type | Description |
|------|----------|----------|------|-------------|
| `health-check` | 5 min | high | function | Check running jobs for liveness, recover stuck ones |
| `agent-session-cleanup` | 1 hour | normal | function | Delete corrupted AgentSession records (invalid IDs, unsaveable) and rebuild indexes |
| `stale-branch-cleanup` | daily | low | function | Clean up session branches older than 72 hours |
| `popoto-index-cleanup` | daily | low | function | Rebuild Popoto model indexes to remove orphaned entries (see [Popoto Index Hygiene](popoto-index-hygiene.md)) |

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

### Resource Guards

Every reflection execution includes resource monitoring:

**Memory instrumentation**: `psutil.Process().memory_info().rss` is captured before and after each reflection. The delta is logged. If the delta exceeds 100MB (`MEMORY_DELTA_WARNING_BYTES`), a WARNING is emitted with the reflection name, delta, and absolute RSS values. Memory monitoring is best-effort -- if `psutil` is unavailable, reflections still run.

**Timeout enforcement**: Each reflection has a configurable timeout (via `timeout` field in YAML, or type-based defaults: 30 min for function, 60 min for agent). Function-type reflections are wrapped in `asyncio.wait_for()`. For async callables, this provides true cancellation. For sync callables running via `run_in_executor()`, the `TimeoutError` is raised but the thread cannot be cancelled (detection-only). Timeout errors are logged and the reflection is marked with error status.

**API call cap (DocsAuditor)**: The `DocsAuditor` class (used by `documentation_audit`) accepts a `max_api_calls` parameter (default: 50). Each Anthropic API call increments a counter. When the cap is reached, processing stops gracefully -- remaining files are skipped, partial results are returned, and a WARNING is logged. This prevents unbounded API consumption.

### Log Rotation

All log files have rotation configured to prevent unbounded growth. Two mechanisms are used depending on who writes the file:

| Log File | Writer | Rotation Mechanism | Max Size | Backups |
|----------|--------|--------------------|----------|---------|
| `bridge.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `bridge/telegram_bridge.py` | 10MB | 5 |
| `watchdog.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `monitoring/bridge_watchdog.py` | 10MB | 5 |
| `reflections.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `scripts/reflections.py` | 10MB | 5 |
| `bridge.error.log` | launchd (StandardErrorPath) | Shell `rotate_log` in `valor-service.sh` | 10MB | 3 |
| `reflections_error.log` | launchd (StandardErrorPath) | Shell `rotate_log` in `valor-service.sh` | 10MB | 3 |

**Python-rotated files** use `RotatingFileHandler` which rotates automatically during writes. No service restart needed. Services using `config/settings.py:configure_logging()` also get rotation automatically via `RotatingFileHandler` with configurable `max_file_size` and `backup_count`.

**Shell-rotated files** are rotated by the `rotate_log` function in `scripts/valor-service.sh` on every service start/restart. A `newsyslog` config (`config/newsyslog.valor.conf`) provides a safety net for long-running services -- macOS runs newsyslog hourly via launchd. Note: since launchd holds file descriptors open, newsyslog rotation may not be effective until the next service restart. The shell `rotate_log` function is the primary mechanism.

### Bridge Watchdog (External)

The bridge watchdog (`com.valor.bridge-watchdog`) is intentionally NOT in the reflection registry. It must run as an external launchd service because it monitors the bridge process itself -- running it inside the process it monitors defeats its purpose.

When the watchdog detects that the bridge process is not running (via `pgrep`), it calls `crash_tracker.log_crash("bridge_dead_on_watchdog_check")` to record the event. This captures SIGKILL and OOM kills that leave no traceback. The crash tracker's pattern detection requires 3+ crashes in 30 minutes before triggering escalation, so a single false positive from a startup race is harmless.

## Daily Maintenance Pipeline (16 Units)

The daily maintenance pipeline runs from `scripts/reflections.py`. It is invoked manually or via launchd (not via the reflection scheduler -- the `daily-maintenance` registry entry was removed in PR #664). The runner loads state from Redis, executes each unit in order, and checkpoints after every unit. If interrupted, the next run resumes from where it left off. Each unit is independently failable -- a crash in one unit does not block the rest.

The pipeline has 16 units: 13 independent items and 3 merged pipelines. Completed units are tracked by string key (e.g. `"legacy_code_scan"`), not by integer position. This means units can be reordered or renamed without data migrations — any unknown key in `completed_steps` is simply skipped.

### 16-Unit Pipeline

**Independent units** (each checkpointed individually):

| # | Key | Name | Description | Scope | Failure Mode |
|---|-----|------|-------------|-------|--------------|
| 1 | `legacy_code_scan` | Clean Up Stale Code | Scans for TODO comments, old-style typing imports | AI repo only | Non-blocking |
| 2 | `log_review` | Review Previous Day's Logs | Extracts structured errors from log files and Redis BridgeEvent records | Per-project | Non-blocking, skips if `logs/bridge.log` missing |
| 3 | `task_management` | Clean Up Task Management | Lists open bug issues via `gh issue list` per project | Per-project | Non-blocking, requires `gh` auth |
| 4 | `documentation_audit` | Audit Documentation | Weekly LLM-powered accuracy audit of `docs/` (see [Documentation Audit](documentation-audit.md)) | AI repo only | Non-blocking, requires `ANTHROPIC_API_KEY` |
| 5 | `skills_audit` | Skills Audit | Validates all SKILL.md files against template standards (see [Skills Audit](do-skills-audit.md)) | AI repo only | Non-blocking |
| 6 | `hooks_audit` | Hooks Audit | Scans hooks.log for recent errors and validates settings.json hook configuration (see [Hooks Best Practices](hooks-best-practices.md)) | AI repo only | Non-blocking |
| 7 | `redis_ttl_cleanup` | Redis TTL Cleanup | Prunes expired records across all Redis models | AI repo only | Non-blocking |
| 8 | `redis_data_quality` | Redis Data Quality | Surfaces data quality issues: unsummarized links, dead channels, error patterns | AI repo only | Non-blocking |
| 9 | `branch_plan_cleanup` | Branch and Plan Cleanup | Deletes merged branches; ensures plans have open issues; flags completed plans for docs migration | AI repo only | Non-blocking, requires `gh` auth |
| 10 | `feature_docs_audit` | Feature Docs Audit | Checks for stale references, README accuracy, plan-masquerading-as-feature, stub docs | AI repo only | Non-blocking |
| 11 | `principal_staleness` | Principal Context Staleness | Checks age of PRINCIPAL.md and flags if stale | AI repo only | Non-blocking |
| 12 | `disk_space_check` | Disk Space Check | Checks free disk space on project volume; finding if below 10 GB (see [Adding Reflection Tasks](adding-reflection-tasks.md)) | AI repo only | Non-blocking |
| 13 | `pr_review_audit` | PR Review Audit | Scans merged PRs for unaddressed review findings from do-pr-review, files GitHub issues with severity labels | Per-project | Non-blocking, requires `gh` auth |

**Merged pipelines** (sub-steps run internally, one checkpoint for the whole group):

| # | Key | Name | Sub-steps | Description |
|---|-----|------|-----------|-------------|
| 14 | `session_intelligence` | Session Intelligence | session_analysis → llm_reflection → auto_fix_bugs | Analyzes sessions, reflects via Haiku, files high-confidence bug issues |
| 15 | `behavioral_learning` | Behavioral Learning | episode_cycle_close → pattern_crystallization | Closes completed SDLC episodes and crystallizes recurring patterns |
| 16 | `daily_report_and_notify` | Daily Report & Notify | produce_report → create_github_issue | Writes report, posts GitHub issues, sends Telegram summary (must be last) |

**Removed:** `step_check_sentry` — was a permanent no-op (Sentry MCP never available in standalone mode). Deleted entirely.

## State & Persistence

All reflections state lives in Redis via three Popoto models defined in `models/reflections.py`.

### ReflectionRun

One record per calendar date. Acts as the primary state checkpoint for resumability.

| Field | Type | Purpose |
|-------|------|---------|
| `date` | UniqueKeyField | YYYY-MM-DD, one run per day |
| `completed_steps` | ListField | Units already finished, e.g. `["legacy_code_scan", "log_review"]` |
| `daily_report` | ListField | Human-readable log lines per unit |
| `findings` | DictField | `{category: [finding_strings]}` |
| `session_analysis` | DictField | Output from session analysis sub-step |
| `reflections` | ListField | LLM reflection outputs |
| `auto_fix_attempts` | ListField | Auto-fix attempt records |
| `step_progress` | DictField | Per-unit metrics, e.g. `{"legacy_code_scan": {"findings": 2}}` |
| `started_at` | SortedField(float) | Unix timestamp, used for cleanup |
| `dry_run` | Field(bool) | True if `--dry-run` mode |

**Checkpoint cycle**: After each unit completes (or fails), the runner saves all state to Redis via `ReflectionRun.save_checkpoint()`. This deletes and recreates the record to handle Popoto's KeyField constraints.

**Resume scenario**: If reflections crashes during `session_intelligence`, the next run loads the ReflectionRun for today, sees `completed_steps = ["legacy_code_scan", ...]`, and continues from `session_intelligence`.

**Integer migration**: If `completed_steps` contains integers (data from before the string-key refactor), the runner resets it to an empty list. This safely re-runs any steps that ran earlier in the day — all units are idempotent.

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

**Cleanup**: Expired entries are pruned at the start of each auto-fix run (inside `session_intelligence`) and during Redis TTL cleanup (`redis_ttl_cleanup`).

### PRReviewAudit

Deduplication tracker for PR review audit findings. Prevents re-filing GitHub issues for already-audited review comments.

| Field | Type | Purpose |
|-------|------|---------|
| `audit_id` | AutoKeyField | UUID |
| `repo` | KeyField | GitHub repo slug (e.g. "tomcounsell/ai") |
| `pr_number` | IntField | Pull request number |
| `comment_id` | UniqueKeyField | Composite dedup key: `{repo}:{pr_number}:{comment_id}:{finding_index}` |
| `severity` | Field | Classified severity (critical, standard, trivial) |
| `filed_issue_url` | Field(null) | URL of the filed GitHub issue, if any |
| `audited_at` | SortedField(float) | Timestamp when audited (for TTL cleanup and time window lookback) |

**Dedup key format**: `{repo}:{pr_number}:{comment_id}:{finding_index}`. A single review comment may contain multiple structured findings; the finding_index ensures each is tracked independently.

**Time window lookback**: `PRReviewAudit.last_successful_run()` returns the most recent `audited_at` timestamp, used by the PR review audit step to determine which PRs to scan. Falls back to yesterday if no prior audits exist. This closes multi-day gaps when reflections is down.

**Cleanup**: Records older than 90 days are pruned via `cleanup_expired()` during Redis TTL cleanup (`redis_ttl_cleanup`).


## Session Analysis (part of `session_intelligence` pipeline)

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

## LLM Reflection (part of `session_intelligence` pipeline)

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

## File Bug Issues (part of `session_intelligence` pipeline)

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

Reflections reads `~/Desktop/Valor/projects.json`, filters to repos present on the current machine via `load_local_projects()`, and runs per-project analysis. A machine with only `ai` checked out analyzes only `ai`; a machine with four repos analyzes all four.

**Per-project steps**: 2 (Log Review), 4 (Task Cleanup), 11 (GitHub Issues + Telegram), 12 (PR Review Audit)
**AI-only steps**: Everything else runs once from the AI repo root

### Configuration

Each project entry in `~/Desktop/Valor/projects.json`:

```json
{
  "working_directory": "~/src/my-project",
  "github": { "org": "myorg", "repo": "my-project" },
  "telegram": { "groups": ["@my_group"] }
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `working_directory` | Yes | Must exist on disk to be included |
| `github.org` / `github.repo` | For issues/tasks | `task_management` and `daily_report_and_notify` skip if absent |
| `telegram.groups` | No | `daily_report_and_notify` skips if absent or empty |

### Subprocess Scoping

Per-project subprocess calls (`gh issue list`, `gh issue create`) use `cwd=project["working_directory"]` rather than `os.chdir`. `gh` auto-detects the GitHub repo from the git remote of the given directory. Both `issue_exists_for_date()` and `create_reflections_issue()` accept and forward this `cwd` parameter to ensure dedup checks target the same repo as issue creation.

### Issue Dedup Guard

The `daily_report_and_notify` pipeline uses two layers of deduplication to prevent duplicate GitHub issues:

1. **GitHub search** -- `issue_exists_for_date(date, cwd)` queries the target repo for existing issues with the same date title. This catches duplicates across separate reflections runs.
2. **In-memory guard** -- A module-level `_created_this_run` set in `scripts/reflections_report.py` tracks `(date, cwd)` tuples created during the current process. This prevents race condition duplicates when multiple projects are processed rapidly and GitHub's search index hasn't updated yet. The guard is reset via `reset_dedup_guard()` at the start of each `step_create_github_issue()` call.

### Findings Namespacing

`state.findings` keys use `"{slug}:category"` format (e.g., `"ai:log_review"`, `"popoto:tasks"`) to prevent per-project findings from colliding.

### Graceful Fallbacks

- `working_directory` absent from disk — project excluded from `load_local_projects()`
- `github` key missing — `task_management` and `daily_report_and_notify` log a warning and skip that project
- `telegram.groups` missing or empty — `daily_report_and_notify` logs and skips
- `data/valor.session` missing — `daily_report_and_notify` skips silently
- `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` not set — `daily_report_and_notify` skips silently
- `telethon` not installed — `daily_report_and_notify` skips silently

## Findings System

Every unit can record findings via `state.add_finding(category, finding_string)`. Findings accumulate in `state.findings` as `{category: [strings]}`.

### Findings Flow

1. **Collected** — Units append findings throughout execution
2. **Checkpointed** — Saved to Redis ReflectionRun after each unit
3. **Reported** — `daily_report_and_notify` writes a local markdown report to `logs/reflections/`
4. **Published** — `daily_report_and_notify` creates per-project GitHub issues and posts to Telegram

## Redis TTL Cleanup (`redis_ttl_cleanup`)

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
| PRReviewAudit | 90 days | `cleanup_expired()` |

## PR Review Audit (`pr_review_audit`)

Scans merged PRs for unaddressed review findings and files GitHub issues. This is the safety net for when the PM merges a PR with tech debt, test gaps, or nits left behind -- items that would otherwise silently disappear.

### How It Works

1. **PR discovery**: For each project with a `github` config, fetches merged PRs since the last successful audit via `gh pr list --state merged --search "merged:>={date}"`
2. **Review parsing**: Fetches review comments and parses the structured do-pr-review format (`**Severity:**`, `**File:**`, `**Code:**`, `**Issue:**`, `**Fix:**`)
3. **Address check**: For each finding, checks if the referenced file was modified in commits after the review comment timestamp (file-level heuristic)
4. **Deduplication**: Checks Redis `PRReviewAudit` model to skip already-audited findings
5. **Issue filing**: Files one GitHub issue per PR with unaddressed findings, grouped by severity, with labels `pr-review-audit` plus severity-specific labels (`critical`, `tech-debt`, `nit`)

### Severity Classification

| Review Format | Classification | GitHub Label |
|--------------|----------------|--------------|
| `blocker` | critical | `critical` |
| `tech_debt` | standard | `tech-debt` |
| `nit` | trivial | `nit` |

### Safety Properties

- **Per-project isolation** -- A failure on one project does not block others
- **Rate limited** -- Processes at most 20 merged PRs per project per run
- **Dedup** -- Redis-backed `PRReviewAudit` model prevents re-filing for audited comments
- **Dry-run safe** -- When `--dry-run` is set, logs findings but skips issue creation and Redis writes
- **Structured format only** -- Only parses well-formed do-pr-review findings; ignores free-text comments

### Findings Namespacing

Findings are added to `state.findings` with key `{slug}:pr_review_audit`. Step progress is recorded in `state.step_progress["pr_review_audit"]` with metrics: `prs_scanned`, `findings_total`, `findings_unaddressed`, `issues_filed`.

## Branch and Plan Cleanup (`branch_plan_cleanup`)

| Action | What It Does |
|--------|--------------|
| Delete merged branches | Removes local branches fully merged into main |
| Orphaned plan check | Ensures every `docs/plans/*.md` file has a matching open GitHub issue |
| Completed plan detection | Flags plans where all checkboxes are checked — needs `/do-docs` then deletion |

## Redis Data Quality (`redis_data_quality`)

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
| Old plist | `com.valor.reflections.plist` (now managed by bridge scheduler) |

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

### Session Log Cleanup

After the daily maintenance pipeline completes, `main()` calls `bridge.session_logs.cleanup_old_snapshots()` to prune session log directories older than 7 days from `logs/sessions/`. The count of removed directories is logged. Failures are caught and logged as non-fatal -- they never block the rest of the pipeline.

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
| `models/reflections.py` | Core models: ReflectionRun (daily pipeline state), ReflectionIgnore (auto-fix suppression), PRReviewAudit (PR review dedup) |
| `scripts/reflections.py` | Daily maintenance 16-unit runner |
| `scripts/reflections_report.py` | GitHub issue creation module |
| `scripts/install_reflections.sh` | launchd installation script (kept for manual invocation) |
| `com.valor.reflections.plist` | launchd schedule definition (kept for manual invocation) |
| `~/Desktop/Valor/projects.json` | Multi-repo project registry |
| `logs/reflections/` | Local report output directory |
| `tests/unit/test_reflection_scheduler.py` | Scheduler and registry tests |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All reflections | Yes — state persistence |
| PyYAML | Registry loader | Yes — reads `config/reflections.yaml` |
| psutil | Memory instrumentation | Optional — memory snapshots degrade gracefully if missing |
| `ANTHROPIC_API_KEY` | `documentation_audit`, `session_intelligence` | Conditional — LLM reflection and docs audit |
| `gh` CLI (authenticated) | `task_management`, `session_intelligence`, `daily_report_and_notify`, `branch_plan_cleanup`, `pr_review_audit` | Conditional — task cleanup, bug issues, PR review audit |
| `telethon` | `daily_report_and_notify` | Conditional — Telegram notifications |
| `~/Desktop/Valor/projects.json` | Multi-repo reflections | Optional — defaults to AI repo only |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| Reflections did not run | `launchctl list \| grep reflections` | `./scripts/install_reflections.sh` |
| No GitHub issue created | No findings, or `gh auth status` failed | Check `tail -20 logs/reflections.log` |
| LLM reflection skipped | `ANTHROPIC_API_KEY` not set | Add to `.env` |
| Telegram post failed | Missing `data/valor.session` | Run `python scripts/telegram_login.py` |
| Auto-fix not triggering | Confidence criteria not met | Check reflection has pattern >=10 chars and non-empty prevention |
| State not resuming | Redis connection issue | Verify Redis is running |
| Unit stuck/timing out | Auto-fix subprocess hung | Check for timeout; review `logs/reflections.log` |
| Reflection killed (SIGKILL) | Resource-based kill (OOM, ulimit) | Check watchdog crash tracker: `python -c "from monitoring.crash_tracker import get_recent_crashes; print(get_recent_crashes(3600))"` |
| High memory delta warning | Reflection consumed >100MB | Check `bridge.log` for `HIGH MEMORY DELTA` entries; investigate the flagged reflection |
| Docs auditor stopped early | API call cap reached | Check `bridge.log` for `API call cap reached`; increase `max_api_calls` if doc count grew |

## See Also

- [Documentation Audit](documentation-audit.md) — `documentation_audit` unit deep dive
- [Hooks Best Practices & Audit](hooks-best-practices.md) — `hooks_audit` unit deep dive
- [Skills Audit](do-skills-audit.md) — `skills_audit` unit deep dive
