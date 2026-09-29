---
name: sdlc
description: "Single-stage SDLC router: assess where work stands, dispatch ONE sub-skill, return. The PM session handles progression."
context: fork
---

# SDLC — Single-Stage Router

Assess where the work stands, invoke ONE sub-skill, and return; the PM session re-invokes this
skill after each stage. For a local session with no PM loop, use `/do-sdlc` to supervise the whole
pipeline.

Read `.claude/skills-global/do-sdlc/SKILL.md` and execute its **router mode**: Steps 1–4 (Resolve →
Ensure the Tracking Session → Assess Current State → Dispatch ONE Sub-Skill), once, then return.
The supervisor loop (Step 5+) and the report/release steps belong to `/do-sdlc`. Honor its repo
context probe (`docs/sdlc/do-sdlc.md`).

## Rules

1. Delegate all work: code goes through `/do-build` or `/do-patch`, tests through `/do-test`,
   plans through `/do-plan`. Never do them directly.
2. Every piece of work needs a GitHub issue, and every code change a plan doc, before BUILD.
3. Never commit to main; code goes to `session/{slug}` branches.
4. Never loop: invoke one sub-skill, then return.

## Router-mode dispatch notes

- **Dispatch via `sdlc-tool next-skill`**, and record it with `sdlc-tool dispatch record` before
  invoking the returned skill. Surface `blocked` decisions to the PM; never guess an alternative.
- **Terminal decision** (`{"decision": "terminal", ...}`): the lane is finished. Return and report
  it complete with its `reason` and `evidence`; record and invoke nothing. This is success.
- **Live-ref cross-check:** `gh pr list --head session/{slug} --state open` queries live refs
  because the `--search` index lags GitHub (Step 3c).
- **Merge gate (row 10):** `/do-merge` fires only when REVIEW and DOCS are complete, the PR merge
  state is CLEAN, CI is all-passing, and the recorded REVIEW verdict is APPROVED at the current
  head (see the guard table in the merged body).

## Pipeline Stages Reference

| Stage | Skill | Dev Model | Effort | Notes |
|-------|-------|-----------|--------|-------|
| ISSUE | /do-issue | sonnet | medium | Or already exists |
| PLAN | /do-plan {slug} | opus | medium | Gate: design |
| CRITIQUE | /do-plan-critique | opus | medium | Gate: adversarial review |
| BUILD | /do-build {plan or issue} | opus | medium | Plan execution |
| TEST | /do-test | opus | medium | Runs and triages suites |
| PATCH | /do-patch | opus | low | Targeted fix (see resume rules in PM persona) |
| REVIEW | /do-pr-review | opus | high | Last judgment gate before merge |
| DOCS | /do-docs | opus | low | Doc cascade |
| MERGE | /do-merge {pr_number} | opus | medium | Programmatic merge gate: verifies all stages, then merges |

**Dev Model** is what the PM passes via `--model` when spawning a dev session for that stage
(Stage→Model Dispatch Table in the PM persona); effort comes from each stage skill's frontmatter.
State transitions live in `agent/pipeline_graph.py` and dispatch logic in `agent/sdlc_router.py`,
both reached at runtime through `sdlc-tool`.
