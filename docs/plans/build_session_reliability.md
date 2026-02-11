---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-02-11
tracking: https://github.com/tomcounsell/ai/issues/80
---

# Build Session Reliability

## Problem

The Feb 10 build session for issue #75 revealed four compounding bugs that caused the build to hang, lose work, and leave uncommitted changes on main. All four bugs manifested in a single session, making the `/build` command unreliable for any non-trivial plan execution.

**Current behavior:**
1. Job queue log lines (`Executing job`, `SDK query`, `SDK responded`, etc.) vanish from `bridge.log` after the Feb 5 bridge module extraction — jobs process fine but are invisible
2. Parallel builder sub-agents hit turn limits or context windows, terminate without committing partial work (~313 lines across 6 files lost)
3. `/build` creates a feature branch but agents write code on main branch in the main worktree — feature branch has zero commits
4. When builders die, the parent build session has no mechanism to detect this — session goes silent for hours

**Desired outcome:**
- All agent and job queue activity visible in `bridge.log`
- Builder agents commit partial work to the feature branch before exiting
- `/build` agents work in an isolated worktree on the correct feature branch
- Parent build orchestrator detects and reports sub-agent failures within minutes

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on worktree vs branch-checkout approach)
- Review rounds: 1 (code review before merge)

## Prerequisites

No prerequisites — all fixes are to existing internal code.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Git worktrees available | `git worktree list` | Verify git supports worktrees |
| Bridge code exists | `python -c "import bridge.telegram_bridge"` | Verify bridge imports work |

Run all checks: `python scripts/check_prerequisites.py docs/plans/build_session_reliability.md`

## Solution

### Key Elements

- **Logging fix**: Add the file handler to the root logger so all modules (including `agent.job_queue`) inherit it
- **Commit-on-exit for builders**: Add instructions to the builder agent definition to commit partial work before exiting on turn/context limits
- **Worktree isolation in /build**: Update the build skill to create a git worktree and pass the worktree path to spawned agents
- **Sub-agent health monitoring**: Add a polling loop to the build orchestrator that detects dead/silent agents and reports to chat

### Flow

**Build invoked** → Create worktree (`.worktrees/{slug}/`) → Spawn builders with worktree CWD → Builders work on feature branch → Health monitor polls agent status → On agent exit: commit partial work → On completion: merge worktree, open PR

### Technical Approach

#### Bug 1: Job queue logging fix

The root cause is in `bridge/telegram_bridge.py:271-275`. The file handler is added only to the `bridge.telegram_bridge` logger, not the root logger. When `agent/job_queue.py` calls `logging.getLogger(__name__)`, it gets `agent.job_queue` which inherits from root — but root only has a `StreamHandler`.

**Fix:** Add the file handler to the root logger instead of the module-specific logger:

```python
root_logger = logging.getLogger()
root_logger.addHandler(file_handler)
```

Also set the root logger's level to DEBUG so child loggers' debug messages propagate. Keep the module logger for bridge-specific messages.

#### Bug 2: Builder commit-on-exit

Update `.claude/agents/builder.md` to include an explicit instruction: before reporting failure or when approaching turn/context limits, commit all changes to the current branch with a `[WIP]` prefix message.

Add to the builder's workflow section:
- Before marking a task failed or when context is running low, stage and commit all changes
- Use `git add -A && git commit -m "[WIP] partial work on {task}" || true` as a safety net
- This ensures partial work is recoverable even on abnormal exit

#### Bug 3: Worktree isolation in /build

Update `.claude/skills/build/SKILL.md` step 4 to:
1. Create a worktree using `agent/worktree_manager.py` conventions: `.worktrees/{slug}/` with branch `build/{slug}`
2. Pass the worktree path in each builder's prompt so they `cd` into it before working
3. After all tasks complete, push from the worktree, open PR, then clean up worktree

This leverages the existing `worktree_manager.py` code which already handles branch creation, settings copying, and cleanup.

#### Bug 4: Sub-agent health monitoring

Add a monitoring section to the build orchestrator's "Step 4: Monitor and Coordinate":
1. After deploying background agents, poll `TaskOutput({task_id, block: false})` every 30 seconds
2. If an agent's output contains exit indicators (task completed or error) but the TaskList still shows `in_progress`, mark the task as failed
3. If an agent has been silent for >5 minutes, log a warning
4. If an agent has been silent for >15 minutes, report failure to chat and attempt to resume or abort
5. On any agent failure, immediately commit whatever work exists in the worktree

## Rabbit Holes

- **Process-level PID tracking for sub-agents** — The Task tool spawns processes internally; trying to track PIDs is fighting the abstraction. Use task status polling instead.
- **Sophisticated health check protocols** — Don't build a heartbeat system. Simple polling of TaskOutput and TaskList is sufficient for the current scale.
- **Automatic retry of failed builders** — Tempting but scope-expanding. For now, detect and report; let the human decide whether to retry.
- **Distributed logging infrastructure** — Don't add structured logging, log aggregation, or ELK-style systems. Just fix handler propagation.

## Risks

### Risk 1: Worktree conflicts with main repo
**Impact:** If builders accidentally modify the main worktree instead of the isolated one, changes end up on main again.
**Mitigation:** The build skill will pass the worktree path explicitly in the prompt. Builders work relative to the path they're given.

### Risk 2: Partial WIP commits create noisy git history
**Impact:** Feature branches accumulate `[WIP]` commits that clutter the PR.
**Mitigation:** The build orchestrator squash-merges or the PR reviewer squash-merges on GitHub. WIP commits are on feature branches only, never main.

### Risk 3: Root logger level change causes log noise
**Impact:** Setting root logger to DEBUG could flood bridge.log with third-party library debug messages.
**Mitigation:** Keep root logger at INFO for console, add a filter to the file handler to only accept loggers from our packages (`bridge.*`, `agent.*`, `tools.*`, `monitoring.*`).

## No-Gos (Out of Scope)

- No automatic retry/restart of failed build agents — detect and report only
- No changes to the session watchdog or Redis-based monitoring — this is about the build orchestrator's own polling
- No structured logging migration — fix handler propagation only
- No changes to how the bridge handles non-build sessions
- No migration of existing log files or format changes

## Update System

No update system changes required — all fixes are to skill definitions, agent definitions, and bridge logging. No new dependencies, config files, or migration steps. The update script will pull these changes naturally via `git pull`.

## Agent Integration

No agent integration required — these are fixes to the build orchestrator skill (`.claude/skills/build/SKILL.md`), the builder agent definition (`.claude/agents/builder.md`), and bridge logging (`bridge/telegram_bridge.py`). No MCP server changes, no `.mcp.json` changes, no new tools needed.

## Documentation

- [ ] Create `docs/features/build-session-reliability.md` describing the worktree isolation, commit-on-exit, and health monitoring behaviors
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline comments in `bridge/telegram_bridge.py` logging section explaining root logger setup

## Success Criteria

- [ ] Job queue log lines (`Executing job`, `SDK query`, `SDK responded`) visible in `bridge.log` after bridge restart
- [ ] Builder agents commit partial work before exiting on failure/timeout
- [ ] `/build` agents operate in `.worktrees/{slug}/` on the `build/{slug}` branch, not on main
- [ ] Build orchestrator detects and reports dead sub-agents within 5 minutes
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (logging-fix)**
  - Name: logging-builder
  - Role: Fix root logger file handler propagation in bridge
  - Agent Type: builder
  - Resume: true

- **Builder (builder-agent-updates)**
  - Name: agent-def-builder
  - Role: Update builder agent definition with commit-on-exit and build skill with worktree isolation + health monitoring
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: reliability-validator
  - Role: Verify all four fixes work correctly
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Fix root logger file handler propagation
- **Task ID**: build-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/telegram_bridge.py`, move the file handler from the module logger to the root logger
- Add a package filter so only `bridge.*`, `agent.*`, `tools.*`, `monitoring.*` loggers write to the file handler (prevent third-party noise)
- Verify `agent.job_queue` logger messages appear in `bridge.log` by checking log output
- Run `ruff check . && black --check .`

### 2. Update builder agent with commit-on-exit behavior
- **Task ID**: build-agent-defs
- **Depends On**: none
- **Assigned To**: agent-def-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/agents/builder.md` to add a "Safety Net" section: before exiting on failure or approaching limits, commit all staged/unstaged changes with `[WIP]` prefix
- Update `.claude/skills/build/SKILL.md` step 4 to create a git worktree (`.worktrees/{slug}/`) instead of `git checkout -b build/{slug}`
- Update `.claude/skills/build/SKILL.md` agent deployment to pass the worktree path to builders so they work there
- Update `.claude/skills/build/SKILL.md` step 4 "Monitor and Coordinate" with health polling: check TaskOutput every check of TaskList, flag agents silent >5min as warning and >15min as failure
- Update `.claude/skills/build/SKILL.md` step 7 to push from worktree and clean up worktree after PR
- Run `ruff check . && black --check .`

### 3. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-logging, build-agent-defs
- **Assigned To**: reliability-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `bridge/telegram_bridge.py` logging adds file handler to root logger with package filter
- Verify `.claude/agents/builder.md` includes commit-on-exit instructions
- Verify `.claude/skills/build/SKILL.md` uses worktree creation, passes path to agents, includes health monitoring loop
- Run `python -c "import bridge.telegram_bridge"` to verify no import errors
- Run `pytest tests/ -v` to verify no test regressions
- Verify all success criteria are addressed

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: reliability-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/build-session-reliability.md` describing the four fixes
- Add entry to `docs/features/README.md` index table

### 5. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: reliability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met including documentation
- Generate final report

## Validation Commands

- `python -c "import bridge.telegram_bridge"` - verify bridge imports without error
- `pytest tests/ -v` - verify no test regressions
- `ruff check .` - lint check
- `black --check .` - format check
- `grep -n "root_logger\|getLogger()" bridge/telegram_bridge.py` - verify root logger setup
- `grep -n "WIP\|commit.*exit\|Safety" .claude/agents/builder.md` - verify commit-on-exit instructions
- `grep -n "worktree\|\.worktrees" .claude/skills/build/SKILL.md` - verify worktree usage in build skill

---

## Open Questions

1. **Worktree branch naming**: The existing `worktree_manager.py` uses `session/{slug}` convention, but the build skill uses `build/{slug}`. Should we unify to one convention or keep them separate? (Recommendation: use `build/{slug}` for builds to distinguish from interactive sessions.)
2. **Log filter packages**: The proposed filter allows `bridge.*`, `agent.*`, `tools.*`, `monitoring.*`. Are there other internal packages that should be included?
3. **WIP commit granularity**: Should builders commit after each file change, or only on exit? (Recommendation: only on exit — per-file commits would be too noisy.)
