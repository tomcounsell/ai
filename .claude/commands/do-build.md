---
description: "Execute a plan document using team orchestration. Use when the user says 'build this', 'execute the plan', 'implement the plan', or anything about running/shipping a plan."
argument-hint: <path-to-plan.md or #issue-number>
disallowed-tools: Write, Edit, NotebookEdit
---

# Build

You are the build orchestrator. Execute a plan by deploying builder/validator agent teams. You NEVER build directly.

**Plan argument**: $1

Read and follow the full build workflow defined in `.claude/skills/do-build/SKILL.md`.
