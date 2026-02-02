# Sync Upstream Template

Selectively bring useful updates from the upstream Django project template into cuttlefish.

## Upstream Repository

- **Repo**: https://github.com/tomcounsell/django-project-template
- **Remote name**: `upstream`
- **Last synced**: 2026-02-02

## Procedure

### 1. Fetch upstream changes

```bash
git fetch upstream
```

If the `upstream` remote doesn't exist yet:
```bash
git remote add upstream https://github.com/tomcounsell/django-project-template.git
git fetch upstream
```

### 2. Review commits since last sync

List all upstream commits since the last sync date shown above:

```bash
git log upstream/main --oneline --after="LAST_SYNCED_DATE"
```

Replace `LAST_SYNCED_DATE` with the date from the "Last synced" field above.

### 3. Analyze each commit

For each commit, determine:

- **SKIP** if:
  - Documentation-only (Sphinx RST files, docs/plans/ for upstream-specific tasks)
  - Dependency-only lock file churn (just run `uv lock --upgrade` locally instead)
  - Changes to files cuttlefish has significantly diverged from (generic MCP server.py, README.md)
  - Already superseded by later commits in the same batch

- **CHERRY-PICK** if:
  - Entirely new files/modules with no overlap (e.g., new utility, new service)
  - Formatting fixes to shared files (behaviors, common models)
  - Bug fixes to shared code

- **MANUALLY APPLY** if:
  - Security version bumps in `pyproject.toml` (apply minimums individually, then `uv lock --upgrade`)
  - Changes to `base.html` CDN versions (update versions and integrity hashes)
  - Structural refactors that touch files cuttlefish has customized

### 4. Apply changes

Execute in this priority order:

1. **Security fixes** — bump dependency minimums in `pyproject.toml`, run `uv lock --upgrade && uv sync --all-extras`
2. **New modules** — cherry-pick with `git cherry-pick <sha> --no-commit`, review, then commit
3. **Bug fixes to shared code** — cherry-pick or manually apply
4. **Frontend CDN updates** — manually update versions in `base.html` with correct integrity hashes
5. **Formatting fixes** — cherry-pick Black/isort fixes to shared files

### 5. Verify

```bash
uv run black . --check
uv run isort --profile black . --check
DJANGO_SETTINGS_MODULE=settings uv run pytest apps/drugs/ -v --tb=short
```

### 6. Commit and push

Commit each logical group separately with clear messages referencing the upstream commit SHA.

```bash
git push origin main
```

### 7. Update this file's sync date

**IMPORTANT**: After completing the sync, update the "Last synced" date at the top of this file to today's date (YYYY-MM-DD format). Commit this update as part of the final push.

## Key context

- **No shared git history**: cuttlefish was not forked from the template. `git merge upstream/main` will NOT work.
- **Diverged files**: `pyproject.toml`, `uv.lock`, `requirements.txt`, `settings/`, `apps/ai/` have all diverged significantly. Never cherry-pick these wholesale.
- **Shared files**: `apps/common/behaviors/`, `apps/common/models/`, `apps/common/utilities/`, `apps/public/templates/base.html` — these track upstream closely and cherry-picks usually work.
- **Migrations**: Any upstream commit that adds Django models will need a migration created separately (coordinate with Tom).
