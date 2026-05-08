# Reflections: Autonomous Maintenance System

The reflections system is the single unified framework for all recurring non-issue work. A scheduler (`agent/reflection_scheduler.py`) reads a declarative registry (`config/reflections.yaml`), tracks state in Redis via the `Reflection` model, and executes reflections on schedule. Ad-hoc and one-shot reflections can also be created via MCP tools or the `agent_session_scheduler --after` path.

## Unified Reflection Scheduler

All recurring tasks are declared in `config/reflections.yaml` and managed by a single scheduler that runs as an asyncio task inside the standalone worker process (`python -m worker`).

### Architecture

```
Worker startup (worker/__main__.py)
  -> ReflectionScheduler.start()
    -> Tick every 60 seconds
      -> For each reflection (registry + ad-hoc Redis records):
        -> Compute next_due via compute_next_due(schedule, ran_at)
        -> Check skip-if-running / paused_until guards
        -> Execute: function (direct callable) or agent (PM session)
        -> Update state in Redis (Reflection model)
        -> Write ReflectionRun history row
```

### Schedule Grammar

Every reflection carries a `schedule` field using the fazm-style triplet:

| Prefix | Format | Example | Behaviour |
|--------|--------|---------|-----------|
| `cron:` | Standard 5-field cron | `cron:0 6 * * *` | Fires at cron times in `cron_tz` (default UTC) |
| `every:` | `<N><s\|m\|h\|d>` | `every:5m` | Fires `N` seconds/minutes/hours/days after last run |
| `at:` | ISO 8601 datetime | `at:2026-05-09T08:00:00Z` | One-shot; fires once, then `auto_delete_after_run=True` self-deletes on success |

`compute_next_due(schedule, last_run, cron_tz="UTC")` in `agent/reflection_scheduler.py` is the canonical parser. It raises `ValueError` on unknown or malformed schedules (including the retired `interval:` prefix).

### Registry Format (`config/reflections.yaml`)

```yaml
reflections:
  - name: session-liveness-check
    description: "Check running sessions for liveness and timeout, recover stuck ones"
    schedule: every:5m
    priority: high
    execution_type: function
    callable: "agent.agent_session_queue._agent_session_health_check"
    enabled: true
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier (Redis key) |
| `schedule` | string | `cron:`, `every:`, or `at:` expression |
| `priority` | string | `urgent`, `high`, `normal`, or `low` |
| `execution_type` | string | `function` (direct callable) or `agent` (PM session) |
| `callable` | string | Dotted Python path (function type) |
| `command` | string | Natural-language prompt for PM session (agent type) |
| `enabled` | bool | Whether active (default: true) |
| `timeout` | int | Per-reflection timeout in seconds. Defaults: 1800 for function, 3600 for agent |
| `cron_tz` | string | IANA timezone for `cron:` schedules (default UTC) |

### Registry Location (Vault-First)

The scheduler resolves `config/reflections.yaml` via a three-level fallback:

1. `REFLECTIONS_YAML` env var (explicit override, e.g., for testing)
2. `~/Desktop/Valor/reflections.yaml` (vault copy, iCloud-synced, skipped under launchd to avoid TCC hangs)
3. `config/reflections.yaml` in-repo (symlink to vault on live machines)

The symlink is created by `sync_reflections_yaml()` in `scripts/update/env_sync.py` during each update run.

### Registered Reflections

**Infrastructure / health:**

| Name | Schedule | Priority | Type | Description |
|------|----------|----------|------|-------------|
| `session-liveness-check` | every:5m | high | function | Check running sessions for liveness and timeout, recover stuck ones |
| `agent-session-cleanup` | every:1h | normal | function | Delete corrupted AgentSession records, repair orphan `$IndexF` members, AND reap cross-process orphan `claude`/MCP processes (see [bridge self-healing](bridge-self-healing.md#7-agent-session-cleanup-agentsession_healthpy)) |
| `stale-branch-cleanup` | every:1d | low | function | Clean up session branches older than 72 hours (disabled) |
| `redis-index-cleanup` | every:1d | low | function | Rebuild Redis model indexes to remove orphaned entries |
| `circuit-health-gate` | every:1m | high | function | Check Anthropic circuit state; manage `queue_paused` and `worker:hibernating` flags atomically |
| `session-count-throttle` | every:1h | normal | function | Count sessions in last hour; write throttle level |
| `failure-loop-detector` | every:1h | normal | function | Scan failed sessions; file one GitHub issue per novel error cluster |
| `session-recovery-drip` | every:30s | high | function | Drip one paused_circuit or paused session back to pending per tick |
| `system-health-digest` | every:1d | low | agent | Daily Telegram health summary **(disabled)** |
| `memory-dedup` | every:1d | normal | function | LLM-based semantic memory consolidation (dry-run default) |
| `sentry-issue-triage` | every:1d | low | agent | Triage unresolved Sentry issues across projects (disabled) |

**Maintenance:**

| Name | Callable | Description |
|------|----------|-------------|
| `tech-debt-scan` | `reflections.maintenance.run_legacy_code_scan` | Scan for TODO comments and `deprecated` typing imports |
| `redis-ttl-cleanup` | `reflections.maintenance.run_redis_ttl_cleanup` | Prune expired records across all Redis models |
| `redis-quality-audit` | `reflections.maintenance.run_redis_data_quality` | Audit data quality: unsummarized links, dead channels, error patterns |
| `merged-branch-cleanup` | `reflections.maintenance.run_branch_plan_cleanup` | Delete merged branches; audit docs/plans/ for stale/orphaned plans **(disabled)** |
| `disk-space-check` | `reflections.maintenance.run_disk_space_check` | Check free disk space; warn if below 10 GB |
| `analytics-rollup` | `reflections.maintenance.run_analytics_rollup` | Aggregate daily analytics; purge old records |

**Auditing:**

| Name | Callable | Description |
|------|----------|-------------|
| `docs-auditor` | `reflections.docs_auditor.run_docs_auditor` | Unified docs auditor: rotates least-recently-audited primary doc, applies auto-fixes, opens `docs-audit/*` PR (see [Docs Auditor](docs-auditor.md)) |
| `do-docs-branch-sweeper` | `reflections.docs_auditor.run_docs_branch_sweeper` | Delete stale `docs-audit/*` branches >7d with no PR; close open `docs-audit/*` PRs >14d |
| `skills-audit` | `reflections.auditing.run_skills_audit` | Validate all SKILL.md files (see [Skills Audit](do-skills-audit.md)) |
| `hooks-audit` | `reflections.auditing.run_hooks_audit` | Audit Claude Code hooks and settings (see [Hooks Best Practices](hooks-best-practices.md)) |
| `pr-review-audit` | `reflections.auditing.run_pr_review_audit` | Scan merged PRs for unaddressed review findings; file GitHub issues **(disabled)** |

**Task management:**

| Name | Callable | Description |
|------|----------|-------------|
| `task-backlog-check` | `reflections.task_management.run_task_management` | Check open bug issues per project and local TODO files **(disabled)** |
| `principal-staleness` | `reflections.task_management.run_principal_staleness` | Check if config/PRINCIPAL.md is stale (>90 days) |

**Pipelines:**

| Name | Callable | Description |
|------|----------|-------------|
| `session-intelligence` | `reflections.session_intelligence.run` | Session Analysis → LLM Reflection → Bug Issue Filing **(disabled)** |
| `behavioral-learning` | `reflections.behavioral_learning.run` | Episode Cycle-Close → Pattern Crystallization |
| `pm-briefings` | `reflections.pm_audio_briefing.run` | Slot-driven PM briefings dispatcher. Each project declares slots (`morning`, `daily_log`, `log_audit`) in `projects.json`. See [pm-briefings.md](pm-briefings.md). |

**Memory management:**

| Name | Callable | Description |
|------|----------|-------------|
| `memory-decay-prune` | `reflections.memory_management.run_memory_decay_prune` | Delete below-threshold memories with zero access (dry-run default) |
| `memory-quality-audit` | `reflections.memory_management.run_memory_quality_audit` | 4-layer audit: baseline quality flags + deterministic supersede + heuristic anomaly detection + Gemma classification fail-soft |
| `embedding-orphan-sweep` | `reflections.memory_management.run_embedding_orphan_sweep` | Reconcile Memory `.npy` embedding files against live records via Popoto `garbage_collect` + `sweep_stale_tempfiles` (dry-run default) |

### Daily PM-facing slots (consolidated)

The `pm-briefings` dispatcher runs slot types per project. The two most important slots:

| Slot type | Surface scanned | Output channel | Consumer |
|-----------|-----------------|----------------|----------|
| `log_audit` | Server logs (`logs/bridge.log`, etc.) per project | Telegram text summary | Engineer triage of error-rate spikes |
| `daily_log` | System activity (commits, PRs, issues, sessions, Telegram decisions, memories, crashes, reflection runs) | Markdown day log written to `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` plus a ~70-word audio brief to PM Telegram | Knowledge search + spoken executive update |

Both slots' helpers live in `reflections/pm_audio_briefing/{daily_log,log_audit}.py`. See [pm-briefings.md](pm-briefings.md) for full design.

## State Model (`models/reflection.py`)

Each reflection (registry-declared or ad-hoc) has one `Reflection` record in Redis:

| Field | Type | Purpose |
|-------|------|---------|
| `name` | KeyField | Unique identifier |
| `schedule` | Field | Schedule string (`cron:`, `every:`, `at:`) |
| `execution_type` | Field | `function` or `agent` |
| `command` | Field | Prompt/CLI for agent-type reflections |
| `output_sink` | Field | Delivery target (see Output Sinks) |
| `ran_at` | Field(float) | Unix timestamp of last execution start |
| `run_count` | IntField | Total number of executions |
| `last_status` | Field | `pending`, `running`, `success`, `error`, `skipped`, `stale_running` |
| `last_error` | Field | Error message from last failure (capped at 1000 chars) |
| `last_duration` | Field(float) | Duration of last run in seconds |
| `last_run_summary` | Field(dict) | `{ran_at, status, duration, error}` compact snapshot for fast dashboard reads |
| `failure_count_consecutive` | IntField | Consecutive failure count (resets on success) |
| `retry_policy` | Field(dict) | `{max_retries, backoff_seconds, max_consecutive_failures_before_pause}` |
| `paused_until` | Field(float) | Unix timestamp; scheduler skips until this time |
| `dead_letter_escalated` | Field(bool) | True once the dead-letter Memory record has been written for this failure run |
| `cost_usd_total` | Field(float) | Rolling total cost in USD |
| `tokens_input_total` | IntField | Rolling total input tokens |
| `tokens_output_total` | IntField | Rolling total output tokens |
| `created_by_session_id` | Field | Session ID that created this record (None for registry-loaded) |
| `auto_delete_after_run` | Field(bool) | If True, record self-deletes after a successful run (always True for `at:` schedules) |

`next_due` is not stored. It is computed as `compute_next_due(schedule, ran_at)` in the scheduler and dashboard layer.

## ReflectionRun (per-run history)

Each completed run writes one `ReflectionRun` row (30-day TTL via `Meta.ttl`). Run history is no longer embedded in the `Reflection` record.

| Field | Type | Notes |
|-------|------|-------|
| `run_id` | AutoKeyField | UUID |
| `name` | KeyField | FK to Reflection.name |
| `timestamp` | Field(float) | Unix epoch when run completed |
| `status` | Field | `success`, `error`, `stale_running` |
| `duration_ms` | IntField | Wall-clock milliseconds |
| `cost_usd` | Field(float) | USD cost for this run |
| `tokens_input` | IntField | Input tokens |
| `tokens_output` | IntField | Output tokens |
| `error` | Field | Error message (capped at 1000 chars) or None |
| `output_summary` | Field | First 1000 chars of run output, or None |
| `delivery_error` | Field | Error from output sink delivery (e.g., Telegram send failure) |
| `projects` | ListField | Per-project breakdown for audit reflections (empty for others) |

Query: `ReflectionRun.query.filter(name=<reflection_name>)`. Convenience: `ReflectionRun.recent_for(name, limit=50)` returns newest-first rows.

## Output Sinks

Every reflection declares an `output_sink` field that controls where its result is delivered. The default is `log_only`.

| Sink | Format | Behaviour |
|------|--------|-----------|
| `log_only` | literal | Result is logged to `worker.log` only (default) |
| `dashboard_only` | literal | Result is visible on the dashboard; no Telegram delivery |
| `memory:<importance>` | e.g. `memory:6.0` | Result summary is saved as a Memory record at the given importance float |
| `telegram:<chat>` | e.g. `telegram:Dev: Valor` | Result is delivered as a Telegram message to the resolved chat |

**Telegram resolution chain** for `telegram:<chat>`:

1. If `<chat>` is a literal integer (e.g. `telegram:-1001234567`), it is used directly as the chat ID.
2. Otherwise, the name is matched against `projects.json` group names (exact match).
3. Then against the DM whitelist by display name.

Delivery failures are surfaced in `ReflectionRun.delivery_error` for that run and logged at WARNING level. They do not count as reflection failures (i.e., `failure_count_consecutive` is not incremented for a delivery error).

## Failure Tracking

The `Reflection` model tracks consecutive failures and auto-pauses misbehaving reflections.

| Field | Default | Meaning |
|-------|---------|---------|
| `failure_count_consecutive` | 0 | Incremented on each error run; reset to 0 on first success |
| `retry_policy` | `{}` | Dict with optional `max_retries`, `backoff_seconds`, `max_consecutive_failures_before_pause` |
| `paused_until` | 0.0 | Unix timestamp; scheduler skips the reflection until `now > paused_until` |
| `dead_letter_escalated` | False | Prevents the dead-letter Memory from firing more than once per failure streak |

**Auto-pause threshold**: when `failure_count_consecutive` reaches 5 (`_DEAD_LETTER_THRESHOLD`), `mark_completed()`:

1. Writes a Memory record (`importance=7.0`, `category="correction"`) describing the failure streak.
2. Sets `dead_letter_escalated=True` so the Memory is written only on the `<5 -> >=5` transition.
3. Sets `paused_until = now + 86400` (24 hours).

Every subsequent failure while `failure_count_consecutive >= 5` extends `paused_until` by another 24 hours.

**Resuming**: call `reflections_resume` (MCP tool) to clear `paused_until`, `failure_count_consecutive`, and `dead_letter_escalated` so the scheduler picks up the reflection on the next tick.

## MCP Surface

Eight tools are available via the `reflections` FastMCP server (`mcp_servers/reflections_server.py`):

| Tool | Description |
|------|-------------|
| `reflections_create` | Create a new Reflection. Validates schedule via `compute_next_due`. `at:` schedules force `auto_delete_after_run=True`. |
| `reflections_list` | List all Reflections, optionally filtered by `group` and `status`. |
| `reflections_get` | Return full record for a named Reflection. |
| `reflections_update` | Update mutable fields (`schedule`, `execution_type`, `command`, `output_sink`, `retry_policy`, `auto_delete_after_run`). Re-validates schedule on change. |
| `reflections_remove` | Delete a Reflection. Caller must pass `_can_remove`. |
| `reflections_runs` | Return recent `ReflectionRun` history rows for a reflection (newest first, capped at 200). |
| `reflections_pause` | Set `paused_until` to a given ISO 8601 datetime (or ~1 year if omitted). |
| `reflections_resume` | Clear `paused_until`, `failure_count_consecutive`, `dead_letter_escalated`. |

**Auth model**: `_caller_id()` reads `AGENT_SESSION_ID` or `VALOR_SESSION_ID` from env.

- Agent callers may only update or remove reflections they created (`created_by_session_id` match).
- Unidentified (CLI) callers may update any reflection. To remove, they must also set `REFLECTIONS_REGISTRY_SOURCE=1` (only the registry sync path should delete reflections out-of-band).

Each tool returns `{"error": str, "code": str}` on failure rather than raising, so the agent sees a description rather than a protocol error.

## One-Shot Reflections

`at:<ISO8601>` schedules create fire-once reflections:

- `auto_delete_after_run` is forced True by both `reflections_create` and `_create_after_reflection`.
- A successful run self-deletes the Reflection record.
- A failed run leaves the record in place for diagnosis (the run's error is in `ReflectionRun.error`).

`tools/agent_session_scheduler.py --after <ISO>` writes a one-shot `at:` Reflection record (via `_create_after_reflection`) instead of a deferred `AgentSession`. The unified scheduler drives execution.

## Stale-Running Reaper

`reap_stale_running()` runs once at worker startup, before the scheduler tick loop begins. It sweeps all `Reflection` records where `last_status == "running"` and where the elapsed time since `ran_at` exceeds `max(2 * interval_seconds, last_duration_or_1800)`. Stale records are transitioned to `error` status so the next tick can re-execute them.

## Skip-if-Running Guard

Before executing a reflection, the scheduler checks whether it is already running. If `last_status == "running"` and elapsed time is within the stale threshold, the reflection is skipped. This prevents concurrent execution of the same reflection.

## Observability

- `ReflectionScheduler.format_status()` prints each reflection's state, time until next run, last duration, and run count.
- `/queue-status` Telegram skill surfaces reflection status.
- The dashboard at `localhost:8500/reflections/` provides full run history and live status.
- `curl -s localhost:8500/dashboard.json` includes the reflections snapshot.

## Resource Guards

**Memory instrumentation**: `psutil.Process().memory_info().rss` is captured before and after each reflection. Deltas above 100MB emit a WARNING. Memory monitoring is best-effort; reflections run even if `psutil` is unavailable.

**Timeout enforcement**: Each reflection has a configurable timeout (via `timeout` field in YAML, or type-based defaults: 30 min for function, 60 min for agent). Function-type reflections use `asyncio.wait_for()`. For sync callables running via `run_in_executor()`, timeout raises but the thread cannot be cancelled (detection-only). Timeouts are logged and the reflection is marked with error status.

**Critical constraint**: Reflection callables run inside the asyncio event loop. Any `subprocess.run()` or other blocking I/O must be wrapped with `await asyncio.to_thread(subprocess.run, ...)` or `loop.run_in_executor()`. A bare `subprocess.run()` inside an `async def` blocks the event loop, freezing the scheduler and preventing worker heartbeat writes.

## Log Rotation

| Log File | Writer | Rotation Mechanism | Max Size | Backups |
|----------|--------|--------------------|----------|---------|
| `bridge.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `bridge/telegram_bridge.py` | 10MB | 5 |
| `watchdog.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `monitoring/bridge_watchdog.py` | 10MB | 5 |
| `worker.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `worker/__main__.py` | 10MB | 5 |
| `bridge.error.log` | launchd (StandardErrorPath) | Shell `rotate_log` in `valor-service.sh` | 10MB | 3 |

A user-space LaunchAgent (`com.valor.log-rotate`) runs `scripts/log_rotate.py` every 30 minutes. See [Log Rotation](log-rotation.md) for the full three-layer design.

## Bridge Watchdog (External)

The bridge watchdog (`monitoring/bridge_watchdog.py`, launchd service `com.valor.bridge-watchdog`) is intentionally NOT in the reflection registry. It must run as an external launchd service because it monitors the bridge process itself.

## Harness Skills vs. Reflections

`/loop` and `/schedule` harness skills are NOT replaced by the reflections system. They provide a different surface for ad-hoc session scheduling. The reflections MCP tools (`reflections_create`, etc.) are first-party and shadow them for agent-initiated recurring work.

## reflections/ Package

All daily maintenance work is implemented as standalone async callables in the `reflections/` package. Each returns:

```python
{"status": "ok" | "error", "findings": [...], "summary": str}
```

| Module | Description |
|--------|-------------|
| `reflections.utils` | Shared helpers: `load_local_projects()`, `is_ignored()`, `load_ignore_entries()`, `has_existing_github_work()`, `run_llm_reflection()` |
| `reflections.maintenance` | 6 maintenance callables (TTL cleanup, data quality, branch/plan cleanup, etc.) |
| `reflections.auditing` | Auditing callables (docs audit, skills audit, hooks audit, PR review audit, branch sweeper) |
| `reflections.task_management` | 2 task management callables (task check, principal staleness) |
| `reflections.session_intelligence` | Pipeline: session analysis → LLM reflection → bug issue filing |
| `reflections.behavioral_learning` | Pipeline: episode cycle-close → pattern crystallization |
| `reflections.pm_audio_briefing` | Slot-driven dispatcher (`pm-briefings` registry entry): `morning`, `daily_log`, `log_audit` per (project × slot) |
| `reflections.memory_management` | 3 memory management callables (decay prune, quality audit, embedding orphan sweep) |

## State & Persistence

### ReflectionIgnore

Suppresses auto-fix for specific patterns. Each entry has a TTL (default 14 days).

| Field | Type | Purpose |
|-------|------|---------|
| `ignore_id` | AutoKeyField | UUID |
| `pattern` | KeyField | Pattern string to match against reflections |
| `reason` | Field | Why this pattern is ignored |
| `created_at` | SortedField(float) | When the entry was created |
| `expires_at` | SortedField(float) | When it expires (created_at + 14 days) |

**Matching**: Case-insensitive substring match.
**Cleanup**: Expired entries are pruned during Redis TTL cleanup (`redis-ttl-cleanup`).

### PRReviewAudit

Deduplication tracker for PR review audit findings.

| Field | Type | Purpose |
|-------|------|---------|
| `audit_id` | AutoKeyField | UUID |
| `repo` | KeyField | GitHub repo slug |
| `pr_number` | IntField | Pull request number |
| `comment_id` | UniqueKeyField | Composite dedup key: `{repo}:{pr_number}:{comment_id}:{finding_index}` |
| `severity` | Field | `critical`, `standard`, or `trivial` |
| `filed_issue_url` | Field(null) | URL of the filed GitHub issue, if any |
| `audited_at` | SortedField(float) | Timestamp when audited |

**Cleanup**: Records older than 90 days are pruned via `cleanup_expired()` during Redis TTL cleanup.

### docs-auditor State

The `docs-auditor` substrate tracks per-file rotation state in a Redis hash:

```python
redis.Redis.from_url(settings.REDIS_URL).hgetall("docs_audit:last_run")
```

Each field is a doc path; each value is the unix timestamp of its most recent audit. See [Docs Auditor](docs-auditor.md) for the substrate design.

### Docs Auditor Authentication

| Component | Credential |
|-----------|-----------|
| AgentSessions (Claude Code via `claude -p`) | `CLAUDE_CODE_OAUTH_TOKEN` |
| `docs-auditor` substrate (`reflections/docs_auditor.py`) | `ANTHROPIC_API_KEY` |

When `ANTHROPIC_API_KEY` is absent or invalid, the substrate logs a single WARNING and returns `{"status": "disabled", ...}`. To enable, add `ANTHROPIC_API_KEY` to `~/Desktop/Valor/.env`.

## Session Analysis (part of `session_intelligence` pipeline)

### Thrash Ratio

```
failure_ratio = max(0.0, 1.0 - (turn_count / tool_call_count))
```

Sessions above `THRASH_RATIO_THRESHOLD = 0.5` are flagged for LLM reflection. The runner caps analysis at the 20 most interesting sessions.

### LLM Reflection

Flagged sessions are sent to Claude Haiku for categorization: `misunderstanding`, `code_bug`, `poor_planning`, `tool_misuse`, `scope_creep`, `integration_failure`.

### File Bug Issues

An issue is filed when a `code_bug` reflection meets 2 of 3 criteria: category matches, `prevention` is non-empty, `pattern` is at least 10 characters.

## Multi-Repo Support

`load_local_projects()` reads `~/Desktop/Valor/projects.json`, filters to repos present on the current machine, and runs per-project analysis.

### Per-Project Audit Iteration

Three audit reflections (`tech-debt-scan`, `skills-audit`, `hooks-audit`) run once per project, aggregating findings via `reflections.utils.run_per_project_audit()`:

1. Loads `load_local_projects()`
2. Evaluates `skip_if(repo_root)` per project
3. Calls `audit_one(project)` for qualifying projects
4. Returns `{status, findings, summary, projects: [...]}`

## Redis TTL Cleanup (`redis-ttl-cleanup`)

| Model | Max Age | Method |
|-------|---------|--------|
| TelegramMessage | 90 days | `cleanup_expired()` |
| Link | 90 days | `cleanup_expired()` |
| Chat | 90 days | `cleanup_expired()` |
| AgentSession | 90 days | `cleanup_expired()` |
| BridgeEvent | 7 days | `cleanup_old()` |
| ReflectionIgnore | Per-entry TTL | `cleanup_expired()` |
| PRReviewAudit | 90 days | `cleanup_expired()` |
| ReflectionRun | 30 days | `Meta.ttl` (Popoto) + `cleanup_older_than()` |

## Memory Management Reflections

### `memory-decay-prune`

- `WF_MIN_THRESHOLD = 0.15`
- `PRUNE_AGE_DAYS = 30`
- `IMPORTANCE_EXEMPT_THRESHOLD = 7.0`
- `MAX_PRUNE_PER_RUN = 50`
- Dry-run default: set `MEMORY_DECAY_PRUNE_APPLY=true` to enable deletion

### `memory-quality-audit`

4-layer always-apply audit:
- **Layer 0**: Baseline zero-access + low-confidence flags. Read-only.
- **Layer 1**: Deterministic supersede of refusal/JSON-shrapnel extraction records (capped at `MAX_LAYER1_SUPERSEDES_PER_RUN=50`).
- **Layer 2**: Heuristic anomaly detection (four signals: `category-default-skew`, `importance-1.0-skew`, `agent-id-cluster`, `html-escape-rate`).
- **Layer 3**: Gemma classification (`gemma4:e2b`, fail-soft). Samples up to 20 last-24h records.
- Issue surfacing: Layer-2/3 candidates file `gh issue create --label memory --label investigation`, deduped by title-prefix search.

### `embedding-orphan-sweep`

Reconciles on-disk Memory embedding store against live records via Popoto's `EmbeddingField.garbage_collect(Memory)` and `EmbeddingField.sweep_stale_tempfiles(Memory)`.

- Dry-run default: set `EMBEDDING_ORPHAN_SWEEP_APPLY=true` to enable deletion.
- Requires popoto >= 1.6.0 (probe via `hasattr(EmbeddingField, "sweep_stale_tempfiles")`).

## Operations

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
| `agent/reflection_scheduler.py` | Unified scheduler: registry loader, `compute_next_due`, executor, stale-running reaper |
| `config/reflections.yaml` | Declarative registry symlink → `~/Desktop/Valor/reflections.yaml` |
| `models/reflection.py` | Reflection state model (per-reflection Redis tracking) |
| `models/reflection_run.py` | ReflectionRun per-run history rows (30-day TTL) |
| `mcp_servers/reflections_server.py` | FastMCP server: 8 reflection management tools |
| `reflections/__init__.py` | Package: all callables return `{"status", "findings", "summary"}` |
| `reflections/utils.py` | Shared helpers |
| `reflections/maintenance.py` | 6 maintenance callables |
| `reflections/auditing.py` | Auditing callables |
| `reflections/task_management.py` | 2 task management callables |
| `reflections/session_intelligence.py` | Session analysis → LLM reflection → bug issue pipeline |
| `reflections/behavioral_learning.py` | Episode cycle-close → pattern crystallization pipeline |
| `reflections/pm_audio_briefing/` | Slot-driven `pm-briefings` dispatcher |
| `reflections/memory_management.py` | 3 memory management callables |
| `models/reflection_ignore.py` | ReflectionIgnore: auto-fix suppression with TTL-based expiry |
| `models/pr_review_audit.py` | PRReviewAudit: PR review finding deduplication |
| `models/reflections.py` | Re-export shim: `ReflectionIgnore`, `PRReviewAudit` |
| `tools/agent_session_scheduler.py` | CLI scheduler tool; `--after <ISO>` path writes a one-shot `at:` Reflection |
| `scripts/update/env_sync.py` | `sync_reflections_yaml()`: creates vault symlink on update |
| `~/Desktop/Valor/projects.json` | Multi-repo project registry |
| `~/Desktop/Valor/reflections.yaml` | Vault copy of the registry (canonical source) |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All reflections | Yes |
| PyYAML | Registry loader | Yes |
| croniter | `cron:` schedule parsing | Yes (for cron schedules) |
| psutil | Memory instrumentation | Optional |
| `ANTHROPIC_API_KEY` | `docs-auditor`, `session-intelligence` | Conditional |
| `gh` CLI (authenticated) | `task-backlog-check`, `session-intelligence`, `pm-briefings`, `merged-branch-cleanup`, `pr-review-audit` | Conditional |
| `tools.tts` | `pm-briefings` (morning, daily_log slots) | Conditional |
| Redis outbox + bridge relay | `pm-briefings` | Yes |
| `~/Desktop/Valor/projects.json` | Multi-repo reflections | Optional |

## Troubleshooting

| Symptom | Diagnosis | Fix |
|---------|-----------|-----|
| No GitHub issue created | No findings, or `gh auth status` failed | Check `tail -20 logs/worker.log` |
| LLM reflection skipped | `ANTHROPIC_API_KEY` not set | Add to `.env` |
| Telegram post failed | Missing `data/valor.session` | Run `python scripts/telegram_login.py` |
| Reflection stuck/timing out | Subprocess hung | Check timeout; review `logs/worker.log` |
| Worker heartbeat stops | Reflection called `subprocess.run()` in `async def` | Wrap with `asyncio.to_thread` or `run_in_executor` |
| Memory decay prune inactive | `MEMORY_DECAY_PRUNE_APPLY` not set | Set `MEMORY_DECAY_PRUNE_APPLY=true` after reviewing dry-run logs |
| High memory delta warning | Reflection consumed >100MB | Check `worker.log` for `HIGH MEMORY DELTA` |
| Config not found | Vault not synced yet | Run `scripts/remote-update.sh` or set `REFLECTIONS_YAML` env var |
| Reflection auto-paused | 5+ consecutive failures | Resume via `reflections_resume` MCP tool |
| `at:` reflection not firing | Target time in the past when created | Create a new one-shot with a future timestamp |

## See Also

- [Docs Auditor](docs-auditor.md) — unified `docs-auditor` substrate
- [Hooks Best Practices & Audit](hooks-best-practices.md) — `hooks-audit` deep dive
- [Skills Audit](do-skills-audit.md) — `skills-audit` deep dive
- [Subconscious Memory](subconscious-memory.md#memory-consolidation) — `memory-dedup` consolidation
- [PM Briefings](pm-briefings.md) — `pm-briefings` slot dispatcher
