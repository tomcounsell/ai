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
| `role_driver.py` | `HeadlessRoleDriver` — builds the subprocess invocation (prime slash command vs. resume), parses stream-json, reconciles the hook-edge snapshot against the turn's own edges. |
| `router.py` | `classify_pm_prefix` (regex, zero LLM calls; strips the matched routing token from a fallback-classified payload so no raw routing string ever reaches the human) and the exit-classification vocabulary (`CLEAN_EXIT_REASONS`, `WRAPUP_ELIGIBLE_EXIT_REASONS`, `ANOMALY_EXIT_REASONS`). |
| `hook_edge.py` / `hook_forwarder.py` | The turn-end/needs-human signal path: a fail-silent NDJSON forwarder writes each hook event to a per-session file; the consumer tails it with a durable `(event_cursor, byte_offset, fingerprint)` cursor. |
| `transcript_tailer.py` | Incremental JSONL transcript reads for dashboard telemetry (byte-offset cadence, unchanged from the prior implementation). |
| `adapter.py` | Executor-facing construction: delivery callbacks, the four-scalar resume persistence, exit-summary publication. |
| `liveness.py` | Single authoritative `sdk_ever_output` derivation (`derive_sdk_ever_output`), consumed by `agent/session_health.py`'s recovery-path checks. |

`.claude/agents/dev.md` is the `dev` subagent definition — authored from the
former Dev prime command plus the shared WORKER rails, with the
steering/continuation contract baked in at authoring time (a subagent cannot
be handed a continuation protocol after the fact).

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
PM output → router.classify_pm_prefix()
    │  [/user]      → deliver via callbacks, session goes dormant awaiting reply
    │  [/complete]  → wrap-up guard → exit summary → drafter delivery
    │  anything else → continue (bounded compliance nudge, then wrap-up guard
    │                   — never an infinite loop)
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
any legacy `GRANITE__*`/`GRANITE_*` env key is still present, so a stale
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
| `agent/session_runner/role_driver.py` | Subprocess construction, prime vs. resume, stream-json parse |
| `agent/session_runner/router.py` | `classify_pm_prefix`, exit-classification frozensets |
| `agent/session_runner/hook_edge.py`, `hook_forwarder.py` | Turn-end / needs-human hook signal path |
| `agent/session_runner/transcript_tailer.py` | Dashboard telemetry transcript reads |
| `agent/session_runner/adapter.py` | Executor wiring, delivery callbacks, resume persistence |
| `.claude/agents/dev.md` | The `dev` subagent definition |
| `.claude/commands/roles/` | Role prime commands (`/roles:prime-{pm,dev,teammate}-role`) |
| `models/agent_session.py` | `claude_session_uuid`, `dev_agent_id`, `runner_cwd`, `claude_version` fields |

## See Also

- [Bridge/Worker Architecture](bridge-worker-architecture.md) — where the runner sits in the enqueue → execute → deliver pipeline
- [Eng Session Architecture](eng-session-architecture.md) — session-type discriminator and routing
- [Session Steering](session-steering.md) — the turn-boundary inbox the preempt watcher consumes
- [Granite OAuth Token Prevention](../infra/granite-oauth-token.md) — the auth credential the runner injects
