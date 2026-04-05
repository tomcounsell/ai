---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/704
last_comment_id:
---

# SDLC Router: State Machine-Based Stage Tracking

## Problem

The SDLC router (`.claude/skills/sdlc/SKILL.md`) determines which pipeline stage to dispatch next by checking for artifacts: plan files, branches, PR diffs, review decisions. This artifact inference approach allows stages to be silently skipped when a prior stage happens to produce artifacts that satisfy a later stage's check.

**Current behavior:**
When `/do-build` creates docs or runs tests inline, the router sees those artifacts and concludes `/do-docs` or `/do-test` are done — even though those stages were never explicitly dispatched. This happened concretely with issue #700 (build created docs, so `/do-docs` was never invoked) and issues #653-657 (5 PRs merged without PR reviews, tracked in #666).

**Desired outcome:**
The router reads `PipelineStateMachine.stage_states` from the PM session as the primary signal for which stages are complete. Every display stage (PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS) must show `completed` in `stage_states` before the router dispatches Row 10 (merge-ready). Artifact inference remains as a fallback for resuming interrupted pipelines or local Claude Code invocations without a PM session.

## Prior Art

- **Issue #488 / PR #490**: Consolidated SDLC stage tracking, removed legacy `sdlc_stages` field, established PipelineStateMachine as single source of truth. Did NOT wire it into the router's assessment logic.
- **Issue #563 / PR #601**: Wired `classify_outcome()` and `fail_stage()` into production via hooks. Made PipelineStateMachine actively track stage transitions. Did NOT change the router to read this state.
- **PR #433**: "Replace inference-based stage tracking with PipelineStateMachine" — Despite the title, this replaced the *internal* tracking but left the router using artifact inference.
- **PR #494**: Wired `PipelineStateMachine.start_stage()` into SDLC dispatch via `pre_tool_use.py` hook.
- **Issue #645**: Added implicit pipeline tracking via observable artifacts (the system this plan partially supersedes as primary signal).
- **Issue #666**: Post-merge review documenting 5 PRs merged without reviews — same root cause as this issue.
- **Issue #707**: Retroactive SDLC verification for session zombie fix — documented stages being skipped.

## Data Flow

1. **Entry point**: Human message arrives via Telegram, PM session (ChatSession) is created with `stage_states` initialized to all-pending
2. **PM invokes `/sdlc`**: Router assesses state and dispatches a sub-skill (e.g., `/do-build`)
3. **`pre_tool_use.py` hook**: When PM spawns a dev-session via Agent tool, hook extracts the SDLC stage from the prompt and calls `PipelineStateMachine.start_stage()` on the PM session, marking it `in_progress`
4. **Dev session executes**: Builder/tester/reviewer does the work
5. **`subagent_stop.py` hook**: When dev session completes, hook calls `classify_outcome()` and routes to `complete_stage()` or `fail_stage()` on the PM session
6. **PM re-invokes `/sdlc`**: Router re-assesses — **currently** using artifact inference, **should** use `stage_states` from step 5
7. **Output**: Router dispatches next sub-skill or reports merge-ready

The gap is at step 6: the state machine is correctly updated in steps 3-5, but the router ignores it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #433 | Replaced inference-based tracking with PipelineStateMachine internally | Only replaced the tracking layer; the SDLC router skill (markdown) was not updated to read stage_states |
| PR #490 | Consolidated tracking fields | Cleaned up data model but did not change the consumer (router) |
| PR #601 | Wired hooks for start/complete/fail | Completed the write side of the state machine; the read side (router) was out of scope |

**Root cause pattern:** Each fix improved the state machine's *write path* (recording transitions) but none addressed the *read path* (the router consulting those recorded transitions). The router is a markdown skill file, not Python code, so it was not touched by code-level refactors.

## Architectural Impact

- **New dependencies**: A new CLI tool (`tools/sdlc_stage_query.py`) that the router can invoke via bash to read stage_states from Redis
- **Interface changes**: SKILL.md gains a new Step 2 substep that runs a Python command before the existing artifact checks
- **Coupling**: Increases coupling between the router skill and the PM session model, but this is intentional — the router *should* be aware of the state machine
- **Data ownership**: No change — PM session continues to own `stage_states`
- **Reversibility**: Easy to revert — remove the new Step 2 substep and the CLI tool; router falls back to pure artifact inference

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on CLI tool design)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing Redis/Popoto infrastructure and the PipelineStateMachine API.

## Solution

### Key Elements

- **CLI query tool** (`tools/sdlc_stage_query.py`): A Python script the router can invoke via bash to read `stage_states` from the PM session. Takes a session ID or issue number, returns JSON with stage statuses.
- **Router SKILL.md update**: Step 2 gains a new substep (2a-prime) that invokes the CLI tool to read `stage_states`. The existing artifact inference steps (2a-2e) become fallback logic for when no `stage_states` data is available.
- **Merge gate enforcement**: Row 10 in the dispatch table gains a precondition: all display stages must show `completed` in `stage_states`.

### Flow

**PM invokes /sdlc** → Router runs CLI query to read stage_states → **If stage_states populated**: use as primary signal for dispatch table → **If stage_states empty/unavailable**: fall back to artifact inference → **Dispatch one sub-skill** → Return

### Technical Approach

- The CLI tool reads `AgentSession.stage_states` from Redis via Popoto, filtering by session_id or issue_url containing the issue number
- The router invokes it via: `python -m tools.sdlc_stage_query --session-id "$VALOR_SESSION_ID" 2>/dev/null`
- Output is JSON: `{"ISSUE": "completed", "PLAN": "completed", "CRITIQUE": "completed", "BUILD": "in_progress", ...}`
- The router parses this and uses it to determine which row in the dispatch table applies
- When `VALOR_SESSION_ID` is not set (local Claude Code), the tool tries `AGENT_SESSION_ID` or falls back gracefully with exit code 0 and empty JSON
- The SKILL.md dispatch table Row 10 gains: "AND all display stages show completed in stage_states (or stage_states unavailable)"

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_stage_query.py` must handle missing sessions, malformed `stage_states` JSON, and Redis connection failures gracefully (exit 0 with empty JSON, never crash)
- [ ] The router must handle the CLI tool returning empty/invalid output (fall back to artifact inference)

### Empty/Invalid Input Handling
- [ ] CLI tool with no args or invalid session ID returns `{}` and exit 0
- [ ] CLI tool with valid session ID but empty `stage_states` returns all-pending dict

### Error State Rendering
- Not applicable — this feature has no user-visible UI; it affects internal routing decisions only

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py` — UPDATE: add tests for the new CLI query tool's output format
- [ ] `tests/integration/test_artifact_inference.py` — UPDATE: verify that artifact inference is only used as fallback when stage_states is unavailable
- [ ] `tests/unit/test_pre_tool_use_start_stage.py` — no change needed (write path unchanged)
- [ ] `tests/unit/test_subagent_stop_hook.py` — no change needed (write path unchanged)

## Rabbit Holes

- **Rewriting the entire SKILL.md from scratch**: The router skill is working and only needs targeted additions. Do not restructure the dispatch table or change how sub-skills are invoked.
- **Making the CLI tool an MCP server**: Overkill for a tool only used by the SDLC router skill. A simple `python -m` invocation is sufficient.
- **Adding stage_states to env vars via sdk_client.py**: Tempting but would make the env var payload huge and stale (stage_states changes during the session). Better to query live from Redis.
- **Modifying PipelineStateMachine API**: The state machine is already correct — it tracks stages properly. The fix is entirely on the read side.

## Risks

### Risk 1: PM session not findable from router context
**Impact:** CLI tool cannot find the session, falls back to artifact inference (same behavior as today — no regression)
**Mitigation:** The `VALOR_SESSION_ID` env var is already injected by sdk_client.py. The CLI tool also supports lookup by issue number as a secondary path.

### Risk 2: Local Claude Code invocations have no PM session
**Impact:** stage_states is always empty for local dev work
**Mitigation:** Artifact inference remains as the explicit fallback. A logged warning reminds that stage tracking is degraded.

## Race Conditions

No race conditions identified — the router reads stage_states at a point-in-time snapshot. The state machine's _save() method is atomic per Redis write. The router is single-threaded within a single skill invocation.

## No-Gos (Out of Scope)

- Changing the PipelineStateMachine API (it's already correct)
- Modifying the hook wiring (pre_tool_use.py, subagent_stop.py are already correct)
- Adding a web UI for stage_states visualization (separate concern, dashboard already has this)
- Making the router loop or orchestrate multiple stages (it remains a single-stage router)

## Update System

No update system changes required — this feature adds a new Python tool and modifies a skill file, both of which are propagated by `git pull` during updates. No new dependencies, config files, or migration steps.

## Agent Integration

No new MCP server needed. The `tools/sdlc_stage_query.py` module is invoked directly via `python -m` from the SDLC router skill's bash commands. The bridge does not need to import or call this tool — it is consumed solely by the router skill running inside Claude Code.

- Integration test: verify that the CLI tool returns correct JSON when given a session ID with populated stage_states

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` to document the router integration (state machine is now read by the router, not just written to by hooks)
- [ ] Add entry to `docs/features/README.md` index table if not already present

## Success Criteria

- [ ] SDLC router Step 2 reads `stage_states` from the PM session via CLI tool as primary signal
- [ ] Row 10 (merge-ready) requires all display stages to show `completed` in `stage_states`
- [ ] `/do-docs` is dispatched even when build step created docs, because DOCS stage is not `completed` in stage_states
- [ ] `/do-test` is dispatched even when build step ran tests, because TEST stage is not `completed` in stage_states
- [ ] Interrupted pipelines can bootstrap `stage_states` from artifacts (graceful resume / fallback)
- [ ] PM session owns `stage_states`; dev sessions do not write to it directly (hooks handle it — unchanged)
- [ ] Local Claude Code invocations (no PM session) fall back to artifact inference with a logged warning
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cli-tool)**
  - Name: tool-builder
  - Role: Implement `tools/sdlc_stage_query.py` CLI tool
  - Agent Type: builder
  - Resume: true

- **Builder (router-skill)**
  - Name: skill-builder
  - Role: Update `.claude/skills/sdlc/SKILL.md` with state machine reading logic
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end: CLI tool reads stage_states, router uses it correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build CLI Query Tool
- **Task ID**: build-cli-tool
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stage_query.py` (create)
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/sdlc_stage_query.py` with `__main__` entry point
- Accept `--session-id` and `--issue-number` arguments
- Load AgentSession from Redis via Popoto, read `stage_states`
- Parse via PipelineStateMachine, output JSON dict of stage -> status
- Handle all error cases gracefully (exit 0, empty JSON)
- Write unit tests in `tests/unit/test_sdlc_stage_query.py`

### 2. Update SDLC Router Skill
- **Task ID**: build-router-skill
- **Depends On**: build-cli-tool
- **Validates**: manual review of SKILL.md diff
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Add new Step 2 substep before artifact checks: invoke CLI tool to read stage_states
- Make artifact inference steps conditional (only run when stage_states unavailable)
- Update Row 10 precondition: all display stages must show `completed`
- Add fallback logic for local invocations (no VALOR_SESSION_ID)
- Preserve all existing dispatch table rows and hard rules

### 3. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-cli-tool, build-router-skill
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify CLI tool returns correct JSON for populated stage_states
- Verify SKILL.md changes are syntactically correct and preserve all dispatch rows
- Verify fallback path works (no session ID -> artifact inference)
- Run existing pipeline state machine tests to confirm no regressions

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: tool-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pipeline-state-machine.md` with router integration details
- Add/update entry in `docs/features/README.md` index

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit and integration tests
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_stage_query.py tests/unit/test_pipeline_state_machine.py -x -q` | exit code 0 |
| CLI tool runs | `python -m tools.sdlc_stage_query --help` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_stage_query.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_stage_query.py` | exit code 0 |
| SKILL.md has state machine step | `grep -c 'stage_states' .claude/skills/sdlc/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. Should the CLI tool support lookup by slug in addition to session-id and issue-number? (Slug lookup would allow `python -m tools.sdlc_stage_query --slug sdlc-state-machine-routing` which is more ergonomic for manual debugging.)
2. When the router falls back to artifact inference (no PM session), should it auto-bootstrap a stage_states record from the inferred artifacts? This would upgrade the session so subsequent invocations use the state machine path. The risk is creating orphan state records.
