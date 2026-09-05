---
title: Remove Job UTC-reattach and 1.8.0-era naive-tzinfo guards
slug: remove-popoto-1-8-0-naive-datetime-scar-tissue
status: Planning
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

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

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

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Open Questions

_placeholder_
