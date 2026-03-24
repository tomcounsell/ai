---
status: Ready
type: feature
appetite: Large
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/514
last_comment_id:
---

# Subconscious Memory

## Problem

Every agent session starts cold. The agent has no memory of past sessions, human preferences, project patterns, or prior decisions. Human instructions sent via Telegram are processed once and forgotten.

**Current behavior:**
Static context via system prompts and CLAUDE.md files. No learning, no reinforcement, no accumulated knowledge across sessions.

**Desired outcome:**
A subconscious memory system where thoughts surface naturally during agent work — triggered by activity, reinforced by outcomes, invisible to the agent. Human instructions persist as high-importance memories. Agent observations are extracted post-session and become available for future sessions.

## Prior Art

- **Issue #394**: Popoto Agent Memory integration layer — original tracking issue, superseded by #514 with concrete implementation design
- **Issue #395**: Multi-persona system — downstream dependent, will use memory partitioning by persona
- **Issue #393**: Behavioral Episode Memory — downstream dependent, builds on these primitives
- **PR #175**: Refactor daydream to unified Redis persistence — established Popoto ORM patterns used throughout

No prior implementations of agent memory exist in this codebase.

## Spike Results

### spike-1: ExistenceFilter API
- **Assumption**: "ExistenceFilter can check topic strings for fast pre-filtering"
- **Method**: code-read
- **Finding**: Yes. `fingerprint_fn=lambda inst: inst.content` allows checking arbitrary strings via `Memory.bloom.might_exist(Memory, "deploy")`. Returns False if definitely absent, True if possibly present (1% false positive rate at default). Auto-integrated via `on_save()` hook — no manual bloom management needed.
- **Confidence**: high
- **Impact on plan**: ExistenceFilter works as designed. We need to fingerprint on content (or extracted keywords) to make bloom checks topic-relevant.

### spike-2: ContextAssembler query_cues API
- **Assumption**: "ContextAssembler can rank memories by topic relevance"
- **Method**: code-read
- **Finding**: `assemble(query_cues={"topic": "deploy"}, agent_id="agent-1")` works. Auto-detects which primitives are present on the model. Returns `AssemblyResult` with `records`, `formatted` (JSON/XML/natural), and `metadata` (pull_count, push_count, token_count, timing_ms). Pull-path uses bloom pre-check then composite score ranking. NOT semantic search — it's multi-factor score-based ranking (decay + confidence + access frequency).
- **Confidence**: high
- **Impact on plan**: ContextAssembler is the retrieval engine. Use `output_format="natural"` for `<thought>` blocks. Query cues should be keywords extracted from tool call context.

### spike-3: additionalContext vs steering for thought injection
- **Assumption**: "Steering (interrupt+query) is the right injection mechanism"
- **Method**: code-read
- **Finding**: **Wrong.** `additionalContext` from the PostToolUse hook is better for `<thought>` blocks. It's passive (appears in next turn's context), requires no active client, costs no API calls, and semantically matches "background hint" vs steering's "supervisor command." Steering uses `client.interrupt()` + `client.query()` which is heavy and designed for urgent course corrections.
- **Confidence**: high
- **Impact on plan**: **Design change.** Use `additionalContext` return from `watchdog_hook()`, not `push_steering_message()`. Simpler, cheaper, more correct.

### spike-4: System prompt priming location
- **Assumption**: "Need to modify multiple prompt loaders for thought priming"
- **Method**: code-read
- **Finding**: Single insertion point: `config/personas/_base.md`. This file feeds into `load_persona_prompt()` which is called by ALL three prompt loaders (`load_system_prompt`, `load_pm_system_prompt`, direct persona). Covers chat, dev, teammate, and PM sessions.
- **Confidence**: high
- **Impact on plan**: One file change, not three.

## Data Flow

### Flow 1: Telegram Ingestion (Human → Memory)
1. **Entry**: Telegram message received by `@client.on(events.NewMessage)` handler (`bridge/telegram_bridge.py:643`)
2. **Store**: `store_message()` saves to TelegramMessage model (`tools/telegram_history/__init__.py:108`)
3. **NEW — Memory save**: After successful store, call `Memory.save()` with content=text, importance=InteractionWeight.HUMAN (6.0)
4. **Output**: Memory record in Redis, immediately available for future ContextAssembler queries

### Flow 2: Thought Injection (Memory → Agent)
1. **Trigger**: PostToolUse hook fires after every tool call (`agent/health_check.py:402`)
2. **Topic extraction**: Extract keywords from tool_name + tool_input (file paths, grep patterns, etc.)
3. **Bloom check**: `Memory.bloom.might_exist(Memory, keyword)` — O(1), ~1ms
4. **Assembly**: If bloom positive, `ContextAssembler.assemble(query_cues={"topic": keyword}, agent_id=agent_id)` — ~5-10ms
5. **Injection**: Return `<thought>` blocks via `additionalContext` in hook response
6. **Output**: Agent sees thoughts as passive context on next turn

### Flow 3: Post-Session Extraction (Agent → Memory)
1. **Trigger**: `BackgroundTask._result` available after session completes (`agent/messenger.py:146`)
2. **Extraction**: Async Haiku call to extract novel observations (decisions, surprises, corrections)
3. **Save**: Each observation saved as Memory with importance=InteractionWeight.AGENT (1.0)
4. **Output**: New memories available for future sessions

### Flow 4: Outcome Reinforcement (Post-Hoc)
1. **Trigger**: After session completes, compare injected thoughts against response stream
2. **Classification**: For each injected memory, determine acted/dismissed/contradicted via semantic overlap
3. **Update**: Call `ObservationProtocol.on_context_used()` to adjust confidence/decay
4. **Output**: Useful memories strengthened, irrelevant ones weakened

## Architectural Impact

- **New dependency**: popoto v1.0.3 (upgrade from v1.0.0) — adds 14 agent-memory primitives
- **New model**: `models/memory.py` — Memory model with decay, confidence, write filtering, access tracking
- **New config**: `config/memory_defaults.py` — centralized Defaults overrides for tuning
- **Interface changes**: PostToolUse hook gains memory check path (additive, no breaking changes)
- **Coupling**: Memory system is read-only from agent perspective. Agent code has zero coupling to memory. Only the hook and bridge have direct dependencies.
- **Data ownership**: Memory records owned by the memory subsystem. TelegramMessage storage unchanged.
- **Reversibility**: High — remove hook logic and Memory.save() calls. No schema migrations, Redis keys can be flushed.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (tuning defaults, validating thought quality)
- Review rounds: 1 (PR review before merge)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto v1.0.3 available | `cd ~/src/popoto && git tag -l v1.0.3` | Agent memory primitives |
| Redis running | `redis-cli ping` | Memory storage backend |
| Anthropic API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku extraction calls |

## Solution

### Key Elements

- **Memory Model**: Level 3 popoto model (DecayingSortedField + WriteFilterMixin + AccessTrackerMixin + ConfidenceField + ExistenceFilter) partitioned by project_key
- **Thought Injection**: PostToolUse hook returns `<thought>` blocks via `additionalContext` — Redis-only hot path, no LLM calls
- **Telegram Ingestion**: Human messages saved as high-importance Memory records on receipt
- **Async Extraction**: Haiku extracts novel observations from agent response stream post-session
- **Outcome Detection**: Post-hoc comparison of injected thoughts vs response content feeds ObservationProtocol
- **Tuning Infrastructure**: Central `config/memory_defaults.py` overrides popoto Defaults

### Flow

**Human sends Telegram message** → Memory.save(importance=HUMAN) → Memory in Redis

**Agent session starts** → System prompt includes `<thought>` priming from `_base.md`

**Agent calls tool** → PostToolUse hook → bloom check → ContextAssembler → `additionalContext` with `<thought>` blocks → Agent sees hints

**Session completes** → Async Haiku extraction → New memories saved → Outcome detection → ObservationProtocol strengthens/weakens

### Technical Approach

- Use popoto's `ContextAssembler` with `output_format="natural"` for human-readable thoughts
- ExistenceFilter fingerprinted on memory content for fast topic relevance checks
- Rate-limit thought injection: only assemble every N tool calls OR on topic change (avoid flooding)
- Track injected thoughts per session for outcome detection (list of (memory_key, content) tuples)
- Haiku extraction prompt focuses on: decisions made, surprises found, corrections received, patterns noticed
- All memory operations wrapped in try/except — memory system failures must never crash the agent

Reference docs in `~/src/popoto/docs/`:
- `guides/agent-memory-quickstart.md` — Level 1-5 progressive adoption
- `guides/subconscious-memory-recipe.md` — SubconsciousMemory recipe reference
- `features/context-assembler.md` — ContextAssembler API
- `features/existence-filter.md` — ExistenceFilter bloom filter
- `fields/constants.py` — Defaults class with all tunable constants

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Memory.save() failures in telegram_bridge.py must log warning, not crash bridge
- [ ] ContextAssembler.assemble() failures in hook must log warning, return no additionalContext
- [ ] ExistenceFilter.might_exist() failures must log warning, skip injection (not block tool call)
- [ ] Haiku extraction API failures must log warning, skip extraction (not block session completion)
- [ ] ObservationProtocol failures must log warning, skip reinforcement (not crash)

### Empty/Invalid Input Handling
- [ ] Empty Telegram message text → skip Memory.save() (no empty content records)
- [ ] Empty response stream → skip extraction (no Haiku call)
- [ ] ContextAssembler returns empty records → no `<thought>` blocks injected
- [ ] Bloom check on empty/None keyword → skip check, no error

### Error State Rendering
- [ ] Memory system failures are invisible to the agent (no error messages in context)
- [ ] Logging captures all failures with enough context for debugging

## Test Impact

No existing tests affected — this is a greenfield feature adding new files (models/memory.py, config/memory_defaults.py, agent/memory_hook.py) and extending existing code with purely additive changes. The PostToolUse hook extension adds a new code path but doesn't modify existing paths.

Existing tests to verify still pass:
- [ ] `tests/unit/test_health_check.py` — existing watchdog_hook tests must still pass after memory hook addition
- [ ] `tests/unit/test_model_relationships.py` — verify Memory model registers correctly
- [ ] `tests/integration/test_steering.py` — steering mechanism unaffected by memory hook

## Rabbit Holes

- **Semantic search**: ContextAssembler uses score-based ranking, not semantic similarity. Don't try to add vector embeddings — the multi-factor scoring (decay + confidence + access) is sufficient and much simpler.
- **Per-turn extraction**: Don't try to extract memories from every tool call or response chunk during the session. Batch extraction post-session is simpler and avoids Haiku latency on the hot path.
- **CoOccurrenceField (Level 4)**: Tempting to add memory associations now. Defer — get Level 3 working and tuned first. Add associations when we have enough memory data to form meaningful graphs.
- **Custom token counter**: ContextAssembler's default `len(str(r)) // 4` is good enough. Don't integrate tiktoken yet.
- **Bloom filter capacity planning**: Start with `capacity=100_000, error_rate=0.01`. Monitor with `fill_ratio()`. Don't over-engineer capacity management before we know memory volume.

## Risks

### Risk 1: Thought injection noise
**Impact:** Too many irrelevant thoughts distract the agent and waste context window tokens.
**Mitigation:** Start conservative — inject max 3 thoughts per trigger, rate-limit to every 10 tool calls or topic change. Tune `WriteFilterMixin` thresholds to gate low-quality memories at save time. Monitor dismissed-vs-acted ratio via outcome detection.

### Risk 2: Bloom filter false positives trigger unnecessary assembly
**Impact:** ContextAssembler runs on every tool call even when no relevant memories exist, adding ~5-10ms latency.
**Mitigation:** ExistenceFilter at 1% false positive rate means 99% of negative checks are free. Assembly is Redis-only (~5-10ms) so even false positive triggers are cheap. Rate limiting prevents worst case.

### Risk 3: Haiku extraction saves low-quality observations
**Impact:** Memory fills with noise, drowning out valuable memories.
**Mitigation:** WriteFilterMixin gates persistence — observations with importance below `WF_MIN_THRESHOLD` (default 0.2) are silently dropped. Tune extraction prompt to focus on novel/surprising content only.

### Risk 4: Popoto v1.0.3 primitives have bugs (we're first production users)
**Impact:** Unexpected behavior in memory operations, potential data loss or corruption.
**Mitigation:** Local editable install at `~/src/popoto` enables immediate debugging and fixes. Strong relationship with maintainer (Tom) for fast upstream patches. All memory operations wrapped in try/except — failures degrade gracefully.

## Race Conditions

### Race 1: Concurrent sessions writing memories for same project
**Location:** Memory.save() in bridge and extraction
**Trigger:** Two sessions running for the same project_key write memories simultaneously
**Data prerequisite:** Redis handles concurrent writes atomically
**State prerequisite:** None — Memory records are independent, no read-modify-write cycles
**Mitigation:** Popoto uses atomic Redis operations (SET, ZADD). No race — concurrent writes are safe.

### Race 2: Extraction reads response while session still writing
**Location:** messenger.py extraction hook
**Trigger:** Extraction starts before `_result` is fully populated
**Data prerequisite:** `_result` must be complete before extraction runs
**State prerequisite:** BackgroundTask must have set `_completed_at`
**Mitigation:** Extraction is triggered AFTER `self._result = await coro` completes (line 146). The await ensures the full response is available.

### Race 3: Bloom filter check during concurrent save
**Location:** PostToolUse hook bloom check concurrent with Memory.save() in bridge
**Trigger:** Human sends message, bridge saves Memory, hook checks bloom simultaneously
**Data prerequisite:** Bloom filter updated before hook checks
**State prerequisite:** None — bloom false negatives are acceptable (memory surfaces on next check)
**Mitigation:** Bloom is eventually consistent. If save hasn't propagated yet, the memory surfaces on the next tool call. No correctness issue, just a potential one-check delay.

## No-Gos (Out of Scope)

- **CoOccurrenceField / Level 4 associations** — deferred until Level 3 is tuned
- **PolicyCache / StreamConsumer** — background pattern crystallization is #393 scope
- **Persona-specific memory partitioning** — #395 scope, but partition conventions are forward-compatible
- **Vector embeddings / semantic search** — score-based ranking is sufficient
- **Memory management UI** — no web interface for viewing/editing memories
- **Cross-project memory sharing** — each project_key is isolated
- **Memory export/backup** — Redis persistence handles durability

## Update System

Update script needs popoto dependency bump:
- `pyproject.toml` changes from `popoto==1.0.0` to `popoto>=1.0.3`
- Since popoto is installed as editable from `~/src/popoto`, the update script should `cd ~/src/popoto && git pull && pip install -e .` to pick up latest
- New files (`models/memory.py`, `config/memory_defaults.py`, `agent/memory_hook.py`) are code changes pulled by standard `git pull`
- No new environment variables or API keys required
- No migration steps — Memory model creates Redis keys on first save

## Agent Integration

No MCP server changes needed. The memory system is invisible to the agent:
- **Bridge integration**: `bridge/telegram_bridge.py` calls `Memory.save()` directly on message receipt — no MCP
- **Hook integration**: `agent/health_check.py` (PostToolUse) checks bloom and injects via `additionalContext` — no MCP
- **Extraction**: Runs as async task in `agent/messenger.py` post-session — no MCP
- The agent never interacts with the memory system. It only sees `<thought>` blocks as passive context.

Integration test: Save a memory via `Memory.save()`, trigger a PostToolUse hook with matching tool context, verify `additionalContext` contains `<thought>` block with the memory content.

## Documentation

- [ ] Create `docs/features/subconscious-memory.md` describing the architecture (two ingestion paths, retrieval, reinforcement)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Document `config/memory_defaults.py` tuning constants with guidance on when to adjust each
- [ ] Add memory system to architecture diagram in CLAUDE.md (under System Architecture)

## Success Criteria

- [ ] `popoto>=1.0.3` in pyproject.toml, importable with agent memory primitives
- [ ] Memory model with DecayingSortedField, ConfidenceField, WriteFilterMixin, AccessTrackerMixin, ExistenceFilter
- [ ] Human Telegram messages create Memory records with InteractionWeight.HUMAN importance
- [ ] PostToolUse hook checks ExistenceFilter, assembles context, injects `<thought>` blocks via additionalContext
- [ ] System prompt priming in `config/personas/_base.md` across all session types
- [ ] Async Haiku extraction runs post-session, saves novel observations as memories
- [ ] Outcome detection compares injected thoughts vs response, feeds ObservationProtocol
- [ ] Sliding window injection: every 3 tool calls, rolling buffer of 9, topic keywords from windows
- [ ] Bigram outcome detection: 1-2 word phrase overlap between injected thoughts and response
- [ ] All memory operations wrapped in try/except with logging (never crash agent)
- [ ] `config/memory_defaults.py` overrides popoto Defaults with tuning constants
- [ ] Logging on all memory ops: injection count, extraction count, outcome breakdown
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (memory-model)**
  - Name: memory-model-builder
  - Role: Create Memory model, Defaults config, and popoto dependency upgrade
  - Agent Type: builder
  - Resume: true

- **Builder (injection)**
  - Name: injection-builder
  - Role: PostToolUse hook extension for bloom check + thought injection via additionalContext
  - Agent Type: builder
  - Resume: true

- **Builder (ingestion)**
  - Name: ingestion-builder
  - Role: Telegram Memory.save() integration and system prompt priming
  - Agent Type: builder
  - Resume: true

- **Builder (extraction)**
  - Name: extraction-builder
  - Role: Async post-session Haiku extraction and outcome detection
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: End-to-end validation of full memory loop
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: memory-docs
  - Role: Feature docs, CLAUDE.md updates, tuning guide
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Memory Model + Defaults + Dependency Upgrade
- **Task ID**: build-memory-model
- **Depends On**: none
- **Validates**: tests/unit/test_memory_model.py (create)
- **Informed By**: spike-1 (ExistenceFilter API), spike-2 (ContextAssembler auto-detection)
- **Assigned To**: memory-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Upgrade `popoto>=1.0.3` in pyproject.toml
- Create `models/memory.py` with Memory model (Level 3 + ExistenceFilter)
- Create `config/memory_defaults.py` overriding popoto Defaults
- Add Memory to `models/__init__.py` exports
- Reference `~/src/popoto/docs/guides/agent-memory-quickstart.md` for model patterns
- Reference `~/src/popoto/src/popoto/fields/constants.py` for all Defaults constants
- Unit tests: model creation, save, query, bloom check, write filter gating, confidence updates

### 2. Telegram Ingestion + System Prompt Priming
- **Task ID**: build-ingestion
- **Depends On**: build-memory-model
- **Validates**: tests/unit/test_memory_ingestion.py (create)
- **Informed By**: spike-4 (system prompt location)
- **Assigned To**: ingestion-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `Memory.save()` call after `store_message()` in `bridge/telegram_bridge.py` (~line 709)
- Use `InteractionWeight.HUMAN` (6.0) for importance
- Skip Memory.save() for empty text, bot messages, and media-only messages without transcription
- Add `<thought>` priming instruction to `config/personas/_base.md`
- Unit tests: verify Memory created from Telegram message, verify empty/bot messages skipped

### 3. PostToolUse Thought Injection
- **Task ID**: build-injection
- **Depends On**: build-memory-model
- **Validates**: tests/unit/test_memory_hook.py (create)
- **Informed By**: spike-1 (bloom API), spike-2 (ContextAssembler), spike-3 (additionalContext)
- **Assigned To**: injection-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-ingestion)
- Create `agent/memory_hook.py` with thought injection logic
- Extract topic keywords from tool_name + tool_input (file paths, grep patterns, command snippets)
- Check `ExistenceFilter.might_exist()` with extracted keywords
- If positive: run `ContextAssembler.assemble()` with keywords as query_cues
- Format results as `<thought>content</thought>` blocks
- Return via `additionalContext` in hook response
- Sliding window rate limiting: keep rolling buffer of last 9 tool calls (3 windows of 3). Every 3rd call, extract topic keywords from current window + previous two windows for richer context
- Track injected thoughts in session-scoped list for outcome detection (list of (memory_key, content) tuples)
- Integrate into `watchdog_hook()` in `agent/health_check.py`
- All operations wrapped in try/except with logging
- Reference `~/src/popoto/docs/features/context-assembler.md` for ContextAssembler API
- Reference `~/src/popoto/docs/features/existence-filter.md` for ExistenceFilter API

### 4. Post-Session Extraction + Outcome Detection
- **Task ID**: build-extraction
- **Depends On**: build-memory-model
- **Validates**: tests/unit/test_memory_extraction.py (create)
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-ingestion and build-injection)
- Create `agent/memory_extraction.py` with:
  - `extract_observations_async(session_id, response_text)` — Haiku call to extract novel observations
  - `detect_outcomes_async(injected_thoughts, response_text)` — compare injected vs response
- Hook extraction into `agent/messenger.py` after `BackgroundTask._result` is available
- Haiku extraction prompt: focus on decisions, surprises, corrections, patterns (not generic statements)
- Save extracted observations as Memory with `InteractionWeight.AGENT` (1.0) importance
- Outcome detection: semantic overlap check (keyword matching sufficient for v1, upgrade to Haiku later if needed)
- Feed outcomes into `ObservationProtocol.on_context_used()`
- Reference `~/src/popoto/docs/guides/subconscious-memory-recipe.md` for extraction patterns
- All operations async, wrapped in try/except

### 5. Integration Validation
- **Task ID**: validate-integration
- **Depends On**: build-ingestion, build-injection, build-extraction
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify full loop: save memory → bloom check → assembly → thought injection → extraction → outcome
- Run existing tests: test_health_check.py, test_model_relationships.py, test_steering.py
- Run new tests: test_memory_model.py, test_memory_ingestion.py, test_memory_hook.py, test_memory_extraction.py
- Verify `ruff check` and `ruff format` pass
- Verify Memory model registers in popoto correctly
- Verify `<thought>` priming appears in system prompts

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/subconscious-memory.md`
- Add entry to `docs/features/README.md`
- Document tuning constants in `config/memory_defaults.py`
- Update architecture section in CLAUDE.md

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Memory model importable | `python -c "from models.memory import Memory; print('OK')"` | output contains OK |
| Defaults override works | `python -c "from config.memory_defaults import apply_defaults; apply_defaults(); from popoto import Defaults; print('OK')"` | output contains OK |
| Popoto version | `python -c "import popoto; print(popoto.__version__)"` | output contains 1.0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Resolved Questions

1. **Extraction prompt tuning**: Start aggressive — extract more, tune down. We want to generate data for tuning Defaults, so over-extraction is better than under-extraction for v1. WriteFilterMixin gates the worst noise at save time.

2. **Rate limiting strategy**: Sliding window of 3, remembering 9. Keep a rolling buffer of the last 9 tool calls (3 windows of 3). Every 3rd tool call, extract topic keywords from the current window with context from the previous two windows. This catches multi-step patterns (e.g., "opened deploy.sh → grepped rollback → read config.yaml" → topic: deployment).

3. **Outcome detection v1**: Bigram (1-2 word phrase) overlap between injected thought content and response text. Pure Python, no LLM. Extract n-grams from both, check intersection. Non-empty overlap → acted, empty → dismissed. Contradicted detection deferred to v2 (requires semantic understanding). Upgrade path: local classifier or Haiku if bigrams prove too noisy.
