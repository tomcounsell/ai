---
name: new-skill
description: Use when creating a new Claude Code skill directory and SKILL.md. Also use when the user says 'create a skill', 'new skill', or 'add a skill'. Handles both shared (~/.claude/skills/) and project-specific (.claude/skills/) skills.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# New Skill

## What this skill does

Creates a new Claude Code skill from scratch, following the canonical template structure. It guides the agent through choosing a skill name, writing trigger-oriented descriptions, structuring the SKILL.md with progressive disclosure, and placing the skill in the correct scope (shared personal vs. project-specific). The result is a complete, spec-compliant skill directory ready for use.

## When to load sub-files

- Creating any new skill → read [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) and use it as the starting skeleton

## Quick start

1. **Choose a name**: lowercase, hyphenated (e.g., `my-new-skill`). Must match the directory name.
2. **Choose scope**:
   - Project-specific: `.claude/skills/<name>/SKILL.md` (only this repo)
   - Shared/personal: `~/.claude/skills/<name>/SKILL.md` (all repos for this user)
3. **Create the directory**: `mkdir -p <scope-path>/<name>/`
4. **Copy the template**: Read [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) and save it as `<name>/SKILL.md`
5. **Fill in the frontmatter**: Set `name`, `description`, and `allowed-tools`
6. **Write the body**: Follow the template sections — "What this skill does", "When to load sub-files", "Quick start"
7. **Test discovery**: Invoke `/name` in Claude Code and verify the skill loads

## Description field rules

The `description` field in frontmatter is what Claude uses to decide whether to load a skill. It must be written carefully.

**Format**: Third person, trigger-oriented. Start with "Use when..." not "This skill...".

**Structure**: `Use when [primary trigger]. Also use when [secondary triggers]. Handles [capability list].`

**Examples**:
- Good: `Use when creating a new Claude Code skill directory and SKILL.md. Also use when the user says 'create a skill'. Handles both shared and project-specific skills.`
- Bad: `This skill helps create new skills for Claude Code.`
- Bad: `A tool for making skills.`

**Rules**:
1. Must describe WHEN to use the skill, not WHAT it is
2. Include natural language phrases users might say (e.g., "create a skill", "add a command")
3. Written in third person — Claude reads this to decide if the skill matches
4. Max 1024 characters (hard limit from spec)
5. Aim for under 200 characters to stay within the 2% context budget across all skills

## Field constraints

| Field | Required | Constraints |
|-------|----------|-------------|
| `name` | Yes | Must match directory name. Lowercase, hyphenated. |
| `description` | Yes | Max 1024 chars. Third person, trigger-oriented ("Use when..."). |
| `allowed-tools` | No | Comma-separated tool names. Restricts which tools the skill can use. Omit to allow all. |
| `hooks` | No | YAML block defining validation hooks that run on Stop events. |
| `disable-model-invocation` | No | Set `true` to prevent Claude from auto-triggering. Use for infrastructure skills (setup, update). |
| `user-invocable` | No | Set `false` to hide from `/slash-command` menu. Use for background reference skills. |
| `context` | No | Set `fork` to run in a separate context. Use for long-running or parallel tasks. |

## Skill directory structure

A minimal skill needs only `SKILL.md`. Larger skills use progressive disclosure:

```
.claude/skills/<name>/
├── SKILL.md              # Main file (REQUIRED, < 500 lines)
├── SUB_FILE.md           # Reference material loaded on demand
├── ANOTHER_SUB_FILE.md   # More reference material
├── scripts/
│   └── validate.sh       # Executable automation (saves context tokens)
└── references/
    └── API_REFERENCE.md  # Detailed specs, schemas, examples
```

**Key principle**: SKILL.md is a navigator. It tells Claude what the skill does and when to read sub-files. Detailed instructions, templates, and reference material go in sub-files that are loaded only when needed.

## Debugging

If a skill is not being discovered or loaded:

1. **Check the name**: `name` in frontmatter must exactly match the directory name
2. **Check frontmatter syntax**: YAML must be valid, enclosed in `---` delimiters
3. **Check description**: Must contain trigger words that match the user's request
4. **Check location**: Skill must be in `.claude/skills/` (project) or `~/.claude/skills/` (personal)
5. **Check `disable-model-invocation`**: If `true`, Claude will not auto-load it — only `/slash-command` works
6. **Check line count**: SKILL.md should stay under 500 lines for optimal loading
7. **Restart Claude Code**: Skill discovery happens at session start; new skills need a restart

## Anti-patterns

- **Monolithic SKILL.md**: Do not put everything in one file. Extract templates, examples, and reference material into sub-files loaded conditionally.
- **Vague descriptions**: "A useful skill for doing things" will never match. Be specific about triggers.
- **First-person descriptions**: "I help create skills" is wrong. Use "Use when creating skills."
- **Missing trigger phrases**: If users say "make a command" but the description only says "create a skill", it will not match. Include synonyms.
- **Hardcoded paths**: Use relative paths for sub-file references so skills work in both project and personal scope.
- **Over-permissive allowed-tools**: Only list tools the skill actually needs. Fewer tools = smaller attack surface.
- **Skipping progressive disclosure**: A 600-line SKILL.md that loads every time wastes context tokens. Split it.

## Version history

- v1.0.0 (2026-02-22): Initial — extracted from new-valor-skill as a generic, repo-agnostic skill creator
