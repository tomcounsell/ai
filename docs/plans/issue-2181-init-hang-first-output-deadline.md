# Runner init-hang: first-output deadline + init-hang circuit breaker (#2181)

## Root cause

An Eng session hung at runner init: the `claude -p` subprocess started and held
its process (heartbeats fired) but never emitted a single SDK message
(`communicated=False` — `derive_sdk_ever_output` False). Zero turns, tokens, or
tool calls. Every safety net missed it and recovery re-spawned the identical
input into the identical hang three times over ~70 min.

Why each net failed:

1. **No first-output deadline.** `communicated=False` is logged every 60s but
   never acted on. The only backstop was the 1800s `SESSION_PROGRESS_DEADLINE_S`
   catch-all in the owned-task progress watcher (`agent/agent_session_queue.py`).
2. **Hang-probe blind spot.** `_owned_task_hang_check` → `subprocess_hang_verdict`
   only returns `"hung"` on flat CPU + no children + no established API socket. An
   init hang that holds an API socket (model connect) or MCP-server children reads
   `"progressing"` forever and falls through to the 1800s deadline.
3. **No circuit breaker.** Recovery requeued the identical zero-output input to
   `pending`, re-spawning the identical hang instead of failing after the first.
4. **No diagnostics.** The runner's stderr/stdout was not captured on a
   `communicated=False` kill, so the real blocker (MCP load / oauth / model
   connect) was not knowable.

## Approach

Scoped to the four named deliverables:

1. **First-output deadline** (`agent/agent_session_queue.py`): in the owned-task
   progress watcher, recover a session that has never produced SDK output past
   `FIRST_OUTPUT_DEADLINE_S` (anchored to the classifier's never-started ceiling
   `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS ≈ 1230s`, well
   below the 1800s catch-all, above the documented ~20-min slow-cold-start
   ceiling), INDEPENDENT of the flat-CPU probe. This closes the blind spot: a
   socket-/child-holding init hang is caught by the first-output deadline even
   when the flat-CPU probe never fires.
2. **Circuit breaker** (`agent/session_health.py`): a kill on a never-communicated
   session uses `reason_kind="init_hang"`, which `_apply_recovery_transition`
   finalizes `failed` (or `abandoned` for local) on the FIRST occurrence instead
   of requeuing — re-spawning identical input reproduces the identical hang. Cap
   is inherently 1.
3. **Diagnostics** (`agent/session_runner/harness/claude.py`): on a teardown that
   kills the subprocess before it ever emitted a stdout event
   (`not _first_stdout_seen` — the `communicated=False` shape), drain and log the
   buffered stderr so the real init blocker is knowable. Best-effort, bounded,
   never masks the cancellation.

## Success Criteria

- A running session with a live subprocess that has never produced SDK output
  (`derive_sdk_ever_output` False) is recovered on `FIRST_OUTPUT_DEADLINE_S`
  (~1230s), independent of the flat-CPU hang probe.
- A never-communicated init hang holding an API socket / MCP children (flat-CPU
  probe returns `"progressing"`) is still caught by the first-output deadline.
- The first zero-output init hang finalizes the session terminal
  (`failed`/`abandoned`) and is NOT requeued to `pending`.
- The runner's buffered stderr is captured and logged on a `communicated=False`
  kill.
- Scoped unit tests pass; no regression in existing `no_progress` recovery tests.

## No-Gos

- Do not shorten the never-started grace for sessions that legitimately cold-start
  slowly (Opus + MCP fleet); the first-output deadline sits at the documented
  ceiling, not below it.
- Do not requeue a zero-output init hang.

## Update System

No update system changes required — this is purely internal worker/runner logic;
no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — no new CLI entry point or bridge import. The
change is internal to the worker's session-recovery path.

## Failure Path Test Strategy

Unit tests drive `_apply_recovery_transition` with `reason_kind="init_hang"` and
assert it finalizes terminal (never requeues to pending), plus the pure
first-output-deadline predicate and the `init_hang` reason-kind selection in the
watcher.

## Test Impact
- [ ] `tests/unit/test_never_started_recovery.py` — no change; existing
  `reason_kind="no_progress"` assertions are unaffected.
- New: `tests/unit/test_init_hang_circuit_breaker.py` — covers the first-output
  deadline predicate, the `init_hang` circuit-breaker terminal finalize, and the
  diagnostics capture.

## Rabbit Holes

- User-facing messaging coalescing and the `killed`-vs-`failed` finalize
  reconciliation (issue's remaining acceptance bullets) are out of this focused
  lane; the circuit breaker removes the re-spawn that produced the duplicate
  notice in the first place.

## Documentation
- [ ] Add a "First-output deadline + init-hang circuit breaker (#2181)" subsection
  to `docs/features/bridge-worker-architecture.md` describing: the
  `FIRST_OUTPUT_DEADLINE_S` recovery path in the owned-task progress watcher, the
  `init_hang` reason-kind that finalizes terminal instead of requeuing, and the
  harness stderr-capture diagnostic on a `communicated=False` kill.
- [ ] Note the `FIRST_OUTPUT_DEADLINE_S` and `init_hang` telemetry counter
  (`{project}:session-health:init_hang_circuit_break`) so operators can find them.
</content>
