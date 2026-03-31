# Goal Gates

Deterministic enforcement gates that prevent the SDLC pipeline from advancing past a required stage. Each gate checks a concrete condition (file exists, PR exists, review posted) without LLM judgment.

## Problem Solved

SDLC stages were silently skipped. The Observer Agent and stage detector improved detection but had no enforcement mechanism. An agent could skip TEST, REVIEW, or DOCS stages by producing a convincing status update that fooled the Observer into delivering early.

## How It Works

### Gate Definitions

Each SDLC stage has a deterministic gate check:

| Stage | Gate Condition | Evidence |
|-------|---------------|----------|
| PLAN | `docs/plans/{slug}.md` exists | File existence |
| BUILD | PR exists for `session/{slug}` branch | `gh pr list` result |
| TEST | `test` stage marked completed in `stage_states` JSON field on AgentSession | `stage_states` field |
| REVIEW | PR review or `## Review:` comment exists | `gh api` review count |
| DOCS | `docs/features/{slug}.md` exists, or plan declares "No documentation changes needed" | File existence or plan content |

### Enforcement Points

Gates are checked at four levels:

1. **`/sdlc` dispatcher** -- Before advancing to the next stage, runs a gate check for the previous stage. If the gate fails, re-invokes the previous skill (max 2 retries before human escalation).

2. **Observer Agent read_session** -- The `_handle_read_session()` response includes a `gate_status` dict for each stage. If the Observer sees a stage marked "completed" but its gate is unsatisfied, it steers back to that stage.

3. **Observer mandatory delivery gates** -- Before any delivery decision (typed outcome success, deterministic guard bypass, or LLM deliver), `_check_mandatory_gates()` checks REVIEW and DOCS gates via `check_review_gate()` and `check_docs_gate()`. If either gate is unsatisfied, delivery is overridden to steer with a coaching message. This hard enforcement runs at three points in the Observer decision flow. Cycle safety allows delivery after 3 forced steerings for the same gate, and gate results are cached per `run()` invocation.

4. **Completion guard in agent_session_queue.py** -- Before delivering the final message to Telegram, `check_all_gates()` runs. If any gate is unsatisfied, a warning listing the missing gates is appended to the delivery message.

### GateResult

All gate checks return a `GateResult` dataclass:

```python
@dataclass
class GateResult:
    satisfied: bool       # Whether the gate condition is met
    evidence: str         # Human-readable description of what was found
    missing: str | None   # What is missing (if unsatisfied)
```

### Error Handling

Gate checks never raise exceptions. All subprocess and IO errors are caught and returned as `GateResult(satisfied=False, evidence="check failed: {error}")`. This ensures a failed gate check (e.g., GitHub rate limiting) does not crash the pipeline -- it reports the failure as an unsatisfied gate.

## Key Files

| File | Role |
|------|------|
| `agent/goal_gates.py` | Pure gate check functions -- no side effects, no codebase imports |
| `agent/agent_session_queue.py` | Nudge loop checks gate status before delivery |
| `agent/agent_session_queue.py` | Completion guard appends warning for unsatisfied gates |
| `.claude/skills/sdlc/SKILL.md` | Step 2.5 gate check instructions before stage dispatch |
| `tests/test_goal_gates.py` | 37 unit tests covering all gates, edge cases, and error paths |

## Design Decisions

- **Deterministic only**: No LLM judgment in gate checks. File existence, API responses, and exit codes only.
- **Fail-open on errors**: A gate check that crashes returns unsatisfied (not an exception), so the pipeline can escalate rather than hang.
- **No graph engine**: Inspired by attractor's `goal_gate=true` pattern but implemented as simple linear checks, not a graph DSL.
- **2-retry cap**: Automatic retries are capped at 2 per gate to prevent infinite loops. After 2 failures, the system escalates to the human.
- **Mandatory delivery gates**: REVIEW and DOCS gates have hard enforcement in the Observer via `_check_mandatory_gates()`. These cannot be bypassed by the LLM Observer or deterministic guard — delivery is blocked until both gates are satisfied (or the 3-steering cycle safety limit is reached).

## Related

- Issue: [#331](https://github.com/tomcounsell/ai/issues/331)
- Plan: `docs/plans/goal_gates.md`
- Chat Dev Session Architecture: `docs/features/chat-dev-session-architecture.md`
- SDLC Enforcement: `docs/features/sdlc-enforcement.md`
