# Context Fidelity Modes

**Module:** `agent/context_modes.py`
**Issue:** [#329](https://github.com/tomcounsell/ai/issues/329)

## Overview

Context fidelity modes control how much session state is forwarded to sub-agents when the SDLC dispatcher invokes a skill. Instead of passing the full conversation transcript (wasteful) or a one-line summary (lossy), each skill declares the compression level it needs.

## Modes

| Mode | Enum Value | Use Case | Content |
|------|-----------|----------|---------|
| **Full** | `ContextFidelity.FULL` | Resume within same skill | Complete session transcript |
| **Compact** | `ContextFidelity.COMPACT` | Stage handoffs (BUILD -> TEST) | Plan path, completed stages, artifacts |
| **Minimal** | `ContextFidelity.MINIMAL` | Individual builder sub-agents | Task description + essential refs |
| **Steering** | `ContextFidelity.STEERING` | Observer coaching messages | Current stage, done stages, recent human messages |

## Skill Registry

Each skill declares its fidelity via `SKILL_FIDELITY` in `agent/context_modes.py`:

```python
from agent.context_modes import SKILL_FIDELITY, ContextFidelity

SKILL_FIDELITY = {
    "do-plan": ContextFidelity.COMPACT,
    "do-build": ContextFidelity.COMPACT,
    "do-test": ContextFidelity.COMPACT,
    "do-patch": ContextFidelity.COMPACT,
    "do-pr-review": ContextFidelity.COMPACT,
    "do-docs": ContextFidelity.COMPACT,
    "builder": ContextFidelity.MINIMAL,
}
```

Unknown skills default to `COMPACT`.

## Usage

```python
from agent.context_modes import ContextRequest, get_context_for_skill

# Build a context request from available session state
request = ContextRequest(
    plan_path="docs/plans/my-feature.md",
    current_stage="TEST",
    completed_stages=["ISSUE", "PLAN", "BUILD"],
    artifacts={"branch": "session/my-feature", "pr_url": "https://..."},
)

# Dispatch builds context sized to the skill's declared fidelity
context = get_context_for_skill("do-test", request)
```

## Design Decisions

- **No token budget enforcement** -- approximate sizes are guidelines, not hard limits. The model handles truncation naturally.
- **No graph DSL** -- we use a linear pipeline, not a graph. The registry is a simple dict.
- **Plain dicts for artifacts** -- when SkillOutcome (#328) ships, artifacts can be replaced with typed objects.
- **Default to compact** -- safe middle ground for any skill not explicitly registered.
