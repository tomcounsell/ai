---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/653
---

# Fix stale summarizer claim in pipeline-state-machine.md

## Problem

The feature doc `docs/features/pipeline-state-machine.md` claims the summarizer reads `get_display_progress()` for Telegram stage rendering (line 124). A comment in `bridge/pipeline_graph.py` line 76 also claims `DISPLAY_STAGES` is used by "summarizer rendering."

**Current behavior:**
A developer reading the docs believes the summarizer directly calls `get_display_progress()` to render pipeline stages. This leads to incorrect assumptions about how to modify stage rendering. A grep of `bridge/summarizer.py` confirms zero references to `get_display_progress`, `PipelineStateMachine`, `pipeline_state`, `pipeline_graph`, `DISPLAY_STAGES`, or `stage_states`.

**Desired outcome:**
The Integration Points section accurately reflects the actual callers of `get_display_progress()`. The stale comment in `pipeline_graph.py` is corrected. All integration points listed are verifiable by grep.

## Prior Art

- **Issue #488**: Consolidate SDLC stage tracking -- the consolidation work that likely introduced the stale claim when refactoring summarizer to use `PipelineStateMachine.get_display_progress()`. The summarizer reference may have been aspirational rather than implemented.
- **Issue #645**: Implicit pipeline tracking -- discovered the stale claim during integration audit.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Doc fix**: Correct the Integration Points section in `pipeline-state-machine.md`
- **Comment fix**: Correct the stale comment in `pipeline_graph.py` line 76
- **Completeness**: Add missing actual callers of `get_display_progress()`

### Technical Approach

1. In `docs/features/pipeline-state-machine.md`, replace the Summarizer bullet in the Integration Points section with the actual callers:
   - `AgentSession.get_stage_progress()` (`models/agent_session.py`) -- convenience wrapper
   - `/do-merge` skill (`.claude/commands/do-merge.md`) -- pre-merge pipeline gate
   - `bridge/coach.py` -- imports `DISPLAY_STAGES` for stage coaching logic

2. In `bridge/pipeline_graph.py` line 76, remove the "and summarizer rendering" claim from the comment. The accurate statement is that `DISPLAY_STAGES` is used by `PipelineStateMachine.get_display_progress()` and `bridge/coach.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a documentation and comment fix only.

### Empty/Invalid Input Handling
- Not applicable -- no code logic changes.

### Error State Rendering
- Not applicable -- no user-visible output changes.

## Test Impact

No existing tests affected -- this is purely a documentation and code comment fix. No runtime behavior, function signatures, or interfaces change.

## Rabbit Holes

- Wiring the summarizer to actually call `get_display_progress()` -- that would be a feature change, not a docs fix, and is explicitly out of scope per issue #653.
- Auditing the entire doc for other stale claims -- limit to what issue #653 identifies.

## Risks

No risks. This is a docs/comment-only change with zero runtime impact.

## Race Conditions

No race conditions identified -- no runtime code changes.

## No-Gos (Out of Scope)

- Wiring the summarizer to use `get_display_progress()` (separate feature if desired)
- Broader doc audit beyond the Integration Points section and pipeline_graph.py comment
- Any runtime code changes

## Update System

No update system changes required -- this is a documentation and comment fix only.

## Agent Integration

No agent integration required -- no runtime behavior changes.

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` Integration Points section (this IS the deliverable)
- [ ] Fix inline comment in `bridge/pipeline_graph.py` line 76

## Success Criteria

- [ ] The Integration Points section in `pipeline-state-machine.md` contains no claims about the summarizer calling `get_display_progress()`
- [ ] The comment in `pipeline_graph.py` line 76 is corrected (no summarizer reference)
- [ ] All listed integration points are verifiable by grep
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (docs-fix)**
  - Name: docs-fixer
  - Role: Fix stale claims in docs and comments
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Fix stale Integration Points and comment
- **Task ID**: build-docs-fix
- **Depends On**: none
- **Validates**: grep confirms no "summarizer" in Integration Points section; grep confirms pipeline_graph.py line 76 has no "summarizer"
- **Assigned To**: docs-fixer
- **Agent Type**: builder
- **Parallel**: false
- Replace the Summarizer bullet in `docs/features/pipeline-state-machine.md` Integration Points with actual callers: AgentSession wrapper and /do-merge skill
- Fix comment in `bridge/pipeline_graph.py` line 76 to remove "and summarizer rendering"

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-docs-fix
- **Assigned To**: docs-fixer
- **Agent Type**: validator
- **Parallel**: false
- Grep `docs/features/pipeline-state-machine.md` for "summarizer" -- should not appear in Integration Points
- Grep `bridge/pipeline_graph.py` line 76 for "summarizer" -- should not match
- Verify each listed integration point has a matching grep hit in the referenced file

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No summarizer claim in integration points | `grep -c "Summarizer.*get_display_progress" docs/features/pipeline-state-machine.md` | exit code 1 |
| No summarizer claim in pipeline_graph comment | `grep -c "summarizer" bridge/pipeline_graph.py` | exit code 1 |
| Lint clean | `python -m ruff check bridge/pipeline_graph.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the fix is fully scoped by the issue.
