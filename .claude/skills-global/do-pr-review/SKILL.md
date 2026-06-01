---
name: do-pr-review
description: "Use when reviewing a pull request. Analyzes code changes, validates against plan requirements, and captures visual proof via screenshots. Triggered by 'review this PR', 'check the pull request', 'do a PR review', or a PR URL."
context: fork
allowed-tools: mcp__byob__*, Bash(gh:*), Bash(git:*), Bash(python:*), Bash(jq:*), Bash(sdlc-tool:*), Read, Write, Edit, Grep, Glob
---

# PR Review

Review a pull request by analyzing its changes against the plan, checking code quality, validating tests, and capturing screenshots of UI changes.

## Surface

Screenshot capture in this skill runs against the user's real, logged-in
Chrome via BYOB MCP (`mcp__byob__browser_*`). Public preview deploys
and authenticated staging URLs are screenshotted the same way — BYOB
just shows you the page the user would see. There is no
anonymous-headless fallback; that surface was retired in #1256.

The calling session must have `requires_real_chrome=True` set. For
SDLC pipeline runs, the bridge auto-infers from message content; for
manual review runs, pass `valor-session create --needs-real-chrome
...`. Two concurrent real-Chrome sessions race on the active tab.

## Review Identity

### Bot Account Model (opt-in per machine)

Pipeline-driven reviews MAY post under a dedicated service-account identity
instead of the operator's `gh` credential. The bot identity is **opt-in per
machine**: only the dedicated bot machine sets `SDLC_AGENT_GH_TOKEN`. Standard
machines leave it blank, and pipeline reviews post under the operator's `gh`
credential — reviewing our own commits is the accepted default.

**Environment variables:**

| Variable | Purpose | When set |
|----------|---------|----------|
| `CLAUDE_AGENT_REVIEW` | `1` when running in pipeline context | Set by `sdk_client.py` at session spawn |
| `SDLC_AGENT_GH_TOKEN` | PAT for the bot account (e.g. `yudame-sdlc-bot`) | Optional. Set only on the dedicated bot machine in `~/Desktop/Valor/.env` |

**Rules:**
- When `CLAUDE_AGENT_REVIEW=1` AND `SDLC_AGENT_GH_TOKEN` is non-empty: inject
  `GH_TOKEN=$SDLC_AGENT_GH_TOKEN` for the single `gh pr review` / `gh pr comment`
  subprocess that posts the review. Emit the `<!-- SDLC-AGENT-REVIEW v1 -->`
  marker. All other `gh` calls (read-only queries) use the operator's credential.
- When `CLAUDE_AGENT_REVIEW=1` AND `SDLC_AGENT_GH_TOKEN` is empty or unset:
  post under the operator's `gh` credential without the marker. This is the
  standard posture on every machine that is not the dedicated bot host.
- When `CLAUDE_AGENT_REVIEW` is unset or `0`: post under the operator's `gh`
  credential. Local developer behavior unchanged.

**Important:** The `env GH_TOKEN=...` wrapper is ONLY applied when `SDLC_AGENT_GH_TOKEN`
is non-empty. Passing an empty `GH_TOKEN` to `gh` would corrupt the stored credential.

### Machine-Readable Marker

Every agent-posted review body starts with the following line (before all other content):
```
<!-- SDLC-AGENT-REVIEW v1 sha=<HEAD_SHA> -->
```

Where `<HEAD_SHA>` is the PR head SHA at review time, resolved via:
```bash
HEAD_SHA=$(gh pr view "$PR_NUMBER" --json headRefOid --jq .headRefOid)
```

The marker:
- Is present when `CLAUDE_AGENT_REVIEW=1` and the review body is composed
- Is absent for local developer runs (no `CLAUDE_AGENT_REVIEW`)
- Is absent for `BLOCKED_ON_CONFLICT` / `PR_CLOSED` comment-only paths (those are
  informational, not code-review verdicts)
- Survives GitHub UI rendering as an invisible HTML comment
- Is queryable via `gh api repos/.../pulls/$N/reviews --jq '.[].body'` for forensics

**The marker is forensic only.** It does NOT prevent the bot's `APPROVED` from
satisfying branch protection. You must configure branch protection separately (see below).

### Branch-Protection Configuration

To prevent the bot's approval from satisfying the "N approving reviews required" gate:

**Pattern A — CODEOWNERS:**
1. Create `.github/CODEOWNERS` assigning critical paths to a human-only team
   (e.g. `* @yudame/human-reviewers`).
2. Enable "Require review from Code Owners" in branch protection.
3. The bot's approval satisfies the general approval count but not the CODEOWNERS gate
   (assuming the bot is not in the `human-reviewers` team).

**Pattern B — GitHub Rulesets (recommended for new repos):**
1. Create a Ruleset requiring N approvals.
2. Set `bypass_actors` to include the bot account.
3. Invert: add the bot account to the `actors_can_approve = false` list so its
   approvals do not count toward the N-review gate.

See `docs/features/do-pr-review-bot-identity.md` for the full provisioning runbook.

### Historical Posture

Existing reviews (e.g. on yudame/cuttlefish PR #354) that were posted under the
operator credential before this fix are left untouched. The cutover applies only
to reviews posted after this change is deployed. Backfilling markers retroactively
is out of scope (GitHub's API does not support editing review bodies via the
standard REST surface).

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

## When to Use

- After `/do-build` creates a PR
- When a PR needs thorough review before merge
- To validate UI changes visually with screenshots
- To generate structured review reports with issue severity classification

## Variables

- `pr_number` (required): The PR number to review (e.g., `42` or `#42`)

## SDLC Context Variables (auto-injected)

When running in the SDLC pipeline, these environment variables are pre-resolved
by `sdk_client.py` from the `AgentSession` (issue #420):

| Variable | Description | Fallback |
|----------|-------------|----------|
| `$SDLC_PR_NUMBER` | PR number | Extract from args or `gh pr list` |
| `$SDLC_PR_BRANCH` | PR head branch | `gh pr view --json headRefName` |
| `$SDLC_SLUG` | Work item slug | Derive from branch name |
| `$SDLC_PLAN_PATH` | Path to plan doc | Derive from slug |
| `$SDLC_ISSUE_NUMBER` | Tracking issue | Extract from PR body |
| `$SDLC_REPO` | GitHub repo (org/name) | `$GH_REPO` |

**Usage:** Prefer `$SDLC_PR_NUMBER` over `$PR_NUMBER` when available.
Fall back to manual resolution if the env var is unset.

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

## Stage Marker (with degraded-mode awareness)

At the very start of this skill, write an in_progress marker and inspect its
output for degraded mode (mirroring the `do-docs` substrate-probe pattern).
Do NOT blanket-suppress the output with `2>/dev/null || true` — a forked
sub-skill must announce degraded mode rather than silently lagging state:

```bash
sdlc-tool stage-marker --stage REVIEW --status in_progress --issue-number {issue_number}
```

Parse the JSON output:
- `{"stage": "REVIEW", "status": "in_progress"}` — substrate present, state persisted; proceed normally.
- `{"status": "degraded", ...}` — **announce at the top of your run**: "running in degraded mode (state not persisted)". The review itself depends only on `gh` and the diff, not the substrate, so proceed.
- Non-zero exit — substrate present but the write genuinely failed; report the stderr diagnostic and proceed (do not silently swallow it).

After posting the review (Step 6), on approval (no blockers):

```bash
sdlc-tool stage-marker --stage REVIEW --status completed --issue-number {issue_number}
```

Apply the same degraded-vs-loud interpretation to the completion marker.

Note: If blockers found, leave as in_progress — the SDLC dispatcher will invoke /do-patch and then re-run review, which will complete the stage after fixes.

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
# Ensure clean git state before switching branches
python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; ensure_clean_git_state(Path('.'))"

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
4. Verify any "No-Gos" from the plan are respected
5. If the plan has an Agent Integration section, verify integration points exist in the codebase (e.g., grep for expected tool calls, imports, or MCP references)

### 4.5. Verification Checks (if plan has ## Verification table)

If the plan document has a `## Verification` section with a machine-readable table, run each check automatically on the PR branch:

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

All review-posting logic lives in `sub-skills/post-review.md §3`. That
sub-skill is the **single source of truth** for the review-post decision tree.
It handles:

- Preflight short-circuit paths (`BLOCKED_ON_CONFLICT`, `PR_CLOSED`) → `gh pr comment` only
- Bot identity injection (`GH_TOKEN_FOR_REVIEW`) when `CLAUDE_AGENT_REVIEW=1`
- Self-authored PR detection → `gh pr comment` fallback
- Normal code-review paths (blockers / tech_debt / zero findings) → `gh pr review`
- `<!-- SDLC-AGENT-REVIEW v1 sha=... -->` marker injection when agent context

See `sub-skills/post-review.md §0` (Identity Setup) and `§3` (Post the Review).

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

After posting the review and verifying it was posted (Steps 6-6.5), emit a typed outcome as the **very last line** of output.

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

**Multi-judge OUTCOME variants:** when the multi-judge path runs (≥2 judges
dispatched), include `judges_run` and `consensus_disagreement` inside
`artifacts`. These mirror the side-fields recorded by `compute_consensus` and
let operators grep session state for disagreement events without round-tripping
through Redis. Single-judge / docs-only / lockfile-only / preflight short-circuit
paths MUST NOT include these fields (they would mislead consumers into thinking
multi-judge ran).

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

### Multi-judge consensus (optional, opt-in)

When `SDLC_REVIEW_JUDGES` enables ≥2 judges, this skill orchestrates parallel
review judges and aggregates their findings before recording a single verdict.
See `docs/features/multi-judge-consensus.md` and
`docs/plans/multi-judge-consensus-gates.md` for the full design.

**Env vars:**
- `SDLC_REVIEW_JUDGES` — comma-separated judge IDs from the fixed roster
  (`code-quality`, `risk`). Default: `code-quality,risk` (both enabled).
  Set to `none` or empty to use the legacy single-judge path.
- `SDLC_REVIEW_K` — K-of-N for consensus arithmetic. Default: 2.
  Effective K is auto-clamped to `min(SDLC_REVIEW_K, len(enabled_judges))`.

**Orchestration (when multi-judge active and PR is not docs-only / lockfile-only):**

1. Spawn K agent forks via the same Task / `context: fork` pattern
   `do-plan-critique` uses. Pass each fork a distinct `judge_id` and a
   distinct system-prompt slice. **Each fork RETURNS its dict via stdout —
   it does NOT write to Redis and does NOT post a PR comment.**
2. Parent collects the K dicts.
3. Parent posts each `## Review (Judge {id}):` per-judge comment
   **sequentially** — the loop awaits each `gh pr comment` exit code
   before posting the next. Per-judge headings use the distinct prefix
   that does NOT match `do-merge.md`'s aggregate regex.
4. Parent calls
   `from agent.sdlc_review_consensus import compute_consensus` and runs
   `compute_consensus(dicts, rule="any-blocker-wins")` to derive the scalar
   verdict + consensus metadata.
5. Parent makes ONE `record_verdict` call passing
   `judges=dicts, consensus=meta` (or, via CLI, `--judges-json` and
   `--consensus-json`). Single-writer invariant is preserved.
6. Parent posts the aggregate `## Review: Approved` /
   `## Review: Changes Requested` comment **last** — strictly after every
   per-judge comment is confirmed posted. This is the comment
   `do-merge.md`'s regex picks up.

**Cost containment:** before spawning judge forks, classify the PR's diff via
the same module `/do-merge` uses:

```bash
SHAPE_JSON=$(python -m scripts.pr_shape_classify --pr "$PR_NUMBER" 2>/dev/null \
  || echo '{"shape":"feature"}')
SHAPE=$(echo "$SHAPE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('shape','feature'))")
case "$SHAPE" in
  docs-only|lockfile-only)
    # Force legacy single-judge path — skip multi-judge orchestration.
    SDLC_REVIEW_JUDGES=none
    ;;
esac
```

`scripts/pr_shape_classify.py` is the single source of truth — both
`/do-merge` (`.claude/commands/do-merge.md`) and this skill invoke it via
`python -m scripts.pr_shape_classify`. Do NOT inline shape logic or fork a
parallel classifier. `SDLC_REVIEW_JUDGES=none` and `SDLC_REVIEW_K=1` remain
independent operator kill switches.

**Monitoring:** when multi-judge runs, the OUTCOME block records
`judges_run` (count) and `consensus_disagreement` (bool, true when any pair
of judges disagreed). Operators can grep these from session state.

### Record the verdict (mandatory)

After emitting the OUTCOME block, record the review verdict on the PM session so the SDLC router's Legal Dispatch Guards (G3, G4) can consume it:

```bash
# Single-judge (legacy / SDLC_REVIEW_JUDGES=none / docs-only / preflight):
# For APPROVED reviews (OUTCOME status=success):
sdlc-tool verdict record --stage REVIEW \
  --verdict "APPROVED" --blockers 0 --tech-debt 0 --issue-number $ISSUE_NUMBER

# For reviews with findings (OUTCOME status=partial or fail):
sdlc-tool verdict record --stage REVIEW \
  --verdict "CHANGES REQUESTED" --blockers $BLOCKERS --tech-debt $TECH_DEBT \
  --issue-number $ISSUE_NUMBER

# For preflight short-circuit (branch cannot merge):
sdlc-tool verdict record --stage REVIEW \
  --verdict "BLOCKED_ON_CONFLICT" --blockers 0 --tech-debt 0 \
  --issue-number $ISSUE_NUMBER

# For preflight short-circuit (PR not open):
sdlc-tool verdict record --stage REVIEW \
  --verdict "PR_CLOSED" --blockers 0 --tech-debt 0 \
  --issue-number $ISSUE_NUMBER

# Multi-judge: pass --judges-json and --consensus-json after computing
# consensus via agent.sdlc_review_consensus.compute_consensus. ONE record
# call writes both the scalar AND the side-fields (single-writer invariant).
sdlc-tool verdict record --stage REVIEW \
  --verdict "$VERDICT" --blockers $BLOCKERS --tech-debt $TECH_DEBT \
  --issue-number $ISSUE_NUMBER \
  --judges-json "$JUDGES_JSON" --consensus-json "$CONSENSUS_JSON"
```

The recorder exits non-zero on failure (e.g. Redis unreachable) so the operator sees the error in their session log, but it still prints `{}` to stdout for callers parsing JSON. A failed recording surfaces loudly; it does not silently corrupt verdict state. If `$ISSUE_NUMBER` is unknown, omit the `--issue-number` flag and the recorder will resolve via `VALOR_SESSION_ID` / `AGENT_SESSION_ID`.

## Hard Rules

1. **Reviews MUST be posted on GitHub.** A review that only exists in agent output is NOT a review. Use `gh pr review` to post, or `gh pr comment` for self-authored PRs. Step 6.5 verifies posting succeeded. The SDLC dispatcher checks for both reviews and comments before advancing.
2. **Tech debt and nits get patched.** `/do-patch` fixes all tech debt and non-subjective nits. Only purely subjective nits may be skipped — and that requires human approval.
3. **Never approve and skip issues.** If you found tech debt or nits, they appear in the review body. The pipeline will patch them. Don't omit findings to make the review look clean.
4. **Approval is reserved for zero-finding reviews ONLY.** If ANY tech_debt or nits exist, use `--request-changes`, never `--approve`. GitHub approval is a meaningful quality gate — it signals the PR is truly ready to merge with no outstanding work.
5. **Pipeline-driven reviews use bot identity when configured (opt-in per machine).** When `CLAUDE_AGENT_REVIEW=1` AND `SDLC_AGENT_GH_TOKEN` is set, the review subprocess MUST use `GH_TOKEN=$SDLC_AGENT_GH_TOKEN`. When the token is unset or empty, post under the operator credential — this is the standard posture on machines that are not the dedicated bot host.
6. **The `<!-- SDLC-AGENT-REVIEW v1 -->` marker MUST appear** in every review body when `CLAUDE_AGENT_REVIEW=1` AND `SDLC_AGENT_GH_TOKEN` is set. The marker is omitted when posting under the operator credential.
7. **`BLOCKED_ON_CONFLICT` and `PR_CLOSED` MUST NEVER call `gh pr review`.** These preflight short-circuit paths use `gh pr comment` exclusively. A formal review API call on a conflicted or closed PR encodes a false code-review verdict.
8. **Visual proof is a hard gate for PRs with UI changes.** If any HTML, CSS, JS/TS, JSX/TSX, Vue, or template files are in the diff, the review MUST capture at least one BYOB screenshot before posting an approval. If screenshots were not captured (BYOB unavailable, app failed to start, or step was skipped), the review MUST post as `CHANGES_REQUESTED` with a blocker citing the missing visual proof. This rule exists because visual bugs in frontend changes are invisible to static analysis — see issue #1380.

## Best Practices

1. **Always read the plan first**: The plan is the source of truth for what should have been built
2. **Focus on correctness over style**: Don't nitpick formatting if the code works
3. **Quote actual code in every finding**: Include the verbatim code snippet, not a paraphrase — this makes hallucinated findings self-evident
4. **Verify before posting**: Every blocker must cite a file you read and code you saw (Step 5.5)
5. **Classify severity honestly**: Don't mark blockers as tech debt to speed up merge
6. **Capture key UI paths**: 1-3 screenshots typical, focus on changed functionality
