---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1627
last_comment_id:
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
- `exit code != N` — passes when `exit_code != N` (e.g. command must fail).
- `output does not contain X` — passes when substring X is absent from output.
- `match count == 0` — passes when output (stripped) is the literal `0` or empty;
  the canonical anti-criterion idiom is `grep -rc PATTERN path | ...` or
  `grep -r PATTERN path | wc -l`.

The grammar stays string-matched and additive — existing positive rows are
untouched. (Note: `grep` exits 1 when no match is found, so `exit code != 0` and
`match count == 0` are two valid spellings of the same "pattern absent" idea; both
are supported so authors can pick the clearer one.)

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
   forms (`exit code != N`, `output does not contain X`, `match count == 0`),
   placed before the positive checks so `!=` is not shadowed by the `exit code N`
   regex. Update the function docstring to list all six supported expectations.
2. Add unit tests to `tests/unit/test_verification_parser.py` covering each inverse
   form (pass and fail case for each), plus a regression test confirming the
   positive forms still parse and evaluate unchanged.
3. Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` `## Verification` block:
   document the three inverse expectations alongside the three positive ones, and
   add an example anti-criterion row (e.g. forbidden pattern absent).
4. Add a one-line pointer in the template's `## No-Gos` section: "For any No-Go
   describing a forbidden code outcome, add a `## Verification` row asserting its
   absence (an anti-criterion)."
5. Upgrade the manual No-Go line in `.claude/skills-global/do-pr-review/SKILL.md`
   (line ~421) and the checklist item in `sub-skills/code-review.md` to: confirm
   every assertable No-Go has a corresponding Verification anti-criterion row;
   advisory `[EXTERNAL]`/`[ORDERED]` No-Gos remain human judgment.
6. Update `docs/features/machine-readable-dod.md` to document inverse expectations
   and the No-Go → anti-criterion derivation.
7. Run `python -m ruff format . && python -m ruff check .` and the parser unit tests.

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
  inverse form plus a grammar-collision regression for `exit code != 0`.
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

- [SEPARATE-SLUG #1627] A separate top-level `## Anti-Criteria` plan section. The
  whole design decision (Q2/Q5) is to NOT add one — anti-criteria live as inverse
  rows in the existing `## Verification` table. Asserted by the Verification row
  "No stray second Anti-Criteria section in template" above.
- [SEPARATE-SLUG #1627] Changing `validate_no_gos_justification.py` to require an
  anti-criterion per No-Go. Anti-criteria are opt-in (Q1) — forcing them would
  punish genuinely advisory No-Gos. Out of scope by design.
- [SEPARATE-SLUG #1627] Wiring anti-criteria into `do-test`. `do-test` runs
  pytest/lint and is plan-unaware; plan-derived assertions belong to the
  plan-aware build/review skills (Q4).
- Nothing else deferred — every relevant item is in scope for this plan.

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

- **Inverse form, violation present:** assert `evaluate_expectation("output does
  not contain X", exit_code=0, output="...X...")` returns `False`.
- **Inverse form, clean:** assert it returns `True` when X is absent.
- **`match count == 0` with non-zero count:** `evaluate_expectation("match count == 0",
  exit_code=0, output="3")` returns `False`; with output `"0"` or empty returns `True`.
- **`exit code != N`:** returns `True` when exit code differs, `False` when equal —
  confirms a command that was supposed to fail but succeeded fails the check.
- **Grammar collision regression:** assert `exit code != 0` is NOT mis-parsed by the
  positive `exit code N` branch (the `!=` branch must be evaluated first).
- **Unrecognized expression still returns `False`** (existing safety default preserved).

## Test Impact
- [ ] `tests/unit/test_verification_parser.py` — UPDATE: add inverse-form cases (pass/fail per form) and a positive-grammar regression case. No existing case changes behavior; the inverse forms are purely additive, so prior assertions remain valid.

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

None remaining — the five open questions from the issue are resolved in the
Solution section (Q1 scope split, Q2 format, Q3 parser grammar, Q4 gate placement,
Q5 relationship to No-Gos). Flagging one design choice for confirmation: the
decision to reuse `## Verification` rather than add a `## Anti-Criteria` section
(Q2/Q5). If a reviewer prefers an explicitly-labeled section despite the drift
risk, that would change the template and parser entry point.

## Documentation
- [ ] Update `docs/features/machine-readable-dod.md` to document the three inverse
  expectations and the No-Go → anti-criterion derivation model.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` Verification guidance
  to name anti-criteria and show an example inverse row.
