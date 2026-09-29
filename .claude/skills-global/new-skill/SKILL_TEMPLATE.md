---
name: skill-name
description: Use when [primary trigger]. [Trigger phrases users say]. (Aim for 120 chars or fewer.)
# model / effort: choose from the placement table in new-skill/SKILL.md, or delete these lines
# to inherit. Leave both unset for a skill invoked mid-task.
# model: sonnet
# effort: low
---

# Skill Name

[Objective, in one short paragraph: what the skill produces, for whom, and why.]

Done when:
- [a checkable condition, e.g. "PR open with CI green", "report lists every item with a verdict"]

## Constraints

- [Safety rules, permissions, protected resources. Gates before outward or irreversible actions.]

## Facts

- [Paths, commands, formats, conventions, and incident-learned gotchas the model cannot infer]

## Procedure

[Only the steps whose order or method is non-obvious. Delete this section if there are none.]

## Sub-files

- [Condition] → [SUB_FILE.md](SUB_FILE.md)
- `scripts/example.sh`: [what it does; deterministic work belongs in scripts]
