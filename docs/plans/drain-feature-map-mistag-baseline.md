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
both surveyed in the issue, are dead on arrival — because a builder who has not
read the issue will reach for them first.

| Candidate | What it would do | Why it fails |
|-----------|------------------|--------------|
| Rename the files | `test_pm_briefings_no_slots_configured.py` → `test_reflections_pm_briefings_no_slots_configured.py` | First-hit-wins ordering is unchanged. `config` (index 45) still beats `reflection` (index 52), so the renamed file still resolves to `config`. **The ordering trap that motivated the guard also blocks the obvious fix.** Verified at `8e62c3a50`. |
| Add ~9 narrow `FEATURE_MAP` keys | One key per offending basename | Three of the nine must be hand-placed *ahead of* `config`, `sdlc`, and `validation` to win. That is more ordering-sensitive hand-placement — the defect restated as the remedy. It also scales with file count forever: every new file in `reflections/` needs another key. |

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
  `tests/unit/test_feature_map_markers.py`, `docs/features/feature-map-marker-guard.md`.
  Nothing imports `tests/marker_map.py` outside `tests/`
  (verified: `git grep -l marker_map` returns those three files only).
- **Systemic risk worth naming**: the marker set is a *selection* mechanism, and
  widening it widens what `-m X` collects. 43 files gain a marker, so
  `pytest -m sessions` grows from 43 to 61 files. That is the intended gain, but
  it also means anyone using `-m "not X"` to *exclude* work will exclude more
  than before. No CI path in this repo uses a negated feature marker
  (`addopts` carries none, and `scripts/pytest-clean.sh` passes markers through
  only when a caller supplies them), so the effect is confined to interactive use.

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
