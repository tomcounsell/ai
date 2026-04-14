# SDLC Repo Addenda

**Status:** Active  
**Issue:** https://github.com/tomcounsell/ai/issues/927  
**Branch/PR:** `session/sdlc-repo-addenda`

## What This Is

`docs/sdlc/` holds one lightweight markdown file per SDLC stage. Each file contains only what is unique to this repo — conventions, required sections, migration patterns, test isolation rules. The global SDLC skills (`~/.claude/skills/do-plan/SKILL.md`, etc.) check for these files at startup and incorporate them as repo-specific addenda.

## Directory Structure

```
docs/sdlc/
├── do-plan.md           # Popoto migration requirement, required plan sections, slug conventions
├── do-plan-critique.md  # Required section enforcement, multi-machine deployment checks
├── do-build.md          # Ruff gates, test isolation, worktree pattern, DoD
├── do-test.md           # Test tiers, Redis isolation, AI judge pattern, quality gates
├── do-patch.md          # Worktree context, ruff auto-fix, test isolation regression
├── do-pr-review.md      # Documentation gate, plan section compliance, UI screenshots
├── do-docs.md           # docs/features/ index, CLAUDE.md quick reference, commit rules
└── do-merge.md          # Documentation gate, plan migration, post-merge cleanup
```

## How Skills Use Addenda

Each global SDLC skill includes this check near the top:

> Before starting, check if `docs/sdlc/do-X.md` exists in the current repo. If it does, read it and incorporate its guidance as repo-specific addenda to these instructions.

If the file is absent (e.g., on a clean clone before running `/update`), the skill runs with its default global behavior — graceful degradation, no errors.

## Update System Integration

The `/update` skill runs `scripts/update/migrations.py` on every update. The `create_sdlc_stubs` migration:

1. Creates `docs/sdlc/` if missing
2. Creates any missing stub files with a standard header
3. Skips files that already exist (idempotent)
4. Records completion in `data/migrations_completed.json`

Running `/update` on a machine that already has all 8 files is a no-op (migration is skipped).

## Reflection Agent

`scripts/sdlc_reflection.py` runs every 3 days via `com.valor.sdlc-reflection.plist`. It:

1. Fetches merged PRs since the last run
2. Classifies each PR by SDLC stage using keyword matching
3. Extracts explicitly flagged learnings (lines starting with `- lesson:`, `- pattern:`, etc.)
4. Proposes edits to the relevant `docs/sdlc/do-X.md` files
5. Opens a PR for human review (does not auto-merge to main)
6. Enforces the 300-line cap per file

### Install the cron

```bash
./scripts/install_sdlc_reflection.sh
```

### Manual run

```bash
python scripts/sdlc_reflection.py --dry-run    # Preview without writing
python scripts/sdlc_reflection.py              # Run and open PR
python scripts/sdlc_reflection.py --days 14    # Larger lookback window
```

State is stored in `data/sdlc_reflection_last_run.json`.

## Authoring Guidelines

- **Max 300 lines per file** — enforced by the reflection agent
- **No duplication** — never copy content from the global skill; only add what is unique to this repo
- **Comment header required** — each file starts with: `<!-- Do not duplicate content from the global skill. Only include what is unique to this repo. Max 300 lines. -->`
- **Hand-authored changes welcome** — edit `docs/sdlc/do-X.md` directly and commit on the feature branch

## Files Added

| File | Purpose |
|------|---------|
| `docs/sdlc/do-plan.md` | Popoto migration note, required sections, slug conventions |
| `docs/sdlc/do-plan-critique.md` | Section enforcement, Popoto check, multi-machine review |
| `docs/sdlc/do-build.md` | Ruff, test isolation, worktree, DoD |
| `docs/sdlc/do-test.md` | Test tiers, Redis isolation, AI judges, quality gates |
| `docs/sdlc/do-patch.md` | Worktree context, auto-fix, Redis safety |
| `docs/sdlc/do-pr-review.md` | Docs gate, section compliance, UI screenshots |
| `docs/sdlc/do-docs.md` | Index, CLAUDE.md, commit rules |
| `docs/sdlc/do-merge.md` | Docs gate, plan migration, post-merge cleanup |
| `scripts/sdlc_reflection.py` | Reflection agent (3-day cron) |
| `com.valor.sdlc-reflection.plist` | launchd schedule definition |
| `scripts/install_sdlc_reflection.sh` | Install script for launchd service |
| `scripts/update/migrations.py` | Added `create_sdlc_stubs` migration |
| `tests/unit/test_sdlc_stubs.py` | Unit tests for migration and stubs |

## Adding a New SDLC Stage

Adding a 9th (or later) SDLC stage requires a new named migration entry in `scripts/update/migrations.py`. The `create_sdlc_stubs` migration is recorded once in `data/migrations_completed.json` — a new stage stub will NOT be auto-created unless a new migration is registered and run.

## See Also

- `docs/features/README.md` — Feature index
- `scripts/update/migrations.py` — Migration registry
- `tests/unit/test_sdlc_stubs.py` — Tests
- `~/.claude/skills/do-plan/SKILL.md` — Global do-plan skill with addendum check
