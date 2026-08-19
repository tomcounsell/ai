---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2807
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-19T08:38:58Z
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
   (`grep -r tools/`), which is why `__pycache__` is read. After: the *file list
   comes from the index* and each file's *content is read from the working tree*
   (`git grep -- 'tools/*.py'`). Those are two different sources and the
   distinction matters at step 4 — see obligation 1.
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

def assert_absent_from_tracked(pattern, *pathspecs, min_files):
    """Assert no tracked file matching *pathspecs* contains *pattern*."""
```

There is deliberately **no `allow=` parameter**. Every exemption across all four
call sites turned out to be path-shaped, so all of them belong in negative
pathspecs — see "Exemptions go in the pathspec" below. Shipping an unused
`allow=` would leave the substring hazard in the signature for a future author
to reach for.

Two **distinct** exception classes are mandatory, not stylistic. A single class
with two messages cannot be asserted apart except by string matching, and the
whole point of the helper is that "scan failed" and "scan examined nothing" stay
separable from each other and from "clean". They are asserted apart in
`tests/unit/test_tracked_content_helper.py` via
`pytest.raises(TrackedScanError, match="128")` under `GIT_INDEX_FILE=/dev/null`
and a separate `pytest.raises(VacuousScanError)` for `'nosuchdir/*.py'`.

Four obligations, which the current call sites each get wrong in a different way:

1. **Index-scoped file list, working-tree content.** This is a two-part contract
   and getting it wrong in either direction breaks a different guarantee:

   > `git grep` resolves the **file list** from the index and reads each file's
   > **content from the working tree**. `--cached` would read content from the
   > index instead. This helper deliberately uses the working-tree form so an
   > uncommitted reintroduction is caught before it is staged.

   Measured at `7ba89ca5c`: an unstaged append to `tools/doctor.py` is found by
   `git grep` (rc=0) and **not** by `git grep --cached` (rc=1). So "index-scoped"
   describes only *which files are opened*, never *what bytes are compared*.

   Two consequences the rest of this plan depends on. First, `--cached` is not
   an available fix for anything here: it would make V-17's unstaged
   `printf … >> tools/sdlc_stage_marker.py` invisible and turn the
   demonstrated-red proof green. Second, because content comes from the working
   tree, a tracked file that is *absent* from the working tree is silently
   skipped — which is obligation 4's problem, not a theoretical one.

   Invoke with `cwd=REPO_ROOT` (always explicit — never inherited from pytest's
   ambient cwd) and an **explicit pathspec, never an implicit whole-repo
   default**. `*.py` for the source-code guards; `*` plus
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

   # Count what the scan can actually READ, not what the index lists.
   present = [p for p in files if (REPO_ROOT / p).exists()]
   absent = sorted(set(files) - set(present))
   if absent:
       raise VacuousScanError(
           f"{len(absent)} of {len(files)} tracked paths are absent from the "
           f"working tree, so git grep skipped them silently: {absent[:5]}"
       )
   if len(present) < min_files:
       raise VacuousScanError(...)
   ```

3. **Three-way exit triage on `git grep`.** `0` → match, report `file:line`.
   `1` → clean. **anything else** → raise `TrackedScanError`, distinct from a
   pass. Exit 128 (not a worktree / unreadable index) and 129 (bad flag) must
   never read as clean. This is the obligation `test_no_legacy_paths.py`
   currently violates despite already using `git grep`.

4. **Non-vacuity floor — over readable files, plus an exact absence check.**
   The floor must count what `git grep` can actually open. Counting index rows
   instead reintroduces the vacuous-green class *inside this plan's flagship
   mitigation*: `git ls-files` reports index entries while `git grep` reads
   working-tree content, so a tracked file missing from disk (plain `mv`/`rm`,
   an interrupted checkout, a sparse checkout) leaves the index count intact
   while `git grep` skips the file and returns 1 — clean.

   Measured at `7ba89ca5c` with `tools/doctor.py` moved aside by a plain `mv`:

   | Probe | Result |
   |---|---|
   | `git grep -l "worktree_interpreters" -- 'tools/*.py'` | rc=0 → **rc=1 (false clean)** |
   | `git ls-files -- 'tools/*.py'` | still **180** |
   | present-on-disk count for `'tools/*.py' 'agent/*.py'` | **265** of 266 |

   **Two checks are required, and a `present`-count floor alone is not enough.**
   With A1's floor at `min_files=170`, 265 present clears it comfortably, so the
   floor stays green on exactly the failure that produced the false clean. Only
   an exact absence check fires. Verified at `7ba89ca5c`: present-count floor
   fires? **False**. Absent-set check fires? **True**.

   So: raise `VacuousScanError` if **any** corpus path is absent from the working
   tree, *and* separately raise it if `len(present) < min_files`. The two catch
   different failures — a partially-materialized checkout versus a corpus-wide
   collapse or a pathspec typo — and neither subsumes the other. This is the same
   reasoning that makes B13 keep both a per-root check and a total floor.

   Failing closed on a sparse or interrupted checkout is intended, not
   collateral: per Risk 3 a guard that cannot see its whole corpus must say so
   rather than report clean. Modelled on the strongest existing pattern in the
   repo, `tests/unit/test_update_pull_fetch_head_race.py` (`__file__`-anchored
   roots, explicit required-anchor list, `len(found) > 250` floor).

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

**Exemptions go in the pathspec. Every one of them is path-shaped, so `allow=`
has no call sites and is not built.** An earlier draft reserved `allow=` for A2
on the belief that A2 filtered output *lines* by a genuinely line-scoped rule.
It does not. Every member of `_ALLOWED_DIRECT_CONSTRUCTORS` at
`tests/unit/test_anthropic_client_semaphore.py:149-158` is a **file path**, and
the filter at `:180-181` (`not any(mod in line for mod in ...)`) drops whole
output lines by path substring. That is whole-file exemption — semantically
identical to a negative pathspec, just implemented less safely.

Worse, the substring is matched against the entire `path:lineno:content` line
rather than against the path field. A line in an unapproved module that merely
*mentions* `agent/llm/wrapper.py` in a comment while calling
`anthropic.AsyncAnthropic(` is silently exempted. A7 at
`tests/unit/test_no_legacy_paths.py:33-37` has the mirror-image problem, filtering
*file paths* two ways at once (exact membership in `ALLOWED_FILES` plus a prefix
rule on `docs/plans/`), and one substring-matched tuple over-exempts there too:
`scripts/update/run.py` would also exempt a future `tools/scripts/update/run.py`.

So `allow=` would ship the exact mechanism this plan rejects, with **zero**
legitimate consumers. Leave it out of the signature entirely rather than
documenting it as reserved — a reserved-but-unused parameter is the one thing
that would send a future author back to substring filtering.

Push every exemption into git pathspec exclusions, which git applies to the
corpus instead of to stdout. No new parameter is needed — the helper already
takes `*pathspecs`. This is the idiom already at
`tests/unit/test_plan_migration_invariant.py:150`
(`f":!{this_file.relative_to(REPO_ROOT)}"`).

Keep `_ALLOWED_DIRECT_CONSTRUCTORS` as a module-level constant that *feeds* the
negative-pathspec list, so the six approved modules keep their
`#1055`/`#1193`/`#1262`/`#1925` provenance comments rather than losing them to a
literal inline list.

**Accepted boundary: the corpus is tracked *files*, read from the working
tree.** Moving to `git grep` closes the build-artifact hole and opens a smaller
one — without `--untracked` it does not see brand-new, never-added source.
**Do not add `--untracked`.** It implies `--exclude-standard`, so it would still
skip gitignored `__pycache__` and buy nothing against the reported defect, while
re-walking the working tree and reopening the mid-walk hazard that Race 1
credits `git grep` with narrowing.

State the boundary precisely — the earlier "caught on first commit" phrasing was
wrong for the common case. Because content is read from the working tree, an
edit to an **already-tracked** file is caught immediately, staged or not; only a
brand-new **untracked** file is invisible, and only until it is `git add`ed
(added to the index — not committed). Record this in both
`docs/features/tracked-content-sweep-guards.md` and the helper docstring:

> *"The corpus is every tracked file matching the pathspec, with content read
> from the working tree. A violation in an already-tracked file is caught
> immediately, staged or not. A violation in a brand-new untracked file is
> invisible until the file is added to the index. `--untracked` would close that
> window but reopens the #2093 mid-walk hazard, so it is deliberately not used."*

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

**The meta-guard's own floor and the positive control collide unless root and
floor are independent parameters.** The meta-guard needs a non-vacuity floor (it
must not become the thing it guards against), but the control points the scanner
at a `tmp_path` holding exactly one file. If the floor is baked into the scanner,
the control's one-file root trips the floor *before* reaching the offender
assertion, so the control passes for the wrong reason — reintroducing the exact
failure it was added to rule out and leaving V-21 uninformative.

Parameterize both:

```python
def _scan_for_bare_grep(root: Path, *, min_files: int) -> list[str]:
    """Return paths under *root* that invoke a bare recursive grep."""
    scanned = sorted(root.rglob("*.py"))
    if len(scanned) < min_files:
        raise AssertionError(
            f"scanned {len(scanned)} files under {root}, floor {min_files} — "
            "the sweep would be vacuous"
        )
    ...
```

The real guard calls `_scan_for_bare_grep(TESTS_DIR, min_files=500)`; the control
calls `_scan_for_bare_grep(tmp_path, min_files=1)` and asserts the planted path
is in the returned list.

**`min_files=500`, not the 600 the critique proposed.** `tests/` holds 784
tracked `.py` at `7ba89ca5c`, so 600 is 76.5% — above this plan's own 60-70%
band (Risk 1) and exposed to ordinary churn. 500 sits at 63.8%.

Never let the control pass a floor of `0`: a scanner that returned `[]`
unconditionally would then satisfy both callers and both would stay green.

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
- [ ] `tests/unit/test_anthropic_client_semaphore.py::TestSharedModuleIsTheOnlyConstructor::test_no_unguarded_async_anthropic_instantiation` — **UPDATE**: route through the helper; convert the six `_ALLOWED_DIRECT_CONSTRUCTORS` entries into negative pathspecs (they are all file paths, so the current whole-line substring filter is unsafe whole-file exemption). Keep the constant as the source of the exclusion list so its provenance comments survive. Post-exclusion corpus 304, `min_files=200`.
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
- [ ] **NEW** `tests/unit/test_tracked_content_helper.py` — unit tests for the helper itself: match, clean, `TrackedScanError` on a broken index (128), `VacuousScanError` on a vacuous pathspec, the two asserted **apart**, `VacuousScanError` naming an absent-from-disk tracked path, empty pattern, whitespace-only pattern, and the BRE metacharacter round-trip (probe written as a raw string, `r"zzz\.never("`). No `allow=` coverage — the parameter is not built.
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
**Mitigation (narrowed, not eliminated):** `git grep` resolves its **file list**
from an atomic index snapshot and never descends untracked runtime trees, so the
*set of paths scanned* cannot shift underneath it and `__pycache__`, `data/` and
`logs/` are never entered at all. That removes the reported failure mode.

It does **not** make the scan wholly race-free, and the plan should not claim it
does: content is read from the working tree file-by-file, so a sibling rewriting
a tracked file mid-scan can still be observed at either version. What changes is
the consequence — a shifting *corpus* silently changes what was guarded, whereas
a shifting *file version* is a benign read of one file or the other, and a file
that disappears outright is caught by obligation 4's absence check rather than
being skipped in silence.

`test_plan_migration_invariant.py:136-145` states it more strongly than is
strictly true (*"`git grep` reads the index, so it is race-free"*). Task 7 should
carry the narrowed wording into the new convention doc rather than copying that
sentence forward, since this lane is the one that makes it doctrine.

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
- [ ] **The floor counts files the scan can actually read, and any tracked path absent from the working tree is an error.** `git ls-files` reports index rows while `git grep` reads working-tree content, so counting index rows leaves a false-clean hole inside the mitigation itself. A present-count floor alone does not close it (265 of 266 present clears a floor of 170), so the helper additionally raises on a non-empty absent set. Demonstrated by V-18b's plain-`mv` mutation, which `git mv` cannot reach. (net-new, critique round 2)
- [ ] **No guard uses substring exemption.** `assert_absent_from_tracked` has no `allow=` parameter; all six A2 exemptions and all four A7 exemptions are negative pathspecs applied to the corpus, not filters over stdout. (net-new, critique round 2)
- [ ] **Scan-failure and vacuity are separately assertable:** `TrackedScanError` and `VacuousScanError` are distinct classes, and `tests/unit/test_tracked_content_helper.py` asserts each against the input that produces it (broken index → `TrackedScanError`; `'nosuchdir/*.py'` → `VacuousScanError`). (net-new, critique — makes Criterion 6 verifiable rather than assumed)
- [ ] **The six floored walks are covered:** each of B7, B8, B11, B12, B13, B14 asserts a minimum scanned-file count, and each floor is individually demonstrated-red by task 5's per-guard mutation. B13 and B14 additionally raise on a missing scan root rather than skipping it. (net-new, critique — 6 of the 10 touched files previously mapped to no criterion)
- [ ] **The regex dialect is pinned and exercised:** no converted guard passes `-F`, `-E`, or `-P`, and a helper test proves a BRE metacharacter pattern (`zzz\.never(`) both reports clean and trips on a planted match. (net-new, critique — an `-F` would leave A3 permanently green)
- [ ] **The meta-guard is proven to catch, not merely to pass:** it self-exempts by resolved path (not filename substring) and a `tmp_path` planted-offender positive control is flagged. Its scanner takes root and floor as independent parameters, so the control's one-file root reaches the offender assertion instead of tripping the floor. (net-new, critique)
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
- Create `tests/tracked_content.py` with `assert_absent_from_tracked(pattern, *pathspecs, min_files)`. **No `allow=` parameter** — every exemption across all four call sites is path-shaped and belongs in a negative pathspec.
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
- **Count files that can actually be read, and assert none is missing.** After
  the returncode triage, filter `files` to those that exist on disk. Raise
  `VacuousScanError` if **any** corpus path is absent from the working tree, and
  separately if `len(present) < min_files`. Both are required: `git ls-files`
  reports index rows while `git grep` reads working-tree content, and with one
  file moved aside the present count is 265 of 266 — comfortably above A1's floor
  of 170, so the floor alone stays green on a measured false clean (verified at
  `7ba89ca5c`). See obligation 4.
- Do **not** reach for `git grep --cached` to close that gap. `--cached` reads
  content from the index, which would make V-17's unstaged
  `printf … >> tools/sdlc_stage_marker.py` invisible and turn the
  demonstrated-red proof green.
- Implement three-way `git grep -n` exit triage: 0 → violation with `file:line`;
  1 → clean; anything else → `TrackedScanError` carrying the exit code and stderr.
- Pass **no** regex flag. `git grep` defaults to POSIX BRE, matching `grep`'s
  own default, so A3's escaped patterns carry over byte-for-byte. Explicitly do
  not add `-F`, `-E`, or `-P`.
- Do **not** pass `--untracked`. Document the tracked-files boundary in the
  docstring.
- Reject an empty or whitespace-only pattern.
- Docstring covers all four obligations, the BRE dialect sentence, the
  fail-closed exit-128 behavior, and the two-part corpus contract stated verbatim:
  *"`git grep` resolves the file list from the index and reads each file's content
  from the working tree; `--cached` would read content from the index instead.
  This helper deliberately uses the working-tree form so an uncommitted
  reintroduction is caught before it is staged."* Follow it with the boundary
  sentence: a violation in an already-tracked file is caught immediately, staged
  or not; a violation in a brand-new untracked file is invisible until the file is
  added to the index. Do **not** describe the corpus as "index-scoped content" —
  that is the error this round corrects, and the docstring is where it would
  become doctrine.
- Write `tests/unit/test_tracked_content_helper.py` covering: match, clean,
  `pytest.raises(TrackedScanError, match="128")` via `GIT_INDEX_FILE=/dev/null`,
  `pytest.raises(VacuousScanError)` via `'nosuchdir/*.py'` (the two asserted
  **apart**, each against the input that produces it), a corpus with one tracked
  file moved aside → `VacuousScanError` naming the absent path, empty pattern,
  whitespace-only pattern, and the BRE metacharacter round-trip.
- **Name the test functions so `-k` can select them unambiguously.** V-7 selects
  on `scan_error`, V-8 on `vacuous`, V-21 on `positive_control`. Those substrings
  must appear in exactly the intended test names.
- **Write the BRE probe pattern as a raw string:** `PROBE = r"zzz\.never("`, so
  the file carries the two-character sequence backslash-dot and the Python value
  handed to `git grep` is `zzz\.never(`. This matters for V-12, which greps the
  test file for that literal: a raw string puts **one** backslash in the file
  bytes, and V-12's `'zzz\\.never('` matches it. Writing it non-raw as
  `"zzz\\.never("` would put **two** backslashes in the file bytes and V-12 would
  need four — measured both ways at `7ba89ca5c`. Raw is also the repo-normal way
  to write a regex literal, so it removes the trap rather than documenting it.
  The test asserts the pattern reports clean against the real corpus and trips on
  a planted `zzz.never(`.

### 2. Convert the four vulnerable subprocess guards
- **Task ID**: build-conversions
- **Depends On**: build-helper
- **Validates**: `tests/unit/test_sdlc_review_finalize.py`, `tests/unit/test_anthropic_client_semaphore.py`, `tests/unit/test_memory_extraction.py`, `tests/unit/test_no_legacy_paths.py`
- **Informed By**: spike-4 (the sweep table; A7 is net-new and appears in no issue)
- **Assigned To**: guard-converter
- **Agent Type**: builder
- **Parallel**: false
**Every conversion pins its own `min_files`.** All four are measured at
`7ba89ca5c` and sit inside the 60-70% band Risk 1 mandates. Leaving any of them
to the builder invites the `current - 1` floors Risk 1 forbids:

Counts are the **post-exclusion** corpus — the same pathspec set the helper
passes to `git ls-files`, so the floor is compared against what is actually
scanned:

| Site | Corpus | Tracked files | `min_files` | Share |
|---|---|---|---|---|
| A1 | `'tools/*.py' 'agent/*.py'` | 266 | **170** | 64% |
| A2 | `'agent/*.py' 'bridge/*.py' 'tools/*.py'` minus 6 exclusions | 304 | **200** | 66% |
| A3 | `'agent/memory_extraction.py'` | 1 | **1** | single file |
| A7 | `'*'` minus 4 exclusions | 2060 | **1300** | 63% |

Put the measured count and its commit in the **assertion message**, not a
comment, per Risk 1 — the person who trips the floor must see the rationale at
the same moment:
`f"scanned {len(present)} tracked files, floor {min_files} (266 tracked at 7ba89ca5c; lower this only if the corpus really shrank)"`.

- A1 `test_sdlc_review_finalize.py:1093` — route through the helper over `'tools/*.py'` and `'agent/*.py'`, `min_files=170`. Add the `#2093`-style comment naming the `.pyc` hazard.
- A2 `test_anthropic_client_semaphore.py:164` — route through the helper using **negative pathspecs, not `allow=`**. Every member of `_ALLOWED_DIRECT_CONSTRUCTORS` is a file path, so the existing `:180-181` line filter is whole-file exemption expressed unsafely (it substring-matches the whole `path:lineno:content` line, so a comment merely mentioning an approved module exempts a real violation). Keep `_ALLOWED_DIRECT_CONSTRUCTORS` as a module constant feeding the exclusion list so the six approved modules keep their `#1055`/`#1193`/`#1262`/`#1925` comments:
  ```python
  assert_absent_from_tracked(
      "anthropic.AsyncAnthropic(",
      "agent/*.py", "bridge/*.py", "tools/*.py",
      ":!agent/anthropic_client.py",
      ":!agent/memory_extraction.py",
      ":!agent/session_completion.py",
      ":!bridge/read_the_room.py",
      ":!bridge/promise_gate.py",
      ":!agent/llm/wrapper.py",
      min_files=200,
  )
  ```
  Note in the comment that this site was **vacuously green**, not merely unpinned.
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
- **Take root and floor as independent parameters** so the floor and the positive control do not collide: `def _scan_for_bare_grep(root: Path, *, min_files: int) -> list[str]`, raising `AssertionError` when `len(scanned) < min_files`. The real guard calls `_scan_for_bare_grep(TESTS_DIR, min_files=500)`; the control calls `_scan_for_bare_grep(tmp_path, min_files=1)`. A floor baked into the scanner would be tripped by the control's one-file root *before* the offender assertion ran, so the control would pass for the wrong reason and V-21 would be uninformative.
- `min_files=500` because `tests/` holds 784 tracked `.py` at `7ba89ca5c` (63.8%, inside Risk 1's 60-70% band). Not 600, which is 76.5% and outside it.
- Never let the control pass `min_files=0` — a scanner returning `[]` unconditionally would satisfy both callers and both would stay green.
- Name the control test so `-k "positive_control"` selects it (V-21).

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
- **Absent-file mutation (net-new, round 2).** Move one tracked file out of A1's corpus with a plain `mv` — *not* `git mv`, which updates the index and so exercises only the case the index-row count already catches — then run A1 and confirm it raises `VacuousScanError` naming the absent path. Measured at `7ba89ca5c`: with `tools/doctor.py` moved aside, `git grep` returns rc=1 (false clean) while `git ls-files` still reports 180, and the present count is 265 of 266 — above the floor of 170, so **only** the absence check fires. Restore with `mv` and confirm `git status` is clean. This is V-18b.
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
- Create `docs/features/tracked-content-sweep-guards.md` per the Documentation section, including the BRE dialect rule and the tracked-files boundary paragraph (why `--untracked` is deliberately not used).
- **State the two-part `git grep` contract correctly — this doc is where it becomes repo-wide doctrine.** `git grep` resolves the *file list* from the index and reads each file's *content from the working tree*; `--cached` would read content from the index instead. Do not write "the corpus is index-scoped content", and do not copy forward `test_plan_migration_invariant.py:136-145`'s stronger claim that `git grep` "is race-free" — the *file list* cannot shift mid-scan and no untracked runtime tree is descended, but content is still read per-file from the working tree. Measured at `7ba89ca5c`: an unstaged append is found by `git grep` (rc=0) and not by `git grep --cached` (rc=1).
- Say plainly that a violation in an already-tracked file is caught immediately, staged or not, and that only a brand-new *untracked* file is invisible — until it is added to the index, not until it is committed.
- Document that the non-vacuity floor counts files present on disk and that any absent tracked path is an error, with the 265-of-266 measurement as the worked example.
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
| net-new | Floor counts readable files; any absent tracked path is an error | 1, 5 | V-18b |
| net-new | Per-root assertion on B13/B14 | 3 | V-18 |
| net-new | Regex dialect pinned to BRE | 1 | V-9, V-12 |
| net-new | Meta-guard self-exempts and is positively controlled | 4 | V-21 |
| net-new | Meta-guard root and floor are independent parameters | 4 | V-5, V-21 |
| net-new | No converted guard passes `allow=`; the parameter is not built | 1, 2 | V-10 |

## Verification

Rows are numbered for the traceability table above. Every row is runnable as
written from the repo root unless a row states otherwise.

Three conventions make that promise hold, each measured at `7ba89ca5c`:

- **Single-node pytest rows pass `-n 0`.** `pyproject.toml`'s `addopts` carry
  `-n auto`, which spawns ~50 xdist workers for a one-file run; observed here as
  a 168-second run ending in `node down: Not properly terminated` and "no tests
  ran". `-n 0` is `scripts/pytest-clean.sh`'s own documented remedy for running a
  single node.
- **A `-k` row cannot pass vacuously.** pytest 9.0.3 exits **5**
  (`NO_TESTS_COLLECTED`) when `-k` deselects everything, and 0 only when at least
  one selected test passed. So "expect exit 0" already forecloses a `-k` typo;
  no separate selected-count assertion is needed. Task 1 pins the test names the
  `-k` expressions rely on (`scan_error`, `vacuous`, `positive_control`).
- **No row's command contains an unescaped `|`.** A `|` inside a table cell must
  be written `\|`, which *renders* as a bare `|` — so a BRE alternation copied
  from the rendered page silently becomes a literal pipe and matches nothing.
  V-22 therefore uses repeated `-e` patterns instead of alternation. (Verified:
  the alternation form returns count 0 where the `-e` form returns 13.)
- **A row that greps a file this plan creates gates on the file being *tracked*,
  not merely present.** `git grep` searches the index's file list, so a helper
  written to disk but not yet `git add`ed is invisible to it and a bare
  `test -f` gate lets the row pass while checking nothing. Measured: with
  `tests/tracked_content.py` untracked and containing `"-F"`, the `test -f` form
  returned **exit 0 (false pass)**; gated on
  `git ls-files --error-unmatch` it correctly fails. V-9 uses the tracked gate.
  This is the plan's own central defect, reproduced inside its verification
  table — the same shape spike-1 found and the same one V-18b exists to catch.

| # | Check | Command | Expected |
|---|-------|---------|----------|
| V-1 | Primary node passes with stale `.pyc` present | `./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q` | exit code 0 |
| V-2 | Stale off-pin `.pyc` was NOT deleted (AC1 precondition still holds) | `test -n "$(find tools agent -name '*.pyc' -not -name '*cpython-314*' -print -quit)"` | exit code 0 |
| V-3 | Tracked source is genuinely clean | `git grep -n "State NOT persisted" -- 'tools/*.py' 'agent/*.py'; test $? -eq 1` | exit code 0 |
| V-4 | No converted guard shells out to `grep` | `git grep -n '"grep"' -- 'tests/unit/test_sdlc_review_finalize.py' 'tests/unit/test_anthropic_client_semaphore.py' 'tests/unit/test_memory_extraction.py' 'tests/unit/test_no_legacy_paths.py'` | exit code 1 |
| V-5 | No bare recursive grep anywhere in `tests/` (meta-guard node) | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q` | exit code 0 |
| V-6 | Each converted guard carries the `.pyc`-hazard comment (#2809 AC2) | `for f in tests/unit/test_sdlc_review_finalize.py tests/unit/test_anthropic_client_semaphore.py tests/unit/test_memory_extraction.py tests/unit/test_no_legacy_paths.py; do git grep -q '2093\|pyc' -- "$f" \|\| { echo "MISSING: $f"; exit 1; }; done` | exit code 0, no output |
| V-7 | Helper raises `TrackedScanError` (**not** `VacuousScanError`) on a broken index | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q -n 0 -k "scan_error"` | exit code 0 |
| V-8 | Helper raises `VacuousScanError` (**not** `TrackedScanError`) on a vacuous pathspec | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q -n 0 -k "vacuous"` | exit code 0 |
| V-9 | No regex-dialect flag was added (an `-F` would leave A3 permanently green) | `git ls-files --error-unmatch tests/tracked_content.py >/dev/null 2>&1 && test -f tests/tracked_content.py && { git grep -nE '"-[FEP]"' -- tests/tracked_content.py; test $? -eq 1; }` | exit code 0 |
| V-10 | All four converted guards pass | `./scripts/pytest-clean.sh tests/unit/test_sdlc_review_finalize.py tests/unit/test_anthropic_client_semaphore.py tests/unit/test_memory_extraction.py tests/unit/test_no_legacy_paths.py -q` | exit code 0 |
| V-11 | All six floored walks pass | `./scripts/pytest-clean.sh tests/unit/test_template_filter_registry.py tests/unit/test_sdlc_lease_helper_binding.py tests/unit/test_sdlc_tool_wrapper.py tests/unit/test_no_positional_query_get.py tests/unit/test_harness_model_coverage.py tests/integration/test_dm_recovery.py -q` | exit code 0 |
| V-12 | The BRE metacharacter path is exercised, not assumed | `git grep -q 'zzz\\.never(' -- tests/unit/test_tracked_content_helper.py` | exit code 0 (exit 1 means the probe is missing or mis-escaped) |
| V-13 | Helper exists and is index-scoped | `git grep -c 'git.*grep' -- tests/tracked_content.py` | output > 0 |
| V-14 | Stranded-bytecode warning shipped, linking #2883 | `git grep -c '2883' -- docs/features/worktree-venv-isolation.md` | output > 0 |
| V-15 | Concern-B follow-up issue is real and open | `gh issue view 2883 --json state -q .state` | output contains OPEN |
| V-16 | **Demonstrated ignored-artifact.** Planted `__pycache__` file is not read (#2808 AC3, #2809 AC3) | `mkdir -p tools/__pycache__ && printf 'State NOT persisted\n' > tools/__pycache__/zz_probe.cpython-312.pyc && ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q; rc=$?; rm -f tools/__pycache__/zz_probe.cpython-312.pyc; exit $rc` | exit code 0 |
| V-17 | **Demonstrated red, per guard, one at a time** (#2807 AC3, #2808 AC4). Repeat for each of the four converted guards against a file *that guard scans*; a single mutation tripping several proves nothing about the ones it did not reach. Shown for A1 | `printf '\n# State NOT persisted\n' >> tools/sdlc_stage_marker.py && ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q; rc=$?; git checkout -- tools/sdlc_stage_marker.py; test $rc -ne 0` | exit code 0, and FAIL output names `tools/sdlc_stage_marker.py:<line>` with no `Binary file` |
| V-18 | **Per-root assertion fires on a vanished root** where the total floor stays green (B13) | `git mv worker worker_zz && ./scripts/pytest-clean.sh tests/unit/test_no_positional_query_get.py -q -n 0; rc=$?; git mv worker_zz worker; test $rc -ne 0` | exit code 0, FAIL message names the missing root |
| V-18b | **Absence check fires when a tracked file leaves the working tree but stays in the index** — the case `git mv` cannot reach | `mv tools/doctor.py /tmp/zz_doc.py && ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q -n 0; rc=$?; mv /tmp/zz_doc.py tools/doctor.py; test $rc -ne 0` | exit code 0, FAIL is a `VacuousScanError` naming `tools/doctor.py` |
| V-19 | **Fresh worktree agrees with the primary checkout** at the same commit (#2808 AC2) | `git worktree add /tmp/zz-verify HEAD && (cd /tmp/zz-verify && PYTHONPATH=/tmp/zz-verify ./scripts/pytest-clean.sh "tests/unit/test_sdlc_review_finalize.py::test_no_module_in_tools_or_agent_claims_state_not_persisted" -q); rc=$?; git worktree remove --force /tmp/zz-verify; exit $rc` | exit code 0, matching V-1 |
| V-20 | **Foreign ambient cwd yields the same verdict** for both converted siblings (#2808 AC7) | `cd /tmp && PYTHONPATH=$HOME/src/ai $HOME/src/ai/scripts/pytest-clean.sh "$HOME/src/ai/tests/unit/test_anthropic_client_semaphore.py" "$HOME/src/ai/tests/unit/test_memory_extraction.py::TestEventLoopSafety::test_no_direct_anthropic_client_grep_canary" -q` | exit code 0, identical to the in-repo run |
| V-21 | **Meta-guard positive control:** a planted offender under `tmp_path` is flagged | `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q -n 0 -k "positive_control"` | exit code 0 |
| V-22 | Sweep table has exactly 13 disposition rows covering all 27 sites (#2809 AC4) | `test "$(awk '/^\| # \| Site/,/^$/' docs/plans/overclaim-guard-greps-whole-worktree.md \| grep -c -e CONVERT -e 'ADD FLOOR' -e 'DOCUMENT AS SAFE')" -eq 13` | exit code 0 |
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

War room 2026-08-19 (round 2), FULL depth (Risk & Robustness, Scope & Value, History & Consistency).
Verdict: **READY TO BUILD (with concerns)** — 0 blockers, 7 concerns, 2 nits.
All round-1 findings were re-verified as landed; every measured figure the round-1
revision introduced (2060 / 2654 / 1363 / 591 / 1231 and the per-root distribution)
reproduces exactly in the primary checkout. This round's findings are net-new.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | The non-vacuity floor counts *index entries* (`git ls-files`) while `git grep` reads *working-tree content* for those paths, so the counted corpus and the scanned corpus never reconcile. A working tree missing tracked files (plain `mv`/`rm`, interrupted checkout, sparse checkout) leaves the index count intact while `git grep` silently skips the absent files and returns 1, i.e. clean. Measured at `2bceee24b`: with `tools/doctor.py` moved aside, `git grep -l "worktree_interpreters" -- 'tools/*.py'` went rc=0 to **rc=1 (false clean)** while `git ls-files -- 'tools/*.py'` still reported **180**. The floor is blind to it, so the vacuous-green class survives inside the plan's flagship mitigation. | pending | Count files that can actually be read, not index rows. After the `git ls-files` returncode triage in task 1: `present = [p for p in files if (REPO_ROOT / p).exists()]` then `if len(present) < min_files: raise VacuousScanError(f"{len(present)} of {len(files)} tracked paths present on disk; absent: {sorted(set(files) - set(present))[:5]}")`. Do NOT switch to `git grep --cached` as the fix without also changing V-17: `--cached` reads content from the index, so V-17's unstaged `printf ... >> tools/sdlc_stage_marker.py` would no longer be seen and the demonstrated-red proof would go green. Add a second floor mutation beside V-18 using plain `mv worker worker_zz` rather than `git mv` — `git mv` updates the index, so V-18 exercises only the case the floor already catches. |
| CONCERN | Risk & Robustness | The plan states an incorrect model of `git grep` in four places (Race 1, Data Flow step 2, obligation 1 "Index-scoped corpus", the Accepted-boundary paragraph) and then specifies that model be written into the helper docstring (task 1) and a new repo-wide convention doc (task 7), so the error ships as taught doctrine. `git grep` without `--cached` resolves the *file list* from the index but reads *content from the working tree*. Measured at `2bceee24b`: an unstaged append to `tools/doctor.py` was found by `git grep` (rc=0) and NOT by `git grep --cached` (rc=1). Race 1's "the corpus cannot shift underneath it" is therefore half true, and "A violation in a new, unstaged file is caught on first commit" is wrong for a modified *tracked* file, which is caught immediately. | pending | State the two-part contract once, in the docstring and the feature doc: "`git grep` resolves the file list from the index and reads each file's content from the working tree; `--cached` would read content from the index instead. This helper deliberately uses the working-tree form so an uncommitted reintroduction is caught before it is staged." Narrow the boundary claim to: "A violation in a brand-new *untracked* file is invisible until it is added to the index; a violation in an already-tracked file is caught immediately, staged or not." Correct Race 1 to "the *file list* cannot shift underneath it, and no untracked runtime tree is ever descended" — the content race is narrowed, not eliminated. |
| CONCERN | Risk & Robustness | Task 4 requires two colliding things of the meta-guard: "Include a non-vacuity floor on the meta-guard's own scan" and a positive control that plants one offender under `tmp_path` and points the scanner at it. If the floor lives inside the scanner, the control's one-file root trips the floor instead of reaching the offender assertion, so the control passes for the wrong reason — exactly the failure it was added to rule out, leaving V-21 uninformative. | pending | Parameterize root and floor independently: `def _scan_for_bare_grep(root: Path, *, min_files: int) -> list[str]`, raising `AssertionError` when `len(scanned) < min_files`. The real guard calls `_scan_for_bare_grep(TESTS_DIR, min_files=600)` (784 tracked `.py` under `tests/` at `40c4fe0a9`, so 600 sits in the plan's own 60-70% band); the control calls `_scan_for_bare_grep(tmp_path, min_files=1)` and asserts the planted path is in the returned list. Never let the control pass a floor of 0 — a scanner returning `[]` unconditionally would then satisfy both callers. |
| CONCERN | Scope & Value | A2 is not a line-substring exemption, so `allow=` is left with zero real call sites while still carrying the hazard the plan condemned for A7. Every member of `_ALLOWED_DIRECT_CONSTRUCTORS` at `tests/unit/test_anthropic_client_semaphore.py:149-158` is a **file path**, and the filter at `:180-181` (`not any(mod in line for mod in ...)`) drops whole output lines by path substring — whole-file exemption, identical in semantics to a negative pathspec. A line in an unapproved module that merely mentions `agent/llm/wrapper.py` in a comment while calling `anthropic.AsyncAnthropic(` is silently exempted, because the substring is matched against the whole `path:lineno:content` line rather than the path field. | pending | A2 becomes `assert_absent_from_tracked("anthropic.AsyncAnthropic(", "agent/*.py", "bridge/*.py", "tools/*.py", ":!agent/anthropic_client.py", ":!agent/memory_extraction.py", ":!agent/session_completion.py", ":!bridge/read_the_room.py", ":!bridge/promise_gate.py", ":!agent/llm/wrapper.py", min_files=200)`. No converted guard then passes `allow=`, so drop it from the signature rather than shipping an abstraction with no consumer. If it is kept, the docstring must read "currently unused; path-shaped exemptions belong in negative pathspecs" — the planned sentence reserving it for A2 is the one thing that would send a builder back to substring filtering. Keep `_ALLOWED_DIRECT_CONSTRUCTORS` as a module constant feeding the pathspec list so the six approved modules keep their #1055/#1193/#1262/#1925 comments. |
| CONCERN | Scope & Value | `min_files` is a required keyword-only argument, but task 2 pins a value for only two of the four conversions: A3 gets `min_files=1` and A7 `min_files=1300`, while A1 and A2 get none. The builder must invent two floors for the one number this plan has already gotten wrong once (round 1 said 1800, the body corrects to 1300) and the one number Risk 1's whole mitigation is about choosing deliberately. Leaving two of four unspecified invites the `current - 1` floors Risk 1 forbids. | pending | Measured at `2bceee24b`: `git ls-files -- 'tools/*.py' 'agent/*.py' \| wc -l` is **266** and `git ls-files -- 'agent/*.py' 'bridge/*.py' 'tools/*.py' \| wc -l` is **310**. At the plan's own 60-70% band that gives A1 `min_files=170` (64%) and A2 `min_files=200` (65%). Put the measured count and its commit in the assertion message, not a comment, per Risk 1: `f"scanned {len(files)} tracked files, floor {min_files} (266 tracked at 2bceee24b; lower this only if the corpus really shrank)"`. |
| CONCERN | History & Consistency | The Verification preamble promises "Every row is runnable as written from the repo root unless a row states otherwise", and V-7 and V-8 are not runnable. Both embed a `try:` compound statement after a `;` inside `python -c`, which Python rejects (verified: `SyntaxError: invalid syntax` at `try`). Both are additionally split into four backtick-quoted fragments inside the table cell (the V-7 row carries 12 backticks), so there is no single command to copy. These are the only runnable evidence for the round-1 flagship fix, for the Success Criterion "Scan-failure and vacuity are separately assertable", and for #2808 AC5 via the traceability table. | pending | V-7 becomes `./scripts/pytest-clean.sh tests/unit/test_tracked_content_helper.py -q -k "scan_error"` and V-8 `... -k "vacuous"`, each expecting exit 0 **and at least one test selected**, so a `-k` typo that selects nothing cannot pass. If a standalone one-liner is wanted, a `;`-joined `try:` never parses — use a `python - <<'PY'` heredoc instead. `tests/__init__.py` already exists, so `from tests.tracked_content import ...` resolves from the repo root. |
| CONCERN | History & Consistency | V-12 cannot pass as written, and it is the sole verification row behind the "regex dialect is pinned and exercised" criterion. The cell carries four literal backslashes in `zzz\\\\.never(`, which in POSIX BRE matches a backslash followed by any character, not the intended `zzz\.never(`. Verified against a file containing `x = "zzz\.never("`: the four-backslash pattern returned count 0 / rc 1; the two-backslash pattern returned count 1 / rc 0. The row therefore reports failure however correctly the helper is implemented, leaving the criterion that catches a builder adding `-F` unverified. | pending | Correct the cell to `git grep -c 'zzz\\.never(' -- tests/unit/test_tracked_content_helper.py` (two backslashes), Expected "exit code 0 and a count of at least 1". `git grep -c` prints nothing and exits 1 on no match, so an Expected of "output > 0" alone is ambiguous with a broken pathspec. The same double-escaping trap applies to the planted-match half of the test, so state in task 1 that the test file must carry the pattern as the two-character sequence backslash-dot, written in a normal (non-raw) Python string as `"zzz\\.never("`. |
| NIT | History & Consistency | Two verification rows cannot fail. V-22's awk-plus-grep pipeline returns **13**, the sweep table's row count, not 27, because three rows cover multiple sites ("A4, A5" and "B1-B6, B9, B10, B15-B20"); its Expected cell reads "rows account for all 27 sites", which no output can confirm or refute. V-9 (`git grep -nE ... -- tests/tracked_content.py`, Expected exit 1) returns exit 1 **today**, before any work exists, because a pathspec matching no tracked file also exits 1 — the "cannot distinguish clean from scanned nothing" hazard from spike-1, reproduced in the plan's own verification table. | pending | Give V-22 a numeric expectation it can miss: wrap the existing pipeline in `test "$(...)" -eq 13`, Expected exit 0, and assert the 4/6/17 arithmetic separately in prose since it is not derivable from row count. Gate V-9 on the file existing: `test -f tests/tracked_content.py && { <existing git grep>; test $? -eq 1; }`, so a missing helper fails the row instead of satisfying it. |
| NIT | Structural check | The round-1 Critique Results table's Implementation Note column still carries the superseded `min_files=1800` in two rows (the `allow=` row's A7 call, and the obligation-1 row's "about 1800"), which those same rows' Addressed By cells correct to 1300 and which task 2's code block pins at 1300. Every other structural check passes: all four repo-mandated sections present and substantive, task numbering 1-8 contiguous, all `Depends On` references resolve with no cycles, all three prerequisites currently green (including the stale `tools/__pycache__/sdlc_stage_marker.cpython-312.pyc` that AC1 depends on), and the only absent file paths are the three this plan creates. | pending | This round's table replaces the round-1 table wholesale, which removes the stale 1800 automatically. If any round-1 row is carried forward by hand, restate its Implementation Note with 1300 so the note column never contradicts task 2. |
---

## Resolved Questions

All three open questions are closed by the critique pass. Recorded here rather
than deleted, because each records a decision a reviewer may want to challenge.

1. **Meta-guard strictness — RESOLVED: shape-matching, self-exempted by resolved
   path, with a planted-offender positive control.** A blanket ban on every
   `subprocess` call to `grep` from `tests/` would force A4 and A5 (single
   absolute file paths, genuinely safe) through the helper for no correctness
   gain. The evasion worry the question raised is real but is answered by the
   positive control rather than by a broader ban: the control proves the matcher
   catches the shape it claims to. The opt-out comment survives for a justified
   survivor **elsewhere** in the suite, and explicitly is not the mechanism the
   meta-guard uses on itself. See Technical Approach → "The meta-guard flags
   itself unless it self-exempts by resolved path".

2. **Non-vacuity floor placement — RESOLVED: hardcoded integers at each call
   site.** A central table in `tests/tracked_content.py` would couple ten
   unrelated tests to one file, and the failure it optimizes for (a fleet-wide
   refactor changing every count at once) is rarer than the failure it creates
   (a reader of one test cannot see its own floor). The floor's rationale goes
   in the assertion message, so the person who trips it sees the reason at the
   moment they see the failure — which is the actual mitigation for Risk 1, and
   it works the same either way.

3. **Scope of the six `rglob` floors — RESOLVED: task 3 stays in scope.** The
   strict reading of #2809 AC4 would permit documenting them as safe and halving
   the diff, but it is the wrong reading: a guard that can silently scan nothing
   is the same defect class as one that scans the wrong corpus, and B13/B14 have
   *explicit* vacuous-green code paths, not merely a theoretical exposure. Six
   of the ten touched files live here, so dropping the task would ship 60% of
   the surface with nothing to check it against. The criterion that closes this
   is now in Success Criteria ("The six floored walks are covered"), and the AC4
   criterion is amended to the three-way disposition 4 / 6 / 17 so it asserts the
   sweep table's own arithmetic.
