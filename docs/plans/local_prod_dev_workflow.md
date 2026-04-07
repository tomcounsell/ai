---
status: Ready
type: chore
tracking: https://github.com/yudame/cuttlefish/issues/219
---

# Local Prod Dev Workflow — Remaining Convenience Items

## Problem

Issue #219 required several changes to support running the local dev server against the production database via `ENV_FILE=.env.prod`. The core `settings/env.py` change was already shipped (commit 4aa9611). Two acceptance criteria items remain:

1. No `Makefile` exists — the `make runserver-prod` convenience target is missing.
2. `CLAUDE.md` Daily Development section has no note about `ENV_FILE` usage.

Developers who want to test against production data currently need to remember the incantation `ENV_FILE=.env.prod uv run python manage.py runserver` from memory, with no discoverable shortcut and no documentation.

## Appetite

Small — two file changes, no logic, no tests needed.

## Solution

### 1. Create `Makefile` at repo root

A minimal Makefile with a single `runserver-prod` target:

```makefile
.PHONY: runserver-prod

runserver-prod:
	ENV_FILE=.env.prod uv run python manage.py runserver
```

Keep it minimal — don't add unrelated targets. The file should be self-documenting.

### 2. Add ENV_FILE note to `CLAUDE.md` Daily Development section

Insert a comment after the standard `runserver` line:

```bash
# Django development server
uv run python manage.py runserver

# Run against production database (read-only — migrations auto-disabled)
ENV_FILE=.env.prod uv run python manage.py runserver
# or: make runserver-prod
```

This makes the workflow discoverable without requiring a separate doc file.

## Files Changed

| File | Change |
|------|--------|
| `Makefile` | Create new — `runserver-prod` target only |
| `CLAUDE.md` | Add 3-line comment block under Daily Development `runserver` entry |

## Out of Scope

- No new logic — `settings/env.py` already supports `ENV_FILE` (commit 4aa9611)
- No changes to `settings/database.py` — migration guard already exists
- No `.gitignore` changes — `.env.*` pattern already covers `.env.prod`
- No additional Makefile targets beyond `runserver-prod`

## Rabbit Holes

- **Comprehensive Makefile**: Don't add `test`, `migrate`, `lint`, etc. targets — this is a one-target file scoped to this issue's acceptance criteria only.
- **Separate docs file**: A one-liner in `CLAUDE.md` is sufficient; no need for `docs/features/local-prod-dev-workflow.md`.

## Success Criteria

- [ ] `Makefile` exists at repo root with `runserver-prod` target
- [ ] `make runserver-prod` invokes `ENV_FILE=.env.prod uv run python manage.py runserver`
- [ ] `CLAUDE.md` Daily Development section mentions `ENV_FILE=.env.prod` and `make runserver-prod`
- [ ] Issue #219 all acceptance criteria satisfied

## Documentation

- [ ] `CLAUDE.md` updated inline (this IS the documentation update)

## Step by Step Tasks

1. Create `/Makefile` with `.PHONY: runserver-prod` and the target
2. Edit `CLAUDE.md` lines 23-24 to add the ENV_FILE comment block
3. Commit both files together: `"Add Makefile runserver-prod target and document ENV_FILE usage (#219)"`
4. Verify `gh issue` #219 acceptance criteria are fully met

## Open Questions

None — scope is fully determined by the remaining acceptance criteria in issue #219.
