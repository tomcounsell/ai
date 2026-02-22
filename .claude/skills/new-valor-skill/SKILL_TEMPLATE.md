# Canonical SKILL.md Template

Copy this structure when creating any new `.claude/skills/*/SKILL.md` file.

---

## Template

```markdown
---
name: skill-name
description: Use when [specific trigger conditions]. Also use when [additional triggers]. Handles [sub-capability 1], [sub-capability 2], and [sub-capability 3].
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

## Version history
- v1.0.0 (YYYY-MM-DD): Initial
```

---

## Description Field Rules

The `description` is how Claude discovers when to use a skill. It must:

- State **what** the skill does and **when** to use it
- Use specific trigger vocabulary, not generic descriptions
- Be non-empty, max 1024 characters, no XML tags

**Too vague** (won't trigger reliably):
```
description: Helps with planning tasks
```

**Specific** (correct):
```
description: Use when discussing a new feature's feasibility, scope, or prerequisites. Also use when the user asks what needs to happen before work begins, wants to create a GitHub issue, or needs background research in the codebase before committing to an approach.
```

## Field Constraints

| Field | Requirements |
|-------|-------------|
| `name` | Lowercase letters, numbers, hyphens only. Max 64 characters. No XML tags. No "anthropic" or "claude". |
| `description` | Non-empty. Max 1024 characters. No XML tags. |
| `allowed-tools` | Optional. Restricts which tools Claude can use when the skill is active. Only supported in Claude Code. |

---

## Adding a New Skill

Follow this checklist every time:

```bash
# 1. Create the directory
mkdir -p .claude/skills/my-new-skill

# 2. Copy this template
cp .claude/skills/new-valor-skill/SKILL_TEMPLATE.md .claude/skills/my-new-skill/SKILL.md

# 3. Edit: name, description, instructions
#    Description must answer: what does it do AND when should it fire?

# 4. Add scripts and sub-files as needed
mkdir -p .claude/skills/my-new-skill/scripts

# 5. Test before committing
#    Restart Claude Code, then ask something that should trigger the skill
claude
# > [ask something matching the description]

# 6. Commit
git add .claude/skills/my-new-skill/
git commit -m "skill: add my-new-skill for [purpose]"
```

---

## Migrating Existing Skills

For each existing skill:

1. **Determine scope.** Team-useful → `.claude/skills/`. Personal workflow → `~/.claude/skills/`.
2. **Flatten nested directories into linked files.** If you had `planning/scoping/SKILL.md` as a separate skill, move it to `planning/SCOPING.md` and reference it from `planning/SKILL.md`.
3. **Audit every description.** Ask: *Would Claude read this and know exactly when to use it versus every other skill?* If not, tighten the trigger language.
4. **Verify discovery:**
```bash
claude
# > What skills are available?
```

---

## Debugging

**Skill doesn't trigger:**
- Is the description specific enough? Add explicit trigger phrases.
- Is the YAML valid? Check for tabs (must be spaces), missing `---` fences, unquoted special characters.
- Is the file in the right path? `ls .claude/skills/*/SKILL.md`

**Run debug mode to see loading errors:**
```bash
claude --debug
```

**Two skills conflict** (Claude picks the wrong one):
Differentiate with distinct vocabulary in the descriptions — not just different phrasing of the same terms.

**Changes not taking effect:**
Skills don't hot-reload. Restart Claude Code after every `SKILL.md` edit.

---

## Sharing with the Team

Skills in `.claude/skills/` are automatically available to all teammates after a `git pull`.

No additional setup required.

The recommended approach for broader distribution (across multiple repos or to external users) is via [Claude Code Plugins](https://code.claude.com/docs/en/plugins), which can bundle skills in a `skills/` directory.
