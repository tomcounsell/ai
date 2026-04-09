---
status: Building
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

**Two classifiers exist in the dispatch chain:**

1. **Bridge-level classifier** (`bridge/routing.py:classify_work_request()`): Runs at message arrival. Returns `ClassificationType` (sdlc/question/passthrough). Result is stored on `AgentSession.classification_type` and used for `is_sdlc` detection and working directory selection (`sdk_client.py` lines 1420-1468). This classifier does NOT control PM dispatch behavior.

2. **Intent classifier** (`agent/intent_classifier.py:classify_intent()`): Runs inside `sdk_client.py` (lines 1560-1596) for unconfigured groups. Returns `IntentResult` with intent "teammate" or "work". Controls the `_teammate_mode` flag which determines whether the PM gets Teammate instructions (direct response) or full SDLC orchestration instructions (lines 1621-1676). Config-driven PM groups (lines 1556-1559) skip this classifier and fall through to the SDLC orchestration block unconditionally.

**Current behavior:**

For unconfigured groups, `classify_intent()` returns either "teammate" (routed to Teammate handler) or "work" (routed to SDLC orchestration). There is no middle ground. A message like "Add this to the knowledge base and draft an issue" gets classified as "work" with high confidence (~0.97) and the PM spawns a full dev-session that runs for 60 minutes, makes 1 tool call, and times out.

For config-driven PM groups (`resolve_persona()` returns `PersonaType.PROJECT_MANAGER`), the intent classifier is skipped entirely (line 1556-1559). The code falls through to the `else` block at line 1626, which unconditionally injects SDLC orchestration instructions. There is no path to direct-action handling for these groups.

**Desired outcome:**

- Tasks like "save this to the knowledge base", "draft an issue", "write a status doc" route to `collaboration` and the PM executes them directly.
- Ambiguous tasks route to `other` -- PM uses judgment without SDLC overhead.
- PM persona documents all available tools so it can actually handle direct work.

## Prior Art

- **PR #387**: Add PM channel mode -- established the PM/Teammate split but left all non-Teammate work routed to full SDLC pipeline
- **PR #228**: SDLC-first architecture -- created the bridge-level classifier with only sdlc/question outcomes
- **PR #602**: Agent-controlled message delivery -- added classification context pass-through but did not expand classification types
- **PR #541**: Dynamic PM persona -- established intent classifier for Teammate mode routing

No prior attempt to add collaboration/other buckets. This is greenfield within the intent classifier.

## Data Flow

### Current flow (showing both classifiers):

```
Telegram message
  -> bridge/routing.py:classify_work_request()
      -> stores ClassificationType on AgentSession.classification_type
      -> feeds is_sdlc property and working_dir selection (sdk_client.py:1420-1468)
  -> sdk_client.py session dispatch:
      -> resolve_persona() checks config
          -> TEAMMATE persona -> _teammate_mode=True (skip intent classifier)
          -> PM/DEV persona -> skip intent classifier, fall to SDLC orchestration
          -> None (unconfigured) -> agent/intent_classifier.py:classify_intent()
              -> "teammate" (conf >= 0.90) -> _teammate_mode=True
              -> "work" -> _teammate_mode=False -> SDLC orchestration
      -> _teammate_mode=True -> Teammate handler (direct response)
      -> _teammate_mode=False -> SDLC orchestration instructions (lines 1628-1676)
```

### After this change:

```
Telegram message
  -> bridge/routing.py:classify_work_request()
      -> stores ClassificationType on AgentSession.classification_type  [EXTENDED: +collaboration, +other]
      -> feeds is_sdlc property and working_dir selection (unchanged)
  -> sdk_client.py session dispatch:
      -> resolve_persona() checks config
          -> TEAMMATE persona -> _teammate_mode=True (unchanged)
          -> PM/DEV persona -> check AgentSession.classification_type  [NEW]
              -> COLLABORATION/OTHER -> inject direct-action prompt  [NEW]
              -> SDLC/None -> SDLC orchestration (unchanged)
          -> None (unconfigured) -> agent/intent_classifier.py:classify_intent()  [EXTENDED]
              -> "teammate" (conf >= 0.90) -> Teammate handler (unchanged)
              -> "collaboration" -> inject direct-action prompt  [NEW]
              -> "other" -> inject direct-action prompt  [NEW]
              -> "work" -> SDLC orchestration (unchanged)
      -> dispatch path selected by _teammate_mode + _collaboration_mode flags
```

**Key changes in the flow:**
1. **Intent classifier** (`agent/intent_classifier.py`): PRIMARY change target. Prompt extended from 2 intents to 4. Parser whitelist extended. New `IntentResult` properties added.
2. **Bridge-level classifier** (`bridge/routing.py`): SECONDARY change. Prompt extended for observability. Result stored on AgentSession but does NOT drive PM dispatch.
3. **SDK client dispatch** (`agent/sdk_client.py`): Adds a new `_collaboration_mode` flag. For config-driven PM groups, checks `AgentSession.classification_type`. For unconfigured groups, checks intent classifier result. Collaboration/other routes to direct-action prompt.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Five files changed (enum, intent classifier, bridge classifier, SDK client conditional, persona template). No new modules, no new dependencies, no architectural changes.

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing Python files and markdown templates.

## Solution

### Key Elements

- **ClassificationType enum extension**: Add COLLABORATION and OTHER members to the existing StrEnum (observability, stored on AgentSession)
- **Intent classifier prompt update** (PRIMARY): Four-outcome LLM prompt replacing the binary teammate/work split, with "if in doubt, classify as collaboration" default
- **Intent classifier parser update**: Extend `_parse_classifier_response()` whitelist to accept "collaboration" and "other"; add `is_collaboration` and `is_other` properties to `IntentResult`
- **Bridge-level classifier prompt update** (SECONDARY): Four-outcome prompt for observability; result stored on AgentSession but does not drive PM dispatch
- **Conditional PM dispatch**: SDK client adds `_collaboration_mode` flag; for config-driven PM groups, checks `AgentSession.classification_type`; for unconfigured groups, checks intent classifier result
- **PM persona tool visibility**: Document memory_search, work-vault, valor_session, gws, officecli in the persona template

### Flow

**Telegram message** -> classify_work_request() stores classification on AgentSession -> sdk_client checks intent classifier OR AgentSession.classification_type -> [sdlc|collaboration|other|question|teammate] -> dispatch path selected

For `collaboration`: PM gets direct-action prompt, handles task with available tools, no dev-session spawned.
For `other`: PM gets direct-action prompt with judgment discretion -- may spawn dev-session or handle directly.
For `sdlc`: Unchanged behavior -- full SDLC orchestration instructions.
For `teammate`: Unchanged behavior -- Teammate handler.

### Technical Approach

**File 1: `agent/intent_classifier.py` (PRIMARY)**
- Extend `CLASSIFIER_PROMPT` from 2 intents (teammate/work) to 4 intents (teammate/collaboration/other/work)
- Add collaboration examples: "Add this to the knowledge base", "Draft an issue for X", "Send a status update", "Write a doc about Y", "Save this file", "Create a summary"
- Add other examples: "Let's think about this", "What should we do about X?", "I have an idea for Y"
- Keep work examples focused on code/PR/deploy tasks
- Change default from "If in doubt, classify as work" to "If in doubt, classify as collaboration"
- Extend `_parse_classifier_response()` whitelist at line 97 from `("teammate", "work")` to `("teammate", "work", "collaboration", "other")`
- Add `IntentResult.is_collaboration` property: returns `True` when intent is "collaboration"
- Add `IntentResult.is_other` property: returns `True` when intent is "other"
- Update `IntentResult.is_work` property: only return `True` when intent is literally "work" (currently returns True for anything non-teammate; this must narrow to exclude collaboration/other)

**File 2: `config/enums.py`**
- Add `COLLABORATION = "collaboration"` and `OTHER = "other"` to `ClassificationType`

**File 3: `bridge/routing.py` (SECONDARY)**
- Extend `_classify_work_request_llm()` prompt to request one of four outcomes: sdlc, collaboration, other, question
- Add collaboration examples matching intent classifier examples
- Change default from "If in doubt, classify as sdlc" to "If in doubt, classify as collaboration"
- Update result parsing to detect "collaboration" and "other", returning `ClassificationType.COLLABORATION` and `ClassificationType.OTHER`

**File 4: `agent/sdk_client.py`**
- Add `_collaboration_mode = False` flag alongside `_teammate_mode`
- For config-driven PM/Dev groups (lines 1556-1559): after setting `_classification_context`, read `AgentSession.classification_type` from Redis. If it is COLLABORATION or OTHER, set `_collaboration_mode = True`
- For unconfigured groups (lines 1560-1596): after `classify_intent()` returns, check `_intent_result.is_collaboration` or `_intent_result.is_other`. If so, set `_collaboration_mode = True`
- At dispatch (line 1621+): add a third branch: `if _teammate_mode -> Teammate handler; elif _collaboration_mode -> direct-action prompt; else -> SDLC orchestration`
- The direct-action prompt instructs the PM: "Handle this task directly using your available tools. You have access to Bash, file operations, GitHub CLI (gh), Google Workspace (gws), memory search, and Office CLI. No dev-session needed unless you determine the task requires code changes to the repository."
- Preserve the Telegram messaging instructions for ALL PM paths (useful for both SDLC and direct work)
- Note: `_session_type == SessionType.PM` override (line 1599) must NOT force `_collaboration_mode = False`. The PM override only prevents Teammate mode for PM sessions; collaboration mode is valid for PM sessions.

**File 5: `config/personas/project-manager.md`**
- Add a "## Available Tools" section documenting:
  - `python -m tools.memory_search save/search/inspect/forget` -- knowledge base operations
  - Work-vault path: `~/src/work-vault/` (or `~/Desktop/Valor/` on bridge machines)
  - `python -m tools.valor_session list/status/steer/kill/create` -- session management
  - `gws` -- Google Workspace CLI (pre-authenticated at `~/src/node_modules/.bin/gws`)
  - `officecli` -- Office document CLI at `~/.local/bin/officecli`
  - `gh` -- GitHub CLI for issues, PRs, repos
- Note: `~/Desktop/Valor/personas/project-manager.md` (private) must be manually updated to match

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_parse_classifier_response()` in `intent_classifier.py` falls through to "work" with confidence 0.0 for unrecognized intents -- after this change, it falls through for intents not in the extended whitelist. Verify with test cases for "unknown 0.5 reason" and "gibberish"
- [ ] `classify_intent()` wraps the entire API call in try/except, returning work intent on failure -- unchanged, conservative default preserved
- [ ] `_classify_work_request_llm()` try/except blocks for Ollama and Haiku fallbacks -- existing fallback behavior preserved; new tests verify collaboration/other parsing within the same error handling

### Empty/Invalid Input Handling
- [ ] `classify_work_request("")` and `classify_work_request(None)` already return "passthrough" -- unchanged by this work
- [ ] `classify_intent("")` goes through the API call which handles empty strings naturally
- [ ] New classification types only apply to the LLM paths, which require non-empty text

### Error State Rendering
- [ ] If intent classification fails entirely, the fallback is "work" (full SDLC) -- unchanged fail-safe
- [ ] If bridge-level classification fails, the fallback is QUESTION -- unchanged
- [ ] No user-visible output changes; routing is invisible to the end user

### Default Behavior Risk Assessment
- [ ] The "if in doubt, classify as collaboration" default shifts failure mode from over-triggering dev-sessions to PM trying tasks directly. This is strictly cheaper when wrong: PM tries directly (seconds) vs. dev-session timeout (60 minutes). The PM retains access to the Agent tool and can spawn a dev-session on demand. Bridge-level `is_sdlc` property and issue/PR fast-path detection are unchanged, so explicit SDLC references always route correctly.

## Test Impact

- [ ] `tests/unit/test_enums.py::TestClassificationType::test_all_members` -- UPDATE: change assertion from `len == 2` to `len == 4`
- [ ] `tests/unit/test_enums.py::TestClassificationType::test_string_equality` -- UPDATE: add assertions for COLLABORATION and OTHER
- [ ] `tests/unit/test_work_request_classifier.py::TestLlmClassification` -- UPDATE: add parametrized test cases for collaboration-type messages
- [ ] `tests/unit/test_intent_classifier.py::TestParseClassifierResponse::test_unknown_intent` -- UPDATE: "collaboration" and "other" are no longer unknown intents; add new test cases for valid parsing of these intents
- [ ] `tests/unit/test_intent_classifier.py::TestIntentResult` -- UPDATE: add tests for `is_collaboration` and `is_other` properties; update `is_work` tests to verify it returns False for collaboration/other intents
- [ ] `tests/unit/test_intent_classifier.py::TestClassifyIntent` -- UPDATE: add mocked API test for collaboration classification
- [ ] `tests/unit/test_intent_classifier.py::TestGoldenExamples` -- UPDATE: add golden examples for collaboration and other intents
- [ ] `tests/unit/test_pm_session_factory.py::TestBridgeSessionTypeRouting` -- REVIEW: tests reference `ClassificationType` members but only check PM/Dev session type routing; may need new test for collaboration classification not changing session_type
- [ ] `tests/unit/test_sdlc_mode.py::TestIsSdlcJobClassificationType` -- UPDATE: add explicit test cases verifying `is_sdlc` returns False for "collaboration" and "other" classification_type values
- [ ] `tests/unit/test_pm_channels.py` -- REVIEW: tests focus on system prompt loading and do not test dispatch logic; likely no changes needed

## Rabbit Holes

- Fixing the passthrough string/enum inconsistency (passthrough is returned as a raw string, not a ClassificationType member) -- separate cleanup, explicitly out of scope per issue #846
- Adding sub-categories within collaboration (e.g., "knowledge-base-write" vs "issue-draft") -- over-engineering for this appetite
- Modifying the Teammate mode routing threshold (0.90 confidence) -- separate tuning concern
- Updating `tools/classifier.py` (the separate request classifier used by other systems) -- different classification system
- Removing the bridge-level classifier entirely -- it serves a distinct purpose (is_sdlc, working_dir) from the intent classifier

## Risks

### Risk 1: LLM misclassifies sdlc work as collaboration
**Impact:** PM tries to handle a coding task directly, fails or produces poor results
**Mitigation:** The direct-action prompt explicitly tells the PM "No dev-session needed unless you determine the task requires code changes to the repository." The PM retains access to the Agent tool and can spawn a dev-session on demand. The classifier prompt includes clear boundary examples distinguishing code tasks from PM-executable tasks.

### Risk 2: Default bias change from "work"/"sdlc" to "collaboration"
**Impact:** Ambiguous messages skip dev-sessions instead of over-triggering them. PM may drop real coding tasks.
**Mitigation:** This is strictly cheaper when wrong -- PM tries directly (seconds) vs. dev-session timeout (60 minutes). The PM can still escalate to a dev-session. Bridge-level `is_sdlc` property and issue/PR fast-path detection are unchanged, so explicit SDLC references always route correctly.

### Risk 3: Config-driven PM groups read stale classification_type
**Impact:** If the bridge-level classifier has not yet stored the classification when `sdk_client.py` reads it from Redis, the classification_type could be None/missing.
**Mitigation:** When `classification_type` is None or missing, fall through to SDLC orchestration (unchanged default behavior). Only override when classification_type is explicitly COLLABORATION or OTHER.

### Risk 4: `is_work` property change breaks callers
**Impact:** `IntentResult.is_work` currently returns True for any non-teammate intent. Narrowing it to only "work" could break callers that use `is_work` as a catch-all.
**Mitigation:** Audit all callers of `is_work` in `sdk_client.py`. The only consumer is the implicit `else` branch (not teammate -> SDLC). After the change, this becomes: not teammate AND not collaboration AND not other -> SDLC. Add `is_direct_action` property as a convenience (True for collaboration or other).

### Risk 5: Private PM persona file out of sync
**Impact:** The private `~/Desktop/Valor/personas/project-manager.md` does not get the tool visibility update on all machines
**Mitigation:** The public template at `config/personas/project-manager.md` serves as the authoritative reference. The `/update` skill propagates the public template.

## Race Conditions

No race conditions identified -- classification is synchronous and single-request scoped. The bridge-level classification result is stored on AgentSession before the SDK client reads it (the bridge creates the session, then the worker picks it up). The intent classifier runs synchronously within the SDK client call.

## No-Gos (Out of Scope)

- Fixing passthrough string/enum inconsistency (separate cleanup)
- Sub-categorizing collaboration types
- Changing `tools/classifier.py` (separate classification system)
- Adding new MCP servers or tools -- this only documents existing tools in the persona
- Modifying the Teammate confidence threshold (0.90)
- Changing the `is_sdlc` property logic on AgentSession

## Update System

No update system changes required -- this feature modifies existing Python files and a persona template that are already part of the standard git pull. The `/update` skill's existing `git pull` step propagates all changes. The private persona file at `~/Desktop/Valor/personas/project-manager.md` must be manually updated on each machine (documented in the build tasks).

## Agent Integration

No agent integration required -- this is a routing/dispatch change internal to the bridge and SDK client. No new MCP servers, no `.mcp.json` changes, no new tools. The PM persona update documents existing tools that are already available to the agent.

## Documentation

- [x] Create `docs/features/pm-routing-collaboration.md` describing the four-way classification and conditional dispatch, covering both classifiers
- [x] Add entry to `docs/features/README.md` index table
- [x] Update inline docstrings for `classify_intent()`, `_parse_classifier_response()`, `classify_work_request()`, and `_classify_work_request_llm()`

## Success Criteria

- [ ] `ClassificationType` enum has 4 members: SDLC, QUESTION, COLLABORATION, OTHER
- [ ] Intent classifier (`agent/intent_classifier.py`) prompt requests one of four intents with clear examples
- [ ] Intent classifier parser whitelist accepts "collaboration" and "other"
- [ ] `IntentResult` has `is_collaboration` and `is_other` properties; `is_work` returns False for collaboration/other
- [ ] Bridge-level classifier (`bridge/routing.py`) prompt requests one of four outcomes with clear examples
- [ ] "If in doubt, classify as collaboration" replaces "If in doubt, classify as sdlc/work" in BOTH classifiers
- [ ] PM sessions classified as collaboration/other get direct-action instructions, not SDLC orchestration
- [ ] Config-driven PM groups read `AgentSession.classification_type` and route collaboration/other to direct-action
- [ ] PM sessions classified as sdlc/work get unchanged SDLC orchestration instructions
- [ ] PM persona template documents: memory_search, work-vault path, valor_session, gws, officecli
- [ ] "Add this to knowledge base and draft an issue" classifies as collaboration in BOTH `classify_intent()` AND `classify_work_request()`
- [ ] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement intent classifier extension, enum extension, bridge classifier update, conditional dispatch, persona update
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

### 2. Update Intent Classifier (PRIMARY)
- **Task ID**: build-intent-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_intent_classifier.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `CLASSIFIER_PROMPT` in `agent/intent_classifier.py` from 2 intents to 4:
  - `teammate` = informational queries (unchanged examples)
  - `collaboration` = PM-executable tasks: create issue, save file, update doc, send message, knowledge base write, draft summary, organize notes
  - `other` = ambiguous, discussion, brainstorming: "Let's think about this", "What should we do about X?", "I have an idea"
  - `work` = code changes, PRs, deploys, bug fixes (unchanged examples)
- Change default from "If in doubt, classify as work" to "If in doubt, classify as collaboration"
- Extend `_parse_classifier_response()` whitelist at line 97 from `("teammate", "work")` to `("teammate", "work", "collaboration", "other")`
- Add `IntentResult.is_collaboration` property: `self.intent == "collaboration"`
- Add `IntentResult.is_other` property: `self.intent == "other"`
- Add `IntentResult.is_direct_action` property: `self.is_collaboration or self.is_other`
- Narrow `IntentResult.is_work`: change to `self.intent == "work"` only (remove the low-confidence-teammate catch-all; dispatch in sdk_client checks `is_teammate`/`is_collaboration`/`is_other` explicitly, then falls through to SDLC)
- Update tests:
  - Add `TestIntentResult.test_collaboration_intent` and `test_other_intent`
  - Update `TestIntentResult.test_work_intent` to verify `is_collaboration` and `is_other` are False
  - Add `TestParseClassifierResponse.test_valid_collaboration_response` and `test_valid_other_response`
  - Verify `test_unknown_intent` still works (e.g., "maybe 0.5 unsure" still falls through to work)
  - Add `TestClassifyIntent.test_collaboration_classification` with mocked API
  - Add golden examples for collaboration and other intents

### 3. Update Bridge-Level Classifier (SECONDARY)
- **Task ID**: build-bridge-classifier
- **Depends On**: build-enum
- **Validates**: tests/unit/test_work_request_classifier.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true (can run in parallel with build-intent-classifier)
- Extend prompt in `_classify_work_request_llm()` to request one of four outcomes: sdlc, collaboration, other, question
- Add collaboration examples matching intent classifier examples
- Change default from "If in doubt, classify as sdlc" to "If in doubt, classify as collaboration"
- Update result parsing to detect "collaboration" and "other" in LLM responses, returning `ClassificationType.COLLABORATION` and `ClassificationType.OTHER`
- Add parametrized test cases for collaboration-type messages in `tests/unit/test_work_request_classifier.py`

### 4. Make PM Dispatch Instructions Conditional
- **Task ID**: build-dispatch
- **Depends On**: build-intent-classifier, build-bridge-classifier
- **Validates**: tests/unit/test_pm_session_factory.py, tests/unit/test_sdlc_mode.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_collaboration_mode = False` flag in `sdk_client.py` alongside `_teammate_mode`
- **Config-driven PM/Dev groups** (lines 1556-1559): After the existing `_classification_context` assignment, read `AgentSession.classification_type` from Redis for the current session. If classification_type is COLLABORATION or OTHER, set `_collaboration_mode = True`. If None/missing, leave as False (backward compatible -- falls through to SDLC).
- **Unconfigured groups** (lines 1560-1596): After `classify_intent()` returns, check `_intent_result.is_collaboration` or `_intent_result.is_other`. If True, set `_collaboration_mode = True`.
- **PM session override** (line 1599): Do NOT reset `_collaboration_mode` -- the existing override only prevents `_teammate_mode=True` for PM sessions. Collaboration mode is valid and desired for PM sessions.
- **Dispatch branching** (line 1621+): Change from two branches to three:
  - `if _teammate_mode:` Teammate handler (unchanged)
  - `elif _collaboration_mode:` Direct-action prompt (NEW) -- "Handle this task directly using your available tools. You have access to Bash, file operations, GitHub CLI (gh), Google Workspace (gws), memory search, and Office CLI. No dev-session needed unless you determine the task requires code changes to the repository."
  - `else:` SDLC orchestration (unchanged)
- Preserve the Telegram messaging instructions for ALL PM paths (append after the dispatch-specific block)
- Add test cases to `test_sdlc_mode.py` verifying `is_sdlc` returns False for "collaboration" and "other" classification_type values

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
- Run `pytest tests/unit/test_enums.py tests/unit/test_intent_classifier.py tests/unit/test_work_request_classifier.py -v`
- Verify `ClassificationType` has 4 members
- Verify intent classifier prompt contains all four intents with examples
- Verify `_parse_classifier_response()` accepts collaboration and other
- Verify `IntentResult.is_work` returns False for collaboration/other
- Verify sdk_client.py has three-way dispatch logic
- Run full test suite: `pytest tests/unit/ -x -q`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-routing-collaboration.md`
- Add entry to `docs/features/README.md` index table
- Update docstrings in `agent/intent_classifier.py`, `bridge/routing.py`, and `agent/sdk_client.py`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_enums.py tests/unit/test_intent_classifier.py tests/unit/test_work_request_classifier.py -v` | exit code 0 |
| Enum has 4 members | `python -c "from config.enums import ClassificationType; assert len(list(ClassificationType)) == 4"` | exit code 0 |
| Intent classifier has collaboration | `grep -c 'collaboration' agent/intent_classifier.py` | output > 0 |
| Intent parser whitelist extended | `grep 'collaboration.*other' agent/intent_classifier.py` | matches line with whitelist tuple |
| Bridge classifier has collaboration | `grep -c 'collaboration' bridge/routing.py` | output > 0 |
| Conditional dispatch exists | `grep -c '_collaboration_mode' agent/sdk_client.py` | output > 0 |
| Persona has tools section | `grep -c 'memory_search' config/personas/project-manager.md` | output > 0 |
| Lint clean | `python -m ruff check config/enums.py agent/intent_classifier.py bridge/routing.py agent/sdk_client.py` | exit code 0 |
| Format clean | `python -m ruff format --check config/enums.py agent/intent_classifier.py bridge/routing.py agent/sdk_client.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-09. Verdict: NEEDS REVISION (2 blockers, 4 concerns, 2 nits). -->
<!-- REVISION 2: Addressed all blockers and concerns from the first critique. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Archaeologist | Plan targets wrong dispatch mechanism -- two separate classifiers exist. `_classification_context` is a descriptive string, not a `ClassificationType` enum, so the proposed check would never match. The actual PM-vs-Teammate decision is in `agent/intent_classifier.py::classify_intent()` via `_teammate_mode` flag. | FIXED: Plan now targets `agent/intent_classifier.py` as PRIMARY change. SDK client dispatch uses intent classifier result directly, not `_classification_context` string. | Intent classifier prompt extended to 4 intents; parser whitelist extended; IntentResult gets new properties; dispatch uses `_intent_result.is_collaboration` / `_intent_result.is_other`. |
| BLOCKER | Skeptic, Operator | `agent/intent_classifier.py` missing from touched-files inventory and data flow. | FIXED: `agent/intent_classifier.py` is now the PRIMARY change target (Task 2). Data flow diagram explicitly shows both classifiers. | File is listed in Solution, Technical Approach, Step by Step Tasks, and Verification. |
| CONCERN | Operator, Adversary | Config-driven PM/Dev groups bypass both classifiers entirely at sdk_client.py lines 1556-1559. The new classification would be stored but never consulted during dispatch. | FIXED: Task 4 adds `AgentSession.classification_type` check inside the config-driven path. When classification_type is COLLABORATION or OTHER, `_collaboration_mode` is set. | Falls back to SDLC when classification_type is None/missing (backward compatible). |
| CONCERN | Skeptic, Adversary | Fallback default changed from `sdlc`/`work` to `collaboration` without safety analysis. | FIXED: Added explicit "Default Behavior Risk Assessment" in Failure Path Test Strategy. Documented the tradeoff: cheaper when wrong (PM tries directly vs 60-min timeout). PM retains ability to spawn dev-session on demand. | Risk 2 in Risks section also documents this. |
| CONCERN | Adversary | `_parse_classifier_response()` at line 96-97 hard-rejects unknown intents, defaulting to "work". | FIXED: Task 2 explicitly extends whitelist to `("teammate", "work", "collaboration", "other")`. Also updates `is_work` property to not catch collaboration/other. | Parser and IntentResult properties updated together in Task 2. |
| CONCERN | Simplifier | Plan extends ClassificationType enum but classify_work_request() is not consumed for PM routing. | FIXED: Plan now clearly distinguishes PRIMARY (intent_classifier) vs SECONDARY (ClassificationType enum for observability). Data flow diagram shows both paths. | ClassificationType extension is for observability and config-driven PM group fallback. |
| NIT | Operator | Test Impact section references test methods but omits `test_pm_session_factory.py` and `test_sdlc_mode.py`. | FIXED: Both files added to Test Impact section with dispositions. | `test_pm_session_factory.py` REVIEW, `test_sdlc_mode.py` UPDATE with explicit collaboration/other test cases. |
| NIT | User | Success criterion ambiguous about which classifier returns "collaboration". | FIXED: Success criteria now explicitly name both `classify_intent()` and `classify_work_request()`. | Separate criteria for intent classifier and bridge-level classifier. |

---

## Open Questions

No open questions -- the issue provides a complete solution sketch with clear acceptance criteria. The critique blockers and concerns have been addressed in this revision.
