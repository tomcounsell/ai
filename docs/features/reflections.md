# Reflections: Autonomous Maintenance System

The reflections system is a unified framework for all recurring non-issue work. A single lightweight scheduler (`agent/reflection_scheduler.py`) reads from a declarative registry (`config/reflections.yaml`), tracks state in Redis, and executes reflections on schedule. This replaces the previously scattered scheduling mechanisms (launchd plists, asyncio loops, startup hooks).

## Unified Reflection Scheduler

All recurring tasks are declared in `config/reflections.yaml` and managed by a single scheduler that runs as an asyncio task inside the standalone worker process (`python -m worker`).

### Architecture

```
Worker startup (worker/__main__.py)
  -> ReflectionScheduler.start()
    -> Tick every 60 seconds
      -> For each reflection in registry:
        -> Check if due (ran_at + interval < now)
        -> Check skip-if-running guard
        -> Execute: function (direct callable) or agent (PM session)
        -> Update state in Redis (Reflection model)
```

### Registry Format (`config/reflections.yaml`)

```yaml
reflections:
  - name: session-liveness-check
    description: "Check running sessions for liveness and timeout, recover stuck ones"
    interval: 300       # 5 minutes
    priority: high
    execution_type: function
    callable: "agent.agent_session_queue._agent_session_health_check"
    enabled: true
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier (used as Redis key) |
| `interval` | int | Seconds between runs |
| `priority` | string | `urgent`, `high`, `normal`, or `low` |
| `execution_type` | string | `function` (direct callable) or `agent` (PM session) |
| `callable` | string | Dotted Python path (for function type) |
| `command` | string | Natural-language prompt for PM session (for agent type) |
| `enabled` | bool | Whether this reflection is active (default: true) |
| `timeout` | int | Optional per-reflection timeout in seconds. Defaults: 1800 (30 min) for function, 3600 (60 min) for agent |

### Registry Location (Vault-First)

The scheduler resolves `config/reflections.yaml` via a three-level fallback:

1. `REFLECTIONS_YAML` env var (explicit override, e.g., for testing)
2. `~/Desktop/Valor/reflections.yaml` (vault copy — iCloud-synced, takes precedence)
3. `config/reflections.yaml` in-repo (symlink to vault on live machines)

On live machines, `config/reflections.yaml` is a symlink to `~/Desktop/Valor/reflections.yaml`.
The symlink is created by `sync_reflections_yaml()` in `scripts/update/env_sync.py` during
each update run. This ensures the scheduler always reads the vault version.

### Registered Reflections

**Infrastructure / health:**

| Name | Interval | Priority | Type | Description |
|------|----------|----------|------|-------------|
| `session-liveness-check` | 5 min | high | function | Check running sessions for liveness and timeout, recover stuck ones |
| `agent-session-cleanup` | 1 hour | normal | function | Delete corrupted AgentSession records and repair orphan `$IndexF` members (phantom-filter guarded — see [bridge self-healing](bridge-self-healing.md#7-agent-session-cleanup-agentagent_session_queuepy)) |
| `stale-branch-cleanup` | daily | low | function | Clean up session branches older than 72 hours (disabled) |
| `redis-index-cleanup` | daily | low | function | Rebuild Redis model indexes to remove orphaned entries |
| `circuit-health-gate` | 1 min | high | function | Check Anthropic circuit state; manage `queue_paused` and `worker:hibernating` flags atomically |
| `session-count-throttle` | 1 hour | normal | function | Count sessions in last hour; write throttle level |
| `failure-loop-detector` | 1 hour | normal | function | Scan failed sessions; file one GitHub issue per novel error cluster |
| `session-recovery-drip` | 30 sec | high | function | Drip one paused_circuit or paused session back to pending per tick (paused_circuit first) |
| `system-health-digest` | daily | low | agent | Daily Telegram health summary **(disabled — spawns agent)** |
| `memory-dedup` | daily | normal | function | LLM-based semantic memory consolidation (dry-run default) |
| `sentry-issue-triage` | daily | low | agent | Triage unresolved Sentry issues across projects (disabled) |

**Maintenance:**

| Name | Callable | Description |
|------|----------|-------------|
| `tech-debt-scan` | `reflections.maintenance.run_legacy_code_scan` | Scan for TODO comments and `deprecated` typing imports |
| `redis-ttl-cleanup` | `reflections.maintenance.run_redis_ttl_cleanup` | Prune expired records across all Redis models |
| `redis-quality-audit` | `reflections.maintenance.run_redis_data_quality` | Audit data quality: unsummarized links, dead channels, error patterns |
| `merged-branch-cleanup` | `reflections.maintenance.run_branch_plan_cleanup` | Delete merged branches; audit docs/plans/ for stale/orphaned plans **(disabled — calls gh CLI)** |
| `disk-space-check` | `reflections.maintenance.run_disk_space_check` | Check free disk space; warn if below 10 GB |
| `analytics-rollup` | `reflections.maintenance.run_analytics_rollup` | Aggregate daily analytics; purge old records |

**Auditing:**

| Name | Callable | Description |
|------|----------|-------------|
| `daily-log-review` | `reflections.auditing.run_log_review` | Review previous day's logs per project |
| `documentation-audit` | `reflections.auditing.run_documentation_audit` | LLM-powered docs accuracy audit (see [Documentation Audit](documentation-audit.md)) |
| `skills-audit` | `reflections.auditing.run_skills_audit` | Validate all SKILL.md files (see [Skills Audit](do-skills-audit.md)) |
| `hooks-audit` | `reflections.auditing.run_hooks_audit` | Audit Claude Code hooks and settings (see [Hooks Best Practices](hooks-best-practices.md)) |
| `feature-docs-audit` | `reflections.auditing.run_feature_docs_audit` | Audit docs/features/ for stale terms, stub docs, dead code refs |
| `pr-review-audit` | `reflections.auditing.run_pr_review_audit` | Scan merged PRs for unaddressed review findings; file GitHub issues **(disabled — calls gh CLI)** |

**Task management:**

| Name | Callable | Description |
|------|----------|-------------|
| `task-backlog-check` | `reflections.task_management.run_task_management` | Check open bug issues per project and local TODO files **(disabled — calls gh CLI)** |
| `principal-staleness` | `reflections.task_management.run_principal_staleness` | Check if config/PRINCIPAL.md is stale (>90 days) |

**Pipelines:**

| Name | Callable | Description |
|------|----------|-------------|
| `session-intelligence` | `reflections.session_intelligence.run` | Session Analysis → LLM Reflection → Bug Issue Filing **(disabled — calls gh CLI and spawns agent)** |
| `behavioral-learning` | `reflections.behavioral_learning.run` | Episode Cycle-Close → Pattern Crystallization |
| `daily-report-and-notify` | `reflections.daily_report.run` | Produce daily report → GitHub issues → Telegram notification (run last) **(disabled — spawns agent)** |

**Memory management:**

| Name | Callable | Description |
|------|----------|-------------|
| `memory-decay-prune` | `reflections.memory_management.run_memory_decay_prune` | Delete below-threshold memories with zero access (dry-run default) |
| `memory-quality-audit` | `reflections.memory_management.run_memory_quality_audit` | Flag memories with quality issues (zero-access, chronically low confidence) |
| `knowledge-reindex` | `reflections.memory_management.run_knowledge_reindex` | Re-index ~/src/work-vault/ docs into KnowledgeDocument records |

### State Model (`models/reflection.py`)

Each reflection gets a `Reflection` record in Redis tracking execution state:

| Field | Type | Purpose |
|-------|------|---------|
| `name` | KeyField | Unique identifier matching registry |
| `ran_at` | Field(float) | Unix timestamp of last execution start |
| `run_count` | IntField | Total number of executions |
| `last_status` | Field | `pending`, `running`, `success`, `error`, `skipped` |
| `last_error` | Field | Error message from last failure |
| `last_duration` | Field(float) | Duration of last run in seconds |
| `run_history` | ListField | Append-only list of run dicts (capped at 200) |

Note: `next_due` is computed as `ran_at + interval` in the dashboard data layer, not stored as a field.

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
| `worker.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `worker/__main__.py` | 10MB | 5 |
| `bridge.error.log` | launchd (StandardErrorPath) | Shell `rotate_log` in `valor-service.sh` | 10MB | 3 |

**Python-rotated files** use `RotatingFileHandler` which rotates automatically during writes. No service restart needed. Services using `config/settings.py:configure_logging()` also get rotation automatically via `RotatingFileHandler` with configurable `max_file_size` and `backup_count`.

**Shell-rotated files** are rotated by the `rotate_log` function in `scripts/valor-service.sh` on every service start/restart. A `newsyslog` config (`config/newsyslog.valor.conf`) provides a safety net for long-running services -- macOS runs newsyslog hourly via launchd.

### Bridge Watchdog (External)

The bridge watchdog (`com.valor.bridge-watchdog`) is intentionally NOT in the reflection registry. It must run as an external launchd service because it monitors the bridge process itself -- running it inside the process it monitors defeats its purpose.

When the watchdog detects that the bridge process is not running (via `pgrep`), it calls `crash_tracker.log_crash("bridge_dead_on_watchdog_check")` to record the event. This captures SIGKILL and OOM kills that leave no traceback.

## reflections/ Package

All daily maintenance work is implemented as standalone async callables in the `reflections/` package. Each callable returns a standard dict:

```python
{"status": "ok" | "error", "findings": [...], "summary": str}
```

**Critical constraint**: Reflection callables are invoked from inside the asyncio event loop. Any `subprocess.run()` or other blocking I/O call must be wrapped with `await asyncio.to_thread(subprocess.run, ...)` or `loop.run_in_executor()`. A bare `subprocess.run()` inside an `async def` blocks the event loop, freezing the reflection scheduler and preventing worker heartbeat writes — which causes the worker watchdog to kill the process.

The package modules:

| Module | Description |
|--------|-------------|
| `reflections.utils` | Shared helpers: `load_local_projects()`, `is_ignored()`, `load_ignore_entries()`, `has_existing_github_work()`, `run_llm_reflection()` |
| `reflections.maintenance` | 6 maintenance callables (TTL cleanup, data quality, branch/plan cleanup, etc.) |
| `reflections.auditing` | 6 auditing callables (log review, docs audit, skills audit, hooks audit, PR review audit) |
| `reflections.task_management` | 2 task management callables (task check, principal staleness) |
| `reflections.session_intelligence` | Pipeline: session analysis → LLM reflection → bug issue filing |
| `reflections.behavioral_learning` | Pipeline: episode cycle-close → pattern crystallization |
| `reflections.daily_report` | Pipeline: collect findings → GitHub issues → Telegram notification |
| `reflections.memory_management` | 3 memory management callables (decay prune, quality audit, knowledge reindex) |

## State & Persistence

### Reflection (per-reflection scheduler state)

See the `Reflection` model description in the scheduler section above. One record per named reflection in the registry, tracking execution timing and history.

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

**Cleanup**: Expired entries are pruned during Redis TTL cleanup (`redis-ttl-cleanup`).

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

**Time window lookback**: `PRReviewAudit.last_successful_run()` returns the most recent `audited_at` timestamp, used by the PR review audit step to determine which PRs to scan.

**Cleanup**: Records older than 90 days are pruned via `cleanup_expired()` during Redis TTL cleanup.

### docs_auditor State

`scripts/docs_auditor.py` tracks the last audit date in Redis via a plain key:

```python
redis.Redis.from_url(settings.REDIS_URL).get("docs_auditor:last_audit_date")
```

This replaced the former `ReflectionRun` dependency (see issue #748 for the migration history).

### Docs Auditor Authentication

The docs auditor uses the **Anthropic Python SDK directly** (not the OAuth subprocess harness used by AgentSessions). This means it requires a different credential:

| Component | Credential Used |
|-----------|----------------|
| AgentSessions (Claude Code via `claude -p`) | `CLAUDE_CODE_OAUTH_TOKEN` |
| DocsAuditor (`scripts/docs_auditor.py`) | `ANTHROPIC_API_KEY` |

**Behavior when `ANTHROPIC_API_KEY` is absent or invalid:**

The auditor performs a startup auth probe (`_check_auth()`) before iterating any docs. If the key is missing, a sentinel string (`"None"`, `"null"`, `"false"`, `"0"`), or invalid (rejected by the Anthropic API), the auditor logs a single `WARNING` and returns immediately:

```
WARNING  docs_auditor: Docs auditor skipping: ANTHROPIC_API_KEY not set
```

No `ERROR` lines are emitted, no docs are processed, and the worker heartbeat is unaffected. The reflection wrapper (`run_documentation_audit()` in `reflections/auditing.py`) returns `{"status": "disabled", ...}` — distinct from `{"status": "ok", ...}` for a schedule-based skip — so dashboards and monitoring can distinguish a permanently-disabled auditor from a temporarily-skipped one.

**To enable docs auditing**, add `ANTHROPIC_API_KEY` to the worker's environment (e.g., in `~/Desktop/Valor/.env` or the worker's launchd plist). The auditor will automatically begin running on its weekly schedule once the key is present and valid.

**Non-auth API failures** (rate limits, transient network errors) are handled by a consecutive-error circuit break: if 3 or more consecutive doc-audit errors occur during a run, the loop exits early with a `WARNING`. This caps error cascade for transient failures without requiring a full circuit breaker integration.

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

These regex patterns are defined in `CORRECTION_PATTERNS` in `reflections/utils.py`.

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

When a reflection is categorized as `code_bug` and meets the confidence threshold, reflections creates a GitHub issue via `gh issue create` with the `bug` label.

### Confidence Criteria

An issue is filed when a reflection meets **2 of 3** criteria:

| Criterion | Condition |
|-----------|----------|
| Category | `category == "code_bug"` |
| Prevention | `prevention` field is non-empty |
| Pattern length | `pattern` field is at least 10 characters |

### Ignore Log

The ignore log (Redis `ReflectionIgnore` model) suppresses issue creation for specific patterns for 14 days. Use `reflections.utils.is_ignored()` with `load_ignore_entries()` to check patterns.

### Safety Properties

- **Issues only** — Creates GitHub issues, never modifies code or opens PRs.
- **Dedup** — If an open issue or PR already exists for the pattern, no duplicate is created.
- **Ignore log** — Patterns can be suppressed via `ReflectionIgnore.add_ignore(pattern, days=14)`.
- **Dry-run** — All logic is testable without external side effects.

## Multi-Repo Support

Reflections reads `~/Desktop/Valor/projects.json`, filters to repos present on the current machine via `load_local_projects()`, and runs per-project analysis.

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
| `github.org` / `github.repo` | For issues/tasks | `task_management` and `daily_report` skip if absent |
| `telegram.groups` | No | `daily_report` skips Telegram notification if absent or empty |

### Graceful Fallbacks

- `working_directory` absent from disk — project excluded from `load_local_projects()`
- `github` key missing — `task_management` and `daily_report` log a warning and skip
- `telegram.groups` missing or empty — `daily_report` logs and skips
- `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` not set — `daily_report` skips silently

## Redis TTL Cleanup (`redis-ttl-cleanup`)

Prunes expired records to keep Redis lean:

| Model | Max Age | Method |
|-------|---------|--------|
| TelegramMessage | 90 days | `cleanup_expired()` |
| Link | 90 days | `cleanup_expired()` |
| Chat | 90 days | `cleanup_expired()` |
| AgentSession | 90 days | `cleanup_expired()` |
| BridgeEvent | 7 days | `cleanup_old()` |
| ReflectionIgnore | Per-entry TTL | `cleanup_expired()` |
| PRReviewAudit | 90 days | `cleanup_expired()` |

## PR Review Audit (`pr-review-audit`)

Scans merged PRs for unaddressed review findings and files GitHub issues.

### How It Works

1. **PR discovery**: For each project with a `github` config, fetches merged PRs since the last successful audit
2. **Review parsing**: Fetches review comments and parses the structured do-pr-review format (`**Severity:**`, `**File:**`, `**Code:**`, `**Issue:**`, `**Fix:**`)
3. **Address check**: For each finding, checks if the referenced file was modified in commits after the review comment timestamp
4. **Deduplication**: Checks Redis `PRReviewAudit` model to skip already-audited findings
5. **Issue filing**: Files one GitHub issue per PR with unaddressed findings, grouped by severity

### Severity Classification

| Review Format | Classification | GitHub Label |
|--------------|----------------|--------------|
| `blocker` | critical | `critical` |
| `tech_debt` | standard | `tech-debt` |
| `nit` | trivial | `nit` |

## Memory Management Reflections

Three new memory management reflections added in issue #748:

### `memory-decay-prune`

Prunes zero-access memories below the weak-forgetting threshold:
- `WF_MIN_THRESHOLD = 0.15` — memories below this score and with zero access are candidates
- `PRUNE_AGE_DAYS = 30` — memory must be at least 30 days old
- `IMPORTANCE_EXEMPT_THRESHOLD = 7.0` — importance >= 7.0 exempt from pruning
- `MAX_PRUNE_PER_RUN = 50` — safety cap per run
- **Dry-run default**: set `MEMORY_DECAY_PRUNE_APPLY=true` to enable actual deletion

### `memory-quality-audit`

Flags memories with quality issues for human review:
- Zero-access memories older than 30 days
- Memories with confidence consistently below 0.2

### `knowledge-reindex`

Re-indexes `~/src/work-vault/` documents into `KnowledgeDocument` Redis records. Gracefully stubs if `tools.knowledge.indexer` is unavailable or the vault directory doesn't exist.

## Operations

### Scheduling

The reflection scheduler starts automatically as part of the standalone worker process (`python -m worker`). No separate launchd plist is needed — the scheduler ticks every 60 seconds.

| Component | Detail |
|-----------|--------|
| Scheduler | `agent/reflection_scheduler.py` (asyncio task in worker) |
| Registry | `config/reflections.yaml` (symlink → `~/Desktop/Valor/reflections.yaml`) |
| State | Redis via `models/reflection.py` |
| Tick interval | 60 seconds |

### Quick Commands

| Command | Description |
|---------|-------------|
| `tail -f logs/worker.log` | Stream worker logs (includes reflection scheduler output) |
| `curl -s localhost:8500/dashboard.json` | Full system state including reflection status |
| `python -c "from models.reflections import ReflectionIgnore; [print(f'{e.pattern}') for e in ReflectionIgnore.get_active()]"` | View active ignore entries |
| `python -c "from models.reflections import ReflectionIgnore; ReflectionIgnore.add_ignore('pattern', reason='why', days=14)"` | Add an ignore entry |

## Key Files

| File | Purpose |
|------|---------|
| `agent/reflection_scheduler.py` | Unified scheduler: registry loader, schedule evaluator, executor |
| `config/reflections.yaml` | Declarative registry symlink → `~/Desktop/Valor/reflections.yaml` |
| `reflections/__init__.py` | Package: all callables return `{"status", "findings", "summary"}` |
| `reflections/utils.py` | Shared helpers: `load_local_projects()`, `is_ignored()`, `run_llm_reflection()` |
| `reflections/maintenance.py` | 6 maintenance callables |
| `reflections/auditing.py` | 6 auditing callables + PR review audit helpers |
| `reflections/task_management.py` | 2 task management callables |
| `reflections/session_intelligence.py` | Session analysis → LLM reflection → bug issue pipeline |
| `reflections/behavioral_learning.py` | Episode cycle-close → pattern crystallization pipeline |
| `reflections/daily_report.py` | Daily report → GitHub issues → Telegram pipeline |
| `reflections/memory_management.py` | 3 memory management callables |
| `models/reflection.py` | Reflection state model (per-reflection Redis tracking) |
| `models/reflection_ignore.py` | ReflectionIgnore: auto-fix suppression with TTL-based expiry |
| `models/pr_review_audit.py` | PRReviewAudit: PR review finding deduplication |
| `models/reflections.py` | Re-export shim: `ReflectionIgnore`, `PRReviewAudit` |
| `scripts/reflections_report.py` | GitHub issue creation module (used by daily_report) |
| `scripts/update/env_sync.py` | `sync_reflections_yaml()`: creates vault symlink on update |
| `~/Desktop/Valor/projects.json` | Multi-repo project registry |
| `~/Desktop/Valor/reflections.yaml` | Vault copy of the registry (canonical source) |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All reflections | Yes — state persistence |
| PyYAML | Registry loader | Yes — reads `config/reflections.yaml` |
| psutil | Memory instrumentation | Optional — memory snapshots degrade gracefully if missing |
| `ANTHROPIC_API_KEY` | `documentation-audit`, `session-intelligence` | Conditional — LLM reflection and docs audit |
| `gh` CLI (authenticated) | `task-backlog-check`, `session-intelligence`, `daily-report-and-notify`, `merged-branch-cleanup`, `pr-review-audit` | Conditional |
| `telethon` | `daily-report-and-notify` | Conditional — Telegram notifications |
| `~/Desktop/Valor/projects.json` | Multi-repo reflections | Optional — defaults to AI repo only |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| No GitHub issue created | No findings, or `gh auth status` failed | Check `tail -20 logs/worker.log` |
| LLM reflection skipped | `ANTHROPIC_API_KEY` not set | Add to `.env` |
| Telegram post failed | Missing `data/valor.session` | Run `python scripts/telegram_login.py` |
| Reflection stuck/timing out | Subprocess hung | Check for timeout; review `logs/worker.log` |
| Worker heartbeat stops / event loop frozen | Reflection called `subprocess.run()` from inside `async def` | All reflection callables must use `await asyncio.to_thread(subprocess.run, ...)` or `asyncio.run_in_executor()` — blocking subprocess calls in async functions freeze the event loop and prevent heartbeat writes |
| Memory decay prune inactive | `MEMORY_DECAY_PRUNE_APPLY` not set | Set `MEMORY_DECAY_PRUNE_APPLY=true` in `.env` after reviewing dry-run logs |
| High memory delta warning | Reflection consumed >100MB | Check `worker.log` for `HIGH MEMORY DELTA`; investigate flagged reflection |
| Config not found | Vault not synced yet | Run `scripts/remote-update.sh` or set `REFLECTIONS_YAML` env var |

## See Also

- [Documentation Audit](documentation-audit.md) — `documentation-audit` unit deep dive
- [Hooks Best Practices & Audit](hooks-best-practices.md) — `hooks-audit` unit deep dive
- [Skills Audit](do-skills-audit.md) — `skills-audit` unit deep dive
- [Subconscious Memory](subconscious-memory.md#memory-consolidation) — `memory-dedup` consolidation
