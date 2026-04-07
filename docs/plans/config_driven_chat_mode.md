---
status: Shipped
type: feature
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/556
last_comment_id: none
---

# Config-Driven Chat Mode

## Problem

The system has no centralized way to configure chat behavior per-group. Mode selection is split across two mechanisms: chat title prefix (`Dev:` vs everything else) determines session type in `telegram_bridge.py:1419-1428`, and a Haiku-based intent classifier in `agent/intent_classifier.py` determines Q&A vs work routing at runtime. This creates three concrete problems:

**Current behavior:**
- DMs are routed through the intent classifier and can trigger full DevSession work pipelines, even though DMs should only be Q&A conversations.
- Group session type is determined solely by chat title prefix (`Dev:` -> dev, everything else -> chat). There is no way to configure a group as Q&A-only in `projects.json`.
- The intent classifier runs on every ChatSession message, adding latency (~200-400ms Haiku call) and occasional misclassification for groups where the mode is known in advance.
- Team chats (no `Dev:`/`PM:` prefix) use Ollama to classify unaddressed messages, but there is no "passive listener" mode where Valor silently stores memories and only responds when @tagged or replied-to.

**Desired outcome:**
- DMs are always Q&A mode -- no classifier, no work sessions.
- Groups are configurable as `"qa"`, `"pm"`, or `"dev"` via the `persona` field in `projects.json`.
- Q&A groups are passive listeners: store memories from all messages, but only respond when @tagged or replied-to.
- Intent classifier is bypassed when the mode is determined by configuration.
- Existing `Dev:`/`PM:` prefix groups continue to work (backward compatible).

## Prior Art

- **PR #529**: "Add ChatSession Q&A mode with intent classifier" -- Merged 2026-03-26. Introduced `intent_classifier.py`, `qa_handler.py`, and `qa_metrics.py`. Foundation that this issue builds on. Successful but uses runtime classification for all messages.
- **Issue #499**: "ChatSession Q&A mode: direct responses without DevSession for non-work queries" -- Closed 2026-03-26. The tracking issue for PR #529.
- **Issue #541**: "Dynamic PM persona: conversational Q&A mode vs structured work mode" -- Closed 2026-03-26. Added Q&A-specific prose formatting in the summarizer. Confirmed Q&A as a routing decision within ChatSession.

## Data Flow

Current message routing path and where changes are needed:

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py` handler (~line 800)
2. **Storage**: Message is stored to Redis history and subconscious memory (~lines 816-863) -- no changes needed, already stores everything
3. **Response decision**: `bridge/routing.py::should_respond_async()` decides whether to respond -- **CHANGE**: Q&A-persona groups should use mention/reply-only logic, skip Ollama
4. **Session type**: Title prefix check at `telegram_bridge.py:1419-1428` sets `_session_type` -- **CHANGE**: Read persona from config, derive session type
5. **Job enqueue**: `enqueue_job()` at line 1430 with `session_type` -- no changes needed
6. **Agent dispatch**: `agent/sdk_client.py::get_agent_response_sdk()` picks up the job
7. **Intent classification**: `classify_intent()` called at line 1480 for ChatSessions -- **CHANGE**: Skip when mode is config-determined (DM or persona field)
8. **Q&A injection**: `build_qa_instructions()` injected at line 1511 when `_qa_mode=True` -- no changes needed
9. **Response delivery**: Nudge loop delivers response via bridge -- no changes needed

## Architectural Impact

- **New dependencies**: None. Uses existing `projects.json` config infrastructure.
- **Interface changes**: `should_respond_async()` gains persona-aware logic. `get_agent_response_sdk()` gains config-driven Q&A bypass. No public API changes.
- **Coupling**: Slightly increases coupling between routing.py and projects.json config structure, but this is intentional -- config should drive behavior.
- **Data ownership**: No change. Session type and Q&A mode remain properties of the session.
- **Reversibility**: High. Each change point has a clear fallback to current behavior. Config-missing groups fall through to existing title-prefix logic.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on passive listener behavior)
- Review rounds: 1 (code review)

The work touches 3 files across 2 layers (bridge and agent). Each change point is small but they must be coordinated correctly. The main complexity is ensuring backward compatibility with existing groups that lack explicit persona config.

## Prerequisites

No prerequisites -- this work uses existing config infrastructure (`projects.json`, `_resolve_persona()`) and existing modules (`intent_classifier.py`, `qa_handler.py`).

## Solution

### Key Elements

- **Config-driven mode resolver**: A function that reads the group's persona from `projects.json` and returns the effective mode (`qa`, `pm`, `dev`, or `None` for unconfigured). Lives in `bridge/routing.py`.
- **DM Q&A forcing**: In `sdk_client.py`, DMs skip the intent classifier and go straight to Q&A mode.
- **Passive listener routing**: In `bridge/routing.py`, Q&A-persona groups store memories (already happens) but only trigger responses on @mention or reply-to-Valor.
- **Session type derivation**: In `telegram_bridge.py`, persona config maps to session type (`dev` persona -> `dev` session, everything else -> `chat` session), with title-prefix as fallback.
- **Classifier bypass**: In `sdk_client.py`, skip `classify_intent()` when mode is already known from config.

### Flow

**Message arrives** -> Bridge stores to history + memory (always) -> `should_respond_async()` checks persona config -> If Q&A persona: only respond on @tag/reply -> If responding: enqueue job with session_type from config -> `get_agent_response_sdk()` checks if mode is config-determined -> If yes: skip classifier, set `_qa_mode` directly -> Q&A handler processes response

### Technical Approach

- **Persona-to-mode mapping**: The existing `persona` field in `projects.json` groups already holds values like `"developer"`, `"project-manager"`, `"teammate"`. Add support for a `"mode"` field at the group level (`"qa"`, `"pm"`, `"dev"`) that takes precedence. If absent, derive from persona: `"teammate"` -> qa, `"project-manager"` -> pm, `"developer"` -> dev.
- **DM detection**: `is_dm` is already computed as `chat_title is None` in `sdk_client.py:1563` and `event.is_private` in `telegram_bridge.py:803`. Use this to force Q&A mode.
- **Backward compatibility**: When no explicit mode/persona is configured for a group, fall through to the existing title-prefix logic (`Dev:` -> dev, everything else -> chat with classifier).
- **Metrics preservation**: Continue calling `record_classification()` even when bypassing the classifier, using a synthetic `IntentResult` with `intent="qa"` and `reasoning="config-determined"` so metrics remain accurate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdk_client.py:1474-1505` -- the intent classifier block has try/except that defaults to work mode. Test that config-driven bypass correctly skips this block without triggering the exception path.
- [ ] `routing.py:684-691` -- reply-to-Valor detection has try/except. Existing behavior, no changes needed.

### Empty/Invalid Input Handling
- [ ] Test that a group with `"mode": ""` (empty string) falls through to title-prefix logic
- [ ] Test that a group with `"mode": "unknown"` falls through to title-prefix logic
- [ ] Test that DMs with no project config still get Q&A mode

### Error State Rendering
- [ ] If persona config lookup fails, verify the message still gets processed (fall through to existing behavior)
- [ ] Q&A responses should still render correctly when config-driven (same `build_qa_instructions()` path)

## Test Impact

- [ ] `tests/unit/test_intent_classifier.py` -- UPDATE: Add tests for config-driven bypass (new test cases, existing tests unchanged since classifier logic itself is not modified)
- [ ] `tests/integration/test_bridge_routing.py` -- UPDATE: Add test cases for Q&A-persona groups using mention-only response logic
- [ ] `tests/integration/test_message_routing.py` -- UPDATE: Add test cases for DM Q&A forcing and persona-to-mode mapping
- [ ] `tests/unit/test_chat_session_factory.py` -- UPDATE: Add test for session_type derivation from persona config

## Rabbit Holes

- **Adding a third session type ("qa")**: Q&A is currently a boolean flag (`qa_mode`) within ChatSession, not a session type. Introducing a third session type would require changes throughout the job queue, session model, and agent dispatch. Keep Q&A as a routing decision within ChatSession.
- **Refactoring the entire routing pipeline**: The current `should_respond_async()` has accumulated complexity (Ollama classification, team chat detection, respond_to_all flags). Tempting to rewrite but out of scope -- only add the persona-aware branch.
- **Per-user mode overrides**: Individual users having different modes within the same group. Interesting but a separate concern. The `get_user_permissions()` function already handles per-user qa_only restrictions.
- **Removing the intent classifier entirely**: Even with config-driven mode, the classifier is still valuable for unconfigured groups. Do not remove it.

## Risks

### Risk 1: Silent regression in existing group routing
**Impact:** Existing `Dev:` or `PM:` prefix groups could get misrouted if the new config lookup accidentally overrides the title-prefix fallback.
**Mitigation:** The config lookup only applies when a group has an explicit `mode` or `persona` field. Groups without config fall through to the existing title-prefix logic unchanged. Integration tests verify both paths.

### Risk 2: Passive listener misses messages that should get responses
**Impact:** Users in Q&A groups send messages expecting a response but Valor stays silent because they forgot to @tag.
**Mitigation:** Reply-to-Valor always triggers a response (covers conversation continuation). The @mention check uses the existing `mention_triggers` config. Log when a message is silently stored so the behavior is observable.

## Race Conditions

No race conditions identified -- all changes are in the synchronous request processing path. The `should_respond_async()` decision and `_qa_mode` assignment both happen within a single request's execution flow with no shared mutable state between requests.

## No-Gos (Out of Scope)

- No new session types -- use existing `qa_mode` boolean within ChatSession
- No changes to the intent classifier's classification logic or threshold
- No changes to the Q&A handler's instruction generation
- No per-user mode overrides within groups
- No changes to memory storage behavior (already stores everything)
- No changes to the nudge loop or response delivery
- No removal of the Ollama classifier for `respond_to_unaddressed` groups (only bypass for Q&A-persona groups)

## Update System

No update system changes required -- this feature uses existing `projects.json` config infrastructure. Users who want to use the new mode field can add `"mode": "qa"` to their group config in `projects.json`. No new dependencies, no migration steps. Existing installations work unchanged until they opt into the new config.

## Agent Integration

No agent integration required -- this is a bridge-internal and agent-dispatch change. The agent itself does not need new tools or MCP server changes. The Q&A handler instructions are already injected into the agent's context by `sdk_client.py`. The change is purely about when and how that injection is triggered (config-driven vs classifier-driven).

## Documentation

- [ ] Create `docs/features/config-driven-chat-mode.md` describing the config schema, mode resolution order, and passive listener behavior
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/chat-dev-session-architecture.md` to reflect config-driven session type selection
- [ ] Update inline docstrings in `bridge/routing.py::should_respond_async()` and `agent/sdk_client.py::get_agent_response_sdk()`

## Success Criteria

- [ ] DMs always use Q&A mode without running the intent classifier
- [ ] Group with `"mode": "qa"` in projects.json stores memories from all messages silently
- [ ] Q&A-mode group only responds when @valor is mentioned or message is a reply to Valor
- [ ] Group with `"mode": "pm"` behaves identically to current `PM:` prefix groups
- [ ] Group with `"mode": "dev"` behaves identically to current `Dev:` prefix groups
- [ ] Intent classifier is not invoked when mode is determined by config (DM or mode field)
- [ ] Existing `Dev:`/`PM:` prefix groups without explicit mode config continue working unchanged
- [ ] Q&A-mode groups do not use Ollama classification for unaddressed messages
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing-and-dispatch)**
  - Name: routing-builder
  - Role: Implement config-driven mode resolution in routing.py, telegram_bridge.py, and sdk_client.py
  - Agent Type: builder
  - Resume: true

- **Validator (routing-and-dispatch)**
  - Name: routing-validator
  - Role: Verify all routing paths work correctly including backward compatibility
  - Agent Type: validator
  - Resume: true

- **Test Engineer (routing-tests)**
  - Name: routing-tester
  - Role: Write unit and integration tests for config-driven mode resolution
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (feature-docs)**
  - Name: docs-writer
  - Role: Create feature documentation and update architecture docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add config-driven mode resolver to routing.py
- **Task ID**: build-mode-resolver
- **Depends On**: none
- **Validates**: tests/unit/test_routing_mode.py (create), tests/integration/test_bridge_routing.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_chat_mode(project, chat_title, is_dm)` function to `bridge/routing.py` that returns `"qa"`, `"pm"`, `"dev"`, or `None` (unconfigured)
- Resolution order: (1) DMs -> always `"qa"`, (2) group config `mode` field -> use directly, (3) group config `persona` field -> map to mode, (4) title prefix `Dev:` -> `"dev"`, (5) title prefix `PM:` -> `"pm"`, (6) `None` (fall through to existing classifier behavior)
- Update `should_respond_async()` to check resolved mode: Q&A-mode groups use mention/reply-only logic (skip Ollama classification)

### 2. Wire session type from config in telegram_bridge.py
- **Task ID**: build-session-type
- **Depends On**: build-mode-resolver
- **Validates**: tests/unit/test_chat_session_factory.py, tests/integration/test_message_routing.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the 2-line title-prefix check at `telegram_bridge.py:1419-1428` with a call to `resolve_chat_mode()`
- Map mode to session_type: `"dev"` -> `"dev"`, `"pm"` or `"qa"` or `None` -> `"chat"`
- Preserve the existing title-prefix fallback for unconfigured groups

### 3. Add DM Q&A forcing and classifier bypass in sdk_client.py
- **Task ID**: build-classifier-bypass
- **Depends On**: build-mode-resolver
- **Validates**: tests/unit/test_intent_classifier.py
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Before the intent classifier block at `sdk_client.py:1474`, check if mode is config-determined
- If `is_dm` (line 1563) or if the group has an explicit mode from config: set `_qa_mode = True` for qa mode, skip `classify_intent()` call
- For non-qa config-determined modes (pm, dev), skip the classifier (mode is already known)
- Record a synthetic classification metric with `reasoning="config-determined"` for observability

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-classifier-bypass
- **Validates**: All test files listed in Test Impact section
- **Assigned To**: routing-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for `resolve_chat_mode()` covering all resolution paths
- Unit tests for DM Q&A bypass in sdk_client
- Integration tests for Q&A-persona group passive listener behavior
- Integration tests for backward compatibility with title-prefix groups

### 5. Validate all routing paths
- **Task ID**: validate-routing
- **Depends On**: build-tests
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify DMs skip classifier and get Q&A mode
- Verify configured Q&A groups only respond to @mentions and replies
- Verify configured PM/Dev groups behave identically to title-prefix groups
- Verify unconfigured groups fall through to existing behavior
- Run full test suite

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/config-driven-chat-mode.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/chat-dev-session-architecture.md`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Mode resolver exists | `grep -c 'def resolve_chat_mode' bridge/routing.py` | output contains 1 |
| DM QA bypass | `grep -c 'is_dm' agent/sdk_client.py` | output > 0 |
| Feature docs exist | `test -f docs/features/config-driven-chat-mode.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. Should the `projects.json` config use a new `"mode"` field at the group level, or repurpose the existing `"persona"` field? The plan currently proposes `"mode"` as primary with persona-to-mode mapping as fallback, but using persona directly would avoid adding a new field.
2. For passive listener (Q&A) groups, should Valor send a brief acknowledgment when storing a memory from an unaddressed message (e.g., a reaction emoji), or should storage be completely silent?
