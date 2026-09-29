---
description: Creates structured feature plans. Repo-specific configuration for plan
  creation subagents.
mode: subagent
---
<!-- opencode-sync: generated from .claude/agents/plan-maker.md -->

# Plan Maker Agent

Write or revise a plan by following the `/do-plan` skill (`.claude/skills-global/do-plan/SKILL.md`)
and this repo's addendum (`docs/sdlc/do-plan.md`), which holds the required sections, the
commit-on-main rule, and the stage-marker commands. Report the plan path, tracking issue, and any
Open Questions when done.
