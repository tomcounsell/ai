---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3184
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-07T02:25:56Z
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
  numbers**. Derive the baseline from **git object content**, never by stashing:

  ```bash
  WORK=$(mktemp -d)                     # never a fixed /tmp/<name> — see below
  BASE=$(git merge-base HEAD origin/main)
  git show "${BASE}:tests/marker_map.py" > "$WORK/marker_map_base.py"   # braces required: see below
  python3 -c "import importlib.util as u; s=u.spec_from_file_location('mm','$WORK/marker_map_base.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('\n'.join(m._report_lines(m.iter_test_files())))" > "$WORK/before.txt"
  python3 tests/marker_map.py --report > "$WORK/after.txt"
  diff "$WORK/before.txt" "$WORK/after.txt"
  ```

  The diff must show exactly two lines changed, both gaining a marker.

- **Never use `git stash` for the baseline.** `refs/stash` is repo-wide, shared
  by every worktree of this checkout, and this repo routinely has 10+ concurrent
  agent worktrees with foreign stash entries already on the stack. `git stash` on
  a clean tree creates no entry and still exits 0, so a `git stash && … && git
  stash pop` chain proceeds to pop **another lane's stash** into this worktree,
  corrupting both. The `git show` recipe above runs the old resolver over the
  current file list with zero working-tree mutation, which is strictly better
  evidence anyway.

- **Never use a fixed `/tmp/<name>` for any artifact.** Concurrent lanes on this
  machine would clobber each other, and a clobbered baseline that happens to
  match yields a passing check that proves nothing. Allocate `WORK=$(mktemp -d)`
  once and pass it forward explicitly.

- **Brace `${BASE}` in the `git show` line.** This machine's shell is zsh, which
  applies its `:t` (tail) history modifier to a bare `$VAR` *even inside double
  quotes*. Written `"$BASE:tests/marker_map.py"`, the `:t` is eaten and git gets
  the revision `…076cests/marker_map.py`, failing with "ambiguous argument".
  The base module then lands empty and `diff` reports all 836 lines as added,
  which reads like a catastrophic blast radius rather than a broken command.
  `"${BASE}:tests/marker_map.py"` is correct. The bug is specific to the bare
  `$VAR` form: the `$(git merge-base HEAD origin/main):tests/marker_map.py`
  command-substitution form used in task 3 and the Verification rows is
  unaffected, and was tested as such.

- **Pin every `git diff` comparison to the merge-base, never `origin/main`.**
  `main` moves constantly here (it advanced twice during this plan's own
  authoring), and #3175 is OPEN against `KNOWN_MISTAGS` in this very file. A row
  written against `origin/main` fires against a PR that never touched the list
  once #3175 lands. `git merge-base HEAD origin/main` is stable for the life of
  the branch and measures only what *this* branch changed. **This requires a
  current `origin/main` ref**: run `git fetch origin --quiet` once before the
  verification block, or a stale ref resolves the merge-base further back and the
  diff picks up changes the branch merely inherited, reintroducing the very false
  positive this rule removes.

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
  The audit prints 25 violations across 24 paths, so that line carries a
  pre-existing off-by-one between path count and violation count. It predates
  this change and is unaffected by it. Leave it; fixing it here would put an
  unrelated edit in the diff.

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
- [SEPARATE-SLUG #3175] Reordering or extending `FEATURE_MAP` for any reason.
  This plan needs no such edit, and a marker-neutral reorder would slip past the
  `--report` diff while still being scope creep, so the Verification table
  carries an explicit anti-criterion against it.

Nothing else is deferred. In particular, the three still-unmarked members of the
population (`test_conftest_autouse_monkeypatch_order.py`,
`test_conftest_isolation_guards.py`, `test_test_redis_server_resolution.py`) are
**not** deferred work: this plan fixes their stems, and the measurement shows
they then resolve to `(None, None)` with `run_audit()` reporting them as neither
new nor stale violations. No `FEATURE_MAP` key targets them and none is wanted,
so there is nothing left to track. They are outside `KNOWN_MISTAGS`, so #3175
would never have picked them up either.

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
- [ ] Rewrite the **module docstring** cross-reference in `tests/marker_map.py`
      (around line 24): "See docs/features/feature-map-marker-guard.md for the
      three mistag mechanisms…". After this change one of those three is fixed
      rather than tracked. Note the location: this line sits above the
      `from __future__ import annotations` import, **outside** the ~290-308 range
      cited above, so a builder scoped to that range will miss it. Either keep
      the count accurate ("two live mechanisms, one fixed") or reword to "the
      mistag mechanisms", and land it with the guard-doc rewrite.
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
- [ ] `python3 tests/marker_map.py --audit` exits 0 reporting **0 new, 0 stale**,
      with `KNOWN_MISTAGS` unmodified. The known-violation *count* it prints is a
      property of `KNOWN_MISTAGS` (which #3175 owns and any new test file can
      move), so it is recorded as an observation, never asserted as a criterion.
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

### 1. Anchor the stem expression and prove the blast radius
- **Task ID**: build-stem
- **Depends On**: none
- **Validates**: tests/unit/test_feature_map_markers.py
- **Informed By**: Freshness Check (2 of 836 files move; audit stays green)
- **Assigned To**: stem-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tests/marker_map.py::_stem`, replace the global-replace expression with
  `basename.removeprefix("test_").removesuffix(".py")`
- Rewrite `_stem`'s docstring and comment so they no longer forbid this change
- Rewrite the module docstring's "the three mistag mechanisms" cross-reference
  (`tests/marker_map.py`, above the `from __future__` import) — mechanism 3 is
  now fixed, not merely tracked
- Do not touch `FEATURE_MAP` or `KNOWN_MISTAGS`
- Measure the blast radius with the stash-free `git show` recipe in the
  Technical Approach, using `WORK=$(mktemp -d)` — **never `git stash`, never a
  fixed `/tmp/<name>`**
- The diff must show exactly two changed lines, both gaining a marker, none
  losing one; `python3 tests/marker_map.py --audit` must report `0 new, 0 stale`
- **If either check disagrees, stop and report.** Do not edit `KNOWN_MISTAGS` to
  make the audit pass

### 2. Update and extend the stem fixtures
- **Task ID**: build-fixtures
- **Depends On**: build-stem
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

### 3. Independent blast-radius validation
- **Task ID**: validate-blast-radius
- **Depends On**: build-fixtures
- **Assigned To**: blast-radius-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-derive the before/after comparison **independently**, from
  `git show $(git merge-base HEAD origin/main):tests/marker_map.py` into a fresh
  `mktemp -d`, so the control cannot inherit the branch's change or the
  builder's artifacts. **Do not `git stash`** — `refs/stash` is repo-wide and
  shared with every concurrent lane
- Confirm exactly two files move and none lose a marker
- Confirm `KNOWN_MISTAGS` is untouched:
  `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py` shows no
  line mentioning `Drain tracked by #3175`
- Confirm `FEATURE_MAP` is untouched: the same diff adds/removes no
  `"key": "value"` entry line
- Confirm both newly-marked test files still collect and pass

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-blast-radius
- **Assigned To**: guard-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite mechanism 3 in `docs/features/feature-map-marker-guard.md`
- Confirm no doc still says the mangled stem is deliberately unfixed
- Confirm the `tests/marker_map.py` module-docstring cross-reference agrees with
  the rewritten doc (task 1 changes it; this task verifies the two match)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: blast-radius-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `git fetch origin --quiet` FIRST — the merge-base anti-criteria are only
  correct against a current `origin/main` ref
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
| Both stem-fidelity fixtures still exist (inverted, not deleted) | `grep -cE 'def test_stem_fidelity_(test_judge\|validate_test_impact)\b' tests/unit/test_feature_map_markers.py` | output contains `2` |
| Anti-criterion: `KNOWN_MISTAGS` untouched (#3175 stays out of scope) | `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -c '^[-+].*Drain tracked by #3175'` | match count == 0 |
| Anti-criterion: `FEATURE_MAP` neither reordered nor extended | `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -cE '^[-+] *"[a-z_]+": "'` | match count == 0 |
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

The two `git diff` anti-criteria are the inverse shape: they read `0` on a clean
tree and must *stay* `0`, so their red-state proof is to inject a throwaway edit
and confirm the count goes non-zero before reverting. Both were proven this way
at plan time:

- `FEATURE_MAP` row: clean → `0`; after inserting one `"zzz_probe": "tools",`
  entry → `1`. Reverted, back to `0`.
- `KNOWN_MISTAGS` row: same method against a `Drain tracked by #3175` line.
  Confirmed the two rows are independent — the FEATURE_MAP injection left the
  `#3175` count at `0`, so neither row masks the other.

Paste all five results into the PR description.

Two anchors were rejected during planning for failing exactly this test.
`grep -rn 'Do NOT .clean this up'` returns `0` on the unmodified checkout —
the phrase wraps across two comment lines, so the check would have passed before
the fix and proven nothing. `grep -c 'deliberately'` bites today but is anchored
on a common adverb that a doc rewrite could reintroduce innocently; `left unfixed
here` names the stale claim itself.

## Critique Results

Round 1 — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses, at plan hash `sha256:b72e627f…`, baseline `de229ee46`. Verdict: **NEEDS REVISION** (1 blocker, 6 concerns, 2 nits). **All 9 addressed in the round-1 revision** (see Addressed By).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Solution/Technical Approach and Task 5 establish the baseline with `git stash && ... && git stash pop`. `refs/stash` is repo-wide, shared by every worktree; 10+ agent worktrees are live and 3 foreign stash entries exist right now. On a clean tree `git stash` creates no entry and exits 0, so the `&&` chain proceeds and `git stash pop` applies ANOTHER LANE'S stash into this worktree. | Technical Approach now derives the baseline via `git show $(git merge-base HEAD origin/main):tests/marker_map.py` into `mktemp -d`; both `git stash` instructions deleted (Technical Approach + task 3). An explicit "Never use `git stash`" rule states why. | Delete both `git stash` instructions. Derive the baseline from git object content: `BASE=$(git merge-base HEAD origin/main)`; `git show "$BASE:tests/marker_map.py" > $WORK/marker_map_base.py`; load it by path with `importlib.util.spec_from_file_location` and call `m._report_lines(m.iter_test_files())`. Runs the OLD resolver over the CURRENT file list with zero working-tree mutation. Run from the repo root: `iter_test_files()` shells to `git ls-files` against the process cwd. |
| CONCERN | Risk & Robustness | Baseline artifacts use fixed absolute paths (`/tmp/before.txt`, `/tmp/marker_before.txt`). Many agents run concurrently against this repo, so two lanes clobber each other and `diff` compares against a foreign baseline. The failure is silent and green-looking. | Technical Approach adds "Never use a fixed `/tmp/<name>`"; tasks now use `WORK=$(mktemp -d)`. | `WORK=$(mktemp -d)` once in Task 1; reference `"$WORK/before.txt"` / `"$WORK/after.txt"` everywhere. Never a fixed `/tmp/<name>` in a repo where `git worktree list` reports concurrent lanes. Pass `$WORK` forward explicitly to Tasks 3 and 5, or have each task recompute the baseline from git. |
| CONCERN | Risk & Robustness | The `KNOWN_MISTAGS` anti-criterion runs `git diff origin/main`, resolving the remote head at execution time. `main` moves constantly (it advanced twice during this plan's authoring), and #3175 is OPEN and targets `KNOWN_MISTAGS` in this exact file. If #3175 lands first, the row fires against a PR that never touched the list. | Both `git diff` Verification rows now pin to `$(git merge-base HEAD origin/main)`; Technical Approach adds the merge-base rule; task 3 restated. | Pin to the branch point: `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -c '^[-+].*Drain tracked by #3175'` with `match count == 0`. The merge-base is stable for the life of the branch, so the row measures only what THIS branch changed. |
| CONCERN | Scope & Value | The same invariant is stated twice at different brittleness, and the stricter one is wrong. Verification asserts only `0 new, 0 stale`; Success Criteria demands the audit report `25 known` violations. The literal 25 derives from `KNOWN_MISTAGS`, which this plan does not own and #3175 exists to shrink. | Success Criteria now asserts `0 new, 0 stale` only; the known-violation count is recorded as an observation. | The audit line is `f"OK: {len(violations)} known, baselined violation(s); 0 new, 0 stale."` in `main()`. `len(violations)` is a property of `KNOWN_MISTAGS`, not of the stem. Drop the literal count from the Success Criterion and assert the `0 new, 0 stale` substring only. |
| CONCERN | Scope & Value | No-Go 2 (`Reordering or extending FEATURE_MAP`) names a forbidden code-level outcome but carries no inverse Verification row, while No-Go 1 does. The `--report` diff catches marker MOVES, but a marker-neutral FEATURE_MAP reorder or key insertion (the exact shape #3010 shipped with its `reflections` key) passes that diff while being the edit No-Go 2 forbids. | New Verification row: `grep -cE '^[-+] *"[a-z_]+": "'` over the merge-base diff. Red-state proven (0 clean, 1 with an injected key). | Add: `git diff $(git merge-base HEAD origin/main) -- tests/marker_map.py \| grep -cE '^[-+] *"[a-z_]+": "'` with `match count == 0`. FEATURE_MAP entries are the only lines matching `"key": "value"` at indent — KNOWN_MISTAGS values are parenthesized tuples spanning lines — so the pattern is specific to the forbidden edit. |
| CONCERN | History & Consistency | Documentation enumerates three prose sites to repair but misses a fourth pointing at the doc from the other direction: `tests/marker_map.py` line 24 (module docstring) says "See docs/features/feature-map-marker-guard.md for the three mistag mechanisms". After this change one of those three is fixed, not merely tracked, so the pointer's framing no longer matches the code — the same stale-cross-reference class the plan calls its highest-cost failure mode in Risk 3. | Documentation gains an explicit module-docstring item noting it sits outside the ~290-308 range; task 1 makes the edit, task 4 verifies it matches the doc. | The line is in the MODULE docstring, above `from __future__ import annotations` — outside the ~290-308 range the plan cites, so a builder scoped to that range will not see it. Either keep the count accurate ("two live mechanisms, one fixed") or reword to "the mistag mechanisms". Land it in the same commit as the guard-doc rewrite. |
| CONCERN | History & Consistency | Prior Art and No-Gos define #3175's scope incompatibly. Prior Art says #3175's scope is "the 24 paths in KNOWN_MISTAGS" and is "disjoint" from this plan; No-Go 2 then defers to #3175 the marker question for three files the plan itself establishes are NOT in KNOWN_MISTAGS. The `[SEPARATE-SLUG]` validator only confirms the issue exists, so this passes mechanically while orphaning the work. | No-Go 2 rewritten to forbid any `FEATURE_MAP` edit; the three latent files are now stated as not-deferred with the measurement, and the `#3175` misattribution is gone. | The plan's own measurement settles it: under the anchored stem those three resolve to `(None, None)` and `run_audit()` reports them neither new nor stale, so no rule wants them marked. Rewrite No-Go 2 as a statement of fact and drop the `[SEPARATE-SLUG #3175]` tag rather than reassigning it — with no deferred work left there is nothing to track. |
| NIT | Scope & Value | Three named agents across seven tasks for a one-line change. Tasks 1-3 are one builder's linear sequence (capture baseline, edit, diff) split into three hand-offs, while Appetite says "Solo dev" with 0 PM check-ins. | Tasks 1-3 collapsed into a single `build-stem` task; graph renumbered to 5 tasks. | n/a (NIT) |
| NIT | History & Consistency | The Rabbit Hole tells the builder to leave `docs/features/README.md` reading "24 pre-existing violations" while the audit prints 25, justified as "not made wrong by this change" — which reads as though the line is accurate when it is a pre-existing off-by-one between path count and violation count. | Rabbit Hole reworded: the off-by-one is named as pre-existing rather than implied correct. | n/a (NIT) |

Round 2 — FULL war room, sequential lenses, at plan hash `sha256:18f013c1…`. Verdict: **NEEDS REVISION** (1 blocker, 2 concerns, 1 nit). **Blocker and both concerns fixed; NOT re-critiqued — the router hit the G2 critique cycle cap (2/2) and escalated.** Round 2 independently re-verified every round-1 fix and found all nine landed; the findings below are new, and the blocker is a defect introduced by the round-1 fix itself.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The round-1 replacement recipe does not run. `git show "$BASE:tests/marker_map.py"` breaks under zsh (this machine's shell): zsh applies the `:t` history modifier to a bare `$VAR` even inside double quotes, so git receives `...076cests/marker_map.py` and fails. `before.txt` lands empty and `diff` then reports all 836 lines as added, which reads like a catastrophic blast radius rather than a broken command. | Applied: the recipe now reads `git show "${BASE}:tests/marker_map.py"`, and the Technical Approach carries a rule explaining the zsh `:t` modifier and scoping it to the bare `$VAR` form. | Brace it: `git show "${BASE}:tests/marker_map.py"`. Verified both directions under /bin/zsh — unbraced fails `ambiguous argument`; braced returns 24860 bytes and the full recipe yields 836 baseline lines identical to the current report. Scoped to the bare `$VAR` form ONLY: the `$(git merge-base ...):tests/marker_map.py` command-substitution form in task 3 and the Verification rows was tested and is unaffected. |
| CONCERN | Risk & Robustness | `git merge-base HEAD origin/main` is only as good as the local `origin/main` ref. In a lane that has not fetched, it resolves further back, so the diff includes changes the branch inherited rather than authored. If #3175 lands and the branch rebases onto it, both anti-criteria fire against a PR that touched neither list — reintroducing the false positive the merge-base fix removed. | Applied: task 5 now runs `git fetch origin --quiet` first, and the merge-base rule states the currency requirement. | Add `git fetch origin --quiet` as the first step of task 5 and state the dependency in the Technical Approach merge-base rule. Do NOT fold `git fetch` into each Verification row — rows execute individually and repeated fetches serialize on the remote; one fetch before the block keeps each row a pure measurement. |
| CONCERN | History & Consistency | Verification and Test Impact contradict each other and will fail a correct build. Test Impact says ADD three fixtures for the latent basenames, under the same "stem fidelity" header the plan tells the builder to rewrite; Verification asserts `grep -c 'def test_stem_fidelity_'` outputs exactly `2`. Naming the new fixtures in the obvious family yields 5 and fails the row. It was written against the pre-change count and never revisited when the ADD instruction appeared. | Applied: the row is now `grep -cE 'def test_stem_fidelity_(test_judge\|validate_test_impact)\b'`, pinning the two fixtures that must be inverted while allowing new ones. | Use `grep -cE 'def test_stem_fidelity_(test_judge\|validate_test_impact)\b' tests/unit/test_feature_map_markers.py` with `output contains 2`. That pins exactly the two fixtures that must be INVERTED rather than deleted — the property the row exists to protect — while leaving the builder free to add any number of new stem fixtures. |
| NIT | History & Consistency | The round-1 revision inlined four general rules (never `git stash` in a shared checkout, never a fixed `/tmp/<name>` under concurrency, always pin `git diff` to the merge-base). All are repo-wide hazards of this repo's concurrent-worktree topology, not properties of the FEATURE_MAP stem; captured only here, the next lane rediscovers them the same expensive way. | Not applied — durable capture is outside this plan's scope; left for the operator to record. | n/a (NIT) |

Round 3 — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), **sequential lenses** (the Agent tool is unavailable in this session, so no finding below is independently corroborated), at plan hash `sha256:9533fc0d…`, baseline `de229ee46`, merge-base `8f0b04a70`. Verdict: **NEEDS REVISION** (1 blocker, 1 concern, 4 nits). This round re-derived every prior measurement by execution: the braced `git show` recipe runs and yields 836 baseline lines, the before/after `--report` diff is exactly the two expected lines, the simulated post-fix audit reports 25 known / 0 new / 0 stale, `KNOWN_MISTAGS` needs no edit, and all three red-state anti-criteria return `1` today. The plan's substance is correct; the blocker is an expectation-cell syntax defect that fails a correct build.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Two Verification rows wrap their Expected cell in backticks — ``output contains `0 new, 0 stale` `` and ``output contains `2` ``. `agent/verification_parser.py::evaluate_expectation` takes `m.group(1).strip()` and tests `substring in output` with **no** backtick stripping (backticks are stripped from the Command cell only), so the literal backticks join the substring and never match. Executed on the current checkout: the audit row emits exactly `OK: 25 known, baselined violation(s); 0 new, 0 stale.` and evaluates `passed: False`; the fixture row emits exactly `2` and evaluates `passed: False`. Both rows must be green **before and after** the change — the audit row is also the Prerequisites red/green baseline — so a fully correct build fails its own verification table. | pending | Delete the backticks from those two Expected cells so they read `output contains 0 new, 0 stale` and `output contains 2`, with no backtick anywhere in either cell. Leave the Check and Command cells alone (the Command cell's backticks are stripped by the parser and are correct). This is the house form: of the 26 `output contains` rows across `docs/plans/*.md`, 24 are bare and the only 2 backticked ones are these. Proof, from the repo root: `.venv/bin/python -c "from agent.verification_parser import run_checks, VerificationCheck as V; print([r.passed for r in run_checks([V(name='a',command='python3 tests/marker_map.py --audit',expected='output contains 0 new, 0 stale')])])"` → `[True]`. Do NOT instead make the commands emit backticks. Blocking because `scripts/validate_build.py` feeds this table to the same evaluator and returns exit 1 on any FAIL, and `docs/sdlc/do-build.md` routes exit 1 into `/do-patch` for up to 3 iterations — a correct build enters a patch loop chasing a phantom and may mutate correct code to chase it. |
| CONCERN | History & Consistency | The plan mandates rewriting the module-docstring cross-reference in `tests/marker_map.py` ("See docs/features/feature-map-marker-guard.md for the three mistag mechanisms…") but the near-verbatim same sentence also sits at `tests/unit/test_feature_map_markers.py:5-7`, and no task, Documentation item, Success Criterion, or anti-criterion grep reaches it. Whichever rewording task 1 applies to `tests/marker_map.py`, the two sibling files ship describing the same doc differently. Identical finding class to round-1 concern 6, for the identical reason: the line sits above the `from __future__` import, outside every line range the plan cites. | pending | The line is at `tests/unit/test_feature_map_markers.py:5-7`, above `from __future__ import annotations` at line 10 — outside the `77-82` range the plan cites for this file, so a builder scoped to that range will not see it. Apply the SAME wording chosen for `tests/marker_map.py` line ~24 so the two files agree verbatim; never reword one and not the other. The existing `grep -c 'left unfixed here' docs/features/feature-map-marker-guard.md` anti-criterion does not reach this file; if a check is wanted, add `grep -rc 'three mistag mechanisms' tests/` with `match count == 0`. The sentence is not itself a prohibition, so the "no comment or docstring still instructs the reader that the global `str.replace` is intentional" criterion does not catch it either. |
| NIT | Risk & Robustness | The `git fetch origin --quiet` currency requirement is stated only for the `git diff` anti-criteria ("once before the verification block") and executed only in task 5, but tasks 1 and 3 also derive their baseline from `git merge-base HEAD origin/main`. A stale `origin/main` there inflates the `--report` diff beyond two lines and trips task 1's "stop and report" clause on a correct build. | pending | n/a (NIT) |
| NIT | Scope & Value | Test Impact instructs the builder to "assert `_stem` directly" for the three latent basenames, but `_stem` is not among the names imported at `tests/unit/test_feature_map_markers.py:17-29`. | pending | n/a (NIT) |
| NIT | Scope & Value | Success Criteria says "All five mangled basenames have a committed fixture asserting their un-mangled stem", but Test Impact commits `_stem` fixtures for only the three latent basenames; the other two are covered by inverted `resolve_marker` tuple assertions, and the all-five `_stem` assertion lives in a Verification row rather than a committed fixture. | pending | n/a (NIT) |
| NIT | History & Consistency | No-Go 2 is still tagged `[SEPARATE-SLUG #3175]` while Prior Art scopes #3175 to "the 24 paths in `KNOWN_MISTAGS`" — and reordering or extending `FEATURE_MAP` is not one of those paths. Round-1 concern 7's note said to drop the tag rather than reassign it; the substantive misattribution was fixed but the tag remains. | pending | n/a (NIT) |
