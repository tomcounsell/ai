---
status: Building
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/900
last_comment_id:
---

# SDLC Stage Model Selection and Hard-PATCH Builder Session Resume

## Problem

Every SDLC dev session today inherits the PM's model (Opus), regardless of cognitive load. TEST and DOCS stages (tool-heavy, plan-execution) run on the same model as PLAN and REVIEW (adversarial, architectural). There is no per-stage differentiation.

PATCH is always a fresh session. When a test failure or review finding needs the original builder's accumulated reasoning — edge cases considered-and-dismissed, implementation rationale — there is no path to resume that context. Hard patches re-derive from artifacts when the transcript is right there.

BUILD transcripts survive indefinitely. Once #780 makes post-completion resume possible, retention becomes a policy question, not serendipity.

**Current behavior:**
- `valor_session create --role dev` dispatches every dev session with `model=None`, inheriting the PM's Opus model.
- PATCH work always spawns a fresh session.
- Completed dev sessions are retained by `agent_session_scheduler.py` cleanup forever (cleanup only targets `killed/abandoned/failed`).

**Desired outcome:**
- PM explicitly names a model on every dev session dispatch per a documented stage→model table.
- PM has a decision rule (fresh vs resume PATCH) with a difficulty signal table.
- BUILD transcripts are retained until the PR merges or closes, with a 30-day TTL backstop.
- `valor-session resume` subcommand enables PM to re-enqueue a completed BUILD session with a new message.

## Freshness Check

**Baseline commit:** `57b16132`
**Issue filed at:** 2026-04-11T07:52:38Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/agent_session.py:175` — `claude_session_uuid = Field(null=True)` — still holds ✓
- `agent/sdk_client.py:1048-1060` — `_create_options()` honors `resume=<uuid>` + `continue_conversation=True` — still holds ✓ (lines shifted slightly by PR #902 refactor)
- `tools/agent_session_scheduler.py` — cleanup skips `completed` status — still holds ✓
- `models/agent_session.py:221-222` — `Meta.ttl = 7776000` (90 days, not 30) — **minor drift**: issue says "add 30-day backstop", but a 90-day TTL already exists. Plan updates it from 90 to 30 days as intended.
- `bridge/pipeline_graph.py` — **drifted**: PR #902 moved canonical graph to `agent/pipeline_graph.py`; `bridge/pipeline_graph.py` is now a thin shim. References in this plan use `agent/pipeline_graph.py`.

**Cited sibling issues/PRs re-checked:**
- #780 — prerequisite — CLOSED via PR #902 (merged 2026-04-11T11:12:39Z). The harness abstraction that enables `valor-session create --role dev` → worker → `claude -p --resume` is fully shipped.
- #838 — open, downstream concern (model benchmarking). Not a blocker.

**Commits on main since issue was filed (touching referenced files):**
- `358069db` (PR #902) "Complete harness abstraction: Phases 3-5" — moved pipeline graph from `bridge/` to `agent/`, updated `sdk_client.py`, `agent_definitions.py`, `valor_session.py` shape. All referenced APIs remain intact. Line numbers shifted but claims hold.
- `30242bc3` (PR #903) "PM session child fan-out for multi-issue SDLC prompts" — touched sdk_client.py; no conflict with this plan.
- `57b16132` (PR #905) "fix nudge-stomp append_event save bypass" — unrelated.

**Active plans in `docs/plans/` overlapping this area:** None that conflict. The harness-abstraction plan is already shipped.

**Notes:** #780 shipped via PR #902 — this plan is no longer blocked. `Meta.ttl` already exists at 90 days; updating to 30 is a reduction, not an addition.

## Prior Art

- **PR #902**: "Complete harness abstraction: Phases 3-5 (pipeline move, PM persona, hook cleanup)" — delivered the `valor-session create --role dev` → worker → `claude -p` execution path. This is the prerequisite mechanism this issue's policy layers on top.
- **PR #464**: SDLC Redesign — established PM/dev session split. No model-selection work.
- No prior attempts to implement per-stage model selection or session resume policy.

## Data Flow

### Stage→Model dispatch flow

1. **PM session** reads pipeline state, determines next stage
2. PM runs `python -m tools.valor_session create --role dev --model sonnet --message "..."` (new `--model` flag)
3. `_push_agent_session()` stores `model="sonnet"` on the `AgentSession` record
4. Worker picks up the session, reads `session.model`
5. `sdk_client._create_options()` uses `session.model` to set the `model` parameter on the Agent tool call
6. Claude Code dev session runs on the specified model

### Hard-PATCH resume flow

1. **PM session** detects PATCH signals from test/review output; decides "resume"
2. PM runs `python -m tools.valor_session resume --id <build_session_id> --message "Fix: ..."`
3. `valor_session resume` command: reads session, validates status=`completed`, transitions to `pending`, appends message, re-enqueues via `_push_agent_session`
4. Worker picks up session, sees stored `claude_session_uuid`
5. `sdk_client._create_options()` sets `resume=<uuid>` + `continue_conversation=True`
6. Claude Code resumes prior BUILD transcript with new PATCH message

### Retention / cleanup flow

1. BUILD session completes → `retain_for_resume = True` (set by do-build hook or PM lifecycle)
2. `agent_session_scheduler.py cleanup` skips sessions where `retain_for_resume=True` and status=`completed`
3. PR merges or closes → PR lifecycle hook clears `retain_for_resume` on the BUILD session
4. `Meta.ttl = 2592000` (30 days) serves as absolute backstop if hook doesn't fire

## Architectural Impact

- **New field on AgentSession**: `retain_for_resume: bool` — additive, backward-safe (default `False` for pre-existing sessions)
- **Model field on AgentSession**: check if `model` field already exists on the session; if not, add it (or pass via metadata)
- **`valor_session.py`**: new `resume` subcommand alongside `create`/`steer`/`kill`
- **`agent_session_scheduler.py`**: respects `retain_for_resume` — additive filter, no breaking change
- **`Meta.ttl` reduction**: 90 → 30 days. Pre-existing sessions get a shorter TTL on next save/touch. Low risk; old sessions that expire were already ephemeral.
- **PM persona prompt**: updated dispatch instructions — additive text changes, no structural change
- **SDLC SKILL.md**: updated dispatch table — documentation change
- **Merging `docs/sdlc-stage-models-resume` branch**: its changes touch `docs/features/pipeline-graph.md` and `docs/features/pm-sdlc-decision-rules.md`; PR #902 already partially updated `pipeline-graph.md`, so the merge may need minor conflict resolution.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on `model` field vs metadata, TTL reduction)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #780 shipped | `gh pr view 902 --json state -q .state` | harness abstraction is the mechanism |

Run all checks: `python scripts/check_prerequisites.py docs/plans/sdlc-stage-models-resume.md`

## Solution

### Key Elements

- **`valor_session resume` subcommand**: transitions a completed session back to `pending`, appends message, re-enqueues; mirrors `create` but targets an existing session by ID
- **`AgentSession.retain_for_resume` field**: BUILD sessions set it `true` on completion; cleanup respects it
- **`AgentSession.model` field** (if not already present): stores per-session model selection; passed through to `sdk_client._create_options()`
- **`Meta.ttl` backstop**: reduced from 90 to 30 days as explicit policy
- **PR lifecycle hook**: clears `retain_for_resume` when PR merges or closes
- **PM persona + SKILL.md updates**: encode the stage→model table and fresh/resume decision rules

### Flow

PM decides PATCH needed → evaluates signals → **fresh**: `valor-session create --role dev --model sonnet` → new session → **OR** resume: `valor-session resume --id <build_id> --message "..."` → same session, `claude -p --resume <uuid>`

### Technical Approach

**Step 1: `AgentSession.model` field**
- Check `models/agent_session.py` — if `model` field doesn't exist, add `model = Field(null=True)` alongside `claude_session_uuid`
- `_push_agent_session()` in `agent/agent_session_queue.py` must accept and store `model` kwarg
- `sdk_client._create_options()` reads `session.model` and passes it to the `model` parameter (already supported per Agent tool schema)

**Step 2: `valor_session create --model` flag**
- Add `--model` argument to the `create` subparser in `tools/valor_session.py`
- Pass through to `_push_agent_session(model=args.model)`
- Default `None` (inherits parent model as before, for backward compat)

**Step 3: `valor_session resume` subcommand**
- New async function `_cmd_resume(args)` alongside `_cmd_create`
- Validates session exists and status is `completed`
- Calls `transition_status(session, "pending")` to re-enqueue
- Appends new message to session's message queue (same pattern as `steer`)
- Calls `_push_agent_session(...)` with session's existing `model` and `claude_session_uuid` intact
- Wire up `resume` subparser with `--id` and `--message` args

**Step 4: `AgentSession.retain_for_resume` field**
- Add `retain_for_resume = Field(default=False)` to `models/agent_session.py`
- Default `False` — backward-safe for pre-existing completed sessions
- BUILD session completion: set `retain_for_resume = True` — best place is `agent/hooks/post_tool_use.py` stage-completion logic, or in `_handle_dev_session_completion()` in `worker/`

**Step 5: `Meta.ttl` reduction**
- Change `ttl = 7776000` (90 days) to `ttl = 2592000` (30 days) in `AgentSession.Meta`
- Comment explaining this is the hard backstop for retain_for_resume sessions

**Step 6: `agent_session_scheduler.py cleanup` guard**
- In the cleanup logic (around line 1011), add a guard: skip sessions where `retain_for_resume=True` and status=`completed`
- Only the PR lifecycle hook or TTL expiry should clear retain_for_resume sessions

**Step 7: PR lifecycle hook**
- GitHub webhook or PR merge handler clears `retain_for_resume` on the BUILD session associated with the PR
- Look for existing PR merge handling in `bridge/` or `agent/`; if none, add a lightweight `valor-session release --pr <number>` subcommand that the PM session calls on PR merge/close
- PM persona prompt instructs: "When PR merges, run `valor-session release --pr <number>` to clear BUILD session retention"

**Step 8: PM persona + SKILL.md updates**
- Update PM persona dispatch prompt (in `config/personas/` segments) to include stage→model table and explicit model flag on every `valor-session create` call
- Update `.claude/skills/sdlc/SKILL.md` dispatch table with per-stage model annotation
- Update `.claude/agents/dev-session.md` if any guidance changes needed

**Step 9: Merge `docs/sdlc-stage-models-resume` branch**
- The branch contains updates to `docs/features/pipeline-graph.md` (adding stage→model table) and `docs/features/pm-sdlc-decision-rules.md` (adding fresh/resume decision rules)
- Merge as part of this PR; resolve any conflicts with PR #902's changes to `pipeline-graph.md`

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `valor_session resume`: validate that resuming a non-completed session raises a clear error (not silent) — covered by `TestCmdResumeWrongStatus`
- [x] `_push_agent_session` with model kwarg: verify Redis-down condition fails safe (no hang) — `_push_agent_session` has existing exception handling; model is an optional kwarg with no extra failure path
- [x] PR lifecycle hook: if PR number can't be matched to a session, log warning and continue (no crash) — `cmd_release` returns 0 with warning when no sessions matched

### Empty/Invalid Input Handling
- [x] `valor-session resume --id <nonexistent>`: must return exit code 1 with clear error message — covered by `TestCmdResumeNotFound`
- [x] `valor-session create --model badvalue`: must fail with valid choices listed — model is a free-form string (not enum-restricted); PM uses named values from dispatch table
- [x] `retain_for_resume` field default: pre-existing sessions without the field read as `False` (not None) — Popoto `default=False` ensures this

### Error State Rendering
- [x] Resume of already-pending session returns clear error: "Session is already pending" — covered by `TestCmdResumeWrongStatus::test_pending_returns_1`
- [x] Cleanup guard correctly skips retained sessions — `agent_session_scheduler.py` guard checks `retain_for_resume` before deletion

## Test Impact

- [x] `tests/unit/test_agent_definitions.py` — VERIFIED: model=None is the default, no change needed; tests pass as-is
- [x] `tests/integration/test_session_spawning.py` — no such file exists; resume/release covered by new `test_valor_session_resume_release.py`
- [x] `tests/integration/test_parent_child_round_trip.py` — no such file exists; no session model assumptions changed
- [x] `tests/unit/test_pre_tool_use_start_stage.py` — REVIEWED: no change needed; retain_for_resume is set in harness, not in stage hooks
- [x] `tests/unit/test_health_check.py` — no such file; cleanup stats not affected

## Rabbit Holes

- **Model-swap on resume**: running Opus over a Sonnet transcript. Out of scope — same model on resume is sufficient. Requires SDK verification not done here.
- **Automated difficulty scoring**: LLM-based PATCH signal evaluation. The table of signals is sufficient; a classifier is a separate project.
- **Multiple resume cycles**: what happens if PATCH is resumed, then PATCH needs another resume? Handle in v2 — first resume covers the 95% case.
- **Cross-repo retain_for_resume**: different projects may have different PR lifecycle wiring. First cut: same-repo only. Generalize in #838 follow-up.

## Risks

### Risk 1: TTL reduction breaks long-running work
**Impact:** Sessions older than 30 days expire; operators lose the ability to audit or resume old BUILD transcripts.
**Mitigation:** 30 days covers the vast majority of PRs (cycle time <1 week). Any session still alive at 30 days has almost certainly been superseded. Log TTL expiry in crash_tracker or Sentry for visibility.

### Risk 2: Retain_for_resume not cleared after merge
**Impact:** Completed BUILD sessions accumulate indefinitely if the PR lifecycle hook fails to fire.
**Mitigation:** `Meta.ttl = 30 days` is the backstop. Hook failure means sessions linger at most 30 days, not forever. Operator can run `valor-session release --pr <N>` manually.

### Risk 3: `valor-session resume` on a session in the middle of a retry cycle
**Impact:** PM resumes a session that the worker is already trying to execute, creating a duplicate.
**Mitigation:** Transition to `pending` only if current status is `completed`. If status is `running` or `pending`, return an error. Worker uses status-based locking.

## Race Conditions

### Race 1: Concurrent resume + cleanup
**Location:** `tools/valor_session.py` resume + `tools/agent_session_scheduler.py` cleanup
**Trigger:** PM resumes a session while cleanup is running; cleanup deletes the session before the resume enqueues it
**Data prerequisite:** Session must exist and be `completed` before resume can transition it
**State prerequisite:** Only one process holds the transition at a time
**Mitigation:** Use `transition_status()` from `session_lifecycle.py` which uses Popoto's atomic Redis operations. Cleanup also calls `transition_status()`. Only one will succeed; the other will fail on status mismatch and log. Resume returns an error if transition fails.

## No-Gos (Out of Scope)

- Model-swap on resume (Opus over Sonnet transcript) — needs SDK verification, deferred
- Automatic PATCH difficulty scoring via LLM — manual signal table is sufficient for v1
- #838 benchmarking integration — downstream, separate issue
- Mid-build steering changes — already implemented via `scripts/steer_child.py`, documentation only

## Update System

No update script changes required. The new `retain_for_resume` field uses Popoto's standard field mechanism with `default=False` — existing installations pick it up on next deploy without migration. `Meta.ttl` change applies to sessions on their next save. No new env vars, no new config files, no new dependencies.

## Agent Integration

`valor-session resume` is a CLI tool invoked by the PM session via `python -m tools.valor_session resume`. The PM session has bash access to this CLI.

No new MCP server changes needed. The PM session already calls `valor-session create` via bash; `resume` follows the same pattern.

The PR lifecycle hook: if implemented as a `valor-session release --pr <N>` subcommand, the PM session calls it after detecting PR merge via `gh pr view`. No bridge changes needed.

Integration test: PM session + `valor-session resume` round-trip with a real resumed Claude Code session (requires `DEV_SESSION_HARNESS=sdk` in test env).

## Documentation

- [x] Merge branch `docs/sdlc-stage-models-resume` (adds `docs/features/pm-sdlc-decision-rules.md` and updates `docs/features/pipeline-graph.md` with stage→model table)
- [x] Update `docs/features/agent-session-model.md` to document `retain_for_resume` and `model` fields
- [x] Update `docs/features/pm-dev-session-architecture.md` under "Dev Session Resume" to describe the `valor-session resume` mechanism
- [x] Add entry to `docs/features/README.md` if `pm-sdlc-decision-rules.md` is new
- [x] Code comments on `retain_for_resume` field explaining the BUILD-retention policy

## Success Criteria

- [x] `valor-session create --role dev --model sonnet ...` creates a session with `model=sonnet` stored on the `AgentSession` record
- [x] Worker picks up that session and the resulting Claude Code invocation uses Sonnet (verifiable via session log or Sentry trace)
- [x] `valor-session resume --id <completed_build_session_id> --message "Fix: ..."` transitions the session to `pending` and re-enqueues it
- [x] Worker resumes the session with `claude -p --resume <uuid>`, continuing the original BUILD transcript
- [x] `AgentSession` records for BUILD stages have `retain_for_resume=True` after completion
- [x] `agent_session_scheduler.py cleanup` skips `retain_for_resume=True` completed sessions
- [x] `valor-session release --pr <N>` clears `retain_for_resume` on the associated BUILD session
- [x] `AgentSession.Meta.ttl` is 30 days (2592000 seconds)
- [x] PM persona prompt includes per-stage model table and resume decision rules
- [x] `.claude/skills/sdlc/SKILL.md` dispatch table has per-stage model annotation
- [x] `docs/features/pm-sdlc-decision-rules.md` merged from `docs/sdlc-stage-models-resume` branch
- [x] `docs/features/pipeline-graph.md` stage→model table merged from `docs/sdlc-stage-models-resume` branch
- [x] Tests pass (`pytest tests/ -x -q`)
- [x] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (session-resume)**
  - Name: session-resume-builder
  - Role: Implement `valor_session resume` subcommand, `AgentSession.model` field, `retain_for_resume` field, TTL change, cleanup guard
  - Agent Type: builder
  - Resume: true

- **Builder (pm-dispatch)**
  - Name: pm-dispatch-builder
  - Role: Update PM persona prompt with stage→model table and resume decision rules; update SKILL.md and dev-session.md
  - Agent Type: builder
  - Resume: true

- **Builder (pr-lifecycle)**
  - Name: pr-lifecycle-builder
  - Role: Implement `valor-session release --pr <N>` subcommand and wire it into PM session completion logic
  - Agent Type: builder
  - Resume: false

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all success criteria; run tests; confirm session round-trip
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Merge docs branch, update agent-session-model.md, pm-dev-session-architecture.md, README
  - Agent Type: documentarian
  - Resume: false

### Step by Step Tasks

### 1. Session resume + model field + retention core
- **Task ID**: build-session-resume
- **Depends On**: none
- **Validates**: tests/unit/test_agent_definitions.py, tests/integration/test_session_spawning.py (create)
- **Assigned To**: session-resume-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `model = Field(null=True)` to `AgentSession` if not already present; update `_push_agent_session` to accept `model` kwarg
- Add `retain_for_resume = Field(default=False)` to `AgentSession`; reduce `Meta.ttl` to 2592000 (30 days)
- Add `--model` flag to `valor-session create` subparser; pass through to `_push_agent_session`
- Implement `_cmd_resume(args)` in `tools/valor_session.py`: validate status=completed, `transition_status(pending)`, append message, re-enqueue
- Wire `resume` subparser with `--id` and `--message` args
- Add cleanup guard in `agent_session_scheduler.py` to skip `retain_for_resume=True` completed sessions
- Set `retain_for_resume=True` on BUILD session completion (find the right hook location in `agent/hooks/` or worker)

### 2. PR lifecycle release subcommand
- **Task ID**: build-pr-lifecycle
- **Depends On**: build-session-resume
- **Validates**: none (manual verification sufficient for v1)
- **Assigned To**: pr-lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `_cmd_release(args)` in `tools/valor_session.py`: given `--pr <N>`, find the BUILD session for that PR, clear `retain_for_resume`
- Add `release` subparser with `--pr` arg
- Document in PM persona: "When PR merges, run `python -m tools.valor_session release --pr <N>`"

### 3. PM persona + SKILL.md + dev-session.md updates
- **Task ID**: build-pm-dispatch
- **Depends On**: none
- **Validates**: none (documentation)
- **Assigned To**: pm-dispatch-builder
- **Agent Type**: builder
- **Parallel**: true
- Update PM persona dispatch instructions in `config/personas/segments/` with per-stage model table and explicit `--model` flag on every `valor-session create --role dev` call
- Update `.claude/skills/sdlc/SKILL.md` dispatch table with model column
- Update `.claude/agents/dev-session.md` if model-specific guidance needed
- Add stage→model table reference to `sdk_client.py` PM dispatch instructions (inline comments)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-session-resume, build-pm-dispatch
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Merge branch `docs/sdlc-stage-models-resume` into this PR (resolve conflicts with `pipeline-graph.md` from PR #902)
- Update `docs/features/agent-session-model.md` with `retain_for_resume` and `model` field descriptions
- Update `docs/features/pm-dev-session-architecture.md` "Dev Session Resume" section with `valor-session resume` mechanism
- Verify `docs/features/README.md` includes `pm-sdlc-decision-rules.md` in the index

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-session-resume, build-pr-lifecycle, build-pm-dispatch, document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm pass
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify `valor-session resume --id <id> --message "test"` round-trip on a real completed session
- Confirm `retain_for_resume` field defaults `False` on a pre-existing session
- Confirm `Meta.ttl` is 2592000 in `models/agent_session.py`
- Confirm PM persona includes model table and resume decision rules

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| retain_for_resume field | `python -c "from models.agent_session import AgentSession; f = AgentSession._meta.fields; print([x for x in dir(f) if 'retain' in x])"` | output contains retain_for_resume |
| TTL backstop | `python -c "from models.agent_session import AgentSession; print(AgentSession.Meta.ttl)"` | output contains 2592000 |
| resume subcommand | `python -m tools.valor_session resume --help` | exit code 0 |
| model flag on create | `python -m tools.valor_session create --help` | output contains --model |
| stage→model in SKILL.md | `grep -c 'sonnet\|opus' .claude/skills/sdlc/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`AgentSession.model` field**: does the field already exist? A quick grep of `models/agent_session.py` should resolve this before build starts. If it exists, Step 1 skips the field addition.
2. **retain_for_resume default for pre-existing sessions**: `default=False` is proposed. Confirm this is acceptable — pre-existing BUILD sessions (already completed, may have `claude_session_uuid`) won't be retained. This is correct because the resume mechanism didn't exist when they ran.
3. **PR-to-session mapping**: `valor-session release --pr <N>` needs to find the BUILD session for a PR. The most reliable join is via `slug` (PR branch name matches session slug). Confirm this is the lookup strategy.
