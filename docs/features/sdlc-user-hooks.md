# SDLC User-Level Hooks

User-level Claude Code hooks that enforce SDLC pipeline rules across all repositories and machines. Deployed to `~/.claude/hooks/sdlc/` by the update system and merged into `~/.claude/settings.json`.

## Problem

SDLC enforcement hooks previously lived only in the project-level `.claude/settings.json`, which uses `$CLAUDE_PROJECT_DIR`-relative paths. This meant hooks only fired when Claude Code ran inside the AI repo itself. On other repos (even after running the update script), none of the enforcement hooks were present, allowing the agent to commit directly to main and skip quality gates.

## Solution

Three standalone hook scripts are deployed at the user level so they fire in every Claude Code session on every machine. Each hook detects SDLC context automatically and silently no-ops when not in an SDLC-managed session.

### Hook Scripts

All scripts live at `~/.claude/hooks/sdlc/` after deployment:

| Script | Event | Matcher | Purpose |
|--------|-------|---------|---------|
| `validate_commit_message.py` | PreToolUse | Bash | Blocks `git commit` to `main` branch in SDLC context |
| `sdlc_reminder.py` | PostToolUse | Write, Edit | One-time advisory about running tests/linting on code file writes |
| `validate_sdlc_on_stop.py` | Stop | (all) | Validates quality gates (pytest, ruff, black) were run before session end |

### SDLC Context Detection

Each hook uses a two-tier detection strategy (implemented inline, no project imports):

1. **Git branch check** (primary, universal): If the current branch name starts with `session/`, we are inside a do-build worktree and SDLC rules apply.
2. **AgentSession model check** (secondary, requires Redis): Query the Popoto `AgentSession` model for SDLC stage history in the current session. This catches cases where SDLC is active but the worktree hasn't been created yet.

Both checks are wrapped in try/except. If either check fails (not in a git repo, Redis unavailable, model not importable), the hook silently allows.

### Fail-Open Design

Every hook wraps its `main()` in a top-level `except Exception: sys.exit(0)` block. If any unexpected error occurs, the hook allows rather than blocks. This prevents hook bugs from disrupting normal development.

### Escape Hatch

The stop validator supports `SKIP_SDLC=1` environment variable to bypass quality gate enforcement in genuine emergencies. A warning is logged to stderr when this is used.

## Update System Integration

The hook deployment is handled by `scripts/update/hardlinks.py`:

### `sync_user_hooks(project_dir, result)`

Called as the final step of `sync_claude_dirs()`. This function:

1. **Copies** hook scripts from `.claude/hooks/user_level/` to `~/.claude/hooks/sdlc/` (uses `shutil.copy2`, not hardlinks, because the scripts must be self-contained)
2. **Sets** executable permissions (`chmod 0o755`)
3. **Merges** hook entries into `~/.claude/settings.json`

### Settings Merge Logic

The merge uses `(matcher, command)` tuples as deduplication keys:

- Existing hooks (e.g., calendar hooks) are never removed or modified
- SDLC hook entries are appended only if not already present
- Repeated runs of the update script produce identical results (idempotent)
- Non-hook config (statusLine, enabledPlugins, etc.) is preserved

## Skill Doc Defense-in-Depth

The `/do-build` and `/do-patch` SKILL.md files include explicit branch verification reminders:

> Before any git commit, verify you are NOT on the main branch: `git rev-parse --abbrev-ref HEAD` must NOT return "main".

This is defense-in-depth alongside the mechanical hook enforcement.

## Source Files

| File | Purpose |
|------|---------|
| `.claude/hooks/user_level/validate_commit_message.py` | Source for commit validation hook |
| `.claude/hooks/user_level/sdlc_reminder.py` | Source for code write reminder hook |
| `.claude/hooks/user_level/validate_sdlc_on_stop.py` | Source for stop quality gate hook |
| `scripts/update/hardlinks.py` | `sync_user_hooks()` and `_merge_sdlc_hook_settings()` |
| `.claude/skills/do-build/SKILL.md` | Branch verification in Critical Rules |
| `.claude/skills/do-patch/SKILL.md` | Branch verification in Critical Rules |

## Testing

| Test File | Coverage |
|-----------|----------|
| `tests/unit/test_user_level_hooks.py` | 28 tests: fast paths, script structure, SDLC context behavior, escape hatch |
| `tests/unit/test_hardlinks_merge.py` | 14 tests: file deployment, settings merge, dedup, preservation, integration |

## Relationship to Project-Level Hooks

The project-level hooks in `.claude/settings.json` remain unchanged. They provide additional enforcement specific to this repo (co-author trailer blocking, documentation validation, etc.). The user-level hooks are a superset that covers the critical SDLC rules universally:

- **Project-level** `.claude/settings.json`: All hooks, including repo-specific ones
- **User-level** `~/.claude/settings.json`: Only the three SDLC enforcement hooks
- Both fire when working in the AI repo (defense-in-depth)
- Only user-level fires when working in other repos
