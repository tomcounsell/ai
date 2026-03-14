---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-03-14
tracking: https://github.com/tomcounsell/ai/issues/399
last_comment_id:
---

# Upgrade SDLC Pipeline to Directed Graph with Cycles

## Problem

Three concerns are conflated in the current SDLC pipeline:

1. **do-X skills reference pipeline flow** -- skills like do-pr-review, do-patch, do-test contain language about what stage comes next ("proceed to docs/merge", "advance to review"). Only the SDLC skill and Observer should own pipeline routing.

2. **Pipeline routing is linear** -- `_next_sdlc_skill()` in `observer.py` walks `STAGE_ORDER` linearly. Real SDLC work has cycles: TEST fails -> PATCH -> TEST again, REVIEW has nits -> PATCH -> TEST -> REVIEW again. These cycles happen today but are handled ad-hoc rather than being modeled in the graph.

3. **Pipeline definition is duplicated** -- the SDLC skill (`SKILL.md`) lists 9 stages including PATCH, the Observer lists 6 stages without PATCH, and `stage_detector.py` has its own `STAGE_ORDER`. These don't agree.

**Current behavior:**

| Location | Pipeline Definition |
|----------|-------------------|
| `SKILL.md` dispatch table | ISSUE -> PLAN -> BUILD -> TEST -> PATCH -> REVIEW -> PATCH -> DOCS -> MERGE |
| `observer.py` `_STAGE_TO_SKILL` | ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS |
| `stage_detector.py` `STAGE_ORDER` | ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS |

**Desired outcome:**
Single canonical graph definition that all routing code derives from, with proper cycle support for test-failure and review-feedback loops.

## Prior Art

- **PR #356**: "Observer-steered worker: remove SDLC_WORKFLOW + rewrite /sdlc as single-stage router" -- established the current Observer + single-stage router architecture
- **PR #378**: "Fix Observer SDLC pipeline: cross-repo gh, classification race, typed outcome merge" -- fixed routing bugs in the Observer
- **PR #321**: "Observer Agent: replace auto-continue/summarizer with stage-aware SDLC steerer" -- original Observer implementation

These PRs built the current linear pipeline. None attempted cycle support.

## Data Flow

1. **Entry point**: Worker agent finishes a stage, output goes to Observer
2. **Observer `run()`**: Parses typed outcome, runs stage detector, applies transitions
3. **`_next_sdlc_skill()`**: Walks `STAGE_ORDER` linearly to find next incomplete stage
4. **Coaching message**: Observer tells worker which `/do-*` skill to invoke next
5. **Worker**: Receives coaching, invokes the skill, produces output -> back to step 1

The problem is in step 3: `_next_sdlc_skill()` only moves forward. When TEST fails, it can't route back to PATCH because PATCH isn't in `STAGE_ORDER`. The cycles happen because the Observer LLM makes ad-hoc decisions, not because the graph models them.

## Architectural Impact

- **New dependencies**: None -- pure Python data structure
- **Interface changes**: `_next_sdlc_skill()` signature changes to accept current stage + outcome
- **Coupling**: Decreases -- single source of truth replaces 3 duplicated definitions
- **Data ownership**: Pipeline graph owns routing logic, `STAGE_ORDER` stays for display
- **Reversibility**: Easy -- the graph is a new module that replaces function internals

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 0 (well-defined scope from issue)
- Review rounds: 1 (routing logic change needs careful review)

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Pipeline graph module** (`bridge/pipeline_graph.py`): Canonical directed graph with edges and cycle support
- **Graph-aware routing**: `_next_sdlc_skill()` replaced with graph traversal that considers current stage + outcome
- **Clean do-X skills**: All pipeline navigation language removed from individual skills

### Flow

**Worker completes stage** -> Observer detects outcome -> Graph determines next stage (forward OR cycle) -> Observer steers worker

### Technical Approach

- Define pipeline as a dict of edges: `{(stage, outcome): next_stage}`
- Happy path: ISSUE->PLAN->BUILD->TEST->REVIEW->DOCS (outcome="success")
- Test failure: TEST->(outcome="fail")->PATCH->TEST (cycle)
- Review feedback: REVIEW->(outcome="fail")->PATCH->TEST->REVIEW (cycle)
- `STAGE_ORDER` remains as the display-only linear list for progress templates
- Both Observer's `_STAGE_TO_SKILL` and SDLC skill's dispatch table derive from the graph
- Graph edges encode the "why" (outcome) not just the "what" (next stage)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_next_sdlc_skill()` replacement must handle unknown stages gracefully (return None like current)
- [ ] Graph lookup with invalid (stage, outcome) pair must not crash -- return happy-path default

### Empty/Invalid Input Handling
- [ ] `get_next_stage(None, "success")` returns first stage (ISSUE)
- [ ] `get_next_stage("TEST", None)` defaults to "success" outcome
- [ ] `get_next_stage("UNKNOWN", "success")` returns None

### Error State Rendering
- [ ] No user-visible output changes -- this is internal routing logic

## Rabbit Holes

- **Full state machine library**: Don't import `transitions` or similar. A dict of edges is sufficient.
- **PATCH as a display stage**: The issue explicitly says don't add PATCH to progress templates. PATCH is a routing concept, not a stage the PM sees.
- **Changing the progress message format**: Out of scope per the issue's No-Gos.
- **Refactoring stage_detector.py beyond graph integration**: Keep detection logic as-is; only change where `STAGE_ORDER` is imported and how routing uses the graph.

## Risks

### Risk 1: Observer deterministic guard bypass
**Impact:** If the graph routing disagrees with the deterministic SDLC guard in `observer.py`, pipelines could stall or loop infinitely.
**Mitigation:** The deterministic guard uses the same graph. Add a max-cycle counter (e.g., max 3 PATCH->TEST cycles before delivering to human).

### Risk 2: Backward compatibility with in-flight sessions
**Impact:** Sessions currently in progress have stage history that doesn't include PATCH. New routing logic must handle sessions that started under the old model.
**Mitigation:** Graph treats missing PATCH history as "not in a cycle" and routes via happy path. No migration needed.

## Race Conditions

No race conditions identified -- pipeline routing is synchronous and single-threaded within the Observer's `run()` method.

## No-Gos (Out of Scope)

- Do NOT change the progress message template -- `STAGE_ORDER` is the PM-facing linear display
- Do NOT add PATCH as a display stage in the message template
- Do NOT make do-X skills aware of the graph -- they remain isolated units of work
- Do NOT change how the Observer LLM makes decisions -- only change the deterministic guard and `_next_sdlc_skill()`

## Update System

No update system changes required -- this is purely internal bridge routing logic. No new dependencies, configs, or migration steps needed.

## Agent Integration

No agent integration required -- this changes internal Observer routing, not agent-facing tools or MCP servers. The bridge code changes are in `bridge/` and `.claude/skills/` markdown files.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/README.md` index table if a pipeline-graph entry is needed
- [ ] Add inline documentation in `bridge/pipeline_graph.py` explaining the graph structure

### Inline Documentation
- [ ] Code comments explaining edge definitions and cycle semantics
- [ ] Updated docstrings for `get_next_stage()` and any modified Observer methods

## Success Criteria

- [ ] Single canonical pipeline graph in `bridge/pipeline_graph.py`
- [ ] `observer.py` `_next_sdlc_skill()` uses graph for routing instead of linear `STAGE_ORDER` walk
- [ ] `observer.py` `_STAGE_TO_SKILL` derived from or consistent with graph
- [ ] `STAGE_ORDER` in `stage_detector.py` remains unchanged (display only)
- [ ] Pipeline navigation language removed from `do-pr-review/SKILL.md`, `do-patch/SKILL.md`, `do-test/SKILL.md`
- [ ] SDLC `SKILL.md` dispatch table references graph, not hardcoded list
- [ ] Test: TEST failure routes to PATCH->TEST cycle
- [ ] Test: REVIEW feedback routes to PATCH->TEST->REVIEW cycle
- [ ] Test: Max cycle limit prevents infinite loops
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline-graph)**
  - Name: graph-builder
  - Role: Create `bridge/pipeline_graph.py` and integrate into Observer
  - Agent Type: builder
  - Resume: true

- **Builder (skill-cleanup)**
  - Name: skill-cleaner
  - Role: Remove pipeline navigation language from do-X skill markdown files
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify graph routing produces correct next-stage for all scenarios
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Pipeline Graph Module
- **Task ID**: build-graph
- **Depends On**: none
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/pipeline_graph.py` with directed graph edges
- Define `PIPELINE_EDGES: dict[tuple[str, str], str]` mapping (stage, outcome) to next_stage
- Export `get_next_stage(current_stage: str, outcome: str) -> tuple[str, str] | None` returning (stage, skill)
- Export `STAGE_TO_SKILL` mapping (replaces Observer's `_STAGE_TO_SKILL`)
- Export `DISPLAY_STAGES` (alias for the linear display order)
- Add max-cycle counter logic (default 3)

### 2. Clean do-X Skills
- **Task ID**: build-skill-cleanup
- **Depends On**: none
- **Assigned To**: skill-cleaner
- **Agent Type**: builder
- **Parallel**: true
- Remove pipeline navigation language from `do-pr-review/SKILL.md`
- Remove pipeline navigation language from `do-patch/SKILL.md` (lines 159-168: "advance to review/document")
- Remove pipeline navigation language from `do-test/SKILL.md` (line 338: "must fix before merge")
- Keep internal flow language (e.g., "proceed to Step 4" within a skill is fine)

### 3. Integrate Graph into Observer
- **Task ID**: build-observer-integration
- **Depends On**: build-graph
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `_STAGE_TO_SKILL` with import from `pipeline_graph.py`
- Replace `_next_sdlc_skill()` body with `get_next_stage()` call
- Keep `STAGE_ORDER` import from `stage_detector.py` for display purposes only
- Update Observer system prompt pipeline stages comment to reference graph

### 4. Update SDLC Skill
- **Task ID**: build-sdlc-skill
- **Depends On**: build-graph
- **Assigned To**: graph-builder
- **Agent Type**: builder
- **Parallel**: false
- Update dispatch table in `SKILL.md` to note it's derived from the graph
- Keep the table for human readability but add a note about canonical source

### 5. Validate Routing
- **Task ID**: validate-routing
- **Depends On**: build-observer-integration, build-skill-cleanup
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify happy path: ISSUE->PLAN->BUILD->TEST->REVIEW->DOCS
- Verify test failure cycle: TEST(fail)->PATCH->TEST
- Verify review feedback cycle: REVIEW(fail)->PATCH->TEST->REVIEW
- Verify max cycle limit triggers delivery to human
- Verify `STAGE_ORDER` unchanged in `stage_detector.py`
- Verify no pipeline navigation language remains in do-X skills

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: graph-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Ensure `pipeline_graph.py` has clear docstrings and comments
- Update `docs/features/README.md` index if needed

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Graph module exists | `test -f bridge/pipeline_graph.py` | exit code 0 |
| No pipeline nav in do-patch | `grep -c "advance to" .claude/skills/do-patch/SKILL.md` | exit code 1 |
| No pipeline nav in do-test | `grep -c "must fix before merge" .claude/skills/do-test/SKILL.md` | exit code 1 |
| STAGE_ORDER unchanged | `grep "STAGE_ORDER = " bridge/stage_detector.py` | output contains ISSUE |

---

## Open Questions

1. Should the max-cycle counter (PATCH->TEST loops) be configurable per-session, or is a hardcoded default of 3 sufficient?
2. The `do-build/SKILL.md` also contains pipeline navigation language ("Advance to branch/implement/test/review/document stage"). Should those be removed too, or are they part of do-build's internal workflow (since do-build spans multiple internal stages)?
