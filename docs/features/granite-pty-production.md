# Granite PTY Container: Production Path

**Status:** Shipped (plan #1572)

## Summary

All bridge-originated `AgentSession` runs execute through the granite PTY
container, not the headless `claude -p stream-json` harness. The container
drives two persistent interactive `claude` TUI sessions (a PM and a Dev) over
PTYs, with a local `granite4.1:3b` model routing between them. A bounded
`PTYPool` caps the number of concurrent interactive pairs the worker holds open.

This is the production cutover of the container first landed in PR #1570
(see [`granite-interactive-tui.md`](granite-interactive-tui.md)). All bridge
sessions route through `Container`; **the transport per role is
config-selectable** (plan #1842): each of PM / Dev runs on an interactive PTY
(the default, flat-billed) or headless `claude -p` (metered). The default —
both roles on PTY — reproduces the original cutover behavior exactly. See
[`per-role-transport.md`](per-role-transport.md) for the selector, the headless
role driver, cost surfacing, and the flip runbook. A regression in the PTY path
is still reverted (see
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
the **classification** role — the PM↔Dev content channel forwards payloads
verbatim from the JSONL transcript (ollama is not called for content routing),
but the turn-classification step still requires the model. The helper:

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

## OAuth Token Prevention

### Problem

Granite PTYs authenticate via the Claude Max subscription OAuth path. Short-lived session tokens
expire after roughly an hour, causing the TUI to render a `/login` prompt mid-session. The PTY
container cannot dismiss an interactive login screen — the session hangs.

### Prevention: `CLAUDE_CODE_OAUTH_TOKEN`

`_build_env()` in `agent/granite_container/pty_driver.py` injects `CLAUDE_CODE_OAUTH_TOKEN` (a
long-lived ~1-year token minted via `claude setup-token`, prefix `sk-ant-oat01-`) into every PTY
child environment when the var is present in `os.environ`. The token is stored in the vault at
`~/Desktop/Valor/.env` and propagates to all machines via iCloud sync automatically.

This mechanism complements the `ANTHROPIC_*` blanking — they serve different purposes and do not
conflict:

| Mechanism | Purpose |
|-----------|---------|
| Blank `ANTHROPIC_API_KEY` / `BASE_URL` / `AUTH_TOKEN` | Force real Claude OAuth endpoint (not ollama) |
| Inject `CLAUDE_CODE_OAUTH_TOKEN` | Supply long-lived token so TUI never prompts for `/login` |

When `CLAUDE_CODE_OAUTH_TOKEN` is absent, the key is removed entirely from the child env so the
TUI falls back to its own credential lookup. It is intentionally NOT blanked.

### Rotation

Mint a new token once per year (approximately) on a browser-accessible machine:

```bash
claude setup-token
```

Copy the resulting `sk-ant-oat01-...` value to `~/Desktop/Valor/.env` under the key
`CLAUDE_CODE_OAUTH_TOKEN`. iCloud propagates it to all machines; no per-machine step needed.

`python -m tools.doctor` reports presence and prefix validity:

```
[GRANITE] CLAUDE_CODE_OAUTH_TOKEN  ok  (prefix sk-ant-oat01-)
```

### Graceful degradation

If absent or expired: the TUI eventually renders `/login`, and the deterministic BYOB re-auth
recovery (#1750) fires as a backstop — driving the already-logged-in Chrome through the OAuth
consent with no LLM in the loop, degrading to the `startup_unresolved` alert only if recovery
fails. The right fix is always token rotation.

Full reference: [`docs/infra/granite-oauth-token.md`](../infra/granite-oauth-token.md) ·
recovery backstop: [`docs/features/granite-login-recovery.md`](granite-login-recovery.md)

## Configuration

Operator-facing settings:

- `granite.pty_pool_size` — hard maximum of concurrent PM+Dev PTY pairs.
  Default `3`. Override via the `GRANITE__PTY_POOL_SIZE` env var (note the
  **double underscore** — pydantic nested-settings delimiter).

- `GRANITE_DELIVERY_TIMEOUT_S` — delivery timeout in seconds for the
  `_deliver_sync` call that schedules a `[/user]` or `[/complete]` payload
  back onto the event loop from the pexpect thread. Default `30.0`. Override
  via the env var of the same name (`DEFAULT_DELIVERY_TIMEOUT_S` constant in
  `agent/granite_container/bridge_adapter.py`).

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
flag is set at spawn time (dropped in issue #1692). The shared WORKER rails
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

### Startup settle conditions (issue #1881)

PM→user delivery is decoupled from whether the Dev PTY has finished priming.
The startup loop watches both PTYs and settles by one of three paths, recorded
on `ContainerResult.startup_settle_reason` and appended to `startup_events`:

| `startup_settle_reason` | Condition | When it fires |
|--------------------------|-----------|---------------|
| `both_idle` | PM and Dev both idle in the **same** cycle | Classic cold start where the two PTYs quiesce together. |
| `pm_latched_dev_idle` | PM went idle on an **earlier** cycle (latched via `pm_ever_idle`), Dev reaches idle later | Fast PM, slow Dev — PM finishes and quiesces before Dev primes, Dev catches up a few cycles later. |
| `pm_terminal_fast` | PM produces a terminal `[/complete]`/`[/user]` turn and settles **immediately**, without waiting for Dev at all | PM-only requests (status check, Q&A, board update) that never need the Dev PTY. |

`startup_settle_reason` is `None` when startup never settles (plateau or ceiling
exit).

Two mechanisms make this work:

- **PM-idle latch (`pm_ever_idle`).** Idle detection is edge-triggered per cycle,
  so in the fast-PM/slow-Dev race PM's idle is observed in an early cycle and
  Dev's in a later one, never together. The latch remembers that PM was idle, so
  the settle gate reads `pm_ever_idle and dev_saw_idle` rather than requiring both
  in the same cycle. (A headless Dev already forces `dev_saw_idle=True`, so once
  PM is idle the gate reduces to PM alone — byte-identical to the pre-#1881 gate.)
- **Terminal-turn fast settle.** When PM has gone idle **this cycle**, the loop
  classifies PM's prime transcript (read-only) against the same pre-loop baseline
  the relay uses. A non-empty `[/complete]` or a `[/user]` turn is user-facing and
  Dev-independent, so startup settles immediately and falls through to the single
  prime-turn relay for delivery. An empty `[/complete]` or non-terminal chatter
  does **not** fast-settle — it falls through to the latched gate, so a genuinely
  stuck PM still reaches plateau/ceiling.

The relay is the single delivery site for all three paths. Its `pm_prime_baseline`
snapshot is taken **before** `_prime_session(pm)` — before PM emits any
task-response output — so the relay's `last_assistant_text(...,
baseline_text_count=pm_prime_baseline)` freshness guard always sees PM's terminal
turn as a new text-bearing entry and delivers it, whichever break reason fired.
Snapshotting the baseline after the loop (its pre-#1881 site) would already count
a fast PM's `[/complete]` that flushed cycles earlier, so the guard returned `""`
and the reply was silently dropped as a compliance nudge instead of `pm_complete`.

### Per-turn prefix-contract reminder (issue #1719)

On every Dev-report handoff (when the PM's `[/dev]` classification routes
work to the Dev PTY), the container appends `PM_TURN_CONTRACT_REMINDER` to
the Dev report text before writing it to PM's PTY:

```
Begin your reply with `[/user]`, `[/complete]`, or `[/dev]` on its own line.
```

This restores the per-turn prefix-contract assertion that the pre-#1694
`--append-system-prompt` path guaranteed on every turn. Without it, the PM
would eventually drift and stop leading with a routing prefix across the 10
PM↔Dev cycles, causing the session to exit as `pm_no_user_message` with a
canned fallback delivered. The reminder is a single line and has negligible
token cost; the full contract is still established by the one-shot
`/prime-pm-role` at session start.

### Wrap-up guard — mandatory user-facing delivery (issues #1647, #1719)

The `_run_wrapup_guard` method fires when the run exits in a
*wrap-up-eligible* state (`pm_complete`, `pm_user`, `pm_max_turns`,
`pm_floor_delivered`) but `result.user_facing_routed` is still `False`. This
happens when PM performs only `[/dev]` routing turns and never emits `[/user]`
or `[/complete]`.

**Note on exit-reason sets:** The wrap-up trigger set (`_wrapup_eligible_exits`
in `container.py`) is distinct from `_CLEAN_GRANITE_EXIT_REASONS` in
`session_executor.py`. The former is the guard trigger; the latter is the
reaction/telemetry "clean" classifier. `pm_max_turns` is in the trigger set
(so the guard fires and attempts delivery) but is NOT in `_CLEAN_GRANITE_EXIT_REASONS`
(it is a non-clean exit from the executor's perspective). `pm_floor_delivered`
is in both — it is both a guard trigger and a clean exit.

The guard:

1. Seeds a Dev report from `_last_dev_report` (captured on every summarize
   call), a fresh Dev idle read, or `DEV_REPORT_UNAVAILABLE` as fallback.
2. Writes `PM_WRAPUP_PROMPT` (seeded with the Dev report) to PM's PTY and
   waits for PM to respond — capped at `MAX_WRAPUP_ATTEMPTS = 1`.
3. **Relaxed floor (#1719):** If PM responds with a **non-empty but
   prefix-less** message, delivers it directly via `on_user_payload` and sets
   `exit_reason = "pm_floor_delivered"`. This bypasses `_route_pm_classification`
   so no `PM_COMPLIANCE_NUDGE` is written to a PTY that is about to be torn
   down, and avoids the canned fallback when PM produced a real response.
4. If PM responds with a `[/user]` or `[/complete]` prefix, that payload is
   delivered normally and `user_facing_routed = True`.
5. If PM still does not produce **any text** after all attempts, delivers
   `OPERATOR_TERMINAL_MESSAGE` directly via `on_user_payload` and sets
   `exit_reason = "pm_no_user_message"`. This is the last-resort canned
   fallback, reserved for a genuinely empty PM transcript.

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
- (e) A session that completes via `pm_floor_delivered` (wrap-up guard
  delivered PM's non-empty but prefix-less last message) sends the real PM
  message — not the canned fallback. This is the expected "defense in depth"
  path when the per-turn reminder (#1719) didn't prevent prefix drift.
- (f) A session that completes via `pm_no_user_message` (wrap-up guard
  exhausted, genuinely empty PM transcript) sends `OPERATOR_TERMINAL_MESSAGE`
  — a brief canned notice that the task was handled. This is a last resort;
  both the per-turn reminder and the relaxed floor should normally prevent it.

## Per-turn silence cap (not total runtime cap)

Sessions can last up to ~6 hours of wall-clock. The bound is **per-turn
silence**, not total runtime: `CYCLE_IDLE_TIMEOUT_S` (12 h sanity ceiling in
`container.py`) is the per-cycle ceiling on a single PTY's idle wait. If a PTY
does not reach idle within this window, the container exits as `pm_hang` /
`dev_hang`. A wall-clock cap would force user-visible mid-session termination
the operator does not want.

> **Hang detection is delegated to the liveness layer** (issue #1724). The 12h
> ceiling is a sanity backstop — real hang recovery is handled by
> `agent/session_health.py`, which uses PTY-activity fields and the two-tier
> no-progress detector rather than a per-turn wall-clock timeout. See
> [Never-Started Session Recovery](never_started_session_recovery.md) for the
> full design.
>
> **Default-tier tool-timeout PTY gate (issue #1784):** for granite PTY sessions,
> the per-tool timeout sub-loop's default-tier kill (`Bash`/`Skill`/`Task`, 300s
> budget) is gated on `mid_run_quiescent_since` — a session whose PTY screen is
> still painting is never killed, regardless of wall-clock age. The kill fires
> when the screen has been quiescent for `>= MID_RUN_QUIESCENCE_SECS (180s)`.
> Worst-case recovery bound: ~330s (300s budget + ~30s tick cadence). SDK sessions
> (no PTY) are unaffected — they continue to use the flat 300s age-only kill.
>
> **Never-started PTY-liveness gate (issue #1792):** the sibling gate for the D0
> never-started kill path. Prime liveness is judged on `last_pty_activity_at`
> freshness (not `mid_run_quiescent_since`, which is always `None` during
> priming). The kill is deferred when the PTY read loop is fresh and
> `last_pty_activity_at` is within `NEVER_STARTED_PTY_LIVENESS_SECS` (default
> 90s, env-overridable). See
> [pm-session-liveness.md — PTY-liveness gates](pm-session-liveness.md#pty-liveness-gates-for-kill-paths)
> for the full side-by-side comparison of both gates.

## Failure-simulation test harness

The silent-wedge failures this production path is prone to (idle-heuristic
breakage on a Claude Code UI revision, startup-dialog drift, process hang, loop,
crash) are reproduced locally and at volume by the
[Granite Failure-Simulation Test Harness](granite-failure-simulation-harness.md)
(#1837). It pairs a deterministic seam-injection substrate (always-on, no model)
with an ollama-backed real-`claude` E2E substrate (free, gated on
`GRANITE_OLLAMA_SMOKE=1`) that doubles as a canary for new `claude` binary
releases. Test-only: it changes nothing in this production path.

## Observability

The adapter writes non-user-visible progress to `agent_session.session_events`
(a `ListField`). Telegram is not spammed. Event types:

| `type` | When | Key fields |
|--------|------|------------|
| `exit_summary` | every run, on completion | `exit_reason`, `turns`, `compliance_misses`, `ts` |
| `exit_anomaly` | `exit_reason in {pm_hang, dev_hang, startup_unresolved, pm_no_user_message, exception (soft→WARNING, hard→ERROR)}` | `exit_reason`, `ts` — logged at ERROR for hard exits (Sentry log-capture picks it up; on-call path for session-runner regressions); WARNING for soft exception exits (had turns → likely network blip, no Sentry alert). For `startup_unresolved` exits: also carries `startup_failure_kind` (`"plateau"` or `"ceiling"`) and `startup_diagnostic_frame` (truncated frame excerpt, up to 1000 chars). Note: `pm_floor_delivered` is a clean exit and does NOT emit `exit_anomaly`. |
| `granite_user_routed` | on each `[/user]` payload routing attempt | `event_type`, `text` (payload size + delivery result) |
| `granite_complete_routed` | on each `[/complete]` payload routing attempt | `event_type`, `text` (payload size + delivery result) |
| `granite_delivery_recovered_via_outbox` | delivery timeout or loop-closed condition in `_deliver_sync` where the payload was successfully re-enqueued to the outbox; also written when the same-thread done-callback fires on task failure/cancellation and re-enqueue succeeds | `event_type`, `text`, `payload_chars`, `reason` (`recovered_via_outbox`), `failure_reason` (exception detail; tagged `[future_uncancellable_possible_duplicate]` when `future.cancel()` returned `False`), `recovered` (`True`), `ts` |
| `granite_delivery_dropped` | delivery timeout or loop-closed condition in `_deliver_sync` where **both** the primary send and the outbox re-enqueue failed (double-failure, permanent loss) | `event_type`, `text`, `payload_chars`, `reason` (`dropped`), `failure_reason` (exception detail), `recovered` (`False`), `ts` |
| `delivery_failure` | a mid-loop `send_cb` raised (back-compat `type` value carried by all delivery-failure events, matching entries already written to Redis prior to the `granite_delivery_*` rename) | `payload_chars`, `reason`, `ts` |

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

### Transcript-read diagnostic (issue #1708)

When the steady-state loop, prime-turn read, or wrap-up-guard read finds no
new PM output, a `WARNING` is emitted to `logs/worker.log` (the granite
container's `logging.getLogger(__name__)` output). The warning names one of
three greppable branches:

| Substring | Meaning |
|-----------|---------|
| `transcript read: path-None` | `pm_transcript` is `None` — the path was never resolved (session-id absent at spawn) |
| `transcript read: file-missing` | path was resolved but the file does not exist on disk |
| `transcript read: no-new-entry` | file exists but `last_assistant_text()` returned empty (valid file, no new text-bearing entry past the baseline count) |

Each warning also logs the fully-resolved attempted path string,
`spec.pm_session_id` / `spec.dev_session_id` presence, and
`pty._session_id`, so an on-call can distinguish a spawn-threading gap (spec
carried IDs, PTY did not) from a slug mismatch.

`grep "transcript read:" logs/worker.log` is the primary triage command for
empty-read investigations.

**The `no-new-entry` branch is the only legitimate path to `OPERATOR_TERMINAL_MESSAGE`.**
The other two branches (`path-None`, `file-missing`) indicate a configuration
or spawn-threading defect rather than a PM that genuinely produced no output.

#### `_needs_session_spawn` session-id invariant

`PTYPool._needs_session_spawn()` returns `True` whenever the spec carries any
per-session identity: `env`, `pm_model`, `cwd` override, OR `pm_session_id` /
`dev_session_id`. This ensures that a spec carrying explicit session-ids always
forces a per-session spawn — even if `env` happens to be empty — preventing a
prewarmed pair (which has no `--session-id` binding) from being reused for a
session that needs a deterministic transcript path.

#### Realpath-resolved transcript slug

Both `_transcript_path()` in `container.py` and the slug computation in
`bridge_adapter.py` apply `os.path.realpath(cwd)` (only when `cwd` is truthy)
before `cwd.replace("/", "-")`. This matches the slug that `claude` itself
computes for its project directory, which also resolves symlinks. Without this
step, a working directory that crosses a symlink (e.g. a `.worktrees/` path
under a symlinked checkout root) would produce a slug that does not match the
transcript file `claude` actually writes, and every steady-state read would
return empty (`transcript read: file-missing`).

Note: `os.path.realpath("")` returns the process CWD, which would corrupt the
slug for falsy values. The `if not session_id: return None` guard in
`_transcript_path` is checked *before* the realpath call, so the path-None
diagnostic branch is never bypassed.

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
| `pty_slot` | `IntField(null=True)` | Stable physical PTYPool slot index (0-based, issue #1663) |
| `startup_failure_kind` | `Field(null=True)` | `"plateau"` or `"ceiling"` for `startup_unresolved` exits; `None` for all other exit reasons (issue #1710) |
| `startup_captured_frame` | `Field(null=True)` | Stripped PM+Dev PTY snapshot captured at startup bail time, size-capped to 6000 chars; `None` for normal exits (issue #1710) |

All are nullable: non-granite sessions and pre-deploy granite sessions
leave them as `None`. The dashboard uses them to surface active PTY processes
and link to transcript files. `startup_failure_kind` and
`startup_captured_frame` are populated only for `startup_unresolved` exits.

#### `pty_slot` semantics

`pty_slot` is the 0-based index of the `PTYPool` slot that ran the session. The
slot index is **stable** for the lifetime of the slot (it does not change across
respawns). It does **not** identify a specific PTY process — use the co-persisted
`pm_pid` / `dev_pid` to correlate the actual OS processes that ran in that slot.

The slot index is stamped onto `ContainerResult.pty_slot` by `BridgeAdapter.run`
immediately after `acquire_pair` exits, then propagated to `AgentSession.pty_slot`
by `_publish_exit_summary`.

**Flow:**
```
PTYPool.acquire_pair() → yields (pm, dev, slot.idx)
  ↓ BridgeAdapter stamps result.pty_slot = slot.idx
  ↓ _publish_exit_summary persists AgentSession.pty_slot
  ↓ _session_to_pipeline copies to PipelineProgress.pty_slot
  ↓ dashboard.json / session modal renders it
```

The **partial-data guard** in `_publish_exit_summary` logs a `WARNING` when
`pm_pid` is set but `pty_slot` is `None` — a signal that the
`acquire_pair` 3-tuple yield has regressed to a 2-tuple.

#### Session modal (issue #1663)

The dashboard session modal surfaces five granite PTY fields in a dedicated
block: `pm_pid`, `dev_pid`, `pm_transcript_path`, `dev_transcript_path`, and
`pty_slot`. The block is rendered only when at least one of these fields is
non-null (granite-path sessions only). `pty_slot` is shown as "PTY pool slot N"
alongside the PID values to give operators a quick correlation between pool slot
occupancy and the running session.

### `exit_reason` and reaction gating

`AgentSession.exit_reason` is granite-path-populated. The dashboard renders a
warning chip for non-clean values. Clean exit reasons: `pm_complete`, `pm_user`,
`pm_floor_delivered` (wrap-up guard delivered a real prefix-less PM message,
issue #1719). Anomaly exit reasons: `pm_hang`, `dev_hang`,
`startup_unresolved`, `pm_no_user_message`, `pm_max_turns`, `exception`.

The executor's reaction logic consults `exit_reason` in addition to
`user_facing_routed`:

- `exit_reason` in anomaly set → `REACTION_ERROR` emoji regardless of
  `user_facing_routed`.
- Clean `exit_reason` + `user_facing_routed=False` (`communicated=False` chip
  in dashboard) → normal reaction (the wrap-up guard fired but the session
  technically completed without user-facing output).
- Clean `exit_reason` + `user_facing_routed=True` → `REACTION_COMPLETE`.
- `pm_floor_delivered` always sets `user_facing_routed=True` (the floor path
  only runs when PM produced non-empty text) → `REACTION_COMPLETE`.

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

#### PTY-activity liveness (issue #1724)

As of issue #1724, `Container.__init__` accepts an optional `on_pty_read:
Callable[[str], None]` hook. `BridgeAdapter` wires `_make_pty_read_callback()`
into this slot, which fires after each turn-boundary idle return inside
`_cycle_idle()`. The callback diff-gates on `_prev_pty_buffer` (only fires when
the screen actually repainted) and writes two new `AgentSession` fields:

| Field | Written when |
|-------|-------------|
| `last_pty_read_loop_at` | Every `on_pty_read` call (proves the loop is alive) |
| `last_pty_activity_at` | Only when `buffer != _prev_pty_buffer` (screen repainted) |

Two additional fields support Path-B mid-run quiescence tracking (observe-only
stage-1 — no recovery fired yet; stage-2 deferred to a follow-up to #1724):

| Field | Written when |
|-------|-------------|
| `mid_run_quiescent_since` | Set on first tick where `last_pty_activity_at` looks stale; cleared on activity |
| `mid_run_pty_snapshot` | Snapshot taken when quiescence is first detected |

`session_health._eval_mid_run_pty_stage1()` reads these fields to detect
suspects and emit a `WARNING: "stage-1 CONFIRMED SUSPECT"` log when
`MID_RUN_QUIESCENCE_SECS` (180s, env-tunable) is exceeded. See
[Never-Started Session Recovery](never_started_session_recovery.md) for the
full Path-A / Path-B design.

#### Wired silent-wedge signals (issue #1843)

Two signals that the session-health machinery already computed but that never
reached a granite session are now wired:

- **CLI-hook tool liveness arms the #1270 tier loop.** Granite's PM/Dev
  `claude` PTY children run the repo CLI hooks (`.claude/hooks/pre_tool_use.py`,
  `post_tool_use.py`), where `AGENT_SESSION_ID` is unset, so the SDK
  in-process `record_tool_boundary` writer no-ops for them. The CLI hooks now
  resolve the `AgentSession` from the on-disk sidecar and stamp
  `current_tool_name` + `last_tool_use_at` directly: the PreToolUse hook sets
  the tool name, the PostToolUse hook clears it, and both refresh the
  timestamp. `last_tool_use_at` is written as a `datetime`
  (`datetime.now(tz=UTC)`), the type `session_health._check_tool_timeout`
  requires (it gates on `isinstance(last_at, datetime)` and short-circuits on a
  float). With these fields populated, the #1270 per-tool timeout tier loop
  arms for granite sessions that stall inside a single tool call. A file-based
  per-session cooldown (`tool_liveness_cooldown` in the sidecar dir, 5s window)
  bounds the write rate, since each CLI-hook invocation is a fresh process and
  cannot share the SDK path's in-memory cooldown; the gate fails open so a
  cooldown-file problem never masks a wedge. Both writes are fail-silent — an
  unresolvable sidecar exits the hook cleanly.
- **Per-iteration PTY-read callback refreshes `last_pty_read_loop_at`
  mid-turn.** `PTYDriver.read_until_idle` now takes an optional
  `on_read_iteration` callback, invoked once per inner poll tick.
  `Container._cycle_idle` threads a throttled wrapper of `self._fire_pty_read`
  through it, so the bridge-adapter freshness writer stamps
  `last_pty_read_loop_at` on inner poll iterations rather than only once per
  `_cycle_idle` return. The wrapper coalesces to at most one stamp per second
  (`PTY_READ_ITER_MIN_INTERVAL_S`), matching the 5s coalescing philosophy of
  the SDK-path liveness writer so verbose output cannot produce an
  AgentSession write storm while still sampling far finer than the
  once-per-cycle boundary fire. A wedge inside a long idle-fallback turn now
  refreshes liveness far sooner than the cycle window. The hook-driven
  `_await_turn_end` path (#1688) already fires `_fire_pty_read` per poll-tick;
  Gap B closes the same window on the idle-fallback read loop. The callback is
  best-effort — a raising callback is caught and never breaks the read loop.

The `granite_wedged` verdict already actuates recovery: `reflections/stall_advisory.py`
builds the session timeline, calls `classify_session_stall`, and runs its
`_maybe_recover` kill-and-recover ladder (#1768/#1773), gated by
`stall_recovery_enabled` (dry-run by default, reversible per-machine `.env`).
The residual — reaping the granite PTY *process group* rather than a single PID
— is owned by #1820's progress-deadline cancel scope and the #1816
`container` `killpg` seam. Issue #1843 adds no kill path and does not touch
`session_health.py`.

#### Priming now stamps PTY liveness fields (issue #1878)

Issue #1792 (PR #1798) added `_prime_pty_alive()` — a deferral for the D0
never-started kill gate in `agent/session_health.py` that keeps a slow-but-alive
cold start (e.g. Opus warming up) from being killed as a wedged session. The
deferral reads `last_pty_read_loop_at` and `last_pty_activity_at`. Those fields
were only ever stamped by the steady-state loop's `on_read_iteration` callback
(`_pty_read_iteration_cb`, #1843 Gap B) — `_prime_session` never wired the same
callback into its own `read_until_idle` calls, so **during priming the fields
never moved**, and `_prime_pty_alive()` could never observe fresh liveness. The
deferral was reachable in code but permanently defeated in the one phase
(priming) it was built to protect.

Issue #1878 closes this gap: `_prime_session` (`agent/granite_container/container.py`)
now passes `on_read_iteration=self._pty_read_iteration_cb` into all three
`read_until_idle` calls it makes (the trust-dismiss loop, the pre-write idle
wait, and the post-write idle wait). This is the same throttled callback the
steady-state loop already used — no new field, no new constant, just the
existing producer wired into the previously-unwired priming consumer path.
With this change:

- `last_pty_read_loop_at` is stamped on every inner poll tick during priming
  (proves the read loop itself is alive), exactly as it already was in the
  steady-state loop.
- `last_pty_activity_at` is stamped only when the normalized PTY frame changes
  (`_normalize_pty_buffer`, the anti-spinner guard from issue #1768 that strips
  spinner glyphs and elapsed-second counters so a merely-animating frame does
  not read as "activity"). A prime that is genuinely progressing (model
  streaming its response) keeps this field fresh; a prime that has actually
  wedged does not.

The net effect: `_prime_pty_alive()` now correctly defers the D0 never-started
kill while a slow cold-start prime is still painting new content, and still
kills on a stale or frozen prime — completing the intent of #1792 rather than
changing its gating logic. See
[pm-session-liveness.md — PTY-liveness gates (Gate 2)](pm-session-liveness.md#pty-liveness-gates-for-kill-paths)
for the gate's branch logic, which is unchanged by this fix.

**Why no `continue`-nudge recovery rung was added here.** The original #1878
plan also scoped a rung that would send a `continue` nudge to a wedged/no-progress
session before killing it, as a cheaper first recovery attempt. That rung was
split out to follow-up issue **#1879** after plan critique found it structurally
infeasible on current main: the external steering queue (`agent/steering.py`,
[Mid-run steering](#mid-run-steering-issue-1779) above) is drained only at the
**top of a completed turn** in the steady-state loop. A session that is
wedged or stuck in `no_progress` by definition never reaches that drain point —
it is still inside an in-flight turn (or, for priming, hasn't reached the
steady-state loop at all). The drain point and the wedge condition are mutually
exclusive on the current architecture, so there is no live consumer for a
nudge to reach. #1879 will build the mid-run steering-drain infrastructure a
nudge rung requires before that recovery path can be added.

### Startup hard ceiling

The startup loop polls both PTYs on short (`STARTUP_CYCLE_TIMEOUT_S` = 3s)
reads until it settles (see "Startup settle conditions" above — settling no
longer requires both PTYs idle in the same cycle, and a PM terminal turn can
settle without Dev reaching idle at all), dismissing transient startup events
(trust-folder, update notice) along the way. A slow cold persona load simply
keeps the loop cycling cheaply. If the PTYs never settle within
`STARTUP_HARD_CEILING_S` (600s), the run exits `startup_unresolved` — the
distinct failure signature for a broken `--permission-mode` flag (a TUI
upgrade renaming the flag means the bypass bar never paints, so the idle
heuristic can never fire). Without the ceiling that failure would burn the
steady-state budget and report a misleading `pm_hang`.

### Startup fast diagnostic: plateau detection, frame capture, and alert (issue #1710)

The startup loop has an **orthogonal** early-bail path layered on top of the
600s ceiling. This fast-diagnostic path does not shorten the ceiling — it
fires only when a deterministic stuck state is confirmed.

#### Plateau detector

Every startup cycle, the loop computes a **write-independent fingerprint**:
`(pm_idle_bool, dev_idle_bool, response)`, where `response` is the value
returned by `_handle_startup` (the parser's verdict, computed *before* any
`write()` call). When `STARTUP_PLATEAU_CYCLES = 10` consecutive identical
fingerprints accumulate, the startup is confirmed stuck and the loop bails
immediately.

**Why the fingerprint is write-independent:** the `write()` call at
`container.py` resets `_turn_text` in `pty_driver.py` before sending, so the
post-write `turn_buffer` (a "capture since the last write" buffer) restarts
empty on each oscillating-event cycle. Hashing the buffer tail would never
repeat across cycles and the counter would never accumulate. By hashing the
parser's *verdict* instead, both stuck shapes accumulate cleanly: an
oscillating event repeats the same `response` string (fingerprint stable); a
silent never-started PTY yields `(False, False, None)` (fingerprint stable
at `(False, False, None)`). Genuine progress flips an idle bool or changes
the parser verdict, resetting the count to zero.

At `STARTUP_CYCLE_TIMEOUT_S = 3s` per cycle, 10 identical cycles ≈ 30 seconds
of confirmed zero-progress before bailing — well under the 600s ceiling, yet
past transient cold-start jitter. The constant is documented as a tuning knob
to tighten after observing real failures.

**Why the fingerprint is computed outside the `response is None` guard:** the
prior code structure only reached the accumulator on the no-event path; a
session emitting a spurious startup event every other cycle would never
accumulate consecutive no-progress cycles if the counter lived there. The
fingerprint is evaluated at the top of the loop body, before any branching,
so every cycle (including event-emitting oscillating ones) is counted.

#### Frame capture (`_capture_startup_frame`)

At bail time — whether plateau or ceiling — the container captures the last
PM and Dev PTY buffer snapshots into a single diagnostic frame string:

- Source: `level_tail` (level-triggered `turn_buffer`, the persistent
  "capture since last `write()`") for each PTY, with `edge_buffer` as
  fallback to ensure the frame is never blank.
- Stripped of non-printable bytes; capped per-buffer and in total (sum cap
  ≈ 6000 chars on `AgentSession`, 1000 chars in the event payload).
- Header line: `[startup-failure kind=plateau|ceiling cycles=N]` followed by
  PM and Dev sections.

The pure helper `_capture_startup_frame(pm_level_tail, dev_level_tail, kind,
cycles)` is unit-testable without a live PTY and handles empty/None buffers.

The captured frame is attached to `ContainerResult` as
`startup_diagnostic_frame`. The `_startup_cycle_idle` return tuple was widened
to surface both the edge-triggered `buffer` (fed to the parser unchanged, so
event detection stays edge-triggered) and the level-triggered `turn_buffer`
(used only by frame capture). This prevents a blanket-swap regression that
would re-fire dismissed events on every poll tick.

#### Startup failure kind

`ContainerResult` gains three nullable fields:

| Field | Values | Description |
|-------|--------|-------------|
| `startup_failure_kind` | `"plateau"` / `"ceiling"` | How the startup failed |
| `startup_diagnostic_frame` | `str \| None` | Human-readable stripped PTY snapshot |
| `startup_plateau_cycles` | `int \| None` | Plateau-only: consecutive identical cycles detected |

#### Telegram alert (`_send_startup_alert`)

On any `startup_unresolved` exit, `BridgeAdapter._maybe_publish_exit_anomaly`
fires a best-effort direct notification to the `"Eng: Valor"` Telegram chat:

```
[granite-startup-failure] kind=plateau cycles=10  session=<id>
<frame excerpt>
```

The alert is gated by `_should_alert(machine) -> bool` — a two-layer
cooldown with **inverted contract** (returns `True` when sending is
permitted):

1. **Process-local (checked first):** a module-level `dict[str, float]` of
   last-alert monotonic timestamps per machine; permits only if
   `time.monotonic() - last >= 300s`. This layer survives Redis-down outages.
2. **Cross-process (Redis TTL):** if the process-local gate permits, attempts
   a per-machine `SET granite:startup_alert_cooldown:{machine} NX EX 300`
   via the Popoto Redis client. Key already existed → suppress. Key set →
   send. Redis unavailable → fall through to the process-local decision
   (send anyway; better a duplicate alert than a silenced outage).

**Subprocess call:** `subprocess.run(["valor-telegram", "send", "--chat",
"Eng: Valor", message], capture_output=True, text=True, timeout=3,
check=False)`. The `timeout=3` bound (not the 10s precedent) keeps
worker-thread blocking short during a fleet-wide outage where every session
triggers the path. After the first alert within a window, subsequent
suppressed sessions skip the subprocess entirely (fast gate, not a 3s call).

**Suppression logging:** when the alert is suppressed due to an active
cooldown window (either layer) OR a send failure (CLI absent, timeout), the
adapter logs `logger.error("[granite-alert-suppressed] ...")` so Sentry
captures it. The suppression tag is **not** emitted on the Redis-down path
(the alert still sends there; logging suppression would be a false signal).

#### Persistence

`BridgeAdapter._publish_exit_summary` persists two new nullable fields on
`AgentSession` for every `startup_unresolved` exit:

| AgentSession field | Source | Cap |
|--------------------|--------|-----|
| `startup_failure_kind` | `ContainerResult.startup_failure_kind` | — |
| `startup_captured_frame` | `ContainerResult.startup_diagnostic_frame` | 6000 chars |

Both are additive nullable fields; existing non-granite sessions and
pre-deploy records read them as `None` (Popoto's `_heal_descriptor_pollution`
handles generic field addition per issues #1099/#1172).

## Failure handling

- **Missing bridge callback** (standalone worker, no bridge registered):
  `_resolve_callbacks` returns `(None, None)`; the adapter installs
  logger-only no-op callbacks and the container still runs to completion. No
  crash, no user delivery.
- **Mid-loop delivery timeout (outbox re-enqueue recovery)**: when the
  event-loop future that delivers a `[/user]` or `[/complete]` payload does
  not resolve within `GRANITE_DELIVERY_TIMEOUT_S` (default 30 s,
  env-overridable), `_deliver_sync` re-enqueues the payload to
  `telegram:outbox:{session_id}` via `_enqueue_to_outbox`. The payload is a
  6-field JSON dict matching the shape used by `agent/output_handler.py`:
  `chat_id`, `reply_to`, `text`, `session_id`, `timestamp`, and optional
  `file_paths`; the key expires after 3600 s. The relay then delivers it so
  the reply is never silently lost. The resulting session event is
  `granite_delivery_recovered_via_outbox` when re-enqueue succeeds, or
  `granite_delivery_dropped` on a double-failure (timeout AND re-enqueue both failed).
  If `future.cancel()` returns `False` (the coroutine was already running and
  cannot be cancelled), the event is additionally tagged
  `[future_uncancellable_possible_duplicate]`; `bridge/redundancy_filter.py`
  is the downstream backstop against duplicate delivery in that case. The same
  re-enqueue path is also attached as a done-callback on the same-thread
  fire-and-forget branch so task failure or cancellation likewise triggers
  recovery. The user never sees a delivery-failed message (no-spam rule).
- **Worker SIGKILL mid-run**: orphan PTY children survive. The next worker
  startup reads `data/granite_pty_pids.json` and PID-kills them. The kill is
  PID-targeted, so an operator's personal interactive `claude` session on
  another project is never touched.
- **Crash-resume PID registration (plan #1851)**: `_resume_crashed_pty`
  spawns its replacement `PTYDriver` OUTSIDE the pool's own spawn paths
  (`_spawn_slot`/`_spawn_session_pair`), so before this fix the resumed
  process's PID was never written to `data/granite_pty_pids.json` and could
  leak as an orphan across a later worker crash/restart cycle. `Container`
  now accepts `on_pty_spawn`/`on_pty_despawn` callbacks (the same injection
  seam as `on_turn`/`on_pty_read`); `BridgeAdapter` wires them to
  `PTYPool.register_pid`/`PTYPool.unregister_pid`. `_resume_crashed_pty`
  calls `on_pty_spawn(new_pid)` immediately after the replacement PTY's
  `spawn()` returns and BEFORE its `write()` call — the process is already
  live at `spawn()`, and a `write()` failure must not leave a live,
  unregistered `claude` process. It calls `on_pty_despawn(dead_pid)` from
  the method's outer `finally` (so it fires on every exit path), gated on
  the dead PTY's `close(force=True)` having actually succeeded — a
  swallowed close failure means the old process may still be alive, so it
  stays registered for the sweep to reap. `PTYPool.register_pid`/
  `unregister_pid` are thread-safe (crash-resume callbacks fire from the
  container's session thread, not the pool's event-loop thread) via a new
  `_pids_lock` guarding every `_spawned_pids` mutation and the
  `_persist_pids` snapshot. The self-spawned/test/CLI `Container` path (no
  `PTYPool`) leaves both callbacks `None` and is unaffected — it already
  tears its PTYs down synchronously via `_close_pair_and_reap` (#1816).
- **PTY-master U-state block (issue #1767)**: when the worker's main thread
  is blocking inside `os.read()` on the PTY master fd (e.g. waiting for the
  granite Dev PTY to produce output), the kernel places it in uninterruptible
  sleep (U-state). In this state `SIGKILL` is queued but not delivered until
  the blocking syscall returns — the process cannot be killed by signal alone.
  The worker watchdog's W3 rung (`launchctl bootout gui/<uid>/com.valor.worker`)
  is the external backstop: removing the launchd job causes the kernel to clean
  the process's fd table on exit, which makes the PTY-master `read()` return
  EOF and unblocks the syscall so the process can exit and launchd can respawn.
  Cross-process PTY fd close is not feasible on macOS (`os.close` only owns
  the calling process's fds; `/proc` is Linux-only; `psutil.open_files` does
  not surface PTY devices), so W3 bootout is the highest-leverage automated
  action. If W3 does not free the process within 10 s, the watchdog escalates
  to W4/W5 CRITICAL alerts requiring operator intervention. See
  [Bridge Self-Healing §18](bridge-self-healing.md) for the full W1→W5 ladder.

## Dev relay and `BuilderHarness`

As of plan #1725, the dev-relay branch of `_route_pm_classification` delegates
to a `BuilderHarness` abstraction (`agent/granite_container/builder.py`) rather
than inlining PTY+transcript logic.

- Bare `[/dev]` and `[/dev:claude]` route to `PtyClaudeBuilder` — the existing
  Dev PTY + JSONL transcript path, extracted verbatim. Behavior is unchanged.
- `[/dev:pi]` routes to `PiSubprocessBuilder` — a subprocess-based alternative
  that bypasses PTY entirely, running `pi -p --mode json` in the same working
  directory as the Dev PTY.

The container caller (`_route_pm_classification`) still owns `_last_dev_report`
assignment and the empty-return fallback gate (`DEV_REPORT_UNAVAILABLE`). The
builder returns only the final assistant text (or `""` on failure); it never
touches those container-owned fields. See
[Pluggable Builder Harness](pluggable-builder-harness.md) for the full seam design.

## Mid-run steering (issue #1779)

The steady-state loop supports two in-flight steering channels, both additive to
the existing `for turn` loop — no new threads, no new synchronization primitives.

### Part 1 — Bridge → PM steering injection

When a Telegram message arrives while a granite session is running, the bridge's
`_ack_steering_routed` RPUSHes a JSON payload onto the Redis list
`steering:{session_id}` via `agent.steering.push_steering_message`. The granite
container drains this list at the **top of each steady-state turn** using the
`poll_steering` callback injected by `BridgeAdapter`:

```python
# BridgeAdapter.run — storage-agnostic closure
def _poll_steering() -> list[dict]:
    if not steering_session_id:
        return []
    try:
        return pop_all_steering_messages(steering_session_id)
    except Exception as e:
        logger.warning("[granite-bridge-adapter] poll_steering drain failed ...")
        return []
```

`pop_all_steering_messages` performs an atomic FIFO LPOP drain — race-free across
the bridge process (writer) and the worker's container thread (sole consumer).

**Why the Redis list, not a Popoto `queued_steering_messages` ListField.** A
Popoto ListField would be consumed via a cross-process read-modify-write: the
bridge reads [A], appends B, saves [A,B], while the container concurrently reads
[A] and saves []. A message can be dropped in this race window with no way to
close it without a distributed lock. The `steering:{session_id}` Redis list uses
atomic RPUSH / LPOP — a message pushed between two drains is simply picked up on
the next turn, never lost. This is why the Redis list is the sole steering inbox
for every harness (SDK, CLI, and granite PTY) — there is no separate Popoto-field
path to keep in sync.

**Injection flow (per turn):**

1. `_poll_steering()` drains the Redis list. The call is fail-silent — a raised
   exception returns `[]` and never crashes the loop, matching the `_on_turn`
   pattern.
2. If any drained message has `is_abort=True`, the container delivers the fixed
   user-facing string `"Session stopped at your request."` through
   `_on_user_payload` and sets `result.user_facing_routed = True` **before**
   breaking with `exit_reason="steer_abort"`. Output emitted after the break is
   dropped, so delivery must precede it. `steer_abort` is listed in
   `_CLEAN_GRANITE_EXIT_REASONS` in `agent/session_executor.py` — it is reported
   as a controlled operator termination, not a `REACTION_ERROR`.
3. For non-abort messages, the container calls `_cycle_idle(self._pm_pty)` to wait
   for PM to finish any in-flight tool execution before writing. This is the whole
   guarantee that steering does not corrupt PM's current turn — the write lands
   only once PM is confirmed idle.
4. Each message is written as `\n[Steering from {sender}]: {text}\n` to the PM
   PTY. Empty or whitespace-only messages are skipped. The `sender` field comes
   from the Redis payload (the bridge records the Telegram sender name when it
   calls `push_steering_message`).
5. The loop falls through to the existing per-turn idle read. The `pm_baseline`
   content-identity guard (a snapshot of the text-bearing JSONL entry count taken
   before the idle read) requires a **new** entry from PM, so PM's response to the
   steering is captured cleanly and routed normally.

**Latency:** at-most-one-turn. A message pushed between two drains is picked up on
the very next PM turn boundary.

**PM-hang during injection:** if `_cycle_idle(PM)` returns `pm_idle=False` after a
non-empty drain, the messages are already atomically removed from Redis and cannot
be re-queued without reintroducing the cross-process race. The container logs a
`WARNING` naming the count and text of lost messages (so an operator can
re-deliver via `valor-session steer --id <id>`) and exits as `pm_hang`.

### Part 2 — PM → Dev `[/dev:steer]` injection

The PM can queue a mid-task correction directly into the Dev PTY by emitting a
`[/dev:steer]` prefix. Both forms are accepted:

```
[/dev:steer]
Focus on the auth module; the migration work is done.
```

```
[/dev:steer] Focus on the auth module; the migration work is done.
```

`classify_pm_prefix` parses both into `destination="dev", harness="steer"`. In
`_route_pm_classification`, the module constant `STEER_HARNESS_SUFFIX = "steer"`
is checked **before** `_get_builder` — `steer` is a reserved harness suffix that
real builder harnesses (`claude`, `pi`) must never receive.

**Token-strip (single-line edge case).** The single-line form `[/dev:steer] text`
fails the anchored `PREFIX_TOKEN_RE` (which requires nothing after `]` on line 1)
and falls to the fallback classifier, which returns the whole tail — including the
literal `[/dev:steer]` token — as the payload with `compliance_miss=True`. Writing
that verbatim to Dev would poison the instruction with the routing token. The
`dev_steer` branch applies a defensive strip regardless of which classifier path
produced the payload:

```python
clean = re.sub(r"\[/dev:steer\]\s*", "", dev_prompt, count=1).strip()
```

This is a no-op for the strict path (already token-free) and removes the leaked
token for the single-line fallback path.

**Write-and-continue semantics (not preemption).** In the synchronous alternating
loop, PM only runs when Dev is idle. At the moment PM emits `[/dev:steer]`, Dev
is always idle — the loop is single-threaded and PM never runs while Dev has an
active turn. The steer text is therefore written directly to the Dev PTY
(`self._dev_pty.write(clean + "\n")`) without any Dev-idle wait, and Dev picks it
up as its next input on the following read. The PM persona describes `[/dev:steer]`
as "queue a correction Dev sees immediately as its next input" — not "interrupt
Dev now." True preemption of an in-flight Dev tool call would require PTY signal
handling and is explicitly out of scope.

After the Dev write, a one-line continuation ack (`PM_DEV_STEER_ACK`) is written
to the PM PTY so PM produces its next turn rather than hanging on an empty idle
read. The turn is recorded as `TurnRecord(classification="dev_steer")` and the
loop continues.

If the payload is empty after token-stripping (a token-only `[/dev:steer]` with no
body), `PM_COMPLIANCE_NUDGE` is written to PM — the same path as an empty `[/dev]`
— and no Dev write occurs.

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
the pexpect thread was skipped as `no_event_loop`); `Container` teardown reaps
only its own self-spawned PTY process groups via `os.killpg` and never touches
pool-owned pairs (#1816 dropped the machine-wide `pkill -f` fallback in favor of this
scoped reaping —
see [Worker Fault Containment](worker-fault-containment.md)); the pool respawns with the
original `cwd`, checks pair liveness at acquire, clears the slot event at
release, and prunes completed respawn tasks; `read_until_idle` declares idle
only after `QUIESCENCE_S` (2.0s) of byte-silence, evaluated level-triggered
against a persistent per-turn capture — an active turn repaints the spinner
at ≥1 Hz and so can never pass the gate, while a settled-and-silent PTY (which
an edge-triggered check could never observe) passes it on every poll. This
replaced an earlier regex loading-spinner negative, which mid-turn cell-
fragment repaints could both evade (false idle) and falsely latch (a stale
spinner frame blocking idle for the rest of the call).

> **#1688 update — `read_until_idle` is no longer the turn-completion
> authority.** With hook-driven turn returns (default on), the parent `Stop`
> hook edge is the completion signal; `read_until_idle` is demoted to a
> running/idle badge, liveness, and crash detection. The idle heuristic above
> is retained as the documented fallback (feature flag off / no edge file). See
> [Granite Hook-Driven Turn Returns](granite-hook-driven-turn-returns.md).

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
| `exit_reason in ("pm_complete", "pm_user", "pm_floor_delivered")` | `completed` | `result.exit_reason` |
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
interim signal. Manual operator action is the only safe reaping path — avoid
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

## Goal anchoring and turn-loop ownership

**Added in issue #1741.**

### PM prime: `/goal` anchored to `$ARGUMENTS`

On its very first turn, the PM session runs `/goal` with a completion condition
anchored to the originating request (`$ARGUMENTS`). The goal text specifies two
required conditions for `[/complete]` to be valid:

1. The Dev has reported the routed work complete (concretely: the Dev's relayed
   report states the PR for `#{N}` is merged).
2. The PM has authored a `FINAL [/complete]` reply delivering the result (not a
   progress report).

The `/goal` evaluator also treats the current turn as **QUIESCENT** (does not
fire another turn) when the PM's most recent output ends with a line beginning
`WAITING:` — the sentinel indicating the PM has handed off to the Dev and is
waiting for the Dev's relay.

Steering messages from the operator and relay messages from the Dev are
**course-corrections toward this goal**, never a redefinition of it. The goal
is fixed at the originating `$ARGUMENTS`; it survives the entire session.

### Dev prime: accepts PM-set `/goal` on first relay

The Dev session does not set its own `/goal`. On the first relay from the PM,
the Dev accepts the goal forwarded by the PM (if any) and proceeds to execute
the implementation work. The Dev's turn-loop is driven entirely by the operator
(the granite PTY container relay); the Dev does not quiesce on `WAITING:`.

### `WAITING:` sentinel

`WAITING:` is a **plain final line** in the PM's turn output:

```
WAITING: Dev is executing {task}; will resume on Dev report. No further PM turn needed until the operator relays the Dev's report.
```

It is consumed exclusively by the `/goal` evaluator as a quiescence signal. It
is **NOT** a routing prefix and is NOT parsed by the granite classifier regex
(`^\[/(dev|user|complete)(?::([a-z0-9_-]+))?\]\s*$`). Avoid `[WAITING]`
or any bracket form — the evaluator only recognizes the bare `WAITING:` prefix.

### Turn-loop ownership

The **operator** (the granite PTY container) is the sole cross-role driver:

- It routes PM output to the Dev when the PM emits `[/dev]`.
- It relays Dev output back to the PM as a steering message.
- It delivers PM `[/user]` output to Telegram.
- It terminates the loop when the PM emits `[/complete]`.

Neither the PM nor the Dev drives each other's turn directly. The `/goal`
condition quiesces on `[/complete]` OR on `WAITING:` at the PM turn boundary
— the operator re-drives after the Dev's relay arrives.

## See also

- [Granite Operator: Interactive TUI](granite-interactive-tui.md) — the
  session-runner container this path builds on.
- [PTY Driver](pty-driver.md) — the substrate driver (submit key, idle signal,
  resume-UUID capture). `PTYDriver` is the claude builder's substrate; the Pi
  builder bypasses it entirely.
- [Pluggable Builder Harness](pluggable-builder-harness.md) — `BuilderHarness`
  seam design, `PtyClaudeBuilder` vs `PiSubprocessBuilder`, `[/dev:<harness>]`
  selector rubric.
- [deployment.md](deployment.md#granite-pty-pool) — env var and the
  `MAX_CONCURRENT_SESSIONS` relationship.
- [bridge-worker-architecture.md](bridge-worker-architecture.md) — where
  `_execute_agent_session` sits in the worker.
- [Omnigent `claude_native_*` Reference Map](omnigent-hook-edge-reference.md) — production-proven
  reference implementation for hook-driven turn completion (Stop/StopFailure as authority,
  PTY reduced to liveness badge, 9 cited practices); feeds future issues #1688/#1719/#1721.
