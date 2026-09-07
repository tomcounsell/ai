---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3175
last_comment_id:
---

# Drain the FEATURE_MAP mistag baseline

## Problem


`pytest -m reflections` does not run the reflections tests. Of the 396 tests in
`tests/unit/reflections/` and `tests/integration/reflections/`, the marker
selects **34** and deselects **362**. Someone who writes a new reflections test,
runs `pytest -m reflections`, and sees it pass has almost certainly not run it.

The cause is that a test's feature marker is derived from a substring match of
its *filename* against `FEATURE_MAP`, an insertion-ordered dict where first hit
wins. `tests/unit/reflections/test_pm_briefings_builder.py` contains no
`FEATURE_MAP` key at all, so it gets no marker. Its neighbour
`test_pm_briefings_no_slots_configured.py` gets `config`, because `config` is a
literal substring of `configured`. `test_sdlc_progress_check.py` gets `sdlc`,
because `sdlc` sits at insertion index 16 and `reflection` at index 52. The
directory these three files live in is named `reflections` and every one of them
is a reflections test, and the resolver never looks at it.

[#3010](https://github.com/tomcounsell/ai/issues/3010) shipped
`tests/unit/test_feature_map_markers.py`, a guard that detects this class of
mistag, with a path-keyed `KNOWN_MISTAGS` baseline recording the 24 files
already wrong on the day it landed. The guard was built so the baseline could
only shrink. Nobody has shrunk it. This issue drains it.

**Current behavior:**

Measured at `8e62c3a50`, over 838 tracked test files:

| | |
|---|---|
| `python tests/marker_map.py --audit` | 25 violations across 24 paths (21 R1, 2 R2, 2 R3) |
| `KNOWN_MISTAGS` entries | 24 |
| files carrying a derived marker | 284 of 838 |
| `tests/unit/reflections/` carrying `reflections` | 2 of 20 |
| `tests/integration/reflections/` carrying `reflections` | 0 of 2 |
| `pytest -m reflections --collect-only` over both packages | 34 of 396 tests, 362 deselected |

**Desired outcome:**

The package directory a test lives in is a declaration of intent, and the
resolver honors it. `tests/unit/reflections/` means `reflections`, for every
file in it, forever, including files nobody has written yet. `KNOWN_MISTAGS`
is empty. `pytest -m reflections` collects 396 of 396.

## Freshness Check


**Baseline commit:** `8e62c3a5095cac702835e56fbd2f0910574270da`
**Issue filed at:** 2026-09-05T12:51:18Z (body last edited 2026-09-06T08:09:47Z)
**Disposition:** Minor drift — one filed measurement does not reproduce, and one
material fact was absent from the write-up. The premise holds in full.

**Every file:line and factual claim in the issue re-verified:**

- `tests/marker_map.py::KNOWN_MISTAGS` — claimed 24 entries covering 21 R1,
  2 R2, 1 R3. **Holds**, with one correction of arithmetic rather than fact:
  the audit reports **25** violations across **24** paths, because
  `test_pm_briefings_no_slots_configured.py` trips R1 *and* R3. The issue body
  counts it once; `docs/features/feature-map-marker-guard.md` describes it as
  "21 from R1, 2 from R2, 1 further from R3", which is the same 24 paths.
- "`config` sits at insertion index 45 and `reflection` at 52" — **holds**.
  Verified by enumerating `FEATURE_MAP` keys; no rename of a
  `test_*_configured.py` file inside `reflections/` escapes `config`.
- "`pytest -m reflections` skips almost the whole `tests/unit/reflections/`
  package" — **holds and is now quantified**: 34 of 396 collected.
- `tests/unit/session_runner/test_schema_routing.py` "alone among 18 siblings"
  — **holds** (`resolved='messaging'` via key `routing`, 17 siblings unmarked).
- `tests/unit/hooks/test_pre_tool_use_foreground_subagents.py` "alone among 5
  siblings" — **holds** (`resolved='sdlc'` via key `pre_tool_use`).
- `tests/unit/test_long_task_checkpointing.py` fragment match — **holds**
  (`resolved='validation'` via key `checkpoint` inside `checkpointing`).

**Drift 1 — the headline measurement does not reproduce.** The issue and
`docs/features/feature-map-marker-guard.md` both state that making the package
directory authoritative "gains 39 markers and corrects 6 with zero losses",
measured at `f3594dd23`. Replayed at `8e62c3a50` the same change gains **17
files newly marked / 21 marker applications** and corrects **4**. The plan uses
the re-measured numbers throughout and treats 39/6 as stale; correcting the
feature doc is a documentation task below.

**Drift 2 — a material fact absent from the write-up.** 47 tracked test files
carry an explicit `pytest.mark.<feature>`, which the guard cannot see. This is
not a drift in the code, it is a gap in the issue's survey, and it changes the
right answer (see Solution). `tests/integration/reflections/test_pm_briefings_e2e.py`
sits in `KNOWN_MISTAGS` while declaring `pytest.mark.reflections` itself.

**Cited sibling issues/PRs re-checked:**

- **#3010** — CLOSED 2026-09-06T12:58:06Z, merged as PR #3190
  (`d14685728`). The guard, `tests/marker_map.py`, and
  `docs/features/feature-map-marker-guard.md` are all on `main`. This is the
  precondition the feature doc records for #3175, and it is satisfied.
- **#3184** — CLOSED 2026-09-07T08:04:43Z, merged as `8e62c3a50`. `_stem` now
  uses `removeprefix("test_")` / `removesuffix(".py")`. **The rebase this plan
  was warned to expect has already happened**: `8e62c3a50` is `origin/main`, the
  baseline for every number here. #3184 explicitly declared `KNOWN_MISTAGS` out
  of scope and in scope for #3175, and it moved the baseline by exactly zero
  entries — re-measured after the stem change, the audit still reports 25/24.
- **#2879 / #2946** — CLOSED. The per-theme test-file splits (PRs #2941, #3005)
  are what *created* `tests/unit/reflections/`, `session_runner/`, `hooks/`,
  `memory_extraction/`, and `output_handler/` as packages. They are the origin
  of the R1/R2 population, not a fix for it.
- **#2805** — the line-keyed-`ALLOWLIST` lesson that makes `KNOWN_MISTAGS`
  path-keyed. Still binding; this plan removes entries, never re-keys them.
- **#3031** — the stale-exemption lesson. Still binding, and load-bearing here:
  draining a file's violation without deleting its baseline entry fails the
  guard, which is exactly the property that makes this drain verifiable.

**Commits on main touching referenced files since the issue was filed:**

- `d14685728` "FEATURE_MAP marker-regression guard" — created every file this
  plan touches.
- `8e62c3a50` "FEATURE_MAP stem strips test_ and .py anchored, not globally" —
  changed `_stem`, added three stem-fidelity fixtures, retagged
  `test_test_judge.py` to `tools` and `test_validate_test_impact.py` to
  `validation`. **Measured effect on this issue's baseline: none.**

**Active plans in `docs/plans/` overlapping this area:**
`docs/plans/feature-map-stem-anchored-strip.md` (`tracking:` → #3184) is the
adjacent lane. Its work is **merged**, so the overlap is historical rather than
live. This plan does not edit that document. The two share
`tests/marker_map.py`; #3184 owned `_stem`, this plan owns `FEATURE_MAP`
resolution semantics, `KNOWN_MISTAGS`, and the assignment mechanism in
`tests/conftest.py`.

## Prior Art

_placeholder_

## Research

_placeholder_

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

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
