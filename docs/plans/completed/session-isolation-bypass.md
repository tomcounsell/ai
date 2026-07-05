---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/887
last_comment_id:
---

# Session Isolation Bypass: Dev Sessions Must Get Worktree Isolation Regardless of PM Creation Path

## Problem

When a PM session is created via `valor-session create` (without a prior `/do-plan`), its dev sub-sessions operate directly in the main checkout instead of an isolated worktree. The dev session runs `git checkout session/{slug}` inside the shared working directory, contaminating concurrent human and agent work.

**Current behavior:**
A PM session created via `valor-session create --role pm --message "Take ownership of issue #884"` dispatches a dev-session via the Agent tool. That dev-session inherits the PM's CWD (the main checkout at `/Users/valorengels/src/ai`). The dev session's `/do-build` skill calls `get_or_create_worktree()` from within the skill text, but the skill's git operations (particularly `git checkout`) happen in the inherited CWD before the worktree is provisioned. The result: `git status` in the main checkout shows branch `session/sdlc-merge-bookkeeping`, uncommitted edits appear from the dev session's activity, and `.worktrees/` has no corresponding directory.

**Desired outcome:**
Every dev session that performs git operations does so inside an isolated worktree under `.worktrees/{slug}/`. The main checkout stays on `main` with a clean working tree, regardless of how the PM session was created.

## Freshness Check

**Baseline commit:** `230b94daad19c14e5a754bf4a390cced3c3a232b`
**Issue filed at:** 2026-04-10
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:2704-2721` -- worktree provisioning in `_execute_agent_session()` -- still holds, only runs for queued sessions
- `agent/sdk_client.py:2001-2002` -- `ValorAgent` receives `working_dir` from PM path -- still holds
- `.claude/skills/do-build/SKILL.md:121-126` -- `get_or_create_worktree()` call in skill text -- still holds

**Cited sibling issues/PRs re-checked:**
- #881 (PM Bash allowlist) -- closed 2026-04-10, merged as PR #883. Restricts PM bash but does not affect dev sub-sessions
- #880 (worktree path-containment guard) -- closed, PR #882. Complementary but different problem class (absence vs. bogus path)
- #62 (worktree enforcement) -- closed 2026-02-10. Original worktree implementation. Did not anticipate Agent-tool-spawned dev sessions

**Commits on main since issue was filed (touching referenced files):**
- `25455036` Lifecycle CAS authority -- touches `agent_session_queue.py` but not the worktree provisioning path; irrelevant to this bug

**Active plans in `docs/plans/` overlapping this area:** None

## Prior Art

- **Issue #62**: Original worktree enforcement -- established the `.worktrees/{slug}/` pattern and `create_worktree()`. Did not address the Agent-tool spawn path because PM/dev session separation did not exist yet.
- **Issue #501**: Async job queue with branch-session mapping -- added `resolve_branch_for_stage()` and the worktree provisioning block in `_execute_agent_session()`. This is the code that correctly provisions worktrees for queued sessions but does not cover Agent-tool-spawned dev sessions.
- **PR #883 (Issue #881)**: PM Bash allowlist -- restricts PM session bash commands to read-only. Does not catch `git checkout` in dev sub-sessions because those run with full permissions.
- **PR #882 (Issue #880)**: Worktree path-containment guard -- prevents `_cleanup_stale_worktree()` from operating on paths outside `.worktrees/`. Different problem class (this issue is about *absence* of a worktree, not a buggy cleanup path).

## Data Flow

The bug exists because two distinct session spawn paths resolve working directories differently:

1. **Queued session path (correct):**
   - `valor-session create` or bridge enqueues `AgentSession` to Redis
   - Worker picks up session, calls `_execute_agent_session()`
   - `_execute_agent_session()` reads `session.slug`, calls `resolve_branch_for_stage(slug, stage)`
   - If `needs_wt=True` and CWD is not already a worktree, calls `get_or_create_worktree()`
   - `working_dir` is updated to `.worktrees/{slug}/`
   - `get_agent_response_sdk()` receives the worktree path as `working_dir`
   - `ValorAgent` launches Claude Code with CWD inside the worktree

2. **Agent-tool spawn path (broken):**
   - PM session (running in main checkout) uses Agent tool to spawn a dev-session subagent
   - Claude Code SDK spawns the dev-session subprocess
   - The subprocess inherits the PM's CWD (main checkout)
   - Dev-session invokes `/do-build`, which calls `get_or_create_worktree()` in its skill text
   - But skill text runs git commands (checkout, status) before and after the worktree call
   - The worktree IS created, but the dev session's initial git operations already ran in the main checkout
   - Additionally, the Agent-tool-spawned subprocess does NOT go through `_execute_agent_session()` at all

3. **Fix target:** Ensure the dev-session subprocess receives the correct worktree CWD *before* any git operations run, regardless of spawn mechanism.

## Architectural Impact

- **New dependencies**: None -- uses existing `get_or_create_worktree()` and `resolve_branch_for_stage()`
- **Interface changes**: `_execute_agent_session()` gains worktree enforcement for dev sessions; `valor-session create` gains optional `--slug` flag
- **Coupling**: Slightly increases coupling between session queue and worktree manager (already adjacent concerns)
- **Data ownership**: No change -- `AgentSession.slug` and `AgentSession.working_dir` remain authoritative
- **Reversibility**: Fully reversible -- the fix adds a guard that can be removed without breaking existing behavior

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Fix A -- Worktree enforcement at `_execute_agent_session()` entry:** When a session has a slug and `session_type == "dev"`, always verify/provision a worktree before launching the agent. This is the load-bearing fix.
- **Fix B -- `valor-session create --slug` flag:** Allow explicit slug at session creation time so the worktree can be provisioned immediately and `AgentSession.working_dir` set to the worktree path before the worker picks it up. Defense-in-depth.
- **Fix C -- Dev-session CWD injection in PM system prompt:** Update the PM system prompt to instruct the PM to pass the worktree path as CWD when spawning dev-sessions via the Agent tool. This ensures the Agent-tool path also gets worktree isolation.

### Flow

**Session created** -> Worker picks up -> `_execute_agent_session()` checks slug + session_type -> Provisions worktree if needed -> Sets `working_dir` to worktree -> Launches agent in worktree CWD

For Agent-tool-spawned dev sessions:
**PM session running** -> PM decides to spawn dev-session -> PM prompt instructs: "use worktree CWD" -> Dev-session Agent tool call includes worktree path -> Dev subprocess starts in worktree

### Technical Approach

1. **In `_execute_agent_session()` (agent/agent_session_queue.py):**
   - After resolving `slug` and `stage`, if `session_type == "dev"` and slug exists, ALWAYS call `get_or_create_worktree()` regardless of stage. The current code already does this when `needs_wt=True`, but it silently falls back to the original `working_dir` on failure. Change the fallback to log a critical warning and set `working_dir` to the worktree path. Also handle the case where the session has no slug but the PM's plan context implies one -- extract slug from the session's message context.

2. **In `valor-session create` (tools/valor_session.py):**
   - Add `--slug` flag to the `create` subcommand
   - When `--slug` is provided: validate the slug, call `get_or_create_worktree()`, set the session's `working_dir` to the worktree path, and set `AgentSession.slug`
   - This ensures sessions created externally get worktree isolation from the start

3. **In PM system prompt (agent/sdk_client.py `load_pm_system_prompt()`):**
   - Add instruction: when spawning a dev-session for a slug-scoped work item, include the worktree path in the Agent tool prompt so the dev-session CWD is the worktree, not the PM's CWD
   - Reference `.worktrees/{slug}/` as the mandatory CWD for dev-session Agent calls

4. **In `_execute_agent_session()` -- main checkout protection:**
   - After all session setup, if `working_dir` resolves to the main checkout AND the session is a dev session with a slug, refuse to proceed. Log a critical error and fail the session rather than contaminating the main checkout. This is the "break glass" guard.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `except Exception` block at `agent_session_queue.py:2717-2721` that silently falls back on worktree creation failure must be changed to log critically and either retry or fail the session
- [ ] `valor_session.py` `cmd_create` has a bare `except Exception` that must surface worktree provisioning errors when `--slug` is provided

### Empty/Invalid Input Handling
- [ ] `--slug ""` (empty string) must be rejected by `_validate_slug()`
- [ ] `--slug "../traversal"` must be rejected by `_validate_slug()`
- [ ] Session with slug but no plan doc should still get a worktree (the worktree is keyed on slug, not plan existence)

### Error State Rendering
- [ ] When the main-checkout protection guard fires, the session must fail with a clear error message visible in logs and session status, not silently continue

## Test Impact

- [ ] `tests/unit/test_worktree_manager.py` -- no changes needed; existing tests cover `get_or_create_worktree()` behavior
- [ ] `tests/unit/test_agent_session_queue.py` (if exists) -- UPDATE: add test cases for dev session worktree enforcement
- [ ] New test file `tests/unit/test_session_isolation_bypass.py` -- CREATE: tests for the three fix paths

No existing tests affected -- the worktree provisioning code being added is new guard logic. The existing `test_worktree_manager.py` tests cover the worktree creation functions themselves and remain valid.

## Rabbit Holes

- **Refactoring the entire Agent-tool spawn path to go through the session queue.** This would be a much larger change that replaces how PM sessions spawn dev sub-sessions. The current fix addresses the symptom (wrong CWD) without requiring architectural changes to the Agent tool itself.
- **Making the do-build skill aware of its own CWD and self-correcting.** The skill text already calls `get_or_create_worktree()`, but relying on skill text for safety invariants is fragile. The fix should be in the infrastructure layer, not the skill layer.
- **Implementing process-level CWD isolation (chroot/namespace).** Overkill for this problem -- git worktrees already provide sufficient filesystem isolation.

## Risks

### Risk 1: Double worktree creation
**Impact:** `get_or_create_worktree()` called both at session entry and inside `/do-build` skill
**Mitigation:** `get_or_create_worktree()` is explicitly idempotent (returns existing path if worktree already exists). Double-calling is harmless.

### Risk 2: Slug not available at session execution time
**Impact:** PM session creates dev session before slug is determined, so worktree cannot be provisioned
**Mitigation:** The slug is derived from the plan doc filename, which exists before `/do-build` runs. If no slug exists, the session is not SDLC-scoped and does not need worktree isolation. The guard only fires for sessions with a known slug.

## Race Conditions

### Race 1: Concurrent worktree creation for same slug
**Location:** `agent/worktree_manager.py:297-386`
**Trigger:** Two dev sessions for the same slug start simultaneously
**Data prerequisite:** The slug's worktree directory must not exist for creation to proceed
**State prerequisite:** Git worktree list must be consistent with filesystem state
**Mitigation:** `create_worktree()` checks `worktree_dir.exists()` first and returns early if it does. The git worktree command itself is atomic at the filesystem level. Two concurrent calls will result in one success and one early-return.

## No-Gos (Out of Scope)

- Refactoring Agent tool spawn to use the session queue (separate architectural decision)
- Adding worktree isolation to teammate sessions (they do conversational work, not code changes)
- Changing how `/do-plan` provisions worktrees (it correctly creates them; the bug is in the downstream path)
- Adding CWD monitoring/enforcement during session execution (runtime guard vs. startup guard)

## Update System

No update system changes required -- this fix modifies internal session execution logic that runs on each machine independently. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is an internal change to session execution infrastructure. No new MCP servers, tool wrappers, or bridge modifications needed. The fix operates at the worker/queue layer, below the agent's tool interface.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to document the worktree enforcement guard and the `--slug` flag for `valor-session create`
- [ ] Add inline code comments on the main-checkout protection guard explaining the 2026-04-10 incident

## Success Criteria

- [ ] A PM session created via `valor-session create --role pm` targeting an existing plan slug provisions a worktree automatically
- [ ] `git status` in the main checkout is unaffected by any dev sub-session activity
- [ ] The main-checkout protection guard fires and fails the session (with a clear error) if worktree provisioning fails for a slugged dev session
- [ ] `valor-session create --slug <slug>` provisions the worktree at creation time
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (isolation-fix)**
  - Name: isolation-builder
  - Role: Implement worktree enforcement guard, --slug flag, and PM prompt update
  - Agent Type: builder
  - Resume: true

- **Validator (isolation-check)**
  - Name: isolation-validator
  - Role: Verify worktree provisioning, main-checkout protection, and edge cases
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core tier: builder + validator pair.

## Step by Step Tasks

### 1. Add worktree enforcement guard in _execute_agent_session
- **Task ID**: build-worktree-guard
- **Depends On**: none
- **Validates**: tests/unit/test_session_isolation_bypass.py (create)
- **Assigned To**: isolation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, within `_execute_agent_session()`, after the existing `resolve_branch_for_stage()` block (line ~2704):
  - If `session_type == "dev"` AND slug is set AND `needs_wt is True` AND `working_dir` does not contain `.worktrees`, escalate the worktree creation failure from a warning to a critical log + session failure
  - Add a final guard: if `working_dir` resolves to the repo root (not a worktree) AND session is dev with slug, log critical and raise to prevent main checkout contamination
- Write unit tests in `tests/unit/test_session_isolation_bypass.py` covering:
  - Dev session with slug gets worktree provisioned
  - Dev session with slug but failed worktree creation raises/fails
  - Non-dev session (PM, teammate) without slug proceeds normally
  - Dev session without slug proceeds normally (ad-hoc, no SDLC)

### 2. Add --slug flag to valor-session create
- **Task ID**: build-slug-flag
- **Depends On**: none
- **Validates**: tests/unit/test_session_isolation_bypass.py (extend)
- **Assigned To**: isolation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/valor_session.py`:
  - Add `--slug` argument to the `create` subparser
  - In `cmd_create()`, if `--slug` is provided: validate via `_validate_slug()`, call `get_or_create_worktree(repo_root, slug)`, set `working_dir` to the worktree path
  - Pass `slug` to `_push_agent_session()` if the function signature supports it, or set it on the session after creation
- Write unit tests covering:
  - `--slug valid-slug` provisions worktree and sets working_dir
  - `--slug "../bad"` is rejected
  - No `--slug` flag works as before (backward compatible)

### 3. Update PM system prompt with worktree CWD instruction
- **Task ID**: build-pm-prompt
- **Depends On**: none
- **Validates**: manual review
- **Assigned To**: isolation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py`, in the PM system prompt section (around line 1888):
  - Add instruction that when spawning dev-sessions for slug-scoped work, the PM must ensure the dev-session Agent call specifies the worktree path as the working directory
  - Reference pattern: `Agent(subagent_type="dev-session", ..., cwd=".worktrees/{slug}/")`

### 4. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-worktree-guard, build-slug-flag, build-pm-prompt
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_isolation_bypass.py -v`
- Run `pytest tests/unit/test_worktree_manager.py -v` (verify no regressions)
- Verify `python -m tools.valor_session create --help` shows `--slug` option
- Review PM prompt changes for correctness
- Run `python -m ruff check . && python -m ruff format --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: isolation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with:
  - New "Worktree Enforcement Guard" section documenting the dev-session CWD protection
  - Updated `valor-session create` documentation mentioning `--slug` flag
  - Reference to issue #887 incident

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify lint: `python -m ruff check .`
- Verify format: `python -m ruff format --check .`
- Verify documentation file exists and references the fix

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_session_isolation_bypass.py -v` | exit code 0 |
| Worktree tests pass | `pytest tests/unit/test_worktree_manager.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Slug flag exists | `python -m tools.valor_session create --help` | output contains --slug |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the root cause is well-understood from the 2026-04-10 incident and the fix direction is specified in the issue itself.
