---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/941
last_comment_id:
---

# Local SDLC Pipeline State Tracking

## Problem

When `/sdlc` runs in a local Claude Code session (not via the bridge/worker), all stage markers silently no-op because neither `VALOR_SESSION_ID` nor `AGENT_SESSION_ID` is set. The merge gate then reports "No pipeline state found" even though every stage ran successfully.

**Current behavior:**
1. User invokes `/sdlc 941` in local Claude Code
2. Each sub-skill calls `python -m tools.sdlc_stage_marker --stage BUILD --status completed`
3. `_find_session()` checks for `VALOR_SESSION_ID` and `AGENT_SESSION_ID` env vars — neither is set
4. Returns `None`, `write_marker()` returns `{}` — silent no-op
5. At merge time, merge gate reports "WARNING: No pipeline state found"

**Desired outcome:**
Stage progress is tracked in Redis for local `/sdlc` runs, and the merge gate reports stage completion status without manual acknowledgment.

## Freshness Check

**Baseline commit:** `c3c64312dff5434b4de215328445e07a5f6c8021`
**Issue filed at:** 2026-04-13T15:56:09Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/sdlc_stage_marker.py:47-62` — `_find_session()` returns None when no env vars set — still holds exactly as described
- `tools/sdlc_stage_query.py:155-165` — falls back to env vars then returns `{}` — still holds
- `agent/pipeline_state.py:150-233` — PipelineStateMachine reads/writes stage_states on AgentSession — still holds
- `models/agent_session.py:1067-1091` — `create_local()` factory method exists and is ready to use — still holds

**Cited sibling issues/PRs re-checked:**
- #729 — closed 2026-04-06 (SDLC router artifact inference removal) — merged, established explicit state tracking
- #782 — referenced as fixing hook-based tracking for bridge sessions, but did not address local sessions — still relevant
- PR #733 — merged 2026-04-06 (added skill stage markers) — wired markers into all skills, but markers still no-op locally

**Commits on main since issue was filed (touching referenced files):**
- No commits have touched `tools/sdlc_stage_marker.py`, `tools/sdlc_stage_query.py`, `agent/pipeline_state.py`, or `models/agent_session.py` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** All references verified as current. The bug is exactly as described in the issue.

## Prior Art

- **#729 / PR #733**: Removed artifact inference from pipeline state, added skill stage markers. Established the marker-based architecture but only wired it for bridge-initiated sessions (where `VALOR_SESSION_ID` is set by the worker). This is the direct ancestor of the current bug.
- **#782**: Fixed hook-based stage tracking for bridge sessions (pre_tool_use hook misses Skill tool invocations). Did not address local sessions.

## Data Flow

1. **Entry point**: User invokes `/sdlc 941` in local Claude Code
2. **SDLC router**: Assesses state via `python -m tools.sdlc_stage_query` → returns `{}` (no session)
3. **Dispatches sub-skill**: e.g., `/do-plan` which calls `python -m tools.sdlc_stage_marker --stage PLAN --status in_progress`
4. **`sdlc_stage_marker._find_session()`**: Checks `--session-id` arg (none), `VALOR_SESSION_ID` (unset), `AGENT_SESSION_ID` (unset) → returns `None`
5. **`write_marker()`**: Receives `None` session → returns `{}` (silent no-op)
6. **Result**: Stage state is never written to Redis, merge gate finds no state

**Fix inserts at step 4**: When no session ID is available, `_find_session()` receives an `--issue-number` argument and resolves the session via `_find_session_by_issue()` (pattern already exists in `sdlc_stage_query.py`). If no session exists at all, the SDLC router ensures one is created before dispatching sub-skills.

## Architectural Impact

- **New dependencies**: None — uses existing `AgentSession.create_local()` factory and existing `_find_session_by_issue()` pattern
- **Interface changes**: `sdlc_stage_marker` CLI gains `--issue-number` flag; `_find_session()` gains issue-number fallback
- **Coupling**: No increase — reuses existing patterns from `sdlc_stage_query`
- **Data ownership**: No change — stage_states remain owned by the PM/local session in Redis
- **Reversibility**: Fully reversible — removing the fallback restores original silent-no-op behavior

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing Redis infrastructure and AgentSession model.

## Solution

### Key Elements

- **Session auto-creation in SDLC router**: Before dispatching sub-skills, the SDLC router creates a local AgentSession keyed by `sdlc-local-{issue_number}` and exports `AGENT_SESSION_ID` for downstream tools
- **Issue-number fallback in stage_marker**: When no session ID is available via env vars, `_find_session()` accepts an `--issue-number` argument and resolves via issue URL matching (same pattern as `sdlc_stage_query._find_session_by_issue()`)
- **Idempotent session lookup**: Running `/sdlc` multiple times on the same issue reuses the same local session (keyed by issue number)

### Flow

**`/sdlc` invoked locally** → Check for AGENT_SESSION_ID → Not found → Create/find local AgentSession by issue number → Export AGENT_SESSION_ID → Dispatch sub-skill → Sub-skill calls `sdlc_stage_marker` → Marker finds session via env var → Writes stage_states to Redis → Merge gate reads populated state

### Technical Approach

1. **`tools/sdlc_stage_marker.py` changes**:
   - Add `--issue-number` CLI argument
   - Extend `_find_session()` to accept optional `issue_number` parameter
   - When no session found via ID/env vars, try `_find_session_by_issue(issue_number)` (extract the function from `sdlc_stage_query.py` into a shared location, or duplicate the simple lookup)
   - To avoid circular imports, duplicate the `_find_session_by_issue()` pattern directly in `sdlc_stage_marker.py` (it's 15 lines)

2. **`tools/sdlc_stage_query.py` changes**:
   - Add issue-number auto-detection: when no session ID is available, parse the current git branch or plan files to infer the issue number
   - This is a nice-to-have; the primary fix is the marker side

3. **SDLC router skill (`SKILL.md`) changes**:
   - After Step 1 (resolve issue), add a session-ensure step: run a small Python snippet that creates or finds a local AgentSession for this issue
   - Export `AGENT_SESSION_ID` so all downstream marker calls find the session
   - Pass `--issue-number` to stage marker calls as a belt-and-suspenders fallback

4. **Session creation helper** (`tools/sdlc_session_ensure.py`):
   - New CLI tool: `python -m tools.sdlc_session_ensure --issue-number 941 --issue-url https://github.com/tomcounsell/ai/issues/941`
   - Creates an AgentSession via `create_local()` if none exists for this issue
   - Returns the session ID (for `AGENT_SESSION_ID` export)
   - Idempotent: if a session already exists for this issue URL, returns its ID
   - Sets `session_type="pm"` so `PipelineStateMachine` and stage queries work correctly
   - Sets `issue_url` for issue-number-based lookups

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_find_session()` in `sdlc_stage_marker.py` already catches all exceptions and returns `None` — test that issue-number fallback also handles Redis errors gracefully
- [ ] `sdlc_session_ensure` must handle Redis connection failures without crashing (exit 0, print `{}`)

### Empty/Invalid Input Handling
- [ ] Test `--issue-number 0` and `--issue-number -1` produce empty result (no crash)
- [ ] Test `sdlc_session_ensure` with missing `--issue-url` still works (creates session without URL)

### Error State Rendering
- [ ] Verify that when session creation fails, the SDLC router continues without state tracking (degraded but functional) rather than blocking the pipeline

## Test Impact

- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: add tests for issue-number auto-detection path
- [ ] `tests/unit/test_pipeline_state.py` — no changes needed (PipelineStateMachine itself is unchanged)

No existing tests for `sdlc_stage_marker` exist as a standalone test file — new tests will be created.

## Rabbit Holes

- **GitHub issue comments as fallback state source**: The issue mentions `fetch_stage_comments()` in `utils/issue_comments.py` as a secondary state source. This adds complexity for minimal gain — if Redis has the state, we don't need GitHub comments. Defer this to a separate issue if needed.
- **Hook-based tracking for local sessions**: Creating `pre_tool_use` / `post_tool_use` hooks that track stage transitions in local Claude Code. The marker-based approach already covers this — hooks are the bridge path.
- **Session cleanup/GC for local sessions**: Local sessions will accumulate in Redis. This is fine for now — the existing `cleanup --age 30` command handles stale sessions. Don't build a custom cleanup system.

## Risks

### Risk 1: Redis not available in local Claude Code sessions
**Impact:** Session creation fails, markers can't write state
**Mitigation:** The `.env` symlink provides Redis connection settings. If Redis is truly unavailable, all tools already handle this gracefully (return `{}`). The pipeline continues without state tracking — degraded but functional, same as today.

### Risk 2: Session ID collisions between local and bridge sessions
**Impact:** Local session could interfere with a bridge-initiated PM session for the same issue
**Mitigation:** Local sessions use a distinct `session_id` format (`sdlc-local-{issue_number}`) that cannot collide with bridge-generated IDs (which use `tg_project_chatid_msgid` format). Additionally, `_find_session_by_issue()` scans PM sessions and prefers the bridge-created one if both exist.

## Race Conditions

No race conditions identified — local Claude Code runs single-threaded, and Redis operations are atomic at the key level. The bridge and local session use different session IDs, so they never contend on the same Redis key.

## No-Gos (Out of Scope)

- GitHub issue comments as a fallback state source (separate issue if needed)
- Hook-based stage tracking for local sessions (markers are sufficient)
- Custom session GC for local sessions (existing cleanup handles this)
- Modifying the bridge/worker path (it already works correctly)
- Cross-machine session sharing (local sessions are per-machine)

## Update System

No update system changes required — this feature is purely internal to the SDLC tools. The new `tools/sdlc_session_ensure.py` will be committed to the repo and available on all machines after `git pull`. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required — this is a change to SDLC skill infrastructure (CLI tools invoked by skills). The tools are invoked via `python -m tools.sdlc_session_ensure` directly in skill SKILL.md files, not through MCP servers or the bridge. No changes to `.mcp.json` or `mcp_servers/` needed.

## Documentation

- [ ] Update `docs/features/sdlc-pipeline-state.md` (if it exists) or create it, describing local session state tracking
- [ ] Update inline docstrings in `tools/sdlc_stage_marker.py` and `tools/sdlc_session_ensure.py`
- [ ] No changes needed to `docs/features/README.md` index (this is a bug fix, not a new feature)

## Success Criteria

- [ ] Running `python -m tools.sdlc_session_ensure --issue-number 941 --issue-url https://github.com/tomcounsell/ai/issues/941` creates a local AgentSession and returns its ID
- [ ] Running the same command again returns the same session ID (idempotent)
- [ ] `python -m tools.sdlc_stage_marker --stage PLAN --status completed --issue-number 941` writes state to Redis (returns `{"stage": "PLAN", "status": "completed"}`)
- [ ] `python -m tools.sdlc_stage_query --issue-number 941` returns populated stage states
- [ ] The merge gate reports stage completion status after a local SDLC run (not "No pipeline state found")
- [ ] No orphaned sessions: reusing the same issue number reuses the same session
- [ ] Bridge/worker path is unaffected (no regressions)
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (sdlc-tools)**
  - Name: sdlc-tools-builder
  - Role: Implement session-ensure tool, update stage marker, update SDLC skill
  - Agent Type: builder
  - Resume: true

- **Validator (sdlc-tools)**
  - Name: sdlc-tools-validator
  - Role: Verify stage markers write state in local sessions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create `tools/sdlc_session_ensure.py`
- **Task ID**: build-session-ensure
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_session_ensure.py (create)
- **Assigned To**: sdlc-tools-builder
- **Agent Type**: builder
- **Parallel**: true
- Create new CLI tool `tools/sdlc_session_ensure.py`
- Accept `--issue-number` (required) and `--issue-url` (optional) arguments
- Search for existing AgentSession with matching `issue_url` ending in `/issues/{issue_number}` (PM sessions first)
- If found, print JSON `{"session_id": "<id>", "created": false}` and exit
- If not found, create via `AgentSession.create_local(session_id="sdlc-local-{issue_number}", project_key="ai", working_dir=os.getcwd(), session_type="pm", issue_url=issue_url, status="running")`
- Print JSON `{"session_id": "<id>", "created": true}` and exit
- Handle all errors gracefully (print `{}`, exit 0)

### 2. Update `tools/sdlc_stage_marker.py`
- **Task ID**: build-stage-marker
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_stage_marker.py (create)
- **Assigned To**: sdlc-tools-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--issue-number` CLI argument (optional, type=int)
- Extend `_find_session()` to accept optional `issue_number` parameter
- After env var lookup fails, if `issue_number` is provided, search for AgentSession with matching `issue_url` suffix `/issues/{issue_number}` (same pattern as `sdlc_stage_query._find_session_by_issue()`)
- Pass `issue_number` from `args.issue_number` to `write_marker()`'s `_find_session()` call

### 3. Update SDLC router skill
- **Task ID**: build-sdlc-skill
- **Depends On**: build-session-ensure, build-stage-marker
- **Validates**: manual verification via `/sdlc` invocation
- **Assigned To**: sdlc-tools-builder
- **Agent Type**: builder
- **Parallel**: false
- In `.claude/skills/sdlc/SKILL.md`, after Step 1 (resolve issue), add a session-ensure step:
  ```bash
  SESSION_JSON=$(python -m tools.sdlc_session_ensure --issue-number {issue_number} --issue-url "https://github.com/{repo}/issues/{issue_number}" 2>/dev/null)
  export AGENT_SESSION_ID=$(echo "$SESSION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
  ```
- Update stage query invocation to pass `--issue-number` as fallback
- Update stage marker calls in other skill SKILL.md files to pass `--issue-number` when available (belt-and-suspenders)

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-session-ensure, build-stage-marker
- **Validates**: pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_stage_marker.py
- **Assigned To**: sdlc-tools-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_sdlc_session_ensure.py`:
  - Test creates session when none exists
  - Test returns existing session (idempotent)
  - Test handles Redis errors gracefully
  - Test CLI output format
- Create `tests/unit/test_sdlc_stage_marker.py`:
  - Test `_find_session()` with issue-number fallback
  - Test `write_marker()` with issue-number resolution
  - Test CLI `--issue-number` argument parsing
  - Test backward compatibility (existing env var path still works)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-sdlc-skill
- **Assigned To**: sdlc-tools-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_stage_query.py -v`
- Verify bridge/worker path unaffected: `pytest tests/unit/test_pipeline_state_machine.py -v`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_session_ensure.py tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_stage_query.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_session_ensure.py tools/sdlc_stage_marker.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_session_ensure.py tools/sdlc_stage_marker.py` | exit code 0 |
| Session ensure idempotent | `python -m tools.sdlc_session_ensure --issue-number 99999 --issue-url "https://github.com/test/test/issues/99999" && python -m tools.sdlc_session_ensure --issue-number 99999` | output contains "created" |
| Pipeline state unaffected | `pytest tests/unit/test_pipeline_state_machine.py -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions — the solution is well-scoped and uses existing patterns from the codebase. The issue's recon was thorough and all assumptions have been verified against current main.
