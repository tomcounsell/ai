---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/846
last_comment_id: 4212516783
---

# PM Routing: Add Collaboration and Other Classifier Buckets

## Problem

The PM work-request classifier only distinguishes two LLM outcomes: "sdlc" (code work) and "question" (informational). Tasks the PM could handle directly -- saving to knowledge base, drafting issues, writing docs, sending updates -- get funneled into a full dev-session SDLC pipeline, wasting 30-60 minutes on work that should take under a minute.

**Current behavior:**

Two classifiers operate in sequence:
1. `bridge/routing.py::classify_work_request()` -- bridge-level classifier with two outcomes: "sdlc" and "question". Result stored as `classification_type` on the AgentSession.
2. `agent/intent_classifier.py::classify_intent()` -- agent-level classifier with two outcomes: "teammate" (informational) and "work" (action request). This is the **actual PM dispatch decision** in `agent/sdk_client.py` lines 1560-1595.

The bridge-level classification determines working directory (ai/ repo vs project repo) and cross-repo routing. The intent classifier determines whether the PM gets SDLC orchestration instructions or Teammate instructions. Neither classifier has a "collaboration" bucket -- work that the PM should handle directly without spawning a dev-session.

Additionally, config-driven PM/Dev groups (`resolve_persona()` returning `PersonaType.PROJECT_MANAGER` or `PersonaType.DEVELOPER`) bypass **both** classifiers entirely at `sdk_client.py` lines 1556-1559 and always receive SDLC orchestration instructions.

Concrete failure: "Add this to knowledge base and draft an issue" was classified as sdlc (bridge) then as work (intent), triggering a full dev-session. The PM could have done this in two direct tool calls.

**Desired outcome:**

- Tasks like "save this to the knowledge base", "draft an issue", "write a status doc" route to `collaboration` and the PM executes them directly.
- Ambiguous tasks route to `other` -- PM uses judgment without SDLC overhead.
- PM persona documents all available tools so it can actually handle direct work.

## Prior Art

- **PR #387**: Add PM channel mode -- established the PM/Teammate split but left all non-Teammate work routed to full SDLC pipeline
- **PR #228**: SDLC-first architecture -- created the bridge classifier with only sdlc/question outcomes
- **PR #602**: Agent-controlled message delivery -- added classification context pass-through but did not expand classification types
- **Issue #846 comment (hotfix 370fd895)**: Fixed `session_mode` racing with `session_type` for PM sessions created via `valor-session create`. The root cause -- `session_mode` as a mutable field -- is acknowledged but out of scope for this plan.

No prior attempt to add collaboration/other buckets. This is greenfield within the classifier.

## Data Flow

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py`
2. **Bridge classification**: `bridge/routing.py::classify_work_request()` runs fast-path checks, then calls `_classify_work_request_llm()` which prompts Ollama/Haiku with two outcomes ("sdlc" or "question"). Result stored as `classification_type` on the AgentSession.
3. **SDK client receives message**: `agent/sdk_client.py::send_to_agent()` reads `classification_type` from the session (lines 1429-1441). This determines working directory and cross-repo routing.
4. **Persona resolution**: `resolve_persona()` checks config for the chat group (lines 1521-1559):
   - If TEAMMATE: skip intent classifier, set `_teammate_mode = True`
   - If PROJECT_MANAGER or DEVELOPER: skip intent classifier, fall through to SDLC orchestration **unconditionally** (this is where config-driven groups bypass classification)
   - If unconfigured: fall through to intent classifier
5. **Intent classification** (unconfigured groups only): `agent/intent_classifier.py::classify_intent()` runs Haiku with two outcomes ("teammate" or "work"). If `is_teammate` (confidence >= 0.90): set `_teammate_mode = True`.
6. **PM guard**: Lines 1599-1612 force `_teammate_mode = False` for PM sessions (session_type == "pm"), preventing PM sessions from entering Teammate mode.
7. **Dispatch decision** (lines 1621-1676): If `_teammate_mode`: inject Teammate instructions. Else: inject SDLC orchestration instructions unconditionally.

**After this change:**
- Step 5 gains a third intent: "collaboration". Messages classified as "collaboration" by `classify_intent()` set a `_collaboration_mode` flag.
- Step 7 becomes a three-way branch: Teammate instructions, collaboration/direct-action instructions, or SDLC orchestration.
- Config-driven PM groups (step 4) also check `classification_type` from the bridge classifier -- if the bridge classified the message as "collaboration", the PM gets direct-action instructions instead of unconditional SDLC.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Five files changed (enum, bridge classifier prompt, intent classifier prompt, SDK client conditional, persona template). No new modules, no new dependencies, no architectural changes.

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing Python files and markdown templates.

## Solution

### Key Elements

- **ClassificationType enum extension**: Add COLLABORATION and OTHER members to the existing StrEnum in `config/enums.py`
- **Bridge classifier prompt update**: Four-outcome prompt in `bridge/routing.py::_classify_work_request_llm()` with clear examples
- **Intent classifier prompt update**: Three-outcome prompt in `agent/intent_classifier.py` adding "collaboration" intent alongside "teammate" and "work"
- **Conditional PM dispatch**: `agent/sdk_client.py` checks intent result and bridge classification to branch between SDLC orchestration, collaboration/direct-action, and Teammate instructions
- **PM persona tool visibility**: Document memory_search, work-vault, valor_session, gws, officecli in the persona template

### Flow

**Telegram message** -> bridge `classify_work_request()` -> [sdlc|collaboration|other|question] stored on session -> `sdk_client` reads session classification -> `classify_intent()` -> [teammate|collaboration|work] -> three-way dispatch

For `collaboration` (either classifier): PM gets direct-action prompt, handles task with available tools, no dev-session spawned.
For `other` (bridge): PM gets direct-action prompt with judgment discretion -- may spawn dev-session or handle directly.
For `sdlc`/`work`: Unchanged behavior -- full SDLC orchestration instructions.
For `teammate`: Unchanged behavior -- Teammate instructions.

### Technical Approach

1. **Extend `ClassificationType`** in `config/enums.py` with `COLLABORATION = "collaboration"` and `OTHER = "other"`
2. **Update bridge classifier** (`bridge/routing.py::_classify_work_request_llm()`):
   - Rewrite prompt to request one of four outcomes: sdlc, collaboration, other, question
   - Keep default as "If in doubt, classify as sdlc" (unchanged -- see Critique Results for rationale)
   - Update result parsing to detect "collaboration" and "other" in LLM responses
3. **Update intent classifier** (`agent/intent_classifier.py`):
   - Add "collaboration" as a third intent alongside "teammate" and "work"
   - Add examples: "Add this to the knowledge base" -> collaboration 0.97, "Draft an issue for X" -> collaboration 0.96, "Send a status update" -> collaboration 0.95
   - Add `is_collaboration` property to `IntentResult`
   - Keep "work" as default for unparseable/low-confidence responses (fail-safe unchanged)
4. **Update SDK client dispatch** (`agent/sdk_client.py`):
   - In the unconfigured-group path (lines 1560-1595): after `classify_intent()`, check `_intent_result.is_collaboration` and set `_collaboration_mode = True`
   - In the config-driven PM/Dev path (lines 1556-1559): check the bridge-level `classification` variable -- if it equals `ClassificationType.COLLABORATION`, set `_collaboration_mode = True`
   - In the dispatch block (lines 1621-1676): add a middle branch: if `_collaboration_mode` (and not `_teammate_mode`), inject direct-action instructions
   - The PM guard (lines 1599-1612) does not need changes -- it only affects `_teammate_mode`
5. **Update PM persona template** (`config/personas/project-manager.md`) with full tool reference section

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_classify_work_request_llm()` has try/except blocks for Ollama and Haiku fallbacks -- existing tests cover fallback behavior; new tests verify collaboration/other parsing within the same error handling
- [ ] `classify_intent()` has try/except around the Anthropic API call -- existing fail-safe returns "work" intent; new "collaboration" intent follows the same error handling (falls back to "work", not "collaboration")
- [ ] If LLM returns an unrecognized word in either classifier, the fallback defaults are unchanged (bridge: "question", intent: "work")

### Empty/Invalid Input Handling
- [ ] `classify_work_request("")` and `classify_work_request(None)` already return "passthrough" -- unchanged by this work
- [ ] `classify_intent("")` with empty message still produces a valid IntentResult -- unchanged behavior
- [ ] New classification types only apply to the LLM path, which requires non-empty text

### Error State Rendering
- [ ] If classification fails entirely, the fallback is "question" (bridge) or "work" (intent) -- no SDLC overhead change
- [ ] No user-visible output changes; routing is invisible to the end user

## Test Impact

- [ ] `tests/unit/test_enums.py::TestClassificationType::test_all_members` -- UPDATE: change assertion from `len == 2` to `len == 4`
- [ ] `tests/unit/test_enums.py::TestClassificationType::test_string_equality` -- UPDATE: add assertions for COLLABORATION and OTHER
- [ ] `tests/unit/test_sdlc_mode.py::TestIsSdlcJobClassificationType::test_classification_type_chat_returns_false` -- REVIEW: verify "collaboration" and "other" classification_type values also return `is_sdlc == False`
- [ ] `tests/unit/test_sdlc_mode.py` -- UPDATE: add parametrized cases for "collaboration" and "other" classification types confirming they are not SDLC

`tests/unit/test_work_request_classifier.py` and `tests/unit/test_intent_classifier.py` already exist and will be updated (not created from scratch). `test_pm_channels.py` and `test_pm_session_factory.py` are new test files to be created.

## Rabbit Holes

- Fixing the passthrough string/enum inconsistency (passthrough is returned as a raw string, not a ClassificationType member) -- separate cleanup, explicitly out of scope per issue #846
- Adding sub-categories within collaboration (e.g., "knowledge-base-write" vs "issue-draft") -- over-engineering for this appetite
- Modifying the Teammate mode routing logic -- Teammate mode is a separate persona path, not affected by this work
- Updating `tools/classifier.py` (the separate request classifier used by other systems) -- that is a different classification system
- Eliminating `session_mode` as a mutable field (per issue #846 comment) -- already hotfixed in 370fd895, root cause fix is separate work

## Risks

### Risk 1: LLM misclassifies sdlc work as collaboration
**Impact:** PM tries to handle a coding task directly, fails or produces poor results
**Mitigation:** The bridge-level default remains "If in doubt, classify as sdlc" (unchanged). The intent classifier default remains "work" for low-confidence/unparseable responses. The direct-action instructions include an explicit fallback: "If you determine this task requires code changes to the repository, spawn a dev-session." Double-classification (bridge + intent) provides two chances to catch misroutes.

### Risk 2: Config-driven PM groups still bypass intent classifier
**Impact:** Config-driven PM groups only see bridge-level classification, not intent classification
**Mitigation:** The plan explicitly addresses this: config-driven PM/Dev groups check the bridge-level `classification` variable for COLLABORATION. This is a weaker signal than the intent classifier (bridge uses smaller model, shorter prompt), but it catches obvious collaboration requests. For config-driven groups, the PM's own judgment ("use your judgment" advisory) is the final safety net.

### Risk 3: Private PM persona file out of sync
**Impact:** The private `~/Desktop/Valor/personas/project-manager.md` does not get the tool visibility update on all machines
**Mitigation:** The plan includes an explicit step to update the private persona. The public template at `config/personas/project-manager.md` serves as the authoritative reference. The `/update` skill propagates the public template.

## Race Conditions

No race conditions identified -- classification is synchronous and single-request scoped. The classification result is passed as a string through the session creation flow with no shared mutable state. The intent classifier runs async but is awaited before dispatch decisions are made.

## No-Gos (Out of Scope)

- Fixing passthrough string/enum inconsistency (separate cleanup)
- Sub-categorizing collaboration types
- Modifying Teammate mode routing or Teammate persona
- Changing `tools/classifier.py` (separate classification system)
- Adding new MCP servers or tools -- this only documents existing tools in the persona
- Eliminating `session_mode` mutability (acknowledged per issue comment, separate work)
- Changing the bridge-level fallback default from "sdlc" (kept as-is for safety)

## Update System

No update system changes required -- this feature modifies existing Python files and a persona template that are already part of the standard git pull. The `/update` skill's existing `git pull` step propagates all changes. The private persona file at `~/Desktop/Valor/personas/project-manager.md` must be manually updated on each machine (documented in the build tasks).

## Agent Integration

No agent integration required -- this is a bridge-internal routing change. The classifiers run inside `bridge/routing.py` and `agent/intent_classifier.py`, and the instruction injection happens in `agent/sdk_client.py`. No new MCP servers, no `.mcp.json` changes, no new tools. The PM persona update documents existing tools that are already available to the agent.

## Documentation

- [ ] Create `docs/features/pm-routing-collaboration.md` describing the four-way bridge classification, three-way intent classification, and conditional dispatch
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline docstrings for `classify_work_request()`, `_classify_work_request_llm()`, `classify_intent()`, and `IntentResult`

## Success Criteria

- [ ] `ClassificationType` enum has 4 members: SDLC, QUESTION, COLLABORATION, OTHER
- [ ] Bridge classifier LLM prompt requests one of four outcomes with clear examples
- [ ] Bridge classifier default remains "If in doubt, classify as sdlc" (unchanged)
- [ ] Intent classifier prompt has three outcomes: teammate, collaboration, work
- [ ] `IntentResult` has `is_collaboration` property
- [ ] PM sessions with collaboration intent get direct-action instructions, not SDLC orchestration
- [ ] PM sessions with work intent get unchanged SDLC orchestration instructions
- [ ] Config-driven PM groups check bridge-level classification for collaboration
- [ ] PM persona template documents: memory_search, work-vault path, valor_session, gws, officecli
- [ ] "Add this to knowledge base and draft an issue" classifies as collaboration (intent classifier)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement enum extension, both classifier prompts, conditional dispatch, persona update
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

### 2. Update Bridge Classifier LLM Prompt
- **Task ID**: build-bridge-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_work_request_classifier.py (update)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite prompt in `_classify_work_request_llm()` to request one of four outcomes: sdlc, collaboration, other, question
- Add clear examples for collaboration: "Add this to the knowledge base", "Draft an issue for X", "Send a status update", "Write a doc about Y", "Save this file"
- Keep default as "If in doubt, classify as sdlc" (unchanged for safety)
- Update result parsing to detect "collaboration" and "other" in LLM responses, returning `ClassificationType.COLLABORATION` and `ClassificationType.OTHER`
- Create `tests/unit/test_work_request_classifier.py` with parametrized test cases for all four classification outcomes

### 3. Update Intent Classifier
- **Task ID**: build-intent-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_intent_classifier.py (update)
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-bridge-classifier)
- Add "collaboration" as a third intent in `agent/intent_classifier.py::CLASSIFIER_PROMPT`
- Add collaboration examples to the prompt: "Add this to the knowledge base" -> collaboration 0.97, "Draft an issue for X" -> collaboration 0.96, "Send a status update to the team" -> collaboration 0.95, "Write a summary doc" -> collaboration 0.94, "Save this to memory" -> collaboration 0.98
- Update `_parse_classifier_response()` to accept "collaboration" as a valid intent (add to the valid set alongside "teammate" and "work")
- Add `is_collaboration` property to `IntentResult`: returns `True` when `intent == "collaboration"` and `confidence >= TEAMMATE_CONFIDENCE_THRESHOLD`
- Keep "work" as the default for unparseable/unknown intents (fail-safe unchanged)
- Create `tests/unit/test_intent_classifier.py` testing: parse of collaboration intent, `is_collaboration` property, unknown intent defaults to work

### 4. Make PM Dispatch Instructions Conditional
- **Task ID**: build-dispatch
- **Depends On**: build-bridge-classifier, build-intent-classifier
- **Validates**: tests/unit/test_sdlc_mode.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- **In the unconfigured-group path** (sdk_client.py lines 1560-1595): after `classify_intent()`, check `_intent_result.is_collaboration` and set `_collaboration_mode = True`
- **In the config-driven PM/Dev path** (sdk_client.py lines 1556-1559): after setting `_classification_context`, check the bridge-level `classification` variable -- if `classification == ClassificationType.COLLABORATION`, set `_collaboration_mode = True`
- **In the dispatch block** (sdk_client.py lines 1621-1676): add a middle branch between the `_teammate_mode` check and the SDLC else block:
  ```
  if _teammate_mode:
      # Teammate instructions (unchanged)
  elif _collaboration_mode:
      # Direct-action instructions for collaboration
  else:
      # SDLC orchestration (unchanged)
  ```
- The direct-action instructions should say: "Handle this task directly using your available tools. You have access to Bash, file operations, GitHub CLI, Google Workspace, memory search, and Office CLI. No dev-session needed unless you determine the task requires code changes to the repository."
- Preserve the Telegram messaging instructions for all PM paths (append after the collaboration block too)
- Update `tests/unit/test_sdlc_mode.py` to add parametrized cases for "collaboration" and "other" classification types confirming they are not SDLC

### 5. Update PM Persona Template
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

### 6. Validate End-to-End
- **Task ID**: validate-all
- **Depends On**: build-dispatch, build-persona
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_enums.py tests/unit/test_sdlc_mode.py -v`
- Verify `ClassificationType` has 4 members
- Verify intent classifier prompt contains "collaboration" examples
- Verify bridge classifier prompt contains all four outcomes
- Verify sdk_client.py three-way dispatch logic (teammate / collaboration / sdlc)
- Run full test suite: `pytest tests/unit/ -x -q`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-routing-collaboration.md`
- Add entry to `docs/features/README.md` index table
- Update docstrings in `bridge/routing.py`, `agent/intent_classifier.py`, and `agent/sdk_client.py`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_enums.py tests/unit/test_sdlc_mode.py -v` | exit code 0 |
| Enum has 4 members | `python -c "from config.enums import ClassificationType; assert len(list(ClassificationType)) == 4"` | exit code 0 |
| Bridge classifier has collaboration | `grep -c 'collaboration' bridge/routing.py` | output > 0 |
| Intent classifier has collaboration | `grep -c 'collaboration' agent/intent_classifier.py` | output > 0 |
| Conditional dispatch exists | `grep -c 'collaboration_mode' agent/sdk_client.py` | output > 0 |
| Persona has tools section | `grep -c 'memory_search' config/personas/project-manager.md` | output > 0 |
| Lint clean | `python -m ruff check config/enums.py bridge/routing.py agent/intent_classifier.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check config/enums.py bridge/routing.py agent/intent_classifier.py agent/sdk_client.py` | exit code 0 |

## Critique Results

### Round 1 (2026-04-09) -- NEEDS REVISION (2 blockers). Revised 2026-04-09.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Archaeologist | Plan targets wrong dispatch mechanism -- two separate classifiers exist. `_classification_context` is a descriptive string, not a `ClassificationType` enum, so the proposed check would never match. The actual PM-vs-Teammate decision is in `agent/intent_classifier.py::classify_intent()` via `_teammate_mode` flag. | **RESOLVED (v2)**: Plan now targets both classifiers. Task 3 adds "collaboration" intent to `intent_classifier.py`. Task 4 checks `_intent_result.is_collaboration` (not `_classification_context` string). | Dispatch uses new `_collaboration_mode` flag set from either intent classifier result or bridge classification. |
| BLOCKER | Skeptic, Operator | `agent/intent_classifier.py` missing from touched-files inventory and data flow. This file controls PM dispatch for unconfigured groups. | **RESOLVED (v2)**: `agent/intent_classifier.py` added to Data Flow (step 5), Technical Approach (point 3), Task 3, and Verification table. | File is now a primary modification target alongside bridge/routing.py. |
| CONCERN | Operator, Adversary | Config-driven PM/Dev groups (via `resolve_persona()`) bypass both classifiers entirely at sdk_client.py lines 1556-1559. The new classification would be stored but never consulted. | **RESOLVED (v2)**: Task 4 explicitly adds a bridge-level `classification` check inside the config-driven PM/Dev path. If bridge classified as COLLABORATION, `_collaboration_mode = True`. | Weaker signal than intent classifier (bridge uses smaller model) but catches obvious cases. PM judgment is the final safety net. |
| CONCERN | Skeptic, Adversary | Fallback default changed from `sdlc` to `collaboration` without safety analysis. Flips failure mode unsafely. | **RESOLVED (v2)**: Bridge default kept as "If in doubt, classify as sdlc" (unchanged). Intent classifier default kept as "work" for unparseable/low-confidence. No default changes. | Added to Success Criteria and No-Gos for explicit tracking. |
| NIT | Operator | Test Impact section omits `test_sdlc_mode.py` which is referenced as validation target in Task 3. | **RESOLVED (v2)**: `test_sdlc_mode.py` added to Test Impact with UPDATE disposition. Clarified that `test_work_request_classifier.py`, `test_pm_channels.py`, and `test_pm_session_factory.py` are new files to create, not existing files to update. | All referenced test files now appear in Test Impact with correct dispositions. |

### Round 2 (2026-04-09) -- APPROVED (0 blockers, 2 concerns, 1 nit)

| Severity | Critic | Finding | Implementation Note |
|----------|--------|---------|---------------------|
| CONCERN | Adversary, Skeptic | Bridge classifier result parsing order not specified for four outcomes. Substring matching (`if "collaboration" in result`) risks collision when LLM returns multi-word text (e.g., "not collaboration, classify as sdlc"). | **ACCEPT**: In Task 2, use first-token extraction (`result = result.split()[0]`) then exact match (`result == "collaboration"`) instead of substring matching. Apply to both Ollama path (line 541) and Haiku path (line 564). Eliminates substring collision entirely. |
| CONCERN | Simplifier | `OTHER` classification type has no distinct behavioral path. Bridge `OTHER` falls through to intent classifier which has no "other" intent, making it meaningless. | **ACCEPT**: In Task 4, treat `OTHER` same as `COLLABORATION` in the config-driven path: `if classification in (ClassificationType.COLLABORATION, ClassificationType.OTHER)`. For unconfigured groups, `OTHER` from bridge is irrelevant since intent classifier makes the dispatch decision. |
| NIT | Operator | Test Impact section claims `test_work_request_classifier.py` and `test_intent_classifier.py` are "new files to create" but they already exist with substantive coverage. | **ACCEPT**: Task 2 Validates changed to "tests/unit/test_work_request_classifier.py (update)" and Task 3 Validates changed to "tests/unit/test_intent_classifier.py (update)". |

---

## Open Questions

No open questions -- all critique blockers resolved in round 1, round 2 concerns accepted with implementation notes. Plan is approved for build.
