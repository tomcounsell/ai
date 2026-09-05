---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2712
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-05T13:56:35Z
---

# Busy-guard scan: narrow the query, unamplify the sweep

## Problem

Every "is anything using this worktree lane?" decision in the codebase resolves by
hydrating the whole `AgentSession` table and filtering in Python.

`_scan_worktree_sessions()` (`agent/worktree_manager.py:457`) is the shared engine
behind both public wrappers — `worktree_busy_check()` (`:546`, fail-open) and
`worktree_busy_probe()` (`:562`, fail-closed). Its query is:

```python
sessions = AgentSession.query.all()   # agent/worktree_manager.py:502
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

**Baseline commit:** `491a88624` (`origin/main`, 2026-09-05). Every line number below
and everywhere else in this document was re-derived against that commit during the
revision pass; the original draft was anchored at `9705769a0`, and `55ad9ac89` (#3162,
landed 2026-09-05T12:52:41Z) added 102 lines to `agent/worktree_manager.py` between the
two, shifting every citation in that file by ~24 lines.
**Issue filed at:** 2026-08-10T05:58:43Z (26 days before planning)
**Disposition:** **Minor drift — with one premise correction that resizes the work.**

**File:line references re-verified at `491a88624`:**

| Issue claim | Verified at | Status |
|---|---|---|
| `_scan_worktree_sessions()` in `agent/worktree_manager.py` | `:457` | Holds (draft said `:433`) |
| `AgentSession.query.all()` | `:502` | Holds (draft said `:478`) |
| `worktree_busy_check()` (fail-open) | `:546` | Holds (draft said `:522`) |
| `worktree_busy_probe()` (fail-closed) | `:562` | Holds (draft said `:538`) |
| Probe called per candidate lane in `tools/disk_reclaim.py` | `:415` | Holds exactly |
| Sweep's deferred import block (where the batch probe joins) | `tools/disk_reclaim.py:358`–`:364` | Holds |
| `slug` is an indexed `KeyField` | `models/agent_session.py:392` | Holds |
| `status` is an `IndexedField` | `models/agent_session.py:160` | Holds |
| `AgentSession.create_child()` takes `slug: str \| None = None` | `models/agent_session.py:1931` | Holds (draft said `:1939`) |
| Materialize-inside-try precedent | `models/agent_session.py:1389` | Holds |
| Scheduled child inherits parent `working_dir` | `tools/agent_session_scheduler.py:434`–`:435` | Holds |
| …and its `AgentSession.create(...)` passes no `slug=` | `tools/agent_session_scheduler.py:440`–`:457` | Holds (draft said `:439`) |
| `working_dir` derived from slug (well-behaved counter-example) | `tools/valor_session.py:753` | Holds (draft said `:750`) |
| Synthetic `dev-{aid[:8]}` slug is a local variable | `agent/session_executor.py:1314` | Holds |
| `TERMINAL_STATUSES` / `NON_TERMINAL_STATUSES` / `ALL_STATUSES` | `models/session_lifecycle.py:65` / `:72` / `:109` | Holds |
| `remove_worktree` calls `worktree_busy_check` | `agent/worktree_manager.py:1696` | Holds (draft said `:1594`) |

**Guard chain in `sweep_worktrees`, re-derived at `491a88624`:** protected `:389`,
`too_young` `:394`, dirty `:398`, live process `:407`, busy probe `:415`. The draft's
`:387` for guard 1 pointed at the comment above the `if`; the rest hold exactly.

**Premise correction — the amplification factor is smaller than the issue states.**

The issue says: *"the sweep's per-lane guard chain calls the probe before it can know
whether the lane is a cheap skip, so the scan cost is paid for lanes that are then
discarded as `too_young`."* That is not what the code does. In `sweep_worktrees`, the
probe is the **fifth** guard, and every guard ahead of it is cheaper:

| Order | Guard | Line |
|---|---|---|
| 1 | `PROTECTED_WORKTREE_SLUGS` | `:389` |
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
- `55ad9ac89` *Stale-branch sweep reaps nightly-triage worktrees and sees checked-out
  branches* (#3162, closed 2026-09-05T12:52:41Z) — **+102 lines in
  `agent/worktree_manager.py`**, adding `reap_idle_worktree()` at `:1007` as a third
  worktree-removal entry point. It shifted every citation in that file and is the reason
  the baseline above moved. It touches neither `_scan_worktree_sessions` nor
  `worktree_busy_*`: `reap_idle_worktree` refuses on a non-empty `git status --porcelain`
  and never consults an `AgentSession` row, so it is a baseline fact for this plan, not a
  code overlap and not a concurrent editor. Verified as an ancestor of `491a88624`.

**Active plans in `docs/plans/` overlapping this area:** one.
`docs/plans/auto-preserve-teardown-half-deleted-worktree.md` (#3167, OPEN) is the nearest
neighbour and edits two regions of `agent/worktree_manager.py`:
`preserve_uncommitted_worktree_changes` (`:1508`) and `_cleanup_stale_worktree` (`:903`,
where it fixes a slug/branch mismatch in the same edit). Neither region references
`worktree_busy_*`, `_scan_worktree_sessions`, or `disk_reclaim`, and neither is reachable
from them. Disjoint functions in one module; a merge conflict is possible, a semantic
conflict is not. See Risk 5.

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
  `:440`–`:457` passes **no `slug=` argument at all**, so the child row has `slug=None`. A
  `filter(slug=lane)` lookup misses that row entirely and reports the lane clear while a
  scheduled child session is queued to run inside it. `AgentSession.create_child()`
  (`models/agent_session.py:1931`) likewise takes `slug: str | None = None`.
- **Confidence**: high. `grep -rn '\.working_dir *=' --include='*.py'` over non-test code
  returns exactly one hit (`agent/session_runner/role_driver.py:198`, a driver attribute,
  not a session row), so `working_dir` is written once at creation and never migrated —
  making the creation sites an exhaustive enumeration. `tools/valor_session.py` is the
  well-behaved counter-example: it derives `working_dir` from the slug
  (`wt_path = get_or_create_worktree(repo_root, slug)` then `working_dir = str(wt_path)`, `:753`–`:754`) and its tests
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
- **Re-measured during the revision pass**: `filter()` returns
  `popoto.models.query.QueryBuilder`, not a list. Building it costs 0.007 ms and issues no
  Redis command; the first `list()` costs 10.0 ms, a second `list()` of the same builder
  costs 8.6 ms and yields distinct objects, and re-iterating the materialized list costs
  0.0013 ms across three passes. This is what forced Decision 0 — see it for the full
  consequence.

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
- **Impact on plan (revision)**: the hoist's saving is real only once the rows are
  materialized. Measured: N passes over a bare `QueryBuilder` cost ~8–10 ms each; N passes
  over the materialized list cost ~0.0004 ms each. Without Decision 0 the hoist saves
  nothing and the plan's own "one fetch" test cannot tell.
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
3. **`list(AgentSession.query.filter(status__in=NON_TERMINAL_STATUSES))`** hydrates only
   non-terminal rows, once, into a plain list. One Redis round trip for the whole sweep;
   the `list()` is what makes that true (Decision 0).
4. **One pass per slug over that in-memory list** assigns each row to the lane containing
   its `working_dir`, building `{slug: (state, detail)}` — same normalization, same
   segment-prefix match, N cheap Python passes instead of N Redis queries.
5. **Each lane** reads its verdict from the map.
6. **Re-probe at the decision point** (`apply=True` only): a lane that survives all guards
   and is about to be handed to `cleanup_after_merge` gets a fresh single-slug
   `worktree_busy_probe` call first. Typically 0–3 lanes per sweep. On the dry-run path the
   sweep returns at `tools/disk_reclaim.py:437`–`:440` before `cleanup_after_merge` (`:443`)
   and deletes nothing, so it takes no re-probe.
7. **Output**: identical tri-state per lane.

Interactive callers (`remove_worktree` at `:1696` via `worktree_busy_check`) are untouched
except that their single scan is now index-narrowed.

## Architectural Impact

- **New dependencies**: none. `NON_TERMINAL_STATUSES` is already exported from
  `models/session_lifecycle.py`. The deferred-import block inside `_scan_worktree_sessions`
  (`agent/worktree_manager.py:490`–`:492`) imports only `AgentSession` and
  `TERMINAL_STATUSES` today, so Task 1 adds one name to an import block that already exists
  — a new import line, not a new dependency.
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
- PM check-ins: 0. The one check-in the draft reserved was to ratify the corrected premise
  (the issue's stated magnitude and its suggested `slug=` fix are both wrong). That is
  settled in Decision 7: ship at the corrected magnitude, on the growth argument rather
  than the present-day 14 ms/day.
- Review rounds: 2 (one critique round has already run; its findings are applied here)

Two functions change, one function is added, no schema moves. The bulk of this plan is
the investigation that says what **not** to build; the build itself is a couple of hours.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.9.0 | `python -c "import popoto; assert tuple(int(x) for x in popoto.__version__.split('.')[:2]) >= (1, 9), popoto.__version__"` | `status__in` set-union lookups, and the single-hydration fix from #2639 that every timing in this plan assumes |
| Reachable Redis | `python -c "from models.agent_session import AgentSession; assert isinstance(list(AgentSession.query.filter(status='pending')), list)"` | The scan under change queries it; a build that cannot reach Redis cannot validate the change. The `list(...)` is what makes the row able to fail — `filter()` alone issues no Redis command (Decision 0), so the unmaterialized form exits 0 against a dead server. Demonstrated during this revision: under `REDIS_URL=redis://127.0.0.1:6399/9` the materialized command exits **1** with `ConnectionError: Error 61 connecting to 127.0.0.1:6399`, and exits **0** against the live server |

## Solution

### Key Elements

- **`_fetch_live_sessions()`** (new, private, `agent/worktree_manager.py`): performs the
  one deferred import and the one indexed query, **materializes it with `list(...)`**, and
  returns `(rows, error_reason)` where `rows` is a plain `list`. It is the single place
  that decides what "live session" means at the Redis boundary, and the single place where
  Redis is actually touched. The `list()` is load-bearing, not stylistic — see Decision 0.
- **`_scan_worktree_sessions(..., *, sessions=None)`** (modified): when `sessions` is
  `None` it fetches; when given a list it matches against that list. The matcher — path
  normalization, segment-prefix containment, the terminal-status check, first-match-wins —
  is not touched and is not duplicated.
- **`worktree_busy_probe_many(repo_root, slugs)`** (new, public): fetches once into a
  materialized list, then calls the existing matcher per slug against that same list.
  Returns `{slug: (state, detail)}` with the same tri-state each single-slug probe would
  have produced. A fetch failure yields `("error", reason)` for **every** requested slug.
  It never raises: every Redis touch happens inside `_fetch_live_sessions`'s `try`, and
  the per-row matching keeps its own `try` (Decision 6).
- **`sweep_worktrees`** (modified): builds the batch map lazily on first need, reads each
  lane's verdict from it, and re-probes fresh, single-slug, immediately before handing a
  lane to `cleanup_after_merge`.

### Flow

`sweep_worktrees` iterates lanes → guards 1–4 unchanged (a `too_young` lane still costs
zero queries) → **first lane to reach guard 5** builds the batch map (one query) →
**every later lane** reads its verdict from the map → a lane that clears all five guards
plus `open_pr` and `unmerged` → **fresh single-slug probe** → `cleanup_after_merge`.

### Technical Approach

**Decision 0 — materialize the query with `list()`, inside the `try`.** This is the
decision the whole hoist rests on, and getting it wrong would have shipped a change that
looks correct, tests green, and saves nothing. `AgentSession.query.filter(...)` returns a
lazy `popoto.models.query.QueryBuilder`, not a list. Two consequences, both measured on
this checkout at `491a88624`:

- **`QueryBuilder.__iter__` re-executes the whole query on every pass.** Constructing the
  builder costs 0.007 ms; the first `list()` costs 10.0 ms; a second `list()` of the same
  builder costs another 8.6 ms and yields distinct objects. Re-iterating an already
  materialized list costs 0.0013 ms for three passes. So handing a bare builder to
  `worktree_busy_probe_many` and looping N slugs over it pays **N full Redis queries** —
  exactly the amplification this plan exists to delete, reintroduced one layer down.
- **`filter()` never touches Redis, so a `try` around it alone catches nothing.** The
  connection error surfaces during iteration. If iteration happens outside
  `_fetch_live_sessions`, the exception escapes `_scan_worktree_sessions` entirely (the
  per-row `try` inside the matching loop only wraps the body, not the `for` header's own
  `__next__`), reaches `sweep_worktrees` at `tools/disk_reclaim.py:415` where there is no
  `except`, and aborts the whole sweep with a traceback instead of producing
  `busy_check_error:`. The fail-closed posture would be replaced by a crash.

Therefore: `rows = list(AgentSession.query.filter(status__in=sorted(NON_TERMINAL_STATUSES)))`
**inside** the existing `try`, mirroring `models/agent_session.py:1389`
(`rows = list(cls.query.filter(status=status))`), which is the established shape in this
codebase for exactly this reason. The "one fetch per sweep" test must count
**materializations** — Redis round trips, or iterations of a list-wrapping spy — never
`filter()` calls, or it cannot detect this class of bug at all.

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

**Decision 4 — re-probe fresh before removal. Settled: the re-probe stays.** (This was
Open Question 2; the critique required it resolved before build, because the test
dispositions depend on whether the sweep ends with one busy-check call site or two. It
does end with two.)

The argument that settles it is that the hoist itself widens a window the current code
keeps narrow. Today the probe runs at guard 5 and only `merged_via_tree` — one `git`
call — separates it from `cleanup_after_merge` for that lane. After the hoist, the
authorizing read is taken once for the whole sweep, and everything the sweep then does for
every remaining lane (`_tree_stats`, `git status`, `merged_via_tree`) sits inside the
window. Dropping the re-probe would therefore trade a real safety property for query cost,
which inverts the priority a fail-closed reaper is built on.

`remove_worktree` does call `worktree_busy_check`, but that wrapper is fail-**open**: it
catches a session that started mid-sweep only while Redis is healthy, and reads a Redis
outage as clear. So without the re-probe the sequence "lane reads clear at guard 5 → a
session starts in it → Redis goes down → lane is deleted under the session" has no guard
that stops it, and that is precisely the macOS cwd-vanished wedge #1246/#1357 exist to
prevent.

Cost: one query for each lane actually being removed — 0 to 3 in the measured run —
against the N it removes from the filtering pass. The re-probe also lands *later* in the
guard chain than today's probe does (after `merged_via_tree` rather than before), so the
authorizing read moves closer to the deletion than it is on `main`. Success Criterion 3 and
the Verification table are worded to this two-call-site shape, and the `all_clear` fixture
at `tests/unit/test_disk_reclaim.py:66` stubs both functions.

**The re-probe fires on the `apply=True` path only, and the invariant is stated that way
everywhere.** The dry-run branch at `tools/disk_reclaim.py:437`–`:440` appends the lane to
`sweep.removed` and `continue`s *before* the `cleanup_after_merge` call at `:443` —
`tests/unit/test_disk_reclaim.py:147`–`:150` (`test_dry_run_never_calls_cleanup`) already
pins that on `main`. Dry run deletes nothing, so it opens no TOCTOU window and there is
nothing for a fresh read to authorize; adding a probe there would buy a query and no safety.
The consequence is that `probe count == len(sweep.removed)` holds **under `apply=True`** and
is false by construction under `apply=False`, where the count is 0 against a non-empty
`sweep.removed`. Every statement of the invariant below carries that qualifier, the probe-count
test is built off `test_removes_lane_when_every_guard_clears`
(`tests/unit/test_disk_reclaim.py:138`, `apply=True`) and never off the dry-run test, and
Task 6's close-out re-measurement — which is a dry run — reports the batch materialization
count and says plainly that the re-probe count is not observable there.

**Decision 5 — lazy, not eager.** Building the map at sweep start would make an all-
`too_young` sweep pay one query where it currently pays zero. Building it on first need
preserves that zero and still collapses everything above it to one.

**Decision 6 — `worktree_busy_probe_many` never raises.** `sweep_worktrees` has no `except`
around guard 5 (`tools/disk_reclaim.py:415`) and this plan does not add one, because the
right place for the guarantee is the callee: every Redis touch lives inside
`_fetch_live_sessions`'s `try` (Decision 0), and the per-row matching keeps the `try` it
has today. An exception that escaped the batch probe would abort the entire sweep rather
than skip one lane, so "returns `("error", …)` for every slug" is a contract, stated in the
docstring and pinned by a test that makes the fetch raise and asserts the sweep completes
with every lane skipped as `busy_check_error:`.

**Decision 7 — ship at the corrected magnitude.** (This was Open Question 1.) The
Freshness Check measured 2 probes per sweep on this checkout, not the 73 the issue
describes, and a present-day cost near 14 ms/day. That is not on its own worth an
engineering pass. What settles it in favour of shipping is the shape of the growth: a lane
that reaches guard 5 and is then skipped as `unmerged` is never removed, so it reaches the
probe again every night, forever. The multiplier is the count of aged unmerged lanes, and
that set only accumulates. Fixing it now costs a Small appetite; the alternative is to
close #2712 as "measured, not worth it" and re-file it when the number is embarrassing.
The plan also carries a real correctness dividend independent of cost — Decision 0 records
a lazy-`QueryBuilder` trap that this codebase can hit anywhere `filter()` is passed around,
and the mutation work in Task 3 repairs five busy-guard tests that are one refactor away
from passing vacuously.

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

All citations below were re-derived at `491a88624`; the draft had the first two crossed
and the third pointed at a test that does not exist.

- [ ] Deferred model import fails → `("error", "model_import_failed:{Type}", "")`, WARNING
      logged. Covered by
      `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py::TestScanWorktreeSessions::test_model_import_failure_tuple`
      (`:210`) and `TestWorktreeBusyProbe::test_model_import_failure_reports_error_state`
      (`:141`); re-assert after the refactor, which splits this block: `AgentSession` and
      `NON_TERMINAL_STATUSES` move into `_fetch_live_sessions`, while `TERMINAL_STATUSES`
      stays imported unconditionally in `_scan_worktree_sessions` (Task 1) so the matcher's
      check keeps its name in scope on the injected `sessions=` path too. Both sites raise
      the same `model_import_failed:{Type}`, so the assertion text is unchanged; only the
      patch target moves.
- [ ] Indexed query raises **on materialization** → `("error", "query_failed:{Type}", "")`,
      WARNING logged. Covered by `TestScanWorktreeSessions::test_query_failure_tuple`
      (`:202`), `TestWorktreeBusyProbe::test_query_failure_reports_error_state` (`:133`),
      and `TestWorktreeBusyCheck::test_query_raises_returns_none` (`:108`). All three set
      `query.all.side_effect` today (`:203`, `:135`, `:110`) and must be re-pointed so the
      raise happens where the real one does — during iteration of the object
      `query.filter(...)` returns, not at the `filter()` call. A spy whose `__iter__`
      raises is the faithful shape; a `filter.side_effect` is not, and would leave
      Decision 0's failure mode untested.
- [ ] Per-row matching raises (a row with an unreadable attribute) → the row is skipped at
      DEBUG and the scan continues. **Not covered today.** The draft cited `:227`, which is
      inside `test_busy_tuple_carries_both_ids` (`:218`), a happy-path busy assertion; the
      file's only three `query.all.side_effect` uses (`:110`, `:135`, `:203`) are all
      whole-query failures. The `except Exception` / `logger.debug` / `continue` branch in
      the matching loop (`agent/worktree_manager.py:539`–`:541`) has zero coverage and
      Task 1 refactors that exact loop. **New test required:** put a row that raises on
      attribute access (e.g. `MagicMock(spec=["status"])`, which has no `working_dir`)
      ahead of a genuine busy row in the same fixture list, and assert the scan still
      returns `("busy", …)` from the later row — proving both the skip and first-match-wins
      survive the `sessions=` injection.
- [ ] **New:** batch fetch raises → `worktree_busy_probe_many` returns `("error", …)` for
      **every** requested slug, and the sweep skips every one of them as
      `busy_check_error:`. The batch path is the new way for a Redis outage to reach the
      reaper, and it is the one that must not default any lane to clear.
- [ ] **New (Decision 6):** `worktree_busy_probe_many` never propagates an exception. Make
      the fetch raise and assert the sweep **completes** with every lane skipped, rather
      than the call raising through `tools/disk_reclaim.py:415`, which has no `except`.

### Empty/Invalid Input Handling

- [ ] `worktree_busy_probe_many(repo_root, [])` returns `{}` and issues **no** query.
- [ ] A slug absent from the map (caller asks for a lane it did not request) must not
      KeyError into a silent clear — `sweep_worktrees` reads with an explicit default that
      is `("error", "not_probed")`, never `("clear", "")`.
- [ ] Rows with `working_dir` empty/None are skipped, as today
      (`agent/worktree_manager.py:510`). Rows with a relative `working_dir` (e.g.
      `".worktrees/sdlc-1218"`) still resolve against `repo_root`
      (`agent/worktree_manager.py:523`) — pinned by
      `test_worktree_manager_busy_guards.py::TestWorktreeBusyCheck::test_relative_working_dir_match`
      (`:95`), which is one of the `query.all` sites that must be re-pointed.
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
**The second-highest-risk item, and the one the draft got backwards.** The draft claimed
these tests "patch `models.agent_session.AgentSession` wholesale, so they survive the query
change" and that only the two error cases needed re-pointing. That is false in the
dangerous direction: an unconfigured `MagicMock().query.filter(status__in=[...])` is
iterable and yields an **empty** sequence (verified). So every test that configures only
`query.all.return_value` and asserts *clear* would pass **vacuously** after the swap,
reaching no matcher code at all. The busy- and error-asserting tests fail loudly, which is
exactly what makes the vacuous set easy to miss on an otherwise-red-then-green run.

- [ ] `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` — UPDATE **every**
      `query.all` site, all 17 of them, not just the error cases:
      `return_value` at `:42`, `:47`, `:58`, `:72`, `:86`, `:97`, `:115`, `:150`, `:163`,
      `:175`, `:185`, `:194`, `:219`, `:235`; `side_effect` at `:110`, `:135`, `:203`.
      **Completion check:** after the refactor,
      `grep -n 'query\.all' tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
      must return nothing.
- [ ] The five that would go vacuous, called out by name because they are the tests backing
      Success Criterion 2 ("the matcher is byte-for-byte the predicate it is today"):
      `TestWorktreeBusyCheck::test_terminal_session_does_not_block` (`:46`),
      `::test_substring_near_miss_does_not_block` (`:84`),
      `::test_session_with_no_working_dir_skipped` (`:114`),
      `TestWorktreeBusyProbe::test_unrelated_sessions_report_clear` (`:174`),
      `::test_terminal_session_in_lane_reports_clear` (`:183`).
      Prove they bite with the same mutation discipline Risk 1 prescribes: make the matcher
      return clear unconditionally and confirm all five turn **red**. A test that stays
      green under that mutation is reaching no code.
- [ ] `TestWorktreeBusyCheck` / `TestWorktreeBusyProbe` / `TestScanWorktreeSessions`
      (`:37`–`:236`) — the assertions themselves are the fail-open/fail-closed contract and
      must not change meaning by one character. Only the mock plumbing moves.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py:146` — no change:
      patches `worktree_busy_check` by object, unaffected.

**New tests** (not "impact", but required by the above):

- [ ] `worktree_busy_probe_many` agrees with N single-slug `worktree_busy_probe` calls over
      the same fixture rows, for clear / busy / error — the anti-drift test for Decision 3.
- [ ] **One rows object, two different verdicts.** Call
      `worktree_busy_probe_many(root, ["a", "b"])` against a single fixture list in which
      only `b` is busy, and assert `{"a": ("clear", ""), "b": ("busy", …)}`. This is the
      test that would have caught a shared-cursor bug, where the second slug sees a
      consumed or re-executed sequence rather than the same rows.
- [ ] **Exactly one materialization per sweep, counted correctly.** The counter goes on
      *iteration*, not on `filter()` — patch `query.filter` to return a spy that wraps a
      list and increments on `__iter__`, then assert the count is 1 across many lanes and
      **0** when every lane is `too_young` (Decision 5). Counting `filter()` calls would
      stay green against the exact defect Decision 0 exists to prevent, so this test is
      specified by the thing it counts, not by the function it patches.
- [ ] **Under `apply=True`**, the single-slug probe is called exactly `len(sweep.removed)`
      times (Decision 4), so the two-call-site shape is bounded rather than merely permitted.
      Build this by extending `test_removes_lane_when_every_guard_clears`
      (`tests/unit/test_disk_reclaim.py:138`, `apply=True`) with a `worktree_busy_probe` call
      counter. Never extend `test_dry_run_never_calls_cleanup` (`:147`, `apply=False`): that
      path returns at `:437`–`:440` before `cleanup_after_merge` (`:443`), so it would assert
      0 probe calls against 1 `sweep.removed` entry and fail the invariant as stated.
- [ ] **Under `apply=False`**, the single-slug probe is called **zero** times while
      `sweep.removed` is non-empty — the dry-run counterpart, asserted so the asymmetry is
      pinned rather than merely tolerated.
- [ ] A lane that reads clear in the snapshot and busy at the re-probe is **not** removed
      (Decision 4).

## Rabbit Holes

- **Indexing `working_dir`.** The predicate is segment-prefix containment, which a Redis
  exact-match index cannot answer; making it indexable means normalizing lane identity into
  a new field and backfilling it. That is a schema migration and an ongoing invariant to
  police, for a query measured at 7 ms. Not worth it at this appetite.
- **Making `slug` and `working_dir` agree everywhere.** The obvious "fix" for spike-1 is to
  pass `slug=parent_session.slug` at `tools/agent_session_scheduler.py:440` and then narrow
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

### Risk 1b: The batch fetch is passed around as a lazy `QueryBuilder`
**Impact:** Two failures at once, both silent. The sweep pays N full Redis queries instead
of one — the amplification the plan exists to remove, reintroduced inside the fix — and a
Redis outage raises during iteration rather than being caught, escaping
`_scan_worktree_sessions` and aborting the entire sweep at `tools/disk_reclaim.py:415`
(no `except` there) instead of skipping lanes as `busy_check_error:`. A "one fetch per
sweep" test that counts `filter()` calls stays green through both.
**Mitigation:** Decision 0 — `rows = list(...)` inside the existing `try`, so the object
crossing the function boundary is a plain list and the only Redis touch is inside a
handler. Pinned three ways: the one-materialization test counts iterations rather than
`filter()` calls; the one-rows-object/two-verdicts test would fail against a re-executing
cursor; and Decision 6's test asserts the sweep completes rather than raising when the
fetch fails.

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
anti-criterion in Verification resolves the second argument of every `.get()` call in
`tools/disk_reclaim.py` **by AST**, not by grep. A line-oriented grep is not sufficient
here: a black-wrapped default (`busy_map.get(\n    slug, ("clear", ""),\n)`) satisfies a
single-line pattern with count 0 while shipping exactly the silent-clear bug this risk
calls the most dangerous line the change could contain. The defeat is latent rather than
live today — `busy_map.get(slug, ("error", "not_probed"))` is short enough that black will
not wrap it — but the check must survive a future reformat, so it is written against the
parse tree. When demonstrating the row red, inject the **wrapped** form as well as the
single-line one.

### Risk 5: Merge conflict with #3167 in `agent/worktree_manager.py`
**Impact:** One other open lane edits the same module. #3167
(`docs/plans/auto-preserve-teardown-half-deleted-worktree.md`) touches **two** regions, not
one: `preserve_uncommitted_worktree_changes` (`:1508`) and `_cleanup_stale_worktree`
(`:903`, where it repairs a slug/branch mismatch in the same edit). `_cleanup_stale_worktree`
is the nearer neighbour of the two — 341 lines below `worktree_busy_probe` (`:562`) versus
946 for the preserve function — so it is the one worth checking, and the draft named only
the farther one.
**Mitigation:** Disjoint functions in both cases. This plan's touched range is `:457`–`:579`
(`_scan_worktree_sessions` through `worktree_busy_probe`, plus the new `_fetch_live_sessions`
and `worktree_busy_probe_many`). Neither `_cleanup_stale_worktree` nor
`preserve_uncommitted_worktree_changes` calls `_scan_worktree_sessions` or `worktree_busy_*`,
and neither is reachable from them, so a textual conflict is resolvable by inspection and
there is no semantic overlap to reason about.

**Not a risk: #3162.** It closed 2026-09-05T12:52:41Z and its commit `55ad9ac89` is an
ancestor of this plan's baseline `491a88624`, so its `reap_idle_worktree` (`:1007`) and its
+102 lines are already in the file every measurement here was taken against. It is a
baseline fact, not a concurrent editor. Recorded explicitly so a reviewer who has seen
#3162 named as a conflict elsewhere does not re-raise it.

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
exported from `models/session_lifecycle.py`; Task 1 adds it to the existing deferred-import
block, which today names only `AgentSession` and `TERMINAL_STATUSES`), no new config file,
no new environment variable, no migration.

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
- [ ] Docstring on `_fetch_live_sessions` stating that the `list(...)` is load-bearing:
      `filter()` returns a lazy `QueryBuilder` that re-queries on every iteration and issues
      no Redis command itself, so removing the `list()` would both reintroduce the per-lane
      amplification and move the Redis failure outside this function's `except` (Decision 0).
      Without that sentence the wrapper reads as noise and the next reader deletes it.
- [ ] Docstring on `worktree_busy_probe_many` covering the contract, the fan-out of a fetch
      error to every requested slug, the never-raises guarantee (Decision 6), and the fact
      that it shares one matcher with the single-slug path.
- [ ] A comment at the `sweep_worktrees` map read naming why the default is
      `("error", "not_probed")` and not `("clear", "")` (Risk 4).

## Success Criteria

- [ ] `_scan_worktree_sessions` issues an indexed `status__in` query; `AgentSession.query.all()`
      no longer appears in `agent/worktree_manager.py`.
- [ ] The query result is **materialized** — `_fetch_live_sessions` returns a `list`, and the
      `list(...)` sits inside the `try` that produces `query_failed:{Type}` (Decision 0).
- [ ] The `working_dir` segment-prefix matcher is byte-for-byte the predicate it is today;
      no `filter(slug=` appears in `agent/worktree_manager.py`.
- [ ] A sweep over N lanes performs **one batch session query, materialized once**, plus,
      **on the `apply=True` path**, **one fresh single-slug re-probe per lane actually
      removed** (Decision 4), and **zero of both** when every lane is filtered out above
      guard 5. Measured by counting iterations of the fetched rows, not `filter()` calls, and
      by asserting the single-slug probe call count equals `len(sweep.removed)` under
      `apply=True`. Under `apply=False` the expected re-probe count is **zero** regardless of
      `len(sweep.removed)`, because the dry-run branch returns at
      `tools/disk_reclaim.py:437`–`:440` before `cleanup_after_merge` and deletes nothing.
- [ ] The re-measured saving is recorded, not assumed: the one-materialization test's
      counter is the measurement, and Task 6 additionally reports the observed
      materialization count from a real `python -m tools.disk_reclaim --json` dry run. The
      plan's justification is a millisecond figure, so something must re-measure it after
      the change rather than only confirming the query's shape.
- [ ] `worktree_busy_check` returns `None` for both clear and error; `worktree_busy_probe`
      returns `clear`/`busy`/`error`; a batch fetch failure yields `error` for every
      requested slug.
- [ ] `worktree_busy_probe_many` and N single-slug probes agree on identical fixture rows
      across all three states.
- [ ] The three stale-monkeypatch tests in `tests/unit/test_disk_reclaim.py` exercise the
      new guard path, proven by mutation: forcing the batch probe to return clear makes
      `test_busy_check_error_also_blocks_removal` fail.
- [ ] `grep -n 'query\.all' tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
      returns nothing, and the five predicate tests named in Test Impact turn red under a
      matcher-returns-clear mutation rather than passing vacuously against an empty
      `MagicMock` sequence.
- [ ] The per-row matching `except` branch has a test — it had none before this change, and
      Task 1 refactors the loop it lives in.
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
- Add `_fetch_live_sessions()` returning `(rows, error_reason)` where `rows` is a **`list`**:
  the deferred import of `AgentSession` / `NON_TERMINAL_STATUSES` (the two names the fetch
  itself needs), then, **inside the `try`**,
  `rows = list(AgentSession.query.filter(status__in=sorted(NON_TERMINAL_STATUSES)))`.
  The `list()` is mandatory and inside the `try` for both reasons in Decision 0: `filter()`
  returns a lazy `QueryBuilder` that re-queries on every iteration, and it issues no Redis
  command itself, so a `try` wrapped around `filter()` alone catches nothing. Mirror
  `models/agent_session.py:1389`. Import failure → `model_import_failed:{Type}`; query
  failure → `query_failed:{Type}`. Keep the existing WARNING logs verbatim.
  `_fetch_live_sessions` owning the `NON_TERMINAL_STATUSES` import does **not** move
  `TERMINAL_STATUSES` out of `_scan_worktree_sessions`: an import inside
  `_fetch_live_sessions` binds a name in that frame only, so Decision 2's surviving Python
  check needs its own binding (see the next bullet). Do not widen `_fetch_live_sessions`'s
  `(rows, error_reason)` return shape to carry the constant back; that shape is pinned by
  the Solution's Key Elements and Success Criterion 2.
- Give `_scan_worktree_sessions` a keyword-only `sessions=None`; when `None`, call
  `_fetch_live_sessions()` and return `("error", reason, "")` on a reason. Leave the
  matching loop, including `if status in TERMINAL_STATUSES: continue` and its per-row
  `except Exception` / `logger.debug` / `continue`, untouched.
  Keep `from models.session_lifecycle import TERMINAL_STATUSES` in `_scan_worktree_sessions`
  itself, **unconditionally**: outside the `if sessions is None:` branch, in the same
  `try` that yields `model_import_failed:{Type}` today (`agent/worktree_manager.py:490`–`:492`).
  The injected `sessions=` batch path never calls `_fetch_live_sessions`, so a name bound
  only there would be unbound at `:513` on that path; the resulting `NameError` is swallowed
  per row by the `except` at `:539`–`:541` and the scan returns `("clear", "", "")` at `:543`,
  a silent fail-**open** inversion of the fail-closed probe. The duplicate import is the
  local answer and it costs nothing.
- Add `worktree_busy_probe_many(repo_root, slugs) -> dict[str, tuple[str, str]]`: fetch
  once into the materialized list; on error return `("error", reason)` for every slug;
  otherwise call `_scan_worktree_sessions(repo_root, s, sessions=rows)` per slug against
  that same list and collapse to the same tri-state `worktree_busy_probe` produces. Return
  `{}` for an empty `slugs` without querying. It must never raise (Decision 6).
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
  from a map built lazily on first read. Build it over every directory child of
  `.worktrees/` except the `PROTECTED_WORKTREE_SLUGS` entries — a superset of the lanes that
  will actually reach guard 5, which is correct and costs nothing extra because the fetch is
  one query regardless of how many slugs are classified against it.
- Read it as `busy_map.get(slug, ("error", "not_probed"))`. Never default to clear.
- On the `apply=True` path only, immediately before the `cleanup_after_merge` call at
  `tools/disk_reclaim.py:443`, call the single-slug `worktree_busy_probe` once more and skip
  on `busy`/`error` using the existing `live_session:` / `busy_check_error:` reason strings.
  The `apply=False` early return at `:437`–`:440` is unchanged and calls no probe: it deletes
  nothing, so it has no TOCTOU window to close. Placing the re-probe above that branch would
  charge the daily dry run a query per candidate lane for no safety.
- Import `worktree_busy_probe_many` alongside the existing deferred imports at `:358`–`:364`,
  keeping `worktree_busy_probe` (Decision 4 keeps the pre-removal call site).
- Add no `try`/`except` around guard 5. The no-raise guarantee belongs in
  `worktree_busy_probe_many` (Decision 6); an `except` here would mask a contract breach
  instead of failing it loudly in tests.
- Change no guard ordering, no skip-reason string, and no JSON output shape.

### 3. Re-point the tests, then prove they bite
- **Task ID**: build-tests
- **Depends On**: build-scan, build-sweep
- **Validates**: `tests/unit/test_disk_reclaim.py`,
  `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Apply every disposition in Test Impact, including re-pointing **all 17** `query.all`
  sites in `test_worktree_manager_busy_guards.py`. Completion check:
  `grep -c 'query\.all' tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`
  returns 0.
- Re-point the three whole-query failure mocks (`:110`, `:135`, `:203`) so the raise happens
  on **iteration**, not on the `filter()` call — a spy whose `__iter__` raises. A
  `filter.side_effect` would leave Decision 0's real failure mode untested.
- Add: batch-vs-single agreement across clear/busy/error; one rows object serving two slugs
  with different verdicts (`["a", "b"]`, busy only in `b`); **exactly one materialization**
  per sweep, counted by iterations of a list-wrapping spy rather than by `filter()` calls;
  zero materializations when every lane is `too_young`; single-slug probe call count equals
  `len(sweep.removed)` under `apply=True` (extend `test_removes_lane_when_every_guard_clears`,
  `tests/unit/test_disk_reclaim.py:138`) and equals zero under `apply=False`;
  snapshot-clear + re-probe-busy is not removed; a fetch that raises
  leaves the sweep completing with every lane `busy_check_error:` rather than propagating;
  a row that raises on attribute access is skipped and a later busy row still wins; an
  unknown status value reads `busy`; `worktree_busy_probe_many(root, [])` returns `{}` and
  queries nothing.
- **Mutation-check each guard and re-measure after each change** — three mutations, each
  re-measured on its own:
  1. Force `worktree_busy_probe_many` to return clear unconditionally → both
     `test_busy_check_error_also_blocks_removal` and `test_skips_lane_with_live_session`
     must **fail**.
  2. Make the matcher return clear unconditionally → all five predicate tests named in Test
     Impact must **fail**. Any that stays green is reaching no code.
  3. Delete the `list(` wrapper in `_fetch_live_sessions` → the one-materialization test
     must **fail** with a count of N rather than 1.
  Paste each red output into the PR.

### 4. Posture validation
- **Task ID**: validate-postures
- **Depends On**: build-tests
- **Assigned To**: posture-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff the matching loop against `main` and confirm the predicate is unchanged.
- Confirm `worktree_busy_check` still collapses error to `None` and `worktree_busy_probe`
  still returns three states.
- Confirm no `filter(slug=`, no `query.all()`, and no clear-valued default on the map read —
  the last one via the AST row, and demonstrated red against the black-wrapped form.
- Confirm `_fetch_live_sessions` returns a materialized `list` and that the `list(...)` sits
  inside the `try` (Decision 0), and that no `QueryBuilder` crosses a function boundary.
- Confirm the three mutation runs each reported red for the guards they target.

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
- **Re-measure, do not assume.** Run `python -m tools.disk_reclaim --json` as a dry run and
  report the observed batch materialization count and the number of lanes that reached guard
  5, alongside the pre-change figures recorded in spike-3 (7.0 ms per `query.all()`, 2 of 11
  lanes reaching the probe). The plan's justification is a measured number, so the close-out
  carries a measured number.
- **State what this run cannot show.** `--json` without `DISK_RECLAIM_APPLY`
  (`tools/disk_reclaim.py:50`) is a dry run, and the re-probe is `apply=True`-only, so the
  observed re-probe count here is zero by construction and is not evidence about Decision 4.
  The `apply=True` invariant is evidenced by the probe-count test in Task 3, not by this run.
  Say so in the close-out rather than reporting the zero as a result.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Busy-guard tests pass | `./scripts/pytest-clean.sh tests/unit/worktree_manager/test_worktree_manager_busy_guards.py -q` | exit code 0 |
| Disk-reclaim tests pass | `./scripts/pytest-clean.sh tests/unit/test_disk_reclaim.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/worktree_manager.py tools/disk_reclaim.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/worktree_manager.py tools/disk_reclaim.py` | exit code 0 |
| Indexed query present | `grep -c 'status__in' agent/worktree_manager.py` | output > 0 |
| Query is materialized (Decision 0) | `grep -cE 'list\(\s*AgentSession\.query\.filter' agent/worktree_manager.py` | output > 0 |
| Batch probe wired into the sweep | `grep -c 'worktree_busy_probe_many' tools/disk_reclaim.py` | output > 0 |
| Full scan gone (anti-criterion) | `grep -c 'query\.all()' agent/worktree_manager.py` | match count == 0 |
| Slug narrowing rejected (anti-criterion, Decision 1) | `grep -c 'filter(slug=' agent/worktree_manager.py` | match count == 0 |
| No lazy builder crosses a function boundary (anti-criterion, Decision 0) | `python -c "import ast,sys; t=ast.parse(open('agent/worktree_manager.py').read()); f=[n for n in ast.walk(t) if isinstance(n,ast.FunctionDef) and n.name=='_fetch_live_sessions'][0]; print('OK' if any(isinstance(n,ast.Call) and getattr(n.func,'id','')=='list' for n in ast.walk(f)) else 'BAD')"` | output `OK` |
| No clear-valued default on the map read (anti-criterion, Risk 4; AST so a black-wrapped default cannot slip past) | `python -c "import ast; t=ast.parse(open('tools/disk_reclaim.py').read()); bad=[n.lineno for n in ast.walk(t) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='get' and len(n.args)>=2 and isinstance(n.args[1],ast.Tuple) and n.args[1].elts and isinstance(n.args[1].elts[0],ast.Constant) and n.args[1].elts[0].value=='clear']; print('BAD' if bad else 'OK', bad)"` | output `OK []` |
| Terminal-status check survives (anti-criterion, Decision 2) | `grep -c 'in TERMINAL_STATUSES' agent/worktree_manager.py` | output > 0 |
| Fail-open wrapper intact | `grep -c 'def worktree_busy_check' agent/worktree_manager.py` | output > 0 |
| Fail-closed wrapper intact | `grep -c 'def worktree_busy_probe' agent/worktree_manager.py` | output > 0 |
| Guard order unchanged in the sweep | `sed -n '/^def sweep_worktrees/,/^def [a-z_]/p' tools/disk_reclaim.py \| grep -oE '"protected"\|"too_young"\|"uncommitted_changes"\|live_process:\|busy_check_error:\|live_session:\|"open_pr"\|"unmerged"' \| paste -sd, -` | exactly `"protected","too_young","uncommitted_changes",live_process:,busy_check_error:,live_session:,"open_pr","unmerged"` |
| No stale `query.all` mocks left behind (Test Impact completion check) | `grep -c 'query\.all' tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` | match count == 0 |
| No stale xfails in scope | `grep -rn 'xfail' tests/unit/test_disk_reclaim.py tests/unit/worktree_manager/` | exit code 1 |

Every anti-criterion row above must be demonstrated red before being trusted: introduce the
forbidden pattern deliberately, confirm the row FAILS, revert, and paste the FAIL output
into the PR description. Two of them need a specific injection rather than an obvious one:

- **Risk 4's clear-valued default** must be injected in **both** the single-line form and
  the black-wrapped multi-line form (`busy_map.get(\n    slug, ("clear", ""),\n)`). The
  wrapped form is the one a line-oriented grep misses; if the AST row lets it through, the
  row is not doing its job.
- **Decision 0's materialization** must be injected by deleting the `list(` wrapper, not by
  removing the query. The point is that the code still runs, still returns rows, and still
  passes every other row in this table while re-querying Redis once per slug.

## Critique Results

War room, FULL depth (Risk & Robustness, Scope & Value, History & Consistency) plus automated
structural checks. **Round 4** (re-critique after the round-3 concern-closing revision `6c667b9ed`),
run 2026-09-05 against `origin/main` at `6c667b9edb87844364c9e20975f789ab77a96e87`.
Mode: independent roster (3 critics). Verdict: **READY TO BUILD (no concerns)**
(0 blockers, 0 concerns, 1 nit).

The single round-3 concern is closed, and the closure was verified in the plan text and against
the current file rather than taken from the revision's own report. Task 1's first bullet now scopes
`_fetch_live_sessions`'s deferred import to `AgentSession` / `NON_TERMINAL_STATUSES` — "the two names
the fetch itself needs" — and adds the paragraph forbidding the wrong fix (widening the pinned
`(rows, error_reason)` return shape to carry the constant back). Task 1's second bullet keeps
`from models.session_lifecycle import TERMINAL_STATUSES` in `_scan_worktree_sessions` itself,
**unconditionally**, outside the `if sessions is None:` branch and inside the same `try` that yields
`model_import_failed:{Type}`, with the fail-open-inversion reason spelled out. Both bullets were
traced against the live file: the import `try` sits at `agent/worktree_manager.py:490`–`:492`, the
`status in TERMINAL_STATUSES` reference at `:513` inside the per-row `try` opened at `:508`, whose
`except Exception` / `logger.debug` / `continue` sits at `:539`–`:541` ahead of the
`return ("clear", "", "")` at `:543` — so the swallowed-`NameError` fail-open inversion the concern
described is genuinely prevented on both the `sessions=None` and the injected `sessions=` paths.
The Failure Path Test Strategy model-import bullet now states the same split, both sites raising the
same `model_import_failed:{Type}` with the assertion text unchanged and only the patch target moving.
The revision (23 insertions, 4 deletions, plan doc only) introduced nothing new that is wrong: it adds
no component, no abstraction, and no responsibility, and changes no skip-reason string, return shape,
or failure posture.

Structural checks pass on every leg. All four repo-mandated sections are substantive; tasks 1-6 have no
numbering gaps and no invalid or circular `Depends On` references; every cited path exists; both
prerequisites pass live (popoto 1.9.0 installed, the materialized Redis probe exits 0); the 17
`query.all` sites in `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` match the plan's
enumerated line numbers exactly, as do the `all_clear` fixture at `tests/unit/test_disk_reclaim.py:66`,
`test_removes_lane_when_every_guard_clears` at `:138`, and `test_dry_run_never_calls_cleanup` at
`:147`-`:150`; the `xfail` anti-criterion holds (grep exit 1); and no No-Go or Rabbit Hole appears as
planned work. Every external citation spot-checked this round holds at the current head:
`models/session_lifecycle.py:65`/`:72`/`:109`, `models/agent_session.py:160`/`:1389`/`:1931`,
`tools/agent_session_scheduler.py:434`-`:435`, `tools/valor_session.py:753`,
`agent/session_executor.py:1314`, `agent/worktree_manager.py:1007`/`:1696`, `tools/disk_reclaim.py:50`,
`:415`, `:437`-`:440`, `:443`, and `pyproject.toml:21`.

One nit remains, converged on by two critics and noted-and-declined by the third. It is residue from the
closure rather than a defect in it: two rationale passages describe the pre-split shape of an import the
task list now splits. It changes no build instruction and no conclusion, so it does not gate the build.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| NIT | History & Consistency, Risk & Robustness (Scope & Value noted it and declined to raise it) | Architectural Impact's "New dependencies" bullet (plan `:334`-`:338`) and the Update System opening (`:830`-`:834`) still describe Task 1 as adding one name to the single existing deferred-import block inside `_scan_worktree_sessions`. Task 1 now splits that block instead: `AgentSession` and `NON_TERMINAL_STATUSES` move into a new `try` in `_fetch_live_sessions`, while `_scan_worktree_sessions`'s block at `agent/worktree_manager.py:490`-`:492` keeps `TERMINAL_STATUSES` alone. Both passages are rationale prose, not the "## Step by Step Tasks" a builder executes, and the "no new dependency" conclusion each draws holds either way, so nothing shipped can be wrong because of them. | n/a — nit, not blocking | Optional wording fix if either passage is edited for another reason: say the block splits across two functions, mirroring the Failure Path Test Strategy's phrasing, rather than that a name is added to the existing block. |
