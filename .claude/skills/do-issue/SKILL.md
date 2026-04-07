---
name: do-issue
description: "Use when creating a new GitHub issue. Ensures issues are self-contained, define domain terms, and provide enough context for the /do-plan skill to produce a quality plan. Triggered by 'create an issue', 'file an issue', or automatically by /sdlc at Step 1."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
argument-hint: "<title or description>"
---

# Create Issue (Quality Issue Creator)

Creates GitHub issues that are self-contained documents a stranger could understand. Every issue must teach the reader what they need to know — define terms, link sources, and state the problem from the reader's perspective.

## Core Philosophy: Think Like a Teacher

The reader of your issue has general software engineering experience but **zero context about this specific codebase**. Every named concept that isn't common knowledge needs:

1. A one-sentence definition
2. A link to its source (repo, docs page, file path, or RFC)

This isn't optional politeness — it's functional. The `/do-plan` skill reads the issue description as its primary input. Undefined terms produce vague plans. Defined terms produce precise plans.

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `RECON.md` | After Step 2, before writing — run the reconnaissance routine |
| `ISSUE_TEMPLATE.md` | Writing the issue body (use as the structural skeleton) |
| `CHECKLIST.md` | Before publishing — run every check, fix failures |

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

## When to Use

- Creating any new GitHub issue (feature, bug, or chore)
- `/sdlc` Step 1 dispatches here when no issue exists
- User says "create an issue", "file a bug", "track this"

## Quick Start Workflow

### Step 1: Understand the Request

Read the user's description. Identify:
- **What type**: bug, feature, or chore
- **What's broken or missing**: the actual problem
- **Domain terms**: any project-specific names, acronyms, or concepts

### Step 2: Research Context

Before writing, gather context so the issue is grounded in reality:

```bash
# Search for related closed issues
gh issue list --state closed --search "KEYWORDS" --limit 5 --json number,title,url

# Search for related merged PRs
gh pr list --state merged --search "KEYWORDS" --limit 5 --json number,title,url

# Check if relevant docs exist
grep -rl "KEYWORD" docs/features/ docs/plans/ 2>/dev/null | head -5
```

### Step 3: Reconnaissance (Explore → Concerns → Fan-out → Synthesize)

Before writing, run the reconnaissance routine to surface unknowns and conflicts. Load `RECON.md` for the full pattern. Summary:

1. **Broad scan** — Spawn an Explore agent (thoroughness: "very thorough") to map the affected area: relevant source files, existing tests, recent PRs, related docs.

2. **Surface concerns** — From the scan results, identify what's unclear, conflicting, stale, already-done, or missing. List each as a discrete question.

3. **Fan-out** — Spawn one Explore agent per concern, all in parallel. Each gets a focused research prompt: investigate one specific question, read the actual code, and return a recommendation.

4. **Synthesize** — Reconcile all findings. Produce:
   - What's confirmed (safe to include in the issue as-is)
   - What needs fixing first (pre-requisites the issue should call out)
   - What the issue should NOT include (already done, aspirational, or wrong assumptions)
   - Revised scope (narrower or broader than the original request)

This step catches stale assumptions, dead code, existing coverage, and architectural conflicts BEFORE they get baked into the issue. Skip only for trivially simple issues (typo fixes, config changes).

Load `ISSUE_TEMPLATE.md` and fill it in. Key rules:

1. **Open with context** — A blockquote intro explaining any non-obvious project context. If the issue references a specific system, library, pattern, or concept that isn't universally known, define it here with a link.

2. **Problem section** — State the problem from the reader's perspective. What's broken, missing, or painful? Include "Current behavior" and "Desired outcome."

3. **Definitions section** — If the issue uses 2+ domain-specific terms, add a Definitions table. Each term gets a one-line definition and a link to where the reader can learn more.

4. **Solution sketch** — Brief description of the approach. Not a full plan (that's `/do-plan`'s job), but enough that the planner knows the direction.

5. **Downstream context** — Explicitly state what happens next: "This issue will be consumed by `/do-plan` to produce a plan document at `docs/plans/{slug}.md`."

### Step 5: Pre-Publish Checklist

Load `CHECKLIST.md` and verify every item before creating the issue.

### Step 6: Create the Issue

```bash
TYPE="feature"  # or "bug" or "chore"

gh issue create \
  --title "Brief, specific title" \
  --label "$TYPE" \
  --body "$(cat /tmp/issue_body.md)"
```

### Step 7: Report

```
Issue created: #{number} — {title}
URL: {url}

Ready for /do-plan when you are.
```

## Integration with SDLC Pipeline

This skill is invoked by `/sdlc` at **Step 1: Ensure a GitHub Issue Exists**. The issue it creates becomes the input for `/do-plan`, which reads:

- The **Problem** section to understand what needs fixing
- The **Solution sketch** to understand the intended direction
- The **Definitions** to understand domain vocabulary
- The **Acceptance criteria** to know when the plan is complete

Quality here directly determines plan quality downstream.

## Anti-Patterns

- **Insider jargon without definitions** — "Fix the Observer's steering loop" tells a stranger nothing. Define Observer, define steering loop.
- **Vague problem statements** — "Improve issue quality" is not a problem. "Issues reference undefined terms, causing `/do-plan` to produce vague plans" is a problem.
- **Solution-only issues** — "Add a YAML config" without stating what problem the config solves. Always lead with the problem.
- **Copy-paste from chat** — Raw conversation messages aren't issues. Rewrite from the reader's perspective.
- **Missing links** — If you reference a file, repo, PR, or concept, link to it. The reader shouldn't have to search.
