# Pipeline State Machine

The Pipeline State Machine (`agent/pipeline_state.py`) provides programmatic stage tracking for the SDLC pipeline. It replaces the inference-based approach (stage detector, skill outcome parser, checkpoint system) with direct state recording at transition points.

## Problem

The previous system inferred pipeline stage status by parsing agent transcripts with regex patterns and cross-checking typed outcome blocks. This led to:
- Completed stages rendering as unchecked in Telegram because regex didn't match the agent's wording
- Four independent systems disagreeing about stage state
- Every reliability patch adding more inference code instead of fixing the root cause

## Solution

Stage status is set programmatically at the points where transitions actually happen. Stage records are written **in-session**, by the Claude Code Skill hooks running inside the eng session as it dispatches each `/do-*` stage skill:

- `start_stage()` in the PreToolUse hook (`agent/hooks/pre_tool_use.py::_start_pipeline_stage`) when the eng session invokes a Skill that maps to a stage via `_handle_skill_tool_start()` and the `_SKILL_TO_STAGE` dict — marks the stage `in_progress`
- `complete_stage()` in the PostToolUse hook (`agent/hooks/post_tool_use.py::_complete_pipeline_stage`) when that mapped Skill returns — reads the current `in_progress` stage via `current_stage()` and marks it `completed`

A single eng session (`SessionType.ENG`, engineer persona) both orchestrates and executes the pipeline: it dispatches one stage skill at a time, and the hooks above advance the state machine at each skill's invoke/return boundary. There is no separate worker post-completion handler that classifies an outcome and routes to `complete_stage()`/`fail_stage()`. The previous PM/Dev split and its `_handle_dev_session_completion()` handler were removed when the roles merged into the single eng session (PR #1691).

State is persisted as a JSON dict on `AgentSession.stage_states` -- one Redis field, no history parsing.

## Production Lifecycle

The state machine is driven entirely by the in-session SDK hook system. This is the end-to-end flow for a single SDLC stage:

1. **Eng session runs a stage skill**: The eng session dispatches one `/do-*` stage skill at a time (e.g., `/do-build`, `/do-test`) via the Skill tool. The same session orchestrates the pipeline and executes the stage work; there is no separate child session per stage.

2. **PreToolUse hook fires on skill invoke**: `agent/hooks/pre_tool_use.py::_handle_skill_tool_start()` looks up the skill name in `_SKILL_TO_STAGE`, and `_start_pipeline_stage()` calls `start_stage(stage)` on the session's `PipelineStateMachine`, marking the stage `in_progress`.

3. **Stage skill executes its work**: The skill produces its artifacts (a PR, test run, review, etc.).

4. **PostToolUse hook fires on skill return**: `agent/hooks/post_tool_use.py::_complete_pipeline_stage()` reads the current `in_progress` stage via `current_stage()` and calls `complete_stage(stage)`, marking it `completed` and the next stage `ready`.

5. **Eng session reads stage_states and routes**: The eng session consults the updated `stage_states` (via the SDLC router) to decide which stage skill to dispatch next, looping until the pipeline reaches MERGE / final delivery.

Child eng sessions are spawned only for multi-issue fan-out (`valor-session create --role eng --parent "$AGENT_SESSION_ID"`, then `valor-session wait-for-children`), not per stage.

### Error Handling

Both the PreToolUse and PostToolUse hooks wrap state machine operations in try/except blocks. A failure in `start_stage()` or `complete_stage()` is logged as a warning but never crashes the session. The pipeline state machine is strictly additive — it enhances observability without introducing new failure modes.

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

`classify_outcome()` is retained on `PipelineStateMachine` but is **not currently wired into the live transition path**. The in-session hooks call `complete_stage()` directly on skill return rather than classifying an outcome and routing through `fail_stage()`. The method (and its three-tier approach below) remains available for callers that need to interpret a stage result, but no production code path invokes it after the PM/Dev merge (PR #1691) removed the `_handle_dev_session_completion()` handler that previously called it.

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

The SDLC router skill (`.claude/skills/sdlc/SKILL.md`) reads `stage_states` as the **primary signal** for routing decisions. This completes the read/write cycle: the in-session hooks write stage transitions (via `start_stage()` on skill invoke and `complete_stage()` on skill return), and the router reads the resulting state to determine which sub-skill to dispatch next.

### How the Router Reads stage_states

The router invokes `tools/sdlc_stage_query.py` via bash to read stored stage state:

```bash
# Bridge sessions (env var available):
python -m tools.sdlc_stage_query

# Local Claude Code sessions (pass issue number explicitly):
python -m tools.sdlc_stage_query --issue-number 941
```

The CLI tool resolves the eng session from `--session-id`, `VALOR_SESSION_ID`, `AGENT_SESSION_ID`, or `--issue-number` (in that priority order), reads `stage_states`, and returns a JSON dict mapping stage names to statuses.

### Routing Logic

- **stage_states available**: Used as the primary signal. A stage is considered complete only if it shows `"completed"` in stage_states.
- **stage_states unavailable** (empty JSON `{}`): Falls back to conversation dispatch history to determine what has already run. Artifact inference is not used. This previously happened for all local Claude Code invocations; with `--issue-number` support and `sdlc_session_ensure`, local sessions now have trackable state in Redis.

### Merge Gate

Row 10 in the dispatch table (merge-ready) requires ALL display stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS) to show `"completed"` in stage_states. This prevents stages from being silently skipped when a prior stage's work happens to produce artifacts that satisfy a later stage's check (e.g., `/do-build` creating docs does not satisfy the DOCS stage).

When stage_states is unavailable (cold start), the merge gate emits an explicit warning listing every unrecorded stage and requires acknowledgment before proceeding. Artifact inference is not used.

### Merge-Before-Complete Enforcement

The merge-before-sign-off gate is enforced in two places:

1. **Engineer persona, Rule 5 ("MERGE is Mandatory Before Sign-Off")** in `config/personas/engineer.md` prevents the eng session from declaring the issue done while an open PR exists.
2. **Final-delivery `pr_open` check**: `agent.pipeline_complete.is_pipeline_complete(states, outcome, pr_open)` only reports the pipeline complete when no PR is left open, so the final-delivery path cannot fire while an unmerged PR exists.

There is no per-completion steering message carrying a merge reminder — that mechanism lived in the removed `_handle_dev_session_completion` handler. The single eng session enforces the gate directly via its persona and the final-delivery check. See `config/personas/engineer.md` Rule 5 and issue #1005.

### Final Delivery (issue #1058)

Final delivery is driven by `_agent_session_hierarchy_health_check` (`agent/session_health.py`) detecting pipeline completion via `agent.pipeline_complete.is_pipeline_complete(states, outcome, pr_open)` and invoking `agent.session_completion.schedule_pipeline_completion` → `_deliver_pipeline_completion`, not by a persona-emitted marker. The completion runner acquires a Redis CAS lock (`pipeline_complete_pending:{parent_id}`, 60s TTL) to deduplicate concurrent invocations, then runs a dedicated harness turn to compose the final summary and delivers it via `send_cb`. See `docs/features/pm-final-delivery.md` for the full protocol.

## Integration Points

- **SDLC Router** (`.claude/skills/sdlc/SKILL.md`): Reads `stage_states` via `tools/sdlc_stage_query.py` CLI tool as primary routing signal
- **Stage Query Tool** (`tools/sdlc_stage_query.py`): CLI interface for reading `stage_states` from an eng session by session ID or issue number
- **PreToolUse hook** (`agent/hooks/pre_tool_use.py`): Calls `start_stage()` on skill invoke (`_handle_skill_tool_start()` with `_SKILL_TO_STAGE` mapping), marking the stage as `in_progress`
- **PostToolUse hook** (`agent/hooks/post_tool_use.py`): Calls `complete_stage()` when a mapped SDLC Skill tool finishes, reading the current `in_progress` stage via `current_stage()`
- **Hierarchy health check** (`agent/session_health.py:_agent_session_hierarchy_health_check()`): Detects pipeline completion via `is_pipeline_complete()` and drives `schedule_pipeline_completion()` for final delivery
- **Eng session**: Uses the state machine for stage queries and routing across the pipeline it both orchestrates and executes
- **AgentSession** (`models/agent_session.py`): `get_stage_progress()` convenience wrapper around `get_display_progress()`
- **Merge Gate** (`.claude/commands/do-merge.md`): Reads `get_display_progress()` for pre-merge pipeline validation

## What Was Deleted

- `bridge/stage_detector.py` -- transcript parsing for stage detection
- `agent/skill_outcome.py` -- typed outcome block parsing
- `agent/checkpoint.py` -- checkpoint persistence for session recovery

## Files

| File | Purpose |
|------|---------|
| `agent/pipeline_state.py` | PipelineStateMachine class — stored-state-only stage tracking (canonical; `agent/pipeline_state.py` is a shim) |
| `tools/sdlc_stage_marker.py` | CLI tool for skills to write in_progress/completed markers (supports `--issue-number` for local sessions) |
| `agent/pipeline_graph.py` | Transition table (PIPELINE_EDGES, DISPLAY_STAGES) (canonical; `agent/pipeline_graph.py` is a shim) |
| `models/agent_session.py` | `stage_states` field on AgentSession |
| `tools/sdlc_stage_query.py` | CLI tool for reading stage_states (used by SDLC router, supports `--issue-number`) |
| `tools/sdlc_session_ensure.py` | CLI tool to create/find local SDLC sessions keyed by issue number |
| `tools/_sdlc_utils.py` | Shared `find_session_by_issue()` helper (deduplicated from sdlc_stage_query) |
| `.claude/skills/sdlc/SKILL.md` | SDLC router skill (reads stage_states in Step 2.0) |
| `agent/hooks/pre_tool_use.py` | `start_stage()` wiring on skill invoke via `_handle_skill_tool_start()` + `_SKILL_TO_STAGE` |
| `agent/hooks/post_tool_use.py` | `complete_stage()` wiring on skill return via `_complete_pipeline_stage()` |
| `agent/session_health.py` | `_agent_session_hierarchy_health_check()` — drives `schedule_pipeline_completion()` for final delivery |
| `tests/unit/test_pipeline_state_machine.py` | State machine unit tests |
| `tests/unit/test_sdlc_stage_query.py` | Stage query CLI tool unit tests |
| `tests/unit/test_pre_tool_use_start_stage.py` | Stage extraction and start_stage wiring tests |
