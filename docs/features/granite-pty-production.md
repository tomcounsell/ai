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
          ├─ acquire (pm, dev) PTY pair from PTYPool with a PairSpawnSpec
          │     (session cwd, env, persona overlay, PM model — pool spawns a
          │      fresh per-session pair at acquire when the spec differs from
          │      its spawn-time defaults; bounded-slot invariant holds)
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
| `PTYPool` | `agent/granite_container/pty_pool.py` | Bounded, singleton pool of PM+Dev PTY slot pairs. `acquire_pair(spawn_spec=...)` blocks when all slots are locked (waiting on a pool-level `asyncio.Condition` notified when a slot turns idle, not a sleep-poll) and spawns a per-session pair in the acquired slot when the `PairSpawnSpec` differs from the pool defaults; `release_pair()` schedules a background respawn so the next acquirer gets fresh PTYs. |
| `BridgeAdapter` | `agent/granite_container/bridge_adapter.py` | Wraps `Container`: resolves `send_cb`, delivers `[/user]`/`[/complete]` payloads mid-loop, writes observability events to `session_events`, returns `""`. |
| `Container` | `agent/granite_container/container.py` | The PoC kernel: drives the PM→granite→Dev→granite→PM loop over two PTYs, classifies PM output, returns a `ContainerResult`. |
| Executor wiring | `agent/session_executor.py` | Replaces the `get_response_via_harness` call with `BridgeAdapter.run` via `asyncio.to_thread`. `send_result=False`. |
| Worker startup hook | `worker/__main__.py` | Verifies granite is reachable (hard gate, Step 4b.5); initializes the pool singleton; kills orphan PTY children recorded in `data/granite_pty_pids.json` from a prior worker run (PID-targeted, not `pkill -f`). |

## Startup precondition: granite must be reachable

Granite is the routing brain — every PM/Dev turn is classified and translated
by an `ollama` call against `granite4.1:3b`. A worker that comes up without it
would accept sessions and silently mis-route every one of them. Because the
granite PTY path is **all-or-nothing** (no runtime fallback), worker startup
treats granite as a **hard precondition**, not a best-effort init.

`worker/__main__.py` Step 4b.5 calls
`granite_classifier.ensure_granite_model()` (run off the event loop via
`asyncio.to_thread`) *before* the PTY pool is built. The helper:

1. confirms the `ollama` python client is importable (the classifier uses it),
2. confirms the `ollama` CLI/daemon is on `PATH`,
3. probes the model with a trivial prompt (`ollama run`, 60s cap),
4. on a failed probe, runs `ollama pull granite4.1:3b` once (15min cap) and
   re-probes.

If granite still can't be made available the worker logs `CRITICAL` and exits
non-zero. launchd's `KeepAlive` respawns it after `ThrottleInterval`, so the
worker self-heals the moment granite becomes reachable instead of running
broken.

**Why startup is the universal chokepoint.** Every restart path funnels through
`main()`: `/update`'s inline restart, the cron deferred restart-flag
(`data/restart-requested` → `agent_session_queue._trigger_restart()` →
`SIGTERM` → launchd respawn), and a manual `valor-service.sh worker-restart`.
Gating here covers all of them. The complementary `/update` Step 4.75 gate
(`scripts/update/run.py`) is a *fast, friendly* early warning that skips the
service restart and tells the operator to pull granite — but the worker gate is
the actual enforcement that no path can bypass.

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

## Per-session spawn (spawn-on-acquire)

Environment variables and the `--append-system-prompt` overlay can only be
injected at process spawn, so `BridgeAdapter.run` passes a `PairSpawnSpec` to
`PTYPool.acquire_pair`. When the spec's cwd/env/persona/model differ from the
pool's spawn-time defaults, the pool closes the slot's pre-warmed pair and
spawns a fresh per-session pair in the **same slot** — the bounded-slot
invariant and the normal release/respawn lifecycle are preserved, at the cost
of spawn latency on acquire. The spec carries:

- **`cwd`** — the session's `working_dir`. Dev sessions with tier-2 worktree
  isolation run their TUIs inside `.worktrees/{slug}/`, and cross-project
  sessions run in their own repo (the #887 worktree-contamination class is
  closed on this path).
- **`env`** — the per-session identity env merged on top of the driver's
  `_build_env()`: `SESSION_TYPE` (drives the `pre_tool_use` PM Bash
  restrictions, issue #1148), `AGENT_SESSION_ID` (hook attribution and the
  liveness writers), `CLAUDE_CODE_TASK_LIST_ID` (task-list isolation),
  `VALOR_PARENT_SESSION_ID` (child-session linking), and Telegram/Sentry auth
  for PM/Teammate sessions.
- **`pm_system_prompt`** — the composed persona overlay (PM SDLC orchestration
  overlay, email persona, or teammate overlay), applied to the PM PTY via
  `claude --append-system-prompt`. This is the SAME persona composition the
  executor resolves for every session type.
- **`pm_model`** — the D1 precedence cascade (`session.model` > settings >
  codebase default), applied to the PM PTY. The Dev PTY has no per-session
  model knob; it stays on `GRANITE__DEV_MODEL` (`PairSpawnSpec.dev_model`
  exists at the pool layer but the adapter never sets it).

In production every bridge-originated session carries a non-empty env, so
**every production acquire takes the spawn-on-acquire path**; the pre-warmed
pair only serves spec-less callers (the granite CLI, tests). A spec matching
the pool defaults reuses the pre-warmed pair as-is.

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
> append (`_append_session_event`) **and persists each append** with
> `save(update_fields=["session_events", "updated_at"])` — the executor's
> post-run saves exclude `session_events` and finalization loads a fresh copy
> by session_id, so an unsaved in-memory append would never reach Redis.

### Liveness (two-tier no-progress detector)

The harness path fed `last_turn_at` via the sdk_client `result` handler and
the liveness hooks; the granite container has neither. `BridgeAdapter` passes
its `_bump_last_turn_at` as the container's `on_turn` hook, which fires once
per classified PM turn (every destination, including `unknown`) and persists
`agent_session.last_turn_at` with `save(update_fields=["last_turn_at"])`. This
keeps the two-tier no-progress detector's sub-check A live for granite
sessions: a wedged session stops bumping `last_turn_at` and Tier-1/Tier-2 can
detect it, instead of riding the sticky own-progress signal forever. The bump
is fail-silent — a Redis failure logs a warning and never crashes the run.

### Startup hard ceiling

The startup loop polls both PTYs on short (`STARTUP_CYCLE_TIMEOUT_S` = 3s)
reads until both reach idle, dismissing transient startup events
(trust-folder, update notice) along the way. A slow cold persona load simply
keeps the loop cycling cheaply. If the PTYs never settle within
`STARTUP_HARD_CEILING_S` (600s), the run exits `startup_unresolved` — the
distinct failure signature for a broken `--permission-mode` flag (a TUI
upgrade renaming the flag means the bypass bar never paints, so the idle
heuristic can never fire). Without the ceiling that failure would burn the
steady-state budget and report a misleading `pm_hang`.

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

## Known limitations (deep-dive audit, PR #1612)

1. **Resume is a fresh TUI session.** The container has no `claude --resume`
   wiring; a reply-to thread continuation or `valor-session resume`
   re-enqueues into a brand-new TUI without the prior Claude Code transcript.
   The executor always sends the **full-context turn input** (the same
   context-prefixed message a first turn gets), so threaded conversations
   keep their conversation context — what is lost is the TUI-internal
   transcript (tool-call history), not the conversational context.
2. **`[/dev]` turns hard-depend on local ollama.** `extract_dev_prompt` /
   `summarize_for_pm` call `ollama.chat` (`granite4.1:3b`); if ollama goes down
   *after* startup the container exits `exception` on the first dev-routed
   turn. Worker startup now health-checks granite as a hard precondition (see
   [Startup precondition](#startup-precondition-granite-must-be-reachable)), so
   a worker can no longer come up with granite already absent — but it does not
   re-check mid-run.
3. **Multi-turn conversations end at the first `[/user]`.** The container
   exits on `pm_user`; a user reply spawns a new container run (fresh PTYs,
   fresh context apart from the steering message).

Hardenings landed by the same audit: mid-loop delivery now schedules onto the
worker loop captured in `BridgeAdapter.run` (previously every delivery from
the pexpect thread was skipped as `no_event_loop`); `Container` skips its
machine-wide `pkill` fallback for pool-owned pairs; the pool respawns with the
original `cwd`, checks pair liveness at acquire, clears the slot event at
release, and prunes completed respawn tasks; `read_until_idle` declares idle
only after `QUIESCENCE_S` (2.0s) of byte-silence, evaluated level-triggered
against a persistent per-turn capture — an active turn repaints the spinner
at ≥1 Hz and so can never pass the gate, while a settled-and-silent PTY (which
an edge-triggered check could never observe) passes it on every poll. This
replaced an earlier regex loading-spinner negative, which mid-turn cell-
fragment repaints could both evade (false idle) and falsely latch (a stale
spinner frame blocking idle for the rest of the call).

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

## `valor-granite-loop` CLI AgentSession lifecycle

The standalone `valor-granite-loop` CLI creates and finalizes an `AgentSession`
record so its runs are visible in the dashboard and `valor-session list`.

### Session creation (before container starts)

Before `Container.run()` is called, `main()` mints a session and persists it:

```python
session_id = "local-" + uuid.uuid4().hex[:12]   # e.g. "local-a3f9b21c8d04"
session = AgentSession.create_local(
    session_id=session_id,
    session_type=SessionType.GRANITE,
    project_key="valor",
    working_dir=args.cwd or os.getcwd(),
)
```

**Why the `local-` prefix is required**: worker startup recovery
(`agent/session_health.py:538`) discriminates by
`session_id.startswith("local")`. A bare-hex id falls through to the bridge
recovery path and would re-execute the CLI run as a bridge session on the next
worker restart.

**Why `session_type=SessionType.GRANITE`**: `create_local` defaults to
`SESSION_TYPE_DEV`, which would silently mislabel the session. Granite CLI
sessions carry `session_type="granite"` so `valor-session list --role granite`
returns only CLI-originated runs, not bridge-originated dev sessions.

### Session finalization (on exit)

| Exit condition | Finalize status | Reason passed |
|---|---|---|
| `exit_reason in ("pm_complete", "pm_user")` | `completed` | `result.exit_reason` |
| All other exit reasons | `failed` | `result.exit_reason` |
| Unexpected exception in `container.run()` | `failed` | `repr(e)` |

The except-block finalizes with `reject_from_terminal=False` to prevent a
double-finalize raise if the post-run path already set the status to `failed`.

### Operational IDs

The stdout summary JSON contains two ID fields that serve different purposes:

| Field | Value | Use |
|---|---|---|
| `agent_session_id` | The `local-`-prefixed record ID | `valor-session steer/kill/status --id` |
| `session_id` | Container's internal trace artifact | Correlating turn traces in the results JSON |

Use `agent_session_id` for all `valor-session` operations. Use `session_id` to
look up the corresponding `ContainerResult` in the results file.

### Best-effort guard

Session persistence failures never affect the CLI exit code or results JSON
output. A single `granite session not recorded: <reason>` line is emitted to
stderr and execution continues normally.

## Completion-Cleanup Safety Floor (issue #1646)

Dev sessions commit work to `session/dev-{id}` branches inside `.worktrees/dev-{id}`.
The PM persona (via #1647) is responsible for the landing decision (auto-merge vs
push+PR) and authorizes cleanup after the work lands. The executor never deletes
branches unconditionally.

**Guard:** All four branch-deletion sites in `agent/` route through `safe_delete_branch`
(in `agent/worktree_manager.py`), which checks merged-ness before deleting:

- **Site A (executor auto-mark):** uses `merged_via_ancestor` (no prior merge). If the
  branch tip is not reachable from `main`, deletion is skipped.
- **Sites B/C (`cleanup_after_merge`, `remove_worktree`):** uses `merged_via_tree` —
  squash-safe via `git merge-tree --write-tree`. Correct for the production
  `gh pr merge --squash` workflow.
- **Site D (`cleanup_stale_branches` reflection):** also uses `merged_via_tree` (stale
  refs are often squash-merged PRs whose local refs were never deleted).

**When a branch is preserved:** A greppable `[unmerged-branch-guard]` warning is logged
naming the branch. The branch and worktree remain on disk. Grep `logs/worker.log` for
`[unmerged-branch-guard]` to find preserved branches.

**Interim accumulation:** Until #1647 lands the PM-authorized landing step, unmerged
dev-session branches accumulate. The `preserved=N` counter in `logs/worker.log` is the
interim signal. Manual operator action is the only safe reaping path — do NOT use
`scripts/worktree-gc.sh --apply` for no-PR branches (it has an unguarded `git branch -D`
at line 208 that would re-destroy the preserved work).

**The only `git branch -D` in `agent/`** lives inside `safe_delete_branch`, behind a
proven-landed check. All other deletion uses `git branch -d` (fails-closed).

## See also

- [Granite Operator: Interactive TUI](granite-interactive-tui.md) — the PoC
  kernel this path builds on.
- [PTY Driver](pty-driver.md) — the substrate driver (submit key, idle signal,
  resume-UUID capture).
- [deployment.md](deployment.md#granite-pty-pool) — env var and the
  `MAX_CONCURRENT_SESSIONS` relationship.
- [bridge-worker-architecture.md](bridge-worker-architecture.md) — where
  `_execute_agent_session` sits in the worker.
