---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-17
tracking: https://github.com/tomcounsell/ai/issues/2133
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-17T11:32:08Z
---

# validate_docs_changed.py stale-marker scan: exit-code and scope fix

## Problem

`scripts/validate_docs_changed.py` is Gate 2 of the documentation lifecycle
(invoked at build completion, `do-build.md` Step 6). Its Phase 2 "stale marker
scan" diverges from its own documented contract in two ways that produce
false-positive PR blocks.

**Current behavior:**
1. **Exit-code mismatch.** `main()` returns exit `1` for BOTH "no expected docs
   changed" (missing docs) AND "deprecated markers found" (stale markers). The
   documented contract (`docs/features/documentation-lifecycle.md:43`) says:
   `0` = pass, `1` = missing docs (hard fail), `2` = stale markers (warning).
   Stale markers should be a non-blocking exit `2`, not a hard exit `1`.
2. **Scan-scope mismatch.** `check_deprecated_markers()` reads the ENTIRE content
   of every matched doc file and flags any line containing a trigger word. When a
   plan lists a large, pre-existing doc as a target (e.g. `CLAUDE.md`,
   `docs/features/tools-reference.md`), the scan sweeps thousands of pre-existing
   lines the PR never touched. Issue #2133 documented four concrete false
   positives — all four lines exist verbatim on `main` and none were added by the
   PR (verified by diff-scoped grep):
   - `CLAUDE.md:262-263` — the "NO LEGACY CODE TOLERANCE" principle text
   - `CLAUDE.md:612` — "Do NOT use a `feature` label"
   - `docs/features/tools-reference.md:371` — historical "legacy … retired in #1256" note
3. **Exit-code collision.** The current `main()` uses exit `2` for "file/command
   error" (plan file not found) — which collides with the documented meaning of
   exit `2` (stale-marker warning). A missing plan file must NOT be silently
   treated as a mere warning.

**Desired outcome:**
- Stale markers → exit `2` (non-blocking warning), per the documented contract.
- Missing docs → exit `1` (hard fail, blocks PR), reserved for this case only.
- Internal/usage errors (plan not found, read failure) → exit `3` (distinct from
  the warning code) so a real error is never confused with a stale-marker warning.
  Exit `3` **extends** the documented 0/1/2 contract by splitting the previously
  overloaded exit `2` (which the old docstring used for BOTH "file/command error"
  AND — per `documentation-lifecycle.md` — "stale markers"); exit `2` now means
  ONLY the non-blocking stale warning.
- The stale-marker scan examines ONLY lines ADDED by this branch
  (`git diff {base}...HEAD -U0` `+` lines), never pre-existing file content.
- Docs (`documentation-lifecycle.md`, `do-build.md`) reflect the reconciled
  contract, including the new error code.

## Freshness Check

**Baseline commit:** 85d5f0432f2f6029677a986b8bbb3b8009cdd24e
**Issue filed at:** 2026-07-17T06:06:16Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/validate_docs_changed.py` — `main()` returns `1` for missing docs AND
  deprecated markers (lines 268-290 return `False` → `main` returns 1 at 342);
  `check_deprecated_markers()` scans full file content (lines 183-217); exit `2`
  used for plan-not-found (line 328) — all still hold.
- `docs/features/documentation-lifecycle.md:43` — "0 = pass, 1 = missing docs
  (hard fail), 2 = stale markers found (warning)" — still present, verbatim.
- `docs/sdlc/do-build.md:104` — "exit 1 BLOCKS PR" — still present, verbatim.

**Cited sibling issues/PRs re-checked:** Issue references #2114/PR #2132 (the
build during which this was found) — merged; not a blocker for this fix.

**Commits on main since issue was filed (touching referenced files):** None. The
three affected files have no commits since `2026-07-17T06:06:16Z`.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** Recon Summary section absent from the issue body, but every claim is
backed by a concrete file:line pointer that was re-verified by hand against the
current code — evidence requirement satisfied manually.

## Prior Art

No prior issues or PRs attempted to fix this validator's exit-code or scan-scope
behavior. Related context only:
- **PR #2132** (#2114 build) — surfaced the false positives; a doc note in
  `docs/plans/completed/merge-gate-baseline-refresh.md:364` already documents the
  full-file-scan false-positive as a known workaround ("applied outside the
  plan's Documentation contract"). This fix removes the need for that workaround.

## Data Flow

1. **Entry point**: `do-build` Step 6 runs `python scripts/validate_docs_changed.py {PLAN_PATH}`.
2. **`extract_doc_paths(plan_text)`**: parses the plan's `## Documentation`
   section → list of expected `.md` paths.
3. **`get_changed_files(base)`** → `changed_docs` (the `.md` subset).
4. **Phase 1** matches expected vs. changed. No match → missing docs → exit `1`.
5. **Phase 2** `check_deprecated_markers(matched)` — TODAY reads full file
   content; AFTER this fix reads only diff-added lines from
   `git diff {base}...HEAD -U0 -- <file>`. Violations → exit `2` (warning).
6. **Output**: `main()` maps the outcome to the reconciled exit code and prints
   pass/warning/fail messaging.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses only `git`, the stdlib, and pytest already present.

## Solution

### Key Elements

- **Exit-code reconciliation** (`main()`): distinguish three outcomes —
  pass (`0`), missing-docs hard fail (`1`), stale-marker warning (`2`) — and move
  internal/usage errors to a distinct code (`3`).
- **Diff-scoped stale scan** (`check_deprecated_markers` + new helper): a helper
  `get_added_lines(doc_path, base_branch)` returns the `+` lines introduced by
  `git diff {base}...HEAD -U0 -- <doc_path>` with their new-file line numbers.
  For untracked/brand-new docs (not in the diff), all lines count as added.
  The marker scan runs over ONLY those added lines, preserving the existing
  code-fence / heading / inline-code skip logic.
- **Structured result** (`validate_docs_changed`): return an outcome the caller
  can map to the right exit code (e.g. a small enum/string:
  `"pass" | "missing_docs" | "stale_markers"`), instead of a bare
  `(success, message)` that erases the missing-vs-stale distinction.

### Flow

`do-build` Step 6 → run validator → validator scans only added lines of matched
docs → outcome maps to exit code (0 pass / 1 missing / 2 stale-warning / 3 error)
→ do-build treats exit 1 as a PR blocker, exit 2 as a non-blocking warning it
surfaces in the build report.

### Technical Approach

- Introduce an outcome discriminator so `main()` can pick the exit code. Keep the
  human-readable message.
- Add `get_added_lines(doc_path, base_branch) -> list[tuple[int, str]]`:
  - Run `git diff --unified=0 {base}...HEAD -- {doc_path}` (three-dot: changes on
    HEAD since branch point — matches the "added by this PR" intent and the
    `do-build` `main...HEAD` convention on line 105).
  - Parse hunk headers with `@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@` — git OMITS
    the `,d` count when it equals 1, so single-line additions emit `@@ -a +c @@`
    and a brand-new single-line doc emits `@@ -0,0 +1 @@`. When the count group is
    None, default `d = 1`. Track new-file line numbers from `c`; collect `+`-body
    lines (excluding the `+++` file header). **(concern #2)**
  - If the diff is empty, before concluding "no added lines" check whether the doc
    is a NEW file that this branch is adding but that three-dot diff misses:
    - Untracked (`git ls-files --error-unmatch <doc>` fails) → treat every line as
      added (brand-new doc).
    - Staged-but-uncommitted addition (`git diff --cached --name-only -- <doc>`
      lists it) → also treat every line as added, so a staged new doc is scanned.
      **(concern #3 — bounded in the normal do-build flow where docs are committed
      before Gate 2, but handled for correctness.)**
- `check_deprecated_markers(matched, base_branch)` iterates matched docs, pulls
  added lines via the helper, and applies the existing skip rules (code fences,
  headings, inline-code stripping) to those lines only. Line numbers reported are
  the true new-file line numbers.
- `main()`:
  - plan not found / read error → exit `3` (was `2`/`1`).
  - missing docs → exit `1`.
  - stale markers → print warning to stderr, exit `2`.
  - pass → exit `0`.
- Update the module docstring exit-code table to match.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `get_changed_files` already swallows `subprocess` errors returning `[]`; the
  new `get_added_lines` helper follows the same pattern (git failure → treat as no
  added lines / fall back). Test asserts a git failure does not crash and yields a
  defined result.
- [ ] No new bare `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] Doc with zero added lines (only deletions or unchanged) → scan finds no
  violations even if the file body contains trigger words. Covered by a test.
- [ ] Untracked brand-new doc → all lines treated as added; a stale marker in it
  IS flagged. Covered by a test.

### Error State Rendering
- [ ] Missing plan file → exit `3` with an error message on stderr (test asserts
  exit code and message channel).
- [ ] Stale marker → warning message on stderr, exit `2` (non-blocking). Test
  asserts the message makes the "warning, not hard fail" distinction visible.

## Test Impact

No existing tests affected — a repo search (`find tests -name '*validate_docs*'`,
`grep -rn validate_docs_changed tests/`) returns no test file for this script, so
this is net-new test coverage. New tests will live in
`tests/unit/test_validate_docs_changed.py`.

## Rabbit Holes

- Do NOT rework `extract_doc_paths` parsing or Phase 1 matching — the issue is
  scoped to the Phase 2 scan and exit-code mapping only.
- Do NOT try to semantically classify stale markers (the Limitations section of
  the feature doc already accepts "simple string matching"). Scope stays: which
  LINES are scanned, and which EXIT CODE results.
- Do NOT change how `do-build` orchestrates the call beyond the doc note; the
  build skill already tolerates non-1 exit codes for other validators.

## Risks

### Risk 1: Three-dot vs two-dot diff base semantics
**Impact:** `get_changed_files` uses two-dot (`git diff {base} HEAD`) while the new
helper uses three-dot (`{base}...HEAD`). A file could appear in Phase 1's matched
set but have no three-dot added lines (e.g. base advanced past the change).
**Mitigation:** Empty added-lines → zero violations (correct: nothing this branch
added is stale). The untracked-file fallback covers brand-new docs. Both paths are
explicitly tested.

### Risk 2: do-build consumers assuming exit 2 == error
**Impact:** If any caller currently treats exit `2` as a fatal error, the new
warning semantics could change behavior.
**Mitigation:** Only `docs/sdlc/do-build.md:104` references this script's exit
codes, and it only calls out "exit 1 BLOCKS PR". No code path branches on exit `2`
today. Doc is updated to state 1 = block, 2 = warning, 3 = error.

## Race Conditions

No race conditions identified — the validator is a synchronous, single-threaded
CLI that reads git state and files once per invocation.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — `scripts/validate_docs_changed.py` is invoked
directly by the `do-build` skill; it is not propagated by `/update` and has no new
dependencies or config.

## Agent Integration

No agent integration required — this script is a build-pipeline validator invoked
by the `do-build` skill via Bash, not an agent-facing tool. No MCP surface, no
`.mcp.json` change, no bridge import.

## Documentation

- [ ] Update `docs/features/documentation-lifecycle.md` — Gate 2 description
  (line 41-43) and the Troubleshooting table (lines 115-116) to state the
  reconciled contract: exit `1` = missing docs (hard fail, BLOCKS), exit `2` =
  stale markers (non-blocking warning, diff-scoped — the ONLY non-blocking code),
  exit `3` = file/command error (BLOCKS); and that the stale scan examines only
  diff-added lines. Mirror the same blocking/non-blocking annotation used in
  do-build.md so all surfaces agree exit 2 is the sole non-blocking code.
  **(concern #1)**
- [ ] Update `docs/sdlc/do-build.md:104` — change "exit 1 BLOCKS PR" to spell out
  the per-code blocking semantics explicitly, e.g. `exit 1 (missing docs) or
  exit 3 (file/command error) BLOCKS PR; exit 2 (stale markers) = non-blocking
  warning, proceed`. The comment is LLM-read (no shell branches on `$?`), so it
  MUST state that exit 3 blocks — otherwise a genuine error reads as
  non-blocking, strictly worse than today. **(concern #1)**
- [ ] Update the module docstring in `scripts/validate_docs_changed.py` exit-code
  table to match.

## Success Criteria

- [ ] Stale marker in an added line → validator exits `2` (not `1`).
- [ ] Missing expected docs → validator exits `1`.
- [ ] Missing plan file → validator exits `3` (not `2`).
- [ ] A trigger word that exists only in PRE-EXISTING (unchanged) file content is
  NOT flagged — validator exits `0`.
- [ ] A trigger word in a brand-new untracked doc IS flagged (exit `2`).
- [ ] New test file `tests/unit/test_validate_docs_changed.py` passes.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `python -m ruff check scripts/validate_docs_changed.py tests/unit/test_validate_docs_changed.py` clean

## Team Orchestration

Solo dev executes directly in the session worktree. No multi-agent fan-out needed
for a single-file fix plus its test module.

## Step by Step Tasks

### 1. Write failing tests (TDD red)
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: tests/unit/test_validate_docs_changed.py (create)
- **Assigned To**: solo dev
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_validate_docs_changed.py` with a git-repo fixture
  (tmp_path init, commit a base doc, branch, add lines).
- Assert the DOCUMENTED contract: exit `2` for stale markers, exit `1` for missing
  docs, exit `3` for missing plan file, exit `0` when trigger words exist only in
  pre-existing content, exit `2` for a stale marker in a new untracked doc.
- **(concern #4)** Add a real-world regression case: a doc whose pre-existing
  (non-added) content contains a real flagged phrase from the issue (e.g.
  CLAUDE.md's "NO LEGACY CODE TOLERANCE") plus one unrelated ADDED line → assert
  exit `0`. Reference `docs/plans/completed/merge-gate-baseline-refresh.md:364` as
  the workaround this removes.
- **(concern #2)** Add a single-line-addition case whose diff hunk header is
  `@@ -0,0 +1 @@` (omitted `,d` count) to prove the parser catches single-line
  additions.
- Run and confirm they FAIL against current code (red).

### 2. Fix the validator (TDD green)
- **Task ID**: build-fix
- **Depends On**: build-tests
- **Validates**: tests/unit/test_validate_docs_changed.py
- **Assigned To**: solo dev
- **Agent Type**: builder
- **Parallel**: false
- Add `get_added_lines(doc_path, base_branch)` helper (diff parse + untracked
  fallback).
- Rework `check_deprecated_markers` to scan added lines only.
- Rework `validate_docs_changed` to return a discriminated outcome and `main()` to
  map outcomes to exit codes 0/1/2/3.
- Update the module docstring exit-code table.
- Run tests → green.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-fix
- **Assigned To**: solo dev
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/documentation-lifecycle.md` (Gate 2 + Troubleshooting).
- Update `docs/sdlc/do-build.md:104`.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-fix, document-feature
- **Assigned To**: solo dev
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_validate_docs_changed.py -q`.
- Run `python -m ruff check` + `python -m ruff format --check` on touched files.
- Verify all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New tests pass | `pytest tests/unit/test_validate_docs_changed.py -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/validate_docs_changed.py tests/unit/test_validate_docs_changed.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/validate_docs_changed.py tests/unit/test_validate_docs_changed.py` | exit code 0 |
| Stale scan is diff-scoped | `grep -c 'get_added_lines' scripts/validate_docs_changed.py` | output > 0 |
| Exit 2 documented for stale | `grep -c 'exit .*2.*stale\|stale.*exit .*2\|2 = stale' docs/features/documentation-lifecycle.md` | output > 0 |
| Stale scan uses three-dot diff | `grep -c 'base_branch}...HEAD\|\.\.\.HEAD' scripts/validate_docs_changed.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk/History/Scope | do-build.md:104 must encode per-exit-code blocking semantics (exit 3 also blocks) | Documentation tasks reworded | Comment is LLM-read; state exit 1 & 3 block, exit 2 is the only non-blocking warning; mirror in documentation-lifecycle.md |
| CONCERN | Risk & Robustness | Hunk-header parser must handle omitted `,d` count (single-line adds `@@ -a +c @@`) | Technical Approach + test | Regex `@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@`, default d=1; test `@@ -0,0 +1 @@` |
| CONCERN | Risk & Robustness | Staged-but-uncommitted new doc → three-dot diff misses it, Phase 2 scans nothing | Technical Approach untracked/staged fallback | On empty diff, also check `git diff --cached --name-only`; bounded since do-build commits docs before Gate 2 |
| CONCERN | Scope & Value | Success Criteria lack a real PR #2132 regression test | build-tests task | Stage a doc with pre-existing "NO LEGACY CODE TOLERANCE" + unrelated added line → assert exit 0 |
| NIT | Scope & Value | Discriminated outcome enum vs. exit int | Accepted as-is | String/enum kept for test readability |
| NIT | History & Consistency | "documented contract" framing vs. new exit 3 | Problem section clarified | Exit 3 extends the contract by splitting overloaded exit 2 |
