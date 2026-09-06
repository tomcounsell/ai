---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3010
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-06T08:46:22Z
---

# FEATURE_MAP Marker-Regression Guard

## Problem

`tests/conftest.py` auto-applies a pytest feature marker to every collected test by taking the
module basename, stripping `test_` and `.py`, and substring-matching the remainder against the
`FEATURE_MAP` dict, first hit wins (`tests/conftest.py:1106` for the map, `:1206` for the loop).
Nothing checks that the marker a file lands on is the marker anyone intended.

**Current behavior:**

A test file can be renamed, moved, or split and silently change marker. Three distinct mechanisms
produce this, all live in the repo today. This plan guards the first two and files the third.

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
3. **Mangled stem, out of scope here.** The hook derives the stem with a global `str.replace`, so
   a basename containing `test_` a second time has that occurrence eaten out of the middle of the
   name. `tests/tools/test_test_judge.py` yields the stem `judge` and
   `tests/unit/test_validate_test_impact.py` yields `validate_impact`, so both miss the
   `FEATURE_MAP` keys (`test_judge`, `validate_test_impact`) written for those exact files. Five
   basenames are mangled today. **Filed as #3184 and deliberately not fixed here**: correcting the
   strip moves two files from no marker to a marker, which contradicts this plan's own criterion
   that `test_youtube_transcription.py` is the only intended marker change. None of the three
   rules below can see this mechanism.

In every case the collection total is unchanged and the suite stays green, so the loss is
invisible. Measured against `f3594dd23`: 21 test files sitting inside a package directory whose
own name resolves to a marker do not carry that marker, and 17 of them carry no marker at all.
`pytest -m reflections` silently skips almost the entire `tests/unit/reflections/` package.

**What this guard reaches, and what it does not.** Rules R1 and R2 need a package directory to
compare against, so they run only on the 80 of 834 tracked test files (9.6%) that sit inside a
themed package: R1 reaches 47, R2 reaches 33. The other 754 files sit directly under a root
directory and are covered by R3 alone. R3 catches fragment matches suite-wide, and it is blind to
ordering collisions, because an ordering collision is by definition a genuine whole-token match
that belongs to the wrong key. Verified against `f3594dd23`: `test_worktree_manager_config.py`
placed directly under `tests/unit/` resolves to `config` via the key `config`; `config` is a
contiguous `_`-delimited token of `worktree_manager_config` so R3 passes; and the parent `unit` is
a known root so R1 and R2 never run. All three rules stay green on a genuine mistag.
**Ordering collisions are caught only inside a themed package directory.** Reaching the other 754
files would need a declaration of intent that does not exist for them, so the rules are not widened
here.

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
per-PR gate is the SDLC TEST stage running `scripts/pytest-clean.sh` locally, backed by the nightly
suite. Those are this repo's CI, so the Solution satisfies the criterion by shipping the guard as an
ordinary unit test that both of them run.

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
- **Finding**: **Not viable.** 554 of 834 files (66.4%) resolve to no marker at all; 280 resolve
  to a marker. A must-be-marked rule would need a 554-entry exemption list on day one, which is a
  manifest pretending to be a guard.
- **Confidence**: high. Re-derived independently during critique round 2. The earlier 552/282 pair
  in this plan was wrong: it is what a `removeprefix("test_")` stem produces, not what the shipped
  `str.replace("test_", "")` stem produces. The two divergent files are `tests/tools/test_test_judge.py`
  and `tests/unit/test_validate_test_impact.py`, which is mechanism 3 (#3184) showing up in the
  measurement. Every count in this plan is against the shipped stem.
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
  by construction and the guard asserts nothing. The renaming alternative is partial: prefixing
  each violating basename with its own package directory name fixes 19 of the 21 R1 violations,
  and the two that survive are `tests/unit/reflections/test_sdlc_progress_check.py` and
  `tests/unit/reflections/test_sdlc_upvote_lanes.py`, which still resolve to `sdlc` because `sdlc`
  sits at insertion index 15, ahead of `reflection` at 44. The ordering trap that motivates the
  guard also blocks the obvious remedy, on a smaller set than first stated.
- **Confidence**: high. Re-derived in critique round 2. An earlier draft of this spike and the body
  of #3175 both put `reflection` at index 52 and concluded the rename fails outright; that does not
  reproduce. `reflection` is at 44 and `config` at 45, so
  `test_reflections_pm_briefings_no_slots_configured.py` does resolve to `reflections`. The
  correction is recorded on #3175.
- **Impact on plan**: the resolver stays unchanged, the 21 violations enter a path-keyed baseline
  with a per-entry reason, and the resolver question is filed separately as #3175 so it can be
  decided on evidence the guard will then be able to produce.


## Data Flow

There are two consumers of one resolution function. Today the function exists only as an inlined
loop inside the pytest hook, which is why nothing else can check it.

**Path A, marker assignment (existing, at collection time):**

1. **Entry point**: `pytest` collects an item; `pytest_collection_modifyitems` runs
   (`tests/conftest.py:1206`).
2. **Stem extraction**: verbatim from `tests/conftest.py::pytest_collection_modifyitems`:
   ```python
   filename = item.nodeid.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")
   ```
   This is a **global** `str.replace`, not a prefix strip. It removes *every* occurrence of
   `test_`, which is mechanism 3 (#3184). `resolve_marker()` must reproduce this expression
   character for character. Using `removeprefix("test_")`, `removesuffix(".py")`, or an anchored
   regex is forbidden: it silently retags `tests/tools/test_test_judge.py` (none to `tools`) and
   `tests/unit/test_validate_test_impact.py` (none to `validation`), breaking the plan's
   byte-identical-behavior requirement and its only-one-intended-change criterion.
3. **Resolution**: the stem is substring-matched against `FEATURE_MAP` in insertion order, first
   hit wins, no hit means no marker.
4. **Output**: `item.add_marker(getattr(pytest.mark, marker_name))`. Consumed later by
   `pytest -m <marker>`.

**Path B, the guard (new, at test time):**

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
  `subprocess`, `argparse`). It deliberately does not import `pytest`, so it runs on a bare
  interpreter with no venv, which keeps `python tests/marker_map.py --audit` usable from any shell.
- **Interface changes**: `FEATURE_MAP` moves from `tests/conftest.py` to `tests/marker_map.py` and
  `tests/conftest.py` imports it back. `git grep FEATURE_MAP` confirms `tests/conftest.py` is the
  only importer today; the ten other hits are docstring prose in split test modules. The
  inlined resolution loop in `pytest_collection_modifyitems` is replaced by a call to
  `resolve_marker()`, preserving behavior exactly.
- **Coupling**: decreases. Marker resolution becomes a named, importable, testable function
  instead of four lines buried in a collection hook that only pytest can reach.
- **Data ownership**: unchanged. `FEATURE_MAP` remains test-suite-owned and lives under `tests/`.
  It is not promoted into `tools/`, where a pytest marker table has no business.
- **Reversibility**: high. Deleting the guard file and moving the dict back is a clean revert with
  no data or state to unwind.


## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 remaining (the one open question, on whether a GitHub Actions workflow was
  wanted, is answered and closed: unit test only)
- Review rounds: 1-2

The code is small: one new stdlib module, one new test file, and a four-line change to a collection
hook. The Medium sizing is entirely alignment cost. The guard is introduced against 24 pre-existing
violations, and how those are dispositioned is a judgement call a reviewer will and should push on.


## Prerequisites

No external prerequisites. The work touches only the test suite, needs no secrets, no services, and
no Redis.

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

### Flow

Author renames or splits a test file → runs the suite, or the SDLC TEST stage runs it for them →
the guard test goes red naming the path, the marker it landed on, the marker its package declares,
and the `FEATURE_MAP` key responsible → author renames the file, adjusts `FEATURE_MAP`, or adds a
reasoned `KNOWN_MISTAGS` entry → green.

The SDLC TEST stage and the nightly suite are this repo's CI, so an ordinary unit test in
`tests/unit/` is the per-PR gate. One implementation, reached through `scripts/pytest-clean.sh`.

### Technical Approach

**The resolution function.** `resolve_marker(basename) -> tuple[str | None, str | None]` returns
both the marker and the `FEATURE_MAP` key that produced it. Rule R3 needs the key, not just the
result, so returning only the marker would force a second implementation.

Its stem expression is copied verbatim from the shipped hook and must not be re-derived from prose:

```python
stem = basename.replace("test_", "").replace(".py", "")
```

`removeprefix`, `removesuffix`, and anchored regexes are forbidden here. They look like the same
thing and are not: they retag `tests/tools/test_test_judge.py` and
`tests/unit/test_validate_test_impact.py`, which is mechanism 3 and belongs to #3184.

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

**R2 attribution is part of the rule, not an implementation detail.** Group the package's files by
resolved marker. **The violating set is every sibling outside the largest group.** The majority is
the package's de facto intent; reporting all of them would make a one-file drift look like a
23-file problem. Measured at `f3594dd23`: `tests/unit/hooks/` splits 4 `None` against 1 `sdlc` and
`tests/unit/session_runner/` splits 17 `None` against 1 `messaging`, so majority attribution yields
**2** violations while a literal reading of "every file must match" yields **23** and would push
the baseline to 45.

**The tie branch is stated behavior, not an accident.** When two or more groups tie for largest
(a 2-vs-2 package, say), there is no majority to attribute against, so **report every file in the
package as ambiguous**, with a rule label distinguishing it from an ordinary R2 violation. Letting
dict-iteration order break the tie would make the guard's output depend on filesystem ordering,
which is the class of bug this whole plan exists to prevent. A synthetic 2-vs-2 fixture covers this
branch in the guard test, because Task 4's real-package mutation only ever exercises 1-vs-N.

**Rule R3, whole-token match.** Suite-wide, with no intent declaration required: the winning
`FEATURE_MAP` key must appear in the stem as a contiguous run of `_`-delimited tokens, not as a
fragment inside a longer word. This catches the second mechanism, which R1 and R2 cannot see.

**Exemptions, keyed by path.** `KNOWN_MISTAGS: dict[str, str]` maps a repo-relative POSIX path to a
prose reason. It is the only exemption mechanism. Nothing is keyed by line number, index, or
ordinal position, per #2805. Two assertions bracket the baseline:

1. `violations - KNOWN_MISTAGS.keys()` must be empty. No new mistags.
2. `KNOWN_MISTAGS.keys() - violations` must be empty. **No stale exemptions.** This is the #3031
   lesson: an exemption that no longer corresponds to a real violation is a silent hole, so fixing
   a file makes the guard demand its baseline entry be deleted. The baseline can only shrink, and
   it cannot rot.

There is deliberately **no whole-package exemption mechanism**. An earlier draft carried an
`EXEMPT_DIRS` dict, and the measured baseline populates zero entries in it, so it would ship as an
unexercised path with neither bracketing assertion applied to it. That is the #3031 hole
reintroduced inside the mechanism meant to close it. Risk 3's concern (a legitimately mixed
package) is handled by per-file `KNOWN_MISTAGS` entries; if a package ever needs more than a
couple, that is evidence R2 is the wrong rule and should be dropped rather than exempted into
meaninglessness.

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
- [ ] A package whose largest marker group ties (2-vs-2) reports every file as ambiguous rather
      than silently picking whichever group dict iteration reached first.

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
- [ ] `tests/tools/test_test_judge.py` and `tests/unit/test_validate_test_impact.py`. NO CHANGE,
      and their markers must NOT change either. Both currently resolve to no marker because of
      mechanism 3 (#3184). If either gains a marker, the builder re-derived the stem instead of
      copying it, and the parity control is compromised.


## Rabbit Holes

- **Redesigning the resolution algorithm.** Substring-plus-insertion-order is the root defect, and
  replacing it with explicit per-file declarations or directory-authoritative resolution is
  tempting the moment you see the numbers. spike-5 shows it also makes rule R1 tautological. It is
  #3175, and it wants the guard to exist first so it can prove its own before/after.
- **A golden manifest of all 834 files.** Checking in `path -> marker` for the whole suite catches
  every change, needs no rules, and needs no exemption list. It also blesses all 554 currently
  unmarked files as correct, adds a required manifest edit to every new test file, and turns a
  rename into a large diff. The rule-based guard says something true about correctness; a manifest
  only says "this changed".
- **Fixing the 21 reflections and bridge files inside this PR.** It is one line if the resolver
  changes, 19 renames plus two hand-ordered `FEATURE_MAP` keys if it does not, and either way it
  makes the diff about the fix rather than the guard. #3175.
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
precisely when nobody has declared what the package is about, and it accuses only the siblings
outside the largest marker group, so a deliberate minority costs one `KNOWN_MISTAGS` entry each
rather than the whole package. There is no whole-package exemption on purpose: an unexercised
`EXEMPT_DIRS` with no bracketing assertions is a silent hole. If a package ever needs more than a
couple of per-file entries, R2 is the wrong rule and should be dropped rather than exempted into
meaninglessness; the builder reports that rather than papering over it.

### Risk 4: The audit passes vacuously

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
  criteria, including the requirement that no file loses a marker. **Scope note:** #3175's title
  says 21 files. This plan hands it 24 paths, so the two R2 entries
  (`tests/unit/session_runner/test_schema_routing.py`,
  `tests/unit/hooks/test_pre_tool_use_foreground_subagents.py`) and
  `tests/unit/test_long_task_checkpointing.py` extend #3175's stated scope beyond its title. The
  reconciliation is recorded as a comment on #3175 so no baseline entry is left untracked.
- [SEPARATE-SLUG #3184] Correcting the global `test_` strip in the stem expression. Mechanism 3 in
  the Problem section: five basenames contain `test_` twice, and two of them
  (`tests/tools/test_test_judge.py`, `tests/unit/test_validate_test_impact.py`) lose a marker a
  `FEATURE_MAP` key was written for. Fixing it here would retag those two files, contradicting this
  plan's own criterion that `test_youtube_transcription.py` is the only intended marker change.
  This plan pins the buggy expression verbatim instead, so the guard measures the world as it is.
- [SEPARATE-SLUG #3175] Changing `pytest_collection_modifyitems` to make the package directory
  authoritative over the basename. spike-5 measured it (39 markers gained, 6 corrected, 0 lost) and
  it is the most promising remedy, but shipping it alongside the guard makes rule R1 tautological.
- [SEPARATE-SLUG #3175] Assigning markers to the four packages whose directory name is not a
  `FEATURE_MAP` key (`hooks`, `memory_extraction`, `output_handler`, `session_runner`, 33 files,
  all currently unmarked and internally uniform). That is new tagging policy, not regression
  prevention. They pass rule R2 as they stand.
- Not deferred, done here: the two `FEATURE_MAP` key additions, the extraction of `resolve_marker`,
  the guard, the synthetic-mistag test, and the documentation.


## Update System

No update system changes required. This work adds one test-suite module and one test file, and edits
`tests/conftest.py`. There are no new dependencies, no config files, no secrets, no services, and no
Popoto models, so there is no migration and nothing to register in
`scripts/update/migrations.py`. `/update` propagates it as an ordinary commit and the guard starts
running on the next test invocation on each machine.


## Agent Integration

No agent integration required. The guard is test-suite infrastructure with no runtime surface. It
adds no CLI entry point to `pyproject.toml [project.scripts]`, the bridge does not import it, and
no MCP server exposes it. The agent reaches it the same way it reaches every other test, by running
`scripts/pytest-clean.sh`.

`tests/marker_map.py` is directly runnable (`python tests/marker_map.py --audit`) so the audit can
be checked outside a pytest run, which incidentally makes it available to an agent via the Bash
tool, but that is a consequence of being a plain script rather than an integration point that needs
wiring or a test.


## Documentation

### Feature Documentation

- [ ] Create `docs/features/feature-map-marker-guard.md`: the three mistag mechanisms with the live
      examples (naming #3184 as the tracker for the third), the three rules and what each one can
      and cannot see, R2's majority attribution and its tie branch, why exemptions are keyed by
      path (#2805) and why stale exemptions are themselves a failure (#3031), and how to respond
      when the guard goes red.
- [ ] **Required line in that doc**, stated plainly rather than implied: R1 and R2 catch ordering
      collisions only inside a themed package directory, which is 80 of 834 tracked test files
      (9.6%). Suite-wide, only fragment matches are caught. A mistagged file sitting directly under
      `tests/unit/` passes all three rules. Give the worked example
      (`test_worktree_manager_config.py` at top level resolves to `config` and stays green).
- [ ] Add a row for it to the `docs/features/README.md` index table, keeping the table's sort order
      (enforced by `.claude/hooks/validators/validate_features_readme_sort.py`).

### Inline Documentation

- [ ] `tests/marker_map.py` module docstring states that it is the single source of marker
      resolution and must stay import-light so it runs on a bare interpreter with no venv.
- [ ] Every `KNOWN_MISTAGS` entry carries a prose reason as its value. A bare path with no reason
      is not an acceptable entry.
- [ ] `resolve_marker`'s stem line carries a comment pointing at #3184 and stating that the global
      `str.replace` is intentional fidelity to the shipped hook, so a future reader does not
      "clean it up" into `removeprefix` and silently retag two files.

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
- [ ] **The coverage boundary is stated, not implied.** R1 and R2 catch ordering collisions only
      inside a themed package directory (80 of 834 files, 9.6%; R1 reaches 47, R2 reaches 33).
      Suite-wide, only fragment matches are caught. This sentence appears in
      `docs/features/feature-map-marker-guard.md`, and the rules are not widened to close it.
- [ ] The stem expression is `basename.replace("test_", "").replace(".py", "")`, copied verbatim
      from the hook. `removeprefix`, `removesuffix`, and anchored regexes appear nowhere in
      `tests/marker_map.py`. Committed fixtures assert
      `resolve_marker("test_test_judge.py") == (None, None)` and
      `resolve_marker("test_validate_test_impact.py") == (None, None)`.
- [ ] R2 attributes violations to the siblings outside the largest marker group, and reports every
      file in the package as ambiguous when the largest group ties. Both branches have committed
      synthetic fixtures.
- [ ] No exemption in the guard is keyed by line number, index, or ordinal position. Every
      `KNOWN_MISTAGS` key is a repo-relative path that `git ls-files` currently returns. There is
      no whole-package exemption mechanism.
- [ ] Stale exemptions fail the guard: deleting a real violation without deleting its baseline
      entry turns the guard red.
- [ ] The issue's "runs in CI on every PR" criterion is met by the guard running as an ordinary
      unit test in `tests/unit/`, executed by the SDLC TEST stage and the nightly suite. Those are
      this repo's CI.
- [ ] Marker resolution has exactly one implementation. The inlined loop is gone from
      `tests/conftest.py`.
- [ ] No test file loses **or gains** a marker relative to `f3594dd23`. The marked-file count is
      exactly 280 before and after; `test_youtube_transcription.py` moving from `messaging` to
      `tools` is the only marker change, and it is count-neutral.
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
  - Role: extract `resolve_marker`, write `tests/marker_map.py`, the guard test, and the two
    `FEATURE_MAP` additions.
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
- **Pin the stem expression by copying it out of the hook, not by re-deriving it from this prose:**
  `stem = basename.replace("test_", "").replace(".py", "")`. It is a global `str.replace`.
  `removeprefix`, `removesuffix`, and anchored regexes are forbidden; each of them retags
  `tests/tools/test_test_judge.py` and `tests/unit/test_validate_test_impact.py` and breaks the
  byte-identical requirement. Comment the line with a pointer to #3184 so it survives future
  tidying.
- Add committed fixtures in the guard test: `resolve_marker("test_test_judge.py") == (None, None)`
  and `resolve_marker("test_validate_test_impact.py") == (None, None)`. These are the two files
  that move if the stem is wrong, so they are the cheapest possible tripwire.
- Standard library only. No `pytest` import, no `tools.*` import, nothing that pulls in the venv.
- Add the two measured `FEATURE_MAP` entries: `"reflections": "reflections"` immediately before
  `"reflection"`, and `"youtube": "tools"` immediately before `"transcript"`.
- Rewrite `pytest_collection_modifyitems` in `tests/conftest.py` to import and call
  `resolve_marker`. Delete the inlined loop; do not leave it commented out.

### 2. Implement the audit rules and the baseline

- **Task ID**: build-audit
- **Depends On**: build-marker-map
- **Validates**: `tests/unit/test_feature_map_markers.py` (create)
- **Informed By**: spike-2 (presence rules are not viable, 554 unmarked files), spike-3 (R1 and R2
  and the 21-entry baseline), spike-4 (R3 and the 3 genuine fragment matches)
- **Assigned To**: `marker-guard-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `iter_test_files()` enumerating from `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'`
  with `check=True`, raising when the result is empty.
- Implement R1, R2, and R3 as separate functions each returning a list of violations carrying
  path, resolved marker, expected marker, matched key, and which rule fired.
- R2 groups a package's files by resolved marker and accuses **every sibling outside the largest
  group**. On a tie for largest, accuse **every file in the package**, labelled ambiguous rather
  than as an ordinary R2 violation. A literal "every file must match" reading yields 23 violations
  instead of 2 and pushes the baseline to 45, tripping the `len(KNOWN_MISTAGS) <= 24`
  anti-criterion; that is the signal you implemented the wrong attribution.
- Add `KNOWN_MISTAGS: dict[str, str]` only, path-keyed, every value a prose reason. Populate it
  with the 24 measured paths. **Do not add `EXEMPT_DIRS` or any other whole-package exemption**;
  the baseline needs none and an unbracketed exemption mechanism is a silent hole.
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
- Add a synthetic **2-vs-2 package** fixture for R2's tie branch: assert all four files come back,
  labelled ambiguous, and that the result does not depend on the order the file list is given in
  (feed it twice, reversed, and assert set equality). Task 4's real-package mutation only ever
  exercises 1-vs-N, so this branch is untested without a fixture.
- Add the vacuity tests: empty `FEATURE_MAP` raises, empty file enumeration raises,
  `resolve_marker("")` returns `(None, None)`.
- Add the stale-exemption test: a `KNOWN_MISTAGS` entry with no matching violation fails.

### 4. Mutation-check every rule

- **Task ID**: validate-mutation
- **Depends On**: build-guard-test
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

### 5. Prove no marker was lost

- **Task ID**: validate-parity
- **Depends On**: build-marker-map, build-audit
- **Assigned To**: `marker-parity-validator`
- **Agent Type**: validator
- **Parallel**: true
- Generate `--report` on the branch. Generate the baseline report **from
  `git show f3594dd23:tests/conftest.py`**, parsing `FEATURE_MAP` out of that text with
  `ast.literal_eval` and applying the stem expression as it literally appears in that file. Do not
  import the branch's `tests/marker_map.py` and do not retype the stem from memory: a control that
  inherits the branch's stem confirms itself and comes back green on a broken implementation.
- Diff them. The only permitted difference is `tests/unit/test_youtube_transcription.py` moving
  from `messaging` to `tools`. Any other line is a blocker.
- Assert the marked-file count is **280 on both sides**, not merely non-decreasing. A one-sided
  floor cannot catch a marker being silently gained, which is what a wrong stem produces.
- Independently confirm via `pytest --collect-only -q -m <marker>` counts for every marker in
  `FEATURE_MAP`, before and after.

### 6. Documentation

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

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: build-marker-map, build-audit, build-guard-test,
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
| Every exemption is a tracked path | `python -c "import subprocess; from tests.marker_map import KNOWN_MISTAGS; tracked=set(subprocess.run(['git','ls-files'],capture_output=True,text=True).stdout.split()); bad=[k for k in KNOWN_MISTAGS if k not in tracked]; assert not bad, bad"` | exit code 0 |
| Every exemption carries a reason | `python -c "from tests.marker_map import KNOWN_MISTAGS; bad=[k for k,v in KNOWN_MISTAGS.items() if not isinstance(v,str) or len(v.strip())<20]; assert not bad, bad"` | exit code 0 |
| No whole-package exemption mechanism (anti-criterion) | `! grep -qF 'EXEMPT_DIRS' tests/marker_map.py tests/unit/test_feature_map_markers.py` | exit code 0 |
| No line-number keying (anti-criterion) | `! grep -qE '\b(lineno\|line_number\|line_no)\b' tests/marker_map.py tests/unit/test_feature_map_markers.py` | exit code 0 |
| Stem is the shipped global replace, not a prefix strip (anti-criterion) | `! grep -qE 'removeprefix\|removesuffix\|re\.(sub\|match)' tests/marker_map.py` | exit code 0 |
| Stem fidelity fixtures hold | `python -c "from tests.marker_map import resolve_marker; assert resolve_marker('test_test_judge.py')==(None,None); assert resolve_marker('test_validate_test_impact.py')==(None,None)"` | exit code 0 |
| Resolution has one implementation (anti-criterion) | `! grep -qF 'for pattern, marker_name in FEATURE_MAP' tests/conftest.py` | exit code 0 |
| Resolver was not made directory-authoritative (anti-criterion) | `python -c "from tests.marker_map import resolve_marker; assert resolve_marker('test_pm_briefings_builder.py')[0] is None"` | exit code 0 |
| Baseline did not grow (anti-criterion) | `python -c "from tests.marker_map import KNOWN_MISTAGS; assert len(KNOWN_MISTAGS) <= 24, len(KNOWN_MISTAGS)"` | exit code 0 |
| youtube test retagged to tools | `python -c "from tests.marker_map import resolve_marker; assert resolve_marker('test_youtube_transcription.py')[0] == 'tools'"` | exit code 0 |
| No marker lost or gained | `test "$(python tests/marker_map.py --report \| grep -vc 'NONE$')" -eq 280` | exit code 0 |
| Feature doc exists | `test -f docs/features/feature-map-marker-guard.md` | exit code 0 |
| Feature doc indexed | `grep -qF 'feature-map-marker-guard' docs/features/README.md` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Full unit suite green | `scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |


## Critique Results

**Critique run 2026-09-06** — FULL depth, independent roster of 3 critics (Risk & Robustness, Scope & Value, History & Consistency) plus automated structural checks. All structural checks pass: required sections present, tasks 1-7 with no numbering gaps, all `Depends On` references valid and acyclic, all referenced paths exist except the three this plan creates, all three prerequisites met, and every Success Criterion maps to a task.

**Verdict: NEEDS REVISION** — 4 blockers, 2 concerns, 2 nits.

**Revision applied 2026-09-06 (round 2).** All 8 rows closed; every Implementation Note was
independently re-derived against `f3594dd23` before being folded in, and all 8 reproduced. Two
further errors were found during that re-derivation and corrected in the same pass, neither of them
raised by the critique:

- The Problem section said 19 of the 21 R1 violations carry no marker. The true figure is **17**;
  four of them carry a wrong marker (`validation`, `config`, `sdlc`, `sdlc`).
- spike-5 and the body of #3175 both put `reflection` at insertion index 52 and concluded the
  rename remedy fails outright. `reflection` is at **44**, ahead of `config` at 45. The rename
  fixes 19 of 21; the two survivors lose to `sdlc` at index 15. The correction is posted on #3175.

Settled constraints re-affirmed and untouched by this revision: unit test only with nothing under
`.github/workflows/`; the 21-file R1 drain, the directory-authoritative resolver, and the four
unmarked packages all stay with #3175; exemptions keyed by path, never by line number or ordinal.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | History & Consistency; Risk & Robustness (Operator); structural replay | spike-2's "552 of 834 files (66%)" / 282 marked does not reproduce. Replaying the shipped stem expression against the real FEATURE_MAP at `f3594dd23` gives **554 unmarked / 280 marked**. The 552/282 pair is only produced by a `removeprefix("test_")` stem, which is not the shipped algorithm, so the plan's "fresh by construction" claim fails in this one detail. This propagates: the Verification row "No marker lost" expects `--report` piped to `grep -vc 'NONE$'` to exceed 281, satisfiable only at 282+, so that row **fails on a byte-identical implementation and passes only on an unfaithful one**. | **CLOSED.** spike-2 (554 unmarked / 280 marked, 66.4%, with the removeprefix artifact named); Rabbit Holes "all 554 currently unmarked files"; Task 2 Informed By; Success Criteria "No test file loses **or gains** a marker"; Verification row "No marker lost or gained" (`-eq 280`). Re-derived independently: 554/280 confirmed. | The two divergent files are `tests/tools/test_test_judge.py` and `tests/unit/test_validate_test_impact.py`. Correct the prose to 554 unmarked / 280 marked (66.4%) everywhere it appears (spike-2, Rabbit Holes' "blesses all 552 currently unmarked files"), and replace the `> 281` floor with an equality against 280: `test "$(python tests/marker_map.py --report \| grep -vc 'NONE$')" -eq 280`. Equality matters independently of the count: a one-sided floor cannot catch a marker being silently *gained*, which is the exact regression row 2 describes. Both FEATURE_MAP additions are marker-count-neutral (youtube swaps messaging to tools; reflections is a same-marker alias), so the expected count is unchanged at 280. |
| BLOCKER | Risk & Robustness (Skeptic); History & Consistency; structural replay | Data Flow step 2 and Task 1 describe the stem as "`test_` and `.py` stripped", but the shipped hook at `tests/conftest.py:1208` uses `str.replace`, which removes **every** occurrence of `test_`, not just the leading prefix. A builder implementing the prose as `removeprefix("test_")` changes marker assignment for two files (`test_test_judge.py` None to `tools`, `test_validate_test_impact.py` None to `validation`), silently violating the plan's own "Behavior must be byte-identical in effect" requirement and the Success Criterion naming `test_youtube_transcription.py` as the only intended change. Task 5's parity control replays "the old algorithm", so a builder who replays it with the same wrong stem gets a **self-confirming green**. | **CLOSED.** Data Flow step A2 (expression quoted verbatim, `removeprefix`/`removesuffix`/anchored regex forbidden); Technical Approach "The resolution function"; Task 1 (pin by copying, comment pointing at #3184, two committed fixtures); Task 5 (baseline generated from `git show f3594dd23:tests/conftest.py`); Test Impact row for the two files; Verification rows "Stem is the shipped global replace" and "Stem fidelity fixtures hold". | `resolve_marker` must compute the stem as `basename.replace("test_", "").replace(".py", "")` — never `removeprefix`/`removesuffix` and never an anchored regex. Pin it by copying the expression verbatim from the hook rather than re-deriving it from prose. Add committed fixtures to `tests/unit/test_feature_map_markers.py` asserting `resolve_marker("test_test_judge.py") == (None, None)` and `resolve_marker("test_validate_test_impact.py") == (None, None)`. Task 5's baseline report must be generated from `git show f3594dd23:tests/conftest.py` plus the literal expression, so the control cannot inherit the branch's stem bug. |
| BLOCKER | Scope & Value; structural replay | R1 and R2 — the only rules that catch the ordering-collision mechanism the plan calls "the guard's primary target" — fire only for files inside a themed package directory: **80 of 834 tracked test files (9.6%)**, R1 reaching 47 and R2 reaching 33. The other 754 files have no rule that can catch an ordering collision, because R3 agrees with the substring resolver whenever the winning key is a genuine whole-token match, which is precisely the ordering-collision shape. spike-1's own headline example passes all three rules cleanly if it sits at `tests/unit/` top level rather than inside a package. | **CLOSED, rules not widened.** Problem section "What this guard reaches, and what it does not" (80/834 = 9.6%, R1 47, R2 33, 754 files on R3 alone, with the `test_worktree_manager_config.py` top-level worked example); Success Criteria "The coverage boundary is stated, not implied"; Documentation required line in `docs/features/feature-map-marker-guard.md`. | Verified: `test_worktree_manager_config.py` placed directly under `tests/unit/` resolves to `config` via key `config`; `config` is a contiguous `_`-delimited token of `worktree_manager_config`, so R3 passes, and parent `unit` is in `KNOWN_ROOT_DIRS`, so R1 and R2 never run — all three rules green on a genuine ordering-collision mistag. Do not widen the rules here. State the boundary plainly in the Problem section, in Success Criteria, and in `docs/features/feature-map-marker-guard.md`: R1/R2 catch ordering collisions only inside a themed package directory; suite-wide, only fragment matches are caught. |
| BLOCKER | Risk & Robustness (Adversary); structural replay | R2 is specified as "every test file in it must resolve to the same marker, `None` included", but the stated baseline records only 2 R2 entries. A literal implementation reports **every** file in a non-uniform package: `tests/unit/hooks/` (5 files) and `tests/unit/session_runner/` (18 files) = 23 paths, making the baseline 21 + 23 + 1 = **45** and tripping the Verification anti-criterion `assert len(KNOWN_MISTAGS) <= 24`. The majority/minority attribution the 2-entry baseline silently depends on appears nowhere in the plan, and it has no defined tie-break for an evenly split package. | **CLOSED.** Technical Approach R2 "attribution is part of the rule" (violating set = every sibling outside the largest group; measured 2 vs a literal 23) and "The tie branch is stated behavior" (whole package reported ambiguous); Task 2 (attribution plus the 45-entry tripwire); Task 3 (synthetic 2-vs-2 fixture, order-independence assertion); Failure Path Test Strategy tie row; Success Criteria R2 row. | State the attribution explicitly in the R2 definition: the violating set is every sibling outside the largest marker group. On a tie for largest (e.g. a 2-vs-2 package), report every file in the package as ambiguous rather than letting dict-iteration order pick a side — that branch must be a stated, tested behavior, not an accident. Add a synthetic 2-vs-2 fixture to `test_audit_reports_a_synthetic_mistag`; Task 4's R2 mutation (one file in `tests/unit/output_handler/`) exercises only 1-vs-N and would leave the tie branch untested. |
| CONCERN | Scope & Value; structural check | `EXEMPT_DIRS` is introduced as a first-class exemption mechanism, but the measured baseline populates zero entries, both bracketing assertions are written only against `KNOWN_MISTAGS`, and the Verification row "Every exemption is a tracked path" imports `EXEMPT_DIRS` and then never iterates it. A stale or untracked directory exemption would therefore pass silently — the exact #3031 hole the plan says it closes, reintroduced in the mechanism meant to close it. | **CLOSED by dropping it.** `EXEMPT_DIRS` is removed from the plan entirely. Technical Approach records why (zero measured entries, unbracketed, reintroduces the #3031 hole); Risk 3's mitigation now rests on per-file entries; Task 2 forbids adding it; Verification rows no longer import it and a new anti-criterion row asserts the identifier appears nowhere. The overlap rule is moot with no directory mechanism. | Either drop `EXEMPT_DIRS` from this PR (the measured baseline needs none, and Risk 3's justification is hypothetical), or give it both bracketing assertions keyed on directories: every key must be a directory under which `git ls-files` currently returns files, and every key must correspond to a package that actually produces an R2 violation. Also define the overlap rule: a file inside an `EXEMPT_DIRS` package must not also hold a `KNOWN_MISTAGS` entry, or the file drops out of the violation set and assertion 2 fires on its now-stale entry. |
| CONCERN | History & Consistency; Risk & Robustness (Skeptic) | The Problem section asserts "Two distinct mechanisms produce this, both live in the repo today". A third is live and verified: the global `test_` strip eats a `test_` occurring inside the name, defeating FEATURE_MAP keys written for those exact files (`test_judge`, `validate_test_impact`). Five basenames contain `test_` twice; two of them lose a marker they were explicitly mapped for. No proposed rule can see it — it surfaces only as an unmarked file with no directory or sibling signal to contradict, the same shape spike-2 argues a presence rule cannot catch. | **CLOSED, out of scope with a tracker: #3184.** Problem section mechanism 3 (five mangled basenames, the two that lose a mapped marker, `str.replace` named as the cause); No-Gos `[SEPARATE-SLUG #3184]`; Documentation covers all three mechanisms. The stripping is NOT fixed here. Issue #3184 records the 5 basenames, both losses, and the measurement that correcting the strip moves exactly 2 of 834 files. | Population is `basename.count("test_") > 1` — exactly 5 files today: `tests/tools/test_test_judge.py`, `tests/unit/test_conftest_autouse_monkeypatch_order.py`, `tests/unit/test_conftest_isolation_guards.py`, `tests/unit/test_test_redis_server_resolution.py`, `tests/unit/test_validate_test_impact.py`. Note that the `conftest_` files also get mangled (to `confautouse_...` / `confisolation_...`), so the defect is latent in all five. Either name it as an explicitly out-of-scope third mechanism with its own tracker, or add a cheap rule flagging any basename where a FEATURE_MAP key matches the raw stem but not the post-strip stem. Do NOT fix the stripping itself here — that changes marker assignment for two files and belongs with #3175. |
| NIT | structural check | Two Verification rows expect "match count == 0" from `grep -c`. Over two file arguments `grep -c` prints one `path:count` line per file rather than a single number, and it exits 1 when the count is zero, so neither row is machine-checkable as written; `-r` on explicit file arguments is also a no-op. | **CLOSED.** Both rows are now `! grep -q...` exit-code assertions (`! grep -qE '\\b(lineno\\|line_number\\|line_no)\\b' ...`, `! grep -qF 'for pattern, marker_name in FEATURE_MAP' tests/conftest.py`), and the `Feature doc indexed` row moved to `grep -qF` for the same reason. Verified on this machine that the form exits 0 on no match and 1 on a seeded match. | Rewrite as an exit-code assertion that inverts cleanly, e.g. `! grep -qrE '\b(lineno\|line_number\|line_no)\b' tests/marker_map.py tests/unit/test_feature_map_markers.py`. Confirmed `\b` works on this machine's BSD grep, so the word-boundary syntax itself is fine. |
| NIT | structural check | #3175 is titled "Drain the FEATURE_MAP mistag baseline: 21 test files carry the wrong marker or none", but this plan's No-Gos hand it 24 paths (21 from R1, 2 from R2, 1 further from R3). Three baseline entries have no tracker under the title as written. | **CLOSED.** No-Gos carries a **Scope note** recording that the two R2 entries and `tests/unit/test_long_task_checkpointing.py` extend #3175's stated scope past its 21-file title, and the reconciliation is posted as a comment on #3175 (which also corrects that issue's `reflection`-at-index-52 claim). No baseline entry is left untracked. | Update #3175's title and body to 24 paths, or note in the No-Gos that the two R2 entries and `test_long_task_checkpointing.py` extend #3175's stated scope. |


### Critique round 2 (2026-09-06)

FULL depth, independent roster of 3 critics (Risk & Robustness, Scope & Value, History &
Consistency). Mode: independent roster (3 critics). All structural checks pass: the four
mandated sections (Documentation, Update System, Agent Integration, Test Impact) are present
and substantive, tasks 1-7 have no numbering gaps, every `Depends On` id resolves and the graph
is acyclic, all referenced paths exist except the three this plan creates, all three
prerequisites pass, and no Popoto model is touched so no migration is owed.

**Verdict: READY TO BUILD (with concerns)** — 0 blockers, 2 concerns, 0 nits.

**Round-1 closure verification.** Every one of the eight round-1 rows was re-checked against
the code rather than read out of its own fix-table cell. The driver replayed the shipped
resolver independently for a third time: `FEATURE_MAP` parsed with `ast.literal_eval` out of
`git show f3594dd23:tests/conftest.py`, population from
`git ls-files --with-tree f3594dd23 'tests/**/test_*.py' 'tests/test_*.py'`, stem computed with
the shipped global `str.replace`. **Every headline figure in this plan reproduces exactly.**
All eight rows are genuinely closed.

- **BLOCKER 1 (spike-2's 552/282 and the `> 281` verification floor)** — closed. The replay
  returns 834 files, **280 marked / 554 unmarked (66.4%)**, matching the plan. `554`, `280` and
  `66.4%` are the only live figures in spike-2, Rabbit Holes, Success Criteria and Task 2; the
  `552`/`282` pair survives only inside sentences that name it as the corrected-away value. The
  Verification row is now an equality (`-eq 280`), not a floor, so a silently *gained* marker
  fails it. Simulating the two `FEATURE_MAP` additions confirms 280 before and 280 after.
- **BLOCKER 2 (the stem described loosely, and a self-confirming parity control)** — closed. The
  shipped hook at `tests/conftest.py:1209` is
  `item.nodeid.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")`, and the
  plan carries `basename.replace("test_", "").replace(".py", "")` verbatim in Data Flow step A2,
  Technical Approach, Task 1 and Success Criteria, with `removeprefix`, `removesuffix` and
  anchored regexes forbidden by name at each site. Two committed fixtures pin
  `test_test_judge.py` and `test_validate_test_impact.py` to `(None, None)`. Task 5's control is
  now generated from `git show f3594dd23:tests/conftest.py`, so it cannot inherit the branch's
  stem.
- **BLOCKER 3 (R1/R2 reach only 9.6% of the suite)** — closed by stating the boundary, with the
  rules deliberately not widened. The replay confirms 80 files across 11 package directories,
  R1 reaching 47 and R2 reaching 33, leaving 754 files on R3 alone. The worked example
  (`test_worktree_manager_config.py` at top level resolving to `config` and staying green on all
  three rules) is stated in the Problem section, in Success Criteria, and mandated in the
  feature doc.
- **BLOCKER 4 (R2 attribution undefined; a literal reading pushes the baseline to 45)** — closed.
  Majority attribution is now part of the rule. The replay confirms majority attribution yields
  exactly **2** violations (`tests/unit/hooks/test_pre_tool_use_foreground_subagents.py`,
  `tests/unit/session_runner/test_schema_routing.py`) against a literal reading's **23**. The tie
  branch is stated behavior with a synthetic 2-vs-2 fixture and an order-independence assertion,
  and the replay confirms **zero** real ties at baseline, so that fixture is the branch's only
  possible coverage.
- **CONCERN 1 (`EXEMPT_DIRS` unbracketed)** — closed by removal. The identifier now appears only
  in text explaining why it is absent, in Task 2's prohibition, and in a new anti-criterion
  Verification row asserting it appears nowhere in the shipped files.
- **CONCERN 2 (a third, unmentioned mistag mechanism)** — closed, out of scope with a tracker.
  Mechanism 3 is described in the Problem section, deferred in No-Gos as
  `[SEPARATE-SLUG #3184]`, and covered in the Documentation section. Issue **#3184 is OPEN** and
  titled for exactly this defect. The buggy strip is pinned as-is rather than fixed.
- **NIT 1 (two `grep -c` rows not machine-checkable)** — closed in form. All three affected rows
  are now exit-code assertions (`! grep -q…` / `grep -q…`). Residue from this fix is raised below
  as round-2 concern 2.
- **NIT 2 (#3175 titled for 21 files while this plan hands it 24)** — closed. The No-Gos scope
  note names the three paths that extend #3175's stated scope, and the reconciliation comment is
  posted on **#3175** (verified present, 2026-09-06T08:09:47Z), which also corrects that issue's
  `reflection`-at-index-52 claim.

Two errors the plan author found and corrected unprompted during the round-2 re-derivation were
also verified: the R1 violation set is 21 files of which **17** carry no marker (four carry a
wrong one), and `reflection` sits at insertion index **44**, ahead of `config` at 45.

**Round-2 findings.**

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Scope & Value (rated BLOCKER); Risk & Robustness (rated CONCERN); structural replay | Task 5 (`validate-parity`) declares `Depends On: build-marker-map` and `Parallel: true`, but its entire body reads `python tests/marker_map.py --report`, and the `--report` flag is created by Task 2 (`build-audit`), not Task 1. An orchestrator honoring the declared graph may dispatch Task 5 alongside Task 2, and its first command then fails on an unrecognized flag before any comparison runs. Aggregated as CONCERN rather than BLOCKER: the seven tasks are executable in numeric order by a single builder, and the failure is immediate, loud, and costs one retry — it can neither produce a wrong parity result nor pass green on a broken implementation. The severity split between the two critics is recorded here rather than resolved silently upward. | **CLOSED.** Task 5 (`validate-parity`) now reads `Depends On: build-marker-map, build-audit`, so no scheduler honoring the declared graph can dispatch it before the `--report` flag exists. `Parallel: true` kept. No other edge changed: Task 6 still lists `validate-mutation, validate-parity` and Task 7 still lists all six prior ids, and the graph stays acyclic. | Change Task 5's `Depends On:` line from `build-marker-map` to `build-marker-map, build-audit`. No other task's edges change: Task 6 already depends on `validate-mutation, validate-parity`, and Task 7 already lists all six prior ids. `Parallel: true` can stay — Task 5 is then schedulable alongside Tasks 3 and 4, which share the `build-audit` dependency and touch a different file than Task 5's read-only `--report` diff. |
| CONCERN | History & Consistency (rated CONCERN); Risk & Robustness (rated NIT); structural replay | The Verification anti-criterion row "Stem is the shipped global replace, not a prefix strip" cannot catch the case it names. Its command is `! grep -qE 'removeprefix\|removesuffix\|re\\.(sub\|match)' tests/marker_map.py`, and in ERE a double backslash before the dot means "a literal backslash followed by any character", so the `re.sub` / `re.match` leg never matches the text it targets. The driver confirmed this empirically: a seeded file containing `re.sub(r"^test_", "", ...)` left the check green, while the single-backslash form matched. This is residue from round 1's own fix to NIT 1, not a repeat finding. The `removeprefix` and `removesuffix` legs do work. | **CLOSED.** The Verification row now reads `! grep -qE 'removeprefix\|removesuffix\|re\.(sub\|match)' tests/marker_map.py` with a single backslash before the dot. Re-verified empirically against `/usr/bin/grep` by seeding one stem variant per leg into a throwaway module: the shipped `basename.replace("test_", "").replace(".py", "")` stem exits 0 (green), while `removeprefix("test_")`, `removesuffix(".py")`, `re.sub(r"^test_", "", ...)` and `re.match(r"^test_(.*)\.py$", ...)` each exit 1 (red). Under the old double-backslash form the same seeds reproduce the defect exactly: the two `re.` seeds stayed green. | Use a single backslash before the dot: `! grep -qE 'removeprefix\|removesuffix\|re\.(sub\|match)' tests/marker_map.py` (only the dot needs escaping; the alternation bars carry a markdown-table pipe escape and are fine). Blast radius is bounded but not nil: Task 1's committed fixture independently catches an anchored-regex stem behaviorally, because `re.sub(r"^test_", "", "test_test_judge.py")` strips only the leading occurrence and yields the stem `test_judge`, which then resolves through the `test_judge` key to `tools` instead of `(None, None)`. So this is a broken belt over working suspenders — fix the regex anyway, because the row is documented as the mechanism that covers this class and a future reader trusting its green will misjudge the coverage. |

**Cycle disposition.** Round 2 is the final authorized critique round: `MAX_CRITIQUE_CYCLES` is
2 and `revision_round_count` already stands at 1, so a revision-demanding verdict would trip G2
and escalate to a human. Both findings above are single-line edits to this document with exact
Implementation Notes, and neither is a blocker, so the verdict is `READY TO BUILD (with
concerns)`. That verdict increments `concern_round_count` (0 of `MAX_CONCERN_RECRITIQUE_ROUNDS`
= 3) rather than `revision_round_count`, so G2 stays clear. The `plan_revising` lock IS set: the
concern bound is unspent, so G7 gate 4 dispatches `/do-plan`, the revision pass clears the lock,
and the lane proceeds to build.

---

## Open Questions

1. **RESOLVED 2026-09-06. Should this land a GitHub Actions workflow?** The issue's fourth
   acceptance criterion says the guard must run in CI on every PR, and this repo has exactly one
   workflow (`.github/workflows/claude.yml`), which only reacts to `@claude` mentions. The question
   was whether to add a `pull_request` workflow running the audit, or to ship the guard as an
   ordinary unit test only.

   **Owner's ruling: unit test only. No GitHub Actions workflow.** Nothing in this work adds a file
   under `.github/workflows/`.

   **Rationale:** the SDLC TEST stage and the nightly suite are this repo's CI. A guard running as a
   normal unit test in `tests/unit/` is therefore executed on every PR, which satisfies the
   criterion as the repo actually gates work.

