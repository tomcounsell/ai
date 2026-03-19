# Observer Agent

The Observer Agent is the **sole controller of SDLC pipeline progression**. It decides whether to steer the worker agent to the next pipeline stage or deliver output to the human on Telegram. It runs synchronously inside `send_to_chat()` in `agent/job_queue.py`.

The worker agent receives only safety rails (`WORKER_RULES` in `agent/sdk_client.py`) -- no pipeline stages, no `/sdlc` invocation instructions. The Observer steers the worker one stage at a time via coaching messages, using the [pipeline graph](pipeline-graph.md) for routing.

## Architecture

```
Worker stops
    |
    v
Stop Reason Routing (deterministic, no LLM)
    |  if stop_reason == "budget_exceeded" -> DELIVER with warning
    |  if stop_reason == "rate_limited" -> STEER with backoff
    |  if stop_reason == "end_turn" or None -> fall through
    v
State Machine Outcome Classification (deterministic, no LLM)
    |  classifies current stage outcome from stop_reason + output tail
    |  determines completed_stage and next_stage
    v
Deterministic SDLC Guard (deterministic, no LLM)
    |  if SDLC + stages remain + no blocker -> STEER to next /do-* skill
    |  if failed stage, terminal stop, at cap, or blocker signal -> fall through
    v
Observer LLM (Sonnet in production)
    |  reads AgentSession state via read_session tool
    |  reads queued steering messages
    |  decides: STEER or DELIVER
    v
send_to_chat() applies state machine transitions
```

## Stage Tracking

The Observer uses [PipelineStateMachine](pipeline-state-machine.md) for all stage tracking. The state machine:
- Records transitions programmatically (not by parsing transcripts)
- Enforces stage ordering via the pipeline graph
- Persists state as a JSON dict on `AgentSession.stage_states`

## Decision Framework

### STEER when:
- Pipeline stages remain incomplete (state machine shows pending/ready/in_progress stages)
- The worker paused with a status update, not a question
- The worker finished one stage and needs to move to the next

### DELIVER when:
- All pipeline stages are complete (MERGE completed)
- The worker is asking the human a genuine question
- The worker hit a blocker requiring human intervention
- An error occurred that the worker cannot recover from
- This is a non-SDLC job (casual conversation, Q&A)

## Coaching Messages

When steering, the Observer crafts a coaching message that:
- Acknowledges what was done
- References the next /do-* skill
- Includes SDLC context variables (PR number, slug, branch)
- Closes with what success looks like for this step

## Tools

The Observer has four tools:
1. `read_session` -- reads current session state (stages, links, history)
2. `update_session` -- persists context_summary and expectations
3. `enqueue_continuation` -- steers the worker with a coaching message
4. `deliver_to_telegram` -- delivers output to the human

## Files

| File | Purpose |
|------|---------|
| `bridge/observer.py` | Observer class with run() method |
| `bridge/pipeline_state.py` | PipelineStateMachine for stage tracking |
| `bridge/pipeline_graph.py` | Transition table and DISPLAY_STAGES |
| `agent/job_queue.py` | send_to_chat() integration point |

## Safety Guards

- **Empty output guard**: delivers immediately to prevent silent loops
- **Narration gate**: auto-continues when output is pure narration without substance
- **Auto-continue cap**: MAX_AUTO_CONTINUES_SDLC (10) prevents infinite loops
- **Human input detection**: regex patterns detect questions and blockers
