---
status: In Progress
type: refactor
appetite: Medium
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/488
last_comment_id:
---

# Consolidate SDLC Stage Tracking, Remove Legacy Fields, Add Cruft Auditor

## Problem

The SDLC pipeline has accumulated duplicative and disconnected systems that prevent reliable stage enforcement:

- Two fields for stage state (`stage_states` vs `sdlc_stages`) with a 4-tier fallback chain in `is_sdlc`
- Two pipeline state modules (`bridge/pipeline_state.py` vs `agent/pipeline_state.py`) with the same class name but entirely different implementations and stage lists
- `PipelineStateMachine.start_stage()` and `complete_stage()` have zero callers in production -- skills complete work but never record it
- 9 dead enrichment fields on AgentSession, a deprecated `last_transition_at` field still in use, and orphaned Observer monitoring code
- Dual `get_stage_progress()` implementations (history parsing vs state machine) with summarizer fallback between them

This directly caused PR #483 merging without pipeline stage progress -- there was no durable record of stage completion.

## Prior Art

- **bridge/pipeline_state.py** (PipelineStateMachine): Runtime, Redis-backed via AgentSession.stage_states. Well-designed but unused -- no skill calls start_stage/complete_stage.
- **agent/pipeline_state.py**: Offline, file-based at `data/pipeline/{slug}/state.json`. Used by do-build for resumable builds. Different stage names (lowercase, includes "branch", "implement", "commit", "pr").
- **AgentSession.get_stage_progress()**: Parses `[stage]` history entries with string matching. Used by `has_remaining_stages()` and `has_failed_stage()`.
- **monitoring/telemetry.py**: Orphaned Observer telemetry module. Observer was removed in SDLC Redesign Phase 2.

## Solution

Six phases, ordered by criticality. Each phase is independently shippable.

### Phase 1: Consolidate stage tracking (Critical)

Retire `sdlc_stages` field, consolidate to `stage_states` only, and wire PipelineStateMachine into each skill's completion path.

- [ ] Remove `sdlc_stages` field from AgentSession model
- [ ] Update `create_dev()` factory to stop accepting/populating `sdlc_stages`; pass initial stage state via `stage_states` instead
- [ ] Simplify `_get_sdlc_stages_dict()` to read only `stage_states` (remove fallback to `sdlc_stages`)
- [ ] Simplify `is_sdlc` property: check only `stage_states` content and `classification_type == "sdlc"` (remove 4-tier cascade to 2 checks)
- [ ] Add a `record_stage_completion(session, stage)` helper function in `bridge/pipeline_state.py` that calls `start_stage()` then `complete_stage()` in one shot (for skills that complete atomically)
- [ ] Wire stage recording into each SDLC skill's completion path: update `.claude/skills/sdlc/SKILL.md` to instruct DevSession agents to call `PipelineStateMachine.complete_stage()` at the end of each stage
- [ ] Update `AgentSession.has_remaining_stages()` and `has_failed_stage()` to use `stage_states` via PipelineStateMachine instead of history parsing
- [ ] Remove `get_stage_progress()` history-parsing method (replaced by PipelineStateMachine.get_display_progress())
- [ ] Update `/do-merge` gate check to read `stage_states` via PipelineStateMachine and report incomplete stages as a strong recommendation

### Phase 2: Eliminate naming conflict

- [ ] Rename `agent/pipeline_state.py` to `agent/build_pipeline.py`
- [ ] Update all imports referencing `agent.pipeline_state` (grep for `from agent.pipeline_state` and `import agent.pipeline_state`)

### Phase 3: Remove dead enrichment fields

- [ ] Remove fields from AgentSession: `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `chat_id_for_enrichment`
- [ ] Remove the "deprecated" comment block (lines 93-101)
- [ ] Audit `tools/telegram_history/` and any remaining readers of these fields; update to read from TelegramMessage instead

### Phase 4: Clean up Observer remnants

- [ ] Delete `monitoring/telemetry.py`
- [ ] Remove `check_observer_telemetry()` stub from `monitoring/health.py`
- [ ] Remove the call to `check_observer_telemetry()` from `get_overall_health()`
- [ ] Fix "Observer / Steering fields" comment on AgentSession line 154 to just "Steering fields"
- [ ] Remove `tests/unit/test_telemetry.py`

### Phase 5: Remove deprecated fields and dual progress path

- [ ] Remove `last_transition_at` field from AgentSession
- [ ] Update `log_lifecycle_transition()` to derive duration from the last history entry timestamp instead
- [ ] Remove dual `get_stage_progress()` path in summarizer -- use only PipelineStateMachine.get_display_progress()

### Phase 6: Add legacy cruft auditor to /do-pr-review

- [ ] Create a new subagent spec at `.claude/agents/cruft-auditor.md` that scans PR diffs for legacy patterns: deprecated fields still being read/written, fallback chains, dual implementations, dead imports, stale comments referencing deleted systems
- [ ] Update `/do-pr-review` skill to dispatch the cruft auditor subagent alongside existing review steps
- [ ] Include findings as a "Legacy Cruft" subsection in PR review output (alongside blockers, tech debt, nits)

## Prerequisites

None -- all changes are internal refactoring with no external dependencies.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_stage_completion()` must handle the case where session has no stage_states yet (fresh session) -- initialize defaults then record
- [ ] `PipelineStateMachine.__init__` already handles None/empty/corrupt stage_states -- verify this still works after removing sdlc_stages fallback
- [ ] `log_lifecycle_transition()` must handle missing history entries gracefully when deriving duration (fallback to started_at/created_at)

### Empty/Invalid Input Handling
- [ ] `_get_sdlc_stages_dict()` returns None when stage_states is None (no fallback to sdlc_stages)
- [ ] `is_sdlc` returns False for sessions with no stage_states and no classification_type
- [ ] Cruft auditor produces empty "Legacy Cruft" section (not an error) when no patterns found

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py` -- UPDATE: tests reference `stage_states` only, remove any sdlc_stages setup
- [ ] `tests/unit/test_pipeline_state.py` -- UPDATE: rename imports from `agent.pipeline_state` to `agent.build_pipeline`
- [ ] `tests/unit/test_sdlc_mode.py` -- UPDATE: remove tests for sdlc_stages field, update is_sdlc tests to reflect simplified 2-check logic
- [ ] `tests/unit/test_telemetry.py` -- DELETE: module being removed
- [ ] `tests/unit/test_dev_session_registration.py` -- UPDATE: remove sdlc_stages from create_dev() calls
- [ ] `tests/integration/test_agent_session_lifecycle.py` -- UPDATE: remove references to sdlc_stages, last_transition_at, dead enrichment fields
- [ ] `tests/integration/test_stage_aware_auto_continue.py` -- UPDATE: use stage_states instead of history-based get_stage_progress()
- [ ] `tests/integration/test_lifecycle_transition.py` -- UPDATE: adjust for last_transition_at removal, duration derived from history
- [ ] `tests/e2e/test_session_lifecycle.py` -- UPDATE: remove references to deprecated fields
- [ ] `tests/e2e/test_session_spawning.py` -- UPDATE: remove sdlc_stages from DevSession creation assertions

## Rabbit Holes

- Migrating existing Redis data for live sessions that have sdlc_stages set -- not worth it; sessions are short-lived and old data expires naturally
- Building a full static analysis tool for the cruft auditor -- a prompt-based LLM scan of the diff is sufficient
- Adding database migrations or versioning for Popoto model changes -- Popoto is schema-less, field removal just means the field stops being written

## Risks

### Risk 1: Breaking in-flight SDLC sessions
**Mitigation:** Field removal is backward-compatible in Popoto (read returns None for removed fields). Deploy during low-activity window.

### Risk 2: Cruft auditor false positives
**Mitigation:** Frame findings as advisory ("Legacy Cruft" subsection) not blockers. Human reviewer decides severity.

### Risk 3: Stage recording adds friction to skill execution
**Mitigation:** The `record_stage_completion()` helper is a single function call. Skills already save session state.

## No-Gos

- Not adding new pip dependencies
- Not changing the PipelineStateMachine's core state transition logic (it is well-designed, just unwired)
- Not building a migration script for existing Redis session data
- Not making the cruft auditor a hard blocker on PR merges

## Update System

No update system changes required -- this is an internal refactoring of models and monitoring code. All changes propagate via standard `git pull` in the update script.

## Agent Integration

No new MCP server exposure needed. The changes are internal to the bridge and model layer:
- PipelineStateMachine is already importable by agent code via `bridge.pipeline_state`
- The cruft auditor is a new subagent spec (markdown file) dispatched by the existing `/do-pr-review` skill -- no MCP wiring needed
- The stage recording calls happen inside DevSession agent context which already has full filesystem access

## Success Criteria

- [ ] One field for stage state (`stage_states`), no `sdlc_stages` field, no fallback chains
- [ ] Each SDLC skill records stage completion via PipelineStateMachine
- [ ] `/do-merge` checks stage_states and reports missing stages
- [ ] No dead enrichment fields, no orphaned Observer code, no deprecated `last_transition_at`
- [ ] `agent/pipeline_state.py` renamed to `agent/build_pipeline.py` (no naming collision)
- [ ] `/do-pr-review` includes a legacy cruft audit subsection
- [ ] All affected tests updated, passing

## Documentation

- [ ] Create `docs/features/sdlc-stage-tracking.md` describing the consolidated stage tracking architecture (PipelineStateMachine as single source of truth, how skills record completion)
- [ ] Update `docs/features/README.md` index table with new entry
- [ ] Update `.claude/skills/sdlc/SKILL.md` if stage recording instructions are added
