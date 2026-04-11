---
slug: pm-autonomous-skills
status: Planning
type: feature
appetite: Large
tracking: https://github.com/tomcounsell/ai/issues/853
created: 2026-04-11
last_comment_id:
---

# PM Autonomous Skill Lifecycle

## Problem

The PM role's skill repertoire is entirely static. Every skill the PM can use must be manually authored as a SKILL.md file by a human developer. There is no mechanism for the PM to discover repeatable patterns in its own work, propose new skills, track which skills perform well, or retire skills that consistently fail.

**Current behavior:**

- All 32 skills are static SKILL.md files requiring manual authorship
- `/skillify` exists but is human-triggered and interview-driven -- the PM cannot initiate it
- The `_SKILL_TO_STAGE` mapping in `agent/hooks/pre_tool_use.py` is a hardcoded dict with no runtime registration
- Skill invocations are tracked for pipeline stage progression but not for effectiveness (success rate, patch frequency, human override rate)
- No mechanism exists to compose existing skills into compound workflows
- No mechanism exists to deprecate or retire underperforming skills

**Desired outcome:**

The PM session can autonomously discover, propose, evaluate, and manage skills over time. Specifically: (1) detect repeatable multi-step patterns in session history, (2) propose candidate skills with draft SKILL.md content, (3) track skill effectiveness metrics, (4) flag underperforming skills for review. All generated skills require human approval before entering production use.

## Freshness Check

**Baseline commit:** `41f5715`
**Issue filed at:** 2026-04-11
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/hooks/pre_tool_use.py:37-46` -- `_SKILL_TO_STAGE` dict still hardcoded with 8 entries -- still holds
- `.claude/skills/skillify/SKILL.md` -- interview-driven workflow, human-only invocation (`disable-model-invocation: true`) -- still holds
- `.claude/skills/do-skills-audit/SKILL.md` -- deterministic validation with 12 rules -- still holds
- `agent/memory_extraction.py` -- post-session Haiku extraction producing categorized JSON observations -- still holds

**Cited sibling issues/PRs re-checked:**
- #854 (unified analytics) -- still open, status Building. Plan at `docs/plans/unified-analytics.md`

**Commits on main since issue was filed (touching referenced files):**
- `41f57151` "Unified analytics system for metrics collection and dashboard (#895)" -- adds `analytics/` module with `record_metric()` API. Directly relevant: provides the metrics infrastructure this plan will use for effectiveness tracking.

**Active plans in `docs/plans/` overlapping this area:** `unified-analytics.md` overlaps on metrics infrastructure but does not address skill-specific metrics.

**Notes:** The analytics module (`analytics/collector.py`) with `record_metric()` is now available on main, which simplifies the effectiveness tracking component -- we can use it directly instead of building custom Redis counters.

## Prior Art

- **PR #794**: Wire Skill tool invocations into PipelineStateMachine stage tracking -- established the pattern of instrumenting skill invocations in `pre_tool_use.py`. Relevant as the integration point for effectiveness tracking.
- **PR #640**: Standardize audit skill naming and quality -- established `/do-skills-audit` as the quality gate. Relevant as the validation gate for generated skills.
- **Issue #544**: PM SDLC decision rules -- established PM autonomy patterns for pipeline decisions. Relevant as prior art for PM autonomous behavior.

No prior issues found related to autonomous skill generation or skill lifecycle management.

## Data Flow

1. **Entry point**: Post-session extraction (`agent/memory_extraction.py`) produces categorized observations from completed sessions
2. **Pattern detection**: A new `tools/skill_lifecycle.py` module queries Memory records for recurring patterns (category=`pattern`, high frequency, similar file paths / tags across sessions)
3. **Candidate generation**: When a pattern threshold is met, the PM proposes a candidate skill by generating a draft SKILL.md and writing it to `.claude/skills/_candidates/`
4. **Validation gate**: The candidate passes through `/do-skills-audit` validation (existing 12-rule checker)
5. **Human approval**: Candidates remain in `_candidates/` until a human promotes them to the active skills directory
6. **Effectiveness tracking**: `pre_tool_use.py` Skill hook calls `record_metric("skill.invocation", ...)` with skill name and session context. Post-session extraction records outcome (success/failure/patch-needed) via `record_metric("skill.outcome", ...)`
7. **Lifecycle management**: `tools/skill_lifecycle.py` queries analytics to compute per-skill effectiveness scores. Skills below threshold are flagged for review in the dashboard.

## Architectural Impact

- **New dependencies**: `analytics.collector.record_metric` (already on main), no external deps
- **Interface changes**: `pre_tool_use.py::_handle_skill_tool_start()` gains a `record_metric` call (additive, no signature change). New `tools/skill_lifecycle.py` CLI module.
- **Coupling**: Loose coupling via analytics sink -- skill tracking writes to the same analytics pipeline as everything else. Pattern detection reads from the Memory model (existing dependency).
- **Data ownership**: Skill effectiveness data owned by analytics (SQLite + Redis). Candidate skills owned by filesystem (`.claude/skills/_candidates/`).
- **Reversibility**: High. Remove `_candidates/` directory, remove `record_metric` calls from `pre_tool_use.py`, delete `tools/skill_lifecycle.py`. No schema migrations.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment on pattern detection thresholds, candidate skill format, dashboard integration)
- Review rounds: 2+ (effectiveness tracking instrumentation review, pattern detection logic review)

## Prerequisites

No prerequisites -- this work builds on the existing analytics module (`analytics/`), memory system (`models/memory.py`), and skill infrastructure (`.claude/skills/`), all of which are on main.

## Solution

### Key Elements

- **Skill Effectiveness Tracker**: Instruments skill invocations and outcomes in the analytics pipeline, providing per-skill success rate, patch frequency, and human override rate
- **Pattern Detector**: Analyzes post-session Memory observations to identify repeatable multi-step workflows that appear across sessions
- **Candidate Skill Generator**: Produces draft SKILL.md files from detected patterns, staged in a `_candidates/` directory for human review
- **Skill Lifecycle Manager**: CLI tool that queries effectiveness metrics, flags underperforming skills, and provides a queryable skill health report

### Flow

**Session completes** -> Post-session extraction saves observations -> Pattern detector identifies recurring workflow -> PM proposes candidate skill -> `/do-skills-audit` validates -> Human reviews in `_candidates/` -> Human promotes to active -> Effectiveness tracking measures ongoing performance -> Lifecycle manager flags underperformers

### Technical Approach

- Instrument `pre_tool_use.py::_handle_skill_tool_start()` to emit `record_metric("skill.invocation", 1, {"skill": name, "session_id": sid})` on every skill invocation
- Add a `post_tool_use` or post-session hook that records outcome: `record_metric("skill.outcome", 1, {"skill": name, "outcome": "success|failure|patch_needed"})`
- Pattern detection uses existing Memory model queries: filter by `category="pattern"`, group by `tags` and `file_paths`, identify clusters appearing 3+ times within a sliding window
- Candidate generation answers the `/skillify` interview questions programmatically from session history, producing a valid SKILL.md with frontmatter
- `_candidates/` directory uses a naming convention: `_candidates/{skill-name}/SKILL.md` with added frontmatter fields `proposed_at`, `source_sessions`, `pattern_description`
- The PM's bash allowlist in `pre_tool_use.py` gets `python -m tools.skill_lifecycle` entries for read-only queries
- Effectiveness query: `python -m tools.skill_lifecycle report` outputs per-skill metrics (invocation count, success rate, avg patches per invocation, last used)
- Dashboard integration via `dashboard.json` endpoint (existing pattern)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_metric` in `analytics/collector.py` already wraps in try/except -- verify skill-specific metric names follow the same pattern
- [ ] Pattern detection in `tools/skill_lifecycle.py` must handle empty Memory result sets gracefully (no observations = no patterns, not a crash)
- [ ] Candidate SKILL.md generation must handle malformed session history (missing fields, empty observations)

### Empty/Invalid Input Handling
- [ ] `skill_lifecycle report` with no analytics data returns an empty report, not an error
- [ ] Pattern detection with zero Memory records returns "no patterns detected"
- [ ] Candidate generation with insufficient session data (fewer than 3 pattern occurrences) declines to generate

### Error State Rendering
- [ ] CLI `skill_lifecycle report` output clearly distinguishes "no data" from "skill performing poorly"
- [ ] Dashboard skill health section shows "no data" state when analytics has no skill metrics

## Test Impact

No existing tests affected -- this is a greenfield feature with no prior test coverage for skill lifecycle management. The only touched existing file is `agent/hooks/pre_tool_use.py` (adding a `record_metric` call), but the existing tests in `tests/unit/test_pm_session_permissions.py` and `tests/unit/test_pre_tool_use.py` test permission enforcement, not metric emission, so they will not break.

## Rabbit Holes

- **Real-time skill hot-loading**: Claude Code discovers skills at session start. Adding skills mid-session is a platform constraint -- do not try to solve it. New skills take effect on the next session.
- **Automatic skill promotion**: Tempting to auto-promote candidates after N successful test runs, but human approval is a hard requirement. The `_candidates/` directory is the staging area, not a testing ground.
- **Natural language skill composition**: Composing skills by chaining their SKILL.md instructions sounds powerful but is a rabbit hole. Stick to detecting patterns and generating standalone skills.
- **Retroactive pattern detection on all historical sessions**: Limit pattern detection to the last 30 days of Memory observations. Full historical analysis is computationally wasteful and produces stale patterns.

## Risks

### Risk 1: Pattern detection produces too many false positives
**Impact:** `_candidates/` directory fills with low-quality skill proposals, creating review fatigue for the human
**Mitigation:** Set a high threshold (5+ occurrences of a pattern within 14 days) before proposing a candidate. Include a `pattern_confidence` score in candidate frontmatter so humans can triage quickly. Rate-limit candidate generation to at most 1 per day.

### Risk 2: Effectiveness metrics are too coarse to be actionable
**Impact:** Success/failure binary doesn't capture the nuance of "skill worked but required 3 patches"
**Mitigation:** Track three distinct outcomes: `success` (no patches needed), `partial` (patches required but eventually passed), `failure` (skill output was abandoned). Also track patch count per invocation as a separate metric.

### Risk 3: PM session's read-only constraints block skill proposal workflow
**Impact:** The PM cannot write files to `.claude/skills/_candidates/` directly
**Mitigation:** The PM delegates candidate file creation to a dev-session. The PM's role is proposing and requesting; the dev-session writes the file. This follows the existing pattern for all PM mutations.

## Race Conditions

No race conditions identified -- all operations are sequential within a single session. Skill effectiveness metrics use the analytics `record_metric()` API which handles concurrent writes via SQLite WAL mode. Pattern detection is a batch read operation with no write contention.

## No-Gos (Out of Scope)

- **Automatic skill deployment** -- generated skills always require human approval before activation
- **Cross-project skill sharing** -- skills are scoped to this repository; no sync to other machines
- **Skill versioning** -- no version history for generated skills; use git history
- **Real-time effectiveness dashboards** -- effectiveness data is queryable via CLI; live dashboard charts are deferred to a future iteration
- **Modifying existing static skills** -- this feature only creates new candidate skills; it does not modify or deprecate existing skills automatically

## Update System

No update system changes required -- this feature adds new Python modules and a new directory (`_candidates/`) that are handled by the existing `git pull` step in the update script. No new dependencies, config files, or migration steps are needed.

## Agent Integration

- **PM bash allowlist**: Add `python -m tools.skill_lifecycle` prefixes to `PM_BASH_ALLOWED_PREFIXES` in `agent/hooks/pre_tool_use.py` so the PM can query skill effectiveness reports
- **No MCP server changes needed**: The skill lifecycle tool is a CLI module invoked via Bash, following the same pattern as `python -m tools.memory_search` and `python -m tools.analytics`
- **Bridge changes**: None. The bridge has no SDLC awareness and does not need to know about skill lifecycle
- **Integration test**: Verify that `python -m tools.skill_lifecycle report` is callable from a PM session (passes the bash allowlist check)

## Documentation

- [ ] Create `docs/features/pm-autonomous-skills.md` describing the autonomous skill lifecycle (discovery, generation, effectiveness tracking, lifecycle management)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `.claude/skills/README.md` to document the `_candidates/` directory and its role in the progressive disclosure hierarchy (L0 should NOT include candidates)
- [ ] Add inline docstrings to `tools/skill_lifecycle.py` for all public functions
- [ ] Update `config/personas/project-manager.md` to reference the skill lifecycle query commands

## Success Criteria

- [ ] `record_metric("skill.invocation", ...)` is emitted on every skill invocation via the pre_tool_use hook
- [ ] `record_metric("skill.outcome", ...)` is emitted after each skill-bearing session completes
- [ ] `python -m tools.skill_lifecycle report` outputs per-skill effectiveness metrics (invocation count, success rate, patch frequency)
- [ ] `python -m tools.skill_lifecycle detect-patterns` identifies repeatable workflows from Memory observations
- [ ] `python -m tools.skill_lifecycle propose --pattern <id>` generates a valid candidate SKILL.md in `_candidates/`
- [ ] Generated candidates pass `/do-skills-audit` validation
- [ ] `.claude/skills/_candidates/` directory exists with a README explaining the approval workflow
- [ ] PM session can run `python -m tools.skill_lifecycle report` (passes bash allowlist)
- [ ] Existing static skills continue to work unchanged (no regressions in `tests/unit/test_pre_tool_use.py`)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (effectiveness-tracking)**
  - Name: tracker-builder
  - Role: Instrument skill invocations and outcomes in analytics pipeline
  - Agent Type: builder
  - Resume: true

- **Builder (pattern-detection)**
  - Name: pattern-builder
  - Role: Implement pattern detection from Memory observations and candidate generation
  - Agent Type: builder
  - Resume: true

- **Builder (lifecycle-cli)**
  - Name: lifecycle-builder
  - Role: Build the skill_lifecycle CLI tool with report, detect-patterns, and propose subcommands
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow from invocation tracking through pattern detection to candidate generation
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 -- Core:** builder, validator, test-engineer, documentarian

## Step by Step Tasks

### 1. Instrument Skill Invocation Tracking
- **Task ID**: build-effectiveness-tracking
- **Depends On**: none
- **Validates**: tests/unit/test_skill_effectiveness.py (create)
- **Assigned To**: tracker-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `record_metric("skill.invocation", 1, {"skill": skill_name, "session_id": session_id})` call in `pre_tool_use.py::_handle_skill_tool_start()` after the existing `_start_pipeline_stage` call
- Add post-session skill outcome recording in `agent/memory_extraction.py::run_post_session_extraction()` -- after extraction completes, emit `record_metric("skill.outcome", 1, {"skill": skill_name, "outcome": outcome})` where outcome is derived from whether patches were needed
- Create `tests/unit/test_skill_effectiveness.py` with tests for metric emission on skill invocation and outcome recording

### 2. Build Pattern Detection Module
- **Task ID**: build-pattern-detection
- **Depends On**: none
- **Validates**: tests/unit/test_skill_pattern_detection.py (create)
- **Assigned To**: pattern-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/skill_lifecycle.py` with pattern detection logic: query Memory records filtered by `category="pattern"`, group by overlapping `tags` and `file_paths`, identify clusters appearing 3+ times within a 14-day window
- Implement candidate SKILL.md generation: given a detected pattern, produce a valid SKILL.md with proper frontmatter (name, description, when_to_use, allowed-tools) by analyzing the session observations that formed the pattern
- Create `.claude/skills/_candidates/README.md` explaining the candidate approval workflow
- Create `tests/unit/test_skill_pattern_detection.py` with tests for pattern grouping, threshold enforcement, and SKILL.md generation format

### 3. Build Skill Lifecycle CLI
- **Task ID**: build-lifecycle-cli
- **Depends On**: build-effectiveness-tracking, build-pattern-detection
- **Validates**: tests/unit/test_skill_lifecycle_cli.py (create)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement CLI subcommands: `report` (query analytics for per-skill metrics), `detect-patterns` (run pattern detection), `propose --pattern <id>` (generate candidate)
- Add `python -m tools.skill_lifecycle` prefixes to `PM_BASH_ALLOWED_PREFIXES` in `pre_tool_use.py`
- Create `tests/unit/test_skill_lifecycle_cli.py` with tests for CLI argument parsing and output format

### 4. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-lifecycle-cli
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `record_metric` calls are wired correctly in pre_tool_use.py
- Verify PM session can run `python -m tools.skill_lifecycle report` (bash allowlist check)
- Verify generated candidate SKILL.md files pass `/do-skills-audit` validation
- Verify existing tests in `tests/unit/test_pre_tool_use.py` still pass
- Run `python -m ruff check .` and `python -m ruff format --check .`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-autonomous-skills.md`
- Add entry to `docs/features/README.md` index table
- Update `.claude/skills/README.md` with `_candidates/` directory documentation
- Update `config/personas/project-manager.md` with skill lifecycle query commands

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lifecycle CLI runs | `python -m tools.skill_lifecycle report --help` | exit code 0 |
| Candidates dir exists | `ls .claude/skills/_candidates/README.md` | exit code 0 |
| PM allowlist updated | `grep -c "skill_lifecycle" agent/hooks/pre_tool_use.py` | output > 0 |
| Feature docs exist | `ls docs/features/pm-autonomous-skills.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. What minimum number of pattern occurrences should trigger a candidate skill proposal? (Current plan says 5 within 14 days -- is that too conservative or too aggressive?)
2. Should the PM be able to request pattern detection on demand (e.g., "check for new skill patterns"), or should it only run as part of post-session extraction?
3. Should candidate skills include a "trial period" metric where they are tracked separately before promotion, or is the human approval gate sufficient?
