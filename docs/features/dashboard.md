# Dashboard

The web UI dashboard provides an operational snapshot of work in flight across all projects.

**Start:** `python -m ui.app` (serves on `localhost:8500`)
**JSON API:** `curl -s localhost:8500/dashboard.json`

## Jobs Table

**Jobs are the top-level list** (issue #2519), auto-refreshing every 5 seconds via HTMX
polling. A **Job** is a unit of work: a GitHub issue, a pull request, or a planned slug.
One Job is served by one or more **AgentSession runs** over its lifetime: an original run,
a recovery respawn, the local `sdlc-local-{N}` anchor, a dev sub-session spawned by a PM.
Expanding a Job row reveals its runs; clicking a Job row or a run row opens that session's
detail modal.

Grouping is presentation-level. Nothing is persisted and no schema changed; the Job key is
derived in `ui/data/jobs.py` from fields already on `AgentSession`. The durable Job
read-model is #2494's work.

### Job identity precedence

| Rank | Key shape | Derived from |
|------|-----------|--------------|
| 1 | `issue:{repo}#{n}` | `issue_number`, `issue_url`, a `sdlc-{N}` slug, or a `sdlc-local-{N}` session id |
| 2 | `pr:{repo}#{n}` | `pr_number` or `pr_url` |
| 3 | `slug:{project}:{slug}` | A named plan slug with no issue yet |
| 4 | `thread:{root}` | Root of the `parent_agent_session_id` chain |

Issue outranks slug because `tools/valor_session.py` mints the `sdlc-{N}` slug *from* an
issue number: a bridge-driven run carrying `slug="sdlc-2137"` and a local anchor carrying
`issue_number=2137` served the same Job, and keying on slug first leaves them as two
unrelated rows.

`{repo}` scopes the key so issue numbers do not collide across repos. Two runs serving one
issue often have the URL on only one of them, so the scope resolves through four tiers:

| Tier | Source | Note |
|------|--------|------|
| 1 | Owner/repo in the run's own `issue_url`, then `pr_url` | A PR is opened against the repo its issue lives in, so a lone `pr_url` still beats a default |
| 2 | The project's `github.org`/`github.repo` from `projects.json` | Closes the gap for a run carrying only `issue_number` |
| 3 | The scope a sibling run of the same work item recorded | Applies when exactly one repo is named across the item's runs |
| 4 | The project key, then `unscoped` | |

Tier 3 exists because `projects.json` is private and iCloud-synced: a fresh machine or a CI
checkout reads nothing and `_load_project_configs()` caches `{}` for the TTL. Without it,
`sdlc-2158` (carrying `issue_url`) and `sdlc-local-2158` (carrying only the number) key to
`issue:tomcounsell/ai#2158` and `issue:valor#2158` and one Job renders as two. When an
item's runs name two different repos the scope is ambiguous, so nothing is adopted and each
run keeps its own: two repos' issue #665 stay two Jobs.

Rank 4 keeps ad-hoc and conversational sessions on the board. A session with no work-item
identity inherits the nearest ancestor that has one; with no such ancestor, its thread root
becomes a Job of one. **Every session lands in exactly one Job.** Gating on `slug` is how
#1379 dropped conversational sessions from tracking.

### Job row columns

| Column | Source | Notes |
|--------|--------|-------|
| Project | `project_key` + `projects.json` lookup | Project name with metadata popover (repo, chat, stack, machine). Carries the expand/collapse control |
| Job | `display_name` | Fallback chain: `slug` > issue/PR title (GitHub lookup) > the newest run's `display_name`. Clipped at 72 chars, full label in the tooltip. Second line carries `#issue`, `PR n`, the slug, or "conversation" |
| Runs | `run_count` | How many AgentSessions served this Job. A second badge appears when more than one run is live |
| Started | Earliest run's `started_at`/`created_at` | Formatted timestamp |
| Status | The live run's status, or the newest outcome once every run is terminal | Duration, liveness signals, freshness chip, and summed `total_cost_usd` when non-zero |
| SDLC Stages | `stage_states` from the run that recorded them | Dot indicators: completed (green), in-progress (blue), failed (red), ready (yellow) |
| Links | `issue_url`, `pr_url` from any run | Issue and PR links |

Totals (`total_cost_usd`, `turn_count`, `tool_call_count`) sum across every run. Liveness is
not summable, so it is read off the **representative** run: the live one, or the newest
outcome once every run is terminal. That is the run `primary_agent_session_id` names and the
one the row's status, duration, and click-through modal already speak for, so the row reads
as one statement about one run. A Job with more than one live run carries an "N live" badge
pointing at the per-run rows.

### Run row columns

Nested run rows (`ui/templates/_partials/session_row.html`) keep the per-session detail the
flat list carried:

| Column | Source | Notes |
|--------|--------|-------|
| Initiator | `initiator` | telegram, email, local, or `session/{parent}` |
| Name | `display_name` property | Fallback chain: `slug` > issue/PR title (GitHub lookup) > `context_summary` > `MESSAGE:`/`FROM:` extracted from system prompt > `type • project` |
| Persona | `session_type` | eng (blue), teammate (green), other (purple). `classification_type` badge shown alongside |
| Started | `started_at` or `created_at` | Formatted timestamp |
| Status | `status` field | Color-coded badge plus the row-level liveness signals above |
| SDLC Stages | `stage_states` | Same dot indicators |
| Run id | `agent_session_id` | First 8 characters |

Per-run counts (`turn_count`/`tool_call_count`, `started_at`) reflect only the current
resume, not the whole Telegram thread. Reply-resumes carry the prior run's history forward
via `thread_first_created_at`/`thread_turn_count`/`thread_tool_call_count`/`thread_run_count`
on the `AgentSession` record; `dashboard.json` also emits the render-time fold
(`thread_display_started_at`, `thread_display_turn_count`, `thread_display_tool_call_count`,
`thread_display_run_count`), which sums the rollup with the in-flight run and falls back to
the per-run values for a never-resumed thread. See
[Thread-Level Timing/Turn Rollup Across Resumes](session-lifecycle.md#thread-level-timingturn-rollup-across-resumes)
for the accumulation semantics.

### Parent/Child Hierarchy

Sessions spawned by a parent (e.g., PM spawning Dev) land in the parent's Job and render as
sibling run rows under it. The relationship stays visible through the run row's Initiator
column (`session/{parent}`).

- Grouping is built from the flat session list using `parent_agent_session_id` (no N+1 queries)
- Orphaned children (parent aged out of the retention window) resolve to their missing
  parent's id, so siblings orphaned together stay in one Job
- The ancestor walk is cycle-guarded and capped at 32 hops

`dashboard.json`'s `sessions` array keeps the nested `children` shape unchanged for
consumers that read it.

### Priority and Classification

- Sessions with `priority` of "urgent" or "high" show a colored priority badge
- `classification_type` (e.g., "sdlc", "qa") appears as an outlined badge next to the persona

### Dormant Sessions

Sessions with status `dormant` are listed in the Name column, indicating the agent is paused waiting on the human.

### Lifecycle Iconography

Of the 9 non-terminal lifecycle states, most render with distinct glyphs in the row template (see [Session Lifecycle](session-lifecycle.md) for state semantics):
`running`, `pending`, `dormant`, `active`, `waiting_for_children`, `paused`, `paused_circuit`, `superseded`. `paused_budget` (#1821) has no dedicated glyph yet and renders as plain status text. Terminal statuses (`completed`, `failed`, `killed`, `abandoned`, `cancelled`, `superseded`) collapse the row into the terminal-status presentation.

## Liveness Signals

The dashboard exposes session liveness as state-of-truth so operators can answer "is this session actually progressing right now, or is it claimed-running-but-dead (ghost)?" without leaving the dashboard.

### Row-level signals (non-terminal only)

Both the Job row (`jobs_table.html`) and the nested run rows (`session_row.html`) render
these, in the same visual vocabulary. On a Job they describe its representative run; on a
run row they describe that run.

- **Freshness chip**: age since `last_evidence_at` rendered as a colored chip via the `freshness_age` Jinja filter:
  - green (`freshness-fresh`) for `<60s`
  - amber (`freshness-warm`) for `<600s`
  - red (`freshness-stale`) for `>=600s`
- **Ghost badge**: when `process_alive == False` (the harness PID returned `ProcessLookupError` from a non-blocking `os.kill(pid, 0)` probe), the row renders a dashed-red `ghost` badge, marking a session whose harness subprocess has died while its record still claims `running`/`active`.
- **Stall advisory**: `stall_advisory` of `suspect` or `stalled` renders a colored badge with `stall_advisory_reason` as its tooltip. `healthy` stays quiet.
- **Staleness**: `is_stale` (status `running` or `active` with `updated_at` more than 10 minutes old) dims the row and gives the status badge a dashed orange border with a `·` marker.
- **Unhealthy**: `unhealthy_reason` renders a warning `!` badge carrying the reason.

### Modal Liveness section

`session_modal_content.html` renders a `Liveness` sub-table between Timing and SDLC, gated by the `_has_liveness` macro. Rows include:

- **PID** — the fenced `exec_pid` (newest `spawn_history` entry via `live_fence`) with one of three chips: alive (probe returned True), `GHOST — process dead` (probe returned False), or unknown (probe returned None: PID is None or `<= 0`, or `PermissionError`/`OSError`)
- `current_tool_name`, `last_evidence_at`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`, `last_tool_use_at`, `last_turn_at`
- `recovery_attempts`, `reprieve_count`
- `unhealthy_reason` (when set)

### Modal Session Runner Identity block (issue #1924)

The modal renders a **Session Runner** block (below the Liveness section)
gated by a Jinja guard — rendered only when at least one of the resume
scalars is non-null:

| Field | Description |
|-------|-------------|
| `claude_session_uuid` | The PM session's `--resume` entry point |
| `dev_agent_id` | The `dev` subagent's continuation handle — the same id is expected across a worker restart if the resumed session continues the same subagent |
| `runner_cwd` | Absolute working directory the resume is scoped to |
| `claude_version` | CLI version the session last ran against |

The bounded turn-history mirror (`{ts, actor: pm|dev, text}` per turn,
extending the `session_events` stream) renders alongside this block as an
observability feed — see [Headless Session
Runner](headless-session-runner.md#simple-resume-d3-four-scalars). It is
never the resume path's source of truth (the on-disk Claude transcripts are);
it exists for dashboard visibility and as a disaster-recovery seed.

### Process-alive probe

`ui/data/sdlc._check_process_alive(pid)` is a non-blocking `os.kill(pid, 0)` with tri-state return: `True` (alive), `False` (`ProcessLookupError` — ghost), or `None` (PID is None or `<= 0` to dodge process-group semantics, or `PermissionError`/`OSError`). The probe is gated to non-terminal probe statuses (`running`, `active`, `paused`, `paused_circuit`) — terminal sessions never trigger a probe.

### PID lifecycle invariant

`AgentSession.exec_pid` is stamped by the runner's `_on_turn_spawn` closure (`agent/session_runner/runner.py`) via `AgentSession.stamp_execution_spawn(...)`, which writes the whole fenced record (`exec_pid`, `pid_create_time`, `exec_cwd`, `exec_harness`, and an append to `spawn_history`) BEFORE the turn-await blocks:

- Stamped at spawn; **not cleared between turns**. A stale `exec_pid` pointing at an exited pid is harmless — the create-time fence (`agent/pid_fence.py::fence_is_live`, comparing the recorded `pid_create_time`) rejects a dead or recycled pid, so staleness is detected by comparison rather than by nulling.
- The retained child handle (`_TurnHandle`) is the runner's primary liveness mechanism; the fenced record is the backstop for cross-process readers (the dashboard, the orphan reaper) that never held the handle.

See [AgentSession Fenced Execution Record](agent-session-fenced-execution-record.md) and [PM Session Liveness](pm-session-liveness.md) for the broader evidence-based liveness model.

## Data Flow

1. **Redis (Popoto):** `AgentSession` records with `datetime.datetime` timestamp fields
2. **Enumeration** (`models/session_enumeration.py`): `enumerate_sessions()` scans the class
   set and filters status in Python. See [One enumeration seam](#one-enumeration-seam)
3. **Data layer** (`ui/data/sdlc.py`): `_safe_float()` converts datetime objects to float
   timestamps via `.timestamp()`. `_session_to_pipeline()` maps all fields to
   `PipelineProgress` Pydantic models. `load_pipelines()` returns the flat retained list;
   `assemble_session_tree()` nests children under parents
4. **Job grouping** (`ui/data/jobs.py`): `group_into_jobs()` collapses the flat list into
   `JobGroup` rows; `get_all_jobs()` is what the page calls
5. **Templates:** `_partials/jobs_table.html` renders Job rows and imports the `session_row`
   macro from `_partials/session_row.html` for the nested runs
6. **HTMX refresh:** `/_partials/jobs/` endpoint returns table HTML every 5 seconds. Expand
   state survives the swap via `window._expandedJobs`

## One enumeration seam

`models/session_enumeration.py::enumerate_sessions()` is the single answer to "what
AgentSessions exist?" The dashboard, `valor-session`, and the analytics rollup all call it.

The seam exists because those three callers each hand-rolled the question and got three
different answers against the same Redis, minutes apart: 22 well-formed `status="pending"`
sessions were individually retrievable by id while `query.filter(status="pending")` returned
nothing. `valor-session kill --all` would have reported success having skipped all 22.

**The scan is the sanctioned path.** `query.all()` reads the class set, so a record with an
intact hash always appears regardless of what the `status` secondary index believes. Status
filtering happens in Python on the scan result.

**A record with no id never leaves the seam.** A partial write leaves a hash without an id.
Dropping it once here covers every caller: `kill --all` would call `finalize_session` on it,
`valor-session list` would print it, the dashboard would try to render it. It is also left
out of the scan counts, so an id-less hash sitting in the index shows up as a divergence
rather than hiding inside a matching total.

**Disagreement is loud.** `check_status_index_divergence()` compares
`query.count(status=...)` against the observed scan count per status and logs a warning
naming each status that disagrees. Both directions are reported: `index < scan` is the
lost-records hole, `index > scan` is the #2101 shape where identity-less hashes inflate the
index set.

The check is throttled to at most one pass per `DIVERGENCE_CHECK_INTERVAL_S` (300s) so the
5-second dashboard poll does not pay for it. The throttle is per-process, which is what a
polling loop needs; a one-shot `valor-session` invocation audits the index once, costing
about as much as the scan it already pays (8ms against 12ms at 24 sessions) and reporting
into exactly the context an operator is watching. `_last_divergence_check_at` starts at
`None` rather than `0.0` because `time.monotonic()` counts from boot: a zero would swallow
the first check of every process opened within 5 minutes of the machine coming up.

## PipelineProgress Model

The `PipelineProgress` Pydantic model is the serialization layer between Redis data and the UI/JSON API.

### Fields

**Core:** `agent_session_id`, `session_id`, `session_type`, `status`, `slug`, `message_text`, `project_key`, `project_name`, `project_metadata`, `branch_name`

**Timestamps:** `created_at`, `started_at`, `completed_at`, `updated_at` (all as float epoch seconds)

**Hierarchy:** `parent_agent_session_id`, `children` (list of nested `PipelineProgress`)

**Metadata:** `context_summary`, `turn_count`, `tool_call_count`, `unhealthy_reason`, `priority`, `classification_type`, `is_stale`

**Liveness:** `exec_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`, `recovery_attempts`, `reprieve_count`, `process_alive`. Existing fields used by the row freshness chip and modal Liveness section: `current_tool_name`, `last_tool_use_at`, `last_turn_at`, `last_evidence_at`

**SDLC:** `stages`, `current_stage`, `events`

**Links:** `issue_url`, `plan_url`, `pr_url`, `issue_number`, `pr_number`

**Session runner identity:** `claude_session_uuid`, `dev_agent_id`, `runner_cwd`, `claude_version`, plus the bounded turn-history mirror

## JSON API

`GET /dashboard.json` returns all fields above for each session, plus health, reflections, and machine info. The `children` array is recursively serialized. All fields are additive -- no breaking changes from prior versions.

The `jobs` array sits alongside `sessions`, which keeps its exact prior shape. Each Job
carries `key`, `kind`, `display_name`, `full_display_name`, `issue_number`, `pr_number`,
`slug`, `repo`, `project_key`, `project_name`, `status`, `is_active`, `run_count`,
`active_run_count`, `primary_agent_session_id`, `is_stale`, `process_alive`,
`unhealthy_reason`, `stall_advisory`, `stall_advisory_reason`, `last_evidence_at`, `stages`,
`current_stage`, `started_at`, `last_activity_at`, `completed_at`, `duration`,
`total_cost_usd`, `turn_count`, `tool_call_count`, `issue_url`, `plan_url`, `pr_url`, and
`sessions`. A Job's `sessions`
entries have their `children` array empty: the Job already lists every run it owns, so
recursing would repeat them.

One scan feeds both views. `dashboard_json` calls `load_pipelines()` once, groups Jobs from
it, then assembles the session tree. `get_analytics_summary()` adds a second scan, narrowed
to `status="completed"`, and cuts both its windows from that one result: the 1d window is a
strict subset of the 7d window (#2122 is the precedent for watching this fan-out).

## Retention

Inactive sessions are filtered by a configurable retention period (env var `DASHBOARD_RETENTION_HOURS`, default 48h). Active sessions are exempt from that window but are still subject to a hard cap (env var `DASHBOARD_MAX_AGE_HOURS`, default 240h / 10 days) — a session wedged in `pending`/`running` ages out of the dashboard rather than accumulating forever.

## Related

- Issues: #657, #2519 (Jobs as the top-level list)
- `ui/data/sdlc.py` -- Session data layer
- `ui/data/jobs.py` -- Job identity and grouping
- `models/session_enumeration.py` -- Shared enumeration seam
- `ui/templates/_partials/jobs_table.html` -- Job rows
- `ui/templates/_partials/session_row.html` -- Nested run rows
- `ui/app.py` -- FastAPI routes including `/dashboard.json`
- `ui/static/style.css` -- Styles for badges, hierarchy, staleness
