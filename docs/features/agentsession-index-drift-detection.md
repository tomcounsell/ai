# AgentSession Index-Drift Detection

Detect-only reconciliation guard that makes it impossible for the AgentSession
index and the underlying Redis hashes to silently disagree without someone
noticing.

## Overview

On 2026-07-14 an eng session crashed with `unpack(b) received extra data` (a
msgpack decode failure). Afterward `AgentSession.query.all()` returned `0`
with **no exception**, `valor_session list` reported "No sessions found", and
the dashboard showed an empty queue -- while **11 AgentSession hashes still
existed in Redis** (`repair_indexes()` later reported `sessions_rebuilt=11,
cleaned=0`). Every observability surface read through the same broken index
and reported "zero sessions" while the data was intact but unreachable.

The root mechanism: when the status index / class set desyncs from the actual
hashes (index empty or unreadable, hashes present), `query.all()` legitimately
returns `[]` -- `get_many_objects` finds no db_keys and returns an empty list
with no error (`popoto/models/query.py:2688-2694`). Nothing distinguishes
"genuinely zero sessions" from "N orphaned hashes the index can no longer
see." Corruption masquerades as emptiness, silently.

This feature closes that gap with a reconciliation function
(`agent/index_drift.py`), a non-fatal worker-startup guard, and a
`python -m tools.doctor` health check. It does **not** fix the index when
drift is found -- see [Detect-Only, Not Repair](#detect-only-not-repair)
below.

## The Reconciliation Function

`agent.index_drift.reconcile_agent_session_index()` returns a 4-tuple:

```python
(hash_count, queryable_count, drifted, truncated) = reconcile_agent_session_index()
```

| Field | Meaning |
|-------|---------|
| `hash_count` | Raw count from a bounded Redis `SCAN` over `AgentSession:*` keys, counting **only** keys of Redis type `hash` whose name has the base-key shape (no `::`). |
| `queryable_count` | `len(AgentSession.query.all())` -- what the index can actually see. |
| `drifted` | `True` iff `hash_count > queryable_count + AGENTSESSION_INDEX_DRIFT_TOLERANCE` -- the primary incident class this guard exists to catch. Only ever computed when the SCAN was exhaustive. |
| `truncated` | `True` iff the bounded SCAN hit the iteration cap before reaching `cursor == 0` -- `hash_count` is a partial undercount and drift is deliberately **not** computed from it. |

### Bounded SCAN vs. `query.all()`

The raw count uses the same bounded-iteration SCAN primitive as
`agent/session_archive.py` (imports `_SCAN_MAX_ITERATIONS` /
`_SCAN_COUNT_HINT` from there rather than duplicating it), so a corrupt or
enormous keyspace cannot hang the guard.

### Capped-list keys are excluded

`AgentSession*` also matches companion capped-list keys of the shape
`AgentSession:<key>::<field>` and any other non-hash key that happens to
share the prefix. Counting those would inflate `hash_count` and produce
false-positive drift on every boot. The SCAN loop explicitly skips any key
containing `::` and confirms `TYPE == hash` before counting, so `hash_count`
is apples-to-apples with `len(AgentSession.query.all())`.

### Truncated scan is never compared

If the SCAN hits `_SCAN_MAX_ITERATIONS` without exhausting the keyspace
(`cursor != 0`), the function logs a `"scan incomplete"` WARNING and returns
`truncated=True` with `drifted=False` unconditionally -- a partial undercount
must never be reported as "no drift," and it is never misclassified as the
distinct stale-index anomaly either.

### The `query.all()`-raises path is surfaced from inside reconcile

If `AgentSession.query.all()` itself raises (a genuinely corrupt hash),
`reconcile_agent_session_index()` catches that internally, logs the loud
ERROR, fires the Sentry capture itself, and returns
`(hash_count, 0, True, truncated)`. This surfacing does **not** depend on any
outer caller's try/except -- both the worker-startup guard and the doctor
check get the loud signal for free even if their own error handling would
otherwise have downgraded it to a silent warning.

### The inverse anomaly is logged distinctly

`hash_count < queryable_count` (stale index members with no backing hash) is
a different, already-partially-mitigated anomaly -- see
[`clean_indexes()` / issue #1459](session-lifecycle.md). It is logged as a
distinct WARNING and does **not** set `drifted=True`, so it is never confused
with the primary "hashes the index can't see" drift class.

## Worker Startup Guard

`worker/__main__.py` invokes `reconcile_agent_session_index()` as **Step 2c**,
immediately after Step 2b (class-set orphan cleanup for `AgentSession` and
`Memory`) and before the worker starts serving. Running it in this quiescent
post-repair, pre-serve window minimizes the chance of racing a concurrent
save (popoto 1.8.0 index maintenance is atomic per save via Lua, so the
race window is sub-millisecond in practice).

The startup call is wrapped in its own try/except, but that try/except is a
**last-resort net for bugs in the detector itself** -- it is strictly weaker
than, and separate from, the loud surfacing that already happens inside
`reconcile_agent_session_index()`. If the detector throws an unexpected
exception, the worker logs a WARNING and continues starting up; it never
crashes worker startup. Real drift -- including a `query.all()` raise -- is
always surfaced as a loud ERROR + Sentry capture from inside the reconcile
call itself, regardless of what this outer guard does.

## Doctor Check

`python -m tools.doctor` registers `_check_agentsession_index_drift` in the
Services group:

- **PASS** -- `hash_count == queryable_count` (within tolerance): `"AgentSession
  index consistent: N hashes, N queryable"`.
- **FAIL** -- drift detected: `"AgentSession index drift: N hashes, M
  queryable -- index desync"`, with a fix hint pointing at `valor-session
  inspect` for investigation and `repair_indexes()` for remediation.
- **FAIL** (truncated) -- the SCAN hit its iteration cap: reports "scan
  incomplete" without claiming drift either way.

Run it directly:

```bash
python -m tools.doctor
```

## `AGENTSESSION_INDEX_DRIFT_TOLERANCE`

Divergence tolerance for the primary `hash_count > queryable_count` drift
check, default `0`, overridable via the `AGENTSESSION_INDEX_DRIFT_TOLERANCE`
env var.

> **Warning:** this default of `0` is a should-always-be-correct invariant,
> not a tuning knob to reach for casually. Raising it above `0` **suppresses
> the exact silent-empty incident class this guard exists to catch** -- a
> hash that exists in Redis but is invisible to `query.all()`. Only widen
> this tolerance if a specific environment is proven noisy with false
> positives at tolerance `0`, and prefer fixing the root cause (an
> apples-to-apples counting bug) over raising the tolerance to paper over it.

## Detect-Only, Not Repair

This guard **never calls `repair_indexes()`** and never mutates Redis in any
way. It was deliberately scoped as detect-only during plan critique: an
automatic self-heal on drift would reopen the non-atomic class-set
delete-then-re-add window documented in
[Session Lifecycle -- Index-Rebuild Race and Read-Path Retry (issue
#1720)](session-lifecycle.md#index-rebuild-race-and-read-path-retry-issue-1720),
and the doctor check's read-only invocation could fire that repair at any
time the worker or dashboard are actively serving -- potentially
reproducing the exact silent-empty incident this guard exists to catch.

Repair of detected drift (making index repair atomic) is out of scope for
this feature and owned by a separate effort: see
`docs/plans/session-recovery-observation-audit.md` ("candidate 13" -- atomic
index repair). This guard's sole responsibility is to make divergence
impossible to miss; recovering from it is a human- or repair-tool-driven next
step.

## Related

- [Session Lifecycle](session-lifecycle.md) -- the 14-state lifecycle model, `clean_indexes()` / issue #1459, and the issue #1720 class-set race this guard's counterpart anomaly relates to
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- the other orphan/corruption reapers running at worker startup
- `docs/plans/session-recovery-observation-audit.md` -- owner of future atomic index-repair work
