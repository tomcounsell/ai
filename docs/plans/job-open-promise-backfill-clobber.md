---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2647
last_comment_id:
revision_applied: false
---

# Job open-promise backfill must not clobber a concurrent promise write

## Problem

`Job.backfill_open_promises_index()` (`models/job.py:449-487`) runs every day from
`Job.repair_indexes()` — deliberately while the bridge and workers are live. Its loop is
read-derive-write with no re-read before the write:

```python
for job in cls.query.filter():
    derived = any(entry.get("removed_ts") is None for entry in job._goal_data().get("promises", []))
    if job.has_open_promises is not derived:
        job.has_open_promises = derived
        job.save()
```

A bare `job.save()` re-encodes and HSETs the model's whole non-indexed field set from the
in-memory instance. The instance was hydrated at the top of this row's iteration. If a PM
`add_promise()` / `remove_promise()` / `append_goal_version()` lands on that Job between the
hydration and the `save()`, the backfill's stale `goal` overwrites it. The loss is a promise —
the durable record of what the system told a human it would do — not a timestamp.

**Current behavior:** the write is a full-hash write derived from a possibly-stale read, on a
path that runs concurrently with live promise mutation.

Two facts bound the exposure without closing it: the write fires only where the stored flag
disagrees with the derived value (a steady-state pass writes nothing), and the loop assigns no
`_now()`, so the write is otherwise convergent across machines running the daily tick against
shared Redis.

**Desired outcome:** the daily backfill can only ever write the one derived field it owns. A
concurrent promise write is structurally unreachable from this loop — not narrowed, not
probabilistically avoided. The no-`_now()` rule stays, because that is what keeps the write
convergent across concurrent machines.

A second, documentation-only finding rode in on the same issue: `docs/features/README.md:67`'s
Durability Model row never picked up the indexed at-rest promise backstop.

## Freshness Check

**Baseline commit:** `8afe2df22`
**Issue filed at:** 2026-08-07T07:01:45Z
**Disposition:** Minor drift — one of the issue's two findings was partially fixed on `main`
after filing.

**File:line references re-verified:**

- `models/job.py:449-487` — `backfill_open_promises_index()` body is verbatim as the issue
  quotes it. **Still holds.**
- `models/job.py:442-443` — `repair_indexes()` calls `cls.backfill_open_promises_index()` after
  `rebuild_indexes()`, on the daily maintenance path. **Still holds.**
- `models/job.py:103` — `has_open_promises = IndexedField(type=bool, default=False)`. **Still
  holds.**
- `models/job.py:165-175` — `_write_goal_data` is the derivation chokepoint. **Still holds.**
- `docs/features/durability-model.md:119` — the issue claims it still says "`Job.status` is the
  only IndexedField". **DRIFTED — already fixed.** Commit `0cee84389` (2026-08-07T14:07:38+07,
  after the issue was filed, `Refs #2647`) rewrote the line to "`Job` carries two IndexedFields,
  both low-cardinality: `status` (active/at-rest) and the derived boolean `has_open_promises`
  (Schema Gate Amendment 1, PR #2646); no index holds a pid, uuid, or timestamp." Both
  IndexedFields are named and the cardinality invariant is stated. **This half of finding 2 is
  done; nothing further is in scope for that file.**
- `docs/features/README.md:67` — Durability Model row lists "at-rest promise backstop on the
  health sweep" with no mention that it is index-served. **Still holds — in scope.**
- `docs/plans/durability-room-job-agentrun.md:543` (Schema Gate Amendment 1) — present, and its
  substance is already mirrored into `models/job.py`'s module docstring (lines 12-20). **Still
  holds.**

**Cited sibling issues/PRs re-checked:**

- #2634 — closed, stays closed. These findings carry forward here, as the issue states.
- PR #2646 / `84f07272c` — merged, shipped `has_open_promises`. Confirmed present.
- #2494 (parent epic) — open.

**Commits on main since issue was filed (touching referenced files):**

- `0cee84389` "Record Job's second IndexedField (has_open_promises) in the durability doc" —
  **partially addresses** finding 2. Its own commit message states "the race in the shipped
  backfill remains open", which matches this plan's scope.

**Active plans in `docs/plans/` overlapping this area:** `job-model-scaling-followups.md` is
`Complete` — the parent plan whose critique produced these findings; not active work.
`durability-room-job-agentrun.md` holds the schema gate this plan cites but is not being edited.
No overlap.

**Bug still reproducible?** Yes, by inspection of the code path. The defect is a write-scope
defect, not a state-dependent one: `save()` with no `update_fields` writes every non-indexed
field, unconditionally, from a stale in-memory instance. See spike-1 for the popoto evidence.

## Prior Art

- **#867** "Race: nudge re-enqueue stomped by worker finally-block finalize_session()" — the
  canonical read-derive-write clobber in this repo. Same shape: an instance hydrated at time T,
  a concurrent writer at T+1, a full-hash `save()` at T+2 that reverts it. Resolved; the remedy
  that stuck across the codebase is `save(update_fields=[...])` with an explicit field list.
  Directly relevant: this plan applies that same established remedy.
- **#875** "promote session_lifecycle.py to status authority with CAS" — the heavier answer to
  the #867 race family (a status authority with compare-and-set). Relevant as the road *not*
  taken here: a derived, self-healing, non-authoritative hint does not warrant a CAS layer, and
  popoto exposes no CAS primitive to build one on (spike-1, finding 4).
- **#2083** "Audit whether popoto 1.8.0 atomic index makes descriptor-pollution scar tissue
  redundant" — establishes that popoto 1.8.0's Lua-backed index maintenance is the repo's
  understood concurrency substrate for IndexedFields. This plan leans on exactly that property.
- **PR #2646** — shipped `has_open_promises` and the backfill being fixed here. The critique
  finding it left behind is this plan's premise.

No prior attempt to fix *this* backfill exists — it shipped four days' worth of commits ago and
has never been patched.

## Research

No relevant external findings — proceeding with codebase context and a direct read of the
pinned popoto source. The one external dependency in scope (popoto 1.8.0) is vendored in
`.venv/` and was read directly, which is stronger evidence than any published documentation
would be. See spike-1.

## Spike Results

### spike-1: What exactly does popoto's `save()` write, and is there an index-maintaining partial write?

- **Assumption**: "A bare `save()` writes the whole hash; a partial write that still maintains
  the `$IndexF` sets either does not exist or requires raw Redis (which the repo forbids)."
- **Method**: code-read of `.venv/lib/python3.14/site-packages/popoto/` (popoto 1.8.0, the
  version pinned in `pyproject.toml` and floored by `config/popoto_floor.py`).
- **Findings** (all with file:line evidence):
  1. `Model.save()` (`popoto/models/base.py:993`) has **no dirty tracking**. With no
     `update_fields`, it re-encodes the entire object and HSETs the full mapping
     (`base.py:1290-1303`). Confirms the clobber mechanism exactly as the issue describes.
  2. **`save(update_fields=[...])` exists and is index-maintaining** (`base.py:998`, implemented
     `base.py:1119-1272`). The HSET mapping is filtered to the listed names
     (`base.py:1133-1147`); `on_save` is invoked for **only** the listed fields
     (`base.py:1176-1185`, `1238-1247`), so `$IndexF` membership is maintained for exactly those.
  3. **IndexedField hash bytes are written by the Lua script, not by the HSET.** `save()`
     *excludes* IndexedField names from the HSET mapping entirely (`base.py:1296-1303`, comment:
     "EVAL (INDEX_SWAP_LUA) owns their hash writes atomically, so the plain HSET must not race
     with them"). `INDEX_SWAP_LUA` (`popoto/fields/indexed_field_mixin.py:97-127`) does the
     SREM-old / SADD-new / HSET-field server-side in one atomic script.
  4. **Therefore `save(update_fields=["has_open_promises"])` produces an empty HSET mapping**
     (the only listed field is an IndexedField, which is filtered out), and popoto takes the
     EVAL-only path with no HSET at all (`base.py:1152`, `1210-1212`). The write is exactly one
     atomic Lua call touching one hash field and its two index sets. `goal` is never sent.
  5. Popoto exposes **no** WATCH / CAS / version field (`grep` for `.watch(`/`transaction=`
     across the package returns nothing). The `pipeline=` parameter is MULTI/EXEC batching with
     no conflict detection. So optimistic concurrency is not an option, and does not need to be.
  6. Single-row re-fetch idiom: `Job.query.get(id=job.id, room_id=job.room_id)` takes the
     direct-key fast path (`popoto/models/query.py:1627-1632`) — one HGETALL, returns `None` if
     the hash is gone. `Job.query.get(id=...)` alone falls through to an index scan and must not
     be used.
  7. Caveat: the partial path skips composite `_meta.indexes` maintenance (which the full path
     does at `base.py:1429-1442`) and skips `is_valid()` / unique pre-checks. **Job declares no
     `Meta.indexes` and no unique fields** (`models/job.py:82-103`), so neither caveat applies.
- **Confidence**: high — direct source read of the pinned version, with line-level citations.
- **Impact on plan**: this converts the critique's prescription from a *narrowing* mitigation
  into a *structural* one. The critique asked for "re-read before write, skip rows whose goal
  changed" — a smaller window, still a window. Scoping the write to the single derived field
  removes the window entirely for the harm that matters (promise loss), because the stale `goal`
  is never transmitted. The re-read is still worth doing, but for a different and lesser reason
  (deriving the flag from fresh data), and its failure mode degrades to a self-healing stale
  hint rather than data loss.

### spike-2: Is `update_fields=` an established idiom here, or would this be novel?

- **Assumption**: "`save(update_fields=[...])` is already load-bearing in this codebase."
- **Method**: code-read — `git grep -n "update_fields" -- '*.py'`.
- **Finding**: 20+ call sites across `agent/`, `.claude/hooks/`, `bridge/`. `agent/sdk_client.py:226`
  documents it as the persistence convention; `agent/output_handler.py:1102` names the reason
  outright: "The helper uses `update_fields=` to avoid clobbering". This is the repo's settled
  answer to precisely this hazard.
- **Finding (added on revision — the first pass missed `models/`)**: the closest precedent is in
  the same layer, on the same ORM. `models/agent_session.py:1998` is
  `fresh.save(update_fields=["chat_message_log", "updated_at"])` following a re-fetch that falls
  back to `self` when the row vanished — the exact re-fetch-then-scoped-write shape this plan is
  about to write. Read `models/agent_session.py:1990-2000` before writing the loop.
- **Finding (`save()` override — does it transfer to `Job`?)**: `models/agent_session.py:995-1024`
  overrides `save()` to stamp `updated_at` with UTC wall-clock, because popoto's `auto_now` mints
  naive *local* time (bug #1645); the override also guards `update_fields` so a partial save that
  omits `updated_at` skips the stamp rather than desyncing memory from Redis. **That reason does
  not transfer to `Job`**, which declares no `auto_now` field: its only temporal field is
  `last_active_at`, a `SortedField` written explicitly by `_write_goal_data`, never automatically.
  `Job` therefore has no `save()` override and needs none, and
  `save(update_fields=["has_open_promises"])` passes straight through to `popoto.Model.save`.
  This is intended, not an oversight — stated here so a reader who notices the asymmetry does not
  have to re-derive it.
- **Confidence**: high.
- **Impact on plan**: no new pattern is being introduced, so no new doc needs to teach one. The
  fix is bringing one straggler into line with an existing convention that the sibling model in
  the same directory already follows.

## Data Flow

Two writers contend for one Job hash.

1. **Writer A — live promise mutation (the victim).** PM emits a promise → `tools/job_tool` /
   the advisory promise gate → `Job.add_promise()` → `Job._write_goal_data(data)`
   (`models/job.py:165`) → sets `self.goal = json.dumps(data)`, derives
   `self.has_open_promises`, calls `self.save()`. A full save: HSET of `goal` +
   `last_active_at` ZADD + one EVAL per IndexedField.
2. **Writer B — daily maintenance (the clobberer).** Daily cleanup reflection →
   `Job.repair_indexes()` (`models/job.py:346`) → after `rebuild_indexes()`, calls
   `backfill_open_promises_index()` (`:443`) → `for job in cls.query.filter()` hydrates every
   Job → derives from that snapshot's `goal` → on disagreement, full `save()`.
3. **The collision.** Between B's hydration of row *i* and B's `save()` of row *i*, A commits.
   B's HSET then rewrites `goal` from its pre-A snapshot. A's promise is gone from Redis; A's
   in-memory instance still believes it landed, so nothing raises and nothing logs.
4. **Reader — the backstop.** `Job.at_rest_with_open_promises()` (`:314`) intersects
   `status="at-rest"` with `has_open_promises=True`, then re-verifies each candidate against
   `open_promises()`. It is fail-open and surfaces to the operator surface only. Critically:
   because the reader re-verifies against `goal`, a *wrong flag* costs at most one wasted
   hydration or one delayed operator signal — while a *lost `goal`* is unrecoverable. That
   asymmetry is what makes write-scoping the right fix and CAS-on-the-flag the wrong one.

**After the fix**, step 3 cannot occur: B's write carries no `goal` bytes at all.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2646 (`84f07272c`) | Shipped `has_open_promises` + the daily backfill, correctly avoiding the lossy `_goal_data()` round-trip that critique BLOCKER 1 flagged | Fixed the *parsing* hazard (never re-serialize a malformed blob) but not the *write-scope* hazard. Sidestepping the round-trip made the code look safe — the instance's `goal` is never re-derived — while `save()` still transmits the hydrated `goal` verbatim. A stale-but-well-formed value is just as destructive as a mangled one. |
| Merge timing on #2634 | The critique verdict landed 4 minutes after PR #2646 merged | Not a code failure — a routing failure. Nothing existed to feed a post-merge critique verdict back into shipped code, so two valid findings fell on the floor. Out of scope here; this plan is the manual carry-forward. |

**Root cause pattern:** an ORM `save()` that looks like "persist my one change" but is actually
"declare my entire in-memory snapshot to be the truth". Every instance of this family in the
repo (#867, this one) is a read-derive-write loop where the author reasoned about the field they
touched and not about the fields they didn't. The durable countermeasure is the same each time:
name the fields.

## Architectural Impact

- **New dependencies**: none. `save(update_fields=[...])` is popoto 1.8.0, already the pinned
  floor.
- **Interface changes**: **none.** `backfill_open_promises_index(cls) -> int` keeps its exact
  signature; only the method body changes. (Round 2 retired the previously-planned keyword-only
  `room_id` parameter — see the Test Isolation subsection under Technical Approach for the facts
  that killed it.) The `int` return (rows restamped) is unchanged.
- **Coupling**: unchanged. The change is entirely inside one method body.
- **Data ownership**: sharpens it. `_write_goal_data` remains the sole authority for `goal` and
  its derived flag; the backfill's write scope is narrowed to state it owns nothing but the
  flag. That is a reduction in what the maintenance path can assert about a Job.
- **Reversibility**: trivial — a single-method revert, no schema or data change.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully determined by the issue's Revised bucket plus spike-1)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.8.0 installed | `.venv/bin/python -c "import popoto; assert tuple(int(p) for p in popoto.__version__.split('.')[:2]) >= (1, 8)"` | `save(update_fields=)` with atomic Lua index maintenance |
| Redis reachable for model tests | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | `tests/unit/test_job_model.py` exercises real Redis |

## Solution

### Key Elements

- **Scoped write**: the backfill's only write becomes
  `job.save(update_fields=["has_open_promises"])`. Per spike-1 this is a single atomic Lua EVAL
  that touches one hash field and its index sets, and sends zero bytes of `goal`. Concurrent
  promise loss from this loop becomes structurally impossible rather than merely unlikely.
- **Fresh derivation**: before deriving, re-fetch the row by primary key
  (`Job.query.get(id=..., room_id=...)`) and derive from the fresh `goal`. This is no longer
  load-bearing for data safety — it is load-bearing for *not writing a wrong flag*, which is a
  strictly lesser and self-healing harm. A row whose hash vanished between the scan and the
  re-fetch (`get` returns `None`) is skipped.

  **Why the re-fetch earns its HGETALL when the compare-and-skip does not** (both are "only a
  wrong flag is at stake" once the write is scoped, so the cost/benefit must be argued for each,
  not assumed from the other): `cls.query.filter()` hydrates the *entire* population up front,
  so the snapshot backing row *N* was read before rows 1..N-1 were processed. On a population of
  any size that staleness is unbounded and grows across the pass — minutes, by the tail. The two
  options do different things with that staleness. The compare-and-skip only *suppresses* the
  write, leaving the flag wrong until the next daily tick and re-losing the same race on that
  tick if the row is still busy. The re-fetch *corrects* it, collapsing the derivation window
  from "age of the whole enumeration" to "one HGETALL before one EVAL". Paying one direct-key
  HGETALL per row, once a day, to convert an unbounded staleness window into a microsecond one is
  the trade this plan accepts; paying nothing to convert it into a deferred wrong answer is not.

- **No new parameter**: the method signature is untouched. The tests need no room scope because
  `tests/conftest.py`'s autouse `redis_test_db` already gives every pytest process its own
  flushed Redis db — see Test Isolation under Technical Approach.
- **Convergence preserved**: still no `_now()` anywhere in the loop, so two machines running the
  daily tick concurrently against shared Redis compute and write the same value. Stated as a
  comment so a future edit does not quietly reintroduce a timestamp.
- **Doc row**: `docs/features/README.md:67`'s Durability Model row picks up that the at-rest
  promise backstop is index-served by `has_open_promises`.

### Flow

Daily cleanup reflection → `Job.repair_indexes()` → quarantine + `$IndexF` clear +
`rebuild_indexes()` → `backfill_open_promises_index()` → per row: re-fetch by key → derive from
fresh `goal` → **if disagreement, write only `has_open_promises`** → return count restamped.

### Technical Approach

Rewrite **only the loop body** of `models/job.py:449-487`. The signature (`:449`), the outer
`try:` (`:466`), the enumeration (`:467`), the inner `try:` (`:468`), the inner
`except Exception` (`:477-482`), the outer `except Exception` (`:483-484`), the summary
`logger.info` (`:485-486`) and the `return stamped` (`:487`) all stay exactly where they are.
The edit is confined to `:469-476`.

**The re-fetch goes INSIDE the per-row `try`.** This is not stylistic. If `fresh = cls.query.get(...)`
is placed above the inner `try:` — the natural reading of "iterate the enumeration to collect
candidates" — then one row whose HGETALL raises escapes to the outer handler at `:483` and aborts
the entire daily sweep with a partial count. That silently converts the shipped contract "one bad
row never stops the backfill" into a whole-pass abort on the live maintenance path, and makes this
plan's own mandated per-row fail-open test unsatisfiable. Required shape, verbatim in structure:

```python
for job in cls.query.filter():
    try:
        fresh = cls.query.get(id=job.id, room_id=job.room_id)
        if fresh is None:
            continue
        derived = any(
            entry.get("removed_ts") is None
            for entry in fresh._goal_data().get("promises", [])
        )
        if fresh.has_open_promises is not derived:
            fresh.has_open_promises = derived
            # Field-scoped on purpose: see Race 3 / the guard comment.
            fresh.save(update_fields=["has_open_promises"])
            stamped += 1
    except Exception as e:  # noqa: BLE001 — one bad row never stops the backfill
        logger.warning(
            "[job] open-promise backfill failed for %s: %s",
            getattr(job, "job_id", "?"),
            e,
        )
```

Notes on that shape:

- The `except` clause logs `getattr(job, "job_id", "?")` — `job`, not `fresh`. `fresh` may be
  unbound when the `get` itself raised; `job` is always in scope from the `for` target. This is
  also why the existing `except` body needs no edit at all.
- `cls.query.get(id=..., room_id=...)` uses the direct-key form with **both** KeyFields — spike-1
  finding 6 confirms `get(id=...)` alone degrades to an index scan.
- Derivation reads `fresh._goal_data()`, never the enumeration snapshot's `job`.
- A Job minted mid-pass is stamped correctly at mint by `_write_goal_data` and needs no backfill,
  so the enumeration missing it costs nothing.
- Keep both `except Exception` layers, the `logger.warning` bodies, the `stamped` count semantics
  and the summary `logger.info`. The fail-open contract on the maintenance path is unchanged.
- Update the docstring to state the write-scope invariant in the same register as the rest of
  this module: the backfill owns the derived flag and nothing else, and the write carries no
  `goal`.
- **Correct the docstring's existing rationale, which is factually wrong.** It currently claims
  `rebuild_indexes()` "cannot help here: a legacy hash has no `has_open_promises` field at all,
  and an absent value indexes as nothing rather than as `False`." That is not what popoto does.
  `rebuild_indexes()` decodes each hash through `decode_popoto_model_hashmap(cls, redis_hash)`
  (`popoto/models/base.py:2839`), which internally constructs `model_class(**model_attrs)` — so the
  missing attribute is filled from `default=False` — and then calls `field.on_save(...)` for
  *every* field on the instance (`popoto/models/base.py:2850-2856`, verified against the pinned
  1.8.0 source). Since
  `repair_indexes()` calls `rebuild_indexes()` at `models/job.py:442`, immediately *before* the
  backfill, legacy rows are already stamped and indexed as `False` by the time the backfill sees
  them. The honest rationale is the one that survives: the backfill is the daily re-derivation
  that catches any row whose stored flag has drifted from its `goal` for any reason, and it is
  what makes a *wrongly-`True`* or *wrongly-`False`* flag self-heal within a day. Write that,
  not the disproven claim.

### Test Isolation (why no `room_id` parameter is added — round-2 reversal)

The previous revision added a keyword-only `room_id` to `backfill_open_promises_index()` and called
it "a correctness requirement and not a convenience", on three premises: that the test Redis DB also
holds live production Jobs, that concurrent agents testing on this machine can contaminate it, and
that `test_backfill_is_idempotent`'s `second == 0` is a claim about the whole database and therefore
unfalsifiable in principle. **All three are false**, verified by reading the fixtures rather than
inferring from the unscoped `cls.query.filter()`:

- `tests/conftest.py:570-641` — `redis_test_db` is `autouse=True`, so it runs for *every* test. It
  does not `SELECT` on the production pool; it replaces `POPOTO_REDIS_DB` (and every popoto
  submodule's captured binding, and the async client) with a new `redis.Redis(db=test_db)`, and
  calls `flushdb()` at **both** setup (`:617`) and teardown (`:636`).
- `tests/db_claim.py:120-153` — `claim_test_db()` claims that db number atomically from the pool
  `[1..15]` under a process-lifetime `fcntl.flock`, memoized per process. db=0 (production) is
  excluded from the pool by construction (`range(1, _TEST_DB_POOL_MAX + 1)`), and the module
  docstring states the whole point is that two concurrent pytest *processes* can never land on the
  same db.

So: the unscoped sweep cannot reach a production Job (wrong db, and db 0 is never claimable),
concurrent agents' pytest processes hold different dbs, and because `flushdb()` runs at *setup* of
every test, "every Job hash in the DB" at assertion time is exactly the rows the test just minted.
`second == 0` is deterministic as written.

**Decision: the parameter is dropped.** Its only remaining value would be a robustness margin
against some future test in the same process minting Jobs *within the same test function* — which
nothing does, and which the per-test `flushdb()` bounds anyway. That does not earn a permanent
widening of a production classmethod's surface, a Success Criterion, and two Verification rows. The
signature stays `backfill_open_promises_index(cls) -> int`, the tests keep calling it with no
arguments, and the red-state proof (below, and Task 2) regains its meaning: the regression test now
runs against `main` unmodified and can only fail on the promise-survival assertion.

Deliberately **not** doing a compare-and-skip on `fresh.goal != job.goal`. The critique
prescribed it as the way to avoid the clobber; spike-1 shows the scoped write removes the
clobber outright, which makes the comparison dead weight that only suppresses correct writes
during a busy pass. A row whose `goal` changed mid-pass gets a flag derived from the *newer*
data, which is the better answer, not a hazard.

**No Popoto schema migration is required.** No field is added, removed, or retyped —
`has_open_promises` already exists and already ships. `scripts/update/migrations.py` needs no
entry.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `models/job.py` per-row `except Exception` (currently `:477-482`) — already covered in
      spirit by `test_backstop_never_raises_into_the_health_cycle`; add a backfill-specific test
      asserting a row whose re-fetch raises is logged and skipped while sibling rows still get
      stamped (observable behavior: return count, not just "no raise").
- [ ] `models/job.py` whole-loop `except Exception` (currently `:483-484`) — add a test that
      monkeypatches `Job.query.filter` to raise and asserts `backfill_open_promises_index()`
      returns `0` rather than propagating into the daily maintenance path.

### Empty/Invalid Input Handling

- [ ] A Job whose `goal` is `None` or malformed JSON: `_goal_data()` already logs and returns
      `{"versions": [], "promises": []}`, deriving `False`. Assert the backfill writes only the
      flag and leaves the malformed `goal` byte-identical afterward — this is the direct
      regression test for the clobber, and it doubles as proof that PR #2646's BLOCKER-1
      property (never re-serialize a bad blob) survives.
- [ ] A Job with an empty promise list derives `False` and, if already `False`, is not written.
      Covered by the existing idempotence test.

### Error State Rendering

- [ ] No user-visible output. The backfill's only surface is `logger.info` / `logger.warning` on
      the maintenance path; `at_rest_with_open_promises` surfaces to the operator surface only,
      never to human chat. Assertions are on log emission and return counts.

## Test Impact

- [ ] `tests/unit/test_job_model.py::TestOpenPromiseIndex::test_backfill_stamps_a_row_the_flag_missed`
      — UPDATE: the setup does `job.has_open_promises = False; job.save()` to fake a legacy row.
      That full `save()` is fine as *test* setup, but the assertion should additionally confirm
      the `goal` survived the backfill unchanged, so the test pins write scope rather than only
      flag correctness.
- [ ] `tests/unit/test_job_model.py::TestOpenPromiseIndex::test_backfill_is_idempotent` — UPDATE:
      keep as-is behaviorally (a settled population still writes nothing), but re-verify it holds
      once derivation moves to the re-fetched row.
- [ ] `tests/unit/test_job_model.py::TestOpenPromiseIndex` — ADD: a concurrency regression test.
      Hydrate the backfill's view, mutate the promise set through a *second* instance of the same
      Job (simulating the live writer), then let the backfill write, then re-read from Redis and
      assert the second instance's promise is still present. This is the test that fails on
      `main` and passes after the fix. Because the signature is unchanged (see Test Isolation),
      it runs against `main` verbatim and can only fail on the promise-survival assertion.
- [ ] `tests/unit/test_job_model.py::TestOpenPromiseIndex` — ADD: the two fail-open tests named
      in Failure Path Test Strategy (per-row raise, whole-loop raise).
- [ ] `tests/unit/test_job_model.py:240`, `:250`, `:251` — NO CHANGE. The previous revision
      mandated rewriting these three call sites to pass `room_id=`; that mandate is withdrawn with
      the parameter. `redis_test_db` (autouse, per-process claimed db, `flushdb()` at every test's
      setup and teardown) already makes the unscoped sweep touch nothing but the rows the test
      minted.
- [ ] No other test file references `backfill_open_promises_index`
      (`git grep -l backfill_open_promises_index -- tests/` → `tests/unit/test_job_model.py`
      only). Nothing else is affected.

No xfail/xpass markers exist for this bug (`grep` over `tests/` for `pytest.mark.xfail` /
`pytest.xfail(` matched nothing job- or promise-related), so there are none to convert.

## Rabbit Holes

- **Building a CAS / version-field layer on Job.** Spike-1 finding 5: popoto has no WATCH, no
  version field, no CAS hook. Building one means either a Lua script of our own or a
  `session_lifecycle`-style authority module (#875) — days of work to protect a *derived,
  self-healing hint* whose every consumer already re-verifies against the source of truth. The
  scoped write gets the actual safety property for one line.
- **Making the backfill hold a cross-machine lock.** `_repair_lock` is a `threading.Lock`, so it
  serializes within a process only; a Redis lock would serialize the daily tick fleet-wide. But
  the loop is convergent by construction (no `_now()`), so concurrent passes compute identical
  values. A distributed lock would add a new failure mode (a stuck lease blocking maintenance)
  to fix a problem that does not exist.
- **Retiring the backfill in favor of a one-shot migration.** Tempting — the field is stamped at
  the `_write_goal_data` chokepoint, so in theory only pre-#2646 rows ever need it. But
  `repair_indexes()` deletes and rebuilds `$IndexF:Job:*` on every run, and a daily
  re-derivation is the cheap insurance against any future gap. Changing the invocation cadence
  is a different decision than fixing the write, and re-litigating #2646's shipped design is not
  this issue's mandate.
- **Auditing every other `save()` in the repo for the same hazard.** Real and worth doing, and
  emphatically a separate slug — a repo-wide `save()` write-scope audit would balloon a
  one-method bug fix into a survey.

## Risks

### Risk 1: `update_fields=` silently skips something the full save was doing

**Impact:** an index or companion key drifts on the maintenance path — the exact class of bug
`repair_indexes()` exists to prevent, which would be a bitter irony.
**Mitigation:** spike-1 finding 7 enumerated the two things the partial path skips: composite
`_meta.indexes` maintenance and `is_valid()`/unique pre-checks. Job declares neither
`Meta.indexes` nor any unique field (`models/job.py:82-103`), so both are no-ops for this model.
The `SortedField` (`last_active_at`) is deliberately not in the field list — writing it would
reintroduce a `_now()`-flavored non-convergence. Verification includes an assertion that the
backfill leaves `last_active_at` unchanged.

### Risk 2: Deriving from a re-fetched row changes the return count under test

**Impact:** `test_backfill_is_idempotent` asserts a settled population returns `0`; if the
re-fetch subtly changes hydration (e.g. bool type coercion), the second pass could report
spurious restamps and the test would fail — or worse, pass while the daily pass writes on every
tick.
**Mitigation:** `type=bool` on the IndexedField is what makes hydration return a real bool rather
than the truthy string `"False"` (already pinned by
`test_flag_is_a_real_bool_not_a_truthy_string`). The `is not` identity comparison depends on
that. Keep the identity comparison, keep the idempotence test, and add the re-fetch path to it.

### Risk 3: The per-row re-fetch doubles the read volume of the daily pass

**Impact:** one extra HGETALL per Job on an unbounded, never-retired population.
**Mitigation:** accepted, and cheap: it is a direct-key HGETALL (spike-1 finding 6), not an index
scan, and it runs once daily on the maintenance path — the same path that already scans every
`Job:*` key twice and rebuilds every index. If the Job population ever makes this matter, the
enumeration itself will have become the problem first, and that is #2494 epic territory.

## Race Conditions

### Race 1: Backfill full-hash write vs. concurrent promise mutation (THE BUG)

**Location:** `models/job.py:465-487` (backfill loop) vs. `models/job.py:165-175`
(`_write_goal_data`, reached from `add_promise` / `remove_promise` / `append_goal_version`).
**Trigger:** the daily `repair_indexes()` tick hydrates Job *J*; before the loop reaches *J*'s
`save()`, a live PM turn calls `J.add_promise(...)` in the worker process and commits. The
backfill's `save()` then HSETs `goal` from its pre-mutation snapshot.
**Data prerequisite:** none — the backfill needs no state established by the writer; the hazard is
purely that it asserts state it did not read fresh.
**State prerequisite:** the stored flag must disagree with the backfill's derived value, since
that is the only condition under which the write fires at all. This is why the bug is rare in
steady state and why it will not stay rare — every legacy row is a disagreement by definition.
**Mitigation:** scope the write to `update_fields=["has_open_promises"]`. Per spike-1 findings
2-4, that field is an IndexedField, IndexedFields are excluded from the HSET mapping, and a
partial save whose entire field list is IndexedFields takes the EVAL-only path with no HSET at
all. The `goal` bytes are never transmitted, so the race has no destructive outcome available to
it. This is structural, not a narrowed window.

### Race 2: Backfill derives a flag from a snapshot that the writer has since invalidated

**Location:** same.
**Trigger:** same sequence as Race 1, after Race 1's mitigation is in place.
**Data prerequisite:** the `goal` JSON must be read fresh for the derived flag to be right.
**State prerequisite:** none.
**Mitigation:** re-fetch by primary key immediately before deriving, so the derivation window
shrinks to the gap between the HGETALL and the EVAL. The residual is explicitly accepted: the
worst outcome is a flag that disagrees with `goal` until the next daily pass. Every consumer
(`at_rest_with_open_promises`) re-verifies against `open_promises()`, so a stale flag can only
cost a hydration or delay an operator-surface signal — never produce a wrong answer, and never
lose data. Documented in the method docstring so the accepted residual is not mistaken for an
oversight.

### Race 3: Two machines run the daily tick concurrently against shared Redis

**Location:** `models/job.py:448-487`, and `_repair_lock` at `:343` (per-process, so it does not
serialize across machines).
**Trigger:** two hosts' daily cleanup reflections overlap.
**Data prerequisite:** none.
**State prerequisite:** both passes must compute the same value for the same row, or they will
flap the flag and each other's index membership.
**Mitigation:** the loop assigns no `_now()` and derives purely from `goal`, so the write is
convergent — both machines write the same bool, and `INDEX_SWAP_LUA` makes each write atomic
against the other (spike-1 finding 3; popoto 1.8.0's atomicity is exactly the property #2083
audited). Preserve this by keeping `last_active_at` out of the `update_fields` list and keeping
every timestamp out of the loop. Enforced by a Verification anti-criterion asserting no `_now()`
and no `last_active_at` appears in the method's **executable code** — the prose (docstring and
comments) is explicitly exempt, because the docstring is required to name both identifiers when
it explains the rule. See the Verification section's preamble for the AST-based extraction that
makes "code only" mean code only.

### Race 4: A row is deleted between enumeration and re-fetch

**Location:** `models/job.py` backfill loop, against `repair_indexes()` leg 1 (identity-less hash
quarantine) or any `Job.delete()`.
**Trigger:** the enumeration yields a Job whose hash is gone by the time the re-fetch runs.
**Data prerequisite:** the hash must exist for a write to be meaningful.
**State prerequisite:** none.
**Mitigation:** `cls.query.get(...)` returns `None` for a missing hash (spike-1 finding 6);
`None` → `continue`, uncounted. A write against a vanished key would resurrect a partial hash
with no KeyField data — precisely the identity-less phantom that `repair_indexes()` leg 1 exists
to destroy (#2207). Skipping is mandatory, not defensive.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2494] Changing the backfill's invocation cadence, or replacing the daily
  maintenance-path call with a one-shot `scripts/update/migrations.py` entry. That re-litigates
  PR #2646's shipped design and belongs to the parent durability epic's scaling work, not to a
  write-scope bug fix.
- [SEPARATE-SLUG #2494] A repo-wide audit of other bare `save()` calls in read-derive-write
  loops for the same hazard. Genuinely valuable and genuinely a survey; it would turn a
  one-method fix into an open-ended sweep.
- Nothing else is deferred. `docs/features/README.md:67` and the full test set are in scope.
  `docs/features/durability-model.md` is **partly done and partly in scope**, and the split
  matters: the *IndexedField enumeration* at `:119` (naming `status` and `has_open_promises` and
  stating the cardinality invariant) is already done on `main` via `0cee84389`, verified in the
  Freshness Check, and must not be re-edited. The *write-scope sentence* in the same file's Index
  safety (#2207 discipline) paragraph does not exist yet and **is in scope for this plan** — it is
  a Documentation checklist item, a Task 3 deliverable, a Success Criterion, and a Verification
  row.

## Update System

No update system changes required. No new dependency (popoto 1.8.0 is the existing pinned
floor), no new config file, no new secret, no service restart semantics. The change is a
single-method body edit plus a docs row; `/update`'s existing `git pull` + service restart
propagates it with no new step.

No Popoto schema migration is required and no `scripts/update/migrations.py` entry is added —
`has_open_promises` already exists on the model and in Redis. Adding a migration here would be
wrong: there is no schema delta to migrate.

## Agent Integration

No agent integration required. `backfill_open_promises_index()` is invoked only by
`Job.repair_indexes()` on the daily cleanup reflection path. It has no CLI entry point, no MCP
tool, and no bridge import, and this plan adds none — the agent's relationship to open promises
is through the existing advisory promise gate and the operator-surface health backstop, both
unchanged.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/durability-model.md` — extend the Index safety (#2207 discipline)
      paragraph to state that the daily `has_open_promises` backfill writes *only* that field
      (`save(update_fields=[...])`), so a maintenance pass on the live path can never overwrite a
      concurrently-written `goal`. The IndexedField enumeration itself is already correct as of
      `0cee84389` and needs no further edit.
- [ ] Update `docs/features/README.md:67` — the Durability Model row's "at-rest promise backstop
      on the health sweep" becomes "index-served at-rest promise backstop (`has_open_promises`)
      whose daily backfill is write-scoped to the derived field". This is the finding-2 remnant
      the Freshness Check confirmed is still open.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation

**These items are prose, and the anti-criteria that forbid `_now()` / `last_active_at` /
extra `update_fields=` occurrences match executable code only** (see the Verification preamble).
Naming the literal identifiers here is therefore required, not a violation. If you find yourself
paraphrasing to dodge a grep, the grep is being run wrong.

- [ ] `backfill_open_promises_index` docstring states the write-scope invariant (owns the derived
      flag, never `goal`), the accepted stale-flag residual and why it is safe (every consumer
      re-verifies), and the no-`_now()` convergence rule with its reason — **naming
      `last_active_at` as the field that must stay out of the `update_fields` list.** Both literal
      identifiers (`_now()` and `last_active_at`) must appear in `__doc__`; the Verification row
      "Docstring states the convergence rule" asserts exactly that, and a comment does not satisfy
      it because comments are not in `__doc__`.
- [ ] The docstring's *existing* `rebuild_indexes()` rationale is replaced, not extended — the
      claim that "an absent value indexes as nothing rather than as `False`" is disproven
      (`popoto/models/base.py:2848-2855`; see Technical Approach). Substitute the daily
      re-derivation / self-healing rationale.
- [ ] The docstring states that the re-fetch is deliberately inside the per-row `try`, so that a
      failing `query.get` costs one row and not the whole pass.
- [ ] A comment at the `update_fields=` call naming what would break if a future edit widened the
      field list or dropped the argument — specifically that adding `last_active_at` would
      reintroduce a per-machine timestamp and break cross-machine convergence (Race 3). This is an
      independent, ungated requirement: it does not substitute for the docstring bullet above.

## Success Criteria

- [ ] `models/job.py::backfill_open_promises_index` writes via
      `save(update_fields=["has_open_promises"])` and nowhere calls a bare `save()`.
- [ ] The loop re-fetches each row by both KeyFields before deriving, and skips rows whose
      re-fetch returns `None`. The re-fetch sits **inside** the per-row `try`, so a raising
      `query.get` is caught by the per-row handler and never reaches the outer one.
- [ ] `backfill_open_promises_index`'s signature is unchanged (`(cls) -> int`) — no `room_id`
      parameter was added, and no test call site was rewritten to pass one.
- [ ] A regression test exercises the interleaving (backfill hydrates → second instance adds a
      promise → backfill writes) and asserts the promise survives in Redis. It must fail on
      `main` **on that assertion** (not on a `TypeError` or any signature mismatch) and pass on
      the branch; the failure output is captured in the PR description.
- [ ] The backfill leaves `goal` and `last_active_at` byte-identical on every row it stamps.
- [ ] No `_now()` and no timestamp assignment appears anywhere in the method's **executable
      code** (convergence across concurrent machines preserved). The docstring and comments are
      exempt and are in fact *required* to name both identifiers when explaining the rule — the
      Verification row strips prose before grepping.
- [ ] Both fail-open layers keep their contract: a per-row failure is logged and skipped, a
      whole-loop failure returns `0`, neither raises into `repair_indexes()`.
- [ ] `docs/features/README.md:67` Durability Model row names the indexed at-rest promise
      backstop.
- [ ] `docs/features/durability-model.md`'s Index safety (#2207 discipline) paragraph states that
      the daily `has_open_promises` backfill writes only that field via
      `save(update_fields=[...])`, so a maintenance pass cannot overwrite a concurrently-written
      `goal`. The IndexedField enumeration at `:119` is untouched (already correct per
      `0cee84389`).
- [ ] The backfill's docstring no longer contains the disproven `rebuild_indexes()` claim ("an
      absent value indexes as nothing rather than as `False`"); it states the daily
      re-derivation / self-healing rationale instead.
- [ ] No `scripts/update/migrations.py` entry was added (there is no schema delta).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Two agents, not four. The critique was right that four named agents with resume state is
ceremony for an `appetite: Small` change that is one method body, four tests, one table row, and
one doc paragraph — especially when Tasks 1 and 2 are strictly sequential and the docs work is
two sentences of prose that the same agent holding the code context can write correctly in less
time than a handoff costs. The validator stays separate, because a change whose whole point is
"the write no longer carries data it does not own" should not be graded by the agent that wrote
it.

### Team Members

- **Builder (code, tests, docs)**
  - Name: `backfill-builder`
  - Role: Rewrite `backfill_open_promises_index` (room scope, re-fetch, write scope, docstring,
    inline comment); add the concurrency regression and fail-open tests and update the two
    existing ones; make both doc edits.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Validator**
  - Name: `backfill-validator`
  - Role: Verify every Success Criterion and run every Verification row, including the red-state
    proof that the regression test fails on `main`.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Scope the backfill write

- **Task ID**: build-backfill-write-scope
- **Depends On**: none
- **Validates**: `tests/unit/test_job_model.py::TestOpenPromiseIndex`
- **Informed By**: spike-1 (popoto `save(update_fields=)` is index-maintaining and takes the
  EVAL-only path when every listed field is an IndexedField; `query.get` needs both KeyFields;
  Job has no `Meta.indexes` or unique fields so the partial path's caveats do not apply);
  spike-2 (`update_fields=` is the established repo idiom, 20+ sites) — **and specifically
  `models/agent_session.py:1998`, the same-layer, same-ORM precedent for the exact
  re-fetch-then-scoped-write shape being written here. Read `models/agent_session.py:1990-2000`
  before writing the loop, and `models/agent_session.py:995-1024` (its `save()` override) to
  confirm why `Job` needs no equivalent** — see spike-2's `save()`-override finding.
- **Assigned To**: backfill-builder
- **Agent Type**: builder
- **Parallel**: false
- **Do not touch the signature.** `backfill_open_promises_index(cls) -> int` stays exactly as
  shipped, and `repair_indexes()`'s call at `:443` stays argument-free. (The `room_id` parameter
  the previous revision mandated is withdrawn — see Test Isolation.)
- Replace **only the loop body at `:469-476`**, keeping the outer `try` at `:466`, the enumeration
  at `:467`, the inner `try` at `:468`, and the inner `except` at `:477-482` in place. Inside the
  inner `try`: `fresh = cls.query.get(id=job.id, room_id=job.room_id)`; `continue` on `None`;
  derive from `fresh._goal_data()`; on disagreement set the flag on `fresh` and call
  `fresh.save(update_fields=["has_open_promises"])`.
- **The re-fetch must be inside the inner `try`.** Placing it above `:468` routes a raising
  HGETALL to the outer handler at `:483` and aborts the whole daily sweep. Copy the exact shape
  from Technical Approach.
- Do **not** add a `fresh.goal != job.goal` comparison — see Technical Approach for why it is
  dead weight once the write is scoped, and why the re-fetch is nonetheless kept.
- Keep both `except Exception` layers, their `logger.warning` bodies, the `stamped` counter
  semantics, and the summary `logger.info` exactly as they are.
- Assign no timestamp anywhere in the method's code; leave `last_active_at` out of the field list.
  (Naming it in the docstring/comment is required — the anti-criteria are code-scoped.)
- Update the docstring per the Inline Documentation checklist — including **replacing** the
  disproven `rebuild_indexes()` rationale — and add the guard comment at the `update_fields=`
  call.

### 2. Concurrency regression + fail-open tests

- **Task ID**: build-backfill-tests
- **Depends On**: build-backfill-write-scope
- **Validates**: `tests/unit/test_job_model.py`
- **Informed By**: spike-1 (a bare `save()` has no dirty tracking, so the interleaving test must
  assert on data re-read from Redis, never on either in-memory instance)
- **Assigned To**: backfill-builder
- **Agent Type**: builder
- **Parallel**: false
- Add the interleaving regression test to `TestOpenPromiseIndex`: mint a Job with a stale flag,
  hold a second instance fetched independently, mutate promises through the second instance, run
  `backfill_open_promises_index()`, then re-read from Redis and assert the promise survived and
  the flag is correct.
- **Confirm the red state, and confirm it fails for the right reason.** Because Task 1 leaves the
  signature alone, the new test runs against `main` verbatim: `git stash` the `models/job.py`
  change (or run the test on a `main` checkout) and confirm it fails **on the assertion that the
  concurrently-added promise survived in Redis** — not on a `TypeError`, a missing keyword
  argument, or any signature mismatch. Paste that assertion failure into the PR description. If
  the failure is anything other than the promise-survival assertion, the test is not proving the
  bug and must be rewritten.
- Add the two fail-open tests (per-row re-fetch raises → logged, skipped, siblings still stamped;
  `Job.query.filter` raises → returns `0`). The per-row test is what pins the re-fetch's placement
  inside the inner `try`: monkeypatch `Job.query.get` to raise for one job_id and assert the
  sibling row is still stamped and the return count is 1, not 0.
- Update `test_backfill_stamps_a_row_the_flag_missed` to also assert `goal` is byte-identical
  after the backfill; re-verify `test_backfill_is_idempotent` still returns `0` on a settled
  population.
- **Leave the three existing call sites at `tests/unit/test_job_model.py:240`, `:250`, `:251`
  unchanged.** The previous revision mandated adding `room_id=` to all of them; that mandate is
  withdrawn. `tests/conftest.py:570-641`'s autouse `redis_test_db` fixture already claims a unique
  per-process Redis db (never db 0) and `flushdb()`s at every test's setup and teardown, so the
  unscoped sweep is already bounded to the rows the test just minted.

### 3. Documentation

- **Task ID**: document-backfill
- **Depends On**: build-backfill-write-scope
- **Validates**: `docs/features/README.md`, `docs/features/durability-model.md` (Verification rows
  "README row updated" and "durability-model records write scope")
- **Informed By**: Freshness Check (`0cee84389` already fixed the IndexedField enumeration at
  `docs/features/durability-model.md:119`; only the write-scope sentence in the Index safety
  paragraph is open) — and the No-Gos, which now state that split explicitly
- **Assigned To**: backfill-builder
- **Agent Type**: builder
- **Parallel**: false (same single agent as Tasks 1-2; the flag was residue from the four-agent
  structure round 1 collapsed)
- Update the `docs/features/README.md:67` Durability Model row to name the index-served at-rest
  promise backstop and its write-scoped daily backfill.
- Extend the Index safety paragraph in `docs/features/durability-model.md` with the write-scope
  invariant. Do not re-edit the IndexedField enumeration — `0cee84389` already fixed it.

### 4. Final validation

- **Task ID**: validate-all
- **Depends On**: build-backfill-write-scope, build-backfill-tests, document-backfill
- **Validates**: every row in the Verification table and every line in Success Criteria
- **Informed By**: the Verification preamble (run the `$CODE` extraction snippet first — every
  method-scoped row greps that file, not `models/job.py` directly, and not a `sed` range)
- **Assigned To**: backfill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification preamble snippet to populate `$CODE`, **then run the mandatory positive
  control** (`printf ... | grep -cE '_now\(\)|last_active_at'` must print `2`). If it does not,
  stop and fix the pattern — every `== 0` row is void until it does. Then run every Verification
  row.
- Confirm the red-state proof for the regression test is recorded in the PR description **and that
  the recorded failure is the promise-survival assertion**, not a `TypeError` or signature
  mismatch. A red state produced by an argument error does not prove the bug and fails this task.
- Verify each Success Criterion, including the two negative ones (no bare `save()`, no
  migrations entry).

## Verification

### Preamble: extracting the method's executable code

Every method-scoped row below greps `$CODE`, defined once by the snippet in this preamble. Run it
first in the same shell.

```bash
# Writes the method's executable code — docstring and comments removed — to a temp file.
# `inspect.getsource` bounds the extraction to exactly this method regardless of what follows it
# in the file; `ast.unparse` of the docstring-stripped body drops every comment and the docstring.
CODE=$(mktemp)
.venv/bin/python - <<'PY' > "$CODE"
import ast, inspect, textwrap
from models.job import Job

src = textwrap.dedent(inspect.getsource(Job.backfill_open_promises_index))
fn = ast.parse(src).body[0]
if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
    fn.body = fn.body[1:]          # drop the docstring
print(ast.unparse(fn))             # ast.unparse emits no comments
PY
```

Two properties this buys, both of which the previous `sed`-range form lacked:

1. **Correct method bounds on macOS.** The old rows used `sed -n '/start/,/^    @classmethod\|^class /p'`,
   whose end regex relies on BRE alternation (`\|`). macOS `/usr/bin/sed` does not support it, so
   the range ran to EOF. That was invisible only because `backfill_open_promises_index` is
   currently the last method in `models/job.py`; appending any method after it would have silently
   widened all six checks, and "No bare save() in the backfill" would have started counting
   unrelated `.save()` sites. `inspect.getsource` has no such failure mode.
2. **Code-only matching.** The docstring is *required* to explain the no-`_now()` convergence rule
   and the comment is *required* to name `last_active_at` as the thing that must not enter the
   field list. Grepping raw source for those tokens would make the Inline Documentation
   requirements and the anti-criteria mutually unsatisfiable. Stripping prose first makes both
   simultaneously achievable, which is the point.

Note that `ast.unparse` normalizes string quotes to single quotes; the patterns below match that
normalized form, not the source's double quotes.

### Preamble step 2: positive control (MANDATORY — run before trusting any `== 0` row)

An anti-criterion that reports `0` proves nothing unless the pattern is known to be capable of
matching. Round 2 lost a blocker to exactly this: the convergence row used `grep -cE '_now\(\)\|last_active_at'`,
where `\|` under ERE is an escaped **literal pipe**, not alternation — so the pattern was the single
literal string `_now()|last_active_at` and could never match real code. It reported the expected `0`
whether or not a builder had reintroduced `_now()`.

Run this control first. If it does not print `2`, the pattern is broken and every `== 0` result
below is meaningless:

```bash
printf 'x = _now()\ny = last_active_at\n' | grep -cE '_now\(\)|last_active_at'
```

Probed on this machine (BSD grep, macOS 25.5.0): the corrected pattern prints `2` on that input and
`0` on `z = 1`; the old `\|` form prints `0` on both. The `\(\)` escapes are correct and must stay —
parentheses are ERE metacharacters. Only the alternation pipe is unescaped.

Apply the same discipline to any row added later: a grep gate is not committed until it has been
seen returning non-zero on a known-matching input.

### Rows

The `== 0` rows below append `|| true` because `grep -c` **exits 1 when the count is zero**. Without
it, a passing anti-criterion prints the expected `0` and returns a non-zero exit status, which a
validator reading the neighbouring "exit code 0" rows scores as a failure. With `|| true` the
command exits 0 and the *printed count* is the assertion.

| Check | Command | Expected |
|-------|---------|----------|
| Job model tests pass | `scripts/pytest-clean.sh tests/unit/test_job_model.py -q` | exit code 0 |
| Lint clean | `python -m ruff check models/job.py tests/unit/test_job_model.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/job.py tests/unit/test_job_model.py` | exit code 0 |
| Positive control (run first) | `printf 'x = _now()\ny = last_active_at\n' \| grep -cE '_now\(\)\|last_active_at'` | prints `2` — if not, every `== 0` row below is void |
| Write is field-scoped | `grep -c "update_fields=\['has_open_promises'\]" "$CODE"` | prints >= 1 (probed: 1 on a post-fix body, 0 on main's) |
| No bare save() in the backfill | `grep -c '\.save()' "$CODE" \|\| true` | prints `0` (probed: main's body prints 1, so this row has a live positive control) |
| Convergence: no timestamp in the backfill's code | `grep -cE '_now\(\)\|last_active_at' "$CODE" \|\| true` | prints `0` — only meaningful after the positive-control row above prints `2` |
| Row re-fetched by both KeyFields | `grep -cE 'query\.get\(id=.*room_id=' "$CODE"` | prints >= 1 (probed: 1 on a post-fix body, 0 on main's) |
| Re-fetch is inside the per-row `try` | `.venv/bin/python -c "import ast,inspect,textwrap;from models.job import Job;fn=ast.parse(textwrap.dedent(inspect.getsource(Job.backfill_open_promises_index))).body[0];loop=[n for n in ast.walk(fn) if isinstance(n,ast.For)][0];print(all(isinstance(s,ast.Try) for s in loop.body) and any('query.get' in ast.unparse(s) for t in loop.body for s in t.body))"` | `True` — the `for` body is nothing but a `Try`, and the `query.get` lives in that `Try`'s body |
| Signature unchanged (no `room_id` added) | `.venv/bin/python -c "import inspect;from models.job import Job;print(str(inspect.signature(Job.backfill_open_promises_index)))"` | `() -> 'int'` — probed on main; the return annotation is a string because `models/job.py` carries `from __future__ import annotations`. Any `room_id` in the output fails the row. |
| Production sweep call unchanged | `grep -n 'backfill_open_promises_index()' models/job.py` | matches the `repair_indexes()` call site at `:443` (no argument) |
| Test call sites unchanged | `grep -c 'backfill_open_promises_index(room_id=' tests/unit/test_job_model.py \|\| true` | prints `0` (no test was rewritten to pass a room scope) |
| Docstring rationale corrected | `.venv/bin/python -c "from models.job import Job; d=' '.join((Job.backfill_open_promises_index.__doc__ or '').split()); print('absent value indexes as nothing' in d)"` | `False` (probed: prints `True` on main's docstring, so the row has a live positive control) |
| Docstring states the convergence rule | `.venv/bin/python -c "from models.job import Job; d=' '.join((Job.backfill_open_promises_index.__doc__ or '').split()); print('_now()' in d and 'last_active_at' in d)"` | `True` (probed: prints `False` on main's docstring) |
| Anti-criterion: no migrations entry added | `git diff main --name-only -- scripts/update/migrations.py` | empty output (probed: prints the path when the file is modified) |
| README row updated | `grep -c 'has_open_promises' docs/features/README.md` | prints > 0 (probed: currently `0` on main, so this is a live gate) |
| durability-model records write scope | `grep -c 'update_fields' docs/features/durability-model.md` | prints > 0 (probed: currently `0` on main, so this is a live gate) |

## Critique Results

Round 3 (post round-2 revision). Round 2's nine findings were all dispositioned and are not
repeated here. Round 3 ran the FULL war room (3 critics) plus a driver that empirically probed
every load-bearing claim against live Redis and the pinned popoto 1.8.0.

**What round 3 confirmed rather than challenged.** All of spike-1's popoto findings hold under
direct source read. Live probes confirm the scoped write preserves `goal`, leaves `last_active_at`
byte-identical, and correctly adds to *and* removes from the `has_open_promises` index set; that a
bare `save()` on the same interleave destroys the promise; and that `query.get` returns `None` for
a deleted row (Race 4). Every cited file:line is exact. Both Prerequisites pass. The `$CODE`
preamble runs, the round-2 positive control prints `2`, and `inspect.signature` prints `() -> 'int'`
exactly as the Verification rows predict. The test-isolation reversal that dropped `room_id` rests
on facts that check out. Structural checks are all PASS.

**The one defect.** All three critics independently flagged the same thing, and the driver had
already measured it: the plan's regression-test recipe cannot go red.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (unanimous 3/3) | The plan's mandated red-state proof does not go red. Task 2 says the new regression test "runs against `main` verbatim: `git stash` the `models/job.py` change (or run the test on a `main` checkout) and confirm it fails **on the assertion that the concurrently-added promise survived in Redis**", and Test Impact specifies the recipe as "hydrate the backfill's view, mutate the promise set through a *second* instance, then let the backfill write". The critique driver executed exactly that recipe against unmodified `main` and measured `stamped=0, promises_survived=1` — **the test passes GREEN on `main`**. The cause is structural, not incidental: `QueryBuilder.__iter__` is `return iter(self.all())` (`popoto/models/query.py:1464`), so `cls.query.filter()` hydrates at call time; a strictly sequential "mutate, then call the whole method" test lets the backfill's own hydration observe the committed promise, the derived value agrees with the stored flag, and no write fires at all. The race requires the mutation to land *between* the enumeration's hydration and the `save()`, which the method's public surface cannot expose. Task 2's contingency ("If the failure is anything other than the promise-survival assertion, the test is not proving the bug and must be rewritten") anticipates a wrong-reason failure but not *no failure at all*, so a builder following the instructions literally has no written path forward. Success Criterion 4 ("It must fail on `main` **on that assertion**") is therefore unsatisfiable as written — the same defect class as round 2's CONCERN 4, recurring in the place round 2's fix did not reach. The round-2 Critique Results table's own alternative for CONCERN 4 ("temporarily change `fresh.save(update_fields=[...])` to `fresh.save()`") does not rescue it either: with the re-fetch in place `fresh` is read immediately before the write, so a bare `save()` on the branch carries a current `goal` and clobbers nothing. | pending | The enumeration must be forced stale by monkeypatch — this is the only shape that reproduces. **Red-state regression test (the Success Criterion 4 artifact), driver-measured `stamped=1, promises_survived=0` on `main` and `stamped=0, promises_survived=1` on the fixed loop:** `job = Job.mint(scratch_room_id, "owes a reply")`; `snap = Job.query.get(id=job.id, room_id=job.room_id)`; `snap.has_open_promises = True; snap.save()` (this is what creates the flag-vs-goal disagreement that makes the write fire at all — without it nothing is written and the test is vacuous); `live = Job.query.get(id=job.id, room_id=job.room_id); live.add_promise("promise A")`; `monkeypatch.setattr(Job.query, "filter", lambda *a, **k: [snap])`; `Job.backfill_open_promises_index()`; then re-read from Redis and assert the promise survived. Delete the `git stash` / `main`-checkout instruction — do not merely supplement it; it is the specific instruction that produces the false green. Note the fixed loop passes this test by *declining to write* (the re-fetch makes flag and goal agree), so it proves promise survival but does NOT exercise the scoped write. **Add a second, complementary test that pins write scope**, driver-measured `stamped=1, promises_survived=1` on the fixed loop: hydrate `snap` BEFORE any promise exists, `live.add_promise(...)`, then drift the stored flag with a scoped write (`drift.has_open_promises = False; drift.save(update_fields=["has_open_promises"])`), then run the backfill against `[snap]`. Assert `stamped == 1` AND the promise survived AND `last_active_at` is unchanged. This second test is green on `main` too (`stamped=0`), so it is a scope pin, not a regression proof — the plan needs both, and must say which is which. |

---

## Open Questions

None. The issue's Revised bucket fully determines the desired outcome, and spike-1 resolved the
one open technical question (whether an index-maintaining partial write exists) with a
higher-confidence answer than the critique's original prescription assumed. The only judgment
call made without asking is documented in Technical Approach: dropping the prescribed
`fresh.goal != job.goal` compare-and-skip, because the scoped write makes it dead weight that
would only suppress correct writes during a busy pass.
