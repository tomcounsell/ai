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

### spike-1: how long does a healthy turn actually go silent?
- **Assumption**: "A healthy turn's silences are short enough that some idle window separates them from a wedge."
- **Method**: data analysis over `~/.claude/projects/*/*.jsonl` (20 recent sessions, 4,356 intra-turn gaps after filtering human waits and resume boundaries) and `logs/sessions/*/tool_use.jsonl` (101,026 pre/post tool pairs).
- **Result**:

  | Series | median | p90 | p95 | p99 | max |
  |---|---|---|---|---|---|
  | Consecutive parent-stream records | 0.2s | 7s | 13s | 127s | — |
  | Foreground `Task`/`Agent` calls (n=1978) | 21s | 332s | 478s | **881s** | **8549s** |

  11.6% of foreground subagent calls exceed 300s; 2.9% exceed 600s; 1.0% exceed 900s; 0.2% exceed 1800s. Background `Agent` calls return in ~0.3s and were excluded — pooling them drags the median from 21s to 4s and would have produced a dangerously small window.

  Deterministic silences any window must clear: `TaskOutput` with `{"block": true, "timeout": 600000}` is a **by-construction 600s** quiet period recurring across sessions; a full `tests/unit/` run is a single ~1200s `Bash` call (`CLAUDE.md`).

  Excluded as non-healthy: a 4896s `Agent` that ended in `[Request interrupted by user]`; 429 rate-limit parks (65567s, 13682s); overnight resume boundaries (46630s, 17714s).
- **Confidence**: high (n is large and the healthy/unhealthy split was verified by reading the bracketing records).
- **Impact if false**: the chosen window is mis-sized; the structure of the fix is unaffected.

### spike-2: is the parent stream a sufficient activity signal?
- **Assumption**: "Wiring an idle timer to the existing `_stamp_stdout_liveness` stream hook is enough."
- **Method**: measured parent-transcript append activity inside 22 foreground `Task` windows longer than 20s.
- **Result**: **FALSE, and decisively.** A foreground subagent appears in the parent's stream-json as exactly one `tool_use` and, much later, one `tool_result`, with nothing between. **27% of `Task` windows had zero parent-file records appended for their entire duration**; max intra-window gap across windows was 1835s (median 577s, p95 1022s). The parent stream genuinely goes dark during healthy nested work.
- **Confidence**: high.
- **Impact if false (it was)**: the original solution sketch's single-signal idle timer is discarded. See spike-4.

### spike-3: is CPU-quiescence a usable wedge signal?
- **Assumption**: "A wedged process consumes no CPU, so CPU time distinguishes grinding from wedged."
- **Method**: code read plus incident-history grep.
- **Result**: **NO — ruled out, not merely risky.** A subagent blocked on HTTPS to the model API consumes near-zero CPU, which is the dominant state of a healthy agent. This is a known incident: `tools/session_progress.py` documents **#2662**, where a healthy session was declared deadlocked because instantaneous `%CPU` read 0.0% between bursts. That module now contractually bans `psutil`, `cpu_percent`, `num_threads`, `.children(`, and `pgrep` for activity inference, enforced by `tests/unit/test_session_progress.py:410`. (`psutil` survives elsewhere in the repo only for *pid identity* — `create_time` matching in `tools/sdlc_lease_heartbeat.py` and `tools/sdlc_supervisor_identity.py` — never for activity.)
- **Confidence**: high.
- **Impact if false (it was)**: the solution sketch's process-liveness gate is discarded and replaced by spike-4's signal.

### spike-4: does a signal exist that ticks during nested subagent work?
- **Assumption**: "Some existing repo signal observes tool calls made inside an in-process subagent."
- **Method**: code read of `agent/session_runner/liveness.py` + empirical coverage count.
- **Result**: **YES, and it already exists.** `agent/session_runner/liveness.py:98` — `tool_activity_ts(session_id)`. A `matcher: ""` `PreToolUse` hook (`agent/session_runner/liveness_hook.py`), registered in every headless spawn's generated settings, rewrites `<hook-edge-dir>/<session_id>/<role>_hook_edges.toolactivity` on every tool call, **including tool calls made from inside an in-process subagent**. It globs the per-session directory and takes the max across hook channels, so any channel ticking counts. It never raises: missing directory, unreadable file, or malformed payload all read `None`.

  Coverage verified on session `ef7d6667`: parent transcript 80 tool calls + subagent transcripts 2,187 = 2,267; the repo hook log recorded **2,266**. Within-session tool-boundary gaps: median 0.6s, p95 11s, p99 32s.
- **Confidence**: high.
- **Impact if false**: there is no viable second signal and the only honest fallback is a much larger constant ceiling.

### spike-5: what is the binding constraint on the idle window?
- **Assumption**: "The window must exceed the longest healthy *subagent* call (8549s)."
- **Method**: derived from spike-4's mechanism.
- **Result**: **FALSE, and the window gets much smaller as a result.** Because the hook fires on the subagent's *own* tool calls, an 8549s subagent is not silent to this signal — it ticks every few seconds. The binding constraint is instead the longest **single tool call**, which fires the hook once at its start and then nothing: a ~1200s full `tests/unit/` `Bash` run, and the 600s `TaskOutput` block. That is a 7x reduction in the required window.
- **Confidence**: high.
- **Impact if false**: the window must grow; the structure is unaffected.

## Appetite

**Medium.** One file carries nearly all the change (`agent/session_runner/runner.py`), the liveness primitive already exists and is already tested, and the blast radius is bounded by an existing well-covered test module. The cost is concentrated in getting the deadline semantics and the test rework right, not in volume of code.

## Solution

Replace the single frozen wall-clock deadline with a **two-deadline model** in `_preempt_watcher`, driven by a combined activity signal.

**1. Activity-based idle deadline (new, primary).** The watcher preempts only after `IDLE` seconds with no observed activity, where activity is the **max** of two timestamps:

- the runner's own in-memory monotonic last-stream-event stamp, fed from the same hook that already drives `_stamp_stdout_liveness` (`runner.py:704-737`) — no Redis read in a 2s poll loop;
- `liveness.tool_activity_ts(session_id)`, which ticks on nested subagent tool calls and is the signal that covers the case the parent stream cannot see.

`IDLE` is provisionally **2400s (40 min)**: 2x the longest known single tool call (a ~1200s `tests/unit/` run), 4x the 600s `TaskOutput` block, and ~19x the overall p99 stream gap of 127s. Under this model the 8549s healthy MERGE-stage `Agent` call survives, because its own tool calls tick the marker throughout.

**2. Absolute wall-clock ceiling (retained, raised, backstop only).** A whole-turn cap that catches a runaway which streams forever. Provisionally **21600s (6h)** for eng, meaningfully above the largest observed healthy turn, so it is a genuine last resort rather than the operative limit. Teammate sessions keep a tight budget.

**3. Truthfulness contract on absent signals.** Per the `session_progress.py` precedent, **absence of the `toolactivity` marker reads UNKNOWN, never "wedged"**. When `tool_activity_ts` returns `None`, the watcher falls back to the stream-event stamp alone and the absolute ceiling still bounds the turn. A missing hook edge must never *shorten* a deadline.

**4. Honest notice.** Rewrite `TIMEOUT_NEEDS_ATTENTION_MESSAGE` to drop the false "paused" claim.

**Recommendation: keep sending it, reworded — do not drop to log-only.** Reasoning: the human's session is genuinely finished and `TURN_TIMEOUT` is `wrapup_eligible=False`, so the wrap-up guard will never say anything else. Dropping the send converts a wrong message into total silence on a request the human made and is waiting on, which is the strictly worse failure (and is the exact silence hazard `_claim_timeout_notice`'s per-run scoping was designed to avoid). The message also carries one genuinely actionable fact: replying resumes the same session. Under the new model the notice additionally becomes *rare and meaningful* — it fires only on a real wedge, so it stops being noise.

Proposed text (persona voice, present-fact, no em-dashes, no promise the system does not keep):

> I stopped this run after a long stretch with no activity. The work so far is saved. Reply and I'll pick it up from there.

Every clause is verifiable: the run was stopped (SIGTERM/SIGKILL), the stretch had no observed activity (that is literally the preempt predicate), the work is saved (`claude_session_uuid`/`runner_cwd`/`dev_agent_id` persisted at `system/init`, worktree cleanup skipped), and a reply resumes it (`claude --resume`).

**5. `ExitReason.TURN_TIMEOUT` stays `is_clean=False`.** Under the new model, reaching it means the turn was genuinely wedged — which *is* a failure and should be recorded as one, should keep the error reaction, and should keep skipping `mark_work_done` (a wedged turn's work is not done). No change to `router.py:434` or `session_executor.py`. This is a deliberate decision, not an omission: the reason the current `status="failed"` mapping feels wrong today is that healthy turns reach it, and this plan fixes that at the source.

**6. Constants stay local.** The new knobs join the existing `SESSION_RUNNER_*` module constants in `runner.py`, following the file's established provisional-constant convention (named, env-overridable, with a grain-of-salt comment). Migrating the whole `SESSION_RUNNER_*` family into `TimeoutSettings` is a worthwhile chore but is a separate, wider change that would touch every constant in the file and their tests; bundling it here would bury the behavioral fix. **Out of scope, file separately.**

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
