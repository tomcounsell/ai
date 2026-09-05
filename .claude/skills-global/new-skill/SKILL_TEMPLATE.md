---
name: skill-name
description: "[Third-person statement of what the skill does and produces]. Use when [most important observable condition], [second condition], or [third condition]."
allowed-tools: Read, Grep, Glob, Bash
---

# Skill Name

## What this skill does
One paragraph. What problem does it solve? What does Claude do differently when this skill is active?

## When to load sub-files
- [Condition A] → read [SUB_FILE_A.md](SUB_FILE_A.md)
- [Condition B] → read [SUB_FILE_B.md](SUB_FILE_B.md)
- [Condition C] → read [SUB_FILE_C.md](SUB_FILE_C.md)

## Quick start
Step-by-step instructions for the most common use of this skill.
Enough to complete the task without reading sub-files.

## Scripts
- `scripts/example.sh` — brief description of what it does and when to use it

---

**Writing the `description`** (delete this block once the frontmatter is filled in):

Both halves are required. The what-statement comes first, because the skill listing
truncates from the end and a what-clause parked last is the first thing to vanish. Then the
conditions, most important first.

Target 250–350 characters, hard cap 1024. Third person, no rhetorical tails, no capability
tables or procedure. Full rules: [SKILL.md](SKILL.md) "Description field rules".

Worked example:

```yaml
description: "Reconstructs the day's work from git history, groups it into time-blocked
  calendar events, and writes them to the repo's mapped calendar. Use when logging or
  syncing a day's work to a calendar, reviewing what was done today, or backfilling a
  prior day."
```

If this skill sets `disable-model-invocation: true`, the description never reaches model
context. Write a short menu label instead, with no "Use when" clause.
