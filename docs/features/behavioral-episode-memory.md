# Behavioral Episode Memory System

Structural behavioral memory for completed SDLC cycles. Episodes capture what happened (fingerprint, trajectory, outcome) and patterns crystallize from repeated episodes to provide warnings and shortcuts for similar future work.

## Problem

Every SDLC cycle is treated as novel. The agent has no mechanism to recognize structural similarity between current work and past work. The Reflections pipeline performs daily maintenance but does not extract reusable behavioral patterns.

## Architecture

```
SDLC Cycle Completes
    |
    v
Reflections Step 16: Cycle-Close
    |-- Read completed AgentSession
    |-- Classify fingerprint (Haiku LLM call)
    |-- Write CyclicEpisode to project vault
    v
Reflections Step 17: Pattern Crystallization
    |-- Scan episodes by fingerprint cluster
    |-- 3+ episodes with consistent outcomes?
    |-- Create/reinforce ProceduralPattern
    v
[Future] Observer reads patterns at stage transitions
```

## Data Models

### CyclicEpisode (`models/cyclic_episode.py`)

Structural behavioral record of a completed SDLC cycle. Stored in Redis via Popoto.

**Fingerprint fields** (used for pattern matching):
- `problem_topology`: one of `new_feature`, `bug_fix`, `refactor`, `integration`, `configuration`, `ambiguous`
- `affected_layer`: one of `model`, `bridge`, `agent`, `tool`, `config`, `test`, `docs`, `infra`, `unknown`
- `ambiguity_at_intake`: float 0.0-1.0
- `acceptance_criterion_defined`: bool

**Trajectory fields** (what happened):
- `tool_sequence`: list of `"{stage}:{tool_type}"` strings (capped at 50)
- `friction_events`: list of `"{stage}|{description}|{count}"` strings (capped at 20)
- `stage_durations`: dict of stage name to seconds
- `deviation_count`: int

**Outcome fields** (how it ended):
- `resolution_type`: one of `clean_merge`, `patch_required`, `abandoned`, `deferred`, `unknown`
- `intent_satisfied`: bool
- `review_round_count`: int
- `surprise_delta`: float

**Namespace isolation**: `vault` KeyField with values like `mem:{project_key}` for project scope.

### ProceduralPattern (`models/procedural_pattern.py`)

Crystallized from 3+ episodes sharing a fingerprint cluster with consistent outcomes. Contains NO project-specific content -- safe for cross-machine sync.

- `problem_topology` + `affected_layer`: fingerprint cluster this pattern matches
- `canonical_tool_sequence`: most common tool sequence from contributing episodes
- `warnings`: friction-derived warnings for the Observer
- `shortcuts`: suggested shortcuts (future)
- `success_rate`, `sample_count`, `confidence`: reinforcement metrics
- `reinforce(success)`: read-modify-write update on new episode

### AgentSession Instrumentation

New fields on `AgentSession`:
- `tool_sequence`: ListField, populated by `append_tool_event(stage, tool_type)`
- `friction_events`: ListField, populated by `append_friction_event(stage, description, count)`

Both fields have capped append helpers (50 and 20 items respectively) and null defaults for backward compatibility.

## Fingerprint Classifier (`scripts/fingerprint_classifier.py`)

Single Claude Haiku LLM call that takes session summary and metadata, returns structured fingerprint JSON. Falls back to `ambiguous`/`unknown` defaults on any failure (timeout, malformed response, API error).

## Pattern Sync (`scripts/pattern_sync.py`)

Cross-machine pattern sharing via JSON files:
- `export_shared_patterns()`: atomic write (temp + rename) to sync directory
- `import_shared_patterns()`: idempotent import with last-write-wins, sample_count tiebreaker
- Sync directory configurable via `SHARED_PATTERNS_DIR` env var

## Reflections Integration

Two new steps added to the Reflections pipeline:

**Step 16 - Episode Cycle-Close**:
- Queries completed SDLC sessions from past 24 hours
- Skips non-SDLC sessions and already-linked episodes (idempotent)
- Classifies fingerprint via LLM
- Creates CyclicEpisode record

**Step 17 - Pattern Crystallization**:
- Groups episodes by fingerprint cluster (topology + layer)
- Creates ProceduralPattern when 3+ episodes exist with consistent outcomes
- Reinforces existing patterns with new data
- Strips all content before writing to shared namespace

**Step 12 - Redis Cleanup**: Extended to clean expired episodes (180 days) and patterns (365 days).

## Phase Roadmap

- **Phase 1-2** (this implementation): Models, instrumentation, classifier, Reflections integration, sync
- **Phase 3** (future): Observer queries patterns at stage transitions, delivers warnings/shortcuts
- **Phase 4** (future): Retrospective classification of past sessions, fingerprint taxonomy tuning

## Related

- Issue: https://github.com/tomcounsell/ai/issues/376
- Plan: `docs/plans/behavioral_episode_memory.md`
- Prior art: Issue #323 (MuninnDB, superseded), Issue #309 (Observer Agent)
