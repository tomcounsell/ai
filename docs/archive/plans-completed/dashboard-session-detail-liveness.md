---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1269
last_comment_id:
revision_applied: true
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

### spike-1: PID is already in-memory, only persistence is missing — BUT clearing must pair with subprocess exit, not session exit
- **Assumption**: "PID is not currently persisted on `AgentSession` and the worker has no
  reference to the harness subprocess PID."
- **Method**: code-read (`agent/session_executor.py`, `agent/session_state.py`,
  `agent/sdk_client.py`).
- **Finding**: PID is **already known** in two places:
  1. `agent/sdk_client.py:2469-2486` — `_run_harness_subprocess` fires the `on_sdk_started(pid)`
     callback right after `asyncio.create_subprocess_exec` returns, with `proc.pid`. **The
     callback fires from THREE call sites in `get_response_via_harness`** (primary at
     `sdk_client.py:2205`; image-dimension fallback at `2243`; stale-UUID fallback at `2295`),
     so up to **3 distinct subprocesses can be spawned during a single turn**, each
     overwriting `harness_pid` with its own pid. Only the last surviving pid is the live one
     when the turn ends.
  2. `agent/session_executor.py:1136-1149` — `_on_sdk_started(pid)` writes PID to the
     in-memory `SessionHandle.pid` (`agent/session_state.py:46`) and saves
     `last_sdk_heartbeat_at` to the AgentSession.
  The handle is dropped on session exit (`_active_sessions.pop()` in the `finally` block at
  `agent/session_executor.py:1846-1848`) and on worker restart. **The naive design — write on
  spawn, clear on session exit — leaves a stale, recyclable PID on the record for the entire
  window between the LAST subprocess exiting and the session body finally exiting** (could be
  many seconds while the next nudge / completion runner runs). Persisting safely requires a
  **paired `on_sdk_finished` callback** that clears the PID at `proc.communicate()` return
  inside `_run_harness_subprocess` (`sdk_client.py:2621`) — symmetric with `on_sdk_started`.
- **Confidence**: high
- **Impact on plan**: PID persistence is a sub-task of this plan (not a separate issue). The
  worker-side change is ~10 lines: add `harness_pid = IntField(null=True)` to AgentSession,
  add a parallel `on_sdk_finished()` parameter to `get_response_via_harness` and
  `_run_harness_subprocess`, fire it at all three call sites paired with `on_sdk_started`,
  invoke it after `proc.communicate()` returns. The session-exit `finally` block performs a
  defensive idempotent clear (PID may already be None). Estimated <60 min.

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

1. **Entry point (worker, harness spawn)** — `agent/sdk_client.py:2482` fires `on_sdk_started(pid)`
   immediately after `asyncio.create_subprocess_exec` returns. Up to **3 invocations per
   turn** at call sites `2205` / `2243` / `2295`.
2. **Subprocess exit (NEW callback)** — `agent/sdk_client.py:2621` `proc.communicate()`
   returns once the subprocess has exited. **A new paired `on_sdk_finished()` callback
   fires here**, symmetric with `on_sdk_started`. Wired through `_run_harness_subprocess`
   (`sdk_client.py:2399`) and `get_response_via_harness` (`sdk_client.py:2045`); the latter
   passes the same callback to all three call sites at `2205` / `2243` / `2295`. Like the
   started callback, exceptions are caught and logged at WARNING. The messenger
   (`agent/messenger.py:65-110`) gains a parallel `on_sdk_finished` field and a
   `notify_sdk_finished()` wrapper.
3. **Persistence (worker callbacks)** — `agent/session_executor.py:1136-1149`
   `_on_sdk_started(pid)` writes `session.harness_pid = pid` (NEW field), saves with
   `update_fields=["last_sdk_heartbeat_at", "harness_pid"]`. The new `_on_sdk_finished()`
   sibling closure clears `session.harness_pid = None` and saves with
   `update_fields=["harness_pid"]`. **PID lifecycle is subprocess-scoped, not
   session-scoped.** The session-exit `finally` block (`agent/session_executor.py:1846-1848`)
   performs a defensive idempotent clear in case the subprocess died abnormally before
   `proc.communicate()` could return cleanly (e.g., `CancelledError` propagation).
3. **Read path (UI data builder)** — `ui/data/sdlc.py::_session_to_pipeline` reads
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
- **Interface changes**:
  - One new field on `AgentSession`: `harness_pid: IntField(null=True)`.
  - One new callback on `BossMessenger`: `on_sdk_finished: Callable[[], None] | None`,
    plus a `notify_sdk_finished()` wrapper paralleling `notify_sdk_started`.
  - New `on_sdk_finished` parameter on `_run_harness_subprocess` and
    `get_response_via_harness` in `agent/sdk_client.py`. Default `None` — non-breaking
    for any caller that doesn't pass it.
  - New fields on `PipelineProgress` dataclass: `harness_pid`, `last_heartbeat_at`,
    `last_sdk_heartbeat_at`, `last_stdout_at`, `recovery_attempts`, `reprieve_count`,
    `process_alive`. New keys in `dashboard.json` payload (additive — no breaking change).
- **Coupling**: Mild increase. `ui/data/sdlc.py` gains a ~10-line liveness probe helper.
  `agent/session_executor.py` gains a parallel closure `_on_sdk_finished` next to the
  existing `_on_sdk_started`. `agent/sdk_client.py` gains one new parameter and a
  callback fire site. `agent/messenger.py` gains one new field + wrapper.
- **Data ownership**: PID is owned by the worker (single writer:
  `_execute_agent_session` for its own session, via the paired
  `_on_sdk_started` / `_on_sdk_finished` closures). Read by UI only.
- **Reversibility**: Trivial. Remove the new field, the new callback, and the template
  additions. PID-using helpers gracefully degrade when PID is None (already the design),
  and the new callback default is `None` so removing it does not break any consumer.

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

- **`AgentSession.harness_pid`**: New nullable IntField. Subprocess-scoped lifecycle:
  written by the worker when each harness subprocess spawns, cleared the instant
  `proc.communicate()` returns for that subprocess. Defensive idempotent clear at session
  exit covers abnormal termination paths.
- **Paired `on_sdk_started` / `on_sdk_finished` callbacks**: Two-callback contract added to
  `BossMessenger` (`agent/messenger.py`) and threaded through `get_response_via_harness` /
  `_run_harness_subprocess` (`agent/sdk_client.py`). Both callbacks fire at all three
  subprocess call sites (`sdk_client.py:2205, 2243, 2295`); each subprocess invocation owns
  the field exclusively for its runtime.
- **Worker callback PID persistence**: `_on_sdk_started(pid)` writes
  `session.harness_pid = pid`; `_on_sdk_finished()` writes `session.harness_pid = None`.
  Both saves use `update_fields=["harness_pid"]` (or `["last_sdk_heartbeat_at",
  "harness_pid"]` on the started callback) so concurrent writes to other fields are not
  clobbered.
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
   `last_stdout_at` block (`models/agent_session.py:281`). Comment block documents the
   single-writer contract (the paired `_on_sdk_started` / `_on_sdk_finished` closures owned
   by `_execute_agent_session`) and the subprocess-scoped lifecycle invariant.
2. **Messenger callback shape**: In `agent/messenger.py:65-110`, add `on_sdk_finished:
   Callable[[], None] | None = None` field (parallel to the existing `on_sdk_started`,
   `on_heartbeat_tick`, `on_stdout_event` fields) and a `notify_sdk_finished()` wrapper
   matching the existing pattern (catch exceptions, log WARNING).
3. **SDK client wiring**: In `agent/sdk_client.py`, add an `on_sdk_finished` parameter to
   both `_run_harness_subprocess` (line 2399) and `get_response_via_harness` (line 2045).
   In `_run_harness_subprocess`, fire the callback after `proc.communicate()` returns
   (just after line 2621, in a `try/except Exception` block matching the pattern at lines
   2482-2486). In `get_response_via_harness`, pass `on_sdk_finished=on_sdk_finished` to
   all three `_run_harness_subprocess` call sites at lines 2205, 2243, 2295 paired with
   the existing `on_sdk_started`. **All three call sites must receive both callbacks** —
   failing to thread one through any of the three would leak a stale PID for that path.
4. **Worker write paths**: Modify `_on_sdk_started` (`agent/session_executor.py:1136-1149`)
   to also set `session.harness_pid = pid` and include it in `update_fields`. Add a sibling
   closure `_on_sdk_finished()` that does `session.harness_pid = None;
   session.save(update_fields=["harness_pid"])` inside a `try/except` matching the existing
   pattern. Wire it into the `BossMessenger(...)` construction at
   `session_executor.py:1173-1180` as a fourth keyword argument. The existing
   `finally` block at `session_executor.py:1846-1848` keeps a defensive idempotent clear
   (best-effort `try/except`) for the abnormal-termination path where
   `_on_sdk_finished` could not fire (worker crash, `CancelledError` propagation).
5. **Data builder**: In `ui/data/sdlc.py::_session_to_pipeline` (around line 629-647),
   read the new ORM fields and call `_check_process_alive(pid)` for non-terminal sessions
   only. Populate the new `PipelineProgress` fields. Pre-existing `last_evidence_at`
   computation already aggregates the right timestamps.
6. **Liveness probe helper** (`ui/data/sdlc.py`):
   ```python
   def _check_process_alive(pid: int | None) -> bool | None:
       if pid is None or pid <= 0:
           # pid <= 0 guard: kill(0, ...) and kill(-pid, ...) have process-group
           # semantics on Linux/macOS — refuse to probe rather than risk a wrong answer.
           return None
       try:
           os.kill(pid, 0)
           return True
       except ProcessLookupError:
           return False
       except (PermissionError, OSError):
           return None  # uncertain — don't lie
   ```
7. **JSON serialization**: Add new fields to `_session_to_json` (`ui/app.py:368-424`),
   maintaining the existing additive contract.
8. **Row template** (`sessions_table.html`): Add a freshness chip span inside the status
   `<td>`, between the status label and duration. Add a distinct glyph for `paused_circuit`
   and `superseded`. Add a ghost indicator when `process_alive == False`.
9. **Modal template** (`session_modal_content.html`): Add a Liveness table after Timing,
   before SDLC. Include PID + probe-result chip (when PID present). Include all heartbeat
   timestamps via `format_timestamp`. Include `recovery_attempts`, `reprieve_count`,
   `current_tool_name`, `watchdog_unhealthy`.
10. **Color tiers** (CSS, in template `<style>` or inline): green `< 60s`, amber `60s-10min`,
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

Verified file paths against the current tree (2026-05-04). The previous draft cited
`test_dashboard_data.py`, `test_session_executor.py`, `test_dashboard_endpoint.py` — none
of those exist. Closest existing tests are listed below, with explicit dispositions.

- [ ] `tests/unit/test_dashboard_pillar_a_fields.py` — UPDATE: this file already covers
      `PipelineProgress`'s Pillar A liveness fields (`current_tool_name`,
      `last_tool_use_at`, `last_turn_at`, `last_evidence_at`). Extend it to also assert
      the new `harness_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`,
      `last_stdout_at`, `recovery_attempts`, `reprieve_count`, `process_alive` fields are
      populated from `AgentSession`.
- [ ] `tests/unit/test_messenger_callbacks.py` — UPDATE: this file already covers
      `BossMessenger.notify_sdk_started` and the existing two callbacks. Add a parallel
      `notify_sdk_finished` test once the callback is added to the dataclass; assert the
      callback fires once per subprocess exit and its exceptions are caught + logged.
- [ ] `tests/unit/test_agent_session_liveness_fields.py` — UPDATE: this file covers the
      existing liveness fields on AgentSession. Add `harness_pid` to the field-existence
      assertion and the IntField nullability assertion.
- [ ] `tests/unit/test_ui_sdlc_data.py` — UPDATE: this file covers `_session_to_pipeline`
      and related ui-data unit logic. Add assertions for the new `PipelineProgress`
      fields, including `process_alive` is None for terminal-status sessions and not None
      for non-terminal status with PID set.
- [ ] No DELETE or REPLACE — all changes are additive to existing test scope.

### New test files (CREATE)

- [ ] `tests/unit/test_dashboard_liveness_probe.py` — unit-test the `_check_process_alive`
      helper across all branches (alive / ghost / None / negative-PID / zero-PID /
      `PermissionError` / generic `OSError`). Use `monkeypatch` on `os.kill` for the
      injected-error branches; use `os.getpid()` for the alive branch and a freshly-spawned-
      then-`wait()`-ed dummy process pid for the ghost branch.
- [ ] `tests/unit/test_session_executor_pid.py` — cover (a) `_on_sdk_started` writes
      `harness_pid` and includes it in `update_fields`, (b) the new `_on_sdk_finished`
      callback clears `harness_pid` to None at subprocess exit and includes it in
      `update_fields`, (c) the session-exit `finally` block performs a defensive idempotent
      clear (no-op if already cleared), (d) ORM save failures during shutdown do not mask
      the original exception. The multi-spawn case (3 subprocesses in one turn) is covered
      by asserting `_on_sdk_started` followed by `_on_sdk_finished` repeats N times in
      sequence and the final post-callback state is `harness_pid is None`.
- [ ] `tests/integration/test_dashboard_liveness_endpoint.py` — GET `/dashboard.json`
      with a synthetic AgentSession that has `harness_pid = os.getpid()`, assert the JSON
      payload exposes `harness_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`,
      `last_stdout_at`, `recovery_attempts`, `reprieve_count`, `process_alive`.

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

### Risk 1: PID recycling produces false-positive "alive" results — and the worker is the most likely recycler
**Impact:** A session whose harness subprocess has died but whose PID was then reassigned
to an unrelated process would show "alive" in the modal, misleading the operator.
**The worker is the most prolific PID consumer on this host.** Each PM/Dev session runs
`gh`, `git`, `pytest`, `ruff`, build subprocesses, MCP servers, and additional `claude`
harness invocations — Linux/macOS reuse the lowest-available PID once the kernel wraps the
PID counter, and a busy worker can churn through hundreds of PIDs per minute. The naive
"PID recycling is rare" framing was wrong: on this deployment, recycling happens *while
the dashboard is rendering*.
**Mitigation (multi-layer):**
1. **Subprocess-scoped PID lifecycle** (the principal mitigation, see spike-1): `harness_pid`
   is cleared by the paired `on_sdk_finished` callback at `proc.communicate()` return —
   immediately, not at session exit. The operator-visible window where a stale PID could
   match a recycled process is reduced from "session lifetime" to "the gap between
   `proc.communicate()` returning and the worker firing the cleanup callback" (single-digit
   milliseconds).
2. **Pair the probe with `last_evidence_at` age**: if the probe says "alive" but
   `last_evidence_at` is >10 min stale, the freshness chip is red — the operator sees both
   signals. The chip dominates the row's visual weight; the modal's "alive" badge cannot
   silently fool an operator who can see the row.
3. **Modal copy**: the "alive" chip carries a tooltip explaining the recycled-PID caveat so
   the operator knows to corroborate against the staleness chip if anything looks off.
4. **No process-exe verification in this plan** (rabbit-holed): we do not snapshot
   `psutil.Process(pid).exe()` at spawn and re-read at probe time to confirm same-binary.
   That's a future hardening if the multi-layer defense above proves insufficient — see
   the explicit No-Go.

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

### Race 1: Multi-spawn turn — subprocess A's PID overwritten by subprocess B before A is cleared
**Location:** `agent/sdk_client.py:2205, 2243, 2295` — three call sites in
`get_response_via_harness` that each invoke `_run_harness_subprocess` with the same
`on_sdk_started` callback. A single turn can spawn the primary subprocess plus an
image-dimension fallback plus a stale-UUID fallback, sequentially.
**Trigger:** A turn that hits one of the fallback paths. The naive design (write-on-spawn,
clear-on-session-exit) leaves PID A in the field when subprocess A exits, then overwrites
with PID B when subprocess B starts — UI sees PID A then PID B. If A's PID has been recycled
by the time the dashboard polls between the two spawns, the probe lies.
**Data prerequisite:** Subprocesses run sequentially — `proc.communicate()` for subprocess A
returns BEFORE subprocess B is spawned. There is never an interleaving where two harness
subprocesses are concurrently writing PID.
**State prerequisite:** The session is mid-turn; the worker holds the per-session lock so no
other turn for this session is in flight.
**Mitigation:** Pair `on_sdk_started` with `on_sdk_finished` (see spike-1). After
`proc.communicate()` returns inside `_run_harness_subprocess`, fire `on_sdk_finished()`
which clears `session.harness_pid = None`. Each subprocess invocation owns the field for
its own runtime, between the start and finish callbacks. Between subprocess A finishing and
subprocess B starting, the field is correctly None.

### Race 2: PID written then cleared between two reads
**Location:** `agent/session_executor.py:1136` (write via `_on_sdk_started`) paired with
the new `_on_sdk_finished` clear at the symmetric exit point. UI reads happen at any moment.
**Trigger:** Subprocess exits between the dashboard's `partial_sessions_table` poll and the
operator clicking the row to open the modal.
**Data prerequisite:** None — both reads are point-in-time and `harness_pid` is read
directly from Popoto, not cached.
**State prerequisite:** None — the model field is the single source of truth.
**Mitigation:** `_check_process_alive(None)` returns `None`, modal renders "unknown".
Acceptable — the operator's question ("is this alive?") is answered correctly: between
subprocesses, no harness is running. The freshness chip remains the dominant signal.

### Race 3: Probe runs while worker is mid-restart
**Location:** Worker restart cycle (`scripts/valor-service.sh restart`).
**Trigger:** Dashboard polls during the ~1s window where the old worker has exited but the
session record still says `running` (the watchdog will reap shortly).
**Data prerequisite:** AgentSession `harness_pid` field has the old (now-dead) PID.
**State prerequisite:** Worker is restarting; PID does not exist.
**Mitigation:** Probe returns `False`, freshness chip is red, modal shows "ghost". This is
**desired behavior** — the operator sees the ghost during the watchdog reap window. No
mitigation needed; this is the feature working correctly.

### Race 4: PID recycled to a worker-spawned `gh`/`git`/`pytest` between subprocess exit and finish-callback fire
**Location:** Inside `_run_harness_subprocess`, between `proc.communicate()` returning at
`agent/sdk_client.py:2621` and the `on_sdk_finished` callback firing.
**Trigger:** The OS reaps the harness subprocess, the worker's own `gh`/`git`/build
subprocesses (started by parallel agent activity) inherit the freed PID, and the dashboard
polls in that gap.
**Data prerequisite:** A worker spawning enough subprocesses to make recycling probable on
this host (true in normal operation — see Risk 1).
**State prerequisite:** PID counter near wrap; the freed harness PID is the lowest available.
**Mitigation:** The gap is single-digit milliseconds (synchronous fall-through from
`proc.communicate()` return to the callback) — far smaller than the 5s dashboard poll
cadence, so the dashboard typically polls outside this window. When the dashboard does poll
inside it, the staleness chip remains the operator's primary signal: a recycled-PID "alive"
read still pairs with a stale `last_evidence_at` (no evidence is being recorded for the dead
harness), so the row's freshness chip is red. The modal's tooltip on the "alive" badge
documents the caveat. Acceptable residual risk; full process-exe verification is explicitly
rabbit-holed.

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
- **Process-exe verification** (`psutil.Process(pid).exe()` snapshot at spawn, re-read at
  probe time, compare strings). This would close the residual recycled-PID window in Risk
  1 / Race 4 but adds storage on `AgentSession`, ORM writes per spawn, and probe latency.
  Subprocess-scoped PID lifecycle plus the staleness chip should be sufficient; if
  operational data shows otherwise, this is the obvious follow-up.

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
      (the paired `_on_sdk_started` / `_on_sdk_finished` closures owned by
      `_execute_agent_session`) and the subprocess-scoped lifecycle invariant
      (set-on-spawn, clear-on-`proc.communicate()`-return, defensive idempotent
      clear-on-session-exit).
- [ ] Docstring on the new `notify_sdk_finished()` wrapper in `BossMessenger` matching the
      existing `notify_sdk_started` / `notify_heartbeat_tick` / `notify_stdout_event`
      docstring style; explicitly note "fires once per subprocess exit".
- [ ] Comment on the freshness chip color tiers in the row template.

## Success Criteria

- [ ] All 8 non-terminal lifecycle states are visually distinguishable somewhere on the
      dashboard surface (row glyph or modal). `paused` and `paused_circuit` are
      distinguishable.
- [ ] Modal renders, when present on the model: `last_heartbeat_at`, `last_sdk_heartbeat_at`,
      `last_stdout_at`, `last_evidence_at`, `last_tool_use_at`, `last_turn_at`,
      `recovery_attempts`, `reprieve_count`, `current_tool_name`, `watchdog_unhealthy`,
      `harness_pid`. Each timestamp uses the `format_timestamp` filter.
- [ ] PID is persisted on `AgentSession` via the worker `_on_sdk_started` callback at each
      harness subprocess spawn and cleared by the paired `_on_sdk_finished` callback at
      `proc.communicate()` return for that same subprocess. The session-exit `finally`
      block performs a defensive idempotent clear. ORM write failures log warnings but do
      not crash. After a multi-spawn turn (primary + image-dimension fallback +
      stale-UUID fallback) the field is correctly None between subprocesses and after the
      last subprocess exits.
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
    `_session_to_pipeline`, extend `_session_to_json`
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

### 1. PID persistence: model + paired callback contract
- **Task ID**: build-pid-persistence
- **Depends On**: none
- **Validates**: `tests/unit/test_session_executor_pid.py` (create),
  `tests/unit/test_messenger_callbacks.py` (extend), existing
  `tests/unit/test_agent_session_liveness_fields.py` and
  `tests/unit/test_messenger.py` must still pass
- **Informed By**: spike-1 (PID already known in handle; clearing must pair with
  subprocess exit, not session exit, to defeat PID recycling); BLOCKER 1 (multi-spawn
  per turn at `sdk_client.py:2205`/`2243`/`2295` makes session-scoped clear unsafe)
- **Assigned To**: pid-persistence-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `harness_pid = IntField(null=True)` to `AgentSession` after the existing
  `last_stdout_at` block (`models/agent_session.py:281`)
- Add `on_sdk_finished: Callable[[], None] | None = None` field and a
  `notify_sdk_finished()` wrapper to `BossMessenger` (`agent/messenger.py:65-110`),
  matching the existing `on_sdk_started` pattern (catch exceptions, log WARNING)
- In `agent/sdk_client.py`, add an `on_sdk_finished` parameter to both
  `_run_harness_subprocess` (line 2399) and `get_response_via_harness` (line 2045).
  Fire the callback after `proc.communicate()` returns (just after line 2621) inside a
  `try/except Exception` block matching the pattern at lines 2482-2486
- Pass `on_sdk_finished=on_sdk_finished` to all three `_run_harness_subprocess` call
  sites in `get_response_via_harness` at lines 2205, 2243, 2295 paired with the
  existing `on_sdk_started`. **All three sites must thread both callbacks** — failing
  to wire one site leaks a stale PID for that path
- Modify `_on_sdk_started` (`agent/session_executor.py:1136-1149`) to set
  `session.harness_pid = pid` and add to `update_fields=["last_sdk_heartbeat_at",
  "harness_pid"]`
- Add a sibling closure `_on_sdk_finished()` next to `_on_sdk_started` that does
  `session.harness_pid = None; session.save(update_fields=["harness_pid"])` in a
  `try/except` matching the existing pattern. Wire it into the `BossMessenger(...)`
  construction at `session_executor.py:1173-1180` as a fourth keyword argument
- In the existing `finally` block at `agent/session_executor.py:1846-1848`, wrap a
  best-effort `session.harness_pid = None; session.save(update_fields=["harness_pid"])`
  in `try/except` for the abnormal-termination path (worker crash,
  `CancelledError` propagation). Idempotent — must succeed even if the field is
  already None
- Document the single-writer contract in a comment block above the new field on
  `AgentSession`, calling out the subprocess-scoped lifecycle invariant explicitly

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
- In `_session_to_pipeline`, read the new ORM fields and call `_check_process_alive(pid)`
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
  branches (alive / ghost / None / negative PID / zero PID / permission error / generic
  OSError). For ghost branch use `subprocess.Popen(["true"]); proc.wait()` (POSIX-portable,
  zero flake risk) and capture pid before wait
- Create `tests/unit/test_session_executor_pid.py` covering: (a) `_on_sdk_started` writes
  `harness_pid` and includes it in `update_fields`; (b) the new `_on_sdk_finished` callback
  clears `harness_pid` at subprocess exit and includes it in `update_fields`; (c) the
  session-exit `finally` block performs a defensive idempotent clear (no-op if already
  cleared); (d) ORM save failures during shutdown do not mask the original exception;
  (e) **multi-spawn case**: simulate 3 sequential subprocesses (mimicking primary +
  image-dimension fallback + stale-UUID fallback) and assert started/finished pairs
  alternate cleanly with the field correctly None between subprocesses and after the last
  exits
- Extend `tests/unit/test_messenger_callbacks.py` to cover the new `notify_sdk_finished()`
  wrapper: assert it fires `on_sdk_finished` when set, no-ops when None, catches and logs
  exceptions at WARNING
- Create `tests/integration/test_dashboard_liveness_endpoint.py` to assert `harness_pid`
  and the new liveness fields appear in `/dashboard.json` for a synthetic AgentSession
  with `harness_pid = os.getpid()`
- Extend `tests/unit/test_dashboard_pillar_a_fields.py` to assert the new
  `PipelineProgress` fields (`harness_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`,
  `last_stdout_at`, `recovery_attempts`, `reprieve_count`, `process_alive`) are populated
  from `AgentSession`
- Extend `tests/unit/test_agent_session_liveness_fields.py` to assert `harness_pid` exists
  on `AgentSession` as a nullable IntField
- Extend `tests/unit/test_ui_sdlc_data.py` to assert `_check_process_alive` is invoked
  only for non-terminal status values
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

<!-- Populated by /do-plan-critique cycle 1 (NEEDS REVISION). Implementation Notes
     embedded in plan body via revision pass dated 2026-05-04. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator + Adversary | Stale PID from multi-spawn — `_on_sdk_started(pid)` fires up to 3× per turn at `agent/sdk_client.py:2205`/`2243`/`2295`; PID is cleared only at session exit. Between subprocess A exit and subprocess B spawn (or session end), gh/git/build subprocesses on the same worker can recycle the freed PID and the probe will lie. | Spike-1, Solution Key Elements, Technical Approach steps 2-4, Step-by-Step Tasks task 1, Race Conditions Race 1+4, Risk 1 | Add `on_sdk_finished: Callable[[], None] \| None` to `BossMessenger` (`agent/messenger.py:65-110`) with a `notify_sdk_finished()` wrapper. Thread `on_sdk_finished` through `_run_harness_subprocess` (`agent/sdk_client.py:2399`) and fire it just after `proc.communicate()` returns at `sdk_client.py:2621` inside `try/except Exception` matching the started-callback pattern. Pass it from all 3 call sites (`2205`/`2243`/`2295`). On the worker, define `_on_sdk_finished()` closure paired with `_on_sdk_started`; clear `session.harness_pid = None; session.save(update_fields=["harness_pid"])` in `try/except`. Wire as 4th kwarg to `BossMessenger(...)` at `session_executor.py:1173-1180`. Keep the session-exit `finally` block clear as a defensive idempotent backstop only. |
| BLOCKER | Archaeologist | Wrong function name + wrong test paths — plan referenced `_session_to_progress` (×4); actual is `_session_to_pipeline` at `ui/data/sdlc.py:540`. Cited test paths `test_dashboard_data.py` / `test_session_executor.py` / `test_dashboard_endpoint.py` don't exist. | Throughout (replaced via global edit), Test Impact section rewritten | Use `_session_to_pipeline` everywhere. Existing tests to extend: `tests/unit/test_dashboard_pillar_a_fields.py` (PipelineProgress fields), `tests/unit/test_messenger_callbacks.py` (notify_sdk_started — extend with notify_sdk_finished), `tests/unit/test_agent_session_liveness_fields.py` (AgentSession schema), `tests/unit/test_ui_sdlc_data.py` (`_session_to_pipeline` logic). New test files: `tests/unit/test_dashboard_liveness_probe.py`, `tests/unit/test_session_executor_pid.py`, `tests/integration/test_dashboard_liveness_endpoint.py`. |
| BLOCKER | Skeptic | Risk-section underweights PID recycling — Race Conditions framed recycling as "rare," but the worker is itself the most likely PID consumer (each session runs gh/git/pytest/ruff/MCP subprocesses; PID counter wraps quickly on a busy host). | Risk 1 rewritten with multi-layer mitigation; Race 4 added for recycled-PID-during-callback-gap; Race 1 added for multi-spawn lifecycle; explicit No-Go for process-exe verification with rationale | Worker is the principal recycler. Subprocess-scoped PID lifecycle (BLOCKER 1's fix) reduces the operator-visible stale-PID window from "session lifetime" to "single-digit milliseconds between `proc.communicate()` returning and the cleanup callback firing." Pair with the staleness chip (`last_evidence_at` >10min → red) so a recycled-PID "alive" still pairs with a stale freshness chip. Document in modal copy. Process-exe verification is rabbit-holed unless residual data shows it's needed. |
| CONCERN | Simplifier + Operator | Test Impact citations point to non-existent files; cleanup-by-CLAUDE.md "Manual Testing Hygiene" reference in validate step had no concrete cleanup recipe. | Test Impact rewritten with verified file paths; Step 5 (validate-liveness) inherits CLAUDE.md cleanup pattern (synthetic test sessions deleted via `AgentSession.query.filter(...).delete()`) | Verified extant test files in `tests/unit/`: `test_dashboard_pillar_a_fields.py`, `test_messenger_callbacks.py`, `test_agent_session_liveness_fields.py`, `test_ui_sdlc_data.py`, `test_session_executor_*.py`. Validator session must use a `dbg-` prefixed `project_key` and delete after run. |
| CONCERN | User | Recycled-PID caveat in modal copy was vague ("Document the tradeoff in the modal copy"); did not specify exact wording or tooltip mechanism. | Risk 1 mitigation 3 + Open Questions Q3 | Tooltip on the "alive" chip: "alive — process exists; PID may be recycled across worker restarts. Cross-check the freshness chip if the row looks stale." Final wording is the Q3 Open Question. |
| CONCERN | Adversary | The pid <= 0 guard rationale was weak — comment said "process-group semantics" but didn't tie back to why we refuse to probe. | Technical Approach step 6 inline comment, Failure Path Test Strategy | Comment in helper: "kill(0, ...) and kill(-pid, ...) have process-group semantics on Linux/macOS — refuse to probe rather than risk a wrong answer." Test asserts both `_check_process_alive(0)` and `_check_process_alive(-1)` return None. |
| CONCERN | Operator | Step 1 task said "Parallel: true" but added work (BLOCKER 1 fix) makes it the longest-pole task with downstream dependencies on the new messenger field for tests. Other builders cannot start until messenger surface is stable. | Step by Step Tasks Step 1 (renamed to "PID persistence: model + paired callback contract") | Task 1 still labeled `Parallel: true` because it has no upstream deps, but its scope grew. Task 4 (tests) explicitly extends `test_messenger_callbacks.py`, which can only proceed after Task 1's messenger field lands. Sequencing preserved by existing `Depends On` chain (4 → 3 → 2 → 1). |
| CONCERN | Skeptic | Documentation step did not call out the new callback; risk that `docs/features/dashboard.md` describes a stale single-callback contract while code ships paired callbacks. | Step 6 (document-liveness) implicit; Inline Documentation list extended | Documentarian must update `docs/features/dashboard.md` Liveness signals section AND `docs/features/bridge-worker-architecture.md` (Worker callback contract subsection) to describe `on_sdk_started` / `on_sdk_finished` as a paired contract with subprocess-scoped lifecycle. |
| CONCERN | Archaeologist | Verification table's Probe-detects-own-PID test passes the running pid but doesn't verify ghost detection in CI (creating-then-killing a child process is OS-dependent and can be flaky). | Verification section + new test | Use `subprocess.Popen(["true"]); proc.wait()` in the unit test (POSIX-portable; "true" returns instantly). Capture pid before wait, assert `_check_process_alive(pid)` returns False after. Document the pattern in the test docstring so future contributors don't replace it with a flaky sleep-based test. |

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
