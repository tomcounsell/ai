# Sub-Skill: Code Review

Judgment work: analyze the PR diff for correctness, security, and quality.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number (fallback: extract from git or nudge feedback)
- `$SDLC_SLUG` — work item slug for finding the plan document
- `$SDLC_PLAN_PATH` — direct path to plan document (fallback: derive from slug)
- `$SDLC_ISSUE_NUMBER` — tracking issue number

## Prerequisites

PR branch must already be checked out (via checkout sub-skill).

## Steps

### 1. Gather PR Context

```bash
PR_NUMBER="${SDLC_PR_NUMBER}"
gh pr view $PR_NUMBER --json title,body,headRefName,baseRefName,files,additions,deletions,headRefOid
gh pr diff $PR_NUMBER
gh pr diff $PR_NUMBER --name-only
```

Capture the HEAD SHA for use in the Prior Review Context step:

```bash
HEAD_SHA=$(gh pr view $PR_NUMBER --json headRefOid --jq .headRefOid)
```

### 2. Load Plan Context

```bash
PLAN_PATH="${SDLC_PLAN_PATH:-}"
if [ -z "$PLAN_PATH" ] && [ -n "${SDLC_SLUG:-}" ]; then
  PLAN_PATH="docs/plans/${SDLC_SLUG}.md"
fi
```

Read the plan document (if it exists) and extract:
- Acceptance criteria / success conditions
- No-Gos (things explicitly excluded)
- Architectural decisions

If `$SDLC_ISSUE_NUMBER` is set, also fetch the issue:
```bash
gh issue view $SDLC_ISSUE_NUMBER
```

### 2.5. Disclosure Parser (mandatory, runs BEFORE findings)

The PR author often documents scope exclusions, deferrals, and follow-ups in the PR body. Findings that match a disclosure are **acknowledged**, not tech debt. Misclassifying a disclosed deferral as tech debt is a calibration failure — it re-surfaces a decision the author already made and documented.

**Step A: Extract disclosure blocks from the PR body.**

Scan the PR body (captured in Step 1) for sections with these headings (case-insensitive, tolerate variants):

- `Out of scope`
- `Deferred` / `Deferred items` / `Deferred to follow-up`
- `Not in this PR` / `Not in scope`
- `Follow-up` / `Follow-ups` / `Follow up` / `Follow-up items`
- `Known limitations`
- `Disclosures`

Also collect inline bullet disclosures anywhere in the body that match these phrase patterns (case-insensitive):

- `deferred`, `deferred to follow-up`, `filed as follow-up`, `tracked separately`
- `out of scope`, `not addressed in this PR`, `dropped`
- `Closes #N`, `Addresses #N`, `Related to #N`, `Tracked by #N`

For each disclosure, capture:
- The disclosure text (one logical bullet or paragraph)
- Any explicitly referenced file paths, feature names, or symbol names
- Any `#N` issue references

**Step B: Verify every follow-up claim resolves to an OPEN issue.**

For each disclosure that claims a follow-up was filed (`filed as follow-up #N`, `tracked by #N`, `Follow-up: #N`, etc.):

```bash
gh issue view N --json number,state,title 2>/dev/null
```

- If the issue exists and state is `OPEN`: the disclosure is verified. Record it as `acknowledged`.
- If the issue is `CLOSED`, does not exist, or the claim references no issue number at all: this becomes a **real Tech Debt finding** with description "Claimed follow-up for [disclosure text] does not resolve to an open tracking issue." Do not classify it as acknowledged.

If the disclosure does not claim a follow-up (e.g., a pure "out of scope" exclusion with no tracking commitment), record it as `acknowledged` with `tracked=false` — note this in the review body but do not escalate to Tech Debt.

**Step C: Use disclosures to classify candidate findings.**

When analyzing the diff in Step 3 and Plan Validation in Step 4, a candidate finding is classified `acknowledged` (not `tech_debt`, not `blocker`) when **all** of the following hold:

1. The finding's file path, feature name, or symbol matches a disclosure (by exact path, substring feature-name match, or `#N` correspondence with the plan/issue).
2. The disclosure's follow-up claim resolves to an OPEN issue (verified in Step B), OR the disclosure is an explicit "out of scope" exclusion with sound rationale.
3. The finding is **not** a violation of an explicit plan `## No-Gos` item (No-Gos override disclosures — if the PR author disclosed doing something the plan explicitly excluded, that is still a blocker).

**Step D: Record disclosure results.**

Emit a `Disclosures` section for use in the review body (a separate section from `Tech Debt`):

```markdown
### Acknowledged Deferrals (verified)
- **[disclosure text]** — tracked by #N (OPEN) — matched findings: [file path, if any]
- **[disclosure text]** — explicit out-of-scope exclusion, no tracking required
```

Findings marked `acknowledged` appear in this section only — they MUST NOT appear in the `Tech Debt` bucket.

### 2.6. Prior Review Context (mandatory, runs BEFORE findings)

Every invocation reads its own prior `## Review:` comments on the PR so repeated passes are idempotent on unchanged inputs, and so calibration drift across runs is visible rather than silent.

**Step A: Fetch prior reviews.**

```bash
REPO="${SDLC_REPO:-${GH_REPO:-}}"
PRIOR_REVIEWS=$(gh api repos/$REPO/issues/$PR_NUMBER/comments \
  --jq '[.[] | select(.body | startswith("## Review:")) | {body: .body, created_at: .created_at, id: .id}]')
```

Also fetch prior formal reviews (for repos where `gh pr review --request-changes` is usable):

```bash
PRIOR_FORMAL_REVIEWS=$(gh api repos/$REPO/pulls/$PR_NUMBER/reviews \
  --jq '[.[] | select(.body | startswith("## Review:")) | {body: .body, submitted_at: .submitted_at, id: .id}]')
```

If both lists are empty, skip the remaining sub-steps and proceed to Step 3.

**Step B: Identify the most recent prior review.**

Pick the most recent comment/review across both lists by timestamp. Extract from its body:

- The embedded HEAD SHA (if present — see Step D of this section; prior reviews emit `<!-- REVIEW_CONTEXT head_sha=... pr_body_hash=... -->`)
- The prior verdict (from the `## Review: <verdict>` heading)
- The prior findings lists (Blockers, Tech Debt, Nits, Acknowledged Deferrals)

**Step C: Idempotency check.**

Compute a hash of the current PR body for comparison:

```bash
PR_BODY_HASH=$(gh pr view $PR_NUMBER --json body --jq .body | shasum -a 256 | awk '{print $1}')
```

If the prior review's embedded `head_sha` equals the current `$HEAD_SHA` **and** the prior review's embedded `pr_body_hash` equals the current `$PR_BODY_HASH`:

- Return the prior verdict unchanged
- Do NOT regenerate findings
- Emit a short note in the review body: `_Idempotent: prior review on HEAD ${HEAD_SHA:0:7} / body hash ${PR_BODY_HASH:0:7} is still valid._`
- Skip to the Post Review sub-skill with the prior findings

If either differs (new commits or new body text), proceed to Step 3 to generate a fresh review.

**Step D: Continuity log.**

When generating a fresh review (inputs changed), compare this run's findings against the prior review's findings after Step 6 classification. Surface discrepancies explicitly in the review body:

```markdown
### Review Delta (vs prior review on HEAD {prior_sha:0:7})
- **Resolved**: [finding from prior review that is no longer present because [reason]]
- **New**: [finding not present in prior review]
- **Unchanged**: [finding carried forward from prior review]
```

If no prior review exists, omit the Review Delta section.

**Step E: Embed context markers in this review's body.**

Every review body emitted by this skill MUST include a trailing HTML-comment marker so the next invocation can detect idempotency:

```markdown
<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

Post the marker at the end of the review body, before the OUTCOME block.

### 3. Analyze the Diff

For each changed file, evaluate:

- **Correctness**: Does the code do what the plan/PR description says?
- **Security**: No secrets, injection vulnerabilities, or unsafe patterns
- **Error handling**: Appropriate error handling at system boundaries
- **Tests**: Are new features covered by tests? Do existing tests pass?
- **Code quality**: Follows project patterns, no unnecessary complexity
- **Documentation**: Are docs updated for user-facing changes?

Check for common issues:
- Leftover debug code (`print()`, `console.log()`, `TODO`)
- Missing error handling for external calls
- Hardcoded values that should be configurable
- Breaking changes without migration path

### 4. Plan Validation (if plan exists)

For each requirement/acceptance criterion in the plan:
1. Locate the corresponding implementation in the PR diff
2. Verify behavior matches the plan specification
3. Check that edge cases mentioned in the plan are handled
4. Verify any "No-Gos" from the plan are respected. No-Gos override disclosures — a disclosed "deferral" that actually violates a plan No-Go is a `blocker`, not an `acknowledged` finding.

If a plan acceptance criterion is not addressed in the diff but matches a verified disclosure from Step 2.5, classify that criterion as `acknowledged` rather than as a blocker. Record it in the `Acknowledged Deferrals (verified)` section.

#### 4b. Plan Checkbox Validation

Walk each unchecked `- [ ]` item in the following plan sections:
- **Acceptance Criteria** / **Success Criteria** -- severity: BLOCKER if unaddressed
- **Test Impact** -- severity: WARNING if unaddressed
- **Documentation** -- severity: WARNING if unaddressed
- **Update System** -- severity: WARNING if unaddressed

For each unchecked item:
1. Assess whether the PR diff addresses it (even if the checkbox is not checked in the plan)
2. If addressed by the diff: silently pass (do not report)
3. If NOT addressed by the diff: report with the appropriate severity

Report format for unaddressed items:
```
**Unaddressed plan item** (BLOCKER|WARNING):
  Section: [section name]
  Item: [checkbox text]
  Assessment: [why the diff does not address this]
```

Only report items that are genuinely unaddressed. False positives are worse than missed items.

### 5. Run Verification Checks (if plan has ## Verification table)

```bash
python -c "
from agent.verification_parser import parse_verification_table, run_checks, format_results
from pathlib import Path
plan = Path('${SDLC_PLAN_PATH}').read_text()
checks = parse_verification_table(plan)
if checks:
    results = run_checks(checks)
    print(format_results(results))
else:
    print('No verification table in plan.')
"
```

### Pre-Verdict Checklist

Before writing the verdict, evaluate each of the following items. Every item must receive a `PASS`, `FAIL`, or `N/A` verdict. No blank entries are allowed. Items marked `FAIL` automatically become findings.

Every item MUST have a verdict. Blank verdicts invalidate the review.

Emit the completed checklist as a bulleted list in the review comment. Format: `- **N. Item name** — PASS/FAIL/N/A — *notes*`. Keep one line per item.

```markdown
## Pre-Verdict Checklist

- **1. All plan acceptance criteria checked against diff** — PASS/FAIL/N/A — *notes*
- **2. No-Gos from plan — none violated** — PASS/FAIL/N/A — *notes*
- **3. New `except Exception` blocks — each has logger/raise/swallow-ok** — PASS/FAIL/N/A — *notes*
- **4. New integration tests — exercise serialization boundary (not in-memory only)** — PASS/FAIL/N/A — *notes*
- **5. Plan internal consistency — spike findings match task steps** — PASS/FAIL/N/A — *notes*
- **6. No hardcoded secrets or debug artifacts** — PASS/FAIL/N/A — *notes*
- **7. New public APIs — docstrings present** — PASS/FAIL/N/A — *notes*
- **8. Breaking changes — migration path documented** — PASS/FAIL/N/A — *notes*
- **9. Tests added for new behavior** — PASS/FAIL/N/A — *notes*
- **10. Tests cover the failure path (not just happy path)** — PASS/FAIL/N/A — *notes*
- **11. UI changes (if any) — screenshot captured** — PASS/FAIL/N/A — *notes*
- **12. Docs updated for user-facing changes** — PASS/FAIL/N/A — *notes*
```

An "Approved" verdict requires all 12 items evaluated (no blank verdicts). Items that do not apply to this PR should be marked `N/A` with a note. An "Approved" verdict with one or more `FAIL` items is not valid — `FAIL` items must be promoted to findings.

The Pre-Verdict Checklist remains the high-signal filter for common calibration bugs (unguarded `except Exception`, in-memory-only integration tests, missing docstrings, etc.). The Rubric below adds structured, mechanically-derived verdict logic on top — the two are complementary, not redundant.

### Rubric

After completing the Pre-Verdict Checklist, evaluate the following 10-item rubric. Each item produces exactly one of: `pass`, `fail`, `acknowledged`, or `n/a`. The verdict is then **mechanically derived** from the rubric results — not from freeform LLM judgment.

| Status | Meaning |
|--------|---------|
| `pass` | The rubric item is satisfied by this PR |
| `fail` | The rubric item is not satisfied — this produces a finding |
| `acknowledged` | The item is not satisfied, but the shortfall is covered by a verified disclosure from Step 2.5 |
| `n/a` | The rubric item does not apply to this PR (e.g., no new public APIs means doc coverage of new APIs is n/a) |

**The 10 rubric items:**

1. **Plan vs. implementation match** — every plan acceptance criterion is either delivered in the diff or covered by an acknowledged deferral. Critical.
2. **New code quality** — type hints where the project uses them, meaningful names, error handling at system boundaries, no copy-paste blocks. Critical.
3. **Test coverage** — every new public function / code path has a corresponding test; pre-existing tests still pass. Critical.
4. **Regression risk to existing callers** — grep callers of changed functions; if a caller's behavior could silently change, it is covered by a test or the change is backward-compatible. Critical.
5. **Data integrity** — migrations are present for schema changes; `update_fields=[...]` lists include `modified_at` when the model has `auto_now`; JSON field additions do not break existing row reads. Critical.
6. **Security** — no new `mark_safe`, `raw()`, `eval`, `exec`, `subprocess` with request-derived input, or SQL string interpolation. No hardcoded secrets. Critical.
7. **Documentation accuracy** — docs updated for user-facing or architecturally-visible changes; no stale references to removed code. Non-critical.
8. **PR body accuracy** — claims in the PR body (file counts, test counts, migrations listed, disclosures) match the actual diff. Non-critical.
9. **Disclosed deferrals** — every disclosure from Step 2.5 either (a) has an OPEN tracking issue, or (b) is an explicit out-of-scope exclusion with sound rationale. Critical.
10. **Follow-up claims verified** — every `filed as follow-up #N` / `tracked by #N` claim in the PR body resolves to an OPEN GitHub issue. Critical.

**Emit the rubric as a checklist in the review body:**

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

Check the box only for items marked `pass` or `n/a`. Leave `fail` and `acknowledged` items unchecked so they remain visible.

**Verdict derivation (mechanical):**

Apply these rules in order. Each rule produces a verdict; stop at the first rule that matches.

1. **Any critical-item `fail` (items 1, 2, 3, 4, 5, 6, 9, 10) AND no matching `acknowledged`** → `CHANGES REQUESTED — Blocker`. The `fail` items become blocker findings.
2. **Any non-critical-item `fail` (items 7, 8)** → `CHANGES REQUESTED — Tech Debt`. The `fail` items become tech_debt findings.
3. **Any `fail` in the Miscellaneous bucket (see below) regardless of severity** → derived per the finding's own severity: `blocker` → Rule 1's verdict; `tech_debt` or `nit` → Rule 2's verdict.
4. **All rubric items are `pass`, `acknowledged`, or `n/a`, and Miscellaneous bucket is empty** → `APPROVED`.

The verdict is NOT freeform — it is derived entirely from the rubric and miscellaneous findings. A reviewer who wants to flag something that doesn't fit the rubric puts it in the Miscellaneous bucket (below), where it still feeds the mechanical verdict.

### Miscellaneous Bucket

The rubric is intentionally small and stable. Real PRs sometimes surface issues that don't fit any of the 10 items — legacy code smells, unusual architectural patterns, domain-specific gotchas, upstream integration risks. These MUST NOT be forced into the rubric.

Instead, emit them in a separate **Miscellaneous** section. Each Miscellaneous finding still uses the finding format (File / Code / Issue / Severity / Fix) and still feeds the mechanical verdict — but it is categorized outside the 10 rubric items.

```markdown
### Miscellaneous
- **File:** `path/to/file.py:42`
  **Code:** `actual_code()`
  **Issue:** [description of the issue that doesn't fit any rubric item]
  **Severity:** blocker | tech_debt | nit
  **Fix:** [suggested fix]
```

If the bucket is empty, emit `### Miscellaneous\n- None` — do not omit the heading.

**Do not** use Miscellaneous as a dumping ground for findings the reviewer is uncertain about. A finding belongs in Miscellaneous only when it is real *and* does not map to a rubric item. When in doubt, prefer the rubric.

### 6. Classify Findings

**Severity Guidelines:**

- **blocker**: Must fix before merge (breaks functionality, security issue, data loss risk)
- **tech_debt**: Fix before merge, patched by `/do-patch` (code quality, missing edge case tests)
- **nit**: Fix before merge unless purely subjective (style, naming, docs wording)
- **acknowledged**: Matches a verified disclosure from Step 2.5 — NOT a blocker or tech_debt. Appears in the `Acknowledged Deferrals (verified)` section, never in `Tech Debt`.

For every `blocker`, `tech_debt`, or `nit` finding you MUST emit exactly this block, with every field present. A finding missing any field is invalid and MUST be dropped, not shortened:
```
**File:** `path/to/file.py:42` (verified: read this file)
**Code:** `the_actual_code_on_that_line()`
**Issue:** [clear description of the problem]
**Severity:** blocker | tech_debt | nit
**Fix:** [suggested fix]
```

**Empty-section rule:** If a severity category has zero findings, emit the heading with an explicit empty marker (`### Blockers\n- None`, `### Tech Debt\n- None`, `### Nits\n- None`, `### Miscellaneous\n- None`, `### Acknowledged Deferrals (verified)\n- None`). Do NOT omit the heading.

**Disclosure separation rule:** Findings classified as `acknowledged` via Step 2.5 MUST NOT appear in Blockers, Tech Debt, or Nits. They appear in the dedicated `Acknowledged Deferrals (verified)` section so human reviewers can audit that the author's disclosures are honest.

### 7. Verify All Findings

Before reporting, verify every blocker and tech_debt finding:
1. Confirm the file exists (you must have read it)
2. Confirm the code exists at or near the cited line
3. Confirm your description of the problem is accurate

Drop any finding that fails verification. A false blocker is worse than a missed issue.

## Completion

Return the following artifacts for the Post Review sub-skill to consume:

1. The Pre-Verdict Checklist (12 items) with verdicts.
2. The Rubric (10 items) with pass/fail/acknowledged/n/a per item.
3. Classified findings (Blockers, Tech Debt, Nits).
4. The Miscellaneous bucket (if non-empty, else `- None`).
5. Acknowledged Deferrals (verified) section from the Disclosure Parser.
6. Review Delta (if a prior review existed on a different HEAD SHA or body hash).
7. The verification results (if any).
8. The mechanically-derived verdict per the Rubric's verdict derivation rules.
9. The `<!-- REVIEW_CONTEXT head_sha=... pr_body_hash=... -->` marker for the Post Review sub-skill to embed in the review body.

If the Prior Review Context idempotency check fired (same HEAD SHA, same PR body hash), return the prior verdict and findings unchanged and flag the review as idempotent so Post Review can short-circuit.
