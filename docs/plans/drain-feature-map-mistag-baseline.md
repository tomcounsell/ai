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
because `sdlc` sits at insertion index 16 and `reflections` at 45. The
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
- "`config` sits at insertion index 45 and `reflection` at 52" — **FALSE, and
  the issue body's rename argument fails with it.** Re-derived at `8e62c3a50`:
  `sdlc` 16, `reflections` **45**, `reflection` **46**, `config` **47**.
  `reflections` sits *ahead* of `config`, so
  `test_reflections_pm_briefings_no_slots_configured.py` resolves to
  `reflections`, not `config`. Issue comment 5557939734 caught this against
  `f3594dd23` (indices 44/45 there) and this plan confirms it still holds after
  #3010 and #3184. **Renaming is a working remedy for 19 of the 21 R1 files**,
  not the dead end the issue body describes; the two survivors are
  `test_sdlc_progress_check.py` and `test_sdlc_upvote_lanes.py`, blocked by
  `sdlc` at index 16. Corrected in Why Previous Fixes Failed below.
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
measured at `f3594dd23`, and issue comment 5557939734 re-asserts that it
"reproduces unchanged". Replayed at `8e62c3a50` the same change gains **17 files
newly marked / 21 marker applications** and corrects **4**.

Three reconstructions of the `f3594dd23` conditions were tried and **none**
produces 39/6: (a) counting parents without the `KNOWN_ROOT_DIRS` exclusion
(17/4), (b) the pre-#3010 `FEATURE_MAP` with the `reflections` and `youtube`
keys removed, under the pre-#3184 global-`replace` stem (17/4), and (c) that
same map under the anchored stem (17/4). The figure does not reproduce under any
of them, so it is treated as wrong rather than as measuring something this plan
has failed to reproduce. The plan uses the re-measured numbers throughout;
correcting `docs/features/feature-map-marker-guard.md` is a documentation task
below.

**Drift 2 — a material fact absent from the write-up.** 47 tracked test files
carry an explicit `pytest.mark.<feature>`, which the guard cannot see. This is
not a drift in the code, it is a gap in the issue's survey, and it changes the
right answer (see Solution). `tests/integration/reflections/test_pm_briefings_e2e.py`
sits in `KNOWN_MISTAGS` while declaring `pytest.mark.reflections` itself.

**Issue comments incorporated:** one, `5557939734` (2026-09-06, scope
reconciliation from #3010's critique round 2). Both of its substantive findings
are adopted: the scope is **24 paths**, not the 21 the title states (21 R1 + 2 R2
+ 1 R3), and the issue body's `reflection`-at-index-52 claim is wrong. Its third
assertion — that the 39/6 measurement reproduces — is the one point this plan
contradicts, with the three failed reconstructions recorded above. `#3184`, which
the comment asks to be "sequenced together" with this work, has since merged.

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
Two, neither of them blocking.

- `docs/plans/feature-map-stem-anchored-strip.md` (`tracking:` → #3184) is the
  adjacent lane. Its work is **merged**, so the overlap is historical rather
  than live. This plan does not edit that document. The two share
  `tests/marker_map.py`; #3184 owned `_stem`, this plan owns `FEATURE_MAP`
  resolution semantics, `KNOWN_MISTAGS`, and the assignment mechanism in
  `tests/conftest.py`.
- `docs/plans/pytest-clean-zero-tests-fail-closed.md` (`tracking:` → #3195,
  `status: Ready`) touches `scripts/pytest-clean.sh` only. It names
  `tests/marker_map.py` in its own Freshness Check as a coordination note and
  explicitly adds no `FEATURE_MAP` entry. **Disjoint file sets; no
  coordination needed beyond not landing both in one branch.**

## Prior Art


- **[#3010 / PR #3190]**: *FEATURE_MAP marker-regression guard* — merged
  2026-09-06. Built `tests/marker_map.py` (the single resolution point shared by
  the collection hook and the guard), the three rules R1/R2/R3, and the
  `KNOWN_MISTAGS` baseline. **Succeeded.** Its `## What was considered and
  rejected` section names "making the package directory authoritative" as
  deferred to this issue, explicitly because the guard had to exist first to
  measure the before/after. That precondition is now met.
- **[#3184]**: *FEATURE_MAP stem uses a global str.replace* — merged
  2026-09-07 as `8e62c3a50`. Anchored the strip. **Succeeded**, and eliminated
  mechanism 3 (mangled stem) structurally rather than by exemption. This plan
  follows the same shape for mechanisms 1 and 2, and #3184 is the precedent for
  the argument that retiring a rule whose defect class became impossible is
  correct rather than a weakening.
- **[#2879 / PRs #2941, #3005]**: *Split the largest test files into per-class
  modules* — merged Aug 2026. Created the themed package directories. This is
  the change that made a *directory* signal exist at all; before it, there were
  no themed packages to be authoritative about. It is also the change that
  introduced 21 of the 24 baseline violations, silently, with the suite green —
  the exact scenario #3010 was later built to catch.
- **[#2946]**: *Split test_output_handler.py and test_memory_extraction.py into
  theme-grouped packages* — merged. Created `tests/unit/output_handler/` and
  `tests/unit/memory_extraction/`, two of the four packages whose directory name
  does not resolve today.
- **[#2805]**: *line-keyed ALLOWLIST silently un-exempted call sites* —
  the reason `KNOWN_MISTAGS` is path-keyed. Constrains this plan: entries are
  deleted, never re-keyed or re-indexed.
- **[#3031]**: *a stale exemption is a silent hole* — the reason the guard fails
  on a baseline entry with no matching violation. This is what makes the drain
  self-proving: a file cannot be fixed without its entry being deleted, and an
  entry cannot be deleted without the file being fixed.
- **[#431]**: *Organize test suite: feature markers, e2e tests, index* — March
  2026, the original introduction of `FEATURE_MAP` and the substring-match
  resolver. The root of every mechanism this plan closes.

No prior attempt to drain the baseline exists. This is the first.

## Research


External research on pytest's marker machinery, because the correctness of the
whole approach rests on *when* a dynamically-added marker becomes visible to
`-m`.

**Queries used:**
- `pytest pytest_collection_modifyitems item.add_marker directory based markers best practice`
- `pytest add_marker after collection -m keyword expression evaluation order caveat`

**Key findings:**

1. **`-m` sees only the markers present when pytest's own deselection runs.**
   pytest performs `-m` deselection inside its *own* `pytest_collection_modifyitems`,
   via `deselect_by_mark`, and `MarkMatcher.from_item` snapshots
   `{mark.name for mark in item.iter_markers()}` at that moment. A marker added
   later (in `pytest_collection_finish`, `pytest_runtest_setup`, or a fixture's
   `request.node.add_marker`) still drives `skip`/`xfail` but is **invisible to
   `-m`**. Ordering between a conftest hook and pytest's internal one is a pluggy
   LIFO detail, not a guarantee.
   ([_pytest.mark source](https://docs.pytest.org/en/stable/_modules/_pytest/mark.html))
   **How it informs the plan:** the repo's hook already wins this race today —
   `-m reflections` does select the 34 files whose marker is derived, proving the
   ordering works. But it is a property of the current plugin stack, not a
   contract. Therefore **every acceptance measurement in this plan is a real
   `pytest --collect-only -m <marker>` run, never a unit test of the resolver.**
   A resolver test would stay green through a hook-ordering regression that
   silently drops every derived marker from `-m` — precisely the invisible
   failure #3010 was built to prevent.

2. **`add_marker` is additive over module-level `pytestmark`.** The docs'
   canonical pattern is exactly this repo's: iterate `items`, call
   `item.add_marker(...)`. It composes with, rather than replaces, an explicit
   `pytestmark`.
   ([Working with custom markers](https://docs.pytest.org/en/stable/example/markers.html))
   **How it informs the plan:** the effective marker set of a file is *already*
   `explicit ∪ derived` in the shipped system — which is what makes the 47
   explicit-marker files coexist quietly with derived ones. An **additive** union
   of directory and basename markers is therefore consistent with the model
   already in production; a *replacing* directory rule would be the novel
   semantics, not the conservative one.

3. **Directory-keyed marking is the documented idiom, and `item.path`
   (a `pathlib.Path`) is preferred over the deprecated `item.fspath`;** guides
   specifically warn to compare path *parts* rather than substring-match the
   path string, to avoid accidental matches.
   ([mark how-to](https://docs.pytest.org/en/stable/how-to/mark.html))
   **How it informs the plan:** directory resolution matches on `Path(...).parts`
   with an **exact** dict lookup, never a substring scan — which is also what
   makes it order-free.

4. **Unregistered markers warn (and error under `--strict-markers`).**
   `pyproject.toml` `addopts` is
   `--tb=short -p no:postgresql -n auto --dist=loadfile --timeout=420 --timeout-method=thread`,
   with no `--strict-markers`. **How it informs the plan:** every marker this
   plan applies (`reflections`, `sessions`, `sdlc`, `messaging`, `git`,
   `validation`) is already declared in `[tool.pytest.ini_options] markers`, so
   no registration change is needed — and a verification row asserts that any
   marker a `DIRECTORY_MAP` entry can produce is a registered one, so a future
   entry naming a typo'd marker fails loudly instead of warning into the void.

## Spike Results


All five spikes ran during planning at `8e62c3a50`. Every number below is
reproducible from the repo; the builder should not re-investigate these.

### spike-1: Does #3184's anchored stem move the baseline?
- **Assumption**: "the 21/2/1 baseline was measured before #3184 and may no longer hold"
- **Method**: code-read + `python tests/marker_map.py --audit` at `8e62c3a50`
- **Finding**: **It does not move it at all.** 25 violations across 24 paths,
  21 R1 / 2 R2 / 2 R3, `KNOWN_MISTAGS` 24 entries, audit exit 0 (`0 new, 0 stale`).
  The two files #3184 retagged (`test_test_judge.py` → `tools`,
  `test_validate_test_impact.py` → `validation`) both sit directly under a
  `KNOWN_ROOT_DIRS` parent and were never in the baseline.
- **Confidence**: high
- **Impact on plan**: no rebase is pending; `8e62c3a50` is the build baseline.

### spike-2: Re-measure the "directory authoritative" gain
- **Assumption**: "+39 markers, 6 corrected, 0 lost, as filed"
- **Method**: prototype — replay the resolver over `git ls-files` with a
  directory-first rule
- **Finding**: **Does not reproduce.** At `8e62c3a50` the directory rule yields
  **+17 files newly marked, +21 marker applications, 4 corrected, 0 files left
  unmarked**. The 4 corrected files are `test_docs_auditor_git_surface.py`
  (`validation`), `test_pm_briefings_no_slots_configured.py` (`config`),
  `test_sdlc_progress_check.py` and `test_sdlc_upvote_lanes.py` (`sdlc`).
- **Confidence**: high
- **Impact on plan**: the 39/6 figure is stale in both the issue and
  `docs/features/feature-map-marker-guard.md`; correcting the doc is a task.
  More importantly it exposes the additive-vs-replacing fork: under a
  *replacing* rule those 4 files lose a marker, breaking acceptance criterion 3
  verbatim; under an *additive* union none does.

### spike-3: What do explicit `pytest.mark.<feature>` declarations do here?
- **Assumption**: "FEATURE_MAP is the only source of feature markers"
- **Method**: code-read — scan all 838 tracked test files for
  `pytest.mark.<registered feature>`
- **Finding**: **False. 47 files carry an explicit feature marker**, and
  `tests/unit/test_feature_map_markers.py` cannot see any of them. Nine of the
  47 declare a marker that *differs* from their derived one and keep both.
  `tests/integration/reflections/test_pm_briefings_e2e.py` is a `KNOWN_MISTAGS`
  entry that already declares `pytest.mark.reflections`, and
  `tests/unit/test_long_task_checkpointing.py` declares `pytest.mark.sdlc`
  alongside its fragment-matched `validation`.
- **Confidence**: high
- **Impact on plan**: decisive for the additive design. The shipped system
  *already* composes explicit and derived markers additively, so unioning the
  directory marker in is a continuation of the existing model rather than a new
  one. It also softens the one marker removal below: the file that loses
  `validation` keeps its explicit `sdlc`.

### spike-4: What does whole-token basename matching cost?
- **Assumption**: "requiring whole-token matches will strip markers off many files"
- **Method**: prototype — resolve all 838 basenames with
  `resolve_marker_whole_token` and diff against `resolve_marker`
- **Finding**: **2 files lose a marker, 0 files change marker.** The two are
  exactly the two R3 violations: `test_pm_briefings_no_slots_configured.py`
  loses `config` (matched inside `configured`) and
  `test_long_task_checkpointing.py` loses `validation` (matched inside
  `checkpointing`). Nothing else in the suite depends on a fragment match.
- **Confidence**: high
- **Impact on plan**: whole-token matching is affordable as the *resolution*
  semantics, not merely as a detector. It also makes new keys order-free: with
  `checkpoint` no longer matching `checkpointing`, a `checkpointing` key can be
  added anywhere in the dict and win, which is how
  `test_long_task_checkpointing.py` keeps `validation` with zero hand-placement.

### spike-5: Is `item.nodeid` a rootdir-relative path with directories?
- **Assumption**: "the collection hook can see the file's package directory"
- **Method**: prototype — a throwaway pytest plugin printing `item.nodeid`,
  `Path(nodeid).parent`, and `item.path.relative_to(config.rootpath)` during a
  real collection, invoked with explicit subdirectory arguments
- **Finding**: confirmed and stable. Invoked as
  `pytest tests/unit/reflections tests/unit/hooks`, every item reported
  `NODEID_PATH=tests/unit/reflections/test_daily_log_aggregator.py`,
  `PARENT=tests/unit/reflections`, and an `item.path`-derived value **identical**
  to the nodeid path. The nodeid is rootdir-relative regardless of the
  invocation arguments.
- **Confidence**: high
- **Impact on plan**: the hook can resolve the directory from `item.nodeid`
  alone. `item.path` is the more idiomatic modern accessor but requires
  `config.rootpath` to relativize; both are correct, and the plan uses
  `item.nodeid` to keep the hook's signature unchanged and its input identical
  to the string the guard audits from `git ls-files`. Keeping Path A and Path B
  on the same string shape is the property #3010 exists to protect.

### spike-6: Measure the full proposed end state
- **Assumption**: "the combined change drains the baseline to empty"
- **Method**: prototype — replay `DIRECTORY_MAP ∪ whole-token basename` over
  all 838 files
- **Finding**:

  | Metric | Before | After |
  |---|---|---|
  | files with ≥1 derived marker | 284 | **327** (+43) |
  | derived marker applications | 284 | **331** (+47) |
  | files losing a derived marker | — | **1** |
  | `tests/unit/reflections/` on `reflections` | 2 / 20 | **20 / 20** |
  | `tests/integration/reflections/` on `reflections` | 0 / 2 | **2 / 2** |
  | packages still subject to R2 | 4 | **1** (`memory_extraction`, uniform → no violation) |

  Per-marker census: `reflections` 28 → 48, `sessions` 43 → 61,
  `messaging` 61 → 67, `sdlc` 85 → 89, `config` 6 → 5. Every other marker
  unchanged. **The single file that loses a marker is
  `tests/unit/reflections/test_pm_briefings_no_slots_configured.py`, which loses
  `config` and gains `reflections`.**
- **Confidence**: high
- **Impact on plan**: `KNOWN_MISTAGS` drains to empty, and the one marker
  removal is a named, measured, deliberate policy decision rather than a
  side effect (see Open Questions).

## Data Flow


Two paths read the same resolver. That is the invariant #3010 established, and
this plan must not break it.

**Path A — marker assignment, at collection time**

1. **Entry point**: `pytest` collects test items.
2. `tests/conftest.py::pytest_collection_modifyitems(items)` runs, ahead of
   pytest's internal `deselect_by_mark`.
3. For each item it takes `item.nodeid.split("::")[0]` → a rootdir-relative
   path such as `tests/unit/reflections/test_pm_briefings_builder.py`
   (spike-5). **Today it discards everything but the basename.**
4. It calls `tests/marker_map.py::resolve_marker(basename)` → a single marker
   or `None`, and calls `item.add_marker(...)` when non-`None`.
5. **Output**: pytest's `deselect_by_mark` snapshots
   `{m.name for m in item.iter_markers()}` — explicit `pytestmark` plus
   whatever step 4 added — and applies `-m`.

**Path B — the guard, at test time**

1. **Entry point**: `tests/unit/test_feature_map_markers.py`, or
   `python tests/marker_map.py --audit` on a bare interpreter.
2. `iter_test_files()` shells `git ls-files 'tests/**/test_*.py' 'tests/test_*.py'`
   → the same rootdir-relative path strings Path A sees, from the index rather
   than the filesystem.
3. `check_r1` / `check_r2` / `check_r3` call the **same** `resolve_marker`, and
   `_partition_packages` groups by full parent path.
4. **Output**: `(violations, new_mistags, stale_exemptions)`, bracketed against
   `KNOWN_MISTAGS` in both directions.

**What changes.** Step A3 stops discarding the directory and step A4 applies a
*set* of markers. Step B3's rules change shape. Both keep calling one function
in `tests/marker_map.py`. The failure mode this ordering prevents — Path A and
Path B disagreeing about what a file resolves to, so the guard certifies a
marker the collector never applied — is the reason `tests/marker_map.py` exists
and is the single most important thing not to regress.

## Why Previous Fixes Failed


No previous attempt to drain this baseline exists, so there is no failure to
analyse. What the table below records instead is why the *two obvious* fixes,
both surveyed in the issue, are the wrong trade — because a builder who has not
read the issue will reach for them first, and because the issue's own reason for
rejecting the first one is factually wrong.

| Candidate | What it would do | Why it is not the answer |
|-----------|------------------|--------------|
| Rename the files | Prefix each violating basename with its package name: `test_pm_briefings_no_slots_configured.py` → `test_reflections_pm_briefings_no_slots_configured.py` | **Works, for 19 of 21 — and is still the wrong trade.** The issue body claims it cannot work because `config` beats `reflection`; that is false (`reflections` 45, `reflection` 46, `config` 47, so the renamed file resolves to `reflections`). It genuinely fails only for `test_sdlc_progress_check.py` and `test_sdlc_upvote_lanes.py`, where `sdlc` at index 16 wins. The real objections are the ones renaming cannot answer: it touches 19 files to encode the directory name a second time, it fixes neither R2 nor R3, and it must be repeated by hand for **every file anyone ever adds** to a themed package, enforced by nothing but a guard entry after the fact. It converts a structural defect into a naming convention. |
| Add ~9 narrow `FEATURE_MAP` keys | One key per offending basename | Three of the nine must be hand-placed *ahead of* `config`, `sdlc`, and `validation` to win. That is more ordering-sensitive hand-placement — the defect restated as the remedy. It also scales with file count forever: every new file in `reflections/` needs another key. |

The correction matters beyond bookkeeping: a reviewer who checks the issue's
index claim will find it wrong and may conclude the whole "renaming is blocked"
premise was invented. It was not — it is right for 2 files and wrong for 19, and
the case against renaming rests on maintenance cost rather than impossibility.

**Root cause pattern:** every one of the three mistag mechanisms is a symptom of
deriving a semantic property (what feature is this test about?) from an
*ordered substring scan of a filename*. #3184 removed one mechanism by making
the stem anchored. This plan removes the remaining two by making the signal
structural: an exact-match directory lookup that cannot collide, and a
whole-token basename match that cannot fragment.

## Architectural Impact


- **New dependencies**: none. `tests/marker_map.py` must stay standard-library
  only and must not import `pytest` — it runs on a bare interpreter
  (`python tests/marker_map.py --audit`). The new code is `dict` lookups and
  `pathlib`.
- **Interface changes**: `resolve_marker(basename) -> (marker, key)` stays, as
  the basename-only resolver used by the guard's rules and by every existing
  fixture. A new `resolve_markers(path) -> frozenset[str]` becomes the
  path-aware entry point the collection hook calls. `tests/conftest.py`'s hook
  signature is unchanged.
- **Coupling**: **reduced.** A file's marker stops depending on the insertion
  position of unrelated `FEATURE_MAP` keys. Directory resolution is an exact
  dict lookup, so it is order-free by construction; whole-token basename
  matching removes the remaining class of accidental collisions (spike-4
  measured the ordering-dependence it removes: two fragment matches, nothing
  else).
- **Data ownership**: `tests/marker_map.py` remains the sole owner of marker
  resolution. It gains a second table, `DIRECTORY_MAP`, keyed by exact
  directory name.
- **Reversibility**: high. The change is confined to two test-infrastructure
  files plus their guard and docs. Nothing in `agent/`, `bridge/`, `tools/`, or
  `worker/` is touched, no Popoto model changes, no migration. Reverting the
  commit restores the previous markers exactly.
- **Blast radius**: `tests/marker_map.py`, `tests/conftest.py`,
  `tests/unit/test_feature_map_markers.py`, plus the prose that describes them:
  `docs/features/feature-map-marker-guard.md` and `tests/README.md`.
  **No production code imports `tests/marker_map.py`.** `git grep -ln marker_map`
  returns exactly three code files — `tests/conftest.py`, `tests/marker_map.py`,
  `tests/unit/test_feature_map_markers.py` — and the rest are documents.
  `tests/README.md` additionally publishes per-marker test *counts*
  (`sdlc` 516, `messaging` 327, `sessions` 293) that this change moves, so it is
  a required documentation update rather than an optional one.
- **Systemic risk worth naming**: the marker set is a *selection* mechanism, and
  widening it widens what `-m X` collects. 43 files gain a marker, so
  `pytest -m sessions` grows from 43 to 61 files. That is the intended gain, but
  it also means anyone using `-m "not X"` to *exclude* work will exclude more
  than before. No CI path in this repo uses a negated feature marker
  (`addopts` carries none, and `scripts/pytest-clean.sh` passes markers through
  only when a caller supplies them), so the effect is confined to interactive use.

## Appetite


**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (one is mandatory: the single-marker-removal policy call in
  Open Questions, which is the only decision a human has to make)
- Review rounds: 2+ (the change edits the guard that is meant to prevent this
  class of change from being wrong, so the review has to establish that the
  replacement rules bite — see the mutation requirements in Verification)

Small in code — the resolver change is on the order of 40 lines — but Medium in
alignment. The work retires two of the three rules of a guard that shipped
yesterday, and "we deleted the rule" needs an argument a reviewer accepts, not
just a green suite.

## Prerequisites


No external prerequisites. Both blocking issues are merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #3010's guard on `main` | `test -f tests/marker_map.py && test -f tests/unit/test_feature_map_markers.py` | The baseline to drain, and the before/after measurement instrument |
| #3184's anchored stem on `main` | `python -c "from tests.marker_map import _stem; assert _stem('test_test_judge.py') == 'test_judge'"` | The stem this plan builds on; an unanchored stem changes every measurement |
| Clean baseline before any edit | `python tests/marker_map.py --audit` | Must print `OK: 25 known, baselined violation(s); 0 new, 0 stale.` before work starts |

## Solution


### Key Elements

- **`DIRECTORY_MAP`** — a second table in `tests/marker_map.py` mapping an
  **exact** test-package directory name to a marker. Exact-match, so it is
  order-free: adding an entry can never shadow or be shadowed by another. This
  is the answer to the issue's objection that "more ordering-sensitive
  hand-placement is the defect, not the remedy" — these entries carry no
  ordering at all.
- **Whole-token basename matching** — `resolve_marker` stops matching bare
  substrings and requires the winning `FEATURE_MAP` key to appear as a
  contiguous run of `_`-delimited tokens. The machinery already exists as
  `_whole_token_match`, written for rule R3; this promotes it from detector to
  semantics.
- **`resolve_markers(path) -> frozenset[str]`** — the new single point of truth,
  returning the **union** of every marker declared by the file's ancestor
  package directories and the marker its basename resolves to. Additive, never
  replacing.
- **A reworked guard** — R1 and R3 retire because their defect classes become
  structurally impossible; R2 survives, narrowed; a new R4 guards the one thing
  the new mechanism can silently get wrong.
- **An empty `KNOWN_MISTAGS`** — all 24 entries deleted, which the guard's own
  stale-exemption assertion (#3031) forces to happen only if the violations are
  genuinely gone.

### Flow

`pytest -m reflections` → collection hook reads
`tests/unit/reflections/test_pm_briefings_builder.py` → **directory
`reflections` → marker `reflections`** ∪ basename → ∅ → `item.add_marker(reflections)`
→ pytest's `deselect_by_mark` sees it → **test runs**.

Today the same journey stops at "basename → ∅ → no marker → deselected".

### Technical Approach

**1. `DIRECTORY_MAP`, exact-match, order-free.**

```python
DIRECTORY_MAP: dict[str, str] = {
    "reflections": "reflections",
    "bridge": "messaging",
    "sdlc_router_decision": "sdlc",
    "sdlc_session_ensure": "sdlc",
    "valor_telegram": "messaging",
    "worktree_manager": "git",
    "hooks": "sdlc",
    "session_runner": "sessions",
    "output_handler": "messaging",
}
```

The first six reproduce exactly what the directory names resolve to under
today's substring scan, so seeding them changes nothing. The last three are
new, and they are what drains the two R2 entries: once `hooks` and
`session_runner` declare an intent, their lone-drifting siblings are no longer
drift, they are files that carry both the package marker and their own. All
three markers are already registered in `pyproject.toml`.

`tests/unit/memory_extraction/` is deliberately **left out**: no `memory`
marker exists, and inventing one is a `pyproject.toml` + docs change belonging
to whoever wants that selector. Its five files resolve uniformly to no marker,
so R2 stays green on it and R4 (below) records the omission with a reason.

**2. Whole-token basename matching.**

`resolve_marker` switches from `if pattern in stem` to
`if _whole_token_match(pattern, stem)`. Measured cost (spike-4): two files lose
a marker, zero change marker. One of the two,
`tests/unit/test_long_task_checkpointing.py`, is restored deliberately by adding
`"checkpointing": "validation"` to `FEATURE_MAP` — placeable **anywhere** in the
dict, because with whole-token matching `checkpoint` no longer competes for it.
That single key is the demonstration that the new semantics make targeted keys
safe, where the issue correctly judged them unsafe under substring-first-hit.

**3. `resolve_markers(path)`, the union.**

Walk the path's parent components from the file outward, stopping at the first
`KNOWN_ROOT_DIRS` name; collect each component's `DIRECTORY_MAP` marker; union
with the basename marker. Returns a `frozenset[str]`, possibly empty. Walking
outward rather than reading only the immediate parent costs nothing today (every
themed package is exactly one level deep) and means a future
`tests/unit/reflections/briefings/` inherits `reflections` instead of silently
falling back to basename-only resolution.

`resolve_marker(basename)` is **kept unchanged in signature** — the guard's rules
and roughly a dozen existing fixtures call it, and Path A/Path B share it.

**4. `tests/conftest.py::pytest_collection_modifyitems`.**

```python
def pytest_collection_modifyitems(items):
    for item in items:
        for marker_name in resolve_markers(item.nodeid.split("::")[0]):
            item.add_marker(getattr(pytest.mark, marker_name))
```

`item.nodeid` is rootdir-relative and carries the full directory path regardless
of the invocation arguments (spike-5). Using it rather than `item.path` keeps
Path A's input byte-identical to the `git ls-files` strings Path B audits, which
is the property that makes the guard's verdict mean anything.

**5. The guard rework — and the honest argument for it.**

The objection this plan must answer is the one recorded in
`docs/features/feature-map-marker-guard.md`: making the directory authoritative
"makes rule R1 tautological (the file's marker would be *defined* as the
directory's marker, so 'they match' proves nothing)."

That is correct, and it is the point. **R1 is a detector for a defect that
directory-authoritative assignment makes impossible.** The distinguishing
question is not "does R1 still prove something?" but "can a file still land in
`tests/unit/reflections/` without the `reflections` marker?" Today the answer is
yes, unless someone adds a `KNOWN_MISTAGS` entry. After this change the answer
is no, by construction. A structural guarantee is strictly stronger than a test
that detects violations of it, and #3184 is the precedent: it deleted mechanism
3 rather than exempting it.

Hollowing out would be deleting R1 while the defect remained reachable. So the
plan draws the line explicitly, and pays for it with proof:

| Rule | Disposition | Why |
|---|---|---|
| **R1** directory intent | **Retire** | Defect class eliminated. Replaced by a *real-collection* proof: a mutation test that plants a file with a colliding basename inside a resolving package, runs actual pytest collection, and asserts the directory marker is applied. That test fails if the mechanism is removed; R1 never tested the mechanism, only the filenames. |
| **R2** sibling uniformity | **Keep, narrowed** | Still meaningful for packages absent from `DIRECTORY_MAP`, where no intent is declared. Its scope shrinks from 4 packages to 1 (`memory_extraction`). |
| **R3** whole-token match | **Retire** | Defect class eliminated: the resolver *is* whole-token, so `resolve_marker` and `resolve_marker_whole_token` are the same function. Replaced by direct `_whole_token_match` unit fixtures (already present) plus a fixture pinning `test_long_task_checkpointing.py` to `validation` via the `checkpointing` key, which is the file the semantics change would otherwise have moved. |
| **R4** package declaration *(new)* | **Add** | Guards what the new mechanism *can* silently get wrong: a new themed package directory added with no `DIRECTORY_MAP` entry falls back to basename-only resolution and quietly reproduces the whole original defect. R4 asserts every non-root test package is either in `DIRECTORY_MAP` or in a small reasoned `UNMAPPED_PACKAGES` dict. It is bracketed in both directions exactly as `KNOWN_MISTAGS` is (#3031): an entry for a package that no longer exists, or now maps, fails. |

R4 is what keeps this a guard rather than a one-time cleanup. Without it the
drain is permanent for today's files and worthless for tomorrow's.

**6. Drain `KNOWN_MISTAGS` to `{}`.**

The dict stays in the module — empty, with its docstring rewritten to say it is
the exemption mechanism of last resort and that it is currently unused. Deleting
the mechanism entirely would remove the #2805 path-keying lesson and the #3031
bracketing along with it, and the next person needing an exemption would invent
a worse one.

**7. The before/after census, committed as evidence.**

Acceptance criterion 3 ("no test file loses a marker it currently has") is a
claim about all 838 files, so it is checked mechanically, not by inspection.
`python tests/marker_map.py --report` already prints `path<TAB>marker` for every
tracked file. It is extended to print the full marker *set*
(`path<TAB>marker1,marker2`) and the PR carries the `diff` of before-report
against after-report. The expected diff is exactly the 48 gaining files and the
single losing file from spike-6 — no other line moves.

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
