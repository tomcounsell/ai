---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3175
last_comment_id: 5557939734
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
because `sdlc` sits at insertion index 16 and `reflections` at 45.

[#3010](https://github.com/tomcounsell/ai/issues/3010) shipped
`tests/unit/test_feature_map_markers.py`, a guard that detects this class of
mistag, with a path-keyed `KNOWN_MISTAGS` baseline in `tests/marker_map.py`
recording the 24 files already wrong on the day it landed. The guard was built
so the baseline could only shrink. Nobody has shrunk it. This issue drains it.

**Current behavior:**

Measured at `6c865fb5f` (`tests/` identical to `8e62c3a50`), over 838 tracked
test files:

| | |
|---|---|
| `python3 tests/marker_map.py --audit` | 25 violations across 24 paths (21 R1, 2 R2, 2 R3) |
| `KNOWN_MISTAGS` entries | 24 |
| files carrying a derived marker | 284 of 838 |
| reflections packages carrying `reflections` | 2 of 22 |
| `pytest -m reflections --collect-only` over both packages | 34 of 396 tests, 362 deselected |

**Desired outcome:**

Every file whose filename lies about what it tests gets renamed so its filename
tells the truth. `tests/unit/reflections/test_pm_briefings_builder.py` becomes
`test_reflections_pm_briefings_builder.py`, and the resolver that already works
resolves it correctly. `pytest -m reflections` collects 396 of 396.
`KNOWN_MISTAGS` drops from 24 entries to **2**, and each survivor carries a
policy reason rather than an unaddressed defect — which is what the issue's
acceptance criterion 1 asks for, verbatim:

> `KNOWN_MISTAGS` ... is empty, **or every remaining entry has a reason that is
> a deliberate policy choice rather than an unaddressed defect**.

The two survivors are the R2 sibling-uniformity pair
(`tests/unit/session_runner/test_schema_routing.py` and
`tests/unit/hooks/test_pre_tool_use_foreground_subagents.py`), which are not
mistags at all: each file's own marker is *correct*, and R2 fires on its
siblings' absence of one. That is a policy choice about how much a lone
correctly-marked sibling should be punished for its neighbours, and it stays
recorded as such.

**What this is not.** Three files (`test_sdlc_progress_check.py`,
`test_sdlc_upvote_lanes.py`, `test_docs_auditor_git_surface.py`) carry a
*correct* second-order marker today — they genuinely are SDLC and validation
tests, living in the reflections package. Renaming moves their derived marker to
`reflections`, so each one gains an explicit `pytestmark` for the marker it
would otherwise shed. Nothing loses coverage. The one marker this plan does
remove is `config` from `test_pm_briefings_no_slots_configured.py`, which was
never about configuration — `config` matched inside the word `configured`.

## Freshness Check


**Baseline commit:** `6c865fb5f` (`tests/` byte-identical to
`8e62c3a5095cac702835e56fbd2f0910574270da`; the two commits between them touch
only `docs/plans/`)
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
- "`config` sits at insertion index 45 and `reflection` at 52" — **FALSE, and
  the issue body's case against renaming fails with it.** Re-derived at
  `6c865fb5f`: `sdlc` 16, `reflections` **45**, `reflection` **46**, `config`
  **47**. `reflections` sits *ahead* of `config`, so
  `test_reflections_pm_briefings_no_slots_configured.py` resolves to
  `reflections`, not `config`. Issue comment 5557939734 caught this against
  `f3594dd23` (indices 44/45 there) and it still holds after #3010 and #3184.
  **This is the correction the whole plan turns on**: renaming is a working
  remedy, not the dead end the issue body describes.
- "`pytest -m reflections` skips almost the whole `tests/unit/reflections/`
  package" — **holds and is now quantified**: 34 of 396 collected.
- `tests/unit/session_runner/test_schema_routing.py` "alone among 18 siblings"
  — **holds** (`resolved='messaging'` via key `routing`, 17 siblings unmarked).
- `tests/unit/hooks/test_pre_tool_use_foreground_subagents.py` "alone among 5
  siblings" — **holds** (`resolved='sdlc'` via key `pre_tool_use`).
- `tests/unit/test_long_task_checkpointing.py` fragment match — **holds**
  (`resolved='validation'` via key `checkpoint` inside `checkpointing`).

**Drift 1 — the "39 markers / 6 corrected" measurement does not reproduce.**
The issue and `docs/features/feature-map-marker-guard.md` both state that making
the package directory authoritative "gains 39 markers and corrects 6 with zero
losses", measured at `f3594dd23`, and issue comment 5557939734 re-asserts that
it "reproduces unchanged". Replayed at this baseline the same change yields
**17 files newly marked and 4 corrected**, and three reconstructions of the
`f3594dd23` conditions all return 17/4. The figure is treated as wrong.

This plan **does not adopt the directory-authoritative change**, so the number no
longer sizes any decision here — but it still sits in the feature doc as a
rejected option's cost estimate, and a wrong number in a rejection rationale is
worth one line to fix. Correcting it is a documentation task below, not a
premise of the Solution.

**Drift 2 — a material fact absent from the write-up.** 99 tracked test files
carry an explicit `pytestmark` line, and 47 carry an explicit
`pytest.mark.<feature>`, which the guard cannot see. This is not a drift in the
code, it is a gap in the issue's survey, and it is the mechanism this plan uses
to preserve the three second-order markers renaming would otherwise shed.
`tests/integration/reflections/test_pm_briefings_e2e.py` sits in `KNOWN_MISTAGS`
while declaring `pytest.mark.reflections` itself.

**A structural fact the write-up omits, load-bearing for anything path-shaped.**
`_partition_packages` keys by **full parent path**, deliberately, and its
docstring says so: two same-named packages in different trees "are two
independent packages with two independent intents".
`tests/unit/test_feature_map_markers.py:313
test_r1_does_not_merge_same_named_packages` encodes that invariant with
synthetic `tests/unit/worktree_manager/` vs `tests/integration/worktree_manager/`
files, and `tests/unit/reflections/` and `tests/integration/reflections/` are a
**live** same-name pair among the 11 tracked test packages. This plan changes no
keying and adds no name-keyed table, so the invariant is untouched — recorded
here so no later step contradicts it.

**Issue comments incorporated:** one, `5557939734` (2026-09-06, scope
reconciliation from #3010's critique round 2). Both of its substantive findings
are adopted: the scope is **24 paths**, not the 21 the title states (21 R1 + 2 R2
+ 1 R3), and the issue body's `reflection`-at-index-52 claim is wrong. Its third
assertion — that the 39/6 measurement reproduces — is the one point this plan
contradicts, with three failed reconstructions recorded above. `#3184`, which
the comment asks to be "sequenced together" with this work, has since merged.

**Cited sibling issues/PRs re-checked:**

- **#3010** — CLOSED 2026-09-06T12:58:06Z, merged as PR #3190 (`d14685728`).
  The guard, `tests/marker_map.py`, and
  `docs/features/feature-map-marker-guard.md` are all on `main`. This is the
  precondition the feature doc records for #3175, and it is satisfied.
- **#3184** — CLOSED 2026-09-07T08:04:43Z, merged as `8e62c3a50`. `_stem` now
  uses `removeprefix("test_")` / `removesuffix(".py")`. **The rebase this plan
  was warned to expect has already happened.** #3184 explicitly declared
  `KNOWN_MISTAGS` out of scope and in scope for #3175, and it moved the baseline
  by exactly zero entries. It also **retired no rule** — R1/R2/R3 all still fire
  at 21/2/2 after it merged, re-measured for this revision.
- **#3072** — the precedent rename. `git mv` on test files, new basenames
  checked against the resolver, prose references swept in the same change.
- **#2879 / #2946** — CLOSED. The per-theme test-file splits (PRs #2941, #3005)
  are what *created* `tests/unit/reflections/`, `session_runner/`, `hooks/`,
  `memory_extraction/`, and `output_handler/` as packages. They are the origin
  of the R1/R2 population.
- **#2805** — the line-keyed-`ALLOWLIST` lesson that makes `KNOWN_MISTAGS`
  path-keyed. Still binding; this plan deletes entries, never re-keys them.
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
One, and it is not blocking.

- `docs/plans/pytest-clean-zero-tests-fail-closed.md` (`tracking:` → #3195,
  `status: Ready`) touches `scripts/pytest-clean.sh` only. It names
  `tests/marker_map.py` in its own Freshness Check as a coordination note and
  explicitly adds no `FEATURE_MAP` entry. **Disjoint file sets; no coordination
  needed beyond not landing both in one branch.** Its subject matter *is*
  relevant to this plan's testing discipline: a pool-exhausted run can print
  nothing and exit 0, so every measurement below reads a passed/collected count
  rather than an exit code.
- The #3184 lane's plan is **archived** at
  `docs/archive/plans-completed/feature-map-stem-anchored-strip.md`, not active
  in `docs/plans/`. Its work is merged; the overlap is historical.

## Prior Art


- **[#3010 / PR #3190]**: *FEATURE_MAP marker-regression guard* — merged
  2026-09-06. Built `tests/marker_map.py` (the single resolution point shared by
  the collection hook and the guard), the three rules R1/R2/R3, and the
  `KNOWN_MISTAGS` baseline. **Succeeded.** Its `## Responding to a red guard`
  ladder lists three remediations in order of preference, and the first is
  *"Rename the file so its basename resolves correctly."* This plan is that
  first remedy, applied 21 times. The guard is not being reshaped to
  accommodate the drain; it is being used the way it documents.
- **[#3072]**: *Four sibling reflections still hardcode "Eng: Valor" in their
  Telegram senders* — **the precedent rename, done the same way.** It moved test
  files with `git mv` and checked the new basenames against the resolver rather
  than adding resolver special-cases, and it updated the prose that named the old
  paths in the same commit. That is the shape this plan follows: `git mv`, verify
  against `resolve_marker`, sweep the doc references.
- **[#3184]**: *FEATURE_MAP stem uses a global str.replace* — merged
  2026-09-07 as `8e62c3a50`. Anchored the strip so `test_test_judge.py` stems to
  `test_judge` rather than `judge`. **Succeeded.** Note carefully what it did
  *not* do: **#3184 retired no rule.** R1, R2, and R3 all still exist and all
  still fire at `6c865fb5f` (21 / 2 / 2). It is a precedent for fixing a
  *mechanism* in place, and for the discipline of measuring that the fix moves
  exactly the files you predicted — not a precedent for deleting a guard rule.
  This plan deletes no rule.
- **[#2879 / PRs #2941, #3005]**: *Split the largest test files into per-class
  modules* — merged Aug 2026. Created the themed package directories and, in
  doing so, introduced 21 of the 24 baseline violations silently with the suite
  green. The split kept each file's old basename while moving it into a package
  whose name now carried the real meaning; renaming is the completion of that
  move rather than a new convention.
- **[#2946]**: *Split test_output_handler.py and test_memory_extraction.py into
  theme-grouped packages* — merged. Created `tests/unit/output_handler/` and
  `tests/unit/memory_extraction/`. Neither package appears in the baseline: both
  are internally uniform, so R2 passes on them.
- **[#2805]**: *line-keyed ALLOWLIST silently un-exempted call sites* —
  the reason `KNOWN_MISTAGS` is path-keyed. Constrains this plan: entries are
  deleted, never re-keyed or re-indexed. A `git mv` changes the path, so each
  renamed file's entry must be **deleted**, never edited to the new path.
- **[#3031]**: *a stale exemption is a silent hole* — the reason the guard fails
  on a baseline entry with no matching violation. This is what makes the drain
  self-proving: a file cannot be fixed without its entry being deleted, and an
  entry cannot be deleted without the file being fixed.
- **[#431]**: *Organize test suite: feature markers, e2e tests, index* — March
  2026, the original introduction of `FEATURE_MAP` and the substring-match
  resolver.

No prior attempt to drain the baseline exists. This is the first.

## Research


External research on pytest's marker machinery, because the plan's
marker-preservation step rests on how an explicit `pytestmark` composes with the
marker the collection hook derives.

**Queries used:**
- `pytest pytest_collection_modifyitems item.add_marker directory based markers best practice`
- `pytest add_marker after collection -m keyword expression evaluation order caveat`

**Key findings:**

1. **`add_marker` is additive over module-level `pytestmark`.** The docs'
   canonical pattern is exactly this repo's: iterate `items`, call
   `item.add_marker(...)`. It composes with, rather than replaces, an explicit
   `pytestmark`.
   ([Working with custom markers](https://docs.pytest.org/en/stable/example/markers.html))
   **How it informs the plan:** the effective marker set of a file is already
   `explicit ∪ derived` in the shipped system. That is the mechanism the three
   marker-preservation edits use, and it is **verified against this repo, not
   just the docs**: `tests/unit/test_reflection_arm.py` derives `reflections`
   from its basename and declares `pytest.mark.sdlc` explicitly;
   `pytest --collect-only -m sdlc` and `-m reflections` each collect all 10 of
   its tests. The mechanism is in production today across 99 files.

2. **`-m` sees only the markers present when pytest's own deselection runs.**
   pytest performs `-m` deselection inside its *own*
   `pytest_collection_modifyitems`, via `deselect_by_mark`, and
   `MarkMatcher.from_item` snapshots `{mark.name for mark in item.iter_markers()}`
   at that moment. A marker added later is invisible to `-m`.
   ([_pytest.mark source](https://docs.pytest.org/en/stable/_modules/_pytest/mark.html))
   **How it informs the plan:** this plan does not touch
   `tests/conftest.py::pytest_collection_modifyitems`, so it neither introduces
   nor mitigates this hazard — the ordering is exactly as inherited. It does
   mean every acceptance measurement must be a real
   `pytest --collect-only -m <marker>` run rather than a unit test of
   `resolve_marker`, because only the real run proves the marker reaches `-m`.
   That is how the two close conditions in Success Criteria are written.

3. **Unregistered markers warn (and error under `--strict-markers`).**
   `pyproject.toml` `addopts` is
   `--tb=short -p no:postgresql -n auto --dist=loadfile --timeout=420 --timeout-method=thread`,
   with no `--strict-markers`. **How it informs the plan:** every marker this
   plan writes explicitly (`sdlc`, `validation`) is already declared in
   `[tool.pytest.ini_options] markers`, so no registration change is needed. A
   typo would warn rather than raise, which is why the verification table asserts
   on real collection *counts* rather than on the absence of an error.

4. **`git mv` preserves history; renames are detected by content similarity.**
   Git stores no rename records — `git log --follow` and `git blame` reconstruct
   them from similarity at read time, and a pure rename with no content change is
   detected at 100% similarity.
   ([git-mv docs](https://git-scm.com/docs/git-mv))
   **How it informs the plan:** rename commits stay pure. Content edits to a
   renamed file (the three `pytestmark` additions) land in a **separate commit**
   from the `git mv`, so `--find-renames` sees an unambiguous 100% match and
   `git log --follow` keeps working on all 21 files.

## Spike Results


All spikes ran during planning; every number was **re-derived at `6c865fb5f`
during this revision** rather than carried forward. The builder should not
re-investigate these, but every one is reproducible in under a minute.

### spike-1: Does #3184's anchored stem move the baseline?
- **Assumption**: "the 21/2/1 baseline was measured before #3184 and may no longer hold"
- **Method**: code-read + `python3 tests/marker_map.py --audit`
- **Finding**: **It does not move it at all.** 25 violations across 24 paths,
  21 R1 / 2 R2 / 2 R3, `KNOWN_MISTAGS` 24 entries, audit exit 0
  (`0 new, 0 stale`). The two files #3184 retagged both sit directly under a
  `KNOWN_ROOT_DIRS` parent and were never in the baseline.
- **Confidence**: high
- **Impact on plan**: no rebase is pending; this is the build baseline.

### spike-2: Does renaming actually clear R1?
- **Assumption**: "renaming cannot work, because `config` beats `reflection`" (the issue body's claim)
- **Method**: prototype — replay `check_r1` / `check_r2` / `check_r3` over the
  838-file list with the 21 R1 basenames rewritten
- **Finding**: **The claim is false and renaming clears R1 completely.** With
  each of the 21 violating basenames rewritten to carry its package name as the
  leading token, and one `FEATURE_MAP` key added:

  | Rule | Before | After |
  |---|---|---|
  | `check_r1` | 21 | **0** |
  | `check_r2` | 2 | **2** |
  | `check_r3` | 2 | **0** |

- **Confidence**: high
- **Impact on plan**: this is the Solution. `KNOWN_MISTAGS` goes 24 → 2.

### spike-3: Naive prefixing is not enough — two files need a token *replaced*
- **Assumption**: "prefix every violating basename with its package name and R1 clears"
- **Method**: prototype — the naive prefix rule
  (`test_X.py` → `test_<pkg>_X.py`) applied to all 21, then `check_r1`
- **Finding**: **Naive prefixing fixes 19 of 21.** The two survivors are
  `tests/unit/reflections/test_sdlc_progress_check.py` and
  `tests/unit/reflections/test_sdlc_upvote_lanes.py`. Prefixing yields
  `test_reflections_sdlc_progress_check.py`, whose stem still contains the
  token `sdlc` at insertion index 16 — ahead of `reflections` at 45 — so
  first-hit resolution still returns `sdlc` and R1 still fires. The leading
  `sdlc` token must be **replaced**, not prefixed:
  `test_sdlc_progress_check.py` → `test_reflections_progress_check.py`.
- **Confidence**: high
- **Impact on plan**: the rename table below is explicit per file rather than
  generated by a rule, and the verification asserts `check_r1 == 0` rather than
  trusting the naming convention.

### spike-4: Is the `checkpointing` key order-sensitive?
- **Assumption**: "adding a narrow key means more ordering-sensitive hand-placement — the defect restated as the remedy" (the issue's objection)
- **Method**: prototype — insert `"checkpointing": "validation"` at the **front**
  of `FEATURE_MAP` and at the **back**, and compare R3
- **Finding**: **Order-independent, and the reason is structural.** R3 compares
  `resolve_marker` against `resolve_marker_whole_token` and fires when the two
  disagree about the *marker*, not the key.

  | Placement | `resolve_marker` | `resolve_marker_whole_token` | `check_r3` |
  |---|---|---|---|
  | front | `('validation', 'checkpointing')` | `('validation', 'checkpointing')` | **0** |
  | back | `('validation', 'checkpoint')` | `('validation', 'checkpointing')` | **0** |

  Both `checkpoint` and `checkpointing` map to `validation`, so whichever key
  wins the substring scan, the marker is the same and the rules agree. **This
  key needs no hand-placement at all**, which is the direct answer to the
  issue's objection: the objection is right about keys that must outrank an
  existing key, and this key does not have to outrank anything.
- **Confidence**: high
- **Impact on plan**: exactly one `FEATURE_MAP` key is added, its position is
  free, and the plan says so in the code comment beside it.

### spike-5: Does an explicit `pytestmark` really compose with the derived marker?
- **Assumption**: "adding `pytest.mark.sdlc` to a file whose basename derives `reflections` keeps both selectors working"
- **Method**: prototype — real `pytest --collect-only -m` runs against a file
  already in this shape
- **Finding**: **Confirmed against production code, not a synthetic fixture.**
  `tests/unit/test_reflection_arm.py` derives `reflections` from its basename
  and declares `pytest.mark.sdlc` explicitly. `--collect-only -m sdlc` collects
  **10 of 10**; `--collect-only -m reflections` collects **10 of 10**. 99
  tracked test files carry an explicit `pytestmark`; 5 declare a feature marker
  that differs from their derived one and keep both.
- **Confidence**: high
- **Impact on plan**: the three marker-preservation edits are a use of a shipped,
  exercised mechanism rather than a new one. This is what makes "0 files lose a
  marker" achievable alongside the renames.

### spike-6: Measure the full proposed end state
- **Assumption**: "21 renames plus one key plus three `pytestmark` lines drains the baseline to 2 policy entries with no coverage loss"
- **Method**: prototype — replay resolution over all 838 files with the rename
  map applied, then real `--collect-only -m` runs for the five affected markers
- **Finding**:

  | Metric | Before | After |
  |---|---|---|
  | `check_r1` / `check_r2` / `check_r3` | 21 / 2 / 2 | **0 / 2 / 0** |
  | `KNOWN_MISTAGS` entries | 24 | **2** |
  | files with a derived marker | 284 | **301** (+17) |
  | files losing a derived marker entirely | — | **0** |
  | files whose derived marker changes value | — | **4** |
  | reflections packages carrying `reflections` | 2 / 22 | **22 / 22** |

  **Derived-marker census** (what `resolve_marker` returns): `reflections`
  28 → 48, `sdlc` 85 → 83, `messaging` 61 → 62, `config` 6 → 5,
  `validation` 9 → 8. Every other marker unchanged.

  **Effective-selector census** (what `-m` collects, suite-wide, out of 17001
  tests), after the three `pytestmark` preservations:

  | Selector | Before | After | Why |
  |---|---|---|---|
  | `-m reflections` | 544 | **906** | +362, the whole point |
  | `-m sdlc` | 2859 | **2859** | the 154 tests in the two `test_sdlc_*` files are preserved by explicit `pytest.mark.sdlc` |
  | `-m validation` | 428 | **428** | the 27 tests in `test_docs_auditor_git_surface.py` preserved by explicit `pytest.mark.validation` |
  | `-m messaging` | 1271 | **1276** | +5, `tests/unit/bridge/test_dispatch.py` gains `messaging` |
  | `-m config` | 127 | **123** | −4, the one deliberate removal |

- **Confidence**: high
- **Impact on plan**: the only coverage change anywhere in the suite is `-m
  config` losing the 4 tests in `test_pm_briefings_no_slots_configured.py`, a
  file that has nothing to do with configuration. That is the single ratification
  the plan asks for.

## Data Flow


Two paths read the same resolver. That is the invariant #3010 established, and
this plan does not touch either one.

**Path A — marker assignment, at collection time**

1. **Entry point**: `pytest` collects test items.
2. `tests/conftest.py::pytest_collection_modifyitems(items)` runs, ahead of
   pytest's internal `deselect_by_mark`.
3. For each item it takes the module basename from
   `item.nodeid.split("::")[0]`.
4. It calls `tests/marker_map.py::resolve_marker(basename)` → a single marker
   or `None`, and calls `item.add_marker(...)` when non-`None`.
5. **Output**: pytest's `deselect_by_mark` snapshots
   `{m.name for m in item.iter_markers()}` — explicit `pytestmark` plus
   whatever step 4 added — and applies `-m`.

**Path B — the guard, at test time**

1. **Entry point**: `tests/unit/test_feature_map_markers.py`, or
   `python3 tests/marker_map.py --audit` on a bare interpreter.
2. `iter_test_files()` shells `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'`
   → the same rootdir-relative path strings Path A sees, from the index rather
   than the filesystem.
3. `check_r1` / `check_r2` / `check_r3` call the **same** `resolve_marker`, and
   `_partition_packages` groups by full parent path.
4. **Output**: `(violations, new_mistags, stale_exemptions)`, bracketed against
   `KNOWN_MISTAGS` in both directions.

**What changes: nothing in the flow.** Both paths keep calling
`resolve_marker(basename)`, unchanged in signature and unchanged in
implementation. The change is entirely in the *inputs*: 21 basenames become
different strings, `FEATURE_MAP` gains one key, three files gain an explicit
`pytestmark`, and `KNOWN_MISTAGS` loses 22 entries.

That is the property that makes this remedy cheap to review. The failure mode
#3010 exists to prevent — Path A and Path B disagreeing about what a file
resolves to — cannot be introduced by a change that edits neither path. The
guard's verdict after the change means exactly what it meant before it.

## Why Previous Fixes Failed


No previous attempt to drain this baseline exists, so there is no failure to
analyse. What this section records instead is the correction that makes the
Solution possible, because the issue body rejects the chosen remedy on a
factually wrong basis and a builder who reads only the issue will not attempt it.

| Candidate | What it would do | Verdict |
|-----------|------------------|---------|
| **Rename the files** *(chosen)* | Rewrite each violating basename so its leading token is its package's name | **Works, and is the guard's own documented first remedy.** The issue body says it "does not reliably work" because `config` beats `reflection`. Re-derived: `reflections` sits at index 45 and `config` at 47, so the renamed file resolves to `reflections`. The one real obstacle is narrower than the issue claims and is handled: two `test_sdlc_*` files need the leading `sdlc` token **replaced** rather than prefixed (spike-3), because `sdlc` sits at index 16. With that, R1 goes to 0. |
| Add ~9 narrow `FEATURE_MAP` keys | One key per offending basename | **Rejected, and the issue's reasoning is right.** Three of the nine would have to be hand-placed *ahead of* `config`, `sdlc`, and `validation` to win a first-hit scan. That is more ordering-sensitive hand-placement — the defect restated as the remedy. The single key this plan does add is exempt from that objection because it outranks nothing: `checkpoint` and `checkpointing` both map to `validation`, so its position is free (spike-4). |
| Make the package directory authoritative | Resolve the marker from the directory, with `FEATURE_MAP` as fallback | **Rejected.** It hollows out the guard: R1's whole job is asking "does this file's marker match its directory's?", and defining the marker *as* the directory's makes the question unanswerable. Retiring R1 one day after #3010 shipped it trades a working detector for an argument. It also needs a second table (`DIRECTORY_MAP`) keyed by directory, a new rule to guard *that* table, and a per-package exemption list — new mechanism to police a change whose whole justification was removing mechanism. |

**Root cause pattern:** the mistags are a symptom of deriving a semantic property
(what feature is this test about?) from an ordered substring scan of a filename.
There are two honest responses: change the derivation, or change the filenames.
#3184 changed the derivation once, narrowly and structurally, by anchoring the
stem. This plan changes the filenames, because the filenames are the thing that
is actually wrong — `test_pm_briefings_builder.py` in `tests/unit/reflections/`
is a reflections test with a name that does not say so. Renaming it is not
encoding the directory name a second time; it is the file finally saying what it
is.

The maintenance objection to renaming — "it must be repeated by hand for every
file anyone ever adds" — is real and is answered by the guard, not by the plan.
R1 fires on the next file added to a themed package with a non-conforming name,
which is exactly the enforcement a naming convention needs and exactly what
#3010 built. Retiring R1 would remove it.

## Architectural Impact


- **New dependencies**: none. No new module, no new table, no new rule.
- **Interface changes**: none. `resolve_marker(basename) -> (marker, key)`,
  `resolve_marker_whole_token`, `_stem`, `_partition_packages`, `check_r1`,
  `check_r2`, `check_r3`, and `run_audit` all keep their current signatures and
  their current bodies. `tests/conftest.py::pytest_collection_modifyitems` is
  not touched.
- **Coupling**: unchanged. The `FEATURE_MAP` first-hit scan remains
  order-sensitive; this plan removes 21 files' *dependence* on that ordering by
  giving them names whose leading token resolves unambiguously, without
  changing the mechanism. The one added key is order-free by measurement
  (spike-4).
- **Data ownership**: `tests/marker_map.py` remains the sole owner of marker
  resolution.
- **Reversibility**: high. `git revert` of the rename commit restores every
  path, and the `KNOWN_MISTAGS` deletions revert with it. Because the renames
  are pure `git mv` with no content change, `git log --follow` and `git blame`
  keep working on all 21 files.
- **Blast radius**: 21 test files renamed (contents untouched by the rename
  commit), 3 of them gaining a `pytestmark` line in a follow-up commit,
  `tests/marker_map.py` (one `FEATURE_MAP` key, 22 `KNOWN_MISTAGS` deletions, 2
  reason rewrites), and the prose that names the old paths. **No production
  code is touched**: `git grep -ln marker_map` returns exactly three code files,
  all under `tests/`, and none of the 21 renamed modules is imported by anything
  (`git grep "from tests\.unit\.reflections\|from tests\.integration\.reflections"`
  returns nothing; the only cross-test imports in the suite are
  `tests.db_claim` and two `tests.unit.session_runner` modules, none of which
  moves).
- **Systemic risk worth naming**: renaming *replaces* a file's derived marker
  rather than adding to it, so three files would shed a genuinely correct
  second-order marker — 154 tests would leave `-m sdlc` and 27 would leave
  `-m validation`. The plan pays that back with an explicit `pytestmark` on each
  of the three, using the composition mechanism already running on 99 files
  (spike-5). Measured result: `-m sdlc` and `-m validation` collect the same
  count before and after. The only selector that shrinks is `-m config`, by the
  4 tests in a file that is not about configuration.
- **Guard surface**: unchanged in shape. R1, R2, and R3 all keep running. After
  the change R1 and R3 report zero violations because the population is clean,
  not because they were retired — so the next file added to a themed package
  with a non-conforming name still fires R1, which is the forward-looking value
  #3010 was built for.

## Appetite


**Size:** Small-to-Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1 (the `-m config` removal ratification in Open Questions)
- Review rounds: 1-2. The change is mechanical and its correctness is
  mechanically checkable: the audit either reports `0 new, 0 stale` against a
  2-entry baseline or it does not.

The revision from the earlier directory-authoritative design cut this
substantially. That design added a table, a rule, a package-exemption list, and
retired two of the guard's three rules one day after they shipped — Medium in
code and Large in alignment. This one renames 21 files, adds one dict key, adds
three `pytestmark` lines, and deletes 22 baseline entries. The guard is used
rather than reshaped, so "we deleted the rule" never has to be argued.

The genuine cost is breadth rather than depth: 21 renames means 21 chances to
break a reference, and the doc sweep is the part most likely to be done
carelessly. That is where the verification weight sits.

## Prerequisites


No external prerequisites. Both blocking issues are merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #3010's guard on `main` | `test -f tests/marker_map.py && test -f tests/unit/test_feature_map_markers.py` | The baseline to drain, and the before/after measurement instrument |
| #3184's anchored stem on `main` | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import _stem; assert _stem('test_test_judge.py') == 'test_judge'"` | The stem this plan builds on; an unanchored stem changes every measurement |
| Clean baseline before any edit | `python3 tests/marker_map.py --audit` | Must print `OK: 25 known, baselined violation(s); 0 new, 0 stale.` before work starts |
| No untracked renames pending | `git status --porcelain tests/` | The audit reads `git ls-files`, so an unstaged rename is invisible to it and every measurement silently reports the old state |

## Solution


### Key Elements

- **21 `git mv` renames.** Each violating basename is rewritten so its leading
  token is a `FEATURE_MAP` key that resolves to the package's marker. Nineteen
  are a prefix; two need the leading `sdlc` token replaced (spike-3).
- **One `FEATURE_MAP` key**, `"checkpointing": "validation"`, positioned
  anywhere, which clears the second R3 violation. It outranks nothing, so it
  reintroduces no ordering sensitivity (spike-4).
- **Three explicit `pytestmark` additions**, preserving the second-order markers
  the renames would otherwise shed, via the composition mechanism already in
  production on 99 files (spike-5).
- **`KNOWN_MISTAGS` drained 24 → 2.** The two survivors are the R2 pair, and
  each gets its reason rewritten from "drain tracked by #3175" to a standing
  policy statement.
- **No change to any rule, any function, or any hook.** R1, R2, and R3 all keep
  running and keep firing on future regressions.

### Flow

`pytest -m reflections` → collection hook reads
`tests/unit/reflections/test_reflections_pm_briefings_builder.py` → basename
stems to `reflections_pm_briefings_builder` → `reflections` matches at index 45
→ `item.add_marker(reflections)` → pytest's `deselect_by_mark` sees it →
**test runs**.

Today the same journey stops at "basename stems to `pm_briefings_builder` →
no `FEATURE_MAP` key matches → no marker → deselected".

### Technical Approach

**1. The 21 renames, explicitly.**

Nineteen are `test_X.py` → `test_<pkg>_X.py`. Two are the `sdlc`-token
replacements. Each is a `git mv` with **no content change**, so the rename is
detected at 100% similarity and `git log --follow` keeps working.

| # | From | To |
|---|---|---|
| 1 | `tests/unit/reflections/test_daily_log_aggregator.py` | `test_reflections_daily_log_aggregator.py` |
| 2 | `tests/unit/reflections/test_daily_log_audio_guard.py` | `test_reflections_daily_log_audio_guard.py` |
| 3 | `tests/unit/reflections/test_daily_log_renderer.py` | `test_reflections_daily_log_renderer.py` |
| 4 | `tests/unit/reflections/test_docs_auditor_git_surface.py` | `test_reflections_docs_auditor_git_surface.py` |
| 5 | `tests/unit/reflections/test_expectation_reconciler.py` | `test_reflections_expectation_reconciler.py` |
| 6 | `tests/unit/reflections/test_log_audit_sentry.py` | `test_reflections_log_audit_sentry.py` |
| 7 | `tests/unit/reflections/test_merged_branch_cleanup.py` | `test_reflections_merged_branch_cleanup.py` |
| 8 | `tests/unit/reflections/test_pm_briefings_builder.py` | `test_reflections_pm_briefings_builder.py` |
| 9 | `tests/unit/reflections/test_pm_briefings_collector.py` | `test_reflections_pm_briefings_collector.py` |
| 10 | `tests/unit/reflections/test_pm_briefings_delivery.py` | `test_reflections_pm_briefings_delivery.py` |
| 11 | `tests/unit/reflections/test_pm_briefings_init.py` | `test_reflections_pm_briefings_init.py` |
| 12 | `tests/unit/reflections/test_pm_briefings_machine_gate.py` | `test_reflections_pm_briefings_machine_gate.py` |
| 13 | `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` | `test_reflections_pm_briefings_no_slots_configured.py` |
| 14 | `tests/unit/reflections/test_pm_briefings_skip_when_empty.py` | `test_reflections_pm_briefings_skip_when_empty.py` |
| 15 | `tests/unit/reflections/test_pm_briefings_slot_match.py` | `test_reflections_pm_briefings_slot_match.py` |
| 16 | `tests/unit/reflections/test_utilities_resolve_eng_group.py` | `test_reflections_utilities_resolve_eng_group.py` |
| 17 | **`tests/unit/reflections/test_sdlc_progress_check.py`** | **`test_reflections_progress_check.py`** (token replaced) |
| 18 | **`tests/unit/reflections/test_sdlc_upvote_lanes.py`** | **`test_reflections_upvote_lanes.py`** (token replaced) |
| 19 | `tests/integration/reflections/test_pm_briefings_dispatch.py` | `test_reflections_pm_briefings_dispatch.py` |
| 20 | `tests/integration/reflections/test_pm_briefings_e2e.py` | `test_reflections_pm_briefings_e2e.py` |
| 21 | `tests/unit/bridge/test_dispatch.py` | `test_bridge_dispatch.py` |

Rows 17 and 18 are the whole reason this table is written out rather than
generated: prefixing them yields `test_reflections_sdlc_progress_check.py`,
whose stem still contains `sdlc` at index 16, ahead of `reflections` at 45, so
first-hit resolution still returns `sdlc` and R1 still fires. The leading `sdlc`
token is dropped, not kept. Verification asserts `check_r1() == []` rather than
trusting the convention.

Row 21 is the one non-reflections rename: `tests/unit/bridge/` resolves to
`messaging` via the `bridge` key, and `test_dispatch.py`'s stem `dispatch` is not
a `FEATURE_MAP` key at all.

**2. One `FEATURE_MAP` key.**

```python
    # "checkpoint" already matches inside "checkpointing" as a fragment, which
    # is the R3 violation. This key's position is free: both keys map to
    # "validation", so whichever wins the first-hit scan, resolve_marker and
    # resolve_marker_whole_token agree and R3 stays silent. It outranks nothing.
    "checkpointing": "validation",
```

Measured both at the front and at the back of the dict: `check_r3()` returns
`[]` either way (spike-4). This is the answer to the issue's "more
ordering-sensitive hand-placement is the defect, not the remedy" objection — the
objection is correct about keys that must outrank an existing key, and this key
does not.

**3. Three `pytestmark` additions, so nothing loses coverage.**

Renaming replaces a file's derived marker. Three of the 21 have a *correct*
non-`reflections` derived marker today, and they keep it explicitly:

| File (post-rename) | Derived before | Derived after | Add | Tests at stake |
|---|---|---|---|---|
| `test_reflections_progress_check.py` | `sdlc` | `reflections` | `pytest.mark.sdlc` | 112 |
| `test_reflections_upvote_lanes.py` | `sdlc` | `reflections` | `pytest.mark.sdlc` | 42 |
| `test_reflections_docs_auditor_git_surface.py` | `validation` | `reflections` | `pytest.mark.validation` | 27 |

`test_reflections_upvote_lanes.py` already has
`pytestmark = [pytest.mark.unit]`; the marker is appended to that list. The
other two get a new `pytestmark` line. This is the shipped composition
mechanism, verified on production code (spike-5), not a new one.

The **fourth** file whose derived marker changes,
`test_reflections_pm_briefings_no_slots_configured.py`, deliberately gets
**nothing**. It sheds `config`, which it acquired because `config` is a literal
substring of `configured`. `-m config` drops from 127 tests to 123. That is the
single ratification this plan asks for (Open Questions).

**4. Drain `KNOWN_MISTAGS` 24 → 2, and give the survivors policy reasons.**

Twenty-two entries are **deleted** (never re-keyed to the new path — #2805).
The guard's stale-exemption bracketing (#3031) makes this self-proving: a
deleted entry whose violation still exists fails as `new`, and a retained entry
whose violation is gone fails as `stale`. The two survivors stay, with reasons
rewritten from "Drain tracked by #3175" — which reads as an open defect — to a
standing policy statement:

```python
KNOWN_MISTAGS: dict[str, str] = {
    # R2 (sibling uniformity), and both entries are a POLICY CHOICE rather than
    # an unaddressed defect (#3175 acceptance criterion 1). Neither file is
    # mistagged: each resolves to a marker that is correct for it. R2 fires on
    # the *siblings'* absence of a marker, not on this file's presence of one.
    # The remedies are to mark 21 sibling files that nobody has asked to select,
    # or to rename a correctly-named file to hide from the rule. Both are worse
    # than the finding. Re-open if either package acquires a package-level
    # marker of its own.
    "tests/unit/session_runner/test_schema_routing.py": (
        "R2 POLICY: lone sibling (1 of 18) resolving to 'messaging' via "
        "'routing', against 17 unmarked siblings in tests/unit/session_runner/, "
        "which does not itself resolve. 'messaging' is correct for this file: "
        "it tests schema routing. Kept deliberately (#3175)."
    ),
    "tests/unit/hooks/test_pre_tool_use_foreground_subagents.py": (
        "R2 POLICY: lone sibling (1 of 5) resolving to 'sdlc' via "
        "'pre_tool_use', against 4 unmarked siblings in tests/unit/hooks/, "
        "which does not itself resolve. 'sdlc' is correct for this file: the "
        "PreToolUse hook is an SDLC surface. Kept deliberately (#3175)."
    ),
}
```

The docstring above `KNOWN_MISTAGS` is updated in the same edit: it currently
says "Draining this baseline is #3175", which becomes stale the moment this
lands.

**5. Sweep every reference to the 21 old basenames.**

The renames break prose, not code, and prose failures are silent. `git grep`
across the repo finds the old basenames in live feature docs, in `tests/README.md`,
in test comments, and in archived plans. Every one is updated in the same change
(the #3072 shape). The specific live references, enumerated at `6c865fb5f`:

| Reference | Names |
|---|---|
| `tests/unit/conftest.py:168` | `test_daily_log_aggregator` |
| `tests/unit/test_plan_migration_invariant.py:155` | `test_merged_branch_cleanup` |
| `tests/unit/test_reflections_package.py:528` | `test_pm_briefings_dispatch` |
| `tests/integration/test_worker_liveness_ingestion.py:61` | `tests/unit/bridge/test_dispatch.py` |
| `tests/README.md:299` | `test_docs_auditor_git_surface.py` |
| `tests/README.md:554` | `test_pm_briefings_no_slots_configured.py` |
| `docs/features/docs-auditor.md:863` | `test_docs_auditor_git_surface.py` |
| `docs/features/expectation-reconciler.md:92` | `test_expectation_reconciler.py` |
| `docs/features/plan-migration-invariant.md:199` | `test_merged_branch_cleanup.py` |
| `docs/features/feature-map-marker-guard.md` | `test_pm_briefings_no_slots_configured.py`, `test_sdlc_progress_check.py` |
| `docs/archive/plans-completed/*.md` | 17 files across 20 of the 21 basenames |

All of these are comments and prose — **none is an import or a code path**. The
enumeration is a starting point, not the contract: the build re-runs the grep at
its own head, because another lane may add a reference while this work is in
flight. `test_dispatch` needs the full path (`tests/unit/bridge/test_dispatch.py`)
rather than the bare basename, which matches 30 unrelated files.

## Failure Path Test Strategy


### Exception Handling Coverage
- [ ] `tests/marker_map.py` contains **no** `except Exception: pass` blocks
  today, and this change adds none. The two existing failure paths both raise
  loudly and both already have tests: `run_audit()` raises `RuntimeError` on an
  empty `FEATURE_MAP` (`test_empty_feature_map_raises`) and `iter_test_files()`
  raises on an empty enumeration (`test_empty_file_enumeration_raises`).
- [ ] Add the same fail-loud treatment to the new table: `run_audit()` raises
  `RuntimeError` if `DIRECTORY_MAP` is empty, with a test proving it. An empty
  `DIRECTORY_MAP` would make R4 pass vacuously and silently revert every file to
  basename-only resolution — the identical vacuous-pass failure the two existing
  raises exist to prevent.
- [ ] `tests/conftest.py`'s hook has no exception handling and must not gain
  any. A `getattr(pytest.mark, <typo>)` on an unregistered marker currently
  warns rather than raising (no `--strict-markers` in `addopts`), so a typo'd
  `DIRECTORY_MAP` value would produce a marker nothing selects, silently. The
  guard closes this instead: a test asserts every `DIRECTORY_MAP` and
  `FEATURE_MAP` value is a marker registered in `pyproject.toml`, parsed from
  the file with `tomllib`.

### Empty/Invalid Input Handling
- [ ] `resolve_markers("")`, `resolve_markers("test_.py")`, and
  `resolve_markers("tests/test_.py")` each return an empty `frozenset`, not
  `None` and not a set containing `None`. The existing `resolve_marker` fixtures
  for `""` and `"test_.py"` stay and gain `resolve_markers` counterparts.
- [ ] A path with fewer than two components (`"test_foo.py"` with no directory)
  must not raise on the ancestor walk.
- [ ] A path whose every ancestor is a `KNOWN_ROOT_DIRS` name
  (`tests/unit/test_foo.py`) yields directory markers of `∅` — the 757-file
  common case, and the one that must be provably unchanged.
- [ ] `_partition_packages` and the R4 walk must agree about what counts as a
  package; a test asserts they enumerate the same directory set from the same
  file list, so R4 cannot pass on a set of packages R2 never sees.

### Error State Rendering
- [ ] The user-visible failure surface is the guard's `pytest.fail` message and
  `python tests/marker_map.py --audit`'s stdout. Both must name the *rule* that
  fired. R4's message must name the unmapped package directory and instruct the
  reader to add a `DIRECTORY_MAP` entry or an `UNMAPPED_PACKAGES` reason —
  tested by asserting on the message content of a synthetic R4 violation, not
  just its truthiness.
- [ ] `--audit` exit codes stay meaningful: 0 clean, 1 on any violation. A test
  invokes `main(["--audit"])` under a patched, deliberately-violating file list
  and asserts a `1`.

## Test Impact


`tests/unit/test_feature_map_markers.py` is the file this plan reshapes; every
disposition below is in it unless stated otherwise.

- [ ] `test_no_violation_outside_known_mistags` — **UPDATE**: keep the
  bracketing in both directions, but it now runs against an empty
  `KNOWN_MISTAGS` and the R2/R4 rule set. It stays the guard's core assertion.
- [ ] `test_known_mistags_are_all_tracked_paths` — **UPDATE**: still correct and
  still cheap against an empty dict, but it becomes vacuous. Pair it with a
  synthetic-population test so it is not silently reaching nothing.
- [ ] `test_known_mistags_all_carry_a_prose_reason` — **UPDATE**: same. Both keep
  guarding the mechanism for whoever next needs an exemption.
- [ ] `test_audit_reports_a_synthetic_mistag_r1` — **DELETE**: R1 retires. Its
  replacement is a real-collection mutation test (see Step by Step Tasks), which
  is strictly stronger: the deleted test asserted on filenames, the replacement
  asserts on markers pytest actually applied.
- [ ] `test_audit_reports_a_synthetic_mistag_r3` — **DELETE**: R3 retires.
- [ ] `test_r1_does_not_merge_same_named_packages` — **REPLACE**: the
  same-named-packages invariant (`tests/unit/helpers/` vs
  `tests/integration/helpers/`) is still load-bearing and must be re-asserted
  against `resolve_markers` and R4 rather than against R1.
- [ ] `test_audit_reports_a_synthetic_mistag_r2`,
  `test_r2_single_file_package_passes_trivially`,
  `test_r2_tie_reports_every_file_as_ambiguous`,
  `test_r2_tie_is_order_independent`,
  `test_same_named_packages_in_different_trees_stay_separate`,
  `test_r2_does_not_merge_same_named_packages` — **KEEP unchanged**. R2 survives
  and its tie/partition branches are still its only coverage.
- [ ] `test_stem_fidelity_test_judge`, `test_stem_fidelity_validate_test_impact`,
  the three `test_stem_unmangled_*` fixtures — **KEEP unchanged**. These are
  #3184's tripwires and must survive this change untouched; they are also a
  useful canary, since a whole-token regression would move `test_test_judge.py`.
- [ ] `test_resolve_marker_empty_string`, `test_resolve_marker_test_dot_py`,
  `test_resolve_marker_no_underscores`, `test_resolve_marker_exact_key_match`,
  `test_youtube_transcription_retagged_to_tools` — **KEEP**, and add
  `resolve_markers` counterparts. All five still pass under whole-token
  matching (`sdlc`, `config`, `youtube` are whole tokens in their stems).
- [ ] `test_whole_token_single_token_stem_matches`,
  `test_whole_token_rejects_fragment_at_single_token` — **KEEP**. They stop
  being R3 support and become tests of the resolution semantics themselves,
  which raises rather than lowers their value.
- [ ] `test_empty_feature_map_raises`, `test_empty_file_enumeration_raises` —
  **KEEP**, and add the `DIRECTORY_MAP`-empty twin.
- [ ] `test_audit_reports_stale_exemption` — **KEEP unchanged**. It is the
  #3031 bracketing that makes the drain self-proving, and it must keep working
  on a synthetic baseline now that the real one is empty.
- [ ] `tests/conftest.py::pytest_collection_modifyitems` — **UPDATE**: applies a
  marker set. No test currently covers this hook at all; that gap is what makes
  the R1 retirement feel risky, and closing it is a task below.
- [ ] **Suite-wide**: no test outside `tests/unit/test_feature_map_markers.py`
  imports `tests/marker_map.py` (verified by `git grep -ln marker_map`), so no
  other test file changes. The 43 files that gain a marker gain it at collection
  time; their assertions are untouched.

## Rabbit Holes


- **Teaching the guard to read explicit `pytest.mark.<feature>` declarations.**
  Spike-3 found 47 files carrying them and a guard blind to all 47. It is a real
  gap and it is *not* this plan: reading them means AST-parsing 838 files inside
  a module that must stay import-light, and it would drag in `pytestmark`
  aliasing, conditional marks, and `pytest.param(marks=...)`. Deferred with an
  issue rather than absorbed.
- **Adding a `memory` marker for `tests/unit/memory_extraction/`.** Touches
  `pyproject.toml`, `tests/README.md`, and the marker taxonomy, and nobody has
  asked for the selector. The package is left unmapped with a recorded reason.
- **Auditing whether the 284 existing markers are semantically *right*.** This
  plan drains a mechanical baseline. "Is `test_ui_sdlc_data.py` really a `webui`
  test or an `sdlc` test?" is a taxonomy question with 838 instances and no
  mechanical answer. Out of scope, and the additive design means it never has to
  be answered to make progress.
- **Replacing `FEATURE_MAP` with per-file explicit markers.** The end state
  everyone eventually proposes. It is 554 files that need a decision, it deletes
  the automatic-tagging property that makes new tests get markers for free, and
  it is a different project.
- **Making `KNOWN_ROOT_DIRS` smarter.** It is a hardcoded tuple of seven names
  and it works. Deriving it (any directory containing a `conftest.py`? any
  directory not in `DIRECTORY_MAP`?) invites exactly the kind of implicit rule
  this plan is removing.
- **Further archaeology on the 39/6 figure.** Three reconstructions of the
  `f3594dd23` conditions were already tried during planning and none reproduces
  it (Freshness Check). That is enough: the re-measurement is the answer, and a
  fourth attempt to reverse-engineer a method nobody wrote down buys nothing.
  Record the corrected number, do not hunt the old one.

## Risks


### Risk 1: Retiring R1 and R3 removes real protection rather than redundant protection
**Impact:** The guard shipped one day before this plan. If the structural
argument is wrong, this change trades a working detector for a claim, and the
next mistag lands silently — the exact outcome #3010 exists to prevent.
**Mitigation:** The retirement is not accepted on argument. Each retired rule
must be paid for with a test that fails when the *mechanism* is removed, and
that is proven red before it is proven green:
1. Delete the directory branch from `resolve_markers` and confirm the
   real-collection mutation test fails. Paste the failure into the PR.
2. Revert `resolve_marker` to substring matching and confirm the
   `checkpointing` fixture fails. Paste the failure into the PR.
3. Empty `DIRECTORY_MAP` and confirm `run_audit()` raises.
A retirement with no red-state proof for its replacement is a blocker, not a nit.

### Risk 2: R4 becomes a rubber stamp
**Impact:** R4 is the only thing keeping this a guard rather than a one-time
cleanup. If `UNMAPPED_PACKAGES` grows without discipline it degrades into the
whole-package exemption that #3010 explicitly rejected as "the exact silent-hole
shape #3031 warns against".
**Mitigation:** `UNMAPPED_PACKAGES` is bracketed in both directions like
`KNOWN_MISTAGS` — an entry for a package that no longer exists, or that has since
been mapped, fails the guard. It ships with exactly one entry
(`tests/unit/memory_extraction`) and a prose reason, so both bracketing
assertions are exercised on day one rather than sitting unexercised. That
single-entry population is the specific defect #3010 called out in the mechanism
it rejected, and it is why this one ships populated.

### Risk 3: The one marker removal breaks a real selection
**Impact:** `tests/unit/reflections/test_pm_briefings_no_slots_configured.py`
loses `config`. If anyone or anything runs `pytest -m config` expecting that
file, it stops appearing.
**Mitigation:** `-m config` currently selects 6 files, of which this is one; it
is a reflections test about briefing slots, and its `config` marker came from
`config` matching inside `configured`. The file gains `reflections`, which is
correct. No script, `addopts`, or CI path in the repo passes `-m config`
(`git grep` over `scripts/`, `pyproject.toml`, `.github/`). Surfaced as the one
Open Question so a human ratifies it rather than discovering it.

### Risk 4: Marker inflation makes negated selections quietly broader
**Impact:** 43 files gain a marker. Anyone running `pytest -m "not sessions"` to
skip a slow area now skips 18 more files than before, silently.
**Mitigation:** No repo-controlled invocation uses a negated feature marker
(verified over `scripts/`, `pyproject.toml`, `.github/`), so the effect is
confined to interactive use, where the widened set is the intended correction.
The per-marker census before/after is committed in the PR so the change in every
selector's size is visible, not inferred.

### Risk 5: The hook-ordering assumption is inherited, not owned
**Impact:** `-m` sees a dynamically-added marker only if the repo's
`pytest_collection_modifyitems` runs ahead of pytest's internal
`deselect_by_mark` (Research finding 1). That ordering is a pluggy LIFO detail
across the whole plugin stack — `pytest-xdist`, `pytest-randomly`,
`pytest-asyncio`, `pytest-timeout` are all loaded here. If it ever inverts,
every derived marker disappears from `-m` and the resolver tests stay green.
**Mitigation:** This plan does not introduce the risk, but it does make the
suite depend on it far more heavily (327 files instead of 284). Every acceptance
measurement is a real `pytest --collect-only -m <marker>` run, and the collect
counts go into the verification table as literal numbers, so an ordering
inversion fails a check instead of passing a mock.

### Risk 6: A concurrent lane edits `tests/marker_map.py`
**Impact:** #3184 shipped into this file hours before this plan was written.
Another lane touching `FEATURE_MAP` produces a conflict on a file where a bad
merge silently changes markers.
**Mitigation:** Both known adjacent lanes are resolved: #3184 is merged, #3195
touches `scripts/pytest-clean.sh` only. The build re-runs
`python tests/marker_map.py --audit` immediately before opening the PR and again
at the merge head, and the census diff is regenerated at the final head rather
than quoted from plan time.

## Race Conditions


No race conditions identified. Every code path this plan touches is synchronous
and single-threaded: `resolve_markers` and the `FEATURE_MAP` / `DIRECTORY_MAP`
lookups are pure functions over strings, `iter_test_files()` is one blocking
`subprocess.run` of `git ls-files`, and `pytest_collection_modifyitems` runs
once per session on the collection list before any test executes. No async, no
shared mutable state, no cross-process data flow, no Redis.

Two ordering hazards exist and neither is a race — both are deterministic
sequencing properties, recorded here so they are not mistaken for one:

- **Hook ordering vs. `deselect_by_mark`** (Risk 5) is a fixed plugin-load
  order resolved once at startup, identical on every run.
- **`FEATURE_MAP` insertion order** determines which key wins a first-hit scan.
  This plan reduces that dependence to near zero (`DIRECTORY_MAP` is exact-match
  and order-free; whole-token matching removes the two measured ordering
  collisions) but does not eliminate the ordered scan itself.

Under `-n auto --dist=loadfile`, each xdist worker collects independently and
applies markers to its own items. Resolution is a pure function of the path, so
all workers reach identical results with no shared state.

## No-Gos (Out of Scope)


- [SEPARATE-SLUG #3223] Teaching the guard to see the 47 files that declare an
  explicit `pytest.mark.<feature>`. Filed with its own recon; sequenced after
  this plan because this plan rewrites the rule set and empties `KNOWN_MISTAGS`,
  and landing #3223 first would conflict for no gain.
- [SEPARATE-SLUG #3223] Correcting the `KNOWN_MISTAGS`-style reason text that
  implies a derived marker is a file's only marker. Same issue, same reason.

Everything else the issue raises is **in scope and done in this plan**, not
deferred: `KNOWN_MISTAGS` is drained to empty here, the R2 entries are drained
here by mapping `hooks` and `session_runner`, the R3 entries are drained here by
whole-token matching plus the `checkpointing` key, the stale 39/6 figure is
corrected in `docs/features/feature-map-marker-guard.md` here, and
`tests/README.md`'s per-marker counts are refreshed here.

`tests/unit/memory_extraction/` staying out of `DIRECTORY_MAP` is **a decision
made in this plan, not a deferral**: no `memory` marker exists, the package
resolves uniformly to no marker so R2 stays green on it, and it ships as the
single reasoned `UNMAPPED_PACKAGES` entry so R4's bracketing assertions are
exercised from day one.

## Update System


No update system changes required. The change is confined to `tests/`, the two
documents describing it, and nothing that `/update` propagates:

- No new dependency, so `scripts/update/deps.py` and the pin set are untouched.
- No new config file, env key, or `.env.example` entry.
- No service restart. `tests/conftest.py` and `tests/marker_map.py` are loaded
  by `pytest` only; the bridge, worker, and reflection scheduler never import
  them, so `./scripts/valor-service.sh restart` is not needed.
- No migration. `data/migrations_completed.json` and
  `scripts/update/migrations.py` are untouched — this plan changes no Popoto
  model and writes nothing to Redis.
- Existing installations need nothing. The next `git pull` gives every machine
  the new resolution, and marker resolution is recomputed from scratch on every
  collection with no persisted state to migrate.

## Agent Integration


No agent integration required. This is test infrastructure with no runtime
surface:

- **No new CLI entry point.** `tests/marker_map.py` already has a
  `python tests/marker_map.py --audit | --report | --count` interface, invoked
  by path rather than through `pyproject.toml [project.scripts]`, deliberately
  so it runs on a bare interpreter with no venv. `--report` changes its output
  format (a marker *set* per line instead of a single marker) and gains no new
  subcommand. No `valor-*` entrypoint is added or changed.
- **The bridge does not import it.** `git grep -ln marker_map` returns three
  code files, all under `tests/`. `bridge/telegram_bridge.py`, `worker/`, and
  `agent/` have no path to this module.
- **No MCP surface.** Nothing in `mcp_servers/` or `.mcp.json` changes.
- **How the agent reaches it**: through the Bash tool, running
  `python tests/marker_map.py --audit` or `pytest -m <marker>` — both of which
  already work and neither of which changes shape. The `--audit` output text
  changes (new rule names), so any prose telling the agent what a red audit
  means is updated in `docs/features/feature-map-marker-guard.md`.

## Documentation


### Feature Documentation
- [ ] Rewrite `docs/features/feature-map-marker-guard.md`. It is the guard's
  reference and this plan invalidates four of its sections:
  - `## Three mistag mechanisms` — mechanisms 1 and 2 join mechanism 3 as
    structurally closed. Rewrite as "what used to go wrong and what closed it",
    with the closing change named for each (#3184, this plan).
  - `## The three rules, and what each can and cannot see` — becomes R2 and R4.
    The retirement of R1 and R3 is stated with its argument, not glossed.
  - **`## The coverage boundary, stated plainly`** — the "80 of 835 tracked test
    files (9.6%)" figure is superseded. Re-derive and restate it: directory
    intent now reaches 81 files across 9 mapped packages, and the 757 files
    under a root directory are covered by resolution semantics rather than by a
    rule. Say plainly what is still uncovered.
  - **`## What was considered and rejected`** — the entry "Making the package
    directory authoritative … Gains 39 markers and corrects 6" is now both
    *adopted* and *numerically wrong*. Replace it with the measured +43 files /
    +47 applications / 1 loss, and move it out of the rejected list.
  - `## Exemptions` and `## Responding to a red guard` — `KNOWN_MISTAGS` is
    empty, `UNMAPPED_PACKAGES` is new, and the remediation ladder changes (add a
    `DIRECTORY_MAP` entry is now the first move, renaming the file is no longer
    the first).
- [ ] Update `tests/README.md`: the auto-tagging description at line 116 says
  markers come from the filename, which is no longer the whole truth, and the
  per-marker counts it publishes (`sdlc` 516, `messaging` 327, `sessions` 293)
  all move. Regenerate the counts from a real collection rather than editing
  them by hand.
- [ ] `docs/features/README.md` — check whether the guard's index row summary
  still describes it accurately after the rule set changes; update if not.

### Inline Documentation
- [ ] `DIRECTORY_MAP` gets a docstring explaining that it is exact-match and
  therefore order-free, and that this is the specific property `FEATURE_MAP`
  lacks.
- [ ] `KNOWN_MISTAGS` keeps its docstring, rewritten: still the only exemption
  mechanism, still path-keyed (#2805), still bracketed both ways (#3031),
  currently empty and expected to stay that way.
- [ ] `UNMAPPED_PACKAGES` documents why a package is deliberately unmapped and
  that it is bracketed in both directions.
- [ ] `resolve_markers` documents the union semantics and, explicitly, that it
  never removes a marker the basename resolves to — the property acceptance
  criterion 3 rests on.
- [ ] The `_stem` docstring names `resolve_marker_whole_token` as one of its two
  callers. Task 4 collapses that function into `resolve_marker`, which would
  leave the docstring pointing at something that no longer exists — update it in
  the same change rather than after (**verified at plan time**: the older
  "#3184 has exactly one line to change" sentence is already gone, rewritten by
  #3184 itself, so there is nothing else stale in this docstring).

### External Documentation Site
- Not applicable. This repo has no Sphinx/MkDocs/Read the Docs site; `docs/` is
  read directly from the repository.

## Success Criteria


- [ ] `python tests/marker_map.py --audit` exits 0 with zero violations, not
      zero *new* violations — the report line reads `0 known, baselined
      violation(s); 0 new, 0 stale`.
- [ ] `KNOWN_MISTAGS == {}`. **Acceptance criterion 1 from the issue**, met by
      emptying rather than by reasoning about residual entries.
- [ ] `pytest -m reflections --collect-only` over `tests/unit/reflections/` and
      `tests/integration/reflections/` collects **396 of 396** tests with zero
      deselected, up from 34 of 396. **Acceptance criterion 2.**
- [ ] The before/after marker census over all 838 tracked files shows exactly
      one file losing exactly one marker
      (`tests/unit/reflections/test_pm_briefings_no_slots_configured.py` losing
      `config`), and that loss is ratified in Open Questions. **Acceptance
      criterion 3**, checked mechanically against a committed snapshot rather
      than by inspection.
- [ ] 43 files gain a marker; per-marker census matches spike-6
      (`reflections` 28→48, `sessions` 43→61, `messaging` 61→67, `sdlc` 85→89,
      `config` 6→5, all others unchanged).
- [ ] Every retired rule has a replacement proven red before green, with the
      failure output pasted into the PR: remove the directory branch → the
      real-collection mutation test fails; revert to substring matching → the
      `checkpointing` fixture fails; empty `DIRECTORY_MAP` → `run_audit()`
      raises.
- [ ] `tests/conftest.py::pytest_collection_modifyitems` has direct test
      coverage for the first time, against a real pytest collection rather than
      a resolver call.
- [ ] Every value in `DIRECTORY_MAP` and `FEATURE_MAP` is a marker registered in
      `pyproject.toml`, asserted by a test that parses the file.
- [ ] `tests/marker_map.py` still runs on a bare interpreter: standard library
      only, no `pytest` import, no file-content reading.
- [ ] Full `tests/unit/` suite green via `scripts/pytest-clean.sh`.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] Documentation updated (`docs/features/feature-map-marker-guard.md`,
      `tests/README.md`), including the corrected 39/6 figure and the
      re-derived coverage boundary.
- [ ] No xfail conversions apply — `grep -rn 'pytest.mark.xfail\|pytest.xfail('
      tests/` returns nothing related to marker resolution (verified at plan
      time: the suite carries no xfail for this defect).

## Team Orchestration


The lead orchestrates and does not build. Three builder/validator pairs plus a
documentarian, sequenced so the resolver is proven before the guard is rewritten
against it.

### Team Members

- **Builder (resolver)**
  - Name: `resolver-builder`
  - Role: `tests/marker_map.py` resolution layer — `DIRECTORY_MAP`,
    whole-token matching, `resolve_markers`, `--report` set output. Owns no
    guard rules.
  - Agent Type: builder
  - Resume: true

- **Builder (collection hook)**
  - Name: `hook-builder`
  - Role: `tests/conftest.py::pytest_collection_modifyitems` and its first-ever
    real-collection tests, including the R1-replacement mutation test.
  - Agent Type: test-engineer
  - Resume: true

- **Builder (guard)**
  - Name: `guard-builder`
  - Role: `tests/unit/test_feature_map_markers.py` — retire R1/R3, narrow R2,
    add R4, empty `KNOWN_MISTAGS`, add `UNMAPPED_PACKAGES` bracketing.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (census)**
  - Name: `census-validator`
  - Role: read-only. Produces the before/after marker census over all 838 files,
    confirms the loss set is exactly one path, confirms the per-marker census
    matches spike-6, and runs the real `--collect-only -m` counts.
  - Agent Type: validator
  - Resume: true

- **Validator (mutation)**
  - Name: `mutation-validator`
  - Role: read-only on the source, but runs the three red-state mutations in its
    **own worktree** and captures the failure output. Must not share a checkout
    with any builder.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `guard-documentarian`
  - Role: `docs/features/feature-map-marker-guard.md`, `tests/README.md`,
    `docs/features/README.md`, and the stale `_stem` docstring.
  - Agent Type: documentarian
  - Resume: true

- **Lead validator**
  - Name: `final-validator`
  - Role: runs the whole Verification table at the final head.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks


### 0. Capture the before-census (must run first, on unmodified `main`)
- **Task ID**: capture-baseline
- **Depends On**: none
- **Validates**: n/a — this task *produces* the instrument every later check uses
- **Informed By**: spike-6 (expected end state), spike-1 (baseline is 25/24)
- **Assigned To**: census-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the tree is at `8e62c3a50` or later with `python tests/marker_map.py
  --audit` printing `OK: 25 known, baselined violation(s); 0 new, 0 stale.`
- Run `python tests/marker_map.py --report` and commit the output verbatim to
  `tests/data/marker_census_before.tsv`, one `path<TAB>marker` line per tracked
  file. This is the frozen "before" that acceptance criterion 3 is measured
  against; it must be captured **before any resolver edit**.
- Record the real collection counts for every feature marker
  (`--collect-only -q -m <marker>` per marker) into the PR description.
- Record `pytest -m reflections --collect-only` over the two reflections
  packages: expected `34/396 tests collected (362 deselected)`.

### 1. Resolution layer
- **Task ID**: build-resolver
- **Depends On**: capture-baseline
- **Validates**: tests/unit/test_feature_map_markers.py (existing fixtures must
  stay green), tests/unit/test_marker_resolution.py (create)
- **Informed By**: spike-2 (directory rule re-measured), spike-4 (whole-token
  costs 2 losses / 0 changes), spike-6 (final census)
- **Assigned To**: resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `DIRECTORY_MAP` with the nine entries from Technical Approach, exact-match,
  with the order-free docstring. Leave `tests/unit/memory_extraction` out.
- Switch `resolve_marker` from `pattern in stem` to
  `_whole_token_match(pattern, stem)`. Keep its `(marker, key)` signature.
- Add `"checkpointing": "validation"` to `FEATURE_MAP`, positioned anywhere, and
  say in a comment *why* position no longer matters.
- Add `resolve_markers(path) -> frozenset[str]`: walk ancestors outward, stop at
  the first `KNOWN_ROOT_DIRS` name, union directory markers with the basename
  marker. Never removes a basename marker.
- Extend `--report` to emit the full marker set per line.
- Make `run_audit()` raise `RuntimeError` on an empty `DIRECTORY_MAP`, matching
  the existing empty-`FEATURE_MAP` raise.
- Keep the module standard-library only: no `pytest` import, no file reads.

### 2. Resolver validation
- **Task ID**: validate-resolver
- **Depends On**: build-resolver
- **Assigned To**: census-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff `python tests/marker_map.py --report` against
  `tests/data/marker_census_before.tsv`. Assert the loss set is exactly
  `{tests/unit/reflections/test_pm_briefings_no_slots_configured.py: config}`
  and the gain set is exactly the 48 files from spike-6.
- Confirm the per-marker census: `reflections` 28→48, `sessions` 43→61,
  `messaging` 61→67, `sdlc` 85→89, `config` 6→5, all others unchanged.
- Confirm the five `_stem` fidelity fixtures from #3184 still pass untouched.

### 3. Collection hook + the R1 replacement
- **Task ID**: build-hook
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_marker_collection.py (create)
- **Informed By**: spike-5 (nodeid is rootdir-relative and carries directories),
  Research finding 1 (`-m` only sees markers added before `deselect_by_mark`)
- **Assigned To**: hook-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Change `pytest_collection_modifyitems` to apply every marker in
  `resolve_markers(item.nodeid.split("::")[0])`.
- **Write the R1 replacement**: a test that runs a *real* pytest collection
  (`pytest.main` with `--collect-only` in a tmp_path tree, or an inline
  `pytester` fixture) over a synthetic package named after a `DIRECTORY_MAP` key
  containing a file whose basename resolves to a *different* marker, and asserts
  the collected item carries the directory marker. This is the test that must
  fail when the directory branch is deleted.
- Write a companion test asserting `-m <dirmarker>` actually *selects* that
  synthetic item, so a hook-ordering inversion (Risk 5) fails here rather than
  silently.
- Assert `resolve_markers` returns an empty frozenset for `""`, `"test_.py"`,
  a bare basename with no directory, and a file directly under `tests/unit/`.

### 4. Guard rework
- **Task ID**: build-guard
- **Depends On**: build-hook, validate-resolver
- **Validates**: tests/unit/test_feature_map_markers.py
- **Informed By**: spike-6 (only `memory_extraction` remains under R2)
- **Assigned To**: guard-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `check_r1`, `check_r3`, and their synthetic tests. Delete
  `resolve_marker_whole_token` **only if** nothing else needs it — with
  `resolve_marker` now whole-token, the two are the same function, so keep one
  and update every caller rather than leaving a shim (no legacy bridges).
- Narrow `check_r2` to packages absent from `DIRECTORY_MAP`. Keep the tie
  branch, the order-independence test, and the same-named-package partition
  tests unchanged.
- Add `check_r4`: every non-root test package is in `DIRECTORY_MAP` or in
  `UNMAPPED_PACKAGES` (path-keyed, prose reason, bracketed both ways). Seed it
  with `tests/unit/memory_extraction` only. Its failure message must name the
  package and the two remedies.
- Add a test that every `DIRECTORY_MAP` and `FEATURE_MAP` value is a marker
  registered in `pyproject.toml`, parsed with `tomllib`.
- Add a test that `_partition_packages` and the R4 package walk enumerate the
  same directory set from the same file list.
- Set `KNOWN_MISTAGS = {}`, rewrite its docstring, keep both bracketing
  assertions and `test_audit_reports_stale_exemption` working on synthetic data.
- Add the acceptance-criterion-3 test: compare `resolve_markers` against
  `tests/data/marker_census_before.tsv` over paths present in both, assert the
  only regression is the one ratified path.

### 5. Mutation proof (own worktree)
- **Task ID**: validate-mutation
- **Depends On**: build-guard
- **Assigned To**: mutation-validator
- **Agent Type**: validator
- **Parallel**: false
- In a **separate worktree** (no builder may hold this checkout), run three
  mutations and capture each failure verbatim:
  1. delete the directory branch from `resolve_markers` → the task-3
     real-collection test must fail;
  2. revert `resolve_marker` to substring matching → the `checkpointing`
     fixture must fail;
  3. set `DIRECTORY_MAP = {}` → `run_audit()` must raise.
- Additionally mutate R4: add a synthetic unmapped package → R4 fails; remove
  the `memory_extraction` entry from `UNMAPPED_PACKAGES` → R4 fails; add an
  entry for a package that does not exist → the stale-bracket assertion fails.
- Revert every mutation. Paste all six failure outputs into the PR description.
  A green suite alone does not close this task.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guard, validate-mutation
- **Assigned To**: guard-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite `docs/features/feature-map-marker-guard.md` per the Documentation
  section, including the corrected +43/+47/1 figures replacing 39/6 and a
  re-derived coverage boundary replacing "80 of 835 (9.6%)".
- Regenerate `tests/README.md`'s per-marker counts from a real collection.
- Fix the stale "#3184 has exactly one line to change" sentence in `_stem`.
- Check `docs/features/README.md`'s index row.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: capture-baseline, build-resolver, validate-resolver,
  build-hook, build-guard, validate-mutation, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table at the final head, not at plan time.
- Re-run the full `tests/unit/` suite through `scripts/pytest-clean.sh`.
- Confirm every Success Criterion checkbox.

## Verification


Every command runs from the repository root. Counts are the measured values from
spike-6; a mismatch is a real failure, not a tolerance to widen.

| Check | Command | Expected |
|-------|---------|----------|
| Audit clean | `python tests/marker_map.py --audit` | exit code 0 |
| Zero violations, not zero *new* | `python tests/marker_map.py --audit` | output contains `OK: 0 known` |
| `KNOWN_MISTAGS` is empty | `python -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import KNOWN_MISTAGS; sys.exit(0 if KNOWN_MISTAGS == {} else 1)"` | exit code 0 |
| reflections fully collected | `./scripts/pytest-clean.sh -m reflections --collect-only -q -p no:randomly tests/unit/reflections tests/integration/reflections 2>&1 \| grep -c '^tests/'` | output > 395 |
| reflections: nothing deselected | `./scripts/pytest-clean.sh -m reflections --collect-only -q -p no:randomly tests/unit/reflections tests/integration/reflections 2>&1 \| grep -c 'deselected'` | match count == 0 |
| Guard suite green | `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py tests/unit/test_marker_resolution.py tests/unit/test_marker_collection.py -q` | exit code 0 |
| Census: exactly one marker lost | `python scripts/marker_census_diff.py tests/data/marker_census_before.tsv --expect-losses tests/unit/reflections/test_pm_briefings_no_slots_configured.py:config` | exit code 0 |
| Census: 43 files gain a marker | `python scripts/marker_census_diff.py tests/data/marker_census_before.tsv --count-gained` | output contains `43` |
| `DIRECTORY_MAP` is populated | `python -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import DIRECTORY_MAP; sys.exit(0 if len(DIRECTORY_MAP) >= 9 else 1)"` | exit code 0 |
| Empty `DIRECTORY_MAP` fails loudly | `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -k empty_directory_map -q` | exit code 0 |
| R1 is retired, not renamed | `grep -c 'def check_r1' tests/marker_map.py` | match count == 0 |
| R3 is retired, not renamed | `grep -c 'def check_r3' tests/marker_map.py` | match count == 0 |
| R4 exists | `grep -c 'def check_r4' tests/marker_map.py` | output > 0 |
| **Anti-criterion** — no `pytest` import in the bare-interpreter module | `grep -cE '^(import pytest\|from pytest)' tests/marker_map.py` | match count == 0 |
| **Anti-criterion** — no file-content reading (that is #3223's territory, No-Go) | `grep -cE 'read_text\|open\(\|\bast\.' tests/marker_map.py` | match count == 0 |
| Bare-interpreter execution still works | `/usr/bin/python3 tests/marker_map.py --count` | output > 800 |
| Every marker value is registered | `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -k registered -q` | exit code 0 |
| Full unit suite | `./scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Stale 39/6 figure removed from the feature doc | `grep -c 'Gains 39' docs/features/feature-map-marker-guard.md` | match count == 0 |
| Stale coverage boundary removed | `grep -c '80 of 835' docs/features/feature-map-marker-guard.md` | match count == 0 |
| `_stem` docstring names no removed function | `grep -c 'resolve_marker_whole_token' tests/marker_map.py` | match count == 0 |

`scripts/marker_census_diff.py` is a small helper this plan creates: it reads the
frozen `path<TAB>markers` snapshot, recomputes the current marker set for every
path still tracked, and reports gains, losses, and the per-marker census. It is
the mechanical form of acceptance criterion 3.

**Patterns pre-verified at plan time.** Every `grep`-based row above was run
against `8e62c3a50` to confirm it matches what it claims to match:
`Gains 39` → 1, `80 of 835` → 1, `def check_r1` → 1,
`resolve_marker_whole_token` → present, `^(import pytest|from pytest)` → 0,
`read_text|open\(|\bast\.` → 0, `/usr/bin/python3 tests/marker_map.py --count`
→ 838. Two candidate rows were **discarded** during this check because their
patterns matched nothing and would have passed vacuously: a `'Gains 39 markers'`
row (the phrase wraps across two lines in the source) and a row asserting
removal of a `_stem` sentence that #3184 had already deleted.

**Red-state proof requirement.** The three anti-criterion rows and the two
retirement rows must each be demonstrated FAILING against a deliberately
violating input before the PR is opened, with the FAIL output pasted into the PR
description. A grep-based anti-criterion that has never been seen to fire is
indistinguishable from a typo'd pattern.

## Critique Results

**Depth**: FULL (3 lenses) — **Mode**: sequential lenses (Agent tool unavailable: not in tool list) — **Findings**: 10 (1 blocker, 6 concerns, 3 nits)

**Round-2 retraction.** Round 1 recorded a BLOCKER asserting a repo-owner directive to
"prefer `git mv` renames checked against the resolver (as in #3072)" over the
directory-authoritative route. **That finding is withdrawn as unsupported.** Issue #3175's
own "Why this is not folded into #3010" section says the opposite: it rejects renaming
("does not reliably work"), and closes with "The third option [making the package
directory authoritative] is the most promising and should be evaluated on its own, after
the guard exists to prove the before/after numbers." The hollowing caveat in that bullet
is scoped to shipping the change *in the same change as the guard*; the guard shipped
separately in #3010. Issue #3072 is "Four sibling reflections still hardcode 'Eng: Valor'
in their Telegram senders" and has no bearing on marker resolution. Do not re-scope the
Solution around that finding. Round 1's BLOCKER 2 and its five concerns were
independently re-measured and survive; they are restated below with corrections.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness (Adversary); Scope & Value (Simplifier); carried from round 1 and re-verified | `DIRECTORY_MAP` is keyed by **bare directory name** while `_partition_packages` is deliberately keyed by **full parent path**, whose docstring states that two same-named packages in different trees "are two independent packages with two independent intents". `tests/unit/test_feature_map_markers.py:313 test_r1_does_not_merge_same_named_packages` encodes that invariant with synthetic `tests/unit/worktree_manager/` vs `tests/integration/worktree_manager/` files, and `worktree_manager` is one of the plan's own nine seeded `DIRECTORY_MAP` entries. Under bare-name keying `resolve_markers` returns `git` for both, so Test Impact's instruction to REPLACE that test "re-asserted against `resolve_markers` and R4" is unsatisfiable as a genuine regression test. R4 compounds it: `DIRECTORY_MAP` is name-keyed but `UNMAPPED_PACKAGES` is path-keyed, so one rule performs two different lookups against the same package set. | pending | Verified by enumerating all 11 non-root test packages at `f63522c07`: `tests/unit/reflections` and `tests/integration/reflections` are already a live same-name pair; they both want `reflections` today, which is the only reason nothing fails yet. Key `DIRECTORY_MAP` by full parent path (`"tests/unit/hooks": "sdlc"`, not `"hooks": "sdlc"`) so it shares `_partition_packages`'s keying and R4's two lookups agree — the nine entries become ten, since `reflections` needs one row per tree. If bare-name inheritance across trees is genuinely intended, say so in Technical Approach item 1 and narrow the invariant's scope in the same change rather than leaving Test Impact asking for an impossible test. |
| CONCERN | Scope & Value; carried from round 1 and re-verified verbatim | Success Criteria restates the issue's acceptance criterion 1 as bare `KNOWN_MISTAGS == {}`, dropping the issue's disjunction. Issue #3175 verbatim: "`KNOWN_MISTAGS` ... is empty, **or every remaining entry has a reason that is a deliberate policy choice rather than an unaddressed defect**." The stricter restatement is what pulls the two new `DIRECTORY_MAP` entries (`hooks` → `sdlc`, `session_runner` → `sessions`) into scope purely to drain the two R2 baseline entries, and R4 plus `UNMAPPED_PACKAGES` in behind them. | pending | Technical Approach item 1 states outright that "the last three are new, and they are what drains the two R2 entries" — that sentence is the direct artifact of the stricter restatement. Restore the disjunction to Success Criteria and decide the `hooks` / `session_runner` mappings on their own merit (Open Question 2 already asks whether they are even the right markers) rather than as a forced consequence. The two R2 entries carry a defensible policy reason as written: the lone sibling's own marker is correct, and R2 fires on the majority's absence. Note also that the issue names the wrong file for `KNOWN_MISTAGS` — it lives in `tests/marker_map.py`, not `tests/unit/test_feature_map_markers.py`. |
| CONCERN | History & Consistency; carried from round 1 and re-verified | The plan cites #3184 as "the precedent for the argument that retiring a rule whose defect class became impossible is correct rather than a weakening". #3184 retired no rule: it anchored `_stem` and left R1, R2, and R3 all running and all still firing. | pending | Re-measured at `f63522c07`, which is post-#3184: `check_r1` = 21, `check_r2` = 2, `check_r3` = 2 — R3, the very rule this plan retires, was still producing violations after #3184 merged. Either drop the precedent claim or restate it narrowly as precedent for closing a *mechanism* in place, and argue the R1/R3 retirement on its own footing, which Risk 1 already concedes argument alone cannot carry. |
| CONCERN | History & Consistency (Consistency Auditor); round 1 flagged the contradiction, this round corrects its diagnosis | The plan uses "files gaining a marker" for two different quantities without distinguishing them. Technical Approach step 7 and Task 2 `validate-resolver` say the gain set is **48** files; the spike-6 table, Success Criteria, and the Verification `--count-gained` row say **43**. Round 1 called 48 a coincidental collision with the post-change `reflections` count and prescribed changing every 48 to 43 — that prescription is wrong and would make Technical Approach step 7's census-diff assertion fail. | pending | Both numbers are real and measure different things. Independently replayed at `f63522c07` over all 838 tracked files: **48** files end with a strictly larger marker set (43 that had none plus five that already had one — `test_docs_auditor_git_surface.py`, `test_pm_briefings_no_slots_configured.py`, `test_sdlc_progress_check.py`, `test_sdlc_upvote_lanes.py`, `test_schema_routing.py`); **43** files go from zero markers to at least one (284 → 327). Marker applications 284 → 331 (+47). Fix by naming the two quantities separately and pinning `scripts/marker_census_diff.py --count-gained` to one definition in the Verification row, rather than by editing a digit. |
| CONCERN | Scope & Value (User); new this round | Spike-2 reports "4 corrected" files, and the Problem section leads with `test_sdlc_progress_check.py` getting `sdlc` and `test_pm_briefings_no_slots_configured.py` getting `config` as defects to fix. Under the plan's chosen **additive** union only one of those four actually changes: three keep their measured-wrong marker and merely gain `reflections` alongside it. The plan never reconciles spike-2's "corrected" with spike-6's "1 loss", so a reader of the Problem statement will expect an outcome the Solution does not deliver. | pending | Verified at `f63522c07`: after the change `test_docs_auditor_git_surface.py` = {reflections, validation}, `test_sdlc_progress_check.py` and `test_sdlc_upvote_lanes.py` = {reflections, sdlc}, and only `test_pm_briefings_no_slots_configured.py` drops to {reflections}. `-m sdlc` therefore still collects the two `test_sdlc_*` reflections files (census `sdlc` 85 → 89 confirms the plan already counts this). Restate spike-2's "4 corrected" as "4 files whose directory marker was missing; 1 of the 4 also sheds a fragment-matched marker", and say in Problem that additive union adds the right marker rather than removing the wrong one — which is exactly the trade Open Question 1 asks a human to ratify. |
| CONCERN | Risk & Robustness (Skeptic); carried from round 1 and re-verified | Risk 5's ongoing mitigation is Task 3's companion test asserting `-m <dirmarker>` actually selects a synthetic item, but Task 3 specifies "`pytest.main` with `--collect-only` in a tmp_path tree, or an inline `pytester` fixture" with no instruction to load the real plugin stack. `pytester` builds an isolated rootdir with its own ini, so neither `pyproject.toml`'s `addopts` nor `pytest-xdist` / `pytest-randomly` / `pytest-asyncio` / `pytest-timeout` are loaded — the permanent regression test would exercise hook ordering against a plugin combination that is not the hazard Risk 5 names. | pending | Use `pytester.runpytest_subprocess()` (never `runpytest_inprocess` or `pytest.main`, both of which reuse the already-initialized plugin manager) and pass the four plugins explicitly with `-p xdist -p randomly -p asyncio -p timeout` plus `-n auto --dist=loadfile`; otherwise state plainly in Risk 5 that the unit test only proves the mechanism absent those plugins and that the Verification table's real `--collect-only -m` rows are the actual Risk 5 evidence. Those rows were confirmed non-vacuous this round: `grep -c '^tests/'` over the real command returns 34 today. |
| CONCERN | Risk & Robustness (Operator); carried from round 1 and re-verified | Task 4 adds a permanent fixture diffing `resolve_markers` against a frozen `tests/data/marker_census_before.tsv` with a hardcoded one-path exception, but the snapshot gets no lifecycle. `KNOWN_MISTAGS` has explicit two-way bracketing (#2805 / #3031); this snapshot has neither a regeneration procedure nor a ratification path, so any future legitimate rename or added test file trips it with no documented remedy — a second undrained baseline of exactly the kind this plan exists to drain. | pending | `tests/data/` does not exist at `f63522c07` (confirmed), so the directory and its lifecycle are both new surface. Either scope the acceptance-criterion-3 diff to this PR only (it has done its job once the drain is proven, and `scripts/marker_census_diff.py` can stay as an on-demand tool) or give the snapshot a documented regeneration path and put it in the failure message: "regenerate via `python tests/marker_map.py --report > tests/data/marker_census_before.tsv` and add the path to the ratified-loss list if the change is intentional". The Documentation section rewrites the feature doc's "Responding to a red guard" ladder but never mentions this new failure mode. |
| NIT | History & Consistency; carried from round 1 and re-verified | Freshness Check lists `docs/plans/feature-map-stem-anchored-strip.md` under "Active plans in `docs/plans/`". No file exists at that path; it is archived at `docs/archive/plans-completed/feature-map-stem-anchored-strip.md`. The plan's own conclusion (the overlap is historical, not live) is unaffected. | pending | n/a (nit) |
| NIT | Scope & Value; carried from round 1 | Success Criteria quotes acceptance criterion 3 as "no test file loses a marker it currently has" while the plan concedes one file does, resolved only in Open Questions. State the amendment in Success Criteria itself so the criterion and the plan agree without a cross-reference. | pending | n/a (nit) |
| NIT | structural; new this round | Test Impact enumerates every test in `tests/unit/test_feature_map_markers.py` except `test_feature_map_is_a_non_empty_dict` (line 137), which has no UPDATE/DELETE/KEEP disposition. It is unaffected by the change, but an unlisted test in an exhaustive disposition list reads as an oversight. | pending | n/a (nit) |

---

## Open Questions


1. **Ratify the single marker removal.**
   `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` currently
   carries `config`, acquired because `config` is a literal substring of
   `configured`. Under whole-token matching it loses `config` and gains
   `reflections` from its package. This is the **only** file in the suite that
   loses a marker (spike-4 and spike-6 both measured it), and it is the one
   place where acceptance criterion 3 read verbatim — "no test file loses a
   marker it currently has" — is not satisfied. The plan's position is that the
   criterion's intent is to prevent silent coverage regression, and deliberately
   removing one measured-wrong marker while adding the right one is the drain
   working rather than a regression. `-m config` drops from 6 files to 5, and no
   script, `addopts`, or CI path in the repo passes `-m config`. **Confirm, or
   say the criterion is literal — in which case whole-token matching is dropped,
   the two R3 entries stay in `KNOWN_MISTAGS` with policy reasons, and
   acceptance criterion 1 is met by the escape hatch instead of by emptying.**

2. **Are `hooks` → `sdlc` and `session_runner` → `sessions` the right
   markers?** These two new `DIRECTORY_MAP` entries are what drain the R2
   baseline, and they hand markers to 23 files at once. `sdlc` is registered as
   "SDLC pipeline stages, observer, steering, **hooks**", which reads as
   confirmation. `session_runner` is the headless turn runner, which sits
   between `sessions` ("lifecycle, watchdog, stall detection, recovery") and
   `sdlc`; two of its files already declare `pytest.mark.sdlc` explicitly, and
   because the union is additive they keep it either way. Is `sessions` the
   intended home, or should `session_runner` map to `sdlc`?

3. **Is retiring R1 and R3 acceptable, given #3010 shipped one day earlier?**
   The plan's argument is that both rules detect defect classes the new
   resolution makes impossible, that #3184 set the precedent for closing a
   mechanism rather than exempting it, and that R4 replaces the guard's
   forward-looking value. It pays for the claim with six red-state mutation
   proofs. If the preference is to keep R1 and R3 running as
   permanently-tautological rules for reassurance, say so — the cost is two
   rules that can never fail, which is its own kind of silent hole.

4. **Should `tests/unit/memory_extraction/` get a marker?** It is the one
   package left unmapped, because no `memory` marker exists and inventing one
   touches `pyproject.toml` and the marker taxonomy. Five files stay unmarked.
   Add a `memory` marker in this plan, map the package to `context`, or leave it
   unmapped with a recorded reason (the plan's current choice)?
