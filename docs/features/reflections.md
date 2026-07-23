# Reflections: Autonomous Maintenance System

> **Single source of truth (post #1273 / #1342):** This document defines the unified Reflection schema, schedule grammar, output sinks, failure tracking, and migration path. Sibling docs (`agent-session-scheduling.md`, `reflections-dashboard.md`, `pm-briefings.md`, the `README.md` index) defer to this page for the canonical model and grammar.

The reflections system is a unified framework for all recurring non-issue work. A single lightweight scheduler (`agent/reflection_scheduler.py`) reads from a declarative registry (`config/reflections.yaml`), tracks state in Redis (`Reflection` + `ReflectionRun` Popoto models), and executes reflections on schedule. This replaces the previously scattered scheduling mechanisms (launchd plists, asyncio loops, startup hooks, ad-hoc `--after`-style one-shots).

`tools/agent_session_scheduler.py --after <ISO>` enqueues a `at:`-grammar Reflection alongside its primary AgentSession write so scheduled work is visible on the dashboard, and the helper-skill `/loop` and `/schedule` are documented as the harness-side fallback for one-off self-pacing within a single conversation. Both surfaces are first-class but reach the same backing data.

## Unified Reflection Scheduler

All recurring tasks are declared in `config/reflections.yaml` and managed by a single scheduler that runs in its own supervised launchd subprocess (`python -m reflections`, `com.valor.reflection-worker`) — out-of-process from the worker for crash-domain isolation. See [Reflection Scheduler Subprocess](reflection-scheduler-subprocess.md).

### Architecture

```
Subprocess startup (reflections/__main__.py, `python -m reflections`)
  -> ReflectionScheduler.start()
    -> Tick every 60 seconds
      -> For each reflection in registry:
        -> Check if due (compute_next_due(schedule) <= now)
        -> Check skip-if-running guard
        -> Execute: function (direct callable) or agent (PM session)
        -> Update state in Redis (Reflection model)
```

### Registry Format (`config/reflections.yaml`)

```yaml
reflections:
  - name: session-liveness-check
    description: "Check running sessions for liveness and timeout, recover stuck ones"
    every: 300s          # unified schedule grammar (issue #1273)
    priority: high
    execution_type: function
    callable: "agent.agent_session_queue._agent_session_health_check"
    enabled: true
    output_sink: log_only
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier (used as Redis key) |
| `every` / `cron` / `at` | string | Unified schedule grammar — exactly one is required. See [Schedule Grammar](#schedule-grammar) below. |
| `priority` | string | `urgent`, `high`, `normal`, or `low` |
| `execution_type` | string | `function` (direct callable) or `agent` (PM session) |
| `callable` | string | Dotted Python path (for function type) |
| `command` | string | Natural-language prompt for PM session (for agent type) |
| `enabled` | bool | Whether this reflection is active (default: true) |
| `project_key` | string | Optional repo-specific gate. Only the machine that owns this project in `projects.json` runs the reflection. See [Repo-Specific Reflections](#repo-specific-reflections-single-machine-ownership). |
| `output_sink` | string | Where to deliver completion summaries: `log_only` (default), `dashboard_only`, `memory:<importance>`, or `telegram:<chat>`. See [Output Sinks](#output-sinks). |
| `auto_delete_after_run` | bool | One-shot reflections (`at:` schedule) — record self-cleans on success. Default: `false`. |
| `retry_policy` | dict | Optional override of `{max_retries, backoff_seconds, max_consecutive_failures_before_pause}`. See [Failure Tracking](#failure-tracking). |
| `timeout` | int | Optional per-reflection timeout in seconds. Defaults: 1800 (30 min) for function, 3600 (60 min) for agent |
| `params` | dict | Optional arbitrary kwargs forwarded to the callable when it declares a `params` keyword argument. The scheduler uses `inspect.signature` to detect whether the callable accepts `params`; if not, it is called without it. Use for feature flags and per-reflection tunables (e.g., `stall_advisory_telegram_enabled: false`). |

**Convention:** Reflections are addressed by `name` (this YAML field) and dispatched by `callable` (dotted path). Numbered-step references (`step_X`) are historical and should not be reintroduced into source, comments, or docs.

### Code-Registered Reflections

`config/reflections.yaml` is a **gitignored symlink** (`~/Desktop/Valor/reflections.yaml`, vault-synced) — a hand-edit to it, or even to a checked-out machine's local copy, never ships via git history and gets clobbered by the next vault→config sync. A reflection that must ship with a feature's code (rather than be registered by hand on each machine after the fact) instead registers itself through a small idempotent helper in `scripts/update/reflection_register.py`, invoked as a step inside `scripts/update/run.py`.

Each registration function (`register_crash_recovery`, `register_test_baseline_refresh`, `register_memory_distill_backfill`, ...) checks whether its entry is already present in the vault's `reflections.yaml`, appends a YAML block matching the file's existing indentation if not, and reports one of `registered` / `noop` / `skipped` via a `RegisterResult`. `scripts/update/run.py` runs every registration step **before** the step that copies the vault file into the per-machine `config/reflections.yaml`, so a freshly-registered entry propagates to the local config on the same `/update` cycle rather than requiring a second run. The reflection scheduler subprocess (`com.valor.reflection-worker`) picks up the change on its next config reload.

This is the mechanism behind three current registrations:

- `crash-recovery` (issue #1917)
- `test-baseline-refresh` (issues #1933/#2004)
- `memory-distill-backfill` (issue #2202 — see [Memory management](#reflection-callables) below and [Subconscious Memory](subconscious-memory.md#distilled-human-ingest-phase-3))

A reflection that an operator is expected to add by hand on a per-machine basis (most of the registry) still just lives in the YAML directly — this mechanism is reserved for reflections that must be live on every machine as a consequence of merging code, with no separate manual step.

### Schedule Grammar

The unified Reflection schema (issue #1273) collapses the prior `interval:` integer-seconds field into one of three string-typed schedule keys. Exactly one must be present per reflection.

| Key | Shape | Example | Semantics |
|-----|-------|---------|-----------|
| `every` | duration string | `every: 300s`, `every: 5m`, `every: 1h`, `every: 24h` | Recurring on a fixed interval. Tracked by `ran_at + interval`. |
| `cron` | five-field cron expression, optional `; tz=<zone>` suffix | `cron: 0 9 * * 1-5; tz=America/New_York` | Recurring on a calendar schedule. Timezone defaults to UTC; explicit zones must be valid IANA names. |
| `at` | ISO-8601 instant | `at: 2026-05-15T09:00:00+00:00` | One-shot — fires exactly once at the given instant. Pair with `auto_delete_after_run: true` so the record self-cleans on success. |

The runtime parser lives in `agent/reflection_schedule.py::compute_next_due()` and depends on `croniter` (declared in `pyproject.toml`).

#### Migration from `interval:` (issue #1273)

The `interval: <int>` field is now `every: <int>s`. The migration is one-shot and idempotent:

- `scripts/migrate_reflections_yaml.py` rewrites `interval: N` → `every: Ns` in place. Running it on an already-migrated YAML is a no-op.
- The `/update` skill invokes the migration on every pull (`scripts/update/run.py` Step 3.65) so machines that haven't migrated yet pick up the change automatically. The wrapper lives in `scripts/update/reflections_yaml.py`.
- The vault copy (`~/Desktop/Valor/reflections.yaml`) is the canonical target; the in-repo `config/reflections.yaml` symlinks to it on live machines.

### Registry Location (Vault-First)

The scheduler resolves `config/reflections.yaml` via a three-level fallback:

1. `REFLECTIONS_YAML` env var (explicit override, e.g., for testing)
2. `~/Desktop/Valor/reflections.yaml` (vault copy — iCloud-synced, takes precedence)
3. `config/reflections.yaml` in-repo (symlink to vault on live machines)

On live machines, `config/reflections.yaml` is a symlink to `~/Desktop/Valor/reflections.yaml`.
The symlink is created by `sync_reflections_yaml()` in `scripts/update/env_sync.py` during
each update run. This ensures the scheduler always reads the vault version.

Under launchd (`VALOR_LAUNCHD=1`), the worker instead reads a **real copy** at
`config/reflections.yaml` that `install_worker.sh` writes from the vault (macOS TCC blocks
launchd agents from reading `~/Desktop`). That copy step is where repo-specific ownership is
applied — see below.

### Repo-Specific Reflections (Single-Machine Ownership)

The registry is **one iCloud-synced file shared verbatim across every machine**. Reflections
that audit a single repo — chiefly the `audits` group, which file GitHub issues against
`tomcounsell/ai` — would therefore run on all N machines and each file its own copy of every
finding. This is what produced the recurring `documentation`-label duplicate flood (and the
same hazard applies to every other issue-filing audit).

The fix extends [single-machine ownership](single-machine-ownership.md) to reflections, and
applies it at **update time, not run time**:

1. A reflection declares `project_key: <key>` in the shared registry (e.g. `project_key: valor`).
2. `install_worker.sh`, right after copying the vault `reflections.yaml` into the launchd-safe
   `config/reflections.yaml`, runs `python -m tools.reflection_machine_filter`. For each entry
   with a `project_key`, if `projects.<key>.machine` (from `projects.json`) is **not** this
   machine, it forces `enabled: false` in the local copy.
3. The scheduler needs **zero runtime ownership logic** — it already skips `enabled: false`
   entries (`load_registry`), and it never reads `projects.json` on the launchd hot path (where
   TCC would hang on the iCloud copy).

Ownership semantics (`tools/reflection_machine_filter.py`):

| Condition | Result |
|-----------|--------|
| `project_key` unset | Unscoped — runs on every machine (unchanged) |
| owner machine == this machine | Left enabled (authored state preserved) |
| owner machine != this machine | Forced `enabled: false` in the local copy |
| `project_key` not in `projects.json` | Fail-open (left as-is) with a warning — a typo never silently disables an audit everywhere |

The filter only **disables**; it never re-enables (so an owned-but-authored-`enabled: false`
reflection like a paused `docs-auditor` stays off). It refuses to write through a symlink, so a
manual run against the symlinked `config/reflections.yaml` can never corrupt the shared vault —
it only ever rewrites the real per-machine copy that `install_worker.sh` produces.

> **Note on `docs-auditor` filing:** issue-filing is **rotation-only**. The `audit()` substrate
> files advisory issues (deleted-target, stub-doc) only under `scope_mode="rotation"` (Caller A,
> the daily reflection). The `/do-docs` SDLC stage (Caller B, `scope_mode="pr-changed-files"`)
> runs `audit()` on every PR but performs auto-fixes only — it does **not** file issues, since a
> deleted-target reference is unfixable and re-detecting it per-PR re-files duplicates. Combined
> with `project_key`-gating, repo audits file issues from exactly one machine, on rotation only.

### Registered Reflections

**Infrastructure / health:**

| Name | Interval | Priority | Type | Description |
|------|----------|----------|------|-------------|
| `session-liveness-check` | 5 min | high | function | Check running sessions for liveness and timeout, recover stuck ones |
| `agent-session-cleanup` | 1 hour | normal | function | Delete corrupted AgentSession records, run `AgentSession.repair_indexes()` unconditionally on every tick (issue #1361 — gate removed), emit per-status `agent_session.indexed_field.stale_members` drift metrics, AND reap cross-process orphan `claude`/MCP processes (phantom-filter guarded — see [bridge self-healing](bridge-self-healing.md#7-agent-session-cleanup-agentsession_healthpy) and [Cross-Process Orphan Reap (#1271)](bridge-self-healing.md#cross-process-orphan-reap-1271)) |
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
| `tech-debt-scan` | `reflections.audits.tech_debt_scan.run` | Scan for TODO comments and `deprecated` typing imports |
| `redis-ttl-cleanup` | `reflections.housekeeping.redis_ttl_cleanup.run` | Prune expired records across all Redis models |
| `redis-quality-audit` | `reflections.audits.redis_quality_audit.run` | Audit data quality: unsummarized links, dead channels, error patterns |
| `merged-branch-cleanup` | `reflections.housekeeping.merged_branch_cleanup.run` | Delete merged branches; audit docs/plans/ for stale/orphaned plans **(disabled — calls gh CLI)** |
| `disk-space-check` | `reflections.housekeeping.disk_space_check.run` | Check free disk space; warn if below 10 GB |
| `analytics-rollup` | `reflections.housekeeping.analytics_rollup.run` | Aggregate daily analytics; purge old records |

**Auditing:**

| Name | Callable | Description |
|------|----------|-------------|
| `docs-auditor` | `reflections.docs_auditor.run_docs_auditor` | Unified docs auditor: rotates least-recently-audited primary doc, applies auto-fixes, opens `docs-audit/*` PR (see [Docs Auditor](docs-auditor.md)) |
| `do-docs-branch-sweeper` | `reflections.docs_auditor.run_docs_branch_sweeper` | Delete stale `docs-audit/*` branches >7d with no PR; close open `docs-audit/*` PRs >14d |
| `skills-audit` | `reflections.audits.skills_audit.run` | Validate all SKILL.md files (see [Skills Audit](do-skills-audit.md)) |
| `hooks-audit` | `reflections.audits.hooks_audit.run` | Audit Claude Code hooks and settings (see [Hooks Best Practices](hooks-best-practices.md)) |
| `pr-review-audit` | `reflections.audits.pr_review_audit.run` | Scan merged PRs for unaddressed review findings; file GitHub issues **(disabled — calls gh CLI)** |

**Task management:**

| Name | Callable | Description |
|------|----------|-------------|
| `task-backlog-check` | `reflections.audits.task_backlog_check.run` | Check open bug issues per project and local TODO files **(disabled — calls gh CLI)** |
| `principal-staleness` | `reflections.audits.principal_staleness.run` | Check if config/PRINCIPAL.md is stale (>90 days) |

**Pipelines:**

| Name | Callable | Description |
|------|----------|-------------|
| `session-intelligence` | `reflections.session_intelligence.run` | Session Analysis → LLM Reflection → Bug Issue Filing **(disabled — calls gh CLI and spawns agent)** |
| `pm-briefings` | `reflections.pm_briefings.run` | Slot-driven PM briefings dispatcher. Each project declares slots (`morning`, `daily_log`, `log_audit`) in `projects.json`. See [pm-briefings.md](pm-briefings.md). |

**Memory management:**

| Name | Callable | Description |
|------|----------|-------------|
| `memory-decay-prune` | `reflections.memory.memory_decay_prune.run` | Delete below-threshold memories with zero access (dry-run default) |
| `memory-quality-audit` | `reflections.memory.memory_quality_audit.run` | 4-layer audit: baseline quality flags (Layer 0) + deterministic supersede of refusal/JSON-shrapnel (Layer 1) + heuristic anomaly detection (Layer 2) + Gemma classification fail-soft (Layer 3); files investigation issues for Layer-2/3 candidates |
| `embedding-orphan-sweep` | `reflections.memory.embedding_orphan_sweep.run` | Reconcile Memory `.npy` embedding files against live records via Popoto `garbage_collect` + `sweep_stale_tempfiles` (dry-run default; opt-in via `EMBEDDING_ORPHAN_SWEEP_APPLY=true`; requires popoto >= 1.6.0) |
| `memory-embedding-backfill` | `reflections.memory.memory_embedding_backfill.run` | Re-embed active Memory records saved without a vector (the `GracefulEmbeddingField` degradation marker, issue #1904) once the provider is healthy again; dry-run default, opt-in via `MEMORY_EMBEDDING_BACKFILL_APPLY=true`; caps at 500 re-embeds/run; partial-saves `["embedding"]` only so `relevance` decay is untouched |

### Daily PM-facing slots (consolidated)

The two daily reflections that used to run separately — `daily-log-review`
and `daily-report-and-notify` — are now slot types under the single
`pm-briefings` dispatcher (issue #1276 consolidation; issue #1292 cutover).
They answer different questions and ship via different output channels but
live in one code path.

| Slot type | Surface scanned | Output channel | Consumer |
|-----------|-----------------|----------------|----------|
| `log_audit` | Server logs (`logs/bridge.log`, etc.) per project | Telegram text summary to the slot's `target_groups` (previous default was `Dev: Valor`) | Engineer triage of error-rate spikes / regressions |
| `daily_log` | System activity (commits, PRs, issues, sessions, Telegram decisions, memories, crashes, reflection runs) | Markdown day log written to `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` (gated by per-slot `vault_writer: true`) plus a `~70-word` audio brief to the slot's first configured PM Telegram chat | Knowledge search ("what happened on day X?") + spoken executive update |

**Why both exist:** they answer different questions. `log_audit` answers
"is anything actively broken?" by scanning the trailing 24h of bridge/worker
logs. `daily_log` answers "what did the system actually do yesterday?" by
aggregating substantive events into a durable, searchable file on the
iCloud-synced work vault. The vault file is auto-indexed by
`tools/valor_ingest.py` so the agent's knowledge base picks it up without
extra wiring.

Both slots' helpers live in
`reflections/pm_briefings/{daily_log,log_audit}.py`.

**daily_log slot pipeline (per project, gated by slot config):**

1. `_collect_day_activity(target_date)` aggregates 7 sources concurrently with
   per-source 30s timeouts and graceful degradation (failures land as
   `[ERROR: source]` lines, the file still writes).
2. `_render_day_log()` produces stable section ordering (Commits & PRs →
   Issues → Sessions → Telegram Decisions → Memory Observations → Errors &
   Incidents → Reflection Findings) using full named entities so a `grep`
   over the vault finds the day file.
3. When `slot_config.vault_writer: true`: atomic write to
   `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` with
   idempotent `mkdir -p`. Single-machine-ownership ensures one slot owns
   this flag; on machines without iCloud sync, the file lands locally —
   that's expected and not an error.
4. `_to_signals_dict(activity)` adapts the `DayActivity` dataclass into the
   `builder.build()` raw_signals shape. Pass A (LLM) + Pass B (word-count
   cut) + Layer 2/3 number-guard regex are applied uniformly via the
   builder pipeline shared with the `morning` slot.
5. The dispatcher in `pm_briefings.delivery` synthesizes the audio via
   `tools.tts.synthesize()` (Kokoro local primary, OpenAI tts-1 fallback)
   and RPUSHes the voice-note payload to `telegram:outbox:{session_id}`.
   On TTS failure, no plaintext fallback is sent — the vault file is
   authoritative.

**Date boundaries are UTC throughout** (per `feedback_timestamp_timezone.md`).
Target day is `utc_now() - timedelta(days=1)`, so the day file is fully sealed
by the time the slot runs at the next scheduler tick after 00:00 UTC.

### State Model (`models/reflection.py`)

Each reflection gets a `Reflection` record in Redis tracking definition + last-run summary. Per-run history rows live separately in `ReflectionRun` (`models/reflection_run.py`) so the size of a Reflection record is bounded — the previous 200-cap embedded `run_history` list is gone.

| Field | Type | Purpose |
|-------|------|---------|
| `reflection_id` | AutoKeyField | Internal Popoto key |
| `name` | KeyField | Unique identifier matching registry |
| `schedule` | Field | Unified schedule string (`every:<dur>` / `cron:<expr>` / `at:<iso>`) |
| `output_sink` | Field | Delivery target (`log_only` default) — see [Output Sinks](#output-sinks) |
| `auto_delete_after_run` | Field(bool) | One-shot self-clean on success (default false) |
| `enabled` | Field(bool) | Whether the scheduler dispatches this reflection (default true) |
| `last_run_summary` | DictField | `{timestamp, status, duration, error}` — fast dashboard read |
| `ran_at` | FloatField | Unix timestamp of last execution start (kept for compatibility) |
| `run_count` | IntField | Total number of executions (kept for compatibility) |
| `last_status` | Field | `pending`, `running`, `success`, `error`, `skipped`, `stale_running` |
| `last_error` | Field | Error message from last failure |
| `last_duration` | FloatField | Duration of last run in seconds |
| `failure_count_consecutive` | IntField | Reset to 0 on success; incremented on error |
| `retry_policy` | DictField | Optional override of `DEFAULT_RETRY_POLICY` (max_retries / backoff_seconds / max_consecutive_failures_before_pause) |
| `paused_until` | FloatField | Auto-pause timestamp set by dead-letter escalation |
| `dead_letter_escalated` | Field(bool) | True iff this reflection has hit the failure threshold and emitted a Memory record at importance 7.0 |
| `cost_usd_total` | FloatField | Running total of Anthropic API spend (agent-type only) |
| `tokens_input_total` | IntField | Running total of input tokens |
| `tokens_output_total` | IntField | Running total of output tokens |
| `created_by_session_id` | Field(null) | Session that created this reflection via MCP; `None` for registry-loaded entries |

Note: `next_due` is computed by `agent.reflection_schedule.compute_next_due()` from the `schedule` string — not stored.

#### Per-Run History (`ReflectionRun`)

Each completed run writes a `ReflectionRun` row (`models/reflection_run.py`) carrying the full per-run record. The Reflection record retains only the latest `last_run_summary` for fast dashboard reads.

| Key | Type | Notes |
|-----|------|-------|
| `run_id` | AutoKeyField | Internal Popoto key |
| `name` | KeyField | Reflection name (matches `Reflection.name`) |
| `timestamp` | float | Unix epoch when the run completed |
| `status` | str | `ok`, `error`, `disabled` (aggregate result) |
| `duration_ms` | int | Total wall-clock milliseconds |
| `error` | str \| None | Top-level error message (capped at 500 chars) |
| `projects` | list[dict] | Per-project breakdown (empty `[]` for non-audit reflections) |
| `cost_usd` | float | Anthropic API spend for this single run |
| `tokens_input` / `tokens_output` | int | Token counts for this single run |
| `output_summary` | str \| None | Optional output line shown on the dashboard / fed to memory or telegram sinks |

Each entry in `projects` has shape `{slug, status, duration, findings_count, error}` where `status ∈ {"ok", "error", "skipped", "disabled"}`. See [Per-Project Audit Iteration](#per-project-audit-iteration) below.

Per-run rows carry a tiered TTL keyed off the parent's frequency (7d for `every:` ≤ 1h, 30d for daily, 90d for weekly+ / cron / at) so the history retention scales with how chatty the reflection is.

### Output Sinks

`output_sink` controls where a reflection's per-run summary lands. The field is defined on `models/reflection.py` (`output_sink`, default `log_only`) and honored by every sink kind that has shipped; unshipped sinks degrade to `log_only` until their delivery path lands.

| Sink | Behavior | Status |
|------|----------|--------|
| `log_only` (default) | Write to worker log + `last_run_summary`. No external delivery. | shipped |
| `dashboard_only` | `last_run_summary` is surfaced on `dashboard.json`'s reflections section; no log/memory/telegram side effect. | shipped |
| `memory:<importance>` | Write a Memory record at the given importance (0.0–10.0); the agent picks it up via subconscious recall. | deferred |
| `telegram:<chat>` | Send the run summary to a Telegram chat (resolved through `projects.json`). On chat-resolution failure: `WARNING` log + `delivery_error` field on the `ReflectionRun` row, run still `success`. | deferred |

**Telegram payload synthesis:** the dispatch path uses synthetic `session_id="reflection:<name>"` so outbox payloads are distinguishable from agent-session sends and don't collide with real session IDs.

### Failure Tracking

Every reflection carries a per-record retry policy (`DEFAULT_RETRY_POLICY` defaults: `max_retries=3`, `backoff_seconds=60`, `max_consecutive_failures_before_pause=5`). On error:

1. `failure_count_consecutive` is incremented and `last_error` recorded.
2. When the threshold (`max_consecutive_failures_before_pause`) is hit, `paused_until` is set 24h in the future and `dead_letter_escalated` flips to `True`.
3. A Memory record at importance 7.0 is written **exactly once per escalation cluster** so the operator sees the failure in subconscious recall without flooding memory on every retry.

Successful runs reset `failure_count_consecutive` to 0; `dead_letter_escalated` remains `True` until the operator clears it (it's an audit signal, not a runtime gate).

The dashboard surfaces `failure_count_consecutive`, `paused_until`, and `dead_letter_escalated` directly on the reflections section of `dashboard.json` (see `ui/data/reflections.py::_build_entry`).

### Skip-if-Running Guard

Before enqueuing a reflection, the scheduler checks if it's already running. If a reflection with the same name has `last_status == "running"`, it's skipped. If a reflection has been running for more than 2x its computed interval (or its explicit `timeout`), it's considered stuck and transitioned to `stale_running` then reset to `error` status so the next tick retries.

### Observability

Reflection status is available via `ReflectionScheduler.format_status()`, showing each reflection's state, time until next run, last duration, and run count. This can be wired to `/queue-status` for Telegram visibility.

### Resource Guards

Every reflection execution includes resource monitoring:

**Memory instrumentation**: `psutil.Process().memory_info().rss` is captured before and after each reflection. The delta is logged. If the delta exceeds 100MB (`MEMORY_DELTA_WARNING_BYTES`), a WARNING is emitted with the reflection name, delta, and absolute RSS values. Memory monitoring is best-effort -- if `psutil` is unavailable, reflections still run.

**Timeout enforcement**: Each reflection has a configurable timeout (via `timeout` field in YAML, or type-based defaults: 30 min for function, 60 min for agent). Function-type reflections are wrapped in `asyncio.wait_for()`. For async callables, this provides true cancellation. For sync callables running via `run_in_executor()`, the `TimeoutError` is raised but the thread cannot be cancelled (detection-only). Timeout errors are logged and the reflection is marked with error status.

**Bulkhead pool**: sync reflections are dispatched on a dedicated `ThreadPoolExecutor` (`_reflection_pool`, `REFLECTION_POOL_WORKERS` workers, default `2`) owned by `agent/reflection_scheduler.py`, not the shared default executor. This isolates wedged reflections from critical-path `run_in_executor` work (Telegram message classification, media transcription), so N stuck reflections cannot starve the rest of the worker. See [Worker Fault Containment](worker-fault-containment.md) (Fix #3, issue #1816).

**Startup-batch concurrency throttle**: after a worker restart, every function-type reflection that accumulated overdue time during the downtime becomes due simultaneously on the first tick. Without a cap, dispatching all of them as concurrent `asyncio.create_task(...)` in one pass saturates the single event loop and can starve time-sensitive coroutines — for example, the granite `_deliver_sync` delivery future (issue #1805). `tick()` caps the number of function-type reflections dispatched per tick at `REFLECTION_STARTUP_MAX_CONCURRENT` (default `4`, env-overridable). Between each dispatch it calls `await asyncio.sleep(0)` to yield the event loop. Excess overdue reflections defer naturally to the next tick (~60 s later). Agent-type reflections, which are already awaited serially, are unaffected by this cap.

**Auth probe (docs auditor)**: The `docs-auditor` substrate runs a startup auth probe against the Anthropic API. On invalid keys it returns `status="disabled"` and skips the run; on transient network errors it logs a warning and proceeds. Optional embedding auth (`OPENAI_API_KEY`) is probed separately — when unavailable, the substrate degrades gracefully to lexical-only matching. See [Docs Auditor](docs-auditor.md).

### Log Rotation

All log files have rotation configured to prevent unbounded growth. Two mechanisms are used depending on who writes the file:

| Log File | Writer | Rotation Mechanism | Max Size | Backups |
|----------|--------|--------------------|----------|---------|
| `bridge.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `bridge/telegram_bridge.py` | 10MB | 5 |
| `watchdog.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `monitoring/bridge_watchdog.py` | 10MB | 5 |
| `worker.log` | Python (RotatingFileHandler) | `logging.handlers.RotatingFileHandler` in `worker/__main__.py` | 10MB | 5 |
| `bridge.error.log` | launchd (StandardErrorPath) | Shell `rotate_log` in `valor-service.sh` | 10MB | 3 |

**Python-rotated files** use `RotatingFileHandler` which rotates automatically during writes. No service restart needed. Services using `config/settings.py:configure_logging()` also get rotation automatically via `RotatingFileHandler` with configurable `max_file_size` and `backup_count`.

**Shell-rotated files** are rotated by the `rotate_log` function in `scripts/valor-service.sh` on every service start/restart. A user-space LaunchAgent (`com.valor.log-rotate`) runs `scripts/log_rotate.py` every 30 minutes to cover long-running services between restarts — no root required. See [Log Rotation](log-rotation.md) for the full three-layer design.

### Bridge Watchdog (External)

The bridge watchdog (`com.valor.bridge-watchdog`) is intentionally NOT in the reflection registry. It must run as an external launchd service because it monitors the bridge process itself -- running it inside the process it monitors defeats its purpose.

When the watchdog detects that the bridge process is not running (via `pgrep`), it calls `crash_tracker.log_crash("bridge_dead_on_watchdog_check")` to record the event. This captures SIGKILL and OOM kills that leave no traceback.

## reflections/ Package

All daily maintenance work is implemented as standalone async callables in the `reflections/` package. Each callable returns a standard dict:

```python
{"status": "ok" | "error", "findings": [...], "summary": str}
```

**Critical constraint**: Reflection callables are invoked from inside the asyncio event loop. Any `subprocess.run()` or other blocking I/O call must be wrapped with `await asyncio.to_thread(subprocess.run, ...)` or `loop.run_in_executor()`. A bare `subprocess.run()` inside an `async def` blocks the event loop, freezing the reflection scheduler and preventing worker heartbeat writes — which causes the worker watchdog to kill the process.

The package is organized into group directories, with one file per reflection. Each file exposes a single `run()` async entry point plus a teaching docstring describing the reflection's purpose, cadence, failure modes, and related reflections.

**Group directories:**

| Directory | Files | Description |
|-----------|-------|-------------|
| `reflections/agents/` | `circuit_health_gate.py`, `session_recovery_drip.py`, `session_count_throttle.py`, `failure_loop_detector.py`, `system_health_digest.py` | Session health and Anthropic circuit management (relocated from `agent/sustainability.py`) |
| `reflections/housekeeping/` | `redis_ttl_cleanup.py`, `merged_branch_cleanup.py`, `disk_space_check.py`, `analytics_rollup.py` | Routine maintenance: expiry, branch cleanup, disk, analytics |
| `reflections/audits/` | `tech_debt_scan.py`, `redis_quality_audit.py`, `skills_audit.py`, `hooks_audit.py`, `pr_review_audit.py`, `task_backlog_check.py`, `principal_staleness.py` | Code quality, data quality, and task tracking audits |
| `reflections/memory/` | `memory_decay_prune.py`, `memory_quality_audit.py`, `embedding_orphan_sweep.py`, `memory_embedding_backfill.py` | Memory lifecycle: pruning, quality audit, orphan sweep, vectorless-record backfill |

**Shared helpers:**

| Module | Description |
|--------|-------------|
| `reflections/utilities.py` | Shared helpers: `load_local_projects()`, `run_per_project_audit()`, `run_llm_reflection()`, `is_ignored()`, `load_ignore_entries()`, `has_existing_github_work()`, `is_high_confidence()`, `extract_structured_errors()`, `PROJECT_ROOT`, `CORRECTION_PATTERNS` |

**Unchanged modules (not part of this refactor):**

| Module | Description |
|--------|-------------|
| `reflections.session_intelligence` | Pipeline: session analysis → LLM reflection → bug issue filing |
| `reflections.pm_briefings` | Slot-driven dispatcher (`pm-briefings` registry entry): `morning`, `daily_log`, `log_audit` per (project × slot) — see [pm-briefings.md](pm-briefings.md) |
| `reflections.docs_auditor` | Unified docs auditor substrate (see [Docs Auditor](docs-auditor.md)) |

> **Registry compatibility:** The old bundle module names (`reflections.maintenance`, `reflections.auditing`, `reflections.task_management`, `reflections.memory_management`) remain as thin re-export shims. Each re-exports the relocated reflections under their original `run_*` names so `config/reflections.yaml`'s historical dotted callable paths still resolve without a vault edit. `agent/sustainability.py` is likewise a re-export shim for the 5 agent reflections (keeping `send_hibernation_notification`, `_get_project_key`, and `_get_redis` defined in place as they are used by `agent/agent_session_queue.py`).

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

### docs-auditor State

The unified `docs-auditor` substrate (`reflections/docs_auditor.py`, issue #1247) tracks per-file rotation state in a Redis hash:

```python
redis.Redis.from_url(settings.REDIS_URL).hgetall("docs_audit:last_run")
```

Each field is a doc path; each value is the unix timestamp of its most recent audit. The hash form replaces both the prior `docs_auditor:last_audit_date` plain-key and avoids a per-file Redis key explosion. See [`docs/features/docs-auditor.md`](./docs-auditor.md) for the substrate design.

### Docs Auditor Authentication

The docs auditor uses the **Anthropic Python SDK directly** (not the OAuth subprocess harness used by AgentSessions). This means it requires a different credential:

| Component | Credential Used |
|-----------|----------------|
| AgentSessions (Claude Code via `claude -p`) | `CLAUDE_CODE_OAUTH_TOKEN` |
| `docs-auditor` substrate (`reflections/docs_auditor.py`) | `ANTHROPIC_API_KEY` |

**Behavior when `ANTHROPIC_API_KEY` is absent or invalid:**

The substrate performs a startup auth probe before iterating any docs. If the key is missing, a sentinel string (`"None"`, `"null"`, `"false"`, `"0"`), or invalid (rejected by the Anthropic API), it logs a single `WARNING` and returns immediately:

```
WARNING  docs_auditor: skipping: ANTHROPIC_API_KEY not set
```

No `ERROR` lines are emitted, no docs are processed, and the worker heartbeat is unaffected. The substrate returns `{"status": "disabled", ...}` — distinct from `{"status": "ok", ...}` for a schedule-based skip — so dashboards and monitoring can distinguish a permanently-disabled auditor from a temporarily-skipped one.

**To enable docs auditing**, add `ANTHROPIC_API_KEY` to the worker's environment (e.g., in `~/Desktop/Valor/.env` or the worker's launchd plist). The auditor will automatically begin running on its daily rotation schedule once the key is present and valid.

**Non-auth API failures** (rate limits, transient network errors) are handled by a consecutive-error circuit break: if 3 or more consecutive doc-audit errors occur during a run, the loop exits early with a `WARNING`. This caps error cascade for transient failures without requiring a full circuit breaker integration.

## Session Analysis (part of `session_intelligence` pipeline)

Queries Redis for recent sessions and computes quality metrics.

### Data Sources

- **AgentSession** — turn count, tool call count, log file path, session tags
- **BridgeEvent** — error events correlated to sessions

The runner caps analysis at the 20 most interesting sessions (sorted by turn count).

> **Removed: thrash-ratio detection (#1414).** A prior heuristic flagged
> sessions as "thrashing" when `1 - (turn_count / tool_call_count) > 0.5`.
> Because `turn_count` is *total assistant turns* (not *successful* turns),
> any session averaging more than ~2 tool calls per turn tripped the
> threshold — roughly 50% of healthy, completed SDLC runs. The detector was
> removed entirely rather than left emitting false positives that auto-filed
> duplicate bug issues. Neither candidate replacement signal (repeated
> identical tool calls, repeated tool errors) is cheaply derivable from the
> available data: `AgentSession` exposes only scalar aggregates, and the
> transcript format records tool inputs/results as truncated summary strings
> with no structured args or `is_error` flag. A missing detector is strictly
> better than one with a 50% false-positive rate.

### Correction Detection

Scans session transcripts for patterns indicating the human corrected the agent:

| Pattern | Example |
|---------|---------|
| Explicit correction | "no, I meant...", "that's wrong" |
| Redirection | "actually, ...", "not what I asked" |
| Stop and redirect | "stop... instead" |
| Repeated instruction | "I said..." |

These regex patterns are defined in `CORRECTION_PATTERNS` in `reflections/utilities.py`.

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

An issue is filed only when a reflection is a **code bug AND** carries at least
one supporting signal (`is_high_confidence()` in `reflections/utilities.py`):

| Criterion | Condition | Role |
|-----------|-----------|------|
| Category | `category == "code_bug"` | **Required** — hard gate |
| Prevention | `prevention` field is non-empty | Supporting (either suffices) |
| Pattern length | `pattern` field is at least 10 characters | Supporting (either suffices) |

This tightened the prior **2-of-3** rule (#1414). Under 2-of-3, a non-code-bug
reflection (e.g. `category="poor_planning"`) could clear the gate on prevention
+ pattern length alone — which is how #1414, an agent-behaviour claim rather
than a code defect, reached "high-confidence" and auto-filed itself. The gate
now hard-requires `code_bug`, so only genuine code defects reach the auto-fix
path.

### Ignore Log

The ignore log (Redis `ReflectionIgnore` model) suppresses issue creation for specific patterns for 14 days. Use `reflections.utilities.is_ignored()` with `load_ignore_entries()` to check patterns.

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

### Per-Project Audit Iteration

Three audit reflections (`tech-debt-scan`, `skills-audit`, `hooks-audit`) run once per project on the current machine, aggregating findings into a single run record with a per-project breakdown. (Documentation/feature-doc audits were consolidated into the `docs-auditor` substrate — see [Docs Auditor](docs-auditor.md).) The shared helper `reflections.utilities.run_per_project_audit(audit_one, *, skip_if=None, name)` handles the iteration:

1. Loads `load_local_projects()` (filtered to repos present on disk)
2. For each project, evaluates `skip_if(repo_root)` first; silently skipped projects are recorded with `status="skipped"` and excluded from `findings`
3. Calls `audit_one(project)` for qualifying projects, prefixing each finding with `[{slug}]`
4. Both `skip_if` and `audit_one` are wrapped in the same `try/except Exception` per project — a failure (e.g. `OSError` on a network mount) is captured as `status="error"` for that project and the loop continues
5. Returns `{status, findings, summary, projects: [...]}` where aggregate `status` follows: any error → `error`; all `disabled` → `disabled`; otherwise `ok`

**Skip predicates (silent no-op when missing):**

| Audit | Skipped when |
|-------|--------------|
| `tech-debt-scan` | Never — always runs |
| `skills-audit` | `.claude/skills-global/do-skills-audit/scripts/audit_skills.py` absent |
| `hooks-audit` | Both `logs/hooks.log` and `.claude/settings.json` absent |

**Timeout budgets** (per-project iteration linearly scales wall-clock; YAML overrides in `~/Desktop/Valor/reflections.yaml` sized for an N=20-project worst case):

| Reflection | YAML `timeout:` |
|------------|-----------------|
| `tech-debt-scan` | 2700s (45 min) |
| `skills-audit` | 600s (10 min) |
| `hooks-audit` | 600s (10 min) |

**Async dispatch:** `run_per_project_audit` is sync; the audits above are sync end-to-end.

### Dashboard

The reflection modal at `localhost:8500` renders a per-project sub-table when `run.projects` is non-empty. Each project gets an indented row with: status badge, `[slug]` tag, duration, error cell. Status badges visually distinguish all four states:

| Badge | Status | Meaning |
|-------|--------|---------|
| `badge-ok` (green) | `ok` | Project ran successfully |
| `badge-error` (red) | `error` | Project body raised |
| `badge-skipped` (gray) | `skipped` | Skip predicate matched silently |
| `badge-disabled` (amber) | `disabled` | Cost cap exhausted (e.g. global API cap) |

The sparkline color is driven by the aggregate `run.status`, independent of per-project badges. Run records without a `projects` field (older entries or non-audit reflections) render as before — the per-project block is gated by `{% if run.projects %}`.

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

4-layer always-apply audit (see [`docs/features/subconscious-memory.md`](./subconscious-memory.md#memory-health-audit) for full design):

- **Layer 0** — baseline zero-access (>30d) + low-confidence (<0.2) flags. Read-only; no issues filed.
- **Layer 1** — deterministic supersede via `_looks_like_refusal` predicate. Sets `superseded_by="cleanup-junk-extraction"` on `extraction-*` records matching refusal/JSON-shrapnel patterns. Capped at `MAX_LAYER1_SUPERSEDES_PER_RUN=50` (operator-tunable via `MEMORY_AUDIT_LAYER1_CAP`).
- **Layer 2** — heuristic anomaly detection (no model). Four signals: `category-default-skew`, `importance-1.0-skew`, `agent-id-cluster`, `html-escape-rate`. Cross-threshold signals become candidates.
- **Layer 3** — granite classification (`granite4.1:3b` via `OLLAMA_CLASSIFIER_MODEL`, fail-soft). Samples up to 20 last-24h records; 30s wallclock budget; 10s `GEMMA_CALL_TIMEOUT_SEC` per call. Verdicts grouped by anomaly_signal; signals with ≥3 matches become candidates. Fails soft if Ollama is unavailable.
- **Issue surfacing** — Layer-2/3 candidates → `gh issue create --label memory --label investigation`, deduped via title-prefix search. Layer 0/1 never file issues.
- **Cluster re-filing suppression (issue #2016)** — the dup-check (`_find_recent_audit_issue` in `reflections/memory/memory_quality_audit.py`) used to search only `--state open`, so closing an anomaly issue provided no protection: the very next daily run would re-file a fresh issue for the same signal. That produced a recurring pattern of the same cluster being opened, closed, and immediately re-opened (#1497, #1786, #1931). The dup-check now queries `gh issue list --state all`, and closing (acknowledging) a memory-audit anomaly issue suppresses re-filing of that same signal — matched by the `[memory-audit] {signal}:` title prefix — for `CLUSTER_REFILE_SUPPRESSION_DAYS = 14` days. A matching OPEN issue always suppresses, as before; a matching CLOSED issue suppresses only while inside the 14-day window. If the anomaly persists past the window, a fresh issue is filed on the next run.

### `embedding-orphan-sweep`

Reconciles the on-disk Memory embedding store (`~/.popoto/content/.embeddings/Memory/`) against live Memory records (issue #1214). Calls Popoto's `EmbeddingField.garbage_collect(Memory)` to remove `.npy` files whose SHA-256-hashed names are no longer in `$Class:Memory`, plus `EmbeddingField.sweep_stale_tempfiles(Memory)` to remove leaked `tmp*.npy` atomic-write tempfiles older than 1 hour.

- **Dry-run default**: set `EMBEDDING_ORPHAN_SWEEP_APPLY=true` to enable actual deletion (matches the `MEMORY_DECAY_PRUNE_APPLY` pattern).
- **Popoto-stub guard**: a runtime capability probe (`hasattr(EmbeddingField, "sweep_stale_tempfiles")`) detects pre-1.6.0 installs and short-circuits with status `"skipped"` and finding `"popoto<1.6 — gc not implemented yet"` rather than silently appearing to succeed.
- **Marker requirement**: `Memory.__embedding_garbage_collect__ = True` opts the model into garbage_collect; without it Popoto's helper is a no-op.
- **Metrics emitted**: `memory.embedding_orphans_swept` and `memory.embedding_tempfiles_swept` counters.

For one-shot reconciliation against an existing backlog, the operator script `scripts/embedding_orphan_reconcile.py` (dry-run default, `--apply` to act) wraps the same Popoto helpers with two additional safety gates: a positive-assertion check (refuses to apply if to-delete intersects expected-keep) and a pre-flight regression guard (refuses to apply if `$Class:Memory` is empty).

### `memory-embedding-backfill`

The inverse of `embedding-orphan-sweep`: heals active Memory records that persisted **without** a `.npy` at all — the `GracefulEmbeddingField` degradation marker (`embedding = None`) written when the embedding provider timed out or was unreachable mid-save (issue #1904; see [Embedding Degradation](subconscious-memory.md#embedding-degradation-persist-without-vector)). Finds records with a falsy `embedding`, skips `superseded_by` records, and — once `OllamaEmbeddingProvider().is_available()` — re-embeds them so they regain the fourth (semantic-similarity) RRF signal.

- **Dry-run default**: set `MEMORY_EMBEDDING_BACKFILL_APPLY=true` (also accepts `1`/`yes`) to enable actual re-embedding (matches the `EMBEDDING_ORPHAN_SWEEP_APPLY` / `MEMORY_DECAY_PRUNE_APPLY` pattern).
- **Per-run cap**: `MAX_BACKFILL_PER_RUN = 500` — a long-outage backlog drains over several daily runs instead of re-saturating a just-recovered provider in one shot.
- **Provider gate**: apply mode still skips every record (reporting it as a skip, not an error) when the provider is unavailable, so a still-down Ollama never triggers a re-save storm.
- **Partial-save contract (critique C1)**: re-embed calls `memory.save(update_fields=["embedding"])`, never a bare `memory.save()`. A bare save re-runs `on_save` for every field, including `Memory.relevance` (a `DecayingSortedField` with `auto_now=True`), which would re-stamp it to "now" and silently un-decay a stale memory.
- **Metrics emitted**: `memory.embedding_backfill_reembedded` (dimensioned by `mode: dry_run | applied`); the summary also surfaces `get_degradation_count()` from `models/graceful_embedding_field.py` — the in-process count of degraded saves observed since worker start, independent of the throttled warning log.

## Operations

### Scheduling

The reflection scheduler runs in its own supervised launchd subprocess
(`python -m reflections`, `com.valor.reflection-worker`, `KeepAlive`+`ThrottleInterval`),
installed by `scripts/install_reflection_worker.sh`. It ticks every 60 seconds. See
[Reflection Scheduler Subprocess](reflection-scheduler-subprocess.md) for the lifecycle,
worker-role install gate, cutover ordering, and the `/dashboard.json`
`reflection_scheduler_*` health surface.

| Component | Detail |
|-----------|--------|
| Scheduler | `agent/reflection_scheduler.py` (run out-of-process by `python -m reflections`) |
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
| `reflections/utilities.py` | Shared helpers: `load_local_projects()`, `is_ignored()`, `run_llm_reflection()`, `extract_structured_errors()`, `CORRECTION_PATTERNS` |
| `reflections/agents/` | 5 agent/session health reflections (one file each): `circuit_health_gate.py`, `session_recovery_drip.py`, `session_count_throttle.py`, `failure_loop_detector.py`, `system_health_digest.py` |
| `reflections/housekeeping/` | 4 housekeeping reflections (one file each): `redis_ttl_cleanup.py`, `merged_branch_cleanup.py`, `disk_space_check.py`, `analytics_rollup.py` |
| `reflections/audits/` | 7 audit reflections (one file each): `tech_debt_scan.py`, `redis_quality_audit.py`, `skills_audit.py`, `hooks_audit.py`, `pr_review_audit.py`, `task_backlog_check.py`, `principal_staleness.py` |
| `reflections/memory/` | 4 memory reflections (one file each): `memory_decay_prune.py`, `memory_quality_audit.py`, `embedding_orphan_sweep.py`, `memory_embedding_backfill.py` |
| `reflections/session_intelligence.py` | Session analysis → LLM reflection → bug issue pipeline |
| `reflections/pm_briefings/` | Slot-driven `pm-briefings` dispatcher: `morning`, `daily_log`, `log_audit` slot modules + builder + delivery |
| `reflections/maintenance.py` | Re-export shim (registry compat): re-exports housekeeping + audit callables under original `run_*` names |
| `reflections/auditing.py` | Re-export shim (registry compat): re-exports audit callables under original `run_*` names |
| `reflections/task_management.py` | Re-export shim (registry compat): re-exports task audit callables under original `run_*` names |
| `reflections/memory_management.py` | Re-export shim (registry compat): re-exports memory callables under original `run_*` names |
| `models/reflection.py` | Reflection state model (per-reflection Redis tracking) |
| `models/reflection_ignore.py` | ReflectionIgnore: auto-fix suppression with TTL-based expiry |
| `models/pr_review_audit.py` | PRReviewAudit: PR review finding deduplication |
| `models/reflections.py` | Re-export shim: `ReflectionIgnore`, `PRReviewAudit` |
| `scripts/reflections_report.py` | GitHub issue creation module (was used by retired `daily_report`) |
| `scripts/update/env_sync.py` | `sync_reflections_yaml()`: creates vault symlink on update |
| `~/Desktop/Valor/projects.json` | Multi-repo project registry |
| `~/Desktop/Valor/reflections.yaml` | Vault copy of the registry (canonical source) |

## Dependencies

| Dependency | Used By | Required |
|------------|---------|----------|
| Redis (Popoto ORM) | All reflections | Yes — state persistence |
| PyYAML | Registry loader | Yes — reads `config/reflections.yaml` |
| psutil | Memory instrumentation | Optional — memory snapshots degrade gracefully if missing |
| `ANTHROPIC_API_KEY` | `docs-auditor`, `session-intelligence` | Conditional — LLM reflection and docs auditor substrate |
| `gh` CLI (authenticated) | `task-backlog-check`, `session-intelligence`, `pm-briefings` (`daily_log` slot uses gh for PR/issue aggregation), `merged-branch-cleanup`, `pr-review-audit` | Conditional |
| `tools.tts` (Kokoro local + OpenAI fallback) | `pm-briefings` (`morning` and `daily_log` slots) | Conditional — voice-note synthesis. Failure logs but does not crash the reflection. |
| Redis outbox + bridge relay | `pm-briefings` | Yes — voice-note delivery uses RPUSH to `telegram:outbox:{session_id}` (no direct Telethon) |
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

- [Docs Auditor](docs-auditor.md) — unified `docs-auditor` substrate (replaces the prior `documentation-audit`, `feature-docs-audit`, and `knowledge-reindex` reflections)
- [Hooks Best Practices & Audit](hooks-best-practices.md) — `hooks-audit` unit deep dive
- [Skills Audit](do-skills-audit.md) — `skills-audit` unit deep dive
- [Subconscious Memory](subconscious-memory.md#memory-consolidation) — `memory-dedup` consolidation
- [Subconscious Memory](subconscious-memory.md#embedding-degradation-persist-without-vector) — `GracefulEmbeddingField` and the `memory-embedding-backfill` reflection it feeds (issue #1904)
