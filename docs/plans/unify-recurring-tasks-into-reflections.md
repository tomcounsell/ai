---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1273
last_comment_id:
revision_applied: true
revision_cycle: 3
---

# Unify Loops, Schedules, Routines, and Reflections into One Persistent Reflection System

## Problem

Today four overlapping mechanisms answer "do this on a schedule":

1. **Reflection scheduler** ([`agent/reflection_scheduler.py`](../../agent/reflection_scheduler.py) + [`models/reflection.py`](../../models/reflection.py) + `~/Desktop/Valor/reflections.yaml`) — asyncio loop in the worker; interval-seconds only; tracks `ran_at`, `run_count`, `last_status`, `last_error`, `last_duration`, and a 200-cap `run_history` per Reflection; logs to `logs/worker.log`.
2. **`/loop` skill** (Claude Code harness) — self-paced re-fire of a prompt via `ScheduleWakeup`; ephemeral; nothing in our Redis.
3. **`/schedule` skill** (Claude Code harness) — cron/`fireAt` remote routines via `create_scheduled_task`; harness-side state only.
4. **`tools/agent_session_scheduler.py`** — schedules an `AgentSession` for an SDLC issue; supports `--after <ISO>` one-shot delay; no recurrence; writes directly to `AgentSession`.

There is **no single answer** to "what recurring AI work is configured on this machine, when did it last run, and what did it produce." The four systems also disagree on schedule grammar, output destination, failure tracking, and cost accounting.

**Current behavior:**
- A user asking "what's running on this box and what did the last run cost?" must dig in three places (Redis, harness state, log files) and get partial answers from each.
- `dashboard.json` only sees the in-repo Reflection scheduler. `/loop` and `/schedule` runs are invisible to our dashboard, our memory system, and our analytics rollup.
- `agent_session_scheduler` overlaps the Reflection scheduler on the "schedule a one-shot AgentSession" axis but writes to a different Popoto model with a different schema.
- Every reflection currently uses interval-seconds. Cron-style ("daily at 09:00 in `America/Los_Angeles`") and one-shot ISO scheduling are not expressible.
- Failure semantics are weak: `mark_completed(error=...)` records `last_status="error"` and bumps nothing else. No consecutive-failure count, no retry/backoff, no dead-letter sink.
- No system tracks `cost_usd`, `input_tokens`, or `output_tokens` per run.

**Desired outcome:**

A single canonical **Reflection** system, owned by this repo, that:
1. Persists every recurring or scheduled task in Popoto/Redis with enough fidelity to answer "what is configured, when did it last run, what did it cost, what did it produce, did it fail?" from `dashboard.json` alone.
2. Accepts a unified schedule grammar (`cron:` / `every:` / `at:`, the fazm triplet) so cron-style, interval-style, and one-shot tasks share one schema.
3. Subsumes `agent_session_scheduler`'s scheduling concern; provides an MCP surface that supersedes the agent's reach for `/loop` and `/schedule` (which we cannot delete — they live in the harness — but can shadow with first-party tooling).
4. Threads run output back into the appropriate sink (Telegram chat, Memory record, dashboard, log) per-reflection.
5. Survives a worker restart with no data loss, no silent skipped runs, and a clear dead-letter path for repeated failures.

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:19:52Z
**Disposition:** Unchanged — issue was filed today; baseline `git log` shows no relevant commits between issue filing and plan time.

**File:line references re-verified:**
- `agent/reflection_scheduler.py` (whole file) — present, 23,230 bytes, asyncio scheduler with vault-first `_resolve_registry_path()` confirmed
- `models/reflection.py` (whole file) — present, 5,452 bytes, `Reflection` Popoto model with embedded `run_history` capped at 200 confirmed (`_RUN_HISTORY_CAP = 200`)
- `tools/agent_session_scheduler.py` (whole file) — present, 45,481 bytes, `--after <ISO>` one-shot delay confirmed at top-of-file usage docs
- `~/Desktop/Valor/reflections.yaml` — symlinked into `config/reflections.yaml` per recent `d47d5a81` commit (mode now `100644`); 330 lines; all entries use `interval:` seconds (no cron, no `at:`, no `every:` yet)
- `docs/features/reflections.md` — present, 40,095 bytes (substantial, recently updated 2026-05-04)
- `docs/features/reflections-dashboard.md` — present, 3,243 bytes
- `docs/features/agent-session-scheduling.md` — present, 7,574 bytes

**Cited sibling issues/PRs re-checked:**
- #1249 (Ingest docs/* into memory) — open; will inherit the new schema when this lands
- #748, #967, #933, #991, #978 — all closed/merged; precedents for Reflection refactors

**Commits on main since issue was filed:** None at plan time (baseline is the merge commit immediately preceding plan).

**Active plans in `docs/plans/` overlapping this area:**
- `daily-reflections-unification.md`, `reflections-modular.md`, `reflections-dashboard.md`, `reflections-quality-pass.md`, `reflections-dead-import.md`, `per-project-audit-reflections.md` — all touch the same surface but at different layers (dashboard rendering, modularization, daily-cluster consolidation). None of them tackles the four-system unification.
- **Specific overlap with `reflections-modular.md`** (status: Ready, owner: Valor): that plan moves each callable into its own file under `reflections/{group}/` and uses YAML `group:` as single source of truth. **This plan must not regress that work.** Concretely: any registry-schema change here must keep `group:` working; the per-file callable layout is unchanged.
- **Specific overlap with `daily-reflections-unification.md`** (status: Ready): consolidates daily reflections per-project. Schedule grammar adopted here must not invalidate that plan's `interval: 86400` declarations — the migration step rewrites them to `every: 1d` losslessly.

**Notes:** No drift. Plan's premises are intact.

## Prior Art

Searched closed issues and merged PRs for related work:

```
gh pr list --state merged --search "reflection scheduler" --limit 10
gh issue list --state closed --search "scheduler unify" --limit 10
gh pr list --state merged --search "agent_session_scheduler" --limit 10
```

- **PR #967** (merged) — extracted the 3,086-line reflections monolith into the `reflections/` package. Precedent for surgical Reflection package refactors. Succeeded.
- **PR #933** (merged) — "reflections quality pass — scheduler, model split, field conventions". Established the model/scheduler split this plan extends. Succeeded.
- **PR #991** (merged) — `{subject}-{verb}` naming standard for reflections. Established naming convention to honor when adding new reflections via the unified API.
- **PR #1187** (merged) — added `projects: list[dict]` to `Reflection.run_history` for per-project audits. Precedent for evolving `run_history` shape additively.
- **No prior issue or PR has attempted to unify `/loop`, `/schedule`, `agent_session_scheduler`, and the Reflection scheduler.** This is a greenfield consolidation.

## Research

**Queries used:**
- "fazm routines cron_jobs cron_runs sqlite mediar-ai" (cited in issue)
- "popoto redis orm time-series capped collection patterns" (for run-history shape)
- "croniter python timezone-aware next_run" (for cron expression evaluation)

**Key findings:**
- **fazm prior art** ([github.com/mediar-ai/fazm/blob/main/CLAUDE.md](https://github.com/mediar-ai/fazm/blob/main/CLAUDE.md)) — uses two SQLite tables: `cron_jobs` (definitions) and `cron_runs` (history), polled every 60s by a launchd job. Run output is threaded back into chat history under `taskId="routine-<id>"`. The `cron:` / `every:` / `at:` triplet is their canonical schedule grammar. **Informs:** schedule grammar adoption (Q2), split-model decision (Q1), output-threading policy (Q5).
- **`croniter`** (PyPI) — battle-tested cron expression evaluator with timezone support via `pytz` or `zoneinfo`. Stable since 2010; widely used in Airflow, Celery Beat, etc. **Informs:** cron next-run computation in `ReflectionEntry.next_due()`.
- **Popoto patterns in this repo** — `models/agent_session.py` and `models/memory.py` show that splitting "definition" and "history" into separate models is the established pattern when history can grow unbounded (Memory has thousands of records, AgentSession lifecycle history is queried independently). The 200-cap on `run_history` already shows we hit the embedded-list ceiling for high-frequency reflections (e.g. `analytics-rollup` runs daily — 200 days of history, but `circuit-health-gate` at 60s would lose a day in 200 minutes).

Memory saves issued: one for each finding above (importance 5.0, source agent).

## Architectural Impact

This change touches the worker's recurring-task subsystem and adds an MCP surface. Specifically:

- **New dependencies:** `croniter` (PyPI) for cron expression parsing. No new runtime services.
- **Interface changes:**
  - `Reflection` Popoto model gains: `schedule` (string, replaces `interval`), `output_sink` (string), `failure_count_consecutive` (int), `retry_policy` (dict), `cost_usd_total` (float), `tokens_input_total` (int), `tokens_output_total` (int).
  - New `ReflectionRun` Popoto model holds per-run history (replaces embedded `run_history`).
  - New `mcp_servers/reflections_server.py` MCP server with tools `reflections_create / list / update / remove / runs / pause / resume`.
  - `tools/agent_session_scheduler.py` `--after <ISO>` becomes a thin wrapper that calls `Reflection.create(schedule="at:<ISO>", execution_type="agent", ...)` for one-shot delayed sessions; recurrence-related flags (none currently) are not added.
- **Coupling:**
  - **Decreases** between `agent/reflection_scheduler.py` and `tools/agent_session_scheduler.py` (one delegates to the other).
  - **Increases** slightly between `mcp_servers/` and `models/reflection.py` (new direct import), but isolated and tested.
- **Data ownership:** All recurring-task state collapses into `Reflection` + `ReflectionRun`. `AgentSession` no longer carries scheduling metadata for one-shot delayed sessions (that lives on the Reflection record now).
- **Reversibility:** The migration is one-shot (per `feedback_no_parallel_migrations`). Rollback within 24h is achievable by reverting the migration script and the schema; rollback after a week of accumulated `ReflectionRun` data requires a forward fix, not a revert.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer.

**Interactions:**
- PM check-ins: 2-3 (architecture-question resolution, migration cutover sign-off, MCP surface review)
- Review rounds: 2+ (model schema, scheduler logic, MCP integration, dashboard surface)

This is a structural refactor with eight committed architectural choices and a one-shot migration of `~/Desktop/Valor/reflections.yaml`. Scope is bounded by the eight architecture questions; communication overhead dominates over coding time.

## Architecture Decisions (8 Questions)

This is the load-bearing section. The issue surfaces eight uncommitted architectural choices; each gets a chosen path with rationale below. The Solution and Tasks sections downstream commit to these.

### Q1: Persistence shape — single model or split?

**Decision:** **Split.** `Reflection` (definition + last-run summary) + `ReflectionRun` (per-run history, unbounded).

**Rationale:**
- The 200-cap on embedded `run_history` already loses data for high-frequency reflections (e.g. `circuit-health-gate` at 60s burns through 200 entries in 3.3 hours — historical post-mortems are impossible).
- fazm's `cron_jobs` + `cron_runs` split is proven prior art for the exact same problem.
- Popoto's `KeyField` + filtered queries (`ReflectionRun.query.filter(name=X, timestamp__gte=Y)`) handle "all runs of reflection X in the last 30 days" efficiently — exactly the dashboard-usability complaint.
- `Reflection.last_run_summary` (a small dict of {ran_at, status, duration, error}) stays embedded for fast dashboard reads — the dashboard never needs full history per-row.

**Implementation guard:** `ReflectionRun` records get a TTL (`Meta.ttl = 86400 * 90`, 90 days) to bound Redis growth. Cleanup happens automatically via Redis expiration, not via `cleanup_expired()` scans.

**Cycle-3 ripple — `ui/data/reflections.py` must be updated in the same PR:** the dashboard reader currently calls `state.run_history` in three places (verified at `ui/data/reflections.py:129` for `has_history`, `:257` for `get_run_history`, `:297` for `get_run_detail`). Removing the embedded `run_history` field without updating these reads would break the dashboard at the moment the PR lands. Disposition: **option (b) — update `ui/data/reflections.py` in the same PR** (chosen over option (a) "keep `run_history` and just deprecate writes" because deprecation leaves dead state on every Reflection record, contradicting the project's NO LEGACY CODE TOLERANCE principle, and over option (c) "compat shim" because a shim that "should never need to run" is a smell per the prevention-over-cleanup feedback). Specifically:
- `_build_entry()` line 129: `has_history` becomes `bool(ReflectionRun.query.filter(name=name, limit=1))` — checks for the existence of any run row, not embedded list truthiness.
- `get_run_history(name, page)` lines 239-277: replace `state.run_history` reads with `ReflectionRun.query.filter(name=name).order_by("-timestamp")` paginated; total_runs becomes the result count.
- `get_run_detail(name, run_index)` lines 280-306: replace `state.run_history[run_index]` with `ReflectionRun.query.filter(name=name).order_by("timestamp")[run_index]`.
- Reader signature stays the same; callers (`ui/routes/reflections.py`) are unchanged.
The dashboard task (Task 9) gains this responsibility; integration test `tests/integration/test_dashboard_reflections.py` gets a new case asserting the dashboard correctly reads from `ReflectionRun` rows.

### Q2: Schedule grammar — adopt fazm's triplet verbatim, extend, or custom?

**Decision:** **Adopt fazm's `cron:` / `every:` / `at:` triplet verbatim. No extensions in this scope.**

**Rationale:**
- Adopting verbatim minimizes invention. Cron handles cron, `every` handles intervals, `at` handles one-shots — these three cover every existing reflection in `reflections.yaml`.
- Event-driven schedules (`on_event:`, `on_merge:`) are tempting but explicitly **out of scope** (see No-Gos). They are a different abstraction — event triggers, not time triggers — and conflating them would muddle the model.
- `every:` accepts human-readable durations: `every: 60s`, `every: 5m`, `every: 1h`, `every: 1d`. Migration of every existing `interval: N` entry becomes `every: Ns` (lossless).
- `cron:` accepts standard 5-field cron with timezone via `cron_tz:` field: `cron: "0 9 * * *"` + `cron_tz: "America/Los_Angeles"`.
- `at:` accepts ISO-8601 with timezone: `at: "2026-05-05T09:00:00-07:00"`.
- Exactly **one of** `cron:` / `every:` / `at:` must be set per Reflection — validated at registry-load time and at MCP-create time.

**Implementation guard:** Schedule parsing lives in one helper, `agent/reflection_scheduler.py::compute_next_due(schedule_str: str, last_run: float | None) -> float`, called from both the asyncio tick loop and the MCP `reflections_create` validator. Never duplicate the logic.

### Q3: Migration path for `reflections.yaml`

**Decision:** **One-shot migration script run during `/update`. Coexists with running reflections — no wait-for-quiescence loop. Atomic YAML rename + delta-loop `ReflectionRun` backfill. No dual-read window. No legacy compat branch.**

**Rationale:**
- `feedback_no_parallel_migrations` mandates fully cutting over.
- The vault-synced `~/Desktop/Valor/reflections.yaml` is the source of truth, so the migration script writes there. The in-repo symlink picks up the change automatically (per `scripts/update/env_sync.py::sync_reflections_yaml` at `run.py:403`).
- The script is idempotent: running it twice on already-migrated YAML is a no-op.
- Migration mapping is mechanical: every `interval: N` → `every: Ns`. No semantic changes. No reflection is lost.
- Pre-flight: the migration script asserts every entry has a valid post-migration `cron:`/`every:`/`at:` field; if any entry is malformed it aborts before writing.
- The `/update` skill gains **Step 3.65** (immediately after Step 3.6's existing data-migration phase at `run.py:622` and before Step 3.7's binary installs at `run.py:637`, **after Step 3's `uv sync` at line 462**) that invokes the migration script. The migration imports `croniter`, which is added in this PR; placing the step after `uv sync` ensures the dep is installed before the migration's import. The YAML was already synced at Step 1.66 (line 403), so by the time Step 3.65 runs, the canonical file is in place. After a single successful update on each machine, the registry is permanently in the new shape.

**Coexistence-with-running-reflections design (replaces the original "wait 60 s for last_status='running' to clear" approach, which would routinely starve under normal load — long-running reflections like `analytics-rollup` and `docs-auditor` regularly run for several minutes, so the wait would abort the migration on a healthy worker):**

The migration is decomposed into three idempotent phases, each safe to run while reflections are mid-execution:

1. **YAML rewrite (atomic).** Read `~/Desktop/Valor/reflections.yaml`, rewrite every `interval: N` → `every: Ns` in memory, write to a sibling temp file in the same directory, then `os.replace(temp, target)`. POSIX guarantees the rename is atomic; concurrent `load_registry()` reads see either the old or the new full file, never a torn read. **Crucially, the YAML and the model schema are independent surfaces** — a mid-flight reflection's `mark_completed()` writes to `Reflection` Popoto fields whose shape is unchanged by the YAML rewrite, so it cannot conflict with the rewrite.
2. **`run_history` → `ReflectionRun` backfill (delta-loop).** Walk every `Reflection` record. For each entry in `run_history`, compute a stable key `(name, timestamp_unix)` and call `ReflectionRun.get_or_create(key=...)`. If the record already exists, skip. If `Reflection.last_status == "running"` at scan time, **do not clear `run_history` yet** — record the reflection's name in a sidecar set `reflections:migration:pending_clear` (Popoto-managed; never raw Redis per the project's no-raw-Redis-on-Popoto-keys invariant). After the loop, walk the sidecar set and for each name, re-fetch the `Reflection` record; if `last_status != "running"`, clear `run_history` atomically (Popoto `save()` is atomic per record) and remove the name from the sidecar set. Reflections that are still running at the end of the migration retain their `run_history` (no data loss); the next migration run (next `/update`) cleans them up. The migration is fully reentrant.
3. **Schema validation pass.** After rewrite + backfill, re-load the registry via `load_registry()` and call `compute_next_due()` on every entry. Any parse error aborts the `/update` step (the rewrite phase is already durable; the bridge keeps serving on the new YAML, only schema validation fails loudly).

This design **never blocks on `last_status="running"`** and never aborts on healthy long-running reflections.

**Implementation guard:** The migration script is `scripts/migrate_reflections_yaml.py` and is invoked from `scripts/update/run.py` **Step 3.65** (immediately after Step 3.6's existing data-migration phase at line 622 and before Step 3.7's binary installs at line 637). The insertion point is chosen because (a) it runs **after Step 3's `uv sync` at line 462**, so the newly-added `croniter` dependency is installed before the migration imports it (cycle-2 blocker fix); (b) the YAML symlink was freshly synced from the vault at Step 1.66 (line 403), so the canonical file is already in place; (c) it precedes Step 5's service-restart logic at line 887, so the worker restarts onto migrated state; (d) Step 1.67 (the cycle-2 draft's slot) was wrong because it ran before `uv sync` and would ImportError on `croniter`; (e) Step 4.7 is already occupied by the sdlc-tool wrapper validation gate (`run.py:839`). On migration failure, the update halts and the bridge keeps serving on the previously-validated config (the YAML temp-file + atomic-rename pattern means a failed rewrite never leaves a partial file behind).

### Q4: `/loop` and `/schedule` collapse

**Decision:** **Shadow with first-party MCP tools. Do NOT actively redirect harness skills.**

**Rationale:**
- `/loop` and `/schedule` live in the Claude Code harness (`~/Library/Application Support/Claude/.../skills/`); we cannot edit or delete them.
- Building thin wrappers in our skill space (e.g., `.claude/skills/loop/`) that override harness skills risks fragility — harness updates could clash.
- Instead, the MCP surface (Q7) gives the agent **first-party** tools (`reflections_create / list / update / remove / runs`) it can prefer over the harness skills. The agent's persona and skill-selection guidance is updated to nudge "use `reflections_create` over `/loop` / `/schedule` when the work should persist on this machine."
- `docs/features/reflections.md` documents the harness-skill fallback for cases where ephemeral harness-side state is genuinely desired (one-off self-pacing during a single conversation).

**Implementation guard:** No skill files under `.claude/skills/loop/` or `.claude/skills/schedule/` are added or deleted. The decision lives in (a) the MCP server's tool descriptions making first-party reflections the obvious choice, and (b) a single new docs section in `docs/features/reflections.md` that explicitly contrasts in-repo Reflections vs. harness `/loop` / `/schedule`.

### Q5: Run-output policy

**Decision:** **Per-reflection `output_sink:` config, with four sink kinds:**

| Sink | Format | Where it goes |
|------|--------|---------------|
| `log_only` | (no extra delivery) | `logs/worker.log` only — default for utility reflections |
| `dashboard_only` | (no extra delivery) | Surfaced in `dashboard.json` reflection summary |
| `memory:<importance>` | Memory record at importance level | `Memory` Popoto model, importance defaults to 5.0 if unspecified |
| `telegram:<chat>` | Telegram message | `chat` is a project key (resolved via `projects.json`) or a literal chat name; bridge delivers via `Dev: Valor`-style routing |

**Rationale:**
- The fazm "thread under synthetic taskId" pattern is one specific case of `telegram:` — making it general avoids hardcoding fazm's UX choice.
- `system-health-digest` (currently disabled per recent reflection-disable churn) already implies `telegram:Dev: Valor`. Making this declarative removes the implicit destination.
- Memory-as-output covers the `daily-reflections-unification.md` plan's per-project-recap need without invention.
- Default for unmigrated reflections is `log_only` — preserves current behavior on cutover.

**Implementation guard:** Output sink resolution is in one helper, `agent/reflection_output.py::deliver(reflection: Reflection, run: ReflectionRun, output: str | dict) -> None`. Each sink kind is a small handler. Telegram delivery uses the existing Redis outbox (does NOT call Telegram directly from the scheduler).

### Q6: Failure tracking and dead letter

**Decision:** **Extend `Reflection` with `failure_count_consecutive`, `retry_policy`, `paused_until`. Dead-letter sink is a Memory record at importance 7.0 (project-level learning).**

**Rationale:**
- `failure_count_consecutive` is a counter that increments on consecutive errors and resets on first success. Dashboard surfaces this prominently.
- `retry_policy` is `{"max_retries": int, "backoff_seconds": int, "max_consecutive_failures_before_pause": int}` with defaults `{3, 60, 5}`.
- After `max_consecutive_failures_before_pause` consecutive failures, the scheduler:
  1. Sets `paused_until = now + 86400` (24h auto-pause)
  2. Saves a Memory record with `importance=7.0`, `category="correction"`, content `"Reflection {name} disabled: {N} consecutive failures, last error: {err}"`
  3. Skips this reflection until `paused_until` passes or an operator clears it via MCP `reflections_resume`.
- We deliberately do NOT auto-create a GitHub issue or Redis stream — Memory is the canonical project-learning sink, and the dashboard already surfaces failures.

**Implementation guard:** `paused_until` is a Unix float timestamp. Scheduler tick checks `paused_until > time.time()` BEFORE checking `next_due` — paused reflections are entirely skipped, not stuck in a "due but failing" loop. MCP `reflections_resume(name)` sets `paused_until = 0` and `failure_count_consecutive = 0`.

### Q7: MCP tools surface

**Decision:** **New `mcp_servers/reflections_server.py` exposes seven tools, with auth grounded in env primitives the SDK client already injects (no invented helpers):**

| Tool | Action | Authorization |
|------|--------|---------------|
| `reflections_create` | Create a new Reflection | Any session (creator recorded in `Reflection.created_by_session_id`) |
| `reflections_list` | List all Reflections, optionally filtered by group/status | Any session (read-only) |
| `reflections_get` | Get one Reflection's full state | Any session (read-only) |
| `reflections_update` | Update an existing Reflection's schedule/sink | Only creator session OR registry-source caller |
| `reflections_remove` | Delete a Reflection (and its history) | Only creator session OR registry-source caller |
| `reflections_runs` | Query `ReflectionRun` history for a reflection | Any session (read-only) |
| `reflections_pause` / `reflections_resume` | Toggle `paused_until` | Only creator session OR registry-source caller |

**Rationale:**
- Mirrors fazm's `routines_*` surface so cross-tool muscle memory transfers.
- The "creator OR registry-source" auth model is the lightest workable rule — registry-loaded reflections (those declared in `reflections.yaml`) are mutable only by an out-of-band caller that operates on the YAML file directly (the migration script, the worker on registry reload, or a human running `python -m mcp_servers.reflections_server` from a shell with no `VALOR_SESSION_ID` in env). Otherwise the session that created a reflection owns it.
- The dashboard at `localhost:8500` reads via the same MCP tools (over HTTP) so we don't have two read paths. Writes from the dashboard are out of scope — dashboard remains read-only in this iteration.

**Auth implementation (concrete; no invented helpers):**

The MCP server resolves the calling session's identity from the env primitives that `agent/sdk_client.py:1380-1385` already injects when spawning a Claude Code subprocess:

- `VALOR_SESSION_ID` — the bridge-level session id (always present for bridge-spawned sessions)
- `AGENT_SESSION_ID` — the canonical AgentSession FK (`agt_xxx`); set for all sessions tracked by the worker

The auth check is a small function in `mcp_servers/reflections_server.py`:

```python
def _caller_id() -> str | None:
    """Return the calling session's identity, or None for registry-source callers
    (the migration script, scheduler reload, or a shell invocation outside of any
    Claude Code session)."""
    return os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")

def _can_mutate(reflection: Reflection) -> bool:
    caller = _caller_id()
    if caller is None:
        # No session context — this is the migration script, scheduler reload,
        # or a direct CLI invocation. Allowed to mutate registry-loaded reflections.
        return True
    return caller == reflection.created_by_session_id
```

This grounds the rule entirely in primitives that already exist in the codebase. There is **no `session.is_root_operator()` method** — that name was a stand-in in the previous draft and is removed. The "root operator" concept collapses to "called from a context where neither env var is set," which is exactly what migration scripts and direct CLI invocations look like. (`AGENT_SESSION_ID` is preferred when present because it is the canonical AgentSession FK; `VALOR_SESSION_ID` is the bridge-level fallback for sessions that pre-date the agent_session_id rollout.)

**Implementation guard:** `Reflection.created_by_session_id` defaults to `None` for registry-loaded reflections (i.e., reflections from `reflections.yaml` have no creator session). The `_can_mutate` function rejects all agent-session callers for these (`caller != None`, and `None == created_by_session_id` only when caller is also `None`), so only the no-env-var path can mutate them — preserving the "registry-loaded reflections are sacred to YAML" invariant. Tests assert: (a) an agent session cannot remove a registry-loaded reflection, (b) an agent session can edit/remove a reflection it created, (c) a no-env-var caller can edit any reflection, (d) a different agent session (caller != created_by_session_id) is blocked.

### Q8: Cost accounting and analytics

**Decision:** **Capture per-run on `ReflectionRun`: `cost_usd`, `input_tokens`, `output_tokens`, `duration_ms`. Roll up daily totals onto `Reflection.cost_usd_total` etc. Feed into the existing `analytics-rollup` reflection.**

**Rationale:**
- Per-run capture is cheap (one extra field set on completion) and gives us the dashboard's "what did this run cost?" view.
- Daily totals on `Reflection` are derived (sum of last 24h of `ReflectionRun`) and computed by the `analytics-rollup` reflection itself when it runs. No double-bookkeeping.
- For function-type reflections, cost is 0 (Python callable, no LLM tokens). For agent-type, cost comes from the AgentSession's existing `cost_usd` and token fields (already captured per #983).

**Implementation guard:** When the executor finishes an agent-type reflection, it reads the spawned `AgentSession.cost_usd` / `tokens_input` / `tokens_output` and writes them onto the `ReflectionRun` row. There is exactly one source of truth per run; rollups read, never re-compute.

### Q9 (Bonus): Bridge watchdog exception

**Confirmed:** The bridge watchdog (`monitoring/bridge_watchdog.py`, separate launchd `com.valor.bridge-watchdog`) MUST stay external. It monitors the worker process; it cannot live inside the worker's own scheduler. `docs/features/reflections.md` already documents this; this plan reaffirms but does not change.

**No other "scheduler-of-the-scheduler" tasks exist.** Confirmed by grepping launchd plists in `~/Library/LaunchAgents/com.valor.*` — only `com.valor.bridge`, `com.valor.bridge-watchdog`, `com.valor.worker`, `com.valor.web-ui`, and `com.valor.update` exist; none of them schedule recurring AI work.

## Spike Results

No spikes needed. The architecture questions all resolve via prior art (fazm, this repo's own model patterns) and confirmed file-system inspection. No verifiable assumption requires prototyping.

## Data Flow

### Runtime path: scheduler tick → reflection execution → output delivery

1. **Scheduler tick** (`ReflectionScheduler._run_loop` in `agent/reflection_scheduler.py`, every 60s)
2. **Load registry** — `load_registry()` reads `~/Desktop/Valor/reflections.yaml` (or vault fallback) — already vault-aware
3. **For each entry**: query `Reflection.get_or_create(name=...)`
4. **Compute due**: `compute_next_due(reflection.schedule, reflection.ran_at)` — using `croniter` for `cron:`, simple add for `every:`, fixed timestamp for `at:`
5. **Skip checks** in order: `enabled=False` → skip; `paused_until > now` → skip with `last_status="skipped"`; `next_due > now` → skip silently
6. **Execute**:
   - **Function type**: call the Python callable in-process; capture exceptions
   - **Agent type**: spawn an `AgentSession` via the existing executor path; wait for completion
7. **Write `ReflectionRun`** record with `{name, timestamp, status, duration_ms, cost_usd, tokens_input, tokens_output, error?, output_summary?}`
8. **Update `Reflection`**: `mark_completed(...)` updates `last_run_summary`, `failure_count_consecutive` (reset on success / increment on error), `paused_until` if threshold breached
9. **Deliver output**: `agent/reflection_output.py::deliver(reflection, run, output)` routes to the configured `output_sink`
10. **Done** — next tick in 60s

### MCP path: agent calls `reflections_create`

1. **Agent calls** `reflections_create(name, schedule, execution_type, callable_or_command, output_sink, ...)`
2. **MCP server validates**: schedule grammar via `compute_next_due(...)`, callable resolves, output_sink format
3. **Writes `Reflection`** record with `created_by_session_id=<calling session>`
4. **Returns** the new Reflection's name + first computed `next_due`
5. **Next scheduler tick picks it up** — no kick required (MCP write is durable; tick reads on next iteration)

### Migration path: `/update` first-run on a machine

1. **`scripts/update/run.py` Step 3.65** runs `scripts/migrate_reflections_yaml.py` immediately after Step 3.6's existing data migrations (line 622) and before Step 3.7's binary installs (line 637). It runs **after Step 3's `uv sync`** (line 462) so the newly-added `croniter` dependency is installed before the migration imports it. The YAML was already synced at Step 1.66 (line 403), so the migration operates on the freshly-synced canonical file.
2. **Phase 1 — atomic YAML rewrite.** Read `~/Desktop/Valor/reflections.yaml`, rewrite every `interval: N` → `every: Ns` in memory, write to a sibling temp file in the same directory, then `os.replace(temp, target)`. POSIX-atomic; concurrent readers see either the old or new full file.
3. **Phase 2 — `run_history` → `ReflectionRun` delta-loop backfill.** Walk every `Reflection` record. For each `run_history` entry, compute key `(name, timestamp_unix)` and call `ReflectionRun.get_or_create(...)`. If `last_status == "running"` at scan time, record the name in `reflections:migration:pending_clear` (Popoto-managed sidecar set) and skip the clear step. After the scan, walk the sidecar; for each name, re-fetch the Reflection and clear `run_history` only if it has stopped running. Reflections still running at exit are handled on the next migration run (no data loss).
4. **Phase 3 — schema validation.** Re-load the registry via `load_registry()` and call `compute_next_due()` on every entry. Any parse error aborts and surfaces a loud failure to `/update`.
5. **Scheduler restart** picks up new shape; `Reflection` records carry forward unchanged.

The migration **never blocks on `last_status="running"`** and is fully reentrant — running it twice on the same machine is a no-op the second time.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Identify `except Exception: pass` blocks in `agent/reflection_scheduler.py` and `models/reflection.py` — each must have a corresponding test asserting observable behavior. Specifically: `_normalize_run_history()` swallowing on type mismatch must log a warning under test.
- [ ] New `agent/reflection_output.py` sink handlers: each handler's exception path must log at WARNING level with the reflection name and sink kind. Tests assert log content via `caplog`.
- [ ] MCP server tool error paths: each tool returns a structured error dict (`{"error": str, "code": str}`) on failure; tests assert this rather than letting exceptions propagate raw to the agent.

### Empty/Invalid Input Handling
- [ ] `compute_next_due("")` → ValueError with message including the bad input
- [ ] `compute_next_due("cron: not-a-cron-expr")` → ValueError, not silent fallback to "every 60s"
- [ ] `reflections_create(schedule="")` returns `{"error": "schedule is required", ...}` without writing
- [ ] `Reflection.create(...)` with `output_sink="telegram:nonexistent-project"` is rejected at schema-validation time, not at run-time

### Error State Rendering
- [ ] Dashboard reflection rows show `failure_count_consecutive` and `paused_until` prominently when nonzero — tests assert these render in `dashboard.json`
- [ ] When a reflection's `output_sink="telegram:..."` and the bridge outbox is unreachable, the run is still recorded as `success` (the work happened; only the delivery failed); a separate WARNING log captures the delivery failure. Test asserts both.

## Test Impact

- [ ] `tests/unit/test_reflection_model.py` — UPDATE: rewrite `Reflection` schema tests for new fields (`schedule`, `output_sink`, `failure_count_consecutive`, `retry_policy`, `paused_until`, `cost_usd_total`); delete tests for `interval`, embedded `run_history`
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: replace interval-only tests with cron/every/at coverage via `compute_next_due()`
- [ ] `tests/unit/test_reflection_runner.py` — REPLACE if it exists (PR #967 may have removed `ReflectionRunner` already; check in build phase)
- [ ] `tests/integration/test_reflections_yaml.py` — UPDATE: assert every entry validates against new grammar; assert migration script is idempotent
- [ ] `tests/unit/test_agent_session_scheduler.py` — UPDATE: `--after` path now writes a Reflection record, not a raw delayed AgentSession; one-shot delay test asserts Reflection schema, not AgentSession schema
- [ ] `tests/integration/test_dashboard_reflections.py` — UPDATE: dashboard JSON assertions cover new fields (`failure_count_consecutive`, `paused_until`, `cost_usd_total`); add a new case asserting `get_run_history()` and `get_run_detail()` in `ui/data/reflections.py` correctly read from `ReflectionRun` Popoto rows after the embedded `run_history` is removed (cycle-3 ripple — verifies the dashboard doesn't break when the field disappears)
- [ ] `tests/unit/test_ui_data_reflections.py` — UPDATE (or REPLACE if absent): assert `_build_entry`, `get_run_history`, and `get_run_detail` no longer reference `state.run_history`; assert paginated reads come from `ReflectionRun.query.filter(name=...)` (cycle-3 ripple)
- [ ] `tests/integration/test_mcp_reflections.py` — REPLACE: new test file; validates all 7 MCP tools and their auth model
- [ ] `tests/integration/test_reflections_migration.py` — REPLACE: new test file; runs the migration script on a fixture YAML and asserts idempotence + content correctness

## Rabbit Holes

- **Building a fancy DSL for schedules.** Adopting fazm's triplet verbatim is the discipline. Resist `every: every-other-tuesday` style extensions — they pull the team into an in-house cron parser.
- **Auto-creating GitHub issues on dead-letter.** Tempting (the SDLC already understands issues), but issue churn from flaky reflections would pollute the backlog. Memory records are the discipline.
- **Replacing the harness `/loop` / `/schedule` skills.** They live in the harness; we cannot edit them. Shadowing via MCP is the discipline (Q4). Don't try to delete or rebind them.
- **Adding event-triggered reflections (`on_event:`, `on_merge:`).** Different abstraction (event triggers, not time triggers). Out of scope; track separately if needed.
- **Cross-machine reflections.** A reflection on machine A "polling for state on machine B" is a coordination problem dressed up as a scheduler problem. Out of scope.
- **Bringing `agent_session_scheduler`'s SDLC issue dispatch under Reflections.** The scheduling axis (one-shot delayed AgentSession) collapses cleanly. The "SDLC orchestration of an issue" axis is bigger and unrelated. Keep `agent_session_scheduler` as the SDLC orchestration tool; it just delegates *scheduling* to Reflection.

## Risks

### Risk 1: Migration breaks an in-flight reflection mid-run
**Impact:** A reflection running at the moment of `/update`'s migration step has `last_status="running"` and a long-running execution in flight. The naive design (wait up to 60s for `last_status="running"` to clear, abort otherwise) would routinely starve under normal load — `analytics-rollup`, `docs-auditor`, and the per-project audit reflections regularly run for several minutes. A single long-running reflection would block every machine's `/update` run.
**Mitigation:** The migration script does NOT wait for in-flight reflections to clear. The design coexists with running reflections in three idempotent phases (see Q3 for the full design):
1. **Atomic YAML rewrite** via temp file + `os.replace()`. The YAML and the model schema are independent surfaces; mid-flight `mark_completed()` writes to Popoto fields whose shape is unchanged by the YAML rewrite.
2. **Delta-loop `run_history` → `ReflectionRun` backfill.** Walk every Reflection; for each `run_history` entry, `ReflectionRun.get_or_create((name, timestamp))`. If `Reflection.last_status == "running"` at scan time, do not clear `run_history` — record the name in `reflections:migration:pending_clear` (a Popoto-managed sidecar set, never raw Redis). After the scan, walk the sidecar and clear `run_history` only on reflections that have since stopped running. Reflections still running at exit retain `run_history` and are handled on the next migration run.
3. **Schema validation** by re-loading the registry through `compute_next_due()`.
The migration is fully reentrant — running it twice on the same machine is a no-op the second time. The model's `mark_completed` is unchanged-shape so even mid-flight runs complete safely. Documented in `scripts/migrate_reflections_yaml.py` docstring.

### Risk 2: `croniter` introduces a new dependency the update script must propagate
**Impact:** Update on a machine where `croniter` isn't installed yet fails at scheduler restart with `ImportError`. **Cycle-3 sub-impact:** the migration script also imports `croniter` (Phase 3 schema validation calls `compute_next_due`), so the migration step itself must run AFTER `uv sync`.
**Mitigation:** `croniter` is added to `pyproject.toml`. The `/update` skill runs `uv sync` (or `pip install -e .`) at Step 3 (`run.py:462`) before any code that imports `croniter`. The migration step is placed at **Step 3.65** (after Step 3.6 data-migrations, before Step 3.7 binary installs) so the migration's import of `croniter` is guaranteed to succeed. The worker is restarted at Step 5 (line 887), also after `uv sync`. Update script Step 4.6 (config validation) extended to import-test `croniter` before continuing as belt-and-suspenders.

### Risk 3: `ReflectionRun` Redis growth is unbounded if TTL is misconfigured
**Impact:** A high-frequency reflection (`circuit-health-gate` at 60s) generates 1,440 `ReflectionRun` records per day. Without TTL, Redis grows ~500KB/day per reflection. Across 33 reflections this is meaningful in a year.
**Mitigation:** `ReflectionRun.Meta.ttl = 86400 * 90` (90 days). Tests assert TTL is set on every `ReflectionRun.create(...)`. A weekly reflection (`reflection-runs-cleanup-watchdog`) verifies orphan rows aren't accumulating (defense in depth).

### Risk 4: MCP authorization model lets any session edit registry-loaded reflections
**Impact:** An agent session calls `reflections_remove("daily-report-and-notify")` and the registry-loaded reflection is gone until the next scheduler restart re-creates it from YAML. Hidden state divergence from `reflections.yaml`.
**Mitigation:** Registry-loaded reflections have `created_by_session_id=None`. The MCP auth check `_can_mutate(reflection)` resolves the caller via `os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")` (the env primitives the SDK client injects at `agent/sdk_client.py:1380-1385`). For an agent-session caller, `caller != None` and `caller != reflection.created_by_session_id` (which is `None`), so the check rejects. Only a no-env-var caller (the migration script, scheduler reload, or a direct shell invocation) can mutate registry-loaded reflections. Tests assert this rule explicitly using `monkeypatch.setenv`/`monkeypatch.delenv` to drive the env primitives.

### Risk 5: Splitting `Reflection` and `ReflectionRun` requires data migration of existing run_history
**Impact:** Existing `Reflection.run_history` lists (up to 200 records each, ~33 reflections) need to be backfilled into `ReflectionRun` rows or accepted as lost.
**Mitigation:** The migration script (Q3) also walks every existing `Reflection` record and creates `ReflectionRun` rows from `run_history`, then clears `run_history`. Migration is idempotent: if `ReflectionRun` rows already exist for a given (name, timestamp), skip. Run history is preserved.

## Race Conditions

### Race 1: Migration writes YAML while scheduler tick reads it
**Location:** `agent/reflection_scheduler.py::load_registry()` vs `scripts/migrate_reflections_yaml.py`
**Trigger:** `/update` runs migration at the same instant as a scheduler tick
**Data prerequisite:** YAML file is in a self-consistent state when read
**State prerequisite:** No partial-write visible
**Mitigation:** Migration writes to a temp file then atomically renames (`os.rename`). Atomic on POSIX. Reader sees either old or new full file, never mid-write.

### Race 2: Two scheduler ticks fire the same reflection (worker restart edge case)
**Location:** `agent/reflection_scheduler.py::_run_loop`
**Trigger:** Worker crashes mid-run; restart happens; reflection's `last_status="running"` is stale; new tick sees it as in-flight and skips, but `next_due` is in the past → repeat skip forever
**Data prerequisite:** `last_status` accurately reflects current execution state
**State prerequisite:** A stale "running" status is detected and cleared
**Mitigation:** On scheduler startup, scan for `Reflection.last_status == "running"` records and check `ran_at` age. Anything older than the per-reflection `timeout` (default 30 min function, 1 hour agent) gets force-marked `error` with `last_error="stale running status cleared on worker restart"`. This logic exists in PR-#1187-era code; preserve it.

### Race 3: Concurrent MCP `reflections_update` and scheduler tick fire same reflection
**Location:** `mcp_servers/reflections_server.py::update_reflection` vs `agent/reflection_scheduler.py::_run_loop`
**Trigger:** Agent updates schedule mid-tick; tick has already computed `next_due` from old schedule
**Data prerequisite:** Schedule used for execution matches the schedule recorded at the time the run was scheduled
**State prerequisite:** Updates take effect on the *next* tick, not the in-flight one
**Mitigation:** Scheduler tick reads the entire `Reflection` record at the start of the iteration; uses that snapshot for the rest of the iteration. Update writes are atomic (Popoto save). Worst case: an in-flight tick uses the previous schedule once, then the next tick uses the new schedule. Acceptable; documented.

## No-Gos (Out of Scope)

- **Event-triggered reflections** (`on_event:`, `on_merge:`, `on_pr_close:`). Different abstraction; defer to a separate plan.
- **Cross-machine reflections.** A reflection on machine A polling state on machine B is a distributed-systems problem dressed up as a scheduler problem.
- **Replacing harness `/loop` and `/schedule` skills.** They live in the harness; we shadow via MCP only (Q4).
- **GitHub-issue dead-letter sink.** Memory records are the discipline (Q6).
- **Dashboard write surface for reflections.** Dashboard stays read-only this iteration; mutation is via MCP only (Q7).
- **Migrating `monitoring/bridge_watchdog.py` into the scheduler.** Confirmed external (Q9).
- **Per-reflection LLM model selection.** All agent-type reflections use the worker's default model; no per-reflection override.
- **Redis-stream-based dead-letter queue.** Memory record at importance 7.0 is the discipline (Q6).

## Update System

The `/update` skill needs three changes:

1. **Add `croniter` to `pyproject.toml`** — `uv sync` picks it up on next update.
2. **Add Step 3.65 to `scripts/update/run.py`** — invoke `scripts/migrate_reflections_yaml.py` immediately after Step 3.6's existing data-migration phase (currently at `run.py:622`) and before Step 3.7's binary installs (`run.py:637`). Halt update on migration error. **Insertion point rationale (cycle-3 fix):** the cycle-2 draft proposed Step 1.67 (right after `sync_reflections_yaml` at line 403), but Step 1.67 runs **before** Step 3's `uv sync` (line 462). The migration imports `croniter`, which this PR adds to `pyproject.toml` — running migration before `uv sync` would `ImportError`. Step 3.65 is the correct slot because (a) it runs **after `uv sync`**, so `croniter` is installed before import; (b) the YAML was synced at Step 1.66 (line 403), so the canonical file is already in place; (c) it runs in the same Step 3.x band as the existing data-migration phase (Step 3.6), keeping migrations grouped; (d) it precedes Step 5's service-restart logic (`run.py:887`), so the worker restarts onto migrated state; (e) Step 4.7 is already occupied by the sdlc-tool wrapper validation gate (`run.py:839`). Atomic temp-file + rename means a failed migration leaves no partial file behind, and the bridge keeps serving on the previously-validated YAML if validation aborts the rewrite.
3. **Update `scripts/update/__init__.py`** preflight to import-test `croniter` (the deps-installed assertion).

`docs/features/reflections.md` gains a "Migration Notes" section documenting:
- The one-shot YAML migration and where the script lives.
- That re-running the migration on already-migrated YAML is a no-op.
- That `Reflection.run_history` is migrated into `ReflectionRun` rows, not lost.

The `/update` skill prose itself (`.claude/skills/update/SKILL.md`) does not need changes — it already invokes `scripts/update/run.py` end-to-end.

## Agent Integration

This work introduces a new MCP server. The agent reaches Reflections via:

- **New MCP server**: `mcp_servers/reflections_server.py` exposes seven tools (Q7). **Cycle-3 fix:** the cycle-2 draft said this server is "registered in `.mcp.json` at the repo root," but `.mcp.json` does not exist in this repo (verified via `ls -la .mcp.json` → "No such file or directory"). The actual MCP registration mechanism is `~/.claude.json`'s `mcpServers` map, self-healed by `scripts/update/mcp_memory.py` at update Step 4.8 (`run.py:861`) under an `fcntl.flock` to coexist with Claude Code's own writes to that file. The plan adds **`scripts/update/mcp_reflections.py`** modeled on `mcp_memory.py` (same lock + atomic-rename pattern) that registers `reflections` under `mcpServers` with `command="python3"` and `module="mcp_servers.reflections_server"`. It is invoked from a new **Step 4.85** in `run.py` immediately after Step 4.8's memory MCP verification. The Dev session, the PM session, and the Teammate session all inherit the registration via `~/.claude.json` because the harness reads from there at session spawn.
- **Bridge does NOT import reflection code directly.** Bridge stays I/O-only. All scheduling decisions happen in the worker.
- **`tools/agent_session_scheduler.py` `--after <ISO>`** becomes a thin CLI that calls `Reflection.create(schedule="at:<ISO>", execution_type="agent", command=...)`. This keeps the existing CLI users (humans, scripts) working without flag changes; they just write to a new model under the hood.
- **No new `pyproject.toml [project.scripts]` entry needed.** The MCP server is launched as `python3 -m mcp_servers.reflections_server` (the standard MCP module pattern, identical to `mcp_servers.memory_server`); the migration script is invoked by `scripts/update/run.py` as a direct `python` call. Both are launched as `python -m ...` commands, which the existing tooling handles.

**Integration tests:**
- `tests/integration/test_mcp_reflections.py` — agent calls each MCP tool, asserts observable Reflection state changes.
- `tests/integration/test_agent_session_scheduler_after.py` — UPDATE: `--after` path writes a Reflection, not a delayed AgentSession.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` to be the **single source of truth** post-merge: covers the new schema, schedule grammar, output sinks, failure tracking, MCP surface, and the migration. Existing prose for Reflection internals largely stays; the model fields and YAML format sections get rewritten.
- [ ] Update `docs/features/agent-session-scheduling.md` to point to `reflections.md` for the scheduling concern; retain the SDLC-orchestration-specific content.
- [ ] Update `docs/features/reflections-dashboard.md` to cover the new fields and the read path through MCP tools.
- [ ] Update `docs/features/README.md` index to flag `reflections.md` as the unified system entry point.

### External Documentation Site
- No external docs site changes — this repo doesn't publish docs externally.

### Inline Documentation
- [ ] Module docstring on `agent/reflection_scheduler.py` updated to cover schedule grammar and MCP surface.
- [ ] Module docstring on `models/reflection.py` and new `models/reflection_run.py` cover the split-model rationale.
- [ ] Tool descriptions in `mcp_servers/reflections_server.py` follow the established MCP server pattern (one-line summary, full args, example).

## Success Criteria

- [ ] All 8 architecture questions are answered in this plan with chosen path and rationale (DONE — see Architecture Decisions section)
- [ ] `models/reflection.py` carries new fields: `schedule`, `output_sink`, `failure_count_consecutive`, `retry_policy`, `paused_until`, `cost_usd_total`, `tokens_input_total`, `tokens_output_total`
- [ ] New `models/reflection_run.py` (`ReflectionRun` Popoto model) exists with TTL=90 days
- [ ] `agent/reflection_scheduler.py::compute_next_due()` handles `cron:`, `every:`, `at:` grammar; pre-migration `interval:` field is rejected with a clear error
- [ ] `~/Desktop/Valor/reflections.yaml` is migrated to the new grammar via `scripts/migrate_reflections_yaml.py`; idempotent
- [ ] `dashboard.json` exposes `failure_count_consecutive`, `paused_until`, `cost_usd_total` per reflection
- [ ] `mcp_servers/reflections_server.py` exposes 7 MCP tools and is registered in `~/.claude.json`'s `mcpServers` map via `scripts/update/mcp_reflections.py` (modeled on `scripts/update/mcp_memory.py`), invoked from `run.py` Step 4.85
- [ ] `tools/agent_session_scheduler.py --after <ISO>` writes a `Reflection`, not a delayed `AgentSession`
- [ ] Failure-tracking semantics implemented: max 5 consecutive failures pauses for 24h and writes a Memory record at importance 7.0
- [ ] `docs/features/reflections.md` is the single source of truth; sibling docs reconciled
- [ ] All `interval:` references in YAML and code are gone (grep confirms)
- [ ] Tests pass (`pytest tests/`, especially the integration tests listed in Test Impact)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (model-schema)**
  - Name: model-builder
  - Role: Extend `Reflection` and create `ReflectionRun` Popoto model with TTL
  - Agent Type: builder
  - Resume: true

- **Builder (scheduler)**
  - Name: scheduler-builder
  - Role: Implement `compute_next_due()`, schedule-grammar parsing, retry/pause logic in `agent/reflection_scheduler.py`
  - Agent Type: builder
  - Resume: true

- **Builder (output-sinks)**
  - Name: output-builder
  - Role: Implement `agent/reflection_output.py` with four sink handlers
  - Agent Type: builder
  - Resume: true

- **Builder (mcp-server)**
  - Name: mcp-builder
  - Role: Implement `mcp_servers/reflections_server.py` with 7 tools and auth model
  - Agent Type: mcp-specialist
  - Resume: true

- **Builder (migration)**
  - Name: migration-builder
  - Role: Implement `scripts/migrate_reflections_yaml.py` and `scripts/update/run.py` Step 3.65 hook; backfill `ReflectionRun` from existing `run_history` via delta-loop (coexists with running reflections)
  - Agent Type: migration-specialist
  - Resume: true

- **Builder (cli-wrapper)**
  - Name: cli-builder
  - Role: Update `tools/agent_session_scheduler.py --after` to delegate to `Reflection.create(...)`
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: dashboard-builder
  - Role: Surface new Reflection fields in `dashboard.json` (read-only)
  - Agent Type: builder
  - Resume: true

- **Validator (schema)**
  - Name: schema-validator
  - Role: Verify model schema matches plan; verify TTL set; verify Popoto save/load round-trips
  - Agent Type: validator
  - Resume: true

- **Validator (scheduler)**
  - Name: scheduler-validator
  - Role: Verify `compute_next_due` covers cron/every/at; verify retry/pause logic; verify race-condition mitigations
  - Agent Type: validator
  - Resume: true

- **Validator (mcp)**
  - Name: mcp-validator
  - Role: Verify all 7 MCP tools work end-to-end; verify auth model
  - Agent Type: validator
  - Resume: true

- **Validator (migration)**
  - Name: migration-validator
  - Role: Verify migration is idempotent; verify run_history backfill; verify update script integration
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-author
  - Role: Author all unit + integration tests per Test Impact section
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-author
  - Role: Update `reflections.md`, `agent-session-scheduling.md`, `reflections-dashboard.md`, `README.md` index
  - Agent Type: documentarian
  - Resume: true

- **Final Validator**
  - Name: lead-validator
  - Role: Run full test suite, verify all success criteria, generate final report
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend Reflection model + create ReflectionRun
- **Task ID**: build-model-schema
- **Depends On**: none
- **Validates**: tests/unit/test_reflection_model.py, tests/unit/test_reflection_run.py (create)
- **Informed By**: Q1 (split model decision), Q6 (failure tracking fields), Q8 (cost fields)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `schedule`, `output_sink`, `failure_count_consecutive`, `retry_policy` (dict), `paused_until`, `cost_usd_total`, `tokens_input_total`, `tokens_output_total`, `created_by_session_id` to `Reflection`
- Create `models/reflection_run.py` with `ReflectionRun` model and `Meta.ttl = 86400 * 90`
- Remove embedded `run_history` from `Reflection`; add `last_run_summary` dict for fast dashboard reads
- Preserve existing `mark_started`, `mark_completed`, `mark_skipped` API surface where shape allows; refactor where shape changes

### 2. Validate model schema
- **Task ID**: validate-model-schema
- **Depends On**: build-model-schema
- **Assigned To**: schema-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all new fields exist on the `Reflection` model
- Verify `ReflectionRun` Meta.ttl is 90 days
- Verify Popoto save/load round-trips for both models without ListField descriptor issues
- Verify `last_run_summary` is a dict, not a ListField

### 3. Implement schedule-grammar parser
- **Task ID**: build-scheduler-grammar
- **Depends On**: build-model-schema
- **Validates**: tests/unit/test_reflection_scheduler.py (update)
- **Informed By**: Q2 (adopt fazm triplet)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `compute_next_due(schedule_str: str, last_run: float | None) -> float` in `agent/reflection_scheduler.py`
- Support `cron:` (with optional `cron_tz:`), `every:` (with `s`/`m`/`h`/`d` suffix), `at:` (ISO-8601)
- Reject pre-migration `interval:` with a clear ValueError
- Add `croniter` to `pyproject.toml`

### 4. Implement retry/pause/dead-letter logic
- **Task ID**: build-scheduler-failure-tracking
- **Depends On**: build-model-schema, build-scheduler-grammar
- **Validates**: tests/unit/test_reflection_scheduler.py (failure-path tests)
- **Informed By**: Q6 (failure tracking + Memory dead-letter)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: false
- On error: increment `failure_count_consecutive`; on success: reset to 0
- After `max_consecutive_failures_before_pause` (default 5): set `paused_until = now + 86400`, save Memory record at importance 7.0, category="correction"
- Tick loop checks `paused_until > now` BEFORE `next_due` check
- On worker restart: scan stale `last_status="running"` and force-clear (preserve PR-#1187-era logic)

### 5. Implement output-sink delivery
- **Task ID**: build-output-sinks
- **Depends On**: build-model-schema
- **Validates**: tests/unit/test_reflection_output.py (create)
- **Informed By**: Q5 (per-reflection sink config)
- **Assigned To**: output-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/reflection_output.py` with `deliver(reflection, run, output)` and four sink handlers
- `log_only` (default), `dashboard_only`, `memory:<importance>`, `telegram:<chat>`
- Telegram delivery via existing Redis outbox (do NOT call Telegram API directly)
- Each handler exception path logs at WARNING with reflection name + sink kind

### 6. Build MCP server
- **Task ID**: build-mcp-server
- **Depends On**: build-model-schema, build-scheduler-grammar
- **Validates**: tests/integration/test_mcp_reflections.py (create)
- **Informed By**: Q7 (7 tools, creator-or-registry-source auth via env primitives)
- **Assigned To**: mcp-builder
- **Agent Type**: mcp-specialist
- **Parallel**: false
- Create `mcp_servers/reflections_server.py` exposing 7 tools: `reflections_create / list / get / update / remove / runs / pause / resume`
- Auth via env primitives the SDK client already injects (`agent/sdk_client.py:1380-1385`):
  - `_caller_id()` returns `os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")`
  - `_can_mutate(reflection)` returns `True` if `caller is None` (registry-source / migration / direct CLI) or `caller == reflection.created_by_session_id`
  - **No `is_root_operator()` helper is created** — the rule is grounded entirely in the existing env primitives
- Registry-loaded reflections (`created_by_session_id=None`) are mutable only by no-env-var callers (migration script, scheduler reload, direct shell)
- **Cycle-3 fix — register via `~/.claude.json`, not `.mcp.json`:** Create `scripts/update/mcp_reflections.py` modeled on `scripts/update/mcp_memory.py` (same `fcntl.flock(LOCK_EX | LOCK_NB)` + retry schedule + atomic-rename + `~/.claude.json.bak` backup pattern). The helper writes a `reflections` entry under `~/.claude.json`'s `mcpServers` map with `command="python3"` and `args=["-m", "mcp_servers.reflections_server"]`. Add a new **Step 4.85** to `scripts/update/run.py` immediately after Step 4.8's memory MCP verification (line 868), invoking the new helper with the same `_mcp_memory_write = config.do_service_restart` write-gating pattern. The repo-root `.mcp.json` referenced in the cycle-2 draft does not exist — verified via `ls -la .mcp.json` returning "No such file or directory."
- Validate schedule grammar at create-time using `compute_next_due()`
- Tests use `monkeypatch.setenv("AGENT_SESSION_ID", ...)` and `monkeypatch.delenv(...)` to drive the auth states explicitly

### 7. Update agent_session_scheduler --after
- **Task ID**: build-cli-wrapper
- **Depends On**: build-model-schema, build-scheduler-grammar
- **Validates**: tests/unit/test_agent_session_scheduler.py (update), tests/integration/test_agent_session_scheduler_after.py
- **Informed By**: Q4 (subsume scheduling axis), Acceptance Criteria
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- `--after <ISO>` path now writes `Reflection.create(schedule=f"at:{iso}", execution_type="agent", ...)`
- Existing flag surface unchanged; users see no behavior change except that `dashboard.json` now shows the scheduled session
- SDLC orchestration concerns (issue dispatch, project-keying) remain in `agent_session_scheduler` — only the scheduling axis collapses

### 8. Implement migration script
- **Task ID**: build-migration
- **Depends On**: build-model-schema, build-scheduler-grammar
- **Validates**: tests/integration/test_reflections_migration.py (create)
- **Informed By**: Q3 (coexists with running reflections), Risk 1 (no wait-for-quiescence)
- **Assigned To**: migration-builder
- **Agent Type**: migration-specialist
- **Parallel**: false
- Create `scripts/migrate_reflections_yaml.py` that:
  - Reads `~/Desktop/Valor/reflections.yaml` (vault path)
  - **Phase 1 (atomic rewrite):** For each entry, rewrites `interval: N` → `every: Ns` in memory; writes a sibling temp file; `os.replace(temp, target)` for atomic POSIX rename. **Does NOT wait for `last_status="running"` to clear** — the YAML and the Popoto record schema are independent surfaces, so mid-flight reflections complete safely.
  - **Phase 2 (delta-loop backfill):** Walk every existing `Reflection`. For each `run_history` entry, `ReflectionRun.get_or_create((name, timestamp))`. If `Reflection.last_status == "running"` at scan time, append the reflection's name to a Popoto-managed sidecar set `reflections:migration:pending_clear` and skip the `run_history` clear. After the scan, walk the sidecar; for each name, re-fetch the Reflection and clear `run_history` only if it has stopped running (atomic Popoto save). Names still in pending state at exit are handled on the next migration run.
  - **Phase 3 (schema validation):** Re-load the registry via `load_registry()`, call `compute_next_due()` on every entry, abort with a clear error message on parse failure.
  - **Idempotent:** detects post-migration shape (every entry already has `cron:`/`every:`/`at:`) and exits cleanly without rewriting; `ReflectionRun.get_or_create` ensures no double-write on re-runs.
  - **No raw Redis:** all sidecar set operations go through Popoto (`Model.query.filter()`, `instance.save()`, etc.), per the project's no-raw-Redis-on-Popoto-keys invariant.
- Add **Step 3.65** to `scripts/update/run.py` (immediately after Step 3.6's data-migration phase at line 622 and before Step 3.7's binary installs at line 637) invoking the script. Step 3.65 runs **after Step 3's `uv sync` at line 462**, so the newly-added `croniter` dependency is installed before the migration imports it (cycle-3 ordering fix). Step 4.7 is already occupied by sdlc-tool wrapper validation; Step 1.67 (the cycle-2 draft slot) was wrong because it ran before `uv sync`.

### 9. Surface new fields in dashboard + repoint `ui/data/reflections.py` reads off embedded `run_history`
- **Task ID**: build-dashboard
- **Depends On**: build-model-schema, build-scheduler-failure-tracking, build-output-sinks
- **Validates**: tests/integration/test_dashboard_reflections.py (update), tests/unit/test_ui_data_reflections.py (update)
- **Informed By**: Q1 cycle-3 ripple — embedded `run_history` is removed; dashboard reader must move to `ReflectionRun` rows in the same PR
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- `dashboard.json` reflection rows expose: `failure_count_consecutive`, `paused_until`, `cost_usd_total`, `output_sink`
- **Update `ui/data/reflections.py` (cycle-3 ripple, mandatory in same PR):**
  - `_build_entry()` line 129: replace `bool(state and isinstance(state.run_history, list) and state.run_history)` with `bool(ReflectionRun.query.filter(name=name, limit=1))`
  - `get_run_history(name, page)` lines 239-277: replace `state.run_history` reads with `ReflectionRun.query.filter(name=name).order_by("-timestamp")` paginated
  - `get_run_detail(name, run_index)` lines 280-306: replace `state.run_history[run_index]` with the indexed `ReflectionRun` row in forward-timestamp order
  - Caller signatures stay the same; `ui/routes/reflections.py` is unchanged
- Dashboard remains read-only this iteration

### 10. Validate scheduler behavior
- **Task ID**: validate-scheduler
- **Depends On**: build-scheduler-grammar, build-scheduler-failure-tracking, build-output-sinks
- **Assigned To**: scheduler-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify cron/every/at parsing (golden tests for each shape, including DST edge case for cron)
- Verify retry/pause/dead-letter writes Memory record at correct importance/category
- Verify race-condition mitigations: stale-running detection, atomic YAML rename, snapshot-per-tick

### 11. Validate MCP server
- **Task ID**: validate-mcp
- **Depends On**: build-mcp-server
- **Assigned To**: mcp-validator
- **Agent Type**: validator
- **Parallel**: false
- Each of 7 MCP tools called end-to-end against a live worker (or mocked Popoto)
- Auth model verified using env-primitive states (driven by `monkeypatch.setenv`/`delenv` on `AGENT_SESSION_ID` and `VALOR_SESSION_ID`):
  - Creator session (env var matches `created_by_session_id`) can edit/remove
  - Different session (env var present but does not match) is blocked
  - No-env-var caller (simulating migration script / direct CLI) can mutate registry-loaded reflections
  - Agent-session caller (env var present) cannot mutate registry-loaded reflections (`created_by_session_id=None`)

### 12. Validate migration
- **Task ID**: validate-migration
- **Depends On**: build-migration
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run migration on a fixture YAML; assert idempotent (second run is a no-op)
- Run migration on a YAML where one entry has malformed `interval`; assert abort with clear message before any rewrite
- **Run migration with simulated `Reflection.last_status="running"`; assert the migration does NOT block, completes Phase 1 (YAML rewrite), records the running reflection's name in `reflections:migration:pending_clear`, and skips the `run_history` clear for that record. Then transition the reflection to `last_status="success"` and re-run the migration — assert it now drains the sidecar and clears `run_history`.**
- Verify `run_history` backfill creates exactly one `ReflectionRun` per history entry; no double-write on re-run (Popoto `get_or_create` semantics)
- Verify YAML rewrite is atomic: simulate a concurrent `load_registry()` call mid-rewrite; assert reader sees either the old or new full file, never a torn read
- Verify the `reflections:migration:pending_clear` sidecar is implemented via Popoto, not raw Redis

### 13. Author tests
- **Task ID**: write-tests
- **Depends On**: build-model-schema, build-scheduler-grammar, build-scheduler-failure-tracking, build-output-sinks, build-mcp-server, build-migration, build-cli-wrapper
- **Assigned To**: test-author
- **Agent Type**: test-engineer
- **Parallel**: false
- Author all unit and integration tests listed in Test Impact section
- Particularly: empty/invalid input tests, error-state rendering tests, race-condition mitigations
- Verify cost-accounting: agent-type reflections write `cost_usd` from `AgentSession`; function-type reflections write `cost_usd=0`

### 14. Documentation
- **Task ID**: document-feature
- **Depends On**: build-model-schema, build-scheduler-grammar, build-scheduler-failure-tracking, build-output-sinks, build-mcp-server, build-migration, build-cli-wrapper, build-dashboard
- **Assigned To**: docs-author
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` to be single source of truth; cover schema, grammar, sinks, failure tracking, MCP, migration
- Update `docs/features/agent-session-scheduling.md` to defer scheduling to `reflections.md`
- Update `docs/features/reflections-dashboard.md` to cover new fields
- Update `docs/features/README.md` index

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-model-schema, validate-scheduler, validate-mcp, validate-migration, write-tests, document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full pytest suite (`pytest tests/`)
- Run `python -m ruff format --check .` and `python -m ruff check .`
- Verify all Success Criteria checkboxes
- Run `grep -r "interval:" config/reflections.yaml` — must return nothing
- Run `grep -rn "interval=" agent/reflection_scheduler.py models/reflection.py` — must return nothing
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No legacy `interval:` in YAML | `grep -r "^\s*interval:" config/reflections.yaml` | exit code 1 |
| No legacy `interval=` in scheduler | `grep -rn "interval=" agent/reflection_scheduler.py models/reflection.py` | exit code 1 |
| `croniter` importable | `python -c "import croniter"` | exit code 0 |
| MCP server importable | `python -c "import mcp_servers.reflections_server"` | exit code 0 |
| MCP server registered in `~/.claude.json` | `python -c "import json; d=json.load(open(__import__('os').path.expanduser('~/.claude.json'))); assert 'reflections' in d.get('mcpServers', {})"` | exit code 0 |
| Dashboard reader migrated off embedded `run_history` | `grep -n "state\.run_history" ui/data/reflections.py` | exit code 1 (no matches) |
| Migration script idempotent | `python scripts/migrate_reflections_yaml.py --dry-run --check-idempotent` | exit code 0 |
| Dashboard surfaces new fields | `curl -s localhost:8500/dashboard.json \| python -c "import json,sys; d=json.load(sys.stdin); r=d['reflections'][0]; assert 'failure_count_consecutive' in r and 'paused_until' in r and 'cost_usd_total' in r"` | exit code 0 |

## Critique Results

Cycle 1: NEEDS REVISION — 3 BLOCKERS, 6 CONCERNS. Rev2 addressed the cycle-1 BLOCKERS (migration design, auth model, Step 4.7 collision).

Cycle 2: NEEDS REVISION — 3 NEW BLOCKERS surfaced (`.mcp.json` doesn't exist; uv-sync ordering; `run_history` removal breaks `ui/data/reflections.py`). Rev3 addresses all three with verified fixes against current source. Verification commands run and recorded in the Implementation Note column below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | (cycle-1) | Q7 references `session.is_root_operator()` which does not exist as a method anywhere in the codebase (`grep -rn 'is_root_operator' agent/ models/ mcp_servers/` returns no results). The auth model needs to use existing primitives or define the helper concretely. | Q7 (rewritten); Risk 4 (rewritten); Task 6 (rewritten); Task 11 (rewritten) | Auth check uses `os.environ.get("AGENT_SESSION_ID") or os.environ.get("VALOR_SESSION_ID")` — the primitives `agent/sdk_client.py:1380-1385` already injects when spawning a Claude Code subprocess. `_caller_id()` returns the env var (or `None` for migration scripts / direct CLI). `_can_mutate(reflection)` returns `True` iff `caller is None` (registry-source caller) or `caller == reflection.created_by_session_id`. No new helper method on a session class is needed. Tests use `monkeypatch.setenv`/`monkeypatch.delenv` to drive auth states. |
| BLOCKER | (cycle-1) | The plan inserts the migration as "Step 4.7" in `scripts/update/run.py`, but Step 4.7 is already occupied (`run.py:839` validates the sdlc-tool wrapper as the green-light gate for service restart). The Update System, Data Flow, and Task 8 sections all repeat this collision. | Q3 (Implementation guard updated); Update System (corrected); Data Flow Migration path (corrected); Task 8 (corrected) | Insertion point is **Step 3.65**, immediately after `sync_reflections_yaml` at `run.py:403` and before Step 1.7's hook audit. Rationale: (a) the YAML has just been synced from the vault on the immediately preceding line, so the migration operates on the canonical file; (b) it precedes service-restart logic, so the worker restarts onto migrated state; (c) Step 4.7 stays with sdlc-tool validation. Atomic temp-file + rename means a failed migration leaves no partial file behind. |
| BLOCKER | (cycle-1) | The original design waits up to 60s for `last_status="running"` to clear before writing YAML, then aborts. This will routinely starve under normal load — `analytics-rollup`, `docs-auditor`, and per-project audits regularly run for several minutes. A single long-running reflection blocks every machine's `/update`. | Q3 (rewritten with 3-phase design); Risk 1 (rewritten); Race 1 (already correct); Task 8 (rewritten); Task 12 (rewritten with new test cases) | Migration coexists with running reflections in 3 idempotent phases: (1) **Atomic YAML rewrite** — temp file + `os.replace()`; the YAML and the model-record schema are independent surfaces, so mid-flight `mark_completed()` cannot conflict. (2) **Delta-loop `run_history` → `ReflectionRun` backfill** — walk every Reflection; `ReflectionRun.get_or_create((name, timestamp))` for each entry; if `last_status="running"` at scan time, append the name to a Popoto-managed sidecar set `reflections:migration:pending_clear` and skip the `run_history` clear. After the scan, drain the sidecar — clear `run_history` only on reflections that have stopped running. (3) **Schema validation** — re-load registry, call `compute_next_due()` on every entry, abort loudly on parse error. The migration is fully reentrant; reflections still running at exit are handled on the next `/update`. |
| BLOCKER | (cycle-2) | The plan proposes registering the new MCP server in `.mcp.json` at the repo root, but no such file is checked into this repo. Verified via `ls -la .mcp.json` → "No such file or directory." The actual MCP registration surface is `~/.claude.json`'s `mcpServers` map, self-healed by `scripts/update/mcp_memory.py` at update Step 4.8. | Agent Integration (rewritten); Q7 / Task 6 (Register-step rewritten); Success Criteria (#481 rewritten); Verification table (new row); | Add `scripts/update/mcp_reflections.py` modeled on `scripts/update/mcp_memory.py` (same `fcntl.flock(LOCK_EX \| LOCK_NB)` + retry schedule + atomic-rename + `~/.claude.json.bak` backup pattern). The helper writes a `reflections` entry under `~/.claude.json`'s `mcpServers` map with `command="python3"` and `args=["-m", "mcp_servers.reflections_server"]`. New **Step 4.85** in `run.py` invokes it immediately after Step 4.8's memory MCP verification, gated by `config.do_service_restart`. All "register in `.mcp.json`" prose is replaced. |
| BLOCKER | (cycle-2) | The plan adds `croniter` as a new dependency but places the migration step at Step 1.67 — BEFORE Step 3's `uv sync` at `run.py:462`. The migration script imports `croniter` (Phase 3 schema validation calls `compute_next_due`), so it would `ImportError` on a fresh machine. Worker import of the scheduler at Step 5 was fine, but the migration step itself was mis-ordered. | Q3 (Implementation guard rewritten); Update System (Step 3.65 rationale rewritten); Risk 2 (rewritten); Task 8 (rewritten); Data Flow Migration path (rewritten) | Move the migration invocation from Step 1.67 to **Step 3.65** — immediately after Step 3.6's existing data-migration phase (`run.py:622`) and before Step 3.7's binary installs (`run.py:637`). This is **after Step 3's `uv sync` at line 462**, so `croniter` is installed before the migration imports it. The YAML symlink was already established at Step 1.66 (line 403), so the canonical file is in place. Worker restart at Step 5 (line 887) remains well after `uv sync`. |
| BLOCKER | (cycle-2) | The plan removes the embedded `run_history` field from the `Reflection` model but doesn't update `ui/data/reflections.py`, which reads `state.run_history` at lines 129, 257, 297. The dashboard would break the moment the PR lands. | Q1 implementation guard (cycle-3 ripple section added); Test Impact (two entries added); Task 9 (renamed + expanded); Verification table (new row); Documentation (no change needed — `reflections-dashboard.md` already in scope) | Disposition: **option (b)** — update `ui/data/reflections.py` in the same PR. Replace `state.run_history` reads with `ReflectionRun` Popoto queries: `_build_entry()` line 129 (has_history), `get_run_history()` lines 239-277, `get_run_detail()` lines 280-306. Caller signatures unchanged. New test `tests/unit/test_ui_data_reflections.py` asserts no remaining `state.run_history` references and that paginated reads come from `ReflectionRun.query`. Rejected option (a) "deprecate writes" — leaves dead state on every record (NO LEGACY CODE TOLERANCE). Rejected option (c) "compat shim" — a shim that should never run is a smell per prevention-over-cleanup feedback. |

---

## Open Questions

(None — all 8 architecture questions resolved in the Architecture Decisions section. If the war-room critique surfaces blocking concerns, they will be added here for human input before build.)
