# /do-patch Skill

A targeted repair skill for fixing test failures and PR review blockers. Invoked by the user directly or automatically by `/do-build` at lifecycle failure points.

## /do-patch vs /do-build

| | `/do-patch` | `/do-build` |
|--|--|--|
| Input | Description of what's broken | Plan document |
| Scope | Targeted fix to existing code | Full implementation from scratch |
| Worktree | Uses existing worktree or CWD | Creates new worktree |
| Agents | Single focused agent | Heavy orchestration (multiple) |
| Caller | User or `do-build` | User |
| Creates PRs? | Never | Yes |
| Commits? | Never | Builders do |
| Use case | Fixing test failures or review blockers | Shipping a feature |

## User Invocation

```
/do-patch "3 tests failing in test_bridge.py — connection timeout"
/do-patch "review blocker: race condition in session lock"
/do-patch  # no args — reads most recent failure from session context
```

Trigger phrases: `"patch this"`, `"fix the failures"`, `"fix the blockers"`, `"do-patch"`

## Model-Invocable Lifecycle

`/do-build` calls `/do-patch` automatically at two lifecycle points:

### 1. Test Failure (no iteration cap)

```
Implement → test → FAIL → /do-patch → test → FAIL → /do-patch → ...
                                                           ↓
                                                         PASS → Review
```

No iteration cap at this stage. The loop continues until tests pass or a human intervenes.

### 2. Review Blockers (capped at 3)

```
Review → blockers → /do-patch → test → re-review → blockers → /do-patch → ...
                                                                      ↓ (3rd time)
                                                               PATCH STUCK → human
```

Capped at **3 patch→test→review iterations**. After 3 cycles, emits a structured "PATCH STUCK" report and waits for human input. The asymmetry is intentional — test failures have an objective signal automation can chase; review blockers are subjective.

## Flow

1. Accept failure description (or read last failure from session context)
2. Read the failing test output or review comment in full
3. Deploy a **single builder agent** to make targeted edits
4. Re-run `/do-test` to verify the fix
5. If pass: report success, update pipeline state to next stage
   - Test failure context → advance to `review`
   - Review blocker context → advance to `document`
6. If fail: retry up to caller's iteration cap, then report stuck with details

## What /do-patch Never Does

- Never creates a PR
- Never commits changes to git
- Never touches the Document or PR pipeline stages
- Never creates new worktrees
- Never modifies pipeline state directly on failure — only `/do-build` advances the pipeline

## Pipeline State on Success

When `/do-patch` fixes a test failure:
```bash
python -c "from agent.pipeline_state import advance_stage; advance_stage('{slug}', 'review')"
```

When `/do-patch` fixes review blockers:
```bash
python -c "from agent.pipeline_state import advance_stage; advance_stage('{slug}', 'document')"
```

## Related

- [SDLC Enforcement](sdlc-enforcement.md) — the quality gate system this skill operates within
- `.claude/skills/do-patch/SKILL.md` — full skill definition
- `.claude/skills/do-build/SKILL.md` — the pipeline orchestrator that invokes this skill
