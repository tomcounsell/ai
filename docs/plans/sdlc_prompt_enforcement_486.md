---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/486
last_comment_id:
---

# SDLC Prompt Enforcement: Stage-by-Stage Agent Orchestration

## Problem

PR #483 was completed by a dev-session that bypassed the entire SDLC pipeline — no test, review, docs, or gate stages executed. The PM spawned a dev-session, which assessed the PR and completed it in 130 seconds with "no stage progress to render."

**Current behavior:**
The dev-session agent description says "execute the complete SDLC pipeline in a single session." The PM prompt injection tells the PM to "spawn a dev-session" with no instruction to orchestrate stages. The dev-session runs the whole pipeline (or skips it entirely) in one shot.

**Desired outcome:**
The PM orchestrates SDLC work stage-by-stage: assess which stage is next, spawn a dev-session for that one stage, verify the result, then progress to the next stage. Dev-sessions are single-stage executors that report back to the PM. All prompts use positive/instructive language.

## Prior Art

- **Issue #465 / PR #466**: SDLC Redesign Phase 2 — established ChatSession/DevSession architecture. Bridge became dumb (nudge loop), PM owns orchestration, DevSession executes. Architecture is correct; prompts don't enforce it.
- **Issue #467**: Pipeline cleanup — removed dead code, `simple` sessions, added e2e tests. Cleaned up plumbing but didn't address prompt content.
- **Issue #474**: PM persona cleanup — removed playlist references, verified dev-session dispatch injection exists. Didn't address stage-by-stage orchestration.
- **Issue #459 / PR #464**: SDLC Redesign Phase 1 — defined the full architecture, introduced session_type discriminator.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #465/466 | Built ChatSession/DevSession architecture | Correct architecture, but dev-session.md still describes itself as full-pipeline executor |
| #474 | Verified PM dispatch injection | Only checked injection exists, not what it says. PM injection lacks stage orchestration instructions |

**Root cause pattern:** Each iteration built correct plumbing but left the agent instructions unchanged. The agents follow their prompts, not the architecture diagrams.

## Data Flow

1. **Telegram message** → Bridge classifies as `sdlc`
2. **Bridge** → Constructs `enriched_message` in `sdk_client.py:1343-1395`
3. **PM prompt injection** (`sdk_client.py:1384`) → Appends "spawn a dev-session" instruction
4. **PM (ChatSession)** → Reads instruction, spawns dev-session Agent with a prompt
5. **Dev-session** → Reads its own `dev-session.md` system prompt + PM's prompt
6. **Dev-session** → Executes work (currently: entire pipeline or nothing)
7. **Dev-session** → Returns result to PM
8. **PM** → Composes delivery message → Telegram

The enforcement gap is at steps 3-5: the PM injection doesn't instruct stage-by-stage orchestration, and dev-session.md tells the agent to run the whole pipeline.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (review prompt wording before deploy)
- Review rounds: 1

## Prerequisites

No prerequisites — this work modifies prompt text and validation logic only.

## Solution

### Key Elements

- **PM prompt injection rewrite**: Instruct the PM to assess the current SDLC stage, spawn one dev-session for that stage, verify the result, then decide the next stage
- **dev-session.md rewrite**: Single-stage executor — receives a stage assignment, executes it, reports result
- **PM persona audit**: Verify the private persona file reinforces stage orchestration
- **SKILL.md update**: Replace Observer Agent references with PM-orchestrated progression; align with single-stage dev-session model
- **Completion validation**: When an SDLC session completes without stage progress, log a warning (see Task 4 for implementation spec)
- **Positive language pass (scoped)**: Rewrite "NEVER/do NOT" patterns in `dev-session.md` and PM injection only. Leave SKILL.md Hard Rules as intentional safety constraints.

### Flow

**SDLC message arrives** → PM assesses current stage → PM spawns dev-session for ONE stage → dev-session executes stage → dev-session reports result → PM verifies → PM spawns next dev-session (or reports done)

### Technical Approach

- Rewrite `sdk_client.py` PM injection (the `if _session_type == "chat":` block) to include stage assessment and single-stage dispatch instructions. Clarify that PM can run read-only Bash commands (gh, grep) for stage assessment — `pre_tool_use.py` only blocks Write/Edit to non-docs paths, not Bash reads.
- Rewrite `.claude/agents/dev-session.md` as single-stage executor
- Update `.claude/skills/sdlc/SKILL.md` — replace Observer Agent references (lines 9, 91) with PM-orchestrated progression model
- Add completion check in `.claude/hooks/stop.py`: use `get_session_id()` to get session ID, import `load_sdlc_state()` from `post_tool_use` to check sdlc_state.json, load `AgentSession` from Redis (same pattern as `_update_agent_session_log_path`), check `classification_type == "sdlc"` with no stage progress, print warning to stderr
- Audit `~/Desktop/Valor/personas/project-manager.md` for alignment

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is prompt text changes

### Empty/Invalid Input Handling
- [ ] Verify PM still functions when stage assessment returns ambiguous state (e.g., PR exists but no tests run)
- [ ] Verify dev-session handles receiving a stage assignment with missing context gracefully

### Error State Rendering
- [ ] Verify the completion warning for "no stage progress" is visible in bridge logs

## Test Impact

- [ ] `tests/unit/test_post_tool_use_sdlc.py` — UPDATE: verify `load_sdlc_state` import path works when imported from stop hook
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: add assertions that SKILL.md no longer references Observer Agent
- [ ] `tests/unit/test_stop_hook_sdlc_warning.py` — ADD: new test file for the stop hook completion warning. Test cases: (a) SDLC-classified session with no stage progress emits warning, (b) SDLC session with stage progress emits no warning, (c) non-SDLC session emits no warning, (d) Redis unavailable degrades gracefully (no crash)

No major test breakage expected for existing tests — changes are primarily to prompt text. The new Python logic (stop hook warning) gets a dedicated test file.

## Rabbit Holes

- Building a hard-blocking stage gate system — the enforcement should be prompt-level guidance, not infrastructure blocks
- Rewriting the entire SDLC skill system — only the prompt injection, agent description, and Observer references in SKILL.md need changing. The SKILL.md routing logic and stage table are correct.
- Adding new pipeline state machine logic — the existing `stage_states` field on AgentSession is sufficient

## Risks

### Risk 1: PM ignores stage instructions
**Impact:** Same behavior as today — dev-session runs whole pipeline
**Mitigation:** The completion warning catches this. Iterate on prompt wording based on observed behavior.

### Risk 2: Stage-by-stage is too slow for simple PRs
**Impact:** Docs-only PRs take 5 dev-session spawns instead of 1
**Mitigation:** PM instructions should include: for trivial/docs-only work, assess whether full pipeline is warranted. The PM makes the judgment call.

## Race Conditions

No race conditions identified — changes are to prompt text and logging, all synchronous.

## No-Gos (Out of Scope)

- Hard-blocking merge guards in SDK hooks — this is prompt-level guidance only
- Changing bridge classification logic — classification is correct
- Modifying the nudge loop or job queue — not relevant
- Building a stage UI — #461 (Observer UI) is separate

## Update System

No update system changes required — prompt text changes are picked up on next bridge restart. The private persona file syncs via iCloud.

## Agent Integration

No agent integration required — this modifies the prompts that agents receive, not the tools they can use. No MCP or bridge changes needed.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` to document stage-by-stage orchestration model
- [ ] Add entry to `docs/features/README.md` if new feature doc created

## Success Criteria

- [ ] dev-session.md describes a single-stage executor
- [ ] PM prompt injection at `sdk_client.py:1384` instructs stage-by-stage orchestration
- [ ] PM persona file reviewed and aligned with stage model
- [ ] SKILL.md Observer references replaced with PM-orchestrated progression
- [ ] `dev-session.md` and PM injection use positive/instructive language (SKILL.md Hard Rules exempted as safety constraints)
- [ ] SDLC sessions that complete without stage progress generate a stderr warning
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-builder
  - Role: Rewrite all prompt text (sdk_client injection, dev-session.md, persona audit)
  - Agent Type: builder
  - Resume: true

- **Validator (prompts)**
  - Name: prompt-validator
  - Role: Verify all prompts use positive language, stage model is consistent across files
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite dev-session agent definition
- **Task ID**: build-dev-session-prompt
- **Depends On**: none
- **Validates**: `grep -c "single.stage\|one stage\|assigned stage" .claude/agents/dev-session.md` returns > 0 AND `grep -c "NEVER\|do NOT" .claude/agents/dev-session.md` returns 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `.claude/agents/dev-session.md` as single-stage executor
- Remove "complete SDLC pipeline in a single session"
- Add: receives stage assignment from PM, executes that stage, reports result
- Use positive/instructive language throughout (no NEVER/do NOT patterns)

### 2. Rewrite PM prompt injection
- **Task ID**: build-pm-injection
- **Depends On**: none
- **Validates**: `grep -c "one dev-session\|stage.by.stage\|one stage" agent/sdk_client.py` returns > 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the `if _session_type == "chat":` block in `sdk_client.py` to instruct stage-by-stage orchestration
- Include: use read-only Bash (gh, grep) to assess current stage, spawn one dev-session per stage, verify result before progressing
- Clarify that PM can run Bash for reads (pre_tool_use.py only blocks Write/Edit to non-docs paths)

### 3. Audit PM persona file
- **Task ID**: build-persona-audit
- **Depends On**: none
- **Validates**: `grep -c "stage" ~/Desktop/Valor/personas/project-manager.md` returns > 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `~/Desktop/Valor/personas/project-manager.md`
- Verify stage orchestration is reinforced
- Add stage guidance if missing

### 4. Add completion warning to stop hook
- **Task ID**: build-completion-warning
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_stop_hook_sdlc_warning.py -x -q` passes
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a new function `_check_sdlc_stage_progress(session_id)` to `.claude/hooks/stop.py`
- Implementation steps:
  1. Get `session_id` via existing `get_session_id(hook_input)` (already in stop.py main)
  2. Import `load_sdlc_state` from `.claude/hooks/post_tool_use` to check if `sdlc_state.json` exists for this session
  3. Load `AgentSession` from Redis using the same pattern as `_update_agent_session_log_path` (query `AgentSession.query.filter(session_id=session_id)`)
  4. Check if `classification_type == "sdlc"` on the session
  5. If SDLC-classified, check `sdlc_stages` and `stage_states` fields. If both are empty/null, print warning to stderr: `"SDLC WARNING: Session {session_id} classified as SDLC but completed with no stage progress"`
  6. Wrap in try/except (non-fatal, same pattern as `_update_agent_session_log_path`)
- Create `tests/unit/test_stop_hook_sdlc_warning.py` with 4 test cases: (a) SDLC + no stages = warning, (b) SDLC + stages = no warning, (c) non-SDLC = no warning, (d) Redis unavailable = no crash

### 4.5. Update SKILL.md Observer references
- **Task ID**: build-skill-md-update
- **Depends On**: none
- **Validates**: `grep -c "Observer" .claude/skills/sdlc/SKILL.md` returns 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace Observer Agent references in SKILL.md with PM-orchestrated progression
- Line 9: change "The Observer Agent handles pipeline progression by re-invoking `/sdlc`" to "The PM (ChatSession) handles pipeline progression by re-invoking `/sdlc`"
- Line 91: change "NEVER loop -- invoke one sub-skill, then return. The Observer handles progression." to equivalent PM-based instruction
- Keep Hard Rules as explicit safety constraints (do not rewrite to positive language — these are router-level prohibitions)

### 5. Positive language pass (scoped)
- **Task ID**: build-positive-language
- **Depends On**: build-dev-session-prompt, build-pm-injection
- **Validates**: `grep -c "NEVER\|do NOT" .claude/agents/dev-session.md` returns 0 AND `grep "NEVER\|do NOT" agent/sdk_client.py | grep -c "session_type"` returns 0
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: false
- Scope: only `.claude/agents/dev-session.md` and the PM injection block in `agent/sdk_client.py`
- Rewrite "NEVER X" as "Always Y" / "Use X instead of Y" — convert prohibitions into instructions
- Explicitly excluded: `.claude/skills/sdlc/SKILL.md` Hard Rules (safety constraints), CLAUDE.md, other skill files

### 6. Validation
- **Task ID**: validate-all
- **Depends On**: build-dev-session-prompt, build-pm-injection, build-persona-audit, build-completion-warning, build-skill-md-update, build-positive-language
- **Validates**: `pytest tests/ -x -q` exit code 0 AND `python -m ruff check .` exit code 0
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify dev-session.md describes single-stage model
- Verify PM injection includes stage orchestration
- Verify SKILL.md has no Observer references
- Verify no "NEVER"/"do NOT" patterns remain in dev-session.md or PM injection
- Run test suite and linter

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Validates**: `grep -c "stage-by-stage\|single-stage" docs/features/chat-dev-session-architecture.md` returns > 0
- **Assigned To**: prompt-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/chat-dev-session-architecture.md` to document stage-by-stage orchestration model

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No NEVER in dev-session | `grep -c "NEVER\|do NOT" .claude/agents/dev-session.md` | exit code 1 |
| Single-stage language | `grep -c "single.stage\|one stage\|assigned stage" .claude/agents/dev-session.md` | output > 0 |
| PM injection has stage | `grep -c "one dev-session\|stage.by.stage\|one stage" agent/sdk_client.py` | output > 0 |
| No Observer in SKILL.md | `grep -c "Observer" .claude/skills/sdlc/SKILL.md` | output = 0 |
| Stop hook warning test | `pytest tests/unit/test_stop_hook_sdlc_warning.py -x -q` | exit code 0 |

## Critique Results

**Critique date:** 2026-03-23
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: 7 total (2 blockers, 3 concerns, 2 nits)

### Blockers

#### 1. Stop hook has no stage-progress validation (Task 4 has no implementation target)
- **Severity**: BLOCKER
- **Critics**: Skeptic, Operator
- **Location**: Task 4 (build-completion-warning), `.claude/hooks/stop.py`
- **Finding**: Task 4 says "In stop hook or session completion path, log warning when SDLC session has no stage_states." But the stop hook (`stop.py`) has zero stage awareness -- it only copies transcripts and saves metadata. The plan does not specify WHERE in the stop hook to add this check, how to access AgentSession from the hook context (the hook receives `hook_input` dict, not a session object), or how to determine if a session was SDLC-classified from the hook's limited context. The validates field points to `tests/unit/test_pipeline_integrity.py` which currently tests URL construction and merge guards -- unrelated to completion warnings.
- **Suggestion**: Specify the exact implementation: (a) the stop hook must read `VALOR_SESSION_ID` from env, (b) query AgentSession from Redis, (c) check `is_sdlc` and whether any stages progressed, (d) log the warning. Add a new test file or clearly new test class, not a vague reference to an existing unrelated test file.

#### 2. SKILL.md contradiction -- plan changes dev-session but not the SDLC skill that defines stage routing
- **Severity**: BLOCKER
- **Critics**: Archaeologist, Skeptic
- **Location**: Solution section, `.claude/skills/sdlc/SKILL.md`
- **Finding**: The plan rewrites `dev-session.md` as a "single-stage executor" but does not address `.claude/skills/sdlc/SKILL.md`, which is the actual stage router the dev-session invokes. SKILL.md line 9 says "The Observer Agent handles pipeline progression" (Observer was deleted in PR #466). SKILL.md line 91 says "NEVER loop -- invoke one sub-skill, then return. The Observer handles progression." The Observer no longer exists. If dev-session becomes a single-stage executor that returns to the PM, but SKILL.md still references a deleted Observer for progression, the pipeline will stall after the first stage.
- **Suggestion**: Add a task to audit and update SKILL.md: remove Observer references (replaced by PM orchestration), ensure stage routing instructions align with the new PM-orchestrates-stage-by-stage model. This is the same root cause pattern from prior art: correct architecture built, but agent instructions left unchanged.

### Concerns

#### 3. Positive language pass scope is unbounded
- **Severity**: CONCERN
- **Critics**: Simplifier, Skeptic
- **Location**: Task 5 (build-positive-language)
- **Finding**: Task 5 says "Grep all prompt files for NEVER, do NOT, don't, cannot" and rewrite each. But SKILL.md alone has 7 NEVER patterns (lines 85-91), many of which are legitimate safety rails (e.g., "NEVER commit to main"). Blindly rewriting safety-critical negative instructions as positive framing risks weakening them. The plan also doesn't define which files are "all prompt files" -- does it include CLAUDE.md, all skill files, persona files?
- **Suggestion**: Scope this task explicitly: only rewrite negatives in `dev-session.md` and the PM injection in `sdk_client.py` (the two files being rewritten). Leave SKILL.md safety rails as-is or mark them as intentional exceptions. Add acceptance criteria for what "positive language" means vs. legitimate prohibitions.

#### 4. PM prompt injection has no stage context to pass
- **Severity**: CONCERN
- **Critics**: Adversary, Operator
- **Location**: Task 2 (build-pm-injection), `sdk_client.py:1384-1394`
- **Finding**: The plan instructs the PM to "assess current stage" and "spawn one dev-session per stage." But the PM injection at sdk_client.py:1384 runs at message construction time -- the PM has no pre-loaded context about what SDLC stage the work is in. The PM would need to run shell commands (gh pr list, grep plans, etc.) before spawning a dev-session. The plan assumes the PM can do stage assessment but doesn't specify how. Current PM is read-only (pre_tool_use.py blocks non-docs writes) and can only use the Agent tool for code work -- can it run Bash commands for `gh pr list`?
- **Suggestion**: Clarify in the plan that the PM can execute read-only Bash commands (gh, grep) for stage assessment without hitting the write guard. Verify pre_tool_use.py allows Bash reads for PM sessions. If not, the PM injection must include explicit instructions on using Bash for assessment before spawning.

#### 5. Four tasks lack validation commands
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Tasks 3, 5, 6, 7
- **Finding**: Tasks build-persona-audit, build-positive-language, validate-all, and document-feature have no `Validates` field. The plan's Verification table at the bottom provides some checks, but these are not linked to specific tasks. A builder agent would not know how to verify task 3 (persona audit) is done correctly.
- **Suggestion**: Add explicit validates to each task. Task 3: `grep -c "stage" ~/Desktop/Valor/personas/project-manager.md` returns > 0. Task 5: `grep -c "NEVER\|do NOT" .claude/agents/dev-session.md` returns 0. Task 6: `pytest tests/ -x -q` exit 0. Task 7: `test -f docs/features/chat-dev-session-architecture.md` (already exists, so validate updated content).

### Nits

#### 6. Line references may drift
- **Severity**: NIT
- **Critics**: Operator
- **Location**: Solution, Task 2
- **Finding**: Plan references `sdk_client.py:1384-1394` and `sdk_client.py:1343-1395` by line number. These will drift as the file is edited. The current PM injection is at lines 1384-1394 as stated, but any preceding edit would shift these.
- **Suggestion**: Reference the code by content marker (e.g., "the `if _session_type == 'chat':` block in sdk_client.py") rather than line numbers, or accept the drift risk since this is a single-session task.

#### 7. Test Impact section is thin
- **Severity**: NIT
- **Critics**: User
- **Location**: Test Impact section
- **Finding**: The Test Impact section lists two test files with vague dispositions ("may need updating"). Since the plan changes prompt text and adds a completion warning (Python logic), `test_pipeline_integrity.py` will likely need a new test class for the warning, not just "may need" updating. The section also doesn't mention potential impact on integration tests that exercise the full SDLC flow.
- **Suggestion**: Be specific: "ADD new test class in test_pipeline_integrity.py for completion warning" and check if any integration tests exercise the PM-spawns-dev-session flow that would be affected by prompt changes.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-7 sequential, no gaps |
| Dependencies valid | PASS | All Depends On references resolve to valid task IDs |
| File paths exist | PASS | 10 of 10 referenced files exist |
| Prerequisites met | PASS | Plan states no prerequisites |
| Cross-references | PASS | No-Gos do not conflict with Solution; all 7 success criteria map to tasks |

### Verdict

**NEEDS REVISION** -- 2 blockers resolved in revision:

1. ~~Task 4 (completion warning) needs a concrete implementation spec~~ -- RESOLVED: Task 4 now specifies exact implementation steps (get session_id, import load_sdlc_state, query AgentSession, check classification_type, check stage fields). New test file `test_stop_hook_sdlc_warning.py` added. Old validates reference to unrelated test file replaced.
2. ~~SKILL.md must be added to audit scope~~ -- RESOLVED: New Task 4.5 (build-skill-md-update) added to replace Observer references with PM-orchestrated progression. SKILL.md added to Scope, Solution, Success Criteria, and Verification sections.

Concerns 3-5 also addressed: positive language pass scoped to 2 files only, PM Bash read capability clarified, all tasks now have Validates fields.

---

## Open Questions

Resolved — PM decides both:
1. ~~Max stage iterations~~ — PM uses judgment on when to escalate vs keep iterating
2. ~~Stage skipping for trivial work~~ — PM assesses whether full pipeline is warranted
