---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-03-03
tracking:
---

# SDLC Summary Improvements

## Problem

The SDLC summary messages sent to Telegram have several UX issues that reduce their usefulness as status updates.

**Current behavior:**
```
✅
☐ ISSUE → ☑ PLAN → ☐ BUILD → ☐ TEST → ☐ REVIEW → ☐ DOCS
• Analyzed issue #241 summarizer code and threshold logic
• Created execution plan for response composition and link rendering improvements
• Ready to proceed to build phase
Issue #241 | Plan
```

Issues:
1. The ISSUE stage checkbox is never checked even though the pipeline cannot proceed without an issue. The issue number should be merged into the first stage label (e.g., `ISSUE 241 →`).
2. Bullet points often state the obvious (e.g., "Analyzed issue #241") — the summarizer LLM should be instructed to omit process-obvious bullets.
3. The summary says "Ready to proceed to build phase" but the pipeline did not actually proceed. The pipeline should always auto-continue until it genuinely needs human approval.
4. The link footer includes a "Plan" link which is unnecessary. Only issue and PR links should appear.

**Desired outcome:**
```
⏳ ISSUE 243 → PLAN → ▶ BUILD → TEST → REVIEW → DOCS
• Implemented token rotation with retry logic
• 12 tests passing, 0 failures
Issue #243 | PR #250
```

- Issue number merged into the ISSUE stage label
- No checkboxes — use bold/arrows for completed stages, plain text for pending
- No plan link in the footer
- Only substantive bullets (no "analyzed the code" filler)
- Pipeline auto-continues through all stages until it needs human input

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Stage progress line format change**: Replace checkbox icons with a cleaner format that embeds the issue number in the ISSUE label
- **Plan link removal**: Stop rendering plan links in the footer
- **Summarizer prompt update**: Instruct the LLM to omit obvious process bullets
- **Auto-continue behavior**: Already handled by stage-aware routing — the issue was a plan-stage stopping point that has since been fixed by the simplify_summarizer work

### Flow

Summary is composed → `_render_stage_progress()` generates new format with issue number → `_render_link_footer()` skips plan links → LLM produces substantive bullets only → structured output delivered to Telegram

### Technical Approach

1. **`_render_stage_progress(session)`**: Change the rendering logic:
   - Accept the issue number from session links (`session.issue_url` → extract `#NNN`)
   - Completed stages: just the stage name (no icon prefix needed, position implies completion)
   - In-progress stage: `▶ STAGE`
   - Pending stages: plain `STAGE`
   - ISSUE stage: render as `ISSUE NNN` when issue number is available
   - Join with ` → `

2. **`_render_link_footer(session)`**: Remove the `plan` case from the rendering loop — only render `issue` and `pr` links.

3. **`SUMMARIZER_SYSTEM_PROMPT`**: Add instruction to omit obvious process bullets like "Analyzed the codebase", "Read through the plan", "Created execution plan". Focus on outcomes and deliverables.

4. **Tests**: Update existing tests in `test_summarizer.py` to match new format expectations.

## Rabbit Holes

- Redesigning the entire summary format or adding new sections — keep the format evolution minimal
- Adding color/emoji for stages — keep it text-based for Telegram compatibility
- Changing the auto-continue logic — the stage-aware routing already handles this correctly

## Risks

### Risk 1: Test breakage from format changes
**Impact:** Existing tests assert specific icon characters (☑, ☐) that will change
**Mitigation:** Update all tests in the same PR; run full test suite before merging

### Risk 2: Downstream consumers expecting old format
**Impact:** If any other code parses the progress line, it could break
**Mitigation:** Grep for ☑/☐ usage outside summarizer to verify no external consumers

## No-Gos (Out of Scope)

- Changing the auto-continue logic or classifier behavior
- Adding new stages to the SDLC pipeline
- Changing the session progress tracking system
- Modifying how stage data is written to Redis

## Update System

No update system changes required — this is a bridge-internal display format change. No new dependencies or config files.

## Agent Integration

No agent integration required — this modifies the summarizer's output formatting only. The changes are in `bridge/summarizer.py` (display code) and the LLM prompt. No new MCP tools or bridge imports needed.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` to reflect new stage progress format and removed plan link
- [ ] Update format examples in the feature doc

## Success Criteria

- [ ] Stage progress line shows `ISSUE NNN → PLAN → ▶ BUILD → TEST → REVIEW → DOCS` format
- [ ] No checkbox icons (☑, ☐) in stage progress output
- [ ] Plan link is not rendered in the link footer
- [ ] Summarizer prompt instructs LLM to omit obvious process bullets
- [ ] All existing tests updated and passing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (summarizer)**
  - Name: summarizer-builder
  - Role: Modify stage progress rendering, link footer, and LLM prompt
  - Agent Type: builder
  - Resume: true

- **Validator (summarizer)**
  - Name: summarizer-validator
  - Role: Verify format changes and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update stage progress rendering
- **Task ID**: build-stage-progress
- **Depends On**: none
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `_render_stage_progress()` in `bridge/summarizer.py` to use new format without checkboxes
- Extract issue number from `session.issue_url` and embed in ISSUE label
- Completed stages show plain name, in-progress shows `▶`, pending shows plain name

### 2. Remove plan link from footer
- **Task ID**: build-remove-plan-link
- **Depends On**: build-stage-progress
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove the `plan` case from `_render_link_footer()`
- Only render `issue` and `pr` links

### 3. Update summarizer LLM prompt
- **Task ID**: build-update-prompt
- **Depends On**: build-remove-plan-link
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Add instruction to `SUMMARIZER_SYSTEM_PROMPT` to omit obvious process bullets
- Provide examples of what to omit vs. what to keep

### 4. Update tests
- **Task ID**: build-update-tests
- **Depends On**: build-update-prompt
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all tests in `tests/test_summarizer.py` that assert old format
- Add test for issue number in stage progress line
- Add test verifying plan link is not rendered

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-update-tests
- **Assigned To**: summarizer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_summarizer.py`
- Verify no other code references ☑/☐ icons for parsing
- Confirm format matches desired outcome

## Validation Commands

- `pytest tests/test_summarizer.py -v` - Verify all summarizer tests pass
- `grep -r '☑\|☐' bridge/ tests/ --include='*.py'` - Ensure no stale checkbox references remain outside summarizer
- `black . && ruff check .` - Code quality
