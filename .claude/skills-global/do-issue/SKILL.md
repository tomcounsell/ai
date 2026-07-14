---
name: do-issue
description: "Create a self-contained GitHub issue ready for planning. Triggered by 'create an issue', 'file an issue', 'track this', or by /sdlc at Step 1."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
argument-hint: "<title or description>"
---

# Create Issue (Quality Issue Creator)

Create a GitHub issue that is a self-contained document a stranger could understand. Every issue must teach the reader what they need to know — define terms, link sources, and state the problem from the reader's perspective. The `/do-plan` skill reads the issue as its primary input: quality here directly determines plan quality downstream.

## Repo Context Probe

If `.claude/skill-context/do-issue.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo layers its SDLC automation onto this generic baseline: a stage/status marker to write at the start and end, cross-repo `gh` targeting, the canonical doc locations to search for related context, and the plan-doc path convention the issue should reference downstream. When the file is absent (the common case in a foreign repo), this skill runs entirely on `git` and `gh` — no repo-specific tooling required.

## Core Philosophy: Think Like a Teacher

The reader of your issue has general software engineering experience but **zero context about this specific codebase**. Every named concept that isn't common knowledge needs:

1. A one-sentence definition
2. A link to its source (repo, docs page, file path, or RFC)

This isn't optional politeness — it's functional. Undefined terms produce vague plans. Defined terms produce precise plans.

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `RECON.md` | After Step 2, before writing — run the reconnaissance routine |
| `ISSUE_TEMPLATE.md` | Writing the issue body (use as the structural skeleton) |
| `CHECKLIST.md` | Before publishing — run every check, fix failures |

## Cross-Repo Resolution

By default `gh` targets the repository of the current working directory. If the context file declares a cross-repo targeting mechanism (e.g. a `GH_REPO` env var), honor it so `gh` commands hit the intended repository.

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

# Check if relevant docs exist (the context file may name canonical doc
# locations; the generic default searches tracked docs)
git grep -l "KEYWORD" -- '*.md' docs/ 2>/dev/null | head -5
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

### Step 4: Write the Issue Body

Load `ISSUE_TEMPLATE.md` and fill it in. Key rules:

1. **Open with context** — A blockquote intro explaining any non-obvious project context. If the issue references a specific system, library, pattern, or concept that isn't universally known, define it here with a link.

2. **Problem section** — State the problem from the reader's perspective. What's broken, missing, or painful? Include "Current behavior" and "Desired outcome."

3. **Definitions section** — If the issue uses 2+ domain-specific terms, add a Definitions table. Each term gets a one-line definition and a link to where the reader can learn more.

4. **Solution sketch** — Brief description of the approach. Not a full plan (that's `/do-plan`'s job), but enough that the planner knows the direction. **For architectural or structural problems where the root cause is still uncertain, write open questions here instead of approaches — do-plan will not challenge a concrete sketch, it will execute it.**

5. **Downstream context** — Explicitly state what happens next: "This issue will be consumed by `/do-plan` to produce a plan document." If the context file declares the repo's plan-doc path convention, name the concrete path.

### Step 5: Pre-Publish Checklist

Load `CHECKLIST.md` and verify every item before creating the issue.

### Step 6: Draft, Verify, and Create the Issue (single bash invocation)

**Single-shell invariant (load-bearing):** The whole sequence below — allocate scratch path, write body, verify anchor, publish, cleanup — MUST run inside ONE bash tool invocation. Each Bash tool call spawns a fresh shell with a new `$$`, so splitting these steps across calls loses `OWNER_PID`/`OWNER_TS`/`ANCHOR`/`DRAFT` and breaks anchor verification. Do not split.

```bash
# Per-invocation draft path (mktemp ensures no collision with any concurrent agent).
# Anchor header proves the draft we publish is the draft we wrote.
# DO NOT "simplify" the anchor check away — it defends against another agent
# clobbering the scratch file between write and publish.
DRAFT=$(mktemp "${TMPDIR:-/tmp}/issue_body.XXXXXX") || { echo "ERROR: mktemp failed" >&2; exit 1; }
OWNER_PID=$$
OWNER_TS=$(date +%s)
ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"

# Write the anchor as the first line, then the issue body.
# Anchor goes via printf (expands ${ANCHOR}); the body heredoc may be quoted or
# unquoted depending on whether you need shell expansion in the body.
printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"
cat >> "$DRAFT" << 'BODY'
…replace this heredoc with the actual issue body from your draft…
BODY

# Verify the anchor BEFORE publishing. Mismatch = a foreign agent clobbered
# the file, or our own write failed — never publish unknown content.
if ! head -1 "$DRAFT" | grep -qF "<!-- ${ANCHOR} -->"; then
  echo "ERROR: draft anchor mismatch — refusing to publish unknown content" >&2
  echo "  expected first line: <!-- ${ANCHOR} -->" >&2
  echo "  actual first line:   $(head -1 "$DRAFT")" >&2
  rm -f "$DRAFT"
  exit 1
fi

TYPE="feature"  # or "bug" or "chore"

gh issue create \
  --title "Brief, specific title" \
  --label "$TYPE" \
  --body "$(cat "$DRAFT")"

# Best-effort cleanup; mktemp paths live under $TMPDIR and the OS reaps stragglers.
rm -f "$DRAFT"
```

### Step 7: Report

```
Issue created: #{number} — {title}
URL: {url}

Ready for /do-plan when you are.
```

## Integration with SDLC Pipeline

This skill is invoked by the repo's SDLC router (in this repo: `/sdlc`) at **Step 1: Ensure a GitHub Issue Exists**. The issue it creates becomes the input for `/do-plan`, which reads:

- The **Problem** section to understand what needs fixing
- The **Solution sketch** to understand the intended direction
- The **Definitions** to understand domain vocabulary
- The **Acceptance criteria** to know when the plan is complete

## Anti-Patterns

- **Insider jargon without definitions** — "Fix the Observer's steering loop" tells a stranger nothing. Define Observer, define steering loop.
- **Vague problem statements** — "Improve issue quality" is not a problem. "Issues reference undefined terms, causing `/do-plan` to produce vague plans" is a problem.
- **Solution-only issues** — "Add a YAML config" without stating what problem the config solves. Always lead with the problem.
- **Copy-paste from chat** — Raw conversation messages aren't issues. Rewrite from the reader's perspective.
- **Missing links** — If you reference a file, repo, PR, or concept, link to it. The reader shouldn't have to search.
