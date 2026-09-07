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


This plan changes data, not code paths. `tests/marker_map.py` gains one dict key
and loses 22 dict entries; no function is added, removed, or edited. So the
failure surface is unchanged, and the interesting failures are all failures of
the *change itself* landing incompletely.

### Exception Handling Coverage
- [ ] No new `try`/`except` anywhere. `tests/marker_map.py` contains no
  `except Exception: pass` blocks today and this change adds none. Its two
  fail-loud paths — `run_audit()` raising `RuntimeError` on an empty
  `FEATURE_MAP` (`test_empty_feature_map_raises`) and `iter_test_files()`
  raising on an empty enumeration (`test_empty_file_enumeration_raises`) — are
  untouched and stay green.
- [ ] `iter_test_files()` reads `git ls-files`, so a rename that is on disk but
  not staged is **invisible to the audit**: it would still see the old paths and
  report `0 new, 0 stale` against the old baseline, a green result that proves
  nothing. Every audit run in this plan is preceded by
  `git status --porcelain tests/` returning empty, and the Verification table
  makes that a row rather than an assumption.

### Empty/Invalid Input Handling
- [ ] No new input surface. `resolve_marker`'s existing fixtures for `""`,
  `"test_.py"`, and a stem with no underscores stay unchanged and stay green;
  they are also the canary that the `checkpointing` key changed nothing about
  the resolver's edge behavior.
- [ ] The 838-file enumeration must be identical before and after: 21 paths
  change value, and the *count* must not move. A rename that accidentally
  collides with an existing basename would silently reduce the tracked set. The
  builder asserts `python3 tests/marker_map.py --count` returns 838 both before
  and after, and that the set of post-rename basenames within each package has
  no duplicates.

### Error State Rendering
- [ ] The user-visible failure surface is `python3 tests/marker_map.py --audit`
  and the guard's `pytest.fail` message, both unchanged. After the drain, a
  regression prints exactly what it printed before, naming the rule (`R1`), the
  path, the resolved marker, the expected marker, and the responsible
  `FEATURE_MAP` key.
- [ ] The two retained `KNOWN_MISTAGS` reasons are themselves an error-rendering
  surface: they are what a future reader sees when asking why those entries are
  still there. They must read as a decision, not as a TODO. A test already
  asserts every entry carries a prose reason
  (`test_known_mistags_all_carry_a_prose_reason`); it keeps passing against the
  2-entry baseline and is no longer at risk of going vacuous, which an empty
  dict would have made it.
- [ ] **Mid-flight partial state is the real hazard.** If the renames land but
  the `KNOWN_MISTAGS` deletions do not, the audit reports 22 stale exemptions
  and fails loudly — good. If the deletions land but a rename does not, the
  audit reports a new mistag and fails loudly — also good. There is no ordering
  of the two halves that produces a silent pass, which is the #3031 bracketing
  working exactly as designed, and it is the reason this drain needs no bespoke
  verification machinery.

## Test Impact


The guard's own test file is **not reshaped by this plan** — no rule is retired,
so no rule's tests are deleted. What changes is the data those tests run against.

- [ ] `tests/unit/test_feature_map_markers.py::test_no_violation_outside_known_mistags`
  — **KEEP unchanged**. It is the assertion that proves the drain: it runs
  against the 2-entry baseline and passes only if `check_r1` and `check_r3`
  return nothing and `check_r2` returns exactly the two policy paths.
- [ ] `test_known_mistags_are_all_tracked_paths` — **KEEP unchanged**. It gets
  *stronger* here, not weaker: it is the assertion that catches a
  `KNOWN_MISTAGS` entry edited to a renamed path instead of deleted (#2805), and
  with 2 real entries it is still reaching something.
- [ ] `test_known_mistags_all_carry_a_prose_reason` — **KEEP unchanged**, and it
  now guards the two policy reasons, which is precisely its purpose. Draining to
  an empty dict would have made this vacuous; draining to 2 does not.
- [ ] `test_audit_reports_stale_exemption` — **KEEP unchanged**. Runs on
  synthetic data and is the #3031 bracketing that makes the drain self-proving.
- [ ] `test_audit_reports_a_synthetic_mistag_r1`,
  `test_audit_reports_a_synthetic_mistag_r2`,
  `test_audit_reports_a_synthetic_mistag_r3` — **KEEP unchanged**. All three
  rules survive; all three synthetic detectors stay. These are the tests that
  would have been deleted by the rejected directory-authoritative design.
- [ ] `test_r1_does_not_merge_same_named_packages`,
  `test_r2_does_not_merge_same_named_packages`,
  `test_same_named_packages_in_different_trees_stay_separate` — **KEEP
  unchanged**. The full-path keying of `_partition_packages` is untouched, so
  the invariant they encode is untouched. Worth stating plainly because
  `tests/unit/reflections/` and `tests/integration/reflections/` are a live
  same-name pair and both are renamed in this change: after the renames they
  still partition as two independent packages with two independent file lists.
- [ ] `test_r2_single_file_package_passes_trivially`,
  `test_r2_tie_reports_every_file_as_ambiguous`,
  `test_r2_tie_is_order_independent` — **KEEP unchanged**. R2's branch coverage
  is unaffected. Note `tests/unit/bridge/` becomes a single-file package that
  now resolves under R1, so it stays out of R2's territory either way.
- [ ] `test_stem_fidelity_test_judge`, `test_stem_fidelity_validate_test_impact`,
  the three `test_stem_unmangled_*` fixtures — **KEEP unchanged**. #3184's
  tripwires; none of the renamed basenames carries a second `test_` token, so
  none of them interacts with the anchored strip.
- [ ] `test_resolve_marker_empty_string`, `test_resolve_marker_test_dot_py`,
  `test_resolve_marker_no_underscores`, `test_resolve_marker_exact_key_match`,
  `test_youtube_transcription_retagged_to_tools` — **KEEP unchanged**. The
  `checkpointing` key changes none of them.
- [ ] `test_whole_token_single_token_stem_matches`,
  `test_whole_token_rejects_fragment_at_single_token` — **KEEP unchanged**.
  R3 survives, so `_whole_token_match` keeps its role as R3's detector.
- [ ] `test_empty_feature_map_raises`, `test_empty_file_enumeration_raises` —
  **KEEP unchanged**.
- [ ] `test_feature_map_is_a_non_empty_dict` (line 137) — **KEEP unchanged**.
  Listed explicitly so this disposition list is exhaustive over the file; the
  added key does not affect it.
- [ ] **ADD** `test_checkpointing_key_position_is_free` — a new fixture asserting
  that `resolve_marker("test_long_task_checkpointing.py")` and
  `resolve_marker_whole_token(...)` both return `validation`, so R3 stays silent
  regardless of where the key sits. This is the fixture that would go red if
  someone later "tidied" the key away or moved `checkpoint` after it in a way
  that changed the marker. It is the plan's one added test.
- [ ] **ADD** `test_known_mistags_holds_only_policy_entries` — a fixture
  asserting every remaining `KNOWN_MISTAGS` reason contains the token `POLICY`.
  This is issue acceptance criterion 1 turned into an assertion: an entry added
  later as an unaddressed defect fails it, so the baseline cannot quietly
  refill with the class of entry this issue drained.
- [ ] **UPDATE** `tests/unit/conftest.py:168`,
  `tests/unit/test_plan_migration_invariant.py:155`,
  `tests/unit/test_reflections_package.py:528`,
  `tests/integration/test_worker_liveness_ingestion.py:61` — comment-only
  references to renamed basenames. No assertion changes.
- [ ] **Suite-wide**: none of the 21 renamed modules is imported by any other
  test (`git grep "from tests\.unit\.reflections\|from tests\.integration\.reflections"`
  returns nothing), and their contents are unchanged by the rename, so no test
  logic anywhere changes. The three `pytestmark` edits add a marker and touch no
  assertion.

## Rabbit Holes


- **Renaming beyond the 21.** The baseline is 24 paths and the rename set is
  exactly the 21 R1 files. It is tempting, while renaming, to "tidy" the other
  basenames in the same packages. Do not: every one of them already resolves
  correctly, a rename with no violation behind it is churn, and it inflates a
  reviewable diff into an unreviewable one.
- **Making the package directory authoritative.** The route this plan rejected.
  It is a genuinely interesting design and it is out of scope here for a reason
  recorded in Why Previous Fixes Failed: it hollows out R1. If someone wants to
  revisit it, it needs its own issue and its own argument, not a paragraph
  inside a drain.
- **Teaching the guard to read explicit `pytest.mark.<feature>` declarations.**
  47 files carry them and the guard sees none. This plan *adds three more*,
  which slightly widens that blind spot — and that is the honest cost of the
  marker-preservation step. It is filed as #3223, not absorbed here: reading
  them means AST-parsing 838 files inside a module that must stay import-light.
- **Auditing whether the 284 existing markers are semantically *right*.** This
  plan drains a mechanical baseline. "Is `test_ui_sdlc_data.py` really a `webui`
  test or an `sdlc` test?" is a taxonomy question with 838 instances and no
  mechanical answer.
- **Adding a `memory` marker for `tests/unit/memory_extraction/`.** That package
  is not in the baseline: it is internally uniform, so R2 passes on it and
  nothing here is broken. Touching it means `pyproject.toml`, `tests/README.md`,
  and the marker taxonomy for a selector nobody has asked for.
- **Refreshing every stale count in `tests/README.md`.** Its headline numbers
  (`sdlc` 516, `messaging` 327, `sessions` 293) are wrong today by a wide margin
  — the real counts are 2859, 1271, and unmeasured — and none of that staleness
  was caused by this change. Fix the two rows this change actually moves, note
  the wider drift, and leave a full README census to whoever owns that document.
- **Further archaeology on the 39/6 figure.** Three reconstructions were already
  tried and none reproduces it. It sizes a rejected option in a feature doc.
  Correct the number in one line; do not hunt the old method.

## Risks


### Risk 1: A rename breaks a reference that is not an import
**Impact:** The 21 modules are referenced by name in 4 live test comments, 2
`tests/README.md` rows, 3 feature docs, and 17 archived plans. None is an
import, so **nothing fails** — the suite stays green while the documentation
quietly points at files that no longer exist. This is the failure mode most
likely to actually happen, and the least likely to be noticed.
**Mitigation:** The doc sweep is a numbered task with its own verification row,
not a bullet inside another task. After the renames, `git grep -n` for each of
the 21 old basenames across the whole repo must return **only** hits inside
`docs/plans/drain-feature-map-mistag-baseline.md` itself (which documents the
rename and legitimately names both sides). `test_dispatch` is greped as the full
path `tests/unit/bridge/test_dispatch.py`, because the bare basename matches 30
unrelated files. The grep is re-run at the final head, not at plan time.

### Risk 2: A renamed file silently loses coverage from a selector
**Impact:** Renaming *replaces* the derived marker. Three files carry a correct
second-order marker today; without the `pytestmark` step, 154 tests leave
`-m sdlc` and 27 leave `-m validation` with no error anywhere.
**Mitigation:** The three `pytestmark` additions, and — because a `pytestmark`
line is easy to write and easy to get subtly wrong — a real
`pytest --collect-only -m <marker>` count for each affected selector, before and
after, in the PR description. Expected: `-m sdlc` 2859 → 2859, `-m validation`
428 → 428, `-m messaging` 1271 → 1276, `-m reflections` 544 → 906, `-m config`
127 → 123. A resolver unit test cannot catch a malformed `pytestmark`; a real
collection can.

### Risk 3: The one marker removal breaks a real selection
**Impact:** `test_reflections_pm_briefings_no_slots_configured.py` loses
`config`. If anyone runs `pytest -m config` expecting that file, it stops
appearing.
**Mitigation:** `-m config` currently collects 127 tests across 6 files; this is
4 tests in 1 of them. The file is a reflections test about briefing slots and its
`config` marker came from `config` matching inside `configured`. No script,
`addopts`, or CI path in the repo passes `-m config` (`git grep` over `scripts/`,
`pyproject.toml`, `.github/`). Surfaced as the single Open Question so a human
ratifies it rather than discovering it.

### Risk 4: A `git mv` is recorded as a delete-plus-add
**Impact:** `git log --follow` and `git blame` stop reaching a file's history,
which for `test_sdlc_progress_check.py` (112 tests, heavily iterated) is a real
loss of context.
**Mitigation:** Git detects renames by content similarity at read time, so a
rename commit that also edits content can fall below the similarity threshold.
The renames land as **one commit containing only renames, no content changes**;
the three `pytestmark` additions land in a **separate follow-up commit**. Verified
with `git log --follow --oneline -- <new path>` reaching pre-rename history for
all 21, and `git show --stat --find-renames` on the rename commit showing 21
`R100` entries and zero adds or deletes.

### Risk 5: A measurement is taken against an unstaged rename
**Impact:** `iter_test_files()` reads `git ls-files`, not the filesystem. A
rename done with `mv` instead of `git mv`, or done but not staged, is invisible
to the audit: it reports `0 new, 0 stale` against the *old* paths and the old
baseline, a green result that proves the opposite of what it appears to.
**Mitigation:** Every audit invocation in the Verification table is preceded by
`git status --porcelain tests/` returning empty, and that check is its own row.
The builder uses `git mv`, never `mv`.

### Risk 6: A pool-exhausted test run reports success while running nothing
**Impact:** The test-DB pool is shared across concurrent lanes (#3195). A
pool-exhausted run can print nothing and exit 0, so a verification step gated on
an exit code would pass having executed no tests. Every count in this plan would
then be unverified.
**Mitigation:** No verification row in this plan gates on an exit code alone.
Every row reads a **collected or passed count** and compares it to a literal
number. A run that collects 0 fails the comparison, which is the intended
behavior.

### Risk 7: A concurrent lane touches `tests/marker_map.py` or a renamed file
**Impact:** A merge conflict on a file where a bad resolution silently changes
markers, or a new file added to `tests/unit/reflections/` while this work is in
flight, which would arrive with a non-conforming name and fire R1.
**Mitigation:** Both known adjacent lanes are resolved: #3184 is merged, #3195
touches `scripts/pytest-clean.sh` only. The build re-runs the full audit and the
doc-reference grep at the merge head rather than quoting plan-time numbers. A new
non-conforming file arriving mid-flight surfaces as an R1 violation, which is the
guard working; the remedy is to rename it too and say so in the PR.

## Race Conditions


No race conditions identified. This plan changes filenames and dict contents;
it adds no code path at all. `resolve_marker` and the `FEATURE_MAP` lookup are
pure functions over strings, `iter_test_files()` is one blocking
`subprocess.run` of `git ls-files`, and `pytest_collection_modifyitems` — which
this plan does not touch — runs once per session on the collection list before
any test executes. No async, no shared mutable state, no cross-process data
flow, no Redis.

Two ordering properties are worth recording so they are not mistaken for races:

- **Hook ordering vs. `deselect_by_mark`** is a fixed plugin-load order resolved
  once at startup, identical on every run. This plan neither introduces nor
  changes that dependency — the collection hook is untouched, and the number of
  files depending on a derived marker moves only from 284 to 301.
- **`FEATURE_MAP` insertion order** determines which key wins a first-hit scan.
  This plan removes 21 files' dependence on that ordering by renaming them, and
  the one key it adds is measurably position-free (spike-4). The ordered scan
  itself remains.

Under `-n auto --dist=loadfile`, each xdist worker collects independently and
applies markers to its own items. Resolution is a pure function of the basename,
so all workers reach identical results with no shared state.

**Commit ordering within the change is deterministic, not racy**, and is
specified in Step by Step Tasks: renames first (pure `git mv`, so rename
detection stays at 100%), then content edits. Reversing that order costs `git
log --follow` on three files (Risk 4).

## No-Gos (Out of Scope)


- [SEPARATE-SLUG #3223] Teaching the guard to see the 47 files that declare an
  explicit `pytest.mark.<feature>`. This plan adds three more such files, so it
  widens that blind spot by three — stated plainly rather than glossed. Filed
  with its own recon; sequenced after this plan.
- [SEPARATE-ISSUE, to file] Making the package directory authoritative over the
  basename. Evaluated in detail during this plan's first draft and **rejected**,
  because it makes R1 tautological and requires retiring R1 and R3 one day after
  #3010 shipped them. It remains a coherent design; it needs its own issue and
  its own argument about what replaces R1's forward-looking value.
- [OUT] Renaming any test file that is not one of the 21 R1 violations.
- [OUT] Adding a `memory` marker for `tests/unit/memory_extraction/`. That
  package is not in the baseline: it is internally uniform, R2 passes on it, and
  nothing about it is broken.
- [OUT] A full refresh of `tests/README.md`'s per-marker counts. They are
  badly stale independently of this change; the two rows this change moves get
  fixed and the wider drift gets a one-line note.

Everything else the issue raises is **in scope and done in this plan**, not
deferred: the 21 R1 entries are drained by renaming, the 2 R3 entries are drained
by renaming plus the `checkpointing` key, the 2 R2 entries are ratified as policy
entries with rewritten reasons (issue acceptance criterion 1's second branch),
the stale 39/6 figure is corrected in
`docs/features/feature-map-marker-guard.md`, and every doc reference to a renamed
file is updated.

## Update System


No update system changes required. The change is confined to `tests/`, the
documents describing it, and nothing that `/update` propagates:

- No new dependency, so `scripts/update/deps.py` and the pin set are untouched.
- No new config file, env key, or `.env.example` entry.
- No service restart. `tests/conftest.py` and `tests/marker_map.py` are loaded
  by `pytest` only; the bridge, worker, and reflection scheduler never import
  them, so `./scripts/valor-service.sh restart` is not needed.
- No migration. `data/migrations_completed.json` and
  `scripts/update/migrations.py` are untouched — this plan changes no Popoto
  model and writes nothing to Redis.
- Existing installations need nothing. The next `git pull` delivers the renamed
  files; marker resolution is recomputed from scratch on every collection with
  no persisted state to migrate.

One operator note worth recording, though it needs no code: a machine with a
**stale `.pyc` or a stale worktree** can keep an old module path alive after a
pull that renamed it. Standard `git pull` handles this (the old file is deleted,
so its bytecode is orphaned and never imported by name), and none of the renamed
modules is imported by name anywhere, so there is no import to break.

## Agent Integration


No agent integration required. This is test infrastructure with no runtime
surface:

- **No new CLI entry point.** `tests/marker_map.py` keeps its existing
  `python3 tests/marker_map.py --audit | --report | --count` interface, invoked
  by path rather than through `pyproject.toml [project.scripts]`, deliberately
  so it runs on a bare interpreter with no venv. No flag is added or changed and
  no `valor-*` entrypoint is touched.
- **The bridge does not import it.** `git grep -ln marker_map` returns three
  code files, all under `tests/`. `bridge/telegram_bridge.py`, `worker/`, and
  `agent/` have no path to this module. None of the 21 renamed test modules is
  imported by production code either.
- **No MCP surface.** Nothing in `mcp_servers/` or `.mcp.json` changes.
- **How the agent reaches it**: through the Bash tool, running
  `python3 tests/marker_map.py --audit` or `pytest -m <marker>`. Both already
  work and neither changes shape — the audit's output format is unchanged, only
  the number in `OK: N known, baselined violation(s)` moves from 25 to 2.
- **One behavioral note the agent will observe**: `pytest -m reflections` starts
  collecting 362 more tests suite-wide (544 → 906). An agent that used
  `-m reflections` as a fast smoke check will find it meaningfully slower, and
  correct for the first time.

## Documentation


The renames break prose, and prose failures are silent. This section is
therefore weighted toward the sweep rather than toward new writing.

### Feature Documentation
- [ ] Update `docs/features/feature-map-marker-guard.md`. This plan **does not**
  change the guard's rules, so the document's structure survives; four factual
  passages move:
  - `## Exemptions: KNOWN_MISTAGS, keyed by path only` — the baseline is 2
    entries, not 24, and both are policy entries. State the acceptance-criterion-1
    disjunction the two survivors satisfy, and what would justify a third.
  - `## The three rules, and what each can and cannot see` — R1, R2, and R3 all
    survive and all still fire on future regressions. Only the *population*
    changed. Update the "21 from R1, 2 from R2, 1 further from R3" counts to
    0 / 2 / 0 and say when they were measured.
  - `## The coverage boundary, stated plainly` — "80 of 835 tracked test files
    (9.6%) — R1 47 files, R2 33 ... the other 755" is drifted. Re-derived at
    `6c865fb5f`: **81 of 838 (9.7%) — R1 48, R2 33, and 757 files** sit directly
    under a `KNOWN_ROOT_DIRS` parent. The renames do not move these numbers
    (package membership is unchanged), so this is a drift correction, not a
    consequence of the change.
  - `## What was considered and rejected` → "Making the package directory
    authoritative. Gains 39 markers and corrects 6 with zero losses" — the
    number is wrong; re-measured it gains **17** and corrects **4**. The option
    stays *rejected* and this plan reinforces why: it would make R1 tautological.
    Add that #3175 chose the rename remedy instead, which is remedy 1 in this
    document's own `## Responding to a red guard` ladder.
  - Two of the renamed basenames are named in this file
    (`test_pm_briefings_no_slots_configured.py`, `test_sdlc_progress_check.py`)
    and must be updated to their new names.
- [ ] Update `docs/features/docs-auditor.md:863` —
  `tests/unit/reflections/test_docs_auditor_git_surface.py` →
  `test_reflections_docs_auditor_git_surface.py`.
- [ ] Update `docs/features/expectation-reconciler.md:92` —
  `test_expectation_reconciler.py` → `test_reflections_expectation_reconciler.py`.
- [ ] Update `docs/features/plan-migration-invariant.md:199` —
  `test_merged_branch_cleanup.py` → `test_reflections_merged_branch_cleanup.py`.
- [ ] Update `tests/README.md`: the index row at line 299
  (`test_docs_auditor_git_surface.py`) and the prose at line 554 naming
  `test_pm_briefings_no_slots_configured.py` as the `configured` fragment-match
  example. That example is now *fixed*, so the sentence becomes a description of
  what the rename corrected rather than of a live defect. Add a `reflections`
  index row reflecting the package's 22 files. Leave the badly-stale headline
  counts (`sdlc` 516, `messaging` 327, `sessions` 293) alone beyond a one-line
  note that they predate several splits — fixing them is not this change's job
  and pretending otherwise hides real drift behind a rename.
- [ ] Sweep `docs/archive/plans-completed/` — 17 archived plans name 20 of the
  21 old basenames. Update each reference to the new path so a reader following
  a historical plan can still find the file, and add no commentary: an archived
  plan is a record, and the only edit it wants is one that keeps its pointers
  resolvable.
- [ ] `docs/features/README.md` — check whether the guard's index row summary is
  still accurate. It should be; the rule set is unchanged. Update only if the row
  quotes a baseline count.

### Inline Documentation
- [ ] `KNOWN_MISTAGS`'s module docstring currently ends "Draining this baseline
  is #3175; it can only shrink, never grow". Rewrite: the drain happened, the
  two survivors are policy entries, and the shrink-only property still holds.
- [ ] The new `"checkpointing": "validation"` key carries the comment explaining
  why its position is free — that both keys map to the same marker, so R3's
  marker-vs-marker comparison is order-independent. This is the sentence that
  stops a future reader from "tidying" it into a position that looks more
  deliberate.
- [ ] The module docstring's opening paragraph describes the substring-match
  coupling and says a test file "can be renamed, moved, or split and land under
  the wrong marker". Still true and still the point; leave it.
- [ ] Comment-only references in `tests/unit/conftest.py:168`,
  `tests/unit/test_plan_migration_invariant.py:155`,
  `tests/unit/test_reflections_package.py:528`, and
  `tests/integration/test_worker_liveness_ingestion.py:61`.

### External Documentation Site
- Not applicable. This repo has no Sphinx/MkDocs/Read the Docs site; `docs/` is
  read directly from the repository.

## Success Criteria


**Lane close conditions.** These two are the conditions the lane closes on,
stated verbatim:

- [ ] `python3 tests/marker_map.py --audit` reports `0 new, 0 stale` with the
      shrunken baseline.
- [ ] `pytest --collect-only -q -m reflections tests/unit/reflections/` collects
      the package.

Concretely, the first prints
`OK: 2 known, baselined violation(s); 0 new, 0 stale.` and exits 0, and the
second collects every test in the package with none deselected.

**Issue acceptance criteria.**

- [ ] **AC1** — `KNOWN_MISTAGS` "is empty, **or** every remaining entry has a
      reason that is a deliberate policy choice rather than an unaddressed
      defect." Met by the **second branch**: 2 entries remain, both R2, both
      files correctly marked, both reasons rewritten to say so and asserted by
      `test_known_mistags_holds_only_policy_entries`. The disjunction is the
      issue's own wording and this plan meets it as written rather than
      manufacturing scope from the stricter half.
- [ ] **AC2** — `pytest -m reflections` collects the reflections packages.
      Over `tests/unit/reflections/` and `tests/integration/reflections/`:
      **396 of 396**, zero deselected, up from 34 of 396. Suite-wide,
      `-m reflections` goes 544 → 906 tests.
- [ ] **AC3** — no test file loses a marker it currently has. Met for 837 of
      838 files. The single exception is
      `test_reflections_pm_briefings_no_slots_configured.py` losing `config`, a
      marker it acquired because `config` is a literal substring of `configured`.
      Ratified in Open Questions; `-m config` goes 127 → 123 tests.

**Mechanical checks.**

- [ ] `check_r1(files) == []` and `check_r3(files) == []`; `check_r2(files)`
      returns exactly the two policy paths.
- [ ] `KNOWN_MISTAGS` has exactly 2 entries, both containing `POLICY` in their
      reason, and neither is a renamed path (#2805: entries are deleted, not
      re-keyed).
- [ ] `python3 tests/marker_map.py --count` returns 838 before and after — no
      rename collided with an existing basename.
- [ ] Derived-marker census moves exactly as measured: 284 → 301 files marked,
      `reflections` 28 → 48, `sdlc` 85 → 83, `messaging` 61 → 62, `config`
      6 → 5, `validation` 9 → 8, everything else unchanged. Zero files lose a
      derived marker entirely.
- [ ] Effective selector counts, from real `--collect-only -m` runs over
      `tests/`: `-m sdlc` 2859 → 2859, `-m validation` 428 → 428, `-m messaging`
      1271 → 1276, `-m reflections` 544 → 906, `-m config` 127 → 123. Every one
      read as a **count**, never as an exit code (#3195).
- [ ] `git show --stat --find-renames` on the rename commit lists 21 `R100`
      entries and zero adds or deletes; `git log --follow` reaches pre-rename
      history for all 21.
- [ ] `git grep -n` for each of the 21 old basenames returns hits only inside
      this plan document.
- [ ] Full `tests/unit/` suite green via `scripts/pytest-clean.sh`, read as a
      passed count.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean.
- [ ] Documentation updated: `docs/features/feature-map-marker-guard.md`
      (including the corrected 17/4 figure and the re-derived 81-of-838 coverage
      boundary), `docs/features/docs-auditor.md`,
      `docs/features/expectation-reconciler.md`,
      `docs/features/plan-migration-invariant.md`, `tests/README.md`, and the 17
      archived plans.
- [ ] No xfail conversions apply — `grep -rn 'pytest.mark.xfail\|pytest.xfail('
      tests/` returns nothing related to marker resolution (verified at plan
      time: the suite carries no xfail for this defect).

## Team Orchestration


The revision to the rename remedy collapsed this from three builder/validator
pairs to one builder and one validator. There is no resolver to build, no guard
to rework, and no rule retirement to prove red — so the mutation-validator and
the two extra builders have nothing left to own. Splitting the work further
would cost more coordination than it saves, and the renames must land as one
atomic commit anyway.

### Team Members

- **Builder**
  - Name: `rename-builder`
  - Role: the 21 `git mv` renames (one commit, no content changes), then the
    content commit: one `FEATURE_MAP` key, three `pytestmark` additions, the
    `KNOWN_MISTAGS` drain from 24 to 2 with rewritten reasons, two added
    fixtures, and the four comment-only test references.
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: `sweep-documentarian`
  - Role: the doc sweep — `docs/features/feature-map-marker-guard.md` (four
    factual passages plus two basenames), three other feature docs,
    `tests/README.md`, and 17 archived plans. Owns the "no old basename survives
    outside this plan document" grep.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `census-validator`
  - Role: read-only. Captures the before-census, re-runs it after, confirms the
    marker deltas and the five real `--collect-only -m` counts, confirms the
    rename commit shows 21 `R100` entries, and runs every row of the
    Verification table at the final head.
  - Agent Type: validator
  - Resume: true

**Sequencing note.** Everything here is serial. The renames must land before any
measurement is meaningful, the content commit must land before the audit can go
green, and the doc sweep must run against the final set of paths. Parallelism
buys nothing on a change this shape, and a builder and a documentarian editing
`tests/README.md` at once would produce exactly the conflict the sequence avoids.

## Step by Step Tasks


### 0. Capture the before-census (must run first, on unmodified `main`)
- **Task ID**: capture-baseline
- **Depends On**: none
- **Assigned To**: census-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `git status --porcelain tests/` is empty and
  `python3 tests/marker_map.py --audit` prints
  `OK: 25 known, baselined violation(s); 0 new, 0 stale.`
- Run `python3 tests/marker_map.py --report` and `--count`. Save the report to a
  **scratch file outside the repository** and paste the summary into the PR
  description. **Do not create `tests/data/` and do not commit a frozen
  snapshot** — a committed census would be a second undrained baseline with no
  regeneration lifecycle, which is the shape this issue exists to remove. The
  before-state is recoverable at any time from git (`git show <base>:` plus a
  `--report` run in a scratch worktree), so freezing it buys nothing.
- Record real collection counts to compare against later, reading the
  **collected count**, never the exit code (#3195):
  `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m <m> tests/`
  for `reflections` (expect 544), `sdlc` (2859), `validation` (428),
  `messaging` (1271), `config` (127); and
  `-m reflections tests/unit/reflections tests/integration/reflections`
  (expect `34/396 tests collected (362 deselected)`).

### 1. The renames, as one pure-rename commit
- **Task ID**: do-renames
- **Depends On**: capture-baseline
- **Informed By**: spike-2 (renaming clears R1), spike-3 (two files need the
  `sdlc` token replaced, not prefixed)
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- `git mv` all 21 files per the table in Technical Approach item 1. Use `git mv`,
  never `mv` — an unstaged rename is invisible to `git ls-files` and every
  subsequent audit silently measures the old state (Risk 5).
- **Change no file contents in this commit.** The three `pytestmark` additions
  land in task 2, so rename detection stays at 100% and `git log --follow`
  survives (Risk 4).
- Verify before committing: `git status --porcelain tests/` shows exactly 21
  `R` entries; `python3 tests/marker_map.py --count` still returns 838.
- The audit will now report 22 stale exemptions and 0 new. That is the expected
  intermediate state — the #3031 bracketing doing its job — not a failure.

### 2. Content edits: one key, three markers, 22 deletions, two fixtures
- **Task ID**: content-edits
- **Depends On**: do-renames
- **Validates**: tests/unit/test_feature_map_markers.py
- **Informed By**: spike-4 (the key's position is free), spike-5 (explicit
  `pytestmark` composes with the derived marker)
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `"checkpointing": "validation"` to `FEATURE_MAP` with the comment
  explaining why its position does not matter.
- Add `pytest.mark.sdlc` to `test_reflections_progress_check.py` and
  `test_reflections_upvote_lanes.py` (the latter already has
  `pytestmark = [pytest.mark.unit]`; append to it). Add
  `pytest.mark.validation` to
  `test_reflections_docs_auditor_git_surface.py`.
- Delete 22 `KNOWN_MISTAGS` entries. **Delete, never re-key to the new path**
  (#2805). Rewrite the two survivors' reasons as policy statements per
  Technical Approach item 4, and update the dict's docstring.
- Add `test_checkpointing_key_position_is_free` and
  `test_known_mistags_holds_only_policy_entries` to
  `tests/unit/test_feature_map_markers.py`.
- Update the four comment-only references in `tests/unit/conftest.py`,
  `tests/unit/test_plan_migration_invariant.py`,
  `tests/unit/test_reflections_package.py`, and
  `tests/integration/test_worker_liveness_ingestion.py`.
- `python3 tests/marker_map.py --audit` must now print
  `OK: 2 known, baselined violation(s); 0 new, 0 stale.`

### 3. Census and collection validation
- **Task ID**: validate-census
- **Depends On**: content-edits
- **Assigned To**: census-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run `--report` and diff against the task-0 scratch capture. Assert: 838
  files both times; 17 files gain a derived marker; **0** files lose one
  entirely; exactly 4 change value, and they are the four named in spike-6.
- Assert the derived census: `reflections` 28 → 48, `sdlc` 85 → 83, `messaging`
  61 → 62, `config` 6 → 5, `validation` 9 → 8, all others unchanged.
- Re-run all five real `--collect-only -m` counts. Expect `-m sdlc` 2859
  (**unchanged** — this is the check that proves the `pytestmark` additions took;
  2705 means one is malformed), `-m validation` 428, `-m messaging` 1276,
  `-m reflections` 906, `-m config` 123.
- Run the lane close condition:
  `pytest --collect-only -q -m reflections tests/unit/reflections/` collects the
  package, and over both packages 396 of 396 with zero deselected.
- Confirm `git show --stat --find-renames` on the task-1 commit lists 21 `R100`
  entries with zero adds or deletes, and `git log --follow --oneline` reaches
  pre-rename history for all 21 new paths.

### 4. Documentation sweep
- **Task ID**: sweep-docs
- **Depends On**: do-renames
- **Assigned To**: sweep-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Work the Documentation section: the four factual passages plus two basenames
  in `docs/features/feature-map-marker-guard.md`, the three other feature docs,
  `tests/README.md`, and the 17 archived plans.
- **The verification is a grep, run at this task's own head rather than trusted
  from the plan-time enumeration** (another lane may have added a reference):

  ```bash
  for b in test_daily_log_aggregator test_daily_log_audio_guard \
           test_daily_log_renderer test_docs_auditor_git_surface \
           test_expectation_reconciler test_log_audit_sentry \
           test_merged_branch_cleanup test_pm_briefings_builder \
           test_pm_briefings_collector test_pm_briefings_delivery \
           test_pm_briefings_init test_pm_briefings_machine_gate \
           test_pm_briefings_no_slots_configured test_pm_briefings_skip_when_empty \
           test_pm_briefings_slot_match test_sdlc_progress_check \
           test_sdlc_upvote_lanes test_utilities_resolve_eng_group \
           test_pm_briefings_dispatch test_pm_briefings_e2e; do
    git grep -n -- "$b" ':!docs/plans/drain-feature-map-mistag-baseline.md'
  done
  git grep -n -- 'tests/unit/bridge/test_dispatch.py' \
    ':!docs/plans/drain-feature-map-mistag-baseline.md'
  ```

  Must print **nothing**. Note the last line greps the full path: the bare
  basename `test_dispatch` matches 30 unrelated files and would drown the
  signal. Note also that several new basenames *contain* their old one
  (`test_reflections_pm_briefings_builder` contains `pm_briefings_builder` but
  not `test_pm_briefings_builder`), which is why each pattern carries its
  leading `test_`.
- Paste the empty grep output into the PR description. A sweep verified by
  "I updated the files I found" is not verified.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-census, sweep-docs
- **Assigned To**: census-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table at the final head, not at plan time.
- Re-run the full `tests/unit/` suite through `scripts/pytest-clean.sh` and read
  the **passed count**, not the exit code.
- `python -m ruff check .` and `python -m ruff format --check .`.
- Confirm every Success Criterion checkbox, including the two verbatim lane
  close conditions.

## Verification


Every command runs from the repository root. Counts are the measured values from
spike-6, re-derived at `6c865fb5f`; a mismatch is a real failure, not a
tolerance to widen. **No row gates on an exit code where a count is available**
(#3195: a pool-exhausted run can print nothing and exit 0).

| Check | Command | Expected |
|-------|---------|----------|
| No unstaged rename hiding the true state | `git status --porcelain tests/` | empty output |
| **Close condition 1** — audit clean on the shrunken baseline | `python3 tests/marker_map.py --audit` | `OK: 2 known, baselined violation(s); 0 new, 0 stale.`, exit 0 |
| **Close condition 2** — reflections package collects | `pytest --collect-only -q -m reflections tests/unit/reflections/` | collects the package, 0 deselected |
| reflections, both packages | `./scripts/pytest-clean.sh -m reflections --collect-only -q -p no:randomly tests/unit/reflections tests/integration/reflections 2>&1 \| tail -1` | `396/396 tests collected` with no `deselected` |
| R1 drained | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import *; print(len(check_r1(iter_test_files())))"` | `0` |
| R3 drained | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import *; print(len(check_r3(iter_test_files())))"` | `0` |
| R2 is the 2 policy entries | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import *; print(sorted(v['path'] for v in check_r2(iter_test_files())))"` | the two `KNOWN_MISTAGS` paths |
| `KNOWN_MISTAGS` is 2 policy entries | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import KNOWN_MISTAGS as K; print(len(K), all('POLICY' in r for r in K.values()))"` | `2 True` |
| No entry was re-keyed instead of deleted | `python3 -c "import sys; sys.path.insert(0,'.'); from tests.marker_map import KNOWN_MISTAGS as K; print(any('test_reflections_' in p for p in K))"` | `False` |
| Population unchanged | `python3 tests/marker_map.py --count` | `838` |
| Rules all still exist (none retired) | `grep -c 'def check_r1\|def check_r2\|def check_r3' tests/marker_map.py` | `3` |
| The one added key | `grep -c '"checkpointing": "validation"' tests/marker_map.py` | `1` |
| `-m sdlc` unchanged — proves the `pytestmark` additions took | `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m sdlc tests/ 2>&1 \| tail -1` | `2859/17001 tests collected` |
| `-m validation` unchanged | `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m validation tests/ 2>&1 \| tail -1` | `428/17001 tests collected` |
| `-m messaging` gains the bridge file | `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m messaging tests/ 2>&1 \| tail -1` | `1276/17001 tests collected` |
| `-m reflections` suite-wide | `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m reflections tests/ 2>&1 \| tail -1` | `906/17001 tests collected` |
| `-m config` sheds exactly the ratified 4 | `./scripts/pytest-clean.sh --collect-only -q -p no:randomly -m config tests/ 2>&1 \| tail -1` | `123/17001 tests collected` |
| Renames are pure | `git show --stat --find-renames <rename-sha> \| grep -c '=> '` | `21`, and no `create mode` / `delete mode` lines |
| History follows | `for f in <21 new paths>; do git log --follow --oneline -- "$f" \| wc -l; done` | every count > 1 |
| Old basenames gone from the repo | the loop in task 4 | empty output |
| Guard suite green | `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q 2>&1 \| tail -1` | reports a passed count, 0 failed |
| Renamed packages still run | `./scripts/pytest-clean.sh tests/unit/reflections tests/integration/reflections tests/unit/bridge -q 2>&1 \| tail -1` | reports a passed count, 0 failed |
| Full unit suite | `./scripts/pytest-clean.sh tests/unit/ -q 2>&1 \| tail -1` | reports a passed count, 0 failed |
| Lint clean | `python -m ruff check .` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |
| **Anti-criterion** — no `pytest` import in the bare-interpreter module | `grep -cE '^(import pytest\|from pytest)' tests/marker_map.py` | `0` |
| **Anti-criterion** — the module still reads no file contents | `grep -cE 'read_text\|open\(\|\bast\.' tests/marker_map.py` | `0` |
| Bare-interpreter execution still works | `/usr/bin/python3 tests/marker_map.py --count` | `838` |
| Stale 39/6 figure corrected | `grep -c 'Gains 39' docs/features/feature-map-marker-guard.md` | `0` |
| Stale coverage boundary corrected | `grep -c '80 of 835' docs/features/feature-map-marker-guard.md` | `0` |
| No frozen census fixture was created | `test ! -e tests/data/marker_census_before.tsv` | exit 0 |

**Patterns pre-verified at plan time.** Every `grep`-based row was run against
`6c865fb5f` to confirm it matches what it claims to match: `Gains 39` → 1,
`80 of 835` → 1, `def check_r1\|def check_r2\|def check_r3` → 3,
`"checkpointing": "validation"` → 0 (correctly absent before the change),
`^(import pytest|from pytest)` → 0, `read_text|open\(|\bast\.` → 0,
`/usr/bin/python3 tests/marker_map.py --count` → 838, and the task-4 grep loop →
non-empty across 17 archived plans, 4 feature docs, `tests/README.md`, and 4 test
comments. A row whose pattern matches nothing *before* the change would pass
vacuously after it, so each one was confirmed to fire first.

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


**One question, and it is the only decision a human has to make.**

1. **Ratify the single marker removal.**
   `tests/unit/reflections/test_pm_briefings_no_slots_configured.py` carries
   `config` today, acquired because `config` is a literal substring of
   `configured`. After the rename it resolves to `reflections` and sheds
   `config`. It is the **only** file in the suite whose effective marker set
   shrinks: `-m config` goes from 127 tests to 123, and the other three files
   whose derived marker changes keep their old marker via an explicit
   `pytestmark`.

   The plan's position is that this is the drain working rather than a
   regression: the file is a reflections test about briefing slots, it has
   nothing to do with configuration, and no script, `addopts`, or CI path in the
   repo passes `-m config` (`git grep` over `scripts/`, `pyproject.toml`,
   `.github/`). Issue acceptance criterion 3 reads "no test file loses a marker
   it currently has", and this is the one place it is not satisfied literally.

   **Confirm** — or say the criterion is literal, in which case the file gets a
   fourth explicit `pytest.mark.config` alongside the other three preservations
   and nothing in the suite loses anything at all. That variant costs one line
   and preserves a marker everyone agrees is wrong; it is a real option, not a
   straw man.

**Questions the earlier draft asked, now answered by the revised approach and
recorded so nobody re-opens them:**

- *Are `hooks` → `sdlc` and `session_runner` → `sessions` the right markers?* —
  **Moot.** No `DIRECTORY_MAP` exists in this plan, so no package-level marker is
  assigned to either. The two R2 entries stay as policy entries instead.
- *Is retiring R1 and R3 acceptable?* — **Moot.** Nothing is retired. All three
  rules survive and keep firing on future regressions, which is what makes the
  rename remedy durable: the next non-conforming file added to a themed package
  trips R1 exactly as #3010 designed.
- *Should `tests/unit/memory_extraction/` get a marker?* — **Moot.** That package
  is internally uniform, so R2 passes on it and it is not in the baseline.
  Nothing about it is broken and this plan does not touch it.
