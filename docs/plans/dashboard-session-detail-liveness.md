---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1269
last_comment_id:
---

# Dashboard: Session List + Detail Modal — Lifecycle, PID, and Liveness

## Problem

The internal localhost dashboard at `localhost:8500` is the operator's primary read surface for the
13-state session lifecycle, but today it does not surface enough state-of-truth to answer the
single most common operator triage question: **"is this session actually progressing right now,
or is it claimed-running-but-dead (a 'ghost session')?"** Operators have to drop into
`valor-session status --id <id>` or tail logs to answer it, defeating the dashboard.

**Current behavior:**

- `ui/templates/_partials/sessions_table.html` row exposes: project, name, persona, started
  timestamp, status label, SDLC stage dots, issue/PR links. Liveness is implied only via the
  `is_stale` CSS class (`updated_at > 10min` on `running`/`active` sessions, per `ui/data/sdlc.py:581-583`).
- `ui/templates/_partials/session_modal_content.html` shows: session/thread/project IDs, branch,
  slug, resume UUID, persona, created/started/completed timestamps, duration, tool-call count,
  links, expectations, SDLC stage chips, original message, timeline.
- Neither surface shows: harness subprocess PID, `last_heartbeat_at`, `last_sdk_heartbeat_at`,
  `last_stdout_at`, `last_evidence_at`, `last_tool_use_at`, `last_turn_at`, `recovery_attempts`,
  `reprieve_count`, `current_tool_name`, or any live-process probe result.
- `paused`, `paused_circuit`, `waiting_for_children`, `superseded` collapse into raw status text
  with no distinguishing iconography. The row template only has custom glyphs for `running`,
  `pending`, `dormant`, `active`, `waiting_for_children`, `completed`.
- When the harness subprocess has died but the session record still says `running` (the #1246
  failure mode), the dashboard renders it indistinguishably from a healthy `running` session.

**Desired outcome:**

- Each session row carries a compact freshness signal — age-since-`last_evidence_at` — that
  immediately distinguishes "recently active" from "claimed running, no evidence in N minutes."
- The detail modal exposes the full state-of-truth: PID (when known), all heartbeat/evidence
  timestamps, `recovery_attempts`, `reprieve_count`, `watchdog_unhealthy` reason,
  `current_tool_name`.
- For sessions claiming `running`/`active`, the modal renders a **process-alive probe result**
  (`os.kill(pid, 0)`) and surfaces a "ghost — process dead" badge if the PID is dead.
- All 8 non-terminal lifecycle states have visual differentiation in the modal; row carries
  enough to distinguish the common `running`/`paused`/`dormant`/`waiting_for_children` triage
  paths.
- Operators can answer "is this session actually doing anything right now?" without leaving the
  dashboard.

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:18:46Z (today, ~hours ago)
**Disposition:** Unchanged

**File:line references re-verified:**

- `ui/templates/_partials/sessions_table.html:1-142` — list row template, exact structure cited
  in issue. **Still holds.**
- `ui/templates/_partials/session_modal_content.html:1-178` — detail modal template. **Still holds.**
- `ui/app.py:279-301` — `partial_sessions_table` and `session_modal_content` route handlers.
  **Still holds (line numbers exact).**
- `ui/app.py:368-424` — `_session_to_json` already serializes `current_tool_name`,
  `last_tool_use_at`, `last_turn_at`, `recent_thinking_excerpt`, `last_evidence_at`,
  `watchdog_unhealthy`. **Confirmed — issue's "data plumbing already done" claim holds.**
- `models/agent_session.py:270-288` — heartbeat / recovery_attempts / reprieve_count fields.
  **Still holds.**
- `agent/session_state.py:46` — `SessionHandle.pid: int | None = None` already populated by
  `_on_sdk_started` callback at `agent/session_executor.py:1136-1149`. **Discovered during recon
  but not in issue body — see Spike Results.**
- `ui/data/sdlc.py:581-583` — `is_stale = (time.time() - updated_at) > 600`. **Still holds.**
- `ui/data/sdlc.py:215-261` — `PipelineProgress` dataclass. **Still holds.**
- `agent/session_health.py:670-683` — psutil `Process(pid).status()` gating for Tier-2 reprieve.
  Confirms `psutil` is already a dep and live-process probes are an established pattern.

**Cited sibling issues/PRs re-checked:**

- #1246 — OPEN. "Reliability risk: tier-1 stdout-staleness reaper missed 11h-stuck SDK
  subprocess." Motivating failure mode for the ghost badge. Different scope (reaper logic vs. UI
  visibility); this plan is strongly complementary.
- #1036 — CLOSED 2026-04-18. "300s no-progress guard kills sessions before first turn." Shipped
  the `last_heartbeat_at` / `last_sdk_heartbeat_at` / `last_stdout_at` / `recovery_attempts` /
  `reprieve_count` fields this plan surfaces.
- #1172 — CLOSED 2026-04-29. "PM session liveness: surface progress or stay graceful." Shipped
  `current_tool_name`, `last_tool_use_at`, `last_turn_at`, `last_evidence_at` and the
  `_session_to_json` enrichment.
- #1028 — OPEN. Dashboard condensation effort. **Coordination signal** — this plan is purely
  additive and respects condensation by keeping the row lean (a single new chip with a single
  age value), with detail in the modal.
- #1245 — provides `tool_call_count`/`turn_count` writeback used by the modal; closed.

**Commits on main since issue was filed (touching referenced files):** None. `git log
--oneline --since="2026-05-04T09:18:46Z" -- ui/app.py ui/templates/_partials/sessions_table.html
ui/templates/_partials/session_modal_content.html models/agent_session.py
models/session_lifecycle.py` returns no commits.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/dashboard-session-derived-analytics.md` (#1245) — touches `ui/data/analytics.py`
  and `_session_to_json` for cost/turn analytics. **Different surface** — the analytics block,
  not row/modal rendering. No conflict.
- `docs/plans/dashboard-context-persistence.md` (#549) — touches `sessions_table.html` for
  project column placement and stage pill rendering. **Already In Progress** — this plan must
  preserve the project-as-first-column layout shipped there.
- `docs/plans/reflections-dashboard.md` — reflections panel; orthogonal.

**Notes:** Recon discovered `SessionHandle.pid` is already populated in-memory by the
`on_sdk_started` callback (`agent/session_executor.py:1136-1149`). The issue body said PID is
"not currently a field on AgentSession" — that's correct; the in-memory handle drops PID when
the worker restarts or the session ends. Persisting requires a small worker-side write. See
spike-1.

## Prior Art

- **PR #1177 (MERGED 2026-04-27)**: "fix(#1172): PM session liveness — see progress or stay
  graceful." Shipped the in-flight visibility fields (`current_tool_name`, `last_tool_use_at`,
  `last_turn_at`, `recent_thinking_excerpt`, `last_evidence_at`) and added them to
  `_session_to_json`. **This plan surfaces those fields in the row/modal templates** — the
  data is there, the rendering is missing.
- **PR #1243 (MERGED 2026-05-01)**: "fix(session-health): promote per-turn SDK signals to Tier
  1." Reinforced the heartbeat-based no-progress detector this plan visualizes.
- **#984 (CLOSED)**: "Worker shutdown hiccups: stale restart flag + zombie PID passes
  worker-status." Validated the `os.kill(pid, 0)` pattern for liveness probing — already used
  in `tools/agent_session_scheduler.py:806`, `bridge/telegram_bridge.py:384`,
  `monitoring/bridge_watchdog.py`.
- **#1245 (issue, plan In Progress)**: dashboard analytics. Adjacent surface, no conflict.
- **#549 (plan In Progress)**: project column placement. **Must preserve** the project-as-
  first-column layout.
- **#1271 (OPEN)**: "Cleanup reflection: reap orphan claude/worker/MCP processes." Adjacent
  failure mode — orphan reaper would benefit from PID persistence too. This plan unblocks it
  as a side effect.

## Research

No external research conducted — this work is purely internal Jinja/HTMX/Popoto/psutil glue
that builds on patterns already established in the codebase (`os.kill(pid, 0)` liveness probe
in 4 existing call sites; `psutil.Process(pid).status()` in `session_health.py`; HTMX polling
already wired to `partial_sessions_table` and `session_modal_content`). No external libraries
or APIs touched. Per do-plan Phase 0.7, "skip if the work is purely internal."

## Spike Results

### spike-1: PID is already in-memory, only persistence is missing
- **Assumption**: "PID is not currently persisted on `AgentSession` and the worker has no
  reference to the harness subprocess PID."
- **Method**: code-read (`agent/session_executor.py`, `agent/session_state.py`,
  `agent/sdk_client.py`).
- **Finding**: PID is **already known** in two places:
  1. `agent/sdk_client.py:2482-2486` — `_run_harness_subprocess` fires the `on_sdk_started(pid)`
     callback right after `asyncio.create_subprocess_exec` returns, with `proc.pid`.
  2. `agent/session_executor.py:1136-1149` — `_on_sdk_started(pid)` writes PID to the
     in-memory `SessionHandle.pid` (`agent/session_state.py:46`) and saves
     `last_sdk_heartbeat_at` to the AgentSession.
  The handle is dropped on session exit (`_active_sessions.pop()` in the `finally` block) and
  on worker restart. Persisting to `AgentSession.harness_pid` is a one-line addition to the
  existing callback — no new wiring, no new subprocess plumbing.
- **Confidence**: high
- **Impact on plan**: PID persistence is a sub-task of this plan (not a separate issue). The
  worker-side change is ~5 lines: add `harness_pid = IntField(null=True)` to AgentSession,
  write it from `_on_sdk_started`, clear it on session exit. Estimated <30 min.

### spike-2: `os.kill(pid, 0)` probe is safe and fast inside the request handler
- **Assumption**: "A live `os.kill(pid, 0)` probe inside `/dashboard.json` or
  `/session/{id}/modal-content` could block the request and degrade dashboard responsiveness."
- **Method**: code-read (`tools/agent_session_scheduler.py:806`,
  `bridge/telegram_bridge.py:384`, `monitoring/bridge_watchdog.py`, Linux/macOS `kill(2)` man
  page). `os.kill(pid, 0)` is a no-op signal that returns success/`ProcessLookupError` based
  on the kernel's process table — it does **not** block on the target process; it's
  effectively `O(1)` and unaffected by the target's state (running, sleeping, zombie, dead).
  Already used in 4 production call sites without timeout wrappers.
- **Confidence**: high
- **Impact on plan**: No timeout/budget guard needed. The probe is a single `os.kill(pid, 0)`
  call in a `try/except (ProcessLookupError, PermissionError)` block. Result: `"alive"` |
  `"ghost"` | `"unknown"` (when PID is None or `PermissionError` raised). Skipped entirely
  for terminal-status sessions.

### spike-3: Caching the probe per-session-per-request is unnecessary
- **Assumption**: "30s polling cadence × 1 probe per running session × N sessions = excessive
  syscalls; a 5s TTL cache is needed."
- **Method**: arithmetic. The dashboard's HTMX polling cadence is 5s for the partial table
  refresh (`/_partials/sessions/`). A typical operator has 3-10 sessions visible. 10 probes
  every 5s = 2 syscalls/sec across the entire process. `os.kill(pid, 0)` is a single
  cheap syscall (kernel process-table lookup, no IPC). At the call-site cost order
  established in spike-2, caching would add complexity (cache key, TTL, invalidation) for
  zero measurable benefit.
- **Confidence**: high
- **Impact on plan**: No probe-result cache. The probe runs on every request handler call
  for sessions claiming non-terminal `running`/`active`/`paused`/`paused_circuit` status.
  Modal probes only run when the modal is opened (one probe per click).

## Data Flow

This plan touches three layers — model, data-builder, template — for both row and modal.

1. **Entry point (worker, harness spawn)** — `agent/sdk_client.py:2482` fires `on_sdk_started(pid)`.
2. **Persistence (worker callback)** — `agent/session_executor.py:1136-1149`
   `_on_sdk_started(pid)` writes `session.harness_pid = pid` (NEW), saves with
   `update_fields=["last_sdk_heartbeat_at", "harness_pid"]`. On session exit (the existing
   `finally` block in `_execute_agent_session`), set `session.harness_pid = None` and save.
3. **Read path (UI data builder)** — `ui/data/sdlc.py::_session_to_progress` reads
   `session.harness_pid`, `session.last_heartbeat_at`, `session.last_sdk_heartbeat_at`,
   `session.last_stdout_at`, `session.recovery_attempts`, `session.reprieve_count` and
   populates new `PipelineProgress` fields. Computes `process_alive` flag for non-terminal
   sessions: `True` / `False` / `None` (when PID is None or probe raised non-ProcessLookupError).
4. **JSON serialization** — `ui/app.py::_session_to_json` adds the new fields to the JSON
   payload so `dashboard.json` consumers see them.
5. **Row template** — `sessions_table.html` adds:
   - For non-terminal sessions: a compact freshness chip showing `last_evidence_at` age
     (e.g. "12s", "3m", "1h"). Color: green (<60s), amber (60s-10min), red (>10min).
   - For sessions claiming `running`/`active`/`paused`/`paused_circuit` with `process_alive
     == False`: a small "ghost" indicator next to the status label.
   - For `paused_circuit`: a distinct icon (e.g. `⛌`) vs. `paused` (e.g. `⏸`).
6. **Modal template** — `session_modal_content.html` adds:
   - PID (when present) with the probe result chip ("alive"/"ghost"/"unknown").
   - A "Liveness" sub-table: `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`,
     `last_evidence_at`, `last_tool_use_at`, `last_turn_at`, `recovery_attempts`,
     `reprieve_count`, `current_tool_name`, `watchdog_unhealthy`.
   - All timestamps formatted via the existing `format_timestamp` filter.

## Architectural Impact

- **New dependencies**: None. `psutil` already a dep; `os.kill(pid, 0)` is in stdlib.
- **Interface changes**: One new field on `AgentSession`: `harness_pid: IntField(null=True)`.
  New fields on `PipelineProgress` dataclass: `harness_pid`, `last_heartbeat_at`,
  `last_sdk_heartbeat_at`, `last_stdout_at`, `recovery_attempts`, `reprieve_count`,
  `process_alive`. New keys in `dashboard.json` payload (additive — no breaking change).
- **Coupling**: Mild increase. `ui/data/sdlc.py` gains a 5-line liveness probe helper.
  `agent/session_executor.py::_on_sdk_started` gains one extra ORM write field.
- **Data ownership**: PID is owned by the worker (single writer:
  `_execute_agent_session` for its own session). Read by UI only.
- **Reversibility**: Trivial. Remove the new field, revert the template additions. PID-using
  helpers gracefully degrade when PID is None (already the design).

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), validator, documentarian

**Interactions:**
- PM check-ins: 0-1 (scope is fixed by issue; no requirement clarification expected)
- Review rounds: 1 (war room critique already enforced before build; one PR review pass)

This is a 2-3 hour build for a careful operator: model field + worker callback + data builder +
two templates + tests + doc. The risk surface is bounded — the new code paths fail-open (no
PID = no probe; ORM write failure = log warning), and the rendering is purely additive.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `psutil` available | `python -c "import psutil"` | Already used in `agent/session_health.py`; stdlib `os.kill` is the actual call site for the probe |
| Redis reachable for ORM writes | `python -c "import redis, os; redis.Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0')).ping()"` | `harness_pid` writes go through Popoto -> Redis |
| Worker code editable | `test -f agent/session_executor.py && test -f agent/sdk_client.py` | The worker callback is where PID is captured |

Run all checks: `python scripts/check_prerequisites.py docs/plans/dashboard-session-detail-liveness.md`

## Solution

### Key Elements

- **`AgentSession.harness_pid`**: New nullable IntField. Written by the worker on harness
  spawn, cleared on session exit.
- **Worker callback PID persistence**: `_on_sdk_started(pid)` adds `session.harness_pid =
  pid` to its existing `update_fields` save. Session exit `finally` clears it.
- **`PipelineProgress` liveness fields**: `harness_pid`, `last_heartbeat_at`,
  `last_sdk_heartbeat_at`, `last_stdout_at`, `recovery_attempts`, `reprieve_count`,
  `process_alive: bool | None`.
- **`_check_process_alive(pid)` helper**: 5-line function in `ui/data/sdlc.py`. Returns
  `True` (alive), `False` (ghost), or `None` (unknown — PID None or non-`ProcessLookupError`).
  Skipped for terminal-status sessions.
- **Row freshness chip**: For non-terminal sessions, a compact age-since-`last_evidence_at`
  chip with three color tiers (green/amber/red). Optional ghost indicator for dead-PID
  sessions.
- **Lifecycle state row glyphs**: `paused_circuit` gets a distinct glyph from `paused`
  (e.g. `⛌` vs `⏸`). `superseded` shows a small "→" indicator.
- **Modal Liveness table**: New section between "Timing" and "SDLC" rendering all heartbeat
  timestamps + counters.

### Flow

Operator opens dashboard → row shows status + freshness chip ("3m" amber) → operator clicks row
→ modal opens with Liveness sub-table showing PID 12345 (alive), last heartbeat 12s ago, last
stdout 3m ago, recovery_attempts 0, reprieve_count 1, current tool "Bash" → operator concludes
session is healthy and progressing.

For ghost detection: row shows status "running" + "5m" red chip + small "ghost" badge → click
→ modal shows PID 12345 (ghost — process dead), last heartbeat 5m ago → operator runs
`valor-session kill --id <id>` confidently.

### Technical Approach

1. **Model**: Add `harness_pid = IntField(null=True)` to `AgentSession` after the existing
   `last_stdout_at` block (`models/agent_session.py:281`).
2. **Worker write**: Modify `_on_sdk_started` (`agent/session_executor.py:1136-1149`) to
   also set `session.harness_pid = pid` and include it in `update_fields`. Add PID-clear on
   session exit in the existing `finally` block at `agent/session_executor.py:1846-1848` —
   guard with `try/except` so an ORM write failure during shutdown never masks the original
   exception.
3. **Data builder**: In `ui/data/sdlc.py::_session_to_progress` (around line 629-647),
   read the new ORM fields and call `_check_process_alive(pid)` for non-terminal sessions
   only. Populate the new `PipelineProgress` fields. Pre-existing `last_evidence_at`
   computation already aggregates the right timestamps.
4. **Liveness probe helper** (`ui/data/sdlc.py`):
   ```python
   def _check_process_alive(pid: int | None) -> bool | None:
       if pid is None:
           return None
       try:
           os.kill(pid, 0)
           return True
       except ProcessLookupError:
           return False
       except (PermissionError, OSError):
           return None  # uncertain — don't lie
   ```
5. **JSON serialization**: Add new fields to `_session_to_json` (`ui/app.py:368-424`),
   maintaining the existing additive contract.
6. **Row template** (`sessions_table.html`): Add a freshness chip span inside the status
   `<td>`, between the status label and duration. Add a distinct glyph for `paused_circuit`
   and `superseded`. Add a ghost indicator when `process_alive == False`.
7. **Modal template** (`session_modal_content.html`): Add a Liveness table after Timing,
   before SDLC. Include PID + probe-result chip (when PID present). Include all heartbeat
   timestamps via `format_timestamp`. Include `recovery_attempts`, `reprieve_count`,
   `current_tool_name`, `watchdog_unhealthy`.
8. **Color tiers** (CSS, in template `<style>` or inline): green `< 60s`, amber `60s-10min`,
   red `> 10min`. Match existing `is_stale` 10-min threshold.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_check_process_alive(pid)` swallows `PermissionError`/`OSError` and returns `None`.
      Test: monkeypatch `os.kill` to raise `PermissionError`, assert returns `None`,
      assert no log spam (the helper does not log — it returns the sentinel).
- [ ] `_on_sdk_started` already wraps the ORM save in `try/except`; the new `harness_pid`
      write inherits that protection. Test: existing `_on_sdk_started` test pattern is in
      `tests/unit/` — extend it to assert `harness_pid` is set on success and logged-but-
      not-raised on save failure.
- [ ] Session-exit PID clear: wrap in `try/except` so ORM failure during shutdown does not
      mask the original session exception. Test: monkeypatch `session.save` to raise on the
      exit path, assert original exception propagates and `harness_pid=None` write was
      attempted.

### Empty/Invalid Input Handling
- [ ] `_check_process_alive(None)` returns `None` (PID-not-known sessions like pre-deploy
      records). Test included in spike helper unit tests.
- [ ] `_check_process_alive(-1)` / `_check_process_alive(0)` — OS treats `kill(0, ...)` as
      "all processes in process group"; that's a privilege-elevation edge case we should
      avoid. Helper rejects PID `<= 0`: returns `None`. Test asserts.
- [ ] PIDs that have been recycled: `os.kill(pid, 0)` returns `True` for whatever process
      now holds that PID. Acceptable — the worst case is "ghost detection misses a recycled-
      PID death," which the existing `is_stale` + `last_evidence_at` chip already catches.
      Document this in the rendered modal copy ("alive — process exists; PID may be recycled
      across worker restarts").

### Error State Rendering
- [ ] Modal renders gracefully when PID is None: shows "PID: -" or omits the row entirely.
      Test via Jinja template render with `pipeline.harness_pid = None`.
- [ ] Modal renders gracefully when probe returns `None` (uncertain): shows "unknown" chip,
      not "ghost". Test via template render with `pipeline.process_alive = None`.
- [ ] Row freshness chip omitted entirely for terminal-status sessions (no need to show
      "3 days ago" on a completed session). Test: render with status `completed`, assert no
      chip element present.

## Test Impact

- [ ] `tests/unit/test_dashboard_data.py` (or equivalent ui-data unit test) — UPDATE: add
      assertions that new `PipelineProgress` fields are populated from `AgentSession`.
- [ ] `tests/unit/test_session_executor.py::test_on_sdk_started` (locate exact name during
      build) — UPDATE: assert `harness_pid` set after callback fires and cleared on exit.
- [ ] `tests/unit/test_agent_session_model.py` (or equivalent) — UPDATE: add field to
      AgentSession schema test if such a test exists; otherwise covered by import.
- [ ] `tests/integration/test_dashboard_endpoint.py` — UPDATE/ADD: GET `/dashboard.json`
      with a session that has `harness_pid` set, assert field appears in JSON.
- [ ] No DELETE or REPLACE — all changes are additive to existing test scope.
- [ ] New test: `tests/unit/test_dashboard_liveness_probe.py` (CREATE) — unit-test the
      `_check_process_alive` helper across all branches (alive / ghost / None / negative PID
      / permission error).

If during build no test for `_on_sdk_started` PID-write exists, create one in
`tests/unit/test_session_executor_pid.py` covering the persist + clear paths.

## Rabbit Holes

- **Don't add inline action buttons** (kill/resume/steer) to the row or modal in this
  plan. The issue's Q4 invites it but it explodes scope into CSRF, button-state degradation,
  and undo flows. Keep the dashboard read-only in this iteration.
- **Don't replace the polling cadence with SSE.** Issue's Q6 invites it, but it's a
  separate architecture project (worker -> Redis pubsub -> uvicorn SSE). The 5s polling
  cadence is sufficient for triage.
- **Don't add a probe-result cache.** Spike-3 confirmed it's unnecessary. Resist temptation
  to "future-proof" with a TTL cache — adds complexity for zero measurable benefit.
- **Don't redesign the row layout.** Project-as-first-column is in flight via #549; this
  plan must coexist. Add the freshness chip *inside* the existing status `<td>`, do not add
  new columns.
- **Don't surface the full `chat_message_log` or `session_events` in the modal.** That's a
  bigger session-detail surface and belongs in its own plan.
- **Don't backfill historical sessions with PID.** Pre-deploy sessions keep `harness_pid =
  None`; the modal handles None gracefully. No migration script needed.
- **Don't add new endpoints.** All work is in two existing partial templates and their
  data builders.

## Risks

### Risk 1: PID recycling produces false-positive "alive" results
**Impact:** A session whose PID is dead but whose number was reassigned to an unrelated
process would show "alive" in the modal, misleading the operator.
**Mitigation:** Pair the probe with `last_evidence_at` age. If the probe says "alive" but
`last_evidence_at` is >10 min stale, the freshness chip is red — operator sees both signals.
Document the tradeoff in the modal copy. Recycling is rare on macOS/Linux on a single-host
deploy with low PID churn; the worst-case "false positive" still has the staleness chip as a
safety net.

### Risk 2: Worker save failure on `harness_pid` write masks session-start
**Impact:** If the new ORM write in `_on_sdk_started` raises (e.g. Redis transient failure),
the session's `last_sdk_heartbeat_at` write is also lost — the session would appear stuck
from the watchdog's perspective.
**Mitigation:** The save is already wrapped in `try/except` and only logs warnings. The
existing pattern is preserved. Verify via test: monkeypatch save to raise, assert session
proceeds and warning is logged.

### Risk 3: Template additions break layout under #549's project-column changes
**Impact:** The in-flight `dashboard-context-persistence` plan (#549) is editing
`sessions_table.html` for project-first-column. Conflicting edits could break either change.
**Mitigation:** All freshness-chip additions stay *inside* the status `<td>` — no new
columns. The `<th>` row is untouched. Coordinate via PR description noting overlap with
#549.

### Risk 4: Probe runs against PID owned by another user
**Impact:** `os.kill(pid, 0)` raises `PermissionError` if the PID exists but is owned by
another user. On a single-machine deploy this shouldn't happen (worker and dashboard run as
the same user), but defensive coding requires handling it.
**Mitigation:** Helper returns `None` ("unknown") on `PermissionError`. Modal renders
"unknown — probe error" rather than lying. Test asserts.

## Race Conditions

### Race 1: PID written then cleared between two reads
**Location:** `agent/session_executor.py:1136` (write) and `agent/session_executor.py:1846`
(clear) span the entire session execution. UI reads happen at any moment.
**Trigger:** Session ends between the dashboard's `partial_sessions_table` poll and the
operator clicking the row to open the modal.
**Data prerequisite:** None — both reads are point-in-time and `harness_pid` is read
directly from Popoto, not cached.
**State prerequisite:** None — the model field is the single source of truth.
**Mitigation:** `_check_process_alive(None)` returns `None`, modal renders "unknown".
Acceptable — the race is benign because the session is already in a terminal state and the
operator's question ("is this alive?") is moot.

### Race 2: Probe runs while worker is mid-restart
**Location:** Worker restart cycle (`scripts/valor-service.sh restart`).
**Trigger:** Dashboard polls during the ~1s window where the old worker has exited but the
session record still says `running` (the watchdog will reap shortly).
**Data prerequisite:** AgentSession `harness_pid` field has the old (now-dead) PID.
**State prerequisite:** Worker is restarting; PID does not exist.
**Mitigation:** Probe returns `False`, freshness chip is red, modal shows "ghost". This is
**desired behavior** — the operator sees the ghost during the watchdog reap window. No
mitigation needed; this is the feature working correctly.

## No-Gos (Out of Scope)

- Inline kill/resume/steer action buttons on row or modal (Q4) — separate plan.
- SSE streaming replacement of polling (Q6) — separate architecture project.
- CLI ↔ dashboard linking via `valor-session status --open` (Q5) — separate small plan.
- New "system health" page (out of issue scope per its own boundary).
- New `/probe/{pid}` REST endpoint — probe runs inline in existing handlers.
- Probe-result caching — spike-3 ruled it out.
- Backfill migration for `harness_pid` on existing records — UI handles None gracefully.
- Replacement of the existing `is_stale` `>10min` rule — kept as the staleness contract;
  the new chip just makes the age visible.
- Process tree visualization or child-process inspection — out of scope.
- Lifecycle state filtering (e.g. "only show paused"). The row already filters via
  category; visual differentiation is the only ask.
- PID reuse / process-group safety beyond the negative-PID guard.

## Update System

No update system changes required. The `harness_pid` field is a Popoto-managed AgentSession
field; existing sessions get `None` by default and the UI handles it gracefully. No new
config files, no new env vars, no migration scripts, no service restarts beyond the standard
worker restart that any code change requires.

## Agent Integration

No agent integration required. This is a dashboard-rendering change. The agent already has
a `Bash` tool and can hit `localhost:8500/dashboard.json` via `curl` if it ever needs to
inspect session state programmatically — the new `harness_pid` and liveness fields appear in
that JSON payload (additive, non-breaking) for any future agent that wants them.

The bridge does not import or call the new code paths. `mcp_servers/` is unaffected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/dashboard.md` with a new "Liveness signals" section describing
      the freshness chip, ghost badge, and modal Liveness table.
- [ ] Update `docs/features/session-lifecycle.md` with a brief note that the dashboard
      surfaces the 13 lifecycle states with distinct iconography for `paused_circuit` /
      `superseded` / etc.
- [ ] No new top-level feature doc — this is an extension of `docs/features/dashboard.md`.

### External Documentation Site
- [ ] N/A — this repo does not use Sphinx/MkDocs/RTD.

### Inline Documentation
- [ ] Docstring on `_check_process_alive` documenting return semantics (alive / ghost /
      unknown) and the recycled-PID caveat.
- [ ] Comment block on `AgentSession.harness_pid` field describing single-writer contract
      (`_on_sdk_started`) and clear-on-exit invariant.
- [ ] Comment on the freshness chip color tiers in the row template.

## Success Criteria

- [ ] All 8 non-terminal lifecycle states are visually distinguishable somewhere on the
      dashboard surface (row glyph or modal). `paused` and `paused_circuit` are
      distinguishable.
- [ ] Modal renders, when present on the model: `last_heartbeat_at`, `last_sdk_heartbeat_at`,
      `last_stdout_at`, `last_evidence_at`, `last_tool_use_at`, `last_turn_at`,
      `recovery_attempts`, `reprieve_count`, `current_tool_name`, `watchdog_unhealthy`,
      `harness_pid`. Each timestamp uses the `format_timestamp` filter.
- [ ] PID is persisted on `AgentSession` via the worker `_on_sdk_started` callback and
      cleared on session exit. ORM write failure logs but does not crash.
- [ ] For sessions claiming `running`/`active`/`paused`/`paused_circuit`, the modal
      displays a probe-result chip ("alive" / "ghost — process dead" / "unknown — probe
      error").
- [ ] An operator can answer "is this session actually progressing right now?" entirely
      from the modal — no need to run `valor-session status --id <id>` for the common
      `running`/`paused`/`dormant` triage path.
- [ ] No regression to the existing common-case row layout (visual diff is additive). Old
      sessions with `harness_pid = None` render correctly.
- [ ] `/dashboard.json` continues to serve all currently-serialized fields plus the new
      ones. Contract stays in sync with rendered UI.
- [ ] `docs/features/dashboard.md` updated with the Liveness signals section.
- [ ] Tests pass (`/do-test`).
- [ ] Lint clean (`python -m ruff check .`).
- [ ] Format clean (`python -m ruff format --check .`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead
NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (worker + model)**
  - Name: pid-persistence-builder
  - Role: Add `AgentSession.harness_pid` field, write it in `_on_sdk_started`, clear on exit
  - Agent Type: builder
  - Resume: true

- **Builder (UI data layer)**
  - Name: liveness-data-builder
  - Role: Extend `PipelineProgress`, add `_check_process_alive` helper, populate fields in
    `_session_to_progress`, extend `_session_to_json`
  - Agent Type: builder
  - Resume: true

- **Builder (templates)**
  - Name: liveness-template-builder
  - Role: Edit `sessions_table.html` (row freshness chip + lifecycle glyphs) and
    `session_modal_content.html` (Liveness table)
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: liveness-tester
  - Role: Unit tests for `_check_process_alive`, integration test for `/dashboard.json`
    new fields, executor test for PID write/clear
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: liveness-validator
  - Role: Read-only verification — render dashboard, click through a synthetic session,
    confirm probe results, confirm chip rendering, confirm no #549 collision
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: liveness-documentarian
  - Role: Update `docs/features/dashboard.md`, add section to `docs/features/session-lifecycle.md`
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 — Core: builder, validator, code-reviewer, test-engineer, documentarian.

## Step by Step Tasks

### 1. PID persistence: model + worker callback
- **Task ID**: build-pid-persistence
- **Depends On**: none
- **Validates**: `tests/unit/test_session_executor_pid.py` (create), existing AgentSession
  schema tests must still pass
- **Informed By**: spike-1 (PID already known in handle, only persistence missing)
- **Assigned To**: pid-persistence-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `harness_pid = IntField(null=True)` to `AgentSession` after the existing
  `last_stdout_at` block (`models/agent_session.py:281`)
- Modify `_on_sdk_started` (`agent/session_executor.py:1136-1149`) to set
  `session.harness_pid = pid` and add to `update_fields=["last_sdk_heartbeat_at",
  "harness_pid"]`
- In the existing `finally` block at `agent/session_executor.py:1846-1848`, wrap a
  best-effort `session.harness_pid = None; session.save(update_fields=["harness_pid"])` in
  `try/except` so a save failure during shutdown never masks the original exception
- Document the single-writer contract in a comment block above the new field

### 2. UI data layer: liveness fields + probe helper
- **Task ID**: build-liveness-data
- **Depends On**: build-pid-persistence
- **Validates**: `tests/unit/test_dashboard_liveness_probe.py` (create), existing
  `ui/data/sdlc.py` tests must still pass
- **Informed By**: spike-2 (probe is safe inline, no timeout needed), spike-3 (no cache
  needed)
- **Assigned To**: liveness-data-builder
- **Agent Type**: builder
- **Parallel**: false
- Add new fields to `PipelineProgress` dataclass (`ui/data/sdlc.py:215-261`):
  `harness_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`,
  `recovery_attempts`, `reprieve_count`, `process_alive: bool | None = None`
- Add `_check_process_alive(pid)` helper at module level (5 lines, see Technical Approach)
- Reject `pid <= 0` in the helper to avoid the kernel's `kill(0, ...)` process-group
  semantics
- In `_session_to_progress`, read the new ORM fields and call `_check_process_alive(pid)`
  only for non-terminal status (`running`/`active`/`paused`/`paused_circuit`); skip for
  terminals
- Extend `_session_to_json` (`ui/app.py:368-424`) to include the new fields in the JSON
  payload (additive — no key removal)

### 3. Templates: row chip + modal Liveness table
- **Task ID**: build-templates
- **Depends On**: build-liveness-data
- **Validates**: Manual render check via `python -m ui.app` and clicking through synthetic
  sessions. Visual diff must show no regression on the common-case row.
- **Informed By**: prior art #549 (project-as-first-column must coexist), recon (existing
  status `<td>` is the host element for the chip)
- **Assigned To**: liveness-template-builder
- **Agent Type**: builder
- **Parallel**: false
- In `sessions_table.html`, add a compact freshness chip *inside* the existing status
  `<td>` between the status label and the duration label. Color tiers: green `<60s`,
  amber `60s-10min`, red `>10min`. Skip entirely for terminal-status sessions
- Add distinct glyphs for `paused_circuit` (e.g. `⛌`) vs. `paused` (e.g. `⏸`); add a
  small "→" indicator for `superseded`
- Add a small "ghost" badge next to the status label when `process_alive == False`
- In `session_modal_content.html`, add a "Liveness" sub-table after Timing and before
  SDLC. Render PID + probe-result chip when `pipeline.harness_pid` is not None. Render
  all heartbeat timestamps via `format_timestamp`. Render `recovery_attempts`,
  `reprieve_count`, `current_tool_name`, `watchdog_unhealthy`
- Inline minimal CSS for the chip color tiers and the ghost badge in the existing
  `<style>` block (or in the global stylesheet — match prevailing convention)

### 4. Tests: unit + integration
- **Task ID**: build-tests
- **Depends On**: build-templates
- **Validates**: All new and existing tests pass; coverage on `_check_process_alive` is
  100%
- **Assigned To**: liveness-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_dashboard_liveness_probe.py` covering the helper across all
  branches (alive / ghost / None / negative PID / permission error)
- Create or extend `tests/unit/test_session_executor_pid.py` covering PID write on
  `_on_sdk_started` and clear on session exit; assert ORM save failure does not crash the
  callback or shutdown
- Extend `tests/integration/test_dashboard_endpoint.py` (or create) to assert
  `harness_pid` and the new liveness fields appear in `/dashboard.json` for a session
  with PID set
- Add a smoke render test for the modal template with `pipeline.harness_pid = None` to
  confirm graceful degradation

### 5. Validation
- **Task ID**: validate-liveness
- **Depends On**: build-tests
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Run `pytest tests/ -x -q` and confirm all tests pass
- Start `python -m ui.app`, open `localhost:8500`, confirm row freshness chip renders
  for at least one running session (use a manual test session if needed; clean up after
  per CLAUDE.md "Manual Testing Hygiene")
- Click a session row and confirm the modal shows the new Liveness sub-table with PID
  + probe result + heartbeats
- Confirm `dashboard.json` contains the new fields (`curl -s localhost:8500/dashboard.json
  | jq '.sessions[0] | {harness_pid, last_heartbeat_at, process_alive}'`)
- Confirm no visual regression on the common-case `running`/`completed`/`pending` rows
- Confirm no overlap collision with #549's project-column placement (project still first
  column)

### 6. Documentation
- **Task ID**: document-liveness
- **Depends On**: validate-liveness
- **Assigned To**: liveness-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/dashboard.md` with a new "Liveness signals" section describing
  the freshness chip, lifecycle glyphs, ghost badge, and modal Liveness table
- Add a brief reference in `docs/features/session-lifecycle.md` noting the dashboard
  surfaces all 13 states with distinct iconography
- Verify `docs/features/README.md` index entry for dashboard still applies

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-liveness
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run `pytest tests/ -x -q` after doc changes (no-op, defensive)
- Re-run `python -m ruff check .` and `python -m ruff format --check .`
- Confirm all Success Criteria boxes are checkable
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `harness_pid` field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'harness_pid')"` | exit code 0 |
| Probe helper exists | `python -c "from ui.data.sdlc import _check_process_alive; assert _check_process_alive(None) is None"` | exit code 0 |
| Probe rejects negative PID | `python -c "from ui.data.sdlc import _check_process_alive; assert _check_process_alive(-1) is None"` | exit code 0 |
| Probe detects own PID alive | `python -c "import os; from ui.data.sdlc import _check_process_alive; assert _check_process_alive(os.getpid()) is True"` | exit code 0 |
| Dashboard JSON has new field | `curl -s localhost:8500/dashboard.json \| jq -e '.sessions[0] \| has(\"harness_pid\")'` | output contains `true` |
| Row template renders without error | `python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('ui/templates')); env.get_template('_partials/sessions_table.html')"` | exit code 0 |
| Modal template renders without error | `python -c "from jinja2 import Environment, FileSystemLoader; env=Environment(loader=FileSystemLoader('ui/templates')); env.get_template('_partials/session_modal_content.html')"` | exit code 0 |
| Docs updated | `grep -q 'Liveness signals' docs/features/dashboard.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

These questions remained after spike resolution; they need supervisor input before final
sign-off but do not block initial critique.

1. **Glyph choice for `paused_circuit` vs `paused`** — proposed `⛌` and `⏸` respectively.
   Acceptable, or prefer something else (e.g. `🚧` for circuit-breaker)? Bias toward the
   existing minimal-emoji style of the row.
2. **Color tier thresholds for the freshness chip** — proposed green `<60s`, amber
   `60s-10min`, red `>10min`. The `>10min` matches the existing `is_stale` rule. Keep as
   proposed, or use a finer-grained scale (e.g. add a yellow tier at `5min`)?
3. **Modal copy for the recycled-PID caveat** — should the "alive" probe chip include a
   tooltip warning about PID recycling, or is the staleness chip sufficient operator
   signal?
