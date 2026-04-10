# Resume Hydration Context

When a PM session resumes mid-SDLC-pipeline, the worker injects a `<resumed-session-context>` block into the first turn containing recent branch commits. This lets the agent correlate commit headlines against plan stages and skip already-completed work instead of wasting tool calls on rediscovery.

## Problem

Without resume hydration, a resumed PM session starts cold with no memory of prior stages. The agent re-reads files, re-runs tests, and re-dispatches stages whose commits are already in `git log`. In observed sessions, roughly 30-40% of tool calls were wasted on rediscovery after each resume.

## How It Works

### Resume Detection

Resume is detected by checking the session's log directory (`logs/sessions/{session_id}/`) for `*_resume.json` files. Since `save_session_snapshot(event="resume")` runs on every session start, the first start always produces exactly 1 file. Two or more files means the session has been started at least twice -- a genuine resume.

### Hydration Flow

1. Worker pops a session via `_pop_agent_session()` or `_pop_agent_session_with_fallback()`
2. Before steering messages are drained, `_maybe_inject_resume_hydration(chosen, worker_key)` runs
3. If the session is a PM session, has a valid `working_dir`, and has 2+ resume files:
   - Calls `_get_git_summary(working_dir=chosen.working_dir, log_depth=10)` for recent branch commits
   - Prepends a `<resumed-session-context>` block to `chosen.message_text`
   - Saves the updated session to Redis
4. Steering messages are drained and appended after the hydration block
5. The agent sees the context on its first turn and skips completed stages

### Context Block Format

```xml
<resumed-session-context>
This session is resuming. The following commits already exist on the branch:
{git log --oneline -10 output}
{git status --short output}
If any of these commits satisfy a stage in your current plan, skip that stage
and proceed to the next uncompleted stage. Do not re-dispatch work that is
already committed.
</resumed-session-context>
```

## Scoping Rules

| Session Type | Receives Hydration |
|--------------|-------------------|
| PM           | Yes (if resumed with valid working_dir) |
| Dev          | No -- one-shot stage executors, do not resume mid-pipeline |
| Teammate     | No -- conversational sessions, no SDLC pipeline |

## Guards

- **Session type**: Only PM sessions receive hydration
- **Working directory**: If `chosen.working_dir` is falsy, hydration is skipped with a debug log to avoid producing git state from the wrong directory
- **Resume count**: Fewer than 2 `*_resume.json` files means first start only -- no hydration
- **Silent failure**: The entire block is wrapped in try/except with a warning log. Session start never crashes due to hydration

## Key Files

| File | Role |
|------|------|
| `agent/agent_session_queue.py` | `_maybe_inject_resume_hydration()` shared async helper, called from both pop paths |
| `agent/session_logs.py` | `_get_git_summary(log_depth=10)` provides the git context with configurable depth |
| `tests/unit/test_resume_hydration.py` | Unit tests covering all guard conditions and the happy path |

## Design Decisions

- **Filesystem-based resume detection** over Redis flags: no new model fields or keys needed; the resume.json files already exist as a side effect of `save_session_snapshot()`
- **Advisory hint** over structured stage-commit mapping: the `<resumed-session-context>` block is plain text for the LLM to interpret. No fragile parsing of commit messages against stage names.
- **`log_depth=10`** for hydration vs the default `log_depth=3` for snapshots: deeper history improves stage correlation without affecting the snapshot writer
- **Prepend before steering** so the agent orients itself on prior work before processing new instructions

## Tracking

- GitHub Issue: [#874](https://github.com/tomcounsell/ai/issues/874)
- Pull Request: [#878](https://github.com/tomcounsell/ai/pull/878)
