---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1536
last_comment_id:
revision_applied: false
refreshed: 2026-06-15
freshness_disposition: Major drift
---

# Session Telemetry Recorder (v1 of epic #1536)

## Problem

When a `claude -p` session misbehaves, we have no durable record of *what it was doing*. The production execution path parses the harness stream-json event stream for aggregate token counts (`agent/sdk_client.py:286-391`) and **discards every event after that**. There is no queryable, per-event trace.

This bit us on **2026-05-31**: a PM session's subprocess hung for 25.5h emitting zero output, wedged the worker's only execution slot, and required a manual `worker-restart`. Afterward there was *nothing to inspect* — no event timeline, no record of the status transitions that orphaned it. The root cause (filed as #1537) had to be reconstructed by reading code, not by reading a trace.

A human watching a terminal Claude Code TUI can tell "stuck" from "still working" at a glance — tokens ticking, tools scrolling, the spinner. Our headless worker keeps no comparable record, so it can neither diagnose nor (eventually) learn from session behavior.

**Current behavior:** Stream-json events are parsed for token totals and dropped. The only per-session persistence is aggregate counters on the `AgentSession` DB record and a coarse `session_events` lifecycle log.

**Desired outcome:** Every session writes a durable, per-event telemetry trace (token deltas, tool-call boundaries, turn events, idle gaps, synthetic timeout/decode/broken-pipe events, and — critically — status transitions with subprocess-kill outcomes). A human can retrieve and read any session's event timeline by `session_id`. This is the **record substrate** the rest of epic #1536 (learning, crash/resume, behavioral capture) builds on; v1 records and displays only.

## Freshness Check

**Baseline commit:** `215aca3ed`
**Issue filed at:** 2026-06-01T06:45:34Z (same session as planning)
**Disposition:** Unchanged (minor drift)

**File:line references re-verified:**
- `agent/sdk_client.py:286` — `accumulate_session_tokens` definition — still holds.
- `agent/sdk_client.py:~1760-1820` — harness/SDK `ResultMessage` handling loop (calls `record_session_activity`, `record_turn_count`, `accumulate_session_tokens`) — still holds; this is the tap point.
- `agent/session_executor.py:626, 1300, 1330` — `last_heartbeat_at` / `last_sdk_heartbeat_at` writes — still holds (issue cited 1769-1806 for the heartbeat loop; confirmed present).
- `agent/messenger.py:445-451` — `SDK heartbeat` log line; `:176` `has_communicated`; `:113` `notify_heartbeat_tick` — still holds.
- `agent/session_health.py:226` `SDK_PROGRESS_FRESHNESS_WINDOW=1800`, `:266` `NO_OUTPUT_BUDGET_SECONDS=1800`, `:1154-1191` recovery transition, `:1304/:1436` running-only query, `:1555-1558` terminal-only reaper — all confirmed by the spike.
- `models/agent_session.py:184` `session_events` ListField; `:1684` `add_event`; `models/session_event.py:30` `SessionEvent` — confirmed coarse lifecycle log, not a telemetry sink.

**Cited sibling issues/PRs re-checked:**
- PR #1487 (`session/granite-agent-loop-poc`) — still OPEN. Event vocabulary confirmed in `agent/claude_session.py` on the branch: `{"type": "result|user|timeout|decode_error|broken_pipe", ...}`.
- #1172 (retired stdout-silence kill), #1226/#1356 (two-tier liveness), #1271 (orphan reaper), #1270 (tool-timeout tiers) — all closed/merged; constraints still apply.

**Commits on main since issue filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none found touching session telemetry/health.

**Notes:** Issue and plan authored in the same session; freshness was effectively trivial at authoring time.

---

### Freshness re-check — 2026-06-15 (refresh pass, baseline `0d000e59`)

**Disposition: Major drift.** Two weeks elapsed since authoring; multiple commits landed on main that invalidate this plan's load-bearing premises. The v1 feature (a per-event telemetry recorder) is still wanted — epic #1536 remains OPEN — but the plan's motivating narrative, its recovery call-site design, and its #1487 schema-compatibility thesis are all stale and must be reworked before build.

**Commits on main since baseline `215aca3ed` touching referenced files:** 12, including:
- **`4ca97abe` PR #1557 — "Liveness recovery confirms subprocess death before requeue (#1537)" — closes the motivating bug.**
- `dd926192` PR #1691 — merged PM/Dev into single Eng role; collapsed SessionType to {eng, teammate, granite}.
- `e702cf9c` PR #1662 — gated own-progress fields on heartbeat freshness (zombie-recovery).
- `09313109`/`971b77d6`/`d005aaa2` — granite **PTY** container production cutover + persona-priming refactor (the direction PR #1487 actually shipped).

**Stale premises (must be revised before this plan is BUILD-ready):**

1. **#1537 is FIXED (closed 2026-06-03, PR #1557).** The plan's Problem statement, Spike-1, Prior Art, Risks, No-Gos, and Success Criteria all treat #1537 as an *open* recovery blind spot and frame `status_transition` as "the telemetry that makes it diagnosable." That framing is now retrospective. The bug the plan was built around no longer exists. The v1 value proposition must be re-justified on the still-valid grounds (durable per-event trace for any future hang / behavioral learning), not on diagnosing #1537.

2. **Recovery call-site restructured — kill outcome is now structured, not hand-threaded.** The plan's Data Flow #4, Solution, and Task 2 thread `kill_issued`/`subprocess_exited`/`pid` down from `agent/session_health.py:1184` via an "optional kwarg / the `reason` string." That call site is gone. PR #1557 introduced `_confirm_subprocess_dead(pid, *, timeout)` returning a `SubprocessKillResult` (`agent/session_health.py:1020-1132`), and the recovery transition now lives in `_apply_recovery_transition` (`:1132`, kill confirmation at `:1318-1341`). The telemetry `status_transition` event should *consume `SubprocessKillResult`* at this new site rather than invent a kwarg-threading mechanism. **Spike-1's entire finding is obsolete and must be re-run or struck.**

3. **#1487 event-vocabulary thesis is unfounded.** PR #1487 **merged 2026-06-01** — but as the *granite PTY PoC*, which took a PTY/TUI direction, NOT the stream-json `ClaudeSession` line-reader the plan productionizes. `grep -rn 'decode_error\|broken_pipe' agent/` now returns **zero hits** — the synthetic-event vocabulary the schema claims to be "#1487-compatible with" is not in the codebase. The "reuse #1487's `timeout`/`decode_error`/`broken_pipe` payloads verbatim" requirement (Solution, Technical Approach, Risk 3, Success Criteria) rests on code that did not land. This must be dropped or re-grounded (e.g. define the schema standalone, or source the vocabulary from the granite PTY trace format that actually shipped).

**File:line anchors re-verified (symbols survive; line numbers drifted):**
- `models/session_lifecycle.py` — `finalize_session` now `:217`, `transition_status` now `:453` — both still free functions. ✔ (these two tap points are intact and remain correct.)
- `agent/sdk_client.py` — `accumulate_session_tokens` `:286`, harness `usage` tap region ~`:2768`, SDK `ResultMessage` handler ~`:1760` — all present; the per-event dispatch loop is intact, so the additive-tap approach still works. ✔
- `agent/session_health.py` — `cleanup_corrupted_agent_sessions` now `:2418` (plan says `:2144`); budget constants `NO_OUTPUT_BUDGET_SECONDS`/`SDK_PROGRESS_FRESHNESS_WINDOW` present. Retention-sweep anchor is valid but renumbered.
- `tools/valor_session.py` — `main()` now `:1273` (plan says `:1185`), `__main__` `:1442`; still NOT a declared console script (Agent Integration claim holds). ✔

**Required action:** Re-run /do-plan revision (or re-spike) to: (a) re-base the value proposition off #1536's durable-trace goal rather than the now-fixed #1537; (b) consume `SubprocessKillResult` at `_apply_recovery_transition` instead of kwarg-threading; (c) drop or re-ground the #1487 schema-compatibility requirement; (d) refresh all drifted file:line numbers. The two `session_lifecycle.py` tap points and the additive `sdk_client.py` taps survive unchanged.

## Prior Art

- **#1487 (open PoC)**: `ClaudeSession` reads stream-json line-by-line with a per-line `select()` deadline and emits synthetic `timeout`/`decode_error`/`broken_pipe` events to `logs/granite_poc_trace.jsonl`. **Source of the typed-event vocabulary and the JSONL-trace approach this plan productionizes.** Not wired into `worker/`.
- **#1128 (closed)**: Added per-session token tracking (`accumulate_session_tokens`) — the existing tap we extend.
- **#1226 / #1356 (closed)**: Two-tier liveness check with per-turn SDK progress fields (`last_tool_use_at`, `last_turn_at`) and the no-output budget. These fields are exactly the kind of signal v1 records as events.
- **#1172 (closed)**: Retired `last_stdout_at` stdout-silence killing — silence ≠ failure. **Constraint:** v1 records idle gaps as facts, never resurrects silence-as-kill.
- **#1537 (open, filed during this plan's spike)**: The recovery blind-spot bug that caused the 25.5h hang. v1 does NOT fix it, but v1's `status_transition` event is the telemetry that makes it (and recurrences) diagnosable.

## Research

No relevant external findings — proceeding with codebase context and PR #1487. The stream-json protocol, JSONL sink, and consumer are all internal; no external libraries or APIs are introduced.

## Spike Results

### spike-1: Why did the two-tier liveness check not recover the 25.5h hang?
- **Assumption**: "DB-state drift to `pending` decoupled the session record from the live stuck subprocess."
- **Method**: code-read (Explore agent over `agent/session_health.py`, executor, reaper).
- **Finding**: **Confirmed and refined.** The recovery transition requeues a no-progress session to `pending` and calls `task.cancel()` (≈0.25s timeout) but never confirms the subprocess exited (`agent/session_health.py:1086-1191`). The orphaned `claude -p` then falls into a three-way blind spot: forward health check queries only `status="running"` (`:1304`, `:1436`); the in-process reaper acts only on *terminal*-status sessions (`:1555-1558`); the #1271 cross-process reaper skips it because PPID≠1 (worker alive). Secondary: nulling `started_at` on requeue restarts the no-output-budget clock from `created_at`.
- **Confidence**: high (file:line evidence throughout).
- **Impact on plan**: (1) The bug itself is **out of scope for v1** — filed as #1537. (2) It pins the single most diagnostic telemetry event: a **`status_transition`** record carrying `from`, `to`, `reason`, `kill_issued`, `subprocess_exited`, `pid`. The recorder must capture this, because the failure was invisible precisely at status-transition time. (3) Idle-gap events alone would NOT have caught this (the session left `running`); the status-transition trail is what reveals the orphaning.

## Data Flow

1. **Entry point**: Worker executes a session → `agent/sdk_client.py` spawns `claude -p --output-format stream-json` (harness path) or runs the `ClaudeSDKClient` query loop.
2. **Stream parse loop** (`agent/sdk_client.py` `_run_harness_subprocess` / the `ResultMessage`/`AssistantMessage` handlers ~1760-1820): each event is already iterated and dispatched to `record_session_activity`, `record_turn_count`, `accumulate_session_tokens`. **New:** alongside these calls, invoke `record_telemetry_event(session_id, event)` — no second parse.
3. **Telemetry helper** (`agent/session_telemetry.py`, new): normalizes the event to `{session_id, ts, type, ...payload}`, derives `idle_gap` from the per-session last-event timestamp, and appends one JSON line to the session's trace file. Fire-and-forget (never raises into the hot loop).
4. **Status transitions** (`models/session_lifecycle.py`): emit a `status_transition` telemetry event from BOTH `transition_status` (`:453`, non-terminal — captures the requeue-to-`pending` orphaning moment) AND `finalize_session` (`:217`, terminal — captures the eventual kill/fail that closes a hang's story). Both are free functions taking `(session, new_status, reason=...)`, NOT methods on `AgentSession`. Kill outcome (`kill_issued`/`subprocess_exited`/`pid`) is only known at the recovery call site (`agent/session_health.py:1184`), so thread it down via an optional kwarg / the `reason` string; it is `None` for transitions that originate elsewhere.
5. **Sink**: append-only `logs/session_telemetry/{session_id}.jsonl`. The executor task is the sole writer of *stream* events, but `status_transition` events fire from the reflection-scheduler thread (via `finalize_session`/`transition_status`) — so writes are NOT lock-free. A per-session lock guards the full open-or-reuse-handle + write sequence (see Race Conditions).
6. **Output**: `python -m tools.valor_session telemetry --id <session_id>` reads the JSONL and renders a human-readable timeline (and `--json` for raw). This is the v1 "consumer that proves value."

## Architectural Impact

- **New dependencies**: none (stdlib `json`, file append).
- **Interface changes**: one new module `agent/session_telemetry.py` with `record_telemetry_event(...)` and `read_session_timeline(session_id)`; one new CLI subcommand on `tools/valor_session`. Additive calls in `sdk_client.py` and `agent_session.py::transition_status`. No existing signatures change.
- **Coupling**: low and one-directional — the recorder reads events that already flow through the parse loop; nothing depends on the recorder's output in v1.
- **Data ownership**: telemetry lives in flat per-session JSONL files under `logs/`, owned by the recorder. Deliberately NOT on the Popoto `AgentSession` record (avoids the `session_events` RMW-race / unbounded-growth problem) and NOT in Redis (sidesteps the no-raw-Redis-on-Popoto rule entirely).
- **Reversibility**: high — delete the module, the CLI subcommand, and the `logs/session_telemetry/` dir. No schema migration, no data backfill.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus validator and documentarian (orchestrated).

**Interactions:**
- PM check-ins: 1-2 (schema sign-off, sink-vs-Popoto confirmation)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (stdlib only, internal code paths).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Writable `logs/` dir | `python -c "import os; assert os.access('logs', os.W_OK)"` | Telemetry sink location |

Run all checks: `python scripts/check_prerequisites.py docs/plans/session_telemetry_recorder.md`

## Solution

### Key Elements

- **`agent/session_telemetry.py` (new)**: `record_telemetry_event(session_id, event)` — normalize → derive idle-gap → append one JSONL line; fail-silent. `read_session_timeline(session_id, limit=None)` — parse the JSONL back into ordered events. Module-level per-session last-event-ts map for idle-gap derivation and a bounded open-handle cache.
- **Event schema (#1487-compatible)**: every line is `{"session_id": str, "ts": iso8601, "type": str, ...payload}`. Types: `turn_start`, `turn_end` (from `result`), `tool_use` (name + duration when derivable), `token_usage` (the raw per-turn `usage` dict + `total_cost_usd`, recorded verbatim off the harness `result` event — NOT a computed delta; the consumer diffs if it wants deltas, which sidesteps the cumulative-vs-per-turn ambiguity), `idle_gap` (seconds since prior event, emitted when the gap exceeds a threshold), `timeout` / `decode_error` / `broken_pipe` (reusing #1487's `{"type", "reason"|"raw"|"error"}` shape), and `status_transition` (`from`, `to`, `reason`, `kill_issued`, `subprocess_exited`, `pid`).
- **Tap points (additive only)**: in `agent/sdk_client.py`'s existing event-dispatch loop (harness `result` handler ~`:2773` + the SDK `ResultMessage` handler ~`:1760`), call `record_telemetry_event` next to the existing `record_*`/`accumulate_*` calls; in `models/session_lifecycle.py` emit `status_transition` from BOTH `transition_status` (non-terminal) and `finalize_session` (terminal).
- **Sink**: append-only `logs/session_telemetry/{session_id}.jsonl`. Per-session event cap (default 10k) → writes a final `{"type":"telemetry_truncated"}` marker and stops, so a runaway session can't fill the disk. Retention sweep (delete files older than N days) folded into the existing `agent-session-cleanup` reflection at `agent/session_health.py:2144` (`cleanup_corrupted_agent_sessions()`). A bounded open-handle cache; eviction of a handle happens under the same per-session lock as writes (see Race Conditions C/Race 2).
- **Consumer**: `tools/valor_session` gains a `telemetry --id <ID> [--json] [--tail N]` subcommand rendering the timeline (timestamp · type · summary), so "stuck vs working" is readable from the recording.

### Flow

`worker runs session` → `claude -p emits stream-json events` → `existing parse loop dispatches each event` → `record_telemetry_event appends JSONL line` → (`status_transition` also emitted on every DB status change) → `logs/session_telemetry/{id}.jsonl` → `valor-session telemetry --id <id>` → **human reads the event timeline**

### Technical Approach

- **No second parse**: the recorder consumes the events the parse loop already produces. The tap is a function call added beside existing `record_session_activity` / `accumulate_session_tokens` / `record_turn_count`.
- **#1487 compatibility**: use `type` as the discriminator and reuse `timeout`/`decode_error`/`broken_pipe` payload shapes verbatim, so when #1487's `ClaudeSession` lands in `worker/` its synthetic events flow into the same sink with no schema change. Where the harness emits native `result`/`assistant` events, map them to `turn_end`/`tool_use` rather than inventing parallel names.
- **Idle-gap derivation**: keep `last_event_monotonic[session_id]`; on each event, if `now - last > IDLE_GAP_THRESHOLD` (default 60s), emit a synthetic `idle_gap` event *before* the real event. Records silence as a fact without making it a kill signal (respects #1172). **Important (C4):** during an ongoing hang there is no "next event," so the gap materializes only when the next event arrives — for a wedged session that is the terminal `status_transition` emitted by `finalize_session` (the kill). The terminal idle_gap therefore *depends on* the terminal-transition tap above; the e2e wedged-session test must kill via the `finalize_session` path so the gap actually appears.
- **status_transition is the priority event** (per spike-1): wire it into both lifecycle functions so the FULL orphaning sequence behind #1537 is visible — the requeue-to-`pending` (`transition_status`) AND the terminal kill/fail (`finalize_session`). The recovery call site (`agent/session_health.py:1184`) passes `kill_issued`/`subprocess_exited`/`pid` down so future hangs are diagnosable end-to-end.
- **Fail-silent**: wrap all recorder calls in try/except that logs at debug and returns — telemetry must never crash or slow the execution loop (same posture as the memory hooks).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The recorder's append path is wrapped in `try/except Exception` that logs at debug and returns. Add a test that forces an `OSError` on write (e.g. read-only dir) and asserts: (a) no exception propagates to the caller, (b) a debug log is emitted, (c) the parse loop continues. No silent `pass` without a log.
- [ ] `read_session_timeline` on a malformed/partial JSONL line skips that line with a logged warning rather than raising; test with a truncated last line.

### Empty/Invalid Input Handling
- [ ] `record_telemetry_event` with `session_id=None`/empty → no-op, no file created, no raise (tested).
- [ ] Unknown/empty event dict → recorded as `{"type":"unknown"}` with the raw payload, never dropped silently and never raised.
- [ ] `read_session_timeline` for a session with no trace file → returns `[]`, not an error (tested).

### Error State Rendering
- [ ] `valor-session telemetry --id <unknown>` prints a clear "no telemetry recorded for <id>" message (not a traceback), exit 0.
- [ ] Timeline rendering of a trace containing `timeout`/`broken_pipe`/`telemetry_truncated` events displays them prominently (these are the "something went wrong" markers a human scans for).

## Test Impact

- [ ] `tests/unit/test_sdk_client.py` (token accumulation tests) — UPDATE: assert `record_telemetry_event` is invoked alongside `accumulate_session_tokens` in the parse loop (mock the recorder; verify call, don't re-test token math).
- [ ] `tests/unit/` coverage of `models/session_lifecycle.py` `transition_status` AND `finalize_session` — UPDATE: assert a `status_transition` telemetry event is emitted on BOTH non-terminal and terminal transitions (terminal is the one that closes a hang's story — C1).
- [ ] New `tests/unit/test_session_telemetry.py` — REPLACE/CREATE: full coverage of `record_telemetry_event`, idle-gap derivation, cap+truncation marker, fail-silent paths, and `read_session_timeline` parsing.
- [ ] New `tests/integration/test_session_telemetry_e2e.py` — CREATE: run a short real session through the harness and assert its JSONL trace contains `turn_*`, `token_delta`, and `status_transition` events retrievable by `session_id`.

No other existing tests are affected — the change is additive (new module + new calls), introducing no behavior change to the execution or liveness paths.

## Rabbit Holes

- **Fixing #1537 here.** Tempting (we found the root cause) but out of scope — v1 records, it does not change recovery logic. The recorder *enables* the fix; it is not the fix.
- **Building the learning model / classifier.** That's pillar-1 phase 2. v1 stops at record + display.
- **A dashboard UI for the timeline.** A CLI timeline satisfies the v1 acceptance ("rendered for a human"). A polished `ui/` view is deferred to avoid front-end scope creep.
- **Productionizing #1487's `ClaudeSession` as part of v1.** v1 only makes the schema #1487-compatible; wiring the persistent session into `worker/` is #1487's own scope.
- **Perfect tool-call duration accounting.** Deriving exact per-tool durations from stream-json is fiddly; v1 records `tool_use` with name + best-effort duration and moves on.
- **Generic structured-logging framework.** Don't build an event-bus abstraction; a single append function is enough.

## Risks

### Risk 1: Per-event write overhead in the hot parse loop
**Impact:** Recording on every event could slow high-throughput sessions.
**Mitigation:** One buffered append per event (no fsync), cached file handle per session, fail-silent. Benchmark a session in the e2e test; if overhead is measurable, batch-flush. Events are already being iterated, so the marginal cost is a `json.dumps` + `write`.

### Risk 2: Unbounded disk growth
**Impact:** A pathological session (millions of events) or accumulation across thousands of sessions fills the disk.
**Mitigation:** Per-session event cap (10k default) with a `telemetry_truncated` marker; retention sweep in the hourly `agent-session-cleanup` reflection deletes traces older than N days. Both `log()`-surfaced, no silent truncation.

### Risk 3: Schema drift from #1487
**Impact:** If v1 invents event names that diverge from #1487, the eventual `ClaudeSession` integration needs a translation shim.
**Mitigation:** Lock the `type` discriminator and reuse #1487's `timeout`/`decode_error`/`broken_pipe` payloads verbatim (verified on the branch). Document the schema in `docs/features/session-telemetry.md` as the shared contract.

## Race Conditions

### Race 1: Concurrent appends to the same session trace file
**Location:** `agent/session_telemetry.py` append path.
**Trigger:** The executor parse-loop task and the reflection-scheduler thread (firing `status_transition` via `finalize_session`/`transition_status`) both append to `{session_id}.jsonl`. Stream events have a single writer (the executor task); status-transition events do NOT — so this is a genuine two-thread race, not a theoretical one.
**Data prerequisite:** Same trace file, two threads. Also the bounded open-handle cache: a handle evicted mid-append would tear a line.
**State prerequisite:** The per-session last-event-ts map is read/written by both the parse loop and (for the terminal idle_gap) the finalize path.
**Mitigation (C3):** A per-session `threading.Lock` guards the ENTIRE open-or-reuse-handle → write → (possible evict) sequence, not just the bare `write()`. The lock registry is a module-level `dict[str, threading.Lock]` acquired via `_locks.setdefault(session_id, threading.Lock())` (atomic under the CPython GIL — document that assumption). Handle eviction from the bounded cache occurs only while holding that same per-session lock. Cross-session writes target different files/locks — no contention.

### Race 2: Idle-gap last-event-ts read/write across threads
**Location:** `agent/session_telemetry.py` idle-gap derivation.
**Trigger:** The finalize path (scheduler thread) computes the terminal idle_gap against `last_event_monotonic[session_id]` that the executor task last wrote.
**Data prerequisite:** Shared `last_event_monotonic` map.
**Mitigation:** Read-modify-write of the per-session entry happens under the same per-session lock from Race 1, so the gap computation and the last-ts update are atomic together.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1537] Fixing the liveness-recovery blind spot (requeue-to-`pending`-without-kill). v1 records the `status_transition` evidence that makes it diagnosable; the fix is tracked separately.
- [SEPARATE-SLUG #1536] Pillar-1 phase-2 learned "healthy vs stalled" classifier, pillar-2 crash/resume learning + auto-resume, and pillar-3 human-TUI behavior capture. All deferred to the epic's later sub-issues.
- [SEPARATE-SLUG #1487] Wiring the persistent `ClaudeSession` (with its synthetic-event emission) into `worker/`. v1 only guarantees schema compatibility.

## Update System

No update system changes required — this feature is purely internal. It adds one Python module, additive call sites, and a `logs/session_telemetry/` directory created on first write. No new dependency, config file, secret, or migration to propagate via `/update`. The retention sweep rides the existing `agent-session-cleanup` reflection, which is already deployed everywhere.

## Agent Integration

The agent reaches the new capability through the **CLI entry point** surface (per this repo's Agent Integration convention), not MCP:
- [ ] `tools/valor_session` gains a `telemetry` subcommand. **`valor-session` is NOT a declared console script** (verified: `pyproject.toml [project.scripts]` has no such entry; `tools/valor_session.py:1185` defines `main()` and `:1354` has a `__main__` guard). The agent invokes it as `python -m tools.valor_session telemetry --id <ID>` via its Bash tool — the same module form CLAUDE.md uses for every other `valor_session` command. No packaging change, so the "No update system changes required" claim holds.
- [ ] No `.mcp.json` change and no bridge import needed — the recorder runs inside the executor the bridge already drives; the agent only *reads* traces via the CLI.
- [ ] Integration test asserts `python -m tools.valor_session telemetry` returns a rendered timeline for a session that has a trace, and a clean "no telemetry" message otherwise.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/session-telemetry.md` — the event schema (the shared #1487 contract), the JSONL sink layout, the CLI consumer, retention/cap behavior, and the explicit non-goal that idle gaps are recorded but never used as a kill signal (#1172).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Add a `valor-session telemetry` row to the Quick Commands table in `CLAUDE.md`.

### Inline Documentation
- [ ] Docstrings on `record_telemetry_event` / `read_session_timeline` documenting the single-writer-per-session invariant and fail-silent contract.
- [ ] A comment at each tap point explaining it is an additive telemetry tap (no behavior change).

## Success Criteria

- [ ] After any session runs, `logs/session_telemetry/{session_id}.jsonl` exists and contains ordered events including `turn_*`, `token_delta`, and `status_transition`.
- [ ] `python -m tools.valor_session telemetry --id <ID>` renders a human-readable timeline; `--json` returns raw events.
- [ ] A deliberately-wedged test session's trace shows the terminal `idle_gap` and the `status_transition` sequence — i.e. the 25.5h-hang scenario would now be diagnosable from the trace alone.
- [ ] Event schema reuses #1487's `type`/`timeout`/`decode_error`/`broken_pipe` shapes (asserted in a test).
- [ ] Recorder is fail-silent: a forced write error does not propagate or stop the parse loop (asserted).
- [ ] Per-session cap + retention sweep enforced (asserted); no silent truncation (marker emitted).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `agent/sdk_client.py` references `record_telemetry_event` and `models/session_lifecycle.py` emits `status_transition` from both `transition_status` and `finalize_session`.

## Team Orchestration

### Team Members

- **Builder (recorder core)**
  - Name: `recorder-builder`
  - Role: `agent/session_telemetry.py` (record + read + idle-gap + cap), the two tap points, status_transition emission.
  - Agent Type: builder
  - Resume: true

- **Builder (consumer CLI)**
  - Name: `cli-builder`
  - Role: `valor-session telemetry` subcommand + rendering.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (telemetry)**
  - Name: `telemetry-tester`
  - Role: unit + integration tests incl. fail-silent and e2e trace retrieval.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `telemetry-validator`
  - Role: verify success criteria, run verification commands.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `telemetry-docs`
  - Role: feature doc + index + CLAUDE.md command row.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Recorder core
- **Task ID**: build-recorder
- **Depends On**: none
- **Validates**: tests/unit/test_session_telemetry.py (create)
- **Informed By**: spike-1 (status_transition is the priority diagnostic event; idle-gap alone insufficient)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/session_telemetry.py`: `record_telemetry_event`, `read_session_timeline`, idle-gap derivation, per-session cap + truncation marker, fail-silent wrapper, per-session append lock.
- Use `type` discriminator; reuse #1487 `timeout`/`decode_error`/`broken_pipe` payloads.

### 2. Wire tap points
- **Task ID**: build-taps
- **Depends On**: build-recorder
- **Validates**: tests/unit/test_sdk_client.py (update), tests/unit/test_agent_session.py (update)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `record_telemetry_event` calls beside existing `record_*`/`accumulate_*` in `agent/sdk_client.py`'s event loop (harness `result` handler ~`:2773` + SDK `ResultMessage` handler ~`:1760`). Record `token_usage` as the raw per-turn `usage` dict verbatim (C5) — no delta math.
- Emit `status_transition` (from/to/reason/kill_issued/subprocess_exited/pid) in BOTH `models/session_lifecycle.py::transition_status` (non-terminal) and `::finalize_session` (terminal) (C1). Thread kill outcome from the recovery call site (`agent/session_health.py:1184`) via an optional kwarg.

### 3. Consumer CLI
- **Task ID**: build-cli
- **Depends On**: build-recorder
- **Validates**: tests/integration/test_session_telemetry_e2e.py (create)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `telemetry --id <ID> [--json] [--tail N]` to `tools/valor_session`; render timestamp · type · summary; clean message when no trace.

### 4. Retention sweep
- **Task ID**: build-retention
- **Depends On**: build-recorder
- **Validates**: tests/unit/test_session_telemetry.py::test_retention_deletes_old_traces (create)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a trace-file retention sweep (delete > N days) into `agent/session_health.py:2144` `cleanup_corrupted_agent_sessions()` (the `agent-session-cleanup` reflection); `log()` what was deleted.

### 5. Tests
- **Task ID**: build-tests
- **Depends On**: build-taps, build-cli, build-retention
- **Assigned To**: telemetry-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: recorder, idle-gap, cap/truncation, fail-silent (forced OSError), malformed-line read, empty/None inputs.
- Integration: short real session → assert trace has `turn_*`/`token_delta`/`status_transition` retrievable by id; CLI renders it.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: telemetry-docs
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/session-telemetry.md` + README index + CLAUDE.md command row.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: telemetry-validator
- **Agent Type**: validator
- **Parallel**: false
- Run verification table; confirm all success criteria incl. the wedged-session diagnosability check.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_session_telemetry.py tests/integration/test_session_telemetry_e2e.py -x -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tap wired (sdk) | `grep -n record_telemetry_event agent/sdk_client.py` | output > 0 |
| status_transition emitted | `grep -n status_transition models/session_lifecycle.py` | output contains status_transition |
| CLI subcommand present | `python -m tools.valor_session telemetry --help` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

Critique verdict: **READY TO BUILD (with concerns)** — 0 blockers, 5 concerns, 3 nits. All folded into the plan below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic/Adversary/Consistency | C1: `transition_status` is a free function in `models/session_lifecycle.py:453` (not a method on `agent_session.py`), and terminal kills go through `finalize_session:217` which the plan never tapped — so the END of a hang's story would be missing. | Data Flow #4, Solution tap points, Task 2, Test Impact, Success Criteria, Verification all rewired to `models/session_lifecycle.py` covering BOTH functions. | Verified via grep: both are free functions taking `(session, new_status, reason=...)`. Kill outcome known only at `session_health.py:1184` — threaded via kwarg. |
| CONCERN | Operator/User | C2: plan claimed `valor-session` is a console script — it is not in `pyproject.toml [project.scripts]`. | Agent Integration corrected to `python -m tools.valor_session telemetry`; preserves "no update changes." | Verified: no script entry; `main()` at `tools/valor_session.py:1185`. |
| CONCERN | Adversary | C3: per-session lock must cover open-or-reuse-handle + write + evict, and the lock registry creation is itself a micro-race. | Race Conditions Race 1 rewritten: lock guards full sequence; registry via `setdefault` (GIL-atomic); eviction under same lock. | — |
| CONCERN | Skeptic/Adversary | C4: idle_gap is reactive (needs a "next event"); a silent hang emits none until the terminal transition — so the promised terminal idle_gap depends on C1. | Idle-gap derivation note + e2e test must kill via `finalize_session` path. | Order: emit synthetic `idle_gap`, then `status_transition`. |
| CONCERN | Skeptic/Archaeologist | C5: schema said `token_delta` but the harness `result` event carries per-turn `usage`; computing a delta risks double-count. | Renamed to `token_usage`; record raw `usage` dict verbatim, consumer diffs. | Verified at `sdk_client.py:2773` — `usage` is per-turn per the inline comment. |
| NIT | — | N1: Task 4 had no Validates. | Added `test_retention_deletes_old_traces`. | — |
| NIT | — | N2: retention-sweep location unstated. | Pinned to `agent/session_health.py:2144 cleanup_corrupted_agent_sessions()`. | — |
| NIT | — | N3: Open Question #1 (Popoto) already answered by the plan. | Converted to a stated decision. | — |

---

## Open Questions

1. **Consumer surface:** CLI timeline for v1, dashboard view deferred. Acceptable, or is a `ui/` view required for v1 acceptance?
2. **Retention window N:** default proposed 14 days for trace files. Right balance of diagnostic value vs disk?
3. **Idle-gap threshold:** default 60s before emitting an `idle_gap` event. Too chatty / too coarse?

_(Sink choice resolved — JSONL-on-disk under `logs/session_telemetry/`; see Architectural Impact for why not Popoto/Redis. The no-raw-Redis rule is hook-enforced and the `session_events` RMW hazard is real, so this is a decision, not an open question.)_
