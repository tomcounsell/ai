# Workspace Safety Invariants

Pre-launch validation of agent working directories, ensuring every Claude Code subprocess starts in a verified, safe location.

## Problem

Agent sessions launch Claude Code subprocesses with a `cwd` parameter that determines where the agent operates on the filesystem. Before this feature, there was no validation that the directory existed, was within the expected repository root, or had a properly sanitized path. This created three risks:

1. **CWD death**: A worktree removed while a session is active leaves the agent in a nonexistent directory (issue #301, partially fixed by PR #304's reactive guard)
2. **Path escape**: A malformed or manipulated path could place the agent outside the project tree
3. **Path injection**: Worktree slugs with special characters could create unexpected directory structures

## Safety Model

The `validate_workspace()` function enforces three invariants, checked in this specific order:

### Invariant 1: CWD Existence

The directory must exist and be an actual directory (not a file or broken symlink).

**Why first**: If the path doesn't exist, there's no point checking containment or slug format. Existence is the cheapest check and the most common failure mode (worktree removed, typo in config).

### Invariant 2: Path Containment

The resolved path must be within the allowed root (`~/src`). Uses `Path.resolve()` to follow symlinks before checking, so a symlink pointing outside the root is correctly rejected.

**Why second**: After confirming the path exists, we need to know it's in bounds. This prevents directory traversal attacks (paths containing `..`) and misconfigured project paths. Resolution before comparison means the check operates on the real filesystem location, not the symbolic one.

### Invariant 3: Slug Sanitization

For worktree paths only: the slug component (the directory name under `.worktrees/`) must match the `VALID_SLUG_RE` pattern (`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`). This reuses the existing slug regex from `worktree_manager.py`.

**Why last**: This is the most specific check and only applies to worktree paths. Regular project paths like `~/src/ai` contain characters that wouldn't pass slug validation, so this check is gated behind the `is_worktree` flag.

**Why only worktrees**: Worktree slugs are derived from user input (issue titles, branch names) and flow into filesystem paths. Regular project directories are defined in `~/Desktop/Valor/projects.json` by the system operator and don't need runtime slug validation.

## Fallback Behavior

On any invariant violation, `validate_workspace()`:

1. Logs a warning with the invalid path and the specific reason for rejection
2. Returns `allowed_root` (`~/src`) as a safe fallback

The function never raises an exception. This is a deliberate design choice: a validation failure should degrade gracefully (agent launches in a safe default directory) rather than kill the session entirely. The agent can still do useful work from the fallback directory, and the warning logs make the issue diagnosable.

## Integration Points

Validation runs at two points in the agent launch path, providing defense in depth:

1. **`_execute_agent_session()`** in `agent/agent_session_queue.py` — validates `session.working_dir` before any agent work begins
2. **`ValorAgent.__init__()`** in `agent/sdk_client.py` — validates `working_dir` before storing it on the agent instance

Both call `validate_workspace()` with the same `allowed_root`. The double-check is intentional: session queue validation catches problems early, while SDK client validation ensures safety even when `ValorAgent` is instantiated directly (e.g., in tests or alternate entry points).

### Worktree Detection

Callers determine `is_worktree` by checking whether `.worktrees` appears in the path string. This is a substring check, not a structural parse. It errs on the side of false positives (applying slug validation to non-worktree paths that happen to contain `.worktrees`), which is the safe direction — extra validation, not less.

## TOCTOU Gap

There is an inherent time-of-check-to-time-of-use gap: the directory could be removed between validation and subprocess launch. This is acknowledged and acceptable because:

- PR #304 already handles CWD death reactively (guards in `remove_worktree()` and `post_merge_cleanup.py`)
- The validation here is proactive/preventive, not a sole line of defense
- The race window is milliseconds in practice

## Implementation

- **Source**: `validate_workspace()` in `agent/worktree_manager.py`
- **Tests**: `tests/unit/test_workspace_safety.py` (23 tests across 4 test classes)
- **Plan**: `docs/plans/workspace-safety-invariants.md`
- **Issue**: [#306](https://github.com/tomcounsell/ai/issues/306)
- **Prior art**: PR #304 (reactive CWD death guard)
