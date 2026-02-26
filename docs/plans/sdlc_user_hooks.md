---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/183
---

# SDLC User-Level Hooks

## Problem

After running the update script on another machine, the SDLC pipeline does not enforce its rules. The agent skips branching and PRs, committing directly to main despite being told to "continue SDLC" after plan approval.

**Current behavior:**
SDLC enforcement hooks live exclusively in the project-level `.claude/settings.json` which uses `$CLAUDE_PROJECT_DIR`-relative paths. These hooks only fire when Claude Code runs inside the AI repo itself. On other repos (even after running the update script), none of the enforcement hooks are present. The update script syncs skills/commands/agents via hardlinks, but the hooks that mechanically block commits to main and enforce quality gates stay behind.

**Desired outcome:**
SDLC enforcement hooks fire in every repo on every machine. The three critical hooks — `validate_commit_message.py` (block commits to main), `sdlc_reminder.py` (remind agent to use branches), and `validate_sdlc_on_stop.py` (validate quality gates before session ends) — are deployed at the user level (`~/.claude/settings.json`) by the update system. They detect SDLC context automatically and silently no-op when not in an SDLC session.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on which hooks to promote to user-level)
- Review rounds: 1

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Portable hook scripts**: New standalone versions of the three SDLC hooks deployed to `~/.claude/hooks/sdlc/`. These scripts use no `$CLAUDE_PROJECT_DIR` dependency — they detect SDLC context from git branch name and session state.
- **Settings merger in hardlinks.py**: The existing `sync_claude_dirs()` function in `scripts/update/hardlinks.py` gains a new step that merges SDLC hook entries into `~/.claude/settings.json` without clobbering existing user hooks.
- **Graceful no-op detection**: Each hook checks if it is in an SDLC context (branch name starts with `session/`, or a skill invocation is active) and silently exits 0 when not — no interference with manual work or non-SDLC repos.
- **Defense-in-depth in skill docs**: Update `/do-build` and `/do-patch` SKILL.md with explicit branch verification steps.

### Flow

**Update runs** → `hardlinks.py` syncs skills/commands → copies hook scripts to `~/.claude/hooks/sdlc/` → merges hook entries into `~/.claude/settings.json` → **Next Claude Code session** → hooks fire on every tool call → SDLC context detected → enforcement active (or silent no-op)

### Technical Approach

**1. Hook script deployment (`~/.claude/hooks/sdlc/`)**

Three self-contained scripts (no dependency on `utils/constants.py` or `$CLAUDE_PROJECT_DIR`):

- `validate_commit_message.py` — PreToolUse(Bash): If the command is `git commit` and the current branch is `main`, check if we are in an SDLC context. If yes, block with a clear error. If no SDLC context (e.g. manual dev work), allow silently. Co-author trailer enforcement stays project-level only — the user-level hook only enforces "no commit to main in SDLC context".
- `sdlc_reminder.py` — PostToolUse(Write|Edit): If a code file (.py/.js/.ts) was written and we are in an SDLC context, emit a one-time reminder about tests and branches.
- `validate_sdlc_on_stop.py` — Stop: If code was modified and SDLC state exists, verify quality gates were run.

**SDLC context detection** (two-tier: lightweight branch check + AgentSession model):
```python
def is_sdlc_context() -> bool:
    """Detect if we are in an SDLC-managed session."""
    # Check 1: On a session/ branch (inside do-build worktree)
    # This is the primary, universal check — no dependencies
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        if branch.startswith("session/"):
            return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Check 2: Query AgentSession model for active SDLC session
    # The Popoto AgentSession model tracks job history and stage progress.
    # If the current session has SDLC stages recorded, we're in SDLC context.
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        try:
            sys.path.insert(0, str(Path.home() / "src" / "ai"))
            from models.agent_session import AgentSession
            sessions = AgentSession.query.filter(
                session_id=session_id, status="active"
            )
            for s in sessions:
                # Check if session has SDLC stage history
                history = getattr(s, "history", None)
                if history and any("stage" in str(h) for h in history):
                    return True
        except Exception:
            pass  # Redis unavailable, model not importable, etc.

    return False
```

**Note:** Check 2 uses the Popoto `AgentSession` model which has job history and stage progress tracking. This is the canonical source of SDLC state. The import is wrapped in try/except so the hook degrades gracefully when the AI repo isn't accessible or Redis isn't running. A dev using the same machine outside of SDLC context won't be affected.

**2. Settings merger (`hardlinks.py`)**

Add a `sync_user_hooks()` function called from `sync_claude_dirs()`:

```python
def sync_user_hooks(project_dir: Path, result: HardlinkSyncResult) -> None:
    """Deploy SDLC hook scripts to ~/.claude/hooks/sdlc/ and merge entries into settings."""
    user_hooks_dir = Path.home() / ".claude" / "hooks" / "sdlc"
    user_hooks_dir.mkdir(parents=True, exist_ok=True)

    # Copy hook scripts (not hardlink — these are standalone, no project dependency)
    src_hooks = project_dir / ".claude" / "hooks" / "user_level"
    for hook_file in src_hooks.glob("*.py"):
        dst = user_hooks_dir / hook_file.name
        shutil.copy2(hook_file, dst)
        dst.chmod(0o755)

    # Merge into ~/.claude/settings.json
    settings_path = Path.home() / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})

    SDLC_HOOKS = {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"python {user_hooks_dir}/validate_commit_message.py", "timeout": 10}]}],
        "PostToolUse": [
            {"matcher": "Write", "hooks": [{"type": "command", "command": f"python {user_hooks_dir}/sdlc_reminder.py", "timeout": 10}]},
            {"matcher": "Edit", "hooks": [{"type": "command", "command": f"python {user_hooks_dir}/sdlc_reminder.py", "timeout": 10}]},
        ],
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": f"python {user_hooks_dir}/validate_sdlc_on_stop.py", "timeout": 15}]}],
    }

    # Merge without clobbering: append SDLC hook entries, deduplicate by command string
    for event, entries in SDLC_HOOKS.items():
        existing = hooks.get(event, [])
        existing_commands = {h["hooks"][0]["command"] for entry in existing for h in [entry] if entry.get("hooks")}
        for entry in entries:
            cmd = entry["hooks"][0]["command"]
            if cmd not in existing_commands:
                existing.append(entry)
        hooks[event] = existing

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
```

**3. Source hook scripts location**

New directory: `.claude/hooks/user_level/` containing the three standalone hook scripts. These are the "portable" versions that do not depend on `utils/constants.py` or any project-internal import. They are copied (not hardlinked) to `~/.claude/hooks/sdlc/` during update.

**4. Defense-in-depth in skill docs**

Add to `/do-build` SKILL.md and `/do-patch` SKILL.md:
```
IMPORTANT: Before any git commit, verify you are NOT on the main branch:
  git rev-parse --abbrev-ref HEAD  # Must NOT be "main"
If on main, create a worktree/branch first.
```

## Rabbit Holes

- **Rewriting the project-level hooks to share code with user-level hooks**: The user-level hooks must be self-contained (no imports from the project). Trying to share code between them would create a dependency graph that defeats the purpose. Accept the duplication.
- **Syncing all project hooks to user-level**: Only the three SDLC enforcement hooks need to be user-level. Calendar hooks, stop.py chat logging, subagent hooks, and documentation validators are project-specific and should stay project-level.
- **Git hooks (pre-commit/pre-push)**: Too aggressive — would affect all repos including manual work, and global git hooks conflict with project-level hooks. Claude Code hooks are the right layer.
- **Managed `~/.claude/CLAUDE.md` section**: Natural language rules have the same failure mode as today (agent ignores them). Mechanical hook enforcement is the answer.

## Risks

### Risk 1: Settings merge clobbers existing user hooks
**Impact:** Calendar hooks or other user-level hooks stop working after update
**Mitigation:** The merge logic deduplicates by command string and only appends new entries. Existing entries with different commands are preserved. The settings file is read, modified, and written back — never overwritten from scratch.

### Risk 2: Hook scripts break in non-AI-repo contexts
**Impact:** Claude Code sessions in random repos crash or hang on hook execution
**Mitigation:** Each hook wraps its entire logic in try/except with fallback to `sys.exit(0)`. The `is_sdlc_context()` check runs first and short-circuits. Git command failures (e.g., not in a git repo) are caught and treated as "not SDLC context."

### Risk 3: Absolute paths in settings.json break across machines
**Impact:** Settings synced via dotfiles or shared config reference wrong paths
**Mitigation:** Hook commands use `~/.claude/hooks/sdlc/` which resolves to the correct home directory on each machine. The update script runs locally on each machine and writes machine-specific absolute paths.

## No-Gos (Out of Scope)

- No changes to the project-level `.claude/settings.json` hooks — they stay as-is for defense-in-depth
- No enforcement on subagent sessions (builders run inside worktrees on feature branches)
- No per-project SDLC configuration — universal enforcement
- No changes to the bridge or MCP servers
- No retroactive cleanup of past direct-to-main commits

## Update System

This feature is fundamentally an update system change:

- `scripts/update/hardlinks.py` gains `sync_user_hooks()` which copies hook scripts and merges settings
- `sync_claude_dirs()` calls `sync_user_hooks()` as its final step
- New directory `.claude/hooks/user_level/` contains the three standalone hook scripts (source of truth, deployed by the update system)
- No new dependencies — uses only stdlib (json, shutil, subprocess, pathlib)
- Migration: Existing installations get the hooks on next `scripts/remote-update.sh` run — no manual steps

## Agent Integration

No agent integration required — this is a Claude Code hook and update system change. No MCP servers, no bridge modifications, no `.mcp.json` changes. The hooks are invoked by the Claude Code runtime, not by the agent.

## Documentation

- [ ] Create `docs/features/sdlc-user-hooks.md` describing the user-level hook deployment, context detection, and settings merge
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline comments in `hardlinks.py` explaining the merge logic

## Success Criteria

- [ ] `~/.claude/hooks/sdlc/validate_commit_message.py` exists and is executable after running the update
- [ ] `~/.claude/hooks/sdlc/sdlc_reminder.py` exists and is executable after running the update
- [ ] `~/.claude/hooks/sdlc/validate_sdlc_on_stop.py` exists and is executable after running the update
- [ ] `~/.claude/settings.json` contains SDLC hook entries for PreToolUse(Bash), PostToolUse(Write), PostToolUse(Edit), and Stop
- [ ] Existing user-level hooks (calendar) are preserved after merge
- [ ] Hook scripts no-op gracefully when not in SDLC context (e.g., manual work in a non-SDLC repo)
- [ ] Hook scripts no-op gracefully when git is not available or not in a git repo
- [ ] `git commit -m "test" on main` is blocked when on a `session/` branch SDLC context
- [ ] `/do-build` SKILL.md contains branch verification reminder
- [ ] Tests pass (`pytest tests/`)
- [ ] Linting passes (`ruff check . && black --check .`)

## Team Orchestration

### Team Members

- **Builder (hooks)**
  - Name: hooks-builder
  - Role: Create user-level hook scripts, add settings merger to hardlinks.py, update skill docs
  - Agent Type: builder
  - Resume: true

- **Validator (hooks)**
  - Name: hooks-validator
  - Role: Verify hook deployment, settings merge, context detection, and no-op behavior
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create docs/features/sdlc-user-hooks.md and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create user-level hook scripts
- **Task ID**: build-user-hooks
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/user_level/` directory
- Write `validate_commit_message.py` — standalone PreToolUse hook that blocks git commit to main in SDLC context
- Write `sdlc_reminder.py` — standalone PostToolUse hook that reminds about tests/branches on code file writes in SDLC context
- Write `validate_sdlc_on_stop.py` — standalone Stop hook that checks quality gates in SDLC context
- Each script must: (a) have no imports from the project, (b) implement `is_sdlc_context()` inline, (c) wrap all logic in try/except with `sys.exit(0)` fallback
- Write `tests/unit/test_user_level_hooks.py` covering context detection, blocking, and no-op paths

### 2. Add settings merger to hardlinks.py
- **Task ID**: build-settings-merge
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `sync_user_hooks()` function to `scripts/update/hardlinks.py`
- Call it from `sync_claude_dirs()` as the final step
- Logic: copy scripts to `~/.claude/hooks/sdlc/`, merge hook entries into `~/.claude/settings.json`
- Merge must deduplicate by command string, never clobber existing hooks
- Write `tests/unit/test_hardlinks_merge.py` covering merge, deduplication, and clobber-prevention

### 3. Update skill docs with branch verification
- **Task ID**: build-skill-docs
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Add branch verification reminder to `.claude/skills/do-build/SKILL.md`
- Add branch verification reminder to `.claude/skills/do-patch/SKILL.md`

### 4. Validate hook deployment and behavior
- **Task ID**: validate-hooks
- **Depends On**: build-user-hooks, build-settings-merge, build-skill-docs
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/update/hardlinks.py` (or the sync function) and verify files deployed to `~/.claude/hooks/sdlc/`
- Verify `~/.claude/settings.json` has SDLC entries without clobbering calendar hooks
- Test each hook script with mock stdin (SDLC context on, off, no git, etc.)
- Run `pytest tests/unit/` — all pass
- Run `ruff check . && black --check .` — all pass

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hooks
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-user-hooks.md`
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `test -x ~/.claude/hooks/sdlc/validate_commit_message.py` — hook is executable
- `test -x ~/.claude/hooks/sdlc/sdlc_reminder.py` — hook is executable
- `test -x ~/.claude/hooks/sdlc/validate_sdlc_on_stop.py` — hook is executable
- `python -c "import json; s=json.load(open('$HOME/.claude/settings.json')); assert 'PreToolUse' in s['hooks']; print('ok')"` — settings merged
- `python -c "import json; s=json.load(open('$HOME/.claude/settings.json')); cmds=[h['hooks'][0]['command'] for e in s['hooks'].get('Stop',[]) for h in [e]]; assert any('validate_sdlc_on_stop' in c for c in cmds); print('ok')"` — stop hook present
- `echo '{}' | python ~/.claude/hooks/sdlc/validate_commit_message.py` — exits 0 (no-op, not a commit)
- `pytest tests/unit/ -q` — all pass
- `ruff check .` — no errors
- `black --check .` — no changes

