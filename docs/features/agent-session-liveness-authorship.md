# AgentSession Liveness Field Authorship

The durable statement of who is authorized to write which liveness field on `AgentSession`, and the `/update` reaper's liveness ladder that decides whether an unresponsive row is still alive. Plan: [`docs/plans/agent-session-updated-at-restamp.md`](../plans/agent-session-updated-at-restamp.md).

## Why this doc exists

A liveness field is only trustworthy if its writers are limited to things that actually happened. Three maintenance writers touch `updated_at` — a corruption probe, an archive restore, and an SDLC run-lock bind — and the table below is the durable statement of which of them may move it. This doc is where a future change checks whether it is about to reintroduce a writer that restamps the timestamp without anything real to report.

## Field authorship

| Field | Authorized writers | Never written by |
|---|---|---|
| `updated_at` | Any writer with something real to report: execution-path saves (`agent/session_health.py`, `agent/session_executor.py`, `agent/health_check.py`'s PostToolUse write, `models/session_lifecycle.py` transitions, `tools/stage_states_helpers.py`), and a full save whose caller genuinely changed the row | A maintenance sweep with nothing to report (the corruption probe); a bind that only re-asserts identity/ownership without changing anything else (the SDLC run-lock bind) |
| `last_heartbeat_at` | The executor's own heartbeat loop only — T+0 write and the 60s tick (`agent/session_executor.py`), both via `save(update_fields=["last_heartbeat_at"])` | Everything else, including every maintenance path |
| `last_turn_at` / `last_tool_use_at` | Turn and tool boundaries only (SDK `result` event, PreToolUse/PostToolUse hooks) | Maintenance sweeps, binds, restores |
| `(exec_pid, pid_create_time)` fence | Spawn only — `stamp_execution_spawn`, called by the runner's `_on_turn_spawn` before the turn-await blocks | Anything after spawn; the fence is not nulled between turns, it is only compared for staleness (see [Agent Session Fenced Execution Record](agent-session-fenced-execution-record.md)) |

Two writers hold this table true, and the distinction between them matters:

- **A corruption probe that writes no field value.** `cleanup_corrupted_agent_sessions()` classifies with `session.is_valid()` instead of calling a bare `session.save()` on every hydrated row as a validation probe — a side effect entirely unrelated to its purpose. `is_valid()` writes nothing to Redis, but it is not read-only with respect to the in-memory instance: its coercion branch calls `setattr()` on the instance during type checking (`popoto/models/base.py:829-839`). That mutation is harmless here because the hydrated instance is discarded at the end of the loop iteration — but "read-only" is the wrong word for it, and no future comment on this code path should use it.
- **Archive restore declares it owns the timestamp it carries.** `_rehydrate_row()` passes `AgentSession(id=archived_id, **fields).save(preserve_updated_at=isinstance(fields.get("updated_at"), datetime))`. The guard is on the value's *type*, never on key presence — `_serialize_session` always writes an `updated_at` key, but its value can be `None` (never stamped before archiving) or an unparsed ISO string (`_deserialize_payload` leaves a string in place on a `ValueError` rather than raising). `AgentSession.__setattr__` normalizes both non-`datetime` shapes to `None` before `save()` ever runs, so an unconditional `preserve_updated_at=True` would silently restore a row with no liveness stamp at all. Only a genuine `datetime` is preserved; everything else falls through to the normal stamp.

### Stage-state advance vs. run-lock bind

These are two different `updated_at` writers on the same SDLC pipeline row, and only one of them moves the timestamp:

- **A stage-state advance** — a stage that actually did something writes its own `stage_states` update through a full save, and that save genuinely reports "this row moved." This is the writer `tools/sdlc_session_ensure.py:73-79` documents as the worker-less-pipeline liveness signal ("no heartbeat AND no `stage_states` write refreshing `updated_at`").
- **The run-lock bind** (`_acquire_run_lock_and_bind()`) — this runs before *every* `ensure_session()` return point, including a dispatch that merely re-reads state and returns without advancing anything. It writes exactly the two fields it changed, `active_run_id` and `owned_run_ids`, through the `update_fields` partial-save carve-out: `session.save(update_fields=["active_run_id", "owned_run_ids"])`. Both fields are listed in `AgentSession._UPDATED_AT_OMISSION_OK_FIELDS` under an "SDLC run-identity bookkeeping" comment group, so the omission logs at DEBUG rather than a per-dispatch WARNING.

A pipeline that is actually advancing still refreshes `updated_at` via its stage-state write. A pipeline that only re-dispatches the same stage without advancing does not — that is the writer that keeps ledger anchors permanently fresh, and narrowing it is what makes the reaper's `is_ledger` skip (below) actually necessary rather than cosmetic.

`preserve_updated_at` (a `save()` parameter) and `update_fields` (the partial-save carve-out) are not interchangeable, even though both suppress the stamp. `update_fields` is for a genuinely partial write — some fields changed, most did not. `preserve_updated_at` is for the one case `update_fields` cannot serve: a full save that must write every field (an INSERT-shaped restore) while the caller owns the timestamp already on the instance. Using `preserve_updated_at` for a two-field bind would persist a whole row and churn every index for no reason; using `update_fields` for a restore would drop every field not explicitly listed.

## The `/update` reaper's liveness ladder

`scripts/update/run.py::_cleanup_stale_sessions` evaluates `running` sessions in this order. Every rung after the first is **additive** — it can only produce a skip, never a finalization:

1. **Live worker in `_active_workers`** → skip, terminal. In-process invocations only; the registry is empty when `/update` runs as a standalone subprocess.
2. **`is_ledger`** → skip, terminal, counted as `skipped_ledger`. Checked before the fence so no later rung can ever reach a ledger row.
3. **The `(exec_pid, pid_create_time)` fence.** Fence live → skip, terminal. **Fence dead → does *not* finalize.** It sets a reason-string selector (`fence_verified_dead = True`) and **falls through** to the rungs below. This fall-through is deliberate: a dead fence is strong evidence, but making it terminal would let a session be finalized seconds after spawn, bypassing the `created_at` floor at rung 6. Two green tests pin the invariant — `test_fence_dead_but_recently_updated_is_still_spared` and `test_fence_dead_but_young_by_created_at_is_still_spared` — under the statement **"the fence ADDS protection; it never subtracts it."**
4. **`last_heartbeat_at`** within `RECENT_ACTIVITY_WINDOW` (30 min) → skip, terminal, counted as `skipped_heartbeat`. Written only by the executor's own heartbeat loop, never by any maintenance path, so it cannot be forged by a sweep. Naive datetimes and raw floats are normalized by `to_unix_ts` and read as real heartbeats — popoto strips tzinfo, so a hydrated value is always naive. Only a missing or unparseable value falls through to the next rung.
5. **`updated_at`** within `RECENT_ACTIVITY_WINDOW` → skip, counted as `skipped_recent`.
6. **`created_at`** age ≥ `age_minutes` (120 min) → finalize.

### Which row shape each rung actually protects

- **Rung 2 (`is_ledger`)** protects the `sdlc-local-*` CLI anchor rows — non-terminal for their whole life, no subprocess behind them by construction. `sdlc-local-*` pipeline anchors are created through the sole production `AgentSession.create_local()` call, which sets `is_ledger=True` unconditionally, so every one of them is spared **here**, before the fence or heartbeat rungs are ever consulted.
- **Rung 4 (`last_heartbeat_at`)** protects a session executing for longer than `RECENT_ACTIVITY_WINDOW` whose fence is absent or half-recorded (legacy row, or `pid_create_time` never captured). Its tier-1 heartbeat writes go through `update_fields=["last_heartbeat_at"]`, which by design does not move `updated_at`, so a long-running turn with no full save in 30 minutes has a stale `updated_at` and a heartbeat no more than 60 seconds old. Rung 4 provides a 1740-second margin — the 30-minute window less the 60-second heartbeat cadence — and it is the rung that makes this population's protection load-bearing rather than opportunistic.
- **Rung 5 (`updated_at`)** protects **non-ledger** rows only — `sdlc-local-*` anchors never reach it, because rung 2 already spared them. Its live consumers are:
  - **The local Claude Code CLI session.** `.claude/hooks/user_prompt_submit.py:338` calls `create_local(..., status="running")` and sets no `is_ledger` flag, so the row is non-ledger, worker-less (no `last_heartbeat_at`), and unfenced. It is kept fresh by the PostToolUse `watchdog_hook` write in `agent/health_check.py`, which does a full `save()` on every tool call. Rung 5 is the only rung standing between a live local session and the `created_at` floor at rung 6 — dropping it would reap live local sessions.
  - **The enqueue window before the T+0 heartbeat.** Between a session's enqueue-time write and its `last_heartbeat_at` T+0 write (`agent/session_executor.py`), `updated_at` is the only fresh signal the row has.
- **Rung 6 (`created_at`)** is the last resort for sessions with no `updated_at` at all — rows with neither a heartbeat nor an `updated_at` stamp.

### The split of reaping authority

This is a **process-liveness** reaper: every rung above rung 2 asks some form of "is the process we spawned still running?" A ledger anchor has no process by construction, so this reaper is structurally the wrong owner for it — that is exactly why rung 2 skips it rather than trying to answer a question that has no meaningful answer for that row shape.

The correct authority for a process-less ledger row is issue-lock ownership, which `tools/sdlc_session_ensure.py --kill-orphans` (`_iter_orphan_sessions`) implements: it resolves against the recorded issue lock owner, failing toward "live" on ambiguous evidence, with an idle-window fallback only when no lock payload resolves. Nothing schedules that CLI today — it is a manual, human-invoked tool. Turning it into an unattended fleet-wide `finalize_session()` actuator is deliberately out of scope here: it needs its own safety argument, a soak period, and a kill switch, the same bar every out-of-process actuator on session state has to clear (see [Agent Session Health Monitor § Single-owner actuation](agent-session-health-monitor.md#single-owner-actuation)). Narrowing the run-lock bind (above) also narrows the `updated_at` signal that CLI's no-payload fallback depends on, so its idle-window threshold has to be re-derived before that actuator is scheduled.

Landing the `is_ledger` skip introduces no regression: the skip substitutes an honest "not my authority" signal for a forged "still fresh" one and holds the net outcome constant.

## `Meta.ttl`: the keepalive is the retention policy

**Policy: `AgentSession` rows live until something deletes them explicitly.** `AgentSession.Meta.ttl` is set to 30 days (`settings.timeouts.agent_session_retain_ttl_s`) and acts only as a backstop for rows the corruption sweep cannot reach. On every healthy row it visits, the sweep calls `AgentSession.refresh_ttl()`, a targeted `EXPIRE` on the row's key (`POPOTO_REDIS_DB.expire(self.db_key.redis_key, self._ttl)`): key metadata only, no field write and no index touch. The call sits as a separate statement *after* classification, in its own `try/except` that never touches the corruption verdict (folding it into the classification branch would turn a transient Redis fault into a bulk delete of live rows).

The policy rests on one fact: expiry would be real deletion from the authoritative store. `restore_if_empty()` only rehydrates on a cold start against an empty keyspace, so a row that aged out on its own is gone from Redis with nothing noticing. The dashboard (`ui/data/sdlc.py`, `ui/data/jobs.py`), resume paths, and `parent_agent_session_id` lineage all read the live store. Deleting the `refresh_ttl()` call is therefore a data-loss change, and the sweep test `tests/unit/test_session_health_phantom_guard.py::TestSweepIsTtlAndUpdatedAtNeutral` goes red if it happens.

**Dropping `Meta.ttl` is the wrong way to express the same policy.** Redis retains the TTL already stamped on a key; clearing the model attribute only stops *future* stamps. A key at TTL 600 left to decay to 595 and then re-saved after `Meta.ttl` is flipped to `None` comes back at 595, still decaying. Every `AgentSession` hash in Redis carries a live 30-day clock from its last write, so removing the attribute would leave the existing population expiring on schedule while every new row never expires at all, and popoto exposes no ORM-level `persist`/`clear_ttl` to fix that up afterward. The keepalive gives both populations the same behaviour.

## See also

- [Agent Session Health Monitor](agent-session-health-monitor.md) — the worker's own in-process recovery loop; its "Single-owner actuation" section is why an out-of-process reaper on session state needs its own safety argument.
- [Agent Session Fenced Execution Record](agent-session-fenced-execution-record.md) — the `(exec_pid, pid_create_time)` fence consulted at rung 3, including the legacy-row rule for absent/unreadable `create_time`.
- [Agent Session Model](agent-session-model.md) — full `AgentSession` field catalog.
- `models/agent_session.py` — `save()`, `preserve_updated_at`, `refresh_ttl()`, `_UPDATED_AT_OMISSION_OK_FIELDS`.
- `scripts/update/run.py` — `_cleanup_stale_sessions`, the reaper's ladder implementation.
- `tools/sdlc_session_ensure.py` — `_acquire_run_lock_and_bind`, `_iter_orphan_sessions` (the issue-lock authority for ledger rows).
