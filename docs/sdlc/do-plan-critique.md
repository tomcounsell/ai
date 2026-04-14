# do-plan-critique addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-plan-critique/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Required Section Enforcement

The critique must verify all four required plan sections are present and substantive:

- **## Documentation** — must include a checkbox task with a `docs/features/` path
- **## Update System** — must address `scripts/update/migrations.py` for any Popoto model changes
- **## Agent Integration** — must address MCP server exposure for new Python tools
- **## Test Impact** — must list affected tests with UPDATE/DELETE/REPLACE dispositions

If any section is missing or contains only a placeholder, raise it as a HIGH-severity blocker.

## Popoto Migration Check

If the plan touches any Popoto model, the critique must verify:
- A migration function is planned in `scripts/update/migrations.py`
- The migration is registered in `MIGRATIONS`
- The plan avoids raw Redis operations

## Multi-Machine Deployment

This repo runs on multiple machines (see `docs/deployment.md`). The Archaeologist critic should check:
- Does the plan require a new env var? It must be added to `.env.example` and `config/settings.py`
- Does the plan introduce new dependencies? They must be propagated via the update system
- Are there race conditions between machines running `/update` simultaneously?
