# AgentSession Pending-Index Phantom Leak (A1 fix)

**Issue:** [#2101](https://github.com/tomcounsell/ai/issues/2101) · **Status:** Shipped (A1 only; B deferred)

## Symptom

The Redis SET `$IndexF:AgentSession:status:pending` (Popoto's secondary index
backing `AgentSession.query.filter(status="pending")`) leaked phantom members at
a sustained rate on production, crash-looping the worker: `scard` reached
~1.37M while the ORM ground truth `AgentSession.query.filter(status="pending")`
reported `0`. Worker startup runs `cleanup_corrupted_agent_sessions()` (→
`repair_indexes()`) before it can register a heartbeat; that cleanup could never
finish scanning a multi-hundred-thousand-member index before the watchdog killed
the process → permanent restart loop.

## The rebuild re-inflation mechanism

`AgentSession.repair_indexes()` (`models/agent_session.py`) deletes each whole
`$IndexF:AgentSession:*` key, then delegates the rebuild to popoto's
`rebuild_indexes()`. popoto's rebuild `scan_iter`s every `AgentSession:*` hash,
`hgetall`s it, decodes it into a model instance, and runs `field.on_save(...)`
for **every** field in a generic loop:

```python
for field_name, field in cls._meta.fields.items():
    field.on_save(instance, field_name=field_name,
                  field_value=getattr(instance, field_name), pipeline=pipeline)
```

For the `status` field, `on_save` `SADD`s the record's redis key into
`$IndexF:AgentSession:status:pending`. Because
`status = IndexedField(default="pending")`, **any identity-less / near-empty
hash — one with no `session_id` — decodes as `status="pending"`** and gets
re-added to `:pending` on **every** rebuild. That is the leak: a one-off manual
`repair_indexes()` dropped `scard` to ~2k, and it refilled to ~217k with no
further action, because the rebuild half of repair re-inflates the index it just
cleared.

## Why the ORM count stays 0 while `scard` climbs

`query.filter(status="pending")` hydrates each index member and passes them
through `_filter_hydrated_sessions` (`agent/session_health.py`). The canonical
identity check there is: a record is hydrated iff **both** `agent_session_id`
and `session_id` are `str`. Identity-less hashes have no `session_id`, so they
are dropped from every ORM query result — the ORM count reads 0 while the raw
`scard` of the index set keeps growing.

## The A1 fix

Bound the intervention to the `status` field's `on_save`, active **only** for the
duration of the rebuild call. `repair_indexes()`:

1. Captures the original bound `status.on_save` classmethod.
2. Installs a transient plain-function shim as an **instance attribute** on the
   `status` field (a classmethod is a non-data descriptor, so an instance
   attribute shadows it; a plain function stored there is unbound, so it receives
   the model instance as its first positional arg — matching popoto's call).
3. The shim runs `_filter_hydrated_sessions([instance])`. If empty (identity-less,
   no `session_id`), it increments a quarantine counter and **skips** the SADD
   (returns the pipeline untouched). Otherwise it delegates to the original
   `on_save` verbatim — healthy records re-index normally.
4. `cls.rebuild_indexes()` is called inside a `try`; the `finally` block removes
   the instance attribute (`del status_field.on_save`), reverting to the class
   classmethod.
5. The per-pass quarantine count is exposed via
   `AgentSession._last_quarantined_identityless` and a WARNING log. The
   `(stale_count, rebuilt_count)` 2-tuple return is **unchanged** (it is unpacked
   at several call sites).

The shim never reimplements popoto's rebuild loop — every other field and the
healthy-record status SADD are delegated to unmodified `rebuild_indexes()`. It
assumes a single-threaded rebuild, which is the actual call context (worker
startup / reflection tick).

### Inverse-bug guard

The guard is scoped to the rebuild path **only**. Normal live
`AgentSession(...).save()` stays unguarded, so a legitimate brand-new pending
session is still added to `:pending` immediately. A permanent class-level gate on
`status.on_save` would be the *inverse* bug — a healthy session that never
appears in the index. A unit test pins that live-save still indexes.

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

## Why B (delete-ordering `srem` fix) is deferred

The plan's Solution B addresses a second, independent phantom source: popoto's
`Model.delete()` deletes the hash first, then `on_delete` reads the now-gone
pointer and can `srem` the wrong member, stranding a phantom. B was **not built**:
A1 converges the index in one `repair_indexes()` pass (convergence test green),
and the whole-key rebuild clears stranded members each pass. Per the plan's
Resolved Decision #2, delete-ordering is deferred — build B only if the delete
path needs *immediate* (pre-next-rebuild) correctness rather than eventual
convergence.

## Known limitation (Risk 4): raw hash keyspace

A1 stops the **index** re-inflation but does not delete the identity-less
`AgentSession:*` hashes themselves. If a live write path keeps manufacturing them,
the raw hash keyspace can still grow while `scard` stays flat.
`_last_quarantined_identityless` is a per-pass event count, not a cumulative
keyspace gauge. Reaping the underlying identity-less hashes / preventing the write
source is handed to [#2086](https://github.com/tomcounsell/ai/issues/2086) or a
follow-up. The read/rebuild resilience fix stands regardless.

## See also

- [Agent Session Queue](agent-session-queue.md) — the #2101 accelerant
  (cooldown-gated corrupted-pop reaper) and `repair_indexes()` context.
- [Agent Session Health Monitor](agent-session-health-monitor.md) —
  `cleanup_corrupted_agent_sessions()` and `_filter_hydrated_sessions`.
