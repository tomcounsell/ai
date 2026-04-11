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

The PM session can autonomously discover, create, evaluate, and manage skills over time. Specifically: (1) detect friction patterns and repeatable multi-step workflows in session history via reflections, (2) generate skills automatically using the existing `/skillify` skill (now agent-invokable), (3) deploy new skills via the standard PR pipeline (PR -> review -> docs -> merge) with no human approval gate, (4) track skill effectiveness metrics, (5) auto-expire unused skills while keeping active ones alive.

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

1. **Entry point**: Reflections (`scripts/reflections.py`) reviews Memory records and session observations for friction patterns -- moments where agents struggled with tool params, repeated multi-step workflows, or worked around missing abstractions
2. **Friction detection**: A new `tools/skill_lifecycle.py` module queries Memory records for friction signals (category=`correction`, `pattern`; tags indicating tool struggles, repeated workarounds) and recurring workflows
3. **Skill generation**: When a friction pattern or repeatable workflow is detected, the system invokes `/skillify` (now agent-invokable) to generate a new SKILL.md. The focus is on minimum complexity skills that prevent friction -- e.g., wrapping a tool with commonly-guessed-wrong params
4. **Deployment**: Generated skills are deployed via the standard PR pipeline: create branch -> PR -> `/do-pr-review` -> `/do-docs` -> `/do-merge`. No human approval gate -- the PR review process is the quality gate
5. **Validation gate**: Generated skills pass through `/do-skills-audit` validation (existing 12-rule checker) before PR creation
6. **Effectiveness tracking**: `pre_tool_use.py` Skill hook calls `record_metric("skill.invocation", ...)` with skill name and session context. Post-session extraction records outcome (success/failure/patch-needed) via `record_metric("skill.outcome", ...)`
7. **Lifecycle management**: Skills have a default expiry (30 days from creation). Each invocation resets the expiry timer. Unused skills expire automatically and are removed. `tools/skill_lifecycle.py` queries analytics to compute per-skill effectiveness scores and manages expiry

## Architectural Impact

- **New dependencies**: `analytics.collector.record_metric` (already on main), no external deps
- **Interface changes**: `pre_tool_use.py::_handle_skill_tool_start()` gains a `record_metric` call (additive, no signature change). New `tools/skill_lifecycle.py` CLI module.
- **Coupling**: Loose coupling via analytics sink -- skill tracking writes to the same analytics pipeline as everything else. Friction detection reads from the Memory model (existing dependency). Reflections triggers pattern review (existing scheduling infrastructure).
- **Data ownership**: Skill effectiveness data owned by analytics (SQLite + Redis). Generated skills live in `.claude/skills/` like all other skills (no separate candidate directory). Expiry metadata in SKILL.md frontmatter.
- **Reversibility**: High. Remove `record_metric` calls from `pre_tool_use.py`, delete `tools/skill_lifecycle.py`, remove generated skills. No schema migrations.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment on friction detection thresholds, skill expiry policy, dashboard integration)
- Review rounds: 2+ (effectiveness tracking instrumentation review, friction detection logic review)

## Prerequisites

No prerequisites -- this work builds on the existing analytics module (`analytics/`), memory system (`models/memory.py`), and skill infrastructure (`.claude/skills/`), all of which are on main.

## Solution

### Key Elements

- **Skill Effectiveness Tracker**: Instruments skill invocations and outcomes in the analytics pipeline, providing per-skill success rate, patch frequency, and human override rate
- **Friction Detector**: Analyzes Memory observations (via reflections) to identify friction patterns -- agents struggling with tool params, repeated workarounds, and multi-step workflows that should be single-step
- **Skill Generator**: Invokes `/skillify` (now agent-invokable) to produce SKILL.md files from detected patterns, deployed via standard PR pipeline
- **Skill Expiry System**: Generated skills have a 30-day default expiry in frontmatter. Each invocation resets the timer. Unused skills are automatically retired
- **Skill Lifecycle Manager**: CLI tool that queries effectiveness metrics, manages expiry, and triggers skill generation from detected patterns

### Flow

**Reflections runs** -> Reviews Memory for friction patterns -> Identifies candidate workflows -> Invokes `/skillify` to generate skill -> `/do-skills-audit` validates -> Creates PR with new skill -> PR review validates -> Docs updated -> Merged to main -> Effectiveness tracking measures ongoing performance -> Expiry system retires unused skills

### Technical Approach

- Instrument `pre_tool_use.py::_handle_skill_tool_start()` to emit `record_metric("skill.invocation", 1, {"skill": name, "session_id": sid})` on every skill invocation
- Add a `post_tool_use` or post-session hook that records outcome: `record_metric("skill.outcome", 1, {"skill": name, "outcome": "success|failure|patch_needed"})`
- **Friction detection** uses Memory model queries: filter by `category="correction"` and `category="pattern"`, identify cases where agents repeatedly guessed wrong tool params, worked around missing abstractions, or performed the same multi-step sequence. Focus on minimum complexity -- a skill wrapping a single tool with correct default params is valuable
- **No minimum occurrence count** -- complexity and friction severity determine whether to skillify, not raw repetition. A single instance of an agent struggling with a tool's params because they're non-obvious is enough
- Remove `disable-model-invocation: true` from `/skillify` frontmatter so agents can invoke it directly
- Generated skills include frontmatter fields: `generated: true`, `generated_at: <date>`, `expires_at: <date+30d>`, `source_pattern: <description>`
- Expiry system: `tools/skill_lifecycle.py expire` checks all skills with `generated: true` and `expires_at` in the past, removes them via PR. Each skill invocation (tracked via analytics) resets `expires_at` to now+30d
- The PM's bash allowlist in `pre_tool_use.py` gets `python -m tools.skill_lifecycle` entries for read-only queries
- Effectiveness query: `python -m tools.skill_lifecycle report` outputs per-skill metrics (invocation count, success rate, avg patches per invocation, last used)
- Reflections integration: add a skill review step to `scripts/reflections.py` that calls `python -m tools.skill_lifecycle detect-friction` and triggers skillification when warranted
- Dashboard integration via `dashboard.json` endpoint (existing pattern)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_metric` in `analytics/collector.py` already wraps in try/except -- verify skill-specific metric names follow the same pattern
- [ ] Friction detection in `tools/skill_lifecycle.py` must handle empty Memory result sets gracefully (no observations = no patterns, not a crash)
- [ ] Skill generation must handle cases where `/skillify` produces invalid output

### Empty/Invalid Input Handling
- [ ] `skill_lifecycle report` with no analytics data returns an empty report, not an error
- [ ] Friction detection with zero Memory records returns "no friction detected"
- [ ] Expiry check with no generated skills returns "no generated skills found"

### Error State Rendering
- [ ] CLI `skill_lifecycle report` output clearly distinguishes "no data" from "skill performing poorly"
- [ ] Dashboard skill health section shows "no data" state when analytics has no skill metrics

## Test Impact

No existing tests affected -- this is a greenfield feature with no prior test coverage for skill lifecycle management. The only touched existing file is `agent/hooks/pre_tool_use.py` (adding a `record_metric` call), but the existing tests in `tests/unit/test_pm_session_permissions.py` and `tests/unit/test_pre_tool_use.py` test permission enforcement, not metric emission, so they will not break.

## Rabbit Holes

- **Real-time skill hot-loading**: Claude Code discovers skills at session start. Adding skills mid-session is a platform constraint -- do not try to solve it. New skills take effect on the next session.
- **Natural language skill composition**: Composing skills by chaining their SKILL.md instructions sounds powerful but is a rabbit hole. Stick to detecting patterns and generating standalone skills.
- **Retroactive pattern detection on all historical sessions**: Limit friction detection to the last 30 days of Memory observations. Full historical analysis is computationally wasteful and produces stale patterns.
- **Complex multi-tool skills**: Focus on minimum complexity skills that wrap friction-prone tool invocations. Resist the urge to generate complex orchestration skills -- those should be human-authored.

## Risks

### Risk 1: Friction detection produces too many low-value skills
**Impact:** Skills directory fills with trivial wrappers that don't meaningfully reduce friction
**Mitigation:** Focus on minimum complexity but meaningful friction reduction. Rate-limit skill generation to at most 1 per day via reflections. The 30-day expiry automatically cleans up skills that never get used.

### Risk 2: Effectiveness metrics are too coarse to be actionable
**Impact:** Success/failure binary doesn't capture the nuance of "skill worked but required 3 patches"
**Mitigation:** Track three distinct outcomes: `success` (no patches needed), `partial` (patches required but eventually passed), `failure` (skill output was abandoned). Also track patch count per invocation as a separate metric.

### Risk 3: PM session's read-only constraints block skill generation workflow
**Impact:** The PM cannot write files to `.claude/skills/` directly
**Mitigation:** The PM delegates skill file creation to a dev-session via `/skillify`. The PM's role is detecting friction and triggering skillification; the dev-session writes the file and creates the PR. This follows the existing pattern for all PM mutations.

## Race Conditions

No race conditions identified -- all operations are sequential within a single session. Skill effectiveness metrics use the analytics `record_metric()` API which handles concurrent writes via SQLite WAL mode. Friction detection is a batch read operation with no write contention.

## No-Gos (Out of Scope)

- **Cross-project skill sharing** -- skills are scoped to this repository; no sync to other machines
- **Skill versioning** -- no version history for generated skills; use git history
- **Real-time effectiveness dashboards** -- effectiveness data is queryable via CLI; live dashboard charts are deferred to a future iteration
- **Modifying existing static skills** -- this feature only creates new generated skills; it does not modify or deprecate human-authored skills automatically

## Update System

No update system changes required -- this feature adds new Python modules that are handled by the existing `git pull` step in the update script. No new dependencies, config files, or migration steps are needed.

## Agent Integration

- **PM bash allowlist**: Add `python -m tools.skill_lifecycle` prefixes to `PM_BASH_ALLOWED_PREFIXES` in `agent/hooks/pre_tool_use.py` so the PM can query skill effectiveness reports
- **Skillify agent-invokable**: Remove `disable-model-invocation: true` from `.claude/skills/skillify/SKILL.md` so agents can trigger skill generation directly
- **No MCP server changes needed**: The skill lifecycle tool is a CLI module invoked via Bash, following the same pattern as `python -m tools.memory_search` and `python -m tools.analytics`
- **Bridge changes**: None. The bridge has no SDLC awareness and does not need to know about skill lifecycle
- **Reflections integration**: Add a skill review step to `scripts/reflections.py` that calls friction detection
- **Integration test**: Verify that `python -m tools.skill_lifecycle report` is callable from a PM session (passes the bash allowlist check)

## Documentation

- [ ] Create `docs/features/pm-autonomous-skills.md` describing the autonomous skill lifecycle (friction detection, generation, effectiveness tracking, expiry)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `.claude/skills/README.md` to document generated skill frontmatter fields (`generated`, `expires_at`) and their role in progressive disclosure
- [ ] Add inline docstrings to `tools/skill_lifecycle.py` for all public functions
- [ ] Update `config/personas/project-manager.md` to reference the skill lifecycle query commands

## Success Criteria

- [ ] `record_metric("skill.invocation", ...)` is emitted on every skill invocation via the pre_tool_use hook
- [ ] `record_metric("skill.outcome", ...)` is emitted after each skill-bearing session completes
- [ ] `python -m tools.skill_lifecycle report` outputs per-skill effectiveness metrics (invocation count, success rate, patch frequency)
- [ ] `python -m tools.skill_lifecycle detect-friction` identifies friction patterns from Memory observations
- [ ] `python -m tools.skill_lifecycle expire` removes generated skills past their expiry date via PR
- [ ] `/skillify` is agent-invokable (`disable-model-invocation` removed from frontmatter)
- [ ] Generated skills include `generated: true`, `generated_at`, `expires_at` frontmatter fields
- [ ] Skill invocations reset the `expires_at` timer for generated skills
- [ ] Reflections includes a skill lifecycle review step
- [ ] Generated skills pass `/do-skills-audit` validation and deploy via PR pipeline
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

- **Builder (friction-detection)**
  - Name: friction-builder
  - Role: Implement friction detection from Memory observations, skill expiry system, and skillify agent-invocation
  - Agent Type: builder
  - Resume: true

- **Builder (lifecycle-cli)**
  - Name: lifecycle-builder
  - Role: Build the skill_lifecycle CLI tool with report, detect-friction, and expire subcommands
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end flow from invocation tracking through friction detection to skill generation and expiry
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

### 2. Build Friction Detection and Skill Expiry Module
- **Task ID**: build-friction-detection
- **Depends On**: none
- **Validates**: tests/unit/test_skill_friction_detection.py (create)
- **Assigned To**: friction-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/skill_lifecycle.py` with friction detection logic: query Memory records filtered by `category="correction"` and `category="pattern"`, identify cases where agents struggled with tool params, worked around missing abstractions, or performed repeated multi-step sequences
- No minimum occurrence count -- friction severity and complexity determine skillification, not repetition. A single correction about wrong tool params is enough
- Remove `disable-model-invocation: true` from `.claude/skills/skillify/SKILL.md` frontmatter so agents can invoke it
- Implement skill expiry: generated skills get `generated: true`, `generated_at: <date>`, `expires_at: <date+30d>` in frontmatter. Invocations reset `expires_at`. `expire` subcommand removes expired skills via PR
- Create `tests/unit/test_skill_friction_detection.py` with tests for friction detection, expiry management, and frontmatter format

### 3. Build Skill Lifecycle CLI and Reflections Integration
- **Task ID**: build-lifecycle-cli
- **Depends On**: build-effectiveness-tracking, build-friction-detection
- **Validates**: tests/unit/test_skill_lifecycle_cli.py (create)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement CLI subcommands: `report` (query analytics for per-skill metrics), `detect-friction` (run friction detection), `expire` (remove expired generated skills)
- Add `python -m tools.skill_lifecycle` prefixes to `PM_BASH_ALLOWED_PREFIXES` in `pre_tool_use.py`
- Add skill review step to `scripts/reflections.py` that calls `python -m tools.skill_lifecycle detect-friction` and triggers skillification when friction is found
- Create `tests/unit/test_skill_lifecycle_cli.py` with tests for CLI argument parsing and output format

### 4. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-lifecycle-cli
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `record_metric` calls are wired correctly in pre_tool_use.py
- Verify PM session can run `python -m tools.skill_lifecycle report` (bash allowlist check)
- Verify `/skillify` frontmatter no longer has `disable-model-invocation: true`
- Verify generated skill frontmatter includes `generated`, `generated_at`, `expires_at` fields
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
- Update `.claude/skills/README.md` with generated skill frontmatter documentation
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
| Skillify agent-invokable | `grep -c "disable-model-invocation" .claude/skills/skillify/SKILL.md` | output 0 |
| PM allowlist updated | `grep -c "skill_lifecycle" agent/hooks/pre_tool_use.py` | output > 0 |
| Feature docs exist | `ls docs/features/pm-autonomous-skills.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Resolved by PM (2026-04-11):

1. **Minimum occurrences?** No minimum count. Minimum complexity is the bar -- focus on friction prevention. If an agent struggles to do something simple because it wrongly guesses tool params, that's ripe for a simple skill definition.
2. **On-demand or post-session only?** Post-session only, triggered by reflections reviewing memory.
3. **Trial period?** No trial period. Fast to create means fast to retire. Default 30-day expiry applied to all generated skills; usage resets the timer.
