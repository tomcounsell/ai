---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2149
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-17T17:07:25Z
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
- **Issue #1938** (Fix 2): the enabling prerequisite for this fix. Before #1938 the
  headless-runner cutover left `claude_pid` unset on PM-turn spawn (only `pm_pid` was
  written), so `find_by_claude_pid` would have returned None for a live PM turn and
  the ownership gate would have been useless. #1938 made `runner.py` write
  `claude_pid = pid` on spawn (`runner.py:629`) and clear it on turn exit
  (`_clear_claude_pid`), which is precisely what makes `find_by_claude_pid(pid)` a
  reliable live-owner signal. This fix depends on that write path being correct — see
  Risk 4 for the fail-silent gap.
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

- **Ownership gate helper (fast reaper only)** — a single predicate,
  `_oneshot_owner_is_live(pid)`, that returns True when the PID is registered as a
  live session's harness. It resolves `AgentSession.find_by_claude_pid(pid)` **inside a
  bounded lookup** (see next bullet) and returns True only when a session is found AND
  `_session_is_alive(session)` (not terminal, heartbeat fresh). Returns False (→
  reapable) for: no owning session, terminal owner, stale heartbeat (dead/hung
  worker), lookup timeout, or lookup exception. Fail-open toward *protecting* on
  transient lookup errors is rejected — see Risk 2; the helper returns False
  (reapable) on timeout/exception to preserve #1632 cleanup, matching the existing
  `_session_is_alive` conservative-False contract.
- **Bounded, fail-toward-reapable lookup (concern 1)** — the fast reaper is
  *deliberately* Redis-free in its hot loop (its docstring: "No heartbeat gate, no
  Redis skip-set scan"). Introducing `find_by_claude_pid` reintroduces a synchronous
  Redis round-trip precisely into the path that must stay responsive during a
  memory-cascade (the #1632 scenario), where Redis itself may be slow or wedged. The
  helper therefore wraps the lookup in a bounded call — a short timeout
  (`ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS`, default 2.0s, added beside
  `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS`) via a `concurrent.futures` single-worker
  executor `.result(timeout=...)`. On `TimeoutError` (or any exception) the helper
  returns **False (reapable)**, so a degraded Redis degrades gracefully to the pre-fix
  fast-cadence cleanup rather than stalling the health loop. A live harness is only
  *not* protected in the narrow window where Redis is wedged AND a stale one-shot is
  actually a live harness — an acceptable trade because a wedged Redis already means a
  system-wide degradation where cleanup takes priority.
- **Fast reaper gate** — `_fast_reap_stale_print_oneshots` computes the staging tuple
  `staged = (pid, create_time)`, then calls the helper after the age match; if the
  owner is live, it **discards `staged` from `_pending_sigkill_orphans`** (concern 2 —
  see below) and `continue`s (no TERM, no KILL), leaving the live harness untouched.
- **Staging-set discard before skip (concern 2)** — the ownership `continue` must sit
  *after* `staged` is computed and must `_pending_sigkill_orphans.discard(staged)`
  before continuing. Otherwise a PID that was SIGTERM'd (staged) on a prior pass and
  then, via PID recycling, resolves to a live owner on a later pass would leak its
  tuple in the staging set permanently (unbounded set growth), and a future age-match
  on the same recycled `(pid, create_time)` could skip the SIGTERM grace and SIGKILL
  immediately. Discarding on the protect path keeps the set a faithful "pending
  SIGKILL" ledger.
- **Hourly reaper: delete the bypass branch, do NOT call the helper (concern 3)** — in
  `_reap_orphan_session_processes`, `session = find_by_claude_pid(pid)` is *already*
  resolved above the `is_stale_oneshot` branch (line 5044) and the very next
  `elif session is not None and _session_is_alive(session): continue` is the exact
  live-owner gate we want. The fix is simply to **delete** the `is_stale_oneshot`
  fast-kill branch (lines 5059-5068) so a stale one-shot falls through to that
  existing gate: live owner → `continue` (protected); no owner / terminal / stale →
  kill. Calling `_oneshot_owner_is_live(pid)` here would issue a *redundant second*
  `find_by_claude_pid` lookup for the PID already resolved — so the helper is wired
  into the fast reaper only. This keeps the one-shot path identical to the surrounding
  is_claude gate with zero new Redis calls in the hourly path.
- **Preserved fast death for orphans** — the #1632 rogue-subagent one-shots have
  no `claude_pid` mapping (they were never a tracked session's harness), so
  `find_by_claude_pid` returns None → helper returns False → still reaped on the
  same fast cadence. No regression to the memory-cascade defense.

### Flow

Reap tick → match stale `--print` one-shot (PPID==1, age>600s) → **resolve owner
via `find_by_claude_pid`** → owner live (`running` + fresh heartbeat)? → **skip
(protect live harness)** ; else (no owner / terminal / stale) → TERM→KILL as today.

### Technical Approach

- Add `ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS = 2.0` beside
  `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS` (line ~108).
- Add `_oneshot_owner_is_live(pid: int | None) -> bool` near `_session_is_alive`
  in `agent/session_health.py`. Body: run
  `session = AgentSession.find_by_claude_pid(pid)` inside a **bounded** call — a
  module-level single-worker `concurrent.futures.ThreadPoolExecutor` submit +
  `.result(timeout=ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS)` — then
  `return bool(session is not None and _session_is_alive(session))`. Wrap the whole
  body so `TimeoutError` **and** any other exception return False (reapable —
  conservative toward cleanup, never raises, never blocks the health loop longer than
  the timeout). `pid is None` short-circuits to False before any lookup.
- In `_fast_reap_stale_print_oneshots`, after `_is_stale_print_oneshot(...)` passes
  and `staged = (pid, create_time)` is computed, add the ownership gate:
  ```python
  if _oneshot_owner_is_live(pid):
      _pending_sigkill_orphans.discard(staged)  # concern 2: never leak a recycled/live PID
      continue
  ```
  placed *before* the `if staged in _pending_sigkill_orphans` escalation block.
- In `_reap_orphan_session_processes`, **delete** the `is_stale_oneshot:` fast-kill
  branch (lines 5059-5068) entirely. `session = find_by_claude_pid(pid)` is already
  resolved at line 5044 and the following
  `elif session is not None and _session_is_alive(session): continue` becomes the sole
  gate for the one-shot signature too — live owner protected, orphan/terminal/stale
  killed. Do NOT call `_oneshot_owner_is_live` here (it would duplicate the already-done
  lookup). Add a DEBUG "protected live harness PID %d — owning session alive" log to the
  existing `elif ... continue` so the protect decision is auditable for one-shots as well.
- Update the module-level comments at lines 100-108 that assert the "no legitimate
  one-shot lives this long" rationale, since that premise is invalidated by the
  headless-runner cutover. The lines 5059-5068 comment block is removed with the branch
  it documented.
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
- [ ] **Bounded-lookup timeout (concern 1)**: add a test that patches
  `find_by_claude_pid` to block longer than `ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS`
  (e.g. `time.sleep`), monkeypatches the timeout constant to a small value, and
  asserts `_oneshot_owner_is_live` returns False (reapable) within the bound rather
  than hanging — proving a wedged Redis degrades to cleanup, not to a stalled loop.
- [ ] `_reap_orphan_session_processes` per-PID try/except already covered by
  `TestInvariants`; no new swallow introduced. The deleted `is_stale_oneshot` branch
  removes a kill path, not an exception path.

### Staging-Set Integrity (concern 2)
- [ ] Add a test that stages `(pid, create_time)` in `_pending_sigkill_orphans` (as if
  SIGTERM'd on a prior pass), then runs a fast-reap pass where `find_by_claude_pid`
  now resolves that PID to a live `running` session, and asserts the tuple is
  **discarded** from `_pending_sigkill_orphans` and the process is neither TERM'd nor
  KILL'd — proving the protect path cannot leak a recycled/live PID into the staging
  ledger.

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
- Fast reaper: previously-staged PID that now resolves to a live owner → tuple
  discarded from `_pending_sigkill_orphans`, no TERM/KILL (staging-leak regression,
  concern 2).
- Hourly reaper: same four ownership cases against `_reap_orphan_session_processes`,
  now flowing through the existing `elif ... _session_is_alive` gate (branch deleted).
- `_oneshot_owner_is_live`: None pid, raising lookup, bounded-timeout (slow lookup →
  False within the bound, concern 1), and the four ownership states.

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

### Risk 4: fail-silent `claude_pid` write recreates the false positive
**Impact:** The ownership gate is only as reliable as the `claude_pid` write it reads.
`runner.py:620-634` persists `claude_pid = pid` on PM-turn spawn inside a
`try/except Exception` that logs at DEBUG and swallows (`[runner] pm_pid/claude_pid
persist failed`). If that `save(update_fields=["pm_pid", "claude_pid"])` fails silently
(Redis blip at spawn time), the live session's `claude_pid` stays None → the reaper's
`find_by_claude_pid(pid)` returns None → the helper returns False (reapable) → the live
harness is killed. This is the exact original false positive, now gated on a rare write
failure instead of on age.
**Mitigation (in scope for this fix — verification only, no code change unless a defect
is found):** (a) The reaper still only acts at age > 600s, so a write that fails but is
retried/backfilled before 10 minutes elapse is harmless. (b) The write is a
same-object, single-field save with no cross-module reach, so the failure surface is
narrow (Redis unavailability, which also degrades the reaper's own lookup toward
reapable regardless). (c) Record in the PR whether any spawn-time backfill of
`claude_pid` exists (e.g. `_on_harness_init` or the heartbeat loop re-asserting it); if
none does, the durable hardening — retry/backfill the `claude_pid` write, or re-assert
it from the heartbeat loop — is captured as Open Question 3 for a follow-up, NOT built
here (keeps this fix Small and localized to the reaper). This risk is called out so the
reviewer knows the gate has a write-side dependency and does not assume the read-side
gate is sufficient on its own.

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
- [ ] Rewrite the module comment at `agent/session_health.py:100-108` to reflect the
  ownership-gate rationale (the "no legitimate one-shot lives >600s" premise is now
  false). The `:5059-5068` "heartbeat gate intentionally bypassed" comment is removed
  along with the branch it documented (concern 3).
- [ ] Docstring for the new `_oneshot_owner_is_live` helper, noting the bounded lookup
  (concern 1) and the fail-toward-reapable contract.
- [ ] Update the `_fast_reap_stale_print_oneshots` docstring, which currently asserts
  "No heartbeat gate, no Redis skip-set scan — the signature alone is decisive": it now
  performs a bounded ownership lookup. Document why the lookup is bounded and why it
  fails toward reapable.

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
- [ ] `grep -c "_oneshot_owner_is_live" agent/session_health.py` returns `2` — the
  helper is *defined once* and *called once* (fast reaper only). The hourly reaper does
  NOT call it (concern 3: it reuses its already-resolved `session` via the existing
  `_session_is_alive` gate). A count > 2 would indicate a redundant second lookup.
- [ ] The hourly reaper's `is_stale_oneshot` fast-kill branch is gone:
  `grep -c "is_stale_oneshot" agent/session_health.py` no longer matches the fast-kill
  branch body (only the signature-detection assignment, if any, remains).
- [ ] **Post-deploy observability (concern 5)**: after the fix ships, replay the real
  incident shape (a live `running` session whose PM-turn one-shot ages past 600s) and
  confirm `logs/worker.log` shows a `protected live harness` line for that PID and
  **no** `[fast-oneshot-reap] SIGTERM'd` / `[orphan-reap] ... fast-kill` line for it —
  i.e. `grep "protected live harness" logs/worker.log` fires and the SIGTERM grep for
  that PID is empty. This is the direct counter-assertion to the 2026-07-17 14:33:19
  incident (`SIGTERM'd stale --print one-shot PID 74819`).

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
- Add `ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS = 2.0` beside
  `ORPHAN_PRINT_ONESHOT_MAX_AGE_SECONDS`.
- Add `_oneshot_owner_is_live(pid)` beside `_session_is_alive`: resolve
  `find_by_claude_pid` **inside a bounded `concurrent.futures` `.result(timeout=...)`
  wrapper** (concern 1); return True only for a found + `_session_is_alive` session;
  return False on None-pid/None-owner/terminal/stale/timeout/exception.
- In `_fast_reap_stale_print_oneshots`, after `_is_stale_print_oneshot` passes and
  `staged = (pid, create_time)` is computed, add
  `if _oneshot_owner_is_live(pid): _pending_sigkill_orphans.discard(staged); continue`
  before the TERM/KILL escalation (concern 2 — discard prevents staging-set leak).
- In `_reap_orphan_session_processes`, **delete** the `is_stale_oneshot` fast-kill
  branch (5059-5068) so the one-shot signature falls through to the existing
  `elif session is not None and _session_is_alive(session): continue` gate; add a DEBUG
  "protected live harness" log there. Do NOT call the helper here (concern 3 — avoids a
  redundant second `find_by_claude_pid`).
- Rewrite the stale module comment at lines ~100-108; the ~5059-5068 comment is removed
  with its branch.

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
| Helper defined once, called by fast reaper only | `grep -c "_oneshot_owner_is_live" agent/session_health.py` | output == 2 (def + 1 call; NOT called by hourly reaper — concern 3) |
| Hourly bypass branch deleted | `grep -n "one-shot lives this long" agent/session_health.py` | no match (the 5059-5068 fast-kill comment+branch is removed) |
| Bounded lookup constant present | `grep -c "ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS" agent/session_health.py` | output >= 2 (definition + use) |
| Bug-encoding test removed | `grep -c "test_stale_bare_print_oneshot_reaped_despite_fresh_heartbeat" tests/unit/test_session_health_orphan_process_reap.py` | match count == 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_session_health_orphan_process_reap.py \| grep -v '# open bug'` | exit code 1 |

## Critique Results

**Verdict:** READY TO BUILD (WITH CONCERNS) — 0 blockers, 6 concerns, 2 nits.
All 6 concerns addressed in this revision pass (`revision_applied: true`).

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| Concern | Redis `find_by_claude_pid` can hang the deliberately-Redis-free fast reaper under a memory cascade; needs a bounded/timeout lookup failing toward "reapable." | Solution → "Bounded, fail-toward-reapable lookup"; Technical Approach; Risk 2; Failure Path Test Strategy (bounded-timeout test); Verification (`ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS` check). | Helper wraps `find_by_claude_pid` in `concurrent.futures` `.result(timeout=ORPHAN_OWNER_LOOKUP_TIMEOUT_SECONDS=2.0)`; `TimeoutError`/exception → False (reapable). |
| Concern | Staging-set leak: the `continue` gate before the discard block leaks a live/recycled PID's tuple in `_pending_sigkill_orphans`. | Solution → "Staging-set discard before skip"; Technical Approach; Failure Path Test Strategy → "Staging-Set Integrity"; new staging-leak test. | `_pending_sigkill_orphans.discard(staged)` immediately before the protect `continue`, after `staged` is computed. |
| Concern | Internal inconsistency on the hourly reaper (helper call vs inline `session` reuse); grep `> 2` rewards a redundant second Redis lookup. | Solution → "Hourly reaper: delete the bypass branch"; Technical Approach; Success Criteria + Verification (grep `== 2`, bypass-branch-deleted check). | Hourly reaper does NOT call the helper — `is_stale_oneshot` branch (5059-5068) is deleted so the signature falls through to the existing `_session_is_alive` gate. Helper wired into the fast reaper only. |
| Concern | Write-side gap: fail-silent `claude_pid` save in `runner.py:620-634` can recreate the false positive. | New Risk 4; new Open Question 3; Prior Art #1938. | Scoped verification-only (record whether a backfill exists); durable hardening deferred to a follow-up per Open Question 3. |
| Concern | No post-deploy observability validation against the real incident. | Success Criteria → "Post-deploy observability" (`protected live harness` log-grep + empty SIGTERM grep for the incident PID shape). | Direct counter-assertion to the 2026-07-17 14:33:19 `SIGTERM'd PID 74819` incident. |
| Concern | Prior Art omits #1938 (made `claude_pid` reliable). | Prior Art (new #1938 entry). | #1938 Fix 2 is the enabling prerequisite: it writes `claude_pid` on spawn, which the ownership gate reads. |
| Nit | Docstring/comment drift — `_fast_reap_stale_print_oneshots` claims "no Redis skip-set scan"; module comments assert invalidated premises. | Inline Documentation tasks (updated docstring + comment rewrites); Technical Approach comment tasks. | Docstring and the lines 100-108 comment updated; 5059-5068 comment removed with its branch. |
| Nit | Verification grep expectations needed to match the single-call design. | Verification table updated (`== 2`, bypass-deleted, timeout-constant checks). | Prevents the grep from rewarding a redundant hourly lookup. |

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
3. **Write-side hardening (Risk 4 / concern 4):** the ownership gate reads `claude_pid`,
   which `runner.py:620-634` writes fail-silently on PM-turn spawn. Should this fix also
   harden that write (retry/backfill on failure, or re-assert `claude_pid` from the
   heartbeat loop) so a spawn-time Redis blip cannot resurrect the false positive? The
   plan currently scopes this OUT (verification-only: the reviewer records whether a
   backfill already exists; durable hardening becomes a follow-up issue) to keep the fix
   Small and localized to the reaper. Confirm that split, or pull the write-side
   hardening into this issue.
