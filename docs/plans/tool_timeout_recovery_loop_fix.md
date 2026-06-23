---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1762
last_comment_id:
revision_applied: true
---

# Tool-Timeout Recovery Loop Fix

## Problem

The `failure-loop-detector` reflection auto-filed issue #1762 after 3 sessions
failed within 4 hours with the identical fingerprint `3ce6a5b1471b9d01`:

> health check: 2 recovery attempts, never progressed (kind=tool_timeout)

Affected sessions: `sdlc-local-1735`, `sdlc-local-1626`,
`tg_valor_-1003449100931_1020`.

**Current behavior:**

When a tool genuinely hangs (a `tool_use` block with no following result/user
event in the transcript), the session-health tool-timeout sub-loop
(`_agent_session_tool_timeout_check`, `agent/session_health.py:2890`) detects
the wedge and recovers the session (`running → pending`) on attempt 1. But the
recovery **leaves the stale wedge signal on the `AgentSession` row**:

- `current_tool_name` and `last_tool_use_at` are durable `AgentSession` fields
  (`models/agent_session.py:500,505`). During normal running they are written by
  the transcript tailer (`agent/granite_container/transcript_tailer.py:153-164`),
  but the tailer's diff-gate **only writes them when it has a non-None value to
  write** — it does not push `None` over a stale value on a fresh transcript.
- The recovery requeue branch (`agent/session_health.py:1811-1895`) injects an
  advisory steering message ("don't call this tool again", PR #1738) but **never
  clears `current_tool_name` / `last_tool_use_at`** on the `AgentSession`. They
  remain pinned to the wedged tool name and frozen at the wedge time.
- On requeue the session resumes with a **brand-new, empty transcript**:
  `bridge_adapter.py:425-426` generates a fresh `uuid.uuid4()` for the Claude
  Code session id on **every** `run()`, so each resume writes to a new
  `~/.claude/projects/{cwd-slug}/{uuid}.jsonl` path. The new transcript is empty
  and contains no `tool_use` block — so the tailer has nothing to overwrite the
  stale fields with, and `current_tool_name` / `last_tool_use_at` stay pinned to
  the pre-recovery wedge values.
- `_check_tool_timeout` (line 360) reads those stale `AgentSession` fields and
  immediately sees an already-expired age (`now - frozen last_tool_use_at >
  budget`).
- Within one 30s sub-loop tick the wedge is re-detected → recovery attempt 2.
- At attempt 2, `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (=2, line 236/1753)
  gates straight to the `failed` terminal branch — no second requeue, no chance
  for the steering to take effect. The session dies with "never progressed".

**Note on the disproven mechanism:** an earlier draft of this plan blamed the
tailer re-reading the *same* transcript and advancing a persisted `byte_offset`.
Driver verification refuted that: each resume gets a *fresh* UUID/transcript
(`bridge_adapter.py:425-426`), and `byte_offset` is **explicitly not persisted**
onto `AgentSession` (`models/agent_session.py:399-400` — it lives only on
in-memory tailer cursors). The re-trip is caused solely by the stale durable
fields on the `AgentSession` row, which the diff-gated tailer never overwrites.

The net effect: **a single genuinely-hung tool deterministically burns both
recovery attempts in ~30-60s and finalizes as `failed`, regardless of whether
retrying would have helped.** The advisory steering injected on attempt 1 has no
opportunity to influence behavior because the stale wedge signal re-fires before
the resumed model takes its first new turn.

**Desired outcome:**

Recovery from a tool_timeout actually gives the resumed session a fair chance to
make progress: the stale wedge signal is cleared on requeue so the
just-re-detected wedge is not re-counted against the budget before the resumed
model has produced a single new turn. A session should only be finalized as
"never progressed (kind=tool_timeout)" when it genuinely fails to progress
**after** a clean recovery — not because the recovery left the tripwire armed.

## Freshness Check

**Baseline commit:** `beed48c9`
**Issue filed at:** 2026-06-22T15:11:17Z
**Disposition:** Unchanged

**File:line references re-verified (issue cites none; verified the symptom-source path):**
- `agent/session_health.py:1753-1775` — terminal `failed` branch emitting the
  exact "N recovery attempts, never progressed (kind=...)" string — still
  present, verbatim match.
- `agent/session_health.py:236` — `MAX_RECOVERY_ATTEMPTS = 2` — still holds.
- `agent/session_health.py:360-385` — `_check_tool_timeout` wedge detector —
  still holds; depends on `current_tool_name` + `last_tool_use_at`.
- `agent/session_health.py:1811-1895` — recovery requeue branch with tool_timeout
  steering injection (#1738) — confirmed it does NOT clear `current_tool_name` /
  `last_tool_use_at`.
- `agent/granite_container/transcript_tailer.py:109-164` — tailer sets
  `current_tool_name` from a `tool_use` block; diff-gated so it only writes a
  non-None value. On a fresh empty transcript it has nothing to write, so the
  stale `AgentSession` value survives untouched.
- `agent/granite_container/bridge_adapter.py:425-426` — fresh `uuid.uuid4()`
  generated per `run()`; every resume writes to a NEW transcript path. Confirms
  the "re-read the same transcript" premise is false.
- `models/agent_session.py:399-400` — `byte_offset` is explicitly NOT persisted
  onto `AgentSession` (lives only on in-memory tailer cursors). Confirms the
  byte-offset-advance mechanism from the prior draft is not viable.
- `models/agent_session.py:500,505` — `current_tool_name` / `last_tool_use_at`
  are durable `AgentSession` fields; these are the stale signal the recovery
  branch must clear.

**Cited sibling issues/PRs re-checked:**
- #1738 (MCP hang graceful degradation — steering + degraded notice) — merged;
  added the requeue-branch steering injection. Relevant: its remediation never
  fires because attempt 2 short-circuits to `failed`.
- #1711 / #1270 / #1724 — feature ancestors of the tool-timeout machinery; no
  drift to the detector contract.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=<issue-createdAt> -- agent/session_health.py
  agent/sustainability.py` returns empty. HEAD is `beed48c9`.

**Active plans in `docs/plans/` overlapping this area:** None found touching
session-health tool-timeout recovery.

**Notes:** The three affected sessions are no longer present in Redis (expected —
failed sessions are reaped by the hourly cleanup reflection), so the root cause
was confirmed by code reading rather than live telemetry replay.

## Prior Art

- **PR #1738**: "MCP hang graceful degradation — steering injection + degraded
  notice (#1711)" — added the advisory steering injection on the requeue branch
  and the `_deliver_tool_timeout_degraded_notice` on terminal failure. Outcome:
  partial. The steering is correct in intent but never gets a turn to act
  because the stale wedge signal re-trips before the resumed model runs, and the
  second wedge detection exhausts the recovery budget.
- **PR #1279**: "per-tool timeout enforcement with per-tier counters (#1270)" —
  introduced `_check_tool_timeout`, the tier budgets, and the 30s sub-loop.
  Outcome: working as designed for the detection half; the gap is on the
  recovery half (signal not reset on requeue).
- **PR #1728** (#1724): "recover stalled never_started and mid-run-wedge granite
  sessions" — added the mid-run quiescence detector. Adjacent; not the source of
  this loop.
- Closed-issue search for "tool_timeout recovery never progressed" returned no
  prior fix attempts at the recovery-reset layer.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1738 | Injected advisory "skip this tool" steering on the requeue branch; added degraded-notice on terminal failure | Steering is queued but never consumed — the resumed session is re-killed by the stale wedge signal before it takes a new turn. Treated the symptom (tell the model to avoid the tool) without resetting the tripwire (the frozen `current_tool_name`/`last_tool_use_at`) that re-fires the kill. |
| PR #1279 | Built the wedge detector + 30s sub-loop + tier budgets | Detection-only. The recovery path it feeds never reset the detector inputs, so a single wedge is counted twice. |

**Root cause pattern:** The recovery path and the detector share durable mutable
state on the `AgentSession` row (`current_tool_name`, `last_tool_use_at`).
Recovery resets the *process* (kills the subprocess, requeues) but not the
*wedge signal* on the row. The resumed session writes a fresh, empty transcript
(new UUID per `run()`), and the diff-gated tailer never pushes `None` to
overwrite the stale fields — so the detector re-reads the pre-recovery values and
immediately re-counts the same wedge. Every prior fix operated on the model's
behavior (steering, notices) rather than on the detector-input lifecycle.

## Data Flow

1. **Entry point**: A session runs a tool. The granite transcript tailer reads
   the JSONL transcript and sets `current_tool_name` + `last_tool_use_at` on the
   `AgentSession` (`transcript_tailer.py:153-164`).
2. **Wedge occurs**: The tool hangs. The transcript has a `tool_use` block with
   no following `user`/result event → `current_tool_name` stays pinned.
3. **Detection (attempt 1)**: `_agent_session_tool_timeout_check` (sub-loop,
   30s cadence, line 2890) calls `_check_tool_timeout` → age > budget → calls
   `_apply_recovery_transition(reason_kind="tool_timeout")` (line 2893).
4. **Recovery (attempt 1)**: `_apply_recovery_transition` (line 1444) cancels the
   task, kills+confirms the subprocess (line 1643), bumps `recovery_attempts` to
   1 (line 1667), takes the `else` requeue branch (line 1811): injects steering,
   sets `priority=high`, `started_at=None`, transitions to `pending`. **Does NOT
   clear `current_tool_name` / `last_tool_use_at`.**
5. **Resume**: Worker picks up the pending session. `bridge_adapter.run()`
   generates a fresh UUID → brand-new empty transcript (`bridge_adapter.py:425-426`).
   The diff-gated tailer has no `tool_use` block to read, so it never overwrites
   the stale `current_tool_name` / `last_tool_use_at` left on the `AgentSession`
   row — both remain pinned to the pre-recovery wedge values.
6. **Re-detection (attempt 2)**: Next 30s tick → `_check_tool_timeout` reads the
   stale `AgentSession` fields and sees `now - frozen_last_tool_use_at > budget`
   (already expired) → `_apply_recovery_transition` → bumps to 2.
7. **Output**: `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (line 1753) →
   `finalize_session("failed", reason="health check: 2 recovery attempts, never
   progressed (kind=tool_timeout)")`. The `failure-loop-detector` later
   fingerprints this reason and files the issue.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None to public APIs. The recovery requeue branch gains
  a state-reset step on the `AgentSession` instance before `transition_status`.
- **Coupling**: Recovery becomes responsible for clearing the durable wedge
  fields on the `AgentSession` row that the detector reads, instead of leaving
  frozen pre-recovery values for the detector to re-count.
- **Data ownership**: Recovery becomes the owner of resetting the wedge tripwire
  it consumes. No change to who writes the fields during normal operation
  (tailer still owns the steady-state writes).
- **Reversibility**: Trivial — the change is additive field resets plus a guarded
  re-baseline; revert is a clean removal.

## Appetite

**Size:** Small

**Team:** Solo dev, validator

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a targeted bug fix in one recovery branch plus tests. The diagnosis is
complete; the bottleneck is verifying the reset semantics with a regression test.

## Prerequisites

No prerequisites — this work has no external dependencies. It is a self-contained
change to `agent/session_health.py` and its unit tests, runnable against the
existing Redis-backed test harness.

## Solution

### Key Elements

- **Wedge-signal reset on tool_timeout requeue**: When the recovery requeue
  branch fires for `reason_kind == "tool_timeout"`, clear **both** detector
  inputs on the `AgentSession` — `current_tool_name = None` **and**
  `last_tool_use_at = None` — before transitioning to `pending`, so the
  just-killed wedge cannot be re-counted before the resumed model takes a fresh
  turn. Clearing only `current_tool_name` is insufficient: a fresh tool name
  paired with the frozen `last_tool_use_at` could re-trip the budget check, so
  both fields must be cleared in the same block.
- **No re-pin to defeat**: Because the resumed session writes a brand-new empty
  transcript (fresh UUID per `run()`, `bridge_adapter.py:425-426`), the tailer
  has no pre-recovery `tool_use` block to re-pin from. Once the recovery branch
  clears the durable fields, `_check_tool_timeout` returns `None` until the
  resumed model genuinely emits a new `tool_use` — no additional guard or
  byte-offset machinery is required.
- **Preserve genuine fail-fast**: A session that resumes, takes new turns, and
  STILL wedges the same (or a different) tool must still be recovered and
  eventually finalized — the fix must not make genuinely stuck sessions immortal.

### Flow

Tool hangs → sub-loop detects wedge (attempt 1) → recovery kills subprocess +
**clears both wedge fields** (`current_tool_name`, `last_tool_use_at`) + injects
steering + requeues → resumed session writes a fresh empty transcript and starts
with a clean detector baseline → model heeds steering and makes progress
(success) OR genuinely wedges again on a *new* turn → detected as a real second
wedge → recovered/finalized only after real, post-recovery non-progress.

### Technical Approach

The core fix lives in the requeue branch of `_apply_recovery_transition`
(`agent/session_health.py:1811-1895`), specifically alongside the existing
tool_timeout steering injection at lines 1824-1845 (~line 1812).

- **Clear both wedge fields on tool_timeout requeue.** In the
  `reason_kind == "tool_timeout"` block, set `entry.current_tool_name = None`
  **and** `entry.last_tool_use_at = None`. Clearing only `current_tool_name`
  leaves the frozen timestamp live: a fresh tool name emitted by the resumed
  model would pair with the stale `last_tool_use_at` and could re-trip the
  budget immediately. Both fields must be cleared together.
- **Persist the cleared fields.** Add `current_tool_name` and `last_tool_use_at`
  to the `update_fields` of the existing requeue-branch `entry.save(...)` (or a
  dedicated best-effort save) so the cleared values are durable before the
  resumed worker reads them. The save must be best-effort (log on failure, never
  block the transition), mirroring the existing steering-injection try/except.
- **No anti-re-pin guard needed.** The resumed session writes a fresh empty
  transcript (new UUID per `run()`, `bridge_adapter.py:425-426`), so there is no
  dangling `tool_use` block for the tailer to re-pin from, and `byte_offset` is
  not persisted on `AgentSession` (`models/agent_session.py:399-400`). Once the
  durable fields are cleared, `_check_tool_timeout` returns `None` until a
  genuinely new `tool_use` arrives. No byte-offset advance or recovery-baseline
  timestamp is required.
- **Keep the steering injection** (lines 1824-1845) — it is still the right
  remediation for the resumed turn; it just needs a live session to act on.
- **Do not change `MAX_RECOVERY_ATTEMPTS`.** The loop is not caused by too few
  attempts; it is caused by both attempts being consumed by the *same* wedge.
  Once recovery resets the signal, attempt 1 buys a genuine retry and the budget
  is meaningful again. (Bumping the cap without the reset would only extend the
  loop, not fix it.)
- **Counter accuracy**: confirm `recovery_attempts` should still increment on the
  first (now-effective) recovery. It should — the increment correctly records
  that a recovery happened; the bug was the *second* increment firing on a stale
  signal.

## Spike Results

### spike-1 (RESOLVED — original premise refuted): Is the re-trip caused by the tailer re-reading the same transcript and a persisted `byte_offset`?
- **Original assumption**: "On requeue the tailer re-reads the *same* `log_path`
  and re-pins `current_tool_name`/`last_tool_use_at` from a dangling pre-recovery
  `tool_use` block; advancing a persisted `byte_offset` suppresses it."
- **Method**: code-read (driver verification during critique).
- **Finding (refutes the assumption)**: Two facts kill the byte-offset mechanism:
  1. `bridge_adapter.py:425-426` generates a fresh `uuid.uuid4()` per `run()`, so
     every resume writes to a **new, empty** transcript path — there is no "same
     transcript" to re-read and no dangling `tool_use` block to re-pin from.
  2. `byte_offset` is **explicitly not persisted** onto `AgentSession`
     (`models/agent_session.py:399-400`); it lives only on in-memory tailer
     cursors. There is nothing to advance.
  The actual re-trip cause is the **stale durable fields** (`current_tool_name`,
  `last_tool_use_at`) left on the `AgentSession` row. The diff-gated tailer only
  writes non-None values, so on a fresh empty transcript it never overwrites the
  stale pre-recovery values — and `_check_tool_timeout` re-counts them.
- **Confidence**: high (both facts verified by direct file reads).
- **Impact on plan**: The byte-offset / recovery-baseline-timestamp mechanisms
  are struck. The fix is simply to clear both durable wedge fields in the
  tool_timeout requeue branch (~`session_health.py:1812`). No spike work remains
  open for build.

### spike-2: Confirm the second recovery fires within one sub-loop tick (no genuine progress window)
- **Assumption**: "The second wedge detection happens before the resumed model
  takes any new turn, so the budget is exhausted by a single wedge."
- **Method**: code-read.
- **Finding**: The sub-loop runs every 30s (`TOOL_TIMEOUT_LOOP_INTERVAL = 30`,
  line 307). On resume, `last_tool_use_at` is the frozen wedge time, already
  older than even the 300s default budget by the time a multi-minute hang was
  first detected. So `_check_tool_timeout` returns a hit on the **very first**
  post-resume tick — before the model's first new turn could plausibly
  complete. Confirms the loop is deterministic, not probabilistic.
- **Confidence**: high.
- **Impact on plan**: Justifies that the fix MUST reset the signal (not just
  raise the cap or widen budgets).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The steering injection block (lines 1824-1845) already wraps in
  `try/except` and logs a warning — the new reset logic must follow the same
  best-effort pattern (a Redis save failure must not block the terminal/requeue
  transition). Add a test asserting the requeue still proceeds (and logs) when
  the reset save raises.
- [ ] No new `except Exception: pass` blocks introduced; any new except must log.

### Empty/Invalid Input Handling
- [ ] `_check_tool_timeout` already returns `None` for empty/None
  `current_tool_name` (lines 372-374) — add/confirm a unit test that a session
  with `current_tool_name=None` post-recovery is NOT re-detected as wedged.
- [ ] Test a session with `last_tool_use_at=None` post-recovery (legacy/cleared)
  is treated as no-wedge.

### Error State Rendering
- [ ] The terminal `failed` branch still delivers
  `_deliver_tool_timeout_degraded_notice` when a *genuine* post-recovery wedge
  exhausts the budget — verify the user-facing degraded notice still fires on the
  true-failure path (regression guard so the fix doesn't suppress legitimate
  notices).

## Test Impact

- [ ] `tests/unit/test_sustainability.py::TestFailureLoopDetector::test_reason_only_session_produces_real_fingerprint` — UPDATE-NONE (KEEP): this test exercises the fingerprint of the *symptom* reason string and is unaffected by the recovery-reset fix; it stays as a guard that the detector still fingerprints the reason correctly. Verify it still passes.
- [ ] `tests/unit/` session-health tool-timeout tests (the suite covering `_check_tool_timeout`, `_apply_recovery_transition`, and the tool_timeout sub-loop) — UPDATE: add a regression case asserting that after a tool_timeout recovery the wedge signal is reset so a second recovery is NOT triggered by the same stale `tool_use`. Locate via `grep -rn "_check_tool_timeout\|tool_timeout\|_apply_recovery_transition" tests/`.
- [ ] If a test currently asserts `current_tool_name` or `last_tool_use_at` survives a recovery requeue, that assertion is now wrong — REPLACE with an assertion that both are cleared on the tool_timeout requeue branch.

## Rabbit Holes

- **Rewriting the transcript tailer's clearing semantics globally.** Tempting to
  make the tailer "smart" about hung tools or push `None` over stale values, but
  that risks the false-positive behavior it already documents (lines 116-125).
  Keep the change scoped to the recovery branch clearing the durable
  `AgentSession` fields.
- **Reviving the byte-offset / transcript-replay mechanism.** It was refuted
  (fresh UUID per resume; `byte_offset` not persisted). Do not reintroduce it.
- **Increasing `MAX_RECOVERY_ATTEMPTS` or widening tier budgets.** These look
  like fixes but only lengthen the loop; the root cause is double-counting one
  wedge. Explicitly out of scope as the primary fix (see No-Gos).
- **Building a general "tool replay suppression" framework.** A single targeted
  reset is sufficient; do not generalize into a tool-blocklist subsystem.
- **Reproducing the exact three dead sessions.** They are reaped from Redis.
  Reproduce the mechanism with a synthetic session fixture, not the originals.

## Risks

### Risk 1: Clearing the wedge signal masks a genuinely stuck session (immortal session)
**Impact:** A session whose tool is truly unrecoverable could loop
recover→clear→re-wedge indefinitely if the clear lets it dodge the budget every
time.
**Mitigation:** `recovery_attempts` still increments on each genuine recovery and
is NOT reset by the wedge-signal clear. The cap (=2) still finalizes a session
that wedges across *distinct* post-recovery turns. The fix only prevents the
*same* wedge from being counted twice within one recovery — it does not reset the
attempt counter. Add a regression test proving a session that wedges a *second*,
genuinely-new tool after a clean recovery is still finalized as failed.

### Risk 2: Clearing `last_tool_use_at` masks a tool that was about to complete
**Impact:** If the tool was genuinely about to return, clearing the wedge fields
could discard the only signal that work was in flight.
**Mitigation:** Recovery only fires after the subprocess is confirmed dead
(line 1643) — there will be no further events for the killed turn. The resumed
session starts a brand-new transcript (fresh UUID), so the in-flight tool of the
dead subprocess is irrelevant to the resumed run. Clearing the durable fields is
therefore safe: there is no live tool whose completion signal could be lost.

## Race Conditions

### Race 1: Cleared fields must persist before the resumed worker reads them
**Location:** `agent/session_health.py:1811-1895` (recovery reset) and the
resumed worker / sub-loop that reads `current_tool_name` / `last_tool_use_at`.
**Trigger:** The recovery branch clears the durable wedge fields but the resumed
worker (or the next sub-loop tick) reads the `AgentSession` before the cleared
values are persisted.
**Data prerequisite:** `current_tool_name = None` and `last_tool_use_at = None`
must be saved to Redis before `transition_status(..., "pending")` returns.
**State prerequisite:** The requeue path already saves the entry and transitions
to `pending` before `_ensure_worker` (lines 1867-1892); adding the two fields to
that save's `update_fields` makes the cleared values durable in the same write,
so they are visible to the resumed worker and the next sub-loop tick.
**Mitigation:** Persist both cleared fields in the existing requeue-branch save
(before the `pending` transition). There is no tailer re-pin race to defeat —
the resumed session writes a fresh empty transcript with no `tool_use` block to
re-pin from.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. The fix,
  its regression tests, and the doc update are all completable within this plan.

Explicitly *not* the chosen mechanism (anti-criteria, not deferrals):
- Raising `MAX_RECOVERY_ATTEMPTS` is NOT the fix — see Verification anti-criterion
  asserting the constant is unchanged.
- Widening the tier budget constants is NOT the fix — see Verification
  anti-criterion asserting the budget defaults are unchanged.

## Update System

No update system changes required — this is a purely internal bug fix in
`agent/session_health.py`. No new dependencies, config files, or migration steps;
nothing to propagate via the `/update` skill beyond the normal `git pull`.

## Agent Integration

No agent integration required — this is a worker-internal change to the
session-health recovery loop. It does not add or modify any tool, MCP server, or
bridge entry point. The behavior is observed by the worker's background
health-check loops, not invoked by the agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` doc covering session health / tool-timeout recovery
  (locate the existing one via `grep -rl "tool_timeout\|session-health\|recovery_attempts" docs/features/`; likely the session-health or sustainability feature doc) to describe the wedge-signal reset on recovery and why it prevents the double-count loop.
- [ ] If no such feature doc exists, add a short subsection to the most relevant
  existing health/sustainability doc rather than creating a new file.

### Inline Documentation
- [ ] Add a code comment at the recovery reset explaining the double-count root
  cause (mirroring the style of the existing #1711/#1537 comments in that file)
  so the next reader understands why the signal is reset here.

### External Documentation Site
- [ ] Not applicable — repo has no external docs site for this area.

## Success Criteria

- [ ] 1. After a tool_timeout recovery, **both** `current_tool_name` and
  `last_tool_use_at` are reset to `None` so the stale wedge values do NOT trigger
  a second recovery within the next sub-loop tick.
- [ ] 2. A session that wedges, recovers cleanly, then makes genuine progress is
  NOT finalized as failed (no false "never progressed").
- [ ] 3. A session that wedges a genuinely-new tool *after* a clean recovery is
  still recovered and eventually finalized — fail-fast preserved.
- [ ] 4. Regression test added reproducing the double-count loop and asserting it
  no longer occurs.
- [ ] 5. On a genuine post-recovery wedge that exhausts the budget, the
  user-facing degraded-service notice (`_deliver_tool_timeout_degraded_notice`)
  still fires — sessions no longer silently die (the original reported symptom);
  the fix must not suppress the notice on the true-failure path. A test asserts
  the notice is delivered on terminal failure.
- [ ] 6. `MAX_RECOVERY_ATTEMPTS` and the tier budget constants are unchanged.
- [ ] 7. Tests pass (`/do-test`)
- [ ] 8. Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly.

### Team Members

- **Builder (recovery-reset)**
  - Name: recovery-reset-builder
  - Role: Implement the wedge-signal reset on the tool_timeout requeue branch —
    clear both `current_tool_name` and `last_tool_use_at` on the `AgentSession`
    and persist them — plus inline docs.
  - Agent Type: debugging-specialist
  - Resume: true

- **Test Engineer (recovery-reset)**
  - Name: recovery-reset-tester
  - Role: Write the regression test reproducing the double-count loop and the
    fail-fast guard test; update any existing assertion that expected the signal
    to survive recovery.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (recovery-reset)**
  - Name: recovery-reset-validator
  - Role: Verify all success criteria, run the Verification table, confirm the
    anti-criteria (constants unchanged).
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: recovery-reset-doc
  - Role: Update the session-health/sustainability feature doc.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. (RESOLVED — no work) byte_offset investigation
- **Task ID**: build-investigate-offset
- **Status**: RESOLVED during critique — no build work required.
- The byte-offset / transcript-replay mechanism was refuted: each resume gets a
  fresh UUID/empty transcript (`bridge_adapter.py:425-426`) and `byte_offset` is
  not persisted on `AgentSession` (`models/agent_session.py:399-400`). The fix is
  simply clearing the durable wedge fields (Task 2). This entry is retained as a
  resolved note so the prior plan's task numbering stays traceable.

### 2. Implement the wedge-signal reset on tool_timeout requeue
- **Task ID**: build-recovery-reset
- **Depends On**: none
- **Validates**: tests/unit session-health tool-timeout suite (see Test Impact)
- **Informed By**: spike-1 (resolved), spike-2
- **Assigned To**: recovery-reset-builder
- **Agent Type**: debugging-specialist
- **Parallel**: false
- In `_apply_recovery_transition` requeue branch (`agent/session_health.py`
  ~1811-1895, alongside the steering injection at ~line 1812/1824), for
  `reason_kind == "tool_timeout"`, set **both** `entry.current_tool_name = None`
  and `entry.last_tool_use_at = None` before `transition_status(..., "pending")`.
- Add both fields to the requeue-branch `entry.save(update_fields=[...])` so the
  cleared values persist before the resumed worker reads them. Keep the save
  best-effort (log on failure, never block the transition), mirroring the
  existing steering-injection try/except.
- Add an inline comment explaining the double-count root cause (stale durable
  fields re-counted because the fresh-transcript tailer never overwrites them).
- Do NOT change `MAX_RECOVERY_ATTEMPTS` or the tier budget constants.

### 3. Write regression + fail-fast tests
- **Task ID**: build-tests
- **Depends On**: build-recovery-reset
- **Validates**: the new tests themselves
- **Assigned To**: recovery-reset-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a unit test that simulates a tool_timeout recovery and asserts **both**
  `current_tool_name` and `last_tool_use_at` are reset to `None` so a second
  sub-loop tick does NOT re-trigger recovery from the stale wedge values.
- Add a fail-fast test: a session that wedges a genuinely-new tool after a clean
  recovery is still finalized as failed at the cap.
- Add a best-effort test: requeue still proceeds (and logs) if the reset save
  raises.
- Add a degraded-notice regression test: on a genuine post-recovery wedge that
  exhausts the budget, `_deliver_tool_timeout_degraded_notice` still fires (the
  original symptom was sessions silently dying — the fix must not suppress the
  user-facing notice on true failure). Covers Success Criterion 5.
- Update/replace any existing assertion that expected `current_tool_name` or
  `last_tool_use_at` to survive a tool_timeout requeue.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-recovery-reset
- **Assigned To**: recovery-reset-doc
- **Agent Type**: documentarian
- **Parallel**: true
- Update the relevant session-health/sustainability feature doc with the
  wedge-signal-reset behavior and the double-count root cause.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: recovery-reset-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands.
- Confirm all success criteria, including the anti-criteria (constants
  unchanged).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q -k "tool_timeout or session_health or sustainability"` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py` | exit code 0 |
| MAX_RECOVERY_ATTEMPTS unchanged (anti-criterion) | `grep -n "MAX_RECOVERY_ATTEMPTS = 2" agent/session_health.py` | output contains MAX_RECOVERY_ATTEMPTS = 2 |
| Tier budget defaults unchanged (anti-criterion) | `grep -c "TOOL_TIMEOUT_INTERNAL_SEC\", 30)\|TOOL_TIMEOUT_MCP_SEC\", 120)\|TOOL_TIMEOUT_DEFAULT_SEC\", 300)" agent/session_health.py` | output contains 3 |
| Wedge reset (tool name) present on requeue | `grep -n "current_tool_name = None" agent/session_health.py` | exit code 0 |
| Wedge reset (timestamp) present on requeue | `grep -n "last_tool_use_at = None" agent/session_health.py` | exit code 0 |
| Degraded notice still wired on terminal failure | `grep -n "_deliver_tool_timeout_degraded_notice" agent/session_health.py` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Open Questions

1. **Degraded-notice on the now-effective first recovery:** Currently the
   degraded notice fires only on terminal (`failed`/abandoned) branches. Should
   the resumed session, after a clean recovery, also surface a lightweight "I hit
   a slow tool and retried" signal, or stay silent until genuine failure? Default
   assumption: stay silent on successful recovery (no user-facing noise) and keep
   the degraded notice on the genuine-failure path only (see Success Criterion 5);
   confirm this is the desired UX.

## Critique Results

[To be filled by /do-plan-critique]
