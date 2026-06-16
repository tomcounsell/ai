---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1710
last_comment_id: 4715280523
revision_applied: true
---

# Granite Startup-Failure Fast Diagnostic

## Problem

The granite PTY container (`agent/granite_container/container.py`) runs an interactive `claude` TUI in a pseudo-terminal. On startup it primes two PTYs (PM + Dev) and loops until both reach idle. When that startup never resolves, the failure is **silent and slow to diagnose**.

**Current behavior:** On the 2026-06-16 cyndra outage, every session hung at startup with `Unknown command: /granite:prime-pm-role`. The container sat logging `container: startup cycle=N pm_idle=... dev_idle=... response=None` (`container.py:841-847`) for ~59 cycles, burned the full `STARTUP_HARD_CEILING_S = 600.0` ceiling (`container.py:106`), exited `startup_unresolved`, produced zero output, and delivered no reply. The only external tell was a buried log line. The offending TUI frame — which literally said `Unknown command: /granite:prime-pm-role` — was in hand on every cycle (`container.py:838-839`) but discarded. Root-causing it took an ~8-hour zombie window plus a deep multi-step investigation.

Startup can fail unresolved for multiple distinct reasons (command-sync bug, transcript-empty, a reworded TUI prompt the parser doesn't recognize). The common thread is not the cause; it's that **the failure moment carries no loud, self-describing diagnostic** and wastes 10 minutes of wall-clock per session before giving up.

**Desired outcome:** a startup that fails to resolve should be **loud and self-diagnosing at failure time** — detect a stuck-state plateau early (N consecutive identical no-progress cycles), capture the offending PM/Dev frame as a diagnostic artifact attached to the session record, emit it loudly (the existing `exit_anomaly` event carrying the frame **plus** a direct human-reachable Telegram notification), and bail fast instead of grinding the full 600s ceiling. This is detection + diagnostic + alert, root-cause-agnostic.

## Freshness Check

**Baseline commit:** `1213936d`
**Issue filed at:** 2026-06-16T04:54:37Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `container.py:106` — `STARTUP_HARD_CEILING_S = 600.0` — still holds (was cited as `:105`; minor drift, now at `:106`).
- `container.py:128` — `STARTUP_CYCLE_TIMEOUT_S = 3.0` — still holds.
- `container.py:837-875` — startup loop polling to ceiling, no early bail — confirmed verbatim. Cited as `:780+`; the loop body is at `:837-875`.
- `container.py:838-839` — `pm_idle`/`dev_idle` tuples each carry the PTY buffer as element `[1]` — confirmed. The frame IS available at failure time.
- `container.py:867` — only `{"cycle", "response"}` appended to `startup_events`; buffers dropped — confirmed.
- `bridge_adapter.py:409-453` — `_maybe_publish_exit_anomaly` already treats `startup_unresolved` as a hard exit, logs ERROR (Sentry-captured), appends `exit_anomaly` session_event — confirmed; this is the extension point.
- `reflections/sdlc_progress.py:206-221` — `_send_alert` → `valor-telegram send --chat "Eng: Valor"` fail-silent — confirmed; the notification precedent.
- `models/agent_session.py:300-322` — granite diagnostic fields (`exit_reason`, `pm_pid`, `pty_slot`, transcript paths) — confirmed; additive field pattern.

**Cited sibling issues/PRs re-checked:**
- #1538 — OPEN. Offline-learning recorder; consumes the same event. Not merged → #1710 must not depend on it.
- #1539 — OPEN. Auto-resume policy; coordination point on resume gating.
- #1708 — CLOSED 2026-06-16 (PR #1713). Fixed one specific cause; orthogonal to #1710.
- #1648 — CLOSED 2026-06-12. Prior art on granite dashboard telemetry/PTY identity fields.

**Commits on main since issue was filed (touching referenced files):** none touching `container.py` startup loop or `bridge_adapter.py` exit-anomaly path after the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** `granite_pty_production_cutover.md` (upstream — established the startup loop; #1710 hardens it), `session_telemetry_recorder.md` (epic #1536 v1 — the future event substrate #1710's frame feeds into; coordinate via shared field, do not block). No conflicting active plan.

**Notes:** Issue file:line pointers drifted by one line (`:105`→`:106`) under a recent edit; claims all hold.

## Prior Art

- **#1648** (closed 2026-06-12): Dashboard telemetry hollow for granite PTY. Added the transcript tailer and PTY identity fields (`pm_pid`, `dev_pid`, transcript paths, `pty_slot`) persisted on AgentSession. Establishes the additive-field pattern #1710 reuses for the diagnostic frame.
- **#1708** (closed 2026-06-16, PR #1713): Fixed a *specific* startup cause (PM transcript read empty / catchup persona resolution). Added `[granite-container]` grep tags. Orthogonal — #1710 makes *any* unresolved startup loud, regardless of cause.
- **#1537** (closed 2026-06-03): Liveness recovery blind spot. Introduced a structured `SubprocessKillResult` return — precedent for returning structured diagnostic data rather than logging-only.
- **PR #1612** (merged 2026-06-11): Production cutover; introduced `STARTUP_HARD_CEILING_S = 600.0` and the startup loop. The 600s ceiling is deliberate (cold-Opus persona load); #1710 must not shorten it, only add an *orthogonal* early-bail on a confirmed plateau.
- **PR #1694** (merged 2026-06-15): Persona-as-priming refactor that (with a non-recursive glob, fixed in `3a3ff1ab`) caused the 2026-06-16 outage this issue responds to.
- No prior attempt at plateau detection or frame capture exists. Confirmed: `tests/unit/granite_container/test_container.py` covers the ceiling exit but has no plateau or frame-capture test.

## Data Flow

1. **Entry point:** Worker dequeues a bridge-originated AgentSession → `BridgeAdapter` → `Container.run()` (`container.py:776`).
2. **Prime + startup loop:** `run()` primes PM/Dev, then enters the startup loop (`container.py:837`). Each cycle reads both PTYs: `pm_idle = self._startup_cycle_idle(self._pm_pty)` and `dev_idle = ...`, each a `(saw_idle, buffer, idle_marker, elapsed_ms)` tuple (`container.py:669-680`). `_handle_startup(pm_idle[1], dev_idle[1])` parses for known transient events (`container.py:599-640`).
3. **Failure point (today):** loop exits when `time.monotonic() >= startup_deadline`; sets `result.exit_reason = "startup_unresolved"` with a count-only message; the PM/Dev buffers held during the loop are discarded (`container.py:869-875`).
4. **Result persistence:** `BridgeAdapter._publish_exit_summary` writes `exit_reason` + PTY identity fields to the AgentSession (`bridge_adapter.py`). `_maybe_publish_exit_anomaly` logs ERROR + appends an `exit_anomaly` session_event (`bridge_adapter.py:409-453`).
5. **Output (today):** dashboard surfaces the `exit_anomaly` event; Sentry captures the ERROR log; the wrap-up guard delivers a terminal fallback to the user. No frame, no direct notification.

**New flow:** at step 3, before bailing, the loop captures the last PM/Dev frame snapshot + plateau metadata onto `ContainerResult` (plateau detected from a cumulative-`turn_buffer` fingerprint evaluated every cycle, not just no-event cycles); at step 4, the adapter folds the frame into the `exit_anomaly` event and the new AgentSession field; at step 5, the adapter checks a per-machine Redis cooldown key, then (if clear) fires a direct `valor-telegram` notification carrying a frame excerpt — suppressed sends log `[granite-alert-suppressed]` at ERROR. Early-bail (step 3) fires on a confirmed plateau well before the 600s deadline.

## Architectural Impact

- **New dependencies:** none. `valor-telegram` is already an installed CLI; `subprocess.run` already used in `reflections/`.
- **Interface changes:** `ContainerResult` gains nullable diagnostic fields (`startup_diagnostic_frame: str | None`, `startup_failure_kind: str | None`, `startup_plateau_cycles: int | None`). AgentSession gains one nullable field for the captured frame. Both additive; no signature breaks.
- **Coupling:** unchanged. Plateau detection and frame capture live entirely inside the startup loop. The notification reuses the existing `_maybe_publish_exit_anomaly` hook — no new alert subsystem.
- **Data ownership:** the container owns frame capture; the adapter owns persistence + notification (consistent with today's `exit_reason` split).
- **Reversibility:** high. Each piece (early-bail, frame capture, notification) is independently revertible; the additive fields are harmless if unread.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm alert channel + frame redaction policy)
- Review rounds: 1 (the startup-loop change is the sensitive surface)

## Prerequisites

No external prerequisites — `valor-telegram` is installed on every machine and the granite container is in-repo. The notification is fail-silent, so a missing/unauthenticated CLI degrades to log-only rather than blocking.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `valor-telegram` on PATH | `command -v valor-telegram` | Direct notification channel (degrades gracefully if absent) |

## Solution

### Key Elements

- **Plateau detector (early bail):** Inside the startup loop, track a fingerprint of *every* cycle — computed at the **top of the loop body, OUTSIDE the `response is None` guard** (the current code at `container.py:848` only reaches its `continue` on the no-event path; a session emitting a spurious startup event every other cycle would never accumulate consecutive no-progress cycles if counting lived there). The fingerprint is `(pm_idle_bool, dev_idle_bool, hash(pm_cumulative_tail + dev_cumulative_tail))` — **`response` is deliberately NOT part of the fingerprint** (an oscillating event would otherwise reset the counter forever). When the fingerprint is identical for **N consecutive cycles** (config constant `STARTUP_PLATEAU_CYCLES`), the startup is confirmed stuck and bails immediately — independent of and well before the 600s wall-clock ceiling. The 600s ceiling remains as the slow-cold-start backstop; the plateau detector is an orthogonal *fast* path for the deterministic-never-started (or oscillating-without-progress) case.
- **Frame capture:** At bail time (plateau OR ceiling), snapshot the last PM and Dev buffers into a single diagnostic frame string and attach it to `ContainerResult`. The frame is the diagnosis — it would have read `Unknown command: /granite:prime-pm-role` immediately.
- **Loud emission:** Extend `_maybe_publish_exit_anomaly` so the `exit_anomaly` session_event for `startup_unresolved` carries the frame (truncated), and fire a direct `valor-telegram` notification to the operator chat with a frame excerpt + session id. Reuses the existing ERROR/Sentry path; adds the human-reachable channel the broken session can't provide for itself.
- **Persistence (decided — persist now, bounded):** Store the (truncated, size-capped) frame on a new nullable AgentSession field so the dashboard and future #1538 recorder can read it without re-deriving. This is a settled decision, not contingent on #1538: persisting a *bounded* copy now is cheap (one nullable field, size-capped) and gives the dashboard the diagnosis immediately. #1538's recorder, when it lands, reads this field rather than re-deriving — it does not replace it.

### Flow

Startup loop running → N identical no-progress cycles detected → **plateau confirmed** → capture PM+Dev frame → set `exit_reason=startup_unresolved` + `startup_failure_kind=plateau` + frame → return early (seconds, not 600s) → BridgeAdapter persists frame to AgentSession + appends frame to `exit_anomaly` event + logs ERROR (Sentry) + fires `valor-telegram` alert → operator sees the exact stuck frame in one message.

(The 600s-ceiling path uses the same capture/emit tail with `startup_failure_kind=ceiling`.)

### Technical Approach

- **Plateau constants** in `container.py` alongside the existing startup constants: `STARTUP_PLATEAU_CYCLES = 10` (consecutive-identical threshold). At `STARTUP_CYCLE_TIMEOUT_S = 3s` per cycle, 10 cycles ≈ 30s of confirmed zero-progress — comfortably past transient cold-start jitter but ~95% short of the 600s ceiling. Document the relationship to `STARTUP_CYCLE_TIMEOUT_S` and that the value is a conservative starting point to tighten after observing real failures.
- **Fingerprint source = cumulative bytes, not the edge-triggered per-call buffer.** `_startup_cycle_idle` currently returns `result.buffer` (`container.py:680`), which `IdleResult` documents (`pty_driver.py:205`) as "the ANSI-stripped text read during THIS call only — edge-triggered." A repainting spinner emits *different bytes each cycle*, so hashing `buffer` would make the fingerprint never repeat and the detector never fire. The fix: change `_startup_cycle_idle` to surface `result.turn_buffer` (`pty_driver.py:219`, "ANSI-stripped capture since the last `write()`" — level-triggered, currently dropped at `container.py:680`) and fingerprint a bounded *tail* of that cumulative capture. A spinner repainting the same region produces the same cumulative tail every cycle (plateau holds); genuine new content (a real progressing load) changes the cumulative tail and resets the counter. Reset the consecutive-count whenever the fingerprint changes. The startup-event parser keeps consuming the edge-triggered `buffer` as today — only the fingerprint reads `turn_buffer`, so event-dedup semantics are unchanged.
- **Frame content:** stripped/printable text of the PM and Dev buffer tails, length-capped (e.g. last few KB each) to keep the session record small. Store both the raw-ish stripped text (human-readable, names the error) — not raw control bytes — to avoid bloat and ANSI noise. Cap total persisted frame size.
- **Capture helper:** a small pure function `_capture_startup_frame(pm_buf, dev_buf, kind, cycles) -> dict` so it is unit-testable without a live PTY. `ContainerResult` carries the result.
- **Notification:** add a fail-silent `_send_startup_alert(...)` in `bridge_adapter.py` modeled on `reflections/sdlc_progress.py::_send_alert` (`subprocess.run([... ], capture_output=True, timeout=3, check=False)`). Because the call uses `check=False` (matching the precedent at `sdlc_progress.py:209-214`), `CalledProcessError` is never raised — so the except clauses are **`FileNotFoundError` (CLI absent) and `subprocess.TimeoutExpired` only**, plus a final `except Exception` catch-all. Do NOT add a `CalledProcessError` branch (it would be dead code; a non-zero exit returns normally under `check=False`). Use `timeout=3` (NOT the precedent's `timeout=10`) — see Race Conditions for the worker-thread blocking rationale. Fired only for `startup_unresolved` (not other anomalies) to avoid alert fatigue, and gated by a per-machine Redis cooldown (below) to prevent storms.
- **Alert-storm cooldown (per-machine Redis TTL):** a fleet-wide outage hangs *every* incoming session, so a naive "one alert per hung session" fires an alert storm. Before sending, set a per-machine cooldown key (`granite:startup_alert_cooldown:{machine}`) with a ~5 min TTL via the Popoto Redis client, modeled on `reflections/sdlc_progress.py::_dedup_set` (`SET key val NX EX <ttl>`). If the key already exists (someone alerted within the window), **skip the Telegram send** but still log + capture. Result: a fleet outage produces one alert per 5 min, not one per session. Redis-unavailable degrades to "send anyway" is acceptable here (better a duplicate alert than a silenced outage) — note this differs from `_dedup_set`'s skip-on-unavailable, deliberately, because the alert is the whole point.
- **Visible suppression:** whenever the alert is suppressed (cooldown active, OR the `valor-telegram` send fails/CLI-absent), emit `logger.error("[granite-alert-suppressed] ...")` (NOT `logger.warning`) so Sentry captures the suppression. A silently-swallowed alert during an outage is itself an outage signal; promoting it to ERROR with the `[granite-alert-suppressed]` grep tag makes the suppression itself observable.
- **#1539 coordination:** the diagnostic carries `startup_failure_kind`; a `plateau`/deterministic-never-started kind is the signal #1539's future auto-resume should treat as "do NOT auto-resume, alert a human instead." #1710 only *records and alerts*; it adds no resume logic. The field is the coordination surface so the alert fires before any future resume attempt.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_send_startup_alert` swallows `FileNotFoundError` (CLI absent) and `subprocess.TimeoutExpired` — each branch has a test asserting a `logger.error("[granite-alert-suppressed] ...")` is emitted and the container result is unaffected (notification never crashes the run). **No `CalledProcessError` test** — the subprocess uses `check=False` (matching `sdlc_progress.py:209`), so that exception is never raised; testing it would assert against unreachable code.
- [ ] Cooldown gate: a test asserts that when the per-machine Redis cooldown key is already set, the Telegram subprocess is NOT invoked, and a `[granite-alert-suppressed]` ERROR is logged. A sibling test asserts the first call (no cooldown key) DOES invoke the subprocess and sets the key.
- [ ] Frame capture (`_capture_startup_frame`) must never raise on empty/None buffers — a test asserts it returns a well-formed dict for `("", "")` and for None inputs.
- [ ] Confirm no new bare `except Exception: pass` is introduced; the existing anomaly path's gating is preserved.

### Empty/Invalid Input Handling
- [ ] `_capture_startup_frame` with empty PM and Dev buffers returns a frame string indicating "no buffer content" rather than an empty/misleading artifact.
- [ ] Plateau detector with zero cycles run (immediate idle) does not false-positive a plateau.
- [ ] Whitespace-only / control-byte-only buffers are stripped to a readable (possibly empty-flagged) frame.

### Error State Rendering
- [ ] Test that on `startup_unresolved` the `exit_anomaly` session_event carries the frame field (non-empty) and the ERROR log fires.
- [ ] Test that the `valor-telegram` alert subprocess is invoked with a message containing the session id and a frame excerpt (assert on the mocked subprocess args) AND with `timeout=3` in the `subprocess.run` kwargs (worker-thread blocking bound).
- [ ] Verify the frame is truncated below the size cap so it does not bloat the AgentSession record.

## Test Impact

- [ ] `tests/unit/granite_container/test_container.py::test_never_idle_exits_startup_unresolved_at_ceiling` — UPDATE: still asserts ceiling exit, but also assert the captured frame + `startup_failure_kind=ceiling` are now populated on the result.
- [ ] `tests/unit/granite_container/test_container.py` — ADD: `test_plateau_bails_early_before_ceiling` (N identical cycles → early `startup_unresolved` with `startup_failure_kind=plateau`, well under the deadline, with a wall-time bound for the end-user-facing timing criterion), `test_oscillating_event_still_plateaus` (spurious event every other cycle still plateaus — BLOCKER regression guard), `test_progress_resets_plateau_counter` (changing cumulative tail resets the count), `test_capture_startup_frame_*` (empty/None/whitespace inputs).
- [ ] `tests/unit/granite_container/test_bridge_adapter.py::test_startup_unresolved_writes_exit_anomaly_event` — UPDATE: assert the `exit_anomaly` event now carries the frame field; ADD sibling tests asserting the `valor-telegram` alert subprocess is invoked (mocked, `timeout=3`), a missing CLI is swallowed with `[granite-alert-suppressed]` ERROR, and the Redis cooldown gate suppresses repeat sends.
- [ ] `tests/unit/test_session_executor_granite.py` (lines ~244, ~286) — VERIFY (likely no change): they parametrize `startup_unresolved` as an exit reason; confirm the added fields don't break routing.

No DELETE/REPLACE dispositions — all changes are additive to the startup-unresolved path.

## Rabbit Holes

- **Don't shorten or "tune" `STARTUP_HARD_CEILING_S`.** The 600s ceiling is deliberate for cold-Opus persona load (PR #1612). The plateau detector is an *additional* fast path, not a replacement. Shortening the ceiling would re-introduce the false-`startup_unresolved` that PR #1612's tests guard against.
- **Don't build a structured SessionEvent/telemetry recorder.** That is epic #1536 / #1538. Carry the frame on the existing `exit_anomaly` session_event + one AgentSession field; let #1538 read it later.
- **Don't add auto-resume or recovery logic.** That is #1539. #1710 is detection + diagnostic + alert only.
- **Don't try to attribute the frame to a precise PM-vs-Dev origin with a per-PTY event tag.** The existing loop already uses a "send to PM" heuristic (`container.py:861-866`); capturing *both* buffers in the frame sidesteps the attribution rabbit hole entirely.
- **Don't parse/interpret the frame content** (e.g. regex-classify the failure cause). The frame is for a human; classification is out of scope and overlaps #1538's advisory classifier.

## Risks

### Risk 1: Plateau threshold too aggressive → false early bail on a genuinely slow cold start
**Impact:** A slow-but-progressing Opus persona load gets killed as a "plateau" before it finishes, regressing into the exact false-`startup_unresolved` PR #1612 fought.
**Mitigation:** The fingerprint resets on *any* buffer change, so a progressing load (streaming repaint with new content) never plateaus. The threshold is tuned to require N *identical* cycles (no new bytes), which a live load does not produce. The ceiling backstop is untouched. A test asserts a "late settle" run (content arriving slowly) does NOT trip the plateau.

### Risk 2: Alert fatigue / notification storm
**Impact:** A fleet-wide outage hangs every incoming session; a naive "one alert per hung session" floods the operator chat (the exact failure shape of the 2026-06-16 outage this plan responds to).
**Mitigation:** Two layers. (1) Fire the direct notification only for `startup_unresolved` (not all anomalies). (2) A per-machine Redis TTL cooldown key (`granite:startup_alert_cooldown:{machine}`, ~5 min, `SET ... NX EX` modeled on `sdlc_progress.py::_dedup_set`) collapses an outage to one alert per window per machine. Suppressed alerts are NOT silent — they log `[granite-alert-suppressed]` at ERROR for Sentry. Notification is fail-silent, best-effort, single and compact (session id + short frame excerpt), and bounded to `timeout=3`.

### Risk 3: Frame leaks sensitive content or bloats the session record
**Impact:** A captured frame could contain large/raw output or sensitive user text persisted on the AgentSession.
**Mitigation:** Strip to printable text, length-cap each buffer tail and the total persisted frame. The frame is a startup-phase TUI snapshot (welcome/error frames), not user message bodies. Cap enforced with a test.

## Race Conditions

The startup loop is single-threaded (`container.py:21-24` documents the loop is single-threaded; reads from both PTYs are not interleaved within a tick). Plateau detection and frame capture run entirely within that single-threaded loop on locally-owned buffers.

The only cross-thread surface is the notification: `_send_startup_alert` runs in the same worker thread that already calls `_maybe_publish_exit_anomaly` (post-run, after the container loop completes). No shared mutable state is read concurrently.

**Worker-thread blocking hazard:** `_send_startup_alert` runs a synchronous `subprocess.run` on the worker thread. The precedent's `timeout=10` would block the worker for up to 10 seconds per hung session — and during a fleet-wide outage, that blocking stacks across every failing session, compounding the very outage we're trying to surface. We therefore use **`timeout=3`** (not the precedent's 10). The cooldown gate (per-machine Redis TTL) further bounds this: after the first alert, subsequent suppressed sessions skip the subprocess entirely (a fast Redis read, not a 3s subprocess), so the worst-case blocking during an outage is one 3s `subprocess.run` per 5 min, not one per session.

The Redis cooldown key is the one piece of shared cross-process state: multiple machines never share it (it's per-machine, keyed by machine name), and within a machine the `SET ... NX EX` is atomic, so concurrent post-run handlers cannot both win the cooldown.

No further race conditions identified — frame capture and plateau detection are synchronous and single-threaded; notification is post-run, cooldown-gated, fail-silent, and bounded to a 3s timeout.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1539] Auto-resume / recovery policy from the captured signature. #1710 records `startup_failure_kind` as the coordination surface; the resume decision is #1539's.
- [SEPARATE-SLUG #1538] The durable structured session-telemetry event recorder / advisory classifier. #1710 emits the frame onto the existing `exit_anomaly` event + an AgentSession field; the recorder that ingests it for offline learning is #1538.
- [SEPARATE-SLUG #1538] Cross-machine alert aggregation / a central alert router. #1710 includes a *per-machine* Redis cooldown (in scope — it's the minimum needed so a single outage doesn't storm one chat), but fleet-wide alert deduplication across machines, alert routing/escalation policy, and any richer rate-limit topology fold into the telemetry/observability work, not this plan.

## Update System

No update system changes required — this feature is purely internal to the granite container and bridge adapter. No new dependencies (`valor-telegram` already installed on every machine), no new config files, no migration steps. The new nullable AgentSession field needs no migration: Popoto's `_heal_descriptor_pollution` walks fields generically (per issues #1099/#1172), so existing records read the new field as None. The new per-machine Redis cooldown key (`granite:startup_alert_cooldown:{machine}`) is a self-expiring TTL key created on demand — no schema, no migration, no provisioning; Redis is already a hard dependency of the running system.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. The granite container is invoked by the worker, not by the agent's tools. The `valor-telegram` notification is an *outbound* operator alert (the system telling a human), not a new capability the agent invokes. No MCP server, no `.mcp.json` change, no new CLI entry point. The bridge already imports and runs the container via `BridgeAdapter`; the changes live inside that existing path.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document the plateau detector (`STARTUP_PLATEAU_CYCLES`, cumulative-`turn_buffer` fingerprint, reset behavior, why it counts every cycle not just no-event cycles), the captured frame artifact (what it contains, size cap, where it lands on AgentSession + `exit_anomaly` event), and the direct `valor-telegram` startup-failure alert (per-machine Redis cooldown, `timeout=3`, `[granite-alert-suppressed]` ERROR tag). Update the startup/failure-behavior section and the observability event table to note the frame-carrying `exit_anomaly` event.
- [ ] No new index entry needed (granite-pty-production.md already in `docs/features/`).

### Inline Documentation
- [ ] Docstring/comment on `_capture_startup_frame` and the plateau constants explaining the fingerprint-reset semantics and why the plateau path is orthogonal to (not a replacement for) the 600s ceiling.
- [ ] Comment on `_send_startup_alert` noting it is fail-silent, fires only for `startup_unresolved`, uses `timeout=3` (worker-thread blocking bound), is gated by the per-machine Redis cooldown, and logs `[granite-alert-suppressed]` at ERROR on suppression. Note explicitly why there is no `CalledProcessError` branch (`check=False`).

## Success Criteria

- [ ] A startup that produces N consecutive identical no-progress cycles bails with `exit_reason=startup_unresolved` and `startup_failure_kind=plateau` in seconds, not 600s (asserted by `test_plateau_bails_early_before_ceiling`).
- [ ] **End-user-facing timing:** on a confirmed plateau, the run returns (`Container.run` exits with `startup_unresolved`) within `STARTUP_PLATEAU_CYCLES × STARTUP_CYCLE_TIMEOUT_S` plus a small margin — i.e. tens of seconds, not the 600s ceiling — so the downstream terminal fallback the user receives arrives within seconds of plateau confirmation rather than ~10 minutes later. A test asserts the elapsed wall-time from plateau-onset to `run()` return is bounded (well under the 600s ceiling), making the user-visible "fast give-up" a hard, regression-guarded criterion rather than an implicit benefit.
- [ ] A slow-but-progressing cold start does NOT trip the plateau detector (asserted by a late-settle test).
- [ ] On `startup_unresolved`, the captured PM+Dev frame is persisted on the AgentSession and folded into the `exit_anomaly` session_event (non-empty, size-capped).
- [ ] On `startup_unresolved` (and no active cooldown), a direct `valor-telegram` notification fires carrying the session id and a frame excerpt; a missing/failing CLI is swallowed without crashing the run, logging `[granite-alert-suppressed]` at ERROR.
- [ ] The per-machine Redis cooldown suppresses repeat alerts within the TTL window (a fleet outage produces one alert per ~5 min, not one per session), and each suppression logs `[granite-alert-suppressed]` at ERROR (Sentry-visible).
- [ ] The 600s ceiling path still works and now also captures the frame (`startup_failure_kind=ceiling`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`) — `docs/features/granite-pty-production.md`
- [ ] grep confirms `bridge_adapter.py` references the new alert helper and `container.py` references the frame-capture helper.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (container)**
  - Name: `container-builder`
  - Role: Plateau detector + frame capture in `container.py`, new `ContainerResult` fields, capture helper.
  - Agent Type: builder
  - Resume: true

- **Builder (adapter)**
  - Name: `adapter-builder`
  - Role: Frame persistence + `exit_anomaly` enrichment + `_send_startup_alert` in `bridge_adapter.py`, new AgentSession field.
  - Agent Type: builder
  - Resume: true

- **Test engineer (startup)**
  - Name: `startup-tester`
  - Role: Plateau/frame/alert unit tests across `test_container.py` and `test_bridge_adapter.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (startup-diagnostic)**
  - Name: `startup-validator`
  - Role: Verify all success criteria, run full granite_container test suite + ruff.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `granite-doc`
  - Role: Update `docs/features/granite-pty-production.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Container: plateau detector + frame capture
- **Task ID**: build-container
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_container.py
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `STARTUP_PLATEAU_CYCLES` constant (with a comment relating it to `STARTUP_CYCLE_TIMEOUT_S` and the 600s ceiling).
- Add `_capture_startup_frame(pm_buf, dev_buf, kind, cycles) -> dict` pure helper: strip to printable text, length-cap each buffer tail + total, handle empty/None.
- Add nullable `ContainerResult` fields: `startup_diagnostic_frame: str | None`, `startup_failure_kind: str | None`, `startup_plateau_cycles: int | None`.
- Change `_startup_cycle_idle` (`container.py:669-680`) to surface `result.turn_buffer` (cumulative, level-triggered) in addition to / instead of the edge-triggered `result.buffer`, so the plateau fingerprint can read cumulative bytes. The startup-event parser keeps reading the edge-triggered `buffer`; only the fingerprint reads the cumulative tail.
- In the startup loop (`container.py:837-875`): compute the fingerprint at the **top of the loop body, BEFORE/OUTSIDE the `response is None` guard at `container.py:848`** so every cycle (including event-emitting ones) is evaluated. Fingerprint = `(pm_idle_bool, dev_idle_bool, hash(pm_cumulative_tail + dev_cumulative_tail))` — **exclude `response`** so an oscillating event cannot reset the counter forever. Count consecutive identical fingerprints; reset on any change. On reaching `STARTUP_PLATEAU_CYCLES`, capture the frame (`kind=plateau`) and bail early. On ceiling exit, capture the frame (`kind=ceiling`). Both set the new result fields.

### 2. Adapter: persistence + enrichment + notification
- **Task ID**: build-adapter
- **Depends On**: build-container
- **Validates**: tests/unit/granite_container/test_bridge_adapter.py
- **Assigned To**: adapter-builder
- **Agent Type**: builder
- **Parallel**: false
- Add one nullable AgentSession field for the captured frame (additive, follows `models/agent_session.py:300-322` pattern).
- In `_publish_exit_summary`: persist the frame field from `ContainerResult` (truncated to the cap).
- In `_maybe_publish_exit_anomaly`: fold the (truncated) frame into the `exit_anomaly` session_event dict; add fail-silent `_send_startup_alert(...)` (modeled on `reflections/sdlc_progress.py:206-221`) fired only for `startup_unresolved`, carrying session id + frame excerpt. Use `subprocess.run(..., timeout=3, check=False)`; except `FileNotFoundError` and `subprocess.TimeoutExpired` only (no `CalledProcessError` — `check=False`), plus an `except Exception` catch-all. Gate the send behind a per-machine Redis cooldown key (`granite:startup_alert_cooldown:{machine}`, ~5 min TTL, `SET ... NX EX` via the Popoto Redis client, modeled on `sdlc_progress.py::_dedup_set`); on cooldown-active OR send-failure, log `logger.error("[granite-alert-suppressed] ...")`.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-container, build-adapter
- **Assigned To**: startup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_plateau_bails_early_before_ceiling` (incl. the end-user-facing timing bound), `test_oscillating_event_still_plateaus` (a spurious event every other cycle still confirms a plateau — guards the BLOCKER fix), `test_progress_resets_plateau_counter` (changing cumulative tail resets the count), `test_capture_startup_frame_*` (empty/None/whitespace), and update `test_never_idle_exits_startup_unresolved_at_ceiling` to assert the new fields.
- Add/update `test_bridge_adapter.py`: `exit_anomaly` event carries the frame; `valor-telegram` alert invoked (mocked subprocess) with `timeout=3`; missing CLI swallowed with `[granite-alert-suppressed]` ERROR; cooldown-active suppresses the subprocess and logs `[granite-alert-suppressed]`; first-call sets the cooldown key. No `CalledProcessError` test (unreachable under `check=False`).

### 4. Validation
- **Task ID**: validate-startup
- **Depends On**: build-tests
- **Assigned To**: startup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/granite_container/ -q` and `python -m ruff check . && python -m ruff format --check .`.
- Verify every Success Criterion; report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-startup
- **Assigned To**: granite-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` (startup/failure behavior, plateau detector, frame artifact, observability event table, the alert channel).

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: startup-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the verification table; confirm docs updated; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Granite container tests pass | `pytest tests/unit/granite_container/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Frame capture helper referenced | `grep -rn "_capture_startup_frame" agent/granite_container/container.py` | output > 0 |
| Alert helper referenced | `grep -rn "_send_startup_alert" agent/granite_container/bridge_adapter.py` | output > 0 |
| Plateau constant present | `grep -rn "STARTUP_PLATEAU_CYCLES" agent/granite_container/container.py` | output > 0 |
| Alert cooldown present | `grep -rn "startup_alert_cooldown" agent/granite_container/bridge_adapter.py` | output > 0 |
| Suppression tag present | `grep -rn "granite-alert-suppressed" agent/granite_container/bridge_adapter.py` | output > 0 |
| Doc updated | `grep -rn "plateau" docs/features/granite-pty-production.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Plateau counting on the `response is None` path misses oscillation | Solution → Plateau detector; Task 1 | Fingerprint now computed every cycle, OUTSIDE the `response is None` guard; `response` excluded from the fingerprint. |
| BLOCKER | critique | Edge-triggered `result.buffer` fingerprint never repeats on a repainting spinner | Technical Approach → Fingerprint source; Task 1 | `_startup_cycle_idle` surfaces cumulative `turn_buffer`; fingerprint hashes its bounded tail; `response` dropped. |
| Concern | critique | `CalledProcessError` test untriggerable (`check=False`) | Solution → Notification; Failure Path Test Strategy | Dropped the `CalledProcessError` branch and its test; keep `FileNotFoundError` + `TimeoutExpired` + catch-all. |
| Concern | critique | Alert storm during fleet-wide outage | Solution → Alert-storm cooldown; Risk 2; Task 2 | Per-machine Redis TTL (~5 min) `SET ... NX EX` gate, modeled on `_dedup_set`. |
| Concern | critique | Fail-silent alert suppression invisible | Solution → Visible suppression | Suppression promoted to `logger.error("[granite-alert-suppressed] ...")` for Sentry. |
| Concern | critique | Worker-thread blocking hazard (`timeout=10`) | Solution → Notification; Race Conditions | Use `timeout=3`; cooldown skips subprocess on repeats; documented in Race Conditions. |
| Concern | critique | Open Question 3 contradicts committed tasks | Solution → Persistence; Open Questions Q3 | Resolved in place: persist a bounded copy now; #1538 reads, does not replace. |
| Concern | critique | No end-user-facing success criterion | Success Criteria | Added wall-time bound: fallback fires within tens of seconds of plateau confirmation, not 600s. |
| Concern | critique | Open Questions (threshold N, alert chat) unresolved | Open Questions Q1/Q2 | Resolved: `STARTUP_PLATEAU_CYCLES = 10` (~30s); alert chat `"Eng: Valor"`. |

---

## Open Questions

All open questions are resolved with concrete decisions (revision pass, post-critique). They are recorded here as decisions-with-rationale rather than questions, and will be removed at finalize.

1. **Plateau threshold value — RESOLVED: `STARTUP_PLATEAU_CYCLES = 10`.** At `STARTUP_CYCLE_TIMEOUT_S = 3s` per cycle, 10 identical cycles ≈ 30s of confirmed zero-progress before bailing. This is a conservative default: it clears transient cold-start jitter (the persona-load streams *new* bytes, resetting the fingerprint) yet saves ~95% of the 600s ceiling. The constant is documented as a tuning knob to tighten after observing real failures; starting conservative avoids regressing the false-`startup_unresolved` PR #1612 guards against.
2. **Alert channel — RESOLVED: `"Eng: Valor"`.** Matches the `reflections/sdlc_progress.py::_send_alert` precedent exactly (`--chat "Eng: Valor"`). A granite startup failure is an infra-level on-call signal, the same audience that already receives SDLC-progress alerts. No dedicated chat is warranted at this volume (the per-machine cooldown already bounds frequency); a dedicated alert chat is a follow-up if alert taxonomy grows.
3. **Frame persistence scope — RESOLVED: persist a bounded copy now.** Covered in Solution → Persistence: a size-capped frame is stored on a new nullable AgentSession field immediately so the dashboard shows the diagnosis without waiting for #1538. #1538's recorder reads this field rather than replacing it. (This resolves the prior contradiction between Q3 and Task 2 / Success Criterion 3, which already committed to persisting now.)
4. **#1539 coordination — RESOLVED: record `startup_failure_kind`, add no resume logic.** #1710 records `startup_failure_kind` (`plateau` = deterministic-never-started / oscillating-without-progress; `ceiling` = slow-or-stuck cold start) as the lightweight coordination surface. The resume *decision* (treat `plateau` as no-auto-resume, alert a human) belongs to #1539; #1710 adds none of that logic — it only records the field and fires the alert so a human is reachable before any future resume attempt.
