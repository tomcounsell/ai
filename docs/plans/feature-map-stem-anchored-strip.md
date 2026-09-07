---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3184
last_comment_id:
---

# FEATURE_MAP stem: anchored prefix/suffix strip

## Problem

`tests/marker_map.py::_stem()` reduces a test module basename to the string that
gets substring-matched against `FEATURE_MAP`. It does that with a **global**
`str.replace`, not an anchored prefix strip:

```python
return basename.replace("test_", "").replace(".py", "")
```

`str.replace` removes *every* occurrence of `test_`, so a basename that contains
`test_` a second time has that occurrence eaten out of the middle of the name.
The mangled stem is then matched against `FEATURE_MAP`, and a key written for
that exact file no longer matches it.

**Current behavior:**

Five tracked test files contain `test_` twice in their basename. All five get a
mangled stem; two of them lose a marker that `FEATURE_MAP` explicitly maps them to:

| File | Stem today | Stem intended | Marker today | Marker intended |
|---|---|---|---|---|
| `tests/tools/test_test_judge.py` | `judge` | `test_judge` | none | `tools` (key `test_judge`) |
| `tests/unit/test_validate_test_impact.py` | `validate_impact` | `validate_test_impact` | none | `validation` (key `validate_test_impact`) |
| `tests/unit/test_conftest_autouse_monkeypatch_order.py` | `confautouse_monkeypatch_order` | `conftest_autouse_monkeypatch_order` | none | none |
| `tests/unit/test_conftest_isolation_guards.py` | `confisolation_guards` | `conftest_isolation_guards` | none | none |
| `tests/unit/test_test_redis_server_resolution.py` | `redis_server_resolution` | `test_redis_server_resolution` | none | none |

The `conftest_` cases are the clearest illustration: the strip cuts `test_` out
of the middle of the word `conftest`, yielding `conf...`. Any future
`FEATURE_MAP` key mentioning `conftest` would silently never match those files.
So the defect is live in two files and latent in the other three.

The marker-regression guard shipped by #3010 cannot see this. Its three rules
(directory intent, sibling uniformity, whole-token match) all need a
contradicting signal, and a mangled stem surfaces only as an unmarked file whose
directory is a known root (`tools/`, `unit/`) and whose siblings are equally
unmarked. #3010 named this "mechanism 3" and deliberately left it unfixed,
because correcting it changes marker assignment for two files, which #3010's own
Success Criteria forbade.

**Desired outcome:**

`_stem` performs an anchored prefix/suffix strip. All five basenames resolve to
their true stem, `test_test_judge.py` gains the `tools` marker and
`test_validate_test_impact.py` gains the `validation` marker, no other file's
marker changes, and the guard's baseline audit stays green with no
`KNOWN_MISTAGS` edits.

## Freshness Check

**Baseline commit:** `de229ee469fe7b2b176b371b6cd19646fce401a0`
**Issue filed at:** 2026-09-06T08:09:28Z
**Disposition:** **Minor drift** — the change site moved; every claim and every
measurement in the issue re-verified true at the baseline commit.

**File:line references re-verified:**

- `tests/conftest.py::pytest_collection_modifyitems` — the issue cites the stem
  expression as living here. **Drifted.** PR #3190 (issue #3010) merged after the
  issue was filed and extracted the expression into
  `tests/marker_map.py::_stem` (line 290). `tests/conftest.py:1114-1119` now
  imports `resolve_marker` and calls it; it contains no stem logic of its own.
  The claim holds verbatim — the expression is byte-for-byte the one the issue
  quotes, just relocated. **This is a net improvement for this work:** #3010's
  own docstring says the extraction gives #3184 "exactly one line to change
  instead of two."
- `FEATURE_MAP` keys `"test_judge"` and `"validate_test_impact"` — both still
  present, now in `tests/marker_map.py`. Still unreachable under the current stem.

**Cited sibling issues/PRs re-checked:**

- **#3010** — **CLOSED** 2026-09-06T12:58:06Z via PR #3190. This was listed in
  the issue as a pre-requisite ("#3010 should land first"). It has landed, so the
  pre-requisite is satisfied. Its resolution delivered exactly what the issue
  anticipated: a single `_stem` call site and a `--report` / `--audit` CLI that
  makes the before/after measurement mechanical.
- **#3175** — still **OPEN** ("Drain the FEATURE_MAP mistag baseline"). Adjacent
  but disjoint; see Prior Art.
- **#2879** — **CLOSED** ("Split the largest test files into per-class modules").
  Background only: file splitting is how basenames of this shape get created, so
  the population can grow again. Not a blocker.

**Commits on main since issue was filed (touching referenced files):**

- `d14685728` "FEATURE_MAP marker-regression guard: catch silently-mistagged test
  files (#3190)" — **changed the location of the root cause, not the root cause.**
  It moved `FEATURE_MAP` and the resolver into `tests/marker_map.py`, added
  `KNOWN_MISTAGS`, the three-rule audit, and — critically for this plan — two
  tripwire fixtures that assert the *buggy* behavior (see Test Impact).

**Re-measurement at the baseline commit** (the issue measured at `f3594dd23`;
every number was re-derived here against current main):

| Measure | Issue (at `f3594dd23`) | Re-measured (at `de229ee46`) |
|---|---|---|
| Tracked test files | 834 | **836** |
| Basenames with `count("test_") > 1` | 5 (listed) | **5, the same five** |
| Files whose `(marker, key)` changes under an anchored strip | 2 | **2, the same two** |
| Files that **lose** a marker | 0 | **0** |
| Guard audit before the fix | n/a (guard did not exist) | `OK: 25 known, baselined violation(s); 0 new, 0 stale.` |
| Guard audit after simulating the fix | n/a | **25 violations, 0 new, 0 stale** |

The last row is the load-bearing new finding: because `tests/tools/` and
`tests/unit/` are both in `KNOWN_ROOT_DIRS`, neither newly-marked file is subject
to rule R1 or R2, and both satisfy R3 as whole-token matches. **`KNOWN_MISTAGS`
needs no edit in either direction** — no new violation appears and no existing
entry goes stale.

**Active plans in `docs/plans/` overlapping this area:** none. No plan document
in `docs/plans/` references `marker_map` or `FEATURE_MAP`.

**Notes:** The `.py` half of the expression is also a global replace, but no
tracked basename contains `.py` twice, so anchoring the suffix is a
correctness-by-construction improvement with zero measured effect today.

## Prior Art

- **#3010 / PR #3190** — *FEATURE_MAP marker-regression guard.* **Merged**
  2026-09-06. Built the guard this plan runs under. It found this defect during
  critique round 2, filed it as #3184, and then took three deliberate steps to
  stop a builder from "fixing it in passing": (a) the stem expression is copied
  verbatim rather than re-derived, (b) `_stem`'s comment explicitly forbids
  `removeprefix`/`removesuffix`/anchored regex, (c) two fixtures assert the two
  affected files resolve to `(None, None)`. **All three of those pins are the
  work item of this plan** — they were correct for #3010 and are now the thing
  standing between the repo and the fix.
- **#3175** — *Drain the FEATURE_MAP mistag baseline: 21 test files carry the
  wrong marker or none.* **OPEN.** Adjacent but **disjoint**: its scope is the 24
  paths in `KNOWN_MISTAGS` (25 rule violations), driven by mechanisms 1 and 2.
  Neither of this plan's two files appears in `KNOWN_MISTAGS`, and the simulated
  fix leaves that list byte-identical. The two efforts do not collide.
- **#2879** — *Split the largest test files into per-class modules.* **CLOSED.**
  Relevant as context for recurrence: splitting a module named `test_judge.py`
  into `tests/tools/test_test_judge.py` is exactly how a double-`test_` basename
  is born, so the population is not closed and a fix at the stem is more durable
  than renaming today's five files.

No prior attempt to fix the stem expression itself exists. This is the first.

## Research

No external research performed, and none needed. The change is purely internal:
one Python standard-library string operation inside the repo's own pytest
collection hook. No external library, API, service, or ecosystem pattern is
involved.

One version fact was verified rather than assumed, because `tests/marker_map.py`
documents that it must run on a bare interpreter with no venv:

- `str.removeprefix` / `str.removesuffix` were added in **Python 3.9**.
- `.python-version` pins **3.14**; `pyproject.toml` sets `requires-python = ">=3.11"`;
  the macOS system interpreter (`/usr/bin/python3`, the realistic "bare
  interpreter" case) is **3.9.6**.

All three clear the 3.9 floor, so the module keeps its no-venv guarantee.

## Data Flow

**Path A — marker assignment, at collection time:**

1. **Entry point**: pytest collects a test item.
2. **`tests/conftest.py::pytest_collection_modifyitems` (line 1114)**: takes
   `item.nodeid`, splits off the file path, takes the basename
   (e.g. `test_test_judge.py`).
3. **`tests/marker_map.py::resolve_marker` (line 309)**: calls `_stem(basename)`.
4. **`tests/marker_map.py::_stem` (line 290)**: reduces the basename to a stem.
   **← the one line this plan changes.**
5. **`resolve_marker`** iterates `FEATURE_MAP` in insertion order and returns the
   first key that is a substring of the stem.
6. **Output**: `item.add_marker(getattr(pytest.mark, marker_name))`, or no marker
   when the stem matched nothing.

**Path B — the guard, at test time:**

1. **Entry point**: `tests/unit/test_feature_map_markers.py`, or
   `python tests/marker_map.py --audit` on a bare interpreter.
2. **`run_audit()` (line 496)** enumerates tracked files via `git ls-files` and
   runs rules R1/R2/R3.
3. **R1 (line 393) and R2 (line 421) call `resolve_marker` on *directory* names**,
   not only on file basenames — `resolve_marker(Path(pkg_path).name)`. `_stem` is
   therefore applied to strings like `reflections`, `session_runner`, `hooks`.
4. **R3 (line 479)** calls both `resolve_marker` and `resolve_marker_whole_token`,
   the latter also routing through `_stem` (line 338).
5. **Output**: violations bracketed against `KNOWN_MISTAGS` into "new" and "stale".

Step 3 is the non-obvious blast radius and the reason this plan's verification
runs the full audit rather than only checking the five file basenames: a change
to `_stem` reaches directory-name resolution too. Measured result: no tracked test
directory name begins with `test_` or ends with `.py`, so directory resolution is
provably unaffected, and the simulated audit confirms it (0 new, 0 stale).

## Architectural Impact

- **New dependencies**: none. `str.removeprefix`/`removesuffix` are stdlib, and
  the module keeps its "standard library only, no pytest import" property.
- **Interface changes**: none. `_stem` is private, its signature
  (`str -> str`) is unchanged, and its two callers (`resolve_marker`,
  `resolve_marker_whole_token`) are both inside `tests/marker_map.py`.
- **Coupling**: unchanged. #3010 already collapsed Path A and Path B onto one
  implementation; this plan edits that one implementation.
- **Data ownership**: unchanged.
- **Reversibility**: trivial. One line plus its comment, two test assertions, one
  doc paragraph. A revert is a single-commit operation with a mechanical
  before/after check (`python3 tests/marker_map.py --report`).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the desired outcome is measured and unambiguous)
- Review rounds: 1

The coding is one line. The cost is entirely in proving the blast radius is
exactly two files, and in undoing three deliberate pins #3010 placed to prevent
precisely this edit — each of which must be updated with an explanation, not just
deleted.

## Prerequisites

No environment prerequisites — this work needs only a checkout and a Python
interpreter. Its one *sequencing* prerequisite, #3010, is already satisfied
(CLOSED 2026-09-06 via PR #3190).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #3010 landed (single `_stem` call site exists) | `python3 -c "import sys;sys.path.insert(0,'.');from tests.marker_map import _stem;print(_stem('x.py'))"` | The change site this plan edits exists |
| Guard audit green before the change | `python3 tests/marker_map.py --audit` | Establishes the red/green baseline |

## Solution

### Key Elements

- **`_stem` anchoring**: strip `test_` only from the front and `.py` only from
  the end, instead of everywhere in the string.
- **Fixture inversion**: the two `test_stem_fidelity_*` fixtures currently assert
  the mangled outcome as correct. They become assertions of the fixed outcome.
- **Population coverage**: extend the fixtures from 2 of the 5 mangled basenames
  to all 5, so the three currently-latent cases are locked in too.
- **Comment repair**: three prose sites tell a future reader that the global
  replace is intentional and must not be "cleaned up". All three must now say the
  opposite, and say why the change was safe.

### Flow

`pytest` collection → `_stem("test_test_judge.py")` → **`"test_judge"`** (was
`"judge"`) → `FEATURE_MAP["test_judge"]` hits → item tagged `tools` (was
untagged).

### Technical Approach

- The edit in `tests/marker_map.py::_stem`:

  ```python
  return basename.removeprefix("test_").removesuffix(".py")
  ```

  replacing `basename.replace("test_", "").replace(".py", "")`.

- Prefer `removeprefix`/`removesuffix` over a regex. They are exact, allocation-free
  on a miss, need no `re` import (preserving the module's import-light property),
  and read as the operation being described.

- **Do not** also reorder, extend, or prune `FEATURE_MAP`, and **do not** edit
  `KNOWN_MISTAGS`. The measurement says neither is necessary; touching either
  would make the two-file blast radius unprovable.

- The builder must **re-run the measurement rather than trust this plan's
  numbers**. The `--report` CLI makes this a two-command control:

  ```bash
  git stash && python3 tests/marker_map.py --report > /tmp/before.txt && git stash pop
  python3 tests/marker_map.py --report > /tmp/after.txt
  diff /tmp/before.txt /tmp/after.txt
  ```

  The diff must show exactly two lines changed, both gaining a marker.

- The three comment sites to repair, all of which currently forbid this change:
  1. `tests/marker_map.py:296-306` — `_stem`'s docstring and the "Do NOT clean
     this up" comment.
  2. `tests/marker_map.py:293-295` — the sentence "#3184 has exactly one line to
     change instead of two" (now satisfied; restate in the past tense).
  3. `tests/unit/test_feature_map_markers.py:77-82` — the header calling the two
     fixtures "the cheapest possible tripwire for mechanism 3".

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope. `_stem` is a pure two-call string
      expression with no branches and no `try` block; neither `resolve_marker`
      nor `resolve_marker_whole_token` catches anything.

### Empty/Invalid Input Handling
- [ ] Confirm the existing empty/invalid-input fixtures still hold under the new
      stem. Both were re-derived by hand during planning and are unchanged:
      `resolve_marker("")` → `(None, None)` (stem `""` matches no key), and
      `resolve_marker("test_.py")` → `(None, None)` (stem `""` via
      `removeprefix` then `removesuffix`, the same empty stem the global replace
      produced).
- [ ] Confirm a basename that is *only* a suffix, e.g. `.py`, and one with no
      `test_` prefix at all, e.g. `helpers.py`, still resolve without raising.
      Anchored strips are no-ops on a miss, so this is a non-regression check.

### Error State Rendering
- [ ] No user-visible output. The failure surface is the audit CLI, and its
      error rendering (`New mistag(s)...` / `Stale KNOWN_MISTAGS entrie(s)...`)
      is unchanged by this plan and already covered by four synthetic-mistag
      tests (`test_audit_reports_a_synthetic_mistag_r1/r2/r3`,
      `test_audit_reports_stale_exemption`).

## Test Impact

- [ ] `tests/unit/test_feature_map_markers.py::test_stem_fidelity_test_judge` —
      **UPDATE**: currently asserts `resolve_marker("test_test_judge.py") == (None, None)`.
      Must become `== ("tools", "test_judge")`. This fixture exists specifically
      to block this change; inverting it is the point, not a workaround.
- [ ] `tests/unit/test_feature_map_markers.py::test_stem_fidelity_validate_test_impact` —
      **UPDATE**: currently asserts `resolve_marker("test_validate_test_impact.py") == (None, None)`.
      Must become `== ("validation", "validate_test_impact")`.
- [ ] `tests/unit/test_feature_map_markers.py` (lines 77-82, the section header
      comment above those two fixtures) — **UPDATE**: it describes the fixtures as
      a tripwire *against* the anchored strip. Rewrite to describe them as the
      assertion that the anchored strip is in place.
- [ ] `tests/unit/test_feature_map_markers.py` — **ADD** three fixtures covering
      the currently-latent members of the population
      (`test_conftest_autouse_monkeypatch_order.py`,
      `test_conftest_isolation_guards.py`,
      `test_test_redis_server_resolution.py`), asserting the *stem* is now
      un-mangled. These three keep resolving to no marker, so asserting only
      `resolve_marker` would be a vacuous green — assert `_stem` directly.
- [ ] `tests/unit/test_feature_map_markers.py::test_no_violation_outside_known_mistags` —
      **NO CHANGE, verify only.** Measured: 25 violations / 0 new / 0 stale both
      before and after. If this goes red, the blast radius is wider than measured
      and the build must stop rather than edit `KNOWN_MISTAGS`.
- [ ] `tests/unit/test_feature_map_markers.py::test_resolve_marker_empty_string`,
      `::test_resolve_marker_test_dot_py`, `::test_resolve_marker_no_underscores`,
      `::test_resolve_marker_exact_key_match`,
      `::test_youtube_transcription_retagged_to_tools` — **NO CHANGE, verify only.**
      All five re-derived under the anchored stem during planning and unchanged.
- [ ] `tests/tools/test_test_judge.py` and `tests/unit/test_validate_test_impact.py` —
      **NO CHANGE to their contents.** They gain a marker at collection time. Both
      `tools` and `validation` are registered in `pyproject.toml [tool.pytest.ini_options] markers`,
      and no CI workflow selects tests by `-m`, so gaining a marker changes no
      selection anywhere. Verify both still collect and pass.

## Rabbit Holes

- **Renaming the five files instead of fixing the stem.** Tempting because it
  needs no logic change, but it treats the symptom: #2879-style module splits
  keep manufacturing double-`test_` basenames, so the population reopens on the
  next split. Fix the expression.
- **Rewriting the stem as a regex.** An anchored `re.sub` is equivalent but drags
  in an `re` import against the module's deliberate import-light constraint, and
  is harder to read than the two named string methods.
- **Draining `KNOWN_MISTAGS` while you are in the file.** It is 24 entries of
  adjacent-looking work belonging to #3175. The measurement shows this change
  needs zero edits there; any edit makes the two-file blast radius unprovable.
- **"Improving" `FEATURE_MAP` ordering or keys.** First-hit-wins insertion order
  is load-bearing for 836 files. Out of scope, and it would confound the
  before/after `--report` diff that proves this change is correct.
- **Chasing the `docs/features/README.md` "24 pre-existing violations" wording.**
  The audit prints 25 violations across 24 paths. The index line is about the
  baseline's path count and is not made wrong by this change. Leave it.

## Risks

### Risk 1: The change moves more than the two measured files
**Impact:** Tests silently gain or lose markers across the suite; the guard's
baseline stops describing reality.
**Mitigation:** The `--report` before/after diff is a complete enumeration over
all 836 tracked files, not a spot check. It is a Verification row, and the
Success Criteria require the diff to be exactly two lines. Independently,
`test_no_violation_outside_known_mistags` fails on any new or stale violation.

### Risk 2: A builder "fixes" the pins instead of updating them
**Impact:** Deleting the two `test_stem_fidelity_*` fixtures rather than
inverting them removes the only assertion that the stem is anchored, so a future
revert would be silent.
**Mitigation:** Test Impact names both fixtures with an explicit UPDATE
disposition and the exact expected tuples. A Verification row asserts both
fixtures still exist by name after the change.

### Risk 3: The comments keep forbidding the change that shipped
**Impact:** The highest-cost failure mode. `_stem`'s comment currently instructs
future readers not to make this edit. Left in place, it either gets obeyed and
the fix is reverted, or it is ignored and the module's comments stop being
trustworthy.
**Mitigation:** All three prose sites are enumerated in the Technical Approach
with file:line, tracked as Documentation tasks, and covered by an anti-criterion
Verification row that greps for the stale prohibition.

### Risk 4: The two newly-marked files start being selected by a marker filter
**Impact:** A `-m tools` or `-m validation` run picks up tests it did not before.
**Mitigation:** Verified at plan time: both markers are already registered in
`pyproject.toml`, and no `.github/workflows/` file selects by `-m`. The effect is
confined to manual local invocations, where the new behavior is the correct one.

## Race Conditions

No race conditions identified. `_stem` is a pure function over a string with no
I/O, no shared mutable state, and no concurrency. It is called synchronously
during pytest collection (single-threaded, before any test runs) and
synchronously inside the audit. The audit's only external read is a
`subprocess.run(["git", "ls-files", ...])` that this plan does not touch.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3175] Draining the `KNOWN_MISTAGS` baseline (24 paths, 25 rule
  violations, mechanisms 1 and 2). This plan measured that its change leaves that
  list byte-identical — 0 new violations, 0 stale entries — so no edit there is
  needed or permitted here.
- [SEPARATE-SLUG #3175] Reordering or extending `FEATURE_MAP` to give the three
  still-unmarked members of the population (`test_conftest_*.py`,
  `test_test_redis_server_resolution.py`) a marker. Their stems become correct
  under this plan; whether they *deserve* a marker is a mapping question that
  belongs with the baseline drain.

## Update System

No update system changes required. This change is confined to the test suite's
collection hook and its guard module. It adds no dependency, no config file, no
service, and no migration, so `scripts/remote-update.sh` and the `/update` skill
need no changes and nothing new propagates to other machines.

## Agent Integration

No agent integration required. `tests/marker_map.py` is test infrastructure. It
declares no entry point in `pyproject.toml [project.scripts]`, the Telegram
bridge does not import it, and the agent reaches it only the way any developer
does — by running pytest or the module's own `--audit` CLI through Bash, both of
which already work and are unchanged by this plan.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/feature-map-marker-guard.md` — the "Mechanism 3,
      mangled stem" entry (currently around lines 52-64) states the stem is a
      global `str.replace` and that correcting it is "deliberately left unfixed
      here", pointing at #3184. Rewrite it to describe the anchored strip as the
      shipped behavior, record that `test_test_judge.py` now carries `tools` and
      `test_validate_test_impact.py` now carries `validation`, and state that the
      guard's three rules still cannot see a mangled stem — which is why the
      direct `_stem` fixtures exist.
- [ ] No new file in `docs/features/`, and no `docs/features/README.md` index
      change: the guard already has an index entry whose description stays
      accurate.

### Inline Documentation
- [ ] Rewrite `_stem`'s docstring and comment in `tests/marker_map.py`
      (lines ~290-308). It currently forbids exactly this change
      ("Do NOT 'clean this up' with a prefix/suffix-stripping helper"). Replace
      with the anchored rationale and the measured two-file effect, and drop the
      now-satisfied "#3184 has exactly one line to change" note.
- [ ] Rewrite the section header above the stem-fidelity fixtures in
      `tests/unit/test_feature_map_markers.py` (lines 77-82) so it describes the
      fixtures as pinning the anchored strip rather than guarding against it.

## Success Criteria

- [ ] `_stem` uses `removeprefix("test_")` and `removesuffix(".py")`; no
      `str.replace` remains in it.
- [ ] `python3 tests/marker_map.py --report` diffed before vs. after shows
      **exactly two changed lines**: `tests/tools/test_test_judge.py` gaining
      `tools`, `tests/unit/test_validate_test_impact.py` gaining `validation`.
- [ ] **Zero** test files lose a marker they carried before the change.
- [ ] `python3 tests/marker_map.py --audit` exits 0, reporting 25 known
      violations, 0 new, 0 stale — with `KNOWN_MISTAGS` unmodified.
- [ ] Both `test_stem_fidelity_*` fixtures still exist, inverted to assert the
      fixed tuples.
- [ ] All five mangled basenames have a committed fixture asserting their
      un-mangled stem.
- [ ] No comment or docstring anywhere still instructs the reader that the global
      `str.replace` is intentional.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates and does not build directly.

### Team Members

- **Builder (stem)**
  - Name: `stem-builder`
  - Role: The `_stem` change, the fixture updates, and the inline comment repair
  - Agent Type: builder
  - Resume: true

- **Validator (blast radius)**
  - Name: `blast-radius-validator`
  - Role: Independently re-derive the before/after `--report` diff and the audit
    result; confirm the two-file claim without reusing the builder's output
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `guard-documentarian`
  - Role: `docs/features/feature-map-marker-guard.md` mechanism-3 rewrite
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Capture the pre-change baseline
- **Task ID**: baseline-report
- **Depends On**: none
- **Validates**: n/a (measurement task)
- **Assigned To**: stem-builder
- **Agent Type**: builder
- **Parallel**: false
- On an unmodified checkout, run `python3 tests/marker_map.py --report > /tmp/marker_before.txt`
- Run `python3 tests/marker_map.py --audit` and record its exact output
- Commit nothing; these artifacts are the control for task 3

### 2. Anchor the stem expression
- **Task ID**: build-stem
- **Depends On**: baseline-report
- **Validates**: tests/unit/test_feature_map_markers.py
- **Informed By**: Freshness Check (2 of 836 files move; audit stays green)
- **Assigned To**: stem-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tests/marker_map.py::_stem`, replace the global-replace expression with
  `basename.removeprefix("test_").removesuffix(".py")`
- Rewrite `_stem`'s docstring and comment so they no longer forbid this change
- Do not touch `FEATURE_MAP` or `KNOWN_MISTAGS`

### 3. Prove the blast radius is exactly two files
- **Task ID**: measure-diff
- **Depends On**: build-stem
- **Assigned To**: stem-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `python3 tests/marker_map.py --report > /tmp/marker_after.txt`
- `diff /tmp/marker_before.txt /tmp/marker_after.txt` — must show exactly two
  changed lines, both gaining a marker, none losing one
- Run `python3 tests/marker_map.py --audit` — must print 25 known, 0 new, 0 stale
- **If either check disagrees, stop and report.** Do not edit `KNOWN_MISTAGS` to
  make the audit pass

### 4. Update and extend the stem fixtures
- **Task ID**: build-fixtures
- **Depends On**: measure-diff
- **Validates**: tests/unit/test_feature_map_markers.py
- **Assigned To**: stem-builder
- **Agent Type**: builder
- **Parallel**: false
- Invert `test_stem_fidelity_test_judge` to `("tools", "test_judge")` and
  `test_stem_fidelity_validate_test_impact` to `("validation", "validate_test_impact")`
- Add `_stem` assertions for the three latent basenames
  (`test_conftest_autouse_monkeypatch_order.py`,
  `test_conftest_isolation_guards.py`, `test_test_redis_server_resolution.py`)
- Rewrite the section header comment above them
- Run `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q`

### 5. Independent blast-radius validation
- **Task ID**: validate-blast-radius
- **Depends On**: build-fixtures
- **Assigned To**: blast-radius-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-derive the before/after comparison from `git stash` rather than reusing
  `/tmp/marker_before.txt`, so the control cannot inherit the branch's change
- Confirm exactly two files move and none lose a marker
- Confirm `KNOWN_MISTAGS` is untouched (`git diff` on `tests/marker_map.py`
  shows no line mentioning `Drain tracked by #3175`)
- Confirm both newly-marked test files still collect and pass

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-blast-radius
- **Assigned To**: guard-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite mechanism 3 in `docs/features/feature-map-marker-guard.md`
- Confirm no doc still says the mangled stem is deliberately unfixed

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: blast-radius-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table
- Confirm all Success Criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard audit green, baseline unchanged | `python3 tests/marker_map.py --audit` | output contains `0 new, 0 stale` |
| Guard tests pass | `./scripts/pytest-clean.sh tests/unit/test_feature_map_markers.py -q` | exit code 0 |
| The two files gained their intended markers | `python3 -c "import sys;sys.path.insert(0,'.');from tests.marker_map import resolve_marker as r;assert r('test_test_judge.py')==('tools','test_judge');assert r('test_validate_test_impact.py')==('validation','validate_test_impact');print('OK')"` | exit code 0 |
| All five mangled basenames now un-mangled | `python3 -c "import sys;sys.path.insert(0,'.');from tests.marker_map import _stem as s;assert s('test_test_judge.py')=='test_judge';assert s('test_validate_test_impact.py')=='validate_test_impact';assert s('test_conftest_isolation_guards.py')=='conftest_isolation_guards';assert s('test_conftest_autouse_monkeypatch_order.py')=='conftest_autouse_monkeypatch_order';assert s('test_test_redis_server_resolution.py')=='test_redis_server_resolution';print('OK')"` | exit code 0 |
| Edge cases unchanged | `python3 -c "import sys;sys.path.insert(0,'.');from tests.marker_map import resolve_marker as r;assert r('')==(None,None);assert r('test_.py')==(None,None);assert r('test_sdlc.py')==('sdlc','sdlc');assert r('test_config.py')==('config','config');assert r('test_youtube_transcription.py')==('tools','youtube');print('OK')"` | exit code 0 |
| No global `test_` replace remains in the stem | `grep -c 'replace("test_"' tests/marker_map.py` | match count == 0 |
| Both stem-fidelity fixtures still exist | `grep -c 'def test_stem_fidelity_' tests/unit/test_feature_map_markers.py` | output contains `2` |
| Anti-criterion: `KNOWN_MISTAGS` untouched (#3175 stays out of scope) | `git diff origin/main -- tests/marker_map.py \| grep -c '^[-+].*Drain tracked by #3175'` | match count == 0 |
| Anti-criterion: no comment still forbids the anchored strip | `grep -c 'clean this up' tests/marker_map.py` | match count == 0 |
| Anti-criterion: no doc still calls mechanism 3 unfixed | `grep -c 'left unfixed here' docs/features/feature-map-marker-guard.md` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

**Red-state proof of the three anti-criteria**, measured at `de229ee46` on an
unmodified checkout. Each returns a non-zero count *today*, so each is proven to
bite rather than passing vacuously; each must return `0` after the change:

- `grep -c 'clean this up' tests/marker_map.py` → `1`
- `grep -c 'left unfixed here' docs/features/feature-map-marker-guard.md` → `1`
- `grep -c 'replace("test_"' tests/marker_map.py` → `1`

The `KNOWN_MISTAGS` anti-criterion is the inverse shape: it reads `0` on a clean
tree and must *stay* `0`, so its red-state proof is to add a throwaway edit to a
`Drain tracked by #3175` line and confirm the count becomes non-zero before
reverting it. Paste all four results into the PR description.

Two anchors were rejected during planning for failing exactly this test.
`grep -rn 'Do NOT .clean this up'` returns `0` on the unmodified checkout —
the phrase wraps across two comment lines, so the check would have passed before
the fix and proven nothing. `grep -c 'deliberately'` bites today but is anchored
on a common adverb that a doc rewrite could reintroduce innocently; `left unfixed
here` names the stale claim itself.

## Critique Results

Round 1 — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses, at plan hash `sha256:b72e627f…`, baseline `de229ee46`. Verdict: **NEEDS REVISION** (1 blocker, 6 concerns, 2 nits).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Solution/Technical Approach and Task 5 establish the baseline with `git stash && ... && git stash pop`. `refs/stash` is repo-wide, shared by every worktree; 10+ agent worktrees are live and 3 foreign stash entries exist right now. On a clean tree `git stash` creates no entry and exits 0, so the `&&` chain proceeds and `git stash pop` applies ANOTHER LANE'S stash into this worktree. | pending | Delete both `git stash` instructions. Derive the baseline from git object content: `BASE=$(git merge-base HEAD origin/main)`; `git show "$BASE:tests/marker_map.py" > $WORK/marker_map_base.py`; load it by path with `importlib.util.spec_from_file_location` and call `m._report_lines(m.iter_test_files())`. Runs the OLD resolver over the CURRENT file list with zero working-tree mutation. Run from the repo root: `iter_test_files()` shells to `git ls-files` against the process cwd. |
| CONCERN | Risk & Robustness | Baseline artifacts use fixed absolute paths (`/tmp/before.txt`, `/tmp/marker_before.txt`). Many agents run concurrently against this repo, so two lanes clobber each other and `diff` compares against a foreign baseline. The failure is silent and green-looking. | pending | `WORK=$(mktemp -d)` once in Task 1; reference `"$WORK/before.txt"` / `"$WORK/after.txt"` everywhere. Never a fixed `/tmp/<name>` in a repo where `git worktree list` reports concurrent lanes. Pass `$WORK` forward explicitly to Tasks 3 and 5, or have each task recompute the baseline from git. |
| CONCERN | Risk & Robustness | The `KNOWN_MISTAGS` anti-criterion runs `git diff origin/main`, resolving the remote head at execution time. `main` moves constantly (it advanced twice during this plan's authoring), and #3175 is OPEN and targets `KNOWN_MISTAGS` in this exact file. If #3175 lands first, the row fires against a PR that never touched the list. | pending | Pin to the branch point: `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -c '^[-+].*Drain tracked by #3175'` with `match count == 0`. The merge-base is stable for the life of the branch, so the row measures only what THIS branch changed. |
| CONCERN | Scope & Value | The same invariant is stated twice at different brittleness, and the stricter one is wrong. Verification asserts only `0 new, 0 stale`; Success Criteria demands the audit report `25 known` violations. The literal 25 derives from `KNOWN_MISTAGS`, which this plan does not own and #3175 exists to shrink. | pending | The audit line is `f"OK: {len(violations)} known, baselined violation(s); 0 new, 0 stale."` in `main()`. `len(violations)` is a property of `KNOWN_MISTAGS`, not of the stem. Drop the literal count from the Success Criterion and assert the `0 new, 0 stale` substring only. |
| CONCERN | Scope & Value | No-Go 2 (`Reordering or extending FEATURE_MAP`) names a forbidden code-level outcome but carries no inverse Verification row, while No-Go 1 does. The `--report` diff catches marker MOVES, but a marker-neutral FEATURE_MAP reorder or key insertion (the exact shape #3010 shipped with its `reflections` key) passes that diff while being the edit No-Go 2 forbids. | pending | Add: `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -cE '^[-+] *"[a-z_]+": "'` with `match count == 0`. FEATURE_MAP entries are the only lines matching `"key": "value"` at indent — KNOWN_MISTAGS values are parenthesized tuples spanning lines — so the pattern is specific to the forbidden edit. |
| CONCERN | History & Consistency | Documentation enumerates three prose sites to repair but misses a fourth pointing at the doc from the other direction: `tests/marker_map.py` line 24 (module docstring) says "See docs/features/feature-map-marker-guard.md for the three mistag mechanisms". After this change one of those three is fixed, not merely tracked, so the pointer's framing no longer matches the code — the same stale-cross-reference class the plan calls its highest-cost failure mode in Risk 3. | pending | The line is in the MODULE docstring, above `from __future__ import annotations` — outside the ~290-308 range the plan cites, so a builder scoped to that range will not see it. Either keep the count accurate ("two live mechanisms, one fixed") or reword to "the mistag mechanisms". Land it in the same commit as the guard-doc rewrite. |
| CONCERN | History & Consistency | Prior Art and No-Gos define #3175's scope incompatibly. Prior Art says #3175's scope is "the 24 paths in KNOWN_MISTAGS" and is "disjoint" from this plan; No-Go 2 then defers to #3175 the marker question for three files the plan itself establishes are NOT in KNOWN_MISTAGS. The `[SEPARATE-SLUG]` validator only confirms the issue exists, so this passes mechanically while orphaning the work. | pending | The plan's own measurement settles it: under the anchored stem those three resolve to `(None, None)` and `run_audit()` reports them neither new nor stale, so no rule wants them marked. Rewrite No-Go 2 as a statement of fact and drop the `[SEPARATE-SLUG #3175]` tag rather than reassigning it — with no deferred work left there is nothing to track. |
| NIT | Scope & Value | Three named agents across seven tasks for a one-line change. Tasks 1-3 are one builder's linear sequence (capture baseline, edit, diff) split into three hand-offs, while Appetite says "Solo dev" with 0 PM check-ins. | pending | n/a (NIT) |
| NIT | History & Consistency | The Rabbit Hole tells the builder to leave `docs/features/README.md` reading "24 pre-existing violations" while the audit prints 25, justified as "not made wrong by this change" — which reads as though the line is accurate when it is a pre-existing off-by-one between path count and violation count. | pending | n/a (NIT) |
