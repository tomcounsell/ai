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

## UI Screenshots

For any PR that touches `ui/`, include before/after screenshots of the actual running app (not mockups). Pick the surface using the dual-surface allowlist documented in `.claude/skills/do-pr-review/SKILL.md` and `sub-skills/screenshot.md`:

- **Local dev (`localhost:8500`), public preview hosts** (`*.vercel.app`, `*.netlify.app`, `*.pages.dev`, `*.fly.dev`, `*.railway.app`, `github.com`) → `agent-browser` or `bowser` (anonymous).
- **Anything else (auth dashboards, private staging, unknown hosts)** → BYOB MCP (`mcp__byob__*`) so the screenshot reflects the user's logged-in session. Default-to-BYOB closes the public-URL-302s-to-login window.

For background on the three surfaces, see [`docs/features/byob-browser-control.md`](../features/byob-browser-control.md).
