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

**New flow:** at step 3, before bailing, the loop captures the last PM/Dev level-triggered frame snapshot + plateau metadata onto `ContainerResult` (plateau detected from a **write-independent fingerprint** — `(pm_idle_bool, dev_idle_bool, response)`, evaluated every cycle, not just no-event cycles; `response` is the parser's pre-write verdict, stable across an oscillating event that `write()` would otherwise reset); at step 4, the adapter folds the frame + `startup_failure_kind` into the `exit_anomaly` event and the new AgentSession field; at step 5, the adapter checks the two-layer cooldown (process-local monotonic gate first, then per-machine Redis key), then (if clear) fires a direct `valor-telegram` notification carrying a frame excerpt + failure kind — suppressed sends (cooldown active or send-fail, NOT Redis-down) log `[granite-alert-suppressed]` at ERROR. Early-bail (step 3) fires on a confirmed plateau well before the 600s deadline.

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

- **Plateau detector (early bail):** Inside the startup loop, track a fingerprint of *every* cycle — computed at the **top of the loop body, OUTSIDE the `response is None` guard** (the current code at `container.py:848` only reaches its `continue` on the no-event path; a session emitting a spurious startup event every other cycle would never accumulate consecutive no-progress cycles if counting lived there). The fingerprint is **write-independent** by construction: `(pm_idle_bool, dev_idle_bool, response)`, where `response` is the value `_handle_startup` returns *this cycle* (`container.py:840`, computed by the pure parser BEFORE any `write()`). When the fingerprint is identical for **N consecutive cycles** (config constant `STARTUP_PLATEAU_CYCLES`), the startup is confirmed stuck and bails immediately — independent of and well before the 600s wall-clock ceiling. The 600s ceiling remains as the slow-cold-start backstop; the plateau detector is an orthogonal *fast* path for the deterministic-never-started (or oscillating-without-progress) case.
  - **Why this signal is write-independent (the BLOCKER fix):** The defeated prior fix hashed the cumulative `turn_buffer` tail. That fails because the oscillating-event path calls `self._pm_pty.write(response)` (`container.py:866`), and `write()` resets `_turn_text = ""` BEFORE sending (`pty_driver.py:456`, docstring lines 439-442). `turn_buffer` is "capture since the last `write()`" (`pty_driver.py:208,219`), so after each oscillating write the PM `turn_buffer` restarts from empty and the cumulative tail changes every cycle — the hash never repeats and the counter never accumulates. The correct signal is the parser's *verdict*, not the post-write byte tail: `response` is derived purely from the edge-triggered buffers by `_handle_startup` BEFORE the write (`container.py:599-640` is documented "pure with respect to the input buffers"), so a recurring event yields the **same `response` string every cycle** and the fingerprint repeats. The two stuck shapes both accumulate: an oscillating event repeats a non-None `response` (fingerprint stable); a silent never-started PTY returns `response=None` with both idle bools `False` (fingerprint stable at `(False, False, None)`). Genuine progress flips an idle bool or changes the parser's verdict to a new event, resetting the count. A repainting spinner that changes raw bytes but yields no recognized event keeps `response=None` and idle `False`, so the fingerprint is correctly stable — we hash the verdict, not the bytes, so spinner repaint is a non-issue.
- **Frame capture:** At bail time (plateau OR ceiling), snapshot the last PM and Dev buffers into a single diagnostic frame string and attach it to `ContainerResult`. The frame is the diagnosis — it would have read `Unknown command: /granite:prime-pm-role` immediately.
- **Loud emission:** Extend `_maybe_publish_exit_anomaly` so the `exit_anomaly` session_event for `startup_unresolved` carries the frame (truncated) **and `startup_failure_kind`** (`plateau` vs `ceiling`), and fire a direct `valor-telegram` notification to the operator chat carrying the frame excerpt + session id **+ the failure kind** (so the operator sees "plateau — deterministic stuck" vs "ceiling — slow/never-settled" without opening the dashboard). This makes `startup_failure_kind` a real in-plan consumer: it is surfaced in both the `exit_anomaly` event payload and the human-facing alert text, not just stored. Reuses the existing ERROR/Sentry path; adds the human-reachable channel the broken session can't provide for itself.
- **Persistence (decided — persist now, bounded):** Store the (truncated, size-capped) frame on a new nullable AgentSession field so the dashboard and future #1538 recorder can read it without re-deriving. This is a settled decision, not contingent on #1538: persisting a *bounded* copy now is cheap (one nullable field, size-capped) and gives the dashboard the diagnosis immediately. #1538's recorder, when it lands, reads this field rather than re-deriving — it does not replace it.

### Flow

Startup loop running → N identical no-progress cycles detected → **plateau confirmed** → capture PM+Dev frame → set `exit_reason=startup_unresolved` + `startup_failure_kind=plateau` + frame → return early (seconds, not 600s) → BridgeAdapter persists frame to AgentSession + appends frame to `exit_anomaly` event + logs ERROR (Sentry) + fires `valor-telegram` alert → operator sees the exact stuck frame in one message.

(The 600s-ceiling path uses the same capture/emit tail with `startup_failure_kind=ceiling`.)

### Technical Approach

- **Plateau constants** in `container.py` alongside the existing startup constants: `STARTUP_PLATEAU_CYCLES = 10` (consecutive-identical threshold). At `STARTUP_CYCLE_TIMEOUT_S = 3s` per cycle, 10 cycles ≈ 30s of confirmed zero-progress — comfortably past transient cold-start jitter but ~95% short of the 600s ceiling. Document the relationship to `STARTUP_CYCLE_TIMEOUT_S` and that the value is a conservative starting point to tighten after observing real failures.
- **Fingerprint source = the parser verdict + idle bools, NOT a byte tail.** The fingerprint is `(pm_idle_bool, dev_idle_bool, response)` (`pm_idle[0]`, `dev_idle[0]`, and the `response` returned by `_handle_startup` at `container.py:840`). This is deliberately *not* derived from any PTY buffer tail, because the only buffer that survives across cycles (`turn_buffer`) is reset by `write()` on the oscillating-event path (see the BLOCKER note in Key Elements). `response` is the parser's verdict, computed pre-write from the edge-triggered buffers, so it is stable across an oscillation and changes only when the recognized event changes. Reset the consecutive-count whenever the fingerprint changes.
- **`_startup_cycle_idle` keeps its edge-triggered return — do NOT blanket-swap it to `turn_buffer`.** The startup-event parser (`_handle_startup` → `parse_startup_frame`) depends on the edge-triggered `result.buffer` (`container.py:680`, `pty_driver.py:205` "text read during THIS call only") precisely so an event is not re-detected and re-answered on every poll tick. Swapping the return to `result.turn_buffer` (as `_cycle_idle` does at `container.py:664`) would regress edge-triggered event parsing into level-triggered, re-firing dismissed events. Because the plateau fingerprint now reads `response` (the parser verdict) and the idle bools — **not** a buffer tail — `_startup_cycle_idle` needs no change to its buffer at all. To make the level-triggered bytes available to *frame capture* (the diagnostic snapshot, a separate concern from the fingerprint) without disturbing the edge-triggered parse path, widen `_startup_cycle_idle`'s return tuple to surface **both** buffers — e.g. `(saw_idle, edge_buffer, level_tail, idle_marker, elapsed_ms)` where `edge_buffer = result.buffer` (unchanged, fed to the parser) and `level_tail = result.turn_buffer` (added, used only for the captured frame). The startup-event parser keeps reading `edge_buffer`; only frame capture reads `level_tail`. A test asserts the startup idle helper surfaces the level-triggered bytes distinctly from the edge-triggered buffer.
- **Frame content:** stripped/printable text of the PM and Dev buffer tails, length-capped (e.g. last few KB each) to keep the session record small. Store both the raw-ish stripped text (human-readable, names the error) — not raw control bytes — to avoid bloat and ANSI noise. Cap total persisted frame size.
- **Capture helper:** a small pure function `_capture_startup_frame(pm_buf, dev_buf, kind, cycles) -> dict` so it is unit-testable without a live PTY. `ContainerResult` carries the result.
- **Notification:** add a fail-silent `_send_startup_alert(...)` in `bridge_adapter.py` modeled on `reflections/sdlc_progress.py::_send_alert`. It shells out to `valor-telegram` best-effort, swallowing all failure modes (CLI absent / timeout / non-zero exit) so the notification never crashes the run. It fires only for `startup_unresolved` (not other anomalies) to avoid alert fatigue, is bounded by a short subprocess timeout (the worker-thread blocking rationale is in Race Conditions), and is gated by the two-layer cooldown (below) to prevent storms. The exact subprocess kwargs, the precise exception set, and why there is no `CalledProcessError` branch are pinned in Task 2.
- **Alert-storm cooldown — two layers, Redis primary + process-local fallback.** A fleet-wide outage hangs *every* incoming session, so a naive "one alert per hung session" fires a storm.
  - **Layer 1 (cross-process, Redis TTL):** before sending, attempt a per-machine cooldown key (`granite:startup_alert_cooldown:{machine}`) with a ~5 min TTL via the Popoto Redis client (`SET key val NX EX <ttl>`, modeled on `_dedup_set`). If the key already existed, the window is active → **suppress** (skip the Telegram send, still log + capture). If we set it, this process wins the window → send. This collapses a same-machine, cross-process storm to one alert per window.
  - **Layer 2 (process-local, in-memory monotonic fallback):** Layer 1 is useless when **Redis itself is the co-casualty** — a Redis-down outage hangs every session AND defeats the Redis gate, so without a second layer the storm protection silently evaporates in exactly the worst case. Add a process-local fallback in a distinctly-named helper `_should_alert(machine) -> bool` (NOT a `_dedup_set` clone — its contract is *inverted*: it returns True when sending is permitted). The helper holds a module-level `dict[str, float]` of last-alert monotonic timestamps keyed by machine; it returns True (and updates the timestamp) only if `time.monotonic() - last >= ~300s`, else False. This bounds a single worker process to one alert per ~5 min per machine **even with Redis fully down**. Order of operations in `_should_alert`: check the process-local monotonic gate FIRST (cheap, always available); only if it permits, attempt the Redis `SET NX EX`. Redis raising/unavailable is treated as "Redis declined to deduplicate, fall through to the process-local decision" — i.e. **send anyway** (better a duplicate alert than a silenced outage), because the process-local layer already bounds the rate. The helper docstring must state this inverted contract explicitly.
- **Visible suppression — fires on cooldown-active and send-fail, NOT on Redis-down.** When the alert is suppressed because a cooldown window is *active* (either layer says "already alerted"), OR the `valor-telegram` send *fails* (CLI absent / timeout / non-zero), emit `logger.error("[granite-alert-suppressed] ...")` (NOT `logger.warning`) so Sentry captures it — a swallowed alert during an outage is itself an outage signal. **Do NOT emit `[granite-alert-suppressed]` on the Redis-down path:** Redis being unavailable does not suppress the alert (Layer 2 still sends), so logging suppression there would be a false signal. A Redis-unavailable degrade logs at most a `logger.warning` like the `_dedup_set` precedent, distinct from the suppression tag.
- **#1539 coordination:** the diagnostic carries `startup_failure_kind`; a `plateau`/deterministic-never-started kind is the signal #1539's future auto-resume should treat as "do NOT auto-resume, alert a human instead." #1710 only *records and alerts*; it adds no resume logic. The field is the coordination surface so the alert fires before any future resume attempt.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_send_startup_alert` swallows `FileNotFoundError` (CLI absent) and `subprocess.TimeoutExpired` — each branch has a test asserting a `logger.error("[granite-alert-suppressed] ...")` is emitted and the container result is unaffected (notification never crashes the run). **No `CalledProcessError` test** — the subprocess uses `check=False` (matching `sdlc_progress.py:209`), so that exception is never raised; testing it would assert against unreachable code.
- [ ] Cooldown gate (two layers): a test asserts that when a cooldown window is active (Redis key set, OR the process-local monotonic gate within window), the Telegram subprocess is NOT invoked and a `[granite-alert-suppressed]` ERROR is logged. A sibling test asserts the first call DOES invoke the subprocess and arms both gates. A Redis-down test asserts the alert still sends (process-local permitting) and does NOT emit `[granite-alert-suppressed]` (inverted suppression contract); a follow-on test asserts a second Redis-down call within the process-local window is suppressed (Layer 2 protects the storm case where Redis is the co-casualty).
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
- [ ] `tests/unit/granite_container/test_container.py` — ADD: `test_plateau_bails_early_before_ceiling` (N identical cycles → early `startup_unresolved` with `startup_failure_kind=plateau`, well under the deadline, with a wall-time bound for the end-user-facing timing criterion), `test_oscillating_event_still_plateaus` (a recurring event repeating the same `response` still plateaus — BLOCKER regression guard), `test_progress_resets_plateau_counter` (a changing parser verdict / flipped idle bool resets the count), `test_startup_cycle_idle_surfaces_level_buffer` (helper surfaces level-triggered `turn_buffer` distinctly — no-blanket-swap guard), `test_capture_startup_frame_*` (empty/None/whitespace inputs).
- [ ] `tests/unit/granite_container/test_bridge_adapter.py::test_startup_unresolved_writes_exit_anomaly_event` — UPDATE: assert the `exit_anomaly` event now carries the frame field AND `startup_failure_kind`; ADD sibling tests asserting the `valor-telegram` alert subprocess is invoked (mocked, `timeout=3`, message contains the failure kind), a missing CLI is swallowed with `[granite-alert-suppressed]` ERROR, the cooldown gate (process-local + Redis) suppresses repeat sends, and the Redis-down path still sends without logging `[granite-alert-suppressed]`.
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
**Mitigation:** The fingerprint resets whenever the parser verdict (`response`) or either idle bool changes. A progressing load eventually flips an idle bool (or surfaces a new recognized event), resetting the count; a settled load breaks the loop entirely (both idle). The threshold requires N *identical* `(pm_idle, dev_idle, response)` tuples, which a live, advancing startup does not sustain — only a deterministically-stuck or oscillating-without-progress session does. The ceiling backstop is untouched. A test asserts a "late settle" run (idle arriving slowly) does NOT trip the plateau.

### Risk 2: Alert fatigue / notification storm
**Impact:** A fleet-wide outage hangs every incoming session; a naive "one alert per hung session" floods the operator chat (the exact failure shape of the 2026-06-16 outage this plan responds to).
**Mitigation:** Layered. (1) Fire the direct notification only for `startup_unresolved` (not all anomalies). (2) A two-layer cooldown via `_should_alert(machine)`: a **process-local monotonic gate** (module-level last-alert timestamp per machine, ~5 min) checked FIRST — this survives even a Redis-down fleet outage, the worst case the naive Redis-only gate silently failed; then a **per-machine Redis TTL key** (`granite:startup_alert_cooldown:{machine}`, ~5 min, `SET ... NX EX`) for cross-process dedup on the same machine. Redis-unavailable falls through to the process-local decision (send anyway — better one duplicate than a silenced outage). Suppressed alerts (cooldown active or send-fail) log `[granite-alert-suppressed]` at ERROR for Sentry; the Redis-down degrade is a `logger.warning`, NOT the suppression tag (it does not suppress). Notification is fail-silent, best-effort, single and compact (session id + failure kind + short frame excerpt), and bounded to a short subprocess timeout.

### Risk 3: Frame leaks sensitive content or bloats the session record
**Impact:** A captured frame could contain large/raw output or sensitive user text persisted on the AgentSession.
**Mitigation:** Strip to printable text, length-cap each buffer tail and the total persisted frame. The frame is a startup-phase TUI snapshot (welcome/error frames), not user message bodies. Cap enforced with a test.

## Race Conditions

The startup loop is single-threaded (`container.py:21-24` documents the loop is single-threaded; reads from both PTYs are not interleaved within a tick). Plateau detection and frame capture run entirely within that single-threaded loop on locally-owned buffers.

The only cross-thread surface is the notification: `_send_startup_alert` runs in the same worker thread that already calls `_maybe_publish_exit_anomaly` (post-run, after the container loop completes). No shared mutable state is read concurrently.

**Worker-thread blocking hazard:** `_send_startup_alert` runs a synchronous `subprocess.run` on the worker thread. The precedent's `timeout=10` would block the worker for up to 10 seconds per hung session — and during a fleet-wide outage, that blocking stacks across every failing session, compounding the very outage we're trying to surface. We therefore use **`timeout=3`** (not the precedent's 10). The cooldown gate (per-machine Redis TTL) further bounds this: after the first alert, subsequent suppressed sessions skip the subprocess entirely (a fast Redis read, not a 3s subprocess), so the worst-case blocking during an outage is one 3s `subprocess.run` per 5 min, not one per session.

The cooldown state has two scopes. The Redis cooldown key is the cross-process layer: multiple machines never share it (it's per-machine, keyed by machine name), and within a machine the `SET ... NX EX` is atomic, so concurrent post-run handlers in *different* processes cannot both win the cooldown. The process-local `_should_alert` timestamp dict is the in-process layer; the worker runs the post-run handler on a single worker thread, so concurrent in-process mutation of the dict is not a hazard in the current threading model (and if it ever ran concurrently, a benign double-send is the worst case, not corruption). The two layers are complementary: Redis handles cross-process dedup on a healthy machine; the process-local gate handles the Redis-down case where the cross-process layer is unavailable.

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
- [ ] Update `docs/features/granite-pty-production.md` — document the plateau detector (`STARTUP_PLATEAU_CYCLES`, the write-independent `(pm_idle, dev_idle, response)` fingerprint, why it counts every cycle not just no-event cycles, why it reads the parser verdict instead of a `turn_buffer` tail — the `write()` reset gotcha), the captured frame artifact (what it contains, size cap, where it lands on AgentSession + `exit_anomaly` event), and the direct `valor-telegram` startup-failure alert (two-layer cooldown: process-local monotonic gate + per-machine Redis key, the inverted `_should_alert` contract, `timeout=3`, `[granite-alert-suppressed]` ERROR tag fires on cooldown/send-fail but NOT Redis-down). Update the startup/failure-behavior section and the observability event table to note the frame-and-kind-carrying `exit_anomaly` event.
- [ ] No new index entry needed (granite-pty-production.md already in `docs/features/`).

### Inline Documentation
- [ ] Docstring/comment on `_capture_startup_frame` and the plateau constants explaining the fingerprint-reset semantics and why the plateau path is orthogonal to (not a replacement for) the 600s ceiling.
- [ ] Comment on `_send_startup_alert` noting it is fail-silent, fires only for `startup_unresolved`, uses `timeout=3` (worker-thread blocking bound), is gated by `_should_alert` (two-layer cooldown), and logs `[granite-alert-suppressed]` at ERROR on suppression (cooldown/send-fail, not Redis-down). Note explicitly why there is no `CalledProcessError` branch (`check=False`). `_should_alert`'s docstring must state its inverted contract (returns True = send permitted) and the process-local-first ordering.

## Success Criteria

- [ ] A startup that produces N consecutive identical no-progress cycles bails with `exit_reason=startup_unresolved` and `startup_failure_kind=plateau` in seconds, not 600s (asserted by `test_plateau_bails_early_before_ceiling`).
- [ ] **End-user-facing timing:** on a confirmed plateau, the run returns (`Container.run` exits with `startup_unresolved`) within `STARTUP_PLATEAU_CYCLES × STARTUP_CYCLE_TIMEOUT_S` plus a small margin — i.e. tens of seconds, not the 600s ceiling — so the downstream terminal fallback the user receives arrives within seconds of plateau confirmation rather than ~10 minutes later. A test asserts the elapsed wall-time from plateau-onset to `run()` return is bounded (well under the 600s ceiling), making the user-visible "fast give-up" a hard, regression-guarded criterion rather than an implicit benefit.
- [ ] A slow-but-progressing cold start does NOT trip the plateau detector (asserted by a late-settle test).
- [ ] On `startup_unresolved`, the captured PM+Dev frame is persisted on the AgentSession and folded into the `exit_anomaly` session_event (non-empty, size-capped).
- [ ] On `startup_unresolved` (and no active cooldown), a direct `valor-telegram` notification fires carrying the session id and a frame excerpt; a missing/failing CLI is swallowed without crashing the run, logging `[granite-alert-suppressed]` at ERROR.
- [ ] The two-layer cooldown suppresses repeat alerts within the ~5 min window (a fleet outage produces one alert per window per machine, not one per session) — including when Redis is the co-casualty, via the process-local monotonic gate — and each suppression logs `[granite-alert-suppressed]` at ERROR (Sentry-visible), while a Redis-down send does NOT log the suppression tag.
- [ ] The 600s ceiling path still works and now also captures the frame (`startup_failure_kind=ceiling`).
- [ ] **Surface-observable reply:** when a startup plateaus, the user who sent the originating message receives a terminal conversational reply at the Telegram surface within seconds (the existing wrap-up guard's fallback, now fired on the fast plateau-bail instead of after the 600s grind) — i.e. the human is no longer left with silence for ~10 minutes. A test (or, where unit coverage is infeasible, a documented manual-verification step) confirms the downstream fallback reply is emitted promptly after plateau confirmation, distinct from the operator-facing `valor-telegram` alert.
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
- Change `_startup_cycle_idle` (`container.py:669-680`) to widen its return tuple to surface **both** buffers: keep `result.buffer` (edge-triggered) as the value fed to the startup-event parser, and ADD `result.turn_buffer` (level-triggered) as a separate element used only by frame capture. Do NOT blanket-swap the parser's buffer to `turn_buffer` (that would regress edge-triggered event parsing into level-triggered, re-firing dismissed events). New signature e.g. `(saw_idle, edge_buffer, level_tail, idle_marker, elapsed_ms)`. The plateau fingerprint does NOT read either buffer — it reads the idle bools + `response`.
- In the startup loop (`container.py:837-875`): compute the fingerprint at the **top of the loop body, BEFORE/OUTSIDE the `response is None` guard at `container.py:848`** so every cycle (including event-emitting ones) is evaluated. Fingerprint = `(pm_idle[0], dev_idle[0], response)` — the write-independent signal: `response` is `_handle_startup`'s pre-write verdict (`container.py:840`), so an oscillating event repeats the same `response` and the counter accumulates, while a silent never-started PTY yields `(False, False, None)`. Do NOT hash any `turn_buffer` tail — `write(response)` (`container.py:866`) resets `_turn_text` (`pty_driver.py:456`), so the post-write cumulative tail is not stable across oscillating cycles (this is the defeated prior fix). Count consecutive identical fingerprints; reset on any change. On reaching `STARTUP_PLATEAU_CYCLES`, capture the frame (`kind=plateau`, from the level-triggered `level_tail` of both PTYs) and bail early. On ceiling exit, capture the frame (`kind=ceiling`). Both set the new result fields.

### 2. Adapter: persistence + enrichment + notification
- **Task ID**: build-adapter
- **Depends On**: build-container
- **Validates**: tests/unit/granite_container/test_bridge_adapter.py
- **Assigned To**: adapter-builder
- **Agent Type**: builder
- **Parallel**: false
- Add one nullable AgentSession field for the captured frame (additive, follows `models/agent_session.py:300-322` pattern).
- In `_publish_exit_summary`: persist the frame field from `ContainerResult` (truncated to the cap).
- In `_maybe_publish_exit_anomaly`: fold the (truncated) frame **and `startup_failure_kind`** into the `exit_anomaly` session_event dict; add fail-silent `_send_startup_alert(...)` (modeled on `reflections/sdlc_progress.py:206-221`) fired only for `startup_unresolved`, carrying session id + frame excerpt + the failure kind (`plateau`/`ceiling`).
  - Subprocess call: `subprocess.run(["valor-telegram", "send", "--chat", "Eng: Valor", message], capture_output=True, text=True, timeout=3, check=False)`. Use `timeout=3` (NOT the precedent's `timeout=10` — worker-thread blocking bound). Except `FileNotFoundError` and `subprocess.TimeoutExpired` only, plus a final `except Exception` catch-all. Do NOT add a `CalledProcessError` branch — `check=False` means a non-zero exit returns normally, so that branch is unreachable dead code.
  - Two-layer cooldown gate via a distinctly-named `_should_alert(machine) -> bool` helper (inverted contract vs `_dedup_set`: returns True when sending is permitted; docstring must state this). (1) Process-local: a module-level `dict[str, float]` of last-alert `time.monotonic()` timestamps per machine; permit only if `monotonic() - last >= ~300s`. Check this FIRST. (2) Cross-process: only if the process-local gate permits, attempt the Redis `SET granite:startup_alert_cooldown:{machine} val NX EX <~300s>` via the Popoto Redis client; key-already-existed → suppress, key-set → send. Redis raising/unavailable → fall through to the process-local decision (send anyway; `logger.warning`, NOT the suppression tag).
  - Suppression logging: on cooldown-active (either layer) OR send-failure, log `logger.error("[granite-alert-suppressed] ...")`. Do NOT log the suppression tag on the Redis-unavailable path (the alert still sends there).

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-container, build-adapter
- **Assigned To**: startup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_plateau_bails_early_before_ceiling` (incl. the end-user-facing timing bound), `test_oscillating_event_still_plateaus` (a spurious event repeating the SAME `response` every cycle — and an every-other-cycle variant — still confirms a plateau, since the fingerprint reads `response` not the post-write buffer; guards the BLOCKER fix), `test_progress_resets_plateau_counter` (a changing parser verdict / flipped idle bool resets the count), `test_startup_cycle_idle_surfaces_level_buffer` (the startup idle helper returns the level-triggered `turn_buffer` distinctly from the edge-triggered `buffer` — guards the no-blanket-swap CONCERN), `test_capture_startup_frame_*` (empty/None/whitespace), and update `test_never_idle_exits_startup_unresolved_at_ceiling` to assert the new fields.
- Add/update `test_bridge_adapter.py`: `exit_anomaly` event carries the frame AND `startup_failure_kind`; `valor-telegram` alert invoked (mocked subprocess) with `timeout=3` and a message containing the failure kind; missing CLI swallowed with `[granite-alert-suppressed]` ERROR; cooldown-active (process-local OR Redis) suppresses the subprocess and logs `[granite-alert-suppressed]`; first-call permits the send and arms both gates; a Redis-down case still sends (process-local permitting) and does NOT log `[granite-alert-suppressed]` (asserts the inverted suppression contract); a second call within the process-local window is suppressed even with Redis down. No `CalledProcessError` test (unreachable under `check=False`).

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
| Process-local cooldown gate present | `grep -rn "_should_alert" agent/granite_container/bridge_adapter.py` | output > 0 |
| Suppression tag present | `grep -rn "granite-alert-suppressed" agent/granite_container/bridge_adapter.py` | output > 0 |
| Doc updated | `grep -rn "plateau" docs/features/granite-pty-production.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | critique | Plateau counting on the `response is None` path misses oscillation | Solution → Plateau detector; Task 1 | Fingerprint computed every cycle, OUTSIDE the `response is None` guard. |
| BLOCKER (re-critique) | critique | Prior fix (hash cumulative `turn_buffer` tail) defeated by `write()` resetting `_turn_text` on the oscillating-event path | Solution → Plateau detector (BLOCKER note); Technical Approach → Fingerprint source; Task 1 | Fingerprint is now the **write-independent** `(pm_idle, dev_idle, response)` — `response` is `_handle_startup`'s pre-write verdict, stable across oscillation; NO `turn_buffer` tail is hashed. Verified against `pty_driver.py:456` and `container.py:866`. |
| Concern (re-critique) | critique | `_startup_cycle_idle` must not blanket-swap to `turn_buffer` (regresses edge-triggered event parse at `container.py:679` vs `:664`) | Technical Approach; Task 1; Tests | Helper widened to return BOTH buffers (edge `buffer` for parser, level `turn_buffer` for frame capture); added `test_startup_cycle_idle_surfaces_level_buffer`. |
| Concern (re-critique) | critique | Redis-down fleet outage has zero storm protection | Solution → Alert-storm cooldown (Layer 2); Risk 2; Race Conditions; Task 2 | Added process-local monotonic `_should_alert(machine)` fallback (inverted contract, checked first); Redis-down falls through to it; suppression tag fires only on cooldown/send-fail, NOT Redis-down. |
| Concern (re-critique) | critique | `startup_failure_kind` had no in-plan consumer | Solution → Loud emission; Task 2; Tests | Surfaced in both the `exit_anomaly` event payload and the human-facing alert text (plateau vs ceiling). |
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
