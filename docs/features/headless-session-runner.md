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
(`/do-build`, `/do-pr-review`) are not: each gets one
non-resumable turn and must reach terminal state before returning. See
[SDLC Fork Turn-Boundary Invariant](sdlc-fork-turn-boundary.md) for that
invariant and the test that guards it.

## Turn Loop

```
worker claims AgentSession → executor builds a SessionRunner (no transport
resolution — there is exactly one transport)
    │
    ▼
turn-start belt resolution (`belt_resolver.resolve_belt`, dark behind
`TOOLBELTS_ENFORCE`): compiles the role's `config/toolbelts/{role}.toml` into
`--tools` / `--mcp-config` / `--strict-mcp-config` argv, ahead of the
positional message. An unresolvable belt refuses the turn with no spawn.
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

Belt resolution runs only when the spawn carries a `role`, so the drafter,
probe, and drafter-review spawns are untouched. With `TOOLBELTS_ENFORCE` off
(the committed default) `resolve_belt` is never called and the argv is
byte-identical to the pre-belt form; the turn-start enforce-state stamp runs
either way. See [Persona Toolbelts](persona-toolbelts.md).

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

**Live identity.** `_on_turn_spawn` stamps the fenced execution record
(`AgentSession.stamp_execution_spawn` — `exec_pid`, `pid_create_time`, `exec_cwd`,
`exec_harness`, plus an append-only `spawn_history` entry) **before** the
turn-await blocks, so the recovery path's `_confirm_subprocess_dead` targets the
real process. The fence is **not cleared between turns** — staleness is detected
by comparing `pid_create_time` (`agent/pid_fence.py::fence_is_live`), so a
dead or recycled pid reads as not-live rather than relying on the field being
nulled. The recovery path reads the fenced `exec_pid`/`pid_create_time` via
`entry.live_fence` and confirms/escalates against that. For processes the
runner spawns itself, the retained child handle (`_TurnHandle`, below) is the
primary liveness mechanism; the fence is the backstop for cross-process reads.
See [`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md). The
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
read on the normal resume path. **No count-based trim** (durability plan
#2494): the event stream is the forensic record and is bounded by the
session's TTL (`Meta.ttl`), not by an arbitrary entry count — a trimmed-away
authorship or delivery event made a delivered session look "owed" to the
at-rest health check, which depends on the full record surviving for the
session's lifetime.

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
  header (`agent/session_runner/harness/claude.py`) scopes the session to — so the header's "ignore
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
(both env-overridable, provisional). Unknown keys are ignored
(`extra="ignore"`), so any override must use the `SESSION_RUNNER__` prefix
exactly.

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

### Mid-turn tool activity (the `.toolactivity` marker, 2026-07-30)

`derive_sdk_ever_output` answers *has it ever spoken*. The progress-deadline
watchdog needs the different question *has it spoken recently*, and for a
session running against a repo other than this one it had no way to answer:
`last_tool_use_at` is written by `.claude/hooks/pre_tool_use.py`, which lives
in **this repo's** project settings and nowhere else, and `last_turn_at` is a
turn-END signal that cannot tick mid-turn. So a Cyndra/client-repo PM row
carried `None` in both fields for its entire life, the freshness clock in
`agent_session_queue._session_progress_ts` collapsed to `acquired_at`, and
`SESSION_PROGRESS_DEADLINE_S` became a hard 30-minute cap on every turn —
three kills in 24h on one thread, at 1799.99–1800.4s, twice mid-deploy.

The runner now carries its own repo-independent signal:

- `agent/session_runner/liveness_hook.py` is registered by
  `generate_hook_settings` on a **`matcher: ""` PreToolUse** entry, so it runs
  on every tool call — including calls made from inside an in-process
  subagent, which fire the parent's hooks (verified empirically against the
  real CLI: a subagent `Bash` call fires the parent's `PreToolUse` with the
  parent `session_id`). This is what keeps the clock alive across the long
  `Agent` tool call a PM spends most of a turn inside.
- It writes one Unix timestamp to
  `<hook-edge-dir>/<session_id>/<role>_hook_edges.toolactivity` and is
  **stdlib-only** by hard requirement: a `PreToolUse` hook is a fresh process
  per tool call, and importing the ORM to write the field directly measured
  ~2.0s versus ~0.07s stdlib-only — a ~30x tax on every tool call in every
  session. The worker reads the marker on its existing 30s watchdog poll via
  `liveness.tool_activity_ts`, so the Redis write never lands on the hot path.
- It does **not** stamp `current_tool_name`. That field arms the per-tool
  timeout sub-loop (`session_health._check_tool_timeout`, 300s default tier);
  the deadline clock only needs freshness, so this fix does not arm a killer
  for sessions that never had one.
- It always exits 0 and prints nothing. A `PreToolUse` hook exiting 2 *blocks
  the tool call* — a liveness stamp must never be able to stop a turn.

Residual, accepted: an orphaned `claude -p` from a prior turn that is still
making tool calls would keep stamping the same session's marker and mask the
deadline for the current turn. The flat-CPU hang probe, the first-output
deadline, and the worker-startup orphan sweep all remain independent of this
signal and still fire.

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

`ExitReason` (`router.py`) is a distinct, higher-level classification from
`HarnessExitClass` (`agent/session_runner/harness/claude_diagnostics.py`):
`ExitReason` labels how a whole turn ended for the role driver / wrap-up
logic; `HarnessExitClass` labels *why the harness subprocess itself* exited
early with no result event (TLS-trust, auth, missing binary, stale UUID,
benign clean-exit, or generic nonzero), and drives the per-class Sentry
tagging/fingerprint split at BRANCH C in `claude.py` (issue #2219). See
[Claude Child Keychain/TLS Diagnostics](claude-child-keychain-tls-diagnostics.md#sentry-bucket-split-by-exit-class-issue-2219)
for the full classifier and Sentry-split reference.

## Foreground-Only Subagents (issue #2420)

The Overview invariant — *"the parent `-p` process blocks until the subagent
finishes"* — holds **only** for foreground subagents. The `Task`/`Agent` spawn
tool defaults to **background**, and a backgrounded subagent silently breaks
it: a PM (eng) session could spawn a background `dev` subagent, emit a
user-facing `route:"user"` ack, and end its turn *before* the subagent
finished. That turn classifies as a clean `pm_user` exit, so the session is
marked `completed` — while the subagent is still running inside the still-alive
`claude -p` process, whose process group is then SIGKILLed at teardown (see
[Subprocess Lifecycle & Teardown Reap](#subprocess-lifecycle--teardown-reap-issue-1938)),
stranding the work with a false "completed". Three prior fixes (#1915, #2051,
#2140) relied on **prose** ("pass `run_in_background: false`") an LLM can drift
past. #2420 replaces prose with a mechanical, two-layer invariant.

**Layer 1 — enforced prohibition (PreToolUse hook).**
`_enforce_foreground_subagents(hook_input)` in `.claude/hooks/pre_tool_use.py`
(called from `main()`, right after `_enforce_tool_budget`) denies any
`Task`/`Agent` spawn in a **positively-resolved `eng` session** whose
`run_in_background` is not *explicitly* `False` (i.e. `True` **or omitted** —
omission resolves to the background default). The deny prints `HOOK BLOCK: …`
naming `run_in_background: false` and issue #2420, then `sys.exit(2)` (the
Claude Code block convention), forcing a foreground re-issue. It mirrors
`_enforce_tool_budget`'s **fail-open** contract: an unresolved session, a
resolution infra error, or a bug inside the check *allows* the tool (a local
TUI / ad-hoc `claude` process that legitimately backgrounds agents must not be
broken) — only a positively-resolved `eng` session is gated. Because failing
open on an *unresolved* session is the exact worker condition under which this
enforcement could silently vanish, every fail-open on a `Task`/`Agent` spawn
emits an observable `[foreground-guard] fail-open: <reason>` line to stderr
(`reason` ∈ `unresolved-session` / `resolution-error` / `internal-error`) and
bumps the shared resolution-error counter — the seam a future fail-open-rate
dashboard will read.

Omission is denied because the harness genuinely leaves the key out: probed
against a live `claude -p` run, a `Task` call issued without the flag reaches
the PreToolUse hook with `run_in_background` **absent** from `tool_input`, and
the tool's own contract is that an absent flag means background. Treating
absence as foreground would let the exact #2420 dispatch shape through.

Two things make that deny land rather than warn. The manifest declares
`pre_tool_use` with `exit_policy = "deny-only"` so the generated command passes
exit 2 through while still mapping a crash to 0; under the earlier blanket
`|| true` the `sys.exit(2)` printed its message and reported success (#2527, see
[Hook Registration Manifest](hook-manifest.md)). And every `Task({...})`
template in the eng-reachable prompt surface carries an explicit
`run_in_background: false`, enforced per-template by
`tests/unit/test_sdlc_fork_no_background.py` across both skill trees,
`.claude/agents/`, `.claude/commands/`, `config/personas/`, and `docs/sdlc/`.
The predecessor test globbed `**/SKILL.md` across two named files and could not
see a skill's sub-files, which is how three flagless templates shipped.

**Layer 2 — fail-closed terminal status (defense-in-depth).** If a clean exit
is somehow reached with a spawned subagent still in flight (a future SDK
change, a slipped path), finalization must not report `completed`. The runner's
finalization chokepoint (`runner.py`, after the wrap-up guard, before
`publish_exit_summary`) downgrades **any clean exit** —
`summary.exit_reason.is_clean`, covering all four wrap-up-eligible clean
reasons `pm_complete` / `pm_user` / `pm_needs_human` / `pm_floor_delivered`
(plus `steer_abort`) — to the new non-clean anomaly `PM_USER_SUBAGENT_LIVE`
when `subagent_in_flight(...)` returns `True`, so `_runner_final_status` returns
`"failed"` (needs-attention), never a false `"completed"`. Scoping to the
`is_clean` predicate at the chokepoint *after* the wrap-up guard — rather than
to the two literals `pm_user`/`pm_complete` after the route decision — is
load-bearing: the wrap-up guard is what assigns `pm_floor_delivered` and can
reassign the others, so a two-literal narrowing would sail past a stranded
subagent under `pm_needs_human` or `pm_floor_delivered` (the #2140 point-fix
trap).

`subagent_in_flight(cwd, claude_session_id, *, projects_root=None, window_s=...)`
(`adapter.py`, next to `sidechain_agent_ids`) checks **every** sidechain
`agent-*.jsonl` transcript of the session, not just the most recently modified
one — with dev-A live and dev-B finished more recently, inspecting only the
newest file reports "not in flight" while A is still running.

A subagent is in flight when its transcript is **both** still being written to
(mtime within `SUBAGENT_LIVE_MTIME_WINDOW_S`, 900s) and **not** closed by a
finished assistant answer. Both halves are required. Recency alone flags a
subagent that finished a second ago; shape alone flags every stranded
transcript from an earlier turn, because sidechain files accumulate under the
claude session id rather than the turn.

"Finished" means an `assistant` record carrying a `text` block, with no
`tool_use` block and `stop_reason != "tool_use"`. Ground truth, measured by
replaying 1573 real on-disk sidechains: the JSONL has **no `result`/`Stop`
record type** (records are `user` / `assistant` / `attachment`); 1454 close on
exactly that assistant-text shape and the other 118 end mid-exchange. Of the
closing records 1224 carry `stop_reason == "end_turn"`, 207 `None` (the
streaming SDK-CLI flush), and 23 `stop_sequence` — so keying completion on
`end_turn` alone would false-downgrade 15% of finished subagents.

**The just-spawned window is the case that matters.** 1571 of 1573 real
sidechains open with a `user` record whose `message.content` is a plain
*string* — the task prompt, written the instant the subagent spawns and before
it has produced anything. That is the fire-and-forget window itself: the PM
backgrounds a dev, the transcript holds only that record, the PM acks and exits
clean. Replaying every transcript truncated to its first record detects
**1573/1573 (100%)**; across every proper prefix (a uniformly sampled live
moment) it detects 87.4%. Any error, missing file, or empty input returns
`False` — the probe must never crash finalization.

**Why this does not reintroduce the #1915/#2051 phantom-wait.** The earlier
phantom-wait bugs came from a parent *ending its turn while a live background
child ran*, then entering a wait-for-notification state nothing fulfilled. This
remedy forbids exactly that shape by forbidding background children: with
backgrounding denied, a `dev` subagent is always foreground, the PM turn
**blocks synchronously** inside the `Task` call until the subagent finishes,
and `route:"user"` can only be emitted *after* completion. There is no live
background child at turn end, so there is no notification to wait for — the PM
never enters a waiting state. Interim responsiveness is preserved: a PM may
emit a pre-delegation `route:"user"` status ack *before* the blocking `Task`
call (the adapter's user callback fires mid-turn), so foreground-only
enforcement does not force silence during a long build. No new silence class is
introduced — foreground subagents already blocked the turn; backgrounding was
never a supported parallelism mechanism, it was the bug.

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
| `agent/session_runner/belt_resolver.py` | Turn-start persona toolbelt resolution, enforce-state stamp, `[missing-capability]` escalation forwarding |
| `agent/session_runner/router.py` | `classify_pm_prefix`, `ExitReason` StrEnum, `TurnFailure`, derived exit-classification frozensets |
| `agent/session_runner/hook_edge.py`, `hook_forwarder.py` | Turn-end / needs-human hook signal path |
| `agent/session_runner/transcript_tailer.py` | Dashboard telemetry transcript reads |
| `agent/session_runner/adapter.py` | Executor wiring, delivery callbacks, resume persistence, `subagent_in_flight` liveness probe (#2420 Layer 2) |
| `.claude/hooks/pre_tool_use.py` | `_enforce_foreground_subagents` — foreground-only subagent PreToolUse guard for eng sessions (#2420 Layer 1) |
| `.claude/agents/dev.md` | The `dev` subagent definition |
| `.claude/commands/roles/` | Role prime commands (`/roles:prime-{pm,dev,teammate}-role`) |
| `models/agent_session.py` | `claude_session_uuid`, `dev_agent_id`, `runner_cwd`, `claude_version` fields |

## See Also

- [HarnessAdapter Seam](harness-adapter.md) — the extracted claude-`-p` subprocess/argv/stream-json knowledge this runner drives through
- [Persona Toolbelts](persona-toolbelts.md): the turn-start belt resolution, its fail-closed cases, and the per-tool cost attribution folded into the stream-json parse path
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — where the runner sits in the enqueue → execute → deliver pipeline
- [Eng Session Architecture](eng-session-architecture.md) — session-type discriminator and routing
- [Session Steering](session-steering.md) — the turn-boundary inbox the preempt watcher consumes
- [Agent Teams Headless Policy](agent-teams-headless-policy.md) — why every headless spawn disables Claude Code agent teams (in-process teammates don't survive the per-turn `--resume`), and the `--settings` override that enforces it
- [Granite OAuth Token Prevention](../infra/granite-oauth-token.md) — the auth credential the runner injects
- [Claude Child Keychain/TLS Diagnostics](claude-child-keychain-tls-diagnostics.md) — `HarnessExitClass` early-exit classifier and the per-class Sentry bucket split at BRANCH C
