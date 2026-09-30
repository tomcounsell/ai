# Build Session Reliability

The build system prevents four failure modes that would otherwise hang sessions, lose work, or operate on the wrong branch.

## Logging Propagation

Session queue log lines (`Executing session`, `SDK query`, `SDK responded`) appear in `bridge.log` because the file handler is attached to the **root logger**, so all child loggers inherit it. A level-based `InternalDebugFilter` ensures internal packages (`bridge`, `agent`, `tools`, `monitoring`, `models`) log at DEBUG level while external packages only pass INFO+. This captures external library warnings/errors while filtering their debug spam.

**File:** `bridge/telegram_bridge.py` (logging configuration section)

## Builder Commit-on-Exit

The builder agent definition (`.claude/agents/builder.md`) includes a **Safety Net** section instructing builders to commit all changes with a `[WIP]` prefix before exiting on failure or timeout:

```bash
git add -A && git commit -m "[WIP] partial work on {task}" || true
```

**File:** `.claude/agents/builder.md`

## Worktree Isolation

The build skill creates an isolated git worktree (`.worktrees/{slug}/`) with a `session/{slug}` branch. All builder agents receive the worktree path and work there. The unified `session/{slug}` branch convention reflects that builds are a skill invoked within a session — planning and building can happen in the same session. After PR creation, the worktree is cleaned up automatically.

**File:** `.claude/skills/do-build/SKILL.md`

## Sub-Agent Health Monitoring

The build orchestrator's monitoring step includes active health polling:

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
