---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3181
last_comment_id:
---

# Classify the remaining naive-tzinfo guards by the #3173 rule

## Problem

Twenty-one `tzinfo is None` coercion guards are scattered across twelve files. Every one of them was written against popoto 1.8.0, which round-tripped `DatetimeField` values as naive datetimes. popoto 1.9.0 decodes stored datetimes as aware UTC, so some of those guards now defend against a state that cannot occur, and several carry comments that assert the old behaviour as fact.

**Current behavior:**
Nothing is broken. The cost is that every lane touching one of these files has to re-derive, from scratch, whether the guard in front of it is load-bearing. Worse, five of the guards carry prose that is now actively wrong ("Popoto strips tzinfo on load", "matching how Popoto SortedField stores them"), so a reader who trusts the comment reaches the wrong conclusion. One site (`reflections/audits/redis_quality_audit.py:61`) guards a field that has never been a datetime at all.

**Desired outcome:**
Every site in the sweep carries a settled verdict. Popoto-only guards are gone, with a test that would have caught the deletion if it were wrong. Mixed-input guards stay, each with a one-line docstring reason that a future reader can trust. A re-run of the sweep returns only the mixed-input survivors.

## Freshness Check

**Baseline commit:** `de229ee46` (recon), re-anchored at plan time to `d27b94f2d`
**Issue filed at:** 2026-09-05 (follow-up to #3173 / PR #3180, merged `2aecc7c93`)
**Disposition:** Minor drift

**File:line references re-verified:** all 26 sweep hits were re-derived from a live run of the issue's own grep at `de229ee46`. Every path the issue names still exists; the per-site line numbers in this plan are the measured ones, not the issue's.

**Cited sibling issues/PRs re-checked:**
- #3173 / PR #3180 — closed, merged at `2aecc7c93`. It is the source of the classification rule, and it left `agent/session_health.py` and `agent/session_pickup.py` classified as keeps.
- #3199 — **open**, and it edits `models/agent_session.py` in the `repair_indexes` region (~:2476-2528). This plan edits `:1092` and `:2225` of the same file. Disjoint regions, but the lanes will both be writing `models/agent_session.py`; whichever lands second rebases.
- #3207 — closed. It was the `test_session_archive` naive-round-trip node split out of #3199.

**Commits on main since issue was filed (touching referenced files):** none. `git log 2aecc7c93..de229ee46` is 131 commits and not one of them touches any file in the sweep set. The code the issue describes is byte-identical to the code this plan changes.

**Active plans in `docs/plans/` overlapping this area:** `agentsession-quarantine-counter-divergence.md` (the #3199 lane) touches `models/agent_session.py`. Overlap is file-level, not region-level.

**Notes:** The drift is in the issue's own measurements, not in the code. See Prior Art for the two corrections.

## Prior Art

- **PR #3180 / issue #3173** — "Remove Job UTC-reattach and 1.8.0-era naive-tzinfo guards". Succeeded. It established the rule this plan applies and demonstrated the failure mode to avoid: its review caught a test that asserted the deletion without ever reaching the deleted line. That is the single most important lesson carried forward here.
- **PR #787 / issue #777** — "Fix watchdog UTC duration: `_to_timestamp` treats naive datetimes as local time". Succeeded. It is *why* `monitoring/session_watchdog.py:65` exists: without the guard, a non-UTC host inflated every duration by its offset and fired false `LIFECYCLE_STALL` events. That guard stays; only its stale rationale changes.
- **Issue #1653 / #1645** — "AgentSession.updated_at stamped 7h in the future: popoto `auto_now` uses naive local `datetime.now()`". Succeeded upstream (popoto #421). It is the origin of `_heal_future_updated_at`, one of this plan's deletion sites — the healer stays, only its naive-input guard goes.
- **Issue #3199** — open, adjacent. Its recon independently confirmed that popoto 1.9.0 changed decode semantics on this exact code path, which corroborates the premise here.

No prior attempt to classify *this* set exists. #3173 deliberately scoped away from it.

## Research

No external research was needed, and none would have been authoritative. The one load-bearing external premise — "popoto 1.9.0 decodes every stored datetime as aware UTC" — was verified directly against the installed package rather than against documentation, because the installed version is what the code runs on.

**What was read:** `.venv/lib/python3.14/site-packages/popoto/models/encoding.py` (`_decode_datetime`, `_LEGACY_DATETIME_RE`, `_LEGACY_DATETIME_FORMAT`) and `popoto/fields/datetime_field.py` (`format_value_pre_save`).

**Key findings:**
- Values written from popoto #521 onward are stored via `obj.isoformat()`, which carries the UTC offset for an aware value, so they decode aware. Confirmed at `encoding.py:207`.
- Pre-#521 legacy rows were stored offset-free as `%Y%m%dT%H:%M:%S.%f`. `_decode_datetime` matches that shape with an anchored regex and re-attaches `timezone.utc` (`encoding.py:159`), so **legacy rows also decode aware**.
- That legacy re-attach is gated on `Defaults.DATETIME_KEY_LEGACY` (`encoding.py:153`). With the switch on, legacy rows decode **naive** again. The switch is read from `POPOTO_DATETIME_KEY_LEGACY`, which appears nowhere in this repo or in `.env.example`, so it is off.
- **A deliberately naive value written post-#521 round-trips naive.** `isoformat()` renders it as `2026-08-07T12:00:00.123456`, which the anchored legacy regex is explicitly written not to match (`encoding.py:92`). This is the one way a guard deletion could bite, and it is why the writer audit below is a prerequisite rather than a nicety.
- `DatetimeField.format_value_pre_save` stamps `datetime.now(timezone.utc)` for `auto_now` / `auto_now_add` fields.

## Data Flow

## Architectural Impact

## Appetite

## Prerequisites

## Solution

## Failure Path Test Strategy

## Test Impact

## Rabbit Holes

## Risks

## Race Conditions

## No-Gos (Out of Scope)

## Update System

## Agent Integration

## Documentation

## Success Criteria

## Team Orchestration

## Step by Step Tasks

## Verification

## Critique Results

---

## Open Questions
