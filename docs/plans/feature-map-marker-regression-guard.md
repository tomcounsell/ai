---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3010
last_comment_id:
---

# FEATURE_MAP Marker-Regression Guard

## Problem

`tests/conftest.py` auto-applies a pytest feature marker to every collected test by taking the
module basename, stripping `test_` and `.py`, and substring-matching the remainder against the
`FEATURE_MAP` dict, first hit wins (`tests/conftest.py:1106` for the map, `:1206` for the loop).
Nothing checks that the marker a file lands on is the marker anyone intended.

**Current behavior:**

A test file can be renamed, moved, or split and silently change marker. Two distinct mechanisms
produce this, both live in the repo today:

1. **Ordering collision.** `FEATURE_MAP` is iterated in dict-insertion order and breaks on first
   hit, so a generic key positioned early beats a specific key positioned late. spike-1 of #2879
   found `test_worktree_manager_config.py` would tag `config` rather than `git`, because `config`
   sits at index 45 and `worktree_manager` at index 63. The same mechanism is live now:
   `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` tags `config`, and
   `tests/unit/session_runner/test_schema_routing.py` tags `messaging` via `routing`.
2. **Fragment match.** The match is a bare substring, not a token, so a pattern can match the
   inside of a longer word. `test_youtube_transcription.py` tags `messaging` because `transcript`
   is a prefix of `transcription`. `test_long_task_checkpointing.py` tags `validation` because
   `checkpoint` is a prefix of `checkpointing`.

In every case the collection total is unchanged and the suite stays green, so the loss is
invisible. Measured against `f3594dd23`: 21 test files sitting inside a package directory whose
own name resolves to a marker do not carry that marker, and 19 of them carry no marker at all.
`pytest -m reflections` silently skips almost the entire `tests/unit/reflections/` package.

**Desired outcome:**

A guard that fails when a test file's `FEATURE_MAP` marker does not resolve as intended, so a
mistag surfaces as a red check on the pull request that introduces it rather than as coverage
that quietly stopped running.


## Freshness Check

**Baseline commit:** `f3594dd23962f8bcdc225edb387e3503e727b671`
**Issue filed at:** 2026-08-25T17:46:44Z
**Disposition:** Unchanged, with one premise correction (see Notes)

**File:line references re-verified:**

- `tests/conftest.py:1106` (`FEATURE_MAP` definition) and `tests/conftest.py:1206`
  (`pytest_collection_modifyitems`, the first-hit loop). Both still present and unchanged in
  substance. The issue does not cite line numbers itself; these were located by symbol.
- `tests/unit/test_no_legacy_paths.py` (the exemption precedent the issue names). Still present,
  50 lines. Its exemptions are a module-level `ALLOWED_FILES` set of repo-relative paths plus a
  `EXEMPT_PREFIXES` path-prefix tuple imported from `scripts/check_issue_disposition.py`. No
  line numbers anywhere. Its own docstring records that a previously hardcoded `docs/plans/`
  prefix broke when a plan was archived (#3031), which is a direct argument for keying exemptions
  by a shared definition rather than a literal.

**Cited sibling issues/PRs re-checked:**

- #2879 "Split the largest test files into per-class modules". CLOSED 2026-08-25T17:50:17Z by
  PR #3005. Its plan is archived at `docs/archive/plans-completed/split-remaining-large-test-files.md`
  and carries spike-1 verbatim.
- PR #3005. MERGED 2026-08-25T17:50:15Z, branch `session/split-remaining-test-files`. Created the
  24 new basenames. Its No-Gos explicitly defer this guard as `[SEPARATE-SLUG #2879]`, which is
  the deferral #3010 exists to re-home.
- #2805. CLOSED. The line-keyed `ALLOWLIST` precedent the issue warns against.
- PR #3012 (`Closes #3011`) cascaded the #2879 renames into docs. It did not touch `FEATURE_MAP`.

**Commits on main since issue was filed (touching referenced files):**

`git log --since=2026-08-25T17:46:44Z -- tests/conftest.py tests/unit/test_no_legacy_paths.py .githooks/pre-commit`
returns four commits, all Redis/popoto test-database work (`8c1a36ad1`, `00a3d93ca`, `b4daa7861`,
`ff20e0311`). None touches `FEATURE_MAP`, the collection hook, or the exemption precedent.
Irrelevant to this plan.

**Active plans in `docs/plans/` overlapping this area:** none. `grep -l FEATURE_MAP docs/plans/*.md`
returns nothing.

**Notes.** One premise in the issue's acceptance criteria does not hold as written. The criterion
"the guard runs in CI on every PR" assumes a CI job that runs the test suite. There is exactly one
GitHub Actions workflow in this repo, `.github/workflows/claude.yml`, and it only reacts to
`@claude` mentions in comments, issues, and reviews. No workflow runs pytest, and neither
`.githooks/pre-commit` nor `.githooks/pre-push` runs tests either (pre-commit's only mention of
tests is a comment explaining why it does not run them in a worktree). The repo's actual
per-PR gate is the SDLC TEST stage running `scripts/pytest-clean.sh` locally. The Solution
addresses this directly rather than declaring the criterion met by placing a file in `tests/`.

All measurements quoted in this plan were produced by replaying the real `FEATURE_MAP` and the
real first-hit algorithm, parsed out of `tests/conftest.py` with `ast.literal_eval`, over the
output of `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'` at the baseline commit. They are
fresh by construction.


## Prior Art

`gh issue list --state closed --search "FEATURE_MAP marker"` and
`gh pr list --state merged --search "FEATURE_MAP"` return the split lineage and nothing else.
No prior attempt at this guard exists, so there is no **Why Previous Fixes Failed** section.

- **#431 / PR #431**: "Organize test suite: feature markers, e2e tests, index". Introduced
  `FEATURE_MAP` and the basename-substring auto-tagging in the first place. The substring match
  and the first-hit-wins ordering both date from here and have never been guarded.
- **#516 / PR #606**: "Comprehensive test suite for agent memory system". Its plan
  (`docs/archive/plans-completed/memory-test-suite-516.md:81`) contains a build prerequisite
  reading "These entries MUST come before the generic 'lifecycle' and 'pipeline' entries because
  matching is first-match-wins". Evidence that the ordering hazard has been rediscovered by hand,
  per plan, for over a year.
- **#2946 / PR #2941**: "Split test_output_handler.py and test_memory_extraction.py into
  theme-grouped packages". Established the package-per-theme convention and created
  `tests/unit/output_handler/` and `tests/unit/memory_extraction/`. Neither directory name
  resolves through `FEATURE_MAP`, so all ten files in them carry no marker. That predates the
  split (the original monolith files carried no marker either), so it is a coverage gap rather
  than a regression, but it is the reason the guard needs a rule for packages whose directory
  name is not itself a `FEATURE_MAP` key.
- **#2879 / PR #3005**: "Split the remaining four large test files into per-theme packages".
  Produced spike-1, the finding this issue is built on, and correctly scoped the guard out to keep
  the split mechanical. The PR review recorded 24/24 new basenames resolving to their intended
  marker and flagged that the guard was the highest-value artifact of the work and the one thing
  exiting without a tracker.
- **#2805**: deleted a line-number-keyed `ALLOWLIST` after unrelated merges shifted line numbers
  and silently un-exempted call sites. The binding precedent for how this guard's exemptions are
  keyed.
- **#3031**: turned `tests/unit/test_no_legacy_paths.py` red when a plan document was archived out
  from under its hardcoded `docs/plans/` prefix. The reason this guard's path predicates are
  derived rather than hardcoded where a derivation exists.


## Research

No relevant external findings. The subject is a first-party pytest `conftest.py` convention and a
first-party dict; nothing about it is answerable from library documentation or ecosystem practice.
Proceeding with codebase context.

**Queries considered and skipped:** pytest marker-registration and `--strict-markers` behavior was
the one plausible external topic. It is not applicable: this guard never runs pytest to determine
a marker, it replays the resolution function directly, so pytest's own marker machinery is not in
the path.


## Spike Results

_placeholder_

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
