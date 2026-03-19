# Deterministic SDLC Guard (Phase 1.75)

> **Deprecated**: The deterministic SDLC guard was subsumed by the `PipelineStateMachine` in `bridge/pipeline_state.py` (PR #433, issue #430). Stage progression is now handled by state machine transitions at job enqueue/completion. The Observer was simplified to only perform steer/deliver classification.

The deterministic SDLC guard was a routing layer in the Observer Agent that force-steered SDLC sessions to the next pipeline stage without consulting the LLM. It sat between the stop-reason routing (Phase 1.5) and the LLM Observer (Phase 2), ensuring pipeline progression was driven by session state rather than LLM judgment.

## Problem

SDLC pipelines were stalling before reaching later stages (especially `/do-docs` and `/do-pr-review`). The root cause: the LLM Observer would sometimes deliver a status update to the human instead of steering to the next stage. This happened when the worker output looked "complete enough" to the LLM even though pipeline stages remained.

The LLM was making subjective routing decisions that should have been deterministic — if stages remain and nothing is broken, the pipeline should always advance.

## Solution

Phase 1.75 inserts a deterministic check after stage transitions and before the LLM. When the session is SDLC, has remaining stages, and no blocker conditions exist, the guard bypasses the LLM entirely and steers to the next stage.

### Decision Flow

```
Phase 1:   Typed Outcome Parser + Stage Detector (deterministic)
Phase 1.5: Stop Reason Routing (budget_exceeded → deliver, rate_limited → steer)
Phase 1.75: Deterministic SDLC Guard ← NEW
Phase 2:   LLM Observer (only reached when guard defers)
```

### Guard Eligibility

The guard fires when ALL six conditions are true:

| Condition | Check | Why |
|-----------|-------|-----|
| `is_sdlc` | `session.is_sdlc_job()` | Only SDLC sessions have pipeline stages |
| `has_remaining` | `session.has_remaining_stages()` | Nothing to steer to if all stages are done |
| `not has_failed` | `not session.has_failed_stage()` | Failed stages need human triage |
| `not stop_is_terminal` | `stop_reason not in ("fail", "budget_exceeded")` | Terminal conditions must reach human |
| `not at_cap` | `auto_continue_count < max_continues` | Respects the auto-continue safety limit (10 for SDLC) |
| `not has_blocker` | `not _output_has_blocker_signal(output)` | Open questions and errors need LLM routing |

If any condition fails, the guard steps aside and the LLM Observer makes the call.

### Blocker Signal Detection

`_output_has_blocker_signal()` scans worker output for patterns that indicate human attention is needed:

- `## Open Questions` section headers
- Explicit asks: "question for Tom", "decision for the PM"
- Decision requests: "should I", "your call", "your input"
- Fatal errors: "FATAL", "unrecoverable", "cannot proceed"
- API key failures: "API key has been revoked/disabled/expired"
- Helplessness signals: "nothing I can do", "requires human"
- Multiple-choice options: "Option A)", "Option B)"

When any pattern matches, the guard defers to the LLM for nuanced routing.

### Stage-to-Skill Mapping

`_next_sdlc_skill()` maps the first pending or in-progress stage to its `/do-*` command:

| Stage | Skill |
|-------|-------|
| ISSUE | `/do-issue` |
| PLAN | `/do-plan` |
| BUILD | `/do-build` |
| TEST | `/do-test` |
| REVIEW | `/do-pr-review` |
| DOCS | `/do-docs` |

## Coaching Message

When the guard fires, it produces a templated coaching message:

> Pipeline has remaining stages. Next: {STAGE}. Continue with {SKILL}. If you encounter a critical blocker requiring human input, state it clearly. Otherwise, press forward.

This is intentionally directive — the guard has already determined the pipeline should advance. The narrow opening for blockers gives the worker an escape hatch without inviting unnecessary pauses.

## Observability

The guard produces structured logs and telemetry:

- **Guard fires**: `Deterministic SDLC guard: forcing steer to {STAGE} ({SKILL}) — remaining stages exist, no failures, stop_reason={reason}`
- **Guard bypassed**: `Deterministic SDLC guard bypassed: has_failed={bool}, stop_reason={reason}, at_cap={bool}, has_blocker={bool} — falling through to LLM Observer`
- **Telemetry**: `record_decision()` with reason `deterministic-sdlc-guard: {STAGE} pending`
- **Return flag**: `deterministic_guard: True` in the decision dict, distinguishing guard decisions from LLM decisions

## Key Files

| File | Purpose |
|------|---------|
| `bridge/observer.py` | Guard logic, `_next_sdlc_skill()`, `_output_has_blocker_signal()`, `_BLOCKER_PATTERNS` |
| `tests/test_observer.py` | `TestObserverSdlcSteering` class with 6 guard-specific tests |

## Testing

Four dedicated tests validate the guard's safety conditions:

| Test | Validates |
|------|-----------|
| `test_guard_fires_on_normal_sdlc_with_remaining` | Guard steers on clean SDLC sessions (positive case) |
| `test_guard_bypassed_when_stage_has_failed` | Failed stages fall through to LLM |
| `test_guard_bypassed_when_stop_reason_is_fail` | Terminal stop_reason falls through to LLM |
| `test_guard_bypassed_when_stop_reason_is_budget_exceeded` | Budget exceeded falls through (defense-in-depth with Phase 1.5) |

## See Also

- [Observer Agent](observer-agent.md) — parent system architecture
- [Typed Skill Outcomes](typed-skill-outcomes.md) — Phase 1 outcome parsing
- [Goal Gates](goal-gates.md) — stage enforcement gates
- [SDLC Enforcement](sdlc-enforcement.md) — pipeline stage model
