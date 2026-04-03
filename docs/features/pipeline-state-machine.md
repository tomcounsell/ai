# Pipeline State Machine

The Pipeline State Machine (`bridge/pipeline_state.py`) provides programmatic stage tracking for the SDLC pipeline. It replaces the inference-based approach (stage detector, skill outcome parser, checkpoint system) with direct state recording at transition points.

## Problem

The previous system inferred pipeline stage status by parsing agent transcripts with regex patterns and cross-checking typed outcome blocks. This led to:
- Completed stages rendering as unchecked in Telegram because regex didn't match the agent's wording
- Four independent systems disagreeing about stage state
- Every reliability patch adding more inference code instead of fixing the root cause

## Solution

Stage status is set programmatically at the points where transitions actually happen:
- `start_stage()` in the PreToolUse hook when the PM dispatches a dev-session for an SDLC stage
- `complete_stage()` in the SubagentStop hook when the dev-session returns successfully
- `fail_stage()` when the session fails

State is persisted as a JSON dict on `AgentSession.stage_states` -- one Redis field, no history parsing.

## Production Lifecycle

The state machine is wired into the Claude Agent SDK hook system. This is the end-to-end flow for a single SDLC stage:

1. **PM dispatches dev-session**: The ChatSession (PM persona) uses the Agent tool with `type="dev-session"` and a prompt containing the stage assignment (e.g., "Stage: BUILD").

2. **PreToolUse hook fires** (`agent/hooks/pre_tool_use.py`):
   - `_maybe_register_dev_session()` detects `tool_name == "Agent"` with `type == "dev-session"`
   - `_extract_stage_from_prompt()` parses the stage name from the prompt text using pattern matching against known SDLC stage names
   - `_start_pipeline_stage()` loads the parent ChatSession from Redis, creates a `PipelineStateMachine`, and calls `start_stage()` -- marking the stage as `in_progress`
   - Failures are caught and logged but never block the Agent tool call

3. **Dev-session executes**: The subagent runs the assigned stage work (e.g., `/do-build`, `/do-test`).

4. **SubagentStop hook fires** (`agent/hooks/subagent_stop.py`):
   - `_record_stage_on_parent()` loads the parent session, creates a `PipelineStateMachine`, and calls `current_stage()` to find the `in_progress` stage
   - Calls `complete_stage()` on the found stage, marking it as `completed` and the next stage as `ready`
   - Injects the updated `stage_states` back to the PM via the hook return value

5. **PM sees updated state**: The ChatSession receives the pipeline state injection and can route to the next stage.

### Stage Extraction

The `_extract_stage_from_prompt()` function extracts the SDLC stage from the dev-session prompt using two strategies:

1. **Structured pattern** (preferred): Matches patterns like `Stage: BUILD`, `Stage to execute -- PLAN`, `Stage to execute: TEST`
2. **Keyword fallback**: If the word "stage" appears in the prompt, scans for any known SDLC stage name (ISSUE, PLAN, CRITIQUE, BUILD, TEST, PATCH, REVIEW, DOCS, MERGE)

If no stage can be extracted, the hook logs a debug message and skips `start_stage()` -- the dev-session still runs, but stage tracking is not activated.

### Error Handling

Both the PreToolUse and SubagentStop hooks wrap all state machine operations in try/except blocks. A failure in `start_stage()` or `complete_stage()` is logged as a warning but never prevents the Agent tool from proceeding. This ensures the pipeline state machine is strictly additive -- it enhances observability without introducing new failure modes.

## API

```python
from bridge.pipeline_state import PipelineStateMachine

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
sm.get_display_progress()     # {stage: status} for DISPLAY_STAGES
sm.get_display_progress(slug="my-feature")  # fills pending gaps from artifacts
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

The state machine validates transitions using `PIPELINE_EDGES` from `bridge/pipeline_graph.py`:

- `start_stage("BUILD")` raises `ValueError` if PLAN is not completed
- ISSUE can always be started (it's the first stage)
- PATCH can start when TEST or REVIEW has failed/completed
- TEST can restart after PATCH completes (cycle support)

## Artifact-Based Inference

When `get_display_progress(slug=...)` is called with a slug, the state machine supplements stored state with observable artifact checks. This fills in "pending"/"ready" gaps when hook-based tracking failed silently.

**Inference sources:**
- **ISSUE/PLAN**: `docs/plans/{slug}.md` file exists on disk
- **CRITIQUE**: Plan frontmatter contains `status: Ready`
- **BUILD**: PR exists for branch `session/{slug}` (from `gh pr view`)
- **TEST**: `statusCheckRollup` contains a passing check with "test" or "ci" in the name
- **REVIEW**: `reviewDecision` is APPROVED or CHANGES_REQUESTED
- **DOCS**: PR files array contains entries under `docs/`

All GitHub checks use a single `gh pr view` call with `timeout=5`. Stored state always takes precedence -- artifact inference only fills in gaps where stored status is "pending" or "ready".

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

## Integration Points

- **PreToolUse hook** (`agent/hooks/pre_tool_use.py`): Calls `start_stage()` when the PM dispatches a dev-session, marking the stage as `in_progress`
- **SubagentStop hook** (`agent/hooks/subagent_stop.py`): Calls `complete_stage()` when the dev-session returns, marking the stage as `completed`
- **ChatSession**: Uses state machine for stage queries and outcome classification
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
| `bridge/pipeline_state.py` | PipelineStateMachine class with artifact-based inference |
| `bridge/pipeline_graph.py` | Transition table (PIPELINE_EDGES, DISPLAY_STAGES) |
| `models/agent_session.py` | `stage_states` field on AgentSession |
| `agent/hooks/pre_tool_use.py` | `start_stage()` wiring via `_extract_stage_from_prompt()` and `_start_pipeline_stage()` |
| `agent/hooks/subagent_stop.py` | `complete_stage()` wiring via `_record_stage_on_parent()` |
| `tests/unit/test_pipeline_state_machine.py` | State machine unit tests |
| `tests/unit/test_pre_tool_use_start_stage.py` | Stage extraction and start_stage wiring tests |
