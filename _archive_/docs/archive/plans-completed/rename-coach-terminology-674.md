---
status: Complete
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/674
last_comment_id:
---

# Rename vestigial 'coach' terminology to reflect actual behavior

## Problem

The codebase contains ~24 files using "coach" / "coaching" terminology inherited from a deleted `bridge/coach.py` module. The coach module was absorbed into the summarizer, but the naming was never updated.

**Current behavior:**
New readers encounter `coaching_message`, `NARRATION_COACHING_MESSAGE`, and "coaching loop" references that imply a coaching/mentoring system. No such system exists -- the actual behavior is output classification, rejection feedback, and nudge/continuation.

**Desired outcome:**
All vestigial "coach" terminology replaced with accurate names (`nudge_feedback`, `NARRATION_NUDGE_FEEDBACK`, "nudge loop"). Dead `bridge/coach.py` references removed entirely.

## Prior Art

- **PR #135**: "Merge coach and classifier into a single LLM pass" -- merged 2026-02-17. This is the PR that absorbed coach into summarizer but left the naming behind.
- **PR #614**: "Unify persona vocabulary: eliminate ChatMode and Q&A naming" -- merged 2026-03-31. Similar terminology cleanup precedent, successful pattern to follow.
- **Issue #668**: Narrow fix that removed only 2 dead bridge/coach.py references. This issue supersedes it by addressing the root cause: the entire "coach" vocabulary is vestigial.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Mechanical find-and-replace across known files. No design decisions, no behavioral changes.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Dataclass rename**: `coaching_message` field in `OutputClassification` becomes `nudge_feedback`
- **Constant rename**: `NARRATION_COACHING_MESSAGE` becomes `NARRATION_NUDGE_FEEDBACK`
- **Terminology cleanup**: "coaching loop" becomes "nudge loop" in docs and comments
- **Dead ref removal**: Delete all references to `bridge/coach.py` and `coaching-loop.md`

### Technical Approach

1. Rename the `coaching_message` field in `bridge/summarizer.py` OutputClassification dataclass
2. Update all references in the summarizer prompt template (JSON field names stay as `coaching_message` in the LLM prompt temporarily -- actually no, rename those too since the prompt and parsing are tightly coupled)
3. Rename `NARRATION_COACHING_MESSAGE` in `bridge/message_quality.py`
4. Update all consumers: `agent/agent_session_queue.py`, `agent/context_modes.py`, `agent/sdk_client.py`
5. Update all test files to use new names
6. Update all doc files to use new terminology
7. Remove dead `bridge/coach.py` and `coaching-loop.md` references

### Flow

No flow change -- this is purely a rename.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a rename, not new logic

### Empty/Invalid Input Handling
- No new functions or modified input handling -- field names change, types and behavior stay identical

### Error State Rendering
- No user-visible output changes

## Test Impact

- [x] `tests/unit/test_summarizer.py` -- UPDATE: rename all `coaching_message` references to `nudge_feedback` (~40 occurrences)
- [x] `tests/unit/test_message_quality.py` -- UPDATE: rename `NARRATION_COACHING_MESSAGE` to `NARRATION_NUDGE_FEEDBACK` (2 tests)
- [x] `tests/unit/test_agent_session_queue_async.py` -- UPDATE: rename `coaching_message=` kwarg to `nudge_feedback=`
- [x] `tests/unit/test_cross_wire_fixes.py` -- UPDATE: update comment referencing "coach" chain
- [x] `tests/conftest.py` -- UPDATE: rename "coach" key in mapping to "summarizer" (line 288)

## Rabbit Holes

- Renaming the JSON field in the LLM prompt: must be done together with the parsing code, not separately. The prompt says `"coaching_message"` and the parser reads `data.get("coaching_message")` -- both must change atomically to `"nudge_feedback"`.
- Touching archived/completed plan files: leave historical plans as-is. Only update active docs.

## Risks

### Risk 1: LLM prompt/parse mismatch
**Impact:** If the JSON field name in the prompt diverges from the parser, all classifications break.
**Mitigation:** Rename both prompt template and `data.get()` call in the same commit. Validate with existing tests.

## Race Conditions

No race conditions identified -- this is a rename of static field names and string constants, no concurrency involved.

## No-Gos (Out of Scope)

- Do NOT rename anything inside archived/completed plan files (e.g., `docs/plans/completed/`)
- Do NOT change any behavioral logic -- only names and strings
- Do NOT modify the LLM's classification behavior or thresholds

## Update System

No update system changes required -- this is an internal rename with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this changes internal field names and documentation only. No MCP servers, tool registrations, or bridge imports are affected.

## Documentation

- [x] Update `docs/features/agent-session-model.md` -- replace coaching references with nudge terminology
- [x] Update `docs/features/context-fidelity-modes.md` -- replace coaching references
- [x] Update `docs/features/goal-gates.md` -- replace coaching references
- [x] Update `docs/features/reaction-semantics.md` -- replace coaching references
- [x] Update `docs/features/skill-context-injection.md` -- replace coaching references
- [x] Update `docs/guides/claude-prompting-best-practices.md` -- replace coaching references
- [x] Update `docs/guides/sdlc-storyline-example.md` -- replace coaching references
- [x] Update `tests/README.md` -- replace coaching references
- [x] Update `.claude/skills/do-pr-review/sub-skills/README.md` -- replace coaching references
- [x] Update `.claude/skills/do-pr-review/sub-skills/checkout.md` -- replace coaching references
- [x] Update `.claude/skills/do-pr-review/sub-skills/code-review.md` -- replace coaching references
- [x] Update `docs/plans/wire-pipeline-graph-563.md` -- remove dead bridge/coach.py references
- [x] Update `docs/plans/unify-persona-vocabulary.md` -- replace coaching references
- [x] Update `docs/plans/audit-unreviewed-prs-660-664.md` -- replace coaching references
- [x] Update `docs/plans/dennett_thinking_skills.md` -- replace coaching references
- [x] Update `monitoring/session_watchdog.py` -- remove dead coaching-loop.md reference

## Success Criteria

- [x] `grep -rn "coach" --include="*.py" .` returns zero matches in functional code
- [x] `grep -rn "coach" --include="*.md" docs/ .claude/` returns zero matches (except archived plans)
- [x] All tests pass (`/do-test`)
- [x] No behavioral changes -- purely rename/cleanup

## Team Orchestration

### Team Members

- **Builder (rename)**
  - Name: rename-builder
  - Role: Execute all renames across Python code, tests, and docs
  - Agent Type: builder
  - Resume: true

- **Validator (verify)**
  - Name: rename-validator
  - Role: Verify zero coach occurrences and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rename Python functional code
- **Task ID**: build-python-rename
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py, tests/unit/test_message_quality.py, tests/unit/test_agent_session_queue_async.py
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `coaching_message` to `nudge_feedback` in `bridge/summarizer.py` (dataclass field, prompt template, parser)
- Rename `NARRATION_COACHING_MESSAGE` to `NARRATION_NUDGE_FEEDBACK` in `bridge/message_quality.py`
- Update `agent/agent_session_queue.py` to use `nudge_feedback` kwarg/field
- Update comments in `agent/context_modes.py` and `agent/sdk_client.py`
- Update `monitoring/session_watchdog.py` to remove dead coaching-loop.md reference

### 2. Update tests
- **Task ID**: build-test-rename
- **Depends On**: build-python-rename
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- Rename all `coaching_message` references to `nudge_feedback` in `tests/unit/test_summarizer.py`
- Rename references in `tests/unit/test_message_quality.py`
- Rename kwarg in `tests/unit/test_agent_session_queue_async.py`
- Update comment in `tests/unit/test_cross_wire_fixes.py`
- Update mapping in `tests/conftest.py`

### 3. Update documentation
- **Task ID**: build-docs-rename
- **Depends On**: none
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace "coaching" with "nudge" terminology in all listed doc files
- Remove dead bridge/coach.py references
- Update tests/README.md

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-python-rename, build-test-rename, build-docs-rename
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "coach" --include="*.py" .` -- expect zero results
- Run `grep -rn "coach" --include="*.md" docs/ .claude/` -- expect zero results (except archived plans)
- Run `pytest tests/unit/ -x -q` -- expect all pass
- Run `python -m ruff check .` -- expect clean

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No coach in Python | `grep -rn "coach" --include="*.py" bridge/ agent/ monitoring/ tools/` | exit code 1 |
| No coach in active docs | `grep -rn "coach" --include="*.md" docs/features/ docs/guides/ .claude/skills/` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- scope is fully defined by the issue.
