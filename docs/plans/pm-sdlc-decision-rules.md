---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/544
last_comment_id:
---

# PM SDLC Decision Rules

## Problem

The ChatSession PM persona orchestrates SDLC work by spawning DevSessions for each pipeline stage. DevSessions execute skill files (do-pr-review, do-patch, do-merge) that contain detailed decision rules -- severity categories, outcome routing, merge gates. However, the PM never reads these skill files. It only sees the DevSession's output and must decide what to do next using generic PM dispatch instructions injected at session start.

**Current behavior:**
- PM dispatch instructions (`sdk_client.py:1563-1600`) contain only: assess, spawn, verify, repeat
- No rules for interpreting review outcomes (`success` vs `partial` vs `fail`)
- No rules for when to auto-merge vs escalate to human
- No rules for handling tech debt and nits (patch immediately? skip? annotate?)
- PM stops to ask permission for obvious merges, or merges while ignoring findings

**Desired outcome:**
- PM reliably patches tech debt and nits before merging (never silently skips findings)
- PM auto-merges when all stages pass cleanly (never asks permission for obvious merges)
- When a nit is genuinely not worth changing, the patch adds an inline code comment explaining why -- not a silent skip
- PM's SDLC decisions are reliable enough to safely obscure process from stakeholders (#540, #541)

## Prior Art

- **PR #487**: "SDLC prompt enforcement: stage-by-stage agent orchestration" -- Established the current PM dispatch instructions. Created the assess/spawn/verify/repeat pattern but without outcome interpretation rules.
- **Issue #520 / PR #523**: "SDLC stage handoff via GitHub issue comments" -- Added `<!-- sdlc-stage-comment -->` markers and `<!-- OUTCOME -->` JSON blocks to stage outputs. The infrastructure for structured handoff exists but the PM has no instructions to parse it.
- **Issue #309**: "Observer Agent: replace auto-continue/summarizer with stage-aware SDLC steerer" -- Earlier attempt at SDLC-aware orchestration, replaced by current ChatSession approach.

## Data Flow

1. **Entry**: DevSession completes a stage (e.g., REVIEW) and emits an `<!-- OUTCOME -->` JSON comment as its last output line
2. **ChatSession PM**: Receives the DevSession's full output text, including the OUTCOME block
3. **Decision point** (gap): PM must parse the outcome and decide what stage to dispatch next
4. **Dispatch**: PM spawns the next DevSession with the appropriate stage assignment
5. **Output**: Stage result flows back to the PM for the next decision cycle

The gap is at step 3: the PM has no rules for parsing OUTCOME blocks or mapping statuses to next actions.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (clear requirements from issue)
- Review rounds: 1

Solo dev work -- the changes are primarily prompt text in `sdk_client.py` and a small addition to `do-patch/SKILL.md`.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **PM decision rules block**: New text appended to the PM dispatch instructions in `sdk_client.py` that tells the PM how to interpret stage outcomes and route accordingly
- **Annotate pattern in do-patch**: A small addition to `do-patch/SKILL.md` instructing the builder to add inline code comments for findings intentionally not fixed, rather than silently skipping

### Flow

**DevSession completes REVIEW** --> PM reads `<!-- OUTCOME -->` block --> status is `success`? --> dispatch DOCS/MERGE
--> status is `partial`? --> dispatch PATCH for tech_debt/nits --> re-REVIEW after patch
--> status is `fail`? --> dispatch PATCH for blockers --> re-REVIEW after patch

**DevSession completes PATCH for nits** --> finding genuinely not worth changing? --> builder adds inline code comment explaining rationale --> finding is "addressed" (annotated, not skipped)

### Technical Approach

1. **Expand PM dispatch text** in `sdk_client.py` (the string starting at line 1563). Add a new section after the current step 4 ("Repeat") covering:
   - How to parse `<!-- OUTCOME {"status":"...", "next_skill":"..."} -->` from DevSession output
   - Decision table: `success` --> proceed to next logical stage; `partial` --> dispatch PATCH; `fail` --> dispatch PATCH
   - Auto-merge rule: when TEST + REVIEW + DOCS all completed with no findings, merge without asking
   - Escalation rule: only ask human when genuinely blocked or when a decision requires business judgment
   - General principle: findings are never silently ignored -- they are either fixed or annotated

2. **Add "annotate" pattern to do-patch** in `.claude/skills/do-patch/SKILL.md`. After the builder agent prompt template (around line 132), add guidance for when a review finding should not be fixed:
   - The builder should add an inline code comment at the relevant location explaining why the code was left as-is
   - The comment format: `# NOTE: [finding summary] -- left as-is because [rationale]`
   - This creates a paper trail so the next reviewer does not re-flag the same issue

3. **Keep prompt text concise** -- the PM dispatch instructions must fit in context without bloating. Use a compact decision table format, not lengthy prose.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this work modifies prompt text strings, not runtime logic

### Empty/Invalid Input Handling
- [ ] Test that PM dispatch instructions are still injected when enriched_message is built (existing test coverage in `test_sdk_client_sdlc.py` validates WORKER_RULES injection; new text must not break that)
- [ ] Verify the OUTCOME block parsing guidance works even when DevSession output contains no OUTCOME block (PM should fall back to its own judgment)

### Error State Rendering
- No user-visible output changes -- this modifies internal PM orchestration behavior

## Test Impact

- [ ] `tests/unit/test_sdk_client_sdlc.py` -- UPDATE: if any tests assert exact PM dispatch text content, they will need updating to include the new decision rules text. Most tests in this file check for WORKER_RULES or system prompt structure, not PM dispatch text verbatim, so impact is likely minimal.
- [ ] `tests/unit/test_sdk_client.py` -- UPDATE: if tests assert on the enriched_message string length or content, they may need adjustment.

No tests assert on do-patch SKILL.md content (skill files are not tested via unit tests -- they are tested via SDLC integration).

## Rabbit Holes

- Modifying the do-pr-review skill to change severity categories or outcome format -- the existing system is correct, the gap is only in PM awareness
- Building a formal OUTCOME parser/deserializer in Python -- the PM reads the JSON from the text output using its LLM capabilities; no code parser needed
- Expanding this to cover every possible stage transition -- start with REVIEW outcomes (the observed failure mode) and add others only if needed
- Trying to make the PM perfectly autonomous for all edge cases -- some situations genuinely need human judgment

## Risks

### Risk 1: PM dispatch text becomes too long and degrades PM reasoning quality
**Impact:** PM loses focus on the conversation and becomes overly mechanical
**Mitigation:** Keep the decision rules to a compact table format (under 15 lines of added text). Test by reviewing total enriched_message length.

### Risk 2: Auto-merge rule triggers merge on a PR that should have been reviewed by a human
**Impact:** Low-quality code ships without human oversight
**Mitigation:** Auto-merge only triggers when ALL gate checks pass (TEST + REVIEW + DOCS completed, 0 findings). Any findings at all route to PATCH first. The do-merge skill already has its own gate checks as a safety net.

## Race Conditions

No race conditions identified -- all operations are synchronous prompt text changes. The PM processes one stage at a time sequentially.

## No-Gos (Out of Scope)

- Modifying do-pr-review outcome format or severity categories
- Modifying do-merge gate checks (they already work correctly)
- Building a Python-level OUTCOME parser (LLM reads the JSON directly)
- Adding decision rules for plan critique outcomes (defer to a follow-up if needed)
- Changing DevSession behavior or skill file execution model

## Update System

No update system changes required -- this modifies prompt text in `sdk_client.py` and a skill file, both of which are propagated by the standard `git pull` in the update script.

## Agent Integration

No agent integration required -- this is a change to the PM's internal orchestration instructions. No new MCP servers, tools, or bridge changes needed. The PM already receives the dispatch instructions via `sdk_client.py`; this work modifies the content of those instructions.

## Documentation

- [ ] Create `docs/features/pm-sdlc-decision-rules.md` describing the PM decision rules, the OUTCOME parsing pattern, and the annotate-rather-than-skip principle
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] PM dispatch instructions include explicit rules for interpreting review outcomes (success, partial, fail)
- [ ] PM auto-proceeds when review status is `success` and all stages are complete
- [ ] PM dispatches PATCH when review status is `partial` or `fail`
- [ ] do-patch skill documents the "annotate" pattern for findings intentionally not fixed
- [ ] PM dispatch text addition is concise (under 20 lines added to the existing block)
- [ ] Existing tests in `test_sdk_client_sdlc.py` still pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (dispatch-rules)**
  - Name: dispatch-builder
  - Role: Expand PM dispatch instructions in sdk_client.py and update do-patch SKILL.md
  - Agent Type: builder
  - Resume: true

- **Validator (dispatch-rules)**
  - Name: dispatch-validator
  - Role: Verify dispatch text is injected correctly and existing tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Expand PM dispatch instructions
- **Task ID**: build-dispatch-rules
- **Depends On**: none
- **Validates**: tests/unit/test_sdk_client_sdlc.py, tests/unit/test_sdk_client.py
- **Assigned To**: dispatch-builder
- **Agent Type**: builder
- **Parallel**: true
- Add SDLC decision rules section to PM dispatch text in `agent/sdk_client.py` (after the current "Repeat" step, before the "Communicating with the stakeholder" section)
- Rules must cover: OUTCOME parsing, status-to-action mapping (success/partial/fail), auto-merge conditions, escalation criteria
- Keep added text concise -- compact decision table format

### 2. Add annotate pattern to do-patch
- **Task ID**: build-annotate-pattern
- **Depends On**: none
- **Validates**: manual review of SKILL.md
- **Assigned To**: dispatch-builder
- **Agent Type**: builder
- **Parallel**: true
- Add guidance to `.claude/skills/do-patch/SKILL.md` for the "annotate rather than skip" pattern
- Place after the builder agent prompt template (around line 132)
- Document the inline code comment format for findings intentionally left as-is

### 3. Validate changes
- **Task ID**: validate-dispatch
- **Depends On**: build-dispatch-rules, build-annotate-pattern
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdk_client_sdlc.py tests/unit/test_sdk_client.py -v`
- Verify PM dispatch text includes the new decision rules
- Verify do-patch SKILL.md includes annotate pattern
- Check total enriched_message size is reasonable

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-dispatch
- **Assigned To**: dispatch-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-sdlc-decision-rules.md`
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dispatch text includes decision rules | `grep -c "OUTCOME" agent/sdk_client.py` | output > 0 |
| Do-patch includes annotate pattern | `grep -c "annotate" .claude/skills/do-patch/SKILL.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue provides clear requirements, production evidence, and a well-scoped solution. The existing OUTCOME contract in do-pr-review and gate checks in do-merge are confirmed to work correctly; the only gap is the PM's awareness of these mechanisms.
