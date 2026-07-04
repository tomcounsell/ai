# Out-of-Domain Recovery + Per-Tool Budget Backstop

Issue #1821 lands two deferred backstops from the liveness-wedge landing
(#1815) and the slot-lease landing (#1820). Both close the same structural
gap from a different angle: **the actors that recover a wedged worker either
run on the very loop they police, or fire only from a background monitor that
a frozen loop also stops running.**

- **Fix #5 — out-of-domain recovery.** A bridge-process actor reads the
  worker's Redis-published loop beacon and lease snapshot, records
  `loop_wedged` when the loop is stale, and drives restart-free slot
  reclamation via a Redis-mediated request — all without ever touching the
  worker's in-memory state or sending it a kill signal.
- **Fix #6 — synchronous per-tool budget.** A pure ALLOW/DENY check runs
  inline in the PreToolUse dispatch path on both hook surfaces, capping a
  session's tool-call count (and, on the SDK/headless path, its cost) even
  when every background health loop is frozen.

This doc is the continuation of [Worker Liveness Recovery](worker-liveness-recovery.md)
(#1815, the dead-man's-switch and bounded PTY waits) and
[Slot-Lease Ownership](slot-lease-ownership.md) (#1820, the `SlotLeaseRegistry`
and its on-loop reap pass).

## Fix #5 — the Redis-mediated cross-process contract

The bridge process cannot touch the worker's in-memory `SlotLeaseRegistry` —
its wrapped `asyncio.Semaphore` is loop-affine, so a lock or release from
another process is undefined. The bridge also cannot read
`last_loop_tick` — it is a `time.monotonic()` value, meaningful only within
the process that produced it; a monotonic clock read across processes is two
unrelated numbers, not a comparable timestamp.

Every cross-process signal therefore goes through Redis, published by the
worker and read by the bridge. All keys are per-host and TTL'd, so a dead
worker's records expire and the bridge correctly sees "no beacon" rather than
a phantom-live registry.

**Worker publishes:**

| Key | Written by | Contents |
|-----|-----------|----------|
| `worker:loop_beacon:{host}` | `_publish_loop_beacon()`, called from `_write_worker_heartbeat()` on the existing off-loop heartbeat cadence | `{wall_ts, loop_beacon_age_s, armed}` — `wall_ts` is `time.time()`, the only field the bridge keys freshness on |
| `worker:slot:leases:{host}` | `_publish_slot_leases()`, called from the #1820 on-loop reap pass `_reap_slot_leases()` | `{permits_free, held, max, ts, owners: [{owner_session_id, acquired_at_wall_ts}]}` |

**Bridge reads and requests:**

| Key | Written by | Read by |
|-----|-----------|---------|
| `worker:slot:reclaim_requests:{host}` | `check_worker_liveness_and_slots()` in `monitoring/session_watchdog.py` | Drained by `_drain_reclaim_requests()` in the worker's on-loop reap pass |

The wall-clock beacon is the fix for the monotonic-clock problem: the
off-loop heartbeat thread already computes a monotonic loop-age each tick, so
`_publish_loop_beacon()` translates that into `time.time()` before writing to
Redis. `loop_beacon_age_s` is carried alongside as an advisory field only —
the bridge never uses it for cross-process time math, only `wall_ts`.
`armed=False` (the loop has not ticked yet) is never treated as wedged.

## The four-actor recovery ownership boundary

Four actors now participate in worker recovery, each owning a disjoint slice:

| Actor | Runs in | Recovers | Mechanism |
|-------|---------|----------|-----------|
| On-loop reap pass (`_reap_slot_leases`, #1820) | Worker loop | Leaked slots, when the loop is alive | Autonomous `registry.reclaim()` on terminal owners, every health tick |
| Dead-man's-switch (`_heartbeat_thread_main`, #1815) | Worker process, off-loop thread | The whole worker process, when the loop is frozen | `_self_kill()` (SIGKILL) on a stale beacon, so launchd respawns |
| `monitoring/worker_watchdog.py` | Separate launchd process | The whole worker process, when it is dead or its disk heartbeat is stale | SIGTERM → SIGKILL → bootout escalation ladder |
| Bridge `session_watchdog` (`check_worker_liveness_and_slots`, #1821) | Bridge process | Leaked slots, from a process other than the one that leaked them | Reads the beacon + lease snapshot; pushes a reclaim-request; never kills anything |

**The no-second-kill rule.** `check_worker_liveness_and_slots` detects and
reclaims — it never sends a process signal, never invokes `launchctl`, and
never writes the critical worker-recovery key the existing kill ladder uses.
When the beacon is missing or stale, it records a `loop_wedged` action and
increments a counter, then returns with no destructive action. Process
recovery stays entirely with the dead-man's-switch and `worker_watchdog.py` —
the bridge only adds detection and a restart-free reclaim trigger on top of
what already exists.

**Being transparent about what actually crosses the process boundary.**
`registry.reclaim()` itself always runs on the worker loop — that is a
consequence of loop affinity, not a choice the bridge can work around. What
crosses the process boundary is the *trigger*: the bridge decides a reclaim
is warranted and writes that decision to Redis; the worker's own reap pass
picks it up and performs the reclaim on its own loop, at its own next tick.
This is enough to satisfy the acceptance requirement that recovery is *driven*
from a process other than the worker loop, without overclaiming that a frozen
loop can somehow be made to release its own semaphore from outside.

## The reclaim-request drain

The drain lives inside `_reap_slot_leases()`, in the **always-run region**
that executes on every tick — placed after Phase 1 detection ends and before
the Phase-2 `if reap_disabled: return` early exit. This placement is
deliberate: it makes the drain the **sole reclaim lever** when
`SLOT_LEASE_REAP_DISABLED=1` gates off the autonomous Phase-2 reclaim. A drain
placed inside or after the Phase-2 loop would be skipped exactly when that
flag is set, defeating the point of having an out-of-domain path at all.

For each drained owner, the worker re-reads its status **fresh** — never
trusting the snapshot the bridge read, since time has passed since the bridge
pushed the request — and reclaims only when that fresh status is an explicit
terminal value. A `get_by_id` lookup that returns `None`, or that raises, is
treated as **unknown → skip**, not as terminal (issue #1868). This is a
deliberate divergence from the autonomous Phase-2 reclaim, which does treat a
`None` read as terminal: the autonomous path only ever sees the state it
snapshotted itself, moments earlier, so a `None` there is very likely a
genuinely deleted terminal session. The bridge-driven drain, by contrast, acts
on a request that could be stale by a full tick or more, so a `None` read is
just as likely to be a transient Redis blip on an otherwise live session.
Treating it as terminal there would risk stripping a live session's permit —
a semaphore over-admission bug worse than the leak the drain exists to fix.
Both the leases-snapshot publish and the drain are fail-quiet: neither can
raise into the worker's health loop.

## Synchronous per-tool budget (Fix #6)

`agent/tool_budget.py` is a pure, synchronous ALLOW/DENY evaluator —
`evaluate_tool_budget(session) -> BudgetVerdict` — modeled on omnigent's
`enforcement.py`: explicitly not a background monitor, called inline at the
point each tool call is dispatched. It is wired into **both** PreToolUse hook
surfaces so the ceiling holds regardless of which harness a session runs
under:

- `agent/hooks/pre_tool_use.py::pre_tool_use_hook` — the SDK/headless path,
  returns `{"decision": "block", "reason": ...}` on a deny.
- `.claude/hooks/pre_tool_use.py::main` — the interactive `claude` TUI /
  granite-PTY path, the load-bearing production surface, exits with code `2`
  on a deny.

A deny fires when `session.tool_call_count >= MAX_TOOL_CALLS_PER_SESSION`
(default `1000`) or `session.total_cost_usd >= SESSION_COST_CAP_USD` (default
`50.0`). `evaluate_tool_budget` decides only; the calling hook actuates the
inline block and, separately, the deny-surfacing side effects.

**`SESSION_COST_CAP_USD` is currently a no-op on granite sessions.**
`total_cost_usd` is written solely by `agent/sdk_client.py` — the SDK
`ResultMessage.total_cost_usd` path and the headless `claude -p stream-json`
`result`-event path. Nothing under `agent/granite_container/` populates it;
the interactive TUI transcript carries no cost line. So on the load-bearing
granite-PTY path, `total_cost_usd` stays `0.0` forever and the cost branch can
never fire — the operative backstop there is the tool-call cap alone. The
cost check is retained because it is live and correct on the SDK/headless
path, and is documented inline as SDK-path-only so its granite no-op status
is never mistaken for a working ceiling.

### The no-session-vs-infra-error fail-open split

The budget's fail-open posture must never brick the agent, but an
unconditional fail-open would let the backstop go silently blind during
exactly the partially-wedged Redis conditions it exists to guard against. So
resolution failures are split into two distinct paths, each in its own code
branch:

- **Genuine no-session** (`AGENT_SESSION_ID` unset, no sidecar, or
  `get_by_id` returns `None` with no exception) — this is the normal path for
  local CLI / non-agent sessions. Allows **silently**, no log noise.
- **Infra / resolution error** (Redis raised, `get_by_id` threw, sidecar JSON
  failed to decode) — the backstop is going blind on this call. Allows (still
  fail-open — a resolution failure must never brick a tool call) but logs
  **loudly at WARNING** with an explicit "backstop is BLIND this call"
  message, and increments `{project_key}:tool-budget:resolution_errors`. A
  rising counter is the operator signal that the budget cannot see sessions —
  not a silent no-op.

### The CLI exit-2 propagation semantics

`.claude/hooks/pre_tool_use.py::main()` is wrapped by a module-level
`except Exception` at the bottom of the file. Two facts make this the correct
wrapper granularity for the budget check to rely on:

1. A deny raises `sys.exit(2)`, which produces a `SystemExit` — not a
   subclass of `Exception` — so the module-level `except Exception` does not
   catch it. The exit code 2 propagates and the tool is denied.
2. A bug inside the budget check itself raises a normal `Exception`, which the
   wrapper does catch, logging via `log_hook_error` before the process exits
   0 — the tool is allowed, and the backstop fails open exactly the way a
   backstop should when it breaks.

The budget check therefore lives inside `main()`, and the deny path always
uses `sys.exit(2)`, never a caught-and-swallowed `return`. Wrapping the deny
in its own bare `except:` or `except BaseException:` would catch the
`SystemExit` too and invert this — a real deny would silently become an
allow — so the check is deliberately left unwrapped at that layer.

### `TOOL_BUDGET_ENABLED` vs `TOOL_BUDGET_AUTO_PAUSE`

Two independent switches gate two independently-sized decisions:

| Switch | Default | Gates |
|--------|---------|-------|
| `TOOL_BUDGET_ENABLED` | on | The evaluator running at all, and the inline deny (block / exit 2) actually firing |
| `TOOL_BUDGET_AUTO_PAUSE` | off | The *additional* disruptive extras a deny performs — the `status → paused_budget` transition and the Telegram ping |

With `TOOL_BUDGET_ENABLED` on and `TOOL_BUDGET_AUTO_PAUSE` off (the shipped
default), an over-budget session is denied inline, counted, logged, and
flagged — but its `status` is left untouched. `TOOL_BUDGET_ENABLED=false` is
the instant kill-switch if the cap ever misfires in production; with it off,
every call is allowed and the evaluator does no work.

**Why `paused_budget` is a new, non-drip status rather than bare `paused`.**
`reflections/agents/session_recovery_drip.py` re-queues sessions whose status
is exactly `paused` or `paused_circuit` back to `pending`, once per tick.
`tool_call_count` and `total_cost_usd` are cumulative and never reset, so a
session set to bare `paused` on a budget trip would be dripped back to
`pending`, immediately re-evaluate the same over-budget verdict, and land back
in `paused` — a `pending → denied → paused → pending` runaway, worse than the
condition the budget exists to stop. `paused_budget` is added to
`models/session_lifecycle.py` as a `NON_TERMINAL_STATUSES` member with
`RECOVERY_OWNERSHIP["paused_budget"] = "human"`, so the drip loop's status
filter never matches it and no such loop can form.

**Race-free deny-surfacing.** On every deny, the hook sets two hook-owned
`AgentSession` fields — `budget_tripped` (bool) and `budget_tripped_reason`
(str) — via a narrow `save(update_fields=[...])`. The hook never writes
`status` directly: on the granite path, `bridge_adapter` writes `status`
through its own partitioned `update_fields` saves, and a concurrent
hook-driven status write from another process or thread would race it,
last-writer-wins. `budget_tripped` / `budget_tripped_reason` are fields no
other writer touches, so they stay race-free and are the authoritative,
always-current human-legible signal — the dashboard, `valor-session status`,
and the adapter/worker all read them directly. Only when
`TOOL_BUDGET_AUTO_PAUSE` is set does the first deny per session additionally
call `transition_status(session, "paused_budget", ...)` (the CAS-protected
status-owner path) and queue a Telegram reaction on the originating message,
mirroring `monitoring/session_watchdog.py::_apply_stall_reaction`'s dedup
pattern. All of this surfacing is fail-quiet — a surfacing error never flips
a deny into an allow, and never crashes the hook; only the notification is
best-effort.

**The granite shared-counter caveat.** On the granite path,
`tool_call_count` sums PM and Dev sub-agent tool calls onto the same session
counter, so the effective per-role ceiling is roughly half
`MAX_TOOL_CALLS_PER_SESSION`, and a trip can deny both roles mid-build. This
is bounded by the conservative default (1000) and the
`TOOL_BUDGET_ENABLED=false` kill-switch. It is a tuning consideration —
granite may eventually want a higher `MAX_TOOL_CALLS_PER_SESSION` — not a
reason to gate the deny off by default, which would leave the backstop inert
exactly where it matters most.

**Id-less deny surfacing (#1873 item 3).** The deny-surfacing dedup gate keys
on `session_id or agent_session_id`. When a session resolves neither id, the
gate would otherwise form a single shared `{project_key}:tool-budget:tripped_applied:None`
slot that collapses every id-less deny together — the first surfaces and every
later one is silently deduped away. `record_budget_trip` therefore bypasses the
`SET NX` gate entirely when no id resolves and surfaces on every id-less deny
(observable via the WARNING log and the `{project_key}:tool-budget:tripped`
counter; the `budget_tripped` flag cannot persist for a keyless, unsaved
session). Id-less sessions do not arise on the shipped hook paths (both pass a
persisted `AgentSession`), so this is defensive hardening — the log/counter
volume stays bounded in practice.

### Deny-but-don't-halt: the metering tradeoff

With `TOOL_BUDGET_AUTO_PAUSE` off (the shipped default), a denied headless/SDK
session is blocked on each over-budget tool call but is **not halted** — the
session keeps issuing tool calls, each denied inline, metering one harness
round-trip per denied call until it reaches max-turns on its own. The deny is a
per-call backstop, not a session terminator.

Whether those wasted round-trips justify a behavior change — flipping the
auto-pause default on, or adding a consecutive-denial hard-stop — cannot be
answered without live denial-distribution data. The current `tripped` counter
increments once per session (gated by the dedup above), not once per denied
call, so it does not measure the per-call metering cost. Making that decision
needs a per-denial instrument.

That observe-first tuning work is owned by **[#1886]** — both the per-denial
`denied_calls` counter (the instrument that would collect the distribution) and
the eventual data-gated default decision. It is deliberately deferred here
pending production data; this feature ships the deny backstop and its
documentation only, with no speculative default change. When #1886 adds the
`denied_calls` counter, its `INCR` must carry its own isolated inner
`try/except` (mirroring the `tripped` counter) so a Redis blip on that one
increment cannot swallow the dedup-key write, the WARNING log, the
`budget_tripped` flag, or the auto-pause.

## Mixed-version-deploy detectability

Fix #5 spans both the bridge and worker processes, so a rolling deploy can
briefly run one new and one old. Both directions degrade safely, and both are
made operator-visible rather than silently dropped:

- **Old worker / new bridge.** The old worker publishes no beacon and no
  lease snapshot. The new bridge sees "no beacon," records `loop_wedged`, and
  defers — no destructive action. Detectable via the rising
  `loop_wedged_detected` counter on the dashboard.
- **New worker / old bridge.** The new worker publishes its beacon and lease
  snapshot and drains `worker:slot:reclaim_requests` every tick, but the old
  bridge never pushes requests into that list — so the drain is a harmless
  no-op. This direction would otherwise be silent, so the worker keeps one
  additional Redis timestamp, `worker:slot:last_reclaim_request_drain:{host}`,
  set whenever the drain pops at least one request. When a terminal-owner
  leak is observed on a tick where `now − last_drain_ts` exceeds
  `BRIDGE_WORKER_BEACON_STALE_S` (the existing beacon-freshness threshold,
  reused rather than adding a dedicated staleness variable), the worker emits
  `bridge_contract_stale` once (deduped via `SET NX EX`) into the
  `worker:watchdog:actions` log and a matching counter. The autonomous #1820
  reaper still reclaims the leak in this direction (unless
  `SLOT_LEASE_REAP_DISABLED=1`), so no slot is actually lost — the signal
  exists purely to make the contract gap visible rather than a quiet drop.

**Stale-check decoupled from the drain (#1873 item 2).** The read-only
`bridge_contract_stale` check no longer runs its own per-owner `get_by_id` loop
over the lease snapshot. `_drain_reclaim_requests` now returns `drained: int`
and no longer calls the stale-check; `_reap_slot_leases` builds an owner→record
map once — only when `drained == 0`, the sole case the stale-check inspects
owners — and calls `_maybe_emit_bridge_contract_stale(drained, owner_records)`
directly, in the always-run region before the Phase-2 reap gate. A per-owner
lookup error stores an `_ABSENT` sentinel (distinct from a positively not-found
`None`) and logs a WARNING, so the transient-DB-error signal survives. The
autonomous **Phase-2 reclaim loop is left unchanged**: it re-reads each owner
FRESH via `get_by_id` at reclaim time and never consults the map. This fresh
read is load-bearing — a `valor-session resume` during the bounded drain window
can un-terminal an owner that was terminal moments earlier, and reclaiming off a
pre-drain snapshot would strip the now-live session's permit (semaphore
over-admission). Total Redis reads are unchanged; this is a structural
decoupling of the stale-check, not a read reduction. The deliberate #1868
divergence is preserved exactly: the read-only stale-check treats `None`/`_ABSENT`
as unknown → skip, while the autonomous reaper's fresh read treats a not-found
`None` as terminal → reclaim.

The healthy-tick reclaim-dedup clear (`_clear_reclaim_dedup`) enumerates its
per-owner markers with a non-blocking `scan_iter` and deletes them in bounded
batches, replacing the blocking `KEYS` scan that held the Redis event loop at
scale. It stays fail-quiet; orphaned markers also age out via their TTL.

The contract is intentionally not hard version-gated. An explicit version
field would add a migration surface for no safety gain, since both directions
already degrade safely; the requirement here is detectability, not blocking.

## The `reclaim_requests` list cap (Race 4)

The per-owner `SET NX` dedup on `worker:slot:reclaim_requests:{host}`
prevents duplicate entries for the *same* owner, but does not bound the list
across *distinct* owners. A burst of many simultaneous leaks, arriving while
the worker tick is slow-but-not-wedged (so the beacon stays fresh and the
bridge keeps pushing), could otherwise grow the list unboundedly. Every push
is followed by `LTRIM worker:slot:reclaim_requests:{host} 0
RECLAIM_REQUESTS_MAX-1` (default 256, mirroring the existing
`worker:watchdog:actions` cap), and the key carries a TTL so a dead worker's
backlog expires on its own. Because the worker drain always re-reads owner
status fresh and `registry.reclaim()` is idempotent, an owner dropped by the
trim is harmless — if still terminal, it is simply re-requested on a later
tick.

## Environment kill-switches

All defaults are conservative-provisional, tunable after observing real
production rates, and read via raw `os.environ.get()` at module scope —
matching the sibling `WORKER_HEARTBEAT_INTERVAL` / #1815 threshold pattern,
so none of these live in `config/settings.py`.

| Variable | Default | Gates |
|----------|---------|-------|
| `BRIDGE_SLOT_RECLAIM_ENABLED` | on | The bridge reclaim-request trigger; off leaves detection/logging running with no push |
| `BRIDGE_WORKER_BEACON_STALE_S` | `90` | Beacon-staleness threshold (loop_wedged detection) and, reused, the `bridge_contract_stale` window |
| `RECLAIM_REQUESTS_MAX` | `256` | List-length cap on `worker:slot:reclaim_requests` (Race 4) |
| `TOOL_BUDGET_ENABLED` | on | The budget evaluator and the inline deny; off always allows |
| `TOOL_BUDGET_AUTO_PAUSE` | off | The `status → paused_budget` transition and Telegram surfacing on a deny |
| `MAX_TOOL_CALLS_PER_SESSION` | `1000` | Tool-call dimension of the budget |
| `SESSION_COST_CAP_USD` | `50.0` | Cost dimension of the budget (SDK/headless-path-only — no-op on granite) |

## Dashboard operator surface

`_get_worker_health()` in `ui/app.py`, surfaced under the `worker` block of
`localhost:8500/dashboard.json`, gained additive fields for both fixes:

- `permits_free` / `held` — read from `worker:slot:leases`.
- `bridge_reclaims` — count of reclaims driven through the bridge-pushed
  request path (as opposed to the autonomous on-loop reaper).
- `loop_wedged_detected` — count of stale/missing-beacon detections.
- `bridge_contract_stale` — count of mixed-version-deploy detections
  (new worker draining an empty request list past the staleness window).
- `tool_budget_tripped` / `tool_budget_resolution_errors` — budget counters.
- The last few `worker:watchdog:actions` entries, for a quick operator read
  of what recovery actually did.

A rising `bridge_reclaims` signals a recurring leak the on-loop reaper isn't
catching on its own. A rising `tool_budget_tripped` signals sessions hitting
the cap. A rising `bridge_contract_stale` signals a mixed-version deploy in
progress. A rising `tool_budget_resolution_errors` signals the budget
backstop is going blind on session resolution.

## See Also

- [Worker Liveness Recovery](worker-liveness-recovery.md) — the dead-man's-switch
  beacon and bounded PTY waits this feature's beacon publish extends (#1815)
- [Slot-Lease Ownership](slot-lease-ownership.md) — the `SlotLeaseRegistry`,
  on-loop reap pass, and `registry.reclaim()` this feature's drain extends (#1820)
- [Bridge Self-Healing](bridge-self-healing.md) — the worker watchdog kill
  ladder this feature explicitly defers all process recovery to
- `agent/tool_budget.py` — the budget evaluator and deny-surfacing helpers
- `agent/session_health.py` — beacon publish, lease-snapshot publish, and the
  reclaim-request drain
- `monitoring/session_watchdog.py` — `check_worker_liveness_and_slots`
