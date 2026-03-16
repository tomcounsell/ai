# Mandatory REVIEW and DOCS Stage Enforcement

Ensures that every SDLC job completing BUILD must also complete REVIEW and DOCS before the Observer delivers output to Telegram.

## Problem

The pipeline graph defined correct edges (TEST -> REVIEW -> DOCS -> MERGE), but the Observer had multiple paths that allowed REVIEW and DOCS to be bypassed — an audit found fewer than 10% of merged PRs reached these stages.

## Solution

### Hard Delivery Gates in Observer (`bridge/observer.py`)

The `_check_mandatory_gates()` method runs REVIEW and DOCS goal gate checks before any deliver-to-Telegram decision. Gate results are cached per `Observer.run()` invocation to avoid redundant API calls.

Three enforcement points:

1. **Typed outcome success path** (line ~580): When a typed outcome reports success and `has_remaining_stages()` returns False, gates are checked before delivering.
2. **Deterministic SDLC guard bypass** (line ~722): When the guard is bypassed (e.g., `needs_human=True`), gates are checked before falling through to the LLM Observer.
3. **LLM Observer deliver decision** (line ~836): After the LLM decides to deliver, gates override to steer if unsatisfied.

### Cycle Safety

If the same gate-forced steering happens 3+ times (tracked via `_gate_steering_count` in session history), the Observer delivers with a warning instead of looping indefinitely.

### Stage Detector Changes (`bridge/stage_detector.py`)

REVIEW and DOCS were removed from `_COMPLETION_PATTERNS`. These stages now only complete via:
- Typed `SkillOutcome` from `/do-pr-review` or `/do-docs`
- Explicit skill invocation detected by `_SKILL_INVOCATION_PATTERN`

This prevents false completions from incidental mentions like "review complete" in unrelated output.

### Graph-Aware `has_remaining_stages()` (`models/agent_session.py`)

Replaced the flat list scan with a graph walk: starting from the last completed stage, walks forward through `get_next_stage()` until it either finds a non-completed stage (returns True) or reaches the terminal MERGE node (returns False).

### Plan Status Update in `/do-docs`

The `/do-docs` skill now updates the plan document's `status:` frontmatter to "Complete" after documentation is created/updated.

## Related

- [Pipeline Graph](pipeline-graph.md) — defines the stage transition edges
- [Observer Agent](observer-agent.md) — routing decisions and deterministic guards
- [Goal Gates](goal-gates.md) — deterministic gate check functions
- [Typed Skill Outcomes](typed-skill-outcomes.md) — how skills report completion

## Tracking

- Issue: [#418](https://github.com/tomcounsell/ai/issues/418)
- PR: [#421](https://github.com/tomcounsell/ai/pull/421)
