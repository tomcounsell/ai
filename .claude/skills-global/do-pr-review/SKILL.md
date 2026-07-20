---
name: do-pr-review
description: "Review a pull request against its plan. Triggered by 'review this PR', 'check the pull request', 'do a PR review', or a PR URL."
argument-hint: "<pr-number>"
context: fork
allowed-tools: mcp__byob__*, Bash(gh:*), Bash(git:*), Bash(python:*), Bash(jq:*), Bash(sdlc-tool:*), Read, Write, Edit, Grep, Glob
---

# PR Review

Review a pull request by analyzing its changes against the plan, checking code quality, validating tests, and capturing visual proof of UI changes before approval.

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

## Variables

- `pr_number` (required): The PR number to review (e.g., `42` or `#42`), passed as the skill argument. The argument is the PR number, NOT the issue number.

## SDLC Context Variables (only if the context file declares them)

If the repo runs this skill inside an SDLC pipeline, the context file may declare
pre-resolved environment variables (`$SDLC_PR_NUMBER`, `$SDLC_PR_BRANCH`,
`$SDLC_SLUG`, `$SDLC_PLAN_PATH`, `$SDLC_ISSUE_NUMBER`, `$SDLC_REPO`). When
present, the sub-skills prefer them and fall back to resolution from the skill
argument, git state, and `gh`. In the generic case, resolve everything from the
argument and `gh`.

## Sub-Skills

This skill is decomposed into focused sub-skills in `sub-skills/`. They are
guidance documents loaded by this orchestrator — not standalone slash commands.
Load each one when its phase begins:

| Sub-skill | Type | Load when... |
|-----------|------|--------------|
| `checkout.md` | Mechanical | Starting the review — context/issue-number resolution, **mergeability preflight (runs first)**, clean git state, checkout PR branch |
| `code-review.md` | Judgment | Preflight passed — parse PR-body disclosures, read prior reviews, analyze diff, validate plan, traverse the 10-item Rubric, evaluate the 12-item Pre-Verdict Checklist, classify and verify findings, run the legacy cruft audit, derive the verdict mechanically |
| `screenshot.md` | Mechanical | The diff touches UI files — start app, capture screenshots, evaluate the visual proof gate |
| `post-review.md` | Mechanical | Findings (or a short-circuit verdict) are ready — format the review body, decide review-vs-comment, post to GitHub, verify posting |
| `outcome-contract.md` | Reference | Emitting the final OUTCOME block — exact JSON for every verdict variant, including multi-judge |

**Determinism note:** `code-review.md` includes a disclosure parser (pre-finding), a prior-review context loader (idempotency on unchanged HEAD SHA + body), an explicit 10-item Rubric with pass/fail/acknowledged/n/a per item, a Miscellaneous bucket for issues outside the rubric, and mechanical verdict derivation. These were introduced in issue #1045 to address non-deterministic verdicts across repeated runs on the same PR.

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

## Review Flow

### 1. Context Resolution & Mergeability Preflight (first actions)

Load `sub-skills/checkout.md` and run its steps in order:

1. **Context Resolution** — resolve `PR_NUMBER`, `REPO`, `PLAN_PATH`, `SLUG`, and
   `ISSUE_NUMBER`. The issue number MUST resolve to a positive integer from the
   PR body first (`Closes #N`, then a tracking-URL fallback); a stale inherited
   `$SDLC_ISSUE_NUMBER` is last resort only, and an unresolvable issue number
   fails loudly (#1731 — never silently divert a verdict onto the wrong issue).

2. **Mergeability Preflight** — one cheap `gh pr view --json mergeable,mergeStateStatus,state`
   call (retry once after 2s on `UNKNOWN`) that catches objective blockers before
   any subjective review. `checkout.md` carries the full decision table; the
   invariants: `state != OPEN` → `PR_CLOSED`; `CONFLICTING`/`DIRTY` (or still
   `UNKNOWN` after retry) → `BLOCKED_ON_CONFLICT`; `BEHIND` is informational —
   proceed.

   On a short-circuit, post the comment via `gh pr comment` (never
   `gh pr review`), emit the matching OUTCOME from `outcome-contract.md`, and
   exit — do NOT checkout, read the diff, or produce a code review body.

3. **Checkout the PR branch (mandatory before any file reads)** — clean git
   state (abort in-progress merge/rebase, stash), then `gh pr checkout`. This
   ensures all subsequent `Read` calls see the PR's actual code, not whatever
   branch the parent conversation had checked out. Without this, the reviewer
   reads stale files that contradict the diff — producing hallucinated findings.

> **Worktree isolation:** When spawning this skill via the Agent tool, use `isolation: "worktree"` so the checkout doesn't disrupt the parent conversation's working directory.

### 2. Code Review

Load `sub-skills/code-review.md` and follow it end to end. It covers:

- Gathering the PR diff, changed files, plan, and tracking issue
- The mandatory disclosure parser and prior-review context loader
- Diff analysis (correctness, security, error handling, tests, quality, docs)
- Plan validation, including No-Gos, anti-criterion advisories, and plan
  checkbox validation
- Running the plan's `## Verification` table on the PR branch (generic baseline:
  run each `Command` and compare against `Expected`; a declared
  verification-table runner overrides). Failed checks are **blockers**.
- Severity classification (`blocker` / `tech_debt` / `nit` / `acknowledged`)
  with the exact per-finding block format and the mandatory empty-section
  markers (`### Blockers\n- None`, etc.)
- Mandatory finding verification — every blocker/tech_debt must cite a file you
  read and code you saw; unverifiable findings are dropped entirely
- The legacy cruft audit (advisory findings)
- The Rubric, Pre-Verdict Checklist, and mechanical verdict derivation

### 3. Screenshot Capture (if UI changes detected)

Load `sub-skills/screenshot.md` when the diff touches UI files (HTML, CSS,
JS/TS under UI directories, JSX/TSX, Vue, templates). It detects UI files, sets
`UI_CHANGES_DETECTED`, starts the app per the repo's `## Running` README
section, captures screenshots via the browser MCP into
`generated_images/pr-$PR_NUMBER/`, and evaluates the **visual proof gate**:

- No UI files changed → skip entirely; the gate is a no-op.
- UI files changed but zero screenshots captured → `VISUAL_PROOF_GATE_FAILED=true`:
  inject the gate's blocker finding and override any otherwise-clean verdict to
  `CHANGES_REQUESTED`. See Hard Rule 7.

### 4. Post Review

Load `sub-skills/post-review.md` — it is the **single source of truth** for the
review-post decision tree:

- Preflight short-circuit paths (`BLOCKED_ON_CONFLICT`, `PR_CLOSED`) → `gh pr comment` only
- Self-authored PR detection → `gh pr comment` fallback
- Normal code-review paths (blockers / tech_debt / zero findings) → `gh pr review`
- Bot-identity token injection and a review marker → **only if the context file declares them** (see Review Identity)

Generic baseline: post `gh pr review --approve` for a clean review, or
`gh pr review --request-changes` when findings exist, under the operator's `gh`
credential. After posting, verify the review or comment actually exists (retry
as a comment if not) and capture `{review_url}` — both per `post-review.md`.

### 5. Record the Verdict (only if the context file declares a substrate)

In the generic case (no substrate declared) the posted GitHub review IS the
verdict — skip this step.

If the context file declares a verdict-recording substrate (so a pipeline router
can consume the verdict programmatically), you MUST record the verdict **here,
before emitting the OUTCOME block**. Recording is a terminal, non-optional action,
not a trailing nicety. A locally-run pipeline (e.g. `/do-sdlc`) has no hooks to
record on your behalf: if you skip this, the router never sees the verdict and the
pipeline stalls in a re-review loop.

Follow the context file's exact invocation. The invariant that must hold: on the
**APPROVED** path, the verdict record AND the REVIEW completion marker are ONE
self-contained block — never record APPROVED without immediately writing the
completion marker, or the marker desyncs from the verdict and the router stalls.
On a findings (`CHANGES REQUESTED`) or preflight short-circuit
(`BLOCKED_ON_CONFLICT` / `PR_CLOSED`) verdict, leave the marker `in_progress` —
the dispatcher re-runs review after `/do-patch`. A failed recording must surface
loudly (non-zero exit), never silently corrupt verdict state.

After recording, read the verdict back (the context file's read-back command) to
confirm it persisted, then proceed to the Output Summary and OUTCOME block.

The same applies to the stage-marker substrate more broadly: if declared, write
the REVIEW `in_progress` marker at the start (after Step 1 resolves the issue
number) and follow its degraded-mode handling. The review itself depends only on
`gh` and the diff, never the substrate. In the generic case, skip stage markers
entirely.

### 6. Output Summary

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

## Outcome Contract

After posting the review, verifying it was posted, and recording the verdict if
a substrate is declared, emit a typed OUTCOME as the **very last line** of
output. Load `sub-skills/outcome-contract.md` for the verdict taxonomy and the
exact JSON block for every variant (APPROVED, CHANGES_REQUESTED partial/fail,
BLOCKED_ON_CONFLICT, PR_CLOSED, and the multi-judge variants). If the context
file declares a verdict substrate, the verdict record from Step 5 must already
be written before you emit this block — the OUTCOME block is the last line, not
the last action.

Multi-judge & cross-vendor consensus review is optional and only runs if the
context file declares it — `outcome-contract.md` carries the invariants. In the
generic case: one reviewer, one verdict.

## Hard Rules

1. **Reviews MUST be posted on GitHub.** A review that only exists in agent output is NOT a review. Use `gh pr review` to post, or `gh pr comment` for self-authored PRs. Verify posting succeeded per `post-review.md`. The SDLC dispatcher checks for both reviews and comments before advancing.
2. **Tech debt and nits get patched.** `/do-patch` fixes all tech debt and non-subjective nits. Only purely subjective nits may be skipped — and that requires human approval.
3. **Never approve and skip issues.** If you found tech debt or nits, they appear in the review body. The pipeline will patch them. Don't omit findings to make the review look clean.
4. **Approval is reserved for zero-finding reviews ONLY.** If ANY tech_debt or nits exist, use `--request-changes`, never `--approve`. GitHub approval is a meaningful quality gate — it signals the PR is truly ready to merge with no outstanding work.
5. **Review identity follows the context file.** Generic default: post under the operator's `gh` credential. If the context file declares a bot/service-account identity and marker, apply it to the single review-posting subprocess only.
6. **`BLOCKED_ON_CONFLICT` and `PR_CLOSED` MUST NEVER call `gh pr review`.** These preflight short-circuit paths use `gh pr comment` exclusively. A formal review API call on a conflicted or closed PR encodes a false code-review verdict.
7. **Visual proof is a hard gate for PRs with UI changes.** If any HTML, CSS, JS/TS, JSX/TSX, Vue, or template files are in the diff, the review MUST capture at least one browser-MCP screenshot before posting an approval. If screenshots were not captured (browser unavailable, app failed to start, or step was skipped), the review MUST post as `CHANGES_REQUESTED` with a blocker citing the missing visual proof. Visual bugs in frontend changes are invisible to static analysis.
8. **If the context file declares a verdict substrate, recording the verdict (Step 5) is mandatory and terminal.** Emitting the OUTCOME block does NOT complete the skill — the declared verdict-record call (and, on APPROVED, the co-written completion marker) must run first. Locally-run pipelines have no hooks to record on your behalf; skipping this leaves the router blind and stalls it in a re-review loop. This is the #1 local-pipeline failure mode — do not exit until the verdict reads back.
9. **Judge subagents run in the foreground and MUST be awaited in-turn (issue #2124 / WS-D).** If the context file declares multi-judge consensus, dispatch the judges and BLOCK on each returning IN THE SAME TURN before you aggregate, post the `## Review:` comment, or record the verdict. NEVER `run_in_background` a judge and return while it is still in flight — a fork that exits with judges running kills those children, so nothing ever posts (the #2112 miss). The aggregate review artifact must be posted AND the verdict recorded BEFORE this skill returns. The REVIEW completion marker is now refused (`REVIEW_ARTIFACT_MISSING`) unless a posted review artifact is verifiable, so an un-awaited-judge exit fails closed rather than advancing the pipeline on nothing.

## Best Practices

1. **Always read the plan first**: The plan is the source of truth for what should have been built
2. **Focus on correctness over style**: Don't nitpick formatting if the code works
3. **Classify severity honestly**: Don't mark blockers as tech debt to speed up merge
4. **Capture key UI paths**: 1-3 screenshots typical, focus on changed functionality
