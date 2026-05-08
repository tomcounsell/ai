# do-pr-review addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-pr-review/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Documentation Gate

Every PR must have a corresponding `docs/features/{slug}.md` if the plan's `## Documentation` section specified one. Verify this file exists before approving. Missing docs are a blocker.

## Plan Section Compliance

Verify the plan included all four required sections (validated by hooks):
- `## Documentation` — has checkbox tasks with `docs/features/` paths
- `## Update System` — addresses `migrations.py` for Popoto changes
- `## Agent Integration` — addresses MCP exposure for new Python tools
- `## Test Impact` — lists affected tests with UPDATE/DELETE/REPLACE

If the PR was built from a plan missing any section, flag it as a blocker.

## Ruff and Test Gates

A PR must not merge with:
- `ruff check .` failures (exit non-zero)
- `ruff format --check .` failures
- Failing unit tests

These are hard gates. No exceptions.

## Multi-Machine Compatibility

If the PR adds new environment variables, verify they are in `.env.example` and `config/settings.py`. If the PR adds new migrations, verify they are registered in `MIGRATIONS` in `scripts/update/migrations.py`.

## Bridge/Worker Changes

If the PR modifies `bridge/`, `agent/`, or `worker/`, flag for restart-after-deploy. The reviewer should note whether the change requires a service restart on all machines.

## Multi-Judge Consensus

This repo opts in to multi-judge consensus at the REVIEW stage by default
(`SDLC_REVIEW_JUDGES=code-quality,risk`, `SDLC_REVIEW_K=2`). Reviewers should
expect:

- Two per-judge comments (`## Review (Judge code-quality):`, `## Review (Judge risk):`)
  posted **before** the aggregate `## Review:` comment that `/do-merge` reads.
- The aggregate verdict is derived by `agent.sdlc_review_consensus.compute_consensus`
  with `rule="any-blocker-wins"` — any judge raising a blocker forces
  `CHANGES_REQUESTED`.
- The OUTCOME block includes `judges_run` (int) and `consensus_disagreement` (bool)
  side-fields when multi-judge runs.
- Cost containment: `docs-only` / `lockfile-only` PRs (classified by
  `python -m scripts.pr_shape_classify --pr $PR_NUMBER`, the same module
  `/do-merge` invokes) force the legacy single-judge path. Operators can also
  set `SDLC_REVIEW_JUDGES=none` or `SDLC_REVIEW_K=1` as independent kill
  switches.

Full design: [`docs/features/multi-judge-consensus.md`](../features/multi-judge-consensus.md).
The shape classifier is shared with `/do-merge` — see
[`docs/features/pr-shape-aware-merge-gates.md`](../features/pr-shape-aware-merge-gates.md).

## UI Screenshots

For any PR that touches `ui/`, include before/after screenshots of the actual running app (not mockups). Capture via BYOB MCP (`mcp__byob__browser_*`) — the only browser surface — so the screenshot reflects the user's real, logged-in Chrome session. See `.claude/skills/do-pr-review/SKILL.md` and `sub-skills/screenshot.md`.

For background, see [`docs/features/byob-browser-control.md`](../features/byob-browser-control.md).
