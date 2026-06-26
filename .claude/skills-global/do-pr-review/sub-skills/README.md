# PR Review Sub-Skills

This directory contains focused sub-skills that decompose the `/do-pr-review` workflow
into single-responsibility phases. Each sub-skill receives pre-resolved context — in
the generic case from `$ARGUMENTS` and `gh`; when the repo-context file declares
pre-resolved `$SDLC_*` environment variables (an SDLC pipeline injecting them), the
sub-skills prefer those and fall back to git/gh resolution.

## Sub-Skills

| File | Type | Responsibility |
|------|------|----------------|
| `checkout.md` | Mechanical | Clean git state, checkout PR branch |
| `code-review.md` | Judgment | Parse disclosures, load prior reviews, traverse Rubric, classify findings, derive mechanical verdict |
| `screenshot.md` | Mechanical | Start app, capture UI screenshots |
| `post-review.md` | Mechanical | Format findings, post review to GitHub |

## Context Variables

When the repo-context file declares an SDLC pipeline that injects them, all
sub-skills can reference these environment variables (otherwise resolve from
`$ARGUMENTS`/git/gh):

| Variable | Example |
|----------|---------|
| `$SDLC_PR_NUMBER` | `220` |
| `$SDLC_PR_BRANCH` | `session/my-feature` |
| `$SDLC_SLUG` | `my-feature` |
| `$SDLC_PLAN_PATH` | `docs/plans/my-feature.md` |
| `$SDLC_ISSUE_NUMBER` | `415` |
| `$SDLC_REPO` | `your-org/your-repo` |

## Invocation

Sub-skills are guidance documents loaded by the parent `SKILL.md` orchestrator.
They are not standalone slash commands. The orchestrator references them for
focused instructions at each phase of the review process.

## Fallback

All sub-skills include fallback instructions for when `$SDLC_*` variables are
absent (backward compatibility). Skills can still derive values from git state,
`gh` CLI, or nudge feedback context.
