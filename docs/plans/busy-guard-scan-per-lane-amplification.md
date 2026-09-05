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

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
