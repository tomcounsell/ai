---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/521
last_comment_id:
---

# Project-Scoped Intentional Memories

## Problem

The agent learns project-level concepts through work but has no way to intentionally persist them. The subconscious memory system (PR #515) captures observations passively via Haiku extraction, and human Telegram messages are saved automatically. But there is no mechanism for the agent to intentionally save a high-level concept it has learned or a decision that was made.

**Current behavior:**
The agent's Claude Code auto-memory (`~/.claude/projects/.../memory/`) captures some concepts, but it is file-based, not queryable, and disconnected from the Memory model. The subconscious memory system only saves what Haiku extracts passively -- it has no "save this intentionally" trigger.

**Desired outcome:**
The agent can intentionally save project-level learnings using the memory search tool's `save()` API. These memories persist in the Memory model, surface via thought injection in future sessions, and are searchable/inspectable.

## Prior Art

- **Issue #518 (closed)**: Memory search tool -- provides save/search/inspect/forget APIs. This is the foundation we build on.
- **PR #515 (merged)**: Subconscious Memory -- passive Haiku extraction + thought injection. This handles automatic observations; we add intentional saves.
- **Issue #520 (closed)**: SDLC issue comments -- captures work-item-level context in GitHub issue comments. Complementary: issue comments are short-lived work context, intentional memories are long-lived project concepts.
- **Issue #500 (reverted)**: Finding model -- attempted to capture learnings but was reverted. This issue and #520 together replace Finding with two purpose-built mechanisms.
- **Issue #482 (closed)**: Migrate raw Redis anti-patterns to Popoto models -- established the Popoto model patterns we use.

## Data Flow

### Trigger 1: User Correction

1. **Entry point**: User sends correction via Telegram ("no, we do X instead of Y")
2. **Bridge**: `bridge/telegram_bridge.py` receives message, stores as TelegramMessage and Memory (existing behavior, importance=6.0)
3. **PM session**: Processes message, spawns Dev session if needed
4. **Agent session**: Agent reads the correction in conversation context
5. **Post-session extraction**: `agent/memory_extraction.py` Haiku extraction picks up the correction as an observation (importance=1.0)
6. **Gap**: The correction is saved at human-message level (6.0) but the *lesson learned* from the correction is only saved at agent-observation level (1.0). The intentional save bridges this gap: the agent calls `memory_search.save()` with the distilled lesson at importance=8.0.

### Trigger 2: Explicit "Remember This"

1. **Entry point**: User says "remember that Redis is used for operational state"
2. **Agent session**: Agent recognizes the intent to persist
3. **Agent action**: Calls `memory_search.save(content="Redis is used for operational state, not for durable records", importance=8.0, source="human")`
4. **Output**: Memory saved in Redis Memory model, indexed in bloom filter, available for future thought injection

### Trigger 3: Architectural Decision (Post-Plan)

1. **Entry point**: Plan is finalized via `/do-plan`
2. **Builder agent**: During build execution, significant architectural decisions are made
3. **Post-merge hook**: After PR merges, a summary extraction step distills the project-level takeaway
4. **Agent action**: Calls `memory_search.save(content="...", importance=7.0, source="agent")`

## Architectural Impact

- **New dependencies**: None. Uses existing `tools/memory_search` save() API and existing Memory model.
- **Interface changes**: No API changes. The memory_search.save() function already exists and has the right signature.
- **Coupling**: Minimal increase. The integration points are: (a) system prompt instructions telling the agent when to save, (b) a post-session extraction enhancement, (c) an optional post-merge hook.
- **Data ownership**: Memory model continues to own all memory records. No new models needed.
- **Reversibility**: High. Remove prompt instructions, revert extraction enhancement, remove post-merge hook. No schema changes.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on trigger detection approach)
- Review rounds: 1 (code review)

The core work is straightforward (prompt engineering + a few integration hooks), but getting the trigger detection right requires careful testing to avoid noise.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Memory search tool installed | `python -c "from tools.memory_search import save; print('OK')"` | save() API available |
| Subconscious memory system active | `python -c "from models.memory import Memory; print('OK')"` | Memory model exists |
| Anthropic API key | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Haiku extraction |

Run all checks: `python scripts/check_prerequisites.py docs/plans/intentional_memory_saves.md`

## Solution

### Key Elements

- **System prompt trigger instructions**: Tell the agent when and how to call `memory_search.save()` during sessions. This is the primary mechanism -- LLM intelligence decides when to save, guided by clear instructions.
- **Enhanced post-session extraction**: Upgrade Haiku extraction to distinguish corrections and decisions from generic observations, saving them at higher importance.
- **Post-merge learning extraction**: After a PR merges, extract the project-level takeaway and save it as a memory.

### Flow

**User correction** -> Agent recognizes correction -> `memory_search.save(lesson, importance=8.0, source="human")` -> Memory model -> Future thought injection

**Explicit "remember this"** -> Agent recognizes save intent -> `memory_search.save(content, importance=8.0, source="human")` -> Memory model -> Future thought injection

**Architectural decision** -> Plan finalized or PR merged -> Extraction identifies decision -> `memory_search.save(decision, importance=7.0, source="agent")` -> Memory model -> Future thought injection

### Technical Approach

1. **Prompt-based trigger detection (primary mechanism)**
   - Add a `## Intentional Memory` section to `config/personas/_base.md` (the base persona prompt)
   - Instruct the agent to call `memory_search.save()` when it detects: user corrections, explicit "remember this" requests, and architectural decisions
   - Use importance levels: 8.0 for human-directed saves (corrections, explicit requests), 7.0 for agent-identified decisions
   - The agent already has access to the memory_search tool via CLI (`python -m tools.memory_search save "content"`)

2. **Enhanced Haiku extraction categories**
   - Modify `EXTRACTION_PROMPT` in `agent/memory_extraction.py` to output structured categories (correction, decision, pattern, surprise)
   - Save corrections and decisions at importance=4.0 (higher than generic agent observations at 1.0, but lower than intentional saves at 7.0-8.0)
   - This creates a tiered importance system: human messages (6.0) < enhanced extraction (4.0) < intentional agent saves (7.0) < human-directed saves (8.0)

3. **Post-merge learning hook**
   - Add a function in `agent/memory_extraction.py` that can be called after a PR merges
   - The SDLC merge stage (or post-merge script) calls this to extract and save the project-level takeaway
   - This is a lightweight addition to the existing extraction pipeline

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `memory_search.save()` already wraps in try/except and returns None on failure -- verify this contract holds for all new call sites
- [ ] Enhanced extraction prompt parsing: test that malformed Haiku output degrades gracefully to the current behavior (flat observations)
- [ ] Post-merge extraction: test that failure to extract does not block merge completion

### Empty/Invalid Input Handling
- [ ] Agent calls `save()` with empty content -- verify returns None (already handled)
- [ ] Haiku returns "NONE" or empty for extraction -- verify no saves attempted (already handled)
- [ ] Post-merge called with no PR context -- verify graceful no-op

### Error State Rendering
- [ ] No user-visible UI in this feature. All saves are background operations.
- [ ] Verify save failures are logged at WARNING level (existing contract)

## Test Impact

- [ ] `tools/memory_search/tests/test_memory_search.py` -- UPDATE: add test cases for higher importance saves (importance=7.0, 8.0) to verify they pass through WriteFilterMixin
- [ ] `tests/unit/test_memory_extraction.py` (if exists) -- UPDATE: test enhanced extraction categories and structured output parsing

No other existing tests affected -- the prompt changes and post-merge hook are additive features that do not modify any existing interfaces or behavior.

## Rabbit Holes

- **NLP-based correction detection**: Do not build a classifier to detect user corrections from message text. Use LLM intelligence (prompt instructions) instead of keyword matching. The agent can understand context better than any regex.
- **Deduplication against auto-memory**: Do not try to sync or deduplicate between Claude Code's file-based auto-memory and the Memory model. They serve different purposes and can coexist.
- **Importance score tuning**: Do not spend time optimizing the exact importance values (7.0 vs 8.0 vs 9.0). Start with reasonable defaults and tune based on observed behavior. The decay system handles stale memories naturally.
- **Real-time correction detection via bridge**: Do not try to detect corrections in the bridge layer before they reach the agent. The agent is the right place to understand conversational context.

## Risks

### Risk 1: Memory Noise
**Impact:** Agent saves too many memories, diluting the signal. Thought injection surfaces irrelevant concepts.
**Mitigation:** Use high importance thresholds (7.0-8.0) for intentional saves. The WriteFilterMixin already gates persistence at min_threshold=0.15, and DecayingSortedField ensures low-quality memories fade. Start conservative and loosen if needed.

### Risk 2: Prompt Instruction Ignored
**Impact:** Agent does not follow system prompt instructions to call save(). Intentional saves never happen.
**Mitigation:** Make instructions concrete with examples. Test with real session transcripts. The memory_search CLI is already documented in CLAUDE.md, so the agent knows the tool exists.

### Risk 3: Extraction Category Parsing Fragility
**Impact:** Enhanced Haiku extraction returns unexpected format, breaking category detection.
**Mitigation:** Fall back to current flat-observation behavior if structured parsing fails. Always wrap in try/except. Test with adversarial Haiku output.

## Race Conditions

No race conditions identified -- all memory save operations are independent writes to Redis via Popoto. The Memory model uses AutoKeyField for unique IDs, so concurrent saves cannot collide. Post-session extraction runs after session completion, so there is no race with in-session saves.

## No-Gos (Out of Scope)

- **Auto-memory sync**: No synchronization between Claude Code's file-based auto-memory and the Memory model
- **Memory editing UI**: No web UI for viewing/editing memories (use CLI: `python -m tools.memory_search`)
- **Cross-project memory sharing**: Memories stay partitioned by project_key. No cross-project queries.
- **Importance auto-tuning**: No ML-based importance scoring. Use fixed tiers.
- **Bridge-layer correction detection**: No message classification in the bridge. Agent handles all intent detection.

## Update System

No update system changes required -- this feature modifies prompt files and Python modules that are already deployed via the standard git pull in the update script. No new dependencies, config files, or migration steps needed.

## Agent Integration

- **Memory search tool**: Already exposed as a CLI tool (`python -m tools.memory_search save "content"`). The agent can call this via Bash tool. No MCP server changes needed.
- **System prompt changes**: The `config/personas/_base.md` file is loaded by the agent automatically. Adding the `## Intentional Memory` section makes the agent aware of when to save.
- **Bridge changes**: No bridge changes needed. The bridge already saves human messages as Memory records. The new intentional save triggers are agent-side, not bridge-side.
- **Integration test**: Add a test that verifies the agent persona prompt contains the intentional memory instructions, and that `memory_search.save()` works with the importance levels used by intentional saves.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` to add a "Flow 5: Intentional Saves" section describing the new trigger mechanism
- [ ] Add entry to `docs/features/README.md` index table if not already present
- [ ] Update `CLAUDE.md` quick reference to mention intentional memory save patterns

### Inline Documentation
- [ ] Docstrings on any new functions in `agent/memory_extraction.py`
- [ ] Code comments on the extraction prompt changes explaining the category system

## Success Criteria

- [ ] Agent can call `save()` to persist high-level concepts during sessions
- [ ] User corrections trigger intentional memory saves (importance >= 8.0)
- [ ] Explicit "remember this" requests are handled
- [ ] Saved memories surface via thought injection in future sessions (existing mechanism, verified)
- [ ] Saved memories are searchable via `search()` API (existing mechanism, verified)
- [ ] Clear distinction maintained: issue comments for work-item context (#520), Memory for project concepts (this issue)
- [ ] Enhanced extraction categories work and fall back gracefully on parse failure
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (prompt-and-extraction)**
  - Name: memory-builder
  - Role: Implement system prompt instructions, enhanced extraction, and post-merge hook
  - Agent Type: builder
  - Resume: true

- **Validator (memory-validation)**
  - Name: memory-validator
  - Role: Verify intentional saves work end-to-end, test failure paths
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs)**
  - Name: memory-docs
  - Role: Update subconscious-memory.md and CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add Intentional Memory Instructions to Base Persona
- **Task ID**: build-persona-prompt
- **Depends On**: none
- **Validates**: tests/unit/test_persona_prompt.py (create) -- verify persona contains intentional memory section
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `## Intentional Memory` section to `config/personas/_base.md`
- Include concrete examples of when to call `python -m tools.memory_search save "content"`
- Specify importance levels: 8.0 for human-directed (corrections, explicit requests), 7.0 for agent-identified decisions
- Include the three trigger categories: user corrections, explicit "remember this", architectural decisions

### 2. Enhance Post-Session Extraction Categories
- **Task ID**: build-extraction-categories
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py (update) -- test structured category output and fallback
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify `EXTRACTION_PROMPT` in `agent/memory_extraction.py` to request structured categories
- Parse categories: correction (importance=4.0), decision (importance=4.0), pattern (importance=1.0), surprise (importance=1.0)
- Fall back to flat observations (all at 1.0) if structured parsing fails
- Update `extract_observations_async()` to use category-aware importance

### 3. Add Post-Merge Learning Extraction
- **Task ID**: build-post-merge-hook
- **Depends On**: none
- **Validates**: tests/unit/test_post_merge_extraction.py (create) -- test extraction from PR summary
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `extract_post_merge_learning()` function to `agent/memory_extraction.py`
- Accept PR title, body, and diff summary as input
- Use Haiku to extract the project-level takeaway
- Save with importance=7.0, source="agent"

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-persona-prompt, build-extraction-categories, build-post-merge-hook
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify persona prompt contains intentional memory instructions
- Verify extraction categories parse correctly with sample Haiku output
- Verify fallback behavior when structured parsing fails
- Verify save() works with importance levels 4.0, 7.0, 8.0
- Run full test suite

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` with Flow 5: Intentional Saves
- Update `docs/features/README.md` index if needed
- Update `CLAUDE.md` quick reference

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: memory-validator
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
| Persona has intentional memory section | `grep -c "Intentional Memory" config/personas/_base.md` | output > 0 |
| Extraction prompt has categories | `grep -c "correction\|decision\|pattern\|surprise" agent/memory_extraction.py` | output > 0 |
| Memory save works at importance 8.0 | `python -c "from tools.memory_search import save; r = save('test', importance=8.0); print('OK' if r else 'FILTERED')"` | output contains OK |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Post-merge hook integration point**: Should the post-merge learning extraction be called from the SDLC merge skill (`/do-merge`) or from a separate post-merge script? The merge skill is the natural place, but it adds coupling between SDLC and memory.

2. **Importance tier calibration**: The proposed tiers are: generic observations (1.0), enhanced extraction corrections/decisions (4.0), human Telegram messages (6.0), agent-identified decisions (7.0), human-directed saves (8.0). Does this hierarchy feel right, or should human-directed saves be even higher (9.0+)?
