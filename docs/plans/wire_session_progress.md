---
status: Complete
type: bug
appetite: Medium
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/202
---

# Wire Session Progress into SDLC Skills

## Problem

The summarizer has working code to render stage progress bars and link footers in Telegram messages:

```
☑ ISSUE → ☑ PLAN → ▶ BUILD → ☐ TEST → ☐ REVIEW → ☐ DOCS
Issue #168 | Plan | PR #176
```

But these have **never appeared** in any Telegram message because the upstream data is never written.

**Current behavior:**
`session.get_stage_progress()` always returns all-pending because no SDLC skill ever calls `session_progress.py`. `session.get_links()` always returns empty because nothing ever calls `set_link()`. The summarizer's `_render_stage_progress()` and `_render_link_footer()` render nothing.

**Desired outcome:**
When an SDLC job runs through the pipeline, each skill updates the session's stage progress and links in Redis. The summarizer renders a visible progress bar and link footer in every Telegram summary for SDLC work.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (verify Telegram output looks right)
- Review rounds: 1

The work touches 7 skill files and 2 Python modules, but each change is small and formulaic. The risk is in wiring, not complexity.

## Prerequisites

No prerequisites — this work has no external dependencies. Redis is already running (required by the existing bridge). `tools/session_progress.py` already exists and works.

## Solution

### Key Elements

- **Session progress calls in SDLC skills**: Each skill file gets bash commands that call `session_progress.py` at entry (stage in_progress) and exit (stage completed), plus link-setting commands where appropriate.
- **SESSION_ID discovery in skills**: Skills extract the session ID from the `SESSION_ID:` line that `sdk_client.py` already injects into every enriched message. This is a grep/parse from the conversation context — no env var changes needed.
- **Debug logging in AgentSession**: Add a logger to `models/agent_session.py` so `append_history()`, `set_link()`, and `get_stage_progress()` calls are observable in logs.

### Flow

**Telegram message** → Bridge enqueues job → SDK client injects `SESSION_ID: {id}` into message → Claude Code runs `/sdlc` → `/sdlc` dispatches `/do-plan` → `/do-plan` extracts SESSION_ID from context → Calls `python -m tools.session_progress --session-id $SID --stage PLAN --status in_progress` → Does plan work → Calls `--stage PLAN --status completed --plan-url $URL` → Returns to `/sdlc` → Dispatches next skill → ... → Summarizer reads session → Renders progress bar + link footer

### Technical Approach

- **Phase A**: Add session_progress calls to each SDLC skill's SKILL.md file. Each skill extracts SESSION_ID from the conversation context (it appears as `SESSION_ID: xxx` in the enriched message from `sdk_client.py` line 807). The skill then calls `python -m tools.session_progress` via bash.
- **Phase B**: Add debug logging to `models/agent_session.py` for `append_history()`, `set_link()`, and `get_stage_progress()`.
- **Phase C**: Add a test that verifies `session_progress.py` correctly updates an AgentSession in Redis.

## Rabbit Holes

- **Passing SESSION_ID as an environment variable**: The issue suggests injecting SESSION_ID as an env var. This is unnecessary — the SDK client already injects `SESSION_ID: {session_id}` into the enriched message text. Skills can extract it with a simple grep/parse. Adding env var plumbing is a separate concern and would require changes to the Claude Agent SDK's subprocess spawning.
- **Real-time progress updates during long builds**: The progress bar shows the state at summary time. Attempting to stream live updates during a build would require WebSocket or polling infrastructure — out of scope.
- **Refactoring session_progress.py**: The tool works as-is. Don't rewrite it — just wire it up.

## Risks

### Risk 1: SESSION_ID not available in sub-agent context
**Impact:** Sub-skills invoked via Task tool may not have access to the original SESSION_ID from the enriched message, since Task tool spawns a new Claude Code process.
**Mitigation:** The SDLC dispatcher (`/sdlc`) extracts SESSION_ID once and passes it to sub-skills in their invocation prompt. Each skill receives it as context, not as an env var. If SESSION_ID is not found, the session_progress call exits 0 with a warning (already implemented in `session_progress.py` line 58).

### Risk 2: Redis connection failures in session_progress
**Impact:** If Redis is down, session_progress calls fail and potentially block the skill.
**Mitigation:** `session_progress.py` already exits 0 on session-not-found (line 58). We should ensure Popoto connection errors are also caught gracefully. The tool is fire-and-forget — skills should not fail if progress tracking fails.

## No-Gos (Out of Scope)

- No env var plumbing changes to `sdk_client.py` or the subprocess spawning
- No changes to the summarizer rendering code (it already works)
- No changes to the auto-continue routing logic (it already reads `get_stage_progress()`)
- No WebSocket or real-time streaming of progress

## Update System

No update system changes required — this feature modifies skill documentation files and adds logging to an existing Python module. No new dependencies, no new config files, no migration steps.

## Agent Integration

No new MCP server or tool exposure needed. `tools/session_progress.py` is invoked via `python -m tools.session_progress` as a bash command from within skill markdown files. The bridge and summarizer already read the session data from Redis via Popoto. No changes to `.mcp.json` or `mcp_servers/` directory.

The key integration point is that skills (running inside Claude Code) call `session_progress.py` via bash, and the bridge (running in the Python process) reads the results via `AgentSession.get_stage_progress()` and `AgentSession.get_links()`. This is an indirect integration through Redis — no direct function calls between the two sides.

## Documentation

- [ ] Update `docs/features/agent-session-model.md` to document the wiring (which skills call session_progress and when)
- [ ] Add entry to `docs/features/README.md` index table if not already present

## Success Criteria

- [ ] `grep -r "session_progress" .claude/skills/` returns matches in `/sdlc`, `/do-plan`, `/do-build`, `/do-test`, `/do-pr-review`, `/do-docs` SKILL.md files
- [ ] `models/agent_session.py` has a logger with debug-level logging on `append_history()`, `set_link()`, `get_stage_progress()`
- [ ] `tools/session_progress.py` gracefully handles Redis connection errors (exits 0 with warning)
- [ ] A unit test verifies `session_progress.py` updates stage and links on an AgentSession
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-wiring)**
  - Name: skill-wiring-builder
  - Role: Add session_progress calls to all SDLC skill SKILL.md files and the SDLC dispatcher
  - Agent Type: builder
  - Resume: true

- **Builder (logging)**
  - Name: logging-builder
  - Role: Add debug logging to AgentSession and error handling to session_progress.py
  - Agent Type: builder
  - Resume: true

- **Validator (wiring)**
  - Name: wiring-validator
  - Role: Verify all skills reference session_progress and calls are syntactically correct
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Wire session_progress into SDLC skill files
- **Task ID**: build-skill-wiring
- **Depends On**: none
- **Assigned To**: skill-wiring-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a "Session Progress" section to `.claude/skills/sdlc/SKILL.md` that:
  - Extracts SESSION_ID from the conversation context (parse the `SESSION_ID: xxx` line from the enriched message)
  - Calls `python -m tools.session_progress --session-id $SID --stage ISSUE --status completed` after verifying the issue exists
  - Passes the extracted SESSION_ID to each sub-skill invocation prompt
- Add session_progress calls to `.claude/skills/do-plan/SKILL.md`:
  - At entry: `--stage PLAN --status in_progress`
  - After plan is written and pushed: `--stage PLAN --status completed`
  - Set links: `--issue-url $ISSUE_URL --plan-url $PLAN_URL`
- Add session_progress calls to `.claude/skills/do-build/SKILL.md`:
  - At entry: `--stage BUILD --status in_progress`
  - After PR is created: `--stage BUILD --status completed --pr-url $PR_URL`
- Add session_progress calls to `.claude/skills/do-test/SKILL.md`:
  - At entry: `--stage TEST --status in_progress`
  - On all tests pass: `--stage TEST --status completed`
  - On test failure: `--stage TEST --status failed`
- Add session_progress calls to `.claude/skills/do-pr-review/SKILL.md`:
  - At entry: `--stage REVIEW --status in_progress`
  - On approval: `--stage REVIEW --status completed`
  - On changes requested: `--stage REVIEW --status failed`
- Add session_progress calls to `.claude/skills/do-docs/SKILL.md`:
  - At entry: `--stage DOCS --status in_progress`
  - On completion: `--stage DOCS --status completed`

### 2. Add debug logging to AgentSession
- **Task ID**: build-logging
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `import logging` and `logger = logging.getLogger(__name__)` to `models/agent_session.py`
- Add `logger.debug(f"append_history({role!r}, {text!r}) on session {self.session_id}")` to `append_history()`
- Add `logger.debug(f"set_link({kind!r}, {url!r}) on session {self.session_id}")` to `set_link()`
- Add `logger.debug(f"get_stage_progress() on session {self.session_id}: {progress}")` to `get_stage_progress()`
- Wrap the Redis/Popoto operations in `session_progress.py` `_find_session()` with try/except to handle connection errors gracefully (exit 0 with warning)

### 3. Validate wiring
- **Task ID**: validate-wiring
- **Depends On**: build-skill-wiring, build-logging
- **Assigned To**: wiring-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "session_progress" .claude/skills/` and verify matches in all expected skill files
- Verify each skill's session_progress call uses correct stage names (ISSUE, PLAN, BUILD, TEST, REVIEW, DOCS)
- Verify `models/agent_session.py` has logger calls in `append_history()`, `set_link()`, `get_stage_progress()`
- Verify `tools/session_progress.py` handles Redis connection errors
- Run `python -c "from models.agent_session import AgentSession; print('import OK')"` to verify no syntax errors

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-wiring
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` with the wiring details
- Add/update entry in `docs/features/README.md` index table

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: wiring-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `grep -r "session_progress" .claude/skills/` - Verifies skills reference session_progress
- `python -c "from models.agent_session import AgentSession; print('OK')"` - Verifies no import errors
- `python -c "from tools.session_progress import main; print('OK')"` - Verifies session_progress imports
- `pytest tests/ -v --tb=short` - Full test suite passes
- `ruff check . && black --check .` - Lint and format checks
