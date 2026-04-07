# PR Review Sub-Skills

This directory contains focused sub-skills that decompose the `/do-pr-review` workflow
into single-responsibility phases. Each sub-skill receives pre-resolved context via
`$SDLC_*` environment variables (injected by `agent/sdk_client.py`, issue #420).

## Sub-Skills

| File | Type | Responsibility |
|------|------|----------------|
| `checkout.md` | Mechanical | Clean git state, checkout PR branch |
| `code-review.md` | Judgment | Read files, analyze diff, classify findings |
| `screenshot.md` | Mechanical | Start app, capture UI screenshots |
| `post-review.md` | Mechanical | Format findings, post review to GitHub |

## Context Variables

All sub-skills can reference these environment variables (when available):

| Variable | Source | Example |
|----------|--------|---------|
| `$SDLC_PR_NUMBER` | `AgentSession.pr_url` | `220` |
| `$SDLC_PR_BRANCH` | `AgentSession.branch_name` | `session/my-feature` |
| `$SDLC_SLUG` | `AgentSession.work_item_slug` | `my-feature` |
| `$SDLC_PLAN_PATH` | `AgentSession.plan_url` | `docs/plans/my-feature.md` |
| `$SDLC_ISSUE_NUMBER` | `AgentSession.issue_url` | `415` |
| `$SDLC_REPO` | `GH_REPO` env var | `tomcounsell/ai` |

## Invocation

Sub-skills are guidance documents loaded by the parent `SKILL.md` orchestrator.
They are not standalone slash commands. The orchestrator references them for
focused instructions at each phase of the review process.

## Fallback

All sub-skills include fallback instructions for when `$SDLC_*` variables are
absent (backward compatibility). Skills can still derive values from git state,
`gh` CLI, or coaching message context.
