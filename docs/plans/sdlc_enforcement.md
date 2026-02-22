---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2025-02-22
tracking: https://github.com/tomcounsell/ai/issues/151
---

# SDLC Enforcement

## Problem

When the agent is asked to make code changes outside of the formal `/do-build` pipeline (ad-hoc requests via Telegram or local sessions), the stated SDLC standards are aspirational, not enforced. The `sdlc.md` command describes a Plan -> Build -> Test -> Review -> Ship cycle with quality gates, but nothing structurally prevents the agent from writing code and committing without running tests or linting.

**Current behavior:**
- `/do-build` pipeline has strong enforcement: worktree isolation, test loops, documentation gates
- Ad-hoc code changes (the majority of daily work) rely entirely on the agent "remembering" to run `pytest`, `ruff`, `black` before committing
- No hook validates that tests passed before a commit is created
- No hook validates that `ruff`/`black` pass before code is shipped
- Commit messages can include co-author trailers despite explicit prohibition
- The `Stop` hook saves session metadata but performs zero quality validation

**Desired outcome:**
- Quality gates fire automatically on every code change, not just `/do-build` runs
- The agent is blocked (via hook exit code 2) from stopping if it wrote code but didn't run tests/linting
- Enforcement is lightweight and fast (< 10s per hook) so it doesn't slow down non-code conversations

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which gates to enforce vs. warn)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Stop hook quality gate**: A new validator that runs at the `Stop` hook point, detecting whether the session modified code files and, if so, whether tests and linting were run
- **Bash hook for commit hygiene**: A `PreToolUse` hook on `Bash` that inspects `git commit` commands for prohibited patterns (co-author trailers)
- **SDLC reminder injection**: A lightweight `PreToolUse` hook on `Write`/`Edit` that adds a reminder about SDLC obligations when code files are being modified

### Flow

**Agent receives change request** -> Writes code (Write/Edit tools) -> PreToolUse hook logs that code was modified -> Agent attempts to stop -> Stop hook checks: did this session modify `.py`/`.js`/`.ts` files? If yes, were `pytest`, `ruff`, `black` invoked? If not, block with exit code 2 and instruct the agent to run quality checks.

### Technical Approach

All enforcement lives in `.claude/hooks/` as lightweight Python scripts, registered in `.claude/settings.json`. No changes to the bridge, agent SDK, or core infrastructure.

**Three new validators:**

1. **`validate_sdlc_on_stop.py`** (Stop hook)
   - Reads the session's tool use log (already captured by `post_tool_use.py`)
   - Checks: were any `.py`, `.js`, `.ts` files written/edited?
   - If yes, checks: did the session invoke `pytest`? `ruff`? `black`?
   - If quality tools were NOT run: exit 2 with clear instruction
   - If no code files were modified: exit 0 (pass through)
   - Escape hatch: if the session includes a Bash call containing `--skip-sdlc` (for genuine emergencies), pass through with a warning

2. **`validate_commit_message.py`** (PreToolUse on Bash)
   - Inspects Bash tool input for `git commit` commands
   - Blocks commits containing `Co-Authored-By:` or `co-authored-by:` trailers
   - Blocks commits with `-m ""` (empty messages)
   - Fast: regex check on tool input string, no subprocess calls

3. **`sdlc_reminder.py`** (PostToolUse on Write/Edit)
   - When a `.py`/`.js`/`.ts` file is written/edited, logs it to the session tracking file
   - Emits a brief reminder: "SDLC: Remember to run tests and linting before completing this task"
   - Does NOT block (exit 0 always) -- purely advisory
   - Only fires once per session (checks session log to avoid spam)

**Session tracking enhancement:**
- The existing `post_tool_use.py` already logs tool usage. Extend it to track which file types were modified and which quality commands were run
- Store in a lightweight JSON file: `data/sessions/{session_id}/sdlc_state.json`

## Rabbit Holes

- **Coverage measurement**: Don't try to enforce coverage percentages (100%/95%/90%) -- just ensure tests are run at all. Coverage enforcement is a separate, much harder problem
- **mypy enforcement**: mypy is slow and many files have issues. Don't block on mypy failures for now -- just `pytest`, `ruff`, `black`
- **Per-file test mapping**: Don't try to figure out which tests correspond to which files. Just require `pytest` was invoked
- **Subagent enforcement**: Subagents (Task tool) have their own sessions. Don't try to track quality across parent/child sessions -- enforce at the top-level session only

## Risks

### Risk 1: False positives on non-code sessions
**Impact:** Agent gets blocked when it only modified a config file or documentation
**Mitigation:** Only trigger on `.py`, `.js`, `.ts` file extensions. Ignore `.md`, `.json`, `.yaml`, `.toml`, `.txt`

### Risk 2: Hook performance slowing down every interaction
**Impact:** Every Stop event takes 10+ seconds, degrading responsiveness
**Mitigation:** All validators do file reads only (no subprocess calls except the stop validator which reads a JSON file). Target < 2s per hook

### Risk 3: Agent learns to game the system
**Impact:** Agent runs `pytest` with no test files, or `ruff check` on an empty directory, to satisfy the gate
**Mitigation:** Phase 2 concern. For now, checking that the commands were invoked is sufficient. Trust but verify

## No-Gos (Out of Scope)

- No coverage enforcement (just test execution)
- No mypy enforcement (too slow, too many existing issues)
- No enforcement on documentation-only changes
- No cross-session tracking (each session stands alone)
- No modification to the `/do-build` pipeline (it already has enforcement)
- No enforcement on subagent sessions (only top-level)

## Update System

No update system changes required -- this is purely internal hook infrastructure. The hooks are synced via the existing `.claude/` hardlink system in the update script.

## Agent Integration

No agent integration required -- these are Claude Code hooks that fire automatically. No MCP server changes, no bridge changes, no `.mcp.json` changes. The hooks interact with the agent purely through the Claude Code hook protocol (stdin JSON, stdout JSON, exit codes).

## Documentation

- [ ] Create `docs/features/sdlc-enforcement.md` describing the enforcement gates, how they work, and the escape hatch
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Code comments on non-obvious logic in each validator

## Success Criteria

- [ ] `validate_sdlc_on_stop.py` blocks agent from stopping if code was modified but tests/lint not run
- [ ] `validate_commit_message.py` blocks commits with co-author trailers
- [ ] `sdlc_reminder.py` emits one-time reminder when code files are modified
- [ ] All three hooks registered in `.claude/settings.json`
- [ ] Hooks add < 2s latency to Stop events
- [ ] Non-code sessions (pure conversation, docs-only) pass through without interference
- [ ] Escape hatch (`--skip-sdlc`) works for genuine emergencies
- [ ] Existing hooks (`stop.py`, `post_tool_use.py`, `pre_tool_use.py`) continue to work unchanged
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hooks)**
  - Name: hook-builder
  - Role: Implement the three validators and register them in settings.json
  - Agent Type: builder
  - Resume: true

- **Builder (tracking)**
  - Name: tracking-builder
  - Role: Extend post_tool_use.py to track code modifications and quality command invocations
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify all hooks fire correctly, test edge cases
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extend session tracking in post_tool_use.py
- **Task ID**: build-tracking
- **Depends On**: none
- **Assigned To**: tracking-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `post_tool_use.py` to detect when Write/Edit tools modify code files (`.py`, `.js`, `.ts`)
- Track in `data/sessions/{session_id}/sdlc_state.json`: `{"code_modified": true, "files": [...], "quality_commands": {"pytest": false, "ruff": false, "black": false}}`
- Detect when Bash tool runs `pytest`, `ruff`, or `black` and update the tracking file accordingly
- Ensure tracking file creation is fast (< 100ms)

### 2. Create validate_sdlc_on_stop.py
- **Task ID**: build-stop-gate
- **Depends On**: build-tracking
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/validators/validate_sdlc_on_stop.py`
- Read the session's `sdlc_state.json` tracking file
- If `code_modified` is true and any quality command is false: exit 2 with clear instructions
- If no code was modified: exit 0
- Support `--skip-sdlc` escape hatch detection
- Follow existing validator patterns (read stdin JSON, output JSON on success, stderr on failure)

### 3. Create validate_commit_message.py
- **Task ID**: build-commit-gate
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_commit_message.py`
- Hook into PreToolUse on Bash
- Parse tool input for `git commit` commands
- Block if commit message contains `Co-Authored-By:` (case-insensitive)
- Block if commit message is empty
- Pass through all non-commit Bash commands immediately (fast path)

### 4. Create sdlc_reminder.py
- **Task ID**: build-reminder
- **Depends On**: build-tracking
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/sdlc_reminder.py`
- PostToolUse hook for Write/Edit on code files
- Emit one-time advisory reminder per session
- Check session tracking to avoid duplicate reminders
- Always exit 0 (never blocks)

### 5. Register hooks in settings.json
- **Task ID**: build-registration
- **Depends On**: build-stop-gate, build-commit-gate, build-reminder
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `validate_sdlc_on_stop.py` to Stop hooks
- Add `validate_commit_message.py` to PreToolUse hooks with Bash matcher
- Add `sdlc_reminder.py` to PostToolUse hooks with Write/Edit matcher
- Preserve all existing hooks

### 6. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-registration
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify hook registration in settings.json is syntactically correct
- Verify each validator script runs standalone with test inputs
- Verify non-code sessions pass through cleanly
- Verify the escape hatch works
- Run existing tests to ensure no regressions

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-enforcement.md`
- Add entry to `docs/features/README.md`
- Include: what's enforced, how it works, escape hatch, troubleshooting

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python .claude/hooks/validators/validate_sdlc_on_stop.py --help` - Verify stop gate script loads
- `python .claude/hooks/validators/validate_commit_message.py --help` - Verify commit gate script loads
- `python .claude/hooks/sdlc_reminder.py --help` - Verify reminder script loads
- `python -c "import json; json.load(open('.claude/settings.json'))"` - Verify settings.json is valid JSON
- `pytest tests/ -v` - Full test suite passes
- `ruff check .` - Linting passes
- `black --check .` - Formatting passes

## Open Questions

1. Should the stop gate **block** (exit 2) or **warn** (exit 0 with message) on first rollout? Blocking is stronger enforcement but risks frustrating the agent on edge cases. Recommendation: block, with the escape hatch as safety valve.
2. Should we enforce that `pytest` actually passed (exit code 0), or just that it was invoked? Enforcing pass status is stronger but requires capturing Bash exit codes in the tracking. Recommendation: start with invocation-only, add exit code tracking in a follow-up.
