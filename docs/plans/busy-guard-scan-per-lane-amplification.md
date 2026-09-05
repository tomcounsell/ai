---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2712
last_comment_id: none
---

# Busy-guard scan: narrow the query, unamplify the sweep

## Problem

Every "is anything using this worktree lane?" decision in the codebase resolves by
hydrating the whole `AgentSession` table and filtering in Python.

`_scan_worktree_sessions()` (`agent/worktree_manager.py:433`) is the shared engine
behind both public wrappers — `worktree_busy_check()` (`:522`, fail-open) and
`worktree_busy_probe()` (`:538`, fail-closed). Its query is:

```python
sessions = AgentSession.query.all()   # agent/worktree_manager.py:478
```

It then discards, in Python, every row whose `status` is terminal and every row whose
`working_dir` is not inside `.worktrees/{slug}/`. The status half of that filter is
pure waste: `status` is an `IndexedField` (`models/agent_session.py:160`) and popoto
1.9.0 answers `status__in=[...]` as an O(1) set union, so the rows the loop is about to
throw away never needed to be fetched.

`tools/disk_reclaim.py` then calls the fail-closed probe once per candidate lane
(`:415`) on a daily unattended reflection. Each call re-runs the same whole-table
hydration from scratch — nothing is shared across the loop.

**Current behavior:**

- Every busy decision, interactive or scheduled, hydrates every `AgentSession` row
  including the terminal ones it is about to drop.
- The daily sweep repeats that hydration once per lane that reaches the probe guard.
  Nothing is memoized between iterations.

**Desired outcome:**

- The scan asks the index for non-terminal rows instead of asking for everything and
  sorting it out afterwards.
- One sweep performs one scan, not one per lane.
- `worktree_busy_check()` stays fail-open and `worktree_busy_probe()` stays fail-closed,
  bit for bit, including on every new code path the change introduces.

## Freshness Check

**Baseline commit:** `9705769a0` (`main`, 2026-09-05)
**Issue filed at:** 2026-08-10T05:58:43Z (26 days before planning)
**Disposition:** **Minor drift — with one premise correction that resizes the work.**

**File:line references re-verified:**

| Issue claim | Verified at | Status |
|---|---|---|
| `_scan_worktree_sessions()` at `agent/worktree_manager.py:433` | `:433` | Holds — exact line, unchanged |
| `AgentSession.query.all()` at `:478` | `:478` | Holds |
| `worktree_busy_check()` at `:522` (fail-open) | `:522` | Holds |
| `worktree_busy_probe()` at `:538` (fail-closed) | `:538` | Holds |
| Probe called per candidate lane in `tools/disk_reclaim.py` | `:415` | Holds |
| `slug` is an indexed `KeyField` | `models/agent_session.py:392` | Holds |

**Premise correction — the amplification factor is smaller than the issue states.**

The issue says: *"the sweep's per-lane guard chain calls the probe before it can know
whether the lane is a cheap skip, so the scan cost is paid for lanes that are then
discarded as `too_young`."* That is not what the code does. In `sweep_worktrees`, the
probe is the **fifth** guard, and every guard ahead of it is cheaper:

| Order | Guard | Line |
|---|---|---|
| 1 | `PROTECTED_WORKTREE_SLUGS` | `:387` |
| 2 | `too_young` | `:394` |
| 3 | `_worktree_is_dirty` | `:398` |
| 4 | `_worktree_has_live_process` | `:407` |
| **5** | **`worktree_busy_probe`** | **`:415`** |

`git show 3ca0811f6:tools/disk_reclaim.py` confirms the order was identical in the
commit that shipped `#2681`, so this was never true — the issue was filed from the
`/do-docs` cascade on the PR and mis-read the ordering. A lane discarded as `too_young`
pays **zero** scans, not one.

Measured dry-run of the real sweep on this checkout (`python -m tools.disk_reclaim --json`):
**11 lanes, of which 8 `too_young` and 1 `uncommitted_changes` — only 2 reached the
probe.** So the live amplification factor here is 2, not 11.

This does not make the issue wrong, it re-aims it. The lanes that *do* reach the probe
are exactly the old, clean, idle ones — and the ones that skip afterwards as `unmerged`
(`rv2888`, `sdlc-2817` in the measured run) are never removed, so they reach the probe
again every single night, forever. **The amplification is proportional to the count of
aged unmerged lanes, and that set only grows.** That is the durable defect; "73 scans a
night" is not.

**Cited sibling issues/PRs re-checked:**

- **#2681** (`Scheduled disk reclaim`) — MERGED 2026-08-10T07:34:47Z, i.e. *after* this
  issue was filed. It is the code under discussion; guard order unchanged since.
- **#2517** (`Nothing reclaims filesystem space on a schedule`) — CLOSED 2026-08-10.
  Its predicted follow-up is this issue.
- **#2639** (`popoto QueryBuilder executes its hydration pipeline twice`) — **CLOSED
  2026-09-05T08:06:46Z, roughly eleven hours before this plan.** `8c1a36ad1` bumped the
  popoto floor to 1.9.0 (`pyproject.toml:21`), which hydrates each row once per
  materialization instead of twice. The issue listed #2639 as "would reduce the constant
  factor here but not the N" — that halving has now landed, and every timing in this plan
  was measured on top of it.
- **#2699** (`_run_guarded_repairs has no wall-clock budget`) — CLOSED 2026-08-31.

**Commits on main since the issue was filed, touching referenced files:**

- `3ca0811f6` *Scheduled disk reclaim* (2026-08-10 14:34) — introduced the code; the
  guard chain this plan measures.
- `8bb12c001` *Harden production Redis against accidental flush* (2026-08-13) — touches
  `tools/disk_reclaim.py`; irrelevant to the query path.
- `ebbb3d592` *feat(nightly): classify newly-confirmed failures* (2026-09-03) —
  irrelevant to the query path.
- `8c1a36ad1` *Bump popoto floor to 1.9.0* (2026-09-05) — **changes the constant factor**,
  see #2639 above.

**Active plans in `docs/plans/` overlapping this area:** none.
`docs/plans/auto-preserve-teardown-half-deleted-worktree.md` (#3167) is the nearest
neighbour — same file, `preserve_uncommitted_worktree_changes` — and contains zero
references to `worktree_busy_*`, `_scan_worktree_sessions`, or `disk_reclaim`. Disjoint
functions in one module; a merge conflict is possible, a semantic conflict is not.

**Notes:** No line numbers drifted. The bug is still present and still worth fixing;
its magnitude is smaller and its shape is different from the issue's description, and
the Appetite below is set to the corrected magnitude.

## Prior Art

- **#2517** — *Nothing reclaims filesystem space on a schedule*. Shipped the scheduled
  reaper and the fail-open/fail-closed split. Its body explicitly predicted this defect
  ("*it currently uses `AgentSession.query.all()`, a full scan… a per-worktree sweep will
  amplify that*") and deliberately scoped it out. Succeeded at its own goal; this issue
  is the tracked remainder.
- **PR #2681** — *Scheduled disk reclaim, replacing the unguarded worktree-gc*. Merged
  2026-08-10. Introduced `sweep_worktrees` and its five-guard chain, and
  `worktree_busy_probe`. Round-6 review recorded zero tech-debt items — this cost was
  known and accepted, not missed.
- **#1357 / #1246** — the original busy guard. `docs/archive/plans-completed/sdlc-1357.md:132`
  records the original decision verbatim: *"Implementation uses `AgentSession.query.all()`
  filtered with a Python comprehension on `working_dir` containment + status; no new
  Redis index. Plan validates … that this is acceptable scale (typical AgentSession count:
  tens, not thousands)."* That premise still holds (57 rows measured live) — which is why
  this is a chore, not a bug.
- **#2305 defect 3** — added `_worktree_has_live_process`, the OS-level scan that sits
  ahead of the probe in the guard chain. Relevant because it is the guard that actually
  catches sessions the AgentSession row cannot see (see Risks, Risk 3).
- **#2639** — *popoto QueryBuilder executes its hydration pipeline twice*. Closed hours
  before this plan; halves the constant factor of every query below.

No prior attempt to fix *this* defect exists, so there is no "Why Previous Fixes Failed"
section: the cost was consciously deferred twice and tracked once.

## Research

No web research was performed, and none is warranted. The only external dependency in
scope is popoto, which is a first-party library developed in this same fleet
(`~/src/popoto`); its query semantics were established by reading the installed source
and by executing queries against the live model, which is stronger evidence than any
documentation page.

**What was consulted instead:**

- `.venv/lib/python3.14/site-packages/popoto/fields/key_field_mixin.py:388–464` —
  documents `field__in` as an OR query implemented as a Redis **Set UNION**, with
  set-based lookups (exact match, `__in`, `__isnull=True`) described as O(1).
- `.venv/lib/python3.14/site-packages/popoto/models/query.py:2856` — `field__in=[v1, v2]`
  confirmed in the public `filter()` contract.
- `pyproject.toml:21` — the popoto floor rationale, which is where the #2639 hydration
  fix is recorded.

These are the findings that make `status__in=` the load-bearing choice in the Solution
rather than a guess.

## Spike Results

Four spikes ran against live Redis on this checkout, read-only, through the ORM.

### spike-1: Is the `working_dir`-inside-lane predicate safely replaceable by `slug=`?
- **Assumption**: "`working_dir` inside `.worktrees/{slug}/` and `slug == {slug}` select
  the same rows, so the indexed `slug` KeyField can replace the Python containment filter."
- **Method**: code-read (every production write site of `AgentSession.working_dir`)
- **Finding**: **FALSE. The assumption does not hold, and there is a concrete production
  counterexample.** `tools/agent_session_scheduler.py:434-435` creates a child session
  with `working_dir = parent_session.working_dir` — which is `.worktrees/{parent_slug}/`
  whenever the parent is a lane session — and its `AgentSession.create(...)` call at
  `:439` passes **no `slug=` argument at all**, so the child row has `slug=None`. A
  `filter(slug=lane)` lookup misses that row entirely and reports the lane clear while a
  scheduled child session is queued to run inside it. `AgentSession.create_child()`
  (`models/agent_session.py:1939`) likewise takes `slug: str | None = None`.
- **Confidence**: high. `grep -rn '\.working_dir *=' --include='*.py'` over non-test code
  returns exactly one hit (`agent/session_runner/role_driver.py:198`, a driver attribute,
  not a session row), so `working_dir` is written once at creation and never migrated —
  making the creation sites an exhaustive enumeration. `tools/valor_session.py` is the
  well-behaved counter-example: it derives `working_dir` from the slug
  (`working_dir = str(get_or_create_worktree(repo_root, slug))`, `:750`) and its tests
  pin that `--parent` re-derives `working_dir` rather than copying the parent's.
- **Impact on plan**: **decisive.** The issue's headline suggestion — "the natural shape
  is an indexed lookup" on `slug` — is rejected. It is a silent correctness regression in
  the fail-closed reaper. The Solution narrows on `status` instead, which is
  behaviour-preserving by construction, and the `working_dir` containment matcher is left
  untouched. Pinned by a No-Go and an anti-criterion.

### spike-2: Does popoto answer `status__in=` from the index, and does it select the same rows?
- **Assumption**: "`filter(status__in=NON_TERMINAL_STATUSES)` returns exactly the rows the
  scan's `status not in TERMINAL_STATUSES` check keeps."
- **Method**: prototype (live query, set-compared against the scan)
- **Finding**: **TRUE.** `status__in` is accepted, and the returned id set is identical to
  the non-terminal subset of `query.all()` (57 == 57, `equal: True`). `TERMINAL_STATUSES`
  and `NON_TERMINAL_STATUSES` are disjoint (`models/session_lifecycle.py:65,72`), and
  `NON_TERMINAL_STATUSES` covers nine values including the three `paused*` variants.
- **Confidence**: high
- **Impact on plan**: this is the query rewrite. It is a strict push-down of a filter the
  Python loop already performs, so it cannot change which sessions are considered.

### spike-3: What is the measured cost, and where is the real amplification?
- **Assumption**: "The daily sweep costs 73 full scans."
- **Method**: prototype (timed queries) + dry-run of the real sweep
- **Finding**: `query.all()` = **7.0 ms** for 57 rows on popoto 1.9.0. The measured sweep
  reaches the probe for **2 of 11 lanes**, so the sweep's present-day cost is ~14 ms/day.
  On this checkout `status__in` measures the same 7.0 ms — **because all 57 live rows are
  non-terminal** (`{'running'}` is the only status present), so the filter currently
  eliminates nothing.
- **Confidence**: high for the numbers; the ratio is a property of one machine at one
  moment, not a constant.
- **Impact on plan**: this is why the fix is **two independent parts, not one**. The
  `status__in` narrowing pays off only in proportion to the terminal:non-terminal ratio,
  which this plan does not control and measured at its worst case. Hoisting the scan out
  of the loop pays off regardless of that ratio. Neither part subsumes the other, and
  shipping only the query narrowing would have left the measured machine exactly as slow
  as before.

### spike-4: Are there rows carrying a status outside the known enum?
- **Assumption**: "`status not in TERMINAL_STATUSES` and `status in NON_TERMINAL_STATUSES`
  agree on every row that exists."
- **Method**: code-read + live query
- **Finding**: they agree today, but they are **not the same predicate**. An unknown
  status value is non-terminal under the current check (so the lane reads **busy** —
  fail-closed) and absent from the index union (so the lane would read **clear** —
  fail-open). Live data carries no such value (`outside ALL_STATUSES: set()`), and
  `grep -rn '\.status *= *"'` finds no direct AgentSession status write outside
  `models/session_lifecycle.py` (the three hits are `models/job.py`, a different model).
- **Confidence**: high
- **Impact on plan**: the Python `status not in TERMINAL_STATUSES` check is **kept** after
  the indexed query rather than deleted as now-redundant. It costs one set membership test
  per surviving row and it is the thing that keeps the fail-closed posture honest if the
  enum ever grows. Risk 2 and a dedicated test cover it.

## Data Flow

**Today — one lane, one decision:**

1. **Entry point**: `sweep_worktrees` iterates `.worktrees/*`, reaches lane `L` at guard 5.
2. **`worktree_busy_probe(repo_root, L)`** → `_scan_worktree_sessions(repo_root, L)`.
3. **`AgentSession.query.all()`** hydrates **every** row in the table.
4. **Python loop** drops terminal rows, then normalizes each survivor's `working_dir` and
   segment-prefix-matches it against `.worktrees/L`.
5. **Output**: `("clear"|"busy"|"error", …)` → the sweep skips or continues.

Steps 2–4 repeat, from scratch, for every lane that reaches guard 5.

**After — one sweep, one scan:**

1. **Entry point**: `sweep_worktrees` iterates `.worktrees/*`; guards 1–4 unchanged, so a
   `too_young` lane still costs nothing.
2. **First lane to reach guard 5** triggers `worktree_busy_probe_many(repo_root, slugs)`
   once. Result memoized for the sweep.
3. **`AgentSession.query.filter(status__in=NON_TERMINAL_STATUSES)`** hydrates only
   non-terminal rows. One query for the whole sweep.
4. **One pass** over those rows assigns each to the lane containing its `working_dir`,
   building `{slug: (state, detail)}` — same normalization, same segment-prefix match, one
   pass instead of N.
5. **Each lane** reads its verdict from the map.
6. **Re-probe at the decision point**: a lane that survives all guards and is about to be
   handed to `cleanup_after_merge` gets a fresh single-slug `worktree_busy_probe` call
   first. Typically 0–3 lanes per sweep.
7. **Output**: identical tri-state per lane.

Interactive callers (`remove_worktree` at `:1594` via `worktree_busy_check`) are untouched
except that their single scan is now index-narrowed.

## Architectural Impact

- **New dependencies**: none. `NON_TERMINAL_STATUSES` is already exported from
  `models/session_lifecycle.py` and is already imported alongside `TERMINAL_STATUSES` in
  the same deferred-import block.
- **Interface changes**: purely additive. `worktree_busy_probe_many(repo_root, slugs) ->
  dict[str, tuple[str, str]]` is new. `_scan_worktree_sessions`, `worktree_busy_check`,
  and `worktree_busy_probe` keep their exact signatures and return shapes.
- **Coupling**: unchanged. The deferred import of `models.agent_session` stays inside the
  function body, preserving the property the existing docstring is explicit about —
  `worktree_manager` is loaded by tooling that must not pay the popoto bootstrap cost just
  to validate a slug.
- **Data ownership**: unchanged. No writes; every query stays read-only through the ORM.
- **Reversibility**: high. Both parts are independently revertable single-function changes
  with no schema, no migration, and no persisted state.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (to ratify the corrected premise in the Freshness Check — the issue's
  stated magnitude and its suggested `slug=` fix are both wrong, and someone should agree
  the reduced scope is still worth shipping)
- Review rounds: 1

Two functions change, one function is added, no schema moves. The bulk of this plan is
the investigation that says what **not** to build; the build itself is a couple of hours.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.9.0 | `python -c "import popoto; assert tuple(int(x) for x in popoto.__version__.split('.')[:2]) >= (1, 9), popoto.__version__"` | `status__in` set-union lookups, and the single-hydration fix from #2639 that every timing in this plan assumes |
| Reachable Redis | `python -c "from models.agent_session import AgentSession; AgentSession.query.filter(status='pending')"` | The scan under change queries it; a build that cannot reach Redis cannot validate the change |

## Solution

### Key Elements

- **`_fetch_live_sessions()`** (new, private, `agent/worktree_manager.py`): performs the
  one deferred import and the one indexed query, and returns `(rows, error_reason)`. It is
  the single place that decides what "live session" means at the Redis boundary.
- **`_scan_worktree_sessions(..., *, sessions=None)`** (modified): when `sessions` is
  `None` it fetches; when given a list it matches against that list. The matcher — path
  normalization, segment-prefix containment, the terminal-status check, first-match-wins —
  is not touched and is not duplicated.
- **`worktree_busy_probe_many(repo_root, slugs)`** (new, public): fetches once, then calls
  the existing matcher per slug against those rows. Returns `{slug: (state, detail)}` with
  the same tri-state each single-slug probe would have produced. A fetch failure yields
  `("error", reason)` for **every** requested slug.
- **`sweep_worktrees`** (modified): builds the batch map lazily on first need, reads each
  lane's verdict from it, and re-probes fresh, single-slug, immediately before handing a
  lane to `cleanup_after_merge`.

### Flow

`sweep_worktrees` iterates lanes → guards 1–4 unchanged (a `too_young` lane still costs
zero queries) → **first lane to reach guard 5** builds the batch map (one query) →
**every later lane** reads its verdict from the map → a lane that clears all five guards
plus `open_pr` and `unmerged` → **fresh single-slug probe** → `cleanup_after_merge`.

### Technical Approach

**Decision 1 — narrow on `status`, never on `slug`.** The issue proposes an indexed `slug`
lookup. spike-1 found a live counterexample: `tools/agent_session_scheduler.py:434-435`
writes a child session whose `working_dir` is the parent's worktree and whose `slug` is
`None`, so `filter(slug=lane)` would silently miss it and report a busy lane clear. That is
a correctness regression in the fail-closed reaper, which is the one caller that must never
guess. `status__in=NON_TERMINAL_STATUSES` gets the indexed lookup the issue wants while
leaving the `working_dir` predicate exactly as it is — it is a push-down of a filter the
Python loop *already performs*, so by construction it cannot change which sessions are
considered.

**Decision 2 — keep the Python `status not in TERMINAL_STATUSES` check.** It looks
redundant after the indexed query and it is not (spike-4): an unknown status value is
non-terminal under the current check and absent from the index union, so deleting the
Python check would silently flip that case from fail-closed to fail-open. One set
membership test per surviving row is not a cost worth arguing about.

**Decision 3 — inject rows rather than write a second matcher.** `worktree_busy_probe_many`
does not re-implement containment matching; it calls `_scan_worktree_sessions` with
`sessions=` pre-populated. One matcher, so batch and single-slug results cannot drift apart
as either is maintained. The per-slug cost drops to a Python pass over an in-memory list.

**Decision 4 — re-probe fresh before removal.** A sweep over dozens of lanes runs `gh`,
`_tree_stats`, and `git status` per lane, so the batch snapshot can be minutes stale by the
time a lane is actually deleted. `remove_worktree` does call `worktree_busy_check`, but that
wrapper is fail-**open** — it catches a session that started mid-sweep only while Redis is
healthy, and reads a Redis outage as clear. The fail-closed guarantee the sweep is
responsible for therefore needs a fresh fail-closed read at the decision point. It costs one
query for each lane actually being removed (0–3 in the measured run), against the N it
removes from the filtering pass.

**Decision 5 — lazy, not eager.** Building the map at sweep start would make an all-
`too_young` sweep pay one query where it currently pays zero. Building it on first need
preserves that zero and still collapses everything above it to one.

**Explicitly preserved postures.** `worktree_busy_check` keeps returning `None` for both
`clear` and `error`. `worktree_busy_probe` keeps returning `clear`/`busy`/`error`. Every
new exception path — the deferred import, the indexed query, the batch fetch — lands on
`("error", …)` exactly like the paths it replaces, and the batch fetch failure fans that
error out to every requested slug rather than defaulting any of them to clear.

## Failure Path Test Strategy

### Exception Handling Coverage

The touched scope contains four handlers. All four are `except Exception` with a
`logger.warning`/`logger.debug` and a defined return, not silent swallows — and each needs
a test asserting the observable outcome, not just the log.

- [ ] Deferred model import fails → `("error", "model_import_failed:{Type}", "")`, WARNING
      logged. Already covered at
      `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py:204`; re-assert
      after the refactor moves it into `_fetch_live_sessions`.
- [ ] Indexed query raises → `("error", "query_failed:{Type}", "")`, WARNING logged.
      Covered at `:212`; must keep passing against `filter(status__in=…)` rather than `all()`.
- [ ] Per-row matching raises (a row with an unreadable attribute) → the row is skipped at
      DEBUG and the scan continues. Covered at `:227`.
- [ ] **New:** batch fetch raises → `worktree_busy_probe_many` returns `("error", …)` for
      **every** requested slug, and the sweep skips every one of them as
      `busy_check_error:`. The batch path is the new way for a Redis outage to reach the
      reaper, and it is the one that must not default any lane to clear.

### Empty/Invalid Input Handling

- [ ] `worktree_busy_probe_many(repo_root, [])` returns `{}` and issues **no** query.
- [ ] A slug absent from the map (caller asks for a lane it did not request) must not
      KeyError into a silent clear — `sweep_worktrees` reads with an explicit default that
      is `("error", "not_probed")`, never `("clear", "")`.
- [ ] Rows with `working_dir` empty/None are skipped, as today (`:483`). Rows with a
      relative `working_dir` (e.g. `".worktrees/sdlc-1218"`) still resolve against
      `repo_root` — pinned at `:102`.
- [ ] `status` empty or None → row skipped, as today.

### Error State Rendering

- [ ] The sweep's user-visible output is its `skipped` reasons. Assert the exact strings
      survive: `busy_check_error:{detail}` and `live_session:{detail}`. A silent behavior
      change here would show up as a lane quietly reaped instead of reported.
- [ ] `worktree_busy_check` must still return `None` on `error` — the fail-open posture is
      user-visible as "interactive removal proceeds during a Redis hiccup", and flipping it
      would break interactive and post-merge cleanup.

## Test Impact

The three `test_disk_reclaim.py` cases below `monkeypatch.setattr(wm, "worktree_busy_probe", …)`.
Once `sweep_worktrees` consults `worktree_busy_probe_many` at guard 5, patching only the
single-slug function leaves those tests **passing vacuously** — patching a function the code
under test no longer calls at that point. This is the highest-risk item in the change: three
green tests that have stopped reaching the guard they claim to cover.

- [ ] `tests/unit/test_disk_reclaim.py::test_skips_lane_with_live_session` — UPDATE: patch
      `worktree_busy_probe_many` to return `{"lane": ("busy", "sess-1")}`; keep asserting
      `live_session:sess-1`.
- [ ] `tests/unit/test_disk_reclaim.py::test_busy_check_error_also_blocks_removal` — UPDATE:
      patch the batch function to fan `("error", "query_failed:ConnectionError")` across
      every slug; keep asserting `busy_check_error:query_failed:ConnectionError` and
      `sweep.removed == []`. This is the load-bearing fail-closed test; it must exercise the
      new path, not the retired one.
- [ ] `tests/unit/test_disk_reclaim.py::all_clear` fixture (`:66`) — UPDATE: stub both
      `worktree_busy_probe_many` (clear for every slug) and `worktree_busy_probe` (clear),
      since the happy path now crosses both the batch map and the pre-removal re-probe.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` (`TestScanWorktreeSessions`,
      `:204`–`:236`) — UPDATE: these patch `models.agent_session.AgentSession` wholesale, so
      they survive the query change, but the two error cases must be re-pointed at
      `filter(status__in=…)` instead of `all()` or they will assert against a call that no
      longer happens.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py::TestWorktreeBusyCheck`
      / `TestWorktreeBusyProbe` (`:38`–`:195`) — UPDATE mechanically (same `AgentSession`
      patch, `filter` instead of `all`); their assertions are the fail-open/fail-closed
      contract and must not change meaning by one character.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py:146` — no change:
      patches `worktree_busy_check` by object, unaffected.

**New tests** (not "impact", but required by the above):

- [ ] `worktree_busy_probe_many` agrees with N single-slug `worktree_busy_probe` calls over
      the same fixture rows, for clear / busy / error — the anti-drift test for Decision 3.
- [ ] The sweep performs **exactly one** batch fetch across many lanes (call counter on the
      query), and **zero** when every lane is `too_young` (Decision 5).
- [ ] A lane that reads clear in the snapshot and busy at the re-probe is **not** removed
      (Decision 4).

## Rabbit Holes

- **Indexing `working_dir`.** The predicate is segment-prefix containment, which a Redis
  exact-match index cannot answer; making it indexable means normalizing lane identity into
  a new field and backfilling it. That is a schema migration and an ongoing invariant to
  police, for a query measured at 7 ms. Not worth it at this appetite.
- **Making `slug` and `working_dir` agree everywhere.** The obvious "fix" for spike-1 is to
  pass `slug=parent_session.slug` at `tools/agent_session_scheduler.py:439` and then narrow
  on the indexed `slug`. Do not. A slug is not decoration — it drives worker routing
  (`_eng_stage_is_worktree_compatible`), branch resolution, and worktree provisioning, so
  stamping one on scheduled children changes scheduling behavior far outside this issue.
- **Chasing the synthetic-slug blind spot.** A slugless eng session gets `dev-{aid[:8]}`
  synthesized at `agent/session_executor.py:1314` as a **local variable** that is never
  written back, and the executor never persists the worktree it resolves either — so such a
  session is invisible to the `working_dir` predicate *and* to a `slug=` predicate alike.
  Real, pre-existing, unchanged by this plan, and already backstopped by
  `_worktree_has_live_process` at guard 4. Filed separately rather than fixed here.
- **Turning the whole sweep into one pass over a session index.** Tempting after building
  the batch map; it would entangle this change with `open_pr_branches` and `merged_via_tree`,
  both of which are `gh`/`git` bound and dominate the sweep's wall clock far more than Redis
  does. The scan is not the sweep's bottleneck and this plan should not pretend otherwise.
- **Reviving the "73 scans a night" framing.** The Freshness Check measured 2 probes per
  sweep on this checkout. Building to the issue's stated magnitude means over-engineering
  for a number that was never true.

## Risks

### Risk 1: The three `test_disk_reclaim.py` monkeypatches go stale and pass vacuously
**Impact:** The fail-closed guarantee for the unattended reaper loses its test coverage
while the suite stays green. This is the exact failure mode that lets a Redis outage delete
every merged lane, and it would ship undetected.
**Mitigation:** Listed first in Test Impact with explicit dispositions. Verified by
mutation, not by a green run: break `worktree_busy_probe_many` to return `("clear", "")`
unconditionally and confirm `test_busy_check_error_also_blocks_removal` **fails**. A test
that stays green under that mutation is not testing anything.

### Risk 2: `status__in` and `not in TERMINAL_STATUSES` diverge on an unknown status
**Impact:** A status value outside `ALL_STATUSES` would be treated as busy today
(fail-closed) and as clear after an index-only narrowing (fail-open) — a live worktree
deleted under a running session.
**Mitigation:** Decision 2 keeps the Python `not in TERMINAL_STATUSES` check after the
indexed query, so the fail-closed reading survives for any status the index union misses.
Pinned by a test that feeds a row with a fabricated status and asserts `busy`.

### Risk 3: The batch snapshot is stale by the time a lane is removed
**Impact:** A session that starts inside a lane mid-sweep is missed, and the lane is
deleted under it — the macOS cwd-vanished wedge that #1246/#1357 exist to prevent.
**Mitigation:** Decision 4's fresh single-slug re-probe immediately before
`cleanup_after_merge`, plus the two guards already inside `remove_worktree`
(`worktree_busy_check` and `_worktree_has_live_process`). Pinned by a test where the
snapshot says clear and the re-probe says busy.

### Risk 4: A lane missing from the batch map defaults to clear
**Impact:** A `KeyError`-avoiding `.get(slug, ("clear", ""))` would turn a lookup bug into
a silent deletion — the single most dangerous line this change could contain.
**Mitigation:** The default is `("error", "not_probed")`, never `("clear", "")`, and an
anti-criterion in Verification greps for a clear-valued default on the map read.

### Risk 5: Merge conflict with #3167 in `agent/worktree_manager.py`
**Impact:** Both lanes edit the same module.
**Mitigation:** Disjoint functions — #3167 works in `preserve_uncommitted_worktree_changes`,
this plan in `_scan_worktree_sessions` / `worktree_busy_*`, several hundred lines apart. A
textual conflict is resolvable by inspection; there is no semantic overlap to reason about.

## Race Conditions

### Race 1: Session starts in a lane between the batch snapshot and the removal
**Location:** `tools/disk_reclaim.py::sweep_worktrees`, between the lazy
`worktree_busy_probe_many` call and the `cleanup_after_merge` call.
**Trigger:** The sweep snapshots session state, then spends seconds-to-minutes running
`_tree_stats`, `git status`, `gh pr list`, and `merged_via_tree` across the remaining lanes.
A session is created with `working_dir` inside an already-snapshotted lane during that window.
**Data prerequisite:** The `AgentSession` row must be visible to the query *before* the
verdict that authorizes removal is read.
**State prerequisite:** The lane must not be deleted while any process holds a cwd inside it.
**Mitigation:** Re-read at the decision point (Decision 4) — a fresh fail-closed
single-slug probe immediately before `cleanup_after_merge`, so the authorizing read is
never older than the guards below it. `remove_worktree`'s own `worktree_busy_check` and
`_worktree_has_live_process` remain as the second and third lines.

### Race 2: Session created between the re-probe and `rmtree`
**Location:** `agent/worktree_manager.py::remove_worktree`, inherited unchanged.
**Trigger:** The classic TOCTOU already documented at
`docs/archive/plans-completed/sdlc-1357.md:206`.
**Data prerequisite / State prerequisite:** as Race 1.
**Mitigation:** Unchanged by this plan and deliberately so. The window shrinks (the
authorizing read moves *closer* to the removal than it is today) and no new window opens.
Called out here so a reviewer can confirm the change does not widen it.

### Race 3: Concurrent sweeps on one machine
**Location:** `sweep_worktrees`.
**Trigger:** A manual `python -m tools.disk_reclaim --apply` alongside the daily reflection.
**Data prerequisite:** none — the sweep is read-only until `cleanup_after_merge`.
**State prerequisite:** Two sweeps must not both decide to remove the same lane.
**Mitigation:** Unchanged. Each sweep's map is process-local and derived from the same
Redis state; the loser of a double removal gets a `cleanup_declined:` from
`cleanup_after_merge`, exactly as today. This plan introduces no shared or cached state
across processes.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #3176]` **The synthetic-slug blind spot.** A slugless eng session runs in
  `.worktrees/dev-{aid[:8]}/` while its stored row keeps `slug=None` and a main-checkout
  `working_dir`, so neither busy predicate can see it. Pre-existing, orthogonal to query
  shape, and unchanged by anything here. Filed as #3176 with its own recon; the open
  question there (whether persisting the synthesized identity is safe given that a slug
  drives worker routing) is genuinely unresolved and does not belong inside a query-cost
  chore.

Nothing else is deferred. The `slug=` narrowing is not deferred — it is **rejected** on
evidence (spike-1) and recorded as Decision 1, with an anti-criterion asserting it does not
appear in the diff. Indexing `working_dir` and stamping slugs onto scheduled children are
rejected likewise, in Rabbit Holes.

## Update System

No update system changes required. This changes two functions and adds a third inside code
that already ships with the repo. No new dependency (`NON_TERMINAL_STATUSES` is already
imported in the same block as `TERMINAL_STATUSES`), no new config file, no new
environment variable, no migration.

The popoto >= 1.9.0 floor this plan relies on is already committed in `pyproject.toml:21`
via `8c1a36ad1`, so `/update`'s existing `uv sync` propagates it with no change to the
update script or skill.

No Popoto schema migration is required: no model, field, or index is added, removed, or
retyped. The change consumes the existing `status` `IndexedField`.

## Agent Integration

No agent integration required. Both entry points already exist and neither changes shape:

- `tools/disk_reclaim.py` already has its CLI (`python -m tools.disk_reclaim`) and is
  already declared in `config/reflections.yaml` as the daily sweep. The change is internal
  to `sweep_worktrees`; its arguments, JSON output shape, and `DISK_RECLAIM_APPLY` arming
  gate are untouched.
- `agent/worktree_manager` is imported directly by `tools/disk_reclaim.py` and by
  `remove_worktree`/`cleanup_after_merge`. `worktree_busy_probe_many` is added for the
  in-process caller only and needs no CLI entry point in `pyproject.toml [project.scripts]`
  and no MCP surface.

Nothing new becomes reachable from Telegram, so there is no bridge wiring and no
agent-invocation integration test to add.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/scheduled-disk-reclaim.md` — the fail-open/fail-closed
      explanation at `:127`–`:133` and the touched-files list at `:173` are correct today
      and describe a per-lane probe. Document the batch probe, the lazy snapshot, and the
      fresh re-probe before removal, keeping the fail-closed rationale as the reason the
      re-probe exists.
- [ ] Update `docs/features/session-isolation.md:226` — "scans `AgentSession.query.all()`
      for live sessions" becomes the indexed non-terminal query. The surrounding
      segment-aware-containment description stays exactly as written; it is still the
      matcher, and saying so explicitly is the point.
- [ ] No new file in `docs/features/`, so no new row in `docs/features/README.md`. Both
      features are already indexed there.

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Rewrite the `_scan_worktree_sessions` docstring: it currently says "Walks the
      AgentSession table", which stops being true. State the indexed query, and state why
      the Python terminal-status check survives it (Decision 2) so nobody deletes it as
      dead code later.
- [ ] Docstring on `worktree_busy_probe_many` covering the contract, the fan-out of a fetch
      error to every requested slug, and the fact that it shares one matcher with the
      single-slug path.
- [ ] A comment at the `sweep_worktrees` map read naming why the default is
      `("error", "not_probed")` and not `("clear", "")` (Risk 4).

## Success Criteria

- [ ] `_scan_worktree_sessions` issues an indexed `status__in` query; `AgentSession.query.all()`
      no longer appears in `agent/worktree_manager.py`.
- [ ] The `working_dir` segment-prefix matcher is byte-for-byte the predicate it is today;
      no `filter(slug=` appears in `agent/worktree_manager.py`.
- [ ] A sweep over N lanes performs **one** session query, and **zero** when every lane is
      filtered out above guard 5.
- [ ] `worktree_busy_check` returns `None` for both clear and error; `worktree_busy_probe`
      returns `clear`/`busy`/`error`; a batch fetch failure yields `error` for every
      requested slug.
- [ ] `worktree_busy_probe_many` and N single-slug probes agree on identical fixture rows
      across all three states.
- [ ] The three stale-monkeypatch tests in `tests/unit/test_disk_reclaim.py` exercise the
      new guard path, proven by mutation: forcing the batch probe to return clear makes
      `test_busy_check_error_also_blocks_removal` fail.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail conversions apply — no expected-failure markers exist in
      `tests/unit/test_disk_reclaim.py` or `tests/unit/worktree_manager/` (verified at plan time).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead
never builds directly.

### Team Members

- **Builder (worktree-manager)**
  - Name: `scan-builder`
  - Role: `agent/worktree_manager.py` only — `_fetch_live_sessions`, the `sessions=`
    injection, and `worktree_busy_probe_many`.
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (disk-reclaim)**
  - Name: `sweep-builder`
  - Role: `tools/disk_reclaim.py` only — lazy batch map, map read with the error default,
    fresh re-probe before `cleanup_after_merge`.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `guard-tester`
  - Role: the Test Impact dispositions and the new tests, including the mutation proof.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `posture-validator`
  - Role: read-only verification that the fail-open/fail-closed postures and the matcher
    are unchanged, and that no test passes vacuously.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: the two feature docs and the docstrings.
  - Agent Type: documentarian
  - Resume: true

`scan-builder` and `sweep-builder` touch disjoint files and declare that split explicitly,
so they can run in parallel without the shared-worktree livelock.

## Step by Step Tasks

### 1. Narrow the query and add the batch probe
- **Task ID**: build-scan
- **Depends On**: none
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
- **Informed By**: spike-1 (slug narrowing rejected — a scheduled child carries the parent's
  worktree `working_dir` with `slug=None`), spike-2 (`status__in` selects exactly the
  non-terminal rows), spike-4 (keep the Python terminal check)
- **Assigned To**: scan-builder
- **Agent Type**: builder
- **Parallel**: true
- **Owns exclusively**: `agent/worktree_manager.py`
- Add `_fetch_live_sessions()` returning `(rows, error_reason)`: the deferred import of
  `AgentSession` / `TERMINAL_STATUSES` / `NON_TERMINAL_STATUSES`, then
  `AgentSession.query.filter(status__in=sorted(NON_TERMINAL_STATUSES))`. Import failure →
  `model_import_failed:{Type}`; query failure → `query_failed:{Type}`. Keep the existing
  WARNING logs verbatim.
- Give `_scan_worktree_sessions` a keyword-only `sessions=None`; when `None`, call
  `_fetch_live_sessions()` and return `("error", reason, "")` on a reason. Leave the
  matching loop, including `if status in TERMINAL_STATUSES: continue`, untouched.
- Add `worktree_busy_probe_many(repo_root, slugs) -> dict[str, tuple[str, str]]`: fetch
  once; on error return `("error", reason)` for every slug; otherwise call
  `_scan_worktree_sessions(repo_root, s, sessions=rows)` per slug and collapse to the same
  tri-state `worktree_busy_probe` produces. Return `{}` for an empty `slugs` without querying.
- Do not touch `worktree_busy_check` or `worktree_busy_probe`.

### 2. Unamplify the sweep
- **Task ID**: build-sweep
- **Depends On**: none (interface agreed from this plan; integrate against build-scan)
- **Validates**: `tests/unit/test_disk_reclaim.py`
- **Informed By**: spike-3 (guard 5 is reached by a minority of lanes, so the map must be
  lazy), Decision 4 (staleness), Risk 4 (the default must not be clear)
- **Assigned To**: sweep-builder
- **Agent Type**: builder
- **Parallel**: true
- **Owns exclusively**: `tools/disk_reclaim.py`
- In `sweep_worktrees`, replace the per-lane `worktree_busy_probe` at guard 5 with a read
  from a lazily-built map (built on first read, over every directory child that could still
  reach guard 5).
- Read it as `busy_map.get(slug, ("error", "not_probed"))`. Never default to clear.
- Immediately before `cleanup_after_merge`, call the single-slug `worktree_busy_probe` once
  more and skip on `busy`/`error` using the existing `live_session:` / `busy_check_error:`
  reason strings.
- Import `worktree_busy_probe_many` alongside the existing deferred imports at `:359`.
- Change no guard ordering, no skip-reason string, and no JSON output shape.

### 3. Re-point the tests, then prove they bite
- **Task ID**: build-tests
- **Depends On**: build-scan, build-sweep
- **Validates**: `tests/unit/test_disk_reclaim.py`,
  `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Apply every disposition in Test Impact.
- Add: batch-vs-single agreement across clear/busy/error; exactly one fetch per sweep;
  zero fetches when every lane is `too_young`; snapshot-clear + re-probe-busy is not
  removed; an unknown status value reads `busy`; `worktree_busy_probe_many(root, [])`
  returns `{}` and queries nothing.
- **Mutation-check each guard and re-measure after each change**: force
  `worktree_busy_probe_many` to return clear unconditionally and confirm
  `test_busy_check_error_also_blocks_removal` and `test_skips_lane_with_live_session` both
  **fail**. Paste the red output into the PR.

### 4. Posture validation
- **Task ID**: validate-postures
- **Depends On**: build-tests
- **Assigned To**: posture-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff the matching loop against `main` and confirm the predicate is unchanged.
- Confirm `worktree_busy_check` still collapses error to `None` and `worktree_busy_probe`
  still returns three states.
- Confirm no `filter(slug=`, no `query.all()`, and no clear-valued default on the map read.
- Confirm the mutation run reported red for the guards it targets.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-postures
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every item in the Documentation section.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: posture-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Busy-guard tests pass | `./scripts/pytest-clean.sh tests/unit/worktree_manager/test_worktree_manager_busy_guards.py -q` | exit code 0 |
| Disk-reclaim tests pass | `./scripts/pytest-clean.sh tests/unit/test_disk_reclaim.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/worktree_manager.py tools/disk_reclaim.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/worktree_manager.py tools/disk_reclaim.py` | exit code 0 |
| Indexed query present | `grep -c 'status__in' agent/worktree_manager.py` | output > 0 |
| Batch probe wired into the sweep | `grep -c 'worktree_busy_probe_many' tools/disk_reclaim.py` | output > 0 |
| Full scan gone (anti-criterion) | `grep -c 'query\.all()' agent/worktree_manager.py` | match count == 0 |
| Slug narrowing rejected (anti-criterion, Decision 1) | `grep -c 'filter(slug=' agent/worktree_manager.py` | match count == 0 |
| No clear-valued default on the map read (anti-criterion, Risk 4) | `grep -cE '\.get\([^)]*,[[:space:]]*\("clear"' tools/disk_reclaim.py` | match count == 0 |
| Terminal-status check survives (anti-criterion, Decision 2) | `grep -c 'in TERMINAL_STATUSES' agent/worktree_manager.py` | output > 0 |
| Fail-open wrapper intact | `grep -c 'def worktree_busy_check' agent/worktree_manager.py` | output > 0 |
| Fail-closed wrapper intact | `grep -c 'def worktree_busy_probe' agent/worktree_manager.py` | output > 0 |
| Guard order unchanged in the sweep | `grep -n 'too_young\|live_process:\|live_session:\|open_pr\|unmerged' tools/disk_reclaim.py \| head -5` | output contains too_young |
| No stale xfails in scope | `grep -rn 'xfail' tests/unit/test_disk_reclaim.py tests/unit/worktree_manager/` | exit code 1 |

The three anti-criteria rows above must each be demonstrated red before being trusted:
introduce the forbidden pattern deliberately, confirm the row FAILS, revert, and paste the
FAIL output into the PR description.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **The issue's stated magnitude is wrong, and the corrected one is small — still ship it?**
   The Freshness Check measured 2 probes per sweep on this checkout, not 73, because the
   probe is the fifth guard and `too_young` eliminates most lanes first. Present-day cost is
   roughly 14 ms/day. The durable argument is that aged **unmerged** lanes reach the probe
   every night and are never removed, so the multiplier only grows — but that is a slower
   and smaller problem than the issue describes. Ship at Small appetite as planned, or close
   #2712 as "measured, not worth it"?

2. **Is the pre-removal re-probe (Decision 4) worth its complexity?** Dropping it makes the
   change a pure simplification and forces the three `test_disk_reclaim.py` monkeypatches to
   be re-pointed only once. Keeping it preserves the fail-closed guarantee exactly at the
   moment of deletion, which is the guarantee #2517 built the probe for. The plan keeps it;
   a reviewer who thinks `remove_worktree`'s own guards suffice should say so at critique,
   not at build.
