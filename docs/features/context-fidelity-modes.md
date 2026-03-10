# Context Fidelity Modes

Right-sized context compression for SDLC pipeline sub-agent steering.

## Problem

When `/sdlc` dispatches to sub-skills, context was passed as raw text — either the full conversation (50k+ tokens) or a brief one-line summary (too lossy). Sub-agents inherited far more context than they needed.

## Solution

Four context fidelity modes, declared per-skill in SKILL.md frontmatter:

| Mode | Use Case | Budget | Content |
|------|----------|--------|---------|
| `full` | Resume within same skill, initial issue creation | Unbounded | Full enriched message |
| `compact` | Stage handoffs (BUILD→TEST, TEST→REVIEW) | ~800 tokens | Plan summary + artifacts + links + stage progress |
| `minimal` | Individual builder sub-agent tasks | ~200 tokens | Task description + file list |
| `steering` | Observer coaching messages | ~300 tokens | Current stage + completed + next + human messages |

## How It Works

### Skill Annotations

Each `/do-*` skill declares its context need in SKILL.md frontmatter:

```yaml
---
name: do-build
context_fidelity: compact
---
```

### Context Builders

`agent/context_modes.py` provides pure functions:

- `build_full_context(session, enriched_message)` — pass-through
- `build_compact_context(session, plan_path, previous_artifacts)` — structured handoff
- `build_minimal_context(session, task_description, relevant_files)` — task-specific
- `build_steering_context(session)` — observer coaching
- `get_context_mode(skill_path)` — reads `context_fidelity` from SKILL.md

### SDLC Dispatcher Integration

The `/sdlc` skill reads the next sub-skill's `context_fidelity` annotation and calls the appropriate builder before invoking it. Falls back to `compact` if annotation missing.

### Observer Integration

The Observer includes `steering_context` in its `read_session` response, built from `build_steering_context()`. This gives the Observer LLM a structured view of pipeline state for crafting coaching messages.

## Skill Assignments

| Skill | Mode | Rationale |
|-------|------|-----------|
| `/do-issue` | `full` | Needs full user request |
| `/do-plan` | `compact` | Needs issue context, not full conversation |
| `/do-build` | `compact` | Needs plan + issue |
| `/do-test` | `compact` | Needs branch/PR info |
| `/do-patch` | `compact` | Needs test/review feedback |
| `/do-pr-review` | `compact` | Needs PR URL |
| `/do-docs` | `compact` | Needs plan + feature location |

## Graceful Degradation

- Context builders never crash — they wrap file reads in try/except
- Missing fields produce explicit "not available" notes
- If context generation fails, the dispatcher falls back to passing issue URL + plan path directly
- `get_context_mode()` returns `"compact"` for missing/invalid annotations

## Related

- Issue: [#329](https://github.com/tomcounsell/ai/issues/329)
- Plan: `docs/plans/context_fidelity_modes.md`
- Correlation IDs: `docs/features/correlation-ids.md`
