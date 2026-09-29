---
name: do-issue
description: "Create a self-contained GitHub issue ready for planning. Triggered by 'create an issue', 'file an issue', 'track this', or by /sdlc at Step 1."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
argument-hint: "<title or description>"
effort: medium
---

# Create Issue

File a GitHub issue that a stranger with general engineering experience and zero
knowledge of this codebase could understand and plan from. `/do-plan` reads it as
its primary input, so undefined terms and unverified claims here become vague or
wrong plans downstream. Sometimes the right output is no issue at all.

**Done when:** the central claim survived the falsification checks (or you
stated plainly why nothing was filed), recon shaped the scope, every check in
`CHECKLIST.md` passes, and the issue exists and its number and URL are reported.

## Repo context

If `.claude/skill-context/do-issue.md` exists, read it and honor it: stage
markers, cross-repo `gh` targeting (e.g. `GH_REPO`), doc locations to search,
the label set, and the plan-doc path convention. Without it, use `git` and `gh`
against the current repository.

## Sub-files

| Sub-file | Load when |
|---|---|
| `RECON.md` | Before writing: the reconnaissance routine |
| `ISSUE_TEMPLATE.md` | Writing the body |
| `CHECKLIST.md` | Before publishing |

## 1. Understand and research

Identify the type (bug, feature, chore), the actual problem, and the domain
terms. Search related closed issues, merged PRs, and docs
(`gh issue list --state closed --search`, `gh pr list --state merged --search`,
`git grep` over docs).

**Mode.** Default is *well-scoped*. Choose *blue-sky* only when the requester
signals exploration and writing verifiable acceptance criteria would mean
inventing specifics nobody gave; an unattended run never stalls on this choice.
Record the mode in the body. In blue-sky mode: recon fans out only on cheap
concerns; terms the exploration exists to pin down go in a
`## Fog (Not Yet Specified)` section with the decision hanging on each; and
acceptance criteria are checkable signals that the fog cleared.

## 2. Recon

Run `RECON.md` (broad scan, concerns, parallel fan-out, four-bucket synthesis).
Skip only for trivial issues (typo, config change, single-file bug with an
obvious fix), and then write `## Recon: Skipped` with the justification.

## 3. Try to kill the issue

Before writing, try to disprove the claim, especially a second-hand one from a
subagent, review, or passing observation (a report is a claim, not a fact):
reproduce it or name the artifact that shows it; check the instances that would
falsify a claimed pattern; and look for a test, comment, or doc that already
decided the opposite (then you are proposing a change, not reporting a defect).
If it does not survive, say so where it came up and file nothing.

## 4. Write the body

Fill `ISSUE_TEMPLATE.md`:

- Open with a context blockquote; give every non-common term a one-line
  definition and a link (a Definitions table for 2+ terms).
- Problem before solution, from the reader's perspective, with current behavior
  and desired outcome.
- The solution sketch gives direction, not a plan. When the root cause of an
  architectural problem is still uncertain, write open questions instead of
  approaches: `/do-plan` executes a concrete sketch rather than challenging it.
- Name the downstream consumer (`/do-plan`) and the plan-doc path if the context
  file declares one.

## 5. Check and publish

Run every item in `CHECKLIST.md` and fix failures. Then publish with the body on
stdin (no temp file), choosing a terminator that cannot appear in the body:

```bash
gh issue create --title "<brief, specific title>" [--label <label>] --body-file - <<'ISSUE_BODY_EOF'
<issue body>
ISSUE_BODY_EOF
```

Labels come only from the context file's label set, or from labels
`gh label list` shows exist; if none fits, omit `--label` (a missing label fails
the create).

Report:

```
Issue created: #{number} — {title}
URL: {url}
```
