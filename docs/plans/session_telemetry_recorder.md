---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-01
tracking: https://github.com/tomcounsell/ai/issues/1536
last_comment_id:
revision_applied: true
refreshed: 2026-06-15
freshness_disposition: Re-based (was Major drift)
---

# Session Telemetry Recorder (v1 of epic #1536)

## Problem

When a `claude -p` session runs, we keep no durable record of *what it was doing*. The production execution path parses the harness stream-json event stream for aggregate token counts (`agent/sdk_client.py:286-391`) and **discards every event after that**. There is no queryable, per-event trace.

Epic #1536 wants to *learn from* session behavior — to eventually distinguish "healthy vs stalled," capture crash/resume signal, and feed a behavioral model. None of that is possible without a record substrate: a durable, per-event trace of every session that a process (or a human) can read back by `session_id`. v1 is exactly that substrate and nothing more — it records and displays; it does not classify, recover, or learn.

A human watching a terminal Claude Code TUI can tell "stuck" from "still working" at a glance — tokens ticking, tools scrolling, the spinner. Our headless worker keeps no comparable record. Aggregate token counters and the coarse `session_events` lifecycle log tell you a session ran and roughly how much it cost; they do not tell you *what happened inside it* — which tools fired, when turns boundaries landed, where the long idle gaps were, when status flipped and why. That per-event timeline is the missing primitive every downstream pillar of #1536 depends on.

**Current behavior:** Stream-json events are parsed for token totals and dropped. The only per-session persistence is aggregate counters on the `AgentSession` DB record and a coarse `session_events` lifecycle log.

**Desired outcome:** Every session writes a durable, per-event telemetry trace (per-turn token usage, tool-call boundaries, turn events, idle gaps, and status transitions carrying their reason and — when known — the recovery kill outcome). A human can retrieve and read any session's event timeline by `session_id`. This is the **record substrate** the rest of epic #1536 (learning, crash/resume, behavioral capture — deferred to sub-issues #1538/#1539/#1540) builds on; v1 records and displays only.

**Note on the historical motivation:** an earlier draft of this plan motivated v1 by the 25.5h hang of 2026-05-31 and framed `status_transition` telemetry as the trace that would have made that hang diagnosable. That specific recovery blind spot was **fixed on 2026-06-03 by PR #1557** (issue #1537, now closed), which added subprocess-death confirmation before requeue. v1 no longer exists to diagnose that bug. The `status_transition` event survives in v1 on independent merit — it is the single highest-signal event for *any* future hang or behavioral analysis, and it is the only event that records the recovery machinery's own decisions — but it is justified by the epic's durable-trace goal, not by an open defect.

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

### Revision applied — 2026-06-15 (re-based against current main `0d000e59`)

**Disposition: Re-based → Ready.** All three drift items resolved by editing the plan body; the two surviving tap points were preserved unchanged. Verified against current main before writing:

1. **#1537 framing removed.** Problem, Prior Art, Risks, No-Gos, and Success Criteria no longer justify v1 (or `status_transition`) via the now-closed #1537. The value proposition is re-grounded on the epic's durable-trace / learning-substrate goal (#1536 still OPEN; sub-issues #1538/#1539/#1540 carry the learning work). `status_transition` is retained as the highest-signal telemetry event on its own merit, not as a bug diagnostic. A short historical note in Problem records *why* the original framing was dropped.
2. **Recovery design re-based on `SubprocessKillResult`.** Confirmed `_confirm_subprocess_dead(pid, *, timeout) -> SubprocessKillResult` at `agent/session_health.py:1038`, the `SubprocessKillResult(confirmed_dead, signal_sent)` NamedTuple at `:1019`, and the recovery transition in `_apply_recovery_transition` at `:1132` (kill confirmation `:1318-1341`; the `_subprocess_confirmed_dead`/`_kill_result` values are in scope at the `finalize_session` calls `:1361-1419`). Data Flow, Solution, Spike-1, and Task 2 now CONSUME that existing result at the recovery site instead of inventing a `kill_issued`/`subprocess_exited`/`pid` kwarg thread from the deleted `:1184` call. Spike-1's obsolete finding is struck and replaced with the current recovery anatomy.
3. **#1487 schema thesis dropped.** Confirmed `grep -rn 'decode_error\|broken_pipe' agent/` returns zero hits — that vocabulary never landed (PR #1487 merged as the granite PTY PoC, not the stream-json line-reader). The v1 event vocabulary is now defined standalone on its own terms; the "reuse #1487's payloads verbatim" requirement is removed from Solution, Technical Approach, Risk 3, and Success Criteria.

**Drifted line numbers refreshed:** retention anchor `cleanup_corrupted_agent_sessions` `:2418` (was `:2144`); `valor_session main()` `:1273` / `__main__` `:1442` (was `:1185`/`:1354`); harness `usage` tap `:2771-2773`; SDK `ResultMessage` handler `:1738-1780`. Surviving anchors re-confirmed: `finalize_session:217`, `transition_status:453`, `accumulate_session_tokens:286`. `valor-session` is still NOT a console script (no `pyproject.toml [project.scripts]` entry) — Agent Integration claim holds.

## Prior Art

- **#1487 (merged 2026-06-01, granite PTY PoC)**: Merged as the interactive-TUI / PTY container direction, NOT a stream-json `ClaudeSession` line-reader. The JSONL-per-line typed-event approach an earlier draft attributed to #1487 **did not land** — `grep -rn 'decode_error\|broken_pipe' agent/` returns zero hits. v1 therefore defines its own event vocabulary from the stream-json events the production parse loop already produces; it does NOT depend on, reuse, or claim compatibility with any #1487 schema.
- **#1128 (closed)**: Added per-session token tracking (`accumulate_session_tokens`) — the existing tap we extend.
- **#1226 / #1356 (closed)**: Two-tier liveness check with per-turn SDK progress fields (`last_tool_use_at`, `last_turn_at`) and the no-output budget. These fields are exactly the kind of signal v1 records as events.
- **#1172 (closed)**: Retired `last_stdout_at` stdout-silence killing — silence ≠ failure. **Constraint:** v1 records idle gaps as facts, never resurrects silence-as-kill.
- **#1537 (closed 2026-06-03, PR #1557)**: Fixed the liveness-recovery blind spot by confirming subprocess death before requeue — it added `_confirm_subprocess_dead(...) -> SubprocessKillResult` and moved recovery into `_apply_recovery_transition`. **Relevance to v1:** that result is a ready-made, structured kill outcome that v1's `status_transition` event consumes at the recovery site. The bug itself is already fixed; v1 records its decisions, it does not change recovery logic.

## Research

No relevant external findings — proceeding with codebase context. The stream-json protocol, JSONL sink, and consumer are all internal; no external libraries or APIs are introduced.

## Spike Results

### spike-1 (superseded): the original "why did the 25.5h hang escape recovery?" finding is obsolete

The original spike-1 diagnosed the recovery blind spot that wedged the worker on 2026-05-31. **That bug was fixed by PR #1557 (#1537, closed 2026-06-03)**, so the finding no longer describes current code. It is retained here only as a pointer; the live recovery anatomy is captured in spike-1b below.

### spike-1b: Where is the recovery kill outcome, and how should `status_transition` consume it?
- **Assumption**: "After PR #1557, the recovery kill outcome is a structured value at the recovery site, so v1 should consume it rather than hand-thread `kill_issued`/`subprocess_exited`/`pid` through the lifecycle functions."
- **Method**: code-read (`agent/session_health.py`, `models/session_lifecycle.py`) against current main `0d000e59`.
- **Finding**: **Confirmed.** PR #1557 introduced `_confirm_subprocess_dead(pid, *, timeout) -> SubprocessKillResult` (`agent/session_health.py:1038`) returning a `SubprocessKillResult(confirmed_dead: bool, signal_sent: bool)` NamedTuple (`:1019`). Recovery now runs in `_apply_recovery_transition` (`:1132`); it `task.cancel()`s the in-flight task, then calls `_confirm_subprocess_dead` via `run_in_executor` (`:1329-1336`) and binds `_kill_result` / `_subprocess_confirmed_dead` (`:1337-1344`). Those values are in scope at every terminal `finalize_session(...)` call in the same function (`:1361-1419`) and at the requeue `else` branch (`:1420`). The recovery target's PID is `entry.claude_pid`. So the recovery site already holds everything the telemetry event wants — `confirmed_dead`, `signal_sent`, `pid`, the `reason` string, and the destination status — at the exact moment it transitions the session.
- **Confidence**: high (file:line evidence at current main).
- **Impact on plan**: (1) The `status_transition` event payload is defined on its own terms: `from`, `to`, `reason`, plus an optional `kill` sub-object `{confirmed_dead, signal_sent, pid}` populated **only** at the recovery site (it is `None` for ordinary transitions that originate elsewhere). (2) v1 does NOT add a kwarg to `transition_status`/`finalize_session` to carry kill outcome. Instead, the recovery site in `_apply_recovery_transition` records the kill outcome directly — it calls `record_telemetry_event(... status_transition with kill=...)` itself, right beside the `finalize_session` call, where `_kill_result` is already in hand. The lifecycle taps in `transition_status`/`finalize_session` emit the *plain* `status_transition` (from/to/reason, `kill=None`) for the universe of transitions; the recovery site supplies the enriched one. This avoids both threading a new parameter and double-emitting (see Data Flow #4 for the de-dup note). (3) `status_transition` remains the single highest-signal event in the trace — it is the only event that records the recovery machinery's own decisions — independent of any specific bug.

## Data Flow

1. **Entry point**: Worker executes a session → `agent/sdk_client.py` spawns `claude -p --output-format stream-json` (harness path) or runs the `ClaudeSDKClient` query loop.
2. **Stream parse loop** (`agent/sdk_client.py` `_run_harness_subprocess` / the `ResultMessage`/`AssistantMessage` handlers ~1760-1820): each event is already iterated and dispatched to `record_session_activity`, `record_turn_count`, `accumulate_session_tokens`. **New:** alongside these calls, invoke `record_telemetry_event(session_id, event)` — no second parse.
3. **Telemetry helper** (`agent/session_telemetry.py`, new): normalizes the event to `{session_id, ts, type, ...payload}`, derives `idle_gap` from the per-session last-event timestamp, and appends one JSON line to the session's trace file. Fire-and-forget (never raises into the hot loop).
4. **Status transitions** (`models/session_lifecycle.py`): emit a *plain* `status_transition` telemetry event from BOTH `transition_status` (`:453`, non-terminal — captures requeue-to-`pending` and other in-flight moves) AND `finalize_session` (`:217`, terminal — captures the kill/fail/complete that closes a session's story). Both are free functions taking `(session, new_status, reason=...)`, NOT methods on `AgentSession`. The plain event carries `{from, to, reason, kill: None}` and covers the entire universe of transitions.
   **Recovery-enriched variant:** the recovery kill outcome is a structured `SubprocessKillResult` already computed inside `_apply_recovery_transition` (`agent/session_health.py:1337-1344`, `_kill_result` / `_subprocess_confirmed_dead`, with `entry.claude_pid`). v1 does NOT thread a kwarg through the lifecycle functions. Instead, the recovery site itself calls `record_telemetry_event(..., type="status_transition", kill={confirmed_dead, signal_sent, pid})` right beside its `finalize_session`/requeue calls, where the result is in scope. **De-dup:** to avoid emitting two `status_transition` records for the same recovery move, the lifecycle-tap emission is suppressed when the caller is the recovery path — the recorder treats the recovery-site emission as authoritative for recovery transitions. The simplest mechanism (chosen): `finalize_session`/`transition_status` accept an optional `emit_telemetry: bool = True`; the recovery site passes `emit_telemetry=False` and emits the enriched event itself. This is one boolean flag, not a kill-outcome payload thread.
5. **Sink**: append-only `logs/session_telemetry/{session_id}.jsonl`. The executor task is the sole writer of *stream* events, but `status_transition` events fire from the reflection-scheduler thread (via `finalize_session`/`transition_status`) — so writes are NOT lock-free. A per-session lock guards the full open-or-reuse-handle + write sequence (see Race Conditions).
6. **Output**: `python -m tools.valor_session telemetry --id <session_id>` reads the JSONL and renders a human-readable timeline (and `--json` for raw). This is the v1 "consumer that proves value."

## Architectural Impact

- **New dependencies**: none (stdlib `json`, file append).
- **Interface changes**: one new module `agent/session_telemetry.py` with `record_telemetry_event(...)` and `read_session_timeline(session_id)`; one new CLI subcommand on `tools/valor_session`. Additive calls in `sdk_client.py`, in `models/session_lifecycle.py::transition_status`/`::finalize_session`, and at the recovery site in `agent/session_health.py`. The only signature change is an optional `emit_telemetry: bool = True` kwarg on the two lifecycle functions (default-True, backward-compatible); no caller is forced to change.
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
- **Event schema (defined standalone on v1's own terms)**: every line is `{"session_id": str, "ts": iso8601, "type": str, ...payload}`. Types: `turn_start`, `turn_end` (from the stream-json `result` event), `tool_use` (name + duration when derivable), `token_usage` (the raw per-turn `usage` dict + `total_cost_usd`, recorded verbatim off the harness `result` event / SDK `ResultMessage` — NOT a computed delta; the consumer diffs if it wants deltas, which sidesteps the cumulative-vs-per-turn ambiguity), `idle_gap` (seconds since prior event, emitted when the gap exceeds a threshold), and `status_transition` (`from`, `to`, `reason`, and an optional `kill` sub-object `{confirmed_dead, signal_sent, pid}` populated only at the recovery site, else `None`). The schema is a v1-internal contract documented in `docs/features/session-telemetry.md`; it makes no claim of compatibility with any external or PoC schema. If the recorder receives an event whose `type` it does not recognise, it records it as `{"type": "unknown", "raw": <payload>}` rather than dropping it — so a future producer can add event types without a recorder change.
- **Tap points (additive only)**: in `agent/sdk_client.py`'s existing event-dispatch loop (harness `result` handler `:2771-2773` + the SDK `ResultMessage` handler `:1738-1780`), call `record_telemetry_event` next to the existing `record_*`/`accumulate_*` calls; in `models/session_lifecycle.py` emit the plain `status_transition` from BOTH `transition_status:453` (non-terminal) and `finalize_session:217` (terminal); and at the recovery site `agent/session_health.py:_apply_recovery_transition` emit the kill-enriched `status_transition` consuming the in-scope `SubprocessKillResult` (`_kill_result`) — passing `emit_telemetry=False` to the lifecycle call to avoid a duplicate plain event.
- **Sink**: append-only `logs/session_telemetry/{session_id}.jsonl`. Per-session event cap (default 10k) → writes a final `{"type":"telemetry_truncated"}` marker and stops, so a runaway session can't fill the disk. Retention sweep (delete files older than N days) folded into the existing `agent-session-cleanup` reflection at `agent/session_health.py:2418` (`cleanup_corrupted_agent_sessions()`). A bounded open-handle cache; eviction of a handle happens under the same per-session lock as writes (see Race Conditions Race 1/Race 2).
- **Consumer**: `tools/valor_session` gains a `telemetry --id <ID> [--json] [--tail N]` subcommand rendering the timeline (timestamp · type · summary), so "stuck vs working" is readable from the recording.

### Flow

`worker runs session` → `claude -p emits stream-json events` → `existing parse loop dispatches each event` → `record_telemetry_event appends JSONL line` → (`status_transition` also emitted on every DB status change) → `logs/session_telemetry/{id}.jsonl` → `valor-session telemetry --id <id>` → **human reads the event timeline**

### Technical Approach

- **No second parse**: the recorder consumes the events the parse loop already produces. The tap is a function call added beside existing `record_session_activity` / `accumulate_session_tokens` / `record_turn_count`.
- **Schema is standalone**: use `type` as the discriminator over v1's own event set. Where the stream-json stream emits native `result`/`assistant` events, map them to `turn_end`/`tool_use` rather than inventing parallel names. Unknown event types are recorded verbatim under `{"type":"unknown","raw":...}` so the vocabulary can grow without a recorder change. No external/PoC schema is referenced or depended on.
- **Idle-gap derivation**: keep `last_event_monotonic[session_id]`; on each event, if `now - last > IDLE_GAP_THRESHOLD` (default 60s), emit a synthetic `idle_gap` event *before* the real event. Records silence as a fact without making it a kill signal (respects #1172). **Important (C4):** during an ongoing hang there is no "next event," so the gap materializes only when the next event arrives — for a recovered session that is the terminal `status_transition` emitted by `finalize_session`. The terminal idle_gap therefore *depends on* the terminal-transition tap above; the e2e long-idle test must drive the session to a terminal transition via the `finalize_session` path so the gap actually appears.
- **status_transition is the priority event** (per spike-1b): wire the plain event into both lifecycle functions so every transition is visible — the in-flight moves (`transition_status`) AND the terminal kill/fail/complete (`finalize_session`). The recovery site (`agent/session_health.py:_apply_recovery_transition`, `:1132`) emits the kill-enriched variant by consuming the in-scope `SubprocessKillResult` (`_kill_result.confirmed_dead` / `.signal_sent`, `entry.claude_pid`) directly — no kwarg threading, and `emit_telemetry=False` on its lifecycle call prevents a duplicate plain emission.
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
- [ ] Timeline rendering of a trace containing a kill-enriched `status_transition` (to `failed`, `kill.confirmed_dead=False`) or a `telemetry_truncated` marker displays them prominently (these are the "something went wrong" markers a human scans for).

## Test Impact

- [ ] `tests/unit/test_sdk_client.py` (token accumulation tests) — UPDATE: assert `record_telemetry_event` is invoked alongside `accumulate_session_tokens` in the parse loop (mock the recorder; verify call, don't re-test token math).
- [ ] `tests/unit/` coverage of `models/session_lifecycle.py` `transition_status` AND `finalize_session` — UPDATE: assert a plain `status_transition` telemetry event (`kill=None`) is emitted on BOTH non-terminal and terminal transitions, and that `emit_telemetry=False` suppresses it (the recovery-site de-dup path — C1).
- [ ] `tests/unit/` coverage of `agent/session_health.py` `_apply_recovery_transition` — UPDATE/CREATE: assert the recovery site emits one kill-enriched `status_transition` carrying `{confirmed_dead, signal_sent, pid}` from the in-scope `SubprocessKillResult`, and passes `emit_telemetry=False` to its lifecycle call so no duplicate plain event is produced.
- [ ] New `tests/unit/test_session_telemetry.py` — REPLACE/CREATE: full coverage of `record_telemetry_event`, idle-gap derivation, cap+truncation marker, fail-silent paths, unknown-event recording, and `read_session_timeline` parsing.
- [ ] New `tests/integration/test_session_telemetry_e2e.py` — CREATE: run a short real session through the harness and assert its JSONL trace contains `turn_*`, `token_usage`, and `status_transition` events retrievable by `session_id`.

No other existing tests are affected — the change is additive (new module + new calls), introducing no behavior change to the execution or liveness paths.

## Rabbit Holes

- **Changing recovery logic.** v1 records the recovery machinery's decisions (via the kill-enriched `status_transition`); it does NOT modify `_apply_recovery_transition` behavior. The kill confirmation that PR #1557 added is already correct; v1 only reads `SubprocessKillResult`.
- **Building the learning model / classifier.** That's the epic's later sub-issues (#1538/#1539/#1540). v1 stops at record + display.
- **A dashboard UI for the timeline.** A CLI timeline satisfies the v1 acceptance ("rendered for a human"). A polished `ui/` view is deferred to avoid front-end scope creep.
- **Inventing a cross-producer event schema.** v1's schema is a v1-internal contract. Do NOT design it to match a hypothetical future PTY-trace producer or any PoC format — the unknown-event passthrough already absorbs new types without a recorder change.
- **Perfect tool-call duration accounting.** Deriving exact per-tool durations from stream-json is fiddly; v1 records `tool_use` with name + best-effort duration and moves on.
- **Generic structured-logging framework.** Don't build an event-bus abstraction; a single append function is enough.

## Risks

### Risk 1: Per-event write overhead in the hot parse loop
**Impact:** Recording on every event could slow high-throughput sessions.
**Mitigation:** One buffered append per event (no fsync), cached file handle per session, fail-silent. Benchmark a session in the e2e test; if overhead is measurable, batch-flush. Events are already being iterated, so the marginal cost is a `json.dumps` + `write`.

### Risk 2: Unbounded disk growth
**Impact:** A pathological session (millions of events) or accumulation across thousands of sessions fills the disk.
**Mitigation:** Per-session event cap (10k default) with a `telemetry_truncated` marker; retention sweep in the hourly `agent-session-cleanup` reflection deletes traces older than N days. Both `log()`-surfaced, no silent truncation.

### Risk 3: Event vocabulary churn as new producers appear
**Impact:** Downstream pillars (#1538/#1539/#1540) or a future PTY-trace producer may emit event types v1 never anticipated; a brittle recorder would drop or crash on them.
**Mitigation:** The recorder is open to unknown types — any unrecognised `type` is recorded verbatim under `{"type":"unknown","raw":...}`, never dropped, never raised. The `type` discriminator and the v1 event set are documented in `docs/features/session-telemetry.md` as the v1-internal contract; new producers add types without a recorder change.

## Race Conditions

### Race 1: Concurrent appends to the same session trace file
**Location:** `agent/session_telemetry.py` append path.
**Trigger:** The executor parse-loop task and other writers — the reflection-scheduler thread (plain `status_transition` via `finalize_session`/`transition_status`) and the worker-loop recovery path (`_apply_recovery_transition`, kill-enriched `status_transition`) — all append to `{session_id}.jsonl`. Stream events have a single writer (the executor task); status-transition events do NOT — so this is a genuine multi-writer race, not a theoretical one.
**Data prerequisite:** Same trace file, two threads. Also the bounded open-handle cache: a handle evicted mid-append would tear a line.
**State prerequisite:** The per-session last-event-ts map is read/written by both the parse loop and (for the terminal idle_gap) the finalize path.
**Mitigation (C3):** A per-session `threading.Lock` guards the ENTIRE open-or-reuse-handle → write → (possible evict) sequence, not just the bare `write()`. The lock registry is a module-level `dict[str, threading.Lock]` acquired via `_locks.setdefault(session_id, threading.Lock())` (atomic under the CPython GIL — document that assumption). Handle eviction from the bounded cache occurs only while holding that same per-session lock. Cross-session writes target different files/locks — no contention.

### Race 2: Idle-gap last-event-ts read/write across threads
**Location:** `agent/session_telemetry.py` idle-gap derivation.
**Trigger:** The finalize path (scheduler thread) computes the terminal idle_gap against `last_event_monotonic[session_id]` that the executor task last wrote.
**Data prerequisite:** Shared `last_event_monotonic` map.
**Mitigation:** Read-modify-write of the per-session entry happens under the same per-session lock from Race 1, so the gap computation and the last-ts update are atomic together.

## No-Gos (Out of Scope)

- [ALREADY-FIXED #1537] The liveness-recovery blind spot (requeue-to-`pending`-without-kill) was fixed by PR #1557 — v1 does not touch recovery logic, it only records the kill outcome that fix already produces.
- [SEPARATE-SLUG #1538/#1539/#1540] The learned "healthy vs stalled" classifier, crash/resume learning + auto-resume, and human-TUI behavior capture. All deferred to the epic's later sub-issues; v1 is the record substrate only.
- [OUT OF SCOPE] Any cross-producer schema contract. v1's event vocabulary is v1-internal; future producers (e.g. a PTY trace) ride the unknown-event passthrough, not a negotiated shared schema.

## Update System

No update system changes required — this feature is purely internal. It adds one Python module, additive call sites, and a `logs/session_telemetry/` directory created on first write. No new dependency, config file, secret, or migration to propagate via `/update`. The retention sweep rides the existing `agent-session-cleanup` reflection, which is already deployed everywhere.

## Agent Integration

The agent reaches the new capability through the **CLI entry point** surface (per this repo's Agent Integration convention), not MCP:
- [ ] `tools/valor_session` gains a `telemetry` subcommand. **`valor-session` is NOT a declared console script** (verified against current main: `pyproject.toml [project.scripts]` has no such entry; `tools/valor_session.py:1273` defines `main()` and `:1442` has a `__main__` guard). The agent invokes it as `python -m tools.valor_session telemetry --id <ID>` via its Bash tool — the same module form CLAUDE.md uses for every other `valor_session` command. No packaging change, so the "No update system changes required" claim holds.
- [ ] No `.mcp.json` change and no bridge import needed — the recorder runs inside the executor the bridge already drives; the agent only *reads* traces via the CLI.
- [ ] Integration test asserts `python -m tools.valor_session telemetry` returns a rendered timeline for a session that has a trace, and a clean "no telemetry" message otherwise.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/session-telemetry.md` — the v1-internal event schema (event types + the `status_transition` kill sub-object + the unknown-event passthrough), the JSONL sink layout, the CLI consumer, retention/cap behavior, and the explicit non-goal that idle gaps are recorded but never used as a kill signal (#1172).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Add a `valor-session telemetry` row to the Quick Commands table in `CLAUDE.md`.

### Inline Documentation
- [ ] Docstrings on `record_telemetry_event` / `read_session_timeline` documenting the single-writer-per-session invariant and fail-silent contract.
- [ ] A comment at each tap point explaining it is an additive telemetry tap (no behavior change).

## Success Criteria

- [ ] After any session runs, `logs/session_telemetry/{session_id}.jsonl` exists and contains ordered events including `turn_*`, `token_usage`, and `status_transition`.
- [ ] `python -m tools.valor_session telemetry --id <ID>` renders a human-readable timeline; `--json` returns raw events.
- [ ] A test session driven to a terminal recovery transition produces a kill-enriched `status_transition` carrying `{confirmed_dead, signal_sent, pid}` sourced from the recovery site's `SubprocessKillResult`, and a preceding `idle_gap` when the session sat idle before the transition — i.e. a future hang's recovery decisions are readable from the trace alone.
- [ ] The recorder records an unrecognised event `type` verbatim under `{"type":"unknown","raw":...}` rather than dropping it (asserted in a test).
- [ ] Recorder is fail-silent: a forced write error does not propagate or stop the parse loop (asserted).
- [ ] Per-session cap + retention sweep enforced (asserted); no silent truncation (marker emitted).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `agent/sdk_client.py` references `record_telemetry_event`, `models/session_lifecycle.py` emits the plain `status_transition` from both `transition_status` and `finalize_session`, and `agent/session_health.py` emits the kill-enriched `status_transition` at the recovery site.

## Team Orchestration

### Team Members

- **Builder (recorder core)**
  - Name: `recorder-builder`
  - Role: `agent/session_telemetry.py` (record + read + idle-gap + cap + unknown-event passthrough), the sdk_client + lifecycle tap points, and the recovery-site kill-enriched `status_transition`.
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
- **Informed By**: spike-1b (status_transition is the priority event; recovery kill outcome consumed from `SubprocessKillResult`, not kwarg-threaded)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/session_telemetry.py`: `record_telemetry_event`, `read_session_timeline`, idle-gap derivation, per-session cap + truncation marker, fail-silent wrapper, per-session append lock.
- Use `type` discriminator over v1's own event set; record unrecognised types verbatim under `{"type":"unknown","raw":...}`.

### 2. Wire tap points
- **Task ID**: build-taps
- **Depends On**: build-recorder
- **Validates**: tests/unit/test_sdk_client.py (update), tests/unit/ session_lifecycle + session_health coverage (update)
- **Assigned To**: recorder-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `record_telemetry_event` calls beside existing `record_*`/`accumulate_*` in `agent/sdk_client.py`'s event loop (harness `result` handler `:2771-2773` + SDK `ResultMessage` handler `:1738-1780`). Record `token_usage` as the raw per-turn `usage` dict verbatim (C5) — no delta math.
- Emit the *plain* `status_transition` (`{from, to, reason, kill: None}`) in BOTH `models/session_lifecycle.py::transition_status:453` (non-terminal) and `::finalize_session:217` (terminal) (C1); add an optional `emit_telemetry: bool = True` kwarg to both for de-dup.
- At `agent/session_health.py::_apply_recovery_transition` (`:1132`), emit the *kill-enriched* `status_transition` by consuming the in-scope `_kill_result: SubprocessKillResult` (`:1337`, `.confirmed_dead`/`.signal_sent`) + `entry.claude_pid`, and pass `emit_telemetry=False` to that path's `finalize_session`/requeue so no duplicate plain event is written. No kwarg-threading through the lifecycle functions.

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
- Add a trace-file retention sweep (delete > N days) into `agent/session_health.py:2418` `cleanup_corrupted_agent_sessions()` (the `agent-session-cleanup` reflection); `log()` what was deleted.

### 5. Tests
- **Task ID**: build-tests
- **Depends On**: build-taps, build-cli, build-retention
- **Assigned To**: telemetry-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: recorder, idle-gap, cap/truncation, fail-silent (forced OSError), malformed-line read, empty/None inputs, unknown-event passthrough, recovery-site kill-enriched `status_transition` + `emit_telemetry=False` de-dup.
- Integration: short real session → assert trace has `turn_*`/`token_usage`/`status_transition` retrievable by id; CLI renders it.

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
| status_transition (lifecycle) | `grep -n status_transition models/session_lifecycle.py` | output contains status_transition |
| status_transition (recovery kill) | `grep -n status_transition agent/session_health.py` | output contains status_transition |
| CLI subcommand present | `python -m tools.valor_session telemetry --help` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

Critique verdict: **READY TO BUILD (with concerns)** — 0 blockers, 5 concerns, 3 nits. All folded into the plan. **Re-based 2026-06-15** against current main `0d000e59`; the implementation notes below are updated where the original critique fix referenced now-stale code (the kwarg-threading design and a few line numbers).

| Severity | Critic | Finding | Addressed By | Implementation Note (current) |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic/Adversary/Consistency | C1: `transition_status` is a free function in `models/session_lifecycle.py:453` (not a method on `agent_session.py`), and terminal kills go through `finalize_session:217` which the plan never tapped — so the END of a session's story would be missing. | Data Flow #4, Solution tap points, Task 2, Test Impact, Success Criteria, Verification all rewired to `models/session_lifecycle.py` covering BOTH functions. | Both are free functions taking `(session, new_status, reason=...)`. **Re-based:** kill outcome is NO LONGER kwarg-threaded — the recovery site `_apply_recovery_transition` (`:1132`) consumes the in-scope `SubprocessKillResult` (`:1337`) and emits the enriched event itself, passing `emit_telemetry=False` to its lifecycle call for de-dup. |
| CONCERN | Operator/User | C2: plan claimed `valor-session` is a console script — it is not in `pyproject.toml [project.scripts]`. | Agent Integration corrected to `python -m tools.valor_session telemetry`; preserves "no update changes." | Verified against current main: no script entry; `main()` at `tools/valor_session.py:1273`, `__main__` at `:1442`. |
| CONCERN | Adversary | C3: per-session lock must cover open-or-reuse-handle + write + evict, and the lock registry creation is itself a micro-race. | Race Conditions Race 1 rewritten: lock guards full sequence; registry via `setdefault` (GIL-atomic); eviction under same lock. | Race 1 trigger now lists three writers (executor task, scheduler thread, worker-loop recovery path). |
| CONCERN | Skeptic/Adversary | C4: idle_gap is reactive (needs a "next event"); a long idle emits none until the next transition — so the promised terminal idle_gap depends on C1. | Idle-gap derivation note + e2e test must drive the session to a terminal transition via the `finalize_session` path. | Order: emit synthetic `idle_gap`, then `status_transition`. |
| CONCERN | Skeptic/Archaeologist | C5: schema said `token_delta` but the harness `result` event carries per-turn `usage`; computing a delta risks double-count. | Renamed to `token_usage`; record raw `usage` dict verbatim, consumer diffs. | Verified at `sdk_client.py:2771-2773` — `usage` is per-turn per the inline comment. |
| NIT | — | N1: Task 4 had no Validates. | Added `test_retention_deletes_old_traces`. | — |
| NIT | — | N2: retention-sweep location unstated. | Pinned to `agent/session_health.py:2418 cleanup_corrupted_agent_sessions()` (re-based from `:2144`). | — |
| NIT | — | N3: Open Question #1 (Popoto) already answered by the plan. | Converted to a stated decision. | — |

---

## Open Questions

1. **Consumer surface:** CLI timeline for v1, dashboard view deferred. Acceptable, or is a `ui/` view required for v1 acceptance?
2. **Retention window N:** default proposed 14 days for trace files. Right balance of diagnostic value vs disk?
3. **Idle-gap threshold:** default 60s before emitting an `idle_gap` event. Too chatty / too coarse?

_(Sink choice resolved — JSONL-on-disk under `logs/session_telemetry/`; see Architectural Impact for why not Popoto/Redis. The no-raw-Redis rule is hook-enforced and the `session_events` RMW hazard is real, so this is a decision, not an open question.)_
