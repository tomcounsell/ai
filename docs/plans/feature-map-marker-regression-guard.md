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
| Test suite is tracked in git | `git ls-files tests/conftest.py` | The guard enumerates its population from the git index, not the filesystem, so an untracked checkout would make it pass vacuously |
| The tests directory is an importable package | `test -f tests/__init__.py` | `tests/conftest.py` imports `tests.marker_map`; without the package marker that import fails at collection |
| The pytest wrapper is present | `test -x scripts/pytest-clean.sh` | Bare `pytest` is forbidden in this repo |


## Solution

### Key Elements

- **`tests/marker_map.py`** (new): the single home for `FEATURE_MAP`, the resolution function, the
  three rules, and the path-keyed baseline. Standard library only, no `pytest` import, runnable as
  `python tests/marker_map.py --audit` on a bare interpreter.
- **`tests/conftest.py`** (changed): imports `FEATURE_MAP` and `resolve_marker` from the new module
  and calls the function instead of re-inlining the loop. Behavior is identical; the point is that
  the guard and the collection hook can no longer disagree.
- **`tests/unit/test_feature_map_markers.py`** (new): the pytest face of the audit, plus a
  self-mutation test that proves the audit reports a synthetic mistag. Without that test the guard
  can pass while reaching nothing.
- **`.github/workflows/feature-map-guard.yml`** (new): runs the audit on `pull_request`. This is
  what makes the issue's "runs in CI on every PR" criterion literally true rather than
  aspirationally true.

### Flow

Author renames or splits a test file → pushes the branch → **GitHub Actions runs the audit in a
few seconds with no venv, no Redis, no dependencies** → a mistag fails the check with the path,
the marker it landed on, the marker its package declares, and the `FEATURE_MAP` key responsible →
author renames the file, adjusts `FEATURE_MAP`, or adds a reasoned `KNOWN_MISTAGS` entry → green.

The same audit also runs as an ordinary unit test, so `scripts/pytest-clean.sh tests/unit/` and the
SDLC TEST stage cover it too. Two surfaces, one implementation.

### Technical Approach

**The resolution function.** `resolve_marker(basename) -> tuple[str | None, str | None]` returns
both the marker and the `FEATURE_MAP` key that produced it. Rule R3 needs the key, not just the
result, so returning only the marker would force a second implementation.

**Rule R1, directory intent.** For a test file whose parent directory is not in
`KNOWN_ROOT_DIRS` (`tests`, `unit`, `integration`, `e2e`, `tools`, `performance`, `ai_judge`):
if the parent directory *name* resolves to marker M, the file's basename must resolve to M. The
directory is a declaration of intent that the basename cannot forge, which is what makes this rule
non-tautological. It catches the ordering-collision mechanism, which is spike-1's finding.

**Rule R2, sibling uniformity.** For a package directory whose own name does not resolve: every
test file in it must resolve to the same marker, `None` included. This catches a single file
drifting away from its package when the package has no name-based intent to compare against.
`tests/unit/session_runner/test_schema_routing.py` picking up `messaging` from the `routing` key,
alone among eighteen siblings, is exactly this shape.

**Rule R3, whole-token match.** Suite-wide, with no intent declaration required: the winning
`FEATURE_MAP` key must appear in the stem as a contiguous run of `_`-delimited tokens, not as a
fragment inside a longer word. This catches the second mechanism, which R1 and R2 cannot see.

**Exemptions, keyed by path.** `KNOWN_MISTAGS: dict[str, str]` maps a repo-relative POSIX path to a
prose reason, and `EXEMPT_DIRS: dict[str, str]` does the same for a whole package. Nothing is keyed
by line number, index, or ordinal position, per #2805. Two assertions bracket the baseline:

1. `violations - KNOWN_MISTAGS.keys()` must be empty. No new mistags.
2. `KNOWN_MISTAGS.keys() - violations` must be empty. **No stale exemptions.** This is the #3031
   lesson: an exemption that no longer corresponds to a real violation is a silent hole, so fixing
   a file makes the guard demand its baseline entry be deleted. The baseline can only shrink, and
   it cannot rot.

**The baseline as measured at `f3594dd23`: 24 distinct paths.** 21 from R1 (18 in
`tests/unit/reflections/`, 2 in `tests/integration/reflections/`, `tests/unit/bridge/test_dispatch.py`),
2 from R2 (`tests/unit/session_runner/test_schema_routing.py`,
`tests/unit/hooks/test_pre_tool_use_foreground_subagents.py`), and 1 further from R3
(`tests/unit/test_long_task_checkpointing.py`; `test_pm_briefings_no_slots_configured.py` also
violates R3 but is already counted under R1). Each entry carries its own reason string. Draining
the baseline is #3175.

**Two in-scope `FEATURE_MAP` additions**, both measured to change no file's marker except the one
intended and to lose no marker anywhere:

- `"reflections": "reflections"` inserted immediately before `"reflection"`. Clears 13 benign R3
  divergences. Zero files change marker.
- `"youtube": "tools"` inserted immediately before `"transcript"`. Corrects
  `test_youtube_transcription.py` from `messaging` to `tools`. One file changes marker, none lose one.

**Demonstrated red.** Two independent proofs, because a passing suite proves nothing:

1. A committed test, `test_audit_reports_a_synthetic_mistag`, that runs the rule functions over a
   synthetic file list containing a deliberately mistagged path and asserts the violation is
   reported with the right path, marker, and key. This keeps the guard honest after every future
   refactor, not just on the day it lands.
2. A manual red/green transcript pasted into the PR body: rename a real file into a mistag, run the
   audit, capture the failure output, restore the name, run again, capture green. Per the repo's
   mutation-check habit, do this once per rule (R1, R2, R3), not once overall, since one mutation
   can leave two of the three rules untouched.


## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] The audit has exactly two failure modes that must be loud rather than silent, and both get a
      test: (a) `FEATURE_MAP` cannot be imported or is not a non-empty dict, and (b) the
      `git ls-files` enumeration returns zero test files. Either condition means the audit is
      reaching nothing, so it must raise rather than report "no violations". A guard that passes
      vacuously is worse than no guard.
- [ ] No `except Exception: pass` blocks are introduced. `subprocess.run` for `git ls-files` uses
      `check=True` so a git failure surfaces instead of yielding an empty list.

### Empty/Invalid Input Handling

- [ ] `resolve_marker("")` and `resolve_marker("test_.py")` return `(None, None)` without raising.
- [ ] A basename with no underscores, and a basename that is exactly a `FEATURE_MAP` key, both
      resolve correctly under the whole-token comparison (the single-token case is the boundary
      where a naive tokenizer goes wrong).
- [ ] A package directory containing exactly one test file passes R2 trivially rather than raising.
- [ ] `KNOWN_MISTAGS` containing a path that is no longer tracked fails rule 2 above with a message
      naming the stale path, instead of being ignored.

### Error State Rendering

- [ ] The assertion message lists every violating path with its resolved marker, expected marker,
      and responsible `FEATURE_MAP` key, one per line, sorted. A guard whose failure message does
      not say what to change gets exempted rather than fixed.
- [ ] The message ends with the exact remediation options: rename the file, reorder or extend
      `FEATURE_MAP`, or add a `KNOWN_MISTAGS` entry with a reason.


## Test Impact

- [ ] `tests/conftest.py::pytest_collection_modifyitems`. UPDATE: replace the inlined four-line
      resolution loop with a call to `resolve_marker()`, and import `FEATURE_MAP` from
      `tests/marker_map.py` rather than defining it in place. Behavior must be byte-identical in
      effect; verified by comparing `pytest --collect-only -q -m <marker>` counts for every marker
      before and after.
- [ ] Marker assignment for two files changes by design and any test asserting the old value must
      follow: `tests/unit/test_youtube_transcription.py` moves from `messaging` to `tools`. A
      `git grep -n "youtube" tests/` sweep confirms no test asserts its marker today, but the
      builder re-checks rather than trusting this line.
- [ ] No existing test file is renamed, moved, or deleted by this work. The 24 baseline files are
      recorded, not touched.
- [ ] `tests/unit/test_no_legacy_paths.py`. NO CHANGE: read as a precedent for the exemption
      shape, not modified.


## Rabbit Holes

- **Redesigning the resolution algorithm.** Substring-plus-insertion-order is the root defect, and
  replacing it with explicit per-file declarations or directory-authoritative resolution is
  tempting the moment you see the numbers. spike-5 shows it also makes rule R1 tautological. It is
  #3175, and it wants the guard to exist first so it can prove its own before/after.
- **A golden manifest of all 834 files.** Checking in `path -> marker` for the whole suite catches
  every change, needs no rules, and needs no exemption list. It also blesses all 552 currently
  unmarked files as correct, adds a required manifest edit to every new test file, and turns a
  rename into a large diff. The rule-based guard says something true about correctness; a manifest
  only says "this changed".
- **Fixing the 21 reflections and bridge files inside this PR.** It is one line if the resolver
  changes and roughly nine hand-ordered `FEATURE_MAP` keys if it does not, and either way it makes
  the diff about the fix rather than the guard. #3175.
- **Registering markers with `--strict-markers`.** Adjacent, real, and a different problem.
- **Extending the rules to test *functions* or classes.** `FEATURE_MAP` is keyed on module
  basename. Nothing below the module is in scope.
- **Making the audit walk the filesystem instead of `git ls-files`.** Tempting because it removes a
  subprocess. It also picks up untracked scratch files, worktrees, and `__pycache__`, and it makes
  the guard's population depend on whatever happens to be lying around.


## Risks

### Risk 1: The guard is introduced with 24 exemptions and reads as theater

**Impact:** A reviewer reasonably asks what a guard is worth when its first act is to bless 24
violations, including the exact class it was built to catch.
**Mitigation:** The baseline shrinks only, never grows, and rule 2 forces stale entries out. Every
entry carries a prose reason, not a bare path. The drain is filed as #3175 with its own acceptance
criteria rather than promised in a comment. And the guard's value is forward-looking by design:
the four packages #2879 and #2941 created that declare intent are 100% consistent today, so the
guard's real job is keeping the next split honest, not relitigating old ones.

### Risk 2: Moving `FEATURE_MAP` out of `tests/conftest.py` breaks collection

**Impact:** If `tests.marker_map` is not importable from `tests/conftest.py`, every test run dies
at collection, which is a maximally loud failure but a wasted cycle.
**Mitigation:** `tests/__init__.py` exists, so `tests` is a real package and `tests/conftest.py` is
a module inside it. `git grep FEATURE_MAP` confirms no importer outside `tests/conftest.py`; the
ten other hits are docstring prose. The builder verifies with a full
`scripts/pytest-clean.sh tests/unit/ -q` before opening the PR, not just a targeted run.

### Risk 3: Rule R2 is too strict for legitimately mixed packages

**Impact:** A package that deliberately holds tests of two different features fails sibling
uniformity and the author reaches for an exemption, eroding the rule.
**Mitigation:** R2 only applies where the directory name does *not* resolve, which is the case
precisely when nobody has declared what the package is about. `EXEMPT_DIRS` takes a whole package
out by path with a reason. If more than one or two packages need it, R2 is the wrong rule and
should be dropped rather than exempted into meaninglessness; the builder reports that rather than
papering over it.

### Risk 4: The new workflow is the repo's first pytest-adjacent CI job

**Impact:** Adding GitHub Actions to a repo that deliberately runs its tests locally could be
unwanted, and could invite "why not run the whole suite here" pressure later.
**Mitigation:** The job runs one stdlib-only script in seconds with no secrets, no venv, and no
services, so it sets no precedent about running the suite. It is also the only way to satisfy the
issue's fourth acceptance criterion as written. Raised as the single Open Question so the call is
made deliberately, and the guard still functions as a unit test if the answer is no.

### Risk 5: The audit passes vacuously

**Impact:** If `git ls-files` returns nothing (wrong cwd, a bare checkout), the audit finds zero
violations and reports success, which is the worst possible failure for a guard.
**Mitigation:** The audit raises when the enumeration is empty or `FEATURE_MAP` is empty, covered
by a test. The verification table asserts a non-zero file count, not just a zero violation count.


## Race Conditions

No race conditions identified. The audit is synchronous, single-threaded, and reads only the git
index and the file system. It performs no writes, holds no locks, and touches no Redis, no
network, and no shared state. Under `pytest-xdist` the guard test runs on a single worker and is
read-only, so concurrent workers cannot interfere with it or with each other through it.


## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3175] Draining the 24-entry `KNOWN_MISTAGS` baseline: fixing the 18
  `tests/unit/reflections/` files, the 2 `tests/integration/reflections/` files,
  `tests/unit/bridge/test_dispatch.py`, `tests/unit/session_runner/test_schema_routing.py`,
  `tests/unit/hooks/test_pre_tool_use_foreground_subagents.py`, and
  `tests/unit/test_long_task_checkpointing.py`. Filed with its own measurements and acceptance
  criteria, including the requirement that no file loses a marker.
- [SEPARATE-SLUG #3175] Changing `pytest_collection_modifyitems` to make the package directory
  authoritative over the basename. spike-5 measured it (39 markers gained, 6 corrected, 0 lost) and
  it is the most promising remedy, but shipping it alongside the guard makes rule R1 tautological.
- [SEPARATE-SLUG #3175] Assigning markers to the four packages whose directory name is not a
  `FEATURE_MAP` key (`hooks`, `memory_extraction`, `output_handler`, `session_runner`, 33 files,
  all currently unmarked and internally uniform). That is new tagging policy, not regression
  prevention. They pass rule R2 as they stand.
- Not deferred, done here: the two `FEATURE_MAP` key additions, the extraction of `resolve_marker`,
  the guard, the synthetic-mistag test, the workflow, and the documentation.


## Update System

No update system changes required. This work adds one test-suite module, one test file, one
workflow file, and edits `tests/conftest.py`. There are no new dependencies, no config files, no
secrets, no services, and no Popoto models, so there is no migration and nothing to register in
`scripts/update/migrations.py`. `/update` propagates it as an ordinary commit and the guard starts
running on the next test invocation on each machine.


## Agent Integration

No agent integration required. The guard is test-suite infrastructure with no runtime surface. It
adds no CLI entry point to `pyproject.toml [project.scripts]`, the bridge does not import it, and
no MCP server exposes it. The agent reaches it the same way it reaches every other test, by running
`scripts/pytest-clean.sh`.

`tests/marker_map.py` is directly runnable (`python tests/marker_map.py --audit`) for the CI job's
benefit, which incidentally makes it available to an agent via the Bash tool, but that is a
consequence of being a plain script rather than an integration point that needs wiring or a test.


## Documentation

### Feature Documentation

- [ ] Create `docs/features/feature-map-marker-guard.md`: the two mistag mechanisms with the live
      examples, the three rules and what each one can and cannot see, why exemptions are keyed by
      path (#2805) and why stale exemptions are themselves a failure (#3031), and how to respond
      when the guard goes red.
- [ ] Add a row for it to the `docs/features/README.md` index table, keeping the table's sort order
      (enforced by `.claude/hooks/validators/validate_features_readme_sort.py`).

### Inline Documentation

- [ ] `tests/marker_map.py` module docstring states that it is the single source of marker
      resolution and must stay import-light so the CI job can run it on a bare interpreter.
- [ ] Every `KNOWN_MISTAGS` and `EXEMPT_DIRS` entry carries a prose reason as its value. A bare
      path with no reason is not an acceptable entry.

### Test Suite Index

- [ ] Update `tests/README.md`. Its existing split procedure (around lines 495 to 566) tells authors
      to check each new basename against `FEATURE_MAP` by hand; replace that manual step with the
      command that runs the audit, and add the worked ordering example the #2879 review asked for
      (`worktree_manager` sitting after `config`) alongside the fragment-match example
      (`config` inside `configured`). Add `tests/marker_map.py` to the file's index of
      test-infrastructure locations, which currently names `tests/conftest.py` as the home of
      `FEATURE_MAP`.


## Success Criteria

- [ ] `tests/unit/test_feature_map_markers.py` exists and fails when a test file is mistagged,
      proven by a committed synthetic-mistag test and by a manual red/green transcript covering
      each of R1, R2, and R3 separately.
- [ ] `python tests/marker_map.py --audit` exits 0 on a clean tree and non-zero on a mistag, using
      only the standard library.
- [ ] No exemption in the guard is keyed by line number, index, or ordinal position. Every
      `KNOWN_MISTAGS` key is a repo-relative path that `git ls-files` currently returns.
- [ ] Stale exemptions fail the guard: deleting a real violation without deleting its baseline
      entry turns the guard red.
- [ ] `.github/workflows/feature-map-guard.yml` runs the audit on `pull_request`, so the guard
      gates every PR. (Subject to the Open Question.)
- [ ] Marker resolution has exactly one implementation. The inlined loop is gone from
      `tests/conftest.py`.
- [ ] No test file loses a marker it had at `f3594dd23`. `test_youtube_transcription.py` moves from
      `messaging` to `tools` and is the only intended change.
- [ ] Full `scripts/pytest-clean.sh tests/unit/ -q` is green.
- [ ] Documentation updated (`docs/features/feature-map-marker-guard.md`, the features index, and
      `tests/README.md`).


## Team Orchestration

Small surface, one builder, one reviewer who is explicitly tasked with mutation-checking rather
than reading. The mutation check is the whole point of this work, so it does not get folded into a
general review pass.

### Team Members

- **Builder (guard)**
  - Name: `marker-guard-builder`
  - Role: extract `resolve_marker`, write `tests/marker_map.py`, the guard test, the two
    `FEATURE_MAP` additions, and the workflow.
  - Agent Type: builder
  - Resume: true

- **Validator (mutation)**
  - Name: `marker-guard-mutator`
  - Role: prove each rule bites. For R1, R2, and R3 separately, introduce a violation, confirm the
    guard reports it with the right path and reason, restore, confirm green. Then confirm the
    stale-exemption assertion by deleting a violation without its baseline entry.
  - Agent Type: validator
  - Resume: true

- **Validator (no-regression)**
  - Name: `marker-parity-validator`
  - Role: prove marker assignment did not change except for the one intended file. Compare
    `--report` output against the same report generated from `f3594dd23`.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `marker-guard-documentarian`
  - Role: `docs/features/feature-map-marker-guard.md`, the features index row, and the
    `tests/README.md` split-procedure rewrite.
  - Agent Type: documentarian
  - Resume: true


## Step by Step Tasks

### 1. Extract marker resolution into `tests/marker_map.py`

- **Task ID**: build-marker-map
- **Depends On**: none
- **Validates**: `tests/unit/test_feature_map_markers.py` (create)
- **Informed By**: spike-1 (ordering is the primary mechanism), spike-4 (`resolve_marker` must
  return the matched key, not only the marker)
- **Assigned To**: `marker-guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/marker_map.py` holding `FEATURE_MAP` moved verbatim from `tests/conftest.py:1106`,
  `KNOWN_ROOT_DIRS`, and `resolve_marker(basename) -> tuple[str | None, str | None]` returning the
  marker and the key that matched.
- Standard library only. No `pytest` import, no `tools.*` import, nothing that pulls in the venv.
- Add the two measured `FEATURE_MAP` entries: `"reflections": "reflections"` immediately before
  `"reflection"`, and `"youtube": "tools"` immediately before `"transcript"`.
- Rewrite `pytest_collection_modifyitems` in `tests/conftest.py` to import and call
  `resolve_marker`. Delete the inlined loop; do not leave it commented out.

### 2. Implement the audit rules and the baseline

- **Task ID**: build-audit
- **Depends On**: build-marker-map
- **Validates**: `tests/unit/test_feature_map_markers.py` (create)
- **Informed By**: spike-2 (presence rules are not viable, 552 unmarked files), spike-3 (R1 and R2
  and the 21-entry baseline), spike-4 (R3 and the 3 genuine fragment matches)
- **Assigned To**: `marker-guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `iter_test_files()` enumerating from `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'`
  with `check=True`, raising when the result is empty.
- Implement R1, R2, and R3 as separate functions each returning a list of violations carrying
  path, resolved marker, expected marker, matched key, and which rule fired.
- Add `KNOWN_MISTAGS: dict[str, str]` and `EXEMPT_DIRS: dict[str, str]`, path-keyed, every value a
  prose reason. Populate `KNOWN_MISTAGS` with the 24 measured paths. Nothing keyed by line number,
  index, or ordinal.
- Add the two bracketing assertions: no violation outside the baseline, and no baseline entry
  without a corresponding violation.
- Add a `__main__` block with `--audit`, `--report` (`path<TAB>marker` per line, `NONE` for
  unmarked), and `--count`, exiting non-zero on violations.

### 3. Write the guard test and its self-mutation proof

- **Task ID**: build-guard-test
- **Depends On**: build-audit
- **Validates**: `tests/unit/test_feature_map_markers.py` (create)
- **Informed By**: the repo's standing rule that a green test often reaches no code at all
- **Assigned To**: `marker-guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_feature_map_markers.py` calling the audit and asserting no violations,
  with a failure message listing each path, marker, expected marker, and key, sorted, plus the
  three remediation options.
- Add `test_audit_reports_a_synthetic_mistag`, which runs the rule functions over a synthetic file
  list containing a deliberately mistagged path and asserts the violation comes back with the
  right path, rule, and key. One case per rule.
- Add the vacuity tests: empty `FEATURE_MAP` raises, empty file enumeration raises,
  `resolve_marker("")` returns `(None, None)`.
- Add the stale-exemption test: a `KNOWN_MISTAGS` entry with no matching violation fails.

### 4. Add the CI workflow

- **Task ID**: build-workflow
- **Depends On**: build-audit
- **Validates**: `.github/workflows/feature-map-guard.yml` (create)
- **Informed By**: the Freshness Check finding that no workflow in this repo runs any test
- **Assigned To**: `marker-guard-builder`
- **Agent Type**: builder
- **Parallel**: true
- Create `.github/workflows/feature-map-guard.yml`, `on: pull_request`, ubuntu-latest,
  `actions/checkout@v4` with full history not required, `actions/setup-python@v5` pinned to the
  interpreter in `.python-version`, one step running `python tests/marker_map.py --audit`.
- No `pip install`, no secrets, no services. If the step needs a dependency, the module is wrong.
- Hold this task if the Open Question is answered "no workflow"; the guard still runs as a unit test.

### 5. Mutation-check every rule

- **Task ID**: validate-mutation
- **Depends On**: build-guard-test, build-workflow
- **Assigned To**: `marker-guard-mutator`
- **Agent Type**: validator
- **Parallel**: false
- Work in a worktree the builder is not editing; concurrent edits corrupt a mutation run in both
  directions.
- For R1: rename a file inside `tests/unit/worktree_manager/` to a basename that resolves
  elsewhere. Confirm red, capture output, restore, confirm green.
- For R2: rename a file inside `tests/unit/output_handler/` so it alone picks up a marker. Confirm
  red, capture, restore, confirm green.
- For R3: add a temporary `FEATURE_MAP` key that matches a fragment of an existing basename.
  Confirm red, capture, restore, confirm green.
- For the stale-exemption assertion: fix one baseline file without removing its entry. Confirm red,
  restore, confirm green.
- Report the four transcripts verbatim for the PR body. A rule that stays green under its own
  mutation is a finding, not a formality.

### 6. Prove no marker was lost

- **Task ID**: validate-parity
- **Depends On**: build-marker-map
- **Assigned To**: `marker-parity-validator`
- **Agent Type**: validator
- **Parallel**: true
- Generate `--report` on the branch. Generate the equivalent report from `f3594dd23` by replaying
  the old algorithm over the same file list.
- Diff them. The only permitted difference is `tests/unit/test_youtube_transcription.py` moving
  from `messaging` to `tools`. Any other line is a blocker.
- Independently confirm via `pytest --collect-only -q -m <marker>` counts for every marker in
  `FEATURE_MAP`, before and after.

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-mutation, validate-parity
- **Assigned To**: `marker-guard-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Write `docs/features/feature-map-marker-guard.md` and add its row to `docs/features/README.md`
  in sort order.
- Rewrite the `tests/README.md` split procedure (around lines 495 to 566) to call the audit instead
  of instructing a manual check, and add both worked examples: the ordering collision
  (`worktree_manager` after `config`) and the fragment match (`config` inside `configured`).
- Update the `tests/README.md` line naming `tests/conftest.py` as the home of `FEATURE_MAP`.

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: build-marker-map, build-audit, build-guard-test, build-workflow,
  validate-mutation, validate-parity, document-feature
- **Assigned To**: `marker-parity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and report each result.
- Run the full `scripts/pytest-clean.sh tests/unit/ -q`, not a targeted subset.
- Confirm every Success Criterion, including that the four mutation transcripts are in the PR body.


## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard test passes | `scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q` | exit code 0 |
| Audit runs on a bare interpreter | `python tests/marker_map.py --audit` | exit code 0 |
| Audit population is non-empty | `python tests/marker_map.py --count` | output > 800 |
| Audit module is standard library only | `python -c "import ast,sys; m=ast.parse(open('tests/marker_map.py').read()); mods={a.name.split('.')[0] for n in ast.walk(m) if isinstance(n,ast.Import) for a in n.names} \| {n.module.split('.')[0] for n in ast.walk(m) if isinstance(n,ast.ImportFrom) and n.module}; assert mods <= set(sys.stdlib_module_names), sorted(mods)"` | exit code 0 |
| Every exemption is a tracked path | `python -c "import subprocess; from tests.marker_map import KNOWN_MISTAGS, EXEMPT_DIRS; tracked=set(subprocess.run(['git','ls-files'],capture_output=True,text=True).stdout.split()); bad=[k for k in KNOWN_MISTAGS if k not in tracked]; assert not bad, bad"` | exit code 0 |
| Every exemption carries a reason | `python -c "from tests.marker_map import KNOWN_MISTAGS, EXEMPT_DIRS; bad=[k for k,v in list(KNOWN_MISTAGS.items())+list(EXEMPT_DIRS.items()) if not isinstance(v,str) or len(v.strip())<20]; assert not bad, bad"` | exit code 0 |
| No line-number keying (anti-criterion) | `grep -crE '\b(lineno\|line_number\|line_no)\b' tests/marker_map.py tests/unit/test_feature_map_markers.py` | match count == 0 |
| Resolution has one implementation (anti-criterion) | `grep -c 'for pattern, marker_name in FEATURE_MAP' tests/conftest.py` | match count == 0 |
| Resolver was not made directory-authoritative (anti-criterion) | `python -c "from tests.marker_map import resolve_marker; assert resolve_marker('test_pm_briefings_builder.py')[0] is None"` | exit code 0 |
| Baseline did not grow (anti-criterion) | `python -c "from tests.marker_map import KNOWN_MISTAGS; assert len(KNOWN_MISTAGS) <= 24, len(KNOWN_MISTAGS)"` | exit code 0 |
| youtube test retagged to tools | `python -c "from tests.marker_map import resolve_marker; assert resolve_marker('test_youtube_transcription.py')[0] == 'tools'"` | exit code 0 |
| No marker lost | `python tests/marker_map.py --report \| grep -vc 'NONE$'` | output > 281 |
| Workflow gates pull requests | `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/feature-map-guard.yml')); trig=d.get(True) or d.get('on'); assert 'pull_request' in trig, trig"` | exit code 0 |
| Feature doc exists | `test -f docs/features/feature-map-marker-guard.md` | exit code 0 |
| Feature doc indexed | `grep -c 'feature-map-marker-guard' docs/features/README.md` | output > 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Full unit suite green | `scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |

Note on the workflow row: GitHub Actions' `on:` key is parsed by PyYAML as the boolean `True`, not
the string `"on"`. The check reads both so it cannot pass vacuously on a `None` lookup.


## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Should this land a GitHub Actions workflow?** The issue's fourth acceptance criterion says the
   guard must run in CI on every PR. This repo has exactly one workflow
   (`.github/workflows/claude.yml`) and it only reacts to `@claude` mentions; nothing runs pytest
   in CI, and neither git hook runs tests either. The plan proposes a small
   `pull_request` workflow that runs one standard-library script in seconds with no venv, no
   secrets, and no services, because that is the only reading under which the criterion is
   literally true. The alternative is to treat the SDLC TEST stage as "CI" and ship the guard as an
   ordinary unit test only. Both are defensible; the first adds a GitHub Actions surface to a repo
   that has deliberately kept testing local. **Recommendation: add the workflow**, precisely because
   it costs nothing to run and needs no dependencies. Answer this before task 4.

