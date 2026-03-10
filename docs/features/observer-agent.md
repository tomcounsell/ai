# Observer Agent

The Observer Agent replaces the fragmented classifier/coach/routing chain with a single Sonnet-powered agent that makes routing decisions with full session context. It runs synchronously inside `send_to_chat()` at the point where `classify_output()`, `classify_routing_decision()`, and `build_coaching_message()` were previously called.

## Architecture

```
Worker stops
    |
    v
Stage Detector (deterministic, no LLM)
    |  parses transcript for /do-* skill invocations
    |  marks stages in_progress or completed
    v
Observer Agent (Sonnet in production, configurable for testing)
    |  reads AgentSession state
    |  reads queued steering messages
    |  makes judgment call
    |
    +-- STEER: enqueue continuation with coaching message
    |          (identity-affirming, with concrete success criteria)
    |
    +-- DELIVER: send output to Telegram for human review
```

**Fallback**: If the Observer API call errors, raw worker output is delivered to Telegram. Output is never silently dropped.

## Stage Detector

The stage detector (`bridge/stage_detector.py`) is a pure function with no side effects. It replaces `tools/session_progress.py`, which required the worker LLM to explicitly call a CLI tool to record stage progress.

### Detection Rules

1. **Skill invocations** (strongest signal): Regex matches `/do-plan`, `/do-build`, `/do-test`, `/do-pr-review`, `/do-docs` in transcript text. When a later stage is invoked, earlier stages are implicitly marked as completed.

2. **Completion markers** (secondary signal): Regex matches for stage-specific evidence (e.g., `github.com/.../issues/123` for ISSUE, `42 passed` for TEST).

### Pipeline Order

```
ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS
```

### Functions

| Function | Purpose |
|----------|---------|
| `detect_stages(transcript)` | Pure function: returns list of `{stage, status, reason}` transitions |
| `apply_transitions(session, transitions)` | Writes transitions to `AgentSession.history` entries, skipping duplicates |

## Observer Decision Framework

The Observer uses Claude API directly (`anthropic.Anthropic().messages.create()`) with tool-use for structured decisions. It has four tools:

| Tool | Purpose |
|------|---------|
| `read_session` | Read current AgentSession state (stages, links, history, queued messages). Must be called first. |
| `update_session` | Persist extracted data (context_summary, expectations, issue/PR URLs) |
| `enqueue_continuation` | Steer: re-enqueue the job with a coaching message |
| `deliver_to_telegram` | Deliver: send output to the human |

### STEER when:
- Pipeline stages remain incomplete
- Worker paused with a status update, not a question
- Worker finished one stage and needs the next
- Missing links (issue URL, PR URL) that should have been created

### DELIVER when:
- All pipeline stages complete
- Worker is asking a genuine question
- Worker hit a blocker requiring human intervention
- Error the worker cannot recover from
- Non-SDLC job (casual conversation, Q&A)
- Final completion with evidence

### Safety Limits
- Maximum 5 tool-use iterations per Observer invocation
- Maximum 10 auto-continues for SDLC jobs, 3 for non-SDLC
- **Hard guard** in `agent/job_queue.py`: cap is enforced regardless of Observer decision — if `auto_continue_count > effective_max`, output is delivered to Telegram
- Each auto-continue increment is logged at INFO level (`Auto-continue {n}/{max} for session {id}`) for full sequence traceability
- If Observer doesn't converge on a decision, defaults to deliver

## Coaching Philosophy

The Observer's coaching messages follow identity-affirming prompting principles (see [Claude Prompting Best Practices](../references/claude_prompting_best_practices.md)):

- **Speak to competence, not compliance** — the worker is a skilled agent, not a script runner
- **Concrete success criteria** — close with what success looks like: "Success here means clean, tested code with no silent assumptions"
- **Narrow opening for questions** — give permission to raise genuine critical questions to the architect/PM, but frame it as a narrow exception, not an invitation to stop
- **Specific over vague** — "verify the tests pass before proceeding" rather than "think carefully"
- **No threats or artificial pressure** — these degrade output quality in Claude models

Example coaching message:
> "Good progress on the plan. Continue with the build — invoke /do-build. Prioritize correctness over speed. If you encounter a critical architecture question that needs human input, state it clearly and directly. Otherwise, press forward. Success here means working code with tests that pass on the first run."

## Session Integration

The Observer reads from and writes to `AgentSession` (Redis-backed via Popoto ORM):

- **`queued_steering_messages`**: Human reply-to messages injected by the bridge. Observer checks these first -- human input always takes priority over automated steering.
- **`context_summary`** and **`expectations`**: Set by Observer on deliver, enabling semantic session routing for future messages.
- **Stage progress**: Read via `get_stage_progress()`, written by `apply_transitions()` as `[stage]` history entries.
- **Links**: Issue URL, PR URL, plan path -- extracted from worker output and persisted via `set_link()`.

## What It Replaced

The Observer replaces three interleaved systems that shared responsibility for routing decisions:

| Old Component | Location | Problem |
|---------------|----------|---------|
| `classify_output()` | `bridge/summarizer.py` | Haiku LLM classifier -- no session context, no stage awareness |
| `classify_routing_decision()` | `agent/job_queue.py` | Rule-based routing with 20+ conditionals, planning language guard, error guard |
| `build_coaching_message()` | `bridge/coach.py` | 5-tier fallback chain -- overly complex, often produced generic coaching |
| `session_progress.py` | `tools/session_progress.py` | CLI tool the worker LLM had to call -- silently failed, stages got skipped |

These were removed from the routing path. See [Coaching Loop](coaching-loop.md) for historical documentation of the old system (now deprecated).

## Key Files

| File | Purpose |
|------|---------|
| `bridge/observer.py` | Observer Agent class, system prompt, tool definitions |
| `bridge/stage_detector.py` | Deterministic stage detection (pure function) |
| `agent/job_queue.py` | `send_to_chat()` wiring -- invokes stage detector then Observer |
| `models/agent_session.py` | `AgentSession` with stage progress, steering queue, links |
| `tests/test_observer.py` | 46 tests: 33 unit (stage detector, Observer tools, fallback) + 13 integration (real API with Haiku floor test) |

## Testing Strategy

Integration tests use **Haiku as a robustness floor**: if the Observer makes correct STEER/DELIVER decisions with Haiku's lower intelligence, production Sonnet handles real-world nuance even better.

The `model` parameter on `Observer.__init__()` defaults to `SONNET` for production and can be overridden for testing. All 13 integration tests pass `model=HAIKU`.

Test categories:
- **Status update steering** (6 parametrized): Observer steers at each SDLC stage
- **Open questions delivery**: Observer delivers when genuine architect/PM questions detected
- **Discernment quality**: coaching messages are substantive, not bare "continue"
- **Cap enforcement**: SDLC cap (10) and non-SDLC cap (3) both cause delivery
- **Error/blocker delivery**: unrecoverable errors go to human
- **Completion delivery**: all-stages-done with evidence goes to human
