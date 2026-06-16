---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-16
tracking: https://github.com/tomcounsell/ai/issues/1710
last_comment_id: 4715280523
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

**New flow:** at step 3, before bailing, the loop captures the last PM/Dev frame snapshot + plateau metadata onto `ContainerResult`; at step 4, the adapter folds the frame into the `exit_anomaly` event and the new AgentSession field; at step 5, the adapter fires a direct `valor-telegram` notification carrying a frame excerpt. Early-bail (step 3) fires on a confirmed plateau well before the 600s deadline.

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

- **Plateau detector (early bail):** Inside the startup loop, track a fingerprint of each no-progress cycle — `(pm_idle_bool, dev_idle_bool, response, hash(pm_buffer_tail + dev_buffer_tail))`. When the fingerprint is identical for **N consecutive cycles** (config constant, e.g. `STARTUP_PLATEAU_CYCLES`), the startup is confirmed stuck and bails immediately — independent of and well before the 600s wall-clock ceiling. The 600s ceiling remains as the slow-cold-start backstop; the plateau detector is an orthogonal *fast* path for the deterministic-never-started case.
- **Frame capture:** At bail time (plateau OR ceiling), snapshot the last PM and Dev buffers into a single diagnostic frame string and attach it to `ContainerResult`. The frame is the diagnosis — it would have read `Unknown command: /granite:prime-pm-role` immediately.
- **Loud emission:** Extend `_maybe_publish_exit_anomaly` so the `exit_anomaly` session_event for `startup_unresolved` carries the frame (truncated), and fire a direct `valor-telegram` notification to the operator chat with a frame excerpt + session id. Reuses the existing ERROR/Sentry path; adds the human-reachable channel the broken session can't provide for itself.
- **Persistence:** Store the (truncated) frame on a new nullable AgentSession field so the dashboard and future #1538 recorder can read it without re-deriving.

### Flow

Startup loop running → N identical no-progress cycles detected → **plateau confirmed** → capture PM+Dev frame → set `exit_reason=startup_unresolved` + `startup_failure_kind=plateau` + frame → return early (seconds, not 600s) → BridgeAdapter persists frame to AgentSession + appends frame to `exit_anomaly` event + logs ERROR (Sentry) + fires `valor-telegram` alert → operator sees the exact stuck frame in one message.

(The 600s-ceiling path uses the same capture/emit tail with `startup_failure_kind=ceiling`.)

### Technical Approach

- **Plateau constants** in `container.py` alongside the existing startup constants: `STARTUP_PLATEAU_CYCLES` (consecutive-identical threshold) — set so the plateau bail fires comfortably after transient cold-start jitter but far short of 600s. Document the relationship to `STARTUP_CYCLE_TIMEOUT_S` (each cycle is ~3s of poll, so N cycles ≈ N×3s of confirmed silence).
- **Fingerprint** uses a bounded tail of each buffer (last K bytes) hashed, so growing-but-stuck buffers (e.g. a spinner re-painting) don't defeat the detector while genuine progress (new content) resets the counter. Reset the consecutive-count whenever the fingerprint changes.
- **Frame content:** stripped/printable text of the PM and Dev buffer tails, length-capped (e.g. last few KB each) to keep the session record small. Store both the raw-ish stripped text (human-readable, names the error) — not raw control bytes — to avoid bloat and ANSI noise. Cap total persisted frame size.
- **Capture helper:** a small pure function `_capture_startup_frame(pm_buf, dev_buf, kind, cycles) -> dict` so it is unit-testable without a live PTY. `ContainerResult` carries the result.
- **Notification:** add a fail-silent `_send_startup_alert(...)` in `bridge_adapter.py` modeled on `reflections/sdlc_progress.py::_send_alert` (subprocess `valor-telegram send --chat "Eng: Valor"`, swallow `FileNotFoundError`/timeout/CalledProcessError with a `logger.warning`). Fired only for `startup_unresolved` (not other anomalies) to avoid alert fatigue.
- **#1539 coordination:** the diagnostic carries `startup_failure_kind`; a `plateau`/deterministic-never-started kind is the signal #1539's future auto-resume should treat as "do NOT auto-resume, alert a human instead." #1710 only *records and alerts*; it adds no resume logic. The field is the coordination surface so the alert fires before any future resume attempt.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_send_startup_alert` swallows `FileNotFoundError` (CLI absent), `subprocess.TimeoutExpired`, and `subprocess.CalledProcessError` — each branch has a test asserting `logger.warning` is emitted and the container result is unaffected (notification never crashes the run).
- [ ] Frame capture (`_capture_startup_frame`) must never raise on empty/None buffers — a test asserts it returns a well-formed dict for `("", "")` and for None inputs.
- [ ] Confirm no new bare `except Exception: pass` is introduced; the existing anomaly path's gating is preserved.

### Empty/Invalid Input Handling
- [ ] `_capture_startup_frame` with empty PM and Dev buffers returns a frame string indicating "no buffer content" rather than an empty/misleading artifact.
- [ ] Plateau detector with zero cycles run (immediate idle) does not false-positive a plateau.
- [ ] Whitespace-only / control-byte-only buffers are stripped to a readable (possibly empty-flagged) frame.

### Error State Rendering
- [ ] Test that on `startup_unresolved` the `exit_anomaly` session_event carries the frame field (non-empty) and the ERROR log fires.
- [ ] Test that the `valor-telegram` alert subprocess is invoked with a message containing the session id and a frame excerpt (assert on the mocked subprocess args).
- [ ] Verify the frame is truncated below the size cap so it does not bloat the AgentSession record.

## Test Impact

- [ ] `tests/unit/granite_container/test_container.py::test_never_idle_exits_startup_unresolved_at_ceiling` — UPDATE: still asserts ceiling exit, but also assert the captured frame + `startup_failure_kind=ceiling` are now populated on the result.
- [ ] `tests/unit/granite_container/test_container.py` — ADD: `test_plateau_bails_early_before_ceiling` (N identical cycles → early `startup_unresolved` with `startup_failure_kind=plateau`, well under the deadline), `test_progress_resets_plateau_counter` (changing frame resets the count), `test_capture_startup_frame_*` (empty/None/whitespace inputs).
- [ ] `tests/unit/granite_container/test_bridge_adapter.py::test_startup_unresolved_writes_exit_anomaly_event` — UPDATE: assert the `exit_anomaly` event now carries the frame field; ADD a sibling test asserting the `valor-telegram` alert subprocess is invoked (mocked) and that a missing CLI is swallowed.
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
**Impact:** A flapping startup or a batch of failing sessions floods the operator chat.
**Mitigation:** Fire the direct notification only for `startup_unresolved` (not all anomalies). Notification is fail-silent and best-effort. Keep the message single and compact (session id + short frame excerpt). If storms become real, a dedupe/rate-limit is a follow-up — out of scope here.

### Risk 3: Frame leaks sensitive content or bloats the session record
**Impact:** A captured frame could contain large/raw output or sensitive user text persisted on the AgentSession.
**Mitigation:** Strip to printable text, length-cap each buffer tail and the total persisted frame. The frame is a startup-phase TUI snapshot (welcome/error frames), not user message bodies. Cap enforced with a test.

## Race Conditions

The startup loop is single-threaded (`container.py:21-24` documents the loop is single-threaded; reads from both PTYs are not interleaved within a tick). Plateau detection and frame capture run entirely within that single-threaded loop on locally-owned buffers.

The only cross-thread surface is the notification: `_send_startup_alert` runs in the same worker thread that already calls `_maybe_publish_exit_anomaly` (post-run, after the container loop completes). No shared mutable state is read concurrently.

No race conditions identified — frame capture and plateau detection are synchronous and single-threaded; notification is post-run and fail-silent.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1539] Auto-resume / recovery policy from the captured signature. #1710 records `startup_failure_kind` as the coordination surface; the resume decision is #1539's.
- [SEPARATE-SLUG #1538] The durable structured session-telemetry event recorder / advisory classifier. #1710 emits the frame onto the existing `exit_anomaly` event + an AgentSession field; the recorder that ingests it for offline learning is #1538.
- [SEPARATE-SLUG #1538] Notification dedupe / rate-limiting beyond "only alert on startup_unresolved." If alert storms prove real, they fold into the telemetry/observability work, not this plan.

## Update System

No update system changes required — this feature is purely internal to the granite container and bridge adapter. No new dependencies (`valor-telegram` already installed on every machine), no new config files, no migration steps. The new nullable AgentSession field needs no migration: Popoto's `_heal_descriptor_pollution` walks fields generically (per issues #1099/#1172), so existing records read the new field as None.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. The granite container is invoked by the worker, not by the agent's tools. The `valor-telegram` notification is an *outbound* operator alert (the system telling a human), not a new capability the agent invokes. No MCP server, no `.mcp.json` change, no new CLI entry point. The bridge already imports and runs the container via `BridgeAdapter`; the changes live inside that existing path.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document the plateau detector (`STARTUP_PLATEAU_CYCLES`, fingerprint/reset behavior), the captured frame artifact (what it contains, size cap, where it lands on AgentSession + `exit_anomaly` event), and the direct `valor-telegram` startup-failure alert. Update the startup/failure-behavior section and the observability event table to note the frame-carrying `exit_anomaly` event.
- [ ] No new index entry needed (granite-pty-production.md already in `docs/features/`).

### Inline Documentation
- [ ] Docstring/comment on `_capture_startup_frame` and the plateau constants explaining the fingerprint-reset semantics and why the plateau path is orthogonal to (not a replacement for) the 600s ceiling.
- [ ] Comment on `_send_startup_alert` noting it is fail-silent and fires only for `startup_unresolved`.

## Success Criteria

- [ ] A startup that produces N consecutive identical no-progress cycles bails with `exit_reason=startup_unresolved` and `startup_failure_kind=plateau` in seconds, not 600s (asserted by `test_plateau_bails_early_before_ceiling`).
- [ ] A slow-but-progressing cold start does NOT trip the plateau detector (asserted by a late-settle test).
- [ ] On `startup_unresolved`, the captured PM+Dev frame is persisted on the AgentSession and folded into the `exit_anomaly` session_event (non-empty, size-capped).
- [ ] On `startup_unresolved`, a direct `valor-telegram` notification fires carrying the session id and a frame excerpt; a missing/failing CLI is swallowed without crashing the run.
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
- In the startup loop (`container.py:837-875`): maintain a fingerprint of each no-progress cycle (idle bools + response + hashed buffer tails); count consecutive identical fingerprints; reset on any change. On reaching `STARTUP_PLATEAU_CYCLES`, capture the frame (`kind=plateau`) and bail early. On ceiling exit, capture the frame (`kind=ceiling`). Both set the new result fields.

### 2. Adapter: persistence + enrichment + notification
- **Task ID**: build-adapter
- **Depends On**: build-container
- **Validates**: tests/unit/granite_container/test_bridge_adapter.py
- **Assigned To**: adapter-builder
- **Agent Type**: builder
- **Parallel**: false
- Add one nullable AgentSession field for the captured frame (additive, follows `models/agent_session.py:300-322` pattern).
- In `_publish_exit_summary`: persist the frame field from `ContainerResult` (truncated to the cap).
- In `_maybe_publish_exit_anomaly`: fold the (truncated) frame into the `exit_anomaly` session_event dict; add fail-silent `_send_startup_alert(...)` (modeled on `reflections/sdlc_progress.py:206-221`) fired only for `startup_unresolved`, carrying session id + frame excerpt.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-container, build-adapter
- **Assigned To**: startup-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_plateau_bails_early_before_ceiling`, `test_progress_resets_plateau_counter`, `test_capture_startup_frame_*` (empty/None/whitespace), and update `test_never_idle_exits_startup_unresolved_at_ceiling` to assert the new fields.
- Add/update `test_bridge_adapter.py`: `exit_anomaly` event carries the frame; `valor-telegram` alert invoked (mocked subprocess); missing CLI swallowed with `logger.warning`.

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
| Doc updated | `grep -rn "plateau" docs/features/granite-pty-production.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Plateau threshold value:** What N (consecutive identical no-progress cycles, ~3s each) is the right fast-bail point? Proposed default is small enough to save most of the 600s (e.g. a low-tens-of-seconds confirmation window) but large enough to clear transient cold-start jitter. The exact number is a tuning call — is a conservative default acceptable, to be tightened after observing real failures?
2. **Alert channel:** Is `"Eng: Valor"` the correct operator chat for an infra-level granite startup-failure alert, matching the `reflections/sdlc_progress.py` precedent? Or should startup failures go to a different/dedicated alert chat?
3. **Frame persistence scope:** Is persisting the stripped frame on the AgentSession (in addition to the `exit_anomaly` event) acceptable, or should the durable persisted copy wait for #1538's recorder and #1710 only emit the event + notification? (Current plan persists a size-capped copy so the dashboard can show it immediately.)
4. **#1539 coordination:** Confirm that recording `startup_failure_kind` (e.g. `plateau` = deterministic-never-started) as a no-resume signal is the right lightweight coordination surface, and that #1710 should add no resume-gating logic itself.
