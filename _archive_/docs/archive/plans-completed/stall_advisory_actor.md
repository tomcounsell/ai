---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1768
last_comment_id:
revision_applied: true
---

# Stall-advisory: promote to actor + granite_wedged signal

## Problem

On 2026-06-23, three `running` sessions wedged after a worker restart. Their granite PTY
containers looped on `transcript read: no-new-entry … using unknown classification` and never
advanced a turn — while still emitting fresh heartbeats. The `stall-advisory` reflection
(`reflections/stall_advisory.py`) **correctly detected** two of them (`STALLED reason=never_started`,
`STALLED reason=kill_transition`) but it is **advisory only**: it warns to the log and never acts.
No reflection killed the wedged sessions or re-drove their work; a human had to. Worse, the
turn-0-loop case (heartbeating but stuck before the first turn) is classified **healthy** because
there are zero `turn_start` events to anchor an idle-gap check.

This is the session-level early-warning layer. Catching one wedged session early prevents the
cascade: three simultaneous hung granite reads saturated the worker thread pool and ultimately
hung the whole worker. If `stall-advisory`'s detection could *act*, the blast radius would have
been one session, not the worker.

**Current behavior:**
- `classify_session_stall()` (`agent/session_stall_classifier.py`) emits `never_started`,
  `kill_transition`, `idle_gap_exceeded_*` — but is blind to the heartbeating-but-stuck case
  where the PTY screen has gone stale while the read-loop and heartbeat stay fresh.
- `reflections/stall_advisory.py` logs `STALLED`/`SUSPECT` findings and optionally sends a
  Telegram alert. **Zero mutations.** Detection exists; the hands do not.
- The `agent-session-cleanup` orphan reaper won't kill a fresh-heartbeat session
  (`_session_is_alive()` gates on heartbeat < 30 min — `agent/session_health.py:3394`).
- `session-recovery-drip` (`agent/sustainability.py:132`) only resumes `paused`/`paused_circuit`,
  never `killed`, and only when an Anthropic-circuit recovery flag is set.

**Desired outcome:**
A session that is heartbeating but demonstrably wedged (no turn progress + stale
`last_pty_activity_at` despite a fresh read-loop/heartbeat) is **detected** (new `granite_wedged`
verdict) and **recovered automatically** — killed and its unanswered human messages re-enqueued via
`valor-catchup` — under conservative, well-gated, dry-run-by-default rules, before it can take the
worker down with it.

## Freshness Check

**Baseline commit:** `9af25e3814a933b22e4ecdae60d9a5866994fb96`
**Issue filed at:** 2026-06-23 (same day; GraphQL rate-limited so exact timestamp not fetched, but
issue is fresh)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_stall_classifier.py:114` (`classify_session_stall`) — verdict logic present as
  described; `never_started`, `kill_transition`, `idle_gap_exceeded_stall` all emitted. Holds.
- `agent/session_health.py:3394` (`_session_is_alive`) — gates on heartbeat <
  `ORPHAN_PROCESS_HEARTBEAT_GRACE_SECONDS` (1800s). Holds — fresh heartbeat ⇒ skipped.
- `agent/sustainability.py:132` (`session_recovery_drip`) — gated on `recovery:active`/
  `worker:recovering`; resumes only `paused_circuit`/`paused`. Holds.
- `agent/granite_container/container.py:454-469` (`_transcript_read_branch`) — returns
  `transcript read: no-new-entry` for a present-but-no-new-text transcript. Holds.
- `agent/granite_container/bridge_adapter.py:680` (`_on_pty_read`) — `last_pty_activity_at` is
  **diff-gated**: stamped only when the buffer differs from the prior read (screen repainted),
  while `last_pty_read_loop_at` is stamped unconditionally. Holds — this is the load-bearing fact
  for the `granite_wedged` signal.

**Cited sibling issues/PRs re-checked:**
- #1724 (path-B mid-run wedge detector) — its stage-1 PTY quiescence detector
  (`agent/session_health.py:2566`) handles the *mid-run tool-wedge* case (tool in flight + stale
  PTY). This issue covers the *complementary* turn-0 / never-progressed case (no tool in flight,
  no turn_start). Both reuse the same `last_pty_activity_at`/`last_pty_read_loop_at` liveness
  primitives. Coordinated, not overlapping.
- #1172 (kill-on-evidence policy) — informs the conservative gating. Holds.
- #1271 (orphan reaper heartbeat gate) — confirms why the reaper won't catch this. Holds.

**Commits on main since issue was filed (touching referenced files):** none relevant —
baseline is the issue-filing-day HEAD.

**Active plans in `docs/plans/` overlapping this area:** Issue #1028 (reflections reorg) will
later touch `reflections/` layout, `config/reflections.yaml`, and the dashboard status grid — a
**coordination signal**, not a blocker. This plan keeps its diff confined to
`agent/session_stall_classifier.py` + `reflections/stall_advisory.py` + a small new action helper +
config/settings + tests/docs. Noted in the PR description.

**Notes:** No drift. All premises hold against `9af25e3`.

## Prior Art

- **#1724 (path-B mid-run wedge detector)**: Added the stage-1 PTY quiescence detector in
  `agent/session_health.py` (around line 2566), the `last_pty_read_loop_at` /
  `last_pty_activity_at` / `mid_run_quiescent_since` / `mid_run_pty_snapshot` fields on
  `AgentSession`, and the diff-gated `_on_pty_read` callback in `bridge_adapter.py`.
  **Directly reusable:** this plan consumes those same liveness fields for the turn-0 case. Stage-1
  is observe-only ("CONFIRMED SUSPECT … awaiting stage-2 dispatch"); this plan does NOT wire
  stage-2 for the tool-in-flight case — it handles the orthogonal turn-0 case via the classifier.
- **#1271 (orphan reaper)**: `agent/session_health.py` `_session_is_alive` heartbeat gate — the
  reason the orphan reaper cannot catch a fresh-heartbeat wedge. Confirms the gap; not a fix to
  reuse.
- **#1539 (crash auto-resume policy)**: Established the dry-run-by-default + `FEATURES__*_ENABLED`
  flag + per-session attempt cap + per-run budget pattern in `config/settings.py`
  (`FeatureSettings.crash_autoresume_*`). **This plan mirrors that exact pattern** for
  `FEATURES__STALL_RECOVERY_ENABLED`.
- **`session-recovery-drip`** (`agent/sustainability.py:132`): the conservative "K-per-tick,
  FIFO, fail-soft, never-raise" reflection-actor shape this plan's action path mirrors.
- **`valor-catchup`** (`bridge/agent_catchup.py`): the agent-judgment re-enqueue mechanism — a
  standalone CLI invoked out-of-band; strongly biased toward NOT replying. Reused as-is via
  subprocess to re-drive the killed session's unanswered human messages.

## Research

No relevant external findings — proceeding with codebase context. This is a purely internal
reflections/worker change with no external libraries, APIs, or ecosystem patterns involved.

## Spike Results

### spike-1: Is `last_pty_activity_at` a reliable wedge signal for the turn-0 loop?
- **Assumption**: "A granite session looping on `no-new-entry` leaves `last_pty_activity_at` stale
  while `last_pty_read_loop_at` and the heartbeat stay fresh."
- **Method**: code-read (`agent/granite_container/bridge_adapter.py:680` `_on_pty_read`).
- **Finding**: PARTIALLY confirmed, with a **material caveat** the critique surfaced. `_on_pty_read`
  stamps `last_pty_read_loop_at` unconditionally on every `_cycle_idle` return, but stamps
  `last_pty_activity_at` **only when `buffer != prev_buffer`**. HOWEVER the diff-gate compares an
  ANSI-stripped but **not cursor/spinner/elapsed-counter-normalized** buffer
  (`bridge_adapter.py:687-695`). The in-repo comment authored by the #1724 work explicitly warns:
  "a blinking cursor or spinner repaint that normalization would strip could keep
  `last_pty_activity_at` fresh on a wedged screen, defeating quiescence detection. Address this
  before wiring stage-2 recovery." A wedged TUI whose spinner/elapsed-counter keeps animating at
  ≥1 Hz would therefore keep `last_pty_activity_at` FRESH — and `granite_wedged` would never fire
  for the exact incident it targets.
- **Confidence**: medium (the mechanism is real; the spinner-defeat risk is unresolved by code-read
  alone — needs empirical confirmation against a real wedged session, OR a normalization
  prerequisite, OR a non-screen corroborating signal).
- **Impact on plan**: **REVISED.** The diff-gate must be normalized (strip spinner/cursor/elapsed
  noise) so a quiescent-but-animating screen produces a stable buffer and `last_pty_activity_at`
  genuinely goes stale on a wedge. This normalization is precisely the work #1724's own comment
  says must precede stage-2 recovery, and this plan is that consumer — so it is in-scope, not a
  rabbit hole. Additionally, `granite_wedged` does NOT rely on stale `last_pty_activity_at` ALONE:
  see spike-3 and the revised Technical Approach for the corroborating `no-new-entry` evidence.

### spike-3: Is there a screen-animation-immune corroborating signal for the wedge?
- **Assumption**: "The `no-new-entry` transcript-read branch (`container.py:454-469`) is immune to
  spinner animation because it inspects the PM *transcript file*, not the rendered screen."
- **Method**: code-read (`agent/granite_container/container.py:454-487`).
- **Finding**: Confirmed at the source level: `_transcript_read_branch` returns
  `transcript read: no-new-entry` based on whether the transcript file has a new text-bearing entry
  past baseline — independent of any spinner repaint on screen. This is a true second signal.
  BUT there is no durable per-session counter of consecutive `no-new-entry` cycles today (verified:
  `AgentSession` has no such field; the branch is computed and logged, not persisted).
- **Confidence**: high (signal exists and is screen-immune) / the *durable counter* would be new
  instrumentation.
- **Impact on plan**: The revised `granite_wedged` design uses a **two-of-two** gate where feasible:
  (a) normalized-buffer staleness of `last_pty_activity_at`, AND (b) the corroborating evidence that
  the read-loop is fresh (proving the container is actively cycling, i.e. not just dead). The
  durable no-new-entry counter is explicitly KEPT OUT of scope to avoid #1028 overlap — instead the
  normalization fix (spike-1 revision) makes signal (a) trustworthy on its own, and the empirical
  test (see Test Impact) confirms staleness actually accrues on a real animating wedge.

### spike-2: What is the canonical "kill a running session" path the action should reuse?
- **Assumption**: "There is a single helper that terminates the session PID and sets status=killed."
- **Method**: code-read (`tools/agent_session_scheduler.py:898`).
- **Finding**: `_kill_agent_session(target, *, skip_process_kill=False)` calls `_kill_process(pid)`
  then `finalize_session(target, "killed", reason=..., skip_auto_tag=True, skip_checkpoint=True)`.
  `transition_status` is the WRONG path — it rejects transitions *to* terminal statuses by default.
- **Confidence**: high
- **Impact on plan**: Action path imports and calls `_kill_agent_session` from
  `tools.agent_session_scheduler`. Dry-run mode skips the call entirely and only logs intent.

## Data Flow

1. **Entry point**: `reflection_scheduler` ticks `stall-advisory` every 300s
   (`config/reflections.yaml`), calling `run_stall_advisory(params)`.
2. **Query**: `run_stall_advisory` lists sessions in `_RUNNING_PROBE_STATUSES`, filters out
   concurrently-terminal ones.
3. **Classify**: for each session, `classify_session_stall(events, session=...)` returns a
   `StallVerdict`. **NEW:** before the idle-gap analysis, a `granite_wedged` check fires when
   `status` is running, there are zero `turn_start` events, the read-loop/heartbeat are fresh, and
   `last_pty_activity_at` is stale past a grace window.
4. **Decide (NEW action-mode)**: for each `stalled` finding whose reason is in
   `{never_started, granite_wedged, idle_gap_exceeded_stall}`, the action path records an
   observation (cross-tick consecutive counter in Redis), and when the session has accumulated
   ≥N consecutive stalled observations AND the per-run cap and per-session budget allow:
   - **Dry-run (default)**: log the intended kill + catchup with the triggering verdict; mutate nothing.
   - **Enforce (`FEATURES__STALL_RECOVERY_ENABLED=true`)**: call `_kill_agent_session(session)` to
     terminate the PID and set status=killed, then `subprocess`-invoke `valor-catchup` to re-enqueue
     genuinely-unanswered human messages, increment the per-session budget counter, and record telemetry.
5. **Output**: `run_stall_advisory` returns `{status, findings, summary}` (unchanged contract,
   extended with recovery counts in the summary); every kill/skip decision is logged with the
   triggering verdict; optional Telegram alert behaviour is unchanged.

## Architectural Impact

- **New dependencies**: `reflections/stall_advisory.py` gains imports of
  `tools.agent_session_scheduler._kill_agent_session`, `config.settings`, and `subprocess`
  (already imported). The classifier gains no new imports (stays zero-write, no
  `agent.session_health` coupling — enforced by existing tests).
  `agent/granite_container/bridge_adapter.py` gains a small `_normalize_pty_buffer` helper (and may
  import a spinner-verb regex from `pty_driver.py` if exposed) — a prerequisite fix, additive.
- **Interface changes**: `StallVerdict` gains a new `reason` value `granite_wedged` (additive, no
  signature change). `run_stall_advisory` return contract is unchanged (summary string extended).
- **Coupling**: `stall_advisory` now couples to the kill path and `valor-catchup`. The detection
  layer (`classify_session_stall`) stays decoupled and read-only. We deliberately put the action in
  `stall_advisory` (action-mode) rather than a new reflection — it already iterates running
  sessions and computes verdicts, so a second reflection would re-run classification and double the
  query load (answers the issue's open question).
- **Data ownership**: new Redis counters under `{project_key}:stall-recovery:*` (consecutive
  observation counts per session, per-session kill budget). Reads/writes go through the same
  POPOTO_REDIS_DB plain-key pattern already used by `read_project_health_counters` and
  `session-recovery-drip` — these are NOT Popoto-managed model keys, so raw `r.get`/`r.incr`/
  `r.delete`/`r.expire` are permitted (the no-raw-redis rule applies only to Popoto model keys).
- **Reversibility**: fully reversible — set `FEATURES__STALL_RECOVERY_ENABLED=false` (the default)
  to return to observe-only behaviour. No schema migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm action-mode-vs-new-reflection decision; confirm threshold defaults)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. `valor-catchup` is already a registered
CLI entry point (`pyproject.toml:79`); the liveness fields already exist on `AgentSession`.

## Solution

### Key Elements

- **Normalized PTY diff-gate** (`agent/granite_container/bridge_adapter.py`): a prerequisite fix.
  The `_on_pty_read` diff-gate currently compares an ANSI-stripped but NOT spinner/cursor/elapsed-
  normalized buffer, so an animating-but-wedged TUI keeps `last_pty_activity_at` fresh and defeats
  detection. Add a `_normalize_pty_buffer()` helper (reusing the spinner-verb knowledge already in
  `pty_driver.py`) that strips the spinner glyph/verb, the elapsed-seconds counter, and cursor/blink
  noise before the `buffer != prev_buffer` comparison. This makes `last_pty_activity_at` a
  trustworthy quiescence signal — exactly the work #1724's in-repo comment says must precede wiring
  recovery. (Detection consumer == this plan, so in-scope.)
- **`granite_wedged` verdict** (`agent/session_stall_classifier.py`): a new stalled reason for a
  running session with zero `turn_start` events, a fresh read-loop (`last_pty_read_loop_at`, proving
  the container is actively cycling — not merely dead), a fresh heartbeat, and a stale
  *normalized* `last_pty_activity_at` (beyond a grace window). Computed from existing session fields;
  zero writes. The fresh-read-loop requirement is the corroborator that distinguishes a genuine
  wedge (cycling but not repainting) from an abandoned/dead session (caught by other reasons).
- **Action-mode in `stall-advisory`** (`reflections/stall_advisory.py`): consumes the existing
  verdicts (no new detection); for actionable `stalled` reasons it records a consecutive-observation
  count, and once gates pass, kills the session and triggers `valor-catchup`. Dry-run by default.
- **Feature flag + thresholds** (`config/settings.py`): `FeatureSettings.stall_recovery_enabled`
  (default False), `stall_recovery_consecutive_observations` (N), `stall_recovery_run_budget` (K),
  `stall_recovery_per_session_budget`, plus classifier-side `GRANITE_WEDGED_PTY_STALE_SECS` grace.
- **Cross-tick state** (Redis plain keys): per-session consecutive-stalled counter and per-session
  kill-attempt budget under `{project_key}:stall-recovery:*`, with TTL so counts decay if a session
  recovers and stops being reported.

### Flow

stall-advisory tick → classify each running session → for each `stalled` finding with an
actionable reason → increment consecutive-stalled counter → if counter ≥ N AND run-cap not hit
AND per-session budget remains:
- dry-run: log `[stall-recovery] WOULD kill session=… reason=… (dry-run)`; mutate nothing
- enforce: `_kill_agent_session(session)` → `subprocess valor-catchup` → increment per-session
  budget → log `[stall-recovery] killed+recovered session=… reason=…`

For any session that classifies healthy/suspect this tick → reset its consecutive-stalled counter.

### Technical Approach

- **Classifier (`_classify`)**: add a `granite_wedged` branch in the never-started region. After
  confirming `session_status in _RUNNING_PROBE_STATUSES` and `not has_turn_start`, *before* the
  `never_started` grace check, evaluate: read `last_pty_read_loop_at`, `last_pty_activity_at`,
  `last_heartbeat_at` off the session. If the read-loop marker is fresh relative to the heartbeat
  (within `HEARTBEAT_FRESHNESS_WINDOW`, mirrored as a local constant to avoid importing
  `session_health`) AND `last_pty_activity_at` is stale by more than `GRANITE_WEDGED_PTY_STALE_SECS`,
  return `StallVerdict("stalled", "granite_wedged", {...signals...})`. Fail-soft: any missing field
  ⇒ fall through to the existing `never_started` path (don't fabricate a wedge). Use
  `bridge.utc.to_unix_ts` for all datetime math.
- **Constants** live as module-level named values with a "provisional/tunable" comment and env
  overrides, mirroring the existing `NEVER_STARTED_GRACE_SECS` style (the live grace constant the
  verdict at `session_stall_classifier.py:216` actually compares against — NOT
  `NEVER_STARTED_CONFIRM_MARGIN_SECS`, which the critique confirmed is dead in the hot path; do not
  model the new constant as a base+margin pair):
  `GRANITE_WEDGED_PTY_STALE_SECS` (default e.g. 600, env `GRANITE_WEDGED_PTY_STALE_SECS`).
- **Prerequisite — normalize the diff-gate**: add `_normalize_pty_buffer(buffer)` in
  `bridge_adapter.py` (or a small shared helper if `pty_driver.py` already exposes the spinner-verb
  regex) and apply it to BOTH sides of the `buffer != self._prev_pty_buffer` comparison and to the
  stored `self._prev_pty_buffer`. Remove/refresh the stale "address before stage-2" comment block
  to reflect that normalization has landed. Keep `last_pty_read_loop_at` stamping unconditional.
- **Action path** is a new internal function `_maybe_recover(session, verdict, settings, r)` in
  `stall_advisory.py`, called only for `stalled` findings. It:
  1. Returns early if `verdict.reason` not in the actionable set
     `{never_started, granite_wedged, idle_gap_exceeded_stall}`.
  2. Increments `{project_key}:stall-recovery:consec:{session_id}` (TTL ~2× the reflection cadence
     so it decays if the session stops being reported).
  3. If counter < `stall_recovery_consecutive_observations` → log "observation N/M", return.
  4. If per-run kill count ≥ `stall_recovery_run_budget` → log "run budget exhausted, skip", return.
  5. If `{project_key}:stall-recovery:budget:{session_id}` ≥ `stall_recovery_per_session_budget`
     → log "per-session budget exhausted, skip", return.
  6. If `not settings.features.stall_recovery_enabled` → log
     `[stall-recovery] WOULD kill+recover session=… reason=… (dry-run)`, return (mutate nothing).
  7. Enforce: re-read session status (Race 1); if still running, `_kill_agent_session(session)`; on
     success `subprocess.run(["valor-catchup"], …)`; increment per-session budget; increment per-run
     counter; log the kill with the triggering verdict. All wrapped fail-soft (never raise; a
     recovery error must not crash the reflection).
  8. **Audit surface (concern 1)**: append a typed session-event (e.g. `stall_recovery_action` with
     fields `verdict_reason`, `killed: bool`, `catchup_invoked: bool`, `catchup_ok: bool`,
     `dry_run: bool`) so a kill-succeeds-but-catchup-fails outcome is durably visible on the
     dashboard feed and queryable — not merely a single WARNING log line that scrolls away. Use the
     existing `_append_session_event` pattern (see `bridge_adapter.py` `granite_user_routed`). On
     catchup failure, also surface it in the reflection summary count (e.g. "1 killed, 1 catchup-failed").
- **Reset on recovery**: when a session classifies healthy/suspect this tick, delete its
  `consec` key so a single slow-but-live turn doesn't accumulate toward a kill.
- **Suspect is never actioned** — only `stalled`, and only the actionable reason subset.
- The Redis helpers reuse the plain-key pattern from `read_project_health_counters` /
  `session-recovery-drip` (these keys are not Popoto-managed).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_maybe_recover` wraps the kill + catchup in try/except and logs at `warning` on
  failure — add a test asserting that when `_kill_agent_session` raises, the reflection still
  completes, returns its summary, and logs the failure (observable behavior, not silent pass).
- [ ] The classifier's outer `classify_session_stall` already returns `healthy/"unclassifiable"`
  on any exception; add a test that a session with malformed datetime fields does not raise and
  does not spuriously return `granite_wedged`.

### Empty/Invalid Input Handling
- [ ] `granite_wedged` check with `last_pty_activity_at=None` (never stamped) → must NOT fire
  granite_wedged (fall through to never_started grace). Test it.
- [ ] `_maybe_recover` with an unknown/`None` `project_key` → fail-soft, no Redis write, no kill.
- [ ] `valor-catchup` subprocess missing on PATH (FileNotFoundError) → swallowed + logged, kill
  still counted (mirror `_send_alert`'s handling). Test it.

### Error State Rendering
- [ ] The reflection summary surfaces recovery counts (e.g. "1 killed, 2 dry-run-would-kill"); test
  the dry-run summary string and the enforce summary string differ as expected.
- [ ] Every kill/skip decision logs the triggering verdict reason — assert via `caplog`.

## Test Impact

- [ ] `tests/unit/test_session_stall_classifier.py` — UPDATE: add cases for `granite_wedged` (fires
  on fresh read-loop + stale pty-activity + no turn_start; does NOT fire when pty-activity is fresh,
  when turn_start exists, or when fields are None). Existing `never_started`/idle cases must still
  pass unchanged.
- [ ] `tests/integration/test_stall_advisory_e2e.py` — UPDATE: add dry-run-emits-but-doesn't-act
  case, enforce-mode kills+re-enqueues case, and the conservative gates (suspect never killed,
  N-consecutive required, run-cap respected, per-session budget respected). Existing advisory-only
  assertions remain valid (default flag is off ⇒ no mutations).
- [ ] `tests/unit/test_stall_detection.py` — REVIEW (likely UPDATE): confirm no assertion presumes
  `granite_wedged` is impossible; adjust any exhaustive verdict-reason enumeration.
- [ ] `tests/integration/test_agent_catchup_recovery.py` — REVIEW: confirm the new subprocess
  invocation of `valor-catchup` from the reflection doesn't conflict with existing catchup tests
  (it shells out; tests mock subprocess). No change expected unless it asserts catchup callers.
- [ ] `tests/unit/` granite-container PTY tests (locate the existing bridge_adapter/`_on_pty_read`
  test, e.g. `tests/unit/test_*bridge_adapter*` / `test_*pty*`) — UPDATE/ADD: a focused unit test
  for `_normalize_pty_buffer` asserting that two buffers differing ONLY by spinner glyph/verb,
  elapsed-seconds counter, or cursor/blink noise normalize to the SAME string (so the diff-gate does
  NOT stamp `last_pty_activity_at`), while a buffer with genuinely new text content normalizes to a
  DIFFERENT string (does stamp). This is the empirical-confirmation test the critique required —
  it pins the spinner-defeat fix and the diff-gated semantics dependency together.

No other existing tests are affected — the action feature ships behind a default-off flag, so all
advisory-only behavior is preserved verbatim when `FEATURES__STALL_RECOVERY_ENABLED` is unset. The
normalization prerequisite is additive (stricter diff-gate) and only changes WHICH buffers count as
"repainted"; any existing bridge_adapter test asserting unconditional `last_pty_read_loop_at`
stamping remains valid.

## Rabbit Holes

- **Wiring stage-2 of #1724 (tool-in-flight mid-run recovery)**: out of scope. That is the
  *tool-wedge* case; this issue is the *turn-0/never-progressed* case. Reuse the liveness fields,
  do not touch `_evaluate_mid_run_quiescence`.
- **Adding a durable no-new-entry cycle counter to the container/AgentSession**: tempting (the issue
  mentions "≥M consecutive no-new-entry cycles") but it requires new persisted container state that
  overlaps with #1028's reflections reorg and #1724's PTY plumbing. The *normalized*
  `last_pty_activity_at` staleness signal (after the prerequisite normalization fix) captures the
  same failure without a new persisted counter. Use it; defer the cycle-counter approach. NOTE: the
  diff-gate normalization itself IS in scope (it is the consumer fix #1724's comment mandates) — do
  not confuse it with the deferred durable counter.
- **Building a new `stall-recovery` reflection**: the issue's open question. Resolved in favour of
  action-mode (avoids a second full classification pass). Do not create a new reflection callable.
- **Recovering a fully-hung worker**: explicitly out of scope (the reflection scheduler is
  in-process; a wedged worker stops ticking). That is the external watchdog's job under the
  companion `bridge` issue.

## Risks

### Risk 1: False kill of a slow-but-live session
**Impact:** A legitimately busy session (e.g. a long granite turn that paints slowly) gets killed,
losing real work.
**Mitigation:** Dry-run by default; act only on `stalled` (never `suspect`); require N consecutive
stalled observations across ticks; the `granite_wedged` check requires *zero turn_start events* (a
session that has ever turned is ineligible for granite_wedged) AND a stale screen well past the
grace window. Per-session and per-run budgets cap blast radius. All thresholds env-tunable.

### Risk 2: Spinner/cursor animation keeps `last_pty_activity_at` fresh on a wedged screen
**Impact:** This is the BLOCKER the critique surfaced. Without normalization, an animating-but-wedged
TUI repaints the spinner/elapsed-counter at ≥1 Hz, the un-normalized diff-gate sees a changed
buffer every tick, `last_pty_activity_at` stays fresh, and `granite_wedged` NEVER fires for the
exact incident it targets.
**Mitigation:** The prerequisite normalization fix (strip spinner/cursor/elapsed noise before the
diff comparison) makes a quiescent-but-animating screen produce a stable normalized buffer, so
`last_pty_activity_at` genuinely goes stale on a wedge. The `_normalize_pty_buffer` unit test
(Test Impact) empirically pins this: spinner-only deltas normalize equal, real-content deltas
normalize unequal. This converts spike-1 from code-read-only to test-confirmed.

### Risk 4: `last_pty_activity_at` semantics change under #1724/#1028 follow-ups
**Impact:** If a future change makes `last_pty_activity_at` stamp unconditionally, the
`granite_wedged` signal silently stops firing.
**Mitigation:** A unit test pins the diff-gated semantics expectation (stale-activity ⇒ wedged) and
documents the dependency in `docs/features/stall-recovery.md`. The coordination note in the PR flags
the shared-field dependency to #1028.

### Risk 3: `valor-catchup` re-enqueues nothing or the wrong thing
**Impact:** The killed session's work is not re-driven, or a spurious session is enqueued.
**Mitigation:** `valor-catchup` is agent-judgment and strongly biased toward NOT replying; it is
already the sanctioned re-enqueue mechanism (`docs/features/agent-judgment-catchup.md`). The kill
happens regardless of catchup outcome (catchup failure is logged, not fatal), so a wedged session is
always stopped even if re-enqueue is a no-op.

## Race Conditions

### Race 1: Session transitions to terminal between classification and kill
**Location:** `reflections/stall_advisory.py` `_maybe_recover`, between the `classify_session_stall`
call and `_kill_agent_session`.
**Trigger:** The worker finalizes the session (completes/fails) on its own thread while the
reflection is mid-decision.
**Data prerequisite:** The session must still be in a `_RUNNING_PROBE_STATUSES` status at kill time.
**State prerequisite:** `_kill_agent_session` → `finalize_session` must be idempotent / safe on an
already-terminal session.
**Mitigation:** Re-read the session status immediately before kill; if terminal, skip and reset the
consec counter. `finalize_session` is already idempotent for terminal targets (used by CLI kill).
Wrap in try/except so a `StatusConflictError` is logged, not fatal.

### Race 2: Two ticks act on the same session (overlapping reflection runs)
**Location:** consecutive-observation + per-session-budget counters.
**Trigger:** A slow tick overruns the 300s cadence and a second tick starts.
**Data prerequisite:** Counters must be monotonic and bounded by the per-session budget.
**State prerequisite:** Budget check and increment should be effectively atomic.
**Mitigation:** Use Redis `INCR` (atomic) for both counters; the per-session budget is checked
before kill and incremented after, so worst case is one extra kill attempt on an already-killed
session — caught by Race 1's terminal re-read. Reflection scheduler runs reflections serially in
practice (single asyncio loop), so true overlap is unlikely.

## No-Gos (Out of Scope)

- [ORDERED] Worker-level full-hang recovery (external watchdog) — explicitly the companion
  `bridge`-labeled issue's job, which must land separately in its own process; the in-process
  reflection cannot recover a worker that has stopped ticking, so this cannot ship here. Gated on
  the bridge watchdog work landing.
- [ORDERED] Stage-2 recovery for the *tool-in-flight* mid-run wedge case (the
  `_evaluate_mid_run_quiescence` "CONFIRMED SUSPECT … awaiting stage-2 dispatch" path in
  `agent/session_health.py:2566`). This plan handles only the orthogonal turn-0 / never-progressed
  case and reuses the #1724 liveness fields read-only. Wiring stage-2 changes a different code path
  with its own gating decisions and must be sequenced as its own change rather than folded in here.
- [ORDERED] Reflections directory/layout reorg and dashboard status-grid changes — sequenced after
  the #1028 reflections-reorg lands (it restructures `reflections/` and `config/reflections.yaml`).
  This plan deliberately keeps its diff confined to the existing `stall_advisory.py` file and only
  flags the shared-field/config dependency; reorganizing the file layout now would collide with that
  in-flight restructure.

## Update System

No update system changes required — this feature is purely internal to the worker's reflection
loop. The `FEATURES__STALL_RECOVERY_ENABLED` flag defaults to False and is set per-machine via the
existing `.env` mechanism (same as `FEATURES__CRASH_AUTORESUME_ENABLED`); enabling it on a machine
is a documented one-line `.env` edit picked up on the next worker restart — no propagation step.
A `.env.example` placeholder + comment is added for the new flag (per the secrets-completeness
check convention), but no new dependency or config file is introduced.

## Agent Integration

No agent integration required — this is a worker-internal reflection change. The agent does not
invoke stall-recovery; it runs autonomously on the worker's reflection scheduler. The action path
shells out to the **already-registered** `valor-catchup` CLI (`pyproject.toml:79`); no new CLI
entry point, no MCP server, no `.mcp.json` change, and no bridge import. Integration coverage is the
e2e test asserting the reflection invokes `valor-catchup` (mocked subprocess) in enforce mode.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/stall-recovery.md` documenting: the `granite_wedged` signal and the
  liveness fields it reads, the action-mode gates (N-consecutive, K-per-run, per-session budget),
  the `FEATURES__STALL_RECOVERY_ENABLED` flag (default off, how to enable, how to revert), the
  dependency on `last_pty_activity_at`'s diff-gated semantics, and the relationship to #1724
  (complementary) and the companion bridge watchdog (backstop).
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update the reflections doc/index only if it enumerates reflections — add stall-advisory's new
  action-mode (cross-reference, don't duplicate).

### External Documentation Site
- [ ] Not applicable — no external docs site for this repo area.

### Inline Documentation
- [ ] Docstring on the new `granite_wedged` classifier branch explaining the fresh-heartbeat /
  stale-screen signature and the diff-gated dependency.
- [ ] Docstring on `_maybe_recover` enumerating every gate and the dry-run contract.
- [ ] `config/settings.py` field descriptions for each new `FeatureSettings` field, mirroring the
  `crash_autoresume_*` style (env var name + issue reference).

## Success Criteria

- [ ] The PTY diff-gate is normalized (`_normalize_pty_buffer`) so spinner/cursor/elapsed-only
  repaints do NOT stamp `last_pty_activity_at`; the empirical unit test confirms spinner-only deltas
  normalize equal and real-content deltas normalize unequal.
- [ ] `classify_session_stall()` emits a `granite_wedged` verdict for a running session with zero
  `turn_start` events, a fresh read-loop/heartbeat, and stale (normalized) `last_pty_activity_at`;
  unit tests cover fire + the three non-fire cases.
- [ ] `stall-advisory` action-mode kills `stalled` sessions (actionable reasons only) and triggers
  `valor-catchup`, gated by N-consecutive-observations, K-per-run cap, and per-session budget.
- [ ] Ships dry-run by default behind `FEATURES__STALL_RECOVERY_ENABLED`; enabling it is documented
  and reversible (single `.env` edit).
- [ ] Telemetry/logging records every kill/skip decision with the triggering verdict reason.
- [ ] Tests cover: granite_wedged detection, dry-run emits-but-doesn't-act, enforce-mode
  kills+re-enqueues, suspect-never-killed, N-consecutive-required, run-cap-respected,
  per-session-budget-respected.
- [ ] `docs/features/stall-recovery.md` created and indexed; flag/gates/signal documented.
- [ ] Tests pass (`/do-test`, narrowly scoped to the touched files)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `reflections/stall_advisory.py` references `valor-catchup` and
  `_kill_agent_session`, and `config/settings.py` defines `stall_recovery_enabled`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (classifier)**
  - Name: classifier-builder
  - Role: Add `granite_wedged` verdict + constants to `agent/session_stall_classifier.py`
  - Agent Type: builder
  - Resume: true

- **Builder (action-mode)**
  - Name: action-builder
  - Role: Add `_maybe_recover` action path + Redis counters to `reflections/stall_advisory.py` and
    `FeatureSettings` fields to `config/settings.py`
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Extend the test files per Test Impact
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: stall-validator
  - Role: Verify all success criteria, run the narrowly-scoped tests, confirm default-off behaviour
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: stall-doc
  - Role: Create `docs/features/stall-recovery.md` + index entry + inline docstrings audit
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types
(see template — Tier 1 core agents suffice: builder, test-engineer, validator, documentarian.)

## Step by Step Tasks

### 0. Normalize the PTY diff-gate (BLOCKER prerequisite)
- **Task ID**: build-normalize
- **Depends On**: none
- **Validates**: the new `_normalize_pty_buffer` unit test (see Test Impact)
- **Informed By**: critique blocker; spike-1 revision; spike-3
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_normalize_pty_buffer(buffer)` to `agent/granite_container/bridge_adapter.py` (reuse
  `pty_driver.py` spinner-verb knowledge if exposed; otherwise a small local regex set for spinner
  glyph/verb + elapsed-seconds counter + cursor/blink noise).
- Apply normalization to both sides of the `buffer != self._prev_pty_buffer` diff and to the stored
  `_prev_pty_buffer`. Keep `last_pty_read_loop_at` stamping unconditional. Refresh the stale
  "address before stage-2" comment block.
- Add the focused unit test: spinner-only delta ⇒ no activity stamp; real-content delta ⇒ stamp.

### 1. Add `granite_wedged` verdict to the classifier
- **Task ID**: build-classifier
- **Depends On**: build-normalize
- **Validates**: tests/unit/test_session_stall_classifier.py
- **Informed By**: spike-1 revision (normalized last_pty_activity_at staleness), spike-3 (fresh-read-loop corroborator)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: false
- Add module constants `GRANITE_WEDGED_PTY_STALE_SECS` (env-overridable, provisional/tunable comment)
  and a local `HEARTBEAT_FRESHNESS_WINDOW` mirror (avoid importing session_health).
- In `_classify`, in the no-turn_start / running-status region, add a `granite_wedged` branch BEFORE
  the never_started grace check: fire only when read-loop/heartbeat fresh AND last_pty_activity_at
  stale past grace; fail-soft on any missing/malformed field (fall through to never_started).
- Keep zero writes and no `agent.session_health` import.

### 2. Add action-mode + feature flag
- **Task ID**: build-action
- **Depends On**: none
- **Validates**: tests/integration/test_stall_advisory_e2e.py
- **Informed By**: spike-2 (confirmed: `_kill_agent_session` is the canonical kill path), #1539 (flag pattern)
- **Assigned To**: action-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `FeatureSettings.stall_recovery_enabled` (default False) + `stall_recovery_consecutive_observations`,
  `stall_recovery_run_budget`, `stall_recovery_per_session_budget` to `config/settings.py`, mirroring
  the `crash_autoresume_*` style. Add `.env.example` placeholder + comment.
- Add `_maybe_recover(session, verdict, settings, r, run_state)` to `reflections/stall_advisory.py`
  implementing the full gate ladder (actionable-reason filter → consec counter → run cap →
  per-session budget → dry-run-vs-enforce → terminal re-read → kill + valor-catchup). Fail-soft throughout.
- Wire it into `run_stall_advisory`: call for `stalled` findings; reset consec counter for
  healthy/suspect; extend the summary string with recovery counts.
- Reuse the plain-key Redis pattern (INCR + TTL) from read_project_health_counters.

### 3. Validate classifier
- **Task ID**: validate-classifier
- **Depends On**: build-classifier
- **Assigned To**: stall-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_stall_classifier.py -q`; verify fire + non-fire cases.

### 4. Extend tests
- **Task ID**: build-tests
- **Depends On**: build-classifier, build-action
- **Validates**: tests/unit/test_session_stall_classifier.py, tests/integration/test_stall_advisory_e2e.py
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add unit cases for granite_wedged (fire + 3 non-fire). Add e2e cases: dry-run no-act, enforce
  kill+re-enqueue, suspect-never-killed, N-consecutive, run-cap, per-session-budget,
  kill-raises-still-completes, catchup-missing-swallowed.
- Review/adjust test_stall_detection.py and test_agent_catchup_recovery.py per Test Impact.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-classifier, build-action, build-tests
- **Assigned To**: stall-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/stall-recovery.md`; add index entry; audit inline docstrings.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-classifier, build-tests, document-feature
- **Assigned To**: stall-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the narrowly-scoped touched tests; confirm default-off preserves advisory-only behaviour;
  verify all success criteria + grep checks.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Classifier unit tests | `pytest tests/unit/test_session_stall_classifier.py -q` | exit code 0 |
| Stall-advisory e2e tests | `pytest tests/integration/test_stall_advisory_e2e.py -q` | exit code 0 |
| Flag defined | `python -c "from config.settings import Settings; assert hasattr(Settings().features, 'stall_recovery_enabled')"` | exit code 0 |
| Flag defaults off | `python -c "from config.settings import Settings; assert Settings().features.stall_recovery_enabled is False"` | exit code 0 |
| granite_wedged emitted | `grep -q granite_wedged agent/session_stall_classifier.py` | exit code 0 |
| Diff-gate normalized | `grep -q _normalize_pty_buffer agent/granite_container/bridge_adapter.py` | exit code 0 |
| Action references kill+catchup | `grep -q _kill_agent_session reflections/stall_advisory.py && grep -q valor-catchup reflections/stall_advisory.py` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_stall_classifier.py reflections/stall_advisory.py config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_stall_classifier.py reflections/stall_advisory.py config/settings.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). FULL run, 3 critics, 2026-06-23. Verdict: NEEDS REVISION → revised. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | all 3 (cross-validated) | `last_pty_activity_at` diff-gate compares an ANSI-stripped but NOT spinner/cursor/elapsed-normalized buffer; an animating-but-wedged TUI keeps it fresh, so `granite_wedged` may never fire. #1724's own in-repo comment (`bridge_adapter.py:687-695`) warns this must be fixed before wiring recovery. | spike-1 revised + new Key Element "Normalized PTY diff-gate" + task build-normalize (prerequisite) + Risk 2 + `_normalize_pty_buffer` empirical unit test in Test Impact | Normalize BOTH sides of the diff and the stored `_prev_pty_buffer`; keep `last_pty_read_loop_at` unconditional. spike-1 was code-read-only — the new unit test is the empirical confirmation. |
| CONCERN | critic | kill-succeeds-but-catchup-fails has no audit surface beyond one WARNING log. | Technical Approach step 8: typed `stall_recovery_action` session-event (verdict_reason, killed, catchup_invoked, catchup_ok, dry_run) via `_append_session_event` + "catchup-failed" count in summary. | Mirrors the `granite_user_routed` event pattern in `bridge_adapter.py`. |
| CONCERN | critic | Plan cited `NEVER_STARTED_CONFIRM_MARGIN_SECS` as a constant precedent, but it is dead in the hot path (verdict at `session_stall_classifier.py:216` uses `NEVER_STARTED_GRACE_SECS`). Could misdirect the builder toward a base+margin constant. | Technical Approach: precedent corrected to `NEVER_STARTED_GRACE_SECS`; explicit "do not model as base+margin pair" note. | `GRANITE_WEDGED_PTY_STALE_SECS` is a single flat env-overridable grace. |

---

## Resolved Decisions

(These were the plan's open questions; resolved at finalization so the build can proceed. Each is
reversible via the env-overridable constants.)

1. **Action-mode vs new reflection** — RESOLVED: action-mode inside `stall-advisory`. It already
   iterates running sessions and computes verdicts; a separate reflection would re-run
   classification and double the query load. This answers the issue's explicit open question.
2. **Threshold defaults** — RESOLVED (conservative, all env-overridable, "provisional/tunable"):
   `GRANITE_WEDGED_PTY_STALE_SECS=600`, `stall_recovery_consecutive_observations=3` (≈15 min at the
   300s cadence), `stall_recovery_run_budget=1` (mirrors recovery-drip's 1/tick),
   `stall_recovery_per_session_budget=2`. Biased toward NOT killing; tune up/down via env with no
   code change. Combined with dry-run-by-default, false-kill risk is minimal at rollout.
3. **Catchup scope** — RESOLVED: invoke `valor-catchup` with no extra flags (sweeps all owned
   chats). It is idempotent and strongly biased toward NOT replying, so a broad sweep is safe and
   avoids adding a new `--chat`/`--session` flag (wider scope, out of this plan). If a future
   incident shows over-broad re-enqueue, a targeted flag can be added then.
