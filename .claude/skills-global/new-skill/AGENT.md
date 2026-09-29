---
name: agent
description: Reference for creating a Claude Code subagent definition.
---

# Creating a Claude Code Agent

An agent definition is a system prompt plus frontmatter, saved at `.claude/agents/<name>.md`
(project) or `~/.claude/agents/<name>.md` (user). Create one only for parallelism or for
fresh-context isolation, for example so the author does not review its own work. Otherwise a
skill does the job.

```markdown
---
name: agent-name
description: "When to delegate to it, specifically. The caller reads this to decide."
effort: medium
# model: omit to inherit the caller's model; set per the placement table in new-skill/SKILL.md
# disallowedTools: Write, Edit, NotebookEdit
---

[Objective: what this agent produces and for whom.]

Done when: [checkable conditions, and what to return: format, length, fields].

[Constraints: what it must not touch or do, and the gates before outward or irreversible actions.]
[Facts it cannot infer: paths, commands, conventions.]
```

The body follows the same standard as a skill body (see new-skill/SKILL.md, "Writing the body").
The caller relays the agent's final message, so define the report's shape.

## Model and effort

- **`model`**: omit it to inherit the caller's model, which is usually right. Set it only when
  the role needs a specific tier (placement table in new-skill/SKILL.md): `haiku` for a one-shot
  classifier or a runner that executes one command and reports; `opus` for a gate whose misses
  escape (review, critique, verification).
- **`effort`**: set it here. The Agent tool can override `model` per call but not `effort`, so a
  role that needs a fixed effort needs it in its definition. Use `low` for well-specified
  execution, `medium` by default, and `high` for gates.

## Tool access

- `tools: Read, Grep, Glob` is an allowlist: the agent gets only those tools.
- `disallowedTools: Write, Edit, NotebookEdit` is a denylist: it removes those tools and keeps
  every other one, including tools added later. Prefer it for read-only agents.
- A skill's `allowed-tools` is different: it pre-approves tools and does not restrict them.

## Other fields

`color` (UI label), `hooks` (lifecycle hooks such as a PostToolUse formatter), `skills`
(skills preloaded into the agent's context), and `permissionMode`. For the current field list,
see Anthropic's subagent docs.
