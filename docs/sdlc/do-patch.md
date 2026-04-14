# do-patch addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-patch/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

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
