---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/376
last_comment_id:
---

# Behavioral Episode Memory System

## Problem

The agent system processes many SDLC cycles but retains no structural memory of them. When the agent encounters a problem structurally similar to one it solved before (same topology, same layer, same friction points), it starts from scratch every time. There is no mechanism to recognize "I've been here before" and apply learned behavioral patterns.

**Current behavior:**
Every SDLC cycle is treated as novel. The Reflections pipeline performs daily maintenance (log review, session analysis, bug filing) but does not extract reusable behavioral patterns from completed work. `AgentSession` tracks lifecycle state but not tool sequences, friction events, or outcome quality. The LLM is excellent at reasoning over content but has no access to structural episode history.

**Desired outcome:**
Completed SDLC cycles are automatically classified and stored as `CyclicEpisode` records with structural fingerprints. When enough episodes share a fingerprint cluster with consistent outcomes, `ProceduralPattern` shortcuts crystallize. The Observer queries these at stage transitions, surfacing warnings ("situations like this have failed here") and shortcuts ("this pattern has a high-success tool sequence") before the agent acts.

## Prior Art

- **Issue #323 (MuninnDB)**: Proposed integrating MuninnDB as a cognitive memory layer with ACT-R activation theory and Hebbian learning. Closed — superseded by this issue. The closing rationale: MuninnDB competed with the LLM's natural semantic reasoning instead of complementing it. This issue focuses on structural/behavioral patterns the LLM cannot self-derive.
- **Issue #309 (Observer Agent)**: Implemented stage-aware SDLC steering (closed, merged). The Observer is the primary consumer of the memory system's warnings and shortcuts. Phase 3 integration depends on this existing infrastructure.

## Data Flow

1. **Entry point**: Agent completes an SDLC cycle (session reaches `completed` status)
2. **Cycle-close hook** (new, in Reflections): Reads the completed `AgentSession` — tool sequence, friction events (from instrumented session data), stage durations
3. **Fingerprint classifier**: Single lightweight LLM call classifies the episode's problem topology, affected layer, ambiguity level
4. **CyclicEpisode write**: Structured record stored in Redis via Popoto, namespaced to the project vault (`mem:{project_key}:`)
5. **Pattern crystallization** (Reflections pipeline): Scans episodes sharing fingerprint clusters; when N episodes with consistent outcomes exist, writes/reinforces a `ProceduralPattern` to the `shared:` namespace
6. **Observer query** (Phase 3): At SDLC stage transitions, Observer queries episodes/patterns matching the current issue's fingerprint. Delivers warnings or shortcuts to the worker agent.
7. **Output**: Warnings and shortcuts injected into worker context before it acts

## Architectural Impact

- **New dependencies**: None external. Uses existing Popoto ORM and Redis. Requires Popoto `Meta.namespace` feature for vault isolation (prerequisite, tracked in Popoto repo).
- **Interface changes**: `AgentSession` gains new fields for tool sequence and friction event accumulation. Reflections pipeline gains new steps. Observer gains memory query capability.
- **Coupling**: Memory system is read-only from the Observer's perspective — loose coupling via Redis queries. Write path is exclusively through the Reflections pipeline (abstraction barrier).
- **Data ownership**: CyclicEpisode and ProceduralPattern are new data domains. Project-scoped episodes stay local; content-stripped patterns sync across machines.
- **Reversibility**: High. New Popoto models and Reflections steps can be removed without affecting existing functionality. AgentSession field additions are backward-compatible (null defaults).

## Appetite

**Size:** Large

**Team:** Solo dev + PM + reviewer(s)

**Interactions:**
- PM check-ins: 2-3 (scope alignment on fingerprint taxonomy, pattern confidence thresholds, sync format)
- Review rounds: 2+ (data model review, Reflections integration review, Observer integration review)

This is a 4-phase feature with external prerequisites (Popoto extensions). Each phase is independently shippable and testable.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Popoto `Meta.namespace` | `.venv/bin/python -c "from popoto import Model; assert hasattr(Model.Meta, 'namespace')"` | Vault isolation for project-scoped episodes |
| Popoto `ListField(max_length=N)` | `.venv/bin/python -c "from popoto import ListField; lf = ListField(max_length=50)"` | Capped tool sequence and friction event lists |
| Popoto `atomic_increment` | `.venv/bin/python -c "from popoto import Model; assert hasattr(Model, 'atomic_increment')"` | Efficient counter updates on pattern reinforcement |
| Redis running | `.venv/bin/python -c "import redis; redis.Redis().ping()"` | Storage backend |
| ANTHROPIC_API_KEY set | `.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Fingerprint classifier LLM call |

## Solution

### Key Elements

- **CyclicEpisode model**: Structured behavioral record with fingerprint (problem topology, affected layer, ambiguity), trajectory (tool sequence, friction events, stage durations), and outcome (resolution type, intent satisfied, review rounds)
- **ProceduralPattern model**: Crystallized from repeated episodes — canonical tool sequence, success rate, confidence, sample count
- **AgentSession instrumentation**: Real-time accumulation of tool_sequence and friction_events during SDLC cycles
- **Fingerprint classifier**: Single lightweight LLM call at cycle close to classify problem topology and ambiguity
- **Reflections integration**: Cycle-close episode creation, pattern crystallization, content stripping for shared layer
- **Observer integration**: Memory query at stage transitions, warning/shortcut delivery

### Flow

**SDLC cycle completes** → Reflections reads AgentSession → LLM classifies fingerprint → CyclicEpisode written to project vault → Pattern crystallization checks shared namespace → ProceduralPattern reinforced/created

**New SDLC cycle starts** → Observer queries memory at stage transition → Matching episodes/patterns found → Warnings or shortcuts delivered to worker → Worker accepts/rejects → Feedback updates pattern confidence

### Technical Approach

- All models use Popoto ORM with Redis backend — consistent with existing architecture
- Two-tier namespace isolation: project vault (`mem:{project_key}:`) for full episodes, shared namespace (`shared:`) for content-stripped patterns
- Reflections pipeline is the sole write path for episodes and patterns (abstraction barrier)
- Observer is a read-only consumer — queries, never writes
- Fingerprint classification uses Claude Haiku (cheap, fast) with a structured enum output
- Pattern crystallization uses deterministic logic (no LLM) — count matching fingerprints, compute success rate
- Cross-machine sync via iCloud-synced JSON export/import (Phase 1), swappable to cloud Redis later

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Fingerprint classifier LLM call: test timeout, malformed response, API error — episode should still be created with `ambiguous` topology
- [ ] Episode write failure: test Redis connection error — should log warning, not crash Reflections pipeline
- [ ] Pattern crystallization: test edge cases (zero episodes, all failed outcomes) — should not create patterns with 0% success rate

### Empty/Invalid Input Handling
- [ ] AgentSession with no tool_sequence: episode created with empty trajectory
- [ ] AgentSession with no SDLC stages: skipped by cycle-close hook (not an SDLC cycle)
- [ ] Empty fingerprint cluster match: Observer returns no warnings/shortcuts

### Error State Rendering
- [ ] Observer warning delivery: test that warnings are surfaced in worker context, not silently dropped
- [ ] Shortcut rejection: test that agent can decline a shortcut without side effects

## Rabbit Holes

- **Embedding-based similarity search**: The issue mentions investigating Redis encoding for embedding vectors. This is premature — start with exact-match fingerprint lookup. Embedding similarity is a Phase 4+ concern.
- **Complex confidence decay models**: Keep pattern confidence simple (success_count / sample_count). Don't build temporal decay, Bayesian updating, or multi-armed bandit exploration. Tune later with real data.
- **Automated PRINCIPAL.md updates**: Explicitly a non-goal per the issue. Don't let pattern insights auto-modify system configuration.
- **RAG/semantic retrieval**: The LLM handles content reasoning. Don't build vector search or semantic similarity on episode content. Structural fingerprints only.
- **Real-time tool tracking granularity**: Don't instrument individual tool calls at sub-second precision. Track tool types per stage, not individual invocations.

## Risks

### Risk 1: Fingerprint taxonomy is too coarse or too fine
**Impact:** Too coarse = false matches, bad shortcuts. Too fine = no patterns ever crystallize.
**Mitigation:** Start with the 6-value `problem_topology` enum from the issue. Tune empirically in Phase 4 using retrospective classification of past sessions. Accept that Phase 1-2 fingerprints may need revision.

### Risk 2: Popoto prerequisites not ready
**Impact:** Cannot implement vault isolation or capped lists, blocking Phase 1 model definitions.
**Mitigation:** Each Popoto extension is a separate PR on that repo. Phase 1 can begin with model stubs using workarounds (manual key prefixing, manual list truncation). Replace with proper Popoto features when available.

### Risk 3: Insufficient completed SDLC sessions to seed patterns
**Impact:** Pattern crystallization requires N similar episodes. If session volume is low, patterns never form.
**Mitigation:** Phase 4 includes retrospective classification of past AgentSession logs to seed initial episodes. Set N threshold low initially (3 episodes) and increase as data grows.

### Risk 4: Observer integration adds latency to stage transitions
**Impact:** Memory queries at every stage transition could slow the pipeline.
**Mitigation:** Fingerprint-based lookup is O(1) in Redis (key-based query, not scan). Set a hard timeout (500ms) on memory queries — skip if slow.

## Race Conditions

### Race 1: Concurrent Reflections and Observer reading episodes
**Location:** models/cyclic_episode.py, Reflections crystallization step, Observer query
**Trigger:** Reflections is crystallizing patterns while Observer queries the same episodes
**Data prerequisite:** Episodes must be fully written before pattern crystallization reads them
**State prerequisite:** Episode fingerprint must be classified before crystallization considers it
**Mitigation:** Reflections is the sole writer (sequential pipeline). Observer is read-only. No concurrent writes to the same episode. Redis reads are atomic. Pattern crystallization can safely read episodes while Observer also reads them — no mutation conflict.

### Race 2: Cross-machine pattern sync conflicts
**Location:** iCloud-synced JSON files, shared Redis namespace
**Trigger:** Two machines export/import patterns simultaneously
**Data prerequisite:** Each machine's local patterns must be consistent before export
**State prerequisite:** Import must be idempotent
**Mitigation:** Last-write-wins on `last_reinforced` timestamp. Higher `sample_count` breaks ties. Import skips existing patterns with equal or newer timestamps. JSON export is atomic (write to temp file, rename).

## No-Gos (Out of Scope)

- Not replacing Redis for operational state (AgentSession, TelegramMessage, BridgeEvent)
- Not building semantic/RAG retrieval — the LLM handles content reasoning
- Not auto-updating PRINCIPAL.md from memory signals
- Not syncing project vault data across machines — only abstracted patterns sync
- Not building a UI for browsing episodes or patterns
- Not implementing real-time streaming of episode data during active sessions
- Phase 3 Observer integration deferred until Phases 1-2 are stable
- Phase 4 calibration deferred until real episode data exists

## Update System

The update script (`scripts/remote-update.sh`) needs changes for cross-machine pattern sync:
- Add `export_shared_patterns()` call before `git pull` (export local patterns to iCloud-synced JSON)
- Add `import_shared_patterns()` call after dependency sync (import patterns from iCloud-synced JSON)
- New config: iCloud sync directory path in `.env` (e.g., `SHARED_PATTERNS_DIR`)
- No migration steps for existing installations — new models create their Redis keys on first write

## Agent Integration

No agent integration required for Phases 1-2 — these are purely infrastructure (models, Reflections pipeline additions). The agent does not directly invoke memory operations.

Phase 3 integration (future, dependent on stable Phase 1-2):
- Observer agent (`agent/observer_agent.py` or equivalent) gains a memory query step at stage transitions
- No new MCP server needed — the Observer calls Python functions directly (same process)
- No `.mcp.json` changes needed
- Integration test: verify Observer queries memory before dispatching to worker, and that warnings/shortcuts appear in worker context

## Documentation

- [ ] Create `docs/features/behavioral-episode-memory.md` describing the memory system architecture, data models, and query patterns
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/reflections.md` to document new episode creation and pattern crystallization steps
- [ ] Code comments on fingerprint classification prompt and pattern crystallization logic

## Success Criteria

- [ ] `CyclicEpisode` Popoto model defined with fingerprint, trajectory, outcome fields — model can be created, saved, queried
- [ ] `ProceduralPattern` Popoto model defined — model can be created, saved, queried
- [ ] `AgentSession` instrumented with `tool_sequence` and `friction_events` fields, accumulated during SDLC cycles
- [ ] Fingerprint classifier prompt produces consistent classifications for test scenarios
- [ ] Reflections pipeline writes CyclicEpisode at cycle close for completed SDLC sessions
- [ ] Pattern crystallization creates/reinforces ProceduralPattern when N episodes share fingerprint cluster
- [ ] Content stripping: ProceduralPatterns contain no project-specific content (issue text, code paths, client names)
- [ ] Cross-machine sync: export/import functions serialize/deserialize patterns correctly
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (data-models)**
  - Name: models-builder
  - Role: Define CyclicEpisode and ProceduralPattern Popoto models, instrument AgentSession
  - Agent Type: builder
  - Resume: true

- **Builder (reflections-integration)**
  - Name: reflections-builder
  - Role: Add cycle-close and pattern crystallization steps to Reflections pipeline
  - Agent Type: builder
  - Resume: true

- **Builder (fingerprint-classifier)**
  - Name: classifier-builder
  - Role: Design and implement the fingerprint classification LLM prompt
  - Agent Type: builder
  - Resume: true

- **Builder (sync)**
  - Name: sync-builder
  - Role: Implement iCloud-synced pattern export/import and update script integration
  - Agent Type: builder
  - Resume: true

- **Validator (models)**
  - Name: models-validator
  - Role: Verify model CRUD operations, namespace isolation, field constraints
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write unit and integration tests for all phases
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: documentarian
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Define CyclicEpisode Model
- **Task ID**: build-episode-model
- **Depends On**: none
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Define `CyclicEpisode` Popoto model in `models/cyclic_episode.py` with fingerprint fields (problem_topology, ambiguity_at_intake, affected_layer, acceptance_criterion_defined), trajectory fields (tool_sequence, friction_events, stage_durations, deviation_count), and outcome fields (resolution_type, intent_satisfied, review_round_count, surprise_delta)
- Add `raw_ref` field linking to AgentSession, `vault` field for project namespace
- Include `cleanup_expired` classmethod consistent with other models

### 2. Define ProceduralPattern Model
- **Task ID**: build-pattern-model
- **Depends On**: none
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Define `ProceduralPattern` Popoto model in `models/procedural_pattern.py` with fingerprint_cluster, tool_sequence, success_rate, sample_count, confidence, last_reinforced fields
- Include `reinforce` method that increments sample_count and updates success_rate/confidence
- Include `cleanup_expired` classmethod

### 3. Instrument AgentSession
- **Task ID**: build-session-instrumentation
- **Depends On**: none
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `tool_sequence` (ListField) and `friction_events` (ListField) fields to AgentSession
- Add `append_tool_event(stage, tool_type)` helper method
- Add `append_friction_event(stage, tool_context, repetition_count)` helper method
- Ensure backward compatibility (null defaults, no migration needed)

### 4. Validate Models
- **Task ID**: validate-models
- **Depends On**: build-episode-model, build-pattern-model, build-session-instrumentation
- **Assigned To**: models-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify CyclicEpisode and ProceduralPattern can be created, saved, queried, deleted
- Verify AgentSession new fields work without breaking existing functionality
- Verify namespace isolation if Popoto Meta.namespace is available

### 5. Build Fingerprint Classifier
- **Task ID**: build-classifier
- **Depends On**: build-episode-model
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Design LLM prompt that takes AgentSession summary + issue context and outputs structured fingerprint (problem_topology enum, affected_layer enum, ambiguity_at_intake float, acceptance_criterion_defined bool)
- Implement as a function in `tools/fingerprint_classifier.py` or `scripts/fingerprint_classifier.py`
- Use Claude Haiku for cost efficiency
- Include fallback to `ambiguous` topology on LLM failure

### 6. Build Reflections Cycle-Close Step
- **Task ID**: build-cycle-close
- **Depends On**: build-episode-model, build-classifier, build-session-instrumentation
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Add new step to Reflections pipeline (after session analysis, step 6): read completed SDLC sessions from past 24h, classify fingerprint, write CyclicEpisode
- Skip non-SDLC sessions (use `is_sdlc_job()`)
- Skip sessions that already have a linked CyclicEpisode (idempotent)

### 7. Build Pattern Crystallization Step
- **Task ID**: build-crystallization
- **Depends On**: build-pattern-model, build-cycle-close
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Add new step to Reflections pipeline (after cycle-close): scan CyclicEpisodes for fingerprint clusters with 3+ episodes and consistent outcomes
- Create or reinforce ProceduralPattern for qualifying clusters
- Strip all content from patterns before writing to shared namespace

### 8. Build Pattern Sync
- **Task ID**: build-sync
- **Depends On**: build-pattern-model
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `export_shared_patterns()`: serialize ProceduralPatterns from shared namespace to JSON in iCloud dir
- Implement `import_shared_patterns()`: read JSON, write to local shared namespace, idempotent (skip existing, last-write-wins)
- Integrate into `scripts/remote-update.sh`

### 9. Write Tests
- **Task ID**: build-tests
- **Depends On**: validate-models, build-classifier, build-cycle-close, build-crystallization, build-sync
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for CyclicEpisode and ProceduralPattern CRUD
- Unit tests for fingerprint classifier (mock LLM responses)
- Integration tests for cycle-close step (create AgentSession, run step, verify episode exists)
- Integration tests for pattern crystallization (create N episodes, run step, verify pattern exists)
- Unit tests for export/import sync functions

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/behavioral-episode-memory.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/reflections.md` with new pipeline steps

### 11. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: models-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify documentation exists and is accurate

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Episode model importable | `.venv/bin/python -c "from models.cyclic_episode import CyclicEpisode"` | exit code 0 |
| Pattern model importable | `.venv/bin/python -c "from models.procedural_pattern import ProceduralPattern"` | exit code 0 |
| AgentSession has tool_sequence | `.venv/bin/python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'tool_sequence')"` | exit code 0 |
| Feature docs exist | `test -f docs/features/behavioral-episode-memory.md` | exit code 0 |

---

## Open Questions

1. **Fingerprint similarity threshold**: Should Phase 1 use exact match on (problem_topology, affected_layer) pairs, or allow partial matches (e.g., same topology, different layer)? Exact match is simpler and more predictable for initial data collection.

2. **Pattern crystallization threshold (N)**: The plan proposes N=3 episodes with consistent outcomes before creating a ProceduralPattern. Is 3 too low (noisy patterns) or too high (slow to crystallize)? Recommend starting at 3 and raising if patterns prove unreliable.

3. **Popoto prerequisites timeline**: The 4 Popoto extensions (atomic_increment, ListField max_length, Meta.namespace, computed_sort) are tracked as separate PRs on the Popoto repo. Should Phase 1 proceed with workarounds (manual key prefixing, manual list truncation) or wait for proper Popoto support? Recommend proceeding with workarounds.

4. **iCloud sync directory**: What is the standard iCloud-synced directory path on each machine for shared pattern storage? This needs to be consistent across machines or configurable via `.env`.

5. **Friction event definition**: What exactly constitutes a "friction event" during SDLC execution? Proposed: any tool call that is retried (same tool type invoked consecutively), any stage that takes >2x the median duration, or any test failure requiring a patch cycle. Need confirmation on the heuristic.
