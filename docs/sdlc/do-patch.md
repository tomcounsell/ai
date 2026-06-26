# do-patch addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-patch/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Cross-Repo `gh` Targeting

For cross-project work, the `GH_REPO` environment variable is set automatically
by `sdk_client.py`. The `gh` CLI natively respects it, so all `gh` commands
target the correct repository — no `--repo` flags or manual parsing needed.

## Branch → Slug → Plan-Doc Convention (Build Context Recovery)

To recover the plan when the caller didn't pass a path: the branch is
`session/{slug}`; the plan lives at `docs/plans/{slug}.md`:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"
```

## Trace & Verify Reference

The full Trace & Verify protocol reference lives at
`docs/features/trace-and-verify.md`.

## Lint / Format Commands

This repo uses `ruff` for both. The generic body defers lint to here:
`python -m ruff check .` and `python -m ruff format --check .`. Do NOT run
`black`. The pre-commit hook auto-fixes via `ruff format` + `ruff check --fix`
and the PostToolUse `format_file.py` hook formats individual files after every
Write/Edit, so on final (non-`--no-verify`) commits manual lint is redundant.

## Plan-Checkbox Sync (Step 3.5 mechanism)

This repo bundles a criterion tick into the fix commit. Read the builder's
reported `criterion_addressed` from Step 2, then tick it in the SAME `git add -A`
as the code fix:

```bash
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"
TICK_SUFFIX=""
if [ -n "$CRITERION_ADDRESSED" ] && [ "$CRITERION_ADDRESSED" != "null" ]; then
  if "${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.plan_checkbox_writer tick "$PLAN_PATH" --criterion "$CRITERION_ADDRESSED"; then
    TICK_SUFFIX=" — addresses \"$CRITERION_ADDRESSED\""
  else
    # NON-FATAL: MATCH_AMBIGUOUS / MATCH_NOT_FOUND. The commit still happens
    # (code change only); the next /do-pr-review round reconciles via tick/untick.
    echo "WARN: plan_checkbox_writer failed for criterion: $CRITERION_ADDRESSED" >&2
  fi
fi
git add -A
git commit -m "fix(#${SDLC_ISSUE_NUMBER}): ${SUMMARY}${TICK_SUFFIX}"
git push origin "HEAD:${BRANCH}"
```

The single-commit invariant is what keeps the merge-gate review-comment
freshness check passing on the next attempt. Never `git commit --amend`.

## Worktree Context

Patches apply inside the worktree at `.worktrees/{slug}/`, not the main checkout. The branch is `session/{slug}`. Never run `git checkout session/{slug}` from main — the worktree IS the checkout.

## Ruff Auto-Fix

This repo's pre-commit hook runs `ruff format` + `ruff check --fix` automatically. Do not manually fix whitespace or import order — commit and let the hook clean up. If the hook fails on a non-fixable lint error, fix that specific error and re-commit.

## Test Isolation Regression

After patching, re-run only the affected unit tests first (`pytest tests/unit/test_*.py -x -q`), then run the full unit suite. Do not skip the isolated run — it surfaces scope issues before the full suite.

## Redis Safety

If the patch touches any Redis operation, double-check it is project-scoped. Raw `r.delete`, `r.srem`, `r.sadd` calls on unscoped keys will corrupt production data. Use `instance.delete()` or `Model.rebuild_indexes()` instead.

## Bridge/Worker Restart

If the patch touches `bridge/`, `agent/`, or `worker/`, restart the bridge after committing:
```bash
./scripts/valor-service.sh restart
```
Verify with `tail -5 logs/bridge.log` — must show "Connected to Telegram".
