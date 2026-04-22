# Subagent Standards

Rules the `/claude-standards` skill uses to audit subagent definitions under `.claude/agents/*.md`.

**Asset location:** `.claude/agents/<name>.md` — each file defines a custom subagent invocable via the Agent tool.

---

## Rules

### 1. Subagents that rely on skills must list them in a `skills:` frontmatter field

Subagents run in isolated execution contexts and do **not** automatically inherit skills from the main conversation. A custom subagent can only use a skill if its frontmatter includes that skill's name in the `skills:` field. Built-in agents (Explore, Plan, Verify, etc.) cannot access skills at all.

Auditing this requires inferring intent, which is judgment-heavy. Report as **INFO** only: for each subagent that has no `skills:` field, list it so the author can confirm the subagent does not depend on any skill. Never auto-fix.

_Additional rules pending — more standards to be added as training material arrives._

---

## Auto-fix eligible

- (none)
