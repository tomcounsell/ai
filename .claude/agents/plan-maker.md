---
name: plan-maker
description: Creates structured feature plans. Repo-specific configuration for plan creation subagents.
---

# Plan Maker Agent

Subagent for creating plans in this repository. For the full planning workflow and Shape Up methodology, see `.claude/skills/make-plan/SKILL.md`.

## Repo-Specific Configuration

- **Output**: `docs/plans/{slug}.md`
- **Branch**: Plans are written directly on `main`
- **Tracking**: GitHub Issues with `plan` label via `gh` CLI
- **Validation**: Hooks enforce required sections (see skill for details)
