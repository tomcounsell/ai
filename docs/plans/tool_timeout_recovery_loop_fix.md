---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-23
tracking: https://github.com/tomcounsell/ai/issues/1762
last_comment_id:
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
recovery **does not clear the wedge signal**:

- `current_tool_name` and `last_tool_use_at` are derived by the transcript
  tailer (`agent/granite_container/transcript_tailer.py:153-164`) from the
  session's JSONL transcript. A genuinely hung tool leaves a `tool_use` block
  with **no following `user`/result event**, so `current_tool_name` stays pinned
  to the wedged tool name and `last_tool_use_at` stays frozen at the wedge time.
- The recovery requeue branch (`agent/session_health.py:1811-1895`) injects an
  advisory steering message ("don't call this tool again", PR #1738) but **never
  clears `current_tool_name` / `last_tool_use_at`** on the `AgentSession`.
- On requeue the session resumes against the **same `log_path` / transcript**.
  The tailer re-reads the same dangling `tool_use` block, re-pins
  `current_tool_name`, and `_check_tool_timeout` (line 360) immediately sees an
  already-expired age (`now - frozen last_tool_use_at > budget`).
- Within one 30s sub-loop tick the wedge is re-detected → recovery attempt 2.
- At attempt 2, `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (=2, line 236/1753)
  gates straight to the `failed` terminal branch — no second requeue, no chance
  for the steering to take effect. The session dies with "never progressed".

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
  `current_tool_name` from a `tool_use` block and only clears it on a following
  `user` event — confirms a dangling `tool_use` re-pins the name on re-read.

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

**Root cause pattern:** The recovery path and the detector share mutable state
(`current_tool_name`, `last_tool_use_at`) sourced from a transcript that is NOT
truncated on recovery. Recovery resets the *process* (kills the subprocess,
requeues) but not the *wedge signal*, so the detector immediately re-counts the
same wedge. Every prior fix operated on the model's behavior (steering, notices)
rather than on the detector-input lifecycle.

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
5. **Resume**: Worker picks up the pending session against the same `log_path`.
   The tailer re-reads the same dangling `tool_use` → re-pins
   `current_tool_name`; `last_tool_use_at` remains the frozen wedge time.
6. **Re-detection (attempt 2)**: Next 30s tick → `_check_tool_timeout` sees
   `now - frozen_last_tool_use_at > budget` (already expired) →
   `_apply_recovery_transition` → bumps to 2.
7. **Output**: `recovery_attempts >= MAX_RECOVERY_ATTEMPTS` (line 1753) →
   `finalize_session("failed", reason="health check: 2 recovery attempts, never
   progressed (kind=tool_timeout)")`. The `failure-loop-detector` later
   fingerprints this reason and files the issue.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None to public APIs. The recovery requeue branch gains
  a state-reset step on the `AgentSession` instance before `transition_status`.
- **Coupling**: Slightly *decreases* coupling between the recovery path and the
  transcript-tailer-derived wedge signal by making recovery explicitly
  re-baseline the signal instead of inheriting frozen values.
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
  branch fires for `reason_kind == "tool_timeout"`, clear/re-baseline the
  detector inputs (`current_tool_name = None`, and either clear or advance
  `last_tool_use_at`) on the `AgentSession` before transitioning to `pending`,
  so the just-killed wedge cannot be re-counted before the resumed model takes a
  fresh turn.
- **Grace window for re-detection**: Ensure the resumed session gets at least
  one full turn (or one full budget window) of headroom after recovery before
  `_check_tool_timeout` can re-trip — the cleared `current_tool_name` already
  achieves this (a `None` name returns `None` from `_check_tool_timeout`), but
  the tailer will re-pin it on its next read of the unchanged transcript.
  Therefore the reset must be paired with a guard so a re-pinned-from-stale-
  transcript value does not immediately re-expire (see Technical Approach).
- **Preserve genuine fail-fast**: A session that resumes, takes new turns, and
  STILL wedges the same (or a different) tool must still be recovered and
  eventually finalized — the fix must not make genuinely stuck sessions immortal.

### Flow

Tool hangs → sub-loop detects wedge (attempt 1) → recovery kills subprocess +
**clears wedge signal** + injects steering + requeues → resumed session gets a
clean detector baseline → model heeds steering and makes progress (success) OR
genuinely wedges again on a *new* turn → detected as a real second wedge →
recovered/finalized only after real, post-recovery non-progress.

### Technical Approach

The core fix lives in the requeue branch of `_apply_recovery_transition`
(`agent/session_health.py:1811-1895`), specifically alongside the existing
tool_timeout steering injection at lines 1824-1845.

- **Clear the wedge tripwire on tool_timeout requeue.** Set
  `entry.current_tool_name = None` so `_check_tool_timeout` returns `None` for
  the resumed session until a genuinely new `tool_use` appears.
- **Defeat tailer re-pinning from the stale transcript.** The tailer reads the
  same `log_path` on resume and will re-set `current_tool_name` /
  `last_tool_use_at` from the dangling pre-recovery `tool_use` block. Two
  candidate mechanisms — the build step will pick whichever the tailer contract
  supports cleanly:
  1. **Byte-offset advance**: the tailer tracks `byte_offset`
     (`transcript_tailer.py:102`). Advancing the persisted offset past the
     pre-recovery transcript means the tailer will not re-emit the stale
     `tool_use` block on resume. This is the preferred mechanism — it addresses
     the source rather than papering over the symptom on the `AgentSession`.
  2. **Recovery baseline timestamp**: persist a `recovery_baseline_at` (or reuse
     `started_at`/`scheduled_at` which the requeue already nulls/sets) and have
     `_check_tool_timeout` ignore a `last_tool_use_at` that predates the most
     recent recovery transition. This guards the detector regardless of what the
     tailer re-pins.
  The spike below resolves which mechanism is correct; the byte-offset approach
  is favored because it fixes the data source.
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

### spike-1: Does the transcript tailer re-pin `current_tool_name` from a pre-recovery dangling `tool_use` on resume, and can `byte_offset` advance suppress it?
- **Assumption**: "On requeue, the tailer re-reads the same `log_path` and
  re-sets `current_tool_name`/`last_tool_use_at` from the dangling pre-recovery
  `tool_use` block; advancing the persisted `byte_offset` past that block
  prevents re-emission."
- **Method**: code-read (confirmed during planning) — to be elevated to a focused
  read of how `byte_offset` is persisted/restored across a `pending→running`
  resume during build.
- **Finding**: `transcript_tailer.py:153-164` sets `current_tool_name` from any
  `tool_use` block and only clears it on a following `user` event
  (lines 109-126). A genuinely hung tool has no following `user` event, so the
  name is re-pinned on every re-read. `byte_offset` is carried on
  `TranscriptTelemetry` (line 102). **Open for build**: confirm `byte_offset` is
  persisted on the `AgentSession` and restored (not reset to 0) on resume, which
  determines whether mechanism (1) is viable standalone or must be paired with
  mechanism (2).
- **Confidence**: high (re-pin behavior); medium (byte_offset persistence
  across resume).
- **Impact on plan**: Selects the Technical Approach mechanism. If `byte_offset`
  is NOT durably restored on resume, fall back to the recovery-baseline-timestamp
  guard in `_check_tool_timeout`.

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
- [ ] If a test currently asserts `current_tool_name` survives a recovery requeue, that assertion is now wrong — REPLACE with an assertion that it is cleared on the tool_timeout requeue branch.

## Rabbit Holes

- **Rewriting the transcript tailer's clearing semantics globally.** Tempting to
  make the tailer "smart" about hung tools, but that risks the false-positive
  fix it already documents (lines 116-125). Keep the change scoped to the
  recovery path / its byte-offset handoff.
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

### Risk 2: byte_offset advance suppresses a legitimately-pending tool result
**Impact:** If a tool was about to complete (result event just past the offset),
advancing the offset could drop a real result.
**Mitigation:** Recovery only fires after the subprocess is confirmed dead
(line 1643) — there will be no further events for the killed turn, so advancing
past the dangling `tool_use` cannot drop a real subsequent result for that turn.
Prefer the recovery-baseline-timestamp guard (mechanism 2) if spike-1 shows
byte_offset is reset to 0 on resume.

## Race Conditions

### Race 1: Tailer re-pin vs. recovery reset ordering
**Location:** `agent/session_health.py:1811-1895` (recovery reset) and
`agent/granite_container/transcript_tailer.py:153-164` (tailer re-pin) on the
resumed worker.
**Trigger:** Recovery clears `current_tool_name` on the `AgentSession`, then the
resumed worker's tailer reads the unchanged transcript and re-pins the stale
name before the model takes a new turn.
**Data prerequisite:** The detector input (`current_tool_name`/`last_tool_use_at`
or `byte_offset`) must be re-baselined such that the stale `tool_use` is not
re-emitted/re-counted.
**State prerequisite:** The recovery reset must persist before the resumed worker
starts tailing — the requeue path already saves the entry and transitions to
`pending` before `_ensure_worker` (lines 1867-1892), so the persisted reset is
visible to the resumed worker.
**Mitigation:** Use the byte-offset advance (mechanism 1) so the source never
re-emits the stale block, OR the recovery-baseline-timestamp guard (mechanism 2)
so the detector ignores a `last_tool_use_at` predating the recovery — either is
order-independent because it does not rely on the tailer losing the race.

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

- [ ] After a tool_timeout recovery, `current_tool_name` (and/or the detector
  baseline) is reset so the same stale `tool_use` does NOT trigger a second
  recovery within the next sub-loop tick.
- [ ] A session that wedges, recovers cleanly, then makes genuine progress is NOT
  finalized as failed (no false "never progressed").
- [ ] A session that wedges a genuinely-new tool *after* a clean recovery is
  still recovered and eventually finalized — fail-fast preserved.
- [ ] Regression test added reproducing the double-count loop and asserting it no
  longer occurs.
- [ ] `MAX_RECOVERY_ATTEMPTS` and the tier budget constants are unchanged.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.
The lead NEVER builds directly.

### Team Members

- **Builder (recovery-reset)**
  - Name: recovery-reset-builder
  - Role: Implement the wedge-signal reset on the tool_timeout requeue branch and
    the chosen anti-re-pin mechanism (byte-offset advance or baseline-timestamp
    guard), plus inline docs.
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

### 1. Confirm byte_offset persistence across resume (resolve spike-1 open item)
- **Task ID**: build-investigate-offset
- **Depends On**: none
- **Validates**: read-only investigation; no test
- **Informed By**: spike-1 (re-pin confirmed; byte_offset persistence open)
- **Assigned To**: recovery-reset-builder
- **Agent Type**: debugging-specialist
- **Parallel**: false
- Read how `byte_offset` is persisted on the `AgentSession` and whether it is
  restored (not reset to 0) when a `pending` session resumes to `running`.
- Decide mechanism (1) byte-offset advance vs. (2) recovery-baseline-timestamp
  guard in `_check_tool_timeout`. Record the decision in the PR description.

### 2. Implement the wedge-signal reset on tool_timeout requeue
- **Task ID**: build-recovery-reset
- **Depends On**: build-investigate-offset
- **Validates**: tests/unit session-health tool-timeout suite (see Test Impact)
- **Informed By**: spike-1, spike-2
- **Assigned To**: recovery-reset-builder
- **Agent Type**: debugging-specialist
- **Parallel**: false
- In `_apply_recovery_transition` requeue branch (`agent/session_health.py`
  ~1811-1895), for `reason_kind == "tool_timeout"`, clear `current_tool_name`
  and apply the chosen anti-re-pin mechanism before `transition_status(...,
  "pending")`.
- Wrap reset persistence best-effort (log on failure, never block the
  transition), mirroring the existing steering-injection try/except.
- Add an inline comment explaining the double-count root cause.
- Do NOT change `MAX_RECOVERY_ATTEMPTS` or the tier budget constants.

### 3. Write regression + fail-fast tests
- **Task ID**: build-tests
- **Depends On**: build-recovery-reset
- **Validates**: the new tests themselves
- **Assigned To**: recovery-reset-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a unit test that simulates a tool_timeout recovery and asserts the wedge
  signal is reset so a second sub-loop tick does NOT re-trigger recovery from the
  same stale `tool_use`.
- Add a fail-fast test: a session that wedges a genuinely-new tool after a clean
  recovery is still finalized as failed at the cap.
- Add a best-effort test: requeue still proceeds (and logs) if the reset save
  raises.
- Update/replace any existing assertion that expected `current_tool_name` to
  survive a tool_timeout requeue.

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
| Wedge reset present on requeue | `grep -n "current_tool_name = None" agent/session_health.py` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Open Questions

1. **Anti-re-pin mechanism choice (spike-1 open item):** Is `byte_offset`
   durably persisted on the `AgentSession` and restored on resume? If yes,
   advancing it past the dangling `tool_use` (mechanism 1) is the cleanest fix.
   If no, we use the recovery-baseline-timestamp guard in `_check_tool_timeout`
   (mechanism 2). This is resolvable by the builder in task 1 and does not need
   human input — flagged only so the critique step can weigh in on the preferred
   mechanism.
2. **Degraded-notice on the now-effective first recovery:** Currently the
   degraded notice fires only on terminal (`failed`/abandoned) branches. Should
   the resumed session, after a clean recovery, also surface a lightweight "I hit
   a slow tool and retried" signal, or stay silent until genuine failure? Default
   assumption: stay silent on successful recovery (no user-facing noise);
   confirm this is the desired UX.

## Critique Results

[To be filled by /do-plan-critique]
