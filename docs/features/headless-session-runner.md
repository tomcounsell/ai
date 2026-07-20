# Headless Session Runner

**Status:** Shipped (issue #1924)

## Overview

Every session role — PM, Dev, Teammate — executes as a headless `claude -p
--output-format stream-json` subprocess, driven by `agent/session_runner/`.
There is no PTY, no interactive TUI, and no ollama in the session-execution
path. Turn-end comes from the protocol itself: a stream-json `result` event
reconciled against a Stop-hook envelope, never from scraping what a terminal
painted.

A session is one top-level `claude -p` process: the **PM**. For eng work the
PM spawns and continues a resumable **`dev` subagent** *inside its own turn*,
using the harness's native agent-continuation mechanism — the parent `-p`
process blocks until the subagent finishes, so a single PM turn can
legitimately contain an entire multi-file build. There is no relay loop, no
process pool, no idle-scraping startup phase.

## Module Map (`agent/session_runner/`)

| Module | Role |
|--------|------|
| `runner.py` | The single-session turn loop for every session type: spawn one `claude -p` per turn, route the PM's output, run the steer-preempt watcher, own resume-scalar persistence timing. |
| `role_driver.py` | `HeadlessRoleDriver` — drives one turn through `harness.claude.ClaudeHarnessAdapter` (see [HarnessAdapter Seam](harness-adapter.md)), which owns all subprocess handling; the driver adds persona priming, `--resume` continuation, turn-end reconciliation, claude-session-id capture (assert-and-alarm on drift, plan #2000 Task 2.1), and the bounded hung-subprocess guard. |
| `harness/` | `HarnessAdapter` protocol + `TurnRequest`/`TurnResult`/`TurnEvent` + the `claude -p` adapter (`ClaudeHarnessAdapter`) — all claude-specific argv/env/stream-json knowledge lives here, extracted from the pre-seam `agent/sdk_client.py`. See [HarnessAdapter Seam](harness-adapter.md). |
| `router.py` | `PM_TURN_JSON_SCHEMA` + `validate_structured_route` (schema-first routing, plan #2000 Task 2.3 — zero LLM calls, zero text parsing) with `classify_pm_prefix` (regex) demoted to a telemetered fallback for when `structured_output` is absent/invalid; strips the matched routing token from a fallback-classified payload so no raw routing string ever reaches the human. Also the `ExitReason` StrEnum (issue #2004), whose per-member `is_clean`/`wrapup_eligible`/`is_anomaly` declarations derive `CLEAN_EXIT_REASONS`, `WRAPUP_ELIGIBLE_EXIT_REASONS`, `ANOMALY_EXIT_REASONS` — see [Exit Classification](#exit-classification-exitreason-issue-2004) below. `pm_user` (a real `route: "user"` answer the PM chose to deliver) and `pm_needs_human` (a runner-forwarded needs-input prompt, from a `needs_human` hook edge firing on an otherwise-unroutable turn) are both clean, wrap-up-eligible exits — kept distinct so the dashboard and reaction gate can tell "the PM answered" from "the PM paused, waiting on the human" (issue #1922). See [HarnessAdapter Seam § Schema Routing](harness-adapter.md#schema-routing-task-23) for the full contract. |
| `hook_edge.py` / `hook_forwarder.py` | The turn-end/needs-human signal path: a fail-silent NDJSON forwarder writes each hook event to a per-session file; the consumer tails it with a durable `(event_cursor, byte_offset, fingerprint)` cursor. |
| `transcript_tailer.py` | Incremental JSONL transcript reads for dashboard telemetry (byte-offset cadence, unchanged from the prior implementation). |
| `adapter.py` | Executor-facing construction: delivery callbacks, the four-scalar resume persistence, exit-summary publication. |
| `liveness.py` | Single authoritative `sdk_ever_output` derivation (`derive_sdk_ever_output`), consumed by `agent/session_health.py`'s recovery-path checks. |

`.claude/agents/dev.md` is the `dev` subagent definition — authored from the
former Dev prime command plus the shared WORKER rails, with the
steering/continuation contract baked in at authoring time (a subagent cannot
be handed a continuation protocol after the fact).

`dev` itself is resumable, but the leaf `context: fork` skills it calls
(`/do-build`, `/do-plan-critique`, `/do-pr-review`) are not: each gets one
non-resumable turn and must reach terminal state before returning. See
[SDLC Fork Turn-Boundary Invariant](sdlc-fork-turn-boundary.md) for that
invariant and the test that guards it.

## Turn Loop

```
worker claims AgentSession → executor builds a SessionRunner (no transport
resolution — there is exactly one transport)
    │
    ▼
runner.run_turn(): spawn `claude -p --output-format stream-json [--resume <uuid>]`
  in the session's working_dir
    │  turn 1 → primes via the role's `/roles:prime-{pm,dev,teammate}-role`
    │           slash command
    │  resumed turn → raw steer/reply text only
    ▼
PM turn runs; for eng work the PM spawns/continues its `dev` subagent inline
    │
    ▼
PM output → SessionRunner._classify_turn() (schema-first, plan #2000 Task 2.3)
    │  structured_output present → router.validate_structured_route()
    │      route: "user"     → deliver via callbacks, session goes dormant awaiting reply
    │      route: "complete" → wrap-up guard → exit summary → drafter delivery
    │      route: "continue" → continue (no compliance-miss)
    │  structured_output absent/invalid → router.classify_pm_prefix() fallback
    │      (prefix-regex; emits schema_routing_fallback telemetry)
    │  neither classifies → continue (bounded compliance nudge, then wrap-up
    │                        guard — never an infinite loop)
    ▼
turn end reconciled: stream-json `result` event (usage, cost, is_error)
  cross-checked against the hook-edge `Stop` envelope
```

## Steer-Preempt (D4)

A watcher polls the Redis steering list (`agent/steering.py`) during the
turn. On a substantive steer it terminates the in-flight subprocess's own
process group: SIGTERM → a bounded grace window → SIGKILL. The kill is
generation-token-guarded — the watcher records `(turn_generation,
process_handle)` at spawn and only acts if both still match, so a steer that
lands just as a turn finishes naturally can never kill the *next* turn's
process. The next turn `--resume`s with the steer injected as its first
message. A per-turn timeout is handled by the identical path
(`turn_end_source="timeout"`) — expiry is a graceful preempt, not an error;
partial work stays in the transcript and the session surfaces as
needs-attention rather than silently discarding a long Dev build.

## Subprocess Lifecycle & Teardown Reap (issue #1938)

The runner is the single owner of its subprocess's teardown. On **any** unwind
of `_run_one_turn` — external cancellation from the health-check recovery path,
an exception, or a normal turn exit — the `finally` block SYNCHRONOUSLY
SIGKILLs and confirms the turn's process group before it returns.

The reap is **cancellation-proof by construction**: it issues `os.killpg(pgid,
SIGKILL)` with no preceding `await` and confirms exit via a bounded
`time.sleep` poll (`SESSION_RUNNER_REAP_CONFIRM_TIMEOUT_S`, default 1.0s), so a
re-delivered `CancelledError` cannot abort it. This matters because the recovery
path double-cancels — `handle.task.cancel()` then `wait_for(handle.task, 0.25s)`
re-cancels on timeout — and a SIGTERM→await-grace→SIGKILL reap would be aborted
mid-grace after only SIGTERM, orphaning a live `claude -p` parented to the
worker. SIGKILL is uncatchable so death is near-instant; the poll cap only bounds
a pathological unkillable/D-state group. (This fast-kill is teardown-only —
steer/timeout preempts keep the graceful SIGTERM→grace→SIGKILL path above.)

Because Python runs the inner-task `finally` to completion before `await
task._task` (`agent/session_executor.py`) resolves in the outer coroutine that
owns worktree cleanup, the group is provably dead before both the recovery-path
confirm and the executor's synthetic-slug cleanup run — cleanup never mutates the
filesystem under a live child.

**Live identity.** The runner writes the live subprocess pid to
`AgentSession.claude_pid` on spawn (alongside `pm_pid`) and clears it on turn
exit, so the recovery path's `_confirm_subprocess_dead` targets the real process.
The recovery path snapshots `claude_pid` **before** cancelling (the teardown
clears it on the same unwind) and confirms/escalates against that snapshot. The
process group is derived from the pid via `os.getpgid` at kill time (`pgid ==
pid` under `start_new_session`) — no pgid is persisted.

**Pathological unkillable group (manual reclamation).** If the ~1s SIGKILL
confirm cannot verify the group is dead (an uninterruptible D-state child), the
runner writes a durable `runner_reap_failed` session event and logs a WARNING
naming the session. The executor's synthetic-slug cleanup reads that marker and
**skips** worktree deletion, so no directory is removed under a possibly-live
child. Reclaim the orphaned worktree manually once the child clears:

```bash
git worktree prune
rm -rf .worktrees/dev-<8hex>   # the path named in the WARNING
```

## Simple Resume (D3, four scalars)

`AgentSession` carries exactly four flat resume fields plus a bounded
observability mirror — there is no per-role handle list:

| Field | Purpose |
|-------|---------|
| `claude_session_uuid` | The PM session's `--resume` entry point. |
| `dev_agent_id` | The dev subagent's continuation handle. |
| `runner_cwd` | Exact absolute working dir — resume is cwd-scoped. |
| `claude_version` | Continuation behavior is CLI-version-specific. |

`claude_session_uuid` is captured the moment the stream-json `system/init`
event is parsed — *before* the turn is awaited — so a preempted or killed
turn's partial transcript is never orphaned behind a stale pre-turn id.
`dev_agent_id` is captured structurally, never from PM prose: the runner
scans `~/.claude/projects/{slug}/{claude_session_uuid}/subagents/agent-*.jsonl`
for new agent ids after every turn (and after a preempt), because the
sidechain file exists from the moment the subagent spawns.

A compact **turn-history mirror** — `{ts, actor: pm|dev, text}` — is appended
to the existing session-event stream every turn. It is observability and a
disaster-recovery seed if on-disk transcripts are ever garbage-collected; the
on-disk Claude transcripts remain the source of truth and the mirror is never
read on the normal resume path. The event stream is capped at
`SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` (default 200, oldest entries
dropped first, `exit_summary` entries preserved) so a long-lived session's
per-save serialization stays bounded.

Stale or invalid scalars (missing `runner_cwd`, unknown `claude_session_uuid`)
discard cleanly to a cold start with a full first-turn prime — there is no
crash on a bad resume pointer.

### Resume goal re-injection (issue #2136)

The four scalars above re-enter the prior transcript, but they carry only
continuation *plumbing* — none states the session's *objective*. If the
transcript's goal was compacted and the operator (or auto-resume reflection)
passes a generic `--message` like "continue", the resumed session is goalless
and has to ask the human to restate the task.

`resume_session()` (`tools/valor_session.py`) closes that gap: before pushing
the resume message onto the steering list, it folds the record's goal into the
first turn input as:

```
[Prior session context: <goal>]

<message>
```

- **Resolution order** (`_resolve_resume_goal`, first non-empty **string**
  wins): `context_summary` (curated "what this session is about") →
  `message_text` (original task anchor) → latest `summary` event (most recent
  progress marker). Non-string / None / whitespace-only fields are skipped —
  the `isinstance(str)` guard makes augmentation opt-in on a real string goal.
- **Cap:** the folded goal is truncated at `_RESUME_GOAL_MAX_CHARS` (4000) with
  an ellipsis so a multi-KB `message_text` can't balloon the first turn.
- **No double-wrap:** an operator-supplied `--message` that already starts with
  `[Prior session context:` is pushed unchanged.
- **SCOPE-header resolution:** because the goal is folded into the MESSAGE body,
  it sits inside "the message below from this sender" that the harness SCOPE
  header (`harness/claude.py`) scopes the session to — so the header's "ignore
  prior threads" instruction no longer contradicts resume semantics. No change
  to `claude.py` is required, and the fix does not depend on the header being
  applied: the goal travels in `message` whether or not the header wraps it.

The augmented text is pushed as `steering_msgs[0]` and drained by the executor
(`session_executor.py:1716`) as the first turn input; the cold-start
(non-resume) turn path is untouched. This mirrors the continuation-augmentation
pattern at `session_executor.py:2262-2269`.

### Stale-UUID fallback vs. the result-event completion signal (issue #1980)

A resumed (`--resume`) turn whose subprocess exits **non-zero** may need one
fresh-session retry — a genuinely stale/invalid UUID makes `claude --resume`
error out before producing any output. `get_response_via_harness`
(`agent/sdk_client.py`) runs exactly that retry once, without `--resume`, using
the caller's `full_context_message`.

That retry is **gated on whether the primary invocation emitted a `result`
event**, not on whether it exited zero. The invariant: *a `result` event is the
protocol's completion signal.* If the resumed subprocess emitted a `result`
event (`stop_reason: end_turn`) and only *then* exited non-zero — a post-turn or
cleanup artifact — the captured completion is authoritative and the fallback is
**skipped**, keeping the real answer. This mirrors, one layer down, the role
driver's residual-#1916 rule (`role_driver.py`: "a nonzero exit AFTER a result
event keeps the result").

The gate keys off the true `result_event_fired` boolean, captured from the
primary invocation's `on_exit_status(returncode, result_event_fired)` callback —
**not** off the returned `result_text`. `result_text` is a non-empty string in
two distinct cases (a fired result event, or accumulated partial text with *no*
result event from a crashed subprocess), so `result_text is None` cannot
distinguish "resume succeeded" from "crashed with partial text." The fallback
therefore still fires whenever no result event fired (partial text or a genuine
stale UUID), preserving all recovery.

Before this gate existed, a valid completion followed by a non-zero exit
triggered the retry, whose empty output overwrote the good `result_text`;
`get_response_via_harness` returned `""`, `HeadlessRoleDriver.run_turn`'s
`if not reply:` guard set `exit_reason="empty_output"`, and the wrap-up guard
delivered the canned `OPERATOR_TERMINAL_MESSAGE` instead of the real answer.
`OPERATOR_TERMINAL_MESSAGE` is now reserved for a genuinely empty PM turn (no
result event and no recoverable text).

## Auth

The runner sets `CLAUDE_CODE_OAUTH_TOKEN` and strips `ANTHROPIC_API_KEY` in
the subprocess environment explicitly, rather than relying on ambient worker
env. `--bare` is never passed (it does not read
`CLAUDE_CODE_OAUTH_TOKEN`). See [Granite OAuth Token
Prevention](../infra/granite-oauth-token.md) for how the long-lived token
itself is minted and rotated.

## Configuration

`SessionRunnerSettings` (`config/settings.py`), env prefix
`SESSION_RUNNER__`: `pm_model`, `dev_model`, `hook_turn_end_wait_s`,
`hook_crash_resume_cap`, plus the per-turn timeout and the steer debounce
(both env-overridable, provisional). A settings-load warning fires loudly if
a pre-cutover `GRANITE__*`/`GRANITE_*` env key is still present, so a stale
vault override never silently reverts to defaults; `/update` surfaces the
same warning during deploy.

## Worker Without ollama

Session dispatch has no ollama dependency. There is no model probe, circuit
breaker, reprobe loop, or degraded-mode deferral in the worker startup path
— the worker starts straight into recovery and queue pickup. Bridge routing
and email triage keep their own direct ollama calls for classification; that
is a separate concern (`local-model-policy.md`, follow-up #1923) untouched by
this cutover.

## Liveness

Health is protocol-derived, not screen-derived: subprocess-alive plus
hook-edge/turn-record recency. The only ceilings are the per-turn timeout and
`hook_turn_end_wait_s`. A turn whose subprocess exits nonzero without a
`result` event classifies as `exit_reason=headless_nonzero_exit_no_result`
even when partial streamed text accumulated; any non-clean `exit_reason`
finalizes the `AgentSession` as `failed` with a persona-safe user message —
never a false `completed` (closing the class of failure documented in the
[PTY-fragility postmortem](../postmortems/2026-07-06-granite-pty-fragility.md)).

`exit_reason=pm_needs_human` (added in issue #1922) is a clean exit, not a
liveness failure: it fires when a `needs_human` hook edge accompanies an
otherwise-unroutable turn, and the runner delivers the PM's text as a genuine
question to the human. `session_executor.py` recognizes it via the single
imported `CLEAN_EXIT_REASONS` set (no separate literal to drift out of sync),
so it never falls into the `failed`/error-reaction path that a genuinely
non-clean `exit_reason` would.

### Liveness signals (`sdk_ever_output`, issue #1935)

The never-started gate (`_never_started_past_grace`) and the `_tier2_reprieve_signal`
reprieve-cap guard both ask the same question: "has the SDK EVER produced
recognized output?" That question is answered by one function,
`agent.session_runner.liveness.derive_sdk_ever_output(entry)` — the single
authoritative liveness signal, owned by the runner package (owner directive,
2026-07-07: *"One authoritative liveness signal makes the most sense. As much
as we can strengthen a single module, let's do that instead of manipulating
the worker."*). `agent/session_health.py` imports and calls it at all four of
its recovery-path derivation sites instead of inlining the OR expression.

`derive_sdk_ever_output` is `bool(last_tool_use_at OR last_turn_at OR
last_stdout_at)`:

- `last_tool_use_at` — a tool boundary fired (PreToolUse/PostToolUse CLI
  hooks, via `agent.hooks.liveness_writers.record_tool_boundary`).
- `last_turn_at` — a turn boundary completed (the harness `result` event,
  via `agent.hooks.liveness_writers.record_turn_boundary`, called with the
  true `AgentSession.session_id` from `agent/sdk_client.py`'s result-event
  handler).
- `last_stdout_at` — the headless stream produced ANY output at all (the
  `init` event or any subsequent stdout line). Stamped by
  `SessionRunner._stamp_stdout_liveness`, wired via two driver adapters in
  `_build_driver`: a 0-arg `on_stdout_event` adapter, and a 1-arg `on_init`
  adapter that composes with (never replaces) `_on_harness_init`'s
  `claude_session_uuid`/`runner_cwd`/`claude_version` persistence. This is
  the headless replacement for the PTY-era `last_pty_read_loop_at`
  per-stream liveness signal (#1843 Gap B), which the granite teardown
  deleted with no headless equivalent — the root cause of the
  toolless-streaming zombie wedge this section documents the fix for. The
  stamp is fail-silent with a per-session-keyed 5s cooldown (mirrors
  `agent.hooks.liveness_writers.COOLDOWN_WINDOW_SEC`'s discipline) to bound
  Redis write rate; a successful stamp emits a debug-level
  `stdout_liveness_stamped` log line so `grep stdout_liveness_stamped
  logs/worker.log` post-deploy positively confirms the write path is firing.

This is a **presence** check, not a freshness check — it does not by itself
detect a mid-turn hang. A subprocess that streams `init` and then genuinely
hangs is caught by the whole-turn deadline (the preempt watcher's
`_kill_turn(cause="timeout")` and the driver's own `asyncio.wait_for`
backstop), not by session-health — accepting a wider detection window (up to
`turn_timeout_s`, 7200s for PM/eng turns) for that rare case in exchange for
eliminating false zombie verdicts on legitimately toolless-streaming turns.

## Exit Classification (`ExitReason`, issue #2004)

`router.py`'s exit-reason vocabulary is a `class ExitReason(StrEnum)`, not a
plain set of string literals. Each member declares its own classification
inline via `__new__(value, is_clean, wrapup_eligible, is_anomaly)` — e.g.
`PM_COMPLETE = ("pm_complete", True, True, False)` — so adding a member
without deciding its classification fails a completeness test
(`tests/unit/session_runner/test_exit_reason.py`) instead of silently
landing "non-clean" by omission (the issue #1922 defect class this closes).
`CLEAN_EXIT_REASONS`, `WRAPUP_ELIGIBLE_EXIT_REASONS`, and
`ANOMALY_EXIT_REASONS` are now *derived* frozensets —
`frozenset(r for r in ExitReason if r.is_clean)` and so on — rather than
hand-maintained lists that could silently drift out of sync with each other.

Because `ExitReason` members ARE `str` (via `StrEnum`), every existing import
site — plain-string comparisons, frozenset membership checks, telemetry
serialization — keeps working unchanged; the enum values are byte-identical
to the pre-enum vocabulary (`"pm_complete"`, `"pm_user"`,
`"headless_subprocess_error"`, etc.), since `exit_summary` session events and
`AgentSession.exit_reason` depend on the exact strings.

Role-driver turn failures (minted in `role_driver.py`, e.g. a subprocess
crash or a missing binary) used to smuggle exception detail into the reason
string itself (`f"headless_subprocess_error: {e}"`). They now carry a
`TurnFailure(reason: ExitReason, detail: str = "")` dataclass instead, whose
`__str__` reproduces the legacy `"reason: detail"` wire format byte-for-byte
— so `exit_message` telemetry is unchanged on the wire, but callers can
inspect `.reason` (an `ExitReason` member) and `.detail` (free text)
separately instead of re-parsing a string.

## Supersedes

This replaces the granite PTY container substrate in full — the interactive
TUI operator, the per-role transport hedge, the PTY failure-simulation
harness, and the PTY-driven hook-turn-return plumbing (whose surviving
mechanism, hook-edge turn detection, graduated into `hook_edge.py` /
`hook_forwarder.py` above unchanged in contract). See the [PTY-fragility
postmortem](../postmortems/2026-07-06-granite-pty-fragility.md) for why the
prior substrate was retired outright rather than patched again.

## Key Files

| File | Purpose |
|------|---------|
| `agent/session_runner/runner.py` | Turn loop, steer-preempt watcher, resume-scalar timing |
| `agent/session_runner/role_driver.py` | Drives one turn through `HarnessAdapter`, prime vs. resume, turn-end reconciliation |
| `agent/session_runner/harness/{base,claude,events}.py` | `HarnessAdapter` protocol, `TurnRequest`/`TurnResult`/`TurnEvent`, the `claude -p` adapter — see [HarnessAdapter Seam](harness-adapter.md) |
| `agent/session_runner/router.py` | `classify_pm_prefix`, `ExitReason` StrEnum, `TurnFailure`, derived exit-classification frozensets |
| `agent/session_runner/hook_edge.py`, `hook_forwarder.py` | Turn-end / needs-human hook signal path |
| `agent/session_runner/transcript_tailer.py` | Dashboard telemetry transcript reads |
| `agent/session_runner/adapter.py` | Executor wiring, delivery callbacks, resume persistence |
| `.claude/agents/dev.md` | The `dev` subagent definition |
| `.claude/commands/roles/` | Role prime commands (`/roles:prime-{pm,dev,teammate}-role`) |
| `models/agent_session.py` | `claude_session_uuid`, `dev_agent_id`, `runner_cwd`, `claude_version` fields |

## See Also

- [HarnessAdapter Seam](harness-adapter.md) — the extracted claude-`-p` subprocess/argv/stream-json knowledge this runner drives through
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — where the runner sits in the enqueue → execute → deliver pipeline
- [Eng Session Architecture](eng-session-architecture.md) — session-type discriminator and routing
- [Session Steering](session-steering.md) — the turn-boundary inbox the preempt watcher consumes
- [Agent Teams Headless Policy](agent-teams-headless-policy.md) — why every headless spawn disables Claude Code agent teams (in-process teammates don't survive the per-turn `--resume`), and the `--settings` override that enforces it
- [Granite OAuth Token Prevention](../infra/granite-oauth-token.md) — the auth credential the runner injects
