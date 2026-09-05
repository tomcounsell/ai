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

Four spikes ran against baseline `f3594dd23`. All four are `code-read` plus an executable replay
of the real `FEATURE_MAP` and the real first-hit loop, parsed out of `tests/conftest.py` with
`ast.literal_eval` so the numbers come from the shipped dict rather than a transcription of it.
The population is `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'`, 834 files.

### spike-1 (retrieved, not re-derived): does the basename-to-marker coupling silently mistag files?

- **Assumption**: "Keeping the original basename as a prefix preserves the marker."
- **Method**: retrieved verbatim from the #2879 lane. The prototype and its finding are recorded
  at `docs/archive/plans-completed/split-remaining-large-test-files.md:87-98`; PR #3005's review
  thread replays it over all 24 new basenames and records the insertion indices it turns on
  (`bridge`=0, `routing`=8, `sdlc`=15, `lifecycle`=32, `config`=45, `worktree_manager`=63).
- **Finding**: **Sufficient, but not automatically.** `FEATURE_MAP` is iterated in insertion order
  and breaks on first hit, so a generic key placed early beats a specific key placed late.
  `test_worktree_manager_config.py` tags `config`, not `git`. `test_worktree_manager_lifecycle.py`
  tags `sessions`, not `git`. Both silently, with the collection total unchanged.
- **Confidence**: high (mechanically verified in the originating lane, and the algorithm re-run
  here reproduces it).
- **Impact on plan**: this is the guard's primary target. It also establishes that "intended
  marker" must come from a source outside the basename, because the basename is the thing that
  lies. The package directory is that source.

### spike-2: is "every test file must resolve to some marker" a viable rule?

- **Assumption**: "Most test files already carry a marker, so requiring one needs a short exemption list."
- **Method**: replay the resolver over all 834 tracked test files, count `None` results.
- **Finding**: **Not viable.** 552 of 834 files (66%) resolve to no marker at all. A
  must-be-marked rule would need a 552-entry exemption list on day one, which is a manifest
  pretending to be a guard.
- **Confidence**: high.
- **Impact on plan**: the guard asserts *consistency* of the marker a file resolves to, never
  *presence*. Unmarked files are only a violation when a sibling or a parent directory declares
  an intent they contradict.

### spike-3: does the package directory work as the declaration of intent?

- **Assumption**: "A test file inside `tests/**/{pkg}/` intends the marker that `{pkg}` itself resolves to."
- **Method**: partition the 834 files by parent directory, excluding the known roots
  (`tests`, `unit`, `integration`, `e2e`, `tools`, `performance`, `ai_judge`); resolve each
  directory name and each basename; compare.
- **Finding**: **Yes, and it fires on real defects today.** 80 files sit in 11 package
  directories. Seven of those directories have a name that resolves (`tests/unit/bridge`,
  `tests/unit/reflections`, `tests/integration/reflections`, `tests/unit/sdlc_router_decision`,
  `tests/unit/sdlc_session_ensure`, `tests/unit/valor_telegram`, `tests/unit/worktree_manager`),
  covering 47 files, of which **21 disagree with their directory**: 18 of the 19 files in
  `tests/unit/reflections/` (only `test_reflection_*`-style names already resolve correctly), both
  files in `tests/integration/reflections/`, and `tests/unit/bridge/test_dispatch.py`.
  The four packages produced by #2879 and #2941 whose directory name resolves
  (`sdlc_router_decision`, `sdlc_session_ensure`, `valor_telegram`, `worktree_manager`) are
  **100% consistent**, which is PR #3005's 24/24 claim reproduced independently.
  The four packages whose directory name does not resolve (`hooks`, `memory_extraction`,
  `output_handler`, `session_runner`) declare no intent, so a second rule is needed for them:
  sibling uniformity. Under that rule `memory_extraction` and `output_handler` are uniform (all
  `None`) and pass, while `hooks` and `session_runner` each have exactly one odd file out.
- **Confidence**: high.
- **Impact on plan**: produces rules R1 (directory intent) and R2 (sibling uniformity), and the
  21-entry pre-existing baseline they must be introduced against.

### spike-4: is there a rule that catches mistags with no declaration of intent at all?

- **Assumption**: "Every mistag mechanism needs an external statement of what was intended."
- **Method**: implement a whole-token variant of the resolver (split the stem on `_`, require the
  pattern's tokens to appear as a contiguous run) and diff its result against the shipped
  substring resolver across all 834 files.
- **Finding**: **Yes, one exists and it is cheap.** Substring and whole-token disagree on only
  **16 of 834 files**. Thirteen of those are the benign `reflection`-matching-`reflections`
  plural (`test_reflections_main.py`, `test_update_reflections_yaml.py`,
  `test_ui_reflections_data.py` and ten more), where both spellings map to the same `reflections`
  marker and nothing is actually mistagged. The remaining **three are genuine fragment matches**:
  `test_pm_briefings_no_slots_configured.py` (`config` inside `configured`, tagged `config`),
  `test_youtube_transcription.py` (`transcript` inside `transcription`, tagged `messaging`), and
  `test_long_task_checkpointing.py` (`checkpoint` inside `checkpointing`, tagged `validation`).
  Adding `"reflections": "reflections"` immediately before the existing `"reflection"` key erases
  the entire benign class with provably zero collateral: any stem containing the token
  `reflections` already contained the substring `reflection`, both keys map to the same marker,
  and inserting directly before `"reflection"` cannot jump ahead of any key that already won.
  Adding `"youtube": "tools"` immediately before `"transcript"` corrects the one genuine fragment
  match whose right answer is unambiguous, and the before/after diff confirms
  `test_youtube_transcription.py` is the only file affected.
- **Confidence**: high.
- **Impact on plan**: produces rule R3 (whole-token match), which applies suite-wide and needs no
  intent declaration, plus two in-scope zero-collateral `FEATURE_MAP` additions.

### spike-5: what does making the package directory authoritative actually cost?

- **Assumption**: "Fixing the 21 pre-existing violations in the same change is cheap."
- **Method**: simulate a directory-authoritative resolver (directory name wins when it resolves,
  basename otherwise) plus the two key additions, and diff marker assignment for all 834 files.
- **Finding**: **The fix is attractive but it cannot ship with the guard.** The simulation gains
  39 markers, corrects 6, and loses 0. But it makes rule R1 tautological: if the effective marker
  is taken from the directory, then "the effective marker equals the directory's marker" is true
  by construction and the guard asserts nothing. The renaming alternative also fails, because
  `config` sits at index 45 and `reflection` at 52, so a file renamed to
  `test_reflections_pm_briefings_no_slots_configured.py` still resolves to `config` first. The
  ordering trap that motivates the guard also blocks the obvious remedy.
- **Confidence**: high.
- **Impact on plan**: the resolver stays unchanged, the 21 violations enter a path-keyed baseline
  with a per-entry reason, and the resolver question is filed separately as #3175 so it can be
  decided on evidence the guard will then be able to produce.


## Data Flow

There are two consumers of one resolution function. Today the function exists only as an inlined
loop inside the pytest hook, which is why nothing else can check it.

**Path A, marker assignment (existing, at collection time):**

1. **Entry point**: `pytest` collects an item; `pytest_collection_modifyitems` runs
   (`tests/conftest.py:1206`).
2. **Stem extraction**: `item.nodeid` is split on `::`, the last path segment taken, `test_`
   and `.py` stripped.
3. **Resolution**: the stem is substring-matched against `FEATURE_MAP` in insertion order, first
   hit wins, no hit means no marker.
4. **Output**: `item.add_marker(getattr(pytest.mark, marker_name))`. Consumed later by
   `pytest -m <marker>`.

**Path B, the guard (new, at test time and in CI):**

1. **Entry point**: `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'` from the repo root,
   so untracked scratch files are invisible and a deleted file cannot leave a stale expectation.
2. **Partition**: each path is split into (package directory, basename). A parent directory in
   `KNOWN_ROOT_DIRS` means "not a package", so the file is covered by R3 only.
3. **Resolution**: the **same** `resolve_marker()` used by Path A step 3, returning both the
   marker and the pattern that matched, because R3 needs the pattern and not just the result.
4. **Rules**: R1 compares the basename's marker to the package directory name's marker; R2
   compares each package's basenames to one another; R3 re-matches the winning pattern at
   `_`-delimited token granularity.
5. **Baseline subtraction**: paths present in `KNOWN_MISTAGS` are removed from the violation set,
   and separately the baseline is checked for entries that no longer correspond to a violation.
6. **Output**: an assertion failure naming each offending path, its resolved marker, its expected
   marker, and the pattern that caused it.

The single point of truth is step 3. Path A and Path B must call the same function or the guard
degrades into a second implementation that can drift away from the thing it is guarding.


## Architectural Impact

- **New dependencies**: none. `tests/marker_map.py` is standard library only (`os`, `pathlib`,
  `subprocess`, `argparse`). It deliberately does not import `pytest`, so the CI job and the
  pre-commit path can run it on a bare interpreter with no venv.
- **Interface changes**: `FEATURE_MAP` moves from `tests/conftest.py` to `tests/marker_map.py` and
  `tests/conftest.py` imports it back. `git grep FEATURE_MAP` confirms `tests/conftest.py` is the
  only importer today; the ten other hits are docstring prose in split test modules. The
  inlined resolution loop in `pytest_collection_modifyitems` is replaced by a call to
  `resolve_marker()`, preserving behavior exactly.
- **Coupling**: decreases. Marker resolution becomes a named, importable, testable function
  instead of four lines buried in a collection hook that only pytest can reach.
- **Data ownership**: unchanged. `FEATURE_MAP` remains test-suite-owned and lives under `tests/`.
  It is not promoted into `tools/`, where a pytest marker table has no business.
- **Reversibility**: high. Deleting the guard file, the workflow, and moving the dict back is a
  clean revert with no data or state to unwind.


## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (one open question, on whether the GitHub Actions workflow is wanted)
- Review rounds: 1-2

The code is small: one new stdlib module, one new test file, a four-line change to a collection
hook, and a short workflow. The Medium sizing is entirely alignment cost. The guard is introduced
against 24 pre-existing violations, and how those are dispositioned is a judgement call a reviewer
will and should push on.


## Prerequisites

No external prerequisites. The work touches only the test suite and a workflow file, needs no
secrets, no services, and no Redis.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv on the committed pin | `python -m tools.doctor` | Running `scripts/pytest-clean.sh` at all |
| `git ls-files` reachable from the repo root | `git -C . ls-files 'tests/**/test_*.py' \| head -1` | The guard enumerates its population from git, not the filesystem |
| Standard library only for the audit module | `python -c "import ast,sys; m=ast.parse(open('tests/marker_map.py').read()); mods={a.name.split('.')[0] for n in ast.walk(m) if isinstance(n,ast.Import) for a in n.names} \| {n.module.split('.')[0] for n in ast.walk(m) if isinstance(n,ast.ImportFrom) and n.module}; assert mods <= set(sys.stdlib_module_names), mods"` | The CI job runs on a bare interpreter |


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
