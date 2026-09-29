# Sub-Skill: Code Review

Judgment work: review the checked-out PR against its plan, classify verified
findings, and derive the verdict mechanically from the Rubric. `PR_NUMBER`,
`ISSUE_NUMBER`, and `PLAN_PATH` come from `checkout.md`.

## 1. Gather Context

```bash
gh pr view "$PR_NUMBER" --json title,body,headRefName,baseRefName,files,additions,deletions,headRefOid
gh pr diff "$PR_NUMBER"
HEAD_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid)
```

`HEAD_SHA` is the commit you are judging; capture it before reading the diff
and use it for the `REVIEW_CONTEXT` marker and any verdict record.

Read the plan (if one resolves) for its acceptance criteria, No-Gos, and
architectural decisions, and `gh issue view "$ISSUE_NUMBER"` for the issue.

## 2. Disclosures (before findings)

Authors document scope exclusions, deferrals, and follow-ups in the PR body. A
finding that matches an honest disclosure is `acknowledged`, not tech debt:
re-raising a documented decision is a calibration failure.

1. **Extract.** Read the PR body and list every scope exclusion, deferral, or
   follow-up the author states, wherever it appears, with any `#N` it cites and
   any file, feature, or symbol it names.
2. **Verify follow-up claims.** For each claim that a follow-up was filed,
   `gh issue view N --json number,state,title`. An OPEN issue verifies it. A
   CLOSED or missing issue, or a claimed follow-up with no issue number, is a
   **tech_debt** finding: "Claimed follow-up for [disclosure] does not resolve
   to an open tracking issue." A pure out-of-scope exclusion with no tracking
   commitment is `acknowledged` with `tracked=false`.
3. **Classify.** A candidate finding is `acknowledged` only when all hold: it
   matches a disclosure (path, feature name, or `#N`); the disclosure's
   follow-up is a verified OPEN issue or an explicit out-of-scope exclusion with
   sound rationale; and it does not violate a plan `## No-Gos` item (No-Gos
   override disclosures; a disclosed No-Go violation is still a blocker).
   `acknowledged` findings appear only under `### Acknowledged Deferrals (verified)`,
   never under Blockers, Tech Debt, or Nits:

```markdown
### Acknowledged Deferrals (verified)
- **[disclosure text]** — tracked by #N (OPEN) — matched findings: [file path, if any]
- **[disclosure text]** — explicit out-of-scope exclusion, no tracking required
```

## 3. Prior Review Context (before findings)

Repeated passes on unchanged inputs must return the same verdict, and drift
between runs must be visible.

```bash
gh api --paginate "repos/{owner}/{repo}/issues/$PR_NUMBER/comments" \
  --jq '[.[] | select(.body | startswith("## Review:")) | {body, created_at, id}]'
gh api --paginate "repos/{owner}/{repo}/pulls/$PR_NUMBER/reviews" \
  --jq '[.[] | select(.body | startswith("## Review:")) | {body, submitted_at, id}]'
PR_BODY_HASH=$(gh pr view "$PR_NUMBER" --json body --jq .body | shasum -a 256 | awk '{print $1}')
```

(`gh api` fills `{owner}/{repo}` from the current repo or `GH_REPO`.)

Take the most recent prior review across both lists and read its
`<!-- REVIEW_CONTEXT head_sha=... pr_body_hash=... -->` marker, its verdict
heading, and its findings.

- **Idempotent:** if its `head_sha` equals `$HEAD_SHA` and its `pr_body_hash`
  equals `$PR_BODY_HASH`, return the prior verdict and findings unchanged,
  flagged idempotent, and skip to post-review.
- **Changed inputs:** review fresh, then add a Review Delta section comparing
  against the prior findings (omit it when there is no prior review):

```markdown
### Review Delta (vs prior review on HEAD {prior_sha:0:7})
- **Resolved**: [prior finding no longer present, and why]
- **New**: [finding not in the prior review]
- **Unchanged**: [finding carried forward]
```

Every review body ends with `<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->`
(before the OUTCOME block) so the next pass can detect idempotency.

## 4. Analyze the Diff and Validate the Plan

Review each changed file for correctness against the plan and PR description,
security, error handling at system boundaries, tests, code quality, and docs.
Grep callers of changed functions for regressions.

For each plan acceptance criterion, find its implementation and check it
behaves as specified, including edge cases the plan names. Also:

- **No-Gos.** Every plan No-Go is respected. For each assertable
  `[DESTRUCTIVE]` or `[SEPARATE-SLUG]` No-Go with no matching `## Verification`
  anti-criterion row, add a non-blocking advisory (anti-criteria are opt-in;
  `[EXTERNAL]` and `[ORDERED]` No-Gos need none). A missing pasted red-state
  FAIL output for an authored anti-criterion is also advisory.
- **Disclosed and satisfied.** A criterion not addressed by the diff but covered
  by a verified disclosure is `acknowledged`. A criterion both covered by a
  disclosure AND demonstrably satisfied by the diff is `pass`: the disclosure is
  informational only, and the plan tick reflects the `pass`. (Otherwise a patch
  that satisfies a deferred criterion gets unticked again by the next review.)
- **Unchecked plan items.** For each unchecked `- [ ]` item under Acceptance or
  Success Criteria (BLOCKER if unaddressed), Test Impact, Documentation, or
  Update System (WARNING if unaddressed): if the diff addresses it, pass
  silently; if not, report it:

```
**Unaddressed plan item** (BLOCKER|WARNING):
  Section: [section name]
  Item: [checkbox text]
  Assessment: [why the diff does not address this]
```

## 5. Run Verification Checks (if the plan has a `## Verification` table)

Run each row's `Command` on the PR branch and compare against `Expected`, or
use the context file's verification-table runner if it declares one. A failed
check is a blocker.

## 6. Rubric and Mechanical Verdict

Evaluate every item as `pass`, `fail`, `acknowledged` (not satisfied, but
covered by a verified disclosure), or `n/a` (does not apply to this PR). A blank
item invalidates the review.

1. **Plan vs. implementation match** — All plan acceptance/success criteria validated against diff: each is delivered or covered by an acknowledged deferral; No-Gos are not violated; the plan's own spike findings and task steps are consistent. Critical.
2. **New code quality** — type hints where the project uses them, meaningful names, error handling at system boundaries (every new `except Exception` logs, re-raises, or is marked as a deliberate swallow), no copy-paste blocks, no leftover debug artifacts. Critical.
3. **Test coverage** — new behavior and code paths have tests, including the failure path; new integration tests exercise the real serialization boundary, not in-memory only; pre-existing tests still pass. Critical.
4. **Regression risk to existing callers** — any caller whose behavior could silently change is covered by a test, or the change is backward-compatible. Critical.
5. **Data integrity** — migrations present for schema changes; `update_fields=[...]` includes `modified_at` when the model has `auto_now`; JSON field additions do not break existing rows. Critical.
6. **Security** — no new `mark_safe`, `raw()`, `eval`, `exec`, `subprocess` with request-derived input, or SQL string interpolation; no hardcoded secrets. Critical.
7. **Documentation accuracy** — docs updated for user-facing or architecturally visible changes; new public APIs have docstrings; breaking changes document a migration path; no stale references to removed code. Non-critical.
8. **PR body accuracy** — claims in the PR body (file counts, test counts, migrations, disclosures) match the diff. Non-critical.
9. **Disclosed deferrals** — every disclosure has an OPEN tracking issue or is an explicit out-of-scope exclusion with sound rationale. Critical.
10. **Follow-up claims verified** — every "filed as follow-up #N" / "tracked by #N" claim resolves to an OPEN issue. Critical.

Emit it in the review body, checking the box only for `pass` or `n/a`:

```markdown
## Rubric

- [ ] **1. Plan vs. implementation match** — pass/fail/acknowledged/n/a — *notes*
- [ ] **2. New code quality** — pass/fail/acknowledged/n/a — *notes*
- [ ] **3. Test coverage** — pass/fail/acknowledged/n/a — *notes*
- [ ] **4. Regression risk to existing callers** — pass/fail/acknowledged/n/a — *notes*
- [ ] **5. Data integrity** — pass/fail/acknowledged/n/a — *notes*
- [ ] **6. Security** — pass/fail/acknowledged/n/a — *notes*
- [ ] **7. Documentation accuracy** — pass/fail/acknowledged/n/a — *notes*
- [ ] **8. PR body accuracy** — pass/fail/acknowledged/n/a — *notes*
- [ ] **9. Disclosed deferrals** — pass/fail/acknowledged/n/a — *notes*
- [ ] **10. Follow-up claims verified** — pass/fail/acknowledged/n/a — *notes*
```

A real issue that fits no rubric item goes in `### Miscellaneous` with its own
severity; it is not a place for findings you are unsure of.

**Verdict derivation (mechanical; first match wins):**

1. Any critical-item `fail` (items 1-6, 9, 10) with no matching `acknowledged`,
   or any Miscellaneous `blocker` → `CHANGES REQUESTED — Blocker`. The fails
   become blocker findings.
2. Any non-critical `fail` (items 7, 8), or any Miscellaneous `tech_debt` or
   `nit` → `CHANGES REQUESTED — Tech Debt`. The fails become tech_debt findings.
3. All items `pass`, `acknowledged`, or `n/a`, and Miscellaneous empty →
   `APPROVED`.

The verdict is never freeform.

## 7. Classify and Verify Findings

- **blocker**: breaks functionality, security issue, or data-loss risk.
- **tech_debt**: code quality or a missing edge-case test; patched before merge.
- **nit**: style, naming, docs wording; patched unless purely subjective.
- **acknowledged**: matches a verified disclosure (section 2).

Each blocker, tech_debt, nit, and Miscellaneous finding uses every field:

```
**File:** `path/to/file.py:42` (verified: read this file)
**Code:** `the_actual_code_on_that_line()`
**Issue:** [clear description of the problem]
**Severity:** blocker | tech_debt | nit
**Fix:** [suggested fix]
```

Before reporting, confirm for each finding that you read the cited file, the
quoted code is at or near the cited line, and the description matches what the
code does; drop any that fails. Each blocker costs a patch-and-review round, and
any single judge's blocker decides the verdict, so report a blocker only with
the cited, verified code that proves it. Report a real issue you cannot fully
prove as tech_debt with the open question stated.

Empty categories keep their heading with an explicit marker:
`### Blockers\n- None`, `### Tech Debt\n- None`, `### Nits\n- None`,
`### Miscellaneous\n- None`, `### Acknowledged Deferrals (verified)\n- None`.

## 8. Legacy Cruft Audit

Scan the diff for legacy patterns (deprecated fields still read or written,
fallback chains, dual implementations, dead imports, stale comments). Dispatch
the repo's cruft-auditor agent if it defines one (e.g.
`.claude/agents/cruft-auditor.md`), with `run_in_background: false`; otherwise
scan directly. Report results in a "Legacy Cruft" subsection; they are
advisory, never blockers.

## Completion

Hand post-review: the Rubric, the classified findings, Miscellaneous,
Acknowledged Deferrals, the Review Delta (if any), verification results (if
any), the derived verdict, and the `REVIEW_CONTEXT` marker, or the prior verdict
flagged idempotent.
