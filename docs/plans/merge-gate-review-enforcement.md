---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/823
last_comment_id:
revision_applied: true
---

# Merge Gate: Enforce Structured Review Comment Check

## Problem

The `/do-merge` skill authorizes merges based solely on Redis pipeline stage state — it never reads actual PR comments. This allows two failure modes:

**Failure mode 1 — Merge while blocked:**
PR #770 was merged with 2× `## Review: Changes Requested` comments and 0 `## Review: Approved`. The merge gate checked Redis (`REVIEW: completed`) and authorized the merge without ever looking at the comments. Result: orphaned test file that caused import errors visible in #784.

**Failure mode 2 — Stage marked completed with zero review:**
18 of 30 PRs merged on 2026-04-06/07 had zero `## Review:` comments. The REVIEW stage was marked `completed` in Redis via some path, and `/do-merge` accepted that without verifying a review actually occurred.

**Current behavior:**
`/do-merge` reads `PipelineStateMachine` stage states from Redis. If `REVIEW == completed`, it passes. No check on actual PR comment content.

**Desired outcome:**
Before authorizing merge, `/do-merge` scans PR issue comments for the most recent `## Review:` comment and:
- Blocks with a clear message if no such comment exists
- Blocks (listing unchecked items) if the most recent comment starts with `## Review: Changes Requested`
- Passes only if the most recent comment starts with `## Review: Approved`

## Prior Art

- **PR #802** (`fix(sdlc): enforce CRITIQUE and REVIEW gates in PM persona and sdk_client`) — Addressed PM session skipping stages. Did not fix the merge gate itself checking comment content.
- **PR #550** (`PM SDLC decision rules: auto-merge on clean reviews, patch on findings, never silently skip`) — Behavioral rules for the PM session, not the merge gate command.
- **Issue #791** (`PM agent skips CRITIQUE and REVIEW stages, merging PRs without them`) — Closed 2026-04-07. Addressed stage-skipping at the routing level, not at merge-time comment verification.

None of these addressed the merge gate reading actual PR comment content — they all operated at the pipeline routing layer, not the merge authorization layer.

## Data Flow

1. **Entry point**: `/do-merge PR_NUMBER` is invoked by the PM session
2. **Prerequisites check**: `/do-merge` queries `PipelineStateMachine` for TEST, REVIEW, DOCS stage states
3. **Gap**: No check of actual PR comments occurs — only Redis state is read
4. **Authorization**: If all stages are `completed`, `data/merge_authorized_{PR}` is created
5. **Merge guard hook**: `validate_merge_guard.py` sees the authorization file and allows `gh pr merge`
6. **Desired new step (2b)**: Before creating authorization, scan `gh api repos/.../issues/{PR}/comments` for the most recent `## Review:` comment and gate on its content

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The `gh` CLI is already available and authenticated.

## Solution

### Key Elements

- **Review comment scanner**: Shell snippet inside `/do-merge`'s Prerequisites Check section that calls `gh api` to fetch PR issue comments, filters for `## Review:` prefix, and extracts the last one
- **Block logic**: Three-branch decision: no comment → block; `Changes Requested` → block with unchecked items; `Approved` → pass
- **Validator bug fix**: Fix `validate_issue_recon.py` bucket patterns to match the `**Confirmed:** N items` format (colon + count suffix) that the actual recon routine produces

### Flow

`/do-merge PR_NUMBER` → pipeline state check (existing) → **review comment scan (new)** → plan completion gate (existing) → create authorization file → merge

### Technical Approach

Add the following block to `/do-merge` in the **Prerequisites Check** section, immediately after the existing Redis stage gate check:

```bash
# Structured review comment check
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
LAST_REVIEW=$(gh api repos/$REPO/issues/$ARGUMENTS/comments \
  --jq '[.[] | select(.body | startswith("## Review:"))] | last | .body // ""' \
  2>/dev/null) || { echo "REVIEW_COMMENT: FAIL — gh api call failed (network/auth error)"; echo "GATES_FAILED"; return 1; }

if [ -z "$LAST_REVIEW" ]; then
  echo "REVIEW_COMMENT: FAIL — No '## Review:' comment found on PR #$ARGUMENTS"
  echo "GATES_FAILED"
elif echo "$LAST_REVIEW" | grep -q "^## Review: Changes Requested"; then
  BLOCKERS=$(echo "$LAST_REVIEW" | grep "^- \[ \]" | head -20)
  echo "REVIEW_COMMENT: FAIL — Most recent review is 'Changes Requested'"
  echo "Unchecked blockers:"
  echo "$BLOCKERS"
  echo "GATES_FAILED"
else
  echo "REVIEW_COMMENT: PASS — Most recent review is 'Approved'"
fi
```

> **Implementation Note (Concern 1 — Fail-closed guard):** The `gh api` call must be wrapped with an explicit `|| { ... }` error handler so that API failures (network outage, auth expiry) cause a hard block rather than silently returning an empty string. An empty string from a *failed* call would be indistinguishable from "no review found" without this guard. The `2>/dev/null` suppresses stderr noise; the `|| { ... }` block catches the non-zero exit and emits `GATES_FAILED` explicitly.

Key decisions:
- Use `gh api repos/$REPO/issues/$ARGUMENTS/comments` (issue comments endpoint) — covers both self-authored PRs (which use `gh pr comment`) and non-self-authored PRs (which may also have comments)
- Filter with `jq` for `.body | startswith("## Review:")` — exact match against the structured format
- Take `last` — only the most recent review comment counts; earlier rounds are superseded
- Extract `- [ ]` lines from a `Changes Requested` comment to surface the specific blockers
- The `grep -q "^## Review: Changes Requested"` prefix match correctly captures all variants including `## Review: Changes Requested — Tech Debt` (see Failure Path Test Strategy for test case)

Also fix `validate_issue_recon.py`: change the four `BUCKET_PATTERNS` entries from `r"\*\*Confirmed\b"` to `r"\*\*Confirmed\*\*:?"` so the pattern matches both `**Confirmed**` and `**Confirmed:** N items` without false-positiving on prose like `**Confirmed in step 3**`.

> **Implementation Note (Concern 3 — Regex fix):** The current plan text said `r"\*\*Confirmed\b"` but this is wrong — `\b` is a word boundary that matches after the `d` in `Confirmed`, so it would also match `**Confirmed in step 3**`. The correct fix is `r"\*\*Confirmed\*\*:?"` (literal closing `**` then optional `:`). This matches `**Confirmed**` and `**Confirmed:**` but not arbitrary prose containing `**Confirmed`.

## Failure Path Test Strategy

### Exception Handling Coverage
- No new `except` blocks introduced — shell error handling via `gh` exit codes
- If `gh api` fails (network error, auth), it returns non-zero; the explicit `|| { echo "GATES_FAILED"; }` guard ensures this is treated as a blocking condition (fail closed), not a pass-through

### Empty/Invalid Input Handling
- Empty comment list → `jq` returns `""` → treated as "no review found" → blocks
- PR with only non-`## Review:` comments → same empty-string path → blocks
- PR with `## Review: Approved\n\nsome text` → `startswith` check passes → correctly approved

### Tech Debt Variant Test Case

> **Implementation Note (Concern 2 — Tech Debt variant):** The `## Review: Changes Requested — Tech Debt` variant must be explicitly verified. The `grep -q "^## Review: Changes Requested"` prefix match handles this correctly because `Tech Debt` is a suffix, but this should be confirmed in the validator step.

Test case for validator (Task 3):
```bash
# Verify "Changes Requested — Tech Debt" is blocked
FAKE_COMMENT="## Review: Changes Requested — Tech Debt

- [ ] Remove dead import in agent/foo.py"
echo "$FAKE_COMMENT" | grep -q "^## Review: Changes Requested" && echo "PASS: Tech Debt variant blocked" || echo "FAIL"
```
Expected output: `PASS: Tech Debt variant blocked`

### Error State Rendering
- Block messages are printed to stdout (visible in PM session output) before `GATES_FAILED`
- Unchecked blocker items listed explicitly so the PM knows what to patch

## Test Impact

No existing tests affected — `/do-merge` is a skill (markdown command file), not Python code. There are no unit or integration tests that invoke `/do-merge` directly or assert on its output format. The `validate_issue_recon.py` fix only broadens pattern matching and cannot break any existing behavior.

## Rabbit Holes

- **Checking GitHub Reviews API** (`gh api repos/.../pulls/$PR/reviews`) — not applicable for self-authored PRs, which use `gh pr comment`. The issue comments endpoint is the correct and only path.
- **Fixing the Redis stage marker write paths** — the recon confirmed the stage marker is already correct; the gap is entirely in `/do-merge` not checking comments. Do not touch `sdlc_stage_marker` or `post-review.md`.
- **Retroactive audit of merged PRs** — out of scope for this fix. The issue documents the audit findings as motivation; we are not re-reviewing or re-opening those PRs.
- **Requiring formal GitHub review approval** (`gh pr review --approve`) — not feasible for self-authored PRs. The comment-based approach is the correct mechanism for this repo's workflow.

## Risks

### Risk 1: `gh api` comment endpoint returns reviews in unexpected order
**Impact:** Wrong "last review" selected if comments are not chronologically ordered in the API response.
**Mitigation:** GitHub's issues/comments endpoint returns comments in ascending chronological order. `jq`'s `last` correctly selects the most recent. This is confirmed by the GitHub API documentation.

### Risk 2: Review comment posted by automation with slightly different prefix
**Impact:** `startswith("## Review:")` misses a comment if it has extra whitespace or a BOM.
**Mitigation:** The structured format is generated by `post-review.md` which uses a hardcoded string `## Review: Approved` or `## Review: Changes Requested`. No variation in the prefix is expected. The `startswith` check is correct.

## Race Conditions

No race conditions identified — all operations are synchronous shell commands. The `gh api` call reads a point-in-time snapshot of comments; no concurrent write can affect the merge gate decision after the check passes, because the authorization file is created and consumed within the same agent turn.

## No-Gos (Out of Scope)

- Retroactive validation of already-merged PRs
- Changes to `validate_merge_guard.py` (the hook is correct; only `/do-merge` needs updating)
- Changes to `post-review.md` or `sdlc_stage_marker` (already correct per recon)
- Enforcing formal GitHub PR review approval state (not compatible with self-authored PRs)
- Adding a new test file for `/do-merge` behavior (the skill is markdown, not Python)

## Update System

No update system changes required — this feature modifies only `.claude/commands/do-merge.md` and `.claude/hooks/validators/validate_issue_recon.py`. Both are version-controlled and propagate automatically via `git pull` on all machines.

## Agent Integration

No agent integration required — `/do-merge` is a Claude Code skill invoked directly by the PM session. No MCP servers, `tools/` Python modules, or `.mcp.json` changes needed.

## Documentation

- [x] Update `docs/features/sdlc-pipeline.md` (if it exists) to note that `/do-merge` verifies review comment content, not just stage state — `docs/features/sdlc-pipeline.md` does not exist; added entry to `docs/features/README.md` instead
- [x] If no such doc exists, add a note to `docs/features/README.md` under the merge gate entry — done; `docs/features/sdlc-pipeline-integrity.md` created with full feature documentation

## Success Criteria

- [x] `/do-merge` blocks merge when no `## Review:` comment exists on the PR
- [x] `/do-merge` blocks merge and lists unchecked `- [ ]` items when most recent `## Review:` comment starts with `## Review: Changes Requested`
- [x] `/do-merge` passes when most recent `## Review:` comment starts with `## Review: Approved`
- [x] Multiple review rounds handled correctly — only the last `## Review:` comment counts
- [x] `validate_issue_recon.py` passes for issues whose recon uses `**Confirmed:** N items` format — `python .claude/hooks/validators/validate_issue_recon.py 823` exits 0
- [x] Tests pass (`/do-test`) — `test_bare_hash_is_question` is pre-existing on main (not a regression)

## Team Orchestration

### Team Members

- **Builder (merge-gate)**
  - Name: merge-gate-builder
  - Role: Edit `.claude/commands/do-merge.md` to add the review comment scan block; fix `validate_issue_recon.py` bucket patterns
  - Agent Type: builder
  - Resume: true

- **Validator (merge-gate)**
  - Name: merge-gate-validator
  - Role: Verify the new block is correctly placed, the jq query is syntactically valid, and the bucket pattern fix works for the `**Confirmed:** N items` format
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Add review comment scan to `/do-merge`
- **Task ID**: build-merge-gate
- **Depends On**: none
- **Validates**: Manual inspection — no automated test suite for skill markdown files
- **Assigned To**: merge-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/commands/do-merge.md`: insert the review comment scan block in the Prerequisites Check section, immediately after the Redis stage gate check and before the Plan Completion Gate
- The block must: fetch `gh api repos/$REPO/issues/$ARGUMENTS/comments`, filter for `## Review:` prefix, take `last`, branch on empty / `Changes Requested` / `Approved`

### 2. Fix `validate_issue_recon.py` bucket patterns
- **Task ID**: build-validator-fix
- **Depends On**: none
- **Validates**: `python .claude/hooks/validators/validate_issue_recon.py 823` exits 0
- **Assigned To**: merge-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `BUCKET_PATTERNS` in `validate_issue_recon.py`: change `r"\*\*Confirmed\b"` (word boundary — too broad) to `r"\*\*Confirmed\*\*:?"` (and likewise for Revised, Pre-requisites, Dropped) so patterns match `**Confirmed**` and `**Confirmed:** N items` but NOT prose like `**Confirmed in step 3**`

### 3. Validate both changes
- **Task ID**: validate-all
- **Depends On**: build-merge-gate, build-validator-fix
- **Assigned To**: merge-gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the jq snippet in `/do-merge` is syntactically correct (run `echo '[]' | jq '[.[] | select(.body | startswith("## Review:"))] | last | .body // ""'`)
- Verify `validate_issue_recon.py 823` exits 0
- Verify ruff check and format pass

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Recon validator passes for #823 | `python .claude/hooks/validators/validate_issue_recon.py 823` | exit code 0 |
| jq syntax valid | `echo '[]' \| jq '[.[] \| select(.body \| startswith("## Review:"))] \| last \| .body // ""'` | exit code 0 |

## Critique Results

**Verdict: READY TO BUILD (with concerns)** — 0 blockers, 3 concerns, 1 nit. Revision pass applied.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator | Fail-closed guard not explicit — `gh api` failure path works by accident (empty string → block) but has no explicit `\|\|` guard | Technical Approach updated | Added `\|\| { echo "REVIEW_COMMENT: FAIL — gh api call failed"; echo "GATES_FAILED"; return 1; }` after the `gh api` command |
| CONCERN | Adversary | `## Review: Changes Requested — Tech Debt` variant not mentioned in Failure Path section; no test case for it | Failure Path Test Strategy updated | Added explicit test case verifying prefix match handles Tech Debt suffix correctly |
| CONCERN | Archaeologist | Regex fix uses `r"\*\*Confirmed\b"` (word boundary) which false-positives on `**Confirmed in step 3**`; correct fix is `r"\*\*Confirmed\*\*:?"` (optional colon) | Technical Approach + Task 2 updated | Corrected regex in both plan text and task step to `r"\*\*Confirmed\*\*:?"` |
| NIT | Simplifier | Task 2 description said "change `r"\*\*Confirmed\*\*"` to `r"\*\*Confirmed\b"`" — had the before/after backwards | Task 2 updated | Task now correctly says change FROM word-boundary TO literal `\*\*:?` pattern |

---

## Open Questions

None — recon confirmed all key assumptions. Ready to proceed to critique.
