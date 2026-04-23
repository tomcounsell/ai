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

### Worker Startup (All-Model Rebuild)

Worker startup calls `run_cleanup()` from `scripts/popoto_index_cleanup` to rebuild indexes for **all** Popoto models (not just AgentSession). This runs as Step 1 of the startup sequence, before corrupted session cleanup and recovery. The total time is logged for monitoring.

### Cleanup Reflection

`scripts/popoto_index_cleanup.py` provides a `run_cleanup()` function registered as the `redis-index-cleanup` reflection in `config/reflections.yaml`. The `ReflectionScheduler` (worker-embedded in `python -m worker`) dispatches this daily while the worker process runs.

### How `run_cleanup()` Works

1. Iterates all Popoto models from `models/__init__.__all__`
2. For each model, counts orphaned index entries (dry-run scan)
3. Calls `Model.rebuild_indexes()` to clean them up
4. Logs per-model orphan counts found and cleaned

Each model is processed independently -- one model failure does not abort the sweep. The SCAN-based `rebuild_indexes()` is safe to run concurrently with normal operations.

### Cleanup Paths

| Path | Trigger | Scope |
|------|---------|-------|
| Worker startup | `python -m worker` | All models via `run_cleanup()` |
| ReflectionScheduler | Worker scheduler tick (daily) | All models via `run_cleanup()` |

## Concurrency Safety

`rebuild_indexes()` uses Redis SCAN (cursor-based, non-blocking) and only adds/removes index entries to match actual hash existence. Concurrent creates and deletes are safe and self-correcting -- any inconsistency introduced by a concurrent operation is fixed on the next run.

## Key Files

| File | Purpose |
|------|---------|
| `models/teammate_metrics.py` | TeammateMetrics Popoto model |
| `agent/teammate_metrics.py` | Refactored metrics module (uses Popoto) |
| `models/agent_session.py` | AgentSession with Meta.ttl |
| `agent/agent_session_queue.py` | Refactored diagnostic fallback |
| `worker/__main__.py` | Worker startup using `run_cleanup()` for all-model rebuild (step 1) |
| `scripts/popoto_index_cleanup.py` | Cleanup function (`run_cleanup()`) and model discovery (`_get_all_models()`) |
| `config/reflections.yaml` | Reflection registry entry for `ReflectionScheduler` |

## Inline Orphan Prevention (Defensive srem)

`rebuild_indexes()` is a batch repair run — it catches orphans after the fact. A complementary inline mechanism in `finalize_session()` prevents a specific class of orphans at creation time:

When stale-object full saves clobber a session's status in Redis (e.g., a session appearing in the `pending` index after being killed), the `finalize_session()` call that ends the session performs a **defensive `srem`** across ALL non-target status index sets immediately after the terminal save. This removes any stale entries left by prior writes.

The defensive `srem` is non-fatal (wrapped in try/except) and depends on three Popoto internals that must be re-verified on Popoto upgrade: `DB_key`, `POPOTO_REDIS_DB.srem()`, and `get_special_use_field_db_key`. See `models/session_lifecycle.py` (`finalize_session`) for implementation detail. The broader fix (preventing stale-object saves in the first place) is documented in [Session Lifecycle](session-lifecycle.md#layer-1b-partial-saves-on-companion-field-methods-950).

## Verification

After the cleanup reflection runs, `grep -rn "import redis" agent/` should return zero hits, and bridge logs should show no `"one or more redis keys points to missing objects"` warnings.
