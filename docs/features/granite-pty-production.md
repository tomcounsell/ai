# Granite PTY Container: Production Path

**Status:** Shipped (plan #1572)

## Summary

All bridge-originated `AgentSession` runs execute through the granite PTY
container, not the headless `claude -p stream-json` harness. The container
drives two persistent interactive `claude` TUI sessions (a PM and a Dev) over
PTYs, with a local `granite4.1:3b` model routing between them. A bounded
`PTYPool` caps the number of concurrent interactive pairs the worker holds open.

This is the production cutover of the PoC kernel landed in PR #1570 (issue
#1546, see [`granite-interactive-tui.md`](granite-interactive-tui.md)). The
cutover is **all-or-nothing**: there is no runtime fallback flag. If a
regression lands on `main`, the change is reverted (see
[Reverting the granite cutover](#reverting-the-granite-cutover)).

## Why

The headless `claude -p` path exits after each turn and cannot drive Claude
Code's interactive TUI (slash commands, persona priming, trust-folder
dismissal). It also requires the `ANTHROPIC_API_KEY` path rather than the Max
subscription OAuth path. The PTY container drives the real TUI and runs on the
Max OAuth path.

## Architecture

```
Telegram inbound → bridge enqueue → AgentSession in Redis
                                            │
                                            ▼
            worker picks session (semaphore-bounded, MAX_CONCURRENT_SESSIONS)
                                            │
                                            ▼
        agent/session_executor.py::_execute_agent_session(session)
                                            │
                                            ▼
        BridgeAdapter.run(user_message, working_dir)
          ├─ resolve send_cb once (agent_session_queue._resolve_callbacks)
          ├─ acquire (pm, dev) PTY pair from PTYPool (blocks if pool full)
          ├─ run Container in asyncio.to_thread (sync pexpect off the loop)
          │     ├─ on each [/user] turn  → send_cb(chat_id, text, reply_to, session)
          │     ├─ on [/complete]        → send_cb(chat_id, summary, reply_to, session)
          │     └─ returns ContainerResult
          ├─ write exit_summary / exit_anomaly to agent_session.session_events
          └─ return "" to BackgroundTask (send_result=False, no double-delivery)
```

### Components

| Component | File | Responsibility |
|-----------|------|----------------|
| `PTYPool` | `agent/granite_container/pty_pool.py` | Bounded, singleton pool of PM+Dev PTY slot pairs. `acquire_pair()` blocks when all slots are locked; `release_pair()` schedules a background respawn so the next acquirer gets fresh PTYs. |
| `BridgeAdapter` | `agent/granite_container/bridge_adapter.py` | Wraps `Container`: resolves `send_cb`, delivers `[/user]`/`[/complete]` payloads mid-loop, writes observability events to `session_events`, returns `""`. |
| `Container` | `agent/granite_container/container.py` | The PoC kernel: drives the PM→granite→Dev→granite→PM loop over two PTYs, classifies PM output, returns a `ContainerResult`. |
| Executor wiring | `agent/session_executor.py` | Replaces the `get_response_via_harness` call with `BridgeAdapter.run` via `asyncio.to_thread`. `send_result=False`. |
| Worker startup hook | `worker/__main__.py` | Initializes the pool singleton; kills orphan PTY children recorded in `data/granite_pty_pids.json` from a prior worker run (PID-targeted, not `pkill -f`). |

## Configuration

One operator-facing setting, in `config/settings.py` under `GraniteSettings`:

- `granite.pty_pool_size` — hard maximum of concurrent PM+Dev PTY pairs.
  Default `3`. Override via the `GRANITE__PTY_POOL_SIZE` env var (note the
  **double underscore** — pydantic nested-settings delimiter).

The pool size is intentionally **smaller** than `MAX_CONCURRENT_SESSIONS`
(default 8) so the Redis queue absorbs over-cap sessions rather than
overcommitting memory. Each `claude --permission-mode bypassPermissions` PTY
consumes ~200 MB resident. See
[deployment.md](deployment.md#granite-pty-pool) for the growth path to 6.

## User-visible behavior

The harness path delivered one final result at session end. The granite path
delivers per-turn `[/user]` payloads **mid-loop** — the user sees responses
"as the PM works" instead of "when the session ends."

- (a) `[/user]` payloads arrive mid-loop instead of at session end.
- (b) `[/complete]` still arrives at session end with the trailing summary.
- (c) The response cadence depends on the PM's `[/user]` decisions and is
  non-deterministic.
- (d) A second, silent `[/user]` payload at session end is possible if the
  PM's final turn classifies as `[/user]` — this is the same model behavior,
  now visible to the operator in real time.

## Per-turn silence cap (not total runtime cap)

Sessions can last up to ~6 hours of wall-clock. The bound is **per-turn
silence**, not total runtime: `CYCLE_IDLE_TIMEOUT_S` (120s in
`container.py`) is the per-cycle ceiling on a single PTY's idle wait. If a PTY
does not reach idle within this window, the container exits as `pm_hang` /
`dev_hang`. A wall-clock cap would force user-visible mid-session termination
the operator does not want.

## Observability

The adapter writes non-user-visible progress to `agent_session.session_events`
(a `ListField`). Telegram is not spammed. Event types:

| `type` | When | Key fields |
|--------|------|------------|
| `exit_summary` | every run, on completion | `exit_reason`, `turns`, `compliance_misses`, `ts` |
| `exit_anomaly` | `exit_reason in {pm_hang, dev_hang, startup_unresolved}` | `exit_reason`, `ts` — also logged at ERROR (Sentry log-capture picks it up; this is the on-call path for kernel regressions) |
| `delivery_failure` | a mid-loop `send_cb` raised | `payload_chars`, `reason`, `ts` |

> Note: `session_events` starts as `None` on a fresh `AgentSession`
> (`ListField(null=True)`). The adapter initializes the list before its first
> append (`_append_session_event`) so events are never silently dropped.

## Failure handling

- **Missing bridge callback** (standalone worker, no bridge registered):
  `_resolve_callbacks` returns `(None, None)`; the adapter installs
  logger-only no-op callbacks and the container still runs to completion. No
  crash, no user delivery.
- **Mid-loop `send_cb` raises**: the adapter logs at WARNING, writes a
  `delivery_failure` event, and continues to the next turn. The user does not
  see a "delivery failed" message (no-spam rule).
- **Worker SIGKILL mid-run**: orphan PTY children survive. The next worker
  startup reads `data/granite_pty_pids.json` and PID-kills them. The kill is
  PID-targeted, so an operator's personal interactive `claude` session on
  another project is never touched.

## Reverting the granite cutover

The cutover is all-or-nothing with no runtime feature flag. To roll back to the
harness path on incident:

1. `git revert <merge-sha>` (or `git revert -m 1 <merge-sha>` for a merge
   commit) and `git push`.
2. Restart the worker: `./scripts/valor-service.sh worker-restart`.
3. Drain stuck sessions from `telegram:outbox:*` — inspect
   `redis-cli LRANGE telegram:outbox:{session_id} 0 -1` for half-delivered
   granite payloads; the drafter is idempotent on retried `[/user]` payloads.
4. No manual flag toggling, no env var changes.

## See also

- [Granite Operator: Interactive TUI](granite-interactive-tui.md) — the PoC
  kernel this path builds on.
- [PTY Driver](pty-driver.md) — the substrate driver (submit key, idle signal,
  resume-UUID capture).
- [deployment.md](deployment.md#granite-pty-pool) — env var and the
  `MAX_CONCURRENT_SESSIONS` relationship.
- [bridge-worker-architecture.md](bridge-worker-architecture.md) — where
  `_execute_agent_session` sits in the worker.
