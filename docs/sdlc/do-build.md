# do-build addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-build/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Lint and Format

This repo uses `ruff` for both formatting and linting. The pre-commit hook auto-fixes all fixable issues via `ruff format` + `ruff check --fix`. Do not run manual lint checks during build — the hook handles it on final commits.

Use `--no-verify` on intermediate WIP commits only. Final commits must go through the hook.

## Test Isolation

Unit tests in `tests/unit/` must never touch production Redis. Use `REDIS_TEST_DB` or a separate prefix. Bulk Redis operations must always be project-scoped. See `tests/README.md` for test markers.

## Worktree Pattern

- Builder agents work in `.worktrees/{slug}/`, not main checkout
- Never `git checkout session/{slug}` — the worktree IS the checkout
- Commits happen at logical checkpoints throughout Implement, not batched at end

## Definition of Done (this repo)

In addition to global DoD, this repo requires:
- `python -m ruff check .` passes (exit 0)
- `python -m ruff format --check .` passes (exit 0)
- `pytest tests/unit/ -x -q` passes
- New `docs/features/` doc created if plan has one in the ## Documentation section
