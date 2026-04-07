---
status: In Progress
type: enhancement
appetite: Medium
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/329
---

# Context Fidelity Modes for Sub-Agent Steering

## Problem

When `/do-build` spawns builder sub-agents or `/sdlc` dispatches to sub-skills, context is passed as raw text — either the full conversation (wasteful, 50k+ tokens) or a brief summary (lossy). There is no principled middle ground. This causes:

1. **Context bloat** — builders receive entire conversation history when they only need the plan + task
2. **Lost context** — session resumes have no structured summary of prior work
3. **Wasted tokens** — sub-agents inherit irrelevant prior conversation

## Appetite

**Size:** Medium — 4 context builder functions, skill annotations, wiring into dispatch

**Team:** Solo dev

**Interactions:**
- Review rounds: 1

## Prerequisites

None — builds on existing `bridge/context.py` and `agent/sdk_client.py`. Does NOT depend on #328 (SkillOutcome) — uses plain dicts for artifacts until that ships.

## Solution

### Phase 1: Define Context Modes and Builders

Create `agent/context_modes.py` with an enum and four builder functions:

| Mode | Function | Use Case | Approximate Size |
|------|----------|----------|-----------------|
| `full` | `build_full_context()` | Resume within same skill | Full session transcript |
| `compact` | `build_compact_context()` | Stage handoffs (BUILD->TEST) | ~800 tokens |
| `minimal` | `build_minimal_context()` | Individual builder tasks | ~200 tokens |
| `steering` | `build_steering_context()` | Observer coaching | ~300 tokens |

Each function takes a `ContextRequest` dataclass containing:
- `plan_path` (optional str) — path to plan document
- `task_description` (optional str) — specific task for minimal mode
- `current_stage` (optional str) — SDLC stage name
- `completed_stages` (optional list[str]) — previously completed stages
- `artifacts` (optional dict) — key outputs from prior stages (branch name, PR URL, etc.)
- `recent_messages` (optional list[str]) — last N human messages for steering
- `session_transcript` (optional str) — full transcript for full mode

### Phase 2: Annotate Skills with Fidelity Requirements

Add a `context_fidelity` field to a skill metadata registry in `agent/context_modes.py`:

```python
SKILL_FIDELITY: dict[str, ContextFidelity] = {
    "do-plan": ContextFidelity.COMPACT,
    "do-build": ContextFidelity.COMPACT,
    "do-test": ContextFidelity.COMPACT,
    "do-patch": ContextFidelity.COMPACT,
    "do-pr-review": ContextFidelity.COMPACT,
    "do-docs": ContextFidelity.COMPACT,
    "builder": ContextFidelity.MINIMAL,
}
```

### Phase 3: Wire into `get_context_for_skill()`

Create a top-level dispatch function:

```python
def get_context_for_skill(
    skill_name: str,
    request: ContextRequest,
) -> str:
    """Build context string for a skill based on its declared fidelity."""
```

This reads the fidelity from `SKILL_FIDELITY` (defaulting to `compact`) and calls the appropriate builder.

### Rabbit Holes

- **Token counting** — do NOT enforce token budgets. Let the model handle truncation. The approximate sizes are guidelines, not hard limits.
- **Thread reuse** — we already have session IDs; no changes to session management.
- **SkillOutcome integration** — use plain dicts for artifacts. When #328 ships, replace dict with SkillOutcome.
- **Automatic compression** — do NOT auto-compress during live sessions. These modes are for dispatch-time context building only.

## No-Gos

- No token budget enforcement (let the model handle truncation naturally)
- No changes to session management or session IDs
- No graph DSL or DOT syntax
- No summary:low/medium/high granularity (overkill for linear pipeline)
- No automatic mid-session context compression

## Update System

No update system changes required — this feature is purely internal to the agent module and does not affect deployment, dependencies, or the update script.

## Agent Integration

No agent integration required — context modes are consumed internally by the SDLC dispatcher and sub-agent spawning code. No new MCP servers or tool changes needed.

## Documentation

- [ ] Create `docs/features/context-fidelity-modes.md` describing the modes and how skills declare fidelity
- [ ] Add entry to `docs/features/README.md` index table

## Tasks

- [ ] Create `agent/context_modes.py` with `ContextFidelity` enum, `ContextRequest` dataclass, and four builder functions
- [ ] Create `tests/test_context_modes.py` with unit tests for each builder
- [ ] Add `get_context_for_skill()` dispatch function
- [ ] Add `SKILL_FIDELITY` registry mapping skill names to modes
- [ ] Verify all tests pass, linting clean
