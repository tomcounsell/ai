# do-plan addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-plan/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Popoto Schema Migration Requirement

When a plan involves changes to any Popoto model (models in `agent/`, `bridge/`, `tools/`, or anywhere using `popoto`):

- Add a migration function to `scripts/update/migrations.py`
- Register it in the `MIGRATIONS` dict (required — `run_pending_migrations()` iterates `MIGRATIONS`)
- Migration must be idempotent; it is recorded once in `data/migrations_completed.json`
- Use subprocess to call a separate migration script if the logic is non-trivial
- Never write raw Redis ops (`r.delete`, `r.srem`, `r.sadd`, `r.zrem`) — use `instance.delete()`, `Model.rebuild_indexes()`, or `transition_status()`

## Required Plan Sections

Every plan in this repo must include all four of these sections (validated by `.claude/hooks/validators/`):

1. **## Documentation** — at least one checkbox task specifying a `docs/features/` path; or explicit "No documentation changes needed" with justification
2. **## Update System** — describe whether `scripts/update/run.py` or `migrations.py` needs changes; new deps to propagate; explicit "No update system changes required" if none
3. **## Agent Integration** — describe MCP server changes, `.mcp.json` changes, or explicit "No agent integration required"
4. **## Test Impact** — checklist of existing test files that will break (UPDATE/DELETE/REPLACE), or explicit "No existing tests affected" with 50+ char justification

Missing any of these sections will fail the pre-commit hook and block plan creation.

## docs/plans/ Commit-on-Main Rule

Plan documents must always be committed directly on `main`, never on feature branches. This is enforced by memory and convention. Use `git checkout main` before creating or updating plan files.

## Transport-Keyed Callback Convention

When adding output callbacks to the bridge, key them by transport type (e.g., `"telegram"`, `"local"`) not by session ID. See `bridge/telegram_bridge.py` and `agent/output_handler.py` for the pattern.

## Slug Conventions

- Slugs are kebab-case, derived from the plan filename (without `.md`)
- Slugs tie together: plan doc, GitHub issue, worktree at `.worktrees/{slug}/`, branch `session/{slug}`, task list
- Create the slug from the GitHub issue title, not from a description of the work
