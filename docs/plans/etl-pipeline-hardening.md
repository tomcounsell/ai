---
status: Ready
type: chore
appetite: Large
owner: Valor Engels
created: 2026-09-06
baseline_commit: bf0a5d577b6361bf44432d6a280f9829e8b9ba94
tracking: https://github.com/tomcounsell/ai/issues/3183
last_comment_id: 5560573967
---

# ETL-grade pipeline hardening

## Problem

The bridge → queue → worker → outbox → relay path is a pipeline, and it already carries most of the vocabulary a production ETL system uses: durable intake, at-least-once with dedup, retries, backpressure, replay scanners. Each of those is implemented locally and differently at every seam, so the system has four terminal sinks and one replayer, three fail-open locks with no counter, untyped Redis payloads, a claim that is a gate rather than a lease, and post-session side effects that vanish on failure. Nobody can answer "what was lost yesterday, and can it be replayed?" from one place.

This plan is the production-path complement to the recursive self-improvement plan (#3177, `docs/plans/recursive-self-improvement.md`). That plan builds a control journal with Lua compare-and-set transitions, epoch leases, durable dispatch intents with an idempotency key, and a `reconciliation_required` state, scoped to its research namespace and explicitly not touching worker pickup. This plan brings the same discipline to the path that plan depends on, and shares its seams rather than forking them. See §Relationship to #3177.

**Current behavior** (every citation re-read at `bf0a5d5`):

- `_schedule_post_session_extraction` (`agent/session_executor.py:339`) is a sync function that `create_task`s the extraction and swallows every exception at `:388`; `run_post_session_extraction` (`agent/memory_extraction.py:1663`) catches all to WARNING and clears state in `finally`. Shutdown drains for 5s (`worker/__main__.py:1081`). A failed extraction leaves no record and never retries. #1866 held the task references; it did not add durability.
- Outbound Telegram text dead-letters after `MAX_RELAY_RETRIES=3` into `DeadLetter` (`models/dead_letter.py`; the `_dead_letter_message` call at the retry cap is `bridge/telegram_relay.py:1498-1500`, inside the retry-cap / poll-re-enqueue region `:1490-1536`); `replay_dead_letters` runs from one site, the bridge connect sequence (`bridge/telegram_bridge.py:3073-3075`). Inside `process_outbox`, malformed outbox JSON is silently discarded at `:1337-1339` and an unknown `type` at `:1343-1347` — both `continue` after a WARNING, with the popped entry gone. (`:1533` is `requeue_raw = json.dumps(message)` in the re-queue branch, not a discard; the round-1 critique corrected an earlier misattribution here.) The session archive has its own `_restore_quarantine` table (`agent/session_archive.py:91`). Sessions finalized at `MAX_RECOVERY_ATTEMPTS=2` or on `init_hang` (`agent/agent_session_queue.py:2581-2607`) keep no replayable input. The corrupted-pop path reaps rows (`:2175-2272`).
- Outbox, steering (`agent/steering.py`), and `valor:sessions:new` payloads are plain dicts read with `.get()`; the only check is `KNOWN_MESSAGE_TYPES` membership. Popoto booleans round-trip as `"False"`, hence `_truthy()` at `agent/session_pickup.py:76`.
- Pop is a Python-sorted scan over `status="pending"` under `SET worker:pop_lock:{key} NX EX 5` (`session_pickup.py:117`) and `SET session:runclaim:{id} NX EX 30` (`models/session_lifecycle.py:834`). Once `running`, nothing redelivers on a timer; recovery is the 300s health sweep plus PID-fence inference plus the startup pass (`agent/session_health.py:1077`). `docs/plans/session-recovery-observation-audit.md` §P0 items 7-8 prescribed an execution-lifetime lease with a fencing generation in July; no issue tracks it.
- `_acquire_pop_lock` (`session_pickup.py:131-134`), `claim_pending_run` (`session_lifecycle.py:858-864`), and `claim_message` (`bridge/dedup.py:188-195`) return success on Redis error with a WARNING and no counter. Documented as deliberate in `bridge-worker-architecture.md` §Redis Pop Lock.
- `_enqueue_agent_reflection` mints `session_id = f"0_{ms}"` (`agent/reflection_scheduler.py:751-752`); a duplicate tick enqueues twice.
- `correlation_id` is on the row but absent from `_harness_env` (`session_executor.py:2194`) and from `build_telegram_outbox_payload` (`agent/output_handler.py`); the worker logs plain text (`worker/__main__.py:425-440`) while the bridge has `StructuredJsonFormatter` (`bridge/log_format.py`).

**Desired outcome:**

One work-item model for side effects, one dead-letter model with a scheduled replayer, versioned schemas on every Redis wire, a stated and counted fail-open policy per lock with the run claim failing closed, idempotent reflection enqueue through the #3177 seam, lineage that crosses the subprocess boundary, and an execution lease that redelivers a dead owner's session inside its TTL and rejects stale-generation writes.

## Relationship to #3177

| Concern | #3177 proposes (research namespace) | This plan proposes (production path) | How they compose |
|---|---|---|---|
| Idempotent create | Gap C: `idempotency_key` + preallocated `agent_session_id` on `_push_agent_session`, NX key in `improve:*` | Reflections pass `idempotency_key=(name, due_window)` through the **same** parameter | **Decided 2026-09-06: this plan's lane 5b lands the seam** (`idempotency_key`, `status` kwarg, `tuple[int, str]` return, fixing the `int`-return contract the #3177 critique flagged). #3177 Gap C consumes the shipped primitive; its lane 3 child issue should reference this plan's seam task instead of redesigning it. |
| Lease and fencing | Gap A: epoch lease in Lua with `TIME`, accept `epoch >= highest_accepted`, compare-and-delete release, for `ImprovementCase` heads | Lane 6: the same Lua idiom keyed by `agent_session_id`, generation checked in `transition_status` and output writes | Same primitive, two key families. Lane 6 extracts the lease script into `models/redis_lease.py` on the `utils.redis_client` accessor; #3177 lane 3 imports it instead of writing a second one. Because lane 6 is a child issue here, #3177 lane 3 blocks on that child, not on this build — task `file-children` files it as soon as lanes 1-5 validate. |
| Stranded work | `reconciliation_required` state + a reflection tick sweeping stale intents (critique BLOCKER fix) | Lane 2: one `DeadLetter` model with `stage`, one `dead-letter-replay` reflection | #3177's stranded intents become one more `stage` value. Its sweep reflection and this replayer are the same tick with two scanners. |
| Fail-open vs fail-closed | Controller pauses when its namespace is unreachable (fail-closed) | Lane 4: run claim fails closed with no break-glass; message claim and pop lock stay fail-open with counters | #3177 assumes one execution per action id; a fail-open run claim violates that under Redis degradation. Lane 4 is a prerequisite for #3177 lane 3, not a nicety. |
| Lineage | Observer adapters with durable cursors, dedup by source id; evidence never reads missing data as success | Lane 5a: `correlation_id` into the subprocess env and outbox payload, JSON worker logs | #3177's objective 1 measures end-to-end journey preservation; it needs a journey that is traceable intake to delivery. Lane 5a is the cheapest evidence source it does not yet have. |
| Steering correlation | Rabbit hole: steering is correlation-free by design | Not proposed | Agreed; steering is out of scope here. |
| Side-effect durability | Kill switch on extraction for trial arms (Gap E) | Lane 1: extraction as a durable `SideEffectJob` | Lane 1's job row is where Gap E's kill switch should live (a `skip` disposition), instead of an env check before `try_reserve_detach_slot()`. |

Nothing here changes #3177's scope. Every row above is either a shared primitive this plan builds first or a production guarantee that plan silently assumes.

## Freshness Check

**Baseline commit:** `bf0a5d577b6361bf44432d6a280f9829e8b9ba94`
**Issue filed at:** 2026-09-06 (same session as this plan)
**Disposition:** Unchanged

**File:line references re-verified:** all references in #3183 were produced against `bf0a5d5` minutes before filing by direct read; no commits have landed on `main` since.

**Cited sibling issues/PRs re-checked:**
- #3177 — open, `docs/plans/recursive-self-improvement.md` at status Planning with a NEEDS REVISION critique; its lane 3 (control substrate, `_push_agent_session` seam) is a child issue not yet filed.
- #1817 — closed; A1-A3 shipped, B1 (`claim_message`) and B2 (`claim_pending_run`) shipped as short NX gates, C via #1865, D via #1866.
- #1866 — closed; held fire-and-forget task references, no durability.
- #1312 / PR #2196 — closed; ⚠ reaction on no live worker, unaffected.

**Re-verified 2026-09-07 at `de229ee469fe7b2b176b371b6cd19646fce401a0` (PLAN stage):** no commit since `bf0a5d5` touches any of the sixteen files this plan cites (`git log bf0a5d5..HEAD -- <cited paths>` is empty), so every file:line reference above still resolves and the disposition stays Unchanged. One post-baseline change does move a mechanism this plan names: `utils/redis_client.py` landed on `main` at `b1a5067` (2026-09-06 16:06, after the 13:44 baseline) as the single sanctioned accessor for non-ORM Redis keys, with six production call sites. It exists because a hand-built `REDIS_URL` client resolved its own database at call time and landed on production db 0 (#3003), and because five call sites imported the module before it was on `main`, silently stalling `telegram:outbox:*` for ~26 hours. Every place this plan said "private client alias" now reads `utils.redis_client.text_redis()`: lane 4's `agent/lock_policy.py`, lane 5b's `agent/enqueue_idempotency.py`, and lane 6's `models/redis_lease.py`. Claims unchanged, mechanism corrected. Disposition: **Minor drift**.

**Active plans in `docs/plans/` overlapping this area:**
- `recursive-self-improvement.md` (#3177) — shares the `_push_agent_session` seam and the lease idiom; handled by §Relationship to #3177.
- `session-recovery-observation-audit.md` — audit, not a plan; its P0 lease prescription is executed by lane 6.
- `resilience-simplification-three-tier.md` — untracked draft; its T3 lease row and #1868 fail-open rule are superseded by lanes 6 and 4 here.
- `durability-room-job-agentrun.md` (#2494) — Room inbox cutover is still shadow-phase; no lane here depends on it and none blocks it.

## Prior Art

- **#1817 / `docs/features/delivery-integrity-hardening.md`**: the red-team sweep that named B1/B2 (atomic claims), C1 (finalize sweep), D3 (fire-and-forget). B1/B2 shipped as NX gates with fail-open; D3 held references. This plan is the durability layer those left out.
- **#2494 durability model**: Room inbox durable append before routing, `SET NX` message binding. The intake half of the pipeline is already ETL-shaped; this plan covers everything after intake.
- **`models/session_lifecycle.py:904-940` issue lock**: a real lease with Lua compare-and-delete release, compare-and-set renewal, and a heartbeat (`tools/sdlc_lease_heartbeat.py`). Lane 6 generalizes this idiom instead of inventing one.
- **`bridge/dead_letters.py` / PR that replaced the JSONL DLQ**: atomic Popoto dead letters for outbound text. Lane 2 generalizes the model rather than adding a sibling.
- **`agent/session_archive.py` `_restore_quarantine`**: the closest existing thing to a DLQ for state, and the one this plan deliberately does **not** absorb. It is SQLite-resident precisely so the skip list and the per-row `attempt_count` survive the event they defend against (a wiped or empty Redis), so lane 2 mirrors it into `DeadLetter(stage="archive_restore")` for observability and leaves the table authoritative. See lane 2's Writers list.
- **`agent/reflection_scheduler.py` retry policy**: reflections already carry `{max_retries, backoff_seconds, max_consecutive_failures_before_pause}`; lane 1's job drain reuses that shape.

## Research

**Queries used:**
- Redis Streams consumer group XAUTOCLAIM pending entries visibility timeout pattern job queue
- transactional outbox pattern dead letter queue replay design idempotency key at-least-once

**Key findings:**
- Streams give redelivery via the pending-entries list plus `XAUTOCLAIM` on an idle threshold, and Redis 8.4 folds reclaim into `XREADGROUP CLAIM`; production guidance is to reclaim on startup, cap by delivery count, and route exhausted entries to a dead-letter queue, and to alert on a growing pending count. Sources: [At-least-once with Redis Streams](https://oneuptime.com/blog/post/2026-03-31-redis-at-least-once-processing-streams/view), [XREADGROUP CLAIM in Redis 8.4](https://redis.io/blog/single-shot-reliable-consumers-with-xreadgroup-claim-in-redis-84/), [Streams consumer-group patterns](https://redis.antirez.com/fundamental/streams-consumer-patterns.html), [XPENDING](https://redis.io/docs/latest/commands/xpending/). Informs spike-1: streams are the right primitive when the queue order is the stream order. This queue's order is a Python filter (priority tier, `scheduled_at`, worker-key routing, sustainability throttles, real-Chrome slot), so a stream would need one stream per `(worker_key, priority)` and still a reaper. The lease is the smaller change.
- The outbox and the idempotency key are two halves of one design: the outbox guarantees the event is not lost; the key guarantees a twice-delivered event acts once. Exhausted retries go to a DLQ with the original payload, reason, and retry history, with a replay path; permanently bad payloads (schema mismatch) are capped and dead-lettered, never dropped. Sources: [Transactional outbox trade-offs](https://www.softwarecraftsperson.com/posts/2025-10-08-transactional-outbox-pattern/), [Outbox with retries and DLQ](https://dev.to/sagarmaheshwary/transactional-outbox-with-rabbitmq-part-2-handling-retries-dead-letter-queues-and-observability-4h19), [Idempotency, DLQ, outbox](https://medium.com/@melistogan6/idempotency-dlq-and-the-outbox-pattern-in-kafka-a-practical-guide-to-consistent-streams-b5e7620ea80d). Informs lanes 2 and 3: validation failure is a dead-letter event, not a drop; the DLQ row keeps the payload snapshot.

## Spike Results

### spike-1: lease versus stream for the session claim
- **Assumption**: "A Redis Stream consumer group can replace the pop lock, run claim, and health sweep."
- **Method**: code-read
- **Finding**: `_pop_agent_session` (`agent/session_pickup.py:247-521`) selects by four-tier priority then FIFO, skips future `scheduled_at`, ledger rows, throttled tiers, and real-Chrome-busy rows, and routes by `worker_key` with a lazy worker start. None of that is expressible as stream order; a stream would carry ids only and the row would still be read and filtered. Redelivery via `XAUTOCLAIM` still needs a caller on a cadence. The issue lock at `models/session_lifecycle.py:904-940` already implements the lease shape (Lua CAS renew, compare-and-delete release, TTL/3 heartbeat).
- **Confidence**: high
- **Impact on plan**: lane 6 is a lease, not a stream. The stream option is recorded in No-Gos with this reason.

### spike-2: where a dead-letter write belongs on the session path
- **Assumption**: "Every session-terminal path has a single chokepoint to add a dead-letter write."
- **Method**: code-read
- **Finding**: `finalize_session` (`models/session_lifecycle.py:233`) is reached from the Stop hook, `agent/session_health.py`, `worker/__main__.py`, and `tools/agent_session_scheduler.py`, and the #3177 critique already recommends it as the one place for reservation settlement. The corrupted-pop reaper (`agent_session_queue.py:2175-2272`) bypasses it because the row cannot be loaded. So two sites: `finalize_session` for `failed` with `recovery_attempts >= MAX` or `exit_reason == init_hang`, and the reaper for unloadable rows (payload is the raw hash).
- **Confidence**: high
- **Impact on plan**: lane 2 task names both sites; a Verification row greps for exactly those two writers on the session path.

### spike-3: reflection idempotency window
- **Assumption**: "`(name, due_window)` is a stable key for a reflection enqueue."
- **Method**: code-read
- **Finding**: `compute_next_due(schedule_str, last_run, *, now)` (`agent/reflection_schedule.py:155-160`) returns a **float epoch, not a datetime** (`tests/unit/test_reflection_schedule_grammar.py:37` pins `compute_next_due("every: 60s", last_run=1_000_000.0) == 1_000_060.0`). The scheduler ticks every 60s (`agent/reflection_scheduler.py:47`); the skip-if-running guard reads a status field, not a lease (#3177 Race 1). Two overlapping ticks that both read the same `Reflection.ran_at` compute the same value, so it is stable across the race window. Agent-type reflections enqueue via `_push_agent_session` at `:754`.
- **Confidence**: high
- **Impact on plan**: lane 5b key is `f"reflection:{name}:{int(due_epoch) // 60 * 60}"` — a float epoch floored to the tick period, never `.timestamp()`. Round-1 critique corrected an earlier `int(due_at.timestamp())` form that would have raised `AttributeError` on a float.

### spike-4: extraction job drain owner
- **Assumption**: "The reflection process can drain extraction jobs without importing worker state."
- **Method**: code-read
- **Finding**: `run_post_session_extraction` takes a session id and reads transcript state through the ORM and `logs/`; it has no dependency on `agent/session_state.py`. `reflections/redis_access.py` already gives the reflection process ORM access. `settings.timeouts` carries the drain cadence pattern.
- **Confidence**: high
- **Impact on plan**: lane 1 drains from a function reflection registered via `reflection_register.py`; the worker enqueues only.

## Data Flow

1. **Entry point**: a session finalizes (`finalize_session`), the relay fails a send, a payload fails validation, a reflection tick fires, or a running session's owner dies.
2. **Side-effect job** (lane 1): `finalize_session` writes a `SideEffectJob(kind="memory_extraction", session_id, attempts=0, next_attempt_at=now)` through the ORM; the `side-effect-drain` reflection pops due rows, runs the kind's handler, and on exception bumps `attempts` with backoff or, at the cap, writes a `DeadLetter(stage="extraction")` and deletes the job.
3. **Dead-letter** (lane 2): every terminal sink writes `DeadLetter(stage, payload_json, reason, attempts, first_seen, last_seen, replayable)`; the `dead-letter-replay` reflection retries `replayable` rows by stage handler and ages the rest; the dashboard tile reads counts by stage.
4. **Wire schema** (lane 3): writers construct `OutboxPayload | SteeringPayload | NotifyPayload` (pydantic, `v: int`) and serialize; readers parse; a `ValidationError` becomes a `DeadLetter(stage="outbox_parse" | "steering_parse")` and the raw bytes are the payload.
5. **Lock policy** (lane 4): **all three** Redis-error branches call `record_lock_degradation(name, policy)`, which `HINCRBY`s `{project}:locks:degraded` on field `"{name}:{policy}"` through `utils.redis_client.text_redis()`; `_acquire_pop_lock` and `claim_message` then return `True` (`policy="open"`), `claim_pending_run` returns `False` (`policy="closed"`). Three call sites, no exceptions — this count is the one the Verification row and the Failure Path tests are both written against.
6. **Lineage** (lane 5a): `_harness_env` adds `VALOR_CORRELATION_ID`; `build_telegram_outbox_payload` adds `correlation_id`; the worker installs `StructuredJsonFormatter`.
7. **Idempotent reflection** (lane 5b): `_push_agent_session` gains `idempotency_key`, `status`, and a `tuple[int, str]` return with a `SET NX` guard on `enqueue:idem:{key}`; the preallocated id is handed to `AgentSession` as `id=`, never `agent_session_id=`. `_enqueue_agent_reflection` receives a `due_epoch` computed in the tick loop *before* `mark_started` overwrites `ran_at`, passes the key, and the seam returns the existing row id on a repeat.
8. **Lease** (lane 6): pop acquires `lease:session:{id}` with generation `g` (Lua, server `TIME`); the executor renews every TTL/3; `transition_status` and outbox writes carry `g` and are rejected when the head generation is higher; the health loop's lapsed-lease scan moves the row to `pending` and dead-letters the prior attempt's fence record.
9. **Output**: dashboard tiles for dead letters by stage and fail-open counts; `logs/worker.log` as JSON; no silent discard anywhere on the path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1817 B2 `claim_pending_run` | 30s `SET NX` gate before `running` | A gate, not a lease: nothing is held or renewed during execution, so a dead owner is inferred from PID liveness 300s later |
| #1866 D3 held tasks | Kept strong references to fire-and-forget tasks | Prevents GC, not loss: a raised exception is still swallowed and never retried |
| JSONL→Popoto `DeadLetter` | Atomic outbound dead letters | Scoped to one stage and replayed only on bridge start |
| `MAX_RELAY_RETRIES` in-payload counter | Retry count travels inside the message | Correct, but the discard branch for unparseable payloads runs before the counter exists |

**Root cause pattern:** each fix hardened one seam with a local mechanism and stopped at "not silently dropped here", leaving the next seam to reinvent the pattern. No shared work-item, dead-letter, or lease primitive existed to reuse.

## Architectural Impact

- **New dependencies**: none. Redis Lua, Popoto, pydantic are in use.
- **Interface changes**: `_push_agent_session` gains `idempotency_key`, `status`, and a `tuple[int, str]` return (owned here; #3177 consumes); `transition_status` gains an optional `generation` kwarg; `DeadLetter` gains `stage`, `payload_json`, `reason`, `attempts`, `replayable`; two new models `SideEffectJob` and `SessionLease` (the latter a non-Popoto key family under `models/redis_lease.py`); three pydantic payload models in `bridge/wire_schemas.py`.
- **Coupling**: the reflection process gains two function reflections that read worker-written rows through the ORM; the worker gains no new imports from `bridge/`. The lease module is imported by `models/session_lifecycle.py` and later by #3177's control journal.
- **Data ownership**: `AgentSession` keeps execution state; `SessionLease` owns the claim; `DeadLetter` owns terminal failures; `SideEffectJob` owns pending side effects. No model substitutes for another.
- **Reversibility**: lanes 1-5 are additive and each behind its own reflection `enabled` flag or settings field. Lane 6 changes the pop path; it ships behind `FeatureSettings.session_lease_enabled` defaulting to false for one release, log-only, then flips.

## Appetite

**Size:** Large, delivered as six independently shippable lanes; this build dispatches lanes 1-5, including the `_push_agent_session` seam. Lane 6 is a child issue filed by the last task because it ships behind a shadow-release flag over two releases.

**Team:** one builder per lane, one validator, one documentarian, code review per PR.

**Interactions:** none; the three open questions were answered before critique (see §Decisions).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable through Popoto | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; assert r.ping()"` | Every lane |
| Interpreter on pin | `.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); assert sys.version.startswith(pin), (pin, sys.version)"` | Worktree venvs |
| Reflection register helper present | `grep -c "def register_reflection" scripts/update/reflection_register.py` | Lanes 1, 2 register ticks through it |

Run via `python scripts/check_prerequisites.py docs/plans/etl-pipeline-hardening.md`.

## Solution

### Key Elements

- **`SideEffectJob`** (`models/side_effect_job.py`): `job_id` AutoKeyField, `kind` IndexedField, `session_id` KeyField, `project_key` KeyField, `attempts` IntField, `next_attempt_at` DatetimeField, `status` IndexedField (`pending | running | done`), `Meta.ttl` 7 days. Handlers registered in `agent/side_effects.py` as `{kind: callable}`; first kind is `memory_extraction`. Drained by `reflections/housekeeping/side_effect_drain.py` every 60s with backoff `min(30 * 2**attempts, 900)` and cap 4.
- **`DeadLetter`** generalized in place: add `stage` IndexedField, `payload_json` Field, `reason`, `attempts` IntField, `first_seen`/`last_seen` DatetimeField, `replayable` Field. Existing `chat_id`, `reply_to`, `text` stay for `stage="telegram_send"`. Stages: `telegram_send`, `email_send`, `outbox_parse`, `steering_parse`, `notify_parse`, `extraction`, `session_recovery_cap`, `session_init_hang`, `session_corrupt_row`, `archive_restore` (observability mirror of the SQLite quarantine; `replayable=False`, since the authoritative retry state stays in `_restore_quarantine`), and (reserved for #3177) `improve_intent`. `reflections/housekeeping/dead_letter_replay.py` every 300s: `replayable` rows go to the stage's handler (`bridge/dead_letters.py` grows a handler registry); non-replayable rows age out at 30 days. `ui/data/dead_letters.py` + a dashboard tile with counts by stage.
- **Wire schemas** (`bridge/wire_schemas.py`): `OutboxPayload`, `SteeringPayload`, `NotifyPayload`, each with `v: int = 1`, constructed by every writer (`build_telegram_outbox_payload`, `push_steering_message`, `publish_session_notify`) and parsed by every reader (`process_outbox`, `_drain_list`, `_session_notify_listener`). Unknown fields allowed; a parse failure dead-letters with the raw string.
- **Lock policy** (`agent/lock_policy.py`): `record_lock_degradation(name: str, policy: Literal["open", "closed"]) -> None` runs `HINCRBY {project}:locks:degraded "{name}:{policy}" 1` through `utils.redis_client.text_redis()` (non-Popoto key), swallowing its own Redis errors so observability can never break a lock path. It is called from **exactly three** branches — one per lock — because it counts *degradation*, not fail-open specifically; the policy is the second half of the field name, so the tile renders count beside policy. Each of the three locks states its policy in its docstring; `claim_pending_run` fails closed, no override. Dashboard tile reads the hash.
- **Lineage**: `VALOR_CORRELATION_ID` in `_harness_env`; `correlation_id` in the outbox payload model; `worker/__main__.py::_configure_logging` uses `bridge.log_format.StructuredJsonFormatter` for the file handler (stderr stays text).
- **Idempotent enqueue seam and reflection key**: `_push_agent_session(..., idempotency_key: str | None = None, status: str = "pending") -> tuple[int, str]`; with a key it runs `SET enqueue:idem:{key} {agent_session_id} NX EX 86400` through `utils.redis_client.text_redis()` after the stale-terminal reconcile and before `async_create`, and on a lost race returns the bound id. Reflections pass `idempotency_key=f"reflection:{name}:{int(due_epoch) // 60 * 60}"` (see lane 5b for where `due_epoch` comes from).
- **Session lease** (child): `models/redis_lease.py` with `acquire(key, ttl) -> generation`, `renew(key, generation)`, `release(key, generation)`, all Lua with server `TIME`; `SessionLease` keyed `lease:session:{agent_session_id}`; pop acquires, executor renews every TTL/3 (TTL 90s), `transition_status(generation=...)` and outbox writes check `generation >= head`; health loop scans for rows `running` with no live lease and moves them to `pending`, dead-lettering the attempt's fence. Pop lock and run claim are deleted once the lease is authoritative.

### Flow

Finalize → job row → drained by reflection → retried or dead-lettered. Send/parse fails → dead letter → replayed by reflection or aged. Lock error → counted, policy applied. Message → correlation id follows it into the subprocess and back out in the outbox. Pop → lease with generation → renewed → checked on every write → lapsed lease redelivers.

### Technical Approach

#### Lane 1: side-effect jobs

- Add `models/side_effect_job.py`, export from `models/__init__.py` with the schema-gate docstring, extend `tests/unit/test_agentsession_index_guard_generalized.py`.
- `agent/side_effects.py`: `HANDLERS = {"memory_extraction": run_post_session_extraction}`; `enqueue(kind, session_id, project_key)`; `run_due(limit)`.
- `finalize_session` replaces the call to `_schedule_post_session_extraction` with `enqueue("memory_extraction", ...)`. Delete `_schedule_post_session_extraction`, `drain_pending_extractions`, and the worker shutdown drain call. Keep `run_post_session_extraction` unchanged.
- `reflections/housekeeping/side_effect_drain.py` with the five-line header; register via a `register_side_effect_drain` in `scripts/update/reflection_register.py` called from `run.py` beside `register_crash_recovery`.
- Migration `side_effect_job_model` in `scripts/update/migrations.py` (no-op marker plus one-time enqueue for sessions completed in the last 24h with no extraction record).
- #3177 Gap E kill switch becomes a `skip` disposition on the handler, keyed by `settings.improvement.extraction_paused` when that settings block exists; until then a `FeatureSettings.side_effects_paused` bool.

#### Lane 2: one dead-letter model and replayer

- Extend `models/dead_letter.py` in place; migration `dead_letter_stage_backfill` sets `stage="telegram_send"`, `replayable=True`, `attempts=MAX_RELAY_RETRIES` on existing rows.
- `bridge/dead_letters.py`: `record(stage, payload, reason, replayable)` and `HANDLERS = {stage: replay_fn}`; `replay_dead_letters(client)` becomes the `telegram_send` handler; the bridge connect call site stays as an eager first pass.
- Writers (all line numbers at baseline `bf0a5d5`):
  - `bridge/telegram_relay.py` retry cap — the `_dead_letter_message` call at `:1498-1500` already writes a row; lane 2 only adds `stage="telegram_send"` and the new fields to it.
  - `bridge/telegram_relay.py` **the two real discard branches inside `process_outbox`**: malformed JSON at `:1337-1339` and unknown `type` at `:1343-1347`. Each `continue` becomes `dead_letters.record(stage="outbox_parse", payload=raw, reason=...)` then `continue`. **Do not touch `_dead_letter_message` (`:943-1020`).** Its seven "discard" mentions are deliberate, still-correct drops of an *already dead-lettered* record that has no deliverable Telegram peer; converting them would dead-letter a dead letter. They are the reason a file-wide `grep discard` can never be the verification for this lane.
  - Email relay equivalent discard branch.
  - `finalize_session` for `session_recovery_cap` and `session_init_hang` with `payload_json = {message_text, chat_id, project_key, extra_context}`.
  - The corrupted-pop reaper (`agent/agent_session_queue.py:2175-2272`) with the raw hash.
  - `agent/session_archive.py` — **observability only. The `_restore_quarantine` SQLite table stays, unchanged and authoritative.** Add one `DeadLetter(stage="archive_restore")` emission in the cap branch at `:420` (the `attempt_count >= SESSION_ARCHIVE_ROW_ATTEMPT_CAP` arm of `_record_row_failure`), and only there — not on every failed row. Wrap it in `try/except Exception: logger.debug(...)` so a Redis failure can never abort the SQLite transaction that is the durable record. Rationale: the table is the live cold-start skip list (`_quarantined_ids` at `:384`, read at `:512` and `:592`) **and** the per-row attempt counter (`_record_row_failure` at `:397-411`). It lives on disk to survive an emptied Redis, which is the exact event the archive exists to recover from; moving it into Popoto would put the skip list inside the store the archive rebuilds, re-attempting every poison row from attempt 0 on every cold start. §Decisions 1 also leaves the live `attempt_count` homeless in a terminal `DeadLetter`. Because the table is untouched, this lane needs **no** SQLite schema migration — `agent/session_archive.py` has no `PRAGMA user_version` and only one ad-hoc `ALTER TABLE` at `:143`, and adding a fleet-wide on-disk migration is out of this plan's appetite.
- `reflections/housekeeping/dead_letter_replay.py` every 300s; register through `reflection_register.py`.
- `ui/data/dead_letters.py`, `ui/templates/dead_letters/` partial, inline route in `ui/app.py`.

#### Lane 3: wire schemas

- `bridge/wire_schemas.py`; `OutboxPayload` carries the union of the telegram and email shapes documented in `bridge-worker-architecture.md` plus `correlation_id: str | None`; `SteeringPayload` mirrors `agent/steering.py`'s entry dict; `NotifyPayload` mirrors `publish_session_notify`.
- Writers call `.model_dump_json()`; readers call `.model_validate_json()` and on `ValidationError` call `dead_letters.record(stage=f"{wire}_parse", payload=raw, ...)` and continue.
- Delete `KNOWN_MESSAGE_TYPES` membership check in favor of a `Literal` on `type`.
- Delete `_truthy()` at `session_pickup.py:76` only if the field it guards moves to a typed pydantic read in this lane; otherwise leave it and note in Rabbit Holes.

#### Lane 4: fail-open policy and counters

- `agent/lock_policy.py::record_lock_degradation(name, policy)`; the counter hash is a non-Popoto key, so the client comes from `utils.redis_client.text_redis()` (lazy import inside the function body, per that module's contract). Do not bind a hand-rolled `REDIS_URL` client or a module-level `POPOTO_REDIS_DB` alias — see the Freshness Check. The function catches and logs its own exceptions; a counter write must never be able to change a lock's answer.
- **Three call sites, one per lock. All three are mandatory** — the counter measures degradation, and a lock that fails closed is degraded too:
  1. `agent/session_pickup.py:131-134` `_acquire_pop_lock` — `record_lock_degradation("pop_lock", "open")`, then keep `return True`. Docstring: "Policy: fail open; duplicate work is preferred to a stalled queue."
  2. `bridge/dedup.py:188-195` `claim_message` — `record_lock_degradation("claim_message", "open")`, then keep `return True`. Same docstring line.
  3. `models/session_lifecycle.py:858-864` `claim_pending_run` — `record_lock_degradation("claim_pending_run", "closed")`, then `return False` (changed from `return True`). No break-glass setting (owner decision 2026-09-06). Docstring: "Policy: fail closed; a duplicate `claude -p` on one worktree corrupts git state."
- `ui/data/locks.py` tile: split each `{name}:{policy}` field into a row showing lock name, declared policy, and degradation count.

#### Lane 5a: lineage

- `_harness_env` adds `VALOR_CORRELATION_ID`; `build_telegram_outbox_payload` and the email payload builder add `correlation_id`; the relay logs it on send.
- `worker/__main__.py::_configure_logging`: file handler uses `StructuredJsonFormatter`; `scripts/log_rotate.py` unaffected (line-oriented).
- `docs/features/correlation-ids.md` gains the two new hops.

#### Lane 5b: idempotent enqueue seam and reflection key

- Add `idempotency_key: str | None = None` and `status: str = "pending"` to `_push_agent_session` (`agent/agent_session_queue.py:204-231`); thread `status` into `async_create` at `:369`; change the return to `tuple[int, str]` (queue depth, bound `agent_session_id`).
- **There are exactly three production callers**, re-derived at `bf0a5d5` with `grep -rn "_push_agent_session" agent/ tools/ bridge/ worker/ models/ scripts/`:
  1. `agent/agent_session_queue.py:1712` — `depth = await _push_agent_session(...)` inside `enqueue_agent_session`. This is the breaking pattern: once it unpacks a tuple, every test that patches the seam with a stub returning a bare `int` raises `TypeError: cannot unpack non-sequence int`.
  2. `tools/valor_session.py:760`.
  3. `agent/reflection_scheduler.py:754`.
  `retry_agent_session` (`agent/agent_session_queue.py:731`) calls `AgentSession.create(**fields)` directly and never touches the seam; `tools/agent_session_scheduler.py` contains no reference at all. Both were named as callers in the pre-critique draft and are **not** — do not go looking for them.
- With a key: `SET enqueue:idem:{key} {preallocated_id} NX EX 86400` via `utils.redis_client.text_redis()` in `agent/enqueue_idempotency.py`, placed after the stale-terminal reconcile at `:361` and before `async_create` at `:369`; on a lost race read the bound id back from the key and return it without creating a row. The preallocated id is minted with the same `AutoKeyField` generator Popoto uses so the created row carries it.
- **Plumb the due value down; do not re-derive it inside the enqueue.** `_enqueue_agent_reflection(entry)` (`agent/reflection_scheduler.py:709`) sees only the registry entry, and so does `run_reflection(entry, state)` (`:597`). The due value is a local inside `is_reflection_due` (`:524`) and is thrown away. It **cannot** be recomputed at the `:635` dispatch site from `state.ran_at`, because `run_reflection` calls `state.mark_started()` at `:607` and `mark_started` does `self.ran_at = time.time(); self.save()` (`models/reflection.py:171-175`) — by `:635` the input has already been clobbered, so a crash-retry would compute a different key. The round-1 critique's "populate it at the `:635` dispatch site" note is corrected here for exactly that reason.
  1. Extract the `ran_at` recovery that `is_reflection_due` does at `:512-521` (the `isinstance` descriptor guard plus the blank-`every:` `_latest_run_timestamp` fallback) into `_effective_last_run(entry, state) -> float | None`, and call it from both `is_reflection_due` and the new helper.
  2. Add `reflection_due_epoch(entry, state, now) -> float | None`: returns `compute_next_due(entry.schedule, last_run=_effective_last_run(entry, state), now=now)`, or `None` when `entry.schedule` is blank or `compute_next_due` raises `ValueError`.
  3. Call it in the tick loop **immediately after** the `is_reflection_due` check at `:938`, i.e. before any `mark_started`, and pass the result through `run_reflection(entry, state, due_epoch=...)` at `:956` and `:971` (default `None`, so no other caller breaks) and on to `_enqueue_agent_reflection(entry, due_epoch=...)` at `:635`.
- `_enqueue_agent_reflection` passes `idempotency_key=f"reflection:{entry.name}:{int(due_epoch) // 60 * 60}"` when `due_epoch is not None`, and `idempotency_key=None` otherwise (a scheduleless / manually triggered reflection keeps today's non-idempotent behavior). The `// 60 * 60` floor is the tick period: it absorbs sub-tick jitter so two ticks inside one window agree. No `.timestamp()` call anywhere — `compute_next_due` returns a float.
- **The preallocated id must be passed as `id=`, never `agent_session_id=`.** `AgentSession.__init__` pops `agent_session_id` at `models/agent_session.py:876-877` (`kwargs.pop("agent_session_id")  # AutoKeyField, ignore`), so a binding passed under that name is dropped **without raising** and the row gets a fresh generated id — the key would then point at a session that does not exist. Popoto's `AutoFieldMixin` accepts an explicit key value under `id`. `tests/unit/test_enqueue_idempotency.py` must assert the created row's key equals the value stored at `enqueue:idem:{key}`; a test that only asserts "one row exists" passes under the broken form.
- #3177 Gap C consumes this seam unchanged; its `admitted` status value is a caller concern (`status="admitted"`), not a seam change.

#### Lane 6 (child issue): execution lease

- `models/redis_lease.py` on `utils.redis_client.text_redis()`; Lua scripts for acquire/renew/release with server `TIME` and `generation >= highest_accepted`.
- Pop: acquire before `transition_status("running")`; executor: renew task every 30s; `transition_status(generation=)` and `TelegramRelayOutputHandler.send` check the head; health loop: lapsed-lease scan replaces `_sweep_dead_worker_sessions` PID inference for `running` rows (PID fence stays for process reaping).
- Ship behind `FeatureSettings.session_lease_enabled=False` for one release, logging would-be rejections; flip; then delete `_acquire_pop_lock` and `claim_pending_run`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Side-effect handler raises: `attempts` increments, `next_attempt_at` backs off, and at the cap a `DeadLetter(stage="extraction")` exists with the session id; test asserts the row, not a log line
- [ ] Reflection process cannot reach Redis mid-drain: the job stays `pending` with unchanged `attempts` (no double count)
- [ ] Relay receives unparseable JSON: a `DeadLetter(stage="outbox_parse")` holds the raw string and the relay continues to the next entry
- [ ] `claim_pending_run` Redis error: returns `False`, `locks:degraded` has field `claim_pending_run:closed` = 1, and no `running` transition occurred
- [ ] `claim_message` Redis error: returns `True`, `locks:degraded` field `claim_message:open` = 1, enqueue proceeds
- [ ] `_acquire_pop_lock` Redis error: returns `True`, `locks:degraded` field `pop_lock:open` = 1
- [ ] `record_lock_degradation` itself cannot reach Redis: it returns `None` without raising and the lock's own return value is unchanged
- [ ] Replay handler raises: `attempts` bumps on the dead letter, `replayable` flips false after 3

### Empty/Invalid Input Handling
- [ ] `record(stage="", ...)` is refused with `ValueError`
- [ ] `OutboxPayload` with `v=2` and unknown fields parses (forward-compatible); missing `chat_id` dead-letters
- [ ] `run_due(limit=0)` is a no-op with no Redis writes

### Error State Rendering
- [ ] Dashboard dead-letter tile renders "0 by stage" on an empty namespace and a per-stage row when seeded
- [ ] Fail-open tile renders each lock name with its policy and count

## Test Impact

- [ ] `tests/unit/test_session_executor_extraction_decoupling.py` — REPLACE: asserts on `_schedule_post_session_extraction` become assertions that `finalize_session` enqueued a `SideEffectJob`
- [ ] `tests/unit/test_session_executor_calendar_heartbeat.py` — UPDATE: remove `drain_pending_extractions` expectations
- [ ] `tests/integration/test_session_finalization_decoupled.py` — UPDATE: extraction path is now the job row
- [ ] `tests/unit/test_dead_letters.py` — UPDATE: new fields and handler registry; add replay-by-stage cases
- [ ] `tests/unit/test_bridge_relay.py`, `tests/unit/test_email_relay.py`, `tests/unit/test_telegram_relay_voice_note.py` — UPDATE: discard branches now assert a dead-letter row; payloads built via `OutboxPayload`
- [ ] `tests/integration/test_redis_models.py`, `tests/unit/test_model_relationships.py` — UPDATE: `DeadLetter` and `SideEffectJob` field sets
- [ ] `tests/unit/test_ui_reflections_data.py` — UPDATE: two new reflections in the registry
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py` — UPDATE: enumerate `SideEffectJob`
- [ ] `tests/unit/test_dedup.py`, `tests/unit/test_agent_session_queue.py`, `tests/integration/test_worker_concurrency.py` — UPDATE: fail-open cases assert the counter; run-claim case asserts fail-closed
- [ ] `tests/unit/output_handler/test_output_handler_handlers.py`, `tests/unit/test_tool_call_delivery.py` — UPDATE: payload carries `correlation_id`
- [ ] `tests/integration/test_harness_env_pm_injection.py` — UPDATE: `VALOR_CORRELATION_ID` present
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: duplicate-tick case
- [ ] **Seam blast radius (lane 5b): 23 test files reference `_push_agent_session`, not four.** Enumerate with `grep -rln "_push_agent_session" tests/` at build time and check every stub's return value. The list at `bf0a5d5`: `tests/integration/{test_agent_session_lifecycle,test_bridge_routing,test_connectivity_gaps,test_lifecycle_transition,test_notify_isolation,test_parent_child_round_trip,test_session_notify,test_silent_failures,test_worker_drain}.py` and `tests/unit/{test_agent_session_queue,test_agent_session_queue_async,test_child_session_gate,test_hub_alias_references,test_pm_session_auto_slug,test_pm_session_refuse_no_issue,test_reflection_scheduler,test_resume_notify,test_teammate_cold_start_finalize,test_valor_session_cli,test_valor_session_create_core,test_valor_session_project_key,test_valor_session_sdlc_metadata,test_valor_session_working_dir_resolution}.py`
- [ ] `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session_queue_async.py` — UPDATE: `_push_agent_session` returns a tuple; new `idempotency_key` and `status` kwargs; lost-race binding case; assert the created row's key equals the value bound into `enqueue:idem:{key}`
- [ ] `tests/unit/test_valor_session_cli.py` — UPDATE: the `_fake_push` stubs at `:280` and `:379` return a literal `1` and must return `(1, "<id>")`. These are the only two confirmed int-returning seam stubs at `bf0a5d5`; re-run the enumeration above in case a peer adds another
- [ ] `tests/integration/test_bridge_routing.py` — NO CHANGE. `TestWorkflowIdAbsent` (`:162-170`) asserts only that `workflow_id` is *absent* from `inspect.signature(_push_agent_session)`; adding `idempotency_key` and `status` does not touch it. The round-1 critique's note that this file "must be updated for the two new kwargs" was wrong on re-derivation and is recorded here so a builder does not chase it
- [ ] `tests/unit/test_agent_session_scheduler_kill.py`, `tests/integration/test_session_spawning.py` — NO CHANGE. Both were named in the pre-critique draft; neither contains a single reference to `_push_agent_session`
- [ ] `tests/integration/test_steering.py` — UPDATE (lane 3): entries built via `SteeringPayload`

## Rabbit Holes

- Replacing the queue with Redis Streams. Spike-1: the selection logic is a Python filter over the row; a stream adds a second substrate and still needs a reaper.
- Exactly-once semantics. The unit is an LLM turn; at-least-once plus idempotent stages is the target, and the lease makes duplicates rejectable, not impossible.
- A generic retry framework. `SideEffectJob` has one drain loop and a handler dict; a second kind is a dict entry, not a framework.
- Migrating the reflection scheduler's own retry policy onto `SideEffectJob`. Reflections are not side effects of sessions; leave them.
- Typing `extra_context`. It is an open bag by design (`tests/unit/test_bridge_context_guards.py` polices dead keys); wire schemas stop at the Redis lists.
- Fixing `_truthy()`'s root cause in Popoto. Out of repo.
- Chasing the ~87 `except Exception: pass` sites named in the three-tier draft. This plan converts the ones on the pipeline path that lose work; the rest stay.

## Risks

### Risk 1: lane 6 changes the hot pop path
**Impact:** a lease bug stalls every worker key.
**Mitigation:** shadow release behind `session_lease_enabled=False` logging would-be rejections for one release; the health sweep stays as backstop until the flag flips; delete the old gates only after.

### Risk 2: run claim fail-closed under a Redis blip stalls pickup
**Impact:** pending sessions wait until Redis recovers.
**Mitigation:** the pop lock is retried on the next loop iteration already; the counter makes the blip visible on the dashboard; there is deliberately no override, so a prolonged Redis degradation stalls pickup rather than risking a duplicate `claude -p`.

### Risk 3: extraction moves out of process and loses the worker's in-memory context
**Impact:** an extraction that depended on worker state fails in the reflection process.
**Mitigation:** spike-4 found no such dependency; the first drain runs with `limit=1` and a Verification row checks a real session's extraction landed.

### Risk 4: dead-letter volume from a misbehaving relay floods Redis
**Impact:** unbounded rows.
**Mitigation:** `Meta.ttl` 30 days, `replayable=False` after 3 replay attempts, and a per-stage cap of 10,000 rows that costs **O(1) on the write path**. `record()` is called from the relay's per-message failure branch, so it must never run a per-stage census or an ordering pass — under the flood the cap is written for, a census makes the mitigation the bottleneck.
- On every `record()`: `HINCRBY {project}:dead_letters:count {stage} 1` and `ZADD {project}:dead_letters:{stage} <first_seen_epoch> <letter_id>`, both through `utils.redis_client.text_redis()`. Two O(log n) commands, no hydration, no scan.
- Eviction runs in the `dead-letter-replay` reflection (300s cadence), never in `record()`: read the `HGET` count, and while it exceeds the cap take `ZRANGE {project}:dead_letters:{stage} 0 <overflow-1>` and, for each id, `DeadLetter.query.get(id).delete()` through the ORM, then `ZREM` the id and `HINCRBY` the counter down.
- **Never a raw Redis delete on the Popoto row.** `.claude/hooks/validators/validate_no_raw_redis_delete.py` blocks it and the guard is machine-global. The sorted set is an eviction *index* over ids; the rows themselves are only ever removed via `instance.delete()`.
- The counter is advisory. It drifts when TTL expiry removes a row without a `ZREM`, so the eviction pass reconciles it from `ZCARD` each time it runs rather than trusting the hash.

### Risk 5: seam contention with #3177 lane 3
**Impact:** two PRs change `_push_agent_session`'s signature.
**Mitigation:** decided: this plan owns the seam (task build-seam); #3177's lane 3 child issue references it.

## Race Conditions

### Race 1: two drain ticks pop the same job
**Location:** `agent/side_effects.py::run_due`
**Trigger:** reflection tick overlaps (#3177 Race 1)
**Data prerequisite:** job `status="pending"`, `next_attempt_at <= now`
**State prerequisite:** both ticks read before either writes
**Mitigation:** `transition_status`-style CAS on `status` via the existing `models/session_lifecycle.py` idiom; the loser skips

### Race 2: relay dead-letters a payload the writer is still retrying
**Location:** `bridge/telegram_relay.py` retry counter vs `record()`
**Trigger:** the in-payload `_relay_attempts` and the dead-letter `attempts` disagree
**Mitigation:** the dead letter is written only at the cap and the payload is removed from the list in the same iteration; `attempts` on the row is seeded from the payload counter

### Race 3: lease renewed by a stale owner after takeover
**Location:** `models/redis_lease.py::renew`
**Trigger:** owner A stalls past TTL, health loop redelivers, B acquires generation g+1, A wakes and renews
**Mitigation:** renew is compare-and-set on generation; A's renew returns `False` and A's next write is rejected by `transition_status(generation=g)`

### Race 5: two callers race on one idempotency key
**Location:** `agent/enqueue_idempotency.py`
**Trigger:** a reflection tick and a crash-retry of the same tick call `_push_agent_session` with one key
**Mitigation:** `SET NX` is single-winner; the loser reads the bound id from the key and returns it; if the key exists but the row does not yet (winner crashed between `SET NX` and `async_create`), the loser retries `async_create` with the preallocated id, matching #3177 Race 2

### Race 4: finalize enqueues a job for a session the archive already restored
**Location:** `finalize_session` vs `session_archive.restore_if_empty`
**Mitigation:** job key is `(kind, session_id)` with `SET NX` on create; a second enqueue binds to the existing row

## No-Gos (Out of Scope)

- [ORDERED] Redis Streams as the queue substrate. Spike-1 reason; revisit only if the selection filter collapses to FIFO.
- [ORDERED] Room inbox authoritative cutover (#2494). Separate plan; nothing here depends on it.
- [ORDERED] Correlation id on steering. Rejected in #3177 with a stated reason.
- [EXTERNAL] Popoto boolean round-trip fix. Upstream repo.
- [DESTRUCTIVE] Deleting `_acquire_pop_lock` / `claim_pending_run` before the lease is authoritative. Lane 6 deletes them only after the flag has been on for one release.
- [DEFERRED] Bounding pending-queue depth. Execution is bounded by the slot registry; pending growth is unobserved.

## Update System

- `scripts/update/run.py` gains `register_side_effect_drain` and `register_dead_letter_replay` steps through `reflection_register.py`, idempotent like `register_crash_recovery`, pinned to no `project_key` (they run on every machine that runs a worker).
- Migrations `side_effect_job_model` and `dead_letter_stage_backfill` registered in `MIGRATIONS`.
- No new dependencies. New settings fields default off or safe; `.env.example` gains `FEATURES__SESSION_LEASE_ENABLED` (lane 6) with `# @optional`; no run-claim override exists.
- **Deploy consequence: a full service restart, fleet-wide.** This is not a hot-reloadable change. Every lane alters code the long-lived processes hold in memory: the bridge's relay and outbox reader (lanes 2, 3, 5a), the worker's pop, finalize, and enqueue paths (lanes 1, 4, 5b), and the reflection process's registry (lanes 1, 2). On every bridge machine, after `/update` pulls the merge: `./scripts/valor-service.sh restart` **and** `worker-restart`. A machine that pulls the code without restarting runs the old bridge against the new models, which is exactly the shape of the #3003 outage (new import, old process, silent stall). Verify per machine with `tail -5 logs/bridge.log` showing "Connected to Telegram".

## Agent Integration

- No new CLI entry point. Replay is a reflection; manual replay is `python -m tools.dead_letters replay --stage X` added to `docs/tools-reference.md` as a thin wrapper over `bridge.dead_letters.replay_stage`.
- The bridge imports `bridge/wire_schemas.py` and the generalized `bridge/dead_letters.py`; the worker imports `agent/side_effects.py` and `agent/lock_policy.py`.
- Integration tests: a finalized session produces a job row visible from the reflection process; a relay discard produces a dead letter visible on the dashboard route.

## Documentation

- [ ] Create `docs/features/pipeline-dead-letters.md`: the one model, stages, replay reflection, dashboard tile, manual replay tool
- [ ] Create `docs/features/side-effect-jobs.md`: the job model, handler registry, drain reflection, how to add a kind
- [ ] Create `docs/features/wire-schemas.md`: the three payload models, versioning rule, parse-failure disposition
- [ ] Update `docs/features/bridge-worker-architecture.md`: Redis Pop Lock section states per-lock policy and the counter; outbox payload table references `OutboxPayload`; Worker Output Delivery notes `correlation_id`
- [ ] Update `docs/features/delivery-integrity-hardening.md`: follow-up workstreams table points B2 and D3 remainders at this plan
- [ ] Update `docs/features/correlation-ids.md`: subprocess env and outbox hops
- [ ] Update `docs/features/subconscious-memory.md`: post-session extraction is a `SideEffectJob`
- [ ] Update `docs/features/reflections.md` and `docs/features/adding-reflection-tasks.md`: the two new reflections and the `reflection_register.py` path
- [ ] Update `docs/features/dashboard.md`: two new tiles
- [ ] Add rows to `docs/features/README.md` and `docs/tools-reference.md`

## Success Criteria

### This build (lanes 1-5)
- [ ] A session finalizing on `main` produces a `SideEffectJob` row and, after the drain, an extraction record; a handler that raises produces a `DeadLetter(stage="extraction")` after 4 attempts
- [ ] `DeadLetter` has a `stage` field and every terminal sink on the relay, session, and archive paths writes it; no silent discard remains in `process_outbox`
- [ ] The `dead-letter-replay` and `side-effect-drain` reflections are registered through `reflection_register.py` and survive a simulated `/update`
- [ ] Outbox, steering, and notify payloads are constructed and parsed through `bridge/wire_schemas.py`
- [ ] Each of the three locks states its policy; every fail-open branch increments the counter; `claim_pending_run` fails closed under a simulated Redis error
- [ ] `VALOR_CORRELATION_ID` reaches the subprocess and `correlation_id` reaches the outbox payload; `logs/worker.log` lines parse as JSON
- [ ] `_push_agent_session` returns the bound id; two consecutive reflection ticks for one due window enqueue one session; a crash-retry with the same key binds to the existing row
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

### Carried to the child issue
- [ ] Lane 6: a `running` session whose owner dies is redelivered within 90s; a stale-generation `transition_status` is rejected; the pop lock and run claim are deleted

## Team Orchestration

### Team Members

- **Builder (side effects)**
  - Name: jobs-builder
  - Role: lane 1
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (dead letters)**
  - Name: dlq-builder
  - Role: lane 2
  - Agent Type: builder
  - Domain: Redis/Popoto data, dashboard
  - Resume: true

- **Builder (wire schemas)**
  - Name: schema-builder
  - Role: lane 3
  - Agent Type: builder
  - Resume: true

- **Builder (locks and lineage)**
  - Name: locks-builder
  - Role: lanes 4 and 5a
  - Agent Type: builder
  - Resume: true

- **Builder (enqueue seam)**
  - Name: seam-builder
  - Role: lane 5b
  - Domain: Redis/Popoto data
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: pipeline-validator
  - Role: Verification rows, mutation-check each new guard
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: pipeline-docs
  - Role: Documentation section
  - Agent Type: documentarian
  - Resume: true

### File Ownership (contended files)

Five builders run in parallel over one lane worktree. Three files are touched by more than one lane, so each gets exactly one owner; every other lane hands its change to that owner as a written spec rather than editing the file. A builder that finds itself about to edit a file it does not own stops and posts the diff it wants to the owner.

| File | Sole owner | What the other lanes hand over |
|---|---|---|
| `models/session_lifecycle.py` | **dlq-builder** (lane 2) | lane 1 hands the one-line swap of `_schedule_post_session_extraction(...)` → `enqueue("memory_extraction", session_id, project_key)` inside `finalize_session`; lane 4 hands the `claim_pending_run` change at `:858-864` (add `record_lock_degradation("claim_pending_run", "closed")`, flip `return True` → `return False`, add the policy docstring line). Lane 2's own edits are the `session_recovery_cap` / `session_init_hang` dead-letter writes. |
| `agent/agent_session_queue.py` | **seam-builder** (lane 5b) | lane 2 hands the corrupted-pop reaper dead-letter write (`:2175-2272`); lane 3 hands the `_session_notify_listener` parse change (`:1053`). Lane 5b's own edits are `_push_agent_session` (`:204-231`, `:361`, `:369`) and the `enqueue_agent_session` call site (`:1712`). Seam-builder is the owner because the signature change is the one edit that must land coherently with its callers. |
| `scripts/update/migrations.py`, `scripts/update/reflection_register.py`, `scripts/update/run.py` | **dlq-builder** (lane 2) | lane 1 hands `side_effect_job_model` (migration), `register_side_effect_drain` (register helper), and its `run.py` step. Lane 2 lands both migrations and both register helpers in one commit so `MIGRATIONS` and the `run.py` step order have a single author. |

Uncontended files stay with their lane: lane 1 owns `models/side_effect_job.py`, `agent/side_effects.py`, `reflections/housekeeping/side_effect_drain.py`; lane 2 owns `models/dead_letter.py`, `bridge/dead_letters.py`, `bridge/telegram_relay.py`, the email relay, `agent/session_archive.py`, `reflections/housekeeping/dead_letter_replay.py`, `ui/data/dead_letters.py`; lane 3 owns `bridge/wire_schemas.py`, `agent/steering.py`, `agent/session_pickup.py`'s parse sites; lane 4 owns `agent/lock_policy.py`, `bridge/dedup.py`, `ui/data/locks.py`; lane 5a owns `agent/session_executor.py`, `agent/output_handler.py`, `worker/__main__.py`; lane 5b owns `agent/enqueue_idempotency.py`, `agent/reflection_scheduler.py`, `tools/valor_session.py`.

`agent/session_pickup.py` is touched by lane 3 (parse) and lane 4 (`_acquire_pop_lock` counter at `:131-134`). These are ~40 lines apart in different functions and are the one overlap left unsplit; **lane 4 owns the file**, and lane 3 hands over its parse change. `models/__init__.py` gains one export from lane 1 and none from lane 2 (`DeadLetter` is already exported) — no contention.

Commit early with explicit paths (`git add <path>`, never `git add -A`) so a peer sees the file move rather than colliding on it.

## Step by Step Tasks

### 1. Side-effect jobs
- **Task ID**: build-jobs
- **Depends On**: none
- **Validates**: `tests/unit/test_side_effect_jobs.py` (create), `tests/unit/test_session_executor_extraction_decoupling.py`, `tests/integration/test_session_finalization_decoupled.py`
- **Informed By**: spike-4
- **Assigned To**: jobs-builder
- **Agent Type**: builder
- **Parallel**: true (files split per §File Ownership; hand the `finalize_session` swap and all three `scripts/update/` changes to dlq-builder)
- Lane 1 Technical Approach in full; delete `_schedule_post_session_extraction` and `drain_pending_extractions`

### 2. Dead-letter model and replayer
- **Task ID**: build-dlq
- **Depends On**: none
- **Validates**: `tests/unit/test_dead_letters.py`, `tests/unit/test_bridge_relay.py`, `tests/unit/test_email_relay.py`, `tests/unit/test_ui_dead_letters.py` (create)
- **Informed By**: spike-2
- **Assigned To**: dlq-builder
- **Agent Type**: builder
- **Parallel**: true (sole owner of `models/session_lifecycle.py` and all three `scripts/update/` files per §File Ownership; hand the reaper write to seam-builder)
- Lane 2 Technical Approach in full. **The `_restore_quarantine` SQLite table is NOT deleted** — the archive change is one guarded, observability-only `DeadLetter` emission in the cap branch at `agent/session_archive.py:420`.

### 3. Wire schemas
- **Task ID**: build-schemas
- **Depends On**: build-dlq
- **Validates**: `tests/unit/test_wire_schemas.py` (create), `tests/unit/test_bridge_relay.py`, `tests/integration/test_steering.py`, `tests/unit/output_handler/test_output_handler_handlers.py`
- **Assigned To**: schema-builder
- **Agent Type**: builder
- **Parallel**: false
- Lane 3 Technical Approach; parse failures call `dead_letters.record`. Hand the `_session_notify_listener` parse to seam-builder and the `session_pickup.py` parse to locks-builder per §File Ownership

### 4. Lock policy and lineage
- **Task ID**: build-locks-lineage
- **Depends On**: none
- **Validates**: `tests/unit/test_lock_policy.py` (create), `tests/unit/test_dedup.py`, `tests/unit/test_agent_session_queue.py`, `tests/integration/test_harness_env_pm_injection.py`
- **Assigned To**: locks-builder
- **Agent Type**: builder
- **Parallel**: true (sole owner of `agent/session_pickup.py`; hand the `claim_pending_run` change to dlq-builder per §File Ownership)
- Lanes 4 and 5a Technical Approach

### 5. Enqueue seam and reflection idempotency
- **Task ID**: build-seam
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py`, `tests/unit/test_agent_session_queue_async.py`, `tests/unit/test_reflection_scheduler.py`, `tests/unit/test_enqueue_idempotency.py` (create)
- **Informed By**: spike-3; #3177 spike-2 and its structural critique finding on the `int` return
- **Assigned To**: seam-builder
- **Agent Type**: builder
- **Parallel**: true (sole owner of `agent/agent_session_queue.py` per §File Ownership; accepts the reaper write from dlq-builder and the notify parse from schema-builder)
- Lane 5b Technical Approach in full; all three real callers of `_push_agent_session` updated for the tuple return; the 23-file test sweep from §Test Impact; lost-race binding test asserting the row key equals the bound value

### 6. Validate lanes 1-5
- **Task ID**: validate-lanes
- **Depends On**: build-jobs, build-dlq, build-schemas, build-locks-lineage, build-seam
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row; mutate each guard (raise in a handler, corrupt a payload, error the run claim) and confirm the row catches it

### 7. File the child issue for lane 6
- **Task ID**: file-children
- **Depends On**: validate-lanes
- **Assigned To**: locks-builder
- **Agent Type**: builder
- **Parallel**: false
- File one issue via `/do-issue`, `Refs #3183` and `Refs #3177`, carrying lane 6's Technical Approach subsection and the §Relationship lease row; it states the shadow-release rule and names `models/redis_lease.py` as the module #3177 lane 3 imports

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lanes
- **Assigned To**: pipeline-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Every item in the Documentation section

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: file-children, document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run Verification; confirm the child issue exists and references both parents

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| SideEffectJob model exported | `.venv/bin/python -c "import models; print(hasattr(models, 'SideEffectJob'))"` | output contains True |
| Fire-and-forget extraction gone | `grep -rn "_schedule_post_session_extraction\|drain_pending_extractions" agent/ worker/ \| wc -l` | output contains 0 |
| DeadLetter has stage | `grep -c "stage = IndexedField" models/dead_letter.py` | output > 0 |
| `process_outbox` dead-letters instead of discarding | `scripts/pytest-clean.sh tests/unit/test_bridge_relay.py -q -k outbox_parse_dead_letters` | exit code 0, ≥1 test collected. The test feeds `process_outbox` one malformed-JSON entry and one `type="nope"` entry and asserts exactly two `DeadLetter` rows with `stage="outbox_parse"` carrying the raw strings. A file-wide `grep discard` cannot be the check here: at `bf0a5d5` it returns 9, and 7 of those are `_dead_letter_message`'s deliberate undeliverable drops (`:943-1020`) that no lane touches and that spell it "dead letter" with a space, so no `grep -v` filter separates them. |
| Session path writes dead letters at two sites | `grep -rn "dead_letters.record(" models/session_lifecycle.py agent/agent_session_queue.py \| wc -l` | output > 1 |
| Quarantine table survives (anti-criterion) | `grep -c "CREATE TABLE IF NOT EXISTS _restore_quarantine" agent/session_archive.py` | output contains 1 |
| Archive quarantine mirrors to a dead letter, guarded | `scripts/pytest-clean.sh tests/unit/test_session_archive_quarantine_dead_letter.py -q` | exit code 0, ≥2 tests collected: one asserts a `DeadLetter(stage="archive_restore")` appears when a row hits `SESSION_ARCHIVE_ROW_ATTEMPT_CAP`; one asserts that with `dead_letters.record` raising, `attempt_count` and `quarantined_at` still commit to SQLite |
| Two reflections registered | `grep -c "def register_side_effect_drain\|def register_dead_letter_replay" scripts/update/reflection_register.py` | output > 1 |
| Wire schemas used by readers | `grep -rn "model_validate_json" bridge/telegram_relay.py agent/steering.py agent/agent_session_queue.py \| wc -l` | output > 2 |
| Degradation counter on all three lock branches | `grep -rn "record_lock_degradation(" agent/session_pickup.py models/session_lifecycle.py bridge/dedup.py \| wc -l` | output contains 3 (exactly one per lock — the three sites enumerated in lane 4) |
| Run claim fails closed | `scripts/pytest-clean.sh tests/unit/test_lock_policy.py -q -k fail_closed` | exit code 0 |
| Correlation reaches subprocess | `grep -c "VALOR_CORRELATION_ID" agent/session_executor.py` | output > 0 |
| Correlation in outbox payload | `grep -c "correlation_id" bridge/wire_schemas.py` | output > 0 |
| Worker logs JSON | `grep -c "StructuredJsonFormatter" worker/__main__.py` | output > 0 |
| Seam carries idempotency key | `grep -c "idempotency_key" agent/agent_session_queue.py` | output > 0 |
| Reflections pass the key | `grep -c "idempotency_key=" agent/reflection_scheduler.py` | output > 0 |
| Duplicate tick enqueues once | `scripts/pytest-clean.sh tests/unit/test_enqueue_idempotency.py tests/unit/test_reflection_scheduler.py -q -k idempot` | exit code 0 |
| No break-glass on the run claim | `grep -rn "run_claim_fail_open" config/ models/ agent/ \| wc -l` | output contains 0 |
| Side-effect and dead-letter tests | `scripts/pytest-clean.sh tests/unit/test_side_effect_jobs.py tests/unit/test_dead_letters.py tests/unit/test_wire_schemas.py -q` | exit code 0 |
| Anti-criterion: no raw Redis on Popoto keys | `grep -rnE "POPOTO_REDIS_DB\.(hset\|hdel\|sadd\|srem\|zadd\|zrem\|delete)\(" models/side_effect_job.py models/dead_letter.py agent/side_effects.py \| wc -l` | output contains 0 |
| Non-ORM Redis goes through the accessor | `/usr/bin/grep -c "from utils.redis_client import" agent/lock_policy.py agent/enqueue_idempotency.py` | each file reports > 0 |
| Anti-criterion: no hand-built Redis client | `/usr/bin/grep -rn "redis.Redis(\|from_url(" agent/lock_policy.py agent/enqueue_idempotency.py agent/side_effects.py \| wc -l` | output contains 0 |
| Anti-criterion: lease not deleted early | `grep -c "def claim_pending_run" models/session_lifecycle.py` | output > 0 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |

## Critique Results

Round 1 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses (Agent tool unavailable in the stage context). **NEEDS REVISION**: 2 blockers, 5 concerns, 5 nits.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Lane 2 deletes the `_restore_quarantine` SQLite table, but that table is the live skip list and per-row attempt counter `restore_if_empty` consults on every cold-start rehydrate (`agent/session_archive.py:384` `_quarantined_ids`, `:398` `_record_row_failure`, read at `:512` and `:592`). It lives in SQLite precisely to survive the event it defends against: a wiped or empty Redis. Moving it to a Popoto `DeadLetter` puts the skip list inside the store the archive exists to rebuild, so after a Redis loss every poison row is re-attempted from attempt 0 on every cold start, and the quarantine write itself targets the Redis that is already degraded. Per §Decisions 1, `DeadLetter` is terminal with a payload snapshot, so the live `attempt_count` has no home. There is also no migration for the on-disk `data/session_archive.db` on the fleet: the module has no `PRAGMA user_version` and one ad-hoc `ALTER TABLE` at `:143`, and §Update System lists only the two Popoto migrations. | pending | Keep `_restore_quarantine` as the authoritative skip list and attempt counter; emit an observability-only `DeadLetter(stage="archive_restore")` alongside it. Emit only in the `attempt_count >= SESSION_ARCHIVE_ROW_ATTEMPT_CAP` branch at `:420`, wrapped in try/except so a Redis failure cannot abort the SQLite transaction that is the durable record. Drop the "SQLite table is deleted" clause and the "Quarantine table gone" verification row. If the table must go, the plan needs a `PRAGMA user_version`-gated SQLite migration and a stated home for `attempt_count` during a Redis outage. |
| BLOCKER | History & Consistency | The Verification row "Relay never discards" is unsatisfiable and its supporting citations point at the wrong code. Run verbatim at `main` the command returns 9; lane 2 can remove at most two of those lines (module docstring `:20`, unknown-type log `:1345`). The other seven are inside `_dead_letter_message` (`bridge/telegram_relay.py:943, 947, 968, 987, 992, 1000, 1020`) and describe deliberate, still-correct discards of undeliverable dead letters that no lane changes; `grep -v "dead_letter"` does not filter them because they spell it "dead letter" with a space. The row also overshoots its own Success Criterion, which correctly scopes the claim to `process_outbox`. Separately the branch citations are wrong: the malformed-JSON discard is at `:1337-1339` and the unknown-type discard at `:1343-1347` (both identical at baseline `bf0a5d5`); `:1533` is `requeue_raw = json.dumps(message)` in the re-queue branch and `1492-1536` is the retry-cap / poll-re-enqueue region. | pending | Replace the file-wide grep with an assertion in `tests/unit/test_bridge_relay.py` that feeds `process_outbox` a malformed JSON entry and an entry with `type="nope"` and asserts two `DeadLetter` rows with `stage="outbox_parse"` — `_dead_letter_message`'s own discards must survive and a file-wide grep cannot distinguish them. Correct the `:1533` and `1492-1536` citations in §Problem and in lane 2's Writers list to `:1337-1339` and `:1343-1347`. |
| CONCERN | History & Consistency | Four sections disagree on whether the fail-CLOSED `claim_pending_run` increments the fail-open counter. Lane 4's Technical Approach adds `record_fail_open` to exactly two locks (`_acquire_pop_lock`, `claim_message`) and gives `claim_pending_run` only a docstring change. But §Failure Path Test Strategy requires "`claim_pending_run` Redis error: returns `False`, `locks:fail_open` has `claim_pending_run=1`", and the Verification row greps three files for `record_fail_open(` expecting `> 2`, reachable only with a third call site in `models/session_lifecycle.py`. A builder following the Technical Approach literally lands two call sites and fails both the row and the test. | pending | Rename the primitive to `record_lock_degradation(name: str, policy: Literal["open", "closed"])` writing `HINCRBY {project}:locks:degraded "{name}:{policy}" 1` through `utils.redis_client.text_redis()` (lazy import in the function body, per that module's contract), and call it from all three branches. The Verification row's `> 2` then becomes satisfiable and the tile can render policy beside count as §Failure Path Test Strategy already requires. Keep `claim_pending_run`'s fail-closed `return False` unchanged — the counter is observability, not a break-glass. |
| CONCERN | History & Consistency | Lane 5b's idempotency key cannot be written where the plan puts it, and its type is wrong. `_enqueue_agent_reflection(entry)` (`agent/reflection_scheduler.py:709`) receives only the registry entry; so does its caller `run_reflection(entry, state)` at `:595`, dispatched at `:635`. The due value is a local inside `is_reflection_due` at `:524` (`next_due = compute_next_due(entry.schedule, last_run=ran_at, now=now)`), and `agent.reflection_schedule.compute_next_due` returns a float epoch, not a datetime (`tests/unit/test_reflection_schedule_grammar.py:37` pins `compute_next_due("every: 60s", last_run=1_000_000.0) == 1_000_060.0`), so `int(due_at.timestamp())` is an AttributeError. Relatedly, lane 5b's preallocated id has a silent-failure gotcha: `AgentSession.__init__` pops the kwarg at `models/agent_session.py:876-877` (`kwargs.pop("agent_session_id")  # AutoKeyField, ignore`), so a binding passed under that name is dropped without raising and the row gets a fresh id. | pending | Add `due_epoch: float` to `run_reflection` and `_enqueue_agent_reflection`, populate it from `compute_next_due(entry.schedule, last_run=state.ran_at)` at the `:635` dispatch site, and use `idempotency_key=f"reflection:{entry.name}:{int(due_epoch)}"` with no `.timestamp()` call. Pass the preallocated id as `id=`, never `agent_session_id=`: Popoto's `AutoFieldMixin` accepts an explicit key value as a constructor override of the generated default, but `models/agent_session.py:876-877` strips `agent_session_id` first. Add a test asserting the created row's `id` equals the value bound into `enqueue:idem:{key}`. |
| CONCERN | Scope & Value | The seam's blast radius is understated by roughly 5x and its caller list is wrong in both directions. 23 test files under `tests/` reference `_push_agent_session`; §Test Impact names four, and two of those (`tests/unit/test_agent_session_scheduler_kill.py`, `tests/integration/test_session_spawning.py`) contain zero references to it. Of the five named production callers only three are real — `enqueue_agent_session` (`agent/agent_session_queue.py:1712`), `tools/valor_session.py:760`, `agent/reflection_scheduler.py:754`. `retry_agent_session` (`agent/agent_session_queue.py:731`) calls `AgentSession.create(**fields)` directly and never touches the seam, and `tools/agent_session_scheduler.py` contains no reference at all. | pending | The breaking pattern is `depth = await _push_agent_session(...)` at `agent/agent_session_queue.py:1712`; once that unpacks a tuple, every test patching the seam with an `async def fake_push(**kwargs)` returning a bare `int` raises `TypeError: cannot unpack non-sequence int`. Enumerate with `grep -rln "_push_agent_session" tests/` (23 files today) and check each `fake_push` return; `tests/unit/test_valor_session_cli.py:280` and `:379` return a literal `1`. `tests/integration/test_bridge_routing.py:162-164` additionally asserts on `inspect.signature(_push_agent_session)` and must be updated for the two new kwargs. |
| CONCERN | Risk & Robustness | Risk 4's mitigation is enforced on the exact path it is meant to survive. `record()` is called from the relay's per-message failure branch, so a misbehaving relay makes every write pay a per-stage census, and "oldest aged first" additionally requires ordering by `first_seen`, which under Popoto means hydrating the stage's rows rather than reading a set cardinality. Under the flood the mitigation is written for, the mitigation becomes the bottleneck. | pending | Enforce the cap with an O(1) counter and an O(log n) eviction index instead of a row census: `HINCRBY {project}:dead_letters:count {stage} 1` and `ZADD {project}:dead_letters:{stage} <first_seen_epoch> <letter_id>` through `utils.redis_client.text_redis()`, then evict via `ZRANGE ... 0 <n-10000>` plus an ORM delete of each `DeadLetter`. Never a raw Redis delete on the Popoto row — `.claude/hooks/validators/validate_no_raw_redis_delete.py` blocks it; the sorted set is the index, not the row store. |
| CONCERN | Risk & Robustness | Five builders run concurrently over one checkout with no declared file-ownership split, and three of them write the same two files. `models/session_lifecycle.py` is edited by lane 1 (the `finalize_session` enqueue swap), lane 2 (`finalize_session` dead-letter writes for `session_recovery_cap` / `session_init_hang`) and lane 4 (`claim_pending_run` fail-closed). `agent/agent_session_queue.py` is edited by lane 2 (the corrupted-pop reaper), lane 3 (`_session_notify_listener` parse) and lane 5b (the seam). `scripts/update/migrations.py`, `scripts/update/reflection_register.py` and `scripts/update/run.py` are each edited by lanes 1 and 2. | pending | Add a file-ownership table to §Team Orchestration naming exactly one owner per contended file, or set `Parallel: false` for the lanes that share one. Cheapest split that removes all three collisions: lane 2 takes sole ownership of every `models/session_lifecycle.py` and `agent/agent_session_queue.py` edit that is not the seam (it already touches both) plus both `scripts/update/` registrations, lane 1 hands it the one-line `enqueue("memory_extraction", ...)` swap, lane 4 hands it the `claim_pending_run` change, and lane 5b stays sole owner of `_push_agent_session` and its callers. |
| NIT | Structural | The plan has no `## Critique Results` section; 26 of 30 plans in `docs/plans/` carry one, and the repo's critique finalize step writes the findings table there. This pass created the section. | pending | |
| NIT | Structural | §Race Conditions is numbered 1, 2, 3, 5, 4 — Race 5 is rendered before Race 4. | pending | |
| NIT | Structural | Tasks 6 (`validate-lanes`), 7 (`file-children`), 8 (`document-feature`) and 9 (`validate-all`) carry no `Validates:` field, unlike tasks 1-5. | pending | |
| NIT | Structural | §Test Impact asks `tests/unit/test_agentsession_index_guard_generalized.py` to "enumerate `SideEffectJob`", but that file is AgentSession-scoped: it exercises `AgentSession.repair_indexes()` and asserts an exact `IndexedField` set for `AgentSession` that a new model does not change. | pending | |
| NIT | Scope & Value | §Agent Integration introduces a new Python tool module (`python -m tools.dead_letters replay --stage X`) without stating its MCP disposition, which this repo's critique addendum lists as that section's job. | pending | |


## Decisions

Answered by the owner on 2026-09-06 via `/ask-me`:

1. **Two models, not one.** `SideEffectJob` is live state with a next-attempt time; `DeadLetter` is terminal with a payload snapshot. Taken as the default (reversible trivia, not asked).
2. **No break-glass on the fail-closed run claim.** Fail-closed is the only behavior; a prolonged Redis degradation stalls pickup rather than risking a duplicate `claude -p` on one worktree.
3. **#3183 owns the `_push_agent_session` seam.** Lane 5b lands `idempotency_key`, `status`, and the `tuple[int, str]` return in this build; #3177 Gap C consumes the shipped primitive.

## Open Questions

None.
