---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-11
tracking: https://github.com/tomcounsell/ai/issues/3289
last_comment_id:
---

# Activity-Aware Turn Deadline, and an Honest Timeout Notice

## Problem

The runner caps every turn with a **hard wall-clock deadline fixed at dispatch**. `started_at` is assigned once at `agent/session_runner/runner.py:1089` and never mutated; `_preempt_watcher` compares against that frozen origin every 2s (`runner.py:1386`):

```python
if self._turn_timeout_s and (loop.time() - started_at) >= self._turn_timeout_s:
    await self._kill_turn(handle, turn_task, cause="timeout")
```

`ENG_TURN_TIMEOUT_S` is 7200s for every non-teammate session (`runner.py:107`, `turn_timeout_for` at `runner.py:344-352`). In the Eng lane the PM spawns the Dev subagent **in the foreground**, so the Dev's whole pipeline runs inside one PM turn — a design the code comment at `runner.py:101-105` already acknowledges and accepts. A healthy full pipeline therefore exceeds 2h routinely and gets SIGTERM'd then SIGKILL'd on its whole process group, identically to a process that has been dead-silent the entire time.

Three consequences, each independently a defect:

1. **Healthy work is killed.** Measured: 0.2% of foreground subagent calls exceed 1800s, and the largest observed healthy one ran 8549s (142 min, a MERGE-stage `Agent` call that completed normally). Today that is a guaranteed kill.
2. **A healthy long turn is filed as a failure.** `ExitReason.TURN_TIMEOUT` is `is_clean=False` (`agent/session_runner/router.py:434`), so `session_executor` maps it to `status="failed"`, posts the error reaction, and skips `mark_work_done`.
3. **The user-facing notice lies.** `TIMEOUT_NEEDS_ATTENTION_MESSAGE` (`runner.py:258-261`) says *"I've paused the work and kept the progress so far."* Nothing is paused — the process group is SIGTERM'd, given a 10s flush grace, then SIGKILL'd (`runner.py:1420-1455`), and the Dev subagent dies with it as a child of the same group.

**Current behavior:** a two-hour healthy turn is killed, recorded as failed, and the human is told the work was paused.

**Desired outcome:** a turn that is demonstrably doing work is never preempted regardless of elapsed time; a turn that is genuinely wedged still is; and when the notice fires, every sentence in it is true.

## Freshness Check

**Disposition: Unchanged.** Baseline `391819bf5` (`origin/main` at plan time). Issue #3289 was filed the same hour this plan was written, and no commits landed on main in between.

- Every file:line reference in the issue was read directly during recon, not inferred: `runner.py:1089`, `:1386`, `:107`, `:344-352`, `:258-261`, `:704-737`, `:856-887`, `:1420-1455`, `router.py:434`, `role_driver.py:466-520`, `session_executor.py:64-123`.
- `git log --oneline -20 -- agent/session_runner/runner.py` — most recent touch is `b7cf2558f` (#3284, Codex dev lane), which added a separate `CODEX__TURN_TIMEOUT_S` budget for the nested Codex executor and did not touch the watcher deadline.
- Cited siblings re-checked: **#3270** closed (notice-spam dedupe, still present and still load-bearing), **#1935** closed (`last_stdout_at`, still the live stamp), **#1930** closed (headless cutover that introduced the constants), **#1938 / #2146** closed (reap paths, untouched here).
- `ls -lt docs/plans/` shows no active plan touching `agent/session_runner/` deadlines. No overlap.
- **Bug still present**, confirmed by reading the code path and by three `cause=timeout` preempts in the retained worker logs (2026-09-08 22:43, 2026-09-08 23:09, 2026-09-11 07:31).

## Research

No WebSearch performed: this is entirely internal to the runner's own deadline logic and the repo's own hook-edge liveness primitive. No external library, API, or ecosystem pattern is involved. Proceeding on codebase evidence, which is the stronger source here — the numbers below were measured on this machine's own session corpus rather than reasoned from general practice.

## Prior Art

| Ref | Relevance |
|---|---|
| **#1930** | The headless `claude -p` cutover. Introduced `ENG_TURN_TIMEOUT_S` / `TEAMMATE_TURN_TIMEOUT_S` and, critically, **dropped** the PTY-era `last_pty_activity_at` signal. This plan restores an equivalent signal to the deadline. |
| **#1935** | Added `_stamp_stdout_liveness` / `last_stdout_at` as a partial replacement — but wired it only to the health checker, never to the turn deadline. |
| **2026-07-30 hook-edge work** | Added `agent/session_runner/liveness_hook.py` and `liveness.tool_activity_ts()`, whose docstring states outright that it "replaces what the #1930 headless cutover dropped". Its only production consumer today is `agent_session_queue._session_progress_ts`. This plan is the second consumer, and the one the docstring anticipates. |
| **#2662** | A healthy session was declared deadlocked from instantaneous `%CPU` reading 0.0% between subprocess bursts. `tools/session_progress.py` now contractually bans `psutil`/`cpu_percent`/`num_threads`/`.children(`/`pgrep` for activity inference, enforced by `tests/unit/test_session_progress.py:410`. |
| **#3270** | The notice-spam incident; added the per-run Redis SETNX dedupe `_claim_timeout_notice`. Preserved unchanged. |
| **#1938 / #2146** | Cancellation-proof process-group reap and SIGKILL escalation. Preserved unchanged. |

### Why previous fixes fell short

#1930 removed the activity signal and replaced it with a generous constant, on the stated reasoning that the ceiling is "an honest ceiling on a protocol that reports completion, not an idle guess" (`runner.py:101-105`). That reasoning is sound about *guessing* and wrong about *measuring*: the alternative to a guess is not a bigger constant, it is an observation. #1935 then built the observation and wired it to a different consumer. The signal and the deadline have simply never been connected.

## Spike Results

<!-- placeholder -->

## Appetite

**Medium.** One file carries nearly all the change (`agent/session_runner/runner.py`), the liveness primitive already exists and is already tested, and the blast radius is bounded by an existing well-covered test module. The cost is concentrated in getting the deadline semantics and the test rework right, not in volume of code.

## Solution

<!-- placeholder -->

## Data Flow

<!-- placeholder -->

## Technical Approach

<!-- placeholder -->

## Step by Step Tasks

<!-- placeholder -->

## Rabbit Holes

<!-- placeholder -->

## No-Gos

<!-- placeholder -->

## Risks

<!-- placeholder -->

## Success Criteria

<!-- placeholder -->

## Documentation

<!-- placeholder -->

## Open Questions

<!-- placeholder -->
