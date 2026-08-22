---
name: sdlc
description: "Single-stage router for development work. Assesses current state, dispatches ONE sub-skill, then returns. The PM session handles pipeline progression."
context: fork
---

# SDLC — Single-Stage Router

This skill is a **router**, not an orchestrator. It assesses where work stands, invokes ONE
sub-skill, and returns. The PM session handles pipeline progression by re-invoking this skill
after each stage completes. In a local Claude Code session (no PM loop), use `/do-sdlc` to
supervise the full pipeline in one invocation.

You MUST NOT write code, run tests, or create plans directly -- delegate everything to sub-skills.

**The substantive procedure lives in the merged SDLC skill.** Read
`.claude/skills-global/do-sdlc/SKILL.md` and execute its **router mode** — Steps 1–4 (Resolve →
Ensure the Tracking Session → Assess Current State → Dispatch ONE Sub-Skill) — then **return**.
Do not run the supervisor loop (Step 5+) or the final-report/release steps; those belong to
`/do-sdlc`. Honor the repo context probe it declares (`docs/sdlc/do-sdlc.md`).

## Router-mode dispatch notes

- **Dispatch via `sdlc-tool next-skill`** (Step 4 of the merged body). Record the dispatch with
  `sdlc-tool dispatch record` before invoking the returned skill. Surface `blocked` decisions to
  the PM; never guess an alternative skill.
- **Live-ref cross-check:** `gh pr list --head session/{slug} --state open` queries live refs
  because the `--search` index lags GitHub (Step 3c of the merged body).
- **Merge gate (row 10):** `/do-merge` fires only when REVIEW and DOCS are complete, the PR merge
  state is CLEAN, CI is all-passing, and the recorded REVIEW verdict is APPROVED at the current
  head — see the guard table in the merged body.

## Hard Rules

1. **NEVER write code directly** -- invoke `/do-build` or `/do-patch`
2. **NEVER run tests directly** -- invoke `/do-test`
3. **NEVER create plans directly** -- invoke `/do-plan`
4. **NEVER skip the issue** -- every piece of work needs a GitHub issue
5. **NEVER skip the plan** -- every code change needs a plan doc first
6. **NEVER commit to main** -- all code goes to `session/{slug}` branches
7. **NEVER loop** -- invoke one sub-skill, then return. The PM session handles progression.

## Pipeline Stages Reference

| Stage | Skill | Dev Model | Notes |
|-------|-------|-----------|-------|
| ISSUE | /do-issue | — | Or already exists |
| PLAN | /do-plan {slug} | opus | Adversarial design |
| CRITIQUE | /do-plan-critique | opus | Adversarial review |
| BUILD | /do-build {plan or issue} | sonnet | Plan execution |
| TEST | /do-test | sonnet | Deterministic runs |
| PATCH | /do-patch | sonnet | Targeted fix (see resume rules in PM persona) |
| REVIEW | /do-pr-review | opus | Code review judgment |
| DOCS | /do-docs | sonnet | Structured writing |
| MERGE | /do-merge {pr_number} | sonnet | Programmatic merge gate: verifies all stages, then merges |

The **Dev Model** column shows the model the PM should pass via `--model` when spawning a dev
session for that stage (see Stage→Model Dispatch Table in PM persona). Pipeline state transitions
are defined in `agent/pipeline_graph.py`; dispatch logic in `agent/sdlc_router.py` — both
accessed at runtime via `sdlc-tool`.
