---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2634
last_comment_id: 5213371553
revision_applied: true
revision_applied_at: 2026-08-07T07:05:00Z
---

# Bound the at-rest open-promise scan

## Scope

**#2634 is the at-rest promise scan only.** Its deliverable is the `has_open_promises` derived
IndexedField, its backfill, the tests, and the schema-gate text amendment — tasks
`build-open-promise-index`, `build-backfill-migration`, `build-tests`, `document-feature`.

**The bounded candidate lookup is #2636.** `recent_for_room`, the tz-normalizing `save()` override,
and the sorted-set score-repair backfill moved there because they need an upstream popoto
sorted-range pushdown before this repo can consume them. Their analysis — the sorted-set key layout,
the naive-datetime score defect, the tie-break divergence, and the measured 60→5 hash-read prototype
— was posted to that issue as
[issuecomment-5213306359](https://github.com/tomcounsell/ai/issues/2636#issuecomment-5213306359) and
is not duplicated here. An earlier draft of this document carried both halves; keeping a second
issue's plan of record inside this one would make `/do-build` execute out-of-scope tasks, so the
split is by document, not by section.

The two halves are independent. The at-rest fix uses an IndexedField intersection and never reads a
sorted-set score, so it does not inherit #2636's tz prerequisite. Rest-by-age is likewise unaffected:
`sweep_to_rest` compares through `bridge.utc.to_unix_ts`, which already treats a naive datetime as
UTC (`bridge/utc.py:38-53`).

## Problem

The at-rest promise backstop runs a full scan of every Job that has ever gone to rest, every 300
seconds, on every worker, to surface a set that is almost always empty.

**Current behavior:**

`Job.at_rest_with_open_promises` (`models/job.py:294-307`) iterates `cls.query.filter(status="at-rest")`
and calls `open_promises()` on each result, which `json.loads` the entire `goal` blob
(`models/job.py:142-150`). The call chain is `agent/session_health.py:917` →
`_agent_session_health_check` (`:4505`) → `_agent_session_health_loop` (`:5143`), sleeping
`AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300` (`:442`), supervised one-per-worker at
`worker/__main__.py:960`.

The at-rest population has no ceiling. Jobs are immortal by design (`models/job.py:25-26`, no
`Meta.ttl`), rest is the steady state every Job eventually reaches, and rest is never terminal — so
the set only grows, for the life of the deployment. Rest-by-age (`JOB_AT_REST_AGE_SECONDS`, 72h)
bounds the *active* set; nothing bounds this one.

Cost per tick is `2 × |at-rest population|` hash reads plus one JSON parse each. The `2×` is not a
typo: `list(QueryBuilder)` executes the whole hydration pipeline twice (measured; filed separately as
#2639). So the scan's cost rises monotonically with lifetime Job count while its useful output —
Jobs at rest still carrying an undischarged promise — stays near zero.

**Desired outcome:**

`Job.at_rest_with_open_promises` hydrates only Jobs that actually carry an open promise. Cost becomes
proportional to the flagged set, which is small by definition, instead of to the at-rest population.
The backstop's observable behavior — which Jobs it surfaces, and the operator log line it writes — is
unchanged.

## Freshness Check

**Baseline commit:** `cc82d223b` (plan branch point; `0a53ad22f` at recon time)
**Issue filed at:** 2026-08-07T04:56:42Z
**Disposition:** **Major drift** — two of the issue's three items left this issue after it was filed.
Scope resolved with the human via issue comment `5212992885`; this plan covers item 2 only.

**File:line references re-verified:**

- `models/job.py` `repair_indexes` leg 2 — issue item 1 claimed "a bare per-member `exists()`
  round-trip" — **GONE, already fixed.** Now at `models/job.py:393-403`, batching `EXISTS` at
  `batch_size = 5000` through a non-transactional pipeline, byte-for-byte the
  `models/agent_session.py:2423-2428` precedent the issue asked to port. Landed as `0a53ad22f`,
  ~46 minutes after the issue was filed.
- `models/job.py:294-307` `at_rest_with_open_promises` — **still holds.** Unbounded
  `filter(status="at-rest")` with a `json.loads` per row. This is the entirety of the remaining scope.
- `models/job.py:239-251` `recent_for_room` — still holds, but **moved out of this issue** to #2636.
- `docs/features/durability-model.md:119` — "`Job.status` is the only IndexedField" still accurate,
  and this plan makes it false. Amendment tracked in Documentation.

**Cited sibling issues/PRs re-checked:**

- **#2494** (Durability refactor) — still **OPEN**, the parent epic.
- **PR #2631** (Durability M3) — **MERGED 2026-08-07T04:58:06Z**, 96 seconds after this issue was
  filed. Its substrate review produced these notes.
- **#2636** (Bound `Job.recent_for_room` via a popoto sorted-range pushdown) — **NEW, OPEN**, filed
  2026-08-07T05:51:23Z. Owns former item 3. It is the #2494 phase-2 cutover prerequisite; this issue
  is not. Spike evidence gathered during this planning pass that belongs to it — the sorted-set key
  layout, a naive-datetime score defect, a tie-break divergence, and a measured 60→5 hash-read
  prototype — was posted there as
  [issuecomment-5213306359](https://github.com/tomcounsell/ai/issues/2636#issuecomment-5213306359)
  rather than carried here.

**Commits on main since issue was filed (touching referenced files):**

- `0a53ad22f` "Pipeline Job.repair_indexes' stale-member scan instead of one round trip each" —
  **already fixes issue item 1.** No work remains.
- `bc3f682a5` "Schema Gate Amendment 1: Job carries two low-cardinality IndexedFields" —
  **resolves this plan's Open Question 1 in advance.** The amendment is recorded in
  `docs/plans/durability-room-job-agentrun.md` and grants the second IndexedField. It also confirms
  the live at-rest Job population at amendment time is **zero**, which collapses the backfill-cost
  risk to a no-op, and records one alternative this plan had not considered (refining the `status`
  enum with an `at-rest-owed` value) along with its disqualifying reason. Folded into Risks 3 and 4
  and into the Documentation checklist below.

**Issue comments incorporated:** comment `5212992885` (tomcounsell, 2026-08-07T05:51:37Z) rescopes
this issue: item 1 shipped, item 3 moved to #2636, "this issue now closes when item 2 lands." This
plan was rewritten to match — an earlier draft covering all three items was replaced, not amended.
Comment `5213371553` (valorengels, 2026-08-07T06:26:43Z) records the schema-gate ruling; verified
landed on `main` as *Schema Gate Amendment 1*, `docs/plans/durability-room-job-agentrun.md:539-555`.

**Re-verified at finalization (baseline `3bb299cbd`):** `models/job.py:294-307`
`at_rest_with_open_promises` re-read — still an unbounded `filter(status="at-rest")` with a
`json.loads` per row; the claim holds. #2636 confirmed **OPEN** ("Bound Job.recent_for_room via a
popoto sorted-range pushdown"), so the deferral target is live. Prerequisites re-run under
`.venv/bin/python`: Redis ping `True`, popoto floor OK, `Job.query.all()` → **0 rows**, so the
spike-2-caveat-2 backfill is a no-op today exactly as the amendment's timing argument assumed.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/durability-room-job-agentrun.md`
(modified 2026-08-07) is the **parent** plan, not a competitor. Lines 528-529 hold the ratified Job
schema this plan proposes to amend, so the amendment is a coordination obligation, not an optional
courtesy.

**Notes:** The net effect of the drift is favourable. Two thirds of the issue left, and the third
that remains is the one with no external dependency — #2636 is gated on an upstream popoto release,
and item 2 is not gated on anything. The recon also disqualified the fix the issue text suggested
(see spike-1), so this is not the mechanical change the issue implied.

## Prior Art

`gh issue list --state closed --search "Job scaling recent_for_room ZREVRANGE at-rest"` and
`gh pr list --state merged --search "Job recent_for_room bounded scan"` both returned empty. The Job
model is roughly a day old; there is no prior attempt at these paths, and therefore no
"Why Previous Fixes Failed" section.

The relevant prior art is adjacent precedent:

- **PR #2631** — Durability M3, which introduced `models/job.py` and ratified its schema one-shot.
  Merged, healthy. Its substrate review produced this issue.
- **`agent/session_health.py:847-850`** — the *sibling* backstop `_check_at_rest_owed_communication`
  solves the same shape by iterating `NON_TERMINAL_STATUSES` and hydrating each. That works only
  because non-terminal AgentSessions are inherently few ("dozens of live rows",
  `durability-room-job-agentrun.md:531`). At-rest Jobs are not few and never will be, so the sibling
  pattern is not transferable — worth stating, because it is the first thing a reader will reach for.
- **`scripts/update/migrations.py:360`** (`_migrate_backfill_pipeline_ledger`) — the write-backfill
  migration pattern this plan needs. The read-only probe pattern at `:288-357` is **not** sufficient
  here (spike-2), despite being the pattern every prior Job/AgentSession field addition used.
- **Anti-precedents worth not copying:** `models/link.py:36-48` and `models/telegram.py:55-70` both
  document themselves as bounded and are full `query.all()` scans with a Python-side cutoff.
  `models/agent_session.py:2536-2555` (`cleanup_expired`) is the same. This plan should not add a
  fourth.

## Research

**Queries used:**

- `popoto redis ORM SortedField partition_by query limit hydration`

**Key findings:**

- Popoto's published documentation ([popoto.io](https://popoto.io/),
  [readthedocs](https://popoto.readthedocs.io/en/latest/), [PyPI](https://pypi.org/project/popoto/))
  covers `SortedField(type=...)` and range lookups, but has **no coverage of `partition_by`, query
  `limit` semantics, index-set encoding, or hydration internals**. The API has also shifted across
  versions, so version-matched documentation for 1.8.0 does not exist.
- **How this informs the plan:** external documentation is not a usable authority here. Every
  behavioral claim below is grounded in the pinned source
  (`.venv/lib/python3.12/site-packages/popoto`, 1.8.0, floor enforced by `config/popoto_floor.py`)
  plus spikes against real Redis. Two behaviors that decide this plan's design — partition orphaning
  and the untyped-boolean hydration trap — are undocumented upstream and would each have been
  discovered the hard way. Both are saved to project memory.

Sources: [Popoto](https://popoto.io/), [Popoto Documentation](https://popoto.readthedocs.io/en/latest/), [popoto on PyPI](https://pypi.org/project/popoto/)

## Spike Results

Four spikes ran during this planning pass. Two of them (the sorted-set key layout and a
naive-datetime score defect) turned out to belong to #2636 once the issue was rescoped, and were
posted there. The two below are the ones that decide *this* plan.

### spike-1: Is an `at_rest_since` SortedField — the fix the issue text suggests — viable?

- **Assumption**: "adding an `at_rest_since`-style SortedField bound is a mechanical way to bound the
  at-rest scan," per the issue body.
- **Method**: prototype (worktree-isolated, throwaway model, Redis db 15) plus source read
- **Finding**: **FALSIFIED for the partitioned form; the unpartitioned form answers the wrong
  question.**

  Partitioning by `status` is the natural shape, since the query already filters on it. It is
  unshippable. popoto's `on_save` (`popoto/fields/sorted_field_mixin.py:496-522`) only removes the
  member from the old partition when `_saved_field_values` shows the partition field changed, and for
  a **lazily-loaded** instance — which is exactly what `query.filter()` returns —
  `popoto/models/encoding.py:429-443` populates `_saved_field_values` with **KeyFields only**. So
  `saved.get("status")` is `None`, `partition_changed` is `False`, and the removal never fires.
  Reproduced against the exact `Job.sweep_to_rest` code shape (`models/job.py:274-278`):

  ```
  [created d1 active]  IndexF active: ['SpikeTmpD:d1']   SortF active: ['SpikeTmpD:d1']
  [after lazy flip]    IndexF active: []                 SortF active: ['SpikeTmpD:d1']  <-- ORPHAN
                       IndexF at-rest: ['SpikeTmpD:d1']  SortF at-rest: []               <-- MISSING
  ```

  The `status` IndexedField swapped correctly — `INDEX_SWAP_LUA` is server-side and never consults
  `_saved_field_values`. The partitioned SortedField did not. The Job becomes permanently invisible
  to the at-rest query and permanently visible to the active one. `.delete()` cannot clean the orphan
  (`on_delete` computes the partition from the *current* instance) and `rebuild_indexes()` does not
  enumerate `$SortF:*`. Worse, when a SortedField is partitioned by `status`, popoto **consumes** the
  `status` kwarg (`query.py:1930-1934`), so the wrong sorted set becomes the sole, unintersected
  answer. Viable only if every rest and revive transition were rewritten to `query.get()` — a
  discipline no test enforces and one future `filter()` loop silently breaks.

  The unpartitioned form has no such bug (one sorted set, idempotent score update) and does intersect
  correctly. But it bounds by **rest time**, not by **open promise**. A rolling
  `at_rest_since__gte = now - W` window silently drops a Job that went to rest before the window —
  which is precisely the long-abandoned promise the backstop exists to catch. Making it safe requires
  a watermark sweep (`__lte=watermark`, advance the watermark, persist it), and `limit=` cannot batch
  that because popoto's early-limit optimization is gated to KeyField ordering (`query.py:2648-2655`).
  That is a stateful design for a query that has a stateless answer.

  Corollary, confirmed and load-bearing for the chosen design: an IndexedField equality filter **does**
  intersect with other index predicates *before* hydration. Sorted fields resolve first
  (`query.py:1897-1934`), other fields after (`:1936-1962`), `set.intersection` at `:1989`, and
  `get_many_objects` issues hash reads only over the intersection (`:2680`). Measured:
  `status='at-rest'` + range → 3 keys / 6 hash reads; `status='at-rest'` alone → 25 keys / 50 hash
  reads.

  **Incidental:** hydration costs **2 hash reads per record**, not 1 — `list(QueryBuilder)` runs the
  pipeline twice. Every `.filter()` in the repo pays double. Filed as #2639.
- **Confidence**: high
- **Impact on plan**: eliminates the issue's own suggested fix in both forms, and selects the derived
  `has_open_promises` IndexedField instead. Without this spike the plan would have shipped the
  partitioned design and silently corrupted rest-state visibility.

### spike-2: Is a boolean `IndexedField` a valid bound?

- **Assumption**: "`IndexedField(default=False)` is genuinely indexed for both values, and
  `filter(status='at-rest', has_open_promises=True)` intersects and hydrates only the intersection."
- **Method**: prototype (worktree-isolated, throwaway model, Redis db 15)
- **Finding**: **CONFIRMED, with two blocking caveats.**

  Index keys are `$IndexF:<Model>:has_open_promises:True` and `:False` — popoto stringifies via
  `str(value)`, capitalized, and **falsy is not skipped**; the `:False` set is fully populated. (Note
  `-` is escaped in value suffixes, so the status set is literally `$IndexF:Job:status:at/-rest` —
  relevant only if hand-inspecting keys.) Hash reads:

  | Query | Rows | Hash reads |
  |---|---|---|
  | `filter(status="at-rest", has_open_promises=True)` | 2 | **4** |
  | `filter(status="at-rest")` | 20 | **40** |

  The lazy-load flip works: taking a record via `query.filter(...)`, setting the flag, and `save()`
  correctly moves it between the `True` and `False` sets. `INDEX_SWAP_LUA` is server-side, so it is
  safe on the production `for job in query.filter(...): job.x = ...; job.save()` shape — the exact
  shape that breaks a partitioned SortedField in spike-1.

  **Caveat 1 (blocking):** `IndexedField(default=False)` *without* `type=bool` hydrates as the
  **string `'False'`**, which is truthy in Python. Raw hash bytes are `b'\xa5False'` (msgpack str)
  versus `b'\xc2'` (msgpack false). The *index* is correct either way, so `filter()` works, but
  `if job.has_open_promises:` is unconditionally `True`. **Declare
  `IndexedField(type=bool, default=False)`.**

  **Caveat 2 (blocking):** rows written before the field existed are invisible to the filter. A
  two-process test — a model without the field writes 3 rows, a model with the field reads them —
  showed the `has_open_promises` index sets do not exist at all for those rows;
  `filter(at-rest, has_open_promises=False)` returned `[]` while `filter(at-rest)` returned all 3.
  The Python default applies at *read* time only; the index is populated by a *write*. Legacy rows
  land in **neither** set, so a `False` sweep would miss them too. A re-save backfill is the fix and
  is sufficient.
- **Confidence**: high
- **Impact on plan**: fixes the field declaration to `type=bool`, and makes the backfill migration
  mandatory and load-bearing rather than defensive. Without the backfill the backstop silently
  under-reports on exactly the pre-existing Jobs most likely to hold a stale promise.

## Data Flow

1. **Entry point**: `worker/__main__.py:960` supervises `_agent_session_health_loop`, one per worker
   process, respawned by `supervise()` on death.
2. **`agent/session_health.py:5143`** awaits `_agent_session_health_check`, then sleeps 300 s
   (`AGENT_SESSION_HEALTH_CHECK_INTERVAL`, `:442`, no env override).
3. **`agent/session_health.py:4505`** calls `_check_jobs_at_rest_with_open_promises` (`:890`),
   deliberately placed *before* the `DISABLE_ORPHAN_REAP` early-return at `:4519` so the kill switch
   cannot disable it.
4. **`models/job.py:254` `sweep_to_rest`** runs first, by design (`models/job.py:262-265`: without it
   the backstop would be "correct logic over permanently-empty input"). It scans
   `filter(status="active")` and transitions idle Jobs past `JOB_AT_REST_AGE_SECONDS` via
   `mark_at_rest()`. **Bounded by rest-by-age; out of scope, and it must keep running first.**
5. **`models/job.py:294` `at_rest_with_open_promises`** — *the change*. Today: scan the whole at-rest
   set, `json.loads` every `goal`, keep the ones with an undischarged promise. After:
   `filter(status="at-rest", has_open_promises=True)` — an index intersection that hydrates only the
   flagged set, then a cheap re-verify against `open_promises()`.
6. **Output**: one `logger.warning("[at-rest-promise] Job %s (room=%s) is at rest with %d open
   promise(s)...")` per flagged Job, carrying the advisories-issued vs promises-authored operator
   metric. **Operator surface only, never human chat; no Job mutation, no discharge, no nag** — the
   Risk 4 no-nag-machine ruling. Discharge is PM-authored via `tools/job_tool promise-remove`.

**The flag's write chokepoint:** every promise mutation funnels through `models/job.py:152`
`_write_goal_data`, reached from `mint` (`:124`, via `save=False`), `add_promise` (`:188-201`),
`remove_promise` (`:203-211`), and `append_goal_version` (`:180-184`). External callers
(`tools/job_tool.py:94,105,114,132`, `bridge/job_router.py:248`) all route through those methods, and
nothing in the repo assigns `job.goal` directly. Deriving the flag inside `_write_goal_data` makes it
un-bypassable by construction.

## Architectural Impact

- **New dependencies**: none. No new imports.
- **Interface changes**: none public. `Job.at_rest_with_open_promises` keeps its signature, return
  type, and fail-open contract. One additive field on the `Job` schema.
- **Coupling**: unchanged. The change stays inside the ORM — no new reach into popoto internals, no
  new Redis key knowledge in `models/job.py`. (This is the material difference from #2636, which does
  take on that coupling.)
- **Data ownership**: unchanged. The `goal` JSON remains the single source of truth for promise state.
  `has_open_promises` is a **derived index projection**, never authoritative — the backstop
  re-verifies against `open_promises()` before flagging.
- **Schema-gate impact**: `docs/plans/durability-room-job-agentrun.md:528-529`, `models/job.py:11-12`,
  and `docs/features/durability-model.md:119` all assert that `status` is the *only* IndexedField.
  This plan makes that false and must amend all three. The gate's machine anti-criterion (line 736,
  `grep -nE "IndexedField" models/room.py models/job.py | grep -iE "pid|uuid|_at\b"` → 0 matches) is
  a *cardinality* rule — never index a pid, uuid, or timestamp — and a two-valued boolean honors it.
  No test introspects `Job._meta.fields` or asserts a field count, so nothing breaks mechanically;
  this is a decision-record obligation.
- **Reversibility**: high. The field is additive; reverting means deleting it and restoring the scan,
  leaving orphan `$IndexF:Job:has_open_promises:*` sets that `repair_indexes` leg 2 already clears on
  the next daily run.

## Appetite

**Size:** Small

**Team:** Solo dev, PM (schema-gate amendment sign-off), code reviewer

**Interactions:**
- PM check-ins: 1 (Open Question 1 — the second IndexedField needs an explicit yes before build)
- Review rounds: 1

One field, one query rewrite, one migration, three doc amendments. The only real overhead is that it
touches a schema the M3 gate ratified one-shot. It was Medium before the issue was rescoped and item
3 left for #2636.

## Prerequisites

Run these with `.venv/bin/python`. A bare `python` on `PATH` is not the project venv and every row
fails with `ModuleNotFoundError: popoto`, which reads as a broken prerequisite rather than a broken
invocation.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB; assert POPOTO_REDIS_DB.ping()"` | Every Job test hits real Redis |
| popoto at/above floor | `.venv/bin/python -c "from config.popoto_floor import assert_popoto_floor; assert_popoto_floor()"` | Index-set encoding and typed-boolean hydration are version-pinned behavior |
| IndexedField accepts a type argument | `.venv/bin/python -c "import inspect; from popoto import IndexedField; assert 'type' in inspect.signature(IndexedField.__init__).parameters or inspect.signature(IndexedField.__init__).parameters.get('kwargs')"` | spike-2 caveat 1 — `type=bool` is mandatory and must be accepted by this popoto version |
| Schema gate amended | `grep -c "Schema Gate Amendment 1" docs/plans/durability-room-job-agentrun.md` | A second IndexedField may not land without the recorded ruling |

## Solution

### Key Elements

- **`has_open_promises`, a derived low-cardinality IndexedField**: a two-valued boolean maintained at
  the single `goal`-writing choke point, so it cannot drift by omission.
- **An index-intersected backstop query**: `filter(status="at-rest", has_open_promises=True)`,
  hydrating only the flagged set, plus a cheap re-verify against the authoritative JSON before
  flagging.
- **One backfill migration**: re-saves every existing Job so its index membership exists. Without it
  the new query is blind to every Job that predates the field.
- **A schema-gate amendment**: the M3 plan text, the model docstring, and the feature doc move from
  "the ONLY IndexedField" to naming both, restating the real cardinality rule so the amendment does
  not read as an erosion of the gate.

### Flow

**Health tick (300 s, per worker)** → [`sweep_to_rest` transitions idle actives to at-rest] →
**at-rest backstop** → [`filter(status="at-rest", has_open_promises=True)`: index intersection,
hydrate only the flagged set] → [re-verify `open_promises()`] → **operator log line per flagged Job**

**Any promise mutation** → [`add_promise` / `remove_promise` / `append_goal_version` / `mint`] →
**`_write_goal_data`** → [derive `has_open_promises` from the promises list] → **save; index
membership swaps server-side**

### Technical Approach

**`build-open-promise-index` — the field and the query.** Declare
`has_open_promises = IndexedField(type=bool, default=False)`. The `type=bool` is mandatory, not
stylistic: without it the value hydrates as the string `'False'`, which is truthy, and every Python
read of the attribute silently inverts (spike-2 caveat 1).

Derive it inside `_write_goal_data` (`models/job.py:152`), the sole writer of `goal`:

```python
self.has_open_promises = any(p.get("removed_ts") is None for p in data.get("promises", []))
```

Set it before the save, including on the `save=False` mint path, so a freshly minted Job's first
`save()` carries a correct value. Then narrow the query:

```python
for job in cls.query.filter(status="at-rest", has_open_promises=True):
    if job.open_promises():          # re-verify against the authoritative JSON
        flagged.append(job)
```

Keep the existing `try/except → logger.warning → []` fail-open wrapper — a backstop query must never
raise into the health loop. The re-verify is what keeps the flag non-authoritative: it costs nothing
on a set that is normally empty, and it means a false-positive flag cannot produce a spurious
operator alert.

**`build-backfill-migration` — the one-pass backfill.** A new field's index membership is **not**
retroactively populated; the Python default applies at read time only (spike-2 caveat 2). Every
pre-existing Job would therefore be invisible to the new filter, and the backstop would silently
under-report on exactly the oldest Jobs — the ones most likely to hold a forgotten promise.

The migration must call `job._write_goal_data(job._goal_data())` (which derives the flag and saves)
rather than a bare `job.save()`, because the derivation lives in `_write_goal_data`, not in `save`.
Register in the `MIGRATIONS` dict (`scripts/update/migrations.py:999-1105`); it may go last, with no
dependency on the strip-before-phantom-purge ordering constraint at `:1064-1073`. Make it idempotent
and resumable, and log progress every N rows so a long run is observable rather than an apparent hang.

**`document-feature` — the schema-gate amendment.** Three texts assert `status` is the only
IndexedField. Amend all three, and record the amendment in the parent plan as an amendment rather than
a silent rewrite, so the gate's decision history stays legible.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `models/job.py` `at_rest_with_open_promises` — the `except Exception → logger.warning → []`
      fail-open wrapper (`:305-306`) is what keeps a Redis hiccup from killing the health loop. Add a
      test that forces `query.filter` to raise and asserts **both** `[] == result` and that a
      `logger.warning` was emitted naming the failure. Today no test covers this path.
- [ ] `models/job.py` `_write_goal_data` — the new derivation must not introduce a swallow. Add a
      test asserting a malformed `goal` (which `_goal_data` already logs and treats as empty,
      `:144-147`) yields `has_open_promises=False` rather than raising.
- [ ] `agent/session_health.py:890` `_check_jobs_at_rest_with_open_promises` — already wrapped; the
      existing test at `tests/unit/test_promise_advisory.py:217` asserts the WARNING log carries the
      job id. Confirm it still passes unchanged.
- [ ] Migration body — the runner records completion by name, so a partially-applied backfill must be
      safe to re-run. Add a test that runs the migration twice and asserts identical end state.
- [ ] No `except Exception: pass` blocks exist in the touched scope — every handler in `models/job.py`
      logs. Verify this holds after the change.

### Empty/Invalid Input Handling

- [ ] `at_rest_with_open_promises` when every at-rest Job has discharged its promises — assert `[]`,
      and assert by row count that no full-population scan occurred (the whole point of the change).
- [ ] `at_rest_with_open_promises` when the at-rest set is empty — assert `[]` without raising.
- [ ] A Job with `goal = None` or `goal = ""` — `_goal_data()` handles it (`:144`); assert
      `has_open_promises` lands `False`.
- [ ] A Job whose promises list is present but every entry has a `removed_ts` — assert the flag flips
      to `False` on the discharging write, not just on the next unrelated save.
- [ ] A legacy Job that predates the field, before the backfill runs — assert it is absent from
      **both** index sets (spike-2 caveat 2), documenting the degraded-but-safe pre-migration state
      rather than pretending it does not exist.

### Error State Rendering

- [ ] No user-visible output in scope. The backstop writes to the **operator** surface only
      (`logger.warning`, never human chat — the Risk 4 no-nag-machine ruling), and mutates nothing.
      Assert the operator log line still renders the job id, room id, open-promise count, and goal on
      the narrowed path. **A silently-narrowed backstop that logs nothing is the exact failure this
      section exists to catch** — the change makes the query cheaper, and the one way it could go
      wrong invisibly is by finding nothing and saying nothing.

## Test Impact

- [ ] `tests/unit/test_job_model.py::test_at_rest_with_open_promises_query` (`:130`) — UPDATE: it
      asserts flagged-set **membership by id**, not list length, so it tolerates the narrowed scan.
      Add a companion case asserting a Job whose promises were all discharged is **absent** from the
      result, which the current test does not check.
- [ ] `tests/unit/test_job_model.py::test_sweep_to_rest_transitions_stale_active_jobs` (`:110`) —
      UPDATE (verify only): `sweep_to_rest` is untouched, but it now runs against Jobs carrying the
      new field. Confirm rather than assume.
- [ ] `tests/unit/test_job_model.py::TestRecencyLookup` (`:141-154`) — UPDATE (verify only):
      `recent_for_room` is **not** changed by this plan (it moved to #2636). These must pass
      unmodified; if they do not, something touched out-of-scope code.
- [ ] `tests/unit/test_promise_advisory.py::test_stale_active_job_reaches_the_operator_log_end_to_end`
      (`:238`) — UPDATE: back-dates `last_active_at` to `now - JOB_AT_REST_AGE_SECONDS - 60` and
      expects the swept Job to be flagged in the same invocation. Under the new field the Job must
      carry `has_open_promises=True` — it will, because the test adds its promise through
      `add_promise`, which now derives the flag. **Verify explicitly; this is the highest-value
      regression test in the suite for this change**, because it is the only one that exercises
      sweep-then-flag in a single tick.
- [ ] `tests/unit/test_promise_advisory.py::test_backstop_surfaces_at_rest_jobs_with_open_promises`
      (`:217`) — UPDATE (verify only): asserts the WARNING log carries the job id.
- [ ] `tests/unit/test_promise_advisory.py::test_backstop_is_invoked_from_the_periodic_sweep`
      (`:266`) — UPDATE (verify only): a **source-text** assertion that
      `await _check_jobs_at_rest_with_open_promises()` appears after
      `await _check_at_rest_owed_communication()` in `session_health.py`. Do not relocate or rename
      that call.
- [ ] `tests/unit/test_job_tool.py` — UPDATE (verify only): `tools/job_tool.py` calls
      `add_promise`/`remove_promise`, which now also write the flag. Confirm the CLI output is
      unchanged.
- [ ] `tests/unit/test_migrations.py::test_registered_in_migrations_dict` (`:266`) and
      `::test_every_migration_name_is_unique` (`:310`) — UPDATE: add the new migration name. The
      ordering test at `:297` pins strip-migrations ahead of the phantom purge; the new backfill has
      no such dependency.
- [ ] NEW: `tests/unit/test_job_model.py` — a case seeding an at-rest population much larger than the
      flagged set and asserting the hydrated row count tracks the flagged set, not the population.
      This is the test that proves the bound actually bounds; without it the change is unverified.
- [ ] NEW: `tests/unit/test_job_model.py` — a case asserting `remove_promise` of the *last* open
      promise flips the flag to `False` and removes the Job from the backstop result.

## Rabbit Holes

- **Bounding `sweep_to_rest` too.** It scans `filter(status="active")` on the same tick, so it is
  tempting to fix both at once. It is genuinely bounded by rest-by-age (72 h), and bounding it
  further needs exactly the `at_rest_since` machinery spike-1 disqualified. Leave it.
- **Building a general drift reconciler for derived index fields.** `repair_indexes` cannot repair
  value-vs-derivation drift for `has_open_promises`, or for any derived field — it rebuilds index sets
  *from* stored hash values, so it faithfully reproduces a wrong one. A general reconciler is a real
  idea and a much larger design. The single choke point plus the backfill plus the re-verify is the
  proportionate answer here.
- **Fixing popoto's 2× hydration while in the neighborhood.** Every `.filter()` in the repo pays two
  hash reads per record (spike-1 incidental). A real 2× win and a real popoto-internals fix affecting
  every model. Separate issue (#2639).
- **Reaching for the sibling `_check_at_rest_owed_communication` pattern.** It bounds by iterating a
  handful of non-terminal statuses, which works only because live AgentSessions are few. Applying the
  same shape here just renames the unbounded scan.
- **Doing #2636's work opportunistically.** The bounded `recent_for_room` read is prototyped,
  measured, and sitting one function away in the same file. It has an owner, an upstream dependency,
  and a cutover gate. Touching it here would make this PR's blast radius unreviewable.

## Risks

### Risk 1: `has_open_promises` drifts from the `goal` JSON

**Impact:** a **false negative** — an at-rest Job with an open promise whose flag says `False` — is
never surfaced. That is exactly the incident the backstop exists to catch, now silent, and silence is
its normal output so nobody notices. `repair_indexes` **cannot** repair this (spike-1 corollary):
`rebuild_indexes()` reconstructs index sets from stored hash values, so a wrong value is faithfully
reproduced.
**Mitigation:** derive the flag inside `_write_goal_data` (`models/job.py:152`), the sole writer of
`goal` — `mint`, `add_promise`, `remove_promise`, and `append_goal_version` all funnel through it, and
no caller in the repo assigns `job.goal` directly. The backfill migration covers legacy rows. The
defensive `open_promises()` re-verify catches false *positives* for free. Residual exposure is a
future writer that assigns `job.goal` directly, which the Verification anti-criterion row detects
mechanically.

### Risk 2: The backfill does not run, or runs partially, and the backstop goes quietly blind

**Impact:** every Job written before the field existed is in **neither** index set (spike-2 caveat 2),
so `filter(has_open_promises=True)` misses all of them. The backstop reports zero and looks healthy.
This is the same false-negative-is-silent problem as Risk 1, reached by a different route, and it
affects the *oldest* Jobs — the population most likely to hold a forgotten promise.
**Mitigation:** the migration is the entire upgrade path and ships in the same release, registered in
`MIGRATIONS` so `/update` propagates it to every machine with no operator action. Make it idempotent
and resumable. Add the test asserting a pre-field Job is absent from both sets, so the degraded state
is documented behavior rather than a surprise. Note the failure is *safe* (observability degrades,
nothing breaks) and self-healing per Job on its next promise write.

### Risk 3: Stale "only IndexedField" phrasing survives in the docs — RESOLVED, now an execution item

**Impact:** three texts state, as a ratified M3 decision, that `status` is the only IndexedField.
Leaving any of them standing after the field lands leaves the plan-of-record contradicting the code —
precisely the "historical artifact in docs" the repo's development principles forbid.
**Mitigation:** the governance half is **already done**. Schema Gate Amendment 1 (`bc3f682a5`,
approved 2026-08-07) grants the second IndexedField and restates the invariant the gate actually
enforces: *no IndexedField may hold an unbounded-cardinality value — never index a pid, uuid, or
timestamp.* A two-valued derived boolean honors it, and the old phrasing described the field list
rather than the safety property. What remains is mechanical: `document-feature` supersedes the stale
phrasing in `models/job.py:11-12`, `docs/features/durability-model.md:119`, and the
`durability-room-job-agentrun.md:528-529` block, pointing each at the amendment. The gate's machine
anti-criterion (line 736) greps for IndexedFields mentioning `pid|uuid|_at`; a two-valued boolean
passes, and no test asserts a field count.

### Risk 4: The backfill runs against a large Job population during `/update`

**Impact:** the migration iterates every Job and writes each one. `scripts/update/migrations.py` runs
during `/update`, which gates the bridge restart, so a slow pass delays the restart on every machine.
**Mitigation:** measured at amendment time (`bc3f682a5`), the live at-rest Job population is
**zero** — the model shipped 2026-08-07. The backfill is a no-op today and will never be cheaper, so
build it now rather than after the phase-2 cutover multiplies the population. Still batch the writes,
skip rows already correct, log progress, and record completion by name in
`data/migrations_completed.json` so it never re-runs: the migration has to stay correct for machines
that come online later with a non-empty table.

### Risk 5: The change is invisible in production because the flagged set is normally empty

**Impact:** the backstop's correct output is almost always zero Jobs. A bug that makes it return zero
*for the wrong reason* is indistinguishable from healthy operation in the logs. Every other risk here
funnels into this one.
**Mitigation:** the row-count test (Test Impact, NEW) proves the bound bounds without relying on
production signal, and the `remove_promise`-flips-the-flag test proves the flag tracks state in both
directions. Neither depends on a non-empty production population. This is why the Failure Path
strategy insists the operator log line still renders on the narrowed path.

## Race Conditions

### Race 1: Concurrent `save()` on the same Job while the backfill migration is iterating

**Location:** `scripts/update/migrations.py` (new migration) versus `models/job.py:221` `touch()` /
`:230` `revive()` / `:152` `_write_goal_data`, called from the bridge and worker.
**Trigger:** the migration reads a Job; before it writes, a live message binds to that Job and calls
`touch()`. The migration's stale in-memory copy then overwrites the fresher `last_active_at`.
**Data prerequisite:** the migration's snapshot must still reflect the record at write time.
**State prerequisite:** no concurrent writer for that Job between read and write.
**Mitigation:** popoto offers no row-level optimistic concurrency, so prevent the overlap rather than
resolve it — `/update` already sequences migrations before the bridge restart, so the migration runs
while the bridge is down. The writes are additionally *convergent*: the flag derives from the record's
own `goal`, so the worst case is one lost `touch()` timestamp on a Job any subsequent message
refreshes. The migration must **not** assign `_now()`.

### Race 2: A promise is discharged between the index read and the re-verify

**Location:** `models/job.py` `at_rest_with_open_promises` (new body), between `query.filter(...)`
resolving the index intersection and the `job.open_promises()` re-verify.
**Trigger:** the PM calls `tools/job_tool promise-remove` in the window between the two.
**Data prerequisite:** none — the re-verify reads the authoritative JSON, which is the newer truth.
**State prerequisite:** none.
**Mitigation:** benign by construction, and the reason the re-verify is ordered *after* the index read
rather than trusted instead of it. The stale index entry is dropped by the re-verify, so the outcome
is "not flagged", which is correct. The inverse (a promise *added* in the window) yields "not flagged
this tick, flagged next tick, 300 s later" — acceptable for an observability backstop whose whole
subject matter is promises left open for days.

### Race 3: Two workers run the health tick concurrently

**Location:** `agent/session_health.py:913-917`.
**Trigger:** two workers' health loops fire in the same window; both call `sweep_to_rest`, both
attempt `mark_at_rest()` on the same idle Job, both then scan.
**Data prerequisite:** the at-rest transition must be visible to the promise scan that follows — which
is exactly why `sweep_to_rest` is called first (`models/job.py:262-265`).
**State prerequisite:** none beyond idempotence.
**Mitigation:** pre-existing and unchanged by this plan. `mark_at_rest()` is idempotent
(`INDEX_SWAP_LUA` is server-side and atomic), and the backstop mutates nothing. The new flag is
likewise written idempotently from the record's own `goal`. Duplicate operator log lines across
workers are pre-existing behavior, not a regression introduced here.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2636] Bounding `Job.recent_for_room`. Moved out of this issue by comment
  `5212992885`; it is gated on an upstream popoto release and is the #2494 phase-2 cutover
  prerequisite. Spike evidence gathered here was posted to that issue. **This plan must not touch
  `recent_for_room`.**
- [SEPARATE-SLUG #2639] popoto's `list(QueryBuilder)` double-execution — every `.filter()` in the repo
  pays 2 hash reads per hydrated record. Real 2× win, but a popoto-internals fix affecting every
  model.
- [SEPARATE-SLUG #2640] `$SortF:Job:*` sorted-set orphan pruning in `Job.repair_indexes`. Leg 2 clears
  `$IndexF:Job:*` and leaves the sorted-set partitions untouched, with no reaper. Unrelated to the
  IndexedField this plan adds, whose orphans leg 2 already handles.
- [SEPARATE-SLUG #2641] The `$SortedF:` prefix typo and missing `Job` entry in
  `.claude/hooks/validators/validate_no_raw_redis_delete.py` (see also #2638 on that hook's scoping).
  A hook fix; mixing it into a model PR muddies the blast radius.
- [SEPARATE-SLUG #2494] Bounding `sweep_to_rest`'s active-set scan. Rest-by-age already bounds it at
  72 h, and the machinery that would bound it further was disqualified by spike-1. Belongs to the
  parent durability epic if it ever becomes real.

## Update System

The backfill ships as **one** entry in `scripts/update/migrations.py` (registered in the `MIGRATIONS`
dict at `:999-1105`, recorded once by name in `data/migrations_completed.json`), so `/update`
propagates it to every machine with no operator action. That is the entire update-system surface:

- No new dependencies, config files, or secrets.
- No change to `scripts/remote-update.sh` or the `/update` skill body.
- No change to `.env.example` or `config/settings.py`.
- Migration ordering: no dependency on the strip-migrations-before-phantom-purge constraint
  (`migrations.py:1064-1073`); the new entry may go last.
- Existing installations: until the migration runs on a given machine, that machine's at-rest backstop
  under-reports for Jobs written before the field existed (spike-2 caveat 2). Degraded but safe — the
  backstop is observability-only, and each Job self-heals on its next promise write.

## Agent Integration

No agent integration required — this is a model-internal change behind an unchanged public method
signature.

- No new CLI entry point in `pyproject.toml [project.scripts]`. `tools/job_tool.py` already exposes
  the Job surface the agent uses (`list`, `create`, `promise-add`, `promise-remove`); its
  `promise-add`/`promise-remove` paths pick up the flag derivation transparently through
  `add_promise`/`remove_promise`, with no wiring change.
- No new bridge import. `agent/session_health.py:917` already calls `at_rest_with_open_promises`; its
  signature, return type, and fail-open contract are unchanged.
- Integration coverage: `tests/integration/test_job_routing.py` exercises the Job surface end to end.
  Confirm it passes unmodified — that is the assertion that the agent-reachable path still works.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/durability-model.md:119` — "`Job.status` is the only IndexedField
      (low-cardinality ...)". Amend to name both IndexedFields and restate the actual cardinality rule
      (never index a pid, uuid, or timestamp), so the amendment reads as a clarification of the rule
      rather than an exception to it.
- [ ] Update the `docs/features/README.md:67` Durability Model row summary to mention the indexed
      at-rest promise backstop.

### Plan-of-record Documentation

- [ ] Update `docs/plans/durability-room-job-agentrun.md:528-529` — the ratified schema block still
      reads "`Job`: only the low-cardinality `status`". Supersede that phrasing and point it at
      **Schema Gate Amendment 1**, which `bc3f682a5` already appended to the same document. Do not
      rewrite the original ruling — the amendment pattern is the one this repo just established, and
      the decision history is the point.

### Inline Documentation

- [ ] Update the `models/job.py` module docstring (`:11-12`) — "`status` is the ONLY IndexedField"
      becomes the two-field statement plus the cardinality rule.
- [ ] Document on the field declaration why `type=bool` is mandatory (without it the value hydrates as
      the truthy string `'False'` — spike-2 caveat 1). A future reader will otherwise "simplify" it.
- [ ] Document in `at_rest_with_open_promises` that `has_open_promises` is a derived projection, never
      authoritative; that the `open_promises()` re-verify is deliberate, not redundant; and that
      `repair_indexes` cannot reconcile value-vs-derivation drift.
- [ ] Document in `_write_goal_data` that it is the sole derivation point and that any new `goal`
      writer must go through it.

## Success Criteria

- [ ] `Job.at_rest_with_open_promises` hydrates only Jobs carrying an open promise — proven by a test
      that seeds an at-rest population much larger than the flagged set and asserts the hydrated row
      count tracks the flagged set, not the population
- [ ] The set of Jobs the backstop surfaces is identical before and after the change for the same
      seeded state, and the operator log line renders unchanged
- [ ] `remove_promise` of the last open promise removes the Job from the backstop result on the next
      tick, and `add_promise` re-adds it
- [ ] The backfill migration is registered in `MIGRATIONS`, idempotent across two runs, and leaves
      every pre-existing Job with a correct `has_open_promises` value
- [ ] `models/job.py`, `docs/features/durability-model.md`, and
      `docs/plans/durability-room-job-agentrun.md` agree on the amended schema — no surviving
      "only IndexedField" claim
- [ ] `Job.recent_for_room` is untouched (it belongs to #2636) — `git diff` confirms
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions needed — no expected-failure markers exist for these paths

## Team Orchestration

### Team Members

- **Builder (job-model)**
  - Name: `job-model-builder`
  - Role: `models/job.py` — the `has_open_promises` field, its derivation in `_write_goal_data`, and
    the narrowed `at_rest_with_open_promises` query
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Builder (migration)**
  - Name: `migration-builder`
  - Role: the backfill migration in `scripts/update/migrations.py`, its registration, and the
    migration-registry test updates
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Test engineer (job-tests)**
  - Name: `job-test-engineer`
  - Role: the Test Impact and Failure Path Test Strategy checklists — updating existing cases and
    adding the row-count, flag-flip, malformed-goal, and pre-field-legacy cases
  - Agent Type: test-engineer
  - Resume: true

- **Validator (job-model)**
  - Name: `job-model-validator`
  - Role: verifies the hydration row count, result-set parity against the pre-change implementation,
    the fail-open contract, and that `recent_for_room` was not touched
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `job-documentarian`
  - Role: the Documentation checklist, including the schema-gate amendment in the parent plan
  - Agent Type: documentarian
  - Resume: true

### Domain framing for `redis-popoto` tasks

Paste into both builder assignments: never write raw Redis on Popoto-managed keys — reads and writes
go through the ORM (`Model.query.filter()`, `instance.save()`, `instance.delete()`). **This plan needs
no raw Redis at all**; the entire change stays inside the ORM, and a diff that reaches for
`POPOTO_REDIS_DB` outside the pre-existing `repair_indexes` body is a signal the design drifted. Any
manual testing uses a `test-`/`dbg-`-prefixed `project_key` or `room_id` and deletes through the ORM
afterward, scoped by that prefix. Never run a bulk operation unscoped.

## Step by Step Tasks

### 1. Derived `has_open_promises` IndexedField and narrowed backstop query

- **Task ID**: build-open-promise-index
- **Depends On**: none
- **Validates**: `tests/unit/test_job_model.py`, `tests/unit/test_promise_advisory.py`
- **Informed By**: spike-2 (confirmed: `type=bool` mandatory or the value hydrates as the truthy
  string `'False'`; lazy-load flip is safe under `INDEX_SWAP_LUA`; legacy rows need a write backfill;
  40 → 4 hash reads), spike-1 (the `at_rest_since` SortedField the issue suggested is disqualified in
  both partitioned and unpartitioned forms; IndexedField intersection bounds hydration before the hash
  reads)
- **Assigned To**: `job-model-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: false
- Declare `has_open_promises = IndexedField(type=bool, default=False)`. `type=bool` is mandatory.
- Derive it inside `_write_goal_data` — the sole writer of `goal` — so no caller can bypass it. Set it
  before the save, including on the `save=False` mint path.
- Narrow `at_rest_with_open_promises` to `filter(status="at-rest", has_open_promises=True)`, keeping
  the fail-open wrapper, and add the `open_promises()` re-verify before flagging.
- Do **not** touch `recent_for_room` — it belongs to #2636.
- Document the `type=bool` requirement, the derived-not-authoritative contract, and why the re-verify
  is deliberate.

### 2. Backfill migration

- **Task ID**: build-backfill-migration
- **Depends On**: build-open-promise-index
- **Validates**: `tests/unit/test_migrations.py`
- **Informed By**: spike-2 caveat 2 (a new field's index membership is not retroactively populated;
  legacy rows are invisible to the filter until written, and land in neither index set)
- **Assigned To**: `migration-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: false
- Iterate every Job and call `job._write_goal_data(job._goal_data())` — a bare `save()` is **not**
  sufficient, because the derivation lives in `_write_goal_data`.
- Do **not** assign a timestamp; preserve each Job's `last_active_at` instant.
- Make it idempotent and resumable; skip rows already correct; log progress every N rows so a long run
  is observable rather than an apparent hang.
- Register in the `MIGRATIONS` dict (`scripts/update/migrations.py:999-1105`); may go last, no
  ordering dependency.
- Update `tests/unit/test_migrations.py::test_registered_in_migrations_dict` and
  `::test_every_migration_name_is_unique`.

### 3. Test suite updates

- **Task ID**: build-tests
- **Depends On**: build-open-promise-index, build-backfill-migration
- **Validates**: `tests/unit/test_job_model.py`, `tests/unit/test_promise_advisory.py`,
  `tests/unit/test_job_tool.py`, `tests/unit/test_migrations.py`
- **Informed By**: spike-2 (the pre-field-legacy state; the `type=bool` trap)
- **Assigned To**: `job-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist top to bottom. Every "UPDATE (verify only)" item must actually be
  run, not assumed.
- Add the new cases from the Failure Path Test Strategy: the row-count proof, the
  last-promise-discharged flag flip, malformed `goal` → `has_open_promises=False`, the fail-open
  `logger.warning` assertion, and the pre-field-legacy absence case.
- **Required, not optional:** the migration-honesty test. Seed a Job row written *without*
  `has_open_promises` (write the hash so the index sets are unpopulated), assert it is invisible to
  `filter(status="at-rest", has_open_promises=True)`, run the migration, assert it is now visible.
  The live population is zero, so this test is the only thing that will ever exercise the migration
  — a no-op migration with no test is unverified code (Resolved Questions).
- Do not relocate or rename `await _check_jobs_at_rest_with_open_promises()` in
  `agent/session_health.py` — `tests/unit/test_promise_advisory.py:266` asserts on the source text.

### 4. Schema-gate amendment and documentation

- **Task ID**: document-feature
- **Depends On**: build-open-promise-index, build-backfill-migration
- **Assigned To**: `job-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Work the Documentation checklist: the `models/job.py` module docstring,
  `docs/features/durability-model.md:119`, the `docs/features/README.md:67` row, and the parent plan's
  ratified-schema block at `durability-room-job-agentrun.md:528-529`.
- Record the schema change as an amendment; do not silently rewrite the M3 ratification.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-open-promise-index, build-backfill-migration, build-tests, document-feature
- **Assigned To**: `job-model-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm every Success Criterion.
- Confirm `recent_for_room` is byte-identical to `main` — out-of-scope drift into #2636 is the most
  likely way this PR goes wrong.
- Confirm no surviving "only IndexedField" claim across code, feature docs, and the parent plan.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `./scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_promise_advisory.py tests/unit/test_job_tool.py tests/unit/test_migrations.py -q` | exit code 0 |
| Full suite | `./scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Boolean field is typed | `grep -c "has_open_promises = IndexedField(type=bool" models/job.py` | output > 0 |
| At-rest scan is index-intersected | `grep -c 'filter(status="at-rest", has_open_promises=True)' models/job.py` | output > 0 |
| Flag derived at the choke point | `sed -n '/def _write_goal_data/,/def goal_versions/p' models/job.py \| grep -c "has_open_promises"` | output > 0 |
| Anti-criterion: `goal` written only via the choke point | `grep -rnE '(self\|job\|_job)[a-z_]*\.goal *=[^=]' models/job.py tools/job_tool.py bridge/job_router.py \| grep -vc 'json.dumps(data)'` | match count == 0 |
| Anti-criterion (#2636): `recent_for_room` untouched | `git diff origin/main -- models/job.py \| grep -c "^[-+].*recent_for_room"` | match count == 0 |
| Anti-criterion (#2636): no raw sorted-set read added | `grep -cE "\.(zrevrange\|zrangebyscore\|zrange)\(" models/job.py` | match count == 0 |
| Anti-criterion (spike-1): no `at_rest_since` field | `grep -c "at_rest_since" models/job.py` | match count == 0 |
| Anti-criterion: no new raw Redis writes | `grep -cE "POPOTO_REDIS_DB\.(zrem\|zadd\|srem\|sadd)\(" models/job.py` | match count == 0 |
| Migration registered | `python -c "from scripts.update.migrations import MIGRATIONS; print(sum(1 for k in MIGRATIONS if 'promise' in k.lower()))"` | output > 0 |
| Schema claim amended in code | `grep -c "ONLY IndexedField" models/job.py` | match count == 0 |
| Schema claim amended in docs | `grep -c "only IndexedField" docs/features/durability-model.md` | match count == 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Skeptic) | Prerequisites row 3 guards the plan's most load-bearing claim but passes vacuously. `IndexedField.__init__` in popoto 1.8.0 has signature `(self, *args, **kwargs)`, so `'type' in parameters` is False and the check falls through to `parameters.get('kwargs')`, a truthy `Parameter`. It returns PASS for any popoto that accepts `**kwargs`, including one that silently discards `type=` — which per spike-2 caveat 1 yields the truthy string `'False'` and inverts every Python read. | pending | The signature check cannot work (verified against popoto 1.8.0). Assert on hydration, not declaration: `assert isinstance(<hydrated>.has_open_promises, bool)` — `str` is never a `bool` subclass. The raw-byte discriminator, both reproduced during critique: typed stores `b'\xc3'`/`b'\xc2'` (msgpack bool), untyped stores `b'\xa5False'` (msgpack str). |
| CONCERN | Risk & Robustness (Operator) | Risk 5 correctly names "returns zero for the wrong reason" as indistinguishable from health, but mitigates it with unit tests only. Nothing in production separates "0 flagged because nothing is owed" from "0 flagged because rows sit in neither index set" — the spike-2 caveat 2 blind state that Risk 2 says arrives if the backfill does not run or runs partially. | pending | Cardinalities are already in hand on the daily path: `repair_indexes` leg 2 enumerates `POPOTO_REDIS_DB.keys("$IndexF:Job:*")` and calls `smembers` per key (`models/job.py:395-396`). Accumulate `len(members)` before the `delete(index_key)` at `models/job.py:403` and log the `has_open_promises` `True`+`False` sum next to the existing stale-member warning. Invariant: the two sets sum to the Job hash count; a lower sum means unbackfilled/blind rows. Sets are keyed literally `...:True` / `...:False` (popoto uses capitalized `str(value)`). |
| CONCERN | Scope & Value (User) | Success Criterion 2 (the backstop surfaces an identical set before and after) is the criterion protecting the user-visible contract, but no task, test, or Verification row states how it is checked — and task 1 deletes the "before" implementation, so the comparand is gone by verification time. As written it gets signed off by inspection, which is how a silently-narrowed backstop ships. | pending | Carry the old one-line scan as an in-test oracle rather than needing pre-change code: assert `{j.job_id for j in Job.at_rest_with_open_promises()}` equals `{j.job_id for j in Job.query.filter(status="at-rest") if j.open_promises()}`. Compare **sets of `job_id`**, never list order — the new path resolves through `set.intersection` (popoto `models/query.py:1989`) and set order is not the old scan's insertion order, so an order-sensitive assert fails spuriously. Composes with the separate row-count bound assertion in the same test. |
| CONCERN | History & Consistency (Consistency Auditor) | The two doc-amendment Verification rows contradict the amendment they verify. Documentation mandates naming both IndexedFields, and ratified Amendment 1 phrases it as "`Job` now declares **two** IndexedFields"; any natural rendering contains the substring `only IndexedField`, so `grep -c "only IndexedField" docs/features/durability-model.md` returns non-zero and FAILS on a correct document. The rows also disagree on case: the code row greps `"ONLY IndexedField"` case-sensitively, so a lowercase survival in `models/job.py` passes the gate meant to catch it. | pending | Pin the negative to the stale singular claim rather than a bare substring: `grep -icE "status.*(is the )?only IndexedField"` → 0, run over **both** `models/job.py` and `docs/features/durability-model.md` with the same `-i`. Pair each with a positive row `grep -c "has_open_promises" <file>` → greater than 0, catching a documentarian who deletes the sentence instead of amending it. The casing split that caused the drift: `models/job.py:11-12` says "ONLY IndexedField", `docs/features/durability-model.md:119` says "only IndexedField". |
| CONCERN | History & Consistency (Consistency Auditor) | Appetite line 338 still states "PM check-ins: 1 (Open Question 1 — the second IndexedField needs an explicit yes before build)", gating build on a PM sign-off, but the plan has **no Open Questions section at all** — revision `b41587647` replaced it with "## Resolved Questions", which opens "No open questions remain." The schema-gate question it names was separately closed by Amendment 1 (`bc3f682a5`) and now sits under Resolved as APPROVED. The Appetite line is a dangling reference to a removed section that declares a human gate which no longer exists; a reader following it will block build needlessly or hunt for an approval already granted. | pending | Change the line to "PM check-ins: 0 — the schema-gate question is resolved by Schema Gate Amendment 1 (`bc3f682a5`)". This survived the finalize-to-Ready revision, which rewrote the Open Questions section without updating the Appetite cross-reference into it, so grep for other survivors of the same edit: `grep -n "Open Question" docs/plans/job-model-scaling-followups.md` should return no references outside the Resolved Questions section itself. Verified during critique: the live `Job` population is **0 hashes**, confirming Risk 4's no-op claim. |
| NIT | Scope & Value (Simplifier) | Appetite sizes this **Small** with "Team: Solo dev" and "One field, one query rewrite, one migration, three doc amendments", but Team Orchestration names five distinct agents across five tasks, every one `Parallel: false`. Five sequential handoffs exceeds the stated appetite, and the two sections disagree on how many people are involved. | pending | — (NIT, exempt) |
| NIT | History & Consistency (Consistency Auditor) | Architectural Impact and Risk 3 both cite the schema gate's machine anti-criterion as "line 736" of `docs/plans/durability-room-job-agentrun.md`. It is at **line 754** — `bc3f682a5` inserted Amendment 1 at lines 539ff and pushed the Verification table down. The Freshness Check folded that commit into Risks 3 and 4 without re-resolving this number. | pending | — (NIT, exempt) |

---

## Resolved Questions

No open questions remain. The plan is finalized and cleared to build.

- **Is the `open_promises()` re-verify worth keeping, or is it belt-and-braces?** **KEEP IT.** It
  costs nothing — the flagged set is normally empty — and it makes a false-positive flag
  unobservable. It deliberately does *not* protect against false negatives; that direction is
  prevented structurally by the `_write_goal_data` choke point plus the backfill. Because "defensive
  code with no test exercising its failure" is a fair critique, the decision is made explicit here
  and `document-feature` must record it inline, so a future reader does not delete it as a redundant
  double check.

- **Should the backfill migration exist at all, given the live population is zero?** **BUILD IT.**
  Schema Gate Amendment 1 measured the live Job population at **zero** (re-verified at finalization:
  `Job.query.all()` → 0 rows), so the migration is a no-op on every machine today — which is exactly
  the argument for shipping it now rather than an argument against shipping it. A new field's index
  membership is never retroactively populated (spike-2 caveat 2), so without the migration the
  backstop under-reports silently on any row written before the field existed, and machines that come
  online later with a non-empty table have no other repair path. The honest objection — a migration
  that provably does nothing is a migration nobody will notice is broken — is answered by the test
  named in `build-tests`: seed a row written *without* the field, run the migration, assert the row
  becomes visible to `filter(status="at-rest", has_open_promises=True)`. That test is a hard
  requirement, not a nice-to-have; without it the migration is unverified code.

### Previously resolved

- **Schema-gate amendment — approve a second IndexedField on `Job`?** **APPROVED** by Schema Gate
  Amendment 1 (`bc3f682a5`, 2026-08-07), recorded in `docs/plans/durability-room-job-agentrun.md`.
  The gate's invariant was never "`status` is the only IndexedField" — it is "no IndexedField holds
  an unbounded-cardinality value", which a two-valued derived boolean honors. The amendment also
  disqualifies an alternative this plan had not considered: refining the `status` enum with an
  `at-rest-owed` value bounds identically and needs no new field, but encodes a boolean into a
  lifecycle enum, so every present and future reader of `filter(status="at-rest")` would silently
  miss owed Jobs — a wrong-answer failure with no test-time signal. No action left beyond the
  documentation supersession tracked in `document-feature`.
