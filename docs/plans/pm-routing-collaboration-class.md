---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/846
last_comment_id:
---

# PM Routing: Add Collaboration and Other Classifier Buckets

## Problem

The PM work-request classifier only distinguishes two LLM outcomes: "sdlc" (code work) and "question" (informational). Tasks the PM could handle directly -- saving to knowledge base, drafting issues, writing docs, sending updates -- get funneled into a full dev-session SDLC pipeline, wasting 30-60 minutes on work that should take under a minute.

**Current behavior:**

`classify_work_request()` in `bridge/routing.py` uses an LLM prompt with two outcomes: "sdlc" and "question". The prompt says "If in doubt, classify as sdlc." PM dispatch instructions in `agent/sdk_client.py` (lines 1608-1664) inject SDLC orchestration unconditionally for all non-Teammate PM sessions, regardless of classification.

Concrete failure: "Add this to knowledge base and draft an issue" was classified as sdlc (0.85 confidence). A dev-session ran for 60 minutes, made 1 tool call, timed out, and delivered nothing. The PM could have done this in two direct tool calls.

**Desired outcome:**

- Tasks like "save this to the knowledge base", "draft an issue", "write a status doc" route to `collaboration` and the PM executes them directly.
- Ambiguous tasks route to `other` -- PM uses judgment without SDLC overhead.
- PM persona documents all available tools so it can actually handle direct work.

## Prior Art

- **PR #387**: Add PM channel mode -- established the PM/Teammate split but left all non-Teammate work routed to full SDLC pipeline
- **PR #228**: SDLC-first architecture -- created the classifier with only sdlc/question outcomes
- **PR #602**: Agent-controlled message delivery -- added classification context pass-through but did not expand classification types

No prior attempt to add collaboration/other buckets. This is greenfield within the classifier.

## Data Flow

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py`
2. **Routing**: `bridge/routing.py` `classify_work_request()` runs fast-path checks, then calls `_classify_work_request_llm()` which prompts Ollama/Haiku with two outcomes
3. **Classification result**: Returns "sdlc", "question", or "passthrough" (fast-path only)
4. **PM session creation**: `agent/sdk_client.py` receives the classification, injects routing context as advisory text, then unconditionally injects SDLC orchestration instructions for all non-Teammate PM sessions
5. **Output**: PM session either spawns a dev-session (sdlc path) or answers directly (question/Teammate path)

After this change, step 4 becomes conditional: sdlc classification gets SDLC orchestration; collaboration/other gets a direct-action prompt telling the PM to handle the task itself.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Four files changed (enum, classifier prompt, SDK client conditional, persona template). No new modules, no new dependencies, no architectural changes.

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing Python files and markdown templates.

## Solution

### Key Elements

- **ClassificationType enum extension**: Add COLLABORATION and OTHER members to the existing StrEnum
- **Classifier prompt update**: Four-outcome LLM prompt with clear examples and "if in doubt, classify as collaboration" default
- **Conditional PM dispatch**: SDK client checks classification and injects either SDLC orchestration or direct-action instructions
- **PM persona tool visibility**: Document memory_search, work-vault, valor_session, gws, officecli in the persona template

### Flow

**Telegram message** -> classify_work_request() -> [sdlc|collaboration|other|question] -> sdk_client conditionally injects instructions -> PM session executes

For `collaboration`: PM gets direct-action prompt, handles task with available tools, no dev-session spawned.
For `other`: PM gets direct-action prompt with judgment discretion -- may spawn dev-session or handle directly.
For `sdlc`: Unchanged behavior -- full SDLC orchestration instructions.

### Technical Approach

- Extend `ClassificationType` in `config/enums.py` with `COLLABORATION = "collaboration"` and `OTHER = "other"`
- Rewrite the LLM prompt in `_classify_work_request_llm()` to request one of four outcomes with clear category examples
- Update result parsing in `_classify_work_request_llm()` to detect "collaboration" and "other" in LLM responses
- In `sdk_client.py`, wrap the SDLC orchestration block (lines 1615-1664) in a conditional that checks `_classification_context`
- Add a new direct-action instruction block for collaboration/other classifications
- Update `config/personas/project-manager.md` with full tool reference section

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_classify_work_request_llm()` has try/except blocks for Ollama and Haiku fallbacks -- existing tests cover fallback behavior; new tests verify collaboration/other parsing within the same error handling
- [ ] If LLM returns an unrecognized word, the fallback default changes from QUESTION to QUESTION (unchanged -- conservative default for unknown responses)

### Empty/Invalid Input Handling
- [ ] `classify_work_request("")` and `classify_work_request(None)` already return "passthrough" -- unchanged by this work
- [ ] New classification types only apply to the LLM path, which requires non-empty text

### Error State Rendering
- [ ] If classification fails entirely, the fallback is QUESTION (no SDLC overhead) -- unchanged behavior
- [ ] No user-visible output changes; routing is invisible to the end user

## Test Impact

- [ ] `tests/unit/test_enums.py::TestClassificationType::test_all_members` -- UPDATE: change assertion from `len == 2` to `len == 4`
- [ ] `tests/unit/test_enums.py::TestClassificationType::test_string_equality` -- UPDATE: add assertions for COLLABORATION and OTHER
- [ ] `tests/unit/test_work_request_classifier.py::TestLlmClassification` -- UPDATE: add parametrized test cases for collaboration-type messages (e.g., "Save this to the knowledge base", "Draft an issue for X")
- [ ] `tests/unit/test_pm_channels.py` -- REVIEW: check if any assertions depend on unconditional SDLC instruction injection
- [ ] `tests/unit/test_sdlc_mode.py` -- REVIEW: check if classification type assumptions need updating

## Rabbit Holes

- Fixing the passthrough string/enum inconsistency (passthrough is returned as a raw string, not a ClassificationType member) -- separate cleanup, explicitly out of scope per issue #846
- Adding sub-categories within collaboration (e.g., "knowledge-base-write" vs "issue-draft") -- over-engineering for this appetite
- Modifying the Teammate mode routing -- Teammate mode is a separate persona path, not affected by this work
- Updating `tools/classifier.py` (the separate request classifier used by other systems) -- that is a different classification system

## Risks

### Risk 1: LLM misclassifies sdlc work as collaboration
**Impact:** PM tries to handle a coding task directly, fails or produces poor results
**Mitigation:** The advisory text already says "use your judgment" -- if the PM encounters code-level work, it can still spawn a dev-session. The classifier prompt will include clear boundary examples. Additionally, the bias shifts from "if in doubt, sdlc" to "if in doubt, collaboration" which is strictly less expensive when wrong (PM tries and escalates vs. 60-minute dev-session timeout).

### Risk 2: Private PM persona file out of sync
**Impact:** The private `~/Desktop/Valor/personas/project-manager.md` does not get the tool visibility update on all machines
**Mitigation:** The plan includes an explicit step to update the private persona. The public template at `config/personas/project-manager.md` serves as the authoritative reference. The `/update` skill propagates the public template.

## Race Conditions

No race conditions identified -- classification is synchronous and single-request scoped. The classification result is passed as a string through the session creation flow with no shared mutable state.

## No-Gos (Out of Scope)

- Fixing passthrough string/enum inconsistency (separate cleanup)
- Sub-categorizing collaboration types
- Modifying Teammate mode routing or intent_classifier.py
- Changing `tools/classifier.py` (separate classification system)
- Adding new MCP servers or tools -- this only documents existing tools in the persona

## Update System

No update system changes required -- this feature modifies existing Python files and a persona template that are already part of the standard git pull. The `/update` skill's existing `git pull` step propagates all changes. The private persona file at `~/Desktop/Valor/personas/project-manager.md` must be manually updated on each machine (documented in the build tasks).

## Agent Integration

No agent integration required -- this is a bridge-internal routing change. The classifier runs inside `bridge/routing.py` and the instruction injection happens in `agent/sdk_client.py`. No new MCP servers, no `.mcp.json` changes, no new tools. The PM persona update documents existing tools that are already available to the agent.

## Documentation

- [ ] Create `docs/features/pm-routing-collaboration.md` describing the four-way classification and conditional dispatch
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings for `classify_work_request()` and `_classify_work_request_llm()`

## Success Criteria

- [ ] `ClassificationType` enum has 4 members: SDLC, QUESTION, COLLABORATION, OTHER
- [ ] Classifier LLM prompt requests one of four outcomes with clear examples
- [ ] "If in doubt, classify as collaboration" replaces "If in doubt, classify as sdlc"
- [ ] PM sessions classified as collaboration/other get direct-action instructions, not SDLC orchestration
- [ ] PM sessions classified as sdlc get unchanged SDLC orchestration instructions
- [ ] PM persona template documents: memory_search, work-vault path, valor_session, gws, officecli
- [ ] "Add this to knowledge base and draft an issue" classifies as collaboration
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement enum extension, classifier prompt, conditional dispatch, persona update
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify classification results and conditional dispatch behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend ClassificationType Enum
- **Task ID**: build-enum
- **Depends On**: none
- **Validates**: tests/unit/test_enums.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `COLLABORATION = "collaboration"` and `OTHER = "other"` to `ClassificationType` in `config/enums.py`
- Update `tests/unit/test_enums.py::TestClassificationType` to assert 4 members and test string equality for new types

### 2. Update Classifier LLM Prompt
- **Task ID**: build-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_work_request_classifier.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite prompt in `_classify_work_request_llm()` to request one of four outcomes: sdlc, collaboration, other, question
- Add clear examples for collaboration: "Add this to the knowledge base", "Draft an issue for X", "Send a status update", "Write a doc about Y", "Save this file"
- Change default from "If in doubt, classify as sdlc" to "If in doubt, classify as collaboration"
- Update result parsing to detect "collaboration" and "other" in LLM responses, returning `ClassificationType.COLLABORATION` and `ClassificationType.OTHER`
- Add parametrized test cases for collaboration-type messages in `tests/unit/test_work_request_classifier.py`

### 3. Make PM Dispatch Instructions Conditional
- **Task ID**: build-dispatch
- **Depends On**: build-enum
- **Validates**: tests/unit/test_pm_channels.py, tests/unit/test_sdlc_mode.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/sdk_client.py`, check `_classification_context` before injecting instructions
- If classification is "sdlc" (or not set, for backward compatibility): inject existing SDLC orchestration block unchanged
- If classification is "collaboration" or "other": inject a direct-action prompt: "Handle this task directly using your available tools. You have access to Bash, file operations, GitHub CLI, Google Workspace, memory search, and Office CLI. No dev-session needed unless you determine the task requires code changes to the repository."
- Preserve the Telegram messaging instructions for all PM paths (they are useful for both SDLC and direct work)

### 4. Update PM Persona Template
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: manual review
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a "## Available Tools" section to `config/personas/project-manager.md` documenting:
  - `python -m tools.memory_search save/search/inspect/forget` -- knowledge base operations
  - Work-vault path: `~/src/work-vault/` (or `~/Desktop/Valor/` on bridge machines)
  - `python -m tools.valor_session list/status/steer/kill/create` -- session management
  - `gws` -- Google Workspace CLI (pre-authenticated at `~/src/node_modules/.bin/gws`)
  - `officecli` -- Office document CLI at `~/.local/bin/officecli`
  - `gh` -- GitHub CLI for issues, PRs, repos
- Note in the plan that `~/Desktop/Valor/personas/project-manager.md` (private) must be manually updated to match

### 5. Validate End-to-End
- **Task ID**: validate-all
- **Depends On**: build-classifier, build-dispatch, build-persona
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_enums.py tests/unit/test_work_request_classifier.py -v`
- Verify `ClassificationType` has 4 members
- Verify classifier prompt contains all four outcomes
- Verify sdk_client.py conditional dispatch logic
- Run full test suite: `pytest tests/unit/ -x -q`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-routing-collaboration.md`
- Add entry to `docs/features/README.md` index table
- Update docstrings in `bridge/routing.py` and `agent/sdk_client.py`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_enums.py tests/unit/test_work_request_classifier.py -v` | exit code 0 |
| Enum has 4 members | `python -c "from config.enums import ClassificationType; assert len(list(ClassificationType)) == 4"` | exit code 0 |
| Classifier prompt has collaboration | `grep -c 'collaboration' bridge/routing.py` | output > 0 |
| Conditional dispatch exists | `grep -c 'collaboration' agent/sdk_client.py` | output > 0 |
| Persona has tools section | `grep -c 'memory_search' config/personas/project-manager.md` | output > 0 |
| Lint clean | `python -m ruff check config/enums.py bridge/routing.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check config/enums.py bridge/routing.py agent/sdk_client.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-09. Verdict: NEEDS REVISION (2 blockers). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Archaeologist | Plan targets wrong dispatch mechanism -- two separate classifiers exist. `_classification_context` is a descriptive string, not a `ClassificationType` enum, so the proposed check would never match. The actual PM-vs-Teammate decision is in `agent/intent_classifier.py::classify_intent()` via `_teammate_mode` flag. Plan only addresses bridge-level classifier but claims the change flows through to PM dispatch, which it does not. | Task 3 rewrite needed | Dispatch block at sdk_client.py line 1626 (`else:` branch). `_classification_context` set at line 1519 holds a string. Must thread `classification` or new intent result into `_teammate_mode` else-block. |
| BLOCKER | Skeptic, Operator | `agent/intent_classifier.py` missing from touched-files inventory and data flow. This file controls PM dispatch for unconfigured groups. Without addressing it, collaboration-type messages still hit `classify_intent()` which returns `"work"` for messages like "Add this to the knowledge base" (confidence ~0.97). | Add to file inventory, update data flow | `intent_classifier.py` lines 23-60 contain `CLASSIFIER_PROMPT` with `"teammate"` vs `"work"` examples. Either add `"collaboration"` intent or check bridge-level `classification_type` before falling through. |
| CONCERN | Operator, Adversary | Config-driven PM/Dev groups (via `resolve_persona()`) bypass both classifiers entirely at sdk_client.py lines 1556-1559. The new classification would be stored on AgentSession but never consulted during dispatch. The plan's failure example likely came from a config-driven PM group. | Task 3 must add classification_type check inside config-driven path | Config-driven path at sdk_client.py line 1556-1559 sets `_classification_context` but does NOT set `_teammate_mode = True`, so else-block runs unconditionally. |
| CONCERN | Skeptic, Adversary | Fallback default changed from `sdlc` to `collaboration` without safety analysis. Flips failure mode: ambiguous messages skip dev-sessions instead of over-triggering them. PM may drop real coding tasks. Plan's own problem statement contradicts the mitigation. | Keep default as `sdlc` for initial rollout; improve collaboration examples instead | In `bridge/routing.py` line 526, current prompt says "If in doubt, classify as sdlc." Add log line at fallback path for monitoring if default must change. |
| NIT | Operator | Test Impact section references test methods but omits `test_pm_session_factory.py` and `test_sdlc_mode.py` which are referenced as validation targets in Task 3. | Add to Test Impact section with dispositions | Minor: ensure all referenced test files appear in Test Impact. |

---

## Open Questions

No open questions -- the issue provides a complete solution sketch with clear acceptance criteria. All implementation decisions are straightforward extensions of existing patterns.
