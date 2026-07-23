# Popoto Index Hygiene

Automated cleanup of orphaned Popoto index entries and migration of raw Redis operations to Popoto models.

## Problem

When AgentSession records expire (via TTL), crash, or are deleted without proper cleanup, their index entries remain as orphans pointing to non-existent Redis hashes. These orphans cause repeated `"one or more redis keys points to missing objects"` warnings on every Popoto query. Additionally, two modules in `agent/` used raw `import redis` connections instead of Popoto models.

## Solution

### TeammateMetrics Popoto Model

`models/teammate_metrics.py` replaces raw Redis counters in `agent/teammate_metrics.py`. Uses a single-instance pattern: one record keyed by `"global"` stores all classification counters (IntField) and response time lists (ListField, capped at 1000 entries). The public API (`record_classification`, `record_response_time`, `get_stats`) is preserved unchanged.

### AgentSession Meta.ttl

`AgentSession` now has `class Meta: ttl = 7776000` (90 days), matching the existing `cleanup_expired(max_age_days=90)` threshold. Popoto resets TTL on every `save()` call, so active sessions never expire -- only truly abandoned sessions are cleaned up automatically at the Redis level.

### Diagnostic Refactor

`_diagnose_missing_session()` in `agent/agent_session_queue.py` no longer uses raw `r.keys()` / `r.ttl()` / `r.exists()`. Instead it uses:
- `POPOTO_REDIS_DB.exists()` for targeted hash existence checks
- `AgentSession.query.filter()` for Popoto-native lookups

### Worker Startup (All-Model Rebuild, Excluding AgentSession)

Worker startup calls `run_cleanup()` from `scripts/popoto_index_cleanup` to rebuild indexes for all Popoto models **except `AgentSession`**. This runs as Step 1 of the startup sequence, before corrupted session cleanup and recovery. The total time is logged for monitoring.

`AgentSession` is excluded (issue #2207) because `run_cleanup()`'s generic per-model loop calls `Model.rebuild_indexes()` directly, which has no identity-less guard -- see [A1 Rebuild Guard](#a1-rebuild-guard-identity-less-phantom-re-inflation) below for why that matters and why AgentSession needs the guarded `repair_indexes()` path instead.

### Cleanup Reflection

`scripts/popoto_index_cleanup.py` provides a `run_cleanup()` function registered as the `redis-index-cleanup` reflection in `config/reflections.yaml`. The `ReflectionScheduler` dispatches this daily from its own out-of-process subprocess (`python -m reflections`) — see [Reflection Scheduler Subprocess](reflection-scheduler-subprocess.md). Like the worker-startup call, this sweep also excludes `AgentSession`.

### How `run_cleanup()` Works

1. Iterates all Popoto models from `models/__init__.__all__`, deduped and filtered by class `__name__` (not the `__all__` export string -- an alias like `AgentSession` for `AgentSession` cannot smuggle a guarded model past the exclusion checks)
2. Skips models in `_SCHEDULER_STATE_MODELS` (live `get_or_create`-per-tick models, see Concurrency Safety) and `_GUARDED_ELSEWHERE` (models whose index hygiene is handled by their own guarded rebuild path -- currently just `AgentSession`)
3. For each remaining model, counts orphaned index entries (dry-run scan) and captures a `keyspace_before` SCARD snapshot of the model's class-set index
4. Runs `Model.rebuild_indexes()` in a daemon thread with a wall-clock timeout (see [Step 1 Un-Wedge](#step-1-un-wedge-daemon-thread--join-timeout) below), then captures `keyspace_after` and computes `keyspace_delta = keyspace_after - keyspace_before`
5. Logs per-model orphan counts found/cleaned and the keyspace delta

Each model is processed independently -- one model failure does not abort the sweep. The SCAN-based `rebuild_indexes()` is safe to run concurrently with normal operations for every model still in scope.

### Cleanup Paths

| Path | Trigger | Scope |
|------|---------|-------|
| Worker startup | `python -m worker` | All models except `AgentSession` via `run_cleanup()` |
| ReflectionScheduler | Reflection subprocess tick (daily, `python -m reflections`) | All models except `AgentSession` via `run_cleanup()` |
| Worker Step 2 | `python -m worker` (post-Step-1) | `AgentSession` only, via the guarded `AgentSession.repair_indexes()` |
| Hourly `agent-session-cleanup` reflection | Worker-internal hourly tick (`agent/session_health.py`) | `AgentSession` only, via the guarded `AgentSession.repair_indexes()` |

## Concurrency Safety

`rebuild_indexes()` uses Redis SCAN (cursor-based, non-blocking) and only adds/removes index entries to match actual hash existence. Concurrent creates and deletes are safe and self-correcting -- any inconsistency introduced by a concurrent operation is fixed on the next run.

**Exception -- live `get_or_create`-per-tick models.** `rebuild_indexes()` *deletes* a model's class-set and KeyField index sets before reconstructing them. During that window, `Model.query.filter(key=...)` returns empty even though the backing hash still exists. A model whose hot path is `get_or_create(name=...)` on a tight loop will therefore spawn a **fresh duplicate record** (e.g. `Reflection.ran_at=None`) if a tick lands inside the window. For `every:`-scheduled reflections a blank record reads as "never run" and fires every tick -- the daily-digest burst-fire bug. Such models are listed in `_SCHEDULER_STATE_MODELS` and skipped by `_get_all_models()`; they are small and continuously indexed by their own `save()` hooks, so a periodic destructive rebuild buys nothing. `is_reflection_due()` adds a second, trigger-agnostic guard: when `ran_at` is lost it recovers the true last-run from `ReflectionRun` history (never rebuilt -- not in `models.__all__`) so a blank record cannot re-fire.

**`AgentSession` operator/dispatch read-path retry (issue #1720).** For `AgentSession` specifically, the measured class-set-empty window during `repair_indexes()` / `rebuild_indexes()` is p99=651ms. The two operator/dispatch reader sites — `tools/valor_session.py::_find_session` and `tools/sdlc_stage_query.py::_find_session_by_id` — apply a bounded 5×200ms retry: on empty result, each site re-reads after 200ms (up to 5 attempts, total max 1000ms) before falling through to the absent-session fallback. This eliminates transient `Session not found` errors at `valor-session status` and SDLC stage dispatch during the hourly `agent-session-cleanup` reflection tick. Internal worker paths (recovery, steering delivery) are excluded — they handle `None` gracefully and latency matters there. See [Session Lifecycle § Index-Rebuild Race and Read-Path Retry](session-lifecycle.md#index-rebuild-race-and-read-path-retry-issue-1720) for the root-cause analysis and spike measurements.

## A1 Rebuild Guard (Identity-Less Phantom Re-Inflation)

**The bug (issue #2101, generalized #2207).** Popoto's `rebuild_indexes()` `scan_iter`s every `AgentSession:*` hash and runs `field.on_save` for EVERY field in a generic loop. Any identity-less / near-empty hash (no `session_id` -- e.g. from a partially-written or corrupted record) decodes SOME default value for every `IndexedField` -- `status` defaults to `"pending"`, and the same applies to `task_type`, `claude_session_uuid`, and `claude_pid`. Each of those decoded values gets re-SADDed into that field's `$IndexF:AgentSession:<field>:<value>` index set on every rebuild, growing forever. `query.filter(...)` then drops these phantom entries via `_filter_hydrated_sessions` (no `session_id`), so the ORM count stays 0 while the raw index `SCARD` climbs unbounded -- this unbounded growth was the mechanism behind the ~7.4M-key Redis flood of 2026-07-22 (#2207).

**The guard.** `AgentSession.repair_indexes()` (`models/agent_session.py`) installs a transient shim on **every** `IndexedField`'s `on_save`, enumerated at runtime via `isinstance(f, IndexedField)` over `cls._meta.fields` -- never a hardcoded field list, so a future new `IndexedField` is automatically covered. Each shim skips the index SADD for identity-less records (rejected by `_filter_hydrated_sessions`, the canonical identity check) and delegates every healthy record to popoto's original `on_save`. The shim is scoped to the `rebuild_indexes()` call only -- normal live `AgentSession(...).save()` stays unguarded, so a legitimate brand-new session is still indexed.

**Install-inside-`try` invariant.** All per-field shims are installed *inside* the `try` block, not before it. If installing a later field's shim raises, the `finally` still restores every field enumerated up to that point from the full field list (not "fields observed installed" -- each pop is a safe no-op for a field whose shim never got installed). This matters because a shim install failure that leaked an uncleaned shim would silently corrupt normal live `save()` behavior for every future session write on that field, not just during the rebuild.

**Non-reentrant lock.** `repair_indexes()` is not safe to run concurrently with itself -- concurrent shim installs on the same field would clobber each other's captured "original" `on_save`. A per-class `threading.Lock()` (`cls._repair_lock`, lazily created once per class) guards the entire install-rebuild-restore sequence. A concurrent caller that loses the race (`acquire(blocking=False)` fails) logs a WARNING and returns a no-op `(0, 0)` rather than racing the shims -- callers unpacking the 2-tuple see "nothing changed" instead of corrupting state. A belt-and-braces assertion inside the install loop additionally raises `RuntimeError` if a shim is ever found already installed on a field -- unreachable given the lock, but converts any lock-bypass bug into a loud failure instead of silent index corruption.

**Quarantine counter.** `cls._last_quarantined_identityless` sums the skip count across all guarded fields for the most recent pass and is logged at WARNING when nonzero. It is also persisted to a plain (non-Popoto-managed) Redis key (`_LAST_QUARANTINED_IDENTITYLESS_REDIS_KEY` in `models/agent_session.py`, TTL-bounded) so the doctor check below -- which runs in its own fresh process -- can see it. The public `(stale_count, rebuilt_count)` 2-tuple return signature is unchanged.

**Why `AgentSession` is excluded from `run_cleanup()`'s generic sweep.** `run_cleanup()`'s per-model loop calls the model's raw `Model.rebuild_indexes()` -- no identity-less guard. Running it against `AgentSession` would re-trigger exactly the re-inflation bug the guard exists to stop. `AgentSession` is therefore listed in `scripts/popoto_index_cleanup.py`'s `_GUARDED_ELSEWHERE` frozenset (keyed by class `__name__`, not the `models.__all__` export string, so an alias can't smuggle it past the exclusion) and is instead covered exclusively by the guarded `AgentSession.repair_indexes()` call in worker Step 2 and the hourly `agent-session-cleanup` reflection.

## Step 1 Un-Wedge (Daemon Thread + Join-Timeout)

`run_cleanup()`'s per-model rebuild previously ran inside `with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor: future.result(timeout=...)`. `ThreadPoolExecutor`'s worker thread is **non-daemon** and is joined a second time at interpreter exit via `concurrent.futures.thread._python_exit` -- so even after `future.result(timeout=...)` raised a `TimeoutError` and the sweep logically moved on, exiting the `with` block still called `executor.shutdown(wait=True)`, which blocked forever joining a rebuild thread that was still running (e.g. hung on an `EmbeddingField` model's Redis SCAN). That mismatch -- an illusory per-call timeout paired with a real blocking join -- caused a genuine 8-hour zero-heartbeat worker wedge (#2207).

The fix (`_run_rebuild_with_timeout()` in `scripts/popoto_index_cleanup.py`) replaces the pool executor with a bare `threading.Thread(target=..., daemon=True)` + `thread.join(timeout=_REBUILD_TIMEOUT_SECONDS)`. On timeout, the thread is **abandoned** -- it keeps running in the background but, being a daemon thread, can never block interpreter shutdown again. `_REBUILD_TIMEOUT_SECONDS` is `int(os.environ.get("POPOTO_INDEX_CLEANUP_REBUILD_TIMEOUT_SECONDS", 30))` -- a named, env-overridable constant, flagged provisional/tunable.

## Keyspace Observability

`run_cleanup()`'s summary dict now records `keyspace_before` / `keyspace_after` / `keyspace_delta` per model -- a cheap `SCARD` on the model's canonical class-set key (`model_class._meta.db_class_set_key.redis_key`), captured immediately before and after each rebuild attempt (including on timeout/error paths, so inflation is visible even when the rebuild itself doesn't complete). This makes phantom-record inflation visible in the worker startup log and reflection output without an expensive full scan, and without changing the existing summary dict's contract (the new keys are additive).

## Doctor Check Wiring

The `agentsession-index-drift` doctor check (`tools/doctor.py::_check_agentsession_index_drift`, see [AgentSession Index-Drift Detection](agentsession-index-drift-detection.md)) is a **detect-only** diagnostic -- it never calls `repair_indexes()` itself, so `AgentSession._last_quarantined_identityless` (an in-memory class attribute) would always read `0` in a freshly-started `python -m tools.doctor` process even right after a worker-side repair quarantined a large batch of phantom hashes.

`_recent_quarantine_suffix()` closes that gap by reading the persisted Redis key set by `repair_indexes()` (see A1 Rebuild Guard above) and appending an informational note -- e.g. `(most recent repair_indexes() quarantined 3 identity-less hash re-add(s))` -- to the check's message when nonzero. This is purely informational: it never gates the check's pass/fail verdict, since a nonzero quarantine count means the guard is working correctly, not that anything is currently broken. If the key is absent, unreadable, or the count is `0`, the suffix is an empty string and the message is unchanged from before.

## Key Files

| File | Purpose |
|------|---------|
| `models/teammate_metrics.py` | TeammateMetrics Popoto model |
| `agent/teammate_metrics.py` | Refactored metrics module (uses Popoto) |
| `models/agent_session.py` | AgentSession with Meta.ttl, the A1 rebuild guard (`repair_indexes()`), and the persisted quarantine-count Redis key |
| `agent/agent_session_queue.py` | Refactored diagnostic fallback |
| `worker/__main__.py` | Worker startup using `run_cleanup()` for all-model rebuild (Step 1, excludes `AgentSession`) and the guarded `AgentSession.repair_indexes()` (Step 2) |
| `scripts/popoto_index_cleanup.py` | Cleanup function (`run_cleanup()`), model discovery (`_get_all_models()`), `_GUARDED_ELSEWHERE` exclusion set, and the daemon-thread rebuild timeout (`_run_rebuild_with_timeout()`) |
| `tools/doctor.py` | `agentsession-index-drift` check, `_recent_quarantine_suffix()` |
| `config/reflections.yaml` | Reflection registry entry for `ReflectionScheduler` |
| `monitoring/sentry_config.py` | `drop_orphan_noise()` Sentry `before_send` filter (see Sentry Orphan-Noise Filter) |

## Inline Orphan Prevention (Defensive srem)

`rebuild_indexes()` is a batch repair run — it catches orphans after the fact. A complementary inline mechanism in `finalize_session()` prevents a specific class of orphans at creation time:

When stale-object full saves clobber a session's status in Redis (e.g., a session appearing in the `pending` index after being killed), the `finalize_session()` call that ends the session performs a **defensive `srem`** across ALL non-target status index sets immediately after the terminal save. This removes any stale entries left by prior writes.

The defensive `srem` is non-fatal (wrapped in try/except) and depends on three Popoto internals that must be re-verified on Popoto upgrade: `DB_key`, `POPOTO_REDIS_DB.srem()`, and `get_special_use_field_db_key`. See `models/session_lifecycle.py` (`finalize_session`) for implementation detail. The broader fix (preventing stale-object saves in the first place) is documented in [Session Lifecycle](session-lifecycle.md#layer-1b-partial-saves-on-companion-field-methods-950).

## Verification

After the cleanup reflection runs, `grep -rn "import redis" agent/` should return zero hits, and bridge logs should show reduced `"one or more redis keys points to missing objects"` warnings.

## Sentry Orphan-Noise Filter

The cleanup infrastructure above **reduces** but cannot **eliminate** transient orphan-index entries: the orphan lifecycle is inherent to Popoto + TTL (Redis SETs have no per-member TTL, so a hash expiry always leaves a ghost SET member until the next sweep). Popoto's `Query` logger emits `"one or more redis keys points to missing objects. Debug with Model.query.keys(clean=True)"` at `error` level on **every** query that touches such a ghost — and the worker polls `AgentSession.query.all()` in a tight loop. Sentry's default `LoggingIntegration` captures each of these as an event, which accumulated **68k+ benign events** on Sentry issue `VALOR-S` (issue #1835).

The churn is benign-transient: the `if redis_hash` guard in Popoto's `get_many_objects` already silently skips ghost hashes, so **no stale data is ever returned**. Three prior orphan-reduction fixes (#860, #1459, #1874) each lowered the volume but none removed the noise, because the error fires on every hit, not once per orphan. Rather than chase the last orphan, the noise is filtered at the Sentry layer:

- **`drop_orphan_noise(event, hint)` in `monitoring/sentry_config.py`** is a `before_send` hook that returns `None` (drops the event) when the event's logged message contains the orphan substring. It checks `logentry.formatted`, `logentry.message`, and the top-level `message` field, and wraps the match in try/except so a filter bug can never suppress a real error.
- **The worker** (`worker/__main__.py`) — the primary emitter — passes `before_send=drop_orphan_noise` to `configure_sentry("worker", ...)` (previously `None`).
- **The bridge** (`bridge/telegram_bridge.py`) composes it: `_sentry_before_send` runs the hibernation check first, then delegates to `drop_orphan_noise`.

This filters the **Sentry** noise only — the diagnostic still appears in bridge/worker logs (a `logging.Filter` on the `POPOTO.Query` logger was rejected because it would hide the diagnostic from logs entirely). Modifying Popoto's source to downgrade the log level was also rejected: Popoto is a pip-installed dependency, and monkey-patching would break on upgrade. The `before_send` layer intercepts after Popoto logs but before Sentry captures.

## Related: Disk-Side Embedding Orphan Cleanup

`redis-index-cleanup` reconciles **Redis-side** orphans (index entries pointing at missing hashes). A parallel mechanism reconciles **disk-side** orphans (`.npy` embedding files in `~/.popoto/content/.embeddings/Memory/` without a live Memory record):

- **Reflection:** `embedding-orphan-sweep` (daily, dry-run by default) calls `EmbeddingField.garbage_collect(Memory)` + `EmbeddingField.sweep_stale_tempfiles(Memory)` from Popoto >= 1.6.0. Set `EMBEDDING_ORPHAN_SWEEP_APPLY=true` to enable deletion. Implemented in `reflections/memory/embedding_orphan_sweep.py::run`.
- **Read-only count:** `_count_disk_orphans(model_class)` in `scripts/popoto_index_cleanup.py` walks the embedding directory and counts orphans via the shared `popoto.fields.embedding_field._compute_expected_keep` helper. Surfaced as `disk_orphan_count` in `python -m tools.memory_search status --deep`.
- **One-shot reconciliation:** `python scripts/embedding_orphan_reconcile.py --dry-run` then `--apply`. Includes a positive-assertion safety check (refuses to apply if to-delete intersects expected-keep) and a pre-flight regression guard (refuses to apply if `$Class:Memory` is empty).
- **Required marker:** `Memory.__embedding_garbage_collect__ = True` opts the model into garbage_collect; without it Popoto's helper is a no-op (defensive default for any future model that attaches `EmbeddingField`).

A prior fix (issue #1214) also corrected `_count_orphans` to read the canonical `model_class._meta.db_class_set_key.redis_key` (= `$Class:{Name}`) instead of the older `{Name}:_all` key, which is empty in production. See [Subconscious Memory § Embedding-File Lifecycle](subconscious-memory.md#embedding-file-lifecycle) for the full lifecycle.
