# Popoto Index Hygiene

Automated cleanup of orphaned Popoto index entries, plus Popoto-model backing for metrics and diagnostics.

## TeammateMetrics Popoto Model

`models/teammate_metrics.py` backs the classification counters in `agent/teammate_metrics.py`. It uses a single-instance pattern: one record keyed by `"global"` stores all classification counters (IntField) and response time lists (ListField, capped at 1000 entries). The public API (`record_classification`, `record_response_time`, `get_stats`) is unchanged.

## AgentSession Meta.ttl

`AgentSession` declares `Meta.ttl = int(settings.timeouts.agent_session_retain_ttl_s)` (30 days). The retention policy is that session rows live until explicitly deleted: `cleanup_corrupted_agent_sessions` calls `AgentSession.refresh_ttl()` on every healthy row it visits, so the TTL is a backstop for rows the sweep cannot reach. See [AgentSession Liveness Field Authorship](agent-session-liveness-authorship.md#metattl-the-keepalive-is-the-retention-policy).

## Diagnostic Refactor

`_diagnose_missing_session()` in `agent/agent_session_queue.py` uses:
- `POPOTO_REDIS_DB.exists()` for targeted hash existence checks
- `AgentSession.query.filter()` for Popoto-native lookups

## Worker Startup (All-Model Rebuild, Excluding AgentSession)

Worker startup calls `run_cleanup()` from `scripts/popoto_index_cleanup` to rebuild indexes for all Popoto models **except `AgentSession`**. This runs as Step 1 of the startup sequence, before corrupted session cleanup and recovery. The total time is logged for monitoring.

`AgentSession` is excluded because `run_cleanup()`'s generic per-model loop calls `Model.rebuild_indexes()` directly, which has no identity-less guard — see [A1 Rebuild Guard](#a1-rebuild-guard-identity-less-phantom-re-inflation) below for why AgentSession needs the guarded `repair_indexes()` path instead.

`Model.rebuild_indexes()` is wrapped by the popoto version-floor interlock for every model, so the generic loop cannot destroy an index under a below-floor popoto. What it still lacks is the **identity-less** guard, which is AgentSession-specific. The two guards are independent and neither substitutes for the other — see [Popoto Version-Floor Guard](popoto-version-floor-guard.md).

## Cleanup Reflection

`scripts/popoto_index_cleanup.py` provides a `run_cleanup()` function registered as the `redis-index-cleanup` reflection in `config/reflections.yaml`. The `ReflectionScheduler` dispatches this daily from its own out-of-process subprocess (`python -m reflections`) — see [Reflection Scheduler Subprocess](reflection-scheduler-subprocess.md). Like the worker-startup call, this sweep also excludes `AgentSession`.

## How `run_cleanup()` Works

1. Iterates all Popoto models from `models/__init__.__all__`, deduped and filtered by class `__name__` (not the `__all__` export string — an alias cannot smuggle a guarded model past the exclusion checks)
2. Skips models in `_SCHEDULER_STATE_MODELS` (live `get_or_create`-per-tick models, see Concurrency Safety) and `_GUARDED_ELSEWHERE` (`AgentSession`, `Room`, `Job` — models whose index hygiene is handled by their own guarded `repair_indexes()` path)
3. For each remaining model, counts orphaned index entries (dry-run scan) and captures a `keyspace_before` SCARD snapshot of the model's class-set index
4. Runs `Model.rebuild_indexes()` in a daemon thread with a wall-clock timeout (see [Step 1 Un-Wedge](#step-1-un-wedge-daemon-thread--join-timeout) below), then captures `keyspace_after` and computes `keyspace_delta = keyspace_after - keyspace_before`
5. Logs per-model orphan counts found/cleaned and the keyspace delta
6. Finally runs `_run_guarded_repairs()`, which invokes the guarded `repair_indexes()` of each `_GUARDED_ELSEWHERE` model that has no other caller — `Room` and `Job`. Results land under the summary's `guarded_repairs` key.

`Job.repair_indexes()` carries one extra step the other guarded paths do not: it runs `Job.renormalize_last_active_scores()` immediately after the rebuild. `rebuild_indexes()` re-scores each Job from a naive-decoded `last_active_at`, bypassing the UTC reattach in `Job.save()`, so on a non-UTC host the rebuild itself re-skews the `last_active_at` sorted-set scores that `Job.recent_for_room`'s bounded read depends on. The renormalization pass walks the class set with `SSCAN` and re-chunks it to `Job._RENORMALIZE_BATCH_SIZE` rows, costing two pipelines per chunk (`HMGET` of three fields decoded by popoto's hash decoder, then `ZSCORE`) with no Job hydrated and no per-row round trip; only a repair writes, via `save(update_fields=["last_active_at"])`. It is fail-open at three grains (a bad row, a failed pipeline, a failed `SSCAN` each log and the sweep continues or returns what it has) and does not change `repair_indexes()`'s `(quarantined, rebuilt)` return arity. See [Durability Model](durability-model.md#last_active_at-score-purity-jobsave).

Each model is processed independently — one model failure does not abort the sweep. The SCAN-based `rebuild_indexes()` is safe to run concurrently with normal operations for every model still in scope.

## The two-part `_GUARDED_ELSEWHERE` contract

Listing a model in `_GUARDED_ELSEWHERE` is only half of registering it. The frozenset removes the model from the generic sweep; `_run_guarded_repairs()`'s `guarded_repairs` registry is what puts its own repair path back on the schedule. A model in the first and absent from the second receives **no index hygiene at all**. `Job` is registered in the `guarded_repairs` registry; `Room` is too.

`AgentSession` is the one deliberate absence from the registry, and it is safe because it has two other production callers (worker Step 2 and the hourly `agent-session-cleanup` reflection). Any new entry in the frozenset needs either a registry entry here or a named caller elsewhere.

## Cleanup Paths

| Path | Trigger | Scope |
|------|---------|-------|
| Worker startup | `python -m worker` | All models except `_GUARDED_ELSEWHERE` via `run_cleanup()`, plus `Room` and `Job` via `_run_guarded_repairs()` |
| ReflectionScheduler | Reflection subprocess tick (daily, `python -m reflections`) | All models except `_GUARDED_ELSEWHERE` via `run_cleanup()`, plus `Room` and `Job` via `_run_guarded_repairs()` |
| Worker Step 2 | `python -m worker` (post-Step-1) | `AgentSession` only, via the guarded `AgentSession.repair_indexes()` |
| Hourly `agent-session-cleanup` reflection | Worker-internal hourly tick (`agent/session_health.py`) | `AgentSession` only, via the guarded `AgentSession.repair_indexes()` |

## Concurrency Safety

`rebuild_indexes()` uses Redis SCAN (cursor-based, non-blocking) and only adds/removes index entries to match actual hash existence. Concurrent creates and deletes are safe and self-correcting — any inconsistency introduced by a concurrent operation is fixed on the next run.

**Exception — live `get_or_create`-per-tick models.** `rebuild_indexes()` *deletes* a model's class-set and KeyField index sets before reconstructing them. During that window, `Model.query.filter(key=...)` returns empty even though the backing hash still exists. A model whose hot path is `get_or_create(name=...)` on a tight loop will therefore spawn a **fresh duplicate record** (e.g. `Reflection.ran_at=None`) if a tick lands inside the window. For `every:`-scheduled reflections a blank record reads as "never run" and fires every tick — the daily-digest burst-fire bug. Such models are listed in `_SCHEDULER_STATE_MODELS` and skipped by `_get_all_models()`; they are small and continuously indexed by their own `save()` hooks, so a periodic destructive rebuild buys nothing. `is_reflection_due()` adds a second, trigger-agnostic guard: when `ran_at` is lost it recovers the true last-run from `ReflectionRun` history (never rebuilt — not in `models.__all__`) so a blank record cannot re-fire.

**The class-set-empty window scales with the keyspace.** `rebuild_indexes()` deletes `$Class:<Model>` outright at the top and only re-adds members at each `batch_size=1000` pipeline flush. The window is therefore not a narrow race at a batch boundary: it is essentially the whole duration of the rebuild, and that duration grows with the row count. Measured by driving the rebuild against a concurrent `query.all()` poller on an isolated Redis:

| AgentSession rows | rebuild wall-clock | concurrent scans returning **zero** |
|---|---|---|
| 150 | 0.24s | 96.5% |
| 1000 | 1.17s | 99.8% |
| 4006 | 22.33s | 91.8% |

The record hashes are untouched throughout — only index keys are deleted — so a raw `AgentSession:*` SCAN still sees every row while `query.all()` sees none. That asymmetry is the discriminator the strip migrations use to tell a blinded scan from a genuinely empty keyspace (see [Migration Guards](#migration-guards-blinded-scans-and-load-bearing-rebuilds)).

**`AgentSession` operator/dispatch read-path retry.** The two operator/dispatch reader sites — `tools/valor_session.py::_find_session` and `tools/sdlc_stage_query.py::_find_session_by_id` — retry an empty class-set read via the shared `tools/class_set_retry.py` helper before falling through to the absent-session fallback. This eliminates transient `Session not found` errors at `valor-session status` and SDLC stage dispatch during the hourly `agent-session-cleanup` reflection tick. Internal worker paths (recovery, steering delivery) are excluded — they handle `None` gracefully and latency matters there. See [Session Lifecycle § Index-Rebuild Race and Read-Path Retry](session-lifecycle.md#index-rebuild-race-and-read-path-retry) for the root-cause analysis.

The bound is a **wall-clock budget**, `TIMEOUTS__CLASS_SET_RETRY_BUDGET_S` (default 3s), not an attempt count. A budget lets a large-keyspace host raise its own ceiling with no code change.

**Exhaustion is logged at WARNING.** A budget-exhausted read returns the same `None` as a genuine miss, so the caller reports "session not found" for a live session. `log_class_set_exhaustion` names the attempt count, the budget, and the env key to raise. Where a direct-key fallback is available (`_find_session`'s `get_by_id`, which never touches the class set), a hit after exhaustion **proves** the class set was mid-rebuild and the message says so; where there is no such fallback (`_find_session_by_id` takes a `session_id`, not the primary key) the message states plainly that stale-index and genuine-absence are indistinguishable rather than asserting either.

A UUID-form argument to `_find_session` skips the retry and goes straight to `get_by_id`. An empty class-set query is the *expected* outcome there, so retrying would spend the whole budget on every UUID lookup and then report a rebuild that never happened.

## A1 Rebuild Guard (Identity-Less Phantom Re-Inflation)

**The bug.** Popoto's `rebuild_indexes()` `scan_iter`s every `AgentSession:*` hash and runs `field.on_save` for EVERY field in a generic loop. Any identity-less / near-empty hash (no `session_id` — e.g. from a partially-written or corrupted record) decodes SOME default value for every `IndexedField` — `status` defaults to `"pending"`, and the same applies to `task_type` and `claude_session_uuid`. (`exec_pid`, the fenced execution pid, is deliberately a plain field rather than an `IndexedField` precisely to avoid this unbounded pid-index cardinality — see [`docs/features/agent-session-fenced-execution-record.md`](agent-session-fenced-execution-record.md).) Each of those decoded values gets re-SADDed into that field's `$IndexF:AgentSession:<field>:<value>` index set on every rebuild, growing forever. `query.filter(...)` then drops these phantom entries via `_filter_hydrated_sessions` (no `session_id`), so the ORM count stays 0 while the raw index `SCARD` climbs unbounded.

**The guard.** `AgentSession.repair_indexes()` (`models/agent_session.py`) installs a transient shim on **every** `IndexedField`'s `on_save`, enumerated at runtime via `isinstance(f, IndexedField)` over `cls._meta.fields` — never a hardcoded field list, so a future new `IndexedField` is automatically covered. Each shim skips the index SADD for identity-less records (rejected by `_filter_hydrated_sessions`, the canonical identity check) and delegates every healthy record to popoto's original `on_save`. The shim is scoped to the `rebuild_indexes()` call only — normal live `AgentSession(...).save()` stays unguarded, so a legitimate brand-new session is still indexed.

**Install-inside-`try` invariant.** All per-field shims are installed *inside* the `try` block, not before it. If installing a later field's shim raises, the `finally` still restores every field enumerated up to that point from the full field list (not "fields observed installed" — each pop is a safe no-op for a field whose shim never got installed). This matters because a shim install failure that leaked an uncleaned shim would silently corrupt normal live `save()` behavior for every future session write on that field, not just during the rebuild.

**Non-reentrant lock.** `repair_indexes()` is not safe to run concurrently with itself — concurrent shim installs on the same field would clobber each other's captured "original" `on_save`. A per-class `threading.Lock()` (`cls._repair_lock`, lazily created once per class) guards the entire install-rebuild-restore sequence. A concurrent caller that loses the race (`acquire(blocking=False)` fails) logs a WARNING and returns a no-op `(0, 0)` rather than racing the shims — callers unpacking the 2-tuple see "nothing changed" instead of corrupting state. A belt-and-braces assertion inside the install loop additionally raises `RuntimeError` if a shim is ever found already installed on a field — unreachable given the lock, but converts any lock-bypass bug into a loud failure instead of silent index corruption.

**Quarantine counter.** `cls._last_quarantined_identityless` sums the skip count across all guarded fields for the most recent pass and is logged at WARNING when nonzero. It is also persisted to a plain (non-Popoto-managed) Redis key (`_LAST_QUARANTINED_IDENTITYLESS_REDIS_KEY` in `models/agent_session.py`, TTL-bounded) so the doctor check below — which runs in its own fresh process — can see it. The public `(stale_count, rebuilt_count)` 2-tuple return signature is unchanged.

**Why `AgentSession` is excluded from `run_cleanup()`'s generic sweep.** `run_cleanup()`'s per-model loop calls the model's raw `Model.rebuild_indexes()` — no identity-less guard. Running it against `AgentSession` would re-trigger the re-inflation bug the guard exists to stop. `AgentSession` is therefore listed in `scripts/popoto_index_cleanup.py`'s `_GUARDED_ELSEWHERE` frozenset (keyed by class `__name__`, not the `models.__all__` export string, so an alias can't smuggle it past the exclusion) and is instead covered exclusively by the guarded `AgentSession.repair_indexes()` call in worker Step 2 and the hourly `agent-session-cleanup` reflection.

## Step 1 Un-Wedge (Daemon Thread + Join-Timeout)

`_run_rebuild_with_timeout()` in `scripts/popoto_index_cleanup.py` runs each rebuild in a bare `threading.Thread(target=..., daemon=True)` + `thread.join(timeout=_REBUILD_TIMEOUT_SECONDS)`. On timeout, the thread is **abandoned** — it keeps running in the background but, being a daemon thread, can never block interpreter shutdown. `_REBUILD_TIMEOUT_SECONDS` is `int(os.environ.get("POPOTO_INDEX_CLEANUP_REBUILD_TIMEOUT_SECONDS", 30))` — a named, env-overridable constant.

## Keyspace Observability

`run_cleanup()`'s summary dict records `keyspace_before` / `keyspace_after` / `keyspace_delta` per model — a cheap `SCARD` on the model's canonical class-set key (`model_class._meta.db_class_set_key.redis_key`), captured immediately before and after each rebuild attempt (including on timeout/error paths, so inflation is visible even when the rebuild itself doesn't complete). This makes phantom-record inflation visible in the worker startup log and reflection output without an expensive full scan, and without changing the existing summary dict's contract (the new keys are additive).

## Doctor Check Wiring

The `agentsession-index-drift` doctor check (`tools/doctor.py::_check_agentsession_index_drift`, see [AgentSession Index-Drift Detection](agentsession-index-drift-detection.md)) is a **detect-only** diagnostic — it never calls `repair_indexes()` itself, so `AgentSession._last_quarantined_identityless` (an in-memory class attribute) would always read `0` in a freshly-started `python -m tools.doctor` process even right after a worker-side repair quarantined a large batch of phantom hashes.

`_recent_quarantine_suffix()` closes that gap by reading the persisted Redis key set by `repair_indexes()` (see A1 Rebuild Guard above) and appending an informational note — e.g. `(most recent repair_indexes() quarantined 3 identity-less hash re-add(s))` — to the check's message when nonzero. This is purely informational: it never gates the check's pass/fail verdict, since a nonzero quarantine count means the guard is working correctly, not that anything is currently broken. If the key is absent, unreadable, or the count is `0`, the suffix is an empty string and the message is unchanged.

## Migration Guards: Blinded Scans and Load-Bearing Rebuilds

Migrations run at `/update` Step 3.6, **before** the service restart, so they are genuinely concurrent with a live worker's index repair. Two distinct families of migration touch this machinery, and they need opposite treatment.

### Strip migrations — the scan is the vulnerable part

`scripts/_strip_migration.py` drives the three field-strip migrations. It scans with `AgentSession.query.all()`, which lands in the class-set window above and can return zero rows on a fully populated keyspace. A zero-record scan reported as success would record the migration permanently complete having stripped nothing.

The engine forks on a bounded, detection-only SCAN for raw `AgentSession:*` hashes:

| `query.all()` | raw hash SCAN | Diagnosis | Exit |
|---|---|---|---|
| 0 rows | 0 hashes, scan exhaustive | genuinely empty keyspace (fresh install) | **0** — nothing to strip, record complete |
| 0 rows | any hashes present | blinded by an index rebuild | **2** — refuse, retry next `/update` |
| 0 rows | scan truncated, or SCAN raised | unprovable | **2** — fail closed |

Only an *exhaustive* SCAN returning zero confirms emptiness. A truncated SCAN hit its iteration cap, so its count is a partial undercount and a zero from it proves nothing.

The strip migrations do **not** rebuild. Their per-record `delete()` + `save()` maintains indexes atomically, so their trailing `clean_indexes()` is a defensive orphan sweep, not a functional requirement.

### Rename migrations — reconstruction is load-bearing, so use the guarded path

Five migrations rename hash fields or Redis keys via raw Redis and then reconstruct the indexes:

| Script | Registry | What it writes raw |
|---|---|---|
| `scripts/migrate_agent_session_keyfield_rename.py` | live | renames the Redis key; `id`, `parent_agent_session_id` |
| `scripts/migrate_unify_parent_session_field.py` | live | `parent_agent_session_id` |
| `scripts/migrate_parent_session_field.py` | unregistered | `parent_session_id`, `role` |
| `scripts/migrate_session_type_pm_to_eng.py` | unregistered | renames the Redis key; `session_type` |
| `scripts/migrate_session_type_chat_to_pm.py` | unregistered | renames the Redis key; `session_type` |

Every one writes a **KeyField** value (`id`, `parent_agent_session_id`, `session_type`) outside the ORM, so no index entry is ever created for the new value. `clean_indexes()` — the substitute the strip family adopts — is **not** an option here: it is removal-only, so it would drop the stale pointers and never add the new ones. Verified directly, by raw-copying an encoded `parent_agent_session_id` onto a record's hash and then querying for it:

```
after raw byte copy   : record invisible to query.filter(parent_agent_session_id=...)
clean_indexes()       : removed 0 orphans, record STILL invisible
index rebuild         : record reachable again
```

Reconstruction is therefore mandatory. The choice that remains is *which* reconstruction, and all five use `AgentSession.repair_indexes()` rather than popoto's raw `rebuild_indexes()`. Both rebuild (repair calls rebuild internally), so both pay the class-set window measured above — that window is inherent to reconstructing the index at all. What the guarded path adds is everything that makes paying it survivable:

- **`assert_popoto_floor()` first.** A below-floor popoto cannot decode the index-pointer fields an at-or-above-floor popoto writes. The raw rebuild deletes every index *before* discovering that, so it destroys the index and rebuilds nothing. The assertion runs before any teardown.
- **`$IndexF` stale-pointer cleanup**, which the raw rebuild never enumerates.
- **The A1 identity-less shim**, so phantom hashes are not re-inflated into the indexes on the way through.

All five route through one helper, `scripts/_migration_index_repair.py::reconstruct_agent_session_indexes`, rather than each carrying its own copy of the call and its failure check.

The helper fails closed in both directions, because the renames have already landed by the time it runs. `repair_indexes()` raising — which is how `assert_popoto_floor()` surfaces — counts an error. So does a `(0, 0)` return, which is what `repair_indexes()` gives **without rebuilding** when its non-reentrant lock is held. Either way the migration exits non-zero and `/update` withholds the completion record.

Three things about that `(0, 0)` branch, because the obvious readings of it are wrong:

- **It cannot currently fire from these scripts.** The lock is a per-class `threading.Lock`, so it is per-*process*. Migrations run as their own subprocess and each `migrate()` is straight-line single-threaded, so nothing in that process can hold it. The check is defense-in-depth against an unreachable branch, not a live hazard being handled.
- **It is not the interlock the `/update` race would need.** Migrations at Step 3.6 are concurrent with a live worker's repair, but that race is *cross-process* and a `threading.Lock` does not span processes. Both sides take their own lock and rebuild. State converges (whichever rebuild finishes last does a full pass), so this is not a correctness break — but nothing here interlocks it, and a reader should not infer that it does.
- **If it did fire, an in-flight repair would not heal it.** `repair_indexes()` deletes every `$IndexF:AgentSession:*` key *before* the lock acquire, so a `(0, 0)` return leaves the field indexes torn down and not rebuilt, and a same-process in-flight repair is already past its own `$IndexF` phase. The actual healer is the worker's startup `repair_indexes()`, which `/update` triggers at the service restart immediately after Step 3.6.

Live exposure is small and worth stating precisely:

- Each call is gated on having actually migrated at least one record, so a **fresh install never fires it** — there are no legacy records to rename.
- The two registry entries are recorded complete on every machine checked, so they do not run again there. The other three are **not `MIGRATIONS` entries at all**, so `/update` never invokes them and no completion record exists or is needed; their inertness follows from being unregistered.

The exposed case is a machine holding un-migrated legacy records. If that stops being true at scale, the fix is to sequence these migrations before the worker starts, not to weaken the reconstruction.

## Key Files

| File | Purpose |
|------|---------|
| `scripts/_strip_migration.py` | Shared strip-migration engine: the zero-record fork, the SCAN discriminator (`agent_session_hash_count()`), and the exit-code contract |
| `scripts/_migration_index_repair.py` | Shared guarded index reconstruction for the five rename migrations (`reconstruct_agent_session_indexes()`): the `repair_indexes()` call and its fail-closed check, in one copy |
| `models/teammate_metrics.py` | TeammateMetrics Popoto model |
| `agent/teammate_metrics.py` | Metrics module backed by Popoto |
| `models/agent_session.py` | AgentSession with Meta.ttl, the A1 rebuild guard (`repair_indexes()`), and the persisted quarantine-count Redis key |
| `agent/agent_session_queue.py` | Diagnostic fallback |
| `worker/__main__.py` | Worker startup using `run_cleanup()` for all-model rebuild (Step 1, excludes `AgentSession`) and the guarded `AgentSession.repair_indexes()` (Step 2) |
| `scripts/popoto_index_cleanup.py` | Cleanup function (`run_cleanup()`), model discovery (`_get_all_models()`), `_GUARDED_ELSEWHERE` exclusion set, the `_run_guarded_repairs()` registry, and the daemon-thread rebuild timeout (`_run_rebuild_with_timeout()`) |
| `tools/doctor.py` | `agentsession-index-drift` check, `_recent_quarantine_suffix()` |
| `config/reflections.yaml` | Reflection registry entry for `ReflectionScheduler` |
| `monitoring/sentry_config.py` | `drop_orphan_noise()` Sentry `before_send` filter (see Sentry Orphan-Noise Filter) |

## Inline Orphan Prevention (Defensive srem)

`rebuild_indexes()` is a batch repair run — it catches orphans after the fact. A complementary inline mechanism in `finalize_session()` prevents a specific class of orphans at creation time:

When stale-object full saves clobber a session's status in Redis (e.g., a session appearing in the `pending` index after being killed), the `finalize_session()` call that ends the session performs a **defensive `srem`** across ALL non-target status index sets immediately after the terminal save. This removes any stale entries left by prior writes.

The defensive `srem` is non-fatal (wrapped in try/except) and depends on three Popoto internals that must be re-verified on Popoto upgrade: `DB_key`, `POPOTO_REDIS_DB.srem()`, and `get_special_use_field_db_key`. See `models/session_lifecycle.py` (`finalize_session`) for implementation detail.

## Verification

After the cleanup reflection runs, `grep -rn "import redis" agent/` should return zero hits, and bridge logs should show reduced `"one or more redis keys points to missing objects"` warnings.

## Sentry Orphan-Noise Filter

The cleanup infrastructure above **reduces** but cannot **eliminate** transient orphan-index entries: the orphan lifecycle is inherent to Popoto + TTL (Redis SETs have no per-member TTL, so a hash expiry always leaves a ghost SET member until the next sweep). Popoto's `Query` logger emits `"one or more redis keys points to missing objects. Debug with Model.query.keys(clean=True)"` at `error` level on **every** query that touches such a ghost — and the worker polls `AgentSession.query.all()` in a tight loop. Sentry's default `LoggingIntegration` captures each of these as an event, which accumulates benign events on a Sentry issue.

The churn is benign-transient: the `if redis_hash` guard in Popoto's `get_many_objects` already silently skips ghost hashes, so **no stale data is ever returned**. Rather than chase the last orphan, the noise is filtered at the Sentry layer:

- **`drop_orphan_noise(event, hint)` in `monitoring/sentry_config.py`** is a `before_send` hook that returns `None` (drops the event) when the event's logged message contains the orphan substring. It checks `logentry.formatted`, `logentry.message`, and the top-level `message` field, and wraps the match in try/except so a filter bug can never suppress a real error.
- **The worker** (`worker/__main__.py`) — the primary emitter — passes `before_send=drop_orphan_noise` to `configure_sentry("worker", ...)`.
- **The bridge** (`bridge/telegram_bridge.py`) composes it: `_sentry_before_send` runs the hibernation check first, then delegates to `drop_orphan_noise`.

This filters the **Sentry** noise only — the diagnostic still appears in bridge/worker logs (a `logging.Filter` on the `POPOTO.Query` logger is not used, because it would hide the diagnostic from logs entirely). Modifying Popoto's source to downgrade the log level is also not done: Popoto is a pip-installed dependency, and monkey-patching would break on upgrade. The `before_send` layer intercepts after Popoto logs but before Sentry captures.

## Related: Disk-Side Embedding Orphan Cleanup

`redis-index-cleanup` reconciles **Redis-side** orphans (index entries pointing at missing hashes). A parallel mechanism reconciles **disk-side** orphans (`.npy` embedding files in `~/.popoto/content/.embeddings/Memory/` without a live Memory record):

- **Reflection:** `embedding-orphan-sweep` (daily, dry-run by default) calls `EmbeddingField.garbage_collect(Memory)` + `EmbeddingField.sweep_stale_tempfiles(Memory)` from Popoto >= 1.6.0. Set `EMBEDDING_ORPHAN_SWEEP_APPLY=true` to enable deletion. Implemented in `reflections/memory/embedding_orphan_sweep.py::run`.
- **Read-only count:** `_count_disk_orphans(model_class)` in `scripts/popoto_index_cleanup.py` walks the embedding directory and counts orphans via the shared `popoto.fields.embedding_field._compute_expected_keep` helper. Surfaced as `disk_orphan_count` in `python -m tools.memory_search status --deep`.
- **One-shot reconciliation:** `python scripts/embedding_orphan_reconcile.py --dry-run` then `--apply`. Includes a positive-assertion safety check (refuses to apply if to-delete intersects expected-keep) and a pre-flight regression guard (refuses to apply if `$Class:Memory` is empty).
- **Required marker:** `Memory.__embedding_garbage_collect__ = True` opts the model into garbage_collect; without it Popoto's helper is a no-op (defensive default for any future model that attaches `EmbeddingField`).

`_count_orphans` reads the canonical `model_class._meta.db_class_set_key.redis_key` (= `$Class:{Name}`) rather than an older `{Name}:_all` key. See [Subconscious Memory § Embedding-File Lifecycle](subconscious-memory.md#embedding-file-lifecycle) for the full lifecycle.
