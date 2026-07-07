---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1935
last_comment_id:
revision_applied: true
---

# Headless runner zombie wedge: toolless-but-streaming turns misclassified as no-output

## Critique Revision (2026-07-07)

### Second revision — CRITIQUE pass 2 (1 NEW BLOCKER + 3 concerns + 1 nit)

- **BLOCKER — Risk 1 mitigation was factually false; post-`init`-then-hang subprocess.** Broadening
  `sdk_ever_output` to include `last_stdout_at` presence means a subprocess that streams `init` once
  and then hangs derives `sdk_ever_output=True` and escapes the never-started gate. The plan claimed
  "idle-gap / turn-deadline detectors key on `last_stdout_at`/`last_activity` freshness and DO fire" —
  **verified FALSE**: `grep -n "last_stdout_at\|last_activity" agent/session_health.py` returns ZERO
  hits. Resolution: adopted **option (a)** — the real backstop for a post-`init` hang is the
  **whole-turn deadline** (runner preempt watcher `runner.py:764` firing `_kill_turn(cause="timeout")`,
  with the driver's `asyncio.wait_for(..., timeout=turn_timeout_s)` at `role_driver.py:404` as the
  backstop). Risk 1, the Success Criterion, and Step 5 rewritten to cite that mechanism honestly,
  including the **detection-latency tradeoff** (a genuine post-`init` hang is now caught at the
  whole-turn deadline — `ENG_TURN_TIMEOUT_S`=7200s / `TEAMMATE`=900s — not at the ~150s never-started
  gate). The finer idle-gap-on-`last_stdout_at`-freshness detector remains the deferred follow-up
  (Open Question 1). Option (b) (build the idle-gap detector now) was rejected as a scope expansion
  with no correctness justification: the whole-turn deadline already recovers the hung turn.
- **Concern — Element 1 hard constraint.** The composing 1-arg `on_init` adapter is now **mandatory**;
  the inline-inside-`_on_harness_init` alternative is **forbidden** because `_on_harness_init`'s
  early-return (`runner.py:540`, `if not sid: return`) and its wrapping try/except (`:538`/`:560`)
  would skip the stamp on a `session_id`-less or persist-failing init event.
- **Concern — Element 3 reframed as co-equal regression fix.** Verified `record_turn_boundary` has a
  single caller (`sdk_client.py:2936`, worker-side result handler) and reads `AGENT_SESSION_ID`, which
  is set ONLY in the subprocess env overlay (`session_executor.py:1783`, value = `agent_session_id`).
  In the worker process it is unset → `last_turn_at` is **~100% dead today**, and even where set the
  `agt_xxx` value can never match `filter(session_id=...)`. Element 3 + Appetite relabeled from
  "defense-in-depth" to a co-equal regression fix.
- **Concern — Element 2 scope guard corrected.** The guard omitted site 2 (`_has_progress` sub-check
  B). Verified `_has_progress` feeds `should_recover` (`session_health.py:3136`) — a recovery-path
  consumer, not a mid-turn cadence detector. Guard reworded to enumerate all four recovery-path sites.
- **Nit — line citation.** `runner.py:431` verified CORRECT at HEAD (`session_id = str(getattr(...) or
  "")`); the critique's "~426" reading was stale. Citation anchored to `_build_driver` and the quoted
  code corrected to include the `or ""` guard.

### First revision — CRITIQUE pass 1 (1 BLOCKER + 4 concerns)

- **BLOCKER — fourth derivation site.** `sdk_ever_output` is derived at FOUR sites, not three; the
  missed one is `_tier2_reprieve_signal` (`session_health.py:1310`), a *second* wedge route via the
  reprieve cap. Element 2 now converts all four through a module-level `_derive_sdk_ever_output`
  helper, gated by two zero-hit greps. Root cause, Data Flow (new step 7), Success Criteria,
  Test Impact, and Step 2 updated.
- **Concern — callback arity.** `on_stdout_event` (0-arg) and `on_init` (1-arg) cannot share one
  closure; Element 1 now specifies two adapters, and the `on_init` adapter composes with (never
  replaces) `_on_harness_init`'s `claude_session_uuid` persistence.
- **Concern — `record_turn_boundary` id.** Element 3 now plumbs the true `AgentSession.session_id`
  from `runner.py:431`, explicitly rejecting the env `agent_session_id` (agt_xxx) and the Claude UUID
  (`data.get("session_id")`) as filter keys.
- **Concern — driver-seam test.** Added a deterministic fake-harness test proving the real stream
  fires `on_stdout_event` during a toolless window (Failure Path Test Strategy + Step 1c).
- **Concern — Test Impact site-4.** Added `test_session_health_compacting_reprieve.py`
  (`_tier2_reprieve_signal` / `reprieve_count` / `MAX_NO_OUTPUT_REPRIEVES`) and the driver-seam file.

## Problem

Since the granite PTY teardown (PR #1930, commit `e8351e4c`, merged 2026-07-07) cut every
session role over to the headless `claude -p` runner, `/do-sdlc` sessions can wedge: the worker
spawns the subprocess, the stream-json `init` event lands and a `claude_session_uuid` is
persisted, but no progress signal that session-health recognizes is written for the full ~150s
never-started grace. The health machinery classifies this as `zombie_uuid_no_output`
(`kind=no_progress`), recovers/retries once, hits the identical condition, and finalizes the
session `failed` after 2 recovery attempts. Two co-occurring instances (`sdlc-local-1933`,
`sdlc-local-1934`) wedged this way in the first batch after the cutover, both stalled at the
PLAN/CRITIQUE transition.

**Root cause.** `sdk_ever_output` — the flag whose being-False drives the zombie verdict — is
derived as `bool(last_tool_use_at or last_turn_at)` at **four** independent sites in
`agent/session_health.py` (verified at HEAD): `:985-987` (`_never_started_past_grace`),
`:1127-1129` (`_has_progress`), `:1310-1312` (`_tier2_reprieve_signal`), and `:2057-2059`
(recovery-classification / `zombie_uuid_no_output` counter). On a headless turn those two fields
are written only by tool-boundary hooks (on a tool call) and by end-of-turn `record_turn_boundary`.
A turn that streams the `init` event and then produces assistant output *without calling a tool
within 150s* (e.g. PM prime resolution + reasoning before its first tool) therefore has **no
recognized progress signal even though the subprocess is demonstrably alive and streaming** — the
persisted `claude_session_uuid` is itself proof the SDK produced output. The PTY→headless cutover
dropped the per-stream-activity liveness write that previously covered this case:
`SessionRunner._build_driver` (`agent/session_runner/runner.py:423-446`) never wires
`on_stdout_event`, so `last_stdout_at` is never refreshed during a headless turn — and
`last_stdout_at` was never part of the `sdk_ever_output` derivation to begin with.

**Two wedge routes, not one.** The never-started gate (`_never_started_past_grace`, site 1) is the
first-observed route. But the reprieve path is a *second* independent route to the same failure: a
toolless-streaming session that survives the grace window still computes `sdk_ever_output=False` at
site 3 (`_tier2_reprieve_signal`), and once `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` (= 20) its
Tier-2 reprieves are suppressed and it is escalated to recovery — the identical wedge, reached via
the reprieve-cap guard instead of the never-started gate. **All four sites must convert together**;
converting only sites 1/2/4 leaves the reprieve route open (critique BLOCKER).

## Freshness Check

**Baseline commit:** `8485db99` (`git rev-parse HEAD` at plan time).
**Issue filed at:** 2026-07-07T06:14:28Z. **Cutover merged:** 2026-07-07T04:54:35Z (`e8351e4c`).
**Disposition:** **Unchanged.**

- All cited file:line references (`session_health.py:985/1127/1310/2057`, `runner.py:423-446`,
  `role_driver.py:175/194/400`, `adapter.py:362-382`, `session_executor.py:1506-1524/1783`,
  `sdk_client.py:2936`, `liveness_writers.py:136`, `session_stall_classifier.py:53/60`) were read
  at HEAD `8485db99` and match the issue's description. No commits have touched
  `agent/session_runner/` since the cutover merged (`git log --oneline --since=e8351e4c -- agent/session_runner/`
  is empty).
- Cited sibling issues re-checked: #1843 CLOSED (granite Gap A/Gap B fix), #1792/#1724/#1356/#1614/#1905
  all CLOSED. #1843's Gap B substrate (`agent/granite_container/` PTY driver) was deleted by the
  cutover — its mid-turn liveness refresh has no headless equivalent, which is the carried-over gap.
- `docs/plans/` overlap check: no active plan touches `agent/session_runner/` or the
  `sdk_ever_output` derivation. The `granite-*` plans are all in `docs/plans/completed/`.
- Bug still present: the code path is unchanged and the reproduction (toolless streaming turn past
  grace → zombie) is deterministic from the derivation, not environmental.

## Prior Art

- **#1843 / `granite-wire-silent-wedge-signals` (CLOSED).** Fixed this exact "silent no-progress"
  class for the PTY runner. **Gap A** wired CLI-hook liveness writes so tool boundaries populate
  `current_tool_name`/`last_tool_use_at` via sidecar resolution
  (`.claude/hooks/pre_tool_use.py:24-102`, `.claude/hooks/post_tool_use.py:455-515`) — this SURVIVES
  the cutover (CLI hooks merge additively with the per-session `--settings` file). **Gap B** made
  PTY reads refresh `last_pty_read_loop_at` mid-turn — this DIED with the PTY substrate and has no
  headless replacement. The current bug is the headless-shaped reincarnation of Gap B.
- **#1724 / PR #1728.** Built `last_pty_read_loop_at` / `last_pty_activity_at` freshness fields and
  the `granite_wedged` verdict — the PTY-era per-stream-activity liveness this plan re-establishes
  for the headless runner via `last_stdout_at`.
- **#1270 (CLOSED).** Per-tool timeout tiers requiring non-null `current_tool_name`. Confirms
  `last_tool_use_at` is a tool-boundary-only signal and cannot cover a toolless turn.
- **#1688 / PR #1847.** Introduced the per-session `--settings` + `hook_forwarder.py` architecture
  and established that generated-settings hooks merge *additively* with repo `.claude/settings.json`.
- **#1905 (CLOSED).** D0 never-started gate makes `no_output_budget_exceeded` unreachable — context
  for how the never-started gate short-circuits before other stall detectors.

## Research

No relevant external findings needed — this is an internal regression in the session-runner
liveness plumbing. The one external fact this plan depends on (Claude Code hooks from `--settings`
merge additively with project `.claude/settings.json` rather than replacing them) was already
researched and recorded in #1843's Research section against the Claude Code hooks/settings docs, and
is corroborated here by worker-log evidence that the per-session settings file coexists with the
repo hooks. Proceeding with codebase context.

## Data Flow

Headless turn, from spawn to zombie verdict:

1. Worker picks up the session, allocates a synthetic slug + worktree (`session_executor.py`
   synthetic-slug path), injects `AGENT_SESSION_ID` into the **subprocess** env overlay
   (`_harness_env`, `session_executor.py:1783`), and constructs `SessionRunner`.
2. `SessionRunner._build_driver` (`runner.py:423-446`) builds `HeadlessRoleDriver` with `on_spawn`
   and `on_init` **but not `on_stdout_event`**.
3. `claude -p` spawns and streams the `init` event → `role_driver._handle_init` → `runner._on_harness_init`
   → `adapter.persist_resume_scalars` writes `claude_session_uuid` (`adapter.py:362-382`). **t ≈ few seconds.**
4. The subprocess produces assistant output (prime resolution, reasoning). No tool call yet → no
   PreToolUse/PostToolUse hook → `last_tool_use_at` unwritten. `on_stdout_event` is None → `last_stdout_at`
   unwritten. `record_turn_boundary` only fires at end-of-turn (`sdk_client.py:2936`) and even then
   reads the worker's unset `AGENT_SESSION_ID` (`liveness_writers.py:136`).
5. The 60s heartbeat keeps `last_heartbeat_at` fresh but that is not a progress signal.
6. At running-seconds > 150s (`NEVER_STARTED_GRACE_SECS`+`CONFIRM_MARGIN`), `_never_started_past_grace`
   returns True (`session_health.py:985-987`, `sdk_ever_output=False`), `_has_progress` denies the
   heartbeat fast-path, the zombie branch fires (`:2057-2066`), and the session is recovered → retried
   → identical wedge → `failed`.
7. **Reprieve route (second path to the same wedge).** Even for a session that clears the grace
   window, `_tier2_reprieve_signal` (`:1310-1312`, `sdk_ever_output=False`) suppresses all Tier-2
   reprieves once `reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` (`:1314-1317`), escalating a
   still-streaming session to recovery. Because this site derives `sdk_ever_output` from the same
   two-field expression, it must be converted in lockstep with the other three.

The fix inserts a progress write at step 4 (stream activity → `last_stdout_at`) and makes both the
step-6 never-started derivation and the step-7 reprieve derivation recognize it.

## Why Previous Fixes Failed

The relevant prior fix (#1843) did not fail — it was **substrate-specific and only partially
portable**. Gap A (tool-boundary liveness via CLI hooks) was implemented in the repo `.claude/hooks/`
layer that both PTY and headless children share, so it carried over. Gap B (per-stream-read liveness)
was implemented inside `agent/granite_container/`'s PTY driver, which the cutover deleted wholesale —
so the signal that covered *toolless streaming* turns silently vanished. No code "regressed" in the
sense of a broken edit; the cutover removed a whole liveness source without re-providing an
equivalent on the new transport. This plan re-provides it via `last_stdout_at` on the headless
stream.

## Architectural Impact

Touches the session-runner liveness seam and the session-health never-started/zombie derivation.
No schema changes (both `last_stdout_at` and the tool/turn fields already exist on `AgentSession`).
The change is additive: it introduces a third, transport-native progress signal
(`last_stdout_at`, fed by the headless stream) into an OR-derivation that already tolerates any one
signal being present. No behavior change for sessions that already emit tool/turn boundaries.

## Appetite

**Medium.** Three focused code edits (wire `on_stdout_event` in the runner; extend the
`sdk_ever_output` derivation across **all four** sites via one helper; fix the
`record_turn_boundary` id resolution — a co-equal regression fix, since `last_turn_at` is a dead
writer today) plus deterministic unit reproductions, a
driver-seam test, and a docs update. The session-health derivation change is small but
load-bearing and spans four sites (one of them a second wedge route via the reprieve cap), so it
warrants careful red-first testing rather than a Small-appetite drive-by.

## Prerequisites

None. `last_stdout_at`, `last_tool_use_at`, `last_turn_at` all already exist on `AgentSession`
(`models/agent_session.py`). `HeadlessRoleDriver` already accepts and threads `on_stdout_event`
(`role_driver.py:175,194,400`) — only the runner's wiring is missing.

## Solution

### Key Elements

1. **Wire `on_stdout_event` in the headless runner — two distinct adapters.** In
   `SessionRunner._build_driver` (`runner.py:423-446`), the driver's two observer slots have
   **different arities**: `on_stdout_event: Callable[[], None]` (0-arg, `role_driver.py:175`) and
   `on_init: Callable[[dict], None]` (1-arg, `role_driver.py:178`). A single closure cannot serve
   both. Provide two adapters over one shared `_stamp_stdout_liveness()` helper:
   - `on_stdout_event` → a **0-arg** adapter that calls `_stamp_stdout_liveness()`.
   - `on_init` must **compose** with (not replace) the existing `self._on_harness_init` — that
     callback persists `claude_session_uuid` + `runner_cwd` + `claude_version` via
     `persist_resume_scalars` (`runner.py:529-561`) and MUST keep doing so. **HARD CONSTRAINT:** use a
     separate **1-arg** composing adapter —
     `def _on_init(data): self._on_harness_init(data); self._stamp_stdout_liveness()` — passed as the
     driver's `on_init`. **The inline-inside-`_on_harness_init` alternative is FORBIDDEN.** Reason
     (verified): `_on_harness_init` early-returns at `runner.py:540` (`sid = data.get("session_id");
     if not sid: return`) and wraps its whole body in a try/except (`:538` try, `:560` except), so a
     stamp placed inside it would be **skipped** on any `session_id`-less init event or if
     `persist_resume_scalars` raises. The composing adapter stamps *after* `_on_harness_init` returns,
     unconditionally — the `init` event is the first proof of output and must count immediately,
     before any assistant token, and independently of whether the resume-scalar persistence
     succeeded. `_on_harness_init` itself is left byte-for-byte unchanged (it keeps owning
     resume-scalar persistence).

   `_stamp_stdout_liveness()` stamps `last_stdout_at = datetime.now(tz=UTC)` on the AgentSession
   (mirroring `session_executor.py:1506-1509`, same fail-silent + 5s-cooldown discipline). This
   restores the per-stream-activity liveness signal the PTY teardown dropped.

2. **Recognize stream activity in the derivation — all FOUR sites.** Introduce a single
   module-level helper `_derive_sdk_ever_output(entry) -> bool` returning
   `bool(last_tool_use_at or last_turn_at or last_stdout_at)`, and replace the inline
   two-field expression at **all four** sites: `session_health.py:985-987`
   (`_never_started_past_grace`), `:1127-1129` (`_has_progress`), `:1310-1312`
   (`_tier2_reprieve_signal`), and `:2057-2059` (recovery classification). The helper must be
   defined at module scope (before `_never_started_past_grace`, since site 3 in
   `_tier2_reprieve_signal` also calls `_never_started_past_grace`). Semantically correct:
   `sdk_ever_output` means "has the SDK ever produced output," and the `init`/stdout stream IS
   output. **Post-edit assertion (critique BLOCKER gate):** `grep -n "sdk_ever_output = bool("
   agent/session_health.py` and `grep -n "_sdk_ever_output = bool(" agent/session_health.py` must
   BOTH return zero hits — every inline derivation is replaced by the helper. **Scope guard:** this
   broadens the "no output ever" input at all FOUR recovery-path sites and nowhere else — site 1
   `_never_started_past_grace` (never-started gate), site 2 `_has_progress` sub-check B (verified a
   recovery-path consumer: its result gates `should_recover` at `session_health.py:3136`, not any
   live turn), site 3 `_tier2_reprieve_signal` (reprieve-cap guard), site 4 the
   `zombie_uuid_no_output` recovery classifier (`:2057`). None of the four is a mid-turn cadence
   detector. Detectors that legitimately need a tool/turn cadence (per-tool timeout tiers, and
   `_has_progress` **sub-check A**'s freshness comparison — which is untouched) are NOT loosened; only
   the presence-based "has the SDK EVER produced output" input is broadened — see No-Gos.

3. **Fix the `record_turn_boundary` id resolution (co-equal regression fix, NOT defense-in-depth) —
   plumb the true `AgentSession.session_id`.** `record_turn_boundary`
   (`agent/hooks/liveness_writers.py:129`, called once from `sdk_client.py:2936`) reads
   `os.environ.get("AGENT_SESSION_ID")` and filters `AgentSession.query.filter(session_id=...)`.
   **Verified: `last_turn_at` is ~100% dead today.** Two stacked reasons:
   (i) `record_turn_boundary` has a *single* caller — `sdk_client.py:2936`, the harness `result`-event
   handler, which runs in the **worker** process; `AGENT_SESSION_ID` is injected only into the
   *subprocess* env overlay (`session_executor.py:1783`), so in the worker it is unset →
   `record_turn_boundary` returns False before any write. (ii) Even where the env var *is* set (the
   in-subprocess CLI-hook path / child-spawn overlay), its value is `agent_session_id` (`agt_xxx`,
   `session_executor.py:1783: "AGENT_SESSION_ID": session.agent_session_id`), which can **never** match
   `filter(session_id=...)` — `session_id` is the distinct Telegram-derived key
   (`models/agent_session.py:141`). The harness result event's `data.get("session_id")` is the
   **Claude UUID** — also wrong for the filter. The ONLY correct value for `filter(session_id=...)` is
   the true `AgentSession.session_id`, which the runner already has in hand in `_build_driver`
   (`runner.py:431`: `session_id = str(getattr(self._agent_session, "session_id", "") or "")` — the
   same value it passes to `HeadlessRoleDriver(session_id=...)`). **Plumbing path:** add an optional
   `session_id: str | None = None` param to `record_turn_boundary`; thread the runner's true
   `AgentSession.session_id` through the harness stream call (`sdk_client.py` stream fn) down to the
   `result`-event handler at `:2936`, which passes it explicitly. When `session_id` is None, fall
   back to `os.environ` (preserves the in-subprocess CLI-hook call sites unchanged). Because
   `last_turn_at` is one of the three OR-signals feeding `sdk_ever_output` and is currently a dead
   writer, restoring it is a genuine regression fix — not merely belt-and-suspenders. It does not by
   itself close the toolless-streaming in-grace wedge (Elements 1+2 do); the two fixes are
   complementary.

### Flow

Post-fix: init event → `last_stdout_at` stamped (t≈few seconds) → `sdk_ever_output` derives True →
`_never_started_past_grace` returns False → no zombie verdict. Subsequent stdout activity keeps
`last_stdout_at` fresh. Tool calls and end-of-turn continue to write their own fields as before.

### Technical Approach

- Add a `_stamp_stdout_liveness()` helper in `SessionRunner` (alongside the existing turn-spawn/init
  observers) that resolves the AgentSession by the true `session_id` and saves `last_stdout_at` with
  `update_fields=["last_stdout_at"]`, fail-silent, with a short in-memory cooldown to bound Redis
  write rate (mirror `liveness_writers.COOLDOWN_WINDOW_SEC = 5.0`). Expose it through **two** driver
  adapters (Element 1): a 0-arg `on_stdout_event` adapter, and a 1-arg `on_init` adapter that first
  delegates to `_on_harness_init` (preserving `persist_resume_scalars`) and then stamps.
- Introduce `agent/session_health.py::_derive_sdk_ever_output(entry) -> bool` at module scope and
  replace the **four** inline `bool(last_tool_use_at or last_turn_at)` / `_sdk_ever_output = bool(...)`
  expressions (`:985`, `:1127`, `:1310`, `:2057`) with a call to it, adding `last_stdout_at`. Keep
  the docstrings' "either per-turn field" language updated to "any stream or turn signal." Confirm
  with the two zero-hit greps in Element 2.
- `record_turn_boundary(session_id: str | None = None)`: if `session_id` is None fall back to
  `os.environ` (preserves the in-subprocess CLI-hook call sites); the harness worker-side call site
  in `sdk_client.py:2936` passes the true `AgentSession.session_id` plumbed from the runner
  (`runner.py:431`), never the Claude UUID (`data.get("session_id")`) and never the env
  `agent_session_id`.

## Failure Path Test Strategy

### Exception Handling Coverage
- `last_stdout_at` stamp save failure (Redis down / no matching session): the closure must swallow
  the exception and log at debug — a liveness-write failure must never crash or wedge the turn.
  Test by monkeypatching the save to raise and asserting the turn proceeds.

### Empty/Invalid Input Handling
- `on_stdout_event` firing before `claude_session_uuid` is persisted: `last_stdout_at` resolves the
  AgentSession by `session_id` (the stable AgentSession key), not the claude uuid, so ordering vs.
  uuid persistence is irrelevant. Test: stamp with only `session_id` present.
- `record_turn_boundary(session_id=None)` with unset env: returns False, no write (unchanged
  behavior). Test preserves the existing no-op contract.

### Error State Rendering
- Not applicable — no user-facing surface. The observable is the session NOT transitioning
  `running→failed` with `context="never progressed (kind=no_progress)"` when it is actually streaming.

### Driver-Seam Coverage (Concern 4)
- **Deterministic proof that the real `claude -p` stream fires `on_stdout_event` during a toolless
  window.** Element 1's unit test can pass by stamping `last_stdout_at` directly, without ever
  proving the driver actually invokes `on_stdout_event` on stdout lines. Close that gap with a
  driver-seam test in `tests/unit/session_runner/test_headless_role_driver.py` (the existing fake-
  harness seam — see `test_claude_session_id_capture` / `_make_harness`): construct a
  `HeadlessRoleDriver` with a fake `harness_fn` that emits an `init` event followed by assistant
  stdout lines and **no tool-call event** (a toolless window), pass a counting `on_stdout_event`
  callback, run one turn, and assert the counter incremented (≥1) — i.e. the stdout stream drives
  the liveness signal even when no tool boundary ever fires. This is deterministic (no real
  subprocess; the harness fake feeds stdout lines through the same `sdk_client.py:2900-2902`
  `on_stdout_event` dispatch the real stream uses).

## Test Impact

- [ ] `tests/unit/test_never_started_recovery.py` (never-started + `sdk_ever_output` +
      `zombie_uuid_no_output`) — UPDATE: add a case where `last_stdout_at` is fresh and assert
      `sdk_ever_output` derives True / no zombie verdict; verify existing cases that set
      `last_tool_use_at`/`last_turn_at` still pass through the new helper unchanged.
- [ ] `tests/unit/test_health_check_recovery_finalization.py` and
      `tests/unit/test_session_health_inference_removed.py` (both reference `sdk_ever_output` /
      recovery finalization) — UPDATE: point derivation assertions at `_derive_sdk_ever_output` and
      the 3-field OR; keep recovery-finalization semantics.
- [ ] **`tests/unit/test_session_health_compacting_reprieve.py`** (Concern 5 — site-4 reprieve cap:
      references `_tier2_reprieve_signal`, `reprieve_count`, `MAX_NO_OUTPUT_REPRIEVES`) — UPDATE: add
      a case where a session past the reprieve cap but with fresh `last_stdout_at` is NOT suppressed
      (reprieve still granted), and confirm the existing "no output ever → suppress at cap" cases
      still hold when all three fields are unset.
- [ ] `tests/unit/test_session_health_trusted_clock.py` (references `sdk_ever_output`) — UPDATE:
      re-point any inline-derivation assertions at the helper.
- [ ] `tests/unit/session_runner/test_runner_liveness.py` — UPDATE: assert `on_stdout_event` is now
      wired and stamps `last_stdout_at`; assert the `on_init` adapter still persists resume scalars
      (`_on_harness_init` not shadowed).
- [ ] `tests/unit/session_runner/test_headless_role_driver.py` — UPDATE/ADD: the Concern-4
      driver-seam test (toolless window fires `on_stdout_event`); existing
      `test_claude_session_id_capture` must still pass (init still persists the uuid).
- [ ] `record_turn_boundary` unit tests (`agent/hooks/liveness_writers`) — UPDATE: add the
      explicit-`session_id` path; keep the env-fallback no-op case.
- [ ] `tests/unit/session_runner/headless_hook_probe.py` — no change expected (it exercises turn-end
      hook firing, not liveness); confirm it still passes.

No existing tests are DELETED or REPLACED — the changes are additive to the derivation (four sites,
one helper) and the runner wiring.

## Rabbit Holes

- **Do NOT** rework the whole session-health stall taxonomy or the `granite_wedged`/stall-advisory
  actuation ladder — this plan touches only the never-started/zombie derivation and one liveness
  write.
- **Do NOT** try to resurrect the deleted PTY liveness path or reason about `last_pty_read_loop_at` —
  that field is dead with the substrate; `last_stdout_at` is its headless replacement.
- **Do NOT** attempt to distinguish "streaming useful tokens" from "streaming a spinner" — the
  never-started gate only asks "did the SDK EVER produce output," and mid-turn hang detection is a
  separate concern owned by other detectors (out of scope here).

## Risks

### Risk 1: Masking a genuinely wedged subprocess that emits `init` then truly hangs
Counting `last_stdout_at` *presence* as progress means a subprocess that streamed `init` and then
hung with no further output is marked as "produced output" and escapes the never-started gate
permanently (the derivation is presence-based, not freshness-based).
**What actually catches it (verified).** There is **no** idle-gap / freshness detector on
`last_stdout_at` — `grep -n "last_stdout_at\|last_activity" agent/session_health.py` returns ZERO
hits, so the earlier claim that session-health idle-gap detectors "key on last_stdout_at freshness"
was false. The real backstop for a post-`init` hang is the **whole-turn deadline** enforced by the
runner, not by session-health:
- The runner's preempt watcher (`_run_preempt_watcher`, `runner.py:764`) checks
  `(loop.time() - started_at) >= self._turn_timeout_s` every poll and calls
  `_kill_turn(cause="timeout")` — this fires FIRST.
- The driver's `asyncio.wait_for(harness_fn(...), timeout=self.turn_timeout_s)` (`role_driver.py:404`)
  is the backstop; on expiry it sets `outcome.hung=True` / `exit_reason="headless_turn_timeout"`.
- `turn_timeout_s = turn_timeout_for(session_type)` (`runner.py:334/183`): `ENG_TURN_TIMEOUT_S`=7200s
  for PM/eng, `TEAMMATE_TURN_TIMEOUT_S`=900s (both env-overridable). The driver's own `wait_for` is
  set slightly higher (`_turn_timeout_s + _term_grace_s + DRIVER_BACKSTOP_MARGIN_S`, `runner.py:442`).

**Accepted tradeoff (honest).** Because the whole-turn deadline is a wall-clock timer measured from
turn start — NOT an idle-gap on stdout freshness — a genuine post-`init` hang is now caught at the
turn deadline (up to 7200s for a PM/eng turn) rather than at the ~150s never-started gate. This is a
detection-latency regression *for the rare init-then-immediate-hang case only*, and it is the correct
tradeoff: a subprocess that streamed `init` genuinely produced output, so the never-started gate
(whose sole question is "did the SDK EVER produce output") SHOULD NOT fire on it; the hung turn is
still recovered, just via the coarser existing backstop. Tightening this to a fast idle-gap detector
keyed on `last_stdout_at` *freshness* is the deferred follow-up (Open Question 1) — a scope expansion
with no correctness justification here, since the turn deadline already recovers the turn.
**Build verification:** add a deterministic test (small injected `turn_timeout_s`) asserting a
fake-harness turn that emits `init` then hangs is preempted/killed via the turn-deadline path
(`outcome.hung=True` / `exit_reason="headless_turn_timeout"`), NOT via the never-started gate.

### Risk 2: Redis write amplification from per-stdout-chunk stamping
Stamping `last_stdout_at` on every stdout event could hammer Redis on a chatty turn.
**Mitigation:** apply the same 5s cooldown the CLI-hook liveness writer uses; the stamp is a single
`update_fields=["last_stdout_at"]` save, coalesced to at most once per 5s per session.

## Race Conditions

### Race 1: `on_stdout_event` fires before the AgentSession row is queryable
The AgentSession record is created and saved by the worker before the subprocess is spawned, so the
row exists before any stdout event. The stamp resolves by `session_id` (stable key). If a resolve
miss ever occurs it fail-silently no-ops (no crash), and the next stdout event (or the init stamp)
retries. No ordering dependency on `claude_session_uuid` persistence.

## No-Gos (Out of Scope)

- Loosening mid-turn stall detectors (per-tool timeout tiers `#1270`, idle-gap). Only the
  `sdk_ever_output` "no output ever" input is broadened — at all four sites that derive it, including
  the `_tier2_reprieve_signal` reprieve-cap guard (site 4). The reprieve *cadence*, the recovery
  ladder, and the per-tool/idle-gap detectors are untouched. Justification: those detectors correctly
  require tool/turn cadence and must keep firing on real mid-turn hangs; broadening them would
  re-introduce the very silent-wedge blindness #1843 closed. Broadening `sdk_ever_output` is safe
  because a session that streamed output genuinely produced output — the exact question that flag
  asks.
- Changing the never-started grace duration or the recovery-attempt cap. The 150s grace is not the
  bug; the missing progress signal is.
- Reviving `agent/granite_container/` or any PTY liveness field.
- [EXTERNAL] A live `/do-sdlc` re-run of #1933/#1934 as the acceptance gate — requires the live
  worker/batch environment and is inherently non-deterministic, so it cannot serve as an in-plan
  automated gate. The deterministic unit repro is the acceptance gate; the live re-run is optional
  manual post-merge confirmation.

## Update System

No update-system changes required. This is a purely internal fix within `agent/session_runner/` and
`agent/session_health.py`; no new dependencies, config keys, migrations, or `scripts/update/` wiring.
No Popoto schema change (all fields already exist), so no `scripts/update/migrations.py` entry.

## Agent Integration

No agent integration required. No new CLI entry point, MCP server, or `.mcp.json` change. The fix is
invisible to the agent surface — it changes only how the worker's liveness plumbing feeds
session-health. The observable effect is that legitimate headless turns stop being killed; existing
tools (`valor-session status/telemetry`, dashboard) surface `last_stdout_at` unchanged.

## Documentation

- [ ] Update `docs/features/headless-session-runner.md` — add a "Liveness signals" subsection
      documenting that the headless runner stamps `last_stdout_at` on `init`/stdout events and that
      `sdk_ever_output` (the never-started/zombie gate AND the `_tier2_reprieve_signal` reprieve-cap
      guard) derives from `last_tool_use_at OR last_turn_at OR last_stdout_at` via the shared
      `_derive_sdk_ever_output` helper. Cross-reference #1843 Gap B as the PTY-era predecessor.
- [ ] Update `docs/features/session-lifecycle.md` (or the session-health reference it links) where
      `zombie_uuid_no_output` / never-started is described, to reflect the 3-signal derivation.

### Feature Documentation
Primary target: `docs/features/headless-session-runner.md`.

### External Documentation Site
Not applicable.

### Inline Documentation
Update the `sdk_ever_output` derivation docstrings in `session_health.py` (all four sites now route
through module-level `_derive_sdk_ever_output`, including `_tier2_reprieve_signal`) and the
`record_turn_boundary` docstring for the new `session_id` param.

## Success Criteria

- A headless session whose turn streams `init` and then produces stdout with NO tool call for >150s
  is NOT classified `zombie_uuid_no_output` and NOT transitioned `running→failed` with
  `kind=no_progress` — verified by a deterministic unit test over the session-health derivation.
- **A toolless-streaming session past the reprieve cap (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES`)
  but with fresh `last_stdout_at` is NOT suppressed by `_tier2_reprieve_signal` (site 4) — verified
  by a unit test; this closes the second wedge route (critique BLOCKER).**
- `sdk_ever_output` derives True when any of `last_tool_use_at`, `last_turn_at`, or `last_stdout_at`
  is set — verified by `_derive_sdk_ever_output` unit tests over all combinations.
- **All four derivation sites route through the helper: `grep -n "sdk_ever_output = bool("
  agent/session_health.py` and `grep -n "_sdk_ever_output = bool(" agent/session_health.py` both
  return zero hits.**
- `SessionRunner._build_driver` wires `on_stdout_event` (0-arg adapter) and an `on_init` adapter that
  still persists resume scalars; a runner unit test asserts `last_stdout_at` is stamped on
  stdout/init AND that `claude_session_uuid` persistence is unaffected.
- **The real driver stream fires `on_stdout_event` during a toolless window — verified by the
  fake-harness driver-seam test in `test_headless_role_driver.py` (Concern 4).**
- `record_turn_boundary(session_id=...)` writes `last_turn_at` keyed on the true
  `AgentSession.session_id` (not the env `agent_session_id`, not the Claude UUID) without depending on
  worker `os.environ` — verified by unit test.
- A post-`init` stall (stdout goes stale after the init stamp) is still recovered — NOT via the
  never-started gate (it correctly no longer fires, since `init` is real output) but via the
  **whole-turn deadline**: with a small injected `turn_timeout_s`, a fake-harness turn that emits
  `init` then hangs is preempted/killed through the turn-deadline path (`role_driver.py:404`
  `asyncio.wait_for` → `outcome.hung=True` / `exit_reason="headless_turn_timeout"`, and/or the
  `runner.py:764` watcher `_kill_turn(cause="timeout")`). Verified by a deterministic regression test
  (Risk 1 guard). The detection-latency tradeoff (turn deadline, not ~150s never-started gate) is
  documented and accepted.
- `python -m ruff format . && python -m ruff check .` clean; the named unit tests pass.

## Step by Step Tasks

### 1. Red-first repro tests
Write failing unit tests: (a) session-health derivation returns `sdk_ever_output=False` today when
only `last_stdout_at` is set (documents the bug) — assert it at BOTH the never-started site and the
`_tier2_reprieve_signal` site so the reprieve route is covered; (b) a runner test asserting
`on_stdout_event` is wired (fails today); (c) a driver-seam test asserting a toolless fake-harness
window fires `on_stdout_event` (Concern 4, fails today). Add the Risk-1 guard test skeleton.

### 2. Extract and extend the derivation — all four sites
Add module-level `_derive_sdk_ever_output(entry)` to `session_health.py` (before
`_never_started_past_grace`, since `_tier2_reprieve_signal` calls the latter). Replace ALL FOUR
inline expressions (`:985`, `:1127`, `:1310`, `:2057`), adding `last_stdout_at`. Update docstrings.
Run the two zero-hit greps (`sdk_ever_output = bool(` and `_sdk_ever_output = bool(`) as the BLOCKER
gate. Flip test (a) green at both sites.

### 3. Wire `on_stdout_event`/`on_init` liveness in the runner (two adapters)
Add the `_stamp_stdout_liveness` helper to `SessionRunner`. Pass a 0-arg adapter as
`on_stdout_event`, and a 1-arg `on_init` adapter that delegates to `_on_harness_init` (preserving
`persist_resume_scalars`) then stamps. Fail-silent + 5s cooldown. Flip tests (b) and (c) green;
confirm the resume-scalar persistence test still passes.

### 4. Fix `record_turn_boundary` id resolution
Add the optional `session_id` param. Plumb the true `AgentSession.session_id` from the runner
(`runner.py:431`) through the `sdk_client.py` stream fn to the result-event call site (`:2936`),
which passes it explicitly — NOT `data.get("session_id")` (Claude UUID). Keep the `os.environ`
fallback for the in-subprocess CLI-hook call sites.

### 5. Risk-1 regression guard
Confirm a post-`init`-then-hang turn is still recovered by the **whole-turn deadline** (NOT the
never-started gate, which correctly no longer fires once `init` stamped `last_stdout_at`). Write a
deterministic driver/runner test with a small injected `turn_timeout_s` and a fake harness that emits
`init` then hangs; assert the turn is preempted/killed via the turn-deadline path (`role_driver.py:404`
`asyncio.wait_for` → `outcome.hung=True` / `exit_reason="headless_turn_timeout"`, and/or the
`runner.py:764` watcher `_kill_turn(cause="timeout")`). Do NOT assert any session-health idle-gap
detector on `last_stdout_at` freshness — none exists (verified zero grep hits).

### N-1. Documentation
Update `docs/features/headless-session-runner.md` and the session-lifecycle/session-health reference
per the Documentation section.

### N. Final Validation
Run the named unit tests + `ruff`. Run the two BLOCKER-gate greps (both must return zero hits).
Confirm no mid-turn stall detector was loosened: the diff in `session_health.py` must touch ONLY the
four `sdk_ever_output` derivation sites (now routed through `_derive_sdk_ever_output`) plus the new
helper — the reprieve *cadence* logic and the per-tool/idle-gap detectors are unchanged; only the
"no output ever" input to the reprieve-cap guard is broadened.

## Verification

- Deterministic: unit tests over `_derive_sdk_ever_output`, the runner `on_stdout_event` wiring, and
  `record_turn_boundary(session_id=...)`.
- Behavioral (manual, post-merge, optional): re-run `/do-sdlc` for #1933/#1934 and confirm the PLAN/
  CRITIQUE transition no longer wedges; watch `logs/worker.log` for `last_stdout_at` freshness and
  absence of `zombie_uuid_no_output` recovery.

## Open Questions

1. **Should `last_stdout_at` freshness (not just presence) gate a mid-turn stall detector too?**
   This plan uses `last_stdout_at` only to satisfy the "SDK ever produced output" never-started gate.
   **Verified during this revision:** `session_health.py` has NO detector keyed on `last_stdout_at`
   or `last_activity` freshness (zero grep hits) — so the only backstop for a post-`init` hang is the
   coarse whole-turn deadline (`turn_timeout_s`, up to 7200s for PM/eng; see Risk 1). A follow-up
   could add a first-class mid-turn idle-gap detector on `last_stdout_at` *freshness* (the true
   headless analogue of PTY Gap B's `last_pty_read_loop_at`) to catch a post-`init` hang closer to the
   ~150s mark instead of at the turn deadline. Deferred as a scope expansion — the whole-turn deadline
   already recovers the hung turn, so this is a latency optimization, not a correctness gap.
2. **Was the observed wedge a toolless turn, or did a tool call fire but the sidecar liveness write
   no-op?** Recon strongly indicates toolless (no tool/liveness log lines at all), and the fix is
   robust either way. If Build's repro shows the sidecar path also failing under the worktree cwd,
   add a sidecar-resolution assertion; otherwise leave the CLI-hook path untouched.
