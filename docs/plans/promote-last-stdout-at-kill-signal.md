---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1046
last_comment_id:
---

# Promote last_stdout_at to Tier-1 Kill Signal

## Problem

The two-tier no-progress detector (PR #1039 / issue #1036) fixed premature kills during warmup, but introduced an inverse failure mode: alive-but-silent Claude sessions are never flagged by tier-1 because tier-1 only checks heartbeat freshness. A `claude -p` subprocess can be healthy (heartbeats every 60s via the SDK watchdog), produce zero stdout for hours, and the health check will never fire.

**Current behavior:** A session with fresh `last_heartbeat_at` or `last_sdk_heartbeat_at` within 90s is unconditionally considered alive by `_has_progress()`. `last_stdout_at` is only consulted inside `_tier2_reprieve_signal()`, which is only called after tier-1 has already flagged the session. Since tier-1 never flags alive-but-silent sessions, tier-2 is never reached, and `last_stdout_at` is effectively dead code in this failure mode.

**Desired outcome:** A session whose `last_stdout_at` is stale beyond a configurable window (`STDOUT_FRESHNESS_WINDOW`, proposed 600s = 10 min) is flagged by tier-1 even when both heartbeats are fresh. Sessions with no stdout ever (warmup) continue to be tolerated by heartbeat-only semantics, with a first-stdout deadline added for the "silent from the start" case.

## Freshness Check

**Baseline commit:** b847ae4a
**Issue filed at:** 2026-04-18 (same day as baseline)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:143` — `HEARTBEAT_FRESHNESS_WINDOW = 90` — still holds at line 143
- `agent/agent_session_queue.py:145` — `STDOUT_FRESHNESS_WINDOW = 90` — **notable**: this constant already exists but is only used in tier-2 (`_tier2_reprieve_signal` line 1682). Issue proposes reusing it as tier-1 input and changing its value to 600s.
- `agent/agent_session_queue.py:150` — `MAX_RECOVERY_ATTEMPTS = 2` — still holds at line 150
- `agent/agent_session_queue.py:1563` — `_has_progress()` — confirmed: no `last_stdout_at` read in this function
- `agent/agent_session_queue.py:1622` — `_tier2_reprieve_signal()` — confirmed: `last_stdout_at` read at line 1678 inside tier-2 only
- `agent/agent_session_queue.py:1866` — `if _reason_kind == "no_progress":` — confirmed: tier-2 gated on this
- `agent/agent_session_queue.py:1870` — `tier1_flagged_total` counter — confirmed exists
- `agent/agent_session_queue.py:1902` — `DISABLE_PROGRESS_KILL` kill-switch — confirmed at line 1902
- `agent/agent_session_queue.py:1929` — task cancel (not SIGTERM) — confirmed at lines 1929-1944
- `agent/agent_session_queue.py:1982` — `recovery_attempts >= MAX_RECOVERY_ATTEMPTS → failed` — confirmed at lines 1982-1994
- `agent/messenger.py:98-110` — `on_stdout_event` callback writing `last_stdout_at` — not re-read (minor; plan approach unchanged)
- `models/agent_session.py:297` — `worker_key = project_key` — not re-read (acknowledged, out of scope)

**Cited sibling issues/PRs re-checked:**
- #1036 — closed 2026-04-18 by PR #1039. PR merged same day; no file changes to relevant code since then.
- #918 (`response_delivered_at` guard) — closed, PR merged; guard present at line 1827, unaffected.
- #944 (slugless dev session child-progress) — closed; child check preserved in `_has_progress()` at lines 1613-1618.
- #963 (child-activity branch) — closed; preserved in `_has_progress()`.
- #1006 (terminal-zombie guard) — closed; guard present at lines 1735-1743, unaffected.

**Commits on main since issue was filed (touching referenced files):** None — no commits to `agent/agent_session_queue.py`, `models/agent_session.py`, or `agent/messenger.py` since issue was filed.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** The constant `STDOUT_FRESHNESS_WINDOW` already exists at line 145 with value 90. Changing it to 600 affects the tier-2 stdout reprieve window as well — this must be evaluated. See Risks section.

## Prior Art

- **Issue #1036 / PR #1039**: "300s no-progress guard kills sessions before first turn despite live SDK heartbeat" — Introduced two-tier detector (dual-heartbeat OR semantics, tier-2 reprieve with psutil + stdout). Fixed premature kills during warmup. Did NOT address alive-but-silent sessions. This issue is the complementary fix for the opposite failure mode.

## Research

No relevant external findings — this is a purely internal change to session health-check logic within the existing PR #1039 scaffold.

## Data Flow

The relevant data flow for the alive-but-silent failure mode:

1. **SDK subprocess** runs `claude -p`; model hangs on API call / MCP tool / retry loop
2. **BackgroundTask watchdog** fires every 60s via `on_heartbeat_tick` → writes `last_sdk_heartbeat_at`
3. **`_heartbeat_loop`** fires every 60s → writes `last_heartbeat_at`
4. **`on_stdout_event`** fires only when stdout arrives → writes `last_stdout_at` (silent: never fires)
5. **Health check** runs `_has_progress()`: both heartbeats fresh → returns `True` → session NOT flagged
6. **`_tier2_reprieve_signal`** never called (tier-1 never flagged) → `last_stdout_at` never consulted
7. **Session stays "running"** indefinitely; downstream pending sessions blocked by PM serialization

**After the fix:**
- Step 5: `_has_progress()` additionally checks `last_stdout_at`: if set AND stale > `STDOUT_FRESHNESS_WINDOW` → returns `False` (tier-1 flagged)
- Step 6: `_tier2_reprieve_signal()` evaluates psutil gates (c)(d)(e); alive-but-silent subprocess passes (c) "alive" → reprieve
- Step 6b: On reprieve: `reprieve_count++`, `tier2_reprieve_total:alive` counter increments, session NOT killed
- The reprieve from (c)"alive" means: this approach needs `STDOUT_FRESHNESS_WINDOW` to be long enough that the psutil "alive" reprieve itself is also time-bounded — i.e., a zombie/dead process (no longer passing gate c) will eventually fail all tier-2 gates.

**Key insight from data flow:** When the subprocess is alive but silent, tier-2 gate (c) "alive" will grant a reprieve. The tier-1 stdout-stale flag will cause repeated evaluation. Once the subprocess eventually dies (or becomes zombie), gate (c) fails and the kill proceeds. This is acceptable behavior — it bounds the maximum session lifetime to `STDOUT_FRESHNESS_WINDOW + one health-check tick` after the process goes non-alive, rather than infinite. The "alive reprieve" is not a loophole; it correctly treats a healthy-but-slow subprocess with tolerance.

**Alternative interpretation from issue:** The issue also asks about a `FIRST_STDOUT_DEADLINE` for sessions that have never produced any stdout. This is distinct: a session with `last_stdout_at is None` after a long warmup is either genuinely starting (warmup case, #1036) or stuck before first output (distinct from alive-but-silent after stdout was seen). Plan addresses this with a separate constant.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #1039 (#1036) | Added tier-1 dual-heartbeat OR + tier-2 psutil/stdout reprieve | Fixed under-kill (premature kills during warmup). Did not add `last_stdout_at` as a tier-1 input — over-tolerant for alive-but-silent sessions. |

**Root cause pattern:** The tier-1 / tier-2 split correctly separates "is the session being managed" (heartbeat) from "is the session doing work" (stdout). PR #1039 only wired `last_stdout_at` into the tier-2 reprieve path, leaving the case where heartbeats are fresh but stdout has been absent for minutes completely undetected by tier-1.

## Architectural Impact

- **New dependencies**: None — all required fields (`last_stdout_at`) and constants (`STDOUT_FRESHNESS_WINDOW`) already exist.
- **Interface changes**: `_has_progress()` signature unchanged; behavior changes (new return-False condition).
- **Coupling**: No new coupling — change is localized to `_has_progress()` and a new `tier1_flagged_stdout_stale` counter emit site in the health-check loop.
- **Data ownership**: No change — `last_stdout_at` continues to be owned by the messenger callback.
- **Reversibility**: High — `DISABLE_PROGRESS_KILL=1` suppresses all kills including the new stdout-stale path. The new constant `STDOUT_FRESHNESS_WINDOW` can be tuned or env-overridden. Reverting the one-line `_has_progress()` change fully reverts to PR #1039 behavior.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all required infrastructure (fields, callbacks, kill path, metrics, kill-switch) was established by PR #1039.

## Solution

### Key Elements

- **New tier-1 stdout-stale branch in `_has_progress()`**: When `last_stdout_at` is set and stale beyond `STDOUT_FRESHNESS_WINDOW`, return `False` even if both heartbeats are fresh.
- **`STDOUT_FRESHNESS_WINDOW` raised from 90s to 600s**: Current value of 90s in tier-2 is too tight for long tool calls. 600s (10 min) is the proposed default; env-tunable.
- **`FIRST_STDOUT_DEADLINE` new constant**: When `last_stdout_at is None` (session never produced stdout), fall back to heartbeat-only until a configurable deadline (proposed 300s = 5 min) has elapsed since `started_at`. After the deadline, treat as stdout-stale. Preserves warmup tolerance (#1036) while bounding the "silent from the start" case.
- **New Redis counter**: `session-health:tier1_flagged_stdout_stale:{project_key}` — distinct from `tier1_flagged_total` (heartbeat-stale) for distinguishing the failure mode in dashboards.

### Flow

Session running silently for 10+ minutes → health check tick → `_has_progress()` checks `last_stdout_at` stale? → yes → returns `False` → tier-1 flags → `tier1_flagged_stdout_stale` counter increments → `_tier2_reprieve_signal()` evaluates → process alive? → "alive" reprieve → `tier2_reprieve_total:alive` increments → reprieve logged → session NOT killed (but now being monitored) → once process goes non-alive → tier-2 all gates fail → kill path executes → `recovery_attempts++` → session killed or finalized as `failed`

### Technical Approach

1. **Raise `STDOUT_FRESHNESS_WINDOW`**: Change from `90` to `600`. This also relaxes the tier-2 stdout reprieve window — a session with stdout 5 minutes ago now gets a reprieve from tier-2 gate (e). This is intentional: the tier-1 flag catches the case; tier-2 gate (e) should be at least as permissive as tier-1 to avoid the asymmetry where tier-1 flags but tier-2 immediately reprieves on stale stdout. (Alternatively, split into two constants: `STDOUT_FRESHNESS_WINDOW_T1 = 600` and `STDOUT_FRESHNESS_WINDOW_T2 = 90`. See Open Questions.)

2. **Add `FIRST_STDOUT_DEADLINE = 300`** (env-tunable): Seconds after `started_at` after which a session with no stdout ever is also flagged by tier-1. Preserves warmup tolerance — `last_stdout_at is None` + young session → heartbeat-only (no change from current). `last_stdout_at is None` + old session → tier-1 flag.

3. **Modify `_has_progress()`**: After the dual-heartbeat OR check, add a stdout-stale branch:
   ```python
   # Tier 1 extension: stdout-stale kill signal (#1046)
   lso = getattr(entry, "last_stdout_at", None)
   now_utc = datetime.now(tz=UTC)
   if lso is not None:
       lso_aware = lso if lso.tzinfo else lso.replace(tzinfo=UTC)
       if (now_utc - lso_aware).total_seconds() >= STDOUT_FRESHNESS_WINDOW:
           return False  # stdout stale; let tier-1 flag
   else:
       # No stdout yet — apply FIRST_STDOUT_DEADLINE relative to started_at
       started = getattr(entry, "started_at", None)
       if started is not None:
           started_aware = started if started.tzinfo else started.replace(tzinfo=UTC)
           if (now_utc - started_aware).total_seconds() >= FIRST_STDOUT_DEADLINE:
               return False  # never produced stdout within deadline; flag
   ```
   This branch runs AFTER the dual-heartbeat OR check. If heartbeats are already stale (tier-1 via heartbeat), we never reach this branch — existing behavior for dead-heartbeat case is unchanged.

4. **Emit counter in health-check loop**: After `tier1_flagged_total` is incremented and `_reason_kind == "no_progress"`, detect whether the flag came from stdout-stale vs. heartbeat-stale using the reason string. Add `tier1_flagged_stdout_stale` counter when applicable. The cleanest approach: `_has_progress()` returns a reason string or a sentinel instead of a plain bool, OR the health-check loop re-checks `last_stdout_at` after `_has_progress()` returns False to attribute the flag. The simpler option (re-check after flagging) avoids changing `_has_progress()`'s return type.

5. **Kill-switch coverage**: `DISABLE_PROGRESS_KILL=1` already suppresses the kill at line 1902. No additional plumbing needed — the new stdout-stale path routes through the same kill gate.

## Failure Path Test Strategy

### Exception Handling Coverage

- The new branch in `_has_progress()` uses `getattr(entry, "last_stdout_at", None)` — no exception can propagate. The `isinstance(lso, datetime)` guard (mirrored from tier-2) handles non-datetime values silently.
- The `tier1_flagged_stdout_stale` counter emit uses the same try/except pattern as existing counters (`logger.debug` on failure) — already tested pattern, no new handlers needed.

### Empty/Invalid Input Handling

- `last_stdout_at = None` → handled by the `lso is not None` branch → falls to `FIRST_STDOUT_DEADLINE` check or no action
- `last_stdout_at` is a non-datetime value → `isinstance` guard skips the branch, no flag
- `started_at = None` → `FIRST_STDOUT_DEADLINE` branch skips — no flag (conservative; warmup tolerance preserved)

### Error State Rendering

This is an internal health-check change with no user-visible output. The Redis counter and log lines are the observable outputs. Tests verify counter increments and log messages.

## Test Impact

- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestDualHeartbeatOrSemantics` — UPDATE: add test cases for `last_stdout_at` stale with fresh heartbeats (tier-1 should flag), and `last_stdout_at is None` with old `started_at` (FIRST_STDOUT_DEADLINE case)
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveGates::test_no_reprieve_on_stale_stdout` — UPDATE: the assertion `_tier2_reprieve_signal(handle, entry)` where `last_stdout_at=_ago(200)` returns `None` is based on `STDOUT_FRESHNESS_WINDOW=90`. After raising to 600, `_ago(200)` returns `"stdout"` (fresh within 600s). Update test to use `_ago(700)` for stale case.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestTier2ReprieveGates::test_reprieve_on_recent_stdout` — UPDATE: still valid after constant change (`_ago(30)` remains fresh), but verify.

## Rabbit Holes

- **Splitting `STDOUT_FRESHNESS_WINDOW` into two constants** (T1=600, T2=90): Cleaner separation but doubles constant count. Start with single constant at 600s; if tier-2 stdout reprieve at 600s proves too permissive, split in a follow-up.
- **Instrumenting stdout gap distribution** before hard-coding 600s: Valuable for production tuning but delays the fix. Use `STDOUT_FRESHNESS_WINDOW` env override to tune post-deploy without code changes.
- **SIGTERM of the claude subprocess on kill**: Issue calls this out explicitly as out-of-scope (separate follow-on to #1036 Fix 2). Do not implement here.
- **Changing PM `worker_key` from `project_key` to `chat_id`**: Separate architectural issue per the issue body. Not in scope.
- **User-visible Telegram notification on kill**: Separate output-routing issue. Not in scope.

## Risks

### Risk 1: `STDOUT_FRESHNESS_WINDOW` raise from 90s to 600s relaxes tier-2 stdout reprieve
**Impact:** Sessions flagged by tier-1 (heartbeat-stale) that have recent stdout up to 10 min ago will now get a tier-2 "stdout" reprieve instead of being killed. This is actually more correct — if stdout is 5 min fresh, the session is doing work. Low regression risk.
**Mitigation:** Review tier-2 test `test_no_reprieve_on_stale_stdout` — update threshold from `_ago(200)` to `_ago(700)`.

### Risk 2: FIRST_STDOUT_DEADLINE flags warmup sessions in the #1036 case
**Impact:** If a session delays first stdout beyond 300s but is genuinely warming up (large context load, slow MCP), tier-1 will flag it.
**Mitigation:** Set `FIRST_STDOUT_DEADLINE = 300` (5 min). With `HEARTBEAT_FRESHNESS_WINDOW = 90`, a session that has been alive for 300s has received 4+ heartbeats — strong evidence it's not a cold-start. If 300s is too tight, env-override via `FIRST_STDOUT_DEADLINE_SECS`. The tier-2 psutil "alive" reprieve also provides a safety net for genuinely running subprocesses.

### Risk 3: Alive-but-silent subprocess receives infinite reprieve via tier-2 gate (c)
**Impact:** A subprocess that is alive but silent will be tier-1 flagged (stdout stale) but tier-2 reprieves it as "alive". It won't be killed until the process eventually dies. This means the actual kill latency for a truly hung-but-alive process is unbounded.
**Mitigation:** This is acceptable. The session is tier-1 flagged and being actively monitored. Tier-2 correctly says "process is alive — don't kill prematurely". If the process never terminates on its own, a timeout-based kill (`exceeded timeout` reason) will eventually trigger regardless of heartbeats. Document this in the `_has_progress()` docstring.

## Race Conditions

### Race 1: `last_stdout_at` read in `_has_progress()` while messenger writes it concurrently
**Location:** `agent/agent_session_queue.py` — `_has_progress()` and `agent/messenger.py` `on_stdout_event`
**Trigger:** Health check reads `last_stdout_at` from Redis while `on_stdout_event` is writing it
**Data prerequisite:** `last_stdout_at` must be a consistent datetime or None when read
**State prerequisite:** None — the field is written atomically by Popoto ORM
**Mitigation:** `getattr(entry, "last_stdout_at", None)` reads the cached value from the already-fetched AgentSession object. The health check fetches sessions at the top of the loop; a concurrent write changes Redis but not the already-fetched object. This is the same pattern used for `last_heartbeat_at` in the existing tier-1 check — no new hazard.

## No-Gos (Out of Scope)

- Subprocess SIGTERM on kill (follow-on to #1036 Fix 2 — separate issue)
- Changing PM `worker_key` from `project_key` to `chat_id` (separate architectural issue)
- User-visible Telegram notification when a session is killed (separate output-routing issue)
- Splitting `STDOUT_FRESHNESS_WINDOW` into T1/T2 constants (start with single constant, split in follow-up if needed)
- Instrumenting stdout gap distribution before setting the threshold (use env-override for post-deploy tuning)

## Update System

No update system changes required — this feature is purely internal to the worker process. No new dependencies, config files, or env vars that must be propagated (the new env-tunable constants have sensible defaults).

## Agent Integration

No agent integration required — this is an internal worker health-check change. No MCP server exposure, no bridge changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — "Two-tier no-progress detector" section: add description of the stdout-stale tier-1 signal, document `STDOUT_FRESHNESS_WINDOW` change from 90s → 600s, document `FIRST_STDOUT_DEADLINE` constant (default 300s, env-tunable via `FIRST_STDOUT_DEADLINE_SECS`), and explain the alive-but-silent failure mode this addresses.
- [ ] Update `docs/features/bridge-self-healing.md` — add `session-health:tier1_flagged_stdout_stale:{project_key}` to the Redis counter reference table so operators know how to distinguish heartbeat-stale vs. stdout-stale kills in dashboards.
- [ ] Update inline docstring for `_has_progress()` in `agent/agent_session_queue.py` to document the new tier-1 stdout-stale branch and `FIRST_STDOUT_DEADLINE` fallback behavior.

## Success Criteria

- [ ] A session whose `last_stdout_at` is stale for `STDOUT_FRESHNESS_WINDOW` (600s) is flagged by tier-1 even when both heartbeats are fresh.
- [ ] A session with `last_stdout_at is None` and young `started_at` (< `FIRST_STDOUT_DEADLINE`) is NOT flagged — heartbeat-only semantics preserved (warmup tolerance, #1036 case).
- [ ] A session with `last_stdout_at is None` and old `started_at` (> `FIRST_STDOUT_DEADLINE`) IS flagged by tier-1.
- [ ] `session-health:tier1_flagged_stdout_stale:{project_key}` Redis counter increments on stdout-triggered tier-1 flags.
- [ ] `DISABLE_PROGRESS_KILL=1` continues to suppress kills while emitting the new metric.
- [ ] After two silent-hang recoveries, session transitions to terminal `failed` (existing PR #1039 path, no code change needed).
- [ ] Unit test: fresh heartbeats + stale `last_stdout_at` (> 600s) → `_has_progress()` returns `False`.
- [ ] Unit test: fresh heartbeats + fresh `last_stdout_at` (< 600s) → `_has_progress()` returns `True`.
- [ ] Unit test: fresh heartbeats + `last_stdout_at is None` + young `started_at` → `_has_progress()` returns `True`.
- [ ] Unit test: fresh heartbeats + `last_stdout_at is None` + old `started_at` → `_has_progress()` returns `False`.
- [ ] Existing tier-2 test `test_no_reprieve_on_stale_stdout` updated for new 600s threshold — passes.
- [ ] Tests pass (`pytest tests/unit/test_health_check_recovery_finalization.py -v`)

## Team Orchestration

### Team Members

- **Builder (health-check)**
  - Name: health-check-builder
  - Role: Implement stdout-stale tier-1 branch, raise `STDOUT_FRESHNESS_WINDOW`, add `FIRST_STDOUT_DEADLINE`, add counter, update existing tests
  - Agent Type: builder
  - Resume: true

- **Validator (health-check)**
  - Name: health-check-validator
  - Role: Verify implementation meets all success criteria; run unit tests
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/bridge-self-healing.md`
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement stdout-stale tier-1 branch
- **Task ID**: build-tier1-stdout
- **Depends On**: none
- **Validates**: `tests/unit/test_health_check_recovery_finalization.py`
- **Assigned To**: health-check-builder
- **Agent Type**: builder
- **Parallel**: true
- Raise `STDOUT_FRESHNESS_WINDOW` from 90 to 600 in `agent/agent_session_queue.py` (line 145)
- Add `FIRST_STDOUT_DEADLINE = 300` constant (env-tunable via `int(os.environ.get("FIRST_STDOUT_DEADLINE_SECS", 300))`)
- Add stdout-stale branch to `_has_progress()` after the dual-heartbeat OR check (before own-progress fields): check `last_stdout_at` stale, then `FIRST_STDOUT_DEADLINE` for `last_stdout_at is None` sessions
- Update `_has_progress()` docstring to document the new tier-1 stdout-stale signal and `FIRST_STDOUT_DEADLINE`
- Add `tier1_flagged_stdout_stale` counter emit in health-check loop after tier-1 flags; detect stdout-stale by re-checking `last_stdout_at` after `_has_progress()` returns False
- Update `tests/unit/test_health_check_recovery_finalization.py` tier-2 test `test_no_reprieve_on_stale_stdout`: change `_ago(200)` to `_ago(700)` to match new 600s threshold
- Add 4 new unit tests in `TestDualHeartbeatOrSemantics` (or a new `TestStdoutStaleTier1` class): fresh heartbeats + stale stdout → False, fresh heartbeats + fresh stdout → True, fresh heartbeats + no stdout + young started_at → True, fresh heartbeats + no stdout + old started_at → False

### 2. Validate tier-1 stdout-stale implementation
- **Task ID**: validate-tier1-stdout
- **Depends On**: build-tier1-stdout
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_health_check_recovery_finalization.py -v` — all tests must pass
- Verify `STDOUT_FRESHNESS_WINDOW = 600` in `agent/agent_session_queue.py`
- Verify `FIRST_STDOUT_DEADLINE` constant exists and is env-tunable
- Verify `_has_progress()` contains the new stdout-stale branch
- Verify `tier1_flagged_stdout_stale` counter emit exists in health-check loop
- Verify 4 new unit tests exist and pass

### 3. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-tier1-stdout
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` "Two-tier no-progress detector" section
- Document stdout-stale tier-1 signal, `STDOUT_FRESHNESS_WINDOW` change from 90→600, `FIRST_STDOUT_DEADLINE` constant, and `tier1_flagged_stdout_stale` metric

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: health-check-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_health_check_recovery_finalization.py -v`
- Run `python -m ruff check agent/agent_session_queue.py`
- Run `python -m ruff format --check agent/agent_session_queue.py`
- Verify `docs/features/bridge-self-healing.md` updated
- Confirm all success criteria checked

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_health_check_recovery_finalization.py -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| STDOUT_FRESHNESS_WINDOW is 600 | `python -c "from agent.agent_session_queue import STDOUT_FRESHNESS_WINDOW; assert STDOUT_FRESHNESS_WINDOW == 600"` | exit code 0 |
| FIRST_STDOUT_DEADLINE exists | `python -c "from agent.agent_session_queue import FIRST_STDOUT_DEADLINE; assert FIRST_STDOUT_DEADLINE > 0"` | exit code 0 |
| tier1_flagged_stdout_stale counter exists | `grep -n "tier1_flagged_stdout_stale" agent/agent_session_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Single vs. split `STDOUT_FRESHNESS_WINDOW`**: Should `STDOUT_FRESHNESS_WINDOW` remain a single constant used by both tier-1 and tier-2, or be split into `STDOUT_FRESHNESS_WINDOW_T1 = 600` and `STDOUT_FRESHNESS_WINDOW_T2 = 90`? Plan proposes single constant at 600s. After raising to 600, tier-2 gate (e) reprieves sessions with stdout up to 10 min old — is this the desired tier-2 behavior?

2. **`FIRST_STDOUT_DEADLINE` threshold**: Is 300s (5 min) the right default for "silent from the start" sessions? Sessions that never emit stdout within 5 min of start will be tier-1 flagged even with fresh heartbeats. The tier-2 "alive" reprieve provides a safety net, but if the subprocess hangs before first stdout for more than 5 min, it will be progressively monitored. Is this acceptable, or should the deadline be longer (e.g., 600s)?
