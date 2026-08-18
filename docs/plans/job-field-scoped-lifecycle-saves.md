---
status: Planning
type: bug
appetite: Small
owner: dev
created: 2026-08-18
tracking: https://github.com/tomcounsell/ai/issues/2860
---

# Field-scoped saves on `Job.touch` / `Job.mark_at_rest` / `Job.revive` (#2860)

## Problem

Three `Job` lifecycle methods in `models/job.py` mutate one or two fields and
then call a bare `self.save()`, which serializes the **entire** model hash.

**Current behavior** (`models/job.py:382-395`):

```python
def touch(self) -> None:
    self.last_active_at = _now()
    self.save()

def mark_at_rest(self) -> None:
    self.status = "at-rest"
    self.save()

def revive(self) -> None:
    self.status = "active"
    self.last_active_at = _now()
    self.save()
```

Each writes every field from an in-memory copy loaded at some earlier point.
A concurrent writer that changed a *different* field between this instance's
load and its save loses that write: last-write-wins over the whole hash rather
than over the field actually being mutated.

The collision surface is real rather than theoretical, and it is worth stating
precisely which of these three has a production caller today (searched
`bridge/`, `agent/`, `worker/`, `tools/`):

- **`revive()`** — called at `bridge/job_router.py:256` on every
  bound-existing-job routing decision. Races `add_expectation` /
  `discharge_expectation` on the same Job during a reply-storm.
- **`mark_at_rest()`** — called from `sweep_to_rest` (`models/job.py:475`).
  Races those same expectation writers on the health-check cadence.
- **`touch()`** — **no current production caller**; only test usages in
  `tests/unit/test_job_model.py`. It is fixed here for consistency and for
  whenever it is wired in, not because it is racing today.

Expectation mutations live in the `goal` field — the exact field none of these
methods intends to touch.

## Why a narrow fix is correct here

The repo already has this idiom and already names it "the structural
clobber-proof idiom":

- `renormalize_last_active_scores` → `fresh.save(update_fields=["last_active_at"])`
- `backfill_open_expectations_index` → `save(update_fields=["has_open_expectations"])`

popoto 1.8.0's `Model.save` supports `update_fields: list = None` for a partial
save ("only listed fields are serialized, validated, and indexed"), and the
`Job.save` override at `models/job.py:120` **already** gates its UTC-reattach on
field scope:

```python
if update_fields is None or "last_active_at" in update_fields:
```

with a docstring that explicitly contemplates "a scoped save that *names* the
field still reattaches." The scoped-save case is anticipated by design; this
change only extends an existing pattern to the three call sites that were
missed.

## Solution

Scope each save to exactly the fields the method mutates.

| Method | Mutates | `update_fields` |
|---|---|---|
| `touch()` | `last_active_at` | `["last_active_at"]` |
| `mark_at_rest()` | `status` | `["status"]` |
| `revive()` | `status`, `last_active_at` | `["status", "last_active_at"]` |

### Interaction with the tz-reattach gate

- `touch()` and `revive()` **name** `last_active_at`, so the reattach fires and
  the `SortedField` score is written as a pure UTC epoch — unchanged behavior.
- `mark_at_rest()` does **not** name it, so the reattach is correctly skipped
  and the `last_active_at` hash value and its partition score are left alone.
  This is the desired semantics: resting a Job by age must never refresh its
  recency. Today's full save re-writes that field on every rest transition.

No new API is invented and no call sites change.

## Files

- `models/job.py` — three one-line `save()` → `save(update_fields=[...])` edits.
- `tests/unit/` — job/expectations tests covering concurrent-writer preservation.

## Tests

Add coverage asserting the clobber-proofing directly, not just the call shape:

1. **`goal` survives `touch()`** — load Job instance A, mutate `goal` (add an
   expectation) and save via a second instance B, then `A.touch()`; re-read and
   assert B's expectation is still present and `last_active_at` advanced.
2. **`goal` survives `revive()`** — same shape; assert `status == "active"` and
   recency advanced, with B's `goal` write intact.
3. **`goal` survives `mark_at_rest()`** — assert `status == "at-rest"`.
4. **`mark_at_rest()` does not refresh recency** — `last_active_at` and its
   partition score are byte-identical before and after.
5. **`touch()` / `revive()` still score correctly** — the `last_active_at`
   SortedField score matches `to_unix_ts(job.last_active_at)` within tolerance
   (guards the reattach gate still firing under a scoped save).

6. **Index maintenance is scoped, not merely inferred** — at least one test
   asserts raw `$IndexF:Job:status:<value>` Redis set membership after
   `mark_at_rest()` (via `POPOTO_REDIS_DB`, the same pattern `repair_indexes`
   uses) rather than only round-tripping through `Job.query.get(...)`. An
   ORM-level read can pass even if the underlying index set is wrong, so this
   verifies the claim at the layer the plan actually makes it about.

Run via `scripts/pytest-clean.sh`, scoped to the job and expectations suites.

## Risks

- **A field is mutated but omitted from its `update_fields` list** — that
  mutation would silently never persist. Mitigated by the table above being
  derived from reading each method body, and by tests 1-5 asserting the
  intended field *did* change, not merely that others survived.
- **Loss of incidental index self-heal.** Because these three call sites do a
  *full* save today, each invocation also re-runs `on_save` for
  `has_open_expectations` (and, for `touch`/`revive`, `last_active_at`),
  silently re-confirming those index pointers as a side effect. After this
  change that incidental repair stops, and index self-healing belongs solely to
  the sanctioned maintenance sweeps: `backfill_open_expectations_index()` and
  `renormalize_last_active_scores()`. This is the intended tradeoff — the
  implicit whole-hash rewrite is precisely the clobber being removed — but it
  is named here so later index drift is not mis-attributed to this change.
- **Low blast radius otherwise**: `update_fields` is already exercised in
  production paths in this same module, so the popoto behavior is proven here.

## Positive controls

These existing tests exercise the `mark_at_rest()` / `revive()` router paths and
must pass **unmodified** as a regression guard:

- `tests/integration/test_job_routing.py:92`
- `tests/unit/test_job_router.py:273`

## No-Gos (Out of Scope)

- Any other bare `self.save()` in `models/job.py` (e.g. line 273). Auditing the
  rest of the model is a separate sweep; this lane is scoped to the three
  lifecycle methods named in #2860.
- The `JSONDecodeError` fail-open path — tracked separately.
