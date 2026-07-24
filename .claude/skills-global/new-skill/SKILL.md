---
name: new-skill
description: Use when creating a Claude Code skill, subagent, or tool, or capturing this session as a skill. Triggered by 'new skill', 'new agent', 'skillify', 'capture this as a skill', 'save this workflow'.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
argument-hint: "<skill-name>"
disable-model-invocation: true
---

# New Skill

## Repo Context Probe

If `.claude/skill-context/new-skill.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo declares its own scaffolding conventions — most commonly how to create a **project tool / CLI** (entry-point registration, packaging, where reference implementations live) and its global-vs-project-only skill placement rules. When the file is absent, this skill creates Claude Code skills and subagents using the portable, spec-compliant patterns below; "create a tool" without a context file falls back to the generic project-tool guidance in `Quick start`.

## What this skill does

Creates Claude Code skills and subagents from scratch, following canonical patterns. Guides through naming, scoping, structuring with progressive disclosure, and registering the artifact. The result is a complete, spec-compliant artifact ready for use. When a repo declares tool-creation conventions (see the probe above), it also scaffolds project tools to that repo's standard.

## When to load sub-files

- Creating a Claude Code skill → read [SKILL_TEMPLATE.md](SKILL_TEMPLATE.md) for the skeleton
- Creating a workflow-capture skill (step-based process with success criteria) → read [WORKFLOW_TEMPLATE.md](WORKFLOW_TEMPLATE.md)
- Capturing this session's repeatable process into a skill (the "skillify" flow) → read [SESSION_CAPTURE.md](SESSION_CAPTURE.md)
- Creating a subagent (`.claude/agents/`) → read [AGENT.md](AGENT.md)
- Creating a project tool / CLI → follow the repo's tool conventions declared in `.claude/skill-context/new-skill.md` if present; otherwise scaffold a small CLI with an entry point registered in the project's package manifest
- Need current Anthropic field specs, substitution variable docs, or a canonical skill example → consult the `audit-skills` skill's bundled `references/` (installed alongside it on every machine), if available

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
| `agent` | No | Which subagent type to use when `context: fork` is set. |
| `argument-hint` | No | Hint shown during autocomplete when the skill expects `$ARGUMENTS`. |
| `model` | No | Model to use when this skill is active. |

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
