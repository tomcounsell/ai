# Pipeline State Machine

The Pipeline State Machine (`agent/pipeline_state.py`) provides programmatic stage tracking for the SDLC pipeline. It replaces the inference-based approach (stage detector, skill outcome parser, checkpoint system) with direct state recording at transition points.

## Problem

The previous system inferred pipeline stage status by parsing agent transcripts with regex patterns and cross-checking typed outcome blocks. This led to:
- Completed stages rendering as unchecked in Telegram because regex didn't match the agent's wording
- Four independent systems disagreeing about stage state
- Every reliability patch adding more inference code instead of fixing the root cause

## Solution

Stage status is set programmatically at the points where transitions actually happen. Two paths write stage records:

**Dev-session path** (PM creates dev session via `valor_session create --role dev`, worker executes):
- `start_stage()` in the PreToolUse hook when the PM calls a Skill that maps to a stage
- `complete_stage()` or `fail_stage()` in `_handle_dev_session_completion()` in the worker after harness execution returns

**Skill path** (when PM uses the Skill tool directly, e.g., `Skill(skill="do-build")`):
- `start_stage()` in the PreToolUse hook via `_handle_skill_tool_start()`, mapped by `_SKILL_TO_STAGE` dict
- `complete_stage()` in the PostToolUse hook via `_complete_pipeline_stage()`, reading the current in_progress stage from Redis

State is persisted as a JSON dict on `AgentSession.stage_states` -- one Redis field, no history parsing.

## Production Lifecycle

The state machine is wired into the worker post-completion handler and the SDK hook system. This is the end-to-end flow for a single SDLC stage:

1. **PM creates dev session**: The PM session calls `python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "Stage: BUILD\n..."`.

2. **Worker executes dev session**: `_execute_agent_session()` routes to `get_response_via_harness()` or `get_agent_response_sdk()` based on `DEV_SESSION_HARNESS`.

3. **Dev session executes**: Runs the assigned stage work (e.g., `/do-build`, `/do-test`).
   - If the Skill tool is invoked, **PreToolUse hook fires** and calls `start_stage()` via `_handle_skill_tool_start()`.

4. **Worker post-completion handler fires** (`_handle_dev_session_completion()` in `agent/agent_session_queue.py`):
   - Looks up the parent PM session via `parent_agent_session_id`
   - Loads the `PipelineStateMachine` on the parent and calls `current_stage()`
   - `classify_outcome(stage, None, result_text)` determines success/fail/ambiguous
   - Routes to `complete_stage()` (success/ambiguous) or `fail_stage()` (fail/partial)
   - Posts GitHub stage comment and steers parent PM session

5. **PM receives steering message**: The PM session receives the completion status and routes to the next stage.

### Error Handling

The PreToolUse and PostToolUse hooks and `_handle_dev_session_completion()` all wrap state machine operations in try/except blocks. A failure in `start_stage()`, `complete_stage()`, or `fail_stage()` is logged as a warning but never crashes the worker or PM session. The pipeline state machine is strictly additive — it enhances observability without introducing new failure modes.

## API

```python
from agent.pipeline_state import PipelineStateMachine

sm = PipelineStateMachine(session)

# Transitions
sm.start_stage("BUILD")       # validates PLAN is completed first
sm.complete_stage("BUILD")    # marks BUILD completed, TEST ready
sm.fail_stage("TEST")         # marks TEST failed, PATCH ready

# Queries
sm.current_stage()            # "BUILD" or None
sm.next_stage("success")      # ("TEST", "/do-test")
sm.has_remaining_stages()     # True if pipeline not complete
sm.has_failed_stage()         # True if any stage failed
sm.get_display_progress()     # {stage: status} for DISPLAY_STAGES — stored state only
sm.classify_outcome(stage, stop_reason, output_tail)  # "success"/"fail"/"partial"/"ambiguous"
```

## Stage Statuses

| Status | Meaning |
|--------|---------|
| `pending` | Stage has not started |
| `ready` | Predecessor completed, this stage can start |
| `in_progress` | Stage is currently running |
| `completed` | Stage finished successfully |
| `failed` | Stage finished with failure |

## Ordering Enforcement

The state machine validates transitions using `PIPELINE_EDGES` from `agent/pipeline_graph.py`:

- `start_stage("BUILD")` raises `ValueError` if PLAN is not completed
- ISSUE can always be started (it's the first stage)
- PATCH can start when TEST or REVIEW has failed/completed
- TEST can restart after PATCH completes (cycle support)

## Artifact-Based Inference

Artifact inference was **deleted in PR #733** (issue #729). `get_display_progress()` no longer accepts a `slug=` parameter and does not check plan files, PRs, or GitHub review state. Stored `stage_states` is the single source of truth.

See `docs/features/sdlc-stage-tracking.md` for why inference was removed and how the belt-and-suspenders skill marker system replaces it.

## Outcome Classification

`classify_outcome()` uses a three-tier approach to classify the result of a completed stage:

### Tier 0: OUTCOME Contract (Structured)

Skills can emit a structured OUTCOME contract as an HTML comment in their output:

```
<!-- OUTCOME {"status":"success","stage":"BUILD","artifacts":{"pr_url":"..."}} -->
```

The contract is a JSON object with these fields:
- `status` (required): `"success"`, `"fail"`, or `"partial"`
- `stage` (optional): The stage name (e.g., `"BUILD"`, `"TEST"`, `"REVIEW"`). If present and mismatched with the expected stage, the contract is ignored and classification falls through to Tier 1/2.
- `artifacts` (optional): Stage-specific metadata (PR URLs, test counts, etc.)

If a valid OUTCOME block is found with a recognized status, it is returned immediately -- no further classification is performed. The `"partial"` status enables nuanced routing: for example, a REVIEW that approves but finds nits returns `"partial"`, which triggers a PATCH cycle via `pipeline_graph.py`.

Skills that emit OUTCOME contracts:
- `/do-build`: success (PR created) or fail (build failed)
- `/do-test`: success (all tests passed), fail (test failures), or partial (flaky tests)
- `/do-pr-review`: success (no findings), partial (approved with findings), or fail (changes requested)

### Tier 1: SDK Stop Reason

Non-`end_turn` stop reasons (rate_limited, timeout, etc.) indicate process failures and return `"fail"`.

### Tier 2: Output Tail Patterns

Stage-specific text patterns in the last ~500 chars of output. Each stage has its own pattern set (e.g., `"pull/"` for BUILD, `"passed"` for TEST).

Falls back to `"ambiguous"` when no pattern matches, for the Observer LLM to handle.

## Router Integration (Read Path)

The SDLC router skill (`.claude/skills/sdlc/SKILL.md`) reads `stage_states` as the **primary signal** for routing decisions. This completes the read/write cycle: hooks write stage transitions (via `start_stage()`/`complete_stage()`/`fail_stage()`), and the router reads the resulting state to determine which sub-skill to dispatch next.

### How the Router Reads stage_states

The router invokes `tools/sdlc_stage_query.py` via bash to read stored stage state:

```bash
# Bridge sessions (env var available):
python -m tools.sdlc_stage_query

# Local Claude Code sessions (pass issue number explicitly):
python -m tools.sdlc_stage_query --issue-number 941
```

The CLI tool resolves the PM session from `--session-id`, `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, or `--issue-number` (in that priority order), reads `stage_states`, and returns a JSON dict mapping stage names to statuses.

### Routing Logic

- **stage_states available**: Used as the primary signal. A stage is considered complete only if it shows `"completed"` in stage_states.
- **stage_states unavailable** (empty JSON `{}`): Falls back to conversation dispatch history to determine what has already run. Artifact inference is not used. This previously happened for all local Claude Code invocations; with `--issue-number` support and `sdlc_session_ensure`, local sessions now have trackable state in Redis.

### Merge Gate

Row 10 in the dispatch table (merge-ready) requires ALL display stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS) to show `"completed"` in stage_states. This prevents stages from being silently skipped when a prior stage's work happens to produce artifacts that satisfy a later stage's check (e.g., `/do-build` creating docs does not satisfy the DOCS stage).

When stage_states is unavailable (cold start), the merge gate emits an explicit warning listing every unrecorded stage and requires acknowledgment before proceeding. Artifact inference is not used.

### Merge-Before-Complete Enforcement

The PM persona includes Rule 5 ("MERGE is Mandatory Before Pipeline Complete") which prevents the PM from emitting `[PIPELINE_COMPLETE]` while an open PR exists. Additionally, the worker's `_handle_dev_session_completion` steering message includes a merge reminder, and continuation PMs carry explicit instructions to check for open PRs before completing. See `config/personas/project-manager.md` Rule 5 and issue #1005.

## Integration Points

- **SDLC Router** (`.claude/skills/sdlc/SKILL.md`): Reads `stage_states` via `tools/sdlc_stage_query.py` CLI tool as primary routing signal
- **Stage Query Tool** (`tools/sdlc_stage_query.py`): CLI interface for reading `stage_states` from a PM session by session ID or issue number
- **PreToolUse hook** (`agent/hooks/pre_tool_use.py`): Calls `start_stage()` for the Skill path (`_handle_skill_tool_start()` with `_SKILL_TO_STAGE` mapping), marking the stage as `in_progress`
- **PostToolUse hook** (`agent/hooks/post_tool_use.py`): Calls `complete_stage()` when a mapped SDLC Skill tool finishes (Skill path)
- **Worker post-completion handler** (`agent/agent_session_queue.py:_handle_dev_session_completion()`): Calls `classify_outcome()` and routes to `complete_stage()` or `fail_stage()` after dev session harness returns (dev-session path)
- **SubagentStop hook** (`agent/hooks/subagent_stop.py`): Logs completion only; SDLC tracking moved to worker
- **PM session**: Uses state machine for stage queries and outcome classification
- **Job Queue** (`agent/agent_session_queue.py`): Creates state machine in `send_to_chat()`, applies transitions from Observer decisions
- **AgentSession** (`models/agent_session.py`): `get_stage_progress()` convenience wrapper around `get_display_progress()`
- **Merge Gate** (`.claude/commands/do-merge.md`): Reads `get_display_progress()` for pre-merge pipeline validation

## What Was Deleted

- `bridge/stage_detector.py` -- transcript parsing for stage detection
- `agent/skill_outcome.py` -- typed outcome block parsing
- `agent/checkpoint.py` -- checkpoint persistence for session recovery

## Files

| File | Purpose |
|------|---------|
| `agent/pipeline_state.py` | PipelineStateMachine class — stored-state-only stage tracking (canonical; `bridge/pipeline_state.py` is a shim) |
| `tools/sdlc_stage_marker.py` | CLI tool for skills to write in_progress/completed markers (supports `--issue-number` for local sessions) |
| `agent/pipeline_graph.py` | Transition table (PIPELINE_EDGES, DISPLAY_STAGES) (canonical; `bridge/pipeline_graph.py` is a shim) |
| `models/agent_session.py` | `stage_states` field on AgentSession |
| `tools/sdlc_stage_query.py` | CLI tool for reading stage_states (used by SDLC router, supports `--issue-number`) |
| `tools/sdlc_session_ensure.py` | CLI tool to create/find local SDLC sessions keyed by issue number |
| `tools/_sdlc_utils.py` | Shared `find_session_by_issue()` helper (deduplicated from sdlc_stage_query) |
| `.claude/skills/sdlc/SKILL.md` | SDLC router skill (reads stage_states in Step 2.0) |
| `agent/agent_session_queue.py` | `_handle_dev_session_completion()` — `complete_stage()`/`fail_stage()` wiring for dev-session path |
| `agent/hooks/pre_tool_use.py` | `start_stage()` wiring — Skill path via `_handle_skill_tool_start()` + `_SKILL_TO_STAGE` |
| `agent/hooks/post_tool_use.py` | `complete_stage()` wiring for Skill path via `_complete_pipeline_stage()` |
| `agent/hooks/subagent_stop.py` | Logs completion only (SDLC tracking moved to worker) |
| `tests/unit/test_pipeline_state_machine.py` | State machine unit tests |
| `tests/unit/test_sdlc_stage_query.py` | Stage query CLI tool unit tests |
| `tests/unit/test_pre_tool_use_start_stage.py` | Stage extraction and start_stage wiring tests |
