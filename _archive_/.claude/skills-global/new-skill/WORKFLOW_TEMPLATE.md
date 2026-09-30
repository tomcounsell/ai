# Workflow-Capture Skill Template

Skeleton for skills that capture a repeatable multi-step process (the shape the
[SESSION_CAPTURE.md](SESSION_CAPTURE.md) flow produces). Reference-style skills use
[SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) instead.

## Skeleton

```markdown
---
name: {{skill-name}}
description: {{what the skill does, then when Claude should automatically invoke it, including trigger phrases and example user messages}}
allowed-tools:
  {{minimum tool permission patterns the workflow needs}}
argument-hint: "{{hint showing argument placeholders}}"
context: {{inline or fork -- omit for inline}}
---

# {{Skill Title}}
Description of skill

## Inputs
- `$ARGUMENTS`: Description of the expected input (omit this section if the skill takes no arguments)

## Goal
Clearly stated goal for this workflow. Best if you have clearly defined artifacts or criteria for completion.

## Steps

### 1. Step Name
What to do in this step. Be specific and actionable. Include commands when appropriate.

**Success criteria**: ALWAYS include this! This shows that the step is done and we can move on. Can be a list.

...
```

## Per-step annotations

- **Success criteria** is REQUIRED on every step — it tells the model when it has the confidence to move on.
- **Execution**: `Direct` (default), `Task agent` (straightforward subagents), `Teammate` (agent with true parallelism and inter-agent communication), or `[human]` (user does it). Only specify if not Direct.
- **Artifacts**: Data this step produces that later steps need (e.g., PR number, commit SHA). Only include if later steps depend on it.
- **Human checkpoint**: When to pause and ask the user before proceeding. Include for irreversible actions (merging, sending messages), error judgment (merge conflicts), or output review.
- **Rules**: Hard rules for the workflow. User corrections during the reference session are especially useful here.

## Step structure tips

- Steps that can run concurrently use sub-numbers: 3a, 3b
- Steps requiring the user to act get `[human]` in the title
- Keep simple skills simple — a 2-step skill doesn't need annotations on every step

## Frontmatter rules

- `allowed-tools`: Minimum permissions needed (use patterns like `Bash(gh:*)` not `Bash`)
- `context`: Only set `context: fork` for self-contained skills that don't need mid-process user input.
- `description` is CRITICAL — it tells the model when to auto-invoke. Say what the skill does, then when to use it, and include trigger phrases. Example: "Cherry-pick a PR to a release branch. Use when the user says 'cherry-pick to release', 'CP this PR', 'hotfix'."
- `argument-hint`: Only include if the skill takes parameters. Use `$ARGUMENTS` in the body for substitution.
