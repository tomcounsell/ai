# Observer Agent (Deterministic)

The Observer is a **fully deterministic** routing component that decides whether to steer the worker agent to the next pipeline stage or deliver output to the human on Telegram. It runs inside `send_to_chat()` in `agent/job_queue.py`.

**Important**: As of the SDLC redesign (#459), the Observer no longer makes LLM calls. All routing decisions are deterministic rules based on session state. If the rules cannot decide, output is delivered to the human.

## Architecture

```
Worker stops
    |
    v
Rule 1: Stop Reason Routing
    |  rate_limited -> STEER with backoff
    |  timeout -> DELIVER
    |  unknown -> DELIVER
    |  end_turn / None -> fall through
    v
Rule 2: Non-SDLC Check
    |  not SDLC -> DELIVER immediately
    v
Rule 3: State Machine Outcome Classification
    |  classifies current stage outcome
    |  determines resolved_stage and next_stage
    v
Rule 4: Human Input Detection
    |  output contains questions/fatal/options -> DELIVER
    v
Rule 5: Failed Stage Check
    |  any stage failed -> DELIVER
    v
Rule 6: Remaining Stages Check
    |  stages remain + next skill known -> STEER to next /do-* skill
    v
Rule 7: Pipeline Complete
    |  no remaining stages -> DELIVER
```

## Key Design Decisions

1. **No LLM fallback**: If deterministic logic cannot decide, deliver to human. This eliminates API cost and latency for routing.
2. **No auto-continue caps**: The deterministic Observer handles routing without artificial caps. The old MAX_AUTO_CONTINUES (3 for non-SDLC, 10 for SDLC) have been raised to 50 as safety limits.
3. **No circuit breaker**: The LLM circuit breaker (exponential backoff, escalation) has been removed since deterministic code doesn't have transient API failures.
4. **Absorbed into ChatSession**: In the new architecture, the Observer's role is logically part of the ChatSession's PM persona orchestration.

## Human Input Detection Patterns

The Observer uses regex heuristics to detect when the worker needs human input:
- Open questions ("Should I...", "Do you want...")
- Fatal errors ("FATAL", "unrecoverable", "cannot proceed")
- Options presented ("Option A)", "Option B)")
- Explicit requests ("requires human", "your call/input/decision")

## Key Files

| File | Purpose |
|------|---------|
| `bridge/observer.py` | Deterministic Observer implementation |
| `bridge/pipeline_state.py` | PipelineStateMachine for stage tracking |
| `bridge/pipeline_graph.py` | Stage transition graph |
| `agent/job_queue.py` | `send_to_chat()` invokes the Observer |

## See Also

- [ChatSession/DevSession Architecture](chat-dev-session-architecture.md) -- the broader session redesign
- [Pipeline Graph](pipeline-graph.md) -- stage transitions used by the state machine
