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

```
claude -p subprocess
  |
  |-- stream-json on stdout ------> HeadlessRoleDriver.on_stdout_event (0-arg)
  |                                   -> SessionRunner._on_stdout_event_liveness
  |                                        -> _stamp_stdout_liveness()  [Redis, cooldown-bounded]
  |                                        -> NEW: self._last_activity_mono = loop.time()
  |
  |-- every tool call, INCLUDING inside an in-process subagent
  |     -> PreToolUse hook (matcher: "", agent/session_runner/liveness_hook.py)
  |          -> writes <hook-edge-dir>/<session_id>/<role>_hook_edges.toolactivity
  |
  v
SessionRunner._preempt_watcher  (polls every STEER_POLL_INTERVAL_S = 2.0s)
  reads:  self._last_activity_mono                    [in-memory, free]
          liveness.tool_activity_ts(session_id)       [O(1) glob + read, wall-clock epoch]
  computes: idle = now - max(stream_activity, tool_activity)
  preempts if:  idle >= IDLE_DEADLINE            (the operative limit)
             or elapsed >= ABSOLUTE_CEILING      (runaway backstop)
  |
  v
_kill_turn(cause="timeout")  -> SIGTERM pgid -> 10s grace -> SIGKILL   [UNCHANGED]
  |
  v
runner loop (runner.py:956-971)
  _claim_timeout_notice(session_id, run_id)   [UNCHANGED, #3270 SETNX dedupe]
  on_user_payload(TIMEOUT_NEEDS_ATTENTION_MESSAGE)   [REWORDED]
  summary.exit_reason = ExitReason.TURN_TIMEOUT      [UNCHANGED, still is_clean=False]
```

**Clock mixing is the one hazard in this flow.** `self._last_activity_mono` is `loop.time()` (monotonic); `tool_activity_ts()` returns a wall-clock epoch float. They must be normalized before being compared — see Technical Approach.

## Technical Approach

All production changes are in `agent/session_runner/runner.py` unless noted.

### A. New constants (module level, alongside the existing `SESSION_RUNNER_*` family)

```python
# Idle deadline: preempt only after this long with NO observed activity.
# Provisional/tunable -- grain of salt. Sized against measured data (#3289):
# 2x the longest known single tool call (a ~1200s full tests/unit run), 4x the
# 600s TaskOutput blocking poll, ~19x the p99 stream gap (127s). A foreground
# subagent does NOT bound this -- its own tool calls tick the activity marker.
# Override with SESSION_RUNNER_ENG_IDLE_TIMEOUT_S.
ENG_IDLE_TIMEOUT_S: float = float(os.environ.get("SESSION_RUNNER_ENG_IDLE_TIMEOUT_S", "2400"))

# Absolute wall-clock ceiling: backstop for a runaway that streams forever.
# Deliberately far above the largest observed healthy turn (8549s) so it is a
# last resort, not the operative limit.
# Override with SESSION_RUNNER_ENG_ABSOLUTE_TIMEOUT_S.
ENG_ABSOLUTE_TIMEOUT_S: float = float(
    os.environ.get("SESSION_RUNNER_ENG_ABSOLUTE_TIMEOUT_S", "21600")
)
```

Teammate sessions are conversational and never host a foreground build. `TEAMMATE_TURN_TIMEOUT_S` (900) becomes the teammate **absolute ceiling**, and the teammate idle deadline is set to the same 900 — so observable teammate behavior is unchanged. `turn_timeout_for` is replaced by a `deadlines_for(session_type) -> TurnDeadlines` returning both values; keep `turn_timeout_for` only if something outside the runner imports it (check `__all__` at `runner.py:2006-2016` and grep before deleting — no legacy shim, per repo policy: if nothing imports it, delete it).

### B. Activity tracking

Add `self._last_activity_mono: float` to the runner, initialized at turn dispatch in `_run_one_turn` alongside `started_at`. Update it in `_on_stdout_event_liveness` (`runner.py:680-686`) **before** delegating to `_stamp_stdout_liveness` — the stamp is cooldown-gated for Redis write-rate reasons and must not gate the in-memory stamp, which is free.

### C. Clock normalization (the subtle part)

`tool_activity_ts()` returns a **wall-clock epoch**; `loop.time()` is **monotonic**. Convert the hook stamp into monotonic space once per poll rather than comparing across clocks:

```python
tool_ts = tool_activity_ts(session_id)           # epoch seconds, or None
now_mono = loop.time()
idle_from_tools = (time.time() - tool_ts) if tool_ts is not None else None
idle_from_stream = now_mono - self._last_activity_mono
idle = idle_from_stream if idle_from_tools is None else min(idle_from_stream, idle_from_tools)
```

Taking `min` of the two idles is the same thing as taking `max` of the two activity timestamps, and it does it without ever subtracting one clock from the other. A `None` from `tool_activity_ts` must **not** make `idle` larger or smaller than the stream-only value — it simply drops out (the UNKNOWN contract). Do not clamp a negative `idle_from_tools` to something surprising: a marker stamped in the future (clock skew) should read as "just active", i.e. `max(0.0, ...)`.

### D. Watcher predicate

Replace the single condition at `runner.py:1386`:

```python
if self._absolute_timeout_s and (now_mono - started_at) >= self._absolute_timeout_s:
    await self._kill_turn(handle, turn_task, cause="timeout")
    return
if self._idle_timeout_s and idle >= self._idle_timeout_s:
    await self._kill_turn(handle, turn_task, cause="timeout")
    return
```

Both keep `cause="timeout"`, so `_kill_turn`, the `handle.killed` branch at `runner.py:956-971`, the `#3270` dedupe, and the `turn_end_source="timeout"` turn event are all untouched. Log which deadline fired (structured field) so the two are distinguishable in `logs/worker.log` without changing the user-facing path.

**Polling cost:** `tool_activity_ts` is a directory glob plus a small file read, called once per 2s tick. That is negligible, and it is the same call `agent_session_queue._session_progress_ts` already makes on a hotter path. Do **not** add an optimization that only samples near the deadline — it adds a second code path and a staleness window for no measurable gain.

### E. Driver backstop re-derivation

`runner.py:668-670` currently derives the driver's `asyncio.wait_for` from `self._turn_timeout_s`. It must now derive from the **largest deadline that can fire**, i.e. the absolute ceiling:

```python
turn_timeout_s=self._absolute_timeout_s + self._term_grace_s + DRIVER_BACKSTOP_MARGIN_S,
```

This preserves the invariant that the watcher always fires before the driver backstop (`role_driver.py:466-520`). Add a test that asserts the ordering directly rather than restating the arithmetic, so a future constant change cannot silently invert it.

### F. Message rewrite

Replace `TIMEOUT_NEEDS_ATTENTION_MESSAGE` (`runner.py:258-261`) with the text in the Solution section, and rewrite its comment — the current comment (`"the work is paused, not lost"`) encodes the same falsehood as the string and must go, not just the string.

### G. What does NOT change

`_kill_turn` and the reap paths (#1938, #2146); `_claim_timeout_notice` (#3270); `ExitReason.TURN_TIMEOUT`'s tuple in `router.py:434`; the `session_executor` non-clean → `status="failed"` mapping; `_stamp_stdout_liveness`'s Redis write and its health-checker consumer; `liveness.py` itself (read-only consumer).

## Step by Step Tasks

Lane identity is fixed: worktree `/Users/valorengels/src/ai/.worktrees/dev-8e5ee1ed`, branch `session/dev-8e5ee1ed`. Do not create a new worktree or branch.

**Task 1 - Deadline constants and role resolution.**
Add `ENG_IDLE_TIMEOUT_S` (2400) and `ENG_ABSOLUTE_TIMEOUT_S` (21600) to `runner.py` with the provisional-constant comment shape the file already uses. Replace `turn_timeout_for` with `deadlines_for(session_type)` returning both values; teammate resolves to idle 900 / absolute 900. **Delete `turn_timeout_for` outright** - a grep confirmed its only importers are `tests/unit/session_runner/test_runner_turns.py` and `test_runner_liveness.py`, so no production shim is warranted (repo policy: no legacy bridges). Update `__all__` (`runner.py:2006-2020`) accordingly.

**Task 2 - Constructor and driver wiring.**
Replace `self._turn_timeout_s` with `self._idle_timeout_s` / `self._absolute_timeout_s`. Keep the explicit `turn_timeout_s=` ctor override working by mapping it onto the absolute ceiling (it is a test seam; `agent/session_executor.py:2231-2238` does not pass it in production), or introduce explicit `idle_timeout_s=` / `absolute_timeout_s=` kwargs and update the test seam. Choose one and be consistent; do not keep both spellings. Re-derive the driver backstop at `runner.py:668-670` from `self._absolute_timeout_s`.

**Task 3 - In-memory activity stamp.**
Add `self._last_activity_mono`, set it at turn dispatch in `_run_one_turn` next to `started_at`, and update it unconditionally in `_on_stdout_event_liveness` (`runner.py:680-686`) before the cooldown-gated `_stamp_stdout_liveness` call.

**Task 4 - Watcher predicate.**
Rewrite the deadline check in `_preempt_watcher` (`runner.py:1380-1388`) per Technical Approach C/D: compute `idle` via the `min`-of-idles normalization, check the absolute ceiling first, then the idle deadline, both with `cause="timeout"`. Add a structured log field naming which deadline fired. The steering-poll body below it is untouched.

**Task 5 - Message rewrite.**
Replace `TIMEOUT_NEEDS_ATTENTION_MESSAGE` (`runner.py:258-261`) with the honest text, and rewrite the comment above it, which currently asserts the same falsehood ("the work is paused, not lost").

**Task 6 - Test rework: the existing suite.**
- `tests/unit/session_runner/test_runner_liveness.py::test_turn_timeout_for_role_table` and `test_runner_turns.py::test_role_aware_turn_timeout` - rewrite against `deadlines_for`.
- `test_runner_liveness.py::test_post_init_hang_is_caught_by_turn_deadline_not_never_started_gate` - this asserts today that a stream-`init`-then-hang is caught by the whole-turn deadline. Under the new model it is caught by the **idle** deadline. Rework it to assert exactly that, preserving its original intent (the never-started liveness gate must not be the thing that catches it).
- `test_runner_preempt.py::test_timeout_expiry_is_graceful_preempt_not_error` - update the asserted delivery text; keep every other assertion (SIGTERM sent, `exit_reason == "turn_timeout"`, one `runner_turn` event with `turn_end_source == "timeout"`).
- `test_runner_preempt.py` dedupe tests (`..._delivered_once_across_two_runs_of_one_row`, `..._redelivered_for_a_later_unrelated_request`) - must still pass; only the text changes.
- `tests/unit/test_session_executor_runner_dispatch.py` - unchanged; `turn_timeout` still maps to `status="failed"`. Run it to prove that.

**Task 7 - New tests: both arms of the fix.**
- *Streaming past the old cap survives*: a turn whose stdout events keep arriving runs past 7200s of simulated time without preempt.
- *Nested-subagent silence survives*: parent stream is silent, but `tool_activity_ts` keeps advancing; no preempt past the old cap. This is the regression test for the reported bug.
- *Silent turn is preempted*: neither signal advances; the preempt fires at the idle deadline, and not before it.
- *UNKNOWN contract*: `tool_activity_ts` returns `None` (no hook edge); the deadline is no shorter than the stream-only computation. Assert a missing marker never shortens the budget.
- *Absolute ceiling*: both signals advance forever; the preempt fires at the ceiling.
- *Ordering invariant*: the driver backstop strictly exceeds the largest watcher deadline. Assert the relation, not the arithmetic.
- *Clock skew*: a `toolactivity` marker stamped in the future reads as "just active" and does not produce a negative or absurd idle.

**Task 8 - Prove the guard red.**
Before landing, run the new nested-subagent test against the pre-fix code and confirm it FAILS. A regression test that has never been red against the known-bad state proves nothing.

**Task 9 - Quality gates.**
`python -m ruff check` and `python -m ruff format` on the diff. Narrow-scope tests only, via `scripts/pytest-clean.sh`, naming the specific files from Tasks 6-7.

**Task 10 - Docs cascade.**
`docs/features/headless-session-runner.md` (the deadline model, both signals, the UNKNOWN contract) and `docs/features/config-timeout-catalog.md` (the two new `SESSION_RUNNER_*` keys). `docs/archive/plans-completed/headless-runner-zombie-liveness.md` records the old 7200/900 split as shipped history; leave it alone.

**Task 11 - File the follow-up.**
Open a `chore` issue for migrating the `SESSION_RUNNER_*` constant family into `TimeoutSettings` / `TIMEOUTS__*`, explicitly scoped out of this PR.

## Rabbit Holes

- **Rewriting the health checker or stall classifier.** `agent/session_health.py` and `agent/session_stall_classifier.py` have their own budgets (`NO_OUTPUT_BUDGET_SECONDS`, `IDLE_SUSPECT_SECS`, compaction reprieves). They operate on `status="running"` rows from outside the turn and are a different concern. Touch nothing there.
- **Migrating the whole `SESSION_RUNNER_*` family into `TimeoutSettings`.** Worthwhile, wide, and unrelated to the behavior fix. Task 11 files it.
- **Making `tool_activity_ts` more precise.** It already observes 2,266 of 2,267 tool calls. Resist adding a second marker, a heartbeat, or a richer payload.
- **Re-litigating `is_clean` / `status="failed"`.** The Solution section decides it stays. Changing it would ripple into the Telegram reaction, `mark_work_done`, and branch cleanup for no benefit once healthy turns stop reaching the path.
- **Optimizing the 2s poll.** A glob plus a small read is not a cost worth a second code path.
- **The Codex dev lane's own `CODEX__TURN_TIMEOUT_S`.** Separate, correctly scoped, bounded by the dev-lane lease TTL. Leave it.

## No-Gos

- **No `psutil` or CPU-based activity inference.** Ruled out by spike-3 and by incident #2662; contractually banned in `tools/session_progress.py` and enforced by `tests/unit/test_session_progress.py:410`. This plan does not reintroduce it under a new name.
- **No treating an absent activity marker as evidence of a wedge.** Absence reads UNKNOWN. A missing hook edge must never shorten a deadline.
- **No change to the SIGTERM to grace to SIGKILL process-group reap** (`_kill_turn`, and the cancellation-proof `finally` reap) - #1938 / #2146 territory.
- **No change to `_claim_timeout_notice`** (#3270 per-run SETNX dedupe).
- **No legacy shim for `turn_timeout_for`.** Grep confirmed only tests import it. Delete it and update the tests.
- **No full-suite test run** from this worktree; narrow scope only, following the repo's test hygiene rules in `CLAUDE.md`.
- **No em-dashes in the user-facing message.**

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **A genuinely wedged turn now holds a worker slot for up to 40 min.** | Medium | Accepted, and not a regression: today it holds the slot for up to 2h. The external health checker (`NO_OUTPUT_BUDGET_SECONDS` = 1800, `IDLE_SUSPECT_SECS` = 300) still observes `status="running"` rows independently. |
| **The hook edge is missing in a foreign repo or a misconfigured spawn**, so nested activity is invisible. | Medium | The UNKNOWN contract keeps behavior no worse than today's stream-only view, and the absolute ceiling still bounds the turn. Explicitly tested (Task 7). |
| **Clock skew between the wall-clock hook stamp and the monotonic stream stamp.** | Low | Never subtract across clocks; convert each to an idle duration in its own clock and take the `min`. Future-stamped markers clamp to "just active". Explicitly tested. |
| **2400s is still wrong** for some tool call longer than a full `tests/unit/` run. | Low | Env-overridable per the provisional-constant convention; the failure mode is a preempt, which is today's failure mode, not a new one. |
| **A `TaskOutput` blocking poll with a longer timeout** could be introduced later and silently approach the window. | Low | The 600s case has 4x headroom. Note the coupling in the constant's comment so a future author sees it. |
| **The reworded message breaks other assertions.** | Low | Grep confirmed a single production call site; the asserting tests are named in Task 6. |
| **Test rework masks a real behavior change** by being rewritten to match the new code. | Medium | Task 8 requires the new nested-subagent test to be proven RED against the pre-fix SHA. A test that was never red proves nothing. |

## Success Criteria

- [ ] A turn whose parent stream keeps producing events runs past 7200s without preempt.
- [ ] A turn whose parent stream is silent but whose `tool_activity_ts` keeps advancing (the nested foreground-subagent case, i.e. the reported bug) runs past 7200s without preempt - **and this test was proven RED on the pre-fix SHA**.
- [ ] A turn with no activity on either signal is preempted at the idle deadline, and not before it.
- [ ] `tool_activity_ts` returning `None` never shortens the deadline relative to the stream-only computation.
- [ ] The absolute ceiling preempts a turn that streams forever.
- [ ] The driver's `asyncio.wait_for` backstop strictly exceeds the largest watcher deadline (asserted as a relation, not restated arithmetic).
- [ ] `TIMEOUT_NEEDS_ATTENTION_MESSAGE` asserts nothing false: no claim that work was paused or suspended. Its surrounding comment is corrected too.
- [ ] `_claim_timeout_notice` per-run dedupe still holds.
- [ ] `turn_timeout` still maps to `status="failed"` (`tests/unit/test_session_executor_runner_dispatch.py` green, unmodified).
- [ ] Existing suites green: `tests/unit/session_runner/test_runner_preempt.py`, `test_runner_liveness.py`, `test_runner_turns.py`, `tests/unit/test_session_executor_runner_dispatch.py`, run narrow-scope via `scripts/pytest-clean.sh`.
- [ ] `python -m ruff check` and `python -m ruff format` clean on the diff.
- [ ] No CPU-based activity inference anywhere in the diff.

## Documentation

- [ ] `docs/features/headless-session-runner.md` - replace the single-deadline description with the two-deadline model: what each signal observes, why the parent stream alone is insufficient (the 27% figure), and the UNKNOWN contract on an absent marker.
- [ ] `docs/features/config-timeout-catalog.md` - add `SESSION_RUNNER_ENG_IDLE_TIMEOUT_S` and `SESSION_RUNNER_ENG_ABSOLUTE_TIMEOUT_S`; correct the entry for the removed `SESSION_RUNNER_ENG_TURN_TIMEOUT_S`.
- [ ] `agent/session_runner/liveness.py` - `tool_activity_ts`'s docstring names `agent_session_queue._session_progress_ts` as "its only production consumer". Add the watcher as the second.
- [ ] `docs/archive/plans-completed/headless-runner-zombie-liveness.md` records the old split as shipped history. Leave it; do not rewrite history.

## Update System

**No Popoto model changes, so no migration is required.** This plan adds two module-level constants to `agent/session_runner/runner.py` and changes in-memory turn state (`self._last_activity_mono`, `self._idle_timeout_s`, `self._absolute_timeout_s`). None of these are `AgentSession` fields or any other Popoto-persisted attribute.

Explicitly verified against the fields this work reads:

- `agent_session.last_stdout_at` - an existing Popoto field, **read and written exactly as today** by `_stamp_stdout_liveness`. This plan adds no field and changes no write path.
- `tool_activity_ts()` reads plain files under the hook-edge directory, not Redis and not the ORM.
- The `#3270` dedupe key `timeout-notice-sent:{session_id}:{run_id}` is a plain Redis string, deliberately not an AgentSession field, and is untouched.

Therefore: **no entry in `scripts/update/migrations.py`, no `MIGRATIONS` registration.** No raw Redis operations are introduced; the only Redis touch in the diff's blast radius is the existing cooldown-gated `save(update_fields=["last_stdout_at"])`, which goes through the ORM.

Deployment note: this changes worker/runner code, so after merge the standard `./scripts/valor-service.sh restart` applies (verify with `tail -5 logs/bridge.log` showing "Connected to Telegram"). No config or secret changes; the two new env keys are optional overrides with in-code defaults.

## Agent Integration

**No new Python tool is introduced, so there is no MCP server exposure to add.**

This work changes the internals of an existing runtime component (`SessionRunner._preempt_watcher`) and consumes an existing internal function (`agent.session_runner.liveness.tool_activity_ts`). Neither is a user-invocable tool, neither belongs in `mcp_servers/`, and nothing here should be reachable from an agent's toolbelt - the turn deadline is runner policy and must not be agent-steerable.

Adjacent surfaces checked and deliberately left alone:

- `mcp_servers/codex_dev_server.py` has its own `settings.codex.turn_timeout_s` for the nested Codex executor. Separate budget, separate lease TTL bound, out of scope.
- `sdlc-tool` and the `tools.*` CLI family gain no new subcommand.
- `tools/session_progress.py` already exposes progress reasoning to agents and is **not** modified here - notably, this plan does not relax its CPU-inference ban.

## Test Impact

| Test | Disposition | Why |
|---|---|---|
| `tests/unit/session_runner/test_runner_liveness.py::test_turn_timeout_for_role_table` | **REPLACE** | `turn_timeout_for` is deleted. Replaced by a `deadlines_for` role table asserting both idle and absolute values per session type. |
| `tests/unit/session_runner/test_runner_liveness.py::test_runner_defaults_to_role_aware_timeout` | **UPDATE** | Assert the runner resolves both deadlines from the role, not one. |
| `tests/unit/session_runner/test_runner_liveness.py::test_explicit_turn_timeout_overrides_role_default` | **UPDATE** | Follows whichever ctor-seam spelling Task 2 settles on. |
| `tests/unit/session_runner/test_runner_liveness.py::test_post_init_hang_is_caught_by_turn_deadline_not_never_started_gate` | **UPDATE** | Same intent (the never-started liveness gate must not be what catches it), new mechanism: it is now the **idle** deadline that catches it. This is the most load-bearing rework in the plan. |
| `tests/unit/session_runner/test_runner_turns.py::test_role_aware_turn_timeout` | **REPLACE** | Asserts `turn_timeout_for("teammate") < turn_timeout_for("eng")`; rewrite against `deadlines_for`. |
| `tests/unit/session_runner/test_runner_preempt.py::test_timeout_expiry_is_graceful_preempt_not_error` | **UPDATE** | Only the asserted delivery text changes. Every other assertion (SIGTERM sent, `exit_reason == "turn_timeout"`, one `runner_turn` event with `turn_end_source == "timeout"`) must survive unchanged. |
| `tests/unit/session_runner/test_runner_preempt.py::test_timeout_notice_delivered_once_across_two_runs_of_one_row` | **UPDATE** | #3270 dedupe must still hold; only the message text changes. |
| `tests/unit/session_runner/test_runner_preempt.py::test_timeout_notice_redelivered_for_a_later_unrelated_request` | **UPDATE** | Same. |
| `tests/unit/session_runner/test_runner_preempt.py` kill/reap tests (`test_sigterm_then_sigkill_escalation`, `test_kill_before_spawn_cancels_task_cooperatively`, `test_external_cancel_reaps_turn_process_group`, `test_reap_turn_group_unkillable_group_reports_not_confirmed`) | **KEEP UNCHANGED** | The reap path is explicitly out of scope. If any of these needs editing, that is a signal the diff has grown beyond the plan. |
| `tests/unit/test_session_executor_runner_dispatch.py` (rows at `:513`, `:556`, `:648`) | **KEEP UNCHANGED** | `turn_timeout` still maps to `status="failed"`. Running these unmodified is the proof of that decision. |
| `tests/unit/session_runner/test_exit_reason.py` | **KEEP UNCHANGED** | The `ExitReason` tuple is not modified. |
| `tests/unit/session_runner/test_headless_role_driver.py::` driver timeout test (`:226`) | **KEEP UNCHANGED** | `HEADLESS_TURN_TIMEOUT` semantics are unchanged; only the value fed to the driver changes. |
| `tests/unit/test_session_progress.py:410` (CPU-ban enforcement) | **KEEP UNCHANGED** | Must stay green as evidence the No-Go held. |
| **NEW** `test_streaming_turn_survives_past_old_cap` | **ADD** | Stream events keep arriving; no preempt past 7200s simulated. |
| **NEW** `test_nested_subagent_silence_survives_past_old_cap` | **ADD** | Parent stream silent, `tool_activity_ts` advancing. **The regression test for the reported bug. Must be proven RED on the pre-fix SHA (Task 8).** |
| **NEW** `test_fully_silent_turn_preempts_at_idle_deadline` | **ADD** | Neither signal advances; preempt fires at the idle deadline and not before. |
| **NEW** `test_absent_tool_activity_marker_never_shortens_deadline` | **ADD** | The UNKNOWN contract. |
| **NEW** `test_absolute_ceiling_preempts_endless_streamer` | **ADD** | Both signals advance forever. |
| **NEW** `test_driver_backstop_exceeds_largest_watcher_deadline` | **ADD** | Assert the relation, not the arithmetic. |
| **NEW** `test_future_stamped_marker_reads_as_just_active` | **ADD** | Clock-skew guard. |

Execution: narrow scope only, via `scripts/pytest-clean.sh`, naming these files explicitly. Per `CLAUDE.md`, a full `tests/unit/` run takes about 20 minutes and would collide with other lanes' Redis state.

## Critique Results

War room run 2026-09-11, FULL depth, independent roster (3 critics), roster gate 3/3 complete, 0 ungrounded.
Verdict: **NEEDS REVISION** (1 BLOCKER, 3 CONCERNS, 1 NIT).

| Severity | Critic | Finding | Disposition |
|---|---|---|---|
| BLOCKER | Scope & Value | The proposed message clause "The work so far is saved" is FALSE for the common synthetic-slug lane. `agent/session_executor.py:2710-2836`'s `finally:` block runs `cleanup_after_merge` for any session whose slug matches `^dev-[0-9a-f]{8}$`, pre-finalizing the session to a terminal status specifically so `remove_worktree` will not refuse, then deleting the worktree directory. The only skip is `_session_recorded_reap_failure` (`:2739`), which covers the rare unconfirmed-SIGKILL case, NOT an ordinary timeout preempt. So on a TURN_TIMEOUT the worktree with all uncommitted work is deleted by the same terminal path that delivers the notice. | ACCEPTED. New Task 12: skip synthetic-slug worktree cleanup when `exit_reason == "turn_timeout"`, alongside the existing reap-failure skip at `session_executor.py:2739`, with a regression test asserting a `dev-{8hex}` worktree survives a TURN_TIMEOUT terminal status. Message text stays as proposed once the worktree is genuinely preserved. |
| CONCERN | Risk & Robustness | The Codex dev-lane MCP call (`mcp__codex_dev__codex_dev_run`, `settings.codex.turn_timeout_s`, `le=900`) is already wedge-killed at 120s by an untouched second mechanism: `agent/session_health.py:573` `_classify_tool_tier` buckets any `mcp__`-prefixed tool into `"mcp"` with `TOOL_TIMEOUT_MCP_SEC=120` (`:545`), and the declared-timeout override at `:562-563` is gated to `tool_name == "Bash"` only. Desired Outcome 1 stays false for that named lane after this plan ships. | ACCEPTED as scope-boundary correction, not new work. Recorded in Rabbit Holes + Risks: the tool-tier wedge sub-loop is a SEPARATE budget on a separate signal and is explicitly out of scope for this PR. A follow-up chore issue covers extending the declared-timeout override beyond Bash. |
| CONCERN | Risk & Robustness | The `ENG_IDLE_TIMEOUT_S = 2400` derivation is anchored on "2x the longest known single tool call (~1200s `tests/unit/` run)", but the Bash tool schema caps declared timeout at 600000ms and `session_health.py` independently wedge-kills a stale Bash call past `min(declared, 600) + 60 = 660s`. A genuinely 1200s-silent single Bash call cannot exist today. The chosen magnitude survives (2400 is still ~3.6x 660) but the stated justification is wrong. | ACCEPTED. Rewrite the constant's comment and the Technical Approach §A derivation to anchor on the real ceiling: `TOOL_TIMEOUT_DECLARED_MAX_SEC + TOOL_TIMEOUT_DECLARED_GRACE_SEC = 660s`, giving 2400 as ~3.6x headroom over the tightest external enforcement. No constant change. |
| CONCERN | History & Consistency | Deleting `turn_timeout_for` outright orphans a prose reference: `docs/removed-defenses.md:85` names it as the live replacement mechanism ("No PTY read loop exists; headless turns are bounded by `turn_timeout_for` instead"). Task 10's docs cascade lists only `docs/features/headless-session-runner.md` and `docs/features/config-timeout-catalog.md`. A code-symbol grep cannot catch this; only a repo-wide text grep across `docs/` does. | ACCEPTED. Add `docs/removed-defenses.md` to the Task 10 docs cascade, updating the `[deadman] loop beacon stale` row to name `deadlines_for` as the replacement. |
| NIT | Risk & Robustness | Technical Approach §C prose promises a `max(0.0, ...)` clamp for a future-stamped hook marker, but the code snippet omits it. Harmless for the preempt decision (the `min()` is safe by construction) but a negative idle duration can reach structured logs. | ACCEPTED. Apply the clamp to the logged value in the Task 3 snippet. |

### Verified non-findings

Three claims the plan was least confident about were independently checked and held:

- **`tool_activity_ts` fires for every headless spawn.** The `matcher: ""` PreToolUse liveness hook is registered unconditionally via `adapter.py:516-530` -> `hook_edge.py::generate_hook_settings`, called once per session at `runner.py:590/647` for both `pm` and `teammate` roles. The UNKNOWN fallback stays the edge case; the core mechanism is sound.
- **The "alternative is total silence" justification for keeping a user-facing notice.** `router.py:434` confirms `TURN_TIMEOUT` carries `wrapup_eligible=False`, and the `wrapup_trigger` computation at `runner.py:1031` confirms the wrap-up path never fires for it. Dropping the notice to log/Sentry-only would leave the user with nothing.
- **The external no-output budget does not shadow the new 2400s idle window.** `agent/session_health.py:1-9` states the detector "does NOT kill on inference from absence of expected activity," and `_tier2_reprieve_signal` (`:1914-1948`) reprieves any session whose OS subprocess is still alive -- exactly the nested-tool-call-in-flight case. `session_stall_classifier.py` is advisory/zero-write and cannot compete.

### Scope note added during aggregation

`agent/hooks/pre_tool_use.py:548-554` stamps `current_tool_name`/`current_tool_started_at` on the AgentSession via `record_tool_boundary` for EVERY tool call, including tools called inside a nested subagent (same session env). The tool-tier wedge sub-loop therefore already behaves as a per-tool idle detector at 30s/120s/300s tiers, refreshed continuously during healthy work and cleared by PostToolUse between calls. It is a per-TOOL budget, not a per-TURN one, so it does not shadow the plan's turn-level idle deadline -- but the two are adjacent budgets on adjacent signals and the plan must say so explicitly rather than leave a reader to discover it.

## Open Questions

1. **Is 2400s the right idle window?** It is derived from measured data (2x the longest known single tool call, 4x the `TaskOutput` block), but "longest single tool call" is a moving target - a future 45-minute test-suite invocation would breach it. Accept 2400 as provisional and env-overridable, or size it off the absolute ceiling instead?
2. **Is 21600s (6h) the right absolute ceiling?** The largest observed healthy turn is 8549s, so 6h gives about 2.5x headroom. Higher makes a runaway more expensive; lower risks clipping a legitimately enormous pipeline.
3. **Teammate sessions**: the plan keeps their effective behavior identical (idle 900 / absolute 900). Should a teammate turn get a distinct, shorter idle window now that the concept exists?
4. **`AskUserQuestion`**: in an interactive session it blocks on human think-time (measured median 258s, p95 4214s) with no tool activity in between. Headless `claude -p` sessions should not reach it, but if any path can, a long human pause would read as idle. Worth an explicit assertion that the headless spawn cannot surface it, or an explicit carve-out?
5. **Should the reworded notice be sent at all?** The plan recommends yes, on the grounds that `wrapup_eligible=False` makes the alternative total silence. Confirm that is the call.
