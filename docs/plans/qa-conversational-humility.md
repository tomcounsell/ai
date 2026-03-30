---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/589
last_comment_id: 4154388316
---

# QA Conversational Humility

## Problem

QA responses in teammate group chats read like authoritative lectures rather than collaborative dialogue. When someone asks a question, Valor produces long, definitive, single-perspective answers that reference internal systems unprompted and never check whether the question was understood correctly.

**Current behavior:**

1. **Authoritative monologue**: "Self-correction relies on three key patterns: **(1)**..." -- responses frame as lectures, not conversation.
2. **No clarification**: Kevin asked about rebuilding qwen models with a 16k window. Valor assumed `num_ctx` runtime config; Kevin meant Ollama's `ollama create` model stamping. Valor never asked.
3. **CLI syntax leaking**: Agent echoes `valor-telegram send --chat` CLI syntax into response text. Root cause is prompt contamination from tool descriptions in `config/personas/_base.md`, `config/SOUL.md`, and `.claude/skills/telegram/SKILL.md`.
4. **No react-only path**: Every reply-to-Valor spawns a full agent session. Banter, jokes, and one-word acknowledgments ("legit", "nice", "lol") get earnest multi-paragraph responses instead of an emoji reaction or silence.

**Desired outcome:**

- QA responses are 50% shorter, acknowledge uncertainty, check understanding when ambiguous
- Multiple perspectives briefly rather than one exhaustively
- CLI syntax never appears in Telegram messages
- Banter/jokes get emoji reactions without spawning agent sessions

## Prior Art

- **Issue #556**: Config-driven chat mode -- Merged. Established QA mode routing for teammate persona groups. This work builds on that routing to improve what happens *inside* QA sessions.
- **Issue #541**: Dynamic PM persona -- Merged. Split conversational Q&A mode from structured work mode. Laid groundwork for mode-specific behavior.
- **Issue #497**: PM should compose Telegram messages via tool, not through summarizer -- Merged. Added `pm_bypass` path in `bridge/response.py`. Relevant because it shows the summarizer can be bypassed for specific session types.
- **Issue #283**: Remove acknowledgment messages -- Merged. Prior work on controlling what gets sent to Telegram.

## Data Flow

The QA response path touches four components in sequence:

1. **Entry point**: Message arrives in a teammate group chat. `bridge/routing.py` `should_respond_async()` checks if it is a reply-to-Valor or @mention. If yes, `resolve_chat_mode()` returns `"qa"`.
2. **Classification**: `bridge/routing.py` `classify_needs_response()` (Ollama, 2-way: work/ignore) decides whether to respond. Currently has no "react-only" option.
3. **Agent session**: `agent/sdk_client.py` creates a ChatSession. `agent/qa_handler.py` `build_qa_instructions()` injects QA-specific prompt. The prompt currently encourages authoritative behavior ("knowledgeable teammate who knows the codebase well") with no guidance on brevity, humility, or clarification.
4. **Output delivery**: `bridge/response.py` `send_response_with_files()` passes through `bridge/summarizer.py` which compresses length but does not edit for conversational tone. The summarizer has QA-specific classification paths (Path B) but no tone enforcement rules.

**CLI syntax leak path**: The agent's system prompt includes `config/personas/_base.md` which contains `valor-telegram send --chat "Dev: Valor" "Hello"` as an example. When the agent is confused about whether to use the CLI tool or just return text, it echoes the CLI syntax into its response text. The summarizer passes this through because it looks like normal content.

## Architectural Impact

- **New dependencies**: None. All changes use existing Ollama and Telethon infrastructure.
- **Interface changes**: `classify_needs_response()` in `bridge/routing.py` changes from 2-way (work/ignore) to 3-way (respond/react/ignore). Callers in `bridge/telegram_bridge.py` need to handle the new "react" return value.
- **Coupling**: Reduces coupling -- social classification stays in the routing layer, not in persona config.
- **Data ownership**: No change. Routing decisions remain in `bridge/routing.py`.
- **Reversibility**: High -- all changes are prompt text, classification logic, and a new code path. No data format or schema changes.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (tone calibration review)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. Uses existing Ollama for classification and existing Telethon `set_reaction()` for emoji reactions.

## Solution

### Key Elements

- **Layer 1 -- QA prompt overhaul**: Rewrite `build_qa_instructions()` to enforce brevity, humility, clarification-first, multi-perspective, and follow-up invitation.
- **Layer 2 -- Summarizer QA tone rules**: Add QA-specific tone enforcement to the summarizer prompt so the Haiku edit pass catches authoritative framing and trims length.
- **Layer 3 -- CLI syntax sanitization**: Remove CLI command examples from persona/SOUL docs that leak into responses, and add a lightweight sanitizer in `bridge/response.py` as defense-in-depth.
- **Layer 4 -- 3-way social classifier**: Extend `classify_needs_response()` from 2-way to 3-way (respond/react/ignore) and wire the "react" path to send an emoji reaction without spawning a session.

### Flow

**Message arrives** -> Routing gate (respond/react/ignore) -> IF react: send emoji, done -> IF respond: Agent session with humility-tuned QA prompt -> Summarizer with QA tone rules -> Telegram

### Technical Approach

**Layer 1 -- QA prompt** (`agent/qa_handler.py`):
- Replace "knowledgeable teammate who knows the codebase well" with "curious colleague" framing
- Add explicit rules: (1) restate understanding before answering, (2) if ambiguous, ask before or alongside answer, (3) cover 2-3 angles briefly not one exhaustively, (4) use "I think" / "from what I've seen" not definitive statements, (5) end with a follow-up question if not sure of the ask, (6) answer their situation first, reference internals only when asked
- Target response length: 2-4 sentences for straightforward questions, 1 paragraph max for complex ones

**Layer 2 -- Summarizer QA tone** (`bridge/summarizer.py`):
- Add a QA-specific instruction block to the summarizer system prompt when `session.chat_mode == "qa"`
- Rules: compress to 2-4 sentences, remove authoritative framing, remove unsolicited internal references, ensure conversational tone
- This is defense-in-depth -- the QA prompt (Layer 1) is the primary control, the summarizer catches leakage

**Layer 3 -- CLI syntax cleanup**:
- In `config/personas/_base.md` and `config/SOUL.md`: move `valor-telegram send` examples into a fenced code block with a clear "TOOL USAGE ONLY -- never include this syntax in response text" header, or remove entirely and rely on the skill file
- In `bridge/response.py`: add a `_sanitize_cli_leaks()` function that strips lines matching `valor-telegram` or `--chat` patterns from response text before sending. Lightweight regex, not an LLM call.

**Layer 4 -- Social classifier** (`bridge/routing.py`):
- Extend `classify_needs_response()` to return a string ("respond" / "react" / "ignore") instead of bool
- Update Ollama prompt from 2-way to 3-way classification
- Add "react" to the fast-path acknowledgment set for common social tokens ("lol", "haha", "nice", emoji-only messages, short banter)
- In `bridge/telegram_bridge.py`: when classifier returns "react", call `set_reaction()` with contextually appropriate emoji (default: "😁" for humor, "👍" for acknowledgments) and skip session creation
- Keep `classify_needs_response_async()` wrapper updated

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `classify_needs_response()` already has a `try/except` for Ollama failures that defaults to `True` (respond). Verify the 3-way version defaults to "respond" on failure (conservative).
- [ ] `_sanitize_cli_leaks()` must handle empty strings and None without raising.

### Empty/Invalid Input Handling
- [ ] `build_qa_instructions()` returns a non-empty string (existing test covers this).
- [ ] Social classifier handles empty string, whitespace-only, and emoji-only inputs gracefully.
- [ ] CLI sanitizer handles response text that is purely CLI commands (should return empty or "Done.").

### Error State Rendering
- [ ] If social classifier fails, message falls through to full response (no silent drop).
- [ ] If `set_reaction()` fails for react-only path, log warning but do not spawn a session as fallback.

## Test Impact

- [ ] `tests/unit/test_qa_handler.py::test_conversational_tone` -- UPDATE: current test checks for "directly" and "conversational" which will change with the new prompt text. Update assertions to match new humility-focused wording.
- [ ] `tests/unit/test_qa_handler.py::test_research_first_behavior` -- UPDATE: if prompt restructuring moves the research section, update assertions.
- [ ] `tests/unit/test_qa_handler.py::test_no_agent_tool_instruction` -- UPDATE: verify "Do NOT spawn a DevSession" is still present in new prompt.
- [ ] `tests/unit/test_config_driven_routing.py` -- UPDATE: tests that assert `classify_needs_response` returns bool will need to handle string return type.
- [ ] `tests/e2e/test_message_pipeline.py` -- UPDATE: if it calls `classify_needs_response`, update for 3-way return value.

## Rabbit Holes

- **Bookend subagents (intake/review) inside sessions**: The issue proposes full intake and review subagents. This is a significant architectural change (new module, SDK lifecycle hooks, session type changes). Defer to a follow-up issue. The prompt + summarizer approach achieves 80% of the benefit at 20% of the complexity.
- **Replacing the external summarizer for QA**: The issue suggests the review subagent should replace the Haiku summarizer for QA sessions. The summarizer bypass logic in `bridge/response.py` is complex (pm_bypass, SDLC checks). Changing the summarizer pipeline is a separate project.
- **Sentiment analysis for emoji selection**: Tempting to use an LLM to pick the "perfect" reaction emoji. A simple mapping (humor -> laugh, acknowledgment -> thumbs up) is sufficient.
- **Training/fine-tuning the social classifier**: The Ollama 3-way classifier with a clear prompt is good enough. Do not spend time on few-shot optimization or model selection.

## Risks

### Risk 1: QA prompt changes make responses too terse
**Impact:** Useful information gets cut. Users have to ask follow-up questions for basic answers.
**Mitigation:** The "2-4 sentences" target is a guideline, not a hard limit. The prompt says "brief" not "minimal". Test with real examples from the issue before shipping.

### Risk 2: 3-way classifier misclassifies genuine questions as "react"
**Impact:** User asks a real question, gets only an emoji reaction, no answer.
**Mitigation:** Conservative default -- if Ollama fails or returns ambiguous, fall through to "respond". Only confident "react" classifications skip the session. The fast-path set is narrow (known social tokens only).

### Risk 3: CLI sanitizer is too aggressive
**Impact:** Legitimate technical content about CLI tools gets stripped from responses.
**Mitigation:** Only strip lines that match the specific `valor-telegram` pattern, not general CLI syntax. The sanitizer targets prompt contamination, not intentional technical discussion.

## Race Conditions

No race conditions identified -- all operations are synchronous within their respective paths. The social classifier runs before session creation (single decision point), and the sanitizer runs in the response pipeline (sequential).

## No-Gos (Out of Scope)

- Bookend subagents (intake/review) inside sessions -- separate architectural project
- Replacing the external summarizer for QA sessions -- separate project
- Changes to the main persona config for social behavior -- social intelligence stays in routing
- Multi-model social classification (e.g., Anthropic fallback for social classifier) -- Ollama is sufficient
- Changing how non-QA sessions (PM, Dev, SDLC) handle tone -- this is QA-only

## Update System

No update system changes required -- this feature modifies prompt text, classification logic, and response filtering, all of which are code changes that propagate via normal `git pull`. No new dependencies, config files, or migration steps.

## Agent Integration

No new MCP server or tool registration needed. The changes are:
- `agent/qa_handler.py` -- prompt text change, automatically used by ChatSession in QA mode
- `bridge/routing.py` -- classification logic change, automatically used by the bridge
- `bridge/response.py` -- sanitizer addition, automatically used in the response pipeline
- `bridge/summarizer.py` -- QA tone rules, automatically used when summarizing QA output
- `config/personas/_base.md` and `config/SOUL.md` -- CLI example cleanup, automatically loaded as system prompt

All changes are internal to existing modules. The bridge imports and calls these directly. No `.mcp.json` changes needed.

## Documentation

- [ ] Create `docs/features/qa-conversational-humility.md` describing the QA tone controls, 3-way social classifier, CLI sanitization, and react-only path
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/config-driven-chat-mode.md` to document the 3-way classifier change (was 2-way)
- [ ] Update inline docstrings in `agent/qa_handler.py`, `bridge/routing.py`, `bridge/response.py`

## Success Criteria

- [ ] QA responses in teammate groups average 50% shorter than current (measured by comparing prompt output length with old vs new instructions on the same test inputs)
- [ ] Ambiguous questions trigger clarification before or alongside the answer
- [ ] Responses reference multiple perspectives rather than one exhaustive explanation
- [ ] Agent never echoes CLI syntax (`valor-telegram`, `--chat`) in response text
- [ ] Reply-to-Valor banter/jokes classified as react-only, emoji sent without session
- [ ] Social classifier lives in `bridge/routing.py`, not in persona config
- [ ] All existing tests pass after updates
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (qa-prompt)**
  - Name: qa-prompt-builder
  - Role: Rewrite QA handler prompt and update summarizer QA tone rules
  - Agent Type: builder
  - Resume: true

- **Builder (social-classifier)**
  - Name: social-classifier-builder
  - Role: Extend classify_needs_response to 3-way, wire react-only path in bridge
  - Agent Type: builder
  - Resume: true

- **Builder (cli-sanitizer)**
  - Name: cli-sanitizer-builder
  - Role: Clean up CLI examples in persona docs and add response sanitizer
  - Agent Type: builder
  - Resume: true

- **Validator (qa-tone)**
  - Name: qa-tone-validator
  - Role: Verify QA prompt produces shorter, humbler responses on test inputs
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite QA handler prompt
- **Task ID**: build-qa-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_qa_handler.py (update)
- **Assigned To**: qa-prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `build_qa_instructions()` in `agent/qa_handler.py` with humility, clarification-first, multi-perspective, and brevity rules
- Update `tests/unit/test_qa_handler.py` assertions to match new prompt text

### 2. Add summarizer QA tone rules
- **Task ID**: build-summarizer-tone
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py (update if needed)
- **Assigned To**: qa-prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Add QA-specific tone enforcement instructions to summarizer system prompt
- Conditioned on `session.chat_mode == "qa"` in the summarizer function

### 3. Clean up CLI syntax leaks
- **Task ID**: build-cli-sanitizer
- **Depends On**: none
- **Validates**: tests/unit/test_cli_sanitizer.py (create)
- **Assigned To**: cli-sanitizer-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove or fence `valor-telegram send` examples in `config/personas/_base.md` and `config/SOUL.md`
- Add `_sanitize_cli_leaks()` in `bridge/response.py` that strips `valor-telegram` and `--chat` patterns
- Wire sanitizer into `send_response_with_files()` after `filter_tool_logs()`
- Create tests for the sanitizer function

### 4. Extend social classifier to 3-way
- **Task ID**: build-social-classifier
- **Depends On**: none
- **Validates**: tests/unit/test_social_classifier.py (create), tests/unit/test_config_driven_routing.py (update)
- **Assigned To**: social-classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `classify_needs_response()` return type from `bool` to `str` ("respond" / "react" / "ignore")
- Update Ollama prompt for 3-way classification
- Extend fast-path acknowledgment set with react-specific tokens
- Update `classify_needs_response_async()` wrapper
- Update all callers in `bridge/routing.py` and `bridge/telegram_bridge.py`
- Add react-only code path in `bridge/telegram_bridge.py` that calls `set_reaction()` and skips session

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-qa-prompt, build-summarizer-tone, build-cli-sanitizer, build-social-classifier
- **Assigned To**: qa-tone-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify QA prompt text includes humility markers
- Verify CLI sanitizer catches known leak patterns
- Verify social classifier returns correct types for test inputs
- Check that no `valor-telegram send` examples remain in persona prompt text (outside fenced tool-only blocks)

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: qa-prompt-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/qa-conversational-humility.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/config-driven-chat-mode.md` for 3-way classifier

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: qa-tone-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met including documentation
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| QA prompt has humility markers | `grep -c "I think\|from what I.ve seen\|clarif" agent/qa_handler.py` | output > 0 |
| CLI sanitizer exists | `grep -c "_sanitize_cli_leaks" bridge/response.py` | output > 0 |
| 3-way classifier | `grep -c "react\|respond\|ignore" bridge/routing.py` | output > 0 |
| No CLI examples in persona | `grep -c "valor-telegram send" config/personas/_base.md` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| # | CONCERN | CRITIC | STATUS |
|---|---------|--------|--------|
| 1 | 3-way return type change silently breaks callers (non-empty string is truthy) | Operator | BLOCKING -- use enum or backward-compatible wrapper |
| 2 | Task 4 must enumerate all classify_needs_response call sites (routing.py:801 missing) | Archaeologist | BLOCKING -- add explicit call site list |
| 3 | Dual-layer tone enforcement (Layer 1 + Layer 2) may double-process and degrade quality | Skeptic | NON-BLOCKING -- consider dropping Layer 2 |
| 4 | Ollama 3-way classification unreliable on 1.7B model; use token matching for react | Adversary | NON-BLOCKING -- two-stage approach recommended |
| 5 | "2-4 sentences" target too aggressive for complex technical questions | Skeptic | NON-BLOCKING -- use qualitative brevity guidance |
| 6 | Fast-path acknowledgment set duplicated between ignore and react | Operator | NON-BLOCKING -- merge into single classification dict |
| 7 | CLI sanitizer regex could strip legitimate technical discussion | Adversary | NON-BLOCKING -- tighten regex to command-like patterns only |

---

## Open Questions

1. Should the 3-way classifier use a dedicated Ollama prompt or extend the existing `classify_needs_response` prompt? The plan proposes extending the existing one for simplicity, but a separate prompt might be cleaner.
2. For the react-only path, should the emoji selection be purely rule-based (humor -> laugh emoji, acknowledgment -> thumbs up) or use a lightweight Ollama call to pick context-appropriate reactions?
