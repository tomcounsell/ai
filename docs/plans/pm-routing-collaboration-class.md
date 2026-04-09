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

The work-request classifier and PM dispatch instructions assume every incoming message is either a pure information query or an SDLC coding task. This causes PM sessions to over-route into the dev-session pipeline for tasks the PM could and should execute directly.

**Current behavior:**

`classify_work_request()` in `bridge/routing.py` returns one of three values: `"sdlc"`, `"question"`, or `"passthrough"`. The LLM prompt only distinguishes two actionable outcomes -- "sdlc" (anything that could result in code changes) and "question" (purely informational). The prompt ends with: *"If in doubt, classify as sdlc."*

When a PM group session is not in Teammate mode, `sdk_client.py` injects the full SDLC orchestration prompt unconditionally -- regardless of what the classifier returned. Every non-Teammate PM session gets told to spawn dev-sessions.

Concrete failure: Tom sent *"Add this to knowledge base and draft an issue for designing this as an interaction for the podcast service."* The classifier returned `sdlc` (0.85 confidence). The PM spawned a dev-session. That dev-session ran for 60 minutes, made 1 tool call, hit the SDK timeout, and failed. The correct behavior: the PM writes a file and calls `gh issue create` -- two direct tool calls, done in under a minute.

Additionally, the PM persona only documents four tool categories (Telegram, GitHub, Sentry, Gmail). It has no visibility into memory/knowledge-base tools, session management tools, or the work-vault.

**Desired outcome:**

- Tasks like "save this to the knowledge base", "draft an issue", "write a status doc", "send an update" route to a `"collaboration"` classification and the PM executes them directly without spawning a dev-session.
- Ambiguous tasks that don't fit sdlc or collaboration route to `"other"` -- the PM uses judgment.
- The PM persona documents all tools it can use.

## Prior Art

- **Issue #541**: Dynamic PM persona -- established the Teammate/PM split but left all non-Teammate work routed to full SDLC pipeline
- **Issue #556 / PR #559**: Config-driven chat mode -- introduced persona resolution by group config
- **Issue #499**: ChatSession Q&A mode -- original Teammate-mode issue
- **Issue #599**: Unify persona vocabulary -- eliminated ChatMode and Q&A naming
- **PR #602**: Agent-controlled message delivery -- added classification context injection

None of these prior issues addressed the gap between "question" and "sdlc" -- they all focused on the Teammate/PM split rather than the granularity of PM work routing.

## Data Flow

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py`
2. **Routing** (`bridge/routing.py`): `classify_work_request()` runs fast-path checks, then calls `_classify_work_request_llm()` for nuanced classification. Returns one of: `sdlc`, `question`, `passthrough`
3. **Session creation** (`agent/sdk_client.py`): Classification result is passed as `_classification_context`. For PM sessions not in Teammate mode, lines 1615-1664 inject the full SDLC orchestration prompt unconditionally
4. **PM execution**: PM reads the SDLC instructions and spawns a dev-session for every task, even ones it could handle directly
5. **Output**: Response delivered back through Telegram

After this change, step 3 becomes conditional: `sdlc` gets SDLC instructions, `collaboration`/`other` get a direct-action prompt.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Four files touched, no new modules, no new dependencies. The classifier prompt change is the most nuanced part; the rest is mechanical.

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing files.

## Solution

### Key Elements

- **Extended ClassificationType enum**: Two new members (`COLLABORATION`, `OTHER`) in `config/enums.py`
- **Updated classifier prompt**: Four-outcome LLM prompt with clear examples for each bucket
- **Conditional PM dispatch**: `sdk_client.py` branches on classification to inject either SDLC orchestration or direct-action instructions
- **Expanded PM persona**: Both public template and private persona file document the full tool inventory

### Flow

**Message arrives** -> `classify_work_request()` -> LLM returns one of `sdlc`/`collaboration`/`other`/`question` -> `sdk_client.py` checks classification -> `sdlc`: inject SDLC orchestration prompt (unchanged) | `collaboration`/`other`: inject direct-action prompt -> PM executes accordingly

### Technical Approach

- Add `COLLABORATION = "collaboration"` and `OTHER = "other"` to `ClassificationType` in `config/enums.py`
- Rewrite the LLM prompt in `_classify_work_request_llm()` to request one of four outcomes with clear examples. Replace "If in doubt, classify as sdlc" with "If in doubt, classify as collaboration"
- Update the result-parsing logic in `_classify_work_request_llm()` to recognize `collaboration` and `other` responses from both Ollama and Haiku
- In `sdk_client.py`, wrap the SDLC orchestration block (lines 1615-1664) in a conditional: only inject when `_classification_context == ClassificationType.SDLC`. For `collaboration`/`other`, inject a shorter direct-action prompt
- Update `config/personas/project-manager.md` with a new "## Available Tools" section listing all PM-accessible tools
- Update `~/Desktop/Valor/personas/project-manager.md` with the same tool inventory (manual step, file not in git)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_classify_work_request_llm()` has `except Exception` blocks for both Ollama and Haiku -- existing tests cover fallback behavior. New classification values must also fall through correctly on parse failure
- [ ] If the LLM returns an unrecognized word, the existing fallback returns `ClassificationType.QUESTION` -- this remains correct

### Empty/Invalid Input Handling
- [ ] `classify_work_request("")` and `classify_work_request(None)` already return `"passthrough"` -- no change needed
- [ ] LLM returning empty string or garbage should still fall through to `QUESTION` default

### Error State Rendering
- [ ] No user-visible output from the classifier -- errors are logged and fallback is used

## Test Impact

- [ ] `tests/unit/test_enums.py::TestClassificationType::test_all_members` -- UPDATE: change assertion from `len == 2` to `len == 4`, add string equality tests for COLLABORATION and OTHER
- [ ] `tests/unit/test_enums.py::TestClassificationType::test_string_equality` -- UPDATE: add assertions for new enum members
- [ ] `tests/unit/test_work_request_classifier.py::TestLlmClassification` -- UPDATE: add parametrized cases for messages that should classify as `collaboration` (e.g., "Add this to the knowledge base", "Draft an issue for X")

## Rabbit Holes

- Refactoring the `passthrough` string to use the enum -- separate cleanup, explicitly out of scope per issue #846 recon
- Adding confidence scores or multi-label classification -- unnecessary complexity for this appetite
- Changing Teammate mode routing -- Teammate mode is orthogonal and untouched
- Rewriting the Ollama/Haiku fallback chain -- the existing dual-backend approach works fine

## Risks

### Risk 1: LLM misclassifies collaboration tasks as sdlc
**Impact:** PM still spawns unnecessary dev-sessions (status quo, not a regression)
**Mitigation:** Change default from "sdlc" to "collaboration" in the prompt. Monitor classification logs after deployment. Can tune prompt examples without code changes.

### Risk 2: LLM misclassifies sdlc tasks as collaboration
**Impact:** PM tries to handle a coding task directly instead of spawning a dev-session, resulting in incomplete work
**Mitigation:** The prompt examples clearly distinguish code-change work from tool-use work. The PM still has judgment to spawn a dev-session if needed. The `other` bucket provides an escape valve for ambiguous cases.

## Race Conditions

No race conditions identified -- classification is a synchronous, stateless function call with no shared mutable state.

## No-Gos (Out of Scope)

- Fixing the `passthrough` string/enum inconsistency (separate cleanup issue)
- Changing Teammate mode behavior or routing
- Adding confidence thresholds or multi-step classification
- Modifying the Ollama/Haiku fallback chain architecture
- Adding new MCP servers or tools -- this only documents existing tools in the persona

## Update System

The private PM persona file (`~/Desktop/Valor/personas/project-manager.md`) lives outside git on each machine. The update skill does not manage it. After this change ships:
- The public template at `config/personas/project-manager.md` is updated automatically via git pull
- The private persona file must be manually updated on each machine, or the operator can delete it so the public template is used as fallback
- No changes to the update script or update skill are needed

## Agent Integration

No agent integration required -- this is a bridge/routing-internal change. The classifier and SDK client are bridge infrastructure, not agent-facing tools. No MCP server changes needed. No `.mcp.json` changes needed.

## Documentation

- [ ] Create `docs/features/pm-routing-classification.md` describing the four-bucket classification system
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings on `classify_work_request()` and `_classify_work_request_llm()` to reflect four outcomes

## Success Criteria

- [ ] `ClassificationType` enum has `COLLABORATION` and `OTHER` members
- [ ] `classify_work_request()` LLM prompt requests one of four outcomes with clear examples
- [ ] PM sessions classified as `collaboration` or `other` receive a direct-action prompt, NOT the SDLC orchestration instructions
- [ ] PM sessions classified as `sdlc` receive the current SDLC orchestration instructions unchanged
- [ ] PM persona (both public template and private file) documents: memory_search, work-vault path, valor_session, gws, officecli
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement enum extension, classifier prompt update, conditional dispatch, persona update
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify backward compatibility and correct classification behavior
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
- Update `tests/unit/test_enums.py::TestClassificationType` to assert `len == 4` and test string equality for new members

### 2. Update Classifier Prompt and Parsing
- **Task ID**: build-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_work_request_classifier.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite the LLM prompt in `_classify_work_request_llm()` to request one of four outcomes: `sdlc`, `collaboration`, `other`, `question`
- Add clear examples for `collaboration`: "Add this to the knowledge base", "Draft an issue for X", "Send a status update", "Write a doc about Y", "Save this file"
- Add clear examples for `other`: ambiguous requests that need PM judgment
- Replace "If in doubt, classify as sdlc" with "If in doubt, classify as collaboration"
- Update result-parsing logic to recognize `collaboration` and `other` in both Ollama and Haiku response handlers
- Add test cases to `tests/unit/test_work_request_classifier.py` for collaboration-type messages

### 3. Make PM Dispatch Instructions Conditional
- **Task ID**: build-dispatch
- **Depends On**: build-enum
- **Validates**: tests/unit/test_pm_session_factory.py, tests/unit/test_sdlc_mode.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/sdk_client.py`, wrap the SDLC orchestration block (lines 1615-1664) in a conditional: only inject when `_classification_context == ClassificationType.SDLC`
- For `collaboration` or `other` classifications, inject a shorter direct-action prompt: "Handle this task directly using your available tools. You have access to: GitHub CLI, memory search, Google Workspace, Office CLI, Telegram, and the work vault. No dev-session needed unless you determine the task requires code changes."
- Ensure `passthrough` and `question` classifications continue to work as before (passthrough gets no extra injection, question gets no SDLC instructions)

### 4. Update PM Persona Documents
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: manual verification
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "## Available Tools" section to `config/personas/project-manager.md` listing:
  - `python -m tools.memory_search save/search/inspect/forget` -- memory and knowledge base
  - `python -m tools.valor_session list/status/steer/kill/create` -- session management
  - `python -m tools.agent_session_scheduler status/list/kill/cleanup` -- queue management
  - `gws <service> <resource> <method>` -- Google Workspace CLI (Gmail, Calendar, Drive, etc.)
  - `officecli <command> <file>` -- Office document creation and editing
  - Work vault path: `~/src/work-vault/` for project knowledge
  - `gh` -- GitHub CLI for issues, PRs, and repo management
  - `python tools/send_telegram.py` -- Telegram messaging
- Add a note in the plan for the builder to echo the same content into `~/Desktop/Valor/personas/project-manager.md` (the private persona file)

### 5. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: build-classifier, build-dispatch, build-persona
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_enums.py tests/unit/test_work_request_classifier.py tests/unit/test_pm_session_factory.py tests/unit/test_sdlc_mode.py -v`
- Verify backward compatibility: messages that previously classified as `sdlc` still classify as `sdlc`
- Verify the SDLC orchestration prompt is unchanged for `sdlc`-classified sessions
- Verify `collaboration`/`other` sessions get the direct-action prompt

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-routing-classification.md` describing the four-bucket system
- Add entry to `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_enums.py tests/unit/test_work_request_classifier.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check config/enums.py bridge/routing.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check config/enums.py bridge/routing.py agent/sdk_client.py` | exit code 0 |
| Enum has 4 members | `python -c "from config.enums import ClassificationType; assert len(list(ClassificationType)) == 4"` | exit code 0 |
| Collaboration in enum | `python -c "from config.enums import ClassificationType; assert ClassificationType.COLLABORATION == 'collaboration'"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | [agent-type] | [The concern raised] | [How/whether addressed] | [Guard condition or gotcha] |

---

## Open Questions

1. Should the `other` bucket inject the direct-action prompt (same as `collaboration`) or a hybrid prompt that mentions dev-session as an option? The current plan uses the same direct-action prompt for both, relying on PM judgment.
2. For the private persona file at `~/Desktop/Valor/personas/project-manager.md` -- should the builder attempt to update it directly, or just document the required changes for manual application? (The file is outside git and machine-specific.)
