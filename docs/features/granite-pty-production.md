# Granite PTY Container: Production Path

**Status:** Shipped (plan #1572)

## Summary

All bridge-originated `AgentSession` runs execute through the granite PTY
container, not the headless `claude -p stream-json` harness. The container
drives two persistent interactive `claude` TUI sessions (a PM and a Dev) over
PTYs, with a local `granite4.1:3b` model routing between them. A bounded
`PTYPool` caps the number of concurrent interactive pairs the worker holds open.

This is the production cutover of the container first landed in PR #1570
(see [`granite-interactive-tui.md`](granite-interactive-tui.md)). The
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
| `Container` | `agent/granite_container/container.py` | The session runner: drives the PM→granite→Dev→granite→PM loop over two PTYs, classifies PM output, returns a `ContainerResult`. |
| Executor wiring | `agent/session_executor.py` | Replaces the `get_response_via_harness` call with `BridgeAdapter.run` via `asyncio.to_thread`. `send_result=False`. |
| Worker startup hook | `worker/__main__.py` | Verifies granite is reachable (hard gate, Step 4b.5); initializes the pool singleton; kills orphan PTY children recorded in `data/granite_pty_pids.json` from a prior worker run (PID-targeted, not `pkill -f`). |

## Startup precondition: granite must be reachable

Granite is the classification model — every PM/Dev turn is classified by a
regex parse over the session's JSONL transcript content; payloads are forwarded
verbatim — no LLM rewrite on the PM↔Dev channel. A worker that comes up
without granite would accept sessions and silently mis-route every one of them
(the classification role still requires it). Because the granite PTY path is
**all-or-nothing** (no runtime fallback), worker startup treats granite as a
**hard precondition**, not a best-effort init.

`worker/__main__.py` Step 4b.5 calls
`granite_classifier.ensure_granite_model()` (run off the event loop via
`asyncio.to_thread`) *before* the PTY pool is built. This is a precondition for
the **classification** role — the PM↔Dev content channel no longer calls ollama
(payloads are forwarded verbatim from the JSONL transcript), but the
turn-classification step still requires the model. The helper:

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

Environment variables can only be injected at process spawn, so
`BridgeAdapter.run` passes a `PairSpawnSpec` to `PTYPool.acquire_pair`. When
the spec's cwd/env/model differ from the pool's spawn-time defaults, the pool
closes the slot's pre-warmed pair and spawns a fresh per-session pair in the
**same slot** — the bounded-slot invariant and the normal release/respawn
lifecycle are preserved, at the cost of spawn latency on acquire. The spec
carries:

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
- **`pm_model`** — the D1 precedence cascade (`session.model` > settings >
  codebase default), applied to the PM PTY. The Dev PTY has no per-session
  model knob; it stays on `GRANITE__DEV_MODEL` (defaults to `opus` since
  issue #1692, when Dev became the full SDLC owner).

**Persona** is no longer in the `PairSpawnSpec`. As of issue #1692, persona
arrives entirely via the prime commands (`.claude/commands/granite/prime-*-role.md`)
that each PTY receives at startup. The `--append-system-prompt` flag is gone.

In production every bridge-originated session carries a non-empty env, so
**every production acquire takes the spawn-on-acquire path**; the pre-warmed
pair only serves spec-less callers (the granite CLI, tests). A spec matching
the pool defaults reuses the pre-warmed pair as-is.

## Prime/work separation (issues #1644 and #1647)

Granite runs in two distinct phases. Getting these phases right is critical:
**self-starting Dev races** and **zero-message completions** are both
production bugs that stem from blurring them.

### Phase 1 — Persona priming

Each PTY receives a persona-priming slash command (`/granite:prime-pm-role`,
`/granite:prime-dev-role`, or `/granite:prime-teammate-role`). Both PM and Dev
receive the user message as `$ARGUMENTS` (issue #1692):

- **PM prime** carries `$ARGUMENTS = user_message`. PM gets full task context
  immediately so it can start routing.
- **Dev prime also carries `$ARGUMENTS = user_message`** as labeled background
  context. Dev reads it when the PM's `[/dev]` relay arrives — but the prime
  text explicitly instructs Dev NOT to act until it receives that relay (the
  anti-self-start guard from issue #1644 now lives in the prime text, not in
  message omission).

Persona is delivered entirely via these prime commands. No `--append-system-prompt`
flag is set at spawn time (removed in issue #1692). The shared WORKER rails
(no-push-to-main, principal context, completion criteria) live in
`.claude/commands/granite/_prime-rails.md` and each role prime references it.

### Prime-turn relay

After both primes complete and the startup phase settles, the container reads
PM's prime-turn buffer (the output PM produced in response to its priming
command) and routes it through the same `_route_pm_classification` helper used
by the steady-state loop. PM often emits the first `[/dev]` instruction **in
its prime turn** rather than waiting for a steady-state read; without the
prime-turn relay this instruction was silently discarded.

The relay sets `_prime_relayed = True` and `_prime_pm_buf_hash` regardless of
the routing outcome (including dev routes). The first steady-state iteration
then reads a **fresh** PM idle before classifying — the stale-buffer race guard
— so the prime buffer is never double-classified.

### Wrap-up guard — mandatory user-facing delivery (issue #1647)

The `_run_wrapup_guard` method fires when the run exits in a
*successful-shaped* state (`pm_complete`, `pm_user`, `pm_max_turns`) but
`result.user_facing_routed` is still `False`. This happens when PM performs
only `[/dev]` routing turns and never emits `[/user]` or `[/complete]`.

The guard:

1. Seeds a Dev report from `_last_dev_report` (captured on every summarize
   call), a fresh Dev idle read, or `DEV_REPORT_UNAVAILABLE` as fallback.
2. Writes `PM_WRAPUP_PROMPT` (seeded with the Dev report) to PM's PTY and
   waits for PM to respond — capped at `MAX_WRAPUP_ATTEMPTS = 1`.
3. If PM responds with a `[/user]` or `[/complete]`, that payload is delivered
   and `user_facing_routed = True`.
4. If PM still does not produce a user-facing message after all attempts,
   delivers `OPERATOR_TERMINAL_MESSAGE` directly via `on_user_payload` and
   sets `exit_reason = "pm_no_user_message"`.

**The human is never left with only an emoji.** The wrap-up guard guarantees
at least `OPERATOR_TERMINAL_MESSAGE` reaches the user for every successful run,
regardless of how the PM classified its turns internally.

### Completion emoji and `user_facing_routed`

The granite path never calls `messenger.send()`, so `has_communicated()` is
always `False` on this path. The executor's post-run emoji branch was updated
(issue #1647 fix) to also consult `agent_session.user_facing_routed`, a new
`Field(default=False)` on `AgentSession` set by `BridgeAdapter._publish_exit_summary`
when `_deliver_sync` confirms at least one `[/user]` or `[/complete]` delivery.
The branch reads:

```python
elif messenger.has_communicated() or getattr(agent_session, "user_facing_routed", False):
    emoji = REACTION_COMPLETE
```

This means a granite session that successfully delivered at least one
user-facing message gets a ✅ completion emoji, consistent with harness
sessions.

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
- (e) A session that completes via `pm_no_user_message` (wrap-up guard
  exhausted) sends `OPERATOR_TERMINAL_MESSAGE` — a brief canned notice that
  the task was handled. This is a last resort; the wrap-up guard should
  normally coax a summary from PM.

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
| `exit_anomaly` | `exit_reason in {pm_hang, dev_hang, startup_unresolved, pm_no_user_message, exception (soft→WARNING, hard→ERROR)}` | `exit_reason`, `ts` — logged at ERROR for hard exits (Sentry log-capture picks it up; on-call path for session-runner regressions); WARNING for soft exception exits (had turns → likely network blip, no Sentry alert) |
| `granite_user_routed` | on each `[/user]` payload routing attempt | `event_type`, `text` (payload size + delivery result) |
| `granite_complete_routed` | on each `[/complete]` payload routing attempt | `event_type`, `text` (payload size + delivery result) |
| `granite_delivery_failure` | a mid-loop `send_cb` raised | `event_type`, `text`, `payload_chars`, `reason`, `ts` |
| `delivery_failure` | a mid-loop `send_cb` raised (legacy alias) | `payload_chars`, `reason`, `ts` |

Normal completions (`pm_complete`, `pm_user`, `pm_max_turns`) do **not** emit
`exit_anomaly`, because they are expected outcomes. `pm_no_user_message` emits
an anomaly despite delivering `OPERATOR_TERMINAL_MESSAGE` (the guard fired as a
last resort), so the operator knows the PM failed to self-summarize.

`exception` exit_reason uses severity gating: if the session had at least one
classified turn (soft exit, likely network blip), `exit_anomaly` is logged at
WARNING with no Sentry alert. If the session crashed before producing any output
(hard exit), it logs at ERROR so Sentry captures it for on-call triage.

> Note: `session_events` starts as `None` on a fresh `AgentSession`
> (`ListField(null=True)`). The adapter initializes the list before its first
> append (`_append_session_event`) **and persists each append** with
> `save(update_fields=["session_events", "updated_at"])` — the executor's
> post-run saves exclude `session_events` and finalization loads a fresh copy
> by session_id, so an unsaved in-memory append would never reach Redis.

### Transcript tailer (issue #1648)

As of issue #1648, the full telemetry signal set (`turn_count`, `tool_call_count`,
`total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`,
`current_tool_name`, `last_tool_use_at`, `recent_thinking_excerpt`) is sourced
from the **transcript tailer** rather than the SDK path or `_bump_last_turn_at`.

The tailer (`agent/granite_container/transcript_tailer.py`) performs
byte-offset-stateful incremental reads of the Claude Code JSONL transcript files
at `~/.claude/projects/{cwd-slug}/{uuid}.jsonl`, polled every 5 seconds
(`_TAILER_INTERVAL_S`). The `cwd-slug` is `cwd.replace("/", "-")` and the
`uuid` is set deterministically at PTY spawn via `claude --session-id <uuid>`
(so the transcript path is known before the session starts).

`BridgeAdapter._run_tailer_task` runs as an `asyncio.Task` (started in `run()`
before `asyncio.to_thread`, cancelled after the container exits). Persistence
uses `asyncio.to_thread` to keep blocking Redis saves off the event loop.
`update_fields` is strictly disjoint from `_publish_exit_summary`'s set to
avoid concurrent-write clobber. The tailer is diff-gated: it skips the save
when turn/tool/token counts are unchanged since the last tick.

**Partial-line handling:** because the JSONL file is appended live by the `claude`
TUI, a tick may read a partially-written trailing line. The tailer advances its
byte offset only to the last complete newline boundary — partial trailing bytes
are re-read on the next tick once the write completes. This prevents partial JSON
lines from being silently skipped.

**ISO→datetime conversion:** `TranscriptTelemetry.last_tool_use_at` stores the
raw ISO-8601 timestamp string from the JSONL entry. Before assigning it to
`AgentSession.last_tool_use_at` (a Popoto `DatetimeField`), the tailer converts
it with `datetime.fromisoformat()` to a tz-aware `datetime` object. A conversion
failure is silently ignored (the field stays at its previous value).

### JSONL Transcript Content Surface

The PTY operator reads message content from the Claude Code JSONL transcript
(the same surface the telemetry tailer consumes) rather than scraping the painted
PTY frame. `last_assistant_text()` in `transcript_tailer.py` reads the last
assistant turn's text blocks, walking newest-first to skip tool-only final entries.

The flush-timing heuristic (read-at-idle vs. assistant-message-flushed) is mitigated
by an mtime snapshot before each idle poll, but not fully eliminated. The deterministic
complement is followup issue **#1688** ("Hook-driven turn returns for granite PTY
shuttle"), which replaces idle-poll heuristics with hook-driven turn boundaries.

### Granite identity fields

`AgentSession` now carries four first-class granite identity fields (issue
#1648), populated by `BridgeAdapter._publish_exit_summary` from
`ContainerResult`:

| Field | Type | Description |
|-------|------|-------------|
| `exit_reason` | `Field(null=True)` | Granite-path exit reason (granite-path-populated; see below for values) |
| `pm_pid` | `IntField(null=True)` | PM PTY OS process ID |
| `dev_pid` | `IntField(null=True)` | Dev PTY OS process ID |
| `pm_transcript_path` | `Field(null=True)` | Absolute path to PM Claude Code JSONL transcript |
| `dev_transcript_path` | `Field(null=True)` | Absolute path to Dev Claude Code JSONL transcript |

All four are nullable: non-granite sessions and pre-deploy granite sessions
leave them as `None`. The dashboard uses them to surface active PTY processes
and link to transcript files.

### `exit_reason` and reaction gating

`AgentSession.exit_reason` is granite-path-populated. The dashboard renders a
warning chip for non-clean values. Clean exit reasons: `pm_complete`, `pm_user`,
`pm_max_turns`. Anomaly exit reasons: `pm_hang`, `dev_hang`,
`startup_unresolved`, `pm_no_user_message`, `exception`.

The executor's reaction logic consults `exit_reason` in addition to
`user_facing_routed`:

- `exit_reason` in anomaly set → `REACTION_ERROR` emoji regardless of
  `user_facing_routed`.
- Clean `exit_reason` + `user_facing_routed=False` (`communicated=False` chip
  in dashboard) → normal reaction (the wrap-up guard fired but the session
  technically completed without user-facing output).
- Clean `exit_reason` + `user_facing_routed=True` → `REACTION_COMPLETE`.

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

> Note: As of issue #1648, the full telemetry signal set (`turn_count`,
> `tool_call_count`, `total_input_tokens`, etc.) is sourced from the transcript
> tailer rather than `_bump_last_turn_at`. The `on_turn` hook remains in place
> to keep `last_turn_at` current for the two-tier detector, but the richer
> liveness fields are now transcript-driven.

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
2. **`[/dev]` content is read from the JSONL transcript, not from ollama.** The
   `extract_dev_prompt` / `summarize_for_pm` ollama call sites have been
   removed. PM→Dev now uses `classification.payload` (verbatim from the PM's
   JSONL transcript) and Dev→PM forwards Dev's last assistant text verbatim via
   `last_assistant_text()` in `transcript_tailer.py`. If ollama goes down
   *after* startup the classification step would fail, but message content
   forwarding is unaffected. Worker startup still health-checks granite as a
   hard precondition for the classification role (see
   [Startup precondition](#startup-precondition-granite-must-be-reachable)).
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

## Local Ollama model policy (post-consolidation)

Since issue #1636, `granite4.1:3b` is the **only local instruct model** required on every machine. It serves two roles:

| Role | Call sites | Constant |
|------|-----------|----------|
| PTY operator (PM↔Dev routing) | regex classify + verbatim transcript-content forward via `last_assistant_text()` in `transcript_tailer.py` (no model call on the content channel) | `GRANITE__DEV_MODEL` (default `granite4.1:3b`) — used for turn classification only |
| Bridge message classification | `routing.classify_needs_response`, `routing.classify_terminus`, `routing._classify_work_request_llm`, `reflections._gemma_classify` (memory audit Layer 3), `email_cs.triage` | `OLLAMA_CLASSIFIER_MODEL = "granite4.1:3b"` in `config/models.py` |

Free-text generation (memory title generation, test AI judge) uses the per-machine `ollama_generation_model` setting (`config/settings.py::ModelSettings`, env `MODELS__OLLAMA_GENERATION_MODEL`, default `gemma4:31b-cloud`). The generation model is **not** a hard worker precondition — generation is fail-soft everywhere. Compare to granite, which IS a hard precondition (Step 4b.5 in `worker/__main__.py`).

**Steady-state local Ollama on a cloud machine (16 GB RAM):**
- `granite4.1:3b` — classification + PTY routing
- `nomic-embed-text` — vector embeddings

**Steady-state on a RAM-rich Apple-Silicon machine (≥ 48 GB):**
- `granite4.1:3b` — classification + PTY routing
- `nomic-embed-text` — vector embeddings
- `gemma4:31b-mlx` — local generation (opt-in, selected by `/setup` from RAM)

**`ensure_generation_model()` helper** (`config/models.py`): probes the configured generation tag and returns `(model_available: bool, detail: str)`. It is a config-layer detection helper, NOT a startup gate like `ensure_granite_model()`. For `:cloud` tags it is a near-no-op (checks cloud signin); for `-mlx` tags it includes a RAM guard that skips the pull when RAM < `MIN_LOCAL_GEN_RAM_GB`. Called by `/setup` and `/update` Step 4 (warning-only, never suppresses restart or blocks worker).

`gemma4:e2b` was the previous local model (standardized in issue #671) and is now in `OLLAMA_SUPERSEDED_MODELS` — removed from every machine by `/update` superseded-cleanup once the granite smoke-test passes.

## See also

- [Granite Operator: Interactive TUI](granite-interactive-tui.md) — the
  session-runner container this path builds on.
- [PTY Driver](pty-driver.md) — the substrate driver (submit key, idle signal,
  resume-UUID capture).
- [deployment.md](deployment.md#granite-pty-pool) — env var and the
  `MAX_CONCURRENT_SESSIONS` relationship.
- [bridge-worker-architecture.md](bridge-worker-architecture.md) — where
  `_execute_agent_session` sits in the worker.
