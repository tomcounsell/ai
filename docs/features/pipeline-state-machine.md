# Pipeline State Machine

The Pipeline State Machine (`bridge/pipeline_state.py`) provides programmatic stage tracking for the SDLC pipeline. It replaces the inference-based approach (stage detector, skill outcome parser, checkpoint system) with direct state recording at transition points.

## Problem

The previous system inferred pipeline stage status by parsing agent transcripts with regex patterns and cross-checking typed outcome blocks. This led to:
- Completed stages rendering as unchecked in Telegram because regex didn't match the agent's wording
- Four independent systems disagreeing about stage state
- Every reliability patch adding more inference code instead of fixing the root cause

## Solution

Stage status is now set programmatically at the points where transitions actually happen:
- `start_stage()` when the Observer steers to a new stage
- `complete_stage()` when the job returns successfully
- `fail_stage()` when the job fails

State is persisted as a JSON dict on `AgentSession.stage_states` -- one Redis field, no history parsing.

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
sm.classify_outcome(stage, stop_reason, output_tail)  # "success"/"fail"/"ambiguous"
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

## Outcome Classification

`classify_outcome()` uses a two-tier approach:
1. **SDK stop_reason**: non-`end_turn` reasons (rate_limited, timeout, etc.) are process failures
2. **Output tail patterns**: stage-specific patterns in the last 500 chars of output

Falls back to `"ambiguous"` for the Observer LLM to handle.

## Integration Points

- **Observer** (`bridge/observer.py`): Creates a state machine per run, uses it for stage queries and outcome classification
- **Job Queue** (`agent/job_queue.py`): Creates state machine in `send_to_chat()`, applies transitions from Observer decisions
- **Summarizer** (`bridge/summarizer.py`): Reads `get_display_progress()` for Telegram stage rendering

## What Was Deleted

- `bridge/stage_detector.py` -- transcript parsing for stage detection
- `agent/skill_outcome.py` -- typed outcome block parsing
- `agent/checkpoint.py` -- checkpoint persistence for session recovery

## Files

| File | Purpose |
|------|---------|
| `bridge/pipeline_state.py` | PipelineStateMachine class |
| `bridge/pipeline_graph.py` | Transition table (PIPELINE_EDGES, DISPLAY_STAGES) |
| `models/agent_session.py` | `stage_states` field on AgentSession |
| `tests/unit/test_pipeline_state_machine.py` | 49 unit tests |
