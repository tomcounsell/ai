# Build Session Reliability

Fixes for four compounding bugs that caused build sessions to hang, lose work, and operate on the wrong branch. Tracked in [issue #80](https://github.com/tomcounsell/ai/issues/80).

## Logging Propagation

**Problem:** After the Feb 5 bridge module extraction, job queue log lines (`Executing job`, `SDK query`, `SDK responded`) disappeared from `bridge.log` because the file handler was only attached to the `bridge.telegram_bridge` module logger.

**Fix:** The file handler is now attached to the **root logger** so all child loggers inherit it. A level-based `InternalDebugFilter` ensures internal packages (`bridge`, `agent`, `tools`, `monitoring`, `models`) log at DEBUG level while external packages only pass INFO+. This captures external library warnings/errors while filtering their debug spam.

**File:** `bridge/telegram_bridge.py` (logging configuration section)

## Builder Commit-on-Exit

**Problem:** Parallel builder sub-agents could hit turn limits or context windows and terminate without committing partial work, losing all code written during the session (~313 lines across 6 files in the Feb 10 incident).

**Fix:** The builder agent definition (`.claude/agents/builder.md`) now includes a **Safety Net** section instructing builders to commit all changes with a `[WIP]` prefix before exiting on failure or timeout:

```bash
git add -A && git commit -m "[WIP] partial work on {task}" || true
```

**File:** `.claude/agents/builder.md`

## Worktree Isolation

**Problem:** `/do-build` created a feature branch but agents worked in the main worktree on the main branch. The feature branch received zero commits.

**Fix:** The build skill now creates an isolated git worktree (`.worktrees/{slug}/`) with a `session/{slug}` branch. All builder agents receive the worktree path and are instructed to work there. The unified `session/{slug}` branch convention reflects that builds are a skill invoked within a session -- planning and building can happen in the same session. After PR creation, the worktree is cleaned up automatically.

**File:** `.claude/skills/do-build/SKILL.md`

## Sub-Agent Health Monitoring

**Problem:** When builder sub-agents died, the parent build session had no mechanism to detect this. Sessions went silent for hours.

**Fix:** The build orchestrator's monitoring step now includes active health polling:

| Threshold | Action |
|-----------|--------|
| 5 minutes of silence | Log warning |
| 15 minutes of silence | Attempt to resume agent; mark task failed if resume fails |
| Any agent failure | Commit whatever work exists in the worktree as a safety net |

**File:** `.claude/skills/do-build/SKILL.md` (Step 4: Monitor and Coordinate)

## Related Files

| File | Role |
|------|------|
| `bridge/telegram_bridge.py` | Root logger setup with InternalDebugFilter |
| `.claude/agents/builder.md` | Builder sub-agent definition with safety net |
| `.claude/skills/do-build/SKILL.md` | Build orchestration with worktree isolation and health monitoring |
| [Issue #80](https://github.com/tomcounsell/ai/issues/80) | Tracking issue |
