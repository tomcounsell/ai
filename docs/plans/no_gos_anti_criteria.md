---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1627
last_comment_id:
revision_applied: true
---

# Promote No-Gos to Testable Anti-Criteria

## Problem

Our plans carry a `## No-Gos (Out of Scope)` section and a validator
(`validate_no_gos_justification.py`) that enforces each entry is tagged
(`[EXTERNAL]`, `[ORDERED]`, `[DESTRUCTIVE]`, `[SEPARATE-SLUG #NNN]`). But that
validator only checks **prose justification syntax** — it never checks that the
No-Go is honored in the shipped code. No-Gos are advisory, not asserted:

- `do-test` never mentions No-Gos. Tests validate positive criteria only.
- `do-pr-review` has one manual line ("Verify any 'No-Gos' from the plan are
  respected") that depends entirely on a human reading the diff.
- `agent/verification_parser.py` handles only positive assertions (`exit code N`,
  `output > N`, `output contains X`). There is no inverse-assertion support.

**Current behavior:** A plan can declare a No-Go and nothing mechanically stops a
build from violating it. The only control is a reviewer remembering to check.

**Desired outcome:** Promote the *mechanically assertable* No-Gos into
machine-checkable **anti-criteria** — negative assertions ("X must NOT happen")
verified automatically during the build verification step and PR review, on par
with positive verification checks. The PAI project's VERIFY phase treats
anti-criteria as first-class asserted criteria; we adopt that idea but reconcile
it with our existing `## No-Gos` convention so we do not end up with two
competing, drifting sections.

## Freshness Check

**Baseline commit:** `bfe3b0a6`
**Issue filed at:** 2026-06-11T06:16:05Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/verification_parser.py:87-118` — `evaluate_expectation()` supports only positive expectations — still holds (203-line file, no inverse support).
- `agent/verification_parser.py:23-40` — `VerificationCheck`/`CheckResult` dataclasses — still holds.
- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:248-274` — `## No-Gos` with four tags — still holds.
- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:424-436` — `## Verification` table with three supported expectations — still holds.
- `.claude/hooks/validators/validate_no_gos_justification.py:39-51` — VALID_TAGS + PUNT_PHRASES, prose-only — still holds.
- `.claude/skills-global/do-build/SKILL.md:354-376` — Step 5.1 runs the Verification table — still holds.
- `.claude/skills-global/do-pr-review/SKILL.md:421` — manual "Verify any 'No-Gos' ... are respected" line — still holds.

**Cited sibling issues/PRs re-checked:** None cited in the issue body.

**Commits on main since issue was filed (touching referenced files):** None — `git log --since` over all referenced files returned empty.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** No drift. All five open questions are resolved below.

## Prior Art

No prior issues or merged PRs found that attempted anti-criteria or inverse
assertions. Searches: closed issues for "anti-criteria No-Gos verification" and
merged PRs for "verification_parser No-Gos" returned only unrelated session-health
work. The `## Verification` table + `verification_parser.py` machinery
(documented in `docs/features/machine-readable-dod.md`) is the closest existing
pattern and is the foundation this plan extends.

## Research

No relevant external findings — this is a purely internal change to our own plan
template, verification parser, and SDLC skills. No external libraries, APIs, or
ecosystem patterns are involved. The conceptual inspiration (PAI's VERIFY-phase
anti-criteria) is already captured in the issue body.

## Solution

The core insight: **anti-criteria are not a new section parallel to No-Gos — they
are the machine-executable expression of the assertable subset of No-Gos.** We
avoid section drift by reusing the existing `## Verification` table machinery and
extending it with inverse-assertion grammar, rather than introducing a separate
`## Anti-Criteria` section that would duplicate and diverge from No-Gos.

### Resolution of the five open questions

**Q1 — Scope split (which No-Gos are assertable).** Only No-Gos that describe a
forbidden *code-level outcome* are mechanically assertable. The four existing tags
already partition this cleanly:
- `[DESTRUCTIVE]` — frequently assertable ("no `DROP TABLE` in the diff", "no call
  to the bulk-delete path").
- `[SEPARATE-SLUG #NNN]` — sometimes assertable (the deferred feature's symbol/file
  must NOT appear in this PR).
- `[EXTERNAL]` and `[ORDERED]` — genuinely advisory (they describe human/world
  actions or cross-system sequencing, not code outcomes) and are **never** required
  to have an anti-criterion.

Anti-criteria are therefore **opt-in per No-Go**, not mandatory for every No-Go.
A No-Go becomes an anti-criterion only when the author can write a command that
mechanically detects its violation.

**Q2 — Format.** No new top-level section. We extend the existing `## Verification`
table: anti-criteria are ordinary rows whose `Expected` column uses the new
inverse grammar (below). To make intent legible, the template documents that
inverse-expectation rows ARE the anti-criteria, and the No-Gos section gains a
one-line pointer encouraging authors to add a Verification row for any assertable
No-Go. This keeps one source of executable checks.

**Q3 — Parser extension.** `evaluate_expectation()` gains three inverse forms,
mirroring the three positive forms exactly:
- `exit code != N` — passes when `exit_code != N` (e.g. command must fail). This is
  distinct from the existing positive `exit code N` (exact match: passes when
  `exit_code == N`). Precedence is unambiguous because the two are syntactically
  disjoint: the `!=` branch matches `r"exit code\s*!=\s*(\d+)"` and is checked FIRST;
  the positive `r"exit code (\d+)"` cannot match a string containing `!=` (`!` is not
  a digit). The PLAN_TEMPLATE's existing `exit code 1` sample row (the "No stale
  xfails" check) is a *positive exact-match* (xfail grep must exit 1 == "no matches
  found") and is unaffected — it does not use `!=`. Task 3 documents bare `exit code N`
  as positive exact-match and `exit code != N` as inverse so the two are never confused.
- `output does not contain X` — passes when substring X is absent from output
  **AND the command produced real output** (see the empty-stdout gate below) so an
  errored command cannot false-pass on trivially-absent substring.
- `match count == 0` — passes when stdout is non-empty **AND every non-blank line**
  of the (stripped) output is the literal `0` or ends with `:0` (the `grep -c`/
  `grep -rc` shape). **Truly-empty stdout fails** (empty-stdout gate, below) — a
  command that errored, hit a missing tool, or wrote only to stderr produces `""` on
  stdout, and `all(...)` over an empty line list is vacuously `True`, which would
  report PASS without the check running meaningfully. The gate rejects that. Note the
  `wc -l` shape (`grep -r ... | wc -l`) emits a leading-whitespace `0` on stdout — that
  is *non-empty* and strips to `0`, so it passes; only *zero bytes* on stdout fail.

**`match count == 0` must be line-robust AND reject empty stdout — this is the
BLOCKER fix.** Two failure modes must be closed at once: (1) the naive
"output stripped equals `0`" matcher silently never passes for the multi-line
`grep -rc` idioms the docstring will recommend; (2) an empty-stdout vacuous pass —
`all(...)` over an empty line list is `True`, so a command that errored, hit a
missing tool, or wrote only to stderr returns `""` on stdout and reports PASS
without running. **The empty-stdout gate closes (2); the per-line matcher closes (1).**

Verified `grep` shapes (all non-empty stdout):
- `grep -c PATTERN file` (single file) → emits literal `0`, exit 1. Strips to `0`. ✓
- `grep -rc PATTERN dir` (directory) → emits **multiple** `path:0` lines, exit 1.
  A whole-string `== "0"` check FAILS this; a per-line `endswith(":0")` check passes. ✓
- `grep -rc PATTERN file` (recursive on a file) → emits `file:0`, exit 1. Same. ✓
- `grep -r PATTERN dir | wc -l` → emits leading-whitespace `       0`, exit 0.
  Strips to `0`, and `"       0"` is non-empty, so the gate lets it through. ✓
- **errored / missing-tool / stderr-only** → emits `""` on stdout (zero bytes).
  The gate fires: this is the silent-false-pass mode the plan exists to prevent. ✗

Concretely, the matcher logic at `agent/verification_parser.py` is:
```python
if not output.strip():           # empty-stdout gate (BLOCKER fix)
    return False                 # errored / stderr-only command never false-passes
lines = [ln.strip() for ln in output.strip().splitlines() if ln.strip()]
return all(ln == "0" or ln.endswith(":0") for ln in lines)
```
The critical distinction: a legitimately-clean `grep -c` returns a literal `0` (one
byte) on stdout — **non-empty**, so the gate does NOT fire and the line matcher
passes it. Only *truly-empty* stdout (the command produced no output at all) fails.
This passes for literal `0`, leading-whitespace `0`, a single `path:0`, and many
`path:0` lines — covering every documented idiom — while rejecting truly-empty
output. Any non-zero count (`3`, `path:3`) fails because that line is neither `0`
nor `:0`-suffixed. The empty-stdout gate is **identical in spirit** to the gate
already specified for `output does not contain X`. The docstring + template pin these
as the supported idioms and show one canonical example so authors do not invent a
shape the matcher rejects.

The grammar stays string-matched and additive — existing positive rows are
untouched.

**Why all three inverse forms are kept (overlap is intentional, not redundant).**
For `grep` specifically, `exit code != 0` and `match count == 0` do overlap — `grep`
exits 1 when no match is found, so both spell "pattern absent." They are kept as two
valid spellings because `match count == 0` reads more clearly for count-based
assertions and `exit code != 0` reads more clearly for "this command must fail."
Critically, **`exit code != N` is the only inverse form that works for non-grep
commands** — e.g. "the migration script must error on a dirty tree" (`exit code != 0`)
or "the validator must not return the success code 2" (`exit code != 2`). Cutting it
to remove the grep overlap would strip the one general-purpose inverse assertion and
leave only grep-shaped checks. The two grep spellings overlapping is a minor,
acceptable cost; `exit code != N` earns its place by covering everything grep cannot.
(The plan's own Verification table uses `match count == 0` with a `grep -c`
single-file row, which strips to a bare `0` and passes — but the matcher above also
handles the `-rc` multi-line shapes so authors are not silently misled.)

**Q4 — Gate placement.** Anti-criteria run wherever the Verification table already
runs — `do-build` Step 5.1 and `do-pr-review` — automatically, because they are
just Verification rows. No separate execution path. Failure behavior is identical
to existing verification rows: any failing row fails the build step (exit 1) and
surfaces as a FAIL line in PR review. We additionally **upgrade the manual No-Go
line in `do-pr-review`** to instruct the reviewer to confirm that every assertable
No-Go has a corresponding Verification row (closing the "author forgot to assert
it" gap), while leaving truly advisory No-Gos as human judgment. `do-test` is left
unchanged — it runs pytest/lint and is the wrong layer for plan-derived assertions
(those belong to the plan-aware build/review skills).

**Q5 — Relationship to No-Gos.** Anti-criteria **derive from** No-Gos; they do not
replace or duplicate them. The `## No-Gos` section remains the human-readable
declaration of scope. The `## Verification` table holds the executable inverse
assertions. The link is documented in both directions (No-Gos points to
Verification; the template's Verification guidance names anti-criteria). Single
source of executable truth, no second section to drift.

### Components touched

| Component | Change |
|---|---|
| `agent/verification_parser.py` | Add 3 inverse forms to `evaluate_expectation()` |
| `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` | Document inverse grammar in `## Verification`; add anti-criterion pointer to `## No-Gos` |
| `.claude/skills-global/do-pr-review/SKILL.md` | Upgrade manual No-Go line to require an asserted-row check |
| `tests/unit/test_verification_parser.py` | Add cases for the 3 inverse forms |
| `docs/features/machine-readable-dod.md` | Document anti-criteria / inverse expectations |

No change to `validate_no_gos_justification.py` (it stays prose-only — anti-criteria
live in the Verification table, not the No-Gos prose, so its scope is unchanged).
No change to `do-test` or `do-build` execution code (the parser change flows through
the existing `parse_verification_table`/`run_checks` calls automatically).

## Data Flow

A plan's `## Verification` table is the single entry point. At build time,
`do-build` Step 5.1 reads the plan file → `parse_verification_table()` extracts
every row (positive and inverse alike) into `VerificationCheck` objects →
`run_checks()` executes each command via subprocess → `evaluate_expectation()`
decides pass/fail per row, now understanding inverse grammar → `format_results()`
renders PASS/FAIL → build fails on any FAIL. At review time, `do-pr-review` runs
the identical pipeline. No new data path: inverse assertions ride the existing one.

## Why Previous Fixes Failed

No prior fixes — greenfield extension of the verification machinery.

## Step by Step Tasks

1. Extend `agent/verification_parser.py::evaluate_expectation()` with three inverse
   forms (`exit code != N`, `output does not contain X`, `match count == 0`).
   Match the `exit code != N` branch with an explicit `!=` regex
   (`r"exit code\s*!=\s*(\d+)"`) ordered **before** the positive `exit code (\d+)`
   branch. **Rationale (corrected):** the positive regex `r"exit code (\d+)"` does
   NOT actually match `"exit code != 0"` — `!` is not a digit, so `re.match` returns
   `None` and would fall through to the safety default `False`, silently failing a
   valid inverse row. Ordering the `!=` branch first is what makes the inverse form
   evaluate at all; it is not about preventing a (non-existent) shadow. Anchor the
   inverse regex tightly (`exit code\s*!=\s*(\d+)`) so it cannot swallow unrelated
   text. Implement `output does not contain X` with an **empty-stdout gate**: return
   `False` when stripped output is empty (an errored/no-output command must not
   false-pass by trivially "not containing" the substring); otherwise return
   `substring not in output`. Implement `match count == 0` with the line-robust
   matcher from Q3, **including the same empty-stdout gate**: `if not output.strip():
   return False` BEFORE the line-parsing logic, so a command that errored / hit a
   missing tool / wrote only to stderr (empty stdout) cannot vacuously pass on
   `all(...)` over an empty line list. A legitimately-clean `grep -c` returns a
   literal `0` on stdout (non-empty), so the gate fires only on truly-empty output —
   spec this distinction explicitly in the docstring. Update the docstring to list
   all six supported expectations with the canonical idiom for each inverse form.
2. Add unit tests to `tests/unit/test_verification_parser.py` covering each inverse
   form (pass and fail case for each), plus a regression test confirming the
   positive forms still parse and evaluate unchanged.
3. Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` `## Verification` block:
   the supported-expectations line currently reads `"exit code N", "output > N",
   "output contains X"` (line ~428) — extend it to list all six, explicitly stating
   that **bare `exit code N` is a positive exact-match** (passes when `exit_code == N`)
   and **`exit code != N` is the inverse** (passes when `exit_code != N`). Reconcile
   the existing `exit code 1` sample row (the "No stale xfails" check at line ~435) by
   noting it is a positive exact-match, not an inverse — it stays as-is. Add an example
   anti-criterion row (e.g. forbidden pattern absent via `match count == 0`).
4. Add a one-line pointer in the template's `## No-Gos` section: "For any No-Go
   describing a forbidden code outcome, add a `## Verification` row asserting its
   absence (an anti-criterion)."
5. Upgrade the manual No-Go line in `.claude/skills-global/do-pr-review/SKILL.md`
   (line ~421) and the checklist item in `sub-skills/code-review.md` to: confirm
   every assertable No-Go has a corresponding Verification anti-criterion row, AND
   confirm the PR description contains the **pasted red-state FAIL output** for each
   authored anti-criterion (posture (a) paper trail — see Task 6); advisory
   `[EXTERNAL]`/`[ORDERED]` No-Gos remain human judgment.
6. Update `docs/features/machine-readable-dod.md` to document inverse expectations
   and the No-Go → anti-criterion derivation. Include a **red-state authoring rule**
   with an explicit enforcement posture — **posture (a): paper-trail PR-checklist
   item**, chosen over fully-mechanical enforcement (the parser cannot know which
   input is "deliberately violating", so true machine enforcement is impossible here).
   The rule: when an author adds an inverse Verification row, they must demonstrate it
   FAILS once against a deliberately-violating input before trusting it (e.g.
   temporarily point the `grep` at a file that DOES contain the forbidden pattern and
   confirm the row reports FAIL), and **paste that FAIL output into the PR
   description** as the paper trail. Enforcement is the PR-review checklist item in
   Task 5 (reviewer confirms the pasted red-state output is present), not a code gate —
   this is stated as the deliberately-chosen posture, not a gap.
7. **Adoption proof (docs-only by default).** The default and required deliverable
   is a worked end-to-end example in `docs/features/machine-readable-dod.md`, run
   **live against the repo** to prove both states: take a real assertable
   `[DESTRUCTIVE]` No-Go pattern (e.g. "no raw `r.delete`/`r.srem` on Popoto keys" —
   assert `grep -rc "r\.delete\|r\.srem" <changed-paths>` → `match count == 0`),
   execute it against clean code (green) and against a deliberately-violating line
   (red), and cite both commands and outputs verbatim in the doc. This proves the
   opt-in mechanism end-to-end without touching any live plan.
   **Do NOT mutate an in-flight plan** in `docs/plans/` whose tracking issue is still
   open — a live SDLC session may read that plan mid-edit and split-brain on it.
   **Optional, only if a closed/merged-issue plan with a suitable assertable
   `[DESTRUCTIVE]` No-Go exists:** additionally add the inverse Verification row to
   that *settled* plan as a second adoption proof. The docs-only example is sufficient
   on its own; the real-plan conversion is a bonus that is reserved for plans whose
   issues are closed so there is no concurrent-edit hazard.
8. Run `python -m ruff format . && python -m ruff check .` and the parser unit tests.

## Success Criteria

- `evaluate_expectation()` recognizes all three inverse forms (`exit code != N`,
  `output does not contain X`, `match count == 0`) and returns correct pass/fail.
- All three positive forms continue to work unchanged (regression-covered).
- `## Verification` rows using inverse grammar run automatically in `do-build`
  Step 5.1 and `do-pr-review` with no new execution path.
- `PLAN_TEMPLATE.md` documents the inverse grammar and names anti-criteria; the
  `## No-Gos` section points authors to add a Verification row for assertable No-Gos.
- `do-pr-review` instructs the reviewer to confirm every assertable No-Go has a
  corresponding anti-criterion row.
- `tests/unit/test_verification_parser.py` covers pass and fail cases for each
  inverse form, a parametrized grep-shape suite for `match count == 0` (bare `0`,
  whitespace `0`, `path:0`, multi-line `path:0` — all pass; `3`, `path:3`,
  mixed, and **empty/whitespace-only stdout** — all fail), an empty-stdout gate case
  for `output does not contain X`, and a grammar-collision regression for
  `exit code != 0`.
- **Red-state proof (posture (a), paper trail):** every authored anti-criterion in
  this PR has its red-state FAIL output pasted into the PR description; the
  `do-pr-review` checklist item (Task 5) confirms its presence. Going forward the
  documented authoring rule requires the same paste. This is a reviewer-enforced paper
  trail, deliberately chosen because the parser cannot mechanically know which input
  is "deliberately violating" — not an unenforced gap.
- **Adoption proof:** a worked end-to-end example in
  `docs/features/machine-readable-dod.md` converts a real assertable `[DESTRUCTIVE]`
  No-Go pattern into an inverse Verification row, run live against the repo to show it
  passes green on clean code and fails red on a violating line (both commands and
  outputs cited). No in-flight plan (open tracking issue) was mutated; a real-plan
  conversion is optional and reserved for closed/merged-issue plans only.
- No second `## Anti-Criteria` section exists anywhere; `validate_no_gos_justification.py`
  is unchanged.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Parser unit tests pass | `python -m pytest tests/unit/test_verification_parser.py -q` | exit code 0 |
| Inverse grammar implemented | `grep -c "does not contain\|match count\|exit code !=" agent/verification_parser.py` | output > 2 |
| Lint clean | `python -m ruff check agent/verification_parser.py` | exit code 0 |
| Template documents anti-criteria | `grep -c "anti-criter" .claude/skills-global/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| Positive grammar not broken (anti-criterion: no removal of `output contains`) | `grep -c "output contains" agent/verification_parser.py` | output > 0 |
| No stray second Anti-Criteria section in template (anti-criterion) | `grep -c "^## Anti-Criteria" .claude/skills-global/do-plan/PLAN_TEMPLATE.md` | match count == 0 |

## No-Gos (Out of Scope)

These are **design exclusions, not deferred work** — none is split off to another
slug or issue, so they carry prose justification rather than a `[SEPARATE-SLUG]` tag
(a self-referencing `#1627` tag would convey no routing information).

- A separate top-level `## Anti-Criteria` plan section is **rejected by design**
  (Q2/Q5), not deferred: anti-criteria live as inverse rows in the existing
  `## Verification` table. Asserted by the Verification row "No stray second
  Anti-Criteria section in template" above.
- Changing `validate_no_gos_justification.py` to require an anti-criterion per No-Go
  is **rejected by design** (Q1): anti-criteria are opt-in, and forcing them would
  punish genuinely advisory `[EXTERNAL]`/`[ORDERED]` No-Gos. Not a future task.
- Wiring anti-criteria into `do-test` is **rejected by design** (Q4): `do-test` runs
  pytest/lint and is plan-unaware; plan-derived assertions belong to the plan-aware
  build/review skills. Not a future task.
- Nothing deferred — every relevant item is either in scope for this plan or a
  by-design exclusion above.

## Update System

The changed files are `.claude/skills-global/` skills (synced to every machine by
`/update` via `scripts/update/hardlinks.py::sync_claude_dirs`) and a Python module
under `agent/`. No new dependencies, config files, or migration steps. The skill
edits propagate automatically through the existing hardlink sync; no new
`RENAMED_REMOVALS` entry is needed because no skill is being renamed or moved
between `skills/` and `skills-global/`. No update-script changes required.

## Agent Integration

No agent integration required. This change is internal to the SDLC tooling: the
agent already invokes `agent/verification_parser.py` indirectly via the
`do-build`/`do-pr-review` skill steps (which run inline `python -c` blocks
importing `parse_verification_table`/`run_checks`/`format_results`). No new CLI
entry point in `pyproject.toml [project.scripts]` and no new bridge import are
needed — the existing skill-embedded import path picks up the extended grammar
automatically. Coverage is via the unit tests on `evaluate_expectation()`.

## Failure Path Test Strategy

- **`output does not contain X`, violation present:** assert it returns `False`
  for `exit_code=0, output="...X..."`.
- **`output does not contain X`, clean:** returns `True` when X is absent **and**
  output is non-empty (e.g. `output="all clean, no matches"`).
- **`output does not contain X`, empty-stdout gate:** `exit_code=1, output=""`
  returns `False` — an errored command with no stdout must NOT false-pass as "safe"
  just because the substring is trivially absent. (CONCERN fix.)
- **`match count == 0` — all documented grep shapes pass:** parametrize over the
  real `grep` output shapes and assert `True` for each:
  - literal `"0"` (`grep -c PATTERN file`)
  - leading-whitespace `"       0"` (`grep -r ... | wc -l`)
  - single `"path/to/file:0"` (`grep -rc PATTERN file`)
  - multi-line `"a.txt:0\nb.txt:0"` (`grep -rc PATTERN dir`)
  (Empty stdout is NOT in this pass list — it is a FAIL case, covered separately
  below by the empty-stdout gate.)
  This is the BLOCKER regression: the matcher must accept the `:0`-suffixed and
  multi-line shapes, not only bare `0`.
- **`match count == 0` empty-stdout gate:** assert `False` for `output=""` (and for
  whitespace-only `"   \n"`). This is the BLOCKER fix: an errored / missing-tool /
  stderr-only command yields empty stdout, and without the gate `all(...)` over an
  empty line list would vacuously report PASS. The literal `0` case above must still
  pass (it is non-empty stdout), confirming the gate fires only on truly-empty output.
- **`match count == 0` with non-zero count:** assert `False` for `"3"`, for
  `"path:3"`, and for a mixed `"a.txt:0\nb.txt:2"` (one non-zero line must fail).
- **`exit code != N`:** returns `True` when exit code differs, `False` when equal —
  confirms a command that was supposed to fail but succeeded fails the check.
- **Grammar collision regression:** assert `evaluate_expectation("exit code != 0",
  exit_code=0, ...)` returns `False` and `exit_code=1` returns `True` — i.e. the
  `!=` branch is reached and evaluated. (Verified prerequisite: the positive
  `r"exit code (\d+)"` regex returns `None` on `"exit code != 0"`, so without the
  inverse branch this row would hit the safety default and silently fail.)
- **Unrecognized expression still returns `False`** (existing safety default preserved).

## Test Impact
- [ ] `tests/unit/test_verification_parser.py` — UPDATE: add inverse-form cases (pass/fail per form), a parametrized `match count == 0` grep-shape suite (passing: bare `0`, whitespace `0`, `path:0`, multi-line `path:0`; failing: non-zero `3`/`path:3`/mixed `a:0\nb:2`, AND empty/whitespace-only stdout via the empty-stdout gate), an empty-stdout gate case for `output does not contain X`, and the `exit code != 0` grammar-collision regression. No existing case changes behavior; the inverse forms are purely additive, so prior assertions remain valid.

## Rabbit Holes

- **Do not** build a generic boolean-expression / regex DSL for expectations. Keep
  to three string-matched inverse forms mirroring the three positive forms. The
  parser's value is its simplicity and predictability.
- **Do not** auto-generate anti-criteria from No-Go prose with an LLM. Authors write
  the command explicitly; mechanical detection requires an explicit detection method.
- **Do not** touch `validate_no_gos_justification.py` — anti-criteria are not prose,
  so its remit is unchanged. Expanding it risks coupling two independent gates.
- **Do not** add anti-criteria to `do-test` — resist the urge to make the test
  runner plan-aware.

## Open Questions

None remaining. The five open questions from the issue are resolved in the
Solution section (Q1 scope split, Q2 format, Q3 parser grammar, Q4 gate placement,
Q5 relationship to No-Gos). The central design choice is **settled, not hedged**:
anti-criteria reuse the existing `## Verification` table as inverse-grammar rows;
**no separate `## Anti-Criteria` section is added.** Critique confirmed this avoids
the section-drift risk of two competing scope declarations. This decision is final
for the plan — the template and parser entry point follow from it.

## Documentation
- [ ] Update `docs/features/machine-readable-dod.md` to document the three inverse
  expectations and the No-Go → anti-criterion derivation model.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` Verification guidance
  to name anti-criteria and show an example inverse row.
