# AgentSession Pending-Index Phantom Leak (A1 Rebuild Guard)

**Status:** Shipped

## Symptom

The Redis SET `$IndexF:AgentSession:status:pending` (Popoto's secondary index
backing `AgentSession.query.filter(status="pending")`) can leak phantom members
while the ORM ground truth `AgentSession.query.filter(status="pending")` reports
`0`. Because worker startup runs `cleanup_corrupted_agent_sessions()` (→
`repair_indexes()`) before it can register a heartbeat, a bloated index that the
cleanup cannot finish scanning keeps the worker from ever registering a
heartbeat, and the watchdog restarts it.

## The rebuild re-inflation mechanism

`AgentSession.repair_indexes()` (`models/agent_session.py`) deletes each whole
`$IndexF:AgentSession:*` key, then delegates the rebuild to popoto's
`rebuild_indexes()`. popoto's rebuild `scan_iter`s every `AgentSession:*` hash,
`hgetall`s it, and decodes it into a model instance. Under popoto >= 1.9.0 the
decoded instance is then run through a **divergence pre-check**: the row's
stored Redis key is compared against the key re-derived from its decoded
values, and any mismatch is skipped into `RebuildIndexesResult.diverged_keys`
*before* reaching `field.on_save` at all. An identity-less row — no
`session_id` — can never derive back to its stored key, so under 1.9.0 this
pre-check, not `field.on_save`, is the seam that catches nearly all of them.

Historically (pre-1.9.0, and still for the rare identity-less row whose
derived key happens to match its stored key), the row instead reaches
`field.on_save` for **every** field in a generic loop:

```python
for field_name, field in cls._meta.fields.items():
    field.on_save(instance, field_name=field_name,
                  field_value=getattr(instance, field_name), pipeline=pipeline)
```

For the `status` field, `on_save` `SADD`s the record's redis key into
`$IndexF:AgentSession:status:pending`. Because
`status = IndexedField(default="pending")`, **any identity-less / near-empty
hash — one with no `session_id` — decodes as `status="pending"`** and would
get re-added to `:pending` on **every** rebuild absent a guard. That is the
underlying leak: without a guard on one seam or the other, the rebuild half of
repair re-inflates the index it just cleared.

## Why the ORM count stays 0 while `scard` climbs

`query.filter(status="pending")` hydrates each index member and passes them
through `_filter_hydrated_sessions` (`agent/session_health.py`). The canonical
identity check there is: a record is hydrated iff **both** `agent_session_id`
and `session_id` are `str`. Identity-less hashes have no `session_id`, so they
are dropped from every ORM query result — the ORM count reads 0 while the raw
`scard` of the index set keeps growing.

## The A1 rebuild guard: two seams, one de-duplicated row count

Two independent seams can catch an identity-less row, and `repair_indexes()`
counts a row caught by either (or both) exactly once, via a
`quarantined_keys: set[str]` of Redis keys rather than an event counter:

**Seam 1 — popoto's divergence pre-check (primary under popoto >= 1.9.0).**
`repair_indexes()` reads `RebuildIndexesResult.diverged_keys` off the return
value of `cls.rebuild_indexes()`, re-decodes each diverged key's raw hash
(`decode_popoto_model_hashmap`, a popoto internal imported at the call site
under `try/except ImportError`), and runs it through the same
`_filter_hydrated_sessions` identity check. A diverged-but-hydrated row (e.g. a
future datetime-key-canonicalization mismatch) is logged and **not** counted —
only genuinely identity-less rows add their key to the quarantine set. An
empty raw hash means the row is **gone**, not identity-less, and is skipped
without counting. If the internal import ever fails, the filter degrades to
the unfiltered `len(diverged_keys)` sum (still correct, just coarser) and
reports the degradation loudly: an unconditional `logger.error` every pass,
plus a `sentry_sdk.capture_message` latched to once per process via the
`_decode_degrade_reported` class attribute (worker startup, the hourly
reflection, and session pickup all call `repair_indexes()`, so an unlatched
capture would flood Sentry for the life of the condition).

**Seam 2 — the retained `on_save` shim (second line of defence).** The
transient shim on each `IndexedField`'s `on_save`, active **only** for the
duration of the rebuild call, remains installed: a hypothetical identity-less
row whose derived key happens to match its stored key would sail past the
divergence pre-check and still needs skipping here. It is generalized: the
field set is computed at runtime from `cls._meta` (rather than naming
`status`), install is wrapped in a non-reentrant `_repair_lock`, and
`AgentSession` is excluded from worker Step 1's raw sweep. `repair_indexes()`:

1. Captures the original bound field `on_save` classmethod for each guarded field.
2. Installs a transient plain-function shim as an **instance attribute** on the
   field (a classmethod is a non-data descriptor, so an instance attribute
   shadows it; a plain function stored there is unbound, so it receives the
   model instance as its first positional arg — matching popoto's call).
3. The shim runs `_filter_hydrated_sessions([instance])`. If empty (identity-less,
   no `session_id`), it adds the row's Redis key to the quarantine set and
   **skips** the SADD (returns the pipeline untouched). Otherwise it delegates
   to the original `on_save` verbatim — healthy records re-index normally.
4. `cls.rebuild_indexes()` is called inside a `try`; the `finally` block removes
   the instance attribute (`del field.on_save`), reverting to the class
   classmethod.
5. The de-duplicated per-pass row count (both seams, every `IndexedField`) is
   exposed via `AgentSession._last_quarantined_identityless` and a WARNING log.
   The `(stale_count, rebuilt_count)` 2-tuple return is **unchanged** (it is
   unpacked at several call sites).

Neither seam reimplements popoto's rebuild loop — every field and the
healthy-record SADDs are delegated to unmodified `rebuild_indexes()`. Both
assume a single-threaded rebuild, which is the actual call context (worker
startup / reflection tick).

### Inverse-bug guard

The guard is scoped to the rebuild path **only**. Normal live
`AgentSession(...).save()` stays unguarded, so a legitimate brand-new pending
session is still added to `:pending` immediately. A permanent class-level gate on
`on_save` would be the *inverse* bug — a healthy session that never appears in
the index. A unit test pins that live-save still indexes.

## Gone-hash orphans are cleared by the whole-key rebuild, not A1

An index member whose backing `AgentSession:*` hash no longer exists (a
"gone-hash orphan") is **not** touched by A1's skip — popoto's `scan_iter` never
sees a hash for it, so nothing re-adds it. These are cleared purely by
`repair_indexes()`'s whole-`$IndexF`-key delete-and-rebuild (the stale-member
scan that deletes the entire index key before rebuild). That whole-key rebuild is
therefore load-bearing and complements A1. A unit test pins this boundary.

## Convergence: one pass

With A1 in place, a bloated `:pending` index converges to the true pending count
in a **single** `repair_indexes()` pass (whole-key delete + rebuild that no
longer re-adds junk) and stays flat across subsequent passes. This is verified by
the convergence unit test.

## Solution B (delete-ordering `srem` fix) is not built

Solution B addresses a second, independent phantom source: popoto's
`Model.delete()` deletes the hash first, then `on_delete` reads the now-gone
pointer and can `srem` the wrong member, stranding a phantom. B is **not built**:
A1 converges the index in one `repair_indexes()` pass (convergence test green),
and the whole-key rebuild clears stranded members each pass. Delete-ordering is
deferred — build B only if the delete path needs *immediate* (pre-next-rebuild)
correctness rather than eventual convergence.

## Known limitation: raw hash keyspace

A1 stops the **index** re-inflation but does not delete the identity-less
`AgentSession:*` hashes themselves. If a live write path keeps manufacturing them,
the raw hash keyspace can still grow while `scard` stays flat.
`_last_quarantined_identityless` is a per-pass de-duplicated **row** count
(across both seams and every `IndexedField`), not a cumulative keyspace gauge.
Reaping the underlying identity-less hashes / preventing the write
source is handled separately. The read/rebuild resilience fix stands regardless.

## See also

- [Popoto Index Hygiene](popoto-index-hygiene.md#a1-rebuild-guard-identity-less-phantom-re-inflation) —
  the generalized guard across all `IndexedField`s, the install-inside-`try` +
  non-reentrant-lock hardening, the `AgentSession` exclusion from worker Step 1's
  raw sweep, and the Step 1 daemon-thread un-wedge.
- [Agent Session Queue](agent-session-queue.md) — the cooldown-gated corrupted-pop
  reaper and `repair_indexes()` context.
- [Agent Session Health Monitor](agent-session-health-monitor.md) —
  `cleanup_corrupted_agent_sessions()` and `_filter_hydrated_sessions`.
