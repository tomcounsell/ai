---
title: Remove Job UTC-reattach and 1.8.0-era naive-tzinfo guards
slug: remove-popoto-1-8-0-naive-datetime-scar-tissue
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3173
last_comment_id:
---

# Remove Job UTC-reattach and 1.8.0-era naive-tzinfo scar tissue

## Problem

`models/job.py` still carries three pieces of machinery built for popoto 1.8.0, plus the docstrings
that justify them:

1. A `Job.save()` override (lines 144-188) that re-attaches UTC to a naive `last_active_at` before
   delegating to `Model.save`.
2. A `Job.renormalize_last_active_scores()` call inside `Job.repair_indexes()` (lines 769-783) that
   sweeps every recency score after every `rebuild_indexes()`.
3. Docstrings at `models/job.py:155`, `:172`, `:533` that describe popoto 1.8.0's decoder in the
   present tense, plus the same story in `docs/features/durability-model.md` (lines 79-108),
   `docs/features/popoto-index-hygiene.md` (line 40), `docs/features/utc-timestamps.md` (lines 87, 94),
   and `scripts/update/migrations.py` (lines 1115-1141).

Commit `8c1a36ad1` raised the floor to `popoto>=1.9.0`. It updated one docstring and one assertion in
`tests/unit/test_job_model.py` and left source and docs untouched. A reader of `Job.save()` is told
"popoto 1.8.0 decodes a stored datetime without tzinfo", which is false on the installed version, and
`repair_indexes()` pays a full-keyspace sweep on every worker start to correct a skew the rebuild no
longer produces.

`agent/session_health.py` and `agent/session_pickup.py` carry inline naive-datetime coercions over
`AgentSession` fields that now always arrive aware.

**Desired outcome.** The Job model, its docs, and the session-health and pickup helpers describe and
rely on popoto 1.9.0's actual contract. Code that existed only to compensate for naive decoding is
gone, along with the tests that pinned it. Every naive-to-UTC coercion that survives lives in exactly
one named helper per module, which is the convention `docs/features/utc-timestamps.md` already
declares.

## Freshness Check

**Disposition: Unchanged.** Baseline commit: `67d714662` (main, 2026-09-05). Issue filed
2026-09-05T09:29:28Z; plan written ~3h later.

| Re-verified | Result |
|---|---|
| `git log --since=<issue createdAt>` over `models/job.py`, `tests/unit/test_job_model.py`, `docs/features/durability-model.md`, `docs/features/utc-timestamps.md`, `agent/session_health.py`, `agent/session_pickup.py` | Zero commits. Only two commits landed on main since filing (`503fb536e`, `67d714662`), both `docs/plans/` for lane `rtr-unconditional-2733`. |
| `models/job.py:155`, `:172`, `:533` (cited 1.8.0-as-present docstrings) | All three exact, no line drift. |
| `docs/features/utc-timestamps.md:87`, `:94` | Both exact. |
| `docs/features/durability-model.md` lines 82-106 (cited) | Passage is at **79-108**, one heading earlier than cited. Same content. Corrected in Technical Approach. |
| `pyproject.toml` popoto pin | `popoto>=1.9.0` (line 21). `.venv` reports 1.9.0. `POPOTO_DATETIME_KEY_LEGACY` unset repo-wide. |
| #2833 (PR) | MERGED 2026-08-18. Introduced the override; unchanged since. |
| #2848 (issue) | CLOSED 2026-09-05T06:42:17Z, ~3h **before** this issue was filed. Its cursored/pipelined sweep is already on main and is what this plan keeps. |
| #2083 (issue) | CLOSED 2026-07-17. The sibling descriptor-pollution audit; same shape, no code overlap. |
| Active plans in `docs/plans/` | `expectation_blocked_state.md` (issue #2862, status Planning) references `models/job.py` 11 times, but only the expectation/goal surface — zero references to `save`, `repair_indexes`, `renormalize`, or `last_active_at`. No conflict. `rtr-unconditional-2733`, `ancestor-safe-service-pid-lookup`, `telegram-forum-topic-awareness`: no overlap. |

**Two premises corrected during the check** (both narrow the work rather than widen it) — see Spike Results:

- The issue's Solution Sketch says "every comment that says popoto strips or drops tzinfo is corrected"
  in `agent/session_health.py` / `agent/session_pickup.py`. **No such comment exists.** Acceptance
  criterion 5 already holds on main, vacuously. What remains in those two files is a pure code
  decision, not a docs correction.
- The Sketch names three tests to delete. There are **five** affected test bodies, not three — see
  Test Impact.

## Prior Art

| Ref | State | Relevance |
|---|---|---|
| [#2833](https://github.com/tomcounsell/ai/pull/2833) (closes #2636) | MERGED 2026-08-18 | Shipped the `Job.save()` UTC-reattach, the `repair_indexes` renormalize call, and the `backfill_job_last_active_scores` migration, against `popoto>=1.8.0`. This plan removes the first two and keeps the third. |
| [#2848](https://github.com/tomcounsell/ai/issues/2848) | CLOSED 2026-09-05 | Made `renormalize_last_active_scores` cursored and pipelined (SSCAN + two pipelines per chunk). That work stays; only the `repair_indexes` caller goes. |
| [#2083](https://github.com/tomcounsell/ai/issues/2083) | CLOSED 2026-07-17 | The sibling audit that removed popoto-1.8.0 descriptor-pollution scar tissue. Same shape, disjoint code. Its precedent: delete the compensation and the tests that pinned it, keep the property test. |
| [#777](https://github.com/tomcounsell/ai/issues/777) | CLOSED 2026-04-07 | The original ~7h-inflated-duration bug from a naive datetime in `_to_timestamp`. Motivated the inline guards. |
| [#1645](https://github.com/tomcounsell/ai/issues/1645) / [#1653](https://github.com/tomcounsell/ai/issues/1653) | CLOSED 2026-06 | The `auto_now` producer bug and its upstream fix (popoto 1.7.1, popoto#421). Established `utils.utc.to_unix_ts` as the single read-path coercer. |
| popoto#519, popoto#521 (1.8.2) | Released 2026-08-07 | Score became a pure function of the stored value; aware values round-trip aware. **This, not 1.9.0, is what killed the reattach** — see Spike Results. |
| popoto#537 (1.9.0) | Installed | Legacy offset-free rows decode as aware UTC. |

**No prior failed fix.** The override worked as designed for the version it targeted; the dependency
moved underneath it. This is a retirement, not a repair, so the template's *Why Previous Fixes Failed*
section is omitted.

## Research

The authoritative source here is the installed dependency itself, read directly, rather than the web:
popoto is a first-party library and its source in `.venv/lib/python3.14/site-packages/popoto/` is the
exact code this repo runs.

**Primary reads (installed popoto 1.9.0):**

- `popoto/fields/sorted_field_mixin.py:387-396` — `convert_to_numeric` for `field.type is datetime`:
  ```python
  if field_value.tzinfo is None:
      return field_value.replace(tzinfo=datetime.timezone.utc).timestamp()
  return field_value.timestamp()
  ```
  The comment cites popoto#519/#521 directly: "A score has to be a pure function of the stored value."
- `popoto/models/encoding.py:105-162` — `_decode_datetime`. A legacy offset-free string is stamped
  `tzinfo=utc` (popoto#537); anything else parses through `fromisoformat`. The docstring states the
  key point for us: "`SortedFieldMixin.convert_to_numeric` has treated a naive value as UTC when
  deriving a score since #519 ... A legacy row therefore scored identically before and after this
  change."
- `popoto/fields/constants.py:38-40` — `POPOTO_DATETIME_KEY_LEGACY` is the only kill switch that
  restores naive decoding of legacy rows. Confirmed unset anywhere in this repo.

**Web search** — query: *"removing defensive naive datetime tzinfo guards after library upgrade
Python risks Postel's law tolerance withdrawal"*. One finding is load-bearing for the
session-health decision below:

> Deleting the check outright is only safe if the upgraded library is genuinely your *sole* inbound
> source of datetimes — which is rarely true once other third-party packages, deserialization paths,
> and `strptime` calls are in play.
> — [Ten Python datetime pitfalls](https://dev.arie.bovenberg.net/blog/python-datetime-pitfalls/),
> [Python.org: How can I enforce aware datetime objects?](https://discuss.python.org/t/how-can-i-enforce-aware-datetime-objects/15004)

The complementary caution from the robustness-principle literature is that *silent* tolerance is the
expensive kind: a receiver that quietly coerces bad input removes the feedback that would have fixed
the producer ([Robustness principle](https://en.wikipedia.org/wiki/Robustness_principle),
[The Harmful Consequences of the Robustness Principle](https://lobste.rs/s/enszyj/harmful_consequences_robustness)).

**How this informs the approach.** It splits the session-health/pickup sites cleanly in two. A
coercer whose declared contract is wider than popoto (it also accepts `float` and ISO strings) keeps
its naive branch, because popoto is demonstrably not its sole inbound source. A bare inline
`x if x.tzinfo else x.replace(tzinfo=UTC)` sitting one line after an `isinstance(x, datetime)` check
on a value read straight off an `AgentSession` field has exactly one inbound source, and it is a
fourth copy of a coercion the module already names — which is the duplication
`docs/features/utc-timestamps.md` explicitly forbids for new code. Those go.

## Spike Results

Two spikes, both resolved during planning. Small appetite caps at two.

### spike-1: Is a `SortedField(type=datetime)` score a pure UTC epoch even for a *naive* in-memory value?

- **Assumption**: "The `Job.save()` reattach is load-bearing for score purity, so removing it is only
  safe once every writer is proven to assign an aware value."
- **Method**: code-read + in-memory prototype (no Redis, no test-DB claim needed).
- **Result**: **The assumption is false.** Ran `SortedFieldMixin.convert_to_numeric` directly under
  `TZ=Asia/Bangkok` (UTC+7) against the installed popoto 1.9.0:

  | input | score |
  |---|---|
  | `datetime(2026,9,5,5,0,0, tzinfo=UTC)` | `1788584400.0` |
  | same value with `tzinfo=None` | `1788584400.0` |
  | `naive.timestamp()` (what a skew would produce) | `1788559200.0` (7h off) |

  popoto normalizes naive → UTC *inside* the score function
  (`sorted_field_mixin.py:394-395`), and has done so since popoto#519 / 1.8.2. The score is a pure
  function of the instant whatever the tzinfo. The reattach cannot affect any score.
- **Confidence**: **high** — empirical, against the exact installed artifact, on a non-UTC TZ.
- **Impact if false**: would have forced a proof that all three `last_active_at` writers are aware
  before removal. Done anyway as a belt-and-braces read (see Data Flow): all three are `_now()`.

### spike-2: Do `agent/session_health.py` or `agent/session_pickup.py` contain comments attributing the naive guards to popoto?

- **Assumption**: "Those two files carry comments saying popoto strips/drops/omits tzinfo, which must
  be corrected" (issue Solution Sketch, acceptance criterion 5).
- **Method**: code-read — `grep -i` for popoto + strip/drop/omit/naive across both files, then a
  ±12-line `popoto` window around each of the nine `tzinfo` sites.
- **Result**: **Zero hits.** No guard in either file mentions popoto at all. Acceptance criterion 5 is
  already satisfied on main.
- **Confidence**: **high** — the probe is as wide as the claim (both files, every guard site, all four
  verbs plus a proximity window).
- **Impact if true (it was false)**: would have added a docs-correction pass to those two files. It
  does not; the remaining decision there is code-only, which is why it is stated explicitly below
  rather than assumed.

### The keep-or-remove decision (acceptance criterion 6)

**Decision: keep the coercion, delete the duplication — route the inline one-liners through the
named helper each module already has.** This is neither of the issue's two options verbatim; it is
option (b) with the one-liner sites rewritten rather than deleted, and it is chosen because deleting
them outright is a *silent* tolerance withdrawal whose only witnesses are tests.

| Site | Disposition | Why |
|---|---|---|
| `session_health.py:400-408` `_ts` | **Keep the naive branch** | Contract is wider than popoto: it accepts `datetime`, `int`, `float`. Named, single-purpose, documented. |
| `session_health.py:719-736` `_at_rest_coerce_ts` | **Keep** | Extends `_ts` with ISO-8601 **strings** from `session_events`, which popoto never produced. Sole inbound source is provably not popoto. |
| `session_pickup.py:38-58` `is_scheduled_eligible` | **Keep** | Handles `int | float` as well as `datetime`; docstring declares it shared with the check-in primitive's tests (#2139). |
| `session_pickup.py:382-389`, `:595-602` `_ensure_tz` (two copies) | **Keep** | Handles `None`, `int`, `float`, `datetime`. Same reasoning. |
| `session_health.py:6305-6313` `_session_is_alive` | **Keep** | Has an explicit `else: age = time.time() - float(hb)` float leg — heterogeneous by construction. |
| `session_health.py:658` `last_at_aware` | **Rewrite through `_ts`** | Bare inline copy; `_ts(last_at)` is already called eleven lines above in the same function. |
| `session_health.py:1614` `started_aware` | **Rewrite through `_ts`** | Bare inline copy behind an `isinstance(started_ref, datetime)` check. |
| `session_health.py:1750` `ts_aware` | **Rewrite through `_ts`** | Same. |
| `session_health.py:1770` `hb_aware` | **Rewrite through `_ts`** | Same. |
| `session_health.py:1805` `started_aware` | **Rewrite through `_ts`** | Same. |
| `session_health.py:1852` `_hb_own_aware` | **Rewrite through `_ts`** | Same. |

**Why not option (a), delete outright.** Two tests
(`test_check_tool_timeout_handles_naive_datetime`, `test_check_tool_timeout_naive_stale_pair_skipped`)
feed a naive datetime through a `SimpleNamespace` and assert UTC handling. Under option (a) they must
be deleted and `datetime.now(tz=UTC) - naive` raises `TypeError` at line 658 for any caller that ever
supplies a non-popoto row. The tolerance is real and cheap; only the *fourth copy of it* is scar
tissue. Routing through `_ts` deletes six duplications, keeps both tests green with no edit, and
lands the module on the convention `docs/features/utc-timestamps.md` already states: "New code must
import `to_unix_ts` rather than add a fourth copy."

**Why not `utils.utc.to_unix_ts` directly at these six sites.** `_ts` is `session_health.py`'s own
name for the same coercion and is already imported-free (module-local). Swapping six call sites to
the shared helper is a wider refactor than this chore, and the issue's Dropped bucket explicitly
defers the module-by-module consolidation. `_ts` stays; a follow-up may fold it into `to_unix_ts`.

## Data Flow

### Write path: how `last_active_at` reaches a Redis score

```
Job.mint()            models/job.py:221   last_active_at=_now()      ─┐
Job.touch()           models/job.py:500   self.last_active_at=_now() ─┼→ Model.save()
Job.mark_active()     models/job.py:521   self.last_active_at=_now() ─┘      │
                                                                             ▼
                                          SortedFieldMixin.convert_to_numeric
                                          (naive → UTC → .timestamp(); pure)
                                                                             │
                                                                             ▼
                                          ZADD $SortF:Job:last_active_at:<room>
```

`git grep -n "last_active_at\s*=" -- '*.py'` outside `tests/` returns exactly four hits: the field
declaration (`:129`), the reattach itself (`:181`), and the three writers above. `_now()` is
`datetime.now(tz=UTC)` (`models/job.py:78-79`). **No production writer can produce a naive value**,
and per spike-1 the score would be correct even if one did. The override is dead on both legs.

### Read path

```
Job.query.get()/filter() → popoto _decode_datetime  ──→ aware UTC datetime (1.9.0)
                             (encoding.py:105-162)          │
                                                            ▼
                              Job.recent_for_room() ZREVRANGE over the same scores
```

### The `repair_indexes` sweep

```
worker start → scripts/popoto_index_cleanup.run_cleanup()
                 └→ _run_guarded_repairs() → Job.repair_indexes()
                       ├─ leg 1: quarantine identity-less hashes
                       ├─ leg 2: clear $IndexF:Job:* keys
                       ├─ rebuild_indexes()   ← re-scores via field.on_save
                       ├─ renormalize_last_active_scores()   ← THIS CALL GOES
                       └─ backfill_open_expectations_index()
```

`rebuild_indexes()` re-scores each row through the same `field.on_save` → `convert_to_numeric` path
proven pure in spike-1. Whether the rebuilt instance decodes aware (1.9.0) or naive (kill switch on),
the score it writes is the UTC epoch. The sweep after it can only ever repair zero rows.

The **other** caller stays: `scripts/update/migrations.py:1162` calls
`Job.renormalize_last_active_scores()` from `_migrate_backfill_job_last_active_scores`, which is
registered in `MIGRATIONS` and recorded in `data/migrations_completed.json`. An already-applied
migration stays in the registry, so the classmethod and its `TestRenormalizeBatching` coverage stay.

## Architectural Impact

**Direction: coupling removed, no new coupling added.** The repo currently holds a private copy of a
timezone invariant that its dependency now guarantees. Three layers assert the same thing:
popoto's `convert_to_numeric`, `Job.save()`'s reattach, and `renormalize_last_active_scores()`'s
post-rebuild sweep. After this change, one layer asserts it — the one that owns the score.

**What gets cheaper.** `Job.repair_indexes()` runs at every worker start via
`scripts/popoto_index_cleanup.run_cleanup`. It currently pays an `SSCAN` walk of the whole Job class
set plus two pipelines per 500-row chunk to repair a skew that cannot occur. Job is immortal (no
`Meta.ttl`), so that cost grows with the lifetime population forever. Removing the call takes a
guaranteed-zero-repair sweep off the worker-start critical path.

**What gets riskier.** Nothing structurally, but the repo gives up a defence-in-depth layer it would
have to re-add if the popoto floor were ever lowered below 1.8.2. That is bounded by
`docs/features/popoto-version-floor-guard.md`, the existing fail-closed interlock that already
refuses to rebuild indexes under a below-floor popoto. The floor guard is the mechanism that makes
this removal safe to keep removed; the plan adds no new guard because one already exists.

**`session_health.py` altitude.** Six inline coercions collapse into calls to the module's own named
helper. The module keeps one place where "a naive timestamp means UTC" is written down, which is what
`docs/features/utc-timestamps.md` has asked for since #777.

## Appetite

**Small.** One work session.

The change is deletion plus docstring rewriting, with one mechanical rewrite repeated six times. No
new behavior, no new dependency, no migration, no schema change. The surface is nine files:

| File | Shape of change |
|---|---|
| `models/job.py` | Delete ~45 lines (`save()` override) + 8 lines (`repair_indexes` sweep call and its comment); rewrite 3 docstring passages |
| `agent/session_health.py` | 6 two-line rewrites |
| `tests/unit/test_job_model.py` | Delete 2 tests, rewrite 1 docstring, trim 1 test's second half |
| `scripts/update/migrations.py` | Rewrite 1 docstring |
| `docs/features/durability-model.md` | Rewrite 1 subsection |
| `docs/features/popoto-index-hygiene.md` | Rewrite 1 paragraph |
| `docs/features/utc-timestamps.md` | Rewrite 2 lines |

What would push this to Medium: touching the seven other modules that carry the same guard
(`agent/session_runner/liveness.py`, `agent/agent_session_queue.py`, `monitoring/session_watchdog.py`,
`monitoring/bridge_watchdog.py`, `reflections/crash_recovery.py`, `models/agent_session.py`,
`utils/utc.py`). Explicitly out of scope — see No-Gos.

## Prerequisites

None. Everything this plan depends on is already on main:

- [x] `popoto>=1.9.0` pinned (`pyproject.toml:21`) and installed (`.venv` reports 1.9.0).
- [x] `POPOTO_DATETIME_KEY_LEGACY` unset repo-wide (confirmed by `git grep`).
- [x] `docs/features/popoto-version-floor-guard.md`'s fail-closed interlock is shipped, so a
      below-floor popoto refuses to rebuild rather than silently re-skewing.
- [x] `backfill_job_last_active_scores` is recorded in `MIGRATIONS` and has run fleet-wide.

## Solution

### Key Elements

1. **Delete `Job.save()`** — `models/job.py:144-188`. `Job` inherits `Model.save` unchanged.
2. **Delete the post-rebuild sweep call** — `models/job.py:769-783`, the
   `cls.renormalize_last_active_scores()` call, its four-line comment, and the `if renormalized:`
   log block. `repair_indexes()` keeps its `(quarantined, rebuilt)` return arity.
3. **Keep `renormalize_last_active_scores()` itself** — the `backfill_job_last_active_scores`
   migration still calls it, and an applied migration stays in the registry.
4. **Correct every 1.8.0-as-present citation** — three docstrings in `models/job.py`, one in
   `scripts/update/migrations.py`, three feature docs.
5. **Collapse six inline coercions in `agent/session_health.py` into `_ts` calls** — keep the naive
   branch inside `_ts`, `_at_rest_coerce_ts`, `_session_is_alive`, and both `session_pickup.py`
   coercers.
6. **Remove the tests that pinned the deleted code**, keep `TestScorePurity` as the regression net.

### Flow

Nothing changes at runtime for a caller. The observable deltas are:

- `Job.repair_indexes()` no longer emits `[job] re-normalized N of M recency score(s) after the index
  rebuild` and no longer walks the class set. Worker start does less Redis work.
- `Job.save()` no longer mutates `self.last_active_at` as a side effect. A caller that assigns a naive
  value and then reads the attribute back now sees the naive value it assigned. No production caller
  does this; only the deleted tests did.

### Technical Approach

**`models/job.py`**

- Remove the whole `def save(...)` block at lines **144-188** (signature, docstring, the
  `if update_fields is None or "last_active_at" in update_fields:` guard, and the `super().save(...)`
  delegation). Verify `datetime` and `UTC` are still used elsewhere in the module before touching the
  imports at line 53 — `_now()` at line 78 uses both, so both stay.
- In `repair_indexes()`, delete lines **770-783**: the comment beginning `# rebuild_indexes()
  re-scores every row via field.on_save on`, the `scanned, renormalized = ...` assignment, and the
  `if renormalized:` logging block. Leave `rebuilt = cls.rebuild_indexes()` and the
  `cls.backfill_open_expectations_index()` call that follows.
- Rewrite three docstring passages:
  - **`:533`** (`recent_for_room`): `popoto 1.8.0's QueryBuilder has no early-limit path` → state the
    version-independent fact. `QueryBuilder` still has no early-limit path for a `SortedField` on
    1.9.0; only the version reference is stale. Reword to `popoto's QueryBuilder has no early-limit
    path for a SortedField`.
  - **`:807-815`** (`renormalize_last_active_scores`): the "two callers" list drops the
    `repair_indexes` bullet entirely and the migration bullet loses "before the `save` UTC-reattach
    override shipped". State what the sweep is now: the one-shot repair of skew written before popoto
    1.8.2 made the score a pure function of the stored value.
  - **`:833`** (same docstring): `a scoped save that names the field so the save tz-reattach fires` →
    the field-scoped write is still the clobber-proof idiom; the reason is now that it cannot touch
    `goal`/`status`, not that it triggers a reattach.
- Grep the module for surviving `1.8.0` / `reattach` strings after the edits.

**`agent/session_health.py`** — six sites, one pattern. Each currently reads:

```python
X_aware = X if X.tzinfo else X.replace(tzinfo=UTC)
age = (NOW - X_aware).total_seconds()
```

and becomes:

```python
age = NOW.timestamp() - _ts(X)
```

`_ts` returns a float for any `datetime` (its naive branch is kept), and every one of the six sites is
already behind an `isinstance(X, datetime)` check, so `_ts` cannot return `None` there. Sites, by
current line number (**re-derive by symbol before editing — this file is ~6500 lines and line numbers
drift**):

| Line | Function | Local name |
|---|---|---|
| 658 | `_check_tool_timeout` | `last_at_aware` |
| 1614 | `_never_started_past_grace` | `started_aware` |
| 1750 | `_has_progress` sub-check A | `ts_aware` |
| 1770 | `_has_progress` sub-check B | `hb_aware` |
| 1805 | `_has_progress` startup-grace leg | `started_aware` |
| 1852 | `_has_progress` own-progress leg | `_hb_own_aware` |

At line 658 `_ts(last_at)` is already computed eleven lines earlier for the epoch gate; reuse that
value rather than calling `_ts` twice.

**`agent/session_pickup.py`** — no code change. Spike-2 found no popoto-attributing comment, and all
three coercers there (`is_scheduled_eligible`, both `_ensure_tz` copies) keep their naive branch under
the Spike Results decision. **This file is listed in the issue's Solution Sketch and deliberately
ends up untouched; the plan records that as a decision, not an omission.**

**`scripts/update/migrations.py:1115-1141`** — rewrite `_migrate_backfill_job_last_active_scores`'s
docstring. It currently opens "popoto 1.8.0 decodes stored datetimes without tzinfo, so before the
`Job.save()` UTC-reattach override shipped...". The migration's *purpose* is historical and stays
accurate as history; say so in the past tense and drop the claim that `repair_indexes` re-runs the
sweep after every rebuild. Do not touch the function body or its `MIGRATIONS` entry.

**Docs** — three files, all describing the same dead mechanism:

- `docs/features/durability-model.md` **lines 79-108** (heading `### last_active_at score purity
  (Job.save())`). *Note: the issue cites 82-106; the passage is at 79-108 on `67d714662`.* Rename the
  heading (it names a method that will not exist), replace the reattach paragraph with popoto's own
  contract, and delete the final paragraph's renormalize-after-rebuild rationale while keeping the
  migration's description.
- `docs/features/popoto-index-hygiene.md` **line 40** — the paragraph opening "`Job.repair_indexes()`
  carries one extra step the other guarded paths do not". After this change it carries no extra step.
  Delete the renormalize description from that paragraph. **This file is not named in the issue's
  Solution Sketch; it was found by grep during planning and would otherwise be left contradicting the
  code.**
- `docs/features/utc-timestamps.md` **line 87** ("`SortedField` / `DatetimeField` deserialization can
  return naive datetimes") and **line 94** ("ai pins `popoto>=1.8.0`"). Line 87's *instruction* —
  route read-path conversions through `utils.utc.to_unix_ts` — stays correct and is the convention
  this plan leans on; only its stated rationale changes. Line 94 becomes `popoto>=1.9.0`.

## Failure Path Test Strategy

This is a removal, so the failure paths that matter are the ones the removed code used to cover.
Each is either still covered by a survivor or explicitly conceded.

### Exception Handling Coverage

- **`renormalize_last_active_scores` fail-open contract** (broken `SSCAN` returns `(0, 0)` and logs a
  WARNING rather than raising). Still exercised — the migration is still a caller, and the first half
  of `test_renormalize_enumeration_failure_returns_zero_and_backfill_still_runs` tests the classmethod
  directly. Only the `repair_indexes`-reached-backfill half needs rework (see Test Impact).
- **`repair_indexes` reaches `backfill_open_expectations_index`** after the rebuild. Currently proved
  only as a side effect of the failing-renormalize test. After the sweep call is gone that proof is
  vacuous, so the assertion must be re-anchored to a real hazard: leg 1/leg 2 or the rebuild raising.
- **`_ts` receiving a non-datetime.** Unchanged; `_ts` keeps its `None` and `int | float` legs.

### Empty/Invalid Input Handling

- **Naive datetime into `_check_tool_timeout` and `_has_progress`.** The whole point of routing
  through `_ts` rather than deleting the one-liners: `test_check_tool_timeout_handles_naive_datetime`
  and `test_check_tool_timeout_naive_stale_pair_skipped` must pass **unedited** after the rewrite.
  If either needs a change, the rewrite was wrong. Treat that as the build's tripwire.
- **A Job whose partition member is absent / instant unreadable.** Skipped by the sweep; behavior
  unchanged, still covered by `TestRenormalizeBatching`.

### Error State Rendering

No user-facing surface changes. The only log line that disappears is
`[job] re-normalized %d of %d recency score(s) after the index rebuild`, which fired only when the
sweep repaired something — i.e. never, on popoto ≥ 1.8.2. Nothing greps for it
(`git grep "re-normalized"` before deleting, to confirm).

### Mutation check (required before the review round closes)

Every guard this plan touches is a deletion, so a green suite proves little on its own. For each
survivor below, mutate and confirm the suite goes red:

| Guard | Mutation | Expected failure |
|---|---|---|
| `_ts` naive branch | delete `val = val.replace(tzinfo=UTC)` | `test_check_tool_timeout_handles_naive_datetime` raises `TypeError` |
| `TestScorePurity` | make `_now()` return `datetime.now()` (naive local) | score assertions still pass (proves popoto, not us, owns purity) |
| `repair_indexes` backfill reach | delete `cls.backfill_open_expectations_index()` | the re-anchored test fails |

## Test Impact

**Five test bodies are affected, not the three the issue's Solution Sketch names.** The two extra were
found by reading `TestScorePurity` and `TestGuardedRepair` in full during planning.

- [ ] `tests/unit/test_job_model.py::TestScorePurity::test_reattach_preserves_the_instant_and_is_idempotent` (line 1019) — **DELETE**: tests only the override's instant-preserving/idempotent behavior.
- [ ] `tests/unit/test_job_model.py::TestScorePurity::test_scoped_save_naming_the_field_still_reattaches` (line 1048) — **DELETE**: asserts `job.last_active_at.tzinfo is UTC` after a scoped save, which is the override's sole observable effect.
- [ ] `tests/unit/test_job_model.py::TestScorePurity::test_scoped_save_excluding_the_field_leaves_the_score_untouched` (line 1033) — **UPDATE**: stays green (popoto, not the override, is what leaves an out-of-scope field alone), but its docstring says "The override must honor that scope" and its assertion message says "the guard let an out-of-scope write through". Both name code that will not exist. Rewrite to pin the popoto contract: a field-scoped save touches no other field's index. **Not named in the issue.**
- [ ] `tests/unit/test_job_model.py::TestGuardedRepair::test_repair_renormalizes_scores_the_rebuild_skewed` (line 1146) — **DELETE**: monkeypatches `rebuild_indexes` to inject a UTC+07 skew and asserts `repair_indexes` sweeps it back. With the sweep call gone the behavior is gone.
- [ ] `tests/unit/test_job_model.py::TestGuardedRepair::test_renormalize_enumeration_failure_returns_zero_and_backfill_still_runs` (line 1184) — **REPLACE**: two halves. Half one (the classmethod returns `(0, 0)` and logs on a broken `SSCAN`) stays valuable and unedited — the migration is still a caller. Half two monkeypatches `Job.renormalize_last_active_scores` to fail and asserts `repair_indexes` still reaches `backfill_open_expectations_index`; once `repair_indexes` never calls it, the patched function is never invoked and the test passes while reaching none of the code it claims to cover. Split the file: keep half one as its own test; re-anchor the backfill-reachability assertion to a hazard `repair_indexes` still has. **Not named in the issue — this is the one that would otherwise ship as a silently vacuous test.**
- [ ] `tests/unit/test_job_model.py::TestScorePurity` class docstring (line 982) — **VERIFY**: already updated by `8c1a36ad1` to cite popoto 1.9.0. No edit expected; confirm it reads correctly once its two sibling tests are gone.
- [ ] `tests/unit/test_job_model.py::TestRenormalizeBatching` (lines 1239-1372) — **KEEP UNCHANGED**: exercises the classmethod directly, which the migration still calls.
- [ ] `tests/unit/test_migrations.py` (lines 371-431) — **KEEP UNCHANGED**: `_migrate_backfill_job_last_active_scores` and its `MIGRATIONS` registration are untouched. Run it to confirm.
- [ ] `tests/unit/test_session_health_tool_timeout.py::test_check_tool_timeout_handles_naive_datetime` (line 177) and `::test_check_tool_timeout_naive_stale_pair_skipped` (line 301) — **KEEP UNCHANGED, as the tripwire**: the `_ts` routing is chosen precisely so these need no edit. If the build finds itself editing either one, the approach drifted toward option (a) and the decision in Spike Results was not followed.
- [ ] `tests/unit/session_runner/test_liveness.py::test_naive_datetime_treated_as_utc` — **KEEP UNCHANGED**: covers `agent/session_runner/liveness.py`, which is out of scope (No-Gos).

No new tests are added. `TestScorePurity`'s surviving two tests are the regression net: they pin the
*score*, which is the property that matters and the one popoto guarantees.

## Rabbit Holes

- **Consolidating every naive guard in the repo onto `utils.utc.to_unix_ts`.** Seven other modules
  carry the same one-liner. Tempting to sweep them all while the context is loaded. Don't — each has
  its own inbound sources to audit, and the audit is the work. File a follow-up instead.
- **Rewriting `_ts` to raise on a naive value.** The robustness-principle literature argues silent
  coercion hides producer bugs, and a `raise` would be the principled endpoint. It is also a behavior
  change on a hot health-check path, in a chore whose premise is "remove dead code". Out of scope.
- **Deleting `renormalize_last_active_scores` because its only remaining caller is a completed
  migration.** The repo's rule is that an applied migration stays in the registry; deleting its
  implementation breaks a fresh machine's `/update`. The issue's Downstream constraints say so
  explicitly.
- **Auditing `agent/session_health.py` end to end.** It is ~6500 lines and every read of it surfaces
  something. This plan touches six two-line sites, named by symbol. Resist widening.
- **Re-deriving popoto's contract from the GitHub CHANGELOG.** The installed artifact in
  `.venv/lib/python3.14/site-packages/popoto/` is what runs. Read that, and `~/src/popoto` only to
  cross-check. The CHANGELOG's 1.9.0 entry sits under `[Unreleased]` in the local checkout, which
  reads as "not shipped" and is misleading.
- **Chasing the `POPOTO_DATETIME_KEY_LEGACY` kill switch.** It is unset, and per spike-1 the score is
  pure even when it is on. Note it in Risks and move on.

## Risks

### Risk 1: A future popoto downgrade below 1.8.2 re-introduces the skew with the compensation gone

**Likelihood: low. Impact: high** — `Job.recent_for_room` trusts scores, so a skewed index buries
recently-active Jobs and the bind-or-mint hot path mints duplicates.

**Mitigation, already shipped:** `docs/features/popoto-version-floor-guard.md` is a fail-closed
interlock that refuses to rebuild indexes when the running interpreter's popoto is below the floor in
`pyproject.toml`. The floor is `>=1.9.0`. A downgrade cannot silently rebuild. No new guard is added
by this plan; the build should re-read that doc and confirm the interlock reads the floor from
`pyproject.toml` rather than a hardcoded version.

### Risk 2: `POPOTO_DATETIME_KEY_LEGACY` is set on some machine, restoring naive decoding

**Likelihood: very low. Impact: negligible.** `git grep` finds no reference in this repo. Even if set,
spike-1 shows `convert_to_numeric` normalizes naive → UTC inside the score function, so scores stay
pure; only in-memory comparisons of a legacy row against an aware `now` would raise `TypeError`, which
is popoto's documented and intended consequence.

### Risk 3: The `test_renormalize_..._backfill_still_runs` rework produces a weaker test

**Likelihood: medium. Impact: medium.** The lazy path is to delete half two outright and lose the
only assertion that `repair_indexes` reaches its final step. The Test Impact entry calls for
re-anchoring, not deleting, and the Failure Path mutation table names the mutation that proves it
(delete `cls.backfill_open_expectations_index()`, expect red).

### Risk 4: A line-number-driven edit hits the wrong code in `agent/session_health.py`

**Likelihood: medium. Impact: low** (caught by tests, but wastes a review round). The file is ~6500
lines and this plan cites six line numbers. **Locate each site by its local variable name and
enclosing function, not by line number**, and re-derive at the head the build actually starts from.

### Risk 5: `models/job.py` imports go unused after the override is deleted

**Likelihood: low. Impact: trivial** (ruff catches it). `UTC` and `datetime` are both still used by
`_now()` at line 78, so neither import should be removed. Named here because "delete the override,
delete its imports" is the reflexive move and it is wrong.

## Race Conditions

No new concurrency. The change removes writes rather than adding them, and every removal narrows an
existing window.

- **`repair_indexes` is already lock-guarded** (`cls._repair_lock`, released in a `finally`). Removing
  a call from inside the critical section shortens the time the lock is held. The lock's acquisition
  and release are untouched.
- **The sweep's own concurrent-write hazard disappears from the repair path.**
  `renormalize_last_active_scores` repairs a row with a fresh re-read plus
  `save(update_fields=["last_active_at"])` specifically so a concurrent expectation write is not
  clobbered. Running it at every worker start means that clobber-proof-but-still-racy write happens on
  every start; after this change it happens only during the one-shot migration. Strictly fewer writes
  racing with live traffic.
- **`Job.save()` removal changes no write ordering.** The override mutated an in-memory attribute
  before delegating; it held no lock and issued no Redis command of its own.
- **Fleet-wide staging is a non-issue.** Machines share one Redis. A machine still running the old
  code sweeps scores that are already correct and repairs zero rows; a machine on the new code skips
  the sweep. Both write the same scores. No ordering constraint between deploys.

## No-Gos (Out of Scope)

Carried from the issue's Downstream constraints and Dropped bucket, plus two the plan adds.

- **No popoto floor change.** `pyproject.toml` keeps `popoto>=1.9.0`. Do not bump, do not pin exact.
- **No new dependencies.**
- **`renormalize_last_active_scores()` stays**, and so does its `MIGRATIONS` entry
  (`backfill_job_last_active_scores`) and its `TestRenormalizeBatching` coverage.
- **`bridge/poll_reconcile.py` and `bridge/poll_registry.py` stay untouched.** Their `tzinfo is None`
  guards parse ISO strings the bridge writes itself with `datetime.now(UTC).isoformat()` through a
  plain Redis `SET`, never through popoto. The 1.9.0 rationale does not reach them.
- **The other seven modules carrying the same guard stay untouched**:
  `agent/session_runner/liveness.py`, `agent/agent_session_queue.py`,
  `monitoring/session_watchdog.py`, `monitoring/bridge_watchdog.py`, `reflections/crash_recovery.py`,
  `models/agent_session.py`, `utils/utc.py`. Deliberately deferred to keep this chore small.
- **`utils.utc.to_unix_ts` keeps its naive branch** regardless of anything decided here. It is the
  documented general read-path coercer and its contract spans floats, ISO strings, and datetimes from
  every source, not just popoto.
- **`agent/session_pickup.py` ends up with no code change.** Named in the issue's Solution Sketch; the
  Spike Results decision keeps all three of its coercers. Recorded as a decision so a reviewer does
  not read it as an omission.
- **No `raise`-on-naive hardening.** Changing `_ts` from coerce to reject is a behavior change on the
  health-check hot path. Separate issue if wanted.
- **No `AgentSession.save()` UTC-stamp removal.** `docs/features/utc-timestamps.md` gates that on
  confirming `auto_now` fires on every save path, which is unfinished and unrelated to the Job score.

## Update System

**No update-system changes required.**

- `scripts/update/migrations.py` is edited, but only its docstring. `MIGRATIONS` is untouched,
  `_migrate_backfill_job_last_active_scores` keeps its identity and behavior, and
  `data/migrations_completed.json` needs no entry. No new migration is added — nothing about this
  change requires a data touch, because the stored hashes were always correct and the scores are
  already pure on every machine after the shipped one-shot sweep.
- No new dependency, config file, env var, or secret to propagate.
- `scripts/remote-update.sh` and `.claude/skills/update/` need no edits.
- **Rollout ordering is unconstrained.** Machines share one Redis; a mixed fleet (some on the old
  code still sweeping, some on the new code skipping) converges on the same scores because the sweep
  repairs zero rows either way. See Race Conditions.

## Agent Integration

**No agent integration required.** This is a model-internal and health-check-internal change.

- No new CLI entry point in `pyproject.toml [project.scripts]`.
- The bridge imports nothing new. `bridge/telegram_bridge.py` is untouched.
- `Job` and `agent/session_health.py` are already reached through existing paths (the router's
  bind-or-mint, the worker's health tick, `scripts/popoto_index_cleanup.run_cleanup`). No public
  signature changes: `Job.save` keeps popoto's signature by inheriting it, and
  `Job.repair_indexes()` keeps its `(quarantined, rebuilt)` return arity.
- **Service restart is still required** after merge, per the repo's standing rule for worker and
  agent code: `./scripts/valor-service.sh restart`, then confirm with `tail -5 logs/bridge.log`.

## Documentation

Three feature docs currently describe the removed mechanism as current behavior. All three are
updates, not new files, so no `docs/features/README.md` index entry is needed.

- [ ] Update `docs/features/durability-model.md` (lines 79-108): rename the
      `### last_active_at score purity (Job.save())` heading, which names a method that will no longer
      exist. Replace the reattach paragraph with popoto's own contract (`convert_to_numeric`
      normalizes naive → UTC since popoto#519/1.8.2; `_decode_datetime` returns aware UTC since
      popoto#537/1.9.0). Keep the `backfill_job_last_active_scores` migration description; delete the
      closing renormalize-after-rebuild rationale.
- [ ] Update `docs/features/popoto-index-hygiene.md` (line 40): delete the "one extra step the other
      guarded paths do not" paragraph's renormalize content — after this change `Job.repair_indexes()`
      carries no extra step. Keep the cross-link to the durability model, retargeted at the renamed
      heading.
- [ ] Update `docs/features/utc-timestamps.md` (lines 87, 94): line 87's instruction to route
      read-path conversions through `utils.utc.to_unix_ts` **stays** — it is the convention this plan
      leans on — but its rationale changes from "popoto deserialization can return naive datetimes" to
      the sources that genuinely still can (ISO strings, floats, non-popoto producers). Line 94's
      "ai pins `popoto>=1.8.0`" becomes `popoto>=1.9.0`.
- [ ] Update the inline docstrings named in Technical Approach: `models/job.py` (`renormalize_last_active_scores`, `recent_for_room`) and `scripts/update/migrations.py::_migrate_backfill_job_last_active_scores`.
- [ ] Verify no feature doc is left dangling: `git grep -n "renormalize\|re-attach\|reattach\|1\.8\.0" -- docs/features/ models/ scripts/` returns nothing describing the removed code as current.

No new feature doc is created — this removes a mechanism rather than adding one, and no
`docs/infra/` entry is needed (no new dependency, service, external API, or deployment change).

## Success Criteria

The issue's six acceptance criteria, plus three the plan adds from findings the issue did not have.

- [ ] `git grep -n "1\.8\.0" models/job.py docs/features/durability-model.md docs/features/utc-timestamps.md` returns no line describing 1.8.0 decoding as current behavior. *(AC1)*
- [ ] `Job.save` is popoto's `Model.save` — no `def save` in `models/job.py` — and `repair_indexes()` no longer calls `renormalize_last_active_scores()`. *(AC2)*
- [ ] `TZ=Asia/Bangkok scripts/pytest-clean.sh tests/unit/test_job_model.py -k ScorePurity` passes. *(AC3)*
- [ ] The reattach and renormalize-after-rebuild tests are gone, and `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_migrations.py` passes. *(AC4)*
- [ ] No comment in `agent/session_health.py` or `agent/session_pickup.py` states popoto strips, drops, or omits tzinfo. *(AC5 — already true on main per spike-2; verify it stays true.)*
- [ ] This plan records the keep-or-remove decision for the session-health and pickup guards with reasoning. *(AC6 — see Spike Results, satisfied at plan time.)*
- [ ] **Added:** `docs/features/popoto-index-hygiene.md` no longer says `Job.repair_indexes()` runs the sweep. *(Third doc, not in the issue.)*
- [ ] **Added:** `tests/unit/test_session_health_tool_timeout.py`'s two naive tests pass **unedited**. *(Tripwire: an edit there means the approach drifted.)*
- [ ] **Added:** the backfill-reachability assertion in `TestGuardedRepair` is re-anchored, not deleted, and fails under the mutation in the Failure Path table. *(Prevents shipping a vacuous test.)*

## Step by Step Tasks

Ordered so each step leaves the suite green. Steps 1-2 are the behavior change; 3-5 are the paperwork
the change makes true.

### 1. Delete the `Job.save()` override

- Remove `def save(...)` and its docstring from `models/job.py` (currently lines 144-188). Locate by
  symbol, not line number.
- Leave the `datetime` and `UTC` imports alone — `_now()` still uses both.
- Run `scripts/pytest-clean.sh tests/unit/test_job_model.py -k ScorePurity` and expect **two failures**
  (`test_reattach_preserves_the_instant_and_is_idempotent`,
  `test_scoped_save_naming_the_field_still_reattaches`). Failures here are the proof the override was
  actually removed; step 3 clears them.

### 2. Remove the post-rebuild sweep from `repair_indexes()`

- Delete the `# rebuild_indexes() re-scores every row via field.on_save on ...` comment, the
  `scanned, renormalized = cls.renormalize_last_active_scores()` call, and the `if renormalized:` log
  block (currently `models/job.py:770-783`).
- Keep `rebuilt = cls.rebuild_indexes()` and `cls.backfill_open_expectations_index()`.
- Confirm `renormalize_last_active_scores` still exists and is still called from
  `scripts/update/migrations.py:1162`.
- `git grep "re-normalized"` to confirm no log consumer depended on the deleted line.

### 3. Update the tests

- Delete `test_reattach_preserves_the_instant_and_is_idempotent` and
  `test_scoped_save_naming_the_field_still_reattaches` from `TestScorePurity`.
- Rewrite the docstring and assertion message of
  `test_scoped_save_excluding_the_field_leaves_the_score_untouched` so it pins popoto's
  field-scoped-save contract instead of the deleted override.
- Delete `test_repair_renormalizes_scores_the_rebuild_skewed` from `TestGuardedRepair`, and with it
  the now-unused `UTC_PLUS_7_REBUILD_SKEW_SECONDS` constant if nothing else references it
  (`git grep` first).
- Split `test_renormalize_enumeration_failure_returns_zero_and_backfill_still_runs`: keep half one
  (classmethod returns `(0, 0)` and logs on broken `SSCAN`) as its own test; re-anchor half two's
  backfill-reachability assertion to a hazard `repair_indexes` still has, rather than to a
  monkeypatched sweep that is no longer called.
- `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_migrations.py` — green.

### 4. Collapse the six inline coercions in `agent/session_health.py`

- For each of the six sites in the Technical Approach table, located **by local variable name and
  enclosing function**, replace `X_aware = X if X.tzinfo else X.replace(tzinfo=UTC)` plus the
  `(NOW - X_aware).total_seconds()` subtraction with `NOW.timestamp() - _ts(X)`.
- At `_check_tool_timeout` (line ~658), reuse the `_ts(last_at)` value already computed for the epoch
  gate rather than calling `_ts` twice.
- Leave `_ts`, `_at_rest_coerce_ts`, and `_session_is_alive` untouched. Leave
  `agent/session_pickup.py` untouched entirely.
- `scripts/pytest-clean.sh tests/unit/test_session_health_tool_timeout.py tests/unit/session_runner/test_liveness.py` — green, with **no test edits**.

### 5. Correct the docstrings and docs

- `models/job.py`: `recent_for_room` (line ~533), `renormalize_last_active_scores` (lines ~807-833).
- `scripts/update/migrations.py`: `_migrate_backfill_job_last_active_scores` docstring only.
- `docs/features/durability-model.md`, `docs/features/popoto-index-hygiene.md`,
  `docs/features/utc-timestamps.md` per the Documentation section.
- Verify: `git grep -n "1\.8\.0\|reattach\|re-attach" -- models/ scripts/ docs/features/` shows only
  accurate historical statements in the past tense.

### 6. Mutation check

Run the three mutations in the Failure Path Test Strategy table and confirm each turns the suite red.
Re-measure after every review round — a green suite on a deletion-only change proves nothing by
itself.

### 7. Final validation

- `python -m ruff check` and `python -m ruff format` (black formatting only; no other linters).
- `TZ=Asia/Bangkok scripts/pytest-clean.sh tests/unit/test_job_model.py -k ScorePurity`.
- `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_migrations.py tests/unit/test_session_health_tool_timeout.py tests/unit/session_runner/test_liveness.py`.
- Walk the Success Criteria checklist.
- `./scripts/valor-service.sh restart` after merge, then `tail -5 logs/bridge.log`.

## Verification

```bash
# AC1 + AC5 + the added third-doc criterion: no stale version claims anywhere
git grep -n "1\.8\.0" models/job.py docs/features/durability-model.md docs/features/utc-timestamps.md
git grep -n -i "popoto.*\(strip\|drop\|omit\|naive\)" agent/session_health.py agent/session_pickup.py
git grep -n "renormalize" docs/features/popoto-index-hygiene.md

# AC2: the override is gone and the sweep call with it
git grep -n "def save" models/job.py            # expect: no output
git grep -n "renormalize_last_active_scores" models/job.py
#   expect: the def, the batch-size comment, the batch helper docstring — NOT a call in repair_indexes
git grep -n "renormalize_last_active_scores" scripts/update/migrations.py   # expect: still called

# AC3: score purity survives on a non-UTC host
TZ=Asia/Bangkok scripts/pytest-clean.sh tests/unit/test_job_model.py -k ScorePurity

# AC4 + the tripwire
scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_migrations.py
scripts/pytest-clean.sh tests/unit/test_session_health_tool_timeout.py tests/unit/session_runner/test_liveness.py
git diff main --stat -- tests/unit/test_session_health_tool_timeout.py   # expect: no change

# The six rewritten sites carry no leftover inline coercion
git grep -n "tzinfo else" agent/session_health.py
#   expect: no output (the surviving guards all use the `if x.tzinfo is None:` statement form
#   inside _ts / _at_rest_coerce_ts / _session_is_alive)

# Formatting
python -m ruff check
python -m ruff format --check
```

**Mutation checks** (each must turn the suite red):

```bash
# 1. _ts keeps the tolerance
#    delete `val = val.replace(tzinfo=UTC)` in agent/session_health.py::_ts
#    → test_check_tool_timeout_handles_naive_datetime raises TypeError
# 2. repair_indexes still reaches its final step
#    delete `cls.backfill_open_expectations_index()` in models/job.py::repair_indexes
#    → the re-anchored TestGuardedRepair test fails
# 3. score purity is popoto's, not ours (this one must stay GREEN)
#    make models/job.py::_now() return `datetime.now()` (naive local)
#    → TestScorePurity still passes, proving the property survives without our compensation
```

## Critique Results

| Severity | Critic(s) | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | The plan tells the build to re-anchor the backfill-reachability assertion to "leg 1/leg 2 or the rebuild raising", but `repair_indexes()` wraps its whole body in a single bare `try:` with only a `finally:` (lock release) and no `except`. A raise from any of those three hazards propagates out of the function before `cls.backfill_open_expectations_index()` is ever reached, so none of the three named hazards can produce the "hazard fired, backfill still ran" scenario the re-anchored test is supposed to prove. | pending | Verified in `models/job.py`: `assert_popoto_floor()` at :712, `cls._repair_lock.acquire(blocking=False)` at :714, bare `try:` at :717, `cls.backfill_open_expectations_index()` at :782, `finally: cls._repair_lock.release()` at :784-785 — no `except` anywhere between. Before building, check whether popoto's `rebuild_indexes()` swallows a per-row failure internally (that would be the only hazard that still reaches backfill). If it does not, drop the hazard framing and re-anchor with a plain happy-path spy: call `Job.repair_indexes()` normally and assert `backfill_open_expectations_index` was invoked. That still fails under the plan's own mutation (delete the `backfill_open_expectations_index()` call), which is the property Risk 3 wants. |
| CONCERN | Risk & Robustness | Technical Approach and Step 4 tell the build to "reuse the `_ts(last_at)` value already computed eleven lines earlier for the epoch gate", but that call is not bound to any name — it is an inline sub-expression inside `anchor is not None and _ts(last_at) < anchor` — and Python's `and` short-circuits it away entirely when `anchor is None` (both `started_at` and `created_at` absent). There is no existing value to reuse. | pending | At `agent/session_health.py:641`: `anchor = _ts(getattr(entry, "started_at", None)) or _ts(getattr(entry, "created_at", None))`, then `if anchor is not None and _ts(last_at) < anchor: return None`. State the rewrite explicitly instead: hoist `last_at_ts = _ts(last_at)` once above the anchor check — safe, because `if not isinstance(last_at, datetime): return None` at :635 already guarantees `_ts` returns a float and never `None` — then use `last_at_ts` in both the epoch-gate comparison and the final `age = datetime.now(tz=UTC).timestamp() - last_at_ts`. |
| CONCERN | Risk & Robustness | Risk 1 calls the popoto version-floor guard "the mechanism that makes this removal safe to keep removed", but `assert_popoto_floor()` is invoked only inside each model's `repair_indexes()` — never inside `Job.save()`, `touch()`, `mark_active()`, `revive()`, or `mint()`, which is exactly the write path the deleted reattach protected. A below-floor downgrade would silently re-skew scores on every ordinary write until the next `repair_indexes()` call, and this same plan deletes the only log line that ever reported skew. | pending | `git grep -n "assert_popoto_floor" -- '*.py'` gives exactly three production call sites, all at the head of a model's own `repair_indexes()`: `models/job.py:712`, `models/agent_session.py:2436`, `models/room.py:240`. `docs/features/popoto-version-floor-guard.md`'s "failure it prevents" section describes an unrelated failure mode (an `ExtraData`/`msgpack.unpackb` crash decoding `IndexedField` pointers during rebuild); it catches a datetime-skew-capable downgrade only incidentally, via the same version comparison, and only at the next repair. Narrow Risk 1's wording to "a delayed, reactive check that fires at the next `repair_indexes()` call, not a synchronous guard on the write path", and state explicitly that the residual write-path gap is accepted. |
| CONCERN | Scope & Value | The six-site `agent/session_health.py` consolidation (Key Elements item 5) has no Success Criteria checkbox of its own. It is proven only indirectly by the "two naive tests pass unedited" tripwire and by a `git grep` buried inside the Verification bash block. It is also not compelled by any of the issue's six acceptance criteria — AC5 is already vacuously true per the plan's own spike-2, and AC6 only requires the plan to record a decision — yet it edits six call sites in a ~6500-line file the plan itself rates "medium likelihood" for a wrong-line edit (Risk 4). | pending | Add a Success Criteria bullet naming the check directly: "all six `X if X.tzinfo else X.replace(tzinfo=UTC)` sites in `agent/session_health.py` are replaced with `_ts(X)`, verified by `git grep -n "tzinfo else" agent/session_health.py` returning no output". The grep is already in the Verification block but is not a tickable criterion; the surviving guards all use the `if x.tzinfo is None:` statement form inside `_ts` / `_at_rest_coerce_ts` / `_session_is_alive`, so the grep is a clean discriminator. |
| CONCERN | Scope & Value | The plan is `status: Ready` and says Open Question 1 "changes one success criterion, not the shape of the work". Reversing it to the issue's option (a) actually invalidates Key Elements item 5, the six-row Technical Approach table, Step 4, at least two Success Criteria bullets, two Verification lines, and requires deleting the two tests currently pinned as an unedited tripwire. That is a build-shape change, not a one-line edit. | pending | Either commit to the plan's own option-(c) reasoning (Spike Results already argues it thoroughly: option (a) makes `datetime.now(tz=UTC) - naive` raise `TypeError` at :658 for any non-popoto caller, and silently withdraws a real tolerance) and close Question 1, or restate its blast radius accurately. If it ever resolves to option (a), Step 4 must additionally delete `test_check_tool_timeout_handles_naive_datetime` (`tests/unit/test_session_health_tool_timeout.py:177`) and `test_check_tool_timeout_naive_stale_pair_skipped` (:301), and the Verification line `git diff main --stat -- tests/unit/test_session_health_tool_timeout.py # expect: no change` must be deleted rather than run. |
| CONCERN | History & Consistency; Structural check | Test Impact opens with a completeness claim — "Five test bodies are affected, not the three the issue's Solution Sketch names" — that is itself off by one. `tests/unit/test_migrate_job_expectations.py::test_is_idempotent` carries an inline comment describing the mechanism this plan deletes as live, ongoing behavior, and that file is named nowhere in the plan. After step 2 the comment describes code that no longer exists, a direct Principle 1 (NO LEGACY CODE TOLERANCE) violation that ships green. | pending | The stale block is `tests/unit/test_migrate_job_expectations.py:137-142` ("The one write the spy tolerates is the recency-score renormalization scoped exactly to ["last_active_at"]: the migration closes with Job.repair_indexes(), whose rebuild re-skews scores on a non-UTC host and whose renormalize sweep repairs them with that field-scoped save on every pass by design (#2636)"). Comment-only fix: drop the repair_indexes/renormalize-sweep clause. The `monkeypatch.setattr(Job, "save", ...)` lambda and `assert saves == []` need no change — the assertion still holds because no `["last_active_at"]`-scoped save occurs at all once the sweep is gone (which is precisely why it ships silently green). Add it to Test Impact as a sixth item and to Step 5. |
| CONCERN | History & Consistency | AC5 and its verification grep are hard-scoped to `agent/session_health.py` and `agent/session_pickup.py`, inherited unmodified from the issue's Solution Sketch, and spike-2's probe was scoped the same way. `ui/data/sdlc.py` carries a comment making exactly the claim AC5 targets — "Popoto strips timezone on serialize/deserialize" — which is false on 1.9.0. The violation survives the build while AC5 reports green. | pending | Exact text at `ui/data/sdlc.py:824-825`, inside `_safe_float` (def at :815). Keep the `if val.tzinfo is None: val = val.replace(tzinfo=datetime.UTC)` code at :823-826 — `_safe_float`'s contract spans `datetime`, `int`, `float`, and `str`, so it is a keep under the plan's own wider-than-popoto rule, and `docs/features/utc-timestamps.md` already names it as intentionally untouched. Rewrite only the two-line comment to state the true rationale (the helper accepts non-popoto sources), and widen AC5's grep from the two `agent/` files to repo-wide, or at minimum add `ui/data/sdlc.py`. |
| NIT | Structural check | The plan had no `## Critique Results` section, which `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` mandates and the CRITIQUE finalize gate reads. All four repo-mandated sections (`## Documentation`, `## Update System`, `## Agent Integration`, `## Test Impact`) are present and substantive. | resolved by this critique (2026-09-05) | N/A (NIT) — this section is the fix. |

## Open Questions

*Status is `Ready` in the sense that the plan is complete and the build sequence is unambiguous. None of the questions below blocks the build: each has a recorded decision with its reasoning in Spike Results, and question 1 is the one the critique stage should adjudicate because it changes one success criterion, not the shape of the work.*

1. **The session-health decision is a third option, not one of the issue's two.** The issue offered
   (a) remove the one-liners and their comments, or (b) keep the naive branch only inside the
   general-purpose coercers and remove the one-liners. This plan takes (b)'s *keep* list and routes
   the six one-liner sites through `_ts` rather than deleting them, so the two existing naive tests
   stay green unedited and no tolerance is silently withdrawn. Is that acceptable, or do you want the
   stricter (a) with the two tests deleted?

2. **Follow-up issue for the other seven modules?** The issue's Dropped bucket says to file one "if
   the plan's decision on session-health guards is remove". The decision here is *consolidate*, not
   remove, which arguably makes the follow-up more attractive (there is now a stated pattern to apply)
   and less urgent (nothing is being withdrawn). File it, or leave it?

3. **`_ts` versus `utils.utc.to_unix_ts` at the six rewritten sites.** The plan uses the module-local
   `_ts` to keep the diff small. `docs/features/utc-timestamps.md` names `to_unix_ts` as the single
   source of truth and lists `agent/session_health._ts` among three older helpers "intentionally left
   untouched". Keep `_ts`, or fold it into `to_unix_ts` as part of this chore?

4. **Anything worth keeping as a canary?** The plan removes all three redundant layers and relies on
   popoto plus the existing version-floor guard. An alternative is to keep a cheap assertion somewhere
   that a freshly reloaded `Job.last_active_at` is aware, as an early warning if the dependency ever
   regresses. `TestScorePurity::test_resave_after_reload_keeps_the_score_a_utc_epoch` already asserts
   `reloaded.last_active_at.tzinfo is UTC`, so arguably the canary exists. Agree, or want more?
