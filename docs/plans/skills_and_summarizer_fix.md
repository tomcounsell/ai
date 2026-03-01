---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-01
tracking: https://github.com/tomcounsell/ai/issues/225
---

# Fix Skill Visibility + Summarizer SDLC Template

## Problem

Two related issues are degrading the agent's effectiveness across projects:

**Problem 1: Skills not visible to non-ai projects.**
When the bridge invokes Claude Code SDK for a project like PsyOptimal, it sets `cwd` to that project's directory. The SDK's `setting_sources=["local", "project"]` excludes user-level skills (`~/.claude/skills/`), which is where the SDLC pipeline skills live. This means `/sdlc`, `/do-plan`, `/do-build`, etc. are invisible when working on non-ai projects.

**Current behavior:** Agent working on PsyOptimal cannot find or invoke SDLC skills. Work is done ad-hoc without the pipeline.

**Problem 2: Summarizer not applying SDLC template.**
The summarizer has well-built SDLC template code (`_compose_structured_summary`, `_render_stage_progress`, `_render_link_footer`), but the session object passed to it is often stale — stage data written by `session_progress.py` during execution hasn't been re-read from Redis before the summary is composed.

**Current behavior:** SDLC responses show plain bullet summaries instead of the structured format with stage progress line and link footer.

**Desired outcome:**
1. All projects see SDLC skills (and all user-level skills)
2. Every SDLC response includes the stage progress line and link footer

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all changes are internal to existing code.

## Solution

### Key Elements

- **SDK setting_sources fix**: Add `"user"` to `setting_sources` so user-level skills are discovered
- **Summarizer session refresh**: Re-read session from Redis before composing structured summary
- **Diagnostic logging**: Add logging so we can verify template is being applied

### Flow

**Message arrives** → SDK launches with user+local+project settings → Skills discovered → Agent works → `session_progress.py` writes stage data → Agent finishes → Summarizer re-reads session from Redis → Structured summary composed with stage line + link footer → Sent to Telegram

### Technical Approach

#### Fix 1: `agent/sdk_client.py`

In `_create_options()`, change:
```python
setting_sources=["local", "project"],
```
to:
```python
setting_sources=["user", "local", "project"],
```

This ensures user-level skills (`~/.claude/skills/sdlc`, etc.) are available in all projects.

#### Fix 2: `bridge/summarizer.py`

In `_compose_structured_summary()`, before reading stage progress and links, re-read the session from Redis to get fresh data:

```python
# Re-read session from Redis to pick up stage data written during execution
if session and hasattr(session, 'session_id'):
    try:
        from models.agent_session import AgentSession
        fresh_sessions = list(AgentSession.query.filter(session_id=session.session_id))
        if fresh_sessions:
            session = fresh_sessions[0]
    except Exception:
        pass  # Fall back to stale session
```

Add a log line when stage progress IS rendered vs when it's missing, so we can verify fix effectiveness.

#### Fix 3: `bridge/response.py`

Similarly, in `send_response_with_files()`, before calling `summarize_response()`, re-read the session:

```python
# Re-read session for fresh stage/link data
if session and hasattr(session, 'session_id'):
    try:
        from models.agent_session import AgentSession
        fresh = list(AgentSession.query.filter(session_id=session.session_id))
        if fresh:
            session = fresh[0]
    except Exception:
        pass
```

## Rabbit Holes

- Do NOT redesign the session passing chain — the existing architecture is sound, the issue is just staleness
- Do NOT add caching or debouncing — a simple re-read from Redis is fast enough
- Do NOT try to make skills project-specific — user-level sharing is the intended pattern

## Risks

### Risk 1: User-level settings override project settings
**Impact:** User-level hooks or permissions could conflict with project-level ones
**Mitigation:** `setting_sources` order matters — user is loaded first, then local/project override. This is the correct precedence.

## No-Gos (Out of Scope)

- Redesigning the summarizer pipeline
- Adding new SDLC stages
- Changing how `session_progress.py` writes data
- Cross-project skill isolation (all user skills shared is correct)

## Update System

No update system changes required — these are internal bridge code changes.

## Agent Integration

No new agent integration required — this fixes existing agent integration (skills being visible via SDK settings, and summarizer using existing session data). The bridge's `sdk_client.py` and `summarizer.py` are bridge-internal code.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` to document the session refresh behavior
- [ ] Add note to `docs/features/session-lifecycle-diagnostics.md` about stage data freshness

## Success Criteria

- [ ] `setting_sources` includes `"user"` in `sdk_client.py`
- [ ] Summarizer re-reads session from Redis before composing structured output
- [ ] SDLC responses show stage progress line (e.g., `☑ ISSUE → ☑ PLAN → ▶ BUILD → ☐ TEST`)
- [ ] SDLC responses show link footer (e.g., `Issue #225 | Plan | PR #226`)
- [ ] Diagnostic log line confirms when stage progress is/isn't rendered
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sdk-and-summarizer)**
  - Name: fix-builder
  - Role: Implement all three fixes
  - Agent Type: builder
  - Resume: true

- **Validator (verify-fixes)**
  - Name: fix-validator
  - Role: Verify setting_sources and session refresh
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix SDK setting_sources
- **Task ID**: build-sdk-fix
- **Depends On**: none
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"user"` to `setting_sources` in `sdk_client.py` `_create_options()`

### 2. Fix summarizer session refresh
- **Task ID**: build-summarizer-fix
- **Depends On**: none
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Add session re-read in `_compose_structured_summary()` in `summarizer.py`
- Add session re-read in `send_response_with_files()` in `response.py`
- Add diagnostic logging for stage progress rendering

### 3. Validate fixes
- **Task ID**: validate-fixes
- **Depends On**: build-sdk-fix, build-summarizer-fix
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `"user"` in setting_sources
- Verify session re-read code exists
- Verify diagnostic logging exists
- Run tests

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fixes
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/summarizer-format.md`
- Update `docs/features/session-lifecycle-diagnostics.md`

## Validation Commands

- `grep -n '"user"' agent/sdk_client.py` — confirms user setting source added
- `grep -n 'AgentSession.query.filter' bridge/summarizer.py` — confirms session re-read
- `grep -n 'AgentSession.query.filter' bridge/response.py` — confirms session re-read
- `grep -n 'stage_progress' bridge/summarizer.py | grep -i log` — confirms diagnostic logging
- `pytest tests/ -x -q 2>&1 | tail -5` — tests pass
