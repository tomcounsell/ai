---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2807
last_comment_id: none
---

# Working-tree sweep guards must scan tracked content

**Lane scope:** this plan covers **three independently-filed reports of the same
defect** — #2807 (lane primary), #2808, #2809. One plan, one branch, one PR. The
PR body carries `Closes #2807`, `Closes #2808`, `Closes #2809`.

## Problem

`tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted`
is a *meta-test*: it asserts a property of the repository's own source text
rather than of runtime behavior. PR #2740 (merged `706fc4da0`) removed the
overclaiming sentence `State NOT persisted` from four sites in
`tools/sdlc_stage_marker.py`, and added this test in the same commit to stop the
sentence creeping back.

The test implements "the sentence is gone from `tools/` and `agent/`" as
`subprocess.run(["grep", "-rn", "State NOT persisted", "tools/", "agent/"])` and
asserts `returncode == 1`. But `grep -r` does not scan *source*; it scans
*files* — including the `__pycache__/` bytecode caches sitting next to the
source. A `.pyc` embeds its module's string literals verbatim.

**Current behavior.** Reproduced at `054f0f0fa` in the primary checkout
`/Users/tomcounsell/src/ai`, running the test's exact argv:

```
returncode 0
'Binary file tools/__pycache__/sdlc_stage_marker.cpython-312.pyc matches\n'
```

The test asserts `returncode == 1`, so it fails. The source is clean:
`git grep -n "State NOT persisted" -- 'tools/*.py' 'agent/*.py'` exits 1.

Three properties make this permanent rather than transient:

1. **Nothing invalidates it.** CPython 3.14 reads and writes only
   `…cpython-314.pyc`. It never stats, validates, or deletes an off-pin cache.
   The mtime/size invalidation that keeps bytecode honest does not apply across
   minor versions.
2. **Nothing sweeps it.** `tools/doctor.py` contains zero occurrences of
   `pycache`/`.pyc`/`bytecode`. No `/update` step purges bytecode.
   `PYTHONDONTWRITEBYTECODE=1` in `scripts/pytest-clean.sh` (#2064) prevents
   *new* writes and cannot remove pre-existing caches.
3. **Nothing makes it visible.** `.gitignore` keeps `*.pyc` out of
   `git status` and out of every `git grep`.

The resulting signal shape is the worst available: **red in the primary
checkout, green in every worktree, at the same commit**, decided entirely by
untracked filesystem residue. It reads to the nightly detector as a real
regression on `main` while no source defect exists. The more dangerous
direction is that the guard is validating *compiled bytecode* rather than
source — and a triager who "fixes" the red by deleting the offending `.pyc`
silently restores a guard that never checked what its name claims.

The defect is a **class**, not an instance. A PLAN-time sweep of `tests/`
(27 codebase-content assertion call sites out of 208 candidate lines triaged)
found three more guards with the same or an adjacent failure mode, one of which
appears in no issue.

**Desired outcome.** Every guard that asserts a string is absent from the
codebase scans **tracked content**, returns the same verdict in any checkout on
any machine regardless of build artifacts on disk, distinguishes "scan found
nothing" from "scan failed to run" and from "scan examined nothing", and fails
loudly when the banned string is reintroduced into tracked source.

## Freshness Check

**Baseline commit:** `054f0f0fa093efd21be272c83998103e83b02ee6`
**Issues filed at:** #2807 2026-08-13T20:29:57Z, #2808 2026-08-13T20:31:12Z,
#2809 2026-08-13T20:33:36Z
**Disposition:** **Minor drift** — the defect still reproduces; two cited
file:line references moved and one factual claim in #2808 is wrong.

**File:line references re-verified:**

| Cited | Claim | Status |
|---|---|---|
| `test_sdlc_review_finalize.py:1054-1068` | the failing test body | **Drifted.** Now `:1084` (def), `subprocess.run` at `:1093`. Claim holds. |
| `test_plan_migration_invariant.py:133-158` | the `git grep` reference pattern | **Drifted slightly.** Comment now at `:136-145`, call at `:145-158`. Claim holds. |
| `test_anthropic_client_semaphore.py:166` | `cwd=`-less sibling | **Confirmed** at `:164-176`. No `cwd=`. |
| `test_memory_extraction.py:2563` | `cwd=`-less sibling | **Confirmed** at `:2563`. No `cwd=`. |
| `tools/doctor.py:442` | `worktree_interpreters` check, no bytecode dimension | **Confirmed.** `grep -c "pycache\|\.pyc\|bytecode" tools/doctor.py` → `0`. |

**Factual correction to #2808.** #2808 states both `cwd=`-less siblings "go
vacuously green". Only one does. Verified by execution from a foreign cwd:

| Site | rc from wrong cwd | Assertion | Outcome |
|---|---|---|---|
| `test_anthropic_client_semaphore.py:164` | 1, empty stdout | `assert not hits` | **passes vacuously** (false green) |
| `test_memory_extraction.py:2563` | 2 | `assert returncode == 1` | **fails loudly** (false red) |

These carry different remediation priorities and the plan treats them
separately. A false-green guard has silently stopped guarding; a false-red one
is noisy but honest.

**Machine-dependence of the artifact.** #2807/#2808 name `cpython-312`; #2809
names `cpython-313`. Both are correct on their own machines. On this machine the
offending file is `tools/__pycache__/sdlc_stage_marker.cpython-312.pyc`
(2 occurrences; the `-314` sibling has 0). **The fix must never hardcode an
interpreter version.**

**Blast radius re-measured here** (excluding `.venv/`, `.worktrees/`): 1143
non-3.14 `.pyc` — 1131 × cpython-312, 12 × cpython-313, against 1238 on-pin.
Under `tools/` + `agent/` alone: 183 off-pin. #2809 reported 2227 on its
machine. The condition is fleet-wide; the count is not portable.

**Cited sibling issues/PRs re-checked:**
- #2740 / `706fc4da0` — merged 2026-08-13. Source removal is complete and
  correct. This is not a regression of it.
- #2093 — closed. Origin of the `git grep` rule in
  `test_plan_migration_invariant.py`. Directly reusable.
- #2064 — closed. Origin of `PYTHONDONTWRITEBYTECODE=1`. Write-prevention only.
- #2617 / #2572 — the `.python-version` pin that orphaned the caches.

**Commits on main since the issues were filed (touching referenced files):**
`b5177404d` (plan revision doc, `g8-terminal-pipeline-no-rebuild`) is the only
commit touching any referenced test file, and it edits a plan doc, not the
tests. **No drift in the root cause.**

**Active plans in `docs/plans/` overlapping this area:** none. 28 plans present;
none addresses grep-based guards, `__pycache__`, or the test-sweep class.

## Prior Art

- **#2093 / PR #2097** — "Fix test-isolation cluster: 5 unit tests flaky under
  `-n auto`" (merged 2026-07-15). Solved this exact hazard class in
  `test_plan_migration_invariant.py` by switching to `git grep`, and wrote the
  rationale into a code comment. **Succeeded.** This plan generalizes that
  fix rather than inventing one.
- **#2064** — "full-suite pytest lock … cross-reap xdist workers" (closed).
  Added `PYTHONDONTWRITEBYTECODE=1` to `scripts/pytest-clean.sh` as
  "defense-in-depth against `__pycache__` cross-checkout poisoning".
  **Partial.** Prevents new `.pyc`; cannot reap existing ones.
- **#2430** — "Nightly regression (6-node batch): … hardlinks `__pycache__`
  cruft (net-new)" (closed 2026-08-06). Prior instance of gitignored
  `__pycache__` residue changing observable repo state. **Succeeded** for its
  own scope (the skills-hardlink area), never generalized.
- **#2557, #2597** — earlier `__pycache__`-residue cases, both in the
  skills-hardlink area. Same pattern: fixed locally, never swept.

**Why previous fixes did not prevent this.** Every prior fix was applied at the
*instance* layer: #2093 fixed one test, #2430/#2557/#2597 fixed one directory
each, #2064 prevented one class of future write. None of them made the *pattern*
un-writable, and none swept for siblings. `test_sdlc_review_finalize.py` was
written a month after #2093 landed the remedy in a neighbouring file, by an
author who had no mechanism telling them the remedy existed. This plan adds
that mechanism (a shared helper plus a meta-guard) so the next author cannot
reintroduce the shape.

## Research

Purely internal. The decision surface is `git grep` semantics, BSD/GNU `xargs`
semantics, and CPython's PEP 3147 cache naming — all of which are more reliably
settled by execution on the target machine than by search, and were settled that
way (see Spike Results). No external findings required.

## Spike Results

### spike-1: `git grep` exit-code contract
- **Assumption**: "`git grep` exits 1 on no-match and non-1 on scan failure, so
  the assertion shape carries over from `grep`."
- **Method**: code-read + direct execution at `054f0f0fa`
- **Finding**: Partly true, with one hazard the issues did not anticipate.

  | Condition | Exit |
  |---|---|
  | match found | 0 |
  | no match, files scanned | 1 |
  | not a git worktree | 128 |
  | unreadable/empty index (`GIT_INDEX_FILE=/dev/null`) | 128 |
  | invalid flag | 129 |
  | **pathspec matches no files** (`-- 'nosuchdir/*.py'`) | **1** |

  The last row is the hazard: **`git grep` cannot distinguish "clean" from
  "scanned nothing"**. A directory rename silently converts the guard into a
  no-op that reports success.
- **Confidence**: high
- **Impact on plan**: `git grep` alone does **not** close the vacuous-green
  class. Every converted guard must be paired with a **non-vacuity floor** that
  asserts the scan actually examined files. This is the single most important
  finding of the planning pass and it is not in any of the three issues.

### spike-2: `git grep` pathspec nesting
- **Assumption**: "`tools/*.py` only matches the top level; nested files need `**`."
- **Method**: direct execution
- **Finding**: **False.** Git pathspec `*` matches `/`. `git grep -- 'tools/*.py'`
  matched 150 files, 90 of them nested (`tools/browser/tests/test_downscale.py`
  etc.). No `**` needed.
- **Confidence**: high
- **Impact on plan**: keeps the pathspecs simple and matches the existing
  `test_plan_migration_invariant.py` form exactly.

### spike-3: the `git ls-files -z | xargs -0 grep` alternative
- **Assumption**: "`xargs` is an acceptable equivalent to `git grep`."
- **Method**: direct execution on this machine (macOS, BSD xargs)
- **Finding**: **Disqualifying.** With an empty file list the pipeline returns
  **rc=0** — grep's *"match found"* code — when nothing was scanned at all:

  ```
  git ls-files -z -- 'nosuchdir/*.py' | xargs -0 grep -n 'anything'   → rc=0
  ```

  `rc=0` is therefore ambiguous between "the banned string is present" and
  "nothing was examined", which destroys the exit-code contract in the most
  dangerous possible direction. Batch splitting is a second, portability-scoped
  hazard: BSD xargs returned 1 for an all-no-match batched run here, but GNU
  xargs documents 123 when any invocation exits 1-125, so the contract is not
  portable across the fleet. A pipeline also launders the exit code of the
  *first* command entirely — a `git ls-files` failure is invisible.
- **Confidence**: high
- **Impact on plan**: **`git grep` is chosen.** It is a single process with no
  pipe, no argv limit, no empty-input ambiguity, and it is already the in-repo
  precedent with a written rationale (#2093). This resolves the supervisor's
  directive to choose between the two forms.

### spike-4: exhaustive sweep of `tests/` for filesystem-walking absence assertions
- **Assumption**: "The three sites named across #2807/#2808/#2809 are the whole set."
- **Method**: code-read, exhaustive (208 candidate lines triaged → 77 genuine
  filesystem-search call sites → 27 codebase-content assertions)
- **Finding**: **False.** Two additional exposures found; see the sweep table in
  Technical Approach. Most notably `tests/unit/test_no_legacy_paths.py:25`
  gates on `if result.returncode == 0:` and does nothing otherwise, so a
  `git grep` exit **128** reads as "clean" — an exposure in a guard that already
  uses `git grep`, proving that the tool switch alone is insufficient.
- **Confidence**: high
- **Impact on plan**: scope (B) is a real sweep with 6 additional hardening
  targets, not a two-line adjacent fix.

## Data Flow

Not applicable in the runtime sense — no component boundaries are crossed. The
relevant flow is the guard's own evaluation chain, which is where the defect
lives:

1. **Entry point**: pytest collects a guard test.
2. **Scan**: the guard names a corpus. Today: a *directory tree on disk*
   (`grep -r tools/`). After: *tracked content in the index*
   (`git grep -- 'tools/*.py'`).
3. **Result**: an exit code plus stdout.
4. **Triage**: today, a single `== 1` comparison that conflates no-match with
   several failure modes. After: an explicit three-way split — match / clean /
   scan-failed — plus a separate non-vacuity assertion on corpus size.
5. **Output**: pass, or a failure naming `file:line` of the offending tracked
   source.

The defect is entirely at step 2, and the vacuous-green class is entirely at
step 4. Fixing only step 2 (what all three issues propose) leaves step 4 open.

## Architectural Impact

- **New dependencies**: none. `git` is already required by the test suite and
  `git grep` is already used for this purpose.
- **Interface changes**: one new internal test helper module,
  `tests/tracked_content.py`. No production interface changes. **No production
  code is modified by this plan at all** — the defect is entirely in test code.
- **Coupling**: *decreases*. Guards stop depending on the filesystem state of
  whatever checkout they run in.
- **Data ownership**: unchanged.
- **Reversibility**: high. Every change is test-local and independently
  revertable per call site.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus a validator and a documentarian.

**Interactions:**
- PM check-ins: 1-2 (scope of the sweep; the concern-B split)
- Review rounds: 1

Medium rather than Small because scope (B) touches ten test files and introduces
a shared helper plus a meta-guard, and because the verification obligation is
unusually strict: every converted guard needs a demonstrated-red mutation check,
and the primary node must be proven in **both** the primary checkout and a
worktree.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo is a git worktree | `git rev-parse --is-inside-work-tree` | `git grep` requires it; guards must fail loudly if absent |
| On-pin interpreter | `python -c "import sys,pathlib; assert '.'.join(map(str,sys.version_info[:2])) == pathlib.Path('.python-version').read_text().strip()"` | `scripts/pytest-clean.sh` aborts off-pin |
| Stale artifact present for the red-state proof | `/usr/bin/grep -rl "State NOT persisted" tools/ agent/` | AC1 requires the fix be proven *with* the stale `.pyc` in place |

Run via `python scripts/check_prerequisites.py docs/plans/overclaim-guard-greps-whole-worktree.md`.

> **Note on the third prerequisite.** If this checkout's stale `.pyc` has been
> swept between planning and building, the builder must **plant an equivalent
> artifact** rather than skip the check. AC1 is meaningless without a red state
> to contrast against. Never satisfy it by deleting bytecode.

## Solution

### Key Elements

- **`tests/tracked_content.py`** — a small shared helper that expresses "no
  tracked file matching this pathspec contains this pattern" correctly, once.
  It owns the `git grep` invocation, the three-way exit-code triage, and the
  non-vacuity floor, so that no call site has to get those right individually.
- **Converted guards** — the four `subprocess`-based guards that are vulnerable
  (one failing, one vacuously green, one false-red, one silently passing on
  scan failure) route through the helper.
- **Floored walks** — the six pure-Python `rglob` guards that are not
  artifact-vulnerable but lack a non-vacuity floor get one.
- **A meta-guard** — a test asserting no test in `tests/` shells out to a bare
  recursive `grep` over a directory. This is what stops the shape recurring, and
  it is the piece that #2093 lacked.
- **A documented hazard** — the pin-bump/stranded-bytecode interaction recorded
  in `docs/features/worktree-venv-isolation.md`.

### Flow

Guard authoring today → author writes `grep -rn` → passes locally in a fresh
worktree → lands → goes red months later in a long-lived checkout, or silently
stops guarding → nightly detector reports a phantom regression.

After: Guard authoring → author reaches for the helper (or the meta-guard blocks
the bare-grep shape at test time) → guard scans tracked content with a
non-vacuity floor → identical verdict in every checkout on every machine.

### Technical Approach

**Decision: `git grep`, not `git ls-files | xargs grep`.** Settled empirically
in spike-3. `git grep` is one process with no pipe, no `ARG_MAX` batching, no
empty-input ambiguity, and an exit code that is not laundered through a
pipeline. It is already the in-repo precedent (`test_plan_migration_invariant.py`,
#2093) with a written rationale. The `xargs` form returns **rc=0** — grep's
"match found" — for an empty file list, which is the most dangerous possible
misreading, and its batched exit code is not portable between BSD and GNU xargs.

**The helper's contract.**

```python
class TrackedScanError(RuntimeError):
    """The scan could not run (git failed). Never a pass."""

class VacuousScanError(AssertionError):
    """The scan ran but examined too few files to mean anything."""

def assert_absent_from_tracked(pattern, *pathspecs, min_files, allow=()):
    """Assert no tracked file matching *pathspecs* contains *pattern*."""
```

Two **distinct** exception classes are mandatory, not stylistic. A single class
with two messages cannot be asserted apart except by string matching, and the
whole point of the helper is that "scan failed" and "scan examined nothing" stay
separable from each other and from "clean". They are asserted apart in
`tests/unit/test_tracked_content_helper.py` via
`pytest.raises(TrackedScanError, match="128")` under `GIT_INDEX_FILE=/dev/null`
and a separate `pytest.raises(VacuousScanError)` for `'nosuchdir/*.py'`.

Four obligations, which the current call sites each get wrong in a different way:

1. **Index-scoped corpus.** `git grep` with `cwd=REPO_ROOT` (always explicit —
   never inherited from pytest's ambient cwd) and an **explicit pathspec, never
   an implicit whole-repo default**. `*.py` for the source-code guards; `*` plus
   negative pathspecs for corpus-wide guards such as A7. The pathspec is
   explicit so the corpus is stated rather than inherited — it is *not*
   required to be Python-only. A7's corpus is deliberately every tracked file
   type, because the legacy path can return in a shell script, a `.md`, or a
   JSON config; a Python-only helper would silently narrow it from 2654 tracked
   files to 1363 (measured at `40c4fe0a9`).

2. **Returncode triage before anything else, on *every* git call.** This
   ordering is load-bearing and is the single easiest thing to get wrong.
   Measured at `40c4fe0a9`:

   | Invocation | rc | stdout |
   |---|---|---|
   | `GIT_INDEX_FILE=/dev/null git ls-files -- 'tools/*.py'` | **128** | empty |
   | `git ls-files -- 'nosuchdir/*.py'` | **0** | empty |

   Both produce **empty stdout**. A floor implemented as "count the lines of
   `git ls-files` output" therefore cannot tell a broken index from a pathspec
   that legitimately matches nothing — it would raise the *vacuity* error for a
   *scan failure*, the Failure Path probe would go green for the wrong reason,
   and Success Criterion 6 would ship unverified. The returncode does separate
   them cleanly, so check it first:

   ```python
   ls = subprocess.run(["git", "ls-files", "--", *pathspecs], cwd=REPO_ROOT, ...)
   if ls.returncode != 0:
       raise TrackedScanError(f"git ls-files failed (rc={ls.returncode}): {ls.stderr.strip()}")
   files = [p for p in ls.stdout.splitlines() if p]
   if len(files) < min_files:
       raise VacuousScanError(...)
   ```

3. **Three-way exit triage on `git grep`.** `0` → match, report `file:line`.
   `1` → clean. **anything else** → raise `TrackedScanError`, distinct from a
   pass. Exit 128 (not a worktree / unreadable index) and 129 (bad flag) must
   never read as clean. This is the obligation `test_no_legacy_paths.py`
   currently violates despite already using `git grep`.

4. **Non-vacuity floor.** Assert the (returncode-checked) `git ls-files` result
   holds at least `min_files` paths. Without this, spike-1 shows a renamed
   directory turns the guard into a silent no-op. Modelled on the strongest
   existing pattern in the repo,
   `tests/unit/test_update_pull_fetch_head_race.py` (`__file__`-anchored roots,
   explicit required-anchor list, `len(found) > 250` floor).

**Regex dialect is pinned: POSIX basic regular expressions.** The four call
sites disagree about what `pattern` is — A1 passes a literal, A3 at
`tests/unit/test_memory_extraction.py:2558-2560` passes BRE with escaped
metacharacters (`anthropic\.Anthropic(`). A builder reaching for `-F` (a
defensible instinct, since three of four patterns are literals) would turn A3's
`\.` into a literal backslash-dot that never matches, leaving A3 **permanently
green** — the exact vacuous-green class this plan exists to close, reintroduced
by the fix itself.

`git grep` already defaults to POSIX basic regular expressions, the same dialect
`grep` uses without `-E`, so A3's patterns carry over byte-for-byte with no flag
change (verified at `40c4fe0a9`: `git grep -c 'anthropic\.Anthropic(' --
'agent/*.py'` exits 0, i.e. matches). **Do not add `-F`, `-E`, or `-P`.**
Docstring line: *"`pattern` is a POSIX basic regular expression interpreted by
`git grep` exactly as `grep` would; `(` is literal, `\.` is a literal dot."*
A helper test must exercise the metacharacter path rather than assume it.

**Exemptions go in the pathspec, not in `allow=`.** A single `allow=()` matched
by substring over stdout cannot carry three incompatible semantics. A2 at
`tests/unit/test_anthropic_client_semaphore.py:176-180` filters output *lines*
by module-path substring; A7 at `tests/unit/test_no_legacy_paths.py:33-37`
filters *file paths* two ways at once (exact membership in `ALLOWED_FILES` plus
a prefix rule on `docs/plans/`). One substring-matched tuple over-exempts:
`scripts/update/run.py` would also exempt a future
`tools/scripts/update/run.py`, and any allowed fragment appearing in matched
source *text* rather than in a path would exempt a real violation.

Push every **path-shaped** exemption into git pathspec exclusions, which git
applies to the corpus instead of to stdout. No new parameter is needed — the
helper already takes `*pathspecs`. This is the idiom already at
`tests/unit/test_plan_migration_invariant.py:150`
(`f":!{this_file.relative_to(REPO_ROOT)}"`). Reserve `allow=` for A2's genuine
line-substring case and say so in the docstring.

**Accepted boundary: the corpus is tracked content only.** Moving to `git grep`
closes the build-artifact hole and opens a smaller one — without `--untracked`
it does not see brand-new, never-committed source. **Do not add `--untracked`.**
It implies `--exclude-standard`, so it would still skip gitignored
`__pycache__` and buy nothing against the reported defect, while re-walking the
working tree and reopening the mid-walk race that Race 1 credits `git grep` with
closing. Record the boundary instead, in both
`docs/features/tracked-content-sweep-guards.md` and the helper docstring:
*"The corpus is tracked content only. A violation in a new, unstaged file is
caught on first commit, which is where CI and the nightly detector read.
`--untracked` would close that window but reopens the #2093 mid-walk race, so it
is deliberately not used."*

**Sweep dispositions** (spike-4; 27 codebase-content assertion sites, all
classified — this table *is* the #2809 enumeration deliverable):

| # | Site | Defect | Disposition |
|---|---|---|---|
| A1 | `test_sdlc_review_finalize.py:1093` | `grep -rn`, no `--include`; reads `__pycache__` | **CONVERT** — the failing node |
| A2 | `test_anthropic_client_semaphore.py:164` | no `cwd=`; `assert not hits` → **vacuous green** | **CONVERT** — highest priority after A1 |
| A3 | `test_memory_extraction.py:2563` | no `cwd=`; rc 2 → **false red** | **CONVERT** — noisy, not silent |
| A7 | `test_no_legacy_paths.py:25` | `if rc == 0:` → exit **128 reads as clean**; no `*.py` pathspec | **CONVERT** — net-new, in no issue |
| A4, A5 | `test_valor_session_working_dir_resolution.py:441,487` | none — absolute single-file paths | **DOCUMENT AS SAFE** |
| A6 | `test_plan_migration_invariant.py:145` | none — the reference pattern | **DOCUMENT AS SAFE** (add floor only if free) |
| B7 | `test_template_filter_registry.py:131` | no non-vacuity floor | **ADD FLOOR** |
| B8 | `test_sdlc_lease_helper_binding.py:89` | no floor, no anchor list | **ADD FLOOR** |
| B11 | `test_sdlc_tool_wrapper.py:54` | no floor; a renamed skills root empties the sweep | **ADD FLOOR** |
| B12 | `test_dm_recovery.py:426` | no floor | **ADD FLOOR** |
| B13 | `test_no_positional_query_get.py:66-68` | `if not root.exists(): continue` over 8 `SCAN_DIRS` — same explicit vacuous path as B14 | **ADD FLOOR — convert to raise** |
| B14 | `test_harness_model_coverage.py:60` | explicit `if not agent_dir.is_dir(): return []` vacuous path | **ADD FLOOR** — convert to raise |
| B1-B6, B9, B10, B15-B20 | 14 further walks | already carry non-vacuity floors | **DOCUMENT AS SAFE** |

The B-series are `rglob("*.py")` walks. They are **not** `.pyc`-vulnerable
(`*.py` does not match `*.pyc`); their only defect is the missing floor. That
distinction is deliberate — this plan does not rewrite them to use `git grep`,
which would be churn for no correctness gain.

**B13 needs a per-root assertion, not only a total floor.** B14 was singled out
for a raise because of its explicit vacuous early return, but
`tests/unit/test_no_positional_query_get.py:66-68` carries the identical shape —
`root = REPO_ROOT / top` then `if not root.exists(): continue` inside a loop over
eight `SCAN_DIRS`. A corpus-total floor cannot catch a vanished root, and the
measured distribution makes that far worse than an even split would suggest
(tracked `.py` per root at `40c4fe0a9`):

| Root | Files | Share |
|---|---|---|
| `tests` | 784 | 63.7% |
| `tools` | 180 | 14.6% |
| `scripts` | 90 | 7.3% |
| `agent` | 86 | 7.0% |
| `bridge` | 44 | 3.6% |
| `models` | 34 | 2.8% |
| `ui` | 10 | 0.8% |
| `worker` | **3** | **0.24%** |
| total | 1231 | |

A renamed `worker/` drops **0.24%** of the corpus, and `ui/` 0.8% — an order of
magnitude inside *any* percentage floor, so those roots would silently stop
being guarded while the floor stayed green. (The critique estimated ~12% on an
even-split assumption; the real measurement makes the case stronger, not weaker,
and a total floor is decisively insufficient here.) This is precisely the
fix-one-instance-never-sweep pattern this plan indicts #2093, #2430, #2557 and
#2597 for, so B13 gets the same treatment as B14 plus a per-root check:

```python
missing = [t for t in SCAN_DIRS if not (REPO_ROOT / t).is_dir()]
assert not missing, f"SCAN_DIRS roots absent from the checkout: {missing} — the sweep would silently skip them"
```

Keep the total floor as well. The two catch different failures — a vanished root
versus a corpus-wide collapse — and neither subsumes the other.

**The meta-guard flags itself unless it self-exempts by resolved path.** The
meta-guard matches the literal shapes (`"-r"` / `"-rn"` with `"grep"` as argv0)
and scans every test under `tests/`, but it lives in
`tests/unit/test_tracked_content_helper.py` and must *contain* those literals to
do its matching. On first run it is its own top offender. The plan's generic
escape hatch — "an explicit opt-out comment for a justified survivor" — is the
wrong instrument here: it invites stamping an opt-out on the one guard that must
never carry one, or loosening the matcher until the self-hit disappears, which
weakens it for every real offender.

Self-exempt by **resolved path**, using the idiom already in this repo at
`tests/unit/test_template_filter_registry.py:128-131`:

```python
self_path = Path(__file__).resolve()
...
if py_file.resolve() == self_path:
    continue
```

Never a filename substring — that would also exempt any future file whose name
happens to contain this one's.

Pair the self-exemption with a **planted-offender positive control in the same
file**: write a temp file under `tmp_path` containing
`subprocess.run(["grep", "-rn", "x", "tools/"])`, point the scanner at
`tmp_path`, and assert it is flagged. Without that control, a correct
self-exemption and a matcher that flags nothing at all are indistinguishable —
both present as a green meta-guard.

**Out-of-scope-but-adjacent**, recorded so the next reader is not surprised:
`scripts/checks/no_new_rebuild_callers.sh:46` has the same ambient-cwd hazard and
is invoked by no test; `reflections/maintenance.py::run_legacy_code_scan` is
notably the one place in the repo that already treats grep exit 2 as an error
rather than a pass. Neither is a test, so neither is in scope for #2809's AC.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The helper must not contain `except Exception: pass`. A `git` invocation
      that fails raises `TrackedScanError` carrying the exit code and stderr.
      Test: force failure via `GIT_INDEX_FILE=/dev/null` (empirically yields
      128) and assert `pytest.raises(TrackedScanError, match="128")` — the
      helper **raises**, never returns clean.
- [ ] **Scan-failure and vacuity must be asserted apart, not merely "the helper
      fails".** Both produce empty `git ls-files` stdout (measured: rc 128 vs
      rc 0), so a probe that only checks "did it fail" passes for the wrong
      reason. Two distinct classes make the distinction assertable:
      `pytest.raises(TrackedScanError)` for the broken index,
      `pytest.raises(VacuousScanError)` for `'nosuchdir/*.py'`. A test that
      accepts either class for either input does not verify Success Criterion 6.
- [ ] No exception handlers exist in the converted call sites today; none are
      added.

### Empty/Invalid Input Handling
- [ ] Empty pathspec match set → `VacuousScanError`, with a message naming the
      pathspec and the expected minimum. Test explicitly: pass
      `'nosuchdir/*.py'` and assert the helper fails rather than reporting clean
      (spike-1 proves raw `git grep` returns 1 here).
- [ ] Empty pattern → rejected by the helper with a clear error, since
      `git grep ""` matches every line and would produce a nonsense guard.
- [ ] Whitespace-only pattern → same rejection.

### Regex Dialect Coverage
- [ ] The metacharacter path is **exercised, not assumed**. A pattern such as
      `zzz\.never(` must report clean against the real corpus, and must trip on
      a planted `zzz.never(` in a tracked file. This is the test that would
      catch a builder adding `-F` and silently neutering A3.

### Error State Rendering
- [ ] A real violation must render `file:line` of the *tracked source*, not a
      `Binary file … matches` line. Assert the message contains `.py:` and does
      **not** contain `Binary file`.
- [ ] A scan failure must render distinguishably from a violation — different
      exception type or an unambiguous prefix — so a future triager can tell
      "the guard found something" from "the guard could not run".

## Test Impact

- [ ] `tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted` — **UPDATE**: route through the helper; add the `#2093`-style explanatory comment naming the `.pyc` hazard so it is not "simplified" back (required by #2809 AC2).
- [ ] `tests/unit/test_anthropic_client_semaphore.py::TestSharedModuleIsTheOnlyConstructor::test_no_unguarded_async_anthropic_instantiation` — **UPDATE**: route through the helper; preserve the `_ALLOWED_DIRECT_CONSTRUCTORS` exemption via the helper's `allow=` parameter.
- [ ] `tests/unit/test_memory_extraction.py::TestEventLoopSafety::test_no_direct_anthropic_client_grep_canary` — **UPDATE**: route through the helper. Single-file scope, so the floor is `min_files=1`.
- [ ] `tests/unit/test_no_legacy_paths.py::test_no_legacy_claude_code_paths` — **UPDATE**: replace the `if rc == 0:` gate with three-way triage; move the `ALLOWED_FILES` and `docs/plans/` exemptions into negative pathspecs. Corpus stays **all tracked file types**, not `*.py`.
- [ ] `tests/unit/test_template_filter_registry.py::test_no_hand_copied_filter_registration_in_tests` — **UPDATE**: add non-vacuity floor.
- [ ] `tests/unit/test_sdlc_lease_helper_binding.py::test_no_module_level_from_import_of_lease_helpers` — **UPDATE**: add non-vacuity floor.
- [ ] `tests/unit/test_sdlc_tool_wrapper.py::TestSkillMarkdownParity` (both tests) — **UPDATE**: add non-vacuity floor over `_iter_include_paths`.
- [ ] `tests/integration/test_dm_recovery.py::TestNoChatTitleFilterRemains::test_scanners_do_not_skip_titleless_dialogs` — **UPDATE**: add non-vacuity floor.
- [ ] `tests/unit/test_no_positional_query_get.py::test_no_positional_agent_session_query_get` — **UPDATE**: add non-vacuity floor **and** a per-root assertion; replace `if not root.exists(): continue` at `:66-68` with an accumulate-then-assert over all eight `SCAN_DIRS`. The total floor alone cannot see a lost `worker/` (0.24% of the corpus).
- [ ] `tests/unit/test_harness_model_coverage.py` (`_agent_py_files`) — **UPDATE**: replace `if not agent_dir.is_dir(): return []` with a raise; add floor.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py` — **NO CHANGE, verified**: its scanner only flags subprocess calls where `_argv_reaches_python(argv)` holds. `["git", "grep", …]` does not reach Python, so the new helper needs no `ALLOWLIST` entry. Confirmed by reading `_argv0_is_skipped`/`_argv_reaches_python` at `:216-260`.
- [ ] `tests/unit/test_plan_migration_invariant.py` — **NO CHANGE**: already correct; it is the reference pattern this plan generalizes.
- [ ] **NEW** `tests/unit/test_tracked_content_helper.py` — unit tests for the helper itself: match, clean, `TrackedScanError` on a broken index (128), `VacuousScanError` on a vacuous pathspec, the two asserted **apart**, empty pattern, whitespace-only pattern, BRE metacharacter round-trip, and `allow=` line filtering.
- [ ] **NEW** meta-guard (in the same new file): no test under `tests/` invokes a bare recursive `grep` over a directory. Self-exempts by resolved path, carries its own non-vacuity floor, and ships with a `tmp_path` planted-offender positive control.

## Rabbit Holes

- **Rewriting the 14 already-safe `rglob` walks to use `git grep`.** They are
  correct. `*.py` cannot match `*.pyc`. Converting them is churn that inflates
  the diff and the review surface with zero correctness gain. Document them as
  safe and move on.
- **Deleting the stale `.pyc` files as part of this fix.** Explicitly dropped in
  all three issues, and it would destroy the red state that AC1 depends on. The
  bytecode question is #2883.
- **Building a general-purpose repo-wide "banned string" registry.** Tempting
  once you see four guards doing the same thing, but it invents a config format,
  a loader, and a discovery mechanism to serve four call sites. The shared
  helper is the right altitude.
- **Chasing the `scripts/` and `reflections/` occurrences of the same shape.**
  Real, noted in Technical Approach, but they are not tests, so they are outside
  #2809's acceptance criteria and outside this lane.
- **Making the meta-guard clever.** An AST-based analyser that understands every
  way to build a grep argv will spend more time on false positives than the
  problem is worth. Match the literal shapes (`"-r"`/`"-rn"` with `"grep"` as
  argv[0]) and allow an explicit opt-out comment.

## Risks

### Risk 1: The non-vacuity floors become maintenance friction
**Impact:** A legitimate refactor that removes files trips a floor, and the next
author lowers the number reflexively rather than thinking, eroding the guard.
**Mitigation:** Set each floor well below the current count (roughly 60-70%, not
`current - 1`) so ordinary churn never trips it, and put the reason in the
assertion message rather than a comment, so the person who sees the failure sees
the rationale at the same moment.

### Risk 2: The demonstrated-red proof is faked or skipped
**Impact:** The whole point of #2809's AC3/AC4 is that the guard still catches a
real reintroduction. A converted guard that is green for the wrong reason is
worse than the bug, because it now *looks* rigorous.
**Mitigation:** Mutation-check every converted guard individually and paste the
observed FAIL output into the PR description. Do not batch this: a single
mutation that trips several guards proves nothing about the ones it did not
reach. (This is the standing repo lesson — a green test often reaches no code
at all.)

### Risk 3: `git grep` unavailable or the checkout is not a worktree
**Impact:** Every converted guard fails at once, in CI or on a machine where the
suite runs from an export rather than a clone.
**Mitigation:** This is the *intended* behavior — fail-closed. Exit 128 raises a
named scan-failure rather than reading as clean. Documented in the helper's
docstring and asserted by a helper unit test, so the failure is legible instead
of mysterious.

### Risk 4: The AC1 red state has evaporated by build time
**Impact:** The builder "verifies" AC1 against a checkout that would have passed
anyway, proving nothing — exactly the trap #2808 names ("a worktree-only green
proves nothing here").
**Mitigation:** Prerequisite check 3 asserts the stale artifact exists before the
build starts. If it is gone, plant an equivalent one rather than skipping.
Verification must run in **both** the primary checkout and a worktree, and the
two runs must agree.

### Risk 5: Scope creep from the sweep
**Impact:** 27 call sites is enough material to turn a bug fix into a
test-infrastructure project.
**Mitigation:** The sweep table fixes the disposition of all 27 up front: 4
convert, 6 get a floor, 17 are documented as safe. Anything discovered beyond
that table goes to a new issue, not into this PR.

## Race Conditions

**No race conditions identified in the shipped behavior** — the change is
test-local and the guards are synchronous.

One pre-existing race is *closed* by this work, and it is worth recording
because it is half the reason #2093 chose `git grep`:

### Race 1 (pre-existing, resolved by this plan): xdist sibling deletes a directory mid-walk
**Location:** `tests/unit/test_sdlc_review_finalize.py:1093` (and every
unconverted `grep -r` site)
**Trigger:** Under `-n auto`, a concurrent worker creates or removes a runtime
tree (`__pycache__`, `data/`, `logs/`) while `grep -r` is descending it.
**Data prerequisite:** none.
**State prerequisite:** the scanned corpus must be stable for the scan's duration.
**Mitigation:** `git grep` reads an atomic index snapshot and never touches
untracked runtime trees, so the corpus cannot shift underneath it. Verbatim from
`test_plan_migration_invariant.py:136-145`: *"a directory vanishing mid-walk
makes grep exit 2 and trips the returncode assertion below. `git grep` reads the
index, so it is race-free and never scans untracked runtime artifacts (#2093)."*

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2883] **Sweeping or purging the 1143 orphaned off-pin `.pyc`
  files.** Concern B from #2808/#2809, split out at plan time and filed as
  #2883. Deliberately separated for two reasons: #2807 states outright that
  "the test must not depend on that housekeeping being done", and bundling a
  fleet-wide file-deletion mechanism into a test-guard fix would make the
  destructive part of the change hard to review on its own merits. Once this
  lane lands, no test's verdict depends on bytecode, which is precisely what
  makes the deferral safe. **The pin-bump warning is NOT deferred** — it ships
  here, in the Documentation section, because it costs nothing and is the part
  that prevents recurrence.
- [SEPARATE-SLUG #2883] **A `tools/doctor.py` bytecode-drift check.** Same
  rationale; it is option 1 in #2883's decision list.
- **Nothing else is deferred.** The sweep (scope B) is fully in scope: all 27
  call sites are dispositioned in the Technical Approach table, 10 are modified
  here, and the remaining 17 are documented as safe with the reason recorded.

## Update System

**No update system changes required.** This lane modifies test code and one
documentation page only; it adds no dependency, no config file, no console
script, and no migration. `scripts/update/` is untouched.

The one adjacent interaction worth naming: `/update` is a plausible *home* for
an off-pin bytecode purge, but that is #2883's decision to make, not this
lane's.

## Agent Integration

**No agent integration required.** This is a test-suite change. Nothing new is
reachable from a Telegram message, no CLI entry point is added to
`pyproject.toml [project.scripts]`, and `bridge/telegram_bridge.py` imports
nothing new. `tests/tracked_content.py` is a test-only helper and is
deliberately not exposed to the agent.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/tracked-content-sweep-guards.md` — the convention:
      what a sweep guard is, why it must scan tracked content rather than the
      working tree, the three obligations (index-scoped corpus, three-way exit
      triage, non-vacuity floor), the `git grep`-over-`xargs` decision with the
      rc=0 evidence from spike-3, and how to use `tests/tracked_content.py`.
- [ ] Add the entry to the `docs/features/README.md` index table.

### Existing Documentation
- [ ] Update `docs/features/worktree-venv-isolation.md`: record that bumping
      `.python-version` **strands** the previous interpreter's bytecode in every
      checkout rather than replacing it, that nothing currently sweeps it, and
      link #2883. This is the concern-B piece that ships in this lane.
- [ ] Update `tests/README.md`: add the sweep-guard convention to the
      contribution guide so a new guard author finds it before writing
      `grep -rn`.

### Inline Documentation
- [ ] The `#2093`-style explanatory comment on each converted guard, naming the
      `.pyc` hazard (explicitly required by #2809 AC2 so it is not "simplified"
      back).
- [ ] Docstring on `assert_absent_from_tracked` covering all three obligations
      and the fail-closed exit-128 behavior.

## Success Criteria

- [ ] `tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted` passes in the primary checkout `~/src/ai` **with the stale off-pin `.pyc` still present** — no `__pycache__` deleted or modified first. (#2807 AC1, #2808 AC1, #2809 AC1)
- [ ] The same node passes in a fresh session worktree, and both runs agree at the same commit. (#2808 AC2)
- [ ] The result does not depend on which `grep` binary `PATH` resolves — no converted guard invokes `grep`. (#2807 AC2)
- [ ] **The scan is index-scoped and carries the hazard comment:** every converted guard routes through `git grep` via the helper, and each carries a `#2093`-style comment naming the `.pyc` hazard so it is not "simplified" back. (#2809 AC2)
- [ ] **Demonstrated ignored-artifact:** with a planted file under `tools/__pycache__/` containing the banned string, the test still passes. (#2808 AC3, #2809 AC3)
- [ ] **Demonstrated red:** reintroducing the banned string into a tracked `.py` under `tools/` or `agent/` fails the test, with the offending `file:line` in the message. (#2807 AC3, #2808 AC4)
- [ ] Every converted guard distinguishes "scan found nothing" from "scan failed to run" — an errored scan does not read as a pass. (#2808 AC5)
- [ ] Every converted guard distinguishes "scan found nothing" from "scan examined nothing" via a non-vacuity floor. (net-new, spike-1)
- [ ] **Scan-failure and vacuity are separately assertable:** `TrackedScanError` and `VacuousScanError` are distinct classes, and `tests/unit/test_tracked_content_helper.py` asserts each against the input that produces it (broken index → `TrackedScanError`; `'nosuchdir/*.py'` → `VacuousScanError`). (net-new, critique — makes Criterion 6 verifiable rather than assumed)
- [ ] **The six floored walks are covered:** each of B7, B8, B11, B12, B13, B14 asserts a minimum scanned-file count, and each floor is individually demonstrated-red by task 5's per-guard mutation. B13 and B14 additionally raise on a missing scan root rather than skipping it. (net-new, critique — 6 of the 10 touched files previously mapped to no criterion)
- [ ] **The regex dialect is pinned and exercised:** no converted guard passes `-F`, `-E`, or `-P`, and a helper test proves a BRE metacharacter pattern (`zzz\.never(`) both reports clean and trips on a planted match. (net-new, critique — an `-F` would leave A3 permanently green)
- [ ] **The meta-guard is proven to catch, not merely to pass:** it self-exempts by resolved path (not filename substring) and a `tmp_path` planted-offender positive control is flagged. (net-new, critique)
- [ ] `tests/` contains no remaining recursive-`grep`-over-a-directory assertion, enforced by the new meta-guard. (#2807 AC4)
- [ ] All 27 filesystem-walking absence assertions are enumerated with a disposition, each **converted, floored, or documented as safe (4 / 6 / 17)**. (#2809 AC4)
- [ ] **Neither converted sibling reads the ambient cwd.** `assert_absent_from_tracked` derives `REPO_ROOT` from `Path(__file__).resolve().parents[N]` and passes it as `cwd=` on every git call. Verified by running both nodes from `/tmp` and observing the same verdict as from the repo root. Today the two diverge from a foreign cwd (A2 returns rc=1 with empty stdout and passes vacuously; A3 returns rc=2 and fails); after the fix both must match their in-repo run. (#2808 AC7)
- [ ] A decision on concern B is recorded — deferred to #2883, with the pin-bump warning shipped here. (#2808 AC6, #2809 AC5)
- [ ] Tests pass (`/do-test`) via `scripts/pytest-clean.sh`, never bare `pytest`.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (guard conversion)**
  - Name: `guard-converter`
  - Role: the helper module, its unit tests, the meta-guard, and the four
    `subprocess`-based conversions (A1, A2, A3, A7).
  - Agent Type: builder
  - Resume: true

- **Builder (walk hardening)**
  - Name: `walk-hardener`
  - Role: non-vacuity floors on the six `rglob` walks (B7, B8, B11, B12, B13,
    B14). Independent of the helper, so it runs in parallel.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `sweep-validator`
  - Role: mutation-check every converted guard individually; run the primary
    node in both the primary checkout and a worktree.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `sweep-documentarian`
  - Role: the new feature doc, the index entry, and the two existing-doc updates.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build the tracked-content helper
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_tracked_content_helper.py` (create)
- **Informed By**: spike-1 (git grep returns 1 for an empty pathspec — a floor is mandatory), spike-2 (`tools/*.py` matches nested paths), spike-3 (`git grep` chosen over `xargs`; the xargs form returns rc=0 on empty input)
- **Assigned To**: guard-converter
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/tracked_content.py` with `assert_absent_from_tracked(pattern, *pathspecs, min_files, allow=())`.
- Define **two** exception classes: `TrackedScanError` (scan could not run) and
  `VacuousScanError` (scan examined too little). One class with two messages is
  not acceptable — it cannot be asserted apart except by string matching.
- Derive `REPO_ROOT` from `Path(__file__).resolve().parents[N]` and pass it as
  `cwd=` on **every** git call. Never the ambient cwd.
- **Order matters:** check `git ls-files`'s own returncode *before* counting its
  output. `rc != 0` → `TrackedScanError`; only then split stdout and compare
  length against `min_files` → `VacuousScanError`. A broken index and a vacuous
  pathspec both yield empty stdout (rc 128 vs 0), so counting first
  misclassifies a scan failure as vacuity and the Failure Path probe goes green
  for the wrong reason.
- Implement three-way `git grep -n` exit triage: 0 → violation with `file:line`;
  1 → clean; anything else → `TrackedScanError` carrying the exit code and stderr.
- Pass **no** regex flag. `git grep` defaults to POSIX BRE, matching `grep`'s
  own default, so A3's escaped patterns carry over byte-for-byte. Explicitly do
  not add `-F`, `-E`, or `-P`.
- Do **not** pass `--untracked`. Document the tracked-only boundary in the
  docstring.
- Reject an empty or whitespace-only pattern.
- Docstring covers all four obligations, the BRE dialect sentence, the
  fail-closed exit-128 behavior, the tracked-only boundary, and the rule that
  `allow=` is for line-substring exemptions only while path-shaped exemptions
  belong in negative pathspecs.
- Write `tests/unit/test_tracked_content_helper.py` covering: match, clean,
  `pytest.raises(TrackedScanError, match="128")` via `GIT_INDEX_FILE=/dev/null`,
  `pytest.raises(VacuousScanError)` via `'nosuchdir/*.py'` (the two asserted
  **apart**, each against the input that produces it), empty pattern,
  whitespace-only pattern, BRE metacharacter round-trip (`zzz\.never(` clean,
  then tripping on a planted `zzz.never(`), and `allow=` line filtering.

### 2. Convert the four vulnerable subprocess guards
- **Task ID**: build-conversions
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_sdlc_review_finalize.py`, `tests/unit/test_anthropic_client_semaphore.py`, `tests/unit/test_memory_extraction.py`, `tests/unit/test_no_legacy_paths.py`
- **Informed By**: spike-4 (the sweep table; A7 is net-new and appears in no issue)
- **Assigned To**: guard-converter
- **Agent Type**: builder
- **Parallel**: false
- A1 `test_sdlc_review_finalize.py:1093` — route through the helper over `'tools/*.py'` and `'agent/*.py'`. Add the `#2093`-style comment naming the `.pyc` hazard.
- A2 `test_anthropic_client_semaphore.py:164` — route through the helper; carry `_ALLOWED_DIRECT_CONSTRUCTORS` across as `allow=`. This is the **one** genuine line-substring exemption, since it filters matched output lines by module path rather than filtering the corpus. Note in the comment that this site was **vacuously green**, not merely unpinned.
- A3 `test_memory_extraction.py:2563` — route through the helper over `'agent/memory_extraction.py'`, `min_files=1`. Keep both patterns exactly as written (`anthropic\.Anthropic(`, `anthropic\.AsyncAnthropic(`) — they are BRE and need no change. Note that this site was false-**red**, correcting #2808.
- A7 `test_no_legacy_paths.py:25` — replace the `if rc == 0:` gate with three-way triage. Corpus stays **every tracked file type**, so the pathspec is `'*'` plus negative pathspecs, not `*.py`:
  ```python
  assert_absent_from_tracked(
      "Desktop/claude_code",
      "*",
      ":!docs/plans/",
      ":!tests/unit/test_no_legacy_paths.py",
      ":!scripts/update/verify.py",
      ":!scripts/update/run.py",
      min_files=1300,
  )
  ```
  Verified at `40c4fe0a9`: this exact pathspec set returns rc=1 (clean) from
  `git grep -l`, and `git ls-files` over it yields **2060** paths, so the
  exemptions are a drop-in replacement for the `ALLOWED_FILES` / `docs/plans/`
  stdout filtering and no `allow=` is needed here at all.
  **`min_files` is 1300, not the 1800 the critique suggested.** 1800 is 87% of
  the 2060-path corpus, which violates this plan's own 60-70% band (Risk 1) and
  would be tripped by ordinary churn — `docs/plans/` alone is 591 tracked files
  and is actively archived. 1300 sits at 63%.
- Do not delete or modify any `__pycache__` content.

### 3. Add non-vacuity floors to the six unfloored walks
- **Task ID**: build-floors
- **Depends On**: none
- **Validates**: `tests/unit/test_template_filter_registry.py`, `tests/unit/test_sdlc_lease_helper_binding.py`, `tests/unit/test_sdlc_tool_wrapper.py`, `tests/integration/test_dm_recovery.py`, `tests/unit/test_no_positional_query_get.py`, `tests/unit/test_harness_model_coverage.py`
- **Informed By**: spike-4 (these are `rglob("*.py")` walks — not `.pyc`-vulnerable; the only defect is the missing floor)
- **Assigned To**: walk-hardener
- **Agent Type**: builder
- **Parallel**: true
- Add a scanned-count floor to each, modelled on `tests/unit/test_update_pull_fetch_head_race.py`.
- Set each floor at roughly 60-70% of the current count, not `current - 1`, so ordinary churn never trips it.
- Put the rationale in the assertion message, not only in a comment.
- In `test_harness_model_coverage.py` (B14), replace `if not agent_dir.is_dir(): return []` with a raise — that early return is an explicit vacuous-green path.
- In `test_no_positional_query_get.py` (B13), apply the **same** treatment: replace `if not root.exists(): continue` at `:66-68` with accumulate-then-assert over all eight `SCAN_DIRS`:
  ```python
  missing = [t for t in SCAN_DIRS if not (REPO_ROOT / t).is_dir()]
  assert not missing, f"SCAN_DIRS roots absent from the checkout: {missing} — the sweep would silently skip them"
  ```
  Keep the total floor **as well**. A total floor cannot see a lost root: `worker/` is 3 of 1231 tracked `.py` (0.24%) and `ui/` is 10 (0.8%), both an order of magnitude inside any percentage band.
- Do **not** convert these to `git grep`. `*.py` cannot match `*.pyc`; conversion is churn.

### 4. Add the recurrence meta-guard
- **Task ID**: build-metaguard
- **Depends On**: build-conversions, build-floors
- **Validates**: `tests/unit/test_tracked_content_helper.py`
- **Assigned To**: guard-converter
- **Agent Type**: builder
- **Parallel**: false
- Add a test asserting no test under `tests/` invokes a bare recursive `grep` over a directory (argv[0] `grep` with `-r`/`-rn`).
- Match literal shapes only; provide an explicit opt-out comment for a justified survivor **elsewhere** in the suite.
- **Self-exempt by resolved path**, never by filename substring, using the idiom at `tests/unit/test_template_filter_registry.py:128-131`: `self_path = Path(__file__).resolve()` then `if py_file.resolve() == self_path: continue`. The meta-guard must contain the literals it matches, so it flags itself otherwise — and the opt-out comment is the wrong instrument for the one guard that must never carry one.
- **Ship a planted-offender positive control in the same file:** write a temp file under `tmp_path` containing `subprocess.run(["grep", "-rn", "x", "tools/"])`, point the scanner at `tmp_path`, and assert it is flagged. Without it, a correct self-exemption and a matcher that flags nothing are indistinguishable.
- Include a non-vacuity floor on the meta-guard's own scan — it must not become the thing it guards against.

### 5. Mutation-check every guard individually
- **Task ID**: validate-mutations
- **Depends On**: build-metaguard
- **Validates**: `tests/unit/test_sdlc_review_finalize.py`, `tests/unit/test_anthropic_client_semaphore.py`, `tests/unit/test_memory_extraction.py`, `tests/unit/test_no_legacy_paths.py`, `tests/unit/test_tracked_content_helper.py`
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- For **each** of the four converted guards separately: introduce the banned pattern into a tracked source file **that guard actually scans**, run the node, capture the FAIL output, revert. A single mutation tripping several guards proves nothing about the ones it did not reach — this satisfies #2809 AC3/AC4 and #2808 AC3/AC4, which are per-guard obligations, never a blanket run.
- For **each** of the six floored walks separately: force an empty scan (temporarily point the root at a nonexistent path), confirm the floor fails, revert.
- For B13 and B14 specifically, run a **second, distinct** mutation: remove one scan root only (B13: `worker/`, the 0.24% root) and confirm the *per-root* assertion fires. The total floor will still be green, which is the whole point of the separate check.
- Confirm the helper raises `TrackedScanError` (not `VacuousScanError`) when `GIT_INDEX_FILE=/dev/null`, and `VacuousScanError` (not `TrackedScanError`) for `'nosuchdir/*.py'`. Assert the classes apart, not merely "it failed".
- Confirm the BRE metacharacter test both reports clean and trips on a planted match, so an added `-F` would be caught.
- Confirm the meta-guard's `tmp_path` planted-offender control is flagged.
- Plant a file under `tools/__pycache__/` containing the banned string; confirm A1 still passes.
- Collect every FAIL output for the PR description.

### 6. Cross-checkout verification
- **Task ID**: validate-both-checkouts
- **Depends On**: validate-mutations
- **Validates**: `tests/unit/test_sdlc_review_finalize.py`, `tests/unit/test_anthropic_client_semaphore.py`, `tests/unit/test_memory_extraction.py`, `tests/unit/test_no_legacy_paths.py`, `tests/unit/test_tracked_content_helper.py`
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the primary node in the **primary checkout** with the stale `.pyc` present. It must pass.
- Run the same node in a **fresh worktree**. It must pass.
- Confirm the two agree at the same commit. A worktree-only green proves nothing (#2808).
- Run the A2 and A3 nodes from `/tmp` (foreign ambient cwd) and confirm each returns the **same verdict** as from the repo root. This is the #2808 AC7 observable: today they diverge (A2 passes vacuously, A3 fails); after the fix both must match.
- Use `scripts/pytest-clean.sh`; never bare `pytest`; never pattern-kill pytest processes.
- Set `PYTHONPATH` explicitly when running in a worktree — the shared venv `.pth` can otherwise import the wrong checkout.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-both-checkouts
- **Validates**: `docs/features/tracked-content-sweep-guards.md` (create), `docs/features/README.md`, `docs/features/worktree-venv-isolation.md`, `tests/README.md`
- **Assigned To**: sweep-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/tracked-content-sweep-guards.md` per the Documentation section, including the BRE dialect rule and the tracked-only boundary paragraph (why `--untracked` is deliberately not used).
- Add the entry to `docs/features/README.md`.
- Update `docs/features/worktree-venv-isolation.md` with the stranded-bytecode warning, linking #2883.
- Update `tests/README.md` with the sweep-guard convention.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: the Verification table as a whole (every row), plus the AC → task → verification-row mapping table
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the Verification table.
- Confirm all Success Criteria, including the per-issue AC mappings.
- Confirm the PR body carries `Closes #2807`, `Closes #2808`, `Closes #2809`.

## Acceptance Criteria Traceability

Every AC across all three issues maps to a plan task **and** a runnable
verification row. `V-n` refers to the numbered rows of the Verification table
below.

| AC | Text (abridged) | Task | Verification row |
|---|---|---|---|
| #2807 AC1 | Primary node passes with the stale `.pyc` still present | 2, 6 | V-1, V-2 |
| #2807 AC2 | Result does not depend on which `grep` binary `PATH` resolves | 2 | V-4 |
| #2807 AC3 | Demonstrated red on reintroduction into a tracked `.py` | 5 | V-17 |
| #2807 AC4 | Sweep of `tests/` finds no remaining recursive-`grep` shape | 4 | V-5, V-21 |
| #2808 AC1 | Passes without deleting or modifying any `__pycache__` | 2, 6 | V-1, V-2 |
| #2808 AC2 | Same node passes in a fresh worktree; both agree at one commit | 6 | V-19 |
| #2808 AC3 | Demonstrated ignore of build artifacts (planted `__pycache__` file) | 5 | V-16 |
| #2808 AC4 | Demonstrated red, with the offending `file:line` in the message | 5 | V-17 |
| #2808 AC5 | "Scan found nothing" separable from "scan failed to run" | 1, 5 | V-7, V-8 |
| #2808 AC6 | Concern-B decision recorded, deferred to a linked follow-up | — (No-Gos) | V-14, V-15 |
| #2808 AC7 | Converted siblings do not read the ambient cwd | 2, 6 | V-20 |
| #2809 AC1 | Passes in the primary checkout without deleting `__pycache__` | 2, 6 | V-1, V-2 |
| #2809 AC2 | Index-scoped scan carrying the `.pyc`-hazard comment | 2 | V-4, V-6 |
| #2809 AC3 | `.pyc`-shaped artifact present, test still passes | 5 | V-16 |
| #2809 AC4 | All filesystem-walking assertions enumerated, each dispositioned | 2, 3 | V-10, V-11, V-22 |
| #2809 AC5 | Decision recorded on sweeping off-pin `.pyc` | 7 | V-14, V-15 |
| net-new | Non-vacuity floor on every converted guard | 1 | V-8 |
| net-new | Per-root assertion on B13/B14 | 3 | V-18 |
| net-new | Regex dialect pinned to BRE | 1 | V-9, V-12 |
| net-new | Meta-guard self-exempts and is positively controlled | 4 | V-21 |

## Verification

Rows are numbered for the traceability table above. Every row is runnable as
written from the repo root unless a row states otherwise.

| # | Check | Command | Expected |
|---|-------|---------|----------|
| V-1 | Primary node passes with stale `.pyc` present | `./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q` | exit code 0 |
| V-2 | Stale off-pin `.pyc` was NOT deleted (AC1 precondition still holds) | `test -n "$(find tools agent -name '*.pyc' -not -name '*cpython-314*' -print -quit)"` | exit code 0 |
| V-3 | Tracked source is genuinely clean | `git grep -n "State NOT persisted" -- 'tools/*.py' 'agent/*.py'; test $? -eq 1` | exit code 0 |
| V-4 | No converted guard shells out to `grep` | `git grep -n '"grep"' -- 'tests/unit/test_sdlc_review_finalize.py' 'tests/unit/test_anthropic_client_semaphore.py' 'tests/unit/test_memory_extraction.py' 'tests/unit/test_no_legacy_paths.py'` | exit code 1 |
| V-5 | No bare recursive grep anywhere in `tests/` (meta-guard node) | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q` | exit code 0 |
| V-6 | Each converted guard carries the `.pyc`-hazard comment (#2809 AC2) | `for f in tests/unit/test_sdlc_review_finalize.py tests/unit/test_anthropic_client_semaphore.py tests/unit/test_memory_extraction.py tests/unit/test_no_legacy_paths.py; do git grep -q '2093\|pyc' -- "$f" \|\| { echo "MISSING: $f"; exit 1; }; done` | exit code 0, no output |
| V-7 | Helper raises `TrackedScanError` (**not** `VacuousScanError`) on a broken index | `GIT_INDEX_FILE=/dev/null python -c "import sys; sys.path.insert(0,'.'); from tests.tracked_content import assert_absent_from_tracked as a, TrackedScanError; ` `try: a('zzz','tools/*.py',min_files=1)` `except TrackedScanError as e: assert '128' in str(e); sys.exit(0)` `sys.exit(1)"` | exit code 0 |
| V-8 | Helper raises `VacuousScanError` (**not** `TrackedScanError`) on a vacuous pathspec | `python -c "import sys; sys.path.insert(0,'.'); from tests.tracked_content import assert_absent_from_tracked as a, VacuousScanError; ` `try: a('zzz','nosuchdir/*.py',min_files=1)` `except VacuousScanError: sys.exit(0)` `sys.exit(1)"` | exit code 0 |
| V-9 | No regex-dialect flag was added (an `-F` would leave A3 permanently green) | `git grep -nE '"-(F\|E\|P)"' -- tests/tracked_content.py` | exit code 1 |
| V-10 | All four converted guards pass | `./scripts/pytest-clean.sh tests/unit/test_sdlc_review_finalize.py tests/unit/test_anthropic_client_semaphore.py tests/unit/test_memory_extraction.py tests/unit/test_no_legacy_paths.py -q` | exit code 0 |
| V-11 | All six floored walks pass | `./scripts/pytest-clean.sh tests/unit/test_template_filter_registry.py tests/unit/test_sdlc_lease_helper_binding.py tests/unit/test_sdlc_tool_wrapper.py tests/unit/test_no_positional_query_get.py tests/unit/test_harness_model_coverage.py tests/integration/test_dm_recovery.py -q` | exit code 0 |
| V-12 | The BRE metacharacter path is exercised, not assumed | `git grep -c 'zzz\\\\.never(' -- tests/unit/test_tracked_content_helper.py` | output > 0 |
| V-13 | Helper exists and is index-scoped | `git grep -c 'git.*grep' -- tests/tracked_content.py` | output > 0 |
| V-14 | Stranded-bytecode warning shipped, linking #2883 | `git grep -c '2883' -- docs/features/worktree-venv-isolation.md` | output > 0 |
| V-15 | Concern-B follow-up issue is real and open | `gh issue view 2883 --json state -q .state` | output contains OPEN |
| V-16 | **Demonstrated ignored-artifact.** Planted `__pycache__` file is not read (#2808 AC3, #2809 AC3) | `mkdir -p tools/__pycache__ && printf 'State NOT persisted\n' > tools/__pycache__/zz_probe.cpython-312.pyc && ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q; rc=$?; rm -f tools/__pycache__/zz_probe.cpython-312.pyc; exit $rc` | exit code 0 |
| V-17 | **Demonstrated red, per guard, one at a time** (#2807 AC3, #2808 AC4). Repeat for each of the four converted guards against a file *that guard scans*; a single mutation tripping several proves nothing about the ones it did not reach. Shown for A1 | `printf '\n# State NOT persisted\n' >> tools/sdlc_stage_marker.py && ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q; rc=$?; git checkout -- tools/sdlc_stage_marker.py; test $rc -ne 0` | exit code 0, and FAIL output names `tools/sdlc_stage_marker.py:<line>` with no `Binary file` |
| V-18 | **Per-root assertion fires on a vanished root** where the total floor stays green (B13) | `git mv worker worker_zz && ./scripts/pytest-clean.sh tests/unit/test_no_positional_query_get.py -q; rc=$?; git mv worker_zz worker; test $rc -ne 0` | exit code 0, FAIL message names the missing root |
| V-19 | **Fresh worktree agrees with the primary checkout** at the same commit (#2808 AC2) | `git worktree add /tmp/zz-verify HEAD && (cd /tmp/zz-verify && PYTHONPATH=/tmp/zz-verify ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q); rc=$?; git worktree remove --force /tmp/zz-verify; exit $rc` | exit code 0, matching V-1 |
| V-20 | **Foreign ambient cwd yields the same verdict** for both converted siblings (#2808 AC7) | `cd /tmp && PYTHONPATH=$HOME/src/ai $HOME/src/ai/scripts/pytest-clean.sh "$HOME/src/ai/tests/unit/test_anthropic_client_semaphore.py" "$HOME/src/ai/tests/unit/test_memory_extraction.py::TestEventLoopSafety::test_no_direct_anthropic_client_grep_canary" -q` | exit code 0, identical to the in-repo run |
| V-21 | **Meta-guard positive control:** a planted offender under `tmp_path` is flagged | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q -k "positive_control or planted"` | exit code 0, at least 1 test selected |
| V-22 | Sweep table arithmetic holds: 4 converted + 6 floored + 17 safe = 27 (#2809 AC4) | `awk '/^\| # \| Site/,/^$/' docs/plans/overclaim-guard-greps-whole-worktree.md \| grep -c 'CONVERT\|ADD FLOOR\|DOCUMENT AS SAFE'` | rows account for all 27 sites |
| V-23 | Feature doc exists | `test -f docs/features/tracked-content-sweep-guards.md` | exit code 0 |
| V-24 | Feature doc is indexed | `git grep -c 'tracked-content-sweep-guards' -- docs/features/README.md` | output > 0 |
| V-25 | No production code touched | `test -z "$(git diff --name-only main... -- ':!tests/' ':!docs/')"` | exit code 0 |
| V-26 | Lint clean | `python -m ruff check .` | exit code 0 |
| V-27 | Format clean | `python -m ruff format --check .` | exit code 0 |

> **On V-17 and V-18.** These are *mutation* rows: they deliberately break the
> tree, observe a FAIL, and revert. Run them one at a time and confirm
> `git status` is clean afterwards. Never run them concurrently with another
> lane's suite on this machine, and never leave a mutation in place across an
> await. V-18 uses `git mv` rather than `rm` so the revert is exact.

## Critique Results

War room 2026-08-19, FULL depth (Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **READY TO BUILD (with concerns)** — 0 blockers, 9 concerns, 1 nit.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | The helper's obligations 2 and 3 have no fixed runtime order, and the plan's own failure probe conflates them. Measured at `054f0f0fa`: `GIT_INDEX_FILE=/dev/null git ls-files -- 'tools/*.py'` exits 128 with empty stdout, observably identical to a pathspec that legitimately matches nothing. If the floor runs first, a broken index raises the vacuity error rather than the named scan failure, so the Failure Path Test Strategy probe goes green for the wrong reason and Success Criterion 6 ships unverified. | pending | Triage `git ls-files`'s own returncode before counting its output: `if ls.returncode != 0: raise TrackedScanError(f"git ls-files failed (rc={ls.returncode}): {ls.stderr.strip()}")`, then split stdout and compare length against `min_files`. Use two distinct exception classes, `TrackedScanError` and `VacuousScanError`, and assert them apart in `tests/unit/test_tracked_content_helper.py` via `pytest.raises(TrackedScanError, match="128")` under `GIT_INDEX_FILE=/dev/null` and a separate `pytest.raises(VacuousScanError)` for `'nosuchdir/*.py'`. One class with two messages cannot be asserted apart without string matching. |
| CONCERN | Risk & Robustness | The signature `assert_absent_from_tracked(pattern, *pathspecs, min_files, allow=())` never states the regex dialect of `pattern`, but the four call sites disagree. A1 passes a literal; A3 at `tests/unit/test_memory_extraction.py:2559-2560` passes BRE with escaped metacharacters (`anthropic\.Anthropic(`). A builder reaching for `-F` (defensible, since three of four patterns are literals) turns A3's `\.` into a literal backslash-dot that never matches, leaving A3 permanently green. That is the vacuous-green class the plan exists to close, reintroduced by the fix. | pending | `git grep` defaults to POSIX **basic** regular expressions, the same dialect `grep` uses without `-E`, so A3's patterns carry over byte-for-byte with no flag change. Do not add `-F`, `-E`, or `-P`. Docstring line: "`pattern` is a POSIX basic regular expression interpreted by `git grep` exactly as `grep` would; `(` is literal, `\.` is a literal dot." Add a helper test that exercises the metacharacter path rather than assuming it: a pattern such as `zzz\.never(` must pass clean and must trip on a planted `zzz.never(` in a tracked file. |
| CONCERN | Risk & Robustness | Task 4's meta-guard matches the literal shapes (`"-r"` / `"-rn"` with `"grep"` as argv0) and scans every test under `tests/`, but it will live in `tests/unit/test_tracked_content_helper.py` and must contain those literals to do its matching. It flags itself on first run. The plan's only escape, "an explicit opt-out comment for a justified survivor", invites stamping an opt-out on the guard that must not carry one, or loosening the matcher until the self-hit disappears, which weakens it for every real offender. | pending | Self-exempt by resolved path using the idiom already in this repo at `tests/unit/test_template_filter_registry.py:129-131`: `self_path = Path(__file__).resolve()` then `if py_file.resolve() == self_path: continue`. Never a filename substring, which would also exempt any future file whose name contains this one's. Pair it with a positive control in the same file: write a temp file under `tmp_path` containing `subprocess.run(["grep", "-rn", "x", "tools/"])`, point the scanner at `tmp_path`, and assert it is flagged. Without that control, a correct self-exemption and a broken matcher are indistinguishable. |
| CONCERN | Scope & Value | Task 3 modifies six of the ten touched test files, yet no Success Criterion covers it. The floor criterion is scoped to converted guards only, and the AC4 criterion offers a binary disposition ("converted or documented as safe") into which ADD FLOOR does not fit. Open Question 3 concedes a strict reading permits dropping task 3 and halving the diff. Sixty percent of the touched surface would ship with nothing for the reviewer to check it against. | pending | Keep task 3 (a guard that can silently scan nothing is the same defect class) and close the open question by adding one criterion: "Each of the six unfloored walks (B7, B8, B11, B12, B13, B14) asserts a minimum scanned-file count, and each floor is individually demonstrated-red by task 5's per-guard mutation." Then amend the AC4 criterion from the two-way "converted or documented as safe" to the three-way "converted, floored, or documented as safe (4 / 6 / 17)" so the criterion asserts the sweep table's own arithmetic. |
| CONCERN | Scope & Value | A single `allow=()` parameter is asked to carry three incompatible exemption semantics. A2 at `tests/unit/test_anthropic_client_semaphore.py:176-180` filters output lines by module-path substring. A7 at `tests/unit/test_no_legacy_paths.py:33-37` filters file paths two ways at once, exact membership in `ALLOWED_FILES` plus a prefix rule on `docs/plans/`. One tuple matched by substring over-exempts: `scripts/update/run.py` would also exempt a future `tools/scripts/update/run.py`, and any allowed fragment appearing in matched source text rather than in a path would exempt a real violation. | pending | Push every path-shaped exemption into git pathspec exclusions, which git applies to the corpus instead of to stdout. No new parameter is needed since the helper already takes `*pathspecs`; this is the idiom at `tests/unit/test_plan_migration_invariant.py:150` (`f":!{this_file.relative_to(REPO_ROOT)}"`). A7 becomes `assert_absent_from_tracked("Desktop/claude_code", "*", ":!docs/plans/", ":!tests/unit/test_no_legacy_paths.py", ":!scripts/update/verify.py", ":!scripts/update/run.py", min_files=1800)`. Reserve `allow=` for A2's genuine line-substring case and say so in the docstring. |
| CONCERN | Scope & Value | Moving to `git grep` closes the build-artifact hole and opens an undiscussed one: without `--untracked` it does not see brand-new untracked source. Verified here, a freshly written `tools/__zztmp_probe.py` carrying the banned string returned rc=1 (clean) from `git grep -l "State NOT persisted" -- 'tools/*.py'` and rc=0 only with `--untracked`. The feature doc is meant to teach the convention repo-wide, and a reader will assume a local run catches their new file. The meta-guard inherits the same gap for a newly authored uncommitted test. | pending | Do not add `--untracked`. It implies `--exclude-standard` so it would still skip gitignored `__pycache__` (confirmed: the `--untracked` sweep returned only `.py` and `.md` hits, zero `.pyc`), but it re-walks the working tree and reopens the mid-walk race that Race 1 credits `git grep` with closing. Instead record the accepted boundary in `docs/features/tracked-content-sweep-guards.md` and the helper docstring: "The corpus is tracked content only. A violation in a new, unstaged file is caught on first commit, which is where CI and the nightly detector read. `--untracked` would close that window but reopens the #2093 mid-walk race, so it is deliberately not used." |
| CONCERN | History & Consistency | The Success Criterion for #2808 AC7 asks the two cwd-less siblings to "fail loudly rather than passing vacuously when the ambient cwd is not the repo root", but obligation 1 makes that state unreachable by mandating `cwd=REPO_ROOT` "always explicit". After conversion neither guard has an ambient-cwd behavior to be loud about. As written the criterion cannot be demonstrated, so the builder either marks it done without evidence or contrives a test for a path the design deleted. | pending | Restate the bullet as the property the fix delivers: "Neither converted sibling reads the ambient cwd. `assert_absent_from_tracked` derives `REPO_ROOT` from `Path(__file__).resolve().parents[N]` and passes it as `cwd=` on every git call. Verified by running both nodes from `/tmp` and observing the same verdict as from the repo root." The observable is concrete: today the two diverge from a foreign cwd (A2 returns rc=1 with empty stdout and passes vacuously, A3 returns rc=2 and fails), and after the fix both must match their in-repo run. |
| CONCERN | History & Consistency | Obligation 1 requires "an explicit `*.py` pathspec", but A7's corpus is not Python. `tests/unit/test_no_legacy_paths.py:26-30` runs `git grep -l "Desktop/claude_code"` with no pathspec and must keep scanning every tracked file type, since the legacy path can return in a shell script, a `.md`, or a JSON config. Task 2 then asks for "a `*.py`-free but explicit pathspec", which reads as contradicting the contract the same plan just stated. A Python-only helper would silently narrow this guard from 2654 tracked files to 780. | pending | Reword obligation 1 to: "**Index-scoped corpus.** `git grep` with `cwd=REPO_ROOT` (always explicit, never inherited from pytest's ambient cwd) and an explicit pathspec, never an implicit whole-repo default. `*.py` for the source-code guards; `*` plus negative pathspecs for corpus-wide guards such as A7." Pin A7 in the task text: pathspec `*`, exclusions `:!docs/plans/` and the three `ALLOWED_FILES` entries, `min_files` from the current tracked count (2654) at the plan's own 60-70 percent band, about 1800. |
| CONCERN | History & Consistency | The sweep table singles out B14 for a raise because of its explicit vacuous early return, but B13 carries the identical shape and gets floor-only treatment. `tests/unit/test_no_positional_query_get.py:66-68` reads `root = REPO_ROOT / top` then `if not root.exists(): continue` inside a loop over eight SCAN_DIRS. A total-count floor does not catch this: losing one root drops about an eighth of the corpus, comfortably inside the 60-70 percent band, so a renamed `worker/` or `models/` silently stops being guarded while the floor stays green. This is the same fix-one-instance-never-sweep pattern the plan indicts #2093, #2430, #2557 and #2597 for. | pending | In `_iter_python_files`, replace `if not root.exists(): continue` with accumulation plus a hard assertion naming the missing root: `missing = [t for t in SCAN_DIRS if not (REPO_ROOT / t).is_dir()]` then `assert not missing, f"SCAN_DIRS roots absent from the checkout: {missing} — the sweep would silently skip them"`. Keep the total floor as well; the two catch different failures (a vanished root versus a corpus-wide collapse) and neither subsumes the other. Update the B13 row's Disposition cell to "ADD FLOOR — convert to raise" so it matches B14. |
| NIT | Structural check | Tasks 5 through 8 (validate-mutations, validate-both-checkouts, document-feature, validate-all) carry no `Validates:` field, while tasks 1 through 4 all do. Every other structural check passes: sections present, task numbering contiguous, all `Depends On` references resolve, no cycles, all three prerequisites currently green, and the three missing file paths are the ones this plan creates. | pending | Add a `Validates:` line to each: task 5 and 6 validate the four converted guard nodes plus `tests/unit/test_tracked_content_helper.py`; task 7 validates `docs/features/tracked-content-sweep-guards.md`; task 8 validates the Verification table as a whole. |

---

## Open Questions

1. **Meta-guard strictness.** Should the meta-guard ban *all* `subprocess` calls
   to `grep` from `tests/`, or only the recursive-over-a-directory shape? Two
   sites (A4, A5) grep a single absolute file path and are genuinely safe; a
   blanket ban would force them through the helper for no correctness gain, but
   a shape-matching ban is more code and can be evaded. The plan currently
   assumes **shape-matching with an opt-out comment**.

2. **Non-vacuity floor placement.** The plan sets floors at roughly 60-70% of
   current counts. Is a hardcoded integer the right instrument, or should the
   floors live in one table in `tests/tracked_content.py` so a fleet-wide
   refactor updates them in one place? One table is tidier but couples ten
   unrelated tests to one file.

3. **Scope confirmation on the six `rglob` floors.** These are not
   `.pyc`-vulnerable and are arguably a separate concern from the reported bug.
   #2809's AC4 says "each is either converted **or documented as safe**" — a
   strict reading permits documenting them as safe (they cannot be fooled by
   artifacts) and dropping task 3 entirely, which would cut the diff roughly in
   half. The plan takes the stricter reading because a guard that can silently
   scan nothing is the same defect class. Confirm which reading is wanted.
