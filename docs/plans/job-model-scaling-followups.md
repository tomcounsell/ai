---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2634
last_comment_id:
---

# Job model scaling follow-ups: bound the candidate lookup and the at-rest promise scan

## Problem

The `Job` model shipped with M3 (PR #2631) carries two unbounded Redis scans that were acceptable
while Job routing runs in shadow and no production Room has more than a handful of Jobs. Both stop
being acceptable the moment the phase-2 authoritative cutover moves the router onto the synchronous
dispatch path, and one of them degrades continuously with no ceiling at all.

**Current behavior:**

1. **`Job.recent_for_room` hydrates the whole Room to pick five rows.** `models/job.py:239-251` calls
   `cls.query.filter(room_id=room_id, last_active_at__gte=_EPOCH)` — a degenerate range whose only
   real bound is the `room_id` partition — then sorts in Python and slices `[:limit]`. popoto
   resolves that filter to `ZRANGEBYSCORE -inf +inf` and pipelines an `HGETALL` for **every** member
   (`popoto/models/query.py:2376,2680`) before the slice ever runs. Measured on a 30-Job Room with
   `limit=5`: **60 HGETALLs** versus the 5 a bounded read needs. Jobs are immortal by design
   (`models/job.py:25-26`, no `Meta.ttl`), so a long-lived Room's candidate lookup grows without a
   ceiling. `docs/plans/durability-room-job-agentrun.md:708` names this a prerequisite for the
   phase-2 cutover, at which point the lookup's latency becomes intake latency.

2. **`Job.at_rest_with_open_promises` scans and JSON-parses the entire at-rest population every
   300 seconds, per worker.** `models/job.py:294-307` iterates `filter(status="at-rest")` and calls
   `open_promises()` on each, which `json.loads` the whole `goal` blob (`models/job.py:142-150`).
   The call chain is `agent/session_health.py:917` → `_agent_session_health_check` (:4505) →
   `_agent_session_health_loop` (:5143), sleeping `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300`
   (:442). Rest-by-age bounds the *active* set at 72h; nothing bounds the at-rest set, and at-rest
   is the steady state every Job eventually reaches. The scan cost therefore rises monotonically
   with lifetime Job count, forever, to surface a set that is almost always empty.

3. **A latent tz defect makes the ZSET scores untrustworthy.** popoto encodes `datetime` with
   `strftime` and decodes with a bare `strptime` — no tzinfo (`popoto/models/encoding.py:87-96`).
   A Job reloaded from Redis carries a *naive* `last_active_at`; any subsequent `save()` recomputes
   the sorted-set score as `naive.timestamp()`, which Python reads as **local** time. Measured skew
   on this machine (UTC+07): **-25200 s**, exactly the offset, on `add_promise()` and
   `mark_at_rest()`. It is invisible today only because `recent_for_room` re-sorts in Python on the
   uniformly-naive decoded value. The moment a ZREVRANGE trusts the score, it becomes an ordering
   defect.

**Desired outcome:**

- `Job.recent_for_room` issues one `ZREVRANGE` and at most `limit` hydrations, independent of Room
  size, and its ordering is trustworthy because ZSET scores are correct on every write path.
- `Job.at_rest_with_open_promises` hydrates only Jobs that actually carry an open promise — normally
  zero — instead of the whole at-rest population.
- The phase-2 authoritative cutover is unblocked on its named scaling prerequisite.

## Freshness Check

**Baseline commit:** `0a53ad22f976dd42221eb1ae0150648729f69c90`
**Issue filed at:** 2026-08-07T04:56:42Z
**Disposition:** **Minor drift** — one of the three issue items is already done; the other two hold
and are worse than the issue states.

**File:line references re-verified:**

- `models/job.py` `Job.repair_indexes` leg 2 — issue claimed "a bare per-member `exists()`
  round-trip" — **GONE, already fixed.** Now at `models/job.py:393-403`, batching `EXISTS` at
  `batch_size = 5000` through a non-transactional pipeline, byte-for-byte the
  `models/agent_session.py:2423-2428` precedent the issue asked to port.
- `models/job.py:294-307` `at_rest_with_open_promises` — **still holds.** Unbounded
  `filter(status="at-rest")` with a `json.loads` per row.
- `models/job.py:239-251` `recent_for_room` — **still holds.** Post-hydration `[:limit]`.
- `docs/features/durability-model.md:44,119` — both references still accurate.

**Cited sibling issues/PRs re-checked:**

- **#2494** (Durability refactor: Room / Job / AgentSession) — still **OPEN**, the parent epic. This
  issue is a carve-out of its M3 block.
- **PR #2631** (Durability M3) — **MERGED 2026-08-07T04:58:06Z**, 96 seconds after this issue was
  filed. It is the PR whose substrate review produced these three notes.

**Commits on main since issue was filed (touching referenced files):**

- `0a53ad22f` "Pipeline Job.repair_indexes' stale-member scan instead of one round trip each" —
  **already fixes issue item 1.** Landed ~46 minutes after the issue was filed. Item 1 is dropped
  from scope; no work remains.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/durability-room-job-agentrun.md`
(modified 2026-08-07 12:12) is the **parent** plan, not a competitor. Its line 708 explicitly names
#2634 as the bounded-ZREVRANGE prerequisite for the phase-2 cutover, and lines 528-529 hold the
ratified Job schema this plan proposes to amend. Coordination obligation: this plan must update that
plan's schema-gate text, not silently contradict it.

**Notes:** The drift is favourable — a third of the issue evaporated — but the recon also surfaced a
prerequisite the issue did not know about (the tz score skew, item 3 above), so net scope is roughly
unchanged and the *risk* profile is higher than "three mechanical changes" implied.

## Prior Art

`gh issue list --state closed --search "Job scaling recent_for_room ZREVRANGE at-rest"` and
`gh pr list --state merged --search "Job recent_for_room bounded scan"` both returned empty. The Job
model is 24 hours old; there is no prior attempt to fix these paths and therefore no
"Why Previous Fixes Failed" section.

The relevant prior art is *adjacent precedent* rather than prior attempts:

- **PR #2631** — Durability M3, which introduced `models/job.py` and ratified its schema. Its
  substrate review produced this issue. Merged, healthy.
- **`agent/memory_retrieval.py:110-122`** — the in-repo, in-production idiom for exactly the read
  this plan needs: derive the ZSET key from the popoto field API
  (`DecayingSortedField.get_sortedset_db_key(...)`), `zrevrange(key, 0, limit - 1)`, decode bytes
  defensively, fail open to `[]`. Its tests at `tests/unit/test_memory_retrieval.py:176-206` are a
  directly reusable template.
- **`models/agent_session.py:2423-2428`** — the pipelined-batch precedent that issue item 1 asked
  for and that `0a53ad22f` already ported.
- **`scripts/update/migrations.py:360`** (`_migrate_backfill_pipeline_ledger`) — the write-backfill
  migration pattern this plan needs. The read-only probe pattern at `:288-357` is *not* sufficient
  here (see spike-4).

## Research

**Queries used:**

- `popoto redis ORM SortedField partition_by query limit hydration`

**Key findings:**

- Popoto's public documentation ([popoto.io](https://popoto.io/),
  [readthedocs](https://popoto.readthedocs.io/en/latest/),
  [PyPI](https://pypi.org/project/popoto/)) documents `SortedField(type=...)` and range lookups
  (`level__lt`, `last_active__gt`) but has **no published coverage of `partition_by`, query
  `limit` semantics, or hydration internals**. The API has also shifted across versions
  (0.5.1 used positional `query.get("...")`; current uses keyword form), so version-matched
  documentation does not exist for 1.8.0.
- **How this informs the plan:** external documentation is not a usable authority here. Every
  behavioral claim in this plan is grounded in the pinned source
  (`.venv/lib/python3.12/site-packages/popoto`, version 1.8.0, floor enforced by
  `config/popoto_floor.py`) plus empirical spikes against real Redis. Builders must not substitute
  a doc-site reading for the source. Four non-obvious behaviors below are undocumented upstream and
  would be discovered the hard way otherwise — they are saved to project memory as well.

Sources: [Popoto](https://popoto.io/), [Popoto Documentation](https://popoto.readthedocs.io/en/latest/), [popoto on PyPI](https://pypi.org/project/popoto/)

## Spike Results

### spike-1: Is the naive-datetime ZSET score skew real?

- **Assumption**: "popoto decodes a `datetime` SortedField without tzinfo, so re-saving a reloaded
  Job recomputes the sorted-set score from a naive datetime read as local time."
- **Method**: prototype (worktree-isolated, real Redis, ORM-scoped cleanup)
- **Finding**: **CONFIRMED.** Measured on this machine (UTC+07):

  | Event | ZSET score vs `time.time()` | tzinfo |
  |---|---|---|
  | `Job.mint()` | 0.0 s | aware UTC |
  | reload via `query.filter(...)` | — | **None (naive)** |
  | `reloaded.add_promise()` | **-25200.0 s** | naive |
  | `reloaded.mark_at_rest()` | -25200.0 s | naive |
  | `reloaded.touch()` | 0.0 s (repairs it) | aware UTC |

  Skew is exactly `-offset`; on a US host it flips sign and pushes scores into the future. It is
  **one-shot, not cumulative** — the naive value round-trips stably, so it lands on the same wrong
  score every time. Only the ZSET score is wrong; the hash field stores the correct instant.
  Affected write paths: `mark_at_rest()` (`models/job.py:226`) and `_write_goal_data()`
  (`models/job.py:152`) → `add_promise` / `remove_promise` / `append_goal_version`. Unaffected:
  `mint()`, `touch()`, `revive()`. Verified live: after `add_promise`, a
  `last_active_at__gte=now-1h` filter returns **0 rows** for a Job active seconds ago.
- **Confidence**: high
- **Impact on plan**: promotes a `save()` tz-normalization override to a **hard prerequisite** for
  `build-bounded-recent`, and adds a re-save backfill for already-skewed rows. Without it, the bounded ZREVRANGE
  would ship an ordering defect that the current implementation accidentally masks.

### spike-2: Does ZREVRANGE + `get_many` reproduce `recent_for_room` exactly?

- **Assumption**: "`recent_for_room` can be replaced by a bounded ZREVRANGE against the partition
  ZSET, hydrated via `Job.query.get_many(keys, skip_none=True)`, returning the same Jobs in the same
  order with only `limit` HGETALLs."
- **Method**: prototype (worktree-isolated, real Redis, instrumented pipeline)
- **Finding**: **CONFIRMED.** Observed key for `room_id = "test-spike2-18544bfd|telegram:1"`:

  ```
  $SortF:Job:last_active_at:test/-spike2/-18544bfd|telegram{&#58;}1
  ```

  Note the prefix is **`$SortF`**, not the `$SortedF` popoto's own docstrings claim — the metaclass
  at `popoto/fields/field.py:124` does `f"${name.strip('Field')}F"` and `str.strip` strips a
  *character set*, so `"SortedField".strip('Field')` → `"Sort"`. Note also that `DB_key.clean()`
  escapes `-` → `/-` and `:` → `{&#58;}`, and every real `room_id` contains a colon
  (`models/room.py:50,55`). **Never f-string this key**; derive it via
  `SortedField.get_sortedset_db_key(Job, "last_active_at", room_id).redis_key`.

  HGETALL counts, 30 Jobs in Room, `limit=5`:

  | | index reads | HGETALL |
  |---|---|---|
  | current `recent_for_room` | `ZRANGEBYSCORE` ×2 | **60** |
  | ZREVRANGE + `get_many` | `ZREVRANGE` ×1 | **5** |

  Order matched the baseline exactly at 7 and at 30 Jobs. `get_many(redis_keys: list, skip_none:
  bool = False)` exists on this version (`popoto/models/query.py:1657`) and its docstring guarantees
  input-order preservation. Empty room: `zrevrange` on a missing key returns `[]`; `get_many([])`
  short-circuits. Missing key mid-list with `skip_none=True`: dropped, no raise. Hydrated Jobs
  expose `job_id` and `current_goal()`, the only two things `bridge/job_router.py:161-189` reads.
- **Confidence**: high
- **Impact on plan**: this is `build-bounded-recent`'s implementation, essentially verbatim. Also surfaced the
  tie-break divergence (see spike-2b below) and the 2× hydration constant.

### spike-2b: Tie-break behavior differs between the two implementations

- **Assumption** (emergent, not pre-registered): "the swap is behavior-preserving in all cases."
- **Method**: prototype
- **Finding**: **FALSIFIED for exact score ties.** With 7 Jobs at an *identical* `last_active_at`
  and `limit=5`, the two paths return a **different subset**, not merely a different order. Python's
  stable sort preserves popoto's ascending-key filter order among equal scores; Redis breaks score
  ties lexicographically by member and `ZREVRANGE` reverses that. The two tie-break rules are
  opposite, so ties straddling the `limit` boundary select different members. Organic ties do not
  occur — `last_active_at` is microsecond-resolution from `_now()` — but a test or migration that
  stamps a constant timestamp will produce them.
- **Confidence**: high
- **Impact on plan**: not a correctness regression (both orderings are arbitrary among ties), but
  the test task must write tie-tolerant assertions, and the backfill migration must **not** stamp
  a constant timestamp.

### spike-3: Is `SortedField(partition_by="status")` a viable bound for the at-rest scan?

- **Assumption**: "an `at_rest_since` SortedField partitioned by `status` bounds the at-rest scan
  cheaply."
- **Method**: prototype (worktree-isolated, throwaway model, Redis db 15)
- **Finding**: **FALSIFIED — this design is unshippable.** popoto's `on_save`
  (`popoto/fields/sorted_field_mixin.py:496-522`) only ZREMs from the old partition when
  `_saved_field_values` shows the partition field changed. For a **lazily-loaded** instance — which
  is exactly what `query.filter()` returns — `popoto/models/encoding.py:429-443` populates
  `_saved_field_values` with **KeyFields only**, so `saved.get("status")` is `None`,
  `partition_changed` is `False`, and the ZREM never fires. Reproduced against the exact
  `Job.sweep_to_rest` shape (`models/job.py:274-278`):

  ```
  [created d1 active]  IndexF active: ['SpikeTmpD:d1']   SortF active: ['SpikeTmpD:d1']
  [after lazy flip]    IndexF active: []                 SortF active: ['SpikeTmpD:d1']  <-- ORPHAN
                       IndexF at-rest: ['SpikeTmpD:d1']  SortF at-rest: []               <-- MISSING
  ```

  The `status` IndexedField swapped correctly (INDEX_SWAP_LUA is server-side and never consults
  `_saved_field_values`); the partitioned SortedField did not. `.delete()` cannot clean the orphan
  (`on_delete` computes the partition from the *current* instance), and `rebuild_indexes()` does not
  enumerate `$SortF:*`. Worse, when a SortedField is partitioned by `status`, popoto **consumes**
  the `status` kwarg (`query.py:1930-1934`), so the wrong ZSET becomes the unintersected sole answer.

  Corollary confirmed: an IndexedField equality filter **does** intersect with any other index
  before hydration — sorted fields resolved first (`query.py:1897-1934`), others after (`:1936-1962`),
  `set.intersection` at `:1989`, and `get_many_objects` HGETALLs only the intersection (`:2680`).
  Measured: `status='at-rest'` + range → 3 keys / 6 HGETALLs; `status='at-rest'` alone → 25 keys /
  50 HGETALLs.

  **Bonus finding:** hydration costs **2 HGETALLs per record**, not 1 — `list(QueryBuilder)`
  executes the whole pipeline twice. Every `Job.query.filter(...)` in the repo pays double. Filed
  as a separate follow-up (see No-Gos).
- **Confidence**: high
- **Impact on plan**: eliminates the partitioned-SortedField design outright and demotes the
  unpartitioned one (it bounds by *rest time*, not by *open promise*, so it needs a watermark sweep
  to avoid silently dropping long-rested Jobs, and `limit=` cannot batch it). Selects the derived
  `has_open_promises` IndexedField as the `build-open-promise-index` design.

### spike-4: Is a boolean `IndexedField` a valid bound?

- **Assumption**: "`IndexedField(default=False)` is genuinely indexed for both values, and
  `filter(status='at-rest', has_open_promises=True)` intersects and hydrates only the intersection."
- **Method**: prototype (worktree-isolated, throwaway model, Redis db 15)
- **Finding**: **CONFIRMED, with two blocking caveats.**

  Index keys are `$IndexF:<Model>:has_open_promises:True` (card 5) and `:False` (card 35) — popoto
  stringifies via `str(value)`, capitalized, and **falsy is not skipped**. HGETALL counts:

  | Query | Rows | HGETALL |
  |---|---|---|
  | `filter(status="at-rest", has_open_promises=True)` | 2 | **4** |
  | `filter(status="at-rest")` | 20 | **40** |

  Lazy-load flip works: taking a record via `query.filter(...)`, setting the flag, and `save()`
  correctly moves it between the `True` and `False` sets — INDEX_SWAP_LUA is server-side and safe on
  the production `for job in query.filter(...): job.x = ...; job.save()` shape, unlike the
  partitioned SortedField.

  **Caveat 1 (blocking):** `IndexedField(default=False)` *without* `type=bool` hydrates as the
  **string `'False'`**, which is truthy in Python. The index is correct either way, so `filter()`
  works, but `if job.has_open_promises:` is unconditionally `True`. **Declare
  `IndexedField(type=bool, default=False)`.**

  **Caveat 2 (blocking):** rows written before the field existed are invisible to the filter. A
  two-process test (V1 model writes 3 rows, V2 model with the field reads them) showed the
  `has_open_promises` index sets do not exist at all for legacy rows;
  `filter(at-rest, has_open_promises=False)` returned `[]` while `filter(at-rest)` returned all 3.
  The Python default applies at *read* time only; the index is populated by a *write*. A no-op
  `for job in Job.query.all(): job.save()` is a sufficient and correct backfill. Failure direction:
  legacy rows land in **neither** set, so the backstop silently under-reports until the backfill
  runs.
- **Confidence**: high
- **Impact on plan**: fixes the `build-open-promise-index` field declaration to `type=bool`, and makes the `build-open-promise-index`
  backfill migration mandatory and load-bearing rather than defensive.

## Data Flow

**Path A — candidate lookup (`build-bounded-recent`), the cutover-critical one:**

1. **Entry point**: a Telegram message arrives at `bridge/telegram_bridge.py:1296`, which spawns
   `shadow_route_job` (`bridge/room_inbox.py:76-121`) as a background task. *After the phase-2
   cutover this becomes synchronous and its latency becomes intake latency.*
2. **`bridge/job_router.py:192` `route_message`**: resolves the `room_id`, then calls
   `Job.recent_for_room(room_id, limit=_CANDIDATE_CAP)` (`:241`, cap = 5 at `:59`).
3. **`models/job.py:239` `recent_for_room`** — *the change*: today, `ZRANGEBYSCORE -inf +inf` over
   the Room partition → HGETALL every member → Python sort → `[:5]`. After: one
   `ZREVRANGE key 0 (2*limit - 1)` → `get_many(skip_none=True)` → truncate to `limit`.
4. **`bridge/job_router.py:161-189` `_classify`**: reads only `job.job_id` and `job.current_goal()`
   off each candidate to build the granite prompt. Empty candidate list → mint NEW with no model
   call. Post-hoc `valid_ids` membership check at `:283`; every failure direction is NEW.
5. **Output**: a bind-to-existing-Job or mint-new decision.

Second caller: `tools/job_tool.py:209` `list` command, `limit=10`, formats via `_job_summary(j)`.

**Path B — at-rest promise backstop (`build-open-promise-index`):**

1. **Entry point**: `worker/__main__.py:960` supervises `_agent_session_health_loop`, one per worker
   process.
2. **`agent/session_health.py:5143`** awaits `_agent_session_health_check`, then sleeps 300 s (`:442`).
3. **`agent/session_health.py:4505`** calls `_check_jobs_at_rest_with_open_promises` (`:890`),
   deliberately placed before the `DISABLE_ORPHAN_REAP` early-return at `:4519` so the kill switch
   cannot disable it.
4. **`models/job.py:254` `sweep_to_rest`**: scans `filter(status="active")`, transitions idle Jobs
   past `JOB_AT_REST_AGE_SECONDS` via `mark_at_rest()`. *Bounded by rest-by-age; out of scope.*
5. **`models/job.py:294` `at_rest_with_open_promises`** — *the change*: today, scan the entire
   at-rest set and `json.loads` every `goal`. After: `filter(status="at-rest",
   has_open_promises=True)`, an index intersection hydrating only the flagged set.
6. **Output**: `logger.warning("[at-rest-promise] Job %s ...")` per flagged Job — operator surface
   only, never human chat, no Job mutation (the Risk 4 no-nag-machine ruling).

**Path C — the flag's write chokepoint (`build-open-promise-index`):** every promise mutation funnels through
`models/job.py:152` `_write_goal_data` — reached from `mint` (`:124`), `add_promise` (`:188-201`),
`remove_promise` (`:203-211`), `append_goal_version` (`:180-184`). External callers
(`tools/job_tool.py:94,105,114,132`, `bridge/job_router.py:248`) all route through those methods.
Deriving the flag inside `_write_goal_data` makes it un-bypassable by construction.

## Architectural Impact

- **New dependencies**: none. Two new imports inside `models/job.py`
  (`popoto.SortedField`, `popoto.redis_db.POPOTO_REDIS_DB`) — both already imported elsewhere in the
  repo and `POPOTO_REDIS_DB` is already imported by `Job.repair_indexes` (`models/job.py:347`).
- **Interface changes**: none public. `Job.recent_for_room` and `Job.at_rest_with_open_promises`
  keep their signatures, return types, and fail-open contracts. One additive field on the `Job`
  schema (`has_open_promises`) and one `save()` override.
- **Coupling**: slightly increased coupling from `models/job.py` to popoto internals — the ZSET key
  layout becomes load-bearing. Mitigated by deriving the key through popoto's own
  `get_sortedset_db_key` API rather than hardcoding, exactly as `agent/memory_retrieval.py:110-122`
  already does. A popoto upgrade that changed the key format would break both call sites together,
  and `config/popoto_floor.py` pins the floor.
- **Data ownership**: unchanged. `goal` JSON remains the single source of truth for promise state;
  `has_open_promises` is a **derived index projection**, never authoritative. Any read that matters
  re-verifies against `open_promises()`.
- **Schema-gate impact**: `docs/plans/durability-room-job-agentrun.md:528-529` and
  `models/job.py:11-12` both assert "`status` is the ONLY IndexedField". This plan makes that false
  and must amend both. The gate's actual machine anti-criterion (line 736,
  `grep -nE "IndexedField" models/room.py models/job.py | grep -iE "pid|uuid|_at\b"` → 0 matches) is
  a *cardinality* rule — never index a pid, uuid, or timestamp — and a two-valued boolean honors it.
- **Reversibility**: high. `build-bounded-recent` is a pure function-body swap behind an unchanged signature; the
  `save()` override is additive and idempotent. The `has_open_promises` field is additive; reverting means deleting it and
  restoring the scan, leaving harmless orphan `$IndexF:Job:has_open_promises:*` sets that
  `repair_indexes` leg 2 already clears.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (schema-gate amendment sign-off), code reviewer

**Interactions:**
- PM check-ins: 1-2 (the schema-gate amendment in Open Question 1 needs an explicit yes)
- Review rounds: 1

The coding is small — two function bodies, one `save()` override, one field, two migrations. The
overhead is that this touches a schema the M3 plan ratified one-shot, and the fix order matters
(tz normalization must land before ZREVRANGE trusts scores).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; assert POPOTO_REDIS_DB.ping()"` | All spikes and tests hit real Redis |
| popoto at/above floor | `python -c "from config.popoto_floor import assert_popoto_floor; assert_popoto_floor()"` | ZSET key layout and `get_many` signature are version-pinned behavior |
| Job.query.get_many accepts skip_none | `python -c "import inspect; from models.job import Job; assert 'skip_none' in inspect.signature(Job.query.get_many).parameters"` | `build-bounded-recent` depends on this kwarg; note `get_many` lives on `popoto.models.query.Query`, which is what `Model.query` resolves to — not on `QueryBuilder` |

## Solution

### Key Elements

- **tz-normalizing `Job.save()`**: a single choke point that re-attaches UTC to a naive
  `last_active_at` before delegating to popoto, so every write path stores a correct ZSET score
  without altering the instant. Preserves `mark_at_rest`/promise-write semantics (no recency
  refresh).
- **Bounded `recent_for_room`**: one `ZREVRANGE` against the Room's partition ZSET, hydrated through
  the ORM's `get_many`, truncated to `limit`. O(limit), not O(Room).
- **Derived `has_open_promises` IndexedField**: a two-valued boolean maintained at the
  `_write_goal_data` choke point, turning the at-rest backstop from a full-population scan into an
  index intersection over a set that is normally empty.
- **Two backfill migrations**: one re-saving every Job to repair tz-skewed ZSET scores, one
  populating the new index for pre-existing rows. Both idempotent, both registered in `MIGRATIONS`.
- **Schema-gate amendment**: the M3 plan text and the `Job` docstring move from "the ONLY
  IndexedField" to "two low-cardinality IndexedFields", with the cardinality rule restated.

### Flow

**Message arrives** → [bridge spawns `shadow_route_job`] → **`route_message`** → [`recent_for_room`:
one ZREVRANGE, ≤`limit` hydrations] → **granite classify** → [bind or mint] → **Job bound**

**Health tick (300 s)** → [`sweep_to_rest` transitions idle actives] → **at-rest backstop** →
[`filter(status="at-rest", has_open_promises=True)`: index intersection, hydrate only flagged] →
[re-verify `open_promises()`] → **operator log line**

### Technical Approach

**Ordering is load-bearing.** `build-tz-normalize` must land before `build-bounded-recent`, because
the bounded lookup makes ZSET scores authoritative and the tz override is what makes them correct.
The backfill (`build-backfill-migration`) must land with or after it. `build-open-promise-index` is
independent of both and may run in parallel.

**`build-tz-normalize` — tz normalization.** Override `save()` on `Job`:

```python
def save(self, *args, **kwargs):
    if self.last_active_at is not None and self.last_active_at.tzinfo is None:
        self.last_active_at = self.last_active_at.replace(tzinfo=UTC)
    return super().save(*args, **kwargs)
```

Chosen over the two alternatives spike-1 surfaced: re-stamping `_now()` on save would silently
resurrect idle Jobs (breaking rest-by-age); migrating `last_active_at` to `type=float` epoch seconds
is also correct but touches every reader, the `_EPOCH` bound, and the sort key. The override is
idempotent, instant-preserving, and covers every current and future write path at one point.

**`build-bounded-recent` — bounded `recent_for_room`.** Replace the body, keeping the signature, the docstring
contract, and the `try/except → logger.warning → []` fail-open wrapper that `bridge/job_router.py`
depends on:

```python
zkey = SortedField.get_sortedset_db_key(cls, "last_active_at", room_id).redis_key
members = POPOTO_REDIS_DB.zrevrange(zkey, 0, (limit * 2) - 1)
keys = [m.decode() if isinstance(m, bytes) else str(m) for m in members]
return cls.query.get_many(keys, skip_none=True)[:limit]
```

Over-fetching `2 * limit` before truncating absorbs dangling ZSET members (whose backing hash is
gone) without a write path: `skip_none=True` drops them, and without the over-fetch each orphan
would silently consume a candidate slot and push the router toward a spurious mint. Cost is at most
`2 * limit` hydrations — still O(1) in Room size. Derive `zkey` through
`get_sortedset_db_key`; **never** f-string it (spike-2: `DB_key.clean()` escapes `-` and `:`, and
every real `room_id` contains a colon).

Delete the now-unused `_EPOCH` module constant (`models/job.py:49`) if no other reference survives —
it existed only to satisfy popoto's "a SortedField needs a filter param" requirement.

**Score repair (folded into `build-backfill-migration`).** Iterate every Job and `save()` it. With the tz override in place this
re-stamps a correct score for rows skewed by a prior `add_promise`/`mark_at_rest`. Idempotent by
construction (a correct score is rewritten to the same value). Must **not** assign a timestamp —
`save()` alone, so the instant is preserved and no artificial ties are created (spike-2b).

**`build-open-promise-index` — `has_open_promises`.** Declare `has_open_promises = IndexedField(type=bool, default=False)`
— `type=bool` is mandatory (spike-4 caveat 1). Derive it inside `_write_goal_data`, the sole writer:

```python
self.has_open_promises = any(p.get("removed_ts") is None for p in data.get("promises", []))
```

Then `at_rest_with_open_promises` becomes
`cls.query.filter(status="at-rest", has_open_promises=True)`, retaining the fail-open `try/except`
and adding a cheap defensive re-verify (`if job.open_promises()`) before flagging — the flagged set
is small by definition, so re-verifying costs nothing and guards against a false positive. False
*negatives* are prevented structurally by the choke point plus the backfill migration; they are not
recoverable by `repair_indexes`, which reconstructs index sets from stored hash values and would
faithfully reproduce a wrong one. That limitation is documented in Risks.

**`build-backfill-migration` — the single combined pass.** `for job in Job.query.all(): job.save()` — with the
derivation in `_write_goal_data`, a plain `save()` alone is *not* enough to derive the flag for a
legacy row (the derivation lives in `_write_goal_data`, not `save`). The migration must therefore
call `job._write_goal_data(job._goal_data())` (which sets the flag and saves) or set the flag
explicitly from `job.open_promises()` before saving. Register in `MIGRATIONS`
(`scripts/update/migrations.py:999-1105`); no ordering dependency on the phantom-purge constraint at
`:1064-1073`, so it may go last. The score repair and the index backfill are one **single** migration pass — one iteration over all
Jobs that repairs the score and derives the flag — never two full passes.

**`document-feature` — schema-gate amendment.** Update `models/job.py:11-12` and
`docs/plans/durability-room-job-agentrun.md:528-529` from "the ONLY IndexedField" to name both
fields, restating the real rule (never index a pid, uuid, or timestamp) so the amendment does not
read as an erosion of the gate.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `models/job.py` `recent_for_room` — the `except Exception → logger.warning → []` fail-open
      wrapper (`:247-249`) is the contract `bridge/job_router.py` relies on. Add a test that forces
      the ZREVRANGE to raise (patch `POPOTO_REDIS_DB.zrevrange` to raise) and asserts **both**
      `[] == result` and that a `logger.warning` containing `recent_for_room` was emitted.
- [ ] `models/job.py` `at_rest_with_open_promises` — same shape (`:305-306`). Add a test forcing
      `query.filter` to raise and asserting `[]` plus the warning.
- [ ] `models/job.py` `save()` override (new) — must **not** swallow exceptions; a failed save has to
      propagate. Add a test asserting the override re-raises when `super().save()` raises.
- [ ] `agent/session_health.py:890` `_check_jobs_at_rest_with_open_promises` — already wrapped; the
      existing test at `tests/unit/test_promise_advisory.py:217` asserts the WARNING log carries the
      job id. Confirm it still passes unchanged.
- [ ] Migration bodies — the repo's migration runner records completion by name; a partially-applied
      backfill must be safe to re-run. Add a test that runs the migration twice and asserts identical
      end state.

### Empty/Invalid Input Handling

- [ ] `recent_for_room` with a Room that has never had a Job — spike-2 confirmed `zrevrange` on a
      missing key returns `[]` and `get_many([])` short-circuits. Existing test
      `tests/unit/test_job_model.py` `test_recent_for_room_empty_room_is_empty` covers this; verify
      it still passes on the new body.
- [ ] `recent_for_room` with `limit=0` — assert it returns `[]` rather than issuing
      `ZREVRANGE key 0 -1` (which would return the whole ZSET). **This is a real trap**:
      `(0 * 2) - 1 == -1`, and `-1` is "last element" in Redis. Guard `limit <= 0` explicitly.
- [ ] `recent_for_room` where a ZSET member's backing hash is gone — assert the dangling member is
      dropped and does not consume a result slot (this is what the `2 * limit` over-fetch buys).
- [ ] `_goal_data()` on a Job with malformed/absent `goal` JSON — already handled
      (`models/job.py:144-147` logs and treats as empty). Assert the derived `has_open_promises`
      lands `False` rather than raising in that case.
- [ ] `at_rest_with_open_promises` when every at-rest Job has discharged its promises — assert `[]`
      and assert (by instrumenting hydration or by row count) that no full-population scan occurred.

### Error State Rendering

- [ ] No user-visible output in scope. The at-rest backstop writes to the **operator** surface only
      (`logger.warning`, never human chat — the Risk 4 no-nag-machine ruling), and the candidate
      lookup's failure mode is invisible to the user by design (fail open → mint NEW). Assert the
      operator log line still renders the job id, room id, open-promise count, and goal on the
      bounded path — a silently-narrowed backstop that logs nothing is the failure this section
      exists to catch.

## Test Impact

- [ ] `tests/unit/test_job_model.py::TestRecencyLookup::test_recent_for_room_returns_newest_first_capped`
      (`:145`) — UPDATE: mints 7 Jobs and asserts `len == 5` plus `recent[0].job_id`. Still valid on
      the new body (7 consecutive `mint()`s produce distinct microsecond scores), but rewrite the
      assertion to be tie-tolerant per spike-2b and add an explicit `sleep`/distinct-timestamp step
      so it can never accidentally create ties.
- [ ] `tests/unit/test_job_model.py::TestRecencyLookup::test_recent_for_room_empty_room_is_empty` —
      UPDATE (verify only): should pass unchanged; confirm rather than assume.
- [ ] `tests/unit/test_job_model.py::test_at_rest_with_open_promises_query` (`:130`) — UPDATE:
      asserts flagged-set **membership by id**, not list length, so it tolerates the narrowed scan.
      Add a companion case asserting a Job whose promises were all discharged is **absent**.
- [ ] `tests/unit/test_job_model.py::test_sweep_to_rest_transitions_stale_active_jobs` (`:110`) —
      UPDATE (verify only): `sweep_to_rest` is untouched, but it now runs against a Job carrying the
      new field; confirm it still passes.
- [ ] `tests/unit/test_promise_advisory.py::test_stale_active_job_reaches_the_operator_log_end_to_end`
      (`:238`) — UPDATE: back-dates `last_active_at` to `now - JOB_AT_REST_AGE_SECONDS - 60` and
      expects the swept Job to be flagged in the same invocation. Under `build-open-promise-index` the Job must carry
      `has_open_promises=True` — it will, because the test adds a promise through `add_promise`,
      which now derives the flag. Verify explicitly; this is the highest-value regression test in
      the suite for this change.
- [ ] `tests/unit/test_promise_advisory.py::test_backstop_surfaces_at_rest_jobs_with_open_promises`
      (`:217`) — UPDATE (verify only): asserts the WARNING log carries the job id.
- [ ] `tests/unit/test_promise_advisory.py::test_backstop_is_invoked_from_the_periodic_sweep`
      (`:266`) — UPDATE (verify only): a **source-text** assertion that
      `await _check_jobs_at_rest_with_open_promises()` appears after
      `await _check_at_rest_owed_communication()` in `session_health.py`. Do not relocate or rename
      that call.
- [ ] `tests/unit/test_job_router.py::test_zero_candidates_mints_without_model_call` (`:139`) —
      UPDATE (verify only): depends on `recent_for_room` returning `[]` for a fresh Room.
- [ ] `tests/unit/test_job_router.py` candidate-seeded paths (`:168-223`) — UPDATE (verify only):
      depend on `recent_for_room` returning seeded Jobs with a usable `current_goal()`.
- [ ] `tests/unit/test_job_tool.py` — UPDATE (verify only): `tools/job_tool.py:209` calls
      `recent_for_room(rid, limit=10)`; confirm the `list` command output is unchanged.
- [ ] `tests/unit/test_migrations.py::test_registered_in_migrations_dict` (`:266`),
      `::test_every_migration_name_is_unique` (`:310`) — UPDATE: add the new migration name.
      Ordering test at `:297` pins strip-migrations ahead of the phantom purge; the new backfill has
      no such dependency.
- [ ] NEW: `tests/unit/test_job_model.py` — add a case asserting a Job re-saved via `add_promise()`
      still sorts correctly in `recent_for_room` (the spike-1 regression). This is the test that
      would have caught the tz skew; without it the fix is unguarded.
- [ ] NEW: `tests/unit/test_job_model.py` — add a case asserting `recent_for_room(room, limit=0)`
      returns `[]` (the `ZREVRANGE 0 -1` trap).

## Rabbit Holes

- **Rewriting `sweep_to_rest` to be bounded too.** It scans `filter(status="active")` on the same
  tick. It is genuinely bounded by rest-by-age (72 h) as the issue states, and bounding it further
  would require the same `at_rest_since` machinery spike-3 just disqualified. Leave it.
- **Fixing popoto's 2×-HGETALL double-execution.** Spike-3 found `list(QueryBuilder)` runs the whole
  pipeline twice, so every `Job.query.filter(...)` in the repo pays double hydration. It is a real
  finding and a real 2× win, but it is a popoto-internals fix affecting every model in the codebase.
  Separate issue.
- **Pruning `$SortF:Job:*` orphans in `repair_indexes`.** Tempting, because leg 2 already clears
  `$IndexF:Job:*` and leaves `$SortF` untouched. But `rebuild_indexes()` does not enumerate `$SortF`
  partitions, so deleting the key would not be repaired; and ZREM-ing individual stale members means
  either raw `zrem` (against the repo's ORM-only rule) or reconstructing a bare `Job(id=..,
  room_id=..)` from a `DB_key.from_redis_key` round trip that spike-2 flagged as not perfectly
  invertible. The `2 * limit` over-fetch in `build-bounded-recent` handles the practical case for free. Separate
  issue.
- **Migrating `last_active_at` to `type=float` epoch seconds.** Correct, and arguably cleaner than a
  `save()` override, but it touches every reader, the `_EPOCH` bound, the sort key, and the on-disk
  encoding of every existing Job. The override achieves the same correctness at a fraction of the
  blast radius.
- **Building a general drift reconciler for derived index fields.** `repair_indexes` cannot fix
  value-vs-derivation drift for `has_open_promises` (or for any derived field). A general reconciler
  is a nice idea and a much larger design. The choke point plus the backfill is the proportionate
  answer here.

## Risks

### Risk 1: The tz fix lands after the ZREVRANGE, or without the score-repair backfill

**Impact:** `recent_for_room` starts trusting ZSET scores while some rows carry scores skewed by the
machine's UTC offset (measured -25200 s here; sign flips on a US host). Affected Jobs sort as hours
older or newer than they are, so the bind-or-mint router sees the wrong five candidates and mints
spurious duplicate Jobs for a Room. Silent — nothing logs, nothing fails.
**Mitigation:** `build-tz-normalize` is declared a hard prerequisite of `build-bounded-recent` via `Depends On`.
The combined backfill pass runs in the same release. The new regression
test — re-save via `add_promise()`, then assert `recent_for_room` ordering — fails loudly if the
override is missing or removed. Fleet note: the skew is machine-local, so a fleet with mixed UTC
offsets writing to shared Redis produces *inconsistent* skew across hosts, which the backfill
resolves only for rows written before it ran; the override is what prevents recurrence.

### Risk 2: `has_open_promises` drifts from the `goal` JSON

**Impact:** a false negative means an at-rest Job with an open promise is never surfaced — exactly
the incident the backstop exists to catch, now silent. `repair_indexes` **cannot** repair this:
`rebuild_indexes()` reconstructs index sets from stored hash values, so it faithfully reproduces a
wrong one (spike-3, C-iv).
**Mitigation:** derive the flag inside `_write_goal_data` (`models/job.py:152`), the sole writer of
`goal` — `mint`, `add_promise`, `remove_promise`, and `append_goal_version` all funnel through it,
and no caller in the repo sets `job.goal` directly. Backfill migration covers legacy rows. A
defensive `open_promises()` re-verify in `at_rest_with_open_promises` catches false *positives*
cheaply. Residual exposure: a future writer that assigns `job.goal` directly would bypass the
derivation — add a Verification anti-criterion row asserting no direct `self.goal =` /`job.goal =`
assignment exists outside `_write_goal_data`.

### Risk 3: The schema-gate amendment is made unilaterally

**Impact:** `docs/plans/durability-room-job-agentrun.md:528-529` and `models/job.py:11-12` both state
"`status` is the ONLY IndexedField" as a *ratified* decision from the M3 schema gate. Adding a second
IndexedField without an explicit amendment leaves the plan-of-record contradicting the code, which is
precisely the "historical artifact in docs" the repo's development principles forbid.
**Mitigation:** Open Question 1 asks for explicit sign-off before `build-open-promise-index` builds. `document-feature`
amends both texts in the same PR. Note the gate's *machine* anti-criterion (line 736) greps for IndexedFields
mentioning `pid|uuid|_at` — a two-valued boolean passes it, and no test in `tests/` introspects
`Job._meta.fields` or asserts a field count, so nothing breaks mechanically. This is a
decision-record risk, not a build risk.

### Risk 4: The backfill migration runs against a large Job population on a hot worker

**Impact:** the combined migration iterates every Job and writes each one. On a fleet with
a large Job table this is a long single-threaded write pass; `scripts/update/migrations.py` runs it
during `/update`, which also gates the bridge restart. A slow migration delays the restart.
**Mitigation:** the Job model is 24 hours old, so the current population is small — run it now, while
it is cheap. Make it idempotent and resumable (skip rows already correct), batch the writes, and log
progress every N rows so a long run is observable rather than an apparent hang. Record it in
`data/migrations_completed.json` by name like every other migration so it never re-runs.

### Risk 5: Tie-break divergence surfaces in a way spike-2b did not predict

**Impact:** with exactly-equal `last_active_at` scores the two implementations select a different
*subset*, not just a different order (spike-2b). Organic ties do not occur at microsecond
resolution, but a migration or test that stamps a constant timestamp creates them, and a test written
against the old tie-break would fail confusingly.
**Mitigation:** the backfill migration is specified to call `save()` **without** assigning a
timestamp, so it cannot create ties. Tests are specified to use distinct timestamps and tie-tolerant
assertions. Both orderings are arbitrary among ties, so this is a test-authoring hazard rather than
a correctness one.

## Race Conditions

### Race 1: Concurrent `save()` on the same Job while the backfill migration is iterating

**Location:** `scripts/update/migrations.py` (new migration) versus `models/job.py:221` `touch()` /
`:230` `revive()` / `:152` `_write_goal_data`, called from the bridge and worker.
**Trigger:** the migration reads a Job, and before it writes, a live message binds to that Job and
calls `touch()`. The migration's stale in-memory copy then overwrites the fresher `last_active_at`
and `goal`.
**Data prerequisite:** the migration's loaded snapshot must still reflect the record at write time.
**State prerequisite:** no concurrent writer for that Job between read and write.
**Mitigation:** popoto offers no row-level optimistic concurrency here, so prevent the overlap rather
than resolve it: `/update` already sequences migrations before the bridge restart, and the migration
should run while the bridge is down. Additionally, the migration's writes are *convergent* — the
score repair recomputes from the record's own `last_active_at`, and the flag derives from the
record's own `goal` — so the worst case is one lost `touch()` timestamp on a Job that any subsequent
message will refresh. Do **not** have the migration assign `_now()`.

### Race 2: `sweep_to_rest` and `at_rest_with_open_promises` run on the same tick, per worker, across multiple workers

**Location:** `agent/session_health.py:913-917`.
**Trigger:** two workers' health loops fire within the same window. Both call `sweep_to_rest`, both
attempt `mark_at_rest()` on the same idle Job.
**Data prerequisite:** the at-rest transition must be visible to the promise scan that follows it —
which is exactly why `sweep_to_rest` is called first (`models/job.py:262-265`: "never correct logic
over empty input").
**State prerequisite:** none beyond idempotence.
**Mitigation:** pre-existing and unchanged by this plan. `mark_at_rest()` is idempotent (setting
`status="at-rest"` twice is a no-op through INDEX_SWAP_LUA, which is server-side and atomic), and the
backstop is observability-only — it mutates nothing. `build-open-promise-index` does not alter this: the boolean flag is
likewise written idempotently from the record's own `goal`. Worth noting that duplicate operator log
lines across workers are pre-existing behavior, not a regression this plan introduces.

### Race 3: A ZSET member is read by ZREVRANGE and its hash is deleted before `get_many` hydrates it

**Location:** `models/job.py` `recent_for_room` (new body), between the `zrevrange` and the
`get_many`.
**Trigger:** a concurrent `job.delete()` (or a `repair_indexes` leg-1 quarantine) lands in the
microseconds between the two calls.
**Data prerequisite:** each returned member's backing hash must exist at hydration time.
**State prerequisite:** none — this is inherently non-atomic and does not need to be atomic.
**Mitigation:** `get_many(skip_none=True)` drops the missing row rather than raising (spike-2), and
the `2 * limit` over-fetch means dropping one still yields a full candidate list. The failure
direction is a shorter candidate list, and every failure direction in `bridge/job_router.py` is
"mint NEW" — safe. No lock needed.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2639] popoto's `list(QueryBuilder)` double-execution — every `.filter()` in the
  repo pays 2 HGETALLs per hydrated record instead of 1 (spike-3). Real 2× win, but it is a
  popoto-internals fix affecting every model, not a Job change.
- [SEPARATE-SLUG #2640] `$SortF:Job:*` orphan pruning in `Job.repair_indexes`. Leg 2 clears
  `$IndexF:Job:*` and leaves the sorted-set partitions untouched; `rebuild_indexes()` does not
  enumerate them, so orphans accumulate with no reaper. `build-bounded-recent`'s `2 * limit` over-fetch neutralizes
  the practical impact on the candidate lookup, which is why this is not a blocker here.
- [SEPARATE-SLUG #2641] `.claude/hooks/validators/validate_no_raw_redis_delete.py` guards on
  `\$SortedF:` (line 83), a prefix popoto never emits — the real prefixes are `$SortF` and
  `$DecayingSortF` (spike-2). `Job` is also missing from that validator's model list while `Room` is
  present (line 100). A hook correctness fix, unrelated to Job scaling.
- [ORDERED] The phase-2 authoritative cutover itself (`docs/plans/durability-room-job-agentrun.md:643-648`)
  — gated on N days of error-free `[room-inbox]` shadow appends plus spot-checked parity (`:655`), a
  human-gated observation window. This plan removes one of its prerequisites; it does not perform it.
- [SEPARATE-SLUG #2494] Bounding `sweep_to_rest`'s active-set scan. Rest-by-age already bounds it at
  72 h, and the `at_rest_since` machinery that would bound it further was disqualified by spike-3.
  Belongs to the parent durability epic if it ever becomes real.

## Update System

The two backfill passes ship as **one** entry in `scripts/update/migrations.py` (registered in the
`MIGRATIONS` dict at `:999-1105`, recorded once by name in `data/migrations_completed.json`), so
`/update` propagates them to every machine with no operator action. That is the whole update-system
surface:

- No new dependencies, config files, or secrets.
- No change to `scripts/remote-update.sh` or the `/update` skill body.
- No change to `.env.example` or `config/settings.py`.
- Migration ordering: no dependency on the strip-migrations-before-phantom-purge constraint
  (`migrations.py:1064-1073`); the new entry may go last.
- Existing installations: the migration is the entire upgrade path. Until it runs on a given
  machine, that machine's at-rest backstop under-reports for Jobs written before the field existed
  (spike-4 caveat 2) — a degraded-but-safe state, since the backstop is observability-only.

## Agent Integration

No agent integration required — this is a model-internal change behind two unchanged public method
signatures.

- No new CLI entry point in `pyproject.toml [project.scripts]`. `tools/job_tool.py` already exposes
  the Job surface the agent uses (`list`, `create`, `promise-add`, `promise-remove`), and its
  `list` command calls `Job.recent_for_room(rid, limit=10)` (`:209`) — it picks up the bounded
  implementation transparently with no wiring change.
- No new bridge import. `bridge/job_router.py:241` already calls `recent_for_room`; the signature,
  return type, and fail-open contract are unchanged.
- Integration coverage: `tests/integration/test_job_routing.py` already exercises the router end to
  end through `recent_for_room`. Confirm it passes unmodified — that is the assertion that the
  agent-reachable path still works.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/durability-model.md:44` — the `Job.recent_for_room` description says
      "a `SortedField(partition_by=room_id)`, post-hoc [narrowing]". Rewrite to state the bounded
      ZREVRANGE and the O(limit) guarantee, and note that the post-hoc narrowing is what was removed.
- [ ] Update `docs/features/durability-model.md:119` — "`Job.status` is the only IndexedField
      (low-cardinality ...)". Amend to name both IndexedFields and restate the actual cardinality
      rule (never index a pid, uuid, or timestamp).
- [ ] Update the `docs/features/README.md:67` Durability Model row summary to mention the bounded
      candidate lookup and the indexed at-rest promise backstop.

### Plan-of-record Documentation

- [ ] Update `docs/plans/durability-room-job-agentrun.md:528-529` — the ratified schema block.
      Record the amendment (two low-cardinality IndexedFields) rather than silently editing, so the
      gate's history stays legible.
- [ ] Update `docs/plans/durability-room-job-agentrun.md:708` — mark the bounded-ZREVRANGE
      prerequisite as satisfied and link this plan.

### Inline Documentation

- [ ] Update the `models/job.py` module docstring (`:11-12`) — "`status` is the ONLY IndexedField"
      becomes the two-field statement plus the cardinality rule.
- [ ] Document the `save()` override with the popoto encode/decode asymmetry it compensates for
      (`popoto/models/encoding.py:87-96`) — this is non-obvious and a future reader will otherwise
      delete it as dead weight.
- [ ] Document why `recent_for_room` over-fetches `2 * limit` (dangling ZSET members) and why the
      ZSET key must be derived via `get_sortedset_db_key` rather than f-strung (`DB_key.clean()`
      escaping).
- [ ] Document in `at_rest_with_open_promises` that `has_open_promises` is a derived projection,
      never authoritative, and that `repair_indexes` cannot reconcile value-vs-derivation drift.

## Success Criteria

- [ ] `Job.recent_for_room` issues exactly one `ZREVRANGE` and at most `2 * limit` HGETALLs
      regardless of Room size (measured: 30-Job Room, `limit=5` → ≤10 HGETALLs, down from 60)
- [ ] `Job.recent_for_room` returns the same Jobs in the same order as the pre-change implementation
      for all non-tied inputs
- [ ] A Job re-saved via `add_promise()` / `mark_at_rest()` carries a ZSET score within 1 s of its
      `last_active_at` (the spike-1 regression, currently off by the machine's UTC offset)
- [ ] `Job.at_rest_with_open_promises` hydrates only Jobs carrying an open promise — verified by row
      count against a seeded population where the at-rest set is much larger than the flagged set
- [ ] The combined backfill migration is registered in `MIGRATIONS`, idempotent across two runs, and
      leaves every pre-existing Job with a correct score and a correct `has_open_promises` value
- [ ] `models/job.py`, `docs/features/durability-model.md`, and
      `docs/plans/durability-room-job-agentrun.md` agree on the amended schema — no surviving
      "only IndexedField" claim
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `bridge/job_router.py` and `tools/job_tool.py` call `recent_for_room` unmodified — grep confirms
      no call-site changes were needed
- [ ] No xfail conversions needed — no expected-failure markers exist for these paths

## Team Orchestration

### Team Members

- **Builder (job-model)**
  - Name: `job-model-builder`
  - Role: `models/job.py` changes — the `save()` tz override, the bounded `recent_for_room`, the
    `has_open_promises` field and its derivation, the narrowed `at_rest_with_open_promises`
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Builder (migration)**
  - Name: `migration-builder`
  - Role: the single combined backfill migration in `scripts/update/migrations.py` and its
    registration + migration-registry test updates
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Test engineer (job-tests)**
  - Name: `job-test-engineer`
  - Role: the Test Impact and Failure Path Test Strategy checklists — updating existing cases and
    adding the tz-regression, `limit=0`, dangling-member, and narrowed-scan cases
  - Agent Type: test-engineer
  - Resume: true

- **Validator (job-model)**
  - Name: `job-model-validator`
  - Role: verifies HGETALL counts, ordering parity against the pre-change implementation, and the
    fail-open contracts
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `job-documentarian`
  - Role: the Documentation checklist, including the schema-gate amendment in the parent plan
  - Agent Type: documentarian
  - Resume: true

### Domain framing for `redis-popoto` tasks

Paste into both builder assignments: never write raw Redis on Popoto-managed keys — reads and writes
go through the ORM (`Model.query.filter()`, `instance.save()`, `instance.delete()`). The one
sanctioned exception in this plan is the **read-only** `ZREVRANGE` in `build-bounded-recent`, which mirrors the
existing production idiom at `agent/memory_retrieval.py:110-122` and is not blocked by
`.claude/hooks/validators/validate_no_raw_redis_delete.py` (that validator inspects Bash commands
only and does not list `zrevrange`). Any manual testing uses a `test-`/`dbg-`-prefixed `project_key`
or `room_id` and deletes through the ORM afterward, scoped by that prefix. Never run a bulk operation
unscoped.

## Step by Step Tasks

### 1. tz-normalizing `Job.save()` override

- **Task ID**: build-tz-normalize
- **Depends On**: none
- **Validates**: `tests/unit/test_job_model.py`
- **Informed By**: spike-1 (confirmed: -25200 s skew on `add_promise`/`mark_at_rest`; `touch()`
  repairs it; `save()` override is the minimal fix; re-stamping `_now()` would break rest-by-age)
- **Assigned To**: `job-model-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: false
- Add a `save()` override to `Job` re-attaching `UTC` to a naive `last_active_at` before delegating
  to `super().save()`. Do not swallow exceptions.
- Document the popoto encode/decode asymmetry (`popoto/models/encoding.py:87-96`) in the override's
  docstring so it is not deleted later as dead weight.
- Add the regression test: mint a Job, reload it via `query.filter`, call `add_promise()`, assert the
  raw ZSET score is within 1 s of `last_active_at`.

### 2. Bounded `recent_for_room` via ZREVRANGE

- **Task ID**: build-bounded-recent
- **Depends On**: build-tz-normalize
- **Validates**: `tests/unit/test_job_model.py`, `tests/unit/test_job_router.py`,
  `tests/unit/test_job_tool.py`, `tests/integration/test_job_routing.py`
- **Informed By**: spike-2 (confirmed: `$SortF` prefix, `get_sortedset_db_key` derivation mandatory,
  `get_many(skip_none=True)` preserves order, 60 → 5 HGETALLs), spike-2b (tie-break divergence)
- **Assigned To**: `job-model-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: false
- Replace the body of `recent_for_room`, keeping the signature, the docstring contract, and the
  `try/except → logger.warning → []` fail-open wrapper.
- Derive the ZSET key via `SortedField.get_sortedset_db_key(cls, "last_active_at", room_id).redis_key`.
  Never f-string it.
- Guard `limit <= 0` before computing the ZREVRANGE stop index — `(0 * 2) - 1 == -1` means "last
  element" in Redis and would return the whole ZSET.
- Over-fetch `2 * limit` members, hydrate with `get_many(keys, skip_none=True)`, truncate to `limit`.
- Delete the `_EPOCH` module constant if no reference survives.

### 3. Validate the bounded lookup

- **Task ID**: validate-bounded-recent
- **Depends On**: build-bounded-recent
- **Assigned To**: `job-model-validator`
- **Agent Type**: validator
- **Parallel**: false
- Instrument HGETALL counts on a 30-Job Room with `limit=5`; assert ≤10, down from 60.
- Assert ordering parity against the pre-change implementation for a non-tied population.
- Assert the fail-open contract: force `zrevrange` to raise, assert `[]` plus a `logger.warning`.
- Assert `limit=0` returns `[]`.

### 4. `has_open_promises` derived IndexedField

- **Task ID**: build-open-promise-index
- **Depends On**: none
- **Validates**: `tests/unit/test_job_model.py`, `tests/unit/test_promise_advisory.py`
- **Informed By**: spike-4 (confirmed: `type=bool` mandatory or the value hydrates as truthy string
  `'False'`; lazy-load flip is safe; legacy rows need a write backfill; 40 → 4 HGETALLs), spike-3
  (partitioned SortedField disqualified; IndexedField intersection bounds hydration)
- **Assigned To**: `job-model-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- Declare `has_open_promises = IndexedField(type=bool, default=False)`. `type=bool` is mandatory.
- Derive it inside `_write_goal_data` — the sole writer of `goal` — so no caller can bypass it. Set
  it before the `save`, including on the `save=False` mint path.
- Narrow `at_rest_with_open_promises` to `filter(status="at-rest", has_open_promises=True)`, keeping
  the fail-open wrapper and adding a defensive `open_promises()` re-verify before flagging.
- Document that the flag is a derived projection, never authoritative, and that `repair_indexes`
  cannot reconcile value-vs-derivation drift.

### 5. Combined backfill migration

- **Task ID**: build-backfill-migration
- **Depends On**: build-tz-normalize, build-open-promise-index
- **Validates**: `tests/unit/test_migrations.py`
- **Informed By**: spike-1 (skewed scores need one re-save), spike-4 caveat 2 (a new field's index is
  not retroactively populated; legacy rows are invisible to the filter until written)
- **Assigned To**: `migration-builder`
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: false
- Write ONE migration that iterates every Job once and both repairs the ZSET score and derives
  `has_open_promises`. Two separate full passes would be wasteful.
- Do **not** assign a timestamp — `save()` alone preserves the instant and avoids creating artificial
  score ties (spike-2b).
- Make it idempotent and resumable; skip rows already correct; log progress every N rows so a long
  run is observable rather than an apparent hang.
- Register in the `MIGRATIONS` dict (`scripts/update/migrations.py:999-1105`); may go last, no
  ordering dependency.
- Update `tests/unit/test_migrations.py::test_registered_in_migrations_dict` and
  `::test_every_migration_name_is_unique`.

### 6. Test suite updates

- **Task ID**: build-tests
- **Depends On**: build-bounded-recent, build-open-promise-index
- **Validates**: `tests/unit/test_job_model.py`, `tests/unit/test_promise_advisory.py`,
  `tests/unit/test_job_router.py`, `tests/unit/test_job_tool.py`
- **Informed By**: spike-2b (tie-tolerant assertions), spike-4 (the `test_stale_active_job_reaches_the_operator_log_end_to_end`
  interaction)
- **Assigned To**: `job-test-engineer`
- **Agent Type**: test-engineer
- **Parallel**: false
- Work the Test Impact checklist top to bottom; every UPDATE (verify only) item must actually be run,
  not assumed.
- Add the new cases from the Failure Path Test Strategy: tz regression, `limit=0`, dangling member,
  narrowed-scan row count, malformed `goal` JSON → `has_open_promises=False`.
- Do not relocate or rename `await _check_jobs_at_rest_with_open_promises()` in
  `agent/session_health.py` — `tests/unit/test_promise_advisory.py:266` asserts on the source text.

### 7. Schema-gate amendment and documentation

- **Task ID**: document-feature
- **Depends On**: build-open-promise-index, build-bounded-recent, build-backfill-migration
- **Assigned To**: `job-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Work the Documentation checklist, including the `models/job.py` module docstring, both
  `docs/features/durability-model.md` references, the `docs/features/README.md` row, and the parent
  plan's ratified-schema block and prerequisite note.
- Record the schema amendment as an amendment; do not silently rewrite the M3 ratification.

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: build-tz-normalize, build-bounded-recent, validate-bounded-recent,
  build-open-promise-index, build-backfill-migration, build-tests, document-feature
- **Assigned To**: `job-model-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm every Success Criterion.
- Confirm no surviving "only IndexedField" claim across code, features docs, and the parent plan.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_job_router.py tests/unit/test_job_tool.py tests/unit/test_promise_advisory.py tests/unit/test_migrations.py -q` | exit code 0 |
| Full suite | `./scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Bounded lookup uses ZREVRANGE | `grep -c "zrevrange" models/job.py` | output > 0 |
| ZSET key is derived, never f-strung | `grep -cE '"\$Sort(ed)?F:' models/job.py` | match count == 0 |
| Boolean field is typed | `grep -c "has_open_promises = IndexedField(type=bool" models/job.py` | output > 0 |
| At-rest scan is index-intersected | `grep -c 'filter(status="at-rest", has_open_promises=True)' models/job.py` | output > 0 |
| No post-hydration slice survives in `recent_for_room` | `sed -n '/def recent_for_room/,/def sweep_to_rest/p' models/job.py \| grep -c "jobs.sort("` | match count == 0 |
| Anti-criterion: `goal` written only via the choke point | `grep -rnE '(self\|job\|_job)[a-z_]*\.goal *=[^=]' models/job.py tools/job_tool.py bridge/job_router.py \| grep -vc 'json.dumps(data)'` | match count == 0 |
| Anti-criterion: no partitioned `at_rest_since` SortedField (spike-3 disqualified) | `grep -c "at_rest_since" models/job.py` | match count == 0 |
| Anti-criterion: no raw `zrem`/`zadd` introduced | `grep -cE "\.(zrem\|zadd)\(" models/job.py` | match count == 0 |
| Migration registered | `python -c "from scripts.update.migrations import MIGRATIONS; print(sum(1 for k in MIGRATIONS if 'job' in k.lower()))"` | output > 0 |
| Callers unchanged | `git diff origin/main --quiet -- bridge/job_router.py tools/job_tool.py` | exit code 0 |
| Schema claim amended in code | `grep -c "ONLY IndexedField" models/job.py` | match count == 0 |
| Schema claim amended in docs | `grep -c "only IndexedField" docs/features/durability-model.md` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Schema-gate amendment — approve adding a second IndexedField to `Job`?**
   `docs/plans/durability-room-job-agentrun.md:528-529` and `models/job.py:11-12` both state, as a
   ratified one-shot decision, that `status` is the *only* IndexedField. `build-open-promise-index` adds
   `has_open_promises = IndexedField(type=bool, default=False)`. The gate's machine anti-criterion
   (line 736 — never index a pid, uuid, or timestamp) is honored by a two-valued boolean, and no test
   introspects the field set, so nothing breaks mechanically. Spike-3 disqualified the alternative
   (`SortedField(partition_by="status")` orphans members on the exact `sweep_to_rest` code shape) and
   the remaining alternative (unpartitioned `at_rest_since`) bounds by rest *time* rather than by
   open *promise*, so it needs a watermark sweep and can still silently drop long-rested Jobs.
   **Recommendation: approve.** Confirm before `build-open-promise-index` runs.

2. **Should the three carved-out follow-ups be filed now or folded in?**
   All three are **filed**: #2639 (popoto 2× hydration), #2640 (`$SortF` orphan pruning), #2641 (the
   `$SortedF` typo in `validate_no_raw_redis_delete.py`). #2641 is a two-line hook fix and could
   arguably ride along in this PR rather than staying separate. **Recommendation: keep all three
   separate** — #2641 touches a validator that gates every Bash call in the repo, and mixing a hook
   change into a model PR muddies the blast radius. Confirm you agree before build.

3. **Is a `Verification` row that greps `git diff main` for unchanged callers actually the assertion
   we want?**
   The intent is "the bounded implementation required no call-site changes", which is the strongest
   evidence the swap is behavior-preserving. But it would also fail if a caller legitimately needed a
   one-line change. **Recommendation: keep it as a signal, and if a caller does need to change, treat
   that as a prompt to re-examine whether the swap is really transparent** rather than as a rule to
   delete.
