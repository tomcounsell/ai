---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2149
last_comment_id:
---

# fast-oneshot-reap: verify orphanhood before killing `claude -p` one-shots

## Problem

The session-health loop runs two reapers that SIGTERM/SIGKILL `claude -p --print`
one-shot processes older than 600s (`ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS`) on
the assumption that "no legitimate `--print` one-shot lives this long" (issue
#1632). That assumption is now false: the headless session runner's PM turns ARE
`claude -p` invocations, and long turns legitimately run 14-19 minutes
(`docs/features/headless-session-runner.md`, `agent/session_runner/harness/claude.py`).

**Current behavior (observed 2026-07-17 14:33:19 UTC):**
`[fast-oneshot-reap] SIGTERM'd stale --print one-shot PID 74819` killed the
**live harness of actively-running session `0_1784286827622`** — spawned
14:19:06 by the current worker, session status `running`, owning worker alive and
healthy, one stage past CRITIQUE. Two minutes later a worker restart's
dead-worker sweep (#1767) correctly found `claude_pid=74819 not alive` and swept
the session to `killed`. The reap on age alone was the root cause; the sweep just
finished the job on corrupted input.

Both reap paths fire on this false positive:
- `_fast_reap_stale_print_oneshots()` (`agent/session_health.py:5129`) — the
  fast-cadence loop reaper. No ownership check at all: PPID==1 + age > 600s → kill.
- `_reap_orphan_session_processes()` (`agent/session_health.py:4913`), the
  `is_stale_oneshot` branch (lines 5059-5068) — **deliberately bypasses** the
  per-PID heartbeat/ownership gate that governs every other signature match.

An earlier fire (13:52:47, PID 53278) targeted a genuinely orphaned harness whose
worker had died — that cleanup is legitimate and must be preserved.

**Desired outcome:** a `claude -p` one-shot is killed only when the reaper can
prove orphanhood — its PID is not registered as any live session's harness
(`find_by_claude_pid` returns None), OR the owning session is not `running`
(terminal/other), OR the owning session's liveness signal is stale (worker dead).
Age alone becomes a trigger to *investigate ownership*, never to kill. Genuinely
orphaned one-shots (the #1632 rogue-subagent cascade) still die fast because they
have no owning session.

## Freshness Check

**Baseline commit:** 7ae53282
**Issue filed at:** 2026-07-17T14:45:08Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:5129` — `_fast_reap_stale_print_oneshots`, no ownership
  gate (`ppid != 1: continue` then straight to TERM/KILL) — still holds.
- `agent/session_health.py:5059-5068` — `is_stale_oneshot` branch in
  `_reap_orphan_session_processes` explicitly bypasses the heartbeat gate
  ("The heartbeat gate is intentionally bypassed") — still holds.
- `agent/session_health.py:108` — `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS = 600` — still holds.
- `agent/session_runner/runner.py:629` — `claude_pid = pid` set on the AgentSession
  when the PM turn spawns (the PID the reaper would resolve via
  `find_by_claude_pid`) — confirmed present.
- `agent/session_health.py:4868` — `_session_is_alive(session)` (terminal → False;
  heartbeat fresh < 30 min → True) — the existing orphanhood proxy, confirmed present.

**Cited sibling issues/PRs re-checked:**
- #1632 — the origin of the fast-kill rule (rogue bare `claude --print` subagent
  cascade, ~4/min, ~250 MB each). Those one-shots have **no** owning session, so
  `find_by_claude_pid` returns None and they remain reapable under the fix.
- #1767 — dead-worker sweep; verifies PID dead via `os.kill(pid, 0)`, does not kill
  on age. Correct; the swept-to-killed step was a symptom, not the defect. Out of scope.
- #1271 — cross-process orphan reaper family; introduced the heartbeat gate the
  fast-kill branch bypasses. In scope for the audit.
- #2145 — sibling age-based false positive (per-tool wedge timeout vs declared Bash
  timeout) from the same afternoon. Separate rule, separate issue — audited, not fixed here.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-17T14:45:08Z -- agent/session_health.py agent/session_runner/harness/claude.py` empty).

**Active plans in `docs/plans/` overlapping this area:** none. `session-recovery-observation-audit.md`
and `resilience-simplification-three-tier.md` touch session recovery broadly but do
not modify the one-shot reap signatures. No coordination blocker.

**Notes:** No drift. The issue's claims hold verbatim against baseline 7ae53282.

## Prior Art

- **Issue #1632**: origin of `_is_stale_print_oneshot` + both reap paths. Solved a
  real memory cascade of orphaned rogue one-shots. Its "no legitimate one-shot lives
  >600s" premise was correct *for bare subagent one-shots* but was invalidated by the
  headless-runner cutover, which made PM turns long-running `claude -p` processes.
  The fix here narrows #1632's kill criterion (add ownership proof) without removing
  its cleanup power.
- **Issue #1271**: introduced `_session_is_alive` / `find_by_claude_pid` per-PID
  heartbeat gate for the general is_claude/is_mcp signatures. This is exactly the
  ownership machinery the one-shot branch bypasses — the fix reuses it rather than
  inventing a parallel mechanism.
- No closed issue or merged PR previously attempted to fix this specific
  live-harness false positive (searched `gh issue list --state closed`, `gh pr list --state merged`).

## Data Flow

1. **Spawn**: worker's headless session runner spawns a PM turn as a `claude -p`
   subprocess (`agent/session_runner/harness/claude.py`); `runner.py:629` writes
   `claude_pid = pid` and `pm_pid = pid` onto the owning `AgentSession`, and the
   `_heartbeat_loop` refreshes `last_heartbeat_at` every 60s while it runs.
2. **Reap tick (fast)**: `_agent_session_health_loop` calls
   `_fast_reap_stale_print_oneshots()` every `AGENT_SESSION_HEALTH_CHECK_INTERVAL`
   (300s). It iterates the process table for PPID==1 `claude --print` procs older
   than 600s and TERM/KILLs them — **without consulting `claude_pid`**.
3. **Reap tick (hourly)**: `_reap_orphan_session_processes()` computes
   `session = find_by_claude_pid(pid)` for every matched proc, but its
   `is_stale_oneshot` branch ignores that lookup and kills anyway.
4. **Consequence**: a live PM turn's PID (registered as `claude_pid`, session
   `running`, heartbeat fresh) is killed; the next worker-restart dead-worker sweep
   (#1767) reads the now-dead `claude_pid` and finalizes the session to `killed`.

The fix inserts an ownership check between the age match (step 2/3) and the kill:
resolve `find_by_claude_pid(pid)` and, when it returns a live-owning session, skip.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1632 | Added `_is_stale_print_oneshot` age gate + two reap paths; deliberately bypassed the heartbeat gate so "an alive owning session does not legitimize a stuck one-shot child" | Correct for bare subagent one-shots (no owning session), but the headless-runner cutover made PM turns long-lived `claude -p` processes *with* a live owning session — the bypass now kills exactly the processes it should protect. |

**Root cause pattern:** an age threshold was treated as *sufficient* evidence of
orphanhood. It is only *suggestive*. Orphanhood must be proven by ownership state
(no owner / terminal owner / dead worker), not inferred from duration.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The change is a localized ownership gate reusing existing helpers
(`find_by_claude_pid`, `_session_is_alive`) plus a focused test rewrite. The
bottleneck is getting the "still kill genuine orphans, never kill live harnesses"
boundary exactly right in tests, not coding volume.

## Prerequisites

No prerequisites — this work has no external dependencies. Runs entirely against
existing in-process helpers and the test suite.

## Solution

### Key Elements

- **Ownership gate helper** — a single predicate,
  `_oneshot_owner_is_live(pid)`, that returns True when the PID is registered as a
  live session's harness. It resolves `AgentSession.find_by_claude_pid(pid)` and
  returns True only when a session is found AND `_session_is_alive(session)` (not
  terminal, heartbeat fresh). Returns False (→ reapable) for: no owning session,
  terminal owner, or stale heartbeat (dead/hung worker). Fail-open toward
  *protecting* on transient lookup errors is rejected — see Risk 2; the helper
  returns False (reapable) on lookup exception to preserve #1632 cleanup, matching
  the existing `_session_is_alive` conservative-False contract.
- **Fast reaper gate** — `_fast_reap_stale_print_oneshots` calls the helper after
  the age match; if the owner is live, `continue` (no TERM, no staging), leaving
  the live harness untouched.
- **Hourly reaper gate** — replace the unconditional `is_stale_oneshot` fast-kill
  in `_reap_orphan_session_processes` with the same live-owner check. The general
  is_claude/is_mcp branch already has ownership gating; the one-shot branch is
  brought into line rather than staying an exception.
- **Preserved fast death for orphans** — the #1632 rogue-subagent one-shots have
  no `claude_pid` mapping (they were never a tracked session's harness), so
  `find_by_claude_pid` returns None → helper returns False → still reaped on the
  same fast cadence. No regression to the memory-cascade defense.

### Flow

Reap tick → match stale `--print` one-shot (PPID==1, age>600s) → **resolve owner
via `find_by_claude_pid`** → owner live (`running` + fresh heartbeat)? → **skip
(protect live harness)** ; else (no owner / terminal / stale) → TERM→KILL as today.

### Technical Approach

- Add `_oneshot_owner_is_live(pid: int | None) -> bool` near `_session_is_alive`
  in `agent/session_health.py`. Body: `session = AgentSession.find_by_claude_pid(pid)`;
  `return bool(session is not None and _session_is_alive(session))`, wrapped so any
  exception returns False (reapable — conservative toward cleanup, never raises).
- In `_fast_reap_stale_print_oneshots`, after `_is_stale_print_oneshot(...)` passes
  and before the TERM/KILL escalation, add `if _oneshot_owner_is_live(pid): continue`.
- In `_reap_orphan_session_processes`, the `is_stale_oneshot:` branch already has
  `session` resolved above it (line 5044). Replace the "bypass heartbeat gate,
  always kill" comment+log with: kill only when `not (session is not None and
  _session_is_alive(session))`; otherwise `continue` with a DEBUG "protected live
  harness" log. This makes the one-shot branch consistent with the surrounding
  is_claude gate.
- Update the module-level comments at lines 100-108 and 5059-5068 that assert the
  "no legitimate one-shot lives this long / heartbeat gate intentionally bypassed"
  rationale, since that is precisely the invalidated premise.
- **Audit (record findings, no code change unless a defect is found):**
  - `_reap_slot_leases` — reaps by lease staleness against a live-owner map, not raw
    age; verify it re-checks ownership. Record disposition.
  - `_never_started_past_grace` (D0 gate) — age-gated but guarded by
    `sdk_ever_output` / `turn_count` / `log_path` / `claude_session_uuid` evidence;
    it recovers (requeues) rather than SIGKILLing a live process. Record as sound.
  - `_sweep_dead_worker_sessions` (#1767) — verifies `os.kill(pid, 0)` dead, not age.
    Record as sound.
  - Per-tool wedge timeout (`_check_tool_timeout`) — age-gated false positive, but
    that is issue #2145's territory. Record cross-reference; do not fix here.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_oneshot_owner_is_live` wraps its `find_by_claude_pid` call; add a test that a
  raising lookup returns False (reapable) — asserts the conservative-cleanup contract,
  not a silent swallow. The existing `_fast_reap_stale_print_oneshots` per-PID
  try/except (DEBUG log, continue) is preserved; a test confirms one raising PID does
  not abort the pass for other PIDs.
- [ ] `_reap_orphan_session_processes` per-PID try/except already covered by
  `TestInvariants`; no new swallow introduced.

### Empty/Invalid Input Handling
- [ ] `_oneshot_owner_is_live(None)` and `find_by_claude_pid(None)` → returns False
  (reapable). Add a test for the None/absent-pid path.

### Error State Rendering
- [ ] Not user-visible (background reaper). Protection/kill decisions must emit a
  distinguishing log line (`protected live harness` vs `SIGTERM'd`) so operators can
  audit reaper behavior in `logs/worker.log`. Add a test asserting the protect path
  logs and does not TERM.

## Test Impact

- [ ] `tests/unit/test_session_health_orphan_process_reap.py::TestStalePrintOneshotFastKill::test_stale_bare_print_oneshot_reaped_despite_fresh_heartbeat`
  — **REPLACE**: this test currently encodes the bug — it sets up a live
  `running` session with `claude_pid=2100`, fresh heartbeat, matching the reaped
  PID, and asserts `killed == 1`. Rewrite so a live-owned one-shot survives
  (`killed == 0`, `terminate` not called), and split out a companion asserting an
  *orphaned* one-shot (no owning session / terminal / stale heartbeat) is still
  reaped (`killed == 1`).
- [ ] `tests/unit/test_session_health_orphan_process_reap.py::TestFastReapStalePrintOneshots::test_reaps_stale_oneshot_and_stages_sigkill`
  — **UPDATE**: its `stale` proc (pid 2300) currently reaps with no owner mocked;
  under the fix `find_by_claude_pid` must be patched to return None (orphan) for the
  assertion to hold. Add `find_by_claude_pid` patching to keep it an orphan-path test.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py::TestFastReapStalePrintOneshots::test_escalates_to_sigkill_on_second_pass`
  — **UPDATE**: same — patch `find_by_claude_pid` to None so the survivor is treated
  as an orphan and escalates to SIGKILL.
- [ ] Other `TestFastReapStalePrintOneshots` cases (`test_self_pid_never_reaped`,
  etc.) — **UPDATE** as needed to patch `find_by_claude_pid` → None so their orphan
  scenarios remain orphan scenarios under the new gate.

New tests to add (in the same file):
- Fast reaper: live-owned stale one-shot survives (`find_by_claude_pid` → running
  session with fresh heartbeat) — `killed/reaped == 0`, no TERM, nothing staged.
- Fast reaper: orphaned stale one-shot (owner None) still reaped.
- Fast reaper: stale one-shot whose owner is terminal (`status="killed"`) reaped.
- Fast reaper: stale one-shot whose owner has stale heartbeat (>30 min) reaped.
- Hourly reaper: same four cases against `_reap_orphan_session_processes`.
- `_oneshot_owner_is_live`: None pid, raising lookup, and the four ownership states.

## Rabbit Holes

- **Chasing the exact PPID==1 mechanism.** Why a live worker-spawned `claude -p`
  reparents to launchd (PPID==1) is interesting but irrelevant to the fix: whatever
  the reparenting cause, the ownership gate is the correct guard. Do not spend time
  re-architecting harness process-group handling — that is a separate concern.
- **Reworking `_session_is_alive`'s 30-minute heartbeat window.** It is generous but
  errs toward *protecting* live sessions, which is exactly the safe direction here.
  Tightening it is out of scope and risks re-introducing false kills.
- **Fixing the dead-worker sweep or #2145 wedge timeout.** Both are separate rules
  with separate issues; only audit-and-record them.
- **Adding a direct worker-PID liveness probe** (resolving the owning worker's
  registered_pid and `os.kill(0)`-ing it). Tempting for a "worker dead" signal, but
  `_session_is_alive`'s heartbeat freshness already proxies worker liveness (the
  worker writes the heartbeat). Only escalate to a direct probe if the audit finds
  the heartbeat proxy demonstrably insufficient.

## Risks

### Risk 1: A genuinely stuck live harness now survives past 600s
**Impact:** If a PM turn truly hangs (not just runs long) but its session stays
`running` with a refreshing heartbeat, the ownership gate protects it and the
one-shot reaper will not kill it.
**Mitigation:** This is correct behavior — a hung turn with a *live* session is the
domain of the evidence-based no-progress detector / tool-timeout sub-loop, not the
orphan reaper. The reaper's job is orphans, not liveness policing. The existing
`_agent_session_health_check` no-progress path (subprocess-hang verdict) and cost
backstop remain the authority for genuinely runaway live sessions.

### Risk 2: `find_by_claude_pid` transient failure mis-protects an orphan
**Impact:** If the Redis lookup raises transiently, a helper that returned True
(protect) would leak an orphan one-shot.
**Mitigation:** The helper returns **False (reapable)** on any exception, matching
`_session_is_alive`'s conservative-False contract. A transient failure therefore
defaults to the pre-fix cleanup behavior for that one PID on that one tick, never a
leak. Covered by an explicit raising-lookup test.

### Risk 3: PID reuse between spawn and reap
**Impact:** macOS recycles PIDs (~5 min). A reaped orphan's PID could be reassigned
to a new live harness before the next tick, or vice versa.
**Mitigation:** `find_by_claude_pid` resolves the *current* owner of that PID at
reap time, and `_session_is_alive` checks that owner's live heartbeat — so a recycled
PID now owned by a live session is correctly protected. The existing SIGKILL-drain
already verifies `create_time` before escalation, guarding the TERM→KILL window.

## Race Conditions

### Race 1: turn exits between age-match and ownership resolution
**Location:** `agent/session_health.py` `_fast_reap_stale_print_oneshots` /
`_reap_orphan_session_processes`, between `_is_stale_print_oneshot` and
`_oneshot_owner_is_live`.
**Trigger:** A live PM turn finishes (clearing `claude_pid` to None via
`runner.py:983`) in the microseconds between the reaper matching its age and
resolving its owner.
**Data prerequisite:** `claude_pid` must reflect the turn's live/exited state.
**State prerequisite:** the session row is the single writer of its own `claude_pid`.
**Mitigation:** If the turn exited, its process is gone — `proc.terminate()` hits
`NoSuchProcess` (already caught, silent) or the PID is recycled and guarded by the
SIGKILL-drain `create_time` check. If the turn is still live, `find_by_claude_pid`
resolves it and it is protected. Either ordering is safe; no lock needed because the
reaper only reads ownership state and the kill is idempotent against a gone process.

### Race 2: heartbeat goes stale mid-tick for a still-live turn
**Location:** `_session_is_alive` heartbeat freshness check.
**Trigger:** A live turn whose `_heartbeat_loop` is briefly starved could show a
heartbeat older than the 30-min window.
**Data prerequisite:** `last_heartbeat_at` refreshed within 30 min.
**State prerequisite:** heartbeat loop running.
**Mitigation:** The 30-min window is ~20x the 60s write cadence — a live turn's
heartbeat is normally <90s old, so a false-stale requires a 30-minute heartbeat
outage, which itself indicates a genuinely wedged worker (a legitimate reap target).
The window's generosity is the intended safety margin.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2145] Per-tool wedge timeout ignoring the Bash call's declared
  timeout — a distinct age-based false positive in `_check_tool_timeout`, tracked
  and fixed separately. This plan only audits and cross-references it.
- Nothing else deferred — the ownership gate, both reaper call sites, the audit of
  the remaining age-gated rules, and the full test rewrite are all in scope.

## Update System

No update system changes required — this is a purely internal change to
`agent/session_health.py` reap logic. No new dependencies, config files, migrations,
or `scripts/update/run.py` changes. No Popoto schema change (reuses existing
`claude_pid` IndexedField and `find_by_claude_pid`).

## Agent Integration

No agent integration required — this is a worker-internal background-reaper change.
No MCP server, `.mcp.json`, CLI entry point, or bridge import is involved. The
behavior is exercised only by the worker's health loop and validated via unit tests.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-session-liveness.md` — add a subsection documenting
  that the one-shot reaper now verifies orphanhood (no owner / terminal / stale
  heartbeat) before killing, and that live PM-turn harnesses are protected regardless
  of age. Cross-reference the headless-session-runner's long-turn reality.
- [ ] Cross-check `docs/features/headless-session-runner.md` for any claim that PM
  turns are bounded under 600s; correct if present.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for internal reaper logic.

### Inline Documentation
- [ ] Rewrite the module comments at `agent/session_health.py:100-108` and
  `:5059-5068` to reflect the ownership-gate rationale (the "no legitimate one-shot
  lives >600s" and "heartbeat gate intentionally bypassed" premises are now false).
- [ ] Docstring for the new `_oneshot_owner_is_live` helper.

## Success Criteria

- [ ] `_fast_reap_stale_print_oneshots` never TERM/KILLs a PID resolving to a
  `running` session with a fresh heartbeat (unit test proves survival at age >600s).
- [ ] `_reap_orphan_session_processes` one-shot branch applies the same live-owner
  gate (unit test proves survival).
- [ ] An orphaned one-shot (owner None / terminal / stale heartbeat) is still reaped
  on the same fast cadence (unit tests prove kill in all three states) — #1632
  cleanup preserved.
- [ ] The bug-encoding test `test_stale_bare_print_oneshot_reaped_despite_fresh_heartbeat`
  is replaced by tests asserting the corrected behavior.
- [ ] Audit findings for the other age-gated rules recorded in the PR description.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n "_oneshot_owner_is_live" agent/session_health.py` confirms the gate is
  referenced by both reap functions.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The
lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (reap-gate)**
  - Name: reap-gate-builder
  - Role: Add `_oneshot_owner_is_live`, wire both reapers, update comments/docstrings.
  - Agent Type: builder
  - Domain: async/concurrency (process reaping, PID lifecycle)
  - Resume: true

- **Builder (tests)**
  - Name: reap-test-builder
  - Role: Rewrite `TestStalePrintOneshotFastKill`, update `TestFastReapStalePrintOneshots`,
    add the new live/orphan/terminal/stale cases and `_oneshot_owner_is_live` unit tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (reap-gate)**
  - Name: reap-validator
  - Role: Verify live harness protected, all three orphan states still reaped, no
    regression to #1632 fast-cadence cleanup, lint/format clean.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reap-documentarian
  - Role: Update `pm-session-liveness.md`, cross-check headless-session-runner doc.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add the ownership-gate helper and wire both reapers
- **Task ID**: build-reap-gate
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_orphan_process_reap.py
- **Assigned To**: reap-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_oneshot_owner_is_live(pid)` beside `_session_is_alive` (resolve
  `find_by_claude_pid`, return True only for a found + `_session_is_alive` session;
  return False on None/terminal/stale/exception).
- In `_fast_reap_stale_print_oneshots`, after `_is_stale_print_oneshot` passes and
  before the TERM/KILL escalation, `if _oneshot_owner_is_live(pid): continue`.
- In `_reap_orphan_session_processes` `is_stale_oneshot` branch, replace the
  unconditional fast-kill with the live-owner gate (skip + DEBUG log when live).
- Rewrite the stale module comments at lines ~100-108 and ~5059-5068.

### 2. Rewrite and extend the reaper tests
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_orphan_process_reap.py
- **Assigned To**: reap-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- REPLACE `test_stale_bare_print_oneshot_reaped_despite_fresh_heartbeat` with a
  live-owned-survives test + an orphaned-still-reaped companion.
- UPDATE `TestFastReapStalePrintOneshots` cases to patch `find_by_claude_pid` → None
  where the intent is an orphan.
- ADD: live/orphan/terminal/stale cases for both reapers, and `_oneshot_owner_is_live`
  unit tests (None pid, raising lookup, four ownership states).

### 3. Validate behavior and regressions
- **Task ID**: validate-reap-gate
- **Depends On**: build-reap-gate, build-tests
- **Assigned To**: reap-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_health_orphan_process_reap.py -q`.
- Confirm live harness protected, all three orphan states reaped, #1632 fast cadence
  intact, lint/format clean.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-reap-gate
- **Assigned To**: reap-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pm-session-liveness.md`; cross-check headless-session-runner doc.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: reap-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reaper tests pass | `pytest tests/unit/test_session_health_orphan_process_reap.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py tests/unit/test_session_health_orphan_process_reap.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py` | exit code 0 |
| Gate wired into both reapers | `grep -c "_oneshot_owner_is_live" agent/session_health.py` | output > 2 |
| Bug-encoding test removed | `grep -c "test_stale_bare_print_oneshot_reaped_despite_fresh_heartbeat" tests/unit/test_session_health_orphan_process_reap.py` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_session_health_orphan_process_reap.py \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should the ownership gate additionally probe the owning worker's registered PID
   directly (`os.kill(0)` on the worker's `worker:registered_pid` value) as a
   stronger "worker dead" signal, or is `_session_is_alive`'s heartbeat-freshness
   proxy sufficient? (Plan currently uses the heartbeat proxy; direct probe listed as
   a rabbit hole unless the audit finds it insufficient.)
2. Is protecting a *genuinely hung but session-live* PM turn (Risk 1) acceptable —
   i.e., confirm the orphan reaper should never police liveness, leaving that to the
   no-progress detector and cost backstop?
