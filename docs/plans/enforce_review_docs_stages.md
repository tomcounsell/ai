---
status: Complete
type: bug
appetite: Medium
owner: Valor
created: 2026-03-16
tracking: https://github.com/tomcounsell/ai/issues/418
last_comment_id:
---

# Enforce Mandatory REVIEW and DOCS Stages

## Problem

The pipeline graph requires TEST→REVIEW→DOCS→MERGE, but an audit of all past SDLC jobs found fewer than 10% of merged PRs reached REVIEW or DOCS. The enforcement layer (Observer + stage detector) has multiple bypass paths that let the pipeline deliver to Telegram after BUILD or TEST without completing remaining stages.

**Current behavior:**
The Observer delivers to Telegram after BUILD or TEST completes, skipping REVIEW and DOCS. 55 plans have merged PRs but stale statuses because DOCS never ran.

**Desired outcome:**
Every SDLC job that reaches BUILD must also complete REVIEW and DOCS. The Observer must not deliver to Telegram until goal gates for REVIEW and DOCS are satisfied.

## Prior Art

- **PR #346** (issue #331): Added goal gates (`agent/goal_gates.py`) — deterministic checks for PLAN, BUILD, TEST, REVIEW, DOCS. Gates are computed and passed to the LLM Observer in `read_session`, but **not enforced**. The LLM sees them but can ignore them.
- **PR #412** (issue #399): Upgraded pipeline to directed graph. Correct edges exist but enforcement didn't follow.
- **PR #415** (issue #414): Fixed `next_skill=None` in typed outcome path. Addressed one routing gap but not the systemic enforcement problem.
- **PR #321**: Original Observer implementation — designed the deterministic guard (line 684) which steers when remaining stages exist, but has bypass conditions (needs_human, has_failed, cap_reached).

## Data Flow

1. Worker agent completes a `/do-*` skill and stops
2. **Stage detector** (`detect_stages` + `apply_transitions`) parses transcript, writes `[stage]` history entries
3. **Observer.run()** fires:
   a. Typed outcome fast path (line 529-617) — can deliver if `has_remaining_stages()` is False
   b. Deterministic SDLC guard (line 684) — steers if remaining stages exist AND no bypass conditions
   c. LLM Observer fallback — can decide to deliver regardless of stage progress
4. If any of (a), (b), (c) decide "deliver", output goes to Telegram — REVIEW and DOCS never run

**The fix**: Insert a hard gate check before any "deliver" decision. If REVIEW or DOCS gates are unsatisfied, override the deliver decision to steer instead.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #346 (goal gates) | Added deterministic gate checks for all stages | Gates are informational — passed to LLM but never enforced. LLM can and does ignore them. |
| PR #412 (graph) | Correct edges in pipeline graph | Graph defines valid transitions but doesn't prevent the Observer from delivering early |
| PR #415 (next_skill fix) | Resolved vague coaching message after BUILD | Fixed one symptom but didn't address the systemic problem: no enforcement layer blocks premature delivery |

**Root cause pattern:** All prior fixes improved routing hints (better coaching, better graph, better gate data) but none added a **hard enforcement check** that blocks delivery when mandatory stages are incomplete.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — the gate check functions already exist
- **Coupling**: Tighter coupling between Observer and goal gates — intentional, this is the enforcement we've been missing
- **Data ownership**: No change
- **Reversibility**: Fully reversible — remove the enforcement checks and behavior returns to current state

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1
- Review rounds: 1

## Prerequisites

No prerequisites — all gate infrastructure already exists in `agent/goal_gates.py`.

## Solution

### Key Elements

- **Hard delivery gate in Observer**: Before any deliver-to-Telegram decision, check if REVIEW and DOCS goal gates are satisfied. If not, override to steer.
- **Tighter stage detector patterns**: Make REVIEW and DOCS completion regexes require explicit outcome markers (typed outcomes or specific phrases), not incidental mentions.
- **Graph-aware `has_remaining_stages()`**: Derive "remaining" from the pipeline graph edges rather than a flat list check.
- **Plan status update in `/do-docs`**: When DOCS runs, update the plan's `status:` frontmatter to "Complete".

### Flow

**Before (broken):**
Worker stops → Stage detector marks stages → Observer checks `has_remaining_stages()` → False (premature) → Delivers to Telegram → REVIEW/DOCS never run

**After (fixed):**
Worker stops → Stage detector marks stages → Observer prepares to deliver → **Hard gate check**: REVIEW gate satisfied? DOCS gate satisfied? → No → Override: steer to next required stage → REVIEW/DOCS run

### Technical Approach

#### Change 1: Hard delivery gate in Observer (`bridge/observer.py`)

Add a `_check_mandatory_gates()` method that runs REVIEW and DOCS goal gates. Call it at three enforcement points:

1. **Typed outcome success + no remaining stages (line 580)**: Before delivering, check gates. If unsatisfied, steer instead.
2. **Deterministic SDLC guard bypass (line 722)**: When the guard is bypassed due to `needs_human`, check if the "question" is from a stage before REVIEW — if REVIEW/DOCS gates aren't satisfied, still steer.
3. **LLM Observer deliver decision (line 836)**: After the LLM decides to deliver, check gates. If unsatisfied, override to steer.

The gate check is cheap (filesystem + one `gh` API call) and already runs in `_handle_read_session`. Cache the result per Observer.run() invocation.

#### Change 2: Tighten stage detector REVIEW/DOCS patterns (`bridge/stage_detector.py`)

Current REVIEW pattern: `review\s+(passed|approved|complete)` — too broad, matches "review complete" anywhere in output.

New approach: Remove REVIEW and DOCS from `_COMPLETION_PATTERNS` entirely. These stages should ONLY be marked complete via:
- Typed `SkillOutcome` from `/do-pr-review` or `/do-docs` (already works via `apply_transitions` cross-check)
- Explicit `/do-pr-review` or `/do-docs` skill invocation detected by `_SKILL_INVOCATION_PATTERN`

This prevents false positives from incidental mentions.

#### Change 3: Graph-aware `has_remaining_stages()` (`models/agent_session.py`)

Replace the flat check with a graph-derived check:
- Get current stage progress
- Find the last completed/failed stage
- Call `get_next_stage()` from the pipeline graph
- If the graph returns a non-None next stage, stages remain

This correctly handles cycles and the PATCH routing-only stage.

#### Change 4: Plan status update in `/do-docs` skill (`.claude/skills/do-docs/SKILL.md`)

Add instruction: after documentation is created/updated, update the plan's `status:` frontmatter from Ready/In Progress to Complete.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] If goal gate check raises an exception, fall back to current behavior (deliver) — don't crash the Observer
- [ ] If `_run_gh_command` times out in `check_review_gate`, the gate returns unsatisfied (which forces steering — safe default)

### Empty/Invalid Input Handling
- [ ] If `work_item_slug` is None (non-SDLC session), skip gate enforcement entirely
- [ ] If no PR exists yet (BUILD not complete), REVIEW gate returns unsatisfied — Observer steers to BUILD

### Error State Rendering
- [ ] When gate enforcement overrides a deliver decision, log clearly: "Delivery blocked: REVIEW/DOCS gates unsatisfied"
- [ ] The coaching message explains what's needed: "REVIEW gate unsatisfied — invoke /do-pr-review"

## Rabbit Holes

- **Making all gates mandatory** — Only REVIEW and DOCS need hard enforcement. PLAN, BUILD, TEST are naturally enforced by the graph routing (you can't build without a plan, can't test without building).
- **Redesigning the Observer decision tree** — Don't restructure the 3-phase flow (typed outcome → deterministic guard → LLM). Just add gate checks at the delivery decision points.
- **Blocking delivery for non-SDLC sessions** — Gate enforcement only applies to sessions where `is_sdlc_job()` returns True. Non-SDLC sessions (Q&A, casual chat) deliver immediately.
- **Making gates async** — The `gh` API call in `check_review_gate` takes <1s. Not worth adding async complexity.

## Risks

### Risk 1: Gate check false negatives (unsatisfied when stage actually completed)
**Impact:** Observer keeps steering to REVIEW/DOCS even though they already ran — infinite loop
**Mitigation:** The gate checks are specific and well-tested. REVIEW gate checks for actual PR reviews. DOCS gate checks for feature doc files. Both have fallback paths (plan says "no docs needed"). Add a cycle counter: if the same stage is steered to 3+ times, deliver with a warning.

### Risk 2: `work_item_slug` not set on session
**Impact:** Gate enforcement can't run without a slug (gates need it to find the plan/PR)
**Mitigation:** Only enforce gates when `work_item_slug` is set. Sessions without a slug are either non-SDLC or pre-PLAN — neither needs REVIEW/DOCS enforcement.

## Race Conditions

No race conditions. The Observer runs synchronously — gate checks read filesystem and GitHub state that was established by prior stages. The worker is not running concurrently when the Observer fires.

## No-Gos (Out of Scope)

- No changes to the pipeline graph (it's already correct)
- No changes to the `/do-*` skill definitions (except adding plan status update to `/do-docs`)
- No new gate types (existing gates for REVIEW and DOCS are sufficient)
- No enforcement for non-SDLC sessions
- No async gate checks

## Update System

No update system changes required — this modifies existing modules only.

## Agent Integration

No agent integration required — this is internal Observer/stage-detector enforcement.

## Documentation

- [ ] Update `docs/features/pipeline-graph.md` to document the mandatory gate enforcement
- [ ] Update `docs/features/observer-agent.md` to describe the hard delivery gate
- [ ] Add entry to `docs/features/README.md` if new doc created

## Success Criteria

- [ ] Observer never delivers to Telegram for SDLC sessions when REVIEW or DOCS goal gates are unsatisfied
- [ ] Stage detector does not mark REVIEW or DOCS as completed from regex patterns — only from typed outcomes or skill invocations
- [ ] `has_remaining_stages()` uses the pipeline graph to determine remaining stages
- [ ] `/do-docs` updates plan `status:` frontmatter to "Complete"
- [ ] Gate enforcement is skipped for non-SDLC sessions (no regression on casual chat)
- [ ] Cycle safety: if REVIEW/DOCS steering repeats 3+ times, deliver with warning instead of looping
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (observer-enforcement)**
  - Name: observer-builder
  - Role: Add hard delivery gate and gate caching to Observer
  - Agent Type: builder
  - Resume: true

- **Builder (stage-detector)**
  - Name: detector-builder
  - Role: Tighten REVIEW/DOCS patterns, update has_remaining_stages
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify gate enforcement blocks premature delivery
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update pipeline and observer docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add hard delivery gate to Observer
- **Task ID**: build-observer-gate
- **Depends On**: none
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_check_mandatory_gates()` method to Observer that runs REVIEW and DOCS goal gates
- Cache gate results per `run()` invocation (avoid redundant gh API calls)
- Insert gate check at 3 enforcement points: typed outcome deliver (line 580), guard bypass (line 722), LLM deliver (line 836)
- When gates unsatisfied: override deliver to steer, with coaching message naming the next required `/do-*` skill
- Add cycle safety: if same gate-forced steering happens 3+ times (tracked via session history), deliver with warning
- Skip gate enforcement when `work_item_slug` is None or `is_sdlc_job()` is False

### 2. Tighten stage detector and has_remaining_stages
- **Task ID**: build-detector-fix
- **Depends On**: none
- **Assigned To**: detector-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove REVIEW and DOCS from `_COMPLETION_PATTERNS` in `bridge/stage_detector.py`
- REVIEW and DOCS completion only via typed outcomes or skill invocation detection
- Update `has_remaining_stages()` in `models/agent_session.py` to use pipeline graph: call `get_next_stage()` with the last completed stage, return True if a non-MERGE next stage exists
- Add `PATCH` to `SDLC_STAGES` list so the stage tracker aligns with the graph

### 3. Add plan status update to /do-docs
- **Task ID**: build-docs-status
- **Depends On**: none
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add instruction to `.claude/skills/do-docs/SKILL.md`: after docs are created, update `status:` in the plan frontmatter to "Complete"

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-observer-gate, build-detector-fix
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: Observer steers to REVIEW after TEST succeeds (REVIEW gate unsatisfied)
- Test: Observer steers to DOCS after REVIEW succeeds (DOCS gate unsatisfied)
- Test: Observer delivers when all gates satisfied
- Test: Observer skips gate enforcement for non-SDLC sessions
- Test: Cycle safety delivers after 3 forced steerings
- Test: Stage detector does not mark REVIEW/DOCS complete from regex
- Test: `has_remaining_stages()` returns correct result from graph

### 5. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-tests
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify no SDLC session can deliver with unsatisfied REVIEW/DOCS gates
- Verify non-SDLC sessions are unaffected

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pipeline-graph.md` with gate enforcement section
- Update `docs/features/observer-agent.md` with hard delivery gate description

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_observer.py tests/unit/test_pipeline_graph.py tests/unit/test_stage_detector.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/observer.py bridge/stage_detector.py models/agent_session.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/observer.py bridge/stage_detector.py models/agent_session.py` | exit code 0 |
| No REVIEW regex | `python -c "from bridge.stage_detector import _COMPLETION_PATTERNS; assert 'REVIEW' not in _COMPLETION_PATTERNS"` | exit code 0 |
| No DOCS regex | `python -c "from bridge.stage_detector import _COMPLETION_PATTERNS; assert 'DOCS' not in _COMPLETION_PATTERNS"` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
