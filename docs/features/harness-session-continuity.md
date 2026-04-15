# Harness Session Continuity

**Status:** Shipped (issue #976)
**Owner:** agent/sdk_client.py, agent/agent_session_queue.py
**Related:** [harness-abstraction.md](harness-abstraction.md), [pm-dev-session-architecture.md](pm-dev-session-architecture.md), PR #909

## Problem

Dev sessions executed via the CLI harness (`claude -p` subprocess) used to rebuild
the entire reply chain, project header, scope text, and steering message into a
single positional `argv` on every turn. On long Telegram threads that argv
crossed the binary's internal chunk limit (~200KB), and the subprocess crashed
with `Separator is found, but chunk is longer than limit`. The prior band-aid
(`_apply_context_budget()`, #958) trimmed the argv string to ≤100KB but could
not stop the binary from overflowing on intra-turn tool output.

## Solution

The harness now uses the Claude CLI's native `--resume` continuity:

1. **First turn**: build the full-context message (`PROJECT` / `FROM` /
   `SESSION_ID` / `TASK_SCOPE` / `SCOPE` / `MESSAGE`), spawn
   `claude -p ... [full_context]`, capture the `session_id` from the stream-json
   `result` event, and persist it on the `AgentSession` record's
   `claude_session_uuid` field via `_store_claude_session_uuid()`.
2. **Subsequent turns**: look up the prior UUID via
   `_get_prior_session_uuid(session_id)`. When found and UUID-valid, spawn
   `claude -p --resume <uuid> ... [raw_new_message]`. The binary loads prior
   context from its on-disk session file; the positional argv stays bounded by
   the size of the new user message alone.

Context overflow becomes structurally impossible for resumed turns. On first
turns and on pathological single mega-messages (pasted transcripts, forwarded
logs), `_apply_context_budget()` still bounds the argv.

## Two Harness Turn Shapes

### Shape A — First turn (no prior UUID)

```
claude -p --verbose --output-format stream-json [full_context_message]
```

`full_context_message` is built by `build_harness_turn_input(skip_prefix=False)`
and carries the `PROJECT` / `FROM` / `SESSION_ID` / `TASK_SCOPE` / `SCOPE` /
`MESSAGE:` headers. `_apply_context_budget()` is applied before spawn.

### Shape B — Resumed turn (prior UUID present and valid)

```
claude -p --verbose --output-format stream-json --resume <uuid> [new_user_message]
```

`new_user_message` is built by `build_harness_turn_input(skip_prefix=True)` and
contains only the raw user message body — no context headers. The binary
already has that context from its session file. `_apply_context_budget()` is
still applied unconditionally to bound pathological single messages.

## Role of `claude_session_uuid` on `AgentSession`

The same Popoto field that the SDK path uses (PR #909) now serves the harness
path too. Every successful `get_response_via_harness()` call that receives a
`session_id` argument writes the captured UUID to the matching
`AgentSession.claude_session_uuid`. The next turn's
`_get_prior_session_uuid()` reads it and passes it into the harness.

Serialization is handled by the worker's per-session queue: two turns for the
same `session_id` are never in flight simultaneously, so the write-before-next-read
invariant holds without additional locking.

## Stale-UUID Fallback

A prior UUID can become stale if the on-disk session file is deleted, the binary
is upgraded to an incompatible format, or the file is never created (e.g.
first-turn binary crash before flush). When `prior_uuid` was set and the
subprocess exits with **any** non-zero return code, the harness retries once
without `--resume`, using the `full_context_message` that the dispatcher passed
as a fallback argument.

The fallback is unconditional on exit code — it does **not** inspect stderr.
Substring matching (e.g. `"requires a valid session"`) against the CLI's error
text was rejected because:

- CLI version drift changes the error phrasing.
- Non-English locales emit localized errors.
- An unnecessary retry on a real (non-stale-UUID) error costs only one extra
  subprocess spawn — strictly cheaper than a silent stuck-empty turn.

## Why `_apply_context_budget()` Is Retained

Even on resumed turns, the single new user message is the positional argv. A
Telegram message can carry a forwarded transcript or a pasted log that alone
exceeds the chunk limit. `_apply_context_budget()` runs unconditionally:

- On first turns: bounds the reconstructed full-context message.
- On resumed turns with typical small messages: a no-op (one length comparison).
- On resumed turns with pathological mega-messages: trims to the safe ceiling,
  preserving the fix against the original "Separator is found" crash.

The optimization "skip the budget when resuming" is explicitly rejected — it
saves nothing on small messages and reintroduces the original crash on large
ones.

## Observability

Two log lines enable grep-based tracking of harness continuity in production:

- **INFO** `[harness] Resuming Claude session <uuid> for session_id=<sid>` —
  emitted on every `--resume` injection.
- **WARNING** `[harness] Stale UUID <uuid> for session_id=<sid>, falling back
  to first-turn path` — emitted on every fallback trigger.

Resume hit-rate: `grep -c 'Resuming Claude session' logs/worker.log`.
Fallback incidence: `grep -c 'Stale UUID' logs/worker.log`.

## File:line Index

- `agent/sdk_client.py`
  - `_get_prior_session_uuid()` — Popoto lookup of prior UUID by `session_id`.
  - `_store_claude_session_uuid()` — Popoto write of UUID after each turn.
  - `_apply_context_budget()` — argv size ceiling (retained safety net).
  - `_UUID_PATTERN` — UUID v4 regex for input validation before `--resume`.
  - `get_response_via_harness()` — harness subprocess entry point with
    `prior_uuid`, `session_id`, and `full_context_message` keyword args.
  - `_run_harness_subprocess()` — the actual `asyncio.create_subprocess_exec`
    call, returns `(result, session_id, returncode)`.
  - `build_harness_turn_input()` — context-prefix builder with
    `skip_prefix` keyword arg.
- `agent/agent_session_queue.py`
  - `_execute_agent_session()` — call site; looks up prior UUID, builds both
    full and minimal message forms, passes both to `get_response_via_harness`.

## Tests

- `tests/unit/test_harness_streaming.py::TestHarnessResume` — argv shape,
  UUID storage, empty/invalid UUID handling, stale-UUID fallback on any
  non-zero exit, observability log assertions.
- `tests/unit/test_cross_repo_gh_resolution.py` — `skip_prefix=True` behavior
  in `build_harness_turn_input()`.
- `tests/integration/test_harness_resume.py` — two-turn cycle against the real
  `claude` binary; gated on `shutil.which("claude")`.

## Out of Scope / No-Go

- BaseHarness abstraction (#780) — downstream consumer.
- Pi harness adoption (#838) — separate work item.
- Structured metrics/dashboards for resume hit-rate — grep-based observability
  is sufficient for now.
- Refactoring `build_harness_turn_input()` beyond the additive `skip_prefix`
  flag.
- Cross-process UUID race coordination — Popoto serialization via the worker
  queue is sufficient.
