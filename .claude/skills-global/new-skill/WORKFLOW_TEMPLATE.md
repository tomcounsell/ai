# Workflow Skill Template

A skeleton for a skill that captures a repeatable multi-stage process, such as the one the
[SESSION_CAPTURE.md](SESSION_CAPTURE.md) flow produces. Use [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md)
for everything else.

```markdown
---
name: {{skill-name}}
description: {{when to use it, with trigger phrases; 120 chars or fewer if possible}}
allowed-tools: {{permission patterns to pre-approve, e.g. Bash(gh pr *)}}
argument-hint: "{{only if it takes arguments}}"
context: fork   # only for self-contained runs that need no mid-process input
# model / effort: see the placement table in new-skill/SKILL.md
---

# {{Skill Title}}

{{Objective: what the workflow produces and why.}} Input: `$ARGUMENTS` is {{…}}.

Done when: {{the finished artifact or state, e.g. "cherry-pick PR open against release/x with CI green"}}.

## Constraints
- {{hard rules, especially corrections the user made in the reference session}}
- {{a human checkpoint before each irreversible or outward step: merge, send, delete, publish}}

## Stages
### 1. {{Stage}}
{{What it must achieve, plus any command or fact the model can't infer.}}
Done: {{only if not obvious from the stage}} · Produces: {{IDs/artifacts later stages need}}
```

Notes:
- A stage earns its own section only when it produces something a later stage needs, has
  a non-obvious completion signal, or sits behind a human checkpoint. Otherwise fold it into
  the objective.
- Stages that can run in parallel get sub-numbers (3a, 3b). Mark stages the user performs with
  `[human]`.
- `allowed-tools` pre-approves; it does not restrict. Use the narrowest patterns the workflow
  needs (`Bash(gh pr *)` rather than `Bash`). To forbid a tool, use `disallowed-tools`.
