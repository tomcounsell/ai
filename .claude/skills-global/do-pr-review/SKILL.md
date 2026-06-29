---
name: do-pr-review
description: "Use when reviewing a pull request. Analyzes code changes, validates against plan requirements, and captures visual proof via screenshots. Triggered by 'review this PR', 'check the pull request', 'do a PR review', or a PR URL."
context: fork
allowed-tools: mcp__byob__*, Bash(gh:*), Bash(git:*), Bash(python:*), Bash(jq:*), Bash(sdlc-tool:*), Read, Write, Edit, Grep, Glob
---

# PR Review

Review a pull request by analyzing its changes against the plan, checking code quality, validating tests, and capturing screenshots of UI changes.

## Repo Context Probe

If `docs/sdlc/do-pr-review.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo layers its review automation onto this generic baseline: a bot/service-account review identity, SDLC env-var injection, stage/status markers and a verdict-recording substrate, cross-repo `gh` targeting, a verification-table runner, multi-judge and cross-vendor consensus, a shape classifier, and repo-specific gates (docs, plan-section compliance, lint). When the file is absent (the common case in a foreign repo), this skill runs entirely on `git`, `gh`, the Read/Grep tools, and a browser MCP for screenshots: it gathers PR context, runs the mergeability preflight, reviews the diff against the plan, captures UI screenshots, classifies findings, and posts a single review verdict to GitHub under the operator's `gh` credential — no repo-specific tooling required.

Throughout, any action described as "if the context file declares X" is skipped in the generic case.

## Surface

Screenshot capture in this skill runs against a browser MCP
(`mcp__byob__browser_*`) — by default the user's real, logged-in Chrome, so
authenticated staging URLs are screenshotted the same way the user would see
them. There is no anonymous-headless fallback in that surface.

If the context file declares a real-Chrome session requirement (a flag the
calling session must set, and how to request it), honor it — concurrent
real-Chrome sessions can race on the active tab. In the generic case, drive the
browser MCP directly.

## Review Identity

**Generic default:** post the review under the operator's `gh` credential.

If the context file declares a bot/service-account review identity (a dedicated
token for pipeline-driven reviews, an injected `GH_TOKEN` for the posting
subprocess, a machine-readable review marker, and branch-protection
configuration so the bot's approval doesn't satisfy human-review gates), follow
its rules for the single review-posting subprocess only. All read-only `gh`
queries always use the operator's credential.

## Cross-Repo Resolution

By default `gh` targets the repository of the current working directory. If the context file declares a cross-repo targeting mechanism (e.g. a `GH_REPO` env var), honor it so `gh` commands hit the intended repository.

## When to Use

- After `/do-build` creates a PR
- When a PR needs thorough review before merge
- To validate UI changes visually with screenshots
- To generate structured review reports with issue severity classification

## Variables

- `pr_number` (required): The PR number to review (e.g., `42` or `#42`)

## SDLC Context Variables (only if the context file declares them)

If the repo runs this skill inside an SDLC pipeline, the context file may declare
pre-resolved environment variables (PR number, branch, slug, plan path, tracking
issue, repo). When present, prefer them and fall back to the manual resolution in
§ 1. In the generic case, resolve everything from `$ARGUMENTS` and `gh`.

## Sub-Skills

This skill is decomposed into focused sub-skills in `sub-skills/`:
- `checkout.md` — Mechanical: **mergeability preflight (runs first)**, clean git state, checkout PR branch
- `code-review.md` — Judgment: parse PR-body disclosures, read prior reviews, traverse the 10-item Rubric, evaluate the 12-item Pre-Verdict Checklist, classify findings, derive the verdict mechanically
- `screenshot.md` — Mechanical: start app, capture UI screenshots
- `post-review.md` — Mechanical: format findings, post review to GitHub

Each sub-skill has a single responsibility and receives pre-resolved context.

**Determinism note:** `code-review.md` includes a disclosure parser (pre-finding), a prior-review context loader (idempotency on unchanged HEAD SHA + body), an explicit 10-item Rubric with pass/fail/acknowledged/n/a per item, a Miscellaneous bucket for issues outside the rubric, and mechanical verdict derivation. These were introduced in issue #1045 to address non-deterministic verdicts across repeated runs on the same PR.

## Mergeability Preflight (first action, before anything else)

Before reading the diff, loading the plan, or running any code review, run the
mergeability preflight (see `sub-skills/checkout.md` → "Mergeability Preflight").
It is cheap (one `gh pr view` API call) and catches objective blockers that
make any subjective code review meaningless:

- If `state != OPEN` → emit `PR_CLOSED` verdict and stop.
- If `mergeable == CONFLICTING` or `mergeStateStatus == DIRTY` → emit
  `BLOCKED_ON_CONFLICT` verdict, cite the `mergeStateStatus`, ask for a rebase,
  and stop.
- If `mergeStateStatus == BEHIND` → note it and proceed (branch needs update
  but has no conflicts).
- Otherwise → proceed with full code review.

This preflight is complementary to the subjective rubric added by #1045: #1045
catches reviews that APPROVED a PR without the reviewer actually evaluating
acceptance criteria; this preflight (#1112) catches reviews that APPROVED a PR
that mechanically cannot merge.

## Stage Marker (only if the context file declares a substrate)

If the context file declares a stage-marker substrate, write the REVIEW
`in_progress` marker at the start (after § 1 resolves the issue number) and
follow its degraded-mode handling. The review itself depends only on `gh` and
the diff, never the substrate. The REVIEW completion marker is written ONLY on
the APPROVED path, co-located in the same block as the APPROVED verdict record
(see "Record the verdict") so the marker can never desync from the verdict. On a
findings (`CHANGES REQUESTED`) verdict, leave the marker `in_progress` — the
dispatcher re-runs review after `/do-patch`.

In the generic case (no substrate declared), skip stage markers entirely.

## Goal Alignment

Every PR review must be grounded in the original intent. Before reviewing code, find and read the plan and tracking issue to understand *what was supposed to be built and why*.

**How to get plan context** (in priority order):
1. Check PR body for `Closes #N` — fetch the issue via `gh issue view N`
2. Extract slug from branch name (e.g., `session/{slug}`) and read `docs/plans/{slug}.md`
3. Look for plan docs in `docs/plans/` that reference the issue number
4. If no plan exists (e.g., hotfix), review against the PR description alone

When plan context is available, the review should validate that implementation matches the plan's:
- Acceptance criteria and success conditions
- No-Gos (things explicitly excluded)
- Architectural decisions and patterns

## Instructions

Follow this review process to validate a pull request:

### 1. PR Context Gathering

**Resolve context variables** (prefer env vars, fall back to manual resolution):
```bash
PR_NUMBER="${SDLC_PR_NUMBER:-$PR_NUMBER}"
REPO="${SDLC_REPO:-${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}}"
PLAN_PATH="${SDLC_PLAN_PATH:-}"
SLUG="${SDLC_SLUG:-}"

# If PLAN_PATH not set, derive from slug or branch
if [ -z "$PLAN_PATH" ] && [ -n "$SLUG" ]; then
  PLAN_PATH="docs/plans/${SLUG}.md"
fi

# Resolve ISSUE_NUMBER — unconditional clobber (never ${ISSUE_NUMBER:-…}).
# IMPORTANT: $ARGUMENTS is the PR number for this skill, NOT the issue number.
# Do NOT use $ARGUMENTS as ISSUE_NUMBER. Do NOT use $SDLC_ISSUE_NUMBER as
# authoritative — a stale inherited env value is exactly the "latched onto
# wrong issue" mechanism this skill must guard against (#1731).
#
# Resolution order (first non-empty positive integer wins):
# 1. PR body extraction: Closes #N / Fixes #N / Resolves #N  (PRIMARY — always run)
# 2. PR body: tracking: https://.../issues/N  (secondary PR-body fallback)
# 3. $SDLC_ISSUE_NUMBER env var (LAST RESORT ONLY — guarded by positive-integer check)
PR_BODY=$(gh pr view "$PR_NUMBER" --json body -q '.body' 2>/dev/null)
ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oiP '(?:closes|fixes|resolves)\s+#\K[0-9]+' | head -1)
if [ -z "$ISSUE_NUMBER" ]; then
  # Also try "tracking: https://.../issues/N" pattern
  ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oP '(?<=issues/)[0-9]+' | head -1)
fi
if [ -z "$ISSUE_NUMBER" ] && [[ "$SDLC_ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  ISSUE_NUMBER="$SDLC_ISSUE_NUMBER"
fi

# Assert ISSUE_NUMBER is a positive integer before any recorder call (#1731).
# An unresolvable issue number must fail loudly so the supervisor sees an
# actionable error rather than a silently diverted verdict on a wrong session.
[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || {
  echo "do-pr-review: could not resolve a positive-integer ISSUE_NUMBER from ARGUMENTS='${ARGUMENTS}', PR body, or SDLC_ISSUE_NUMBER. Pass the issue number as skill args or ensure the PR body contains 'Closes #N'." >&2
  exit 1
}
```

**Run the mergeability preflight FIRST** (see `sub-skills/checkout.md` →
"Mergeability Preflight" for the decision table and short-circuit behavior):

```bash
PREFLIGHT_JSON=$(gh pr view "$PR_NUMBER" --json mergeable,mergeStateStatus,state)
PR_STATE=$(echo "$PREFLIGHT_JSON" | jq -r '.state')
PR_MERGEABLE=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeable')
PR_MERGE_STATUS=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeStateStatus')

# Retry once if GitHub has not finished computing mergeability
if [ "$PR_MERGEABLE" = "UNKNOWN" ]; then
  sleep 2
  PREFLIGHT_JSON=$(gh pr view "$PR_NUMBER" --json mergeable,mergeStateStatus,state)
  PR_STATE=$(echo "$PREFLIGHT_JSON" | jq -r '.state')
  PR_MERGEABLE=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeable')
  PR_MERGE_STATUS=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeStateStatus')
fi
```

Apply the decision table from `sub-skills/checkout.md`:
- `state != OPEN` → post `PR_CLOSED` comment, emit `status:"fail"` OUTCOME with
  `verdict:"PR_CLOSED"`, exit immediately. Do NOT checkout, read diff, or
  produce a code review body.
- `mergeable == CONFLICTING` OR `mergeStateStatus == DIRTY` → post
  `BLOCKED_ON_CONFLICT` comment citing the `mergeStateStatus`, emit
  `status:"fail"` OUTCOME with `verdict:"BLOCKED_ON_CONFLICT"`, exit
  immediately.
- `mergeable == "UNKNOWN"` after retry → treat conservatively as `CONFLICTING`:
  emit `BLOCKED_ON_CONFLICT` verdict, post `gh pr comment` only, and stop. Do
  NOT post a code review when mergeability is unresolved. This prevents approving
  a PR that GitHub hasn't finished evaluating.
- Otherwise (including `BEHIND`, `UNSTABLE`, `HAS_HOOKS`, `CLEAN`) → proceed to
  the checkout and full review below.

**Fetch PR details:**
```bash
gh pr view $PR_NUMBER --json title,body,headRefName,baseRefName,files,additions,deletions
```

**Checkout the PR branch (mandatory before any file reads):**
```bash
# Ensure clean git state before switching branches (abort any in-progress
# merge/rebase, stash uncommitted changes). If the context file declares a
# clean-git-state helper, use it instead.
git merge --abort 2>/dev/null; git rebase --abort 2>/dev/null; git stash --include-untracked 2>/dev/null

gh pr checkout $PR_NUMBER
```
This ensures all subsequent `Read` calls see the PR's actual code, not whatever branch the parent conversation had checked out. Without this, the reviewer reads stale files that contradict the diff — producing hallucinated findings.

> **Worktree isolation:** When spawning this skill via the Agent tool, use `isolation: "worktree"` so the checkout doesn't disrupt the parent conversation's working directory.

**Get the full diff:**
```bash
gh pr diff $PR_NUMBER
```

**Get changed files:**
```bash
gh pr diff $PR_NUMBER --name-only
```

**Find and read the associated plan and issue:**
- Check PR body for `Closes #N` — run `gh issue view N` to get the tracking issue context
- Extract slug from the head branch name and read `docs/plans/{slug}.md`
- The plan contains acceptance criteria, no-gos, and requirements to validate against
- Keep the plan summary in mind throughout the entire review

### 2. Code Review

**Analyze the diff for:**

- **Correctness**: Does the code do what the plan/PR description says?
- **Security**: No secrets, injection vulnerabilities, or unsafe patterns
- **Error handling**: Appropriate error handling at system boundaries
- **Tests**: Are new features covered by tests? Do existing tests still pass?
- **Code quality**: Follows project patterns, no unnecessary complexity
- **Documentation**: Are docs updated for user-facing changes?

**Check for common issues:**
- Leftover debug code (`print()`, `console.log()`, `TODO`)
- Missing error handling for external calls
- Hardcoded values that should be configurable
- Breaking changes without migration path

### 3. Screenshot Capture (if UI changes detected)

**Determine if screenshots needed:**

Check the changed file list for UI-related extensions and patterns:
- `*.html`, `*.htm` — HTML templates
- `*.jsx`, `*.tsx` — React components
- `*.vue` — Vue single-file components
- `*.css`, `*.scss`, `*.sass`, `*.less` — Stylesheets
- `*.js`, `*.ts` — JavaScript/TypeScript (when the file is under a `ui/`, `frontend/`, `static/`, `assets/`, `templates/`, or `components/` directory)
- Django/Jinja template files (`.html` under `templates/`)
- Any file whose path contains `ui/`, `frontend/`, `static/`, `web/`, `assets/`, or `templates/`

**Set the UI gate flag early:**

```bash
UI_FILES=$(gh pr diff $PR_NUMBER --name-only | grep -E '\.(html|htm|jsx|tsx|vue|css|scss|sass|less)$|/(ui|frontend|static|web|assets|templates)/' || true)
UI_CHANGES_DETECTED=false
SCREENSHOTS_CAPTURED=0

if [ -n "$UI_FILES" ]; then
  UI_CHANGES_DETECTED=true
  echo "UI files changed — visual proof required before approval:"
  echo "$UI_FILES"
fi
```

**If no UI files changed:** skip this step entirely. The screenshot gate is a no-op — `SCREENSHOTS_CAPTURED=0` is fine and approval is not blocked.

**If UI files changed (`UI_CHANGES_DETECTED=true`):**

```bash
# Prepare screenshot directory
mkdir -p generated_images/pr-$PR_NUMBER

# PR branch was already checked out in Step 1.
# Start the app using the repo's '## Running' README section, then capture via BYOB MCP:
# (replace bash with mcp__byob__browser_* tool calls)
#   mcp__byob__browser_navigate(url="http://localhost:8000", waitUntil="networkidle")
#   mcp__byob__browser_read(url="http://localhost:8000", reuseTab=true, screens=1)
#   mcp__byob__browser_screenshot(tabId=<tab>, savePath="generated_images/pr-$PR_NUMBER/01_main_view.png")

# After each successful screenshot, increment the counter:
# SCREENSHOTS_CAPTURED=$((SCREENSHOTS_CAPTURED + 1))
```

**Screenshot naming convention:**
- `01_main_view.png` - Primary affected view
- `02_feature_demo.png` - New feature in action
- `03_edge_case.png` - Edge case or error state

**Visual proof gate (evaluated after this step, before posting any approval):**

If `UI_CHANGES_DETECTED=true` AND `SCREENSHOTS_CAPTURED=0`, the review MUST NOT approve.
Set a gate failure flag:

```bash
VISUAL_PROOF_GATE_FAILED=false
if [ "$UI_CHANGES_DETECTED" = "true" ] && [ "$SCREENSHOTS_CAPTURED" -eq 0 ]; then
  VISUAL_PROOF_GATE_FAILED=true
  echo "VISUAL PROOF GATE FAILED: UI files were changed but no screenshots were captured."
  echo "This PR cannot be approved without visual proof of the UI changes."
  echo "Posting 'Request Changes' verdict with a note to capture screenshots."
fi
```

This flag is consumed in Step 6: if `VISUAL_PROOF_GATE_FAILED=true`, override any otherwise-clean verdict to `CHANGES_REQUESTED` and add a **blocker** finding documenting the missing visual proof. The blocker text should name the specific UI files that changed and explain that at least one screenshot is required before this PR can be approved.

### 4. Plan Validation (if plan exists)

If a plan document was found in step 1:

For each requirement/acceptance criterion in the plan:
1. Locate the corresponding implementation in the PR diff
2. Verify behavior matches the plan specification
3. Check that edge cases mentioned in the plan are handled
4. Verify any "No-Gos" from the plan are respected. For every `[DESTRUCTIVE]` or
   `[SEPARATE-SLUG]` No-Go (assertable No-Gos — those describing a forbidden code-level
   outcome), confirm that a corresponding `## Verification` anti-criterion row exists.
   If a clearly assertable No-Go has no inverse Verification row, flag it as a
   non-blocking advisory item (not a hard gate — anti-criteria are opt-in per author).
   `[EXTERNAL]` and `[ORDERED]` No-Gos are genuinely advisory; no anti-criterion row
   is required for them. Also confirm that the PR description contains the **pasted
   red-state FAIL output** for each authored anti-criterion (evidence that the author
   exercised the row against a deliberately-violating input before trusting it). A
   missing red-state paste is a non-blocking advisory; the binding gate is the green
   Step 4.5 Verification run.
5. If the plan has an Agent Integration section, verify integration points exist in the codebase (e.g., grep for expected tool calls, imports, or MCP references)

### 4.5. Verification Checks (if plan has ## Verification table)

If the plan document has a `## Verification` section with a machine-readable table, run each check on the PR branch. Generic baseline: read the table, run each `Command`, and compare against its `Expected` column. If the context file declares a verification-table runner, use it instead.

Include the verification results in the review comment under a "Verification Results" section. If any check fails, classify it as a **blocker**.

### 5. Issue Identification & Classification

**Severity Guidelines:**

- **blocker**: Must fix before merge
  - Breaks core functionality
  - Security vulnerability
  - Data loss risk
  - Missing tests for critical paths
  - Crashes or severe errors

- **tech_debt**: Fix before merge (patched automatically by `/do-patch`)
  - Code quality issues
  - Missing tests for edge cases
  - Performance improvements
  - Refactoring opportunities
  - These are NOT optional — the SDLC pipeline will invoke `/do-patch` to fix them

- **nit**: Fix before merge unless purely subjective (patched automatically by `/do-patch`)
  - Style/formatting
  - Minor naming improvements
  - Documentation wording
  - Future enhancements
  - Only skip nits that are genuinely subjective (e.g., naming preference) — requires human approval

**For every issue found you MUST emit exactly this block, with every field present. A finding missing any field is invalid and MUST be dropped, not shortened:**

```
**File:** `path/to/file.py:42` (verified: read this file)
**Code:** `the_actual_code_on_that_line()`
**Issue:** [clear description of the problem]
**Severity:** blocker | tech_debt | nit
**Fix:** [suggested fix]
```

The `Code:` field MUST be a verbatim quote from the file, not paraphrased. The `File:` path MUST be a file you read with the Read tool during this review. If you cannot produce both of these, do not include the finding.

**Empty-section rule (MANDATORY):** If a severity category has zero findings, you MUST still emit the heading with an explicit empty marker — `### Blockers\n- None`, `### Tech Debt\n- None`, `### Nits\n- None`. Do NOT omit the heading. Downstream parsing and the three-tier decision tree in Step 6 depend on every category appearing.

### 5.5. Verify Findings (mandatory)

Before posting, verify every blocker and tech_debt finding:

1. **Confirm the file exists** — you must have read it with the Read tool during this review
2. **Confirm the code exists** — the function, class, or pattern you're citing must appear in the file at or near the line you reference
3. **Confirm the behavior** — re-read the relevant code to make sure your description of the problem is accurate

**If a finding fails verification** (file doesn't exist, function not found, behavior described doesn't match actual code):
- **Drop it entirely.** Do not include unverified findings in the review.
- A false blocker is worse than a missed real issue — it wastes time and erodes trust.

This step exists because of issue #181: a prior review hallucinated two "blocker" findings citing functions and files that did not exist.

### 5.6. Legacy Cruft Audit

Run the cruft auditor on the PR diff to identify legacy patterns (deprecated fields
still read/written, fallback chains, dual implementations, dead imports, stale comments).

Include any findings as a "Legacy Cruft" subsection in the review output.
Cruft findings are advisory, not blockers. See `.claude/agents/cruft-auditor.md`.

### 6. Post Review

**Visual proof gate check (before posting):**

If `VISUAL_PROOF_GATE_FAILED=true` (set in Step 3), inject a blocker finding
regardless of the code-review verdict:

```
**File:** `(PR diff — UI files without visual proof)`
**Code:** `(see UI_FILES list from Step 3)`
**Issue:** UI changes detected but no BYOB screenshots were captured. Visual
proof is required before this PR can be approved. At least one screenshot of
the affected UI must be included in the review.
**Severity:** blocker
**Fix:** Start the app, navigate to the affected page(s) via BYOB MCP, capture
at least one screenshot with mcp__byob__browser_screenshot, and re-run the
review.
```

This blocker is real and counts toward the verdict: the review MUST post as
`CHANGES_REQUESTED`, not `APPROVED`, regardless of other findings.

If a `sub-skills/post-review.md` exists, it is the **single source of truth** for
the review-post decision tree. The decision tree, in any configuration:

- Preflight short-circuit paths (`BLOCKED_ON_CONFLICT`, `PR_CLOSED`) → `gh pr comment` only
- Self-authored PR detection → `gh pr comment` fallback
- Normal code-review paths (blockers / tech_debt / zero findings) → `gh pr review`
- Bot-identity token injection and a review marker → **only if the context file declares them** (see Review Identity)

Generic baseline: post `gh pr review --approve` for a clean review, or
`gh pr review --request-changes` when findings exist, under the operator's `gh`
credential.

### 6.5. Verify Review Was Posted

**Always verify the review or comment exists after posting:**
```bash
# Check for formal reviews
REVIEW_COUNT=$(gh api repos/$REPO/pulls/$PR_NUMBER/reviews --jq length)

# Check for comments (used for self-authored PRs)
COMMENT_COUNT=$(gh api repos/$REPO/issues/$PR_NUMBER/comments --jq '[.[] | select(.body | startswith("## Review:"))] | length')

if [ "$REVIEW_COUNT" -eq 0 ] && [ "$COMMENT_COUNT" -eq 0 ]; then
  echo "WARNING: Review was not posted. Retrying as comment..."
  gh pr comment $PR_NUMBER --body "$REVIEW_BODY"
fi
```

**After verification, fetch the review URL:**
```bash
# Try formal review URL first
REVIEW_URL=$(gh api repos/$REPO/pulls/$PR_NUMBER/reviews --jq '.[-1].html_url // empty')

# Fall back to comment URL
if [ -z "$REVIEW_URL" ]; then
  REVIEW_URL=$(gh api repos/$REPO/issues/$PR_NUMBER/comments --jq '.[-1].html_url // empty')
fi
```
Save this URL as `{review_url}` for the output summary.

### 6.6. Record the verdict (only if the context file declares a substrate)

In the generic case (no substrate declared) the posted GitHub review IS the
verdict — skip this step.

If the context file declares a verdict-recording substrate (so a pipeline router
can consume the verdict programmatically), you MUST record the verdict **here,
before emitting the OUTCOME block**. Recording is a terminal, non-optional action,
not a trailing nicety. A locally-run pipeline (e.g. `/do-sdlc`) has no hooks to
record on your behalf: if you skip this, the router never sees the verdict and the
pipeline stalls in a re-review loop. Do NOT treat the OUTCOME block as your final
action — the verdict record must already be written when you emit it.

Follow the context file's exact invocation. The invariant that must hold: on the
**APPROVED** path, the verdict record AND the REVIEW completion marker are ONE
self-contained block — never record APPROVED without immediately writing the
completion marker, or the marker desyncs from the verdict and the router stalls.
On a findings (`CHANGES REQUESTED`) or preflight short-circuit
(`BLOCKED_ON_CONFLICT` / `PR_CLOSED`) verdict, leave the marker `in_progress`. A
failed recording must surface loudly (non-zero exit), never silently corrupt
verdict state.

After recording, read the verdict back (the context file's read-back command) to
confirm it persisted, then proceed to the Output Summary and OUTCOME block.

### 7. Output Summary

**Present review summary** (use bullets — Telegram-bound output must not contain markdown tables; see docs/features/message-drafter.md):

- **Branch** — `{head_branch}` → `{base_branch}`
- **Plan** — `{plan_file}` or "none"
- **Result** — {Approved | Changes Requested}
- **Review** — [{review_url}]({review_url})

**Issues Found: {total}**
- **Blockers: {count}**
- **Tech Debt: {count}**
- **Nits: {count}**

**Screenshots: {count}** captured -> `generated_images/pr-$PR_NUMBER/`

## Integration Notes

**Works with:**
- `/do-build` - Reviews PRs created by the build workflow
- Repo `## Running` README section — start server per the project's own docs
- BYOB MCP (`mcp__byob__browser_*`) - Handles browser automation and screenshot capture
- `gh` CLI - Fetches PR data and posts reviews

**Screenshot storage:**
- Saved to `generated_images/pr-$PR_NUMBER/` directory
- Auto-detected and sent via Telegram bridge
- Bridge uses RELATIVE_PATH_PATTERN to auto-detect generated_images/ files

## Outcome Contract

After posting the review (Step 6), verifying it was posted (Step 6.5), and recording the verdict if a substrate is declared (Step 6.6), emit a typed outcome as the **very last line** of output. If the context file declares a verdict substrate, the verdict record from Step 6.6 must already be written before you emit this block — the OUTCOME block is the last line, not the last action.

**Verdict taxonomy:**

| Verdict | When | OUTCOME status |
|---------|------|----------------|
| `APPROVED` | Preflight clean + zero findings + pre-verdict checklist all PASS/N/A | `success` |
| `CHANGES_REQUESTED` | Preflight clean but findings (blockers, tech_debt, or nits) exist | `partial` (tech_debt/nits only) or `fail` (blockers) |
| `BLOCKED_ON_CONFLICT` | Preflight detected `mergeable=CONFLICTING` or `mergeStateStatus=DIRTY` — short-circuited, no code review performed | `fail` |
| `PR_CLOSED` | Preflight detected `state != OPEN` — short-circuited, no code review performed | `fail` |

**Success (APPROVED — no blockers, no tech_debt, no nits):**
```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0},"notes":"Approved with no findings.","next_skill":"/do-docs"} -->
```

**Partial (CHANGES_REQUESTED — no blockers, but has tech_debt and/or nits that need patching):**
```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1},"notes":"Changes requested: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

**Fail (CHANGES_REQUESTED — blockers found):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":2,"tech_debt":1,"nits":0},"notes":"Changes requested: 2 blockers found.","failure_reason":"2 blockers must be fixed before merge","next_skill":"/do-patch"} -->
```

**Fail (BLOCKED_ON_CONFLICT — preflight short-circuit):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"BLOCKED_ON_CONFLICT","artifacts":{"review_url":"{comment_url}","mergeStateStatus":"DIRTY","mergeable":"CONFLICTING"},"notes":"Branch has merge conflicts; rebase required before review.","failure_reason":"mergeStateStatus=DIRTY — author must rebase/resolve conflicts before review can proceed","next_skill":null} -->
```

**Fail (PR_CLOSED — preflight short-circuit):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"PR_CLOSED","artifacts":{"review_url":"{comment_url}","state":"CLOSED"},"notes":"PR is not open; review skipped.","failure_reason":"state=CLOSED — no review performed on a closed PR","next_skill":null} -->
```

**Important**: The outcome block uses HTML comment syntax (`<!-- ... -->`) so it's invisible in rendered markdown but parseable by the pipeline. Always emit it as the very last line of output. Use `"partial"` — not `"success"` — whenever tech_debt or non-subjective nit findings exist. This ensures the pipeline routes to `/do-patch` before advancing to `/do-docs`. For `BLOCKED_ON_CONFLICT` and `PR_CLOSED`, `next_skill` is `null` — the pipeline should NOT auto-advance; the author must rebase or the PM must handle the closed-PR case manually.

**Multi-judge OUTCOME variants (only when a consensus model is active):** when the
multi-judge path runs (≥2 judges dispatched), include `judges_run` and
`consensus_disagreement` inside `artifacts` so operators can grep session state
for disagreement events. Single-judge (the generic default) / docs-only /
preflight short-circuit paths MUST NOT include these fields (they would mislead
consumers into thinking multi-judge ran).

**Multi-judge success (APPROVED via 2-of-2 consensus, all judges aligned):**
```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":false},"notes":"Approved via 2-of-2 consensus (code-quality, risk).","next_skill":"/do-docs"} -->
```

**Multi-judge fail (CHANGES_REQUESTED — judges disagreed, any-blocker-wins triggered):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":1,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":true},"notes":"Changes requested via 2-of-2 consensus: risk judge raised 1 blocker, code-quality approved.","failure_reason":"1 blocker must be fixed before merge","next_skill":"/do-patch"} -->
```

**Multi-judge partial (CHANGES_REQUESTED — judges aligned on tech_debt/nits, no blockers):**
```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1,"judges_run":2,"consensus_disagreement":false},"notes":"Changes requested via 2-of-2 consensus: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

### Multi-judge & cross-vendor consensus (optional, only if the context file declares it)

The generic baseline is a **single reviewer**: you evaluate the diff, classify
findings, and post one verdict. The multi-judge OUTCOME variants above apply only
when a repo opts into consensus review.

If the context file declares a multi-judge consensus model (≥2 parallel review
judges aggregated into one verdict, an optional cross-vendor judge, a PR-diff
shape classifier for cost containment, and a single-writer verdict recorder),
orchestrate it exactly as the context file specifies. The invariants that hold in
every consensus configuration:

- Each judge fork RETURNS its dict — it does not post a PR comment or record state itself.
- The parent posts per-judge comments under a heading prefix distinct from the aggregate `## Review:` comment, then posts the aggregate comment **last**.
- ONE verdict-record call writes the scalar verdict plus any consensus metadata (single-writer invariant).
- A failed/skipped optional judge is treated as a skip, never a crash (unless the repo marks it fail-closed).

In the generic case, skip all of this — one reviewer, one verdict.

### Record the verdict

Canonical step is **6.6** (above) — verdict recording runs immediately after the
review is posted and **before** the OUTCOME block, so a substrate-backed router
always sees the verdict. Multi-judge: exactly ONE single-writer record call after
`compute_consensus`.

## Hard Rules

1. **Reviews MUST be posted on GitHub.** A review that only exists in agent output is NOT a review. Use `gh pr review` to post, or `gh pr comment` for self-authored PRs. Step 6.5 verifies posting succeeded. The SDLC dispatcher checks for both reviews and comments before advancing.
2. **Tech debt and nits get patched.** `/do-patch` fixes all tech debt and non-subjective nits. Only purely subjective nits may be skipped — and that requires human approval.
3. **Never approve and skip issues.** If you found tech debt or nits, they appear in the review body. The pipeline will patch them. Don't omit findings to make the review look clean.
4. **Approval is reserved for zero-finding reviews ONLY.** If ANY tech_debt or nits exist, use `--request-changes`, never `--approve`. GitHub approval is a meaningful quality gate — it signals the PR is truly ready to merge with no outstanding work.
5. **Review identity follows the context file.** Generic default: post under the operator's `gh` credential. If the context file declares a bot/service-account identity and marker, apply it to the single review-posting subprocess only.
6. **`BLOCKED_ON_CONFLICT` and `PR_CLOSED` MUST NEVER call `gh pr review`.** These preflight short-circuit paths use `gh pr comment` exclusively. A formal review API call on a conflicted or closed PR encodes a false code-review verdict.
7. **Visual proof is a hard gate for PRs with UI changes.** If any HTML, CSS, JS/TS, JSX/TSX, Vue, or template files are in the diff, the review MUST capture at least one browser-MCP screenshot before posting an approval. If screenshots were not captured (browser unavailable, app failed to start, or step was skipped), the review MUST post as `CHANGES_REQUESTED` with a blocker citing the missing visual proof. Visual bugs in frontend changes are invisible to static analysis.
8. **If the context file declares a verdict substrate, recording the verdict (Step 6.6) is mandatory and terminal.** Emitting the OUTCOME block does NOT complete the skill — the `sdlc-tool verdict record` call (and, on APPROVED, the co-written completion marker) must run first. Locally-run pipelines have no hooks to record on your behalf; skipping this leaves the router blind and stalls it in a re-review loop. This is the #1 local-pipeline failure mode — do not exit until the verdict reads back.

## Best Practices

1. **Always read the plan first**: The plan is the source of truth for what should have been built
2. **Focus on correctness over style**: Don't nitpick formatting if the code works
3. **Quote actual code in every finding**: Include the verbatim code snippet, not a paraphrase — this makes hallucinated findings self-evident
4. **Verify before posting**: Every blocker must cite a file you read and code you saw (Step 5.5)
5. **Classify severity honestly**: Don't mark blockers as tech debt to speed up merge
6. **Capture key UI paths**: 1-3 screenshots typical, focus on changed functionality
