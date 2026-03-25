---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/519
last_comment_id:
---

# Claude Code Memory Integration

## Problem

Claude Code sessions are amnesic. The subconscious memory system (PR #515) stores observations in Redis via the Memory model and surfaces them as `<thought>` blocks during Telegram agent sessions. Claude Code sessions -- where the developer works directly in the CLI -- are completely disconnected from this memory. No ingestion, no recall, no extraction, no feedback loop.

**Current behavior:**
- Claude Code hooks (`pre_tool_use.py`, `post_tool_use.py`, `stop.py`) log tool calls and track SDLC state but have zero memory system integration.
- The sliding window keyword extraction, bloom pre-check, and ContextAssembler query that power thought injection in agent sessions do not run in Claude Code.
- When a Claude Code session ends, decisions made and corrections received are lost. The Haiku extraction that runs on agent session completion does not run here.
- Outcome detection (bigram overlap that strengthens/weakens memories) only runs in agent sessions.

**Desired outcome:**
Memory works identically whether the session is a Telegram agent conversation or a local Claude Code session. The full loop -- ingest, recall, extract, feedback -- runs in both contexts.

## Prior Art

- **PR #515 (merged)**: Subconscious Memory -- passive Haiku extraction + thought injection via `agent/memory_hook.py` and `agent/memory_extraction.py`.
- **Issue #518 (merged)**: Memory search tool -- provides save/search/inspect/forget APIs via `tools/memory_search.py`. This is the query backend.
- **Issue #521 (merged)**: Intentional memory saves -- agent can explicitly save project-level learnings. Established importance tiers and extraction categories.
- **`agent/memory_hook.py`**: In-memory sliding window pattern (WINDOW_SIZE=3, BUFFER_SIZE=9), bloom pre-check, ContextAssembler query, thought formatting. This is the reference implementation to port.
- **`agent/memory_extraction.py`**: Haiku extraction with categorized output (CORRECTION, DECISION, PATTERN, SURPRISE), outcome detection via bigram overlap, ObservationProtocol feedback. Already has `run_post_session_extraction()`.

## Data Flow

### Flow 1: Recall (PostToolUse hook)

1. **Entry point**: PostToolUse hook fires after every tool call in Claude Code
2. **State accumulation**: Tool name + input appended to a JSON sidecar file at `data/sessions/{session_id}/memory_buffer.json` (hooks are stateless processes -- cannot use in-memory dicts)
3. **Window check**: Every WINDOW_SIZE (3) tool calls, extract keywords from the buffer
4. **Bloom pre-check**: Check ExistenceFilter for topic relevance (O(1), ~1ms)
5. **Query**: If bloom hits, run ContextAssembler query via memory search `search()` backend
6. **Injection**: Return `<thought>` blocks via hook stdout `additionalContext` field
7. **Tracking**: Record injected thought IDs in sidecar for later outcome detection

### Flow 2: Ingestion (UserPromptSubmit hook)

1. **Entry point**: UserPromptSubmit hook fires on each user prompt
2. **Quality filter**: Skip trivial prompts (under 50 chars, common words like "yes", "continue", "ok")
3. **Bloom dedup**: Check ExistenceFilter to avoid saving duplicate content
4. **Save**: Call `Memory.safe_save()` with importance=6.0, source="human"
5. **Silent failure**: All errors caught and logged, never block prompt submission

### Flow 3: Extraction (Stop hook)

1. **Entry point**: Stop hook fires when Claude Code session ends
2. **Transcript read**: Read the session transcript from `transcript_path` in hook input
3. **Haiku extraction**: Call `extract_observations_async()` from `agent/memory_extraction.py`
4. **Outcome detection**: Read injected thought IDs from sidecar, call `detect_outcomes_async()` against transcript
5. **Cleanup**: Remove session sidecar files

### Flow 4: Deja Vu Signals

1. **Trigger**: During recall (Flow 1), bloom hits on multiple keywords but ContextAssembler returns low-confidence results (below surfacing threshold)
2. **Vague recognition**: Inject `<thought>I have encountered something related to [topic] before, but the details are unclear.</thought>`
3. **Novel territory**: When bloom misses entirely on a topic that seems significant (high keyword count, no bloom hits), inject `<thought>This is new territory -- I should pay attention to what works here.</thought>`
4. **Purpose**: Shape approach (caution or curiosity) without dictating specific actions

## Architectural Impact

- **New files**: `hooks/hook_utils/memory_bridge.py` -- bridge module that wraps Memory model imports and ContextAssembler calls for use from hooks. Hooks are standalone scripts that run in fresh processes; this module handles the sys.path setup and import boilerplate.
- **Modified files**: `post_tool_use.py`, `stop.py` (add memory calls), `.claude/settings.json` (add UserPromptSubmit hook), new `user_prompt_submit.py` hook.
- **State files**: `data/sessions/{session_id}/memory_buffer.json` (tool call buffer), `data/sessions/{session_id}/injected_thoughts.json` (thought tracking for outcome detection).
- **Interface changes**: No API changes. All new behavior is internal to hooks.
- **Coupling**: Hooks import from `models/memory.py`, `agent/memory_hook.py` (keyword extraction), `agent/memory_extraction.py` (extraction + outcome detection). These are one-way dependencies.
- **Reversibility**: High. Remove hook calls, delete bridge module, revert settings.json. No schema changes.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on deja vu signal design)
- Review rounds: 1-2 (code review, hook timeout verification)

Five hook files to modify or create, one bridge module, sidecar state management, and careful timeout budgeting. The core porting of `memory_hook.py` logic is straightforward, but adapting from in-memory state to file-based state and respecting hook timeouts requires care.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Memory model available | `python -c "from models.memory import Memory; print('OK')"` | Redis Memory model for save/query |
| Memory search tool available | `python -c "from tools.memory_search import search, save; print('OK')"` | Unified search backend (issue #518) |
| ContextAssembler available | `python -c "from popoto import ContextAssembler; print('OK')"` | Memory query assembly |
| Anthropic API key | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Haiku extraction in Stop hook |
| ExistenceFilter available | `python -c "from popoto.fields.existence_filter import ExistenceFilter; print('OK')"` | Bloom pre-check |

Run all checks: `python scripts/check_prerequisites.py docs/plans/claude-code-memory-integration.md`

## Solution

### Key Elements

- **File-based sliding window in PostToolUse**: Port `agent/memory_hook.py` logic to work across stateless hook invocations using JSON sidecar files instead of in-memory dicts.
- **UserPromptSubmit ingestion hook**: New hook that saves user prompts as Memory records with dedup and quality filtering.
- **Stop hook extraction**: Extend `stop.py` to run Haiku extraction and outcome detection on session end.
- **Memory bridge module**: Shared utility in `hook_utils/memory_bridge.py` that handles imports and wraps Memory/ContextAssembler calls for hook scripts.
- **Deja vu tier**: New injection tier between silence and full thought for partial bloom matches.

### Flow

**PostToolUse** -> accumulate tool call in sidecar -> every 3 calls, extract keywords -> bloom check -> ContextAssembler query -> inject `<thought>` blocks via additionalContext

**UserPromptSubmit** -> quality filter (length, trivial check) -> bloom dedup -> `Memory.safe_save(importance=6.0)` -> silent success/failure

**Stop** -> read transcript -> `extract_observations_async()` -> `detect_outcomes_async()` with sidecar thought IDs -> cleanup sidecar files

### Technical Approach

1. **Memory bridge module** (`hooks/hook_utils/memory_bridge.py`)
   - Handles `sys.path` setup to import from project root (`models/`, `agent/`, `config/`, `tools/`)
   - Exposes `recall(session_id, tool_name, tool_input)` -> returns additionalContext string or None
   - Exposes `ingest(content)` -> saves as Memory, returns bool
   - Exposes `extract(session_id, transcript_path)` -> runs full extraction pipeline
   - All functions wrapped in try/except, return None/False on failure

2. **File-based sliding window** (inside memory bridge)
   - Sidecar path: `data/sessions/{session_id}/memory_buffer.json`
   - Structure: `{"count": N, "buffer": [{"tool_name": "...", "tool_input": {...}}, ...], "injected": [{"memory_id": "...", "content": "..."}, ...]}`
   - Atomic write via tmp + rename (same pattern as `save_sdlc_state()`)
   - Buffer capped at BUFFER_SIZE (9) entries
   - Keywords extracted using `agent.memory_hook.extract_topic_keywords()` (reuse, do not duplicate)

3. **PostToolUse integration** (`post_tool_use.py`)
   - After existing SDLC state updates, call `memory_bridge.recall(session_id, tool_name, tool_input)`
   - If recall returns additionalContext, output it via hook response JSON: `{"additionalContext": "..."}`
   - Hook must complete within 5s timeout. Memory query is Redis-only (~5-10ms). Safe margin.

4. **UserPromptSubmit hook** (new file: `user_prompt_submit.py`)
   - Read hook input from stdin (same protocol as other hooks)
   - Extract user prompt content from hook input
   - Skip if content length < 50 chars or matches trivial patterns
   - Call `memory_bridge.ingest(content)`
   - Register in `.claude/settings.json` under UserPromptSubmit hooks
   - Must not interfere with existing calendar_prompt_hook.sh

5. **Stop hook extraction** (`stop.py`)
   - After existing transcript backup, call `memory_bridge.extract(session_id, transcript_path)`
   - This runs Haiku extraction (API call, ~2-3s) and outcome detection (~1ms)
   - Stop hook timeout is currently not set (defaults vary). Ensure 15s is sufficient.
   - Cleanup sidecar files in `data/sessions/{session_id}/` after extraction

6. **Deja vu signals** (inside memory bridge recall)
   - After bloom check, if bloom hits on 2+ keywords but ContextAssembler returns no records above threshold: inject vague recognition thought
   - If bloom misses on all keywords and keyword count >= 5 (significant topic): inject novel territory thought
   - These are low-priority injections -- only fire when full recall would be silent

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `memory_bridge.recall()` returns None on any exception -- hook continues without memory injection
- [ ] `memory_bridge.ingest()` returns False on any exception -- prompt submission proceeds normally
- [ ] `memory_bridge.extract()` returns None on any exception -- session stop completes normally
- [ ] Sidecar file corruption (invalid JSON) -- reset to empty state, do not crash
- [ ] Redis unavailable -- all memory operations fail silently, hooks continue

### Empty/Invalid Input Handling
- [ ] PostToolUse with no tool_input -- recall returns None (no keywords to extract)
- [ ] UserPromptSubmit with empty prompt -- ingest skips (length filter)
- [ ] Stop with no transcript_path -- extract returns None
- [ ] Sidecar file missing (first tool call) -- initialize fresh state

### Timeout Budget
- [ ] PostToolUse recall: bloom check (~1ms) + ContextAssembler (~5-10ms) + file I/O (~1ms) = ~15ms. Well within 5s.
- [ ] UserPromptSubmit ingest: bloom check (~1ms) + Memory.safe_save (~5ms) + file I/O (~1ms) = ~10ms. Well within 15s.
- [ ] Stop extraction: transcript read (~5ms) + Haiku API call (~2-3s) + outcome detection (~5ms) = ~3-4s. Within 15s.

### Error State Rendering
- [ ] No user-visible UI. All operations are background.
- [ ] Failures logged at WARNING level to stderr (hook convention)
- [ ] Hook output format validated: only `{"additionalContext": "..."}` for PostToolUse, empty for others

## Test Impact

- [ ] `tests/unit/test_memory_hook.py` -- UPDATE: `extract_topic_keywords()` is reused by hooks; add test cases for edge inputs (empty tool_input, missing fields) to ensure robustness when called from hook context
- [ ] `tests/unit/test_memory_extraction.py` -- UPDATE: `extract_observations_async()` and `detect_outcomes_async()` are called from Stop hook; add test for sync wrapper and transcript-based input (vs agent response text)
- [ ] `tests/unit/test_memory_ingestion.py` -- UPDATE: verify ingestion quality filter logic matches the new UserPromptSubmit hook filter (same thresholds)

No other existing tests affected -- the hook changes are additive and the memory model interface is unchanged.

## Rabbit Holes

- **In-memory state sharing between hooks**: Do not attempt shared memory, pipes, or sockets between hook invocations. JSON sidecar files are the right pattern -- simple, debuggable, atomic.
- **Full transcript parsing**: Do not parse the JSONL transcript into structured conversation turns for extraction. Pass the raw text to Haiku and let it extract observations. Parsing is fragile and unnecessary.
- **Custom bloom filter for hooks**: Do not build a separate bloom filter for the hook context. Reuse the existing `Memory.bloom` ExistenceFilter directly via the bridge module.
- **Hook response protocol extensions**: Do not try to modify the Claude Code hook protocol. Use only `additionalContext` for injection -- this is the documented and supported mechanism.
- **Async in hooks**: Hooks are synchronous scripts. Do not use asyncio event loops. Use `asyncio.run()` for the one async call (Haiku extraction in Stop) and keep everything else synchronous.

## Risks

### Risk 1: Hook Timeout Pressure
**Impact:** Memory operations push hooks past their timeout, causing Claude Code to kill the hook process. Tool calls or session stops appear to hang.
**Mitigation:** All Redis operations are sub-10ms. The only slow path is Haiku extraction in the Stop hook (~3s). Budget is 15s. If extraction times out, catch the exception and skip it. Add a configurable timeout for the Haiku call itself.

### Risk 2: Sidecar File Accumulation
**Impact:** Long sessions with many tool calls accumulate sidecar files in `data/sessions/`. Disk usage grows over time.
**Mitigation:** Stop hook cleans up sidecar files after extraction. Add a TTL-based cleanup in the bridge module that removes sidecar dirs older than 7 days. The existing `data/sessions/` directory already has session-scoped subdirs.

### Risk 3: Import Overhead in Hooks
**Impact:** Importing `models.memory`, `popoto`, and `config.memory_defaults` in every hook invocation adds startup latency. Each hook is a fresh Python process.
**Mitigation:** Lazy imports -- only import memory modules when the sliding window triggers a query (every 3rd tool call). On non-query invocations, only JSON file I/O runs. Measure import time and set a ceiling (200ms).

### Risk 4: Deja Vu Signal Noise
**Impact:** Vague recognition thoughts fire too often, polluting context with unhelpful signals.
**Mitigation:** Set conservative thresholds: require 3+ bloom hits for vague recognition (not just 2), and 7+ keywords with zero bloom hits for novel territory. Start with these disabled behind a flag and enable after observing recall behavior.

## Race Conditions

- **Sidecar file contention**: Claude Code runs hooks sequentially (one at a time per session), so there is no concurrent access to sidecar files within a session. Cross-session contention is prevented by session-scoped directories.
- **Redis writes from multiple sessions**: Memory.safe_save() uses AutoKeyField for unique IDs. Concurrent saves from different sessions cannot collide.
- **Bloom filter reads during writes**: ExistenceFilter is append-only. A hook reading the bloom while another process writes is safe -- worst case is a false negative (miss a just-added memory), which is acceptable.

## No-Gos (Out of Scope)

- **Agent-side memory_hook.py changes**: Do not modify the existing `agent/memory_hook.py`. It works for Telegram agent sessions. The hook-side implementation is a parallel path, not a replacement.
- **Hook protocol changes**: Do not request changes to the Claude Code hook protocol. Work within the existing PreToolUse/PostToolUse/Stop/UserPromptSubmit event model.
- **Cross-session memory sharing**: Each session has its own sidecar state. Do not attempt to share tool call buffers or injected thought lists between sessions.
- **Memory UI**: No web interface for viewing hook-injected memories. Use the CLI: `python -m tools.memory_search search "query"`.
- **Telegram bridge changes**: The bridge already has its own memory integration. This issue is exclusively about Claude Code hooks.

## Update System

No update system changes required -- this feature modifies hook scripts and adds a utility module under `.claude/hooks/`, which are already deployed via the standard `git pull` in the update script. The `.claude/settings.json` change (adding UserPromptSubmit hook) is also tracked in git. No new pip dependencies, config files, or migration steps needed.

## Agent Integration

- **No MCP server changes**: All memory operations happen inside hook scripts, which run as subprocesses of Claude Code. The agent does not need to invoke memory operations via MCP -- they happen automatically via hooks.
- **No bridge changes**: The Telegram bridge already has its own memory integration path. This feature is hook-only.
- **Settings.json update**: The UserPromptSubmit hook registration in `.claude/settings.json` is the only configuration change. This is committed to git and deployed automatically.
- **Integration test**: Add a test that simulates hook stdin input, verifies the memory bridge functions return expected types, and confirms sidecar file creation/cleanup. This validates that the hooks can actually reach the Memory model from the hook execution context.

## Documentation

- [ ] Create `docs/features/claude-code-memory.md` describing the hook-based memory integration (recall, ingestion, extraction, deja vu)
- [ ] Update `docs/features/subconscious-memory.md` to add a "Claude Code Integration" section cross-referencing the new doc
- [ ] Add entry to `docs/features/README.md` index table for the new feature doc
- [ ] Update `CLAUDE.md` quick reference to mention memory hooks and sidecar file locations

### Inline Documentation
- [ ] Docstrings on all functions in `hooks/hook_utils/memory_bridge.py`
- [ ] Module docstring on `user_prompt_submit.py` explaining the ingestion flow
- [ ] Comments in `post_tool_use.py` and `stop.py` explaining the memory integration points

## Success Criteria

- [ ] PostToolUse hook queries memory and surfaces `<thought>` blocks during Claude Code sessions
- [ ] UserPromptSubmit hook ingests user prompts as memories (with dedup and quality filter)
- [ ] Stop hook runs Haiku extraction on session transcript
- [ ] Stop hook runs outcome detection on injected thoughts
- [ ] Deja vu signals surface for partial bloom matches and novel territory
- [ ] All hook state persisted to filesystem (JSON sidecar files, no in-memory state)
- [ ] All operations fail silently -- memory failures never block tool execution or session flow
- [ ] Hook timeouts respected (5s for PostToolUse, 15s for Stop, 15s for UserPromptSubmit)
- [ ] Integration tests covering the bridge module functions
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook-memory)**
  - Name: hook-memory-builder
  - Role: Implement memory bridge, modify hooks, create UserPromptSubmit hook
  - Agent Type: builder
  - Resume: true

- **Validator (hook-memory-validation)**
  - Name: hook-memory-validator
  - Role: Verify hook memory integration end-to-end, test timeouts and failure paths
  - Agent Type: validator
  - Resume: true

- **Documentarian (docs)**
  - Name: hook-memory-docs
  - Role: Create claude-code-memory.md, update subconscious-memory.md and CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create Memory Bridge Module
- **Task ID**: build-memory-bridge
- **Depends On**: none
- **Validates**: tests/unit/test_memory_bridge.py (create) -- test recall, ingest, extract functions with mocked Redis
- **Assigned To**: hook-memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/hook_utils/memory_bridge.py`
- Implement `recall(session_id, tool_name, tool_input)` with file-based sliding window
- Implement `ingest(content)` with quality filter and bloom dedup
- Implement `extract(session_id, transcript_path)` wrapping async extraction in `asyncio.run()`
- Implement `load_sidecar()` / `save_sidecar()` with atomic write pattern
- Implement deja vu signal logic (partial bloom match, novel territory detection)
- All functions wrapped in try/except, fail silently

### 2. Integrate Recall into PostToolUse Hook
- **Task ID**: build-post-tool-use-recall
- **Depends On**: build-memory-bridge
- **Validates**: tests/unit/test_post_tool_use_memory.py (create) -- test hook output includes additionalContext
- **Assigned To**: hook-memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `.claude/hooks/post_tool_use.py` to call `memory_bridge.recall()` after SDLC state updates
- Output hook response JSON with `additionalContext` when recall returns thoughts
- Ensure hook stays within 5s timeout

### 3. Create UserPromptSubmit Hook
- **Task ID**: build-user-prompt-submit
- **Depends On**: build-memory-bridge
- **Validates**: tests/unit/test_user_prompt_submit.py (create) -- test quality filter and ingestion
- **Assigned To**: hook-memory-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with task 2)
- Create `.claude/hooks/user_prompt_submit.py`
- Read hook input, extract prompt content
- Apply quality filter (min length, trivial prompt detection)
- Call `memory_bridge.ingest(content)`
- Register in `.claude/settings.json` under UserPromptSubmit (append to existing hooks list)

### 4. Integrate Extraction into Stop Hook
- **Task ID**: build-stop-extraction
- **Depends On**: build-memory-bridge
- **Validates**: tests/unit/test_stop_memory.py (create) -- test extraction call and sidecar cleanup
- **Assigned To**: hook-memory-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with tasks 2-3)
- Modify `.claude/hooks/stop.py` to call `memory_bridge.extract()` after transcript backup
- Add sidecar cleanup (remove `data/sessions/{session_id}/memory_buffer.json` and `injected_thoughts.json`)
- Ensure hook stays within 15s timeout (Haiku call is the bottleneck)

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-post-tool-use-recall, build-user-prompt-submit, build-stop-extraction
- **Assigned To**: hook-memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify hook timeout budgets with timing measurements
- Test failure paths: Redis unavailable, corrupt sidecar, missing transcript
- Test deja vu signal thresholds

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: hook-memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/claude-code-memory.md`
- Update `docs/features/subconscious-memory.md`
- Update `docs/features/README.md` index
- Update `CLAUDE.md` quick reference

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: hook-memory-validator
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
| Memory bridge importable | `python -c "sys.path.insert(0, '.claude/hooks'); from hook_utils.memory_bridge import recall, ingest, extract; print('OK')"` | output OK |
| PostToolUse outputs additionalContext | `echo '{"tool_name":"Read","tool_input":{"file_path":"test.py"},"session_id":"test"}' \| python .claude/hooks/post_tool_use.py` | valid JSON or empty |
| UserPromptSubmit hook exists | `test -f .claude/hooks/user_prompt_submit.py && echo OK` | output OK |
| Settings.json has UserPromptSubmit | `python -c "import json; d=json.load(open('.claude/settings.json')); print('OK' if 'UserPromptSubmit' in d.get('hooks',{}) else 'MISSING')"` | output OK |
| Sidecar cleanup works | `python -c "from pathlib import Path; p=Path('data/sessions/test-cleanup'); p.mkdir(parents=True, exist_ok=True); (p/'memory_buffer.json').write_text('{}'); print('OK')"` | output OK |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Deja vu default state**: Should deja vu signals be enabled by default or gated behind a flag (e.g., `data/deja-vu-enabled` sentinel file)? The risk of noise suggests starting disabled, but the feature loses value if never enabled.

2. **Stop hook timeout**: The current Stop hook timeout in settings.json is not explicitly set (uses Claude Code default). Should we increase it to 15s to accommodate Haiku extraction, or is the default sufficient?

3. **UserPromptSubmit hook ordering**: The existing calendar_prompt_hook.sh runs first. Should the memory ingestion hook run before or after it? Order may matter if the calendar hook modifies the prompt content.
