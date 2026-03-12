---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/375
last_comment_id:
---

# Observer SDLC Pipeline Fixes

## Problem

Three bugs in the Observer/SDLC pipeline cause cross-project work to silently fail and stage progress to display incorrectly.

**Current behavior:**

1. **Cross-repo `gh` resolution (CRITICAL):** When SDLC is invoked for a non-ai project (e.g., popoto), the worker runs with `cwd=ai/` (the orchestrator repo). The `/sdlc` skill calls `gh issue view 179` which resolves against the ai repo, finding a MERGED issue. The worker concludes "already done" and returns in 34 seconds — never touching the actual popoto issue #179 (which is OPEN with no PR). This silently breaks ALL cross-project SDLC work.

2. **Classification race condition:** When a user replies to resume an SDLC session, `asyncio.create_task(classify_and_update_reaction())` fires but `enqueue_job()` is called before it completes. The job gets `classification_type=None` → `is_sdlc=false` → Observer delivers instead of steering. User must manually reply "continue".

3. **Stage detector drops typed outcomes:** When a typed `SkillOutcome` says DOCS succeeded but the regex didn't detect it, `apply_transitions()` logs a warning but never adds the stage to the transitions list. So completed stages render as ☐ in Telegram.

**Desired outcome:**

1. Cross-project `gh` commands target the correct repo using `--repo {org}/{repo}` from `config/projects.json`.
2. Reply-to-resume inherits `classification_type` from the original session.
3. Typed outcomes that report success are merged into transitions when regex misses them.

## Prior Art

- **[#309](https://github.com/tomcounsell/ai/issues/309)**: Observer Agent design — introduced the current architecture where Observer steers between stages.
- **[#321](https://github.com/tomcounsell/ai/pull/321)**: Observer implementation — shipped the Observer with `is_sdlc_job()` check.
- **[#328](https://github.com/tomcounsell/ai/issues/328)** / **[#351](https://github.com/tomcounsell/ai/pull/351)**: Typed outcomes — added structured `SkillOutcome` signals but cross-check only logs warnings, doesn't act on mismatches.
- **[#331](https://github.com/tomcounsell/ai/issues/331)**: Goal gates — related effort to prevent stage skipping, but doesn't address the detection gap.
- **[#354](https://github.com/tomcounsell/ai/issues/354)**: Removed full-pipeline instructions from worker — made the worker single-stage, increasing reliance on Observer for steering (and making Bug 2 more impactful).

## Data Flow

### Bug 1: Cross-repo `gh` resolution

1. **Entry**: User sends "issue 179" to Dev: Popoto chat
2. **Bridge** (`telegram_bridge.py`): Routes to project `popoto`, sets `working_dir=/Users/valorengels/src/popoto`
3. **SDK client** (`sdk_client.py:862`): `classify_work_request("issue 179")` → `"sdlc"`. Because `project_working_dir != AI_REPO_ROOT`, sets `working_dir = AI_REPO_ROOT` (line 937). Injects `WORK REQUEST for project popoto`, `TARGET REPO: /Users/valorengels/src/popoto`, `GITHUB: tomcounsell/popoto` into prompt.
4. **Worker** (Claude Code subprocess): Runs in `cwd=ai/`. Invokes `/sdlc` skill.
5. **SDLC skill** (`SKILL.md` Step 1): Runs `gh issue view 179` — resolves against ai repo (cwd), finds MERGED issue #179 ("Session tagging system"). Never checks popoto.
6. **Worker**: Returns "already merged and complete" in 34 seconds.
7. **Observer**: Delivers false completion to Telegram.

### Bug 2: Classification race

1. **Entry**: User replies to a Valor message to resume SDLC work
2. **Bridge** (line 682-684): Creates `session_id` from `reply_to_msg_id` (continuation)
3. **Bridge** (line 777-799): Creates `classification_result = {}`, spawns async task to classify. Task NOT awaited.
4. **Bridge** (line 1122-1140): Calls `enqueue_job(classification_type=classification_result.get("type"))` — gets `None` because async task hasn't completed.
5. **Job queue** (`job_queue.py:1139-1143`): Creates `AgentSession` with `classification_type=None`. The `SDLC_MODE activated` history entry is never written.
6. **Observer** (line 250): `session.is_sdlc_job()` returns `False` → treats as non-SDLC → delivers instead of steering.

### Bug 3: Typed outcome drop

1. **Worker**: Invokes `/do-docs`, completes successfully. Typed outcome: `{status: "success", stage: "DOCS"}`.
2. **Observer** (line 398): `detect_stages(transcript)` — regex doesn't match "DOCS" completion pattern in this transcript.
3. **Observer** (line 399): `parse_outcome_from_text(transcript)` — finds typed outcome.
4. **Stage detector** (`apply_transitions`, line 199-206): Cross-check detects mismatch, logs WARNING. Does NOT add DOCS to transitions.
5. **Stage detector** (line 215): `if not transitions: return 0` — returns early because only regex transitions are in the list.
6. **Summary**: DOCS renders as ☐ despite being complete.

## Architectural Impact

- **No new dependencies**: All fixes use existing config infrastructure (`config/projects.json`) and session persistence (Redis/Popoto ORM).
- **Interface changes**: None. `apply_transitions()` signature unchanged; it already accepts `outcome` parameter.
- **Coupling**: Bug 1 fix adds a dependency from skill markdown files on the `GITHUB:` context line (already injected by `sdk_client.py`). This is intentional coupling — skills should use the project config.
- **Reversibility**: All three fixes are additive and safe to revert independently.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all fixes modify existing code using existing config infrastructure.

## Solution

### Key Elements

- **Cross-repo `gh` fix**: Update `/sdlc` skill and all `/do-*` skills to parse `GITHUB:` from prompt context and use `--repo {org}/{repo}` for all `gh` commands.
- **Classification inheritance**: On reply-to-resume, look up the original session's `classification_type` from Redis before enqueuing.
- **Typed outcome merge**: When `apply_transitions()` detects a cross-check mismatch (typed outcome says success, regex missed it), add the stage to transitions instead of just warning.

### Flow

**Message arrives** → Bridge routes to project → SDK client injects `GITHUB: org/repo` → Worker runs `/sdlc` with `gh --repo org/repo` → Observer steers with correct `is_sdlc` → Stage detector records typed outcomes → Checkboxes display correctly

### Technical Approach

- **Bug 1**: The `GITHUB:` line is already injected by `sdk_client.py:977-978` using `config/projects.json`. Skills just need to parse it and pass `--repo` to `gh`. The `/sdlc` skill template will include instructions to extract the repo identifier from the `GITHUB:` context line. Other `/do-*` skills that call `gh` will get the same treatment.

- **Bug 2**: Before calling `enqueue_job()`, check if `session_id` indicates a continuation (reply-to). If so, look up the existing `AgentSession` by session_id and inherit its `classification_type`. This is a 5-line fix in `telegram_bridge.py` between lines 799 and 1122.

- **Bug 3**: In `apply_transitions()`, after the cross-check warning at line 201-206, add the typed outcome's stage to the transitions list. This is a 6-line fix.

## Regression Test Cases

These tests prevent the three bugs from recurring. Add to existing test files.

### In `tests/test_observer.py` — new class `TestApplyTransitionsTypedOutcomeMerge`

| Test Name | Scenario | Assert |
|-----------|----------|--------|
| `test_typed_outcome_merged_when_regex_misses` | `transitions=[]`, `outcome=SkillOutcome(status="success", stage="DOCS")` | `apply_transitions()` returns 1, session history contains `"DOCS COMPLETED"` |
| `test_typed_outcome_not_merged_on_failure` | `transitions=[]`, `outcome=SkillOutcome(status="fail", stage="DOCS")` | returns 0, no DOCS entry in history |
| `test_typed_outcome_skipped_when_regex_already_detected` | `transitions=[{stage: "DOCS", status: "completed"}]`, same outcome | returns 1 (from regex), no duplicate DOCS entry |
| `test_typed_outcome_skipped_when_stage_already_completed` | Session already has `"DOCS COMPLETED"` in history, `outcome=SkillOutcome(status="success", stage="DOCS")` | returns 0, no duplicate |
| `test_typed_outcome_none_stage_no_crash` | `outcome=SkillOutcome(status="success", stage=None)` | returns 0, no crash |
| `test_typed_outcome_with_regex_transitions_both_recorded` | `transitions=[{stage: "BUILD", status: "completed"}]`, `outcome=SkillOutcome(status="success", stage="DOCS")` | returns 2, both BUILD and DOCS in history |

### In `tests/test_stage_aware_auto_continue.py` — new class `TestClassificationInheritance`

| Test Name | Scenario | Assert |
|-----------|----------|--------|
| `test_reply_to_resume_inherits_sdlc_classification` | Create session with `classification_type="sdlc"`, simulate reply-to-resume with empty `classification_result` | Enqueued job has `classification_type="sdlc"` |
| `test_reply_to_resume_async_classifier_overrides_inheritance` | Create session with `classification_type="sdlc"`, simulate reply where async classifier completes with `type="question"` before enqueue | Enqueued job has `classification_type="question"` |
| `test_reply_to_resume_missing_session_falls_through` | No existing session in Redis, reply-to-resume with empty `classification_result` | Enqueued job has `classification_type=None` (no crash) |
| `test_fresh_message_no_inheritance` | Existing session exists, but message is NOT a reply | Enqueued job uses async classifier result, not inherited |

### In `tests/test_observer.py` — new class `TestCrossRepoGhResolution`

| Test Name | Scenario | Assert |
|-----------|----------|--------|
| `test_sdlc_skill_contains_repo_flag_instructions` | Read `.claude/skills/sdlc/SKILL.md` | Contains `--repo` in `gh issue view` and `gh pr list` examples |
| `test_enriched_message_includes_github_line` | Call SDK client prompt builder with popoto project config | Enriched message contains `GITHUB: tomcounsell/popoto` |
| `test_all_do_skills_reference_github_context` | Read all `/do-*` skill SKILL.md files that contain `gh ` commands | Each file references `GITHUB:` context line or `--repo` |

### In `tests/test_observer.py` — new class `TestObserverSdlcSteering`

| Test Name | Scenario | Assert |
|-----------|----------|--------|
| `test_observer_steers_when_is_sdlc_and_remaining_stages` | Session with `classification_type="sdlc"`, stages pending | Observer decision is `steer`, not `deliver` |
| `test_observer_delivers_when_all_stages_complete` | Session with all stages completed | Observer decision is `deliver` |

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `apply_transitions()` already has try/except around `session.append_history()` — test that typed outcome merge entries go through the same path
- [ ] Classification inheritance lookup should handle missing sessions gracefully (no crash if session was already cleaned up)

### Empty/Invalid Input Handling
- [ ] `apply_transitions()` with `outcome.stage = None` — should not crash
- [ ] `apply_transitions()` with `outcome.status != "success"` — should not merge
- [ ] Classification lookup with no existing session — should fall through to current behavior

### Error State Rendering
- [ ] Stage progress display when some stages come from typed outcomes — verify checkboxes render correctly

## Rabbit Holes

- **Rewriting the classification system**: The race condition fix is a targeted inheritance, not a redesign of async classification. Don't refactor the entire classification pipeline.
- **Making skills auto-detect repo from git**: Skills should use the explicitly-provided `GITHUB:` line, not try to infer repo from `git remote` (which would resolve to ai/ repo).
- **Adding `--repo` to every `gh` call globally**: Only cross-project SDLC skills need this. Local dev commands and non-SDLC flows should not be affected.

## Risks

### Risk 1: Skill instructions not followed by LLM
**Impact:** Worker ignores `--repo` instructions in skill markdown, still runs `gh` against wrong repo.
**Mitigation:** Make the instructions unambiguous with concrete examples. Add a verification step in the SDLC skill that validates the issue was fetched from the correct repo.

### Risk 2: Classification inheritance masks a changed intent
**Impact:** User replies to an SDLC session with a non-SDLC message, but it gets classified as SDLC because it inherited from the parent session.
**Mitigation:** Only inherit classification when the async classifier hasn't completed yet (i.e., `classification_result` is still empty). If the classifier HAS completed by the time we check, use its result. Additionally, the intake classifier already detects intent changes — this is a fallback, not an override.

## Race Conditions

### Race 1: Classification task completes between check and enqueue
**Location:** `bridge/telegram_bridge.py` lines 799-1140
**Trigger:** Async classification task finishes after inheritance check but before `enqueue_job()`
**Data prerequisite:** Original session must exist in Redis with `classification_type` populated
**State prerequisite:** `classification_result` dict is empty at check time
**Mitigation:** Use a simple check: if `classification_result.get("type")` is still `None` at enqueue time AND this is a reply-to continuation, look up the parent session. If the classifier has already populated the result, use it. No lock needed — both paths converge on the same `classification_type="sdlc"` for SDLC continuations.

## No-Gos (Out of Scope)

- Refactoring the async classification pipeline
- Adding `--repo` to non-SDLC flows or local dev commands
- Changing the Observer's decision framework (just fixing its inputs)
- Adding new config fields to `config/projects.json` (already has everything needed)
- Handling mixed-repo sessions (one session working across multiple repos)

## Update System

No update system changes required — all fixes are to bridge code and skill markdown files that are already synced by the update process. No new dependencies or config files to propagate.

## Agent Integration

No new agent integration required. The fixes modify:
1. Skill instructions (`.claude/skills/`) — synced via hardlinks
2. Bridge code (`bridge/`) — takes effect on restart
3. Stage detector (`bridge/stage_detector.py`) — takes effect on restart

The bridge restart after code changes is already part of the standard workflow. No MCP server changes needed.

## Documentation

- [ ] Update `docs/features/sdlc-first-routing.md` — add section on cross-repo `gh` resolution and the `GITHUB:` context line
- [ ] Add entry to `docs/features/README.md` if not already present for cross-repo SDLC
- [ ] Update inline comments in `bridge/stage_detector.py` to document typed outcome merge behavior

## Success Criteria

- [ ] Sending "issue 179" to Dev: Popoto fetches popoto issue #179 (not ai repo #179)
- [ ] All `/do-*` skills that call `gh` include `--repo` instructions for cross-project builds
- [ ] Reply-to-resume on an SDLC session inherits `classification_type="sdlc"` and Observer steers without manual "continue"
- [ ] Typed outcome merge: when regex misses a stage but typed outcome reports success, stage is recorded in session history
- [ ] Stage progress display shows all completed stages as ☑ after a full pipeline run
- [ ] Log output confirms typed outcome merge (e.g., "Stage DOCS merged from typed outcome (regex missed)")
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-fixes)**
  - Name: bridge-builder
  - Role: Fix classification race and stage detector in Python bridge code
  - Agent Type: builder
  - Resume: true

- **Builder (skill-updates)**
  - Name: skill-builder
  - Role: Update SDLC and /do-* skill markdown files with --repo instructions
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: integration-validator
  - Role: Verify all three fixes work together end-to-end
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix cross-repo `gh` in SDLC skill
- **Task ID**: build-sdlc-skill
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/sdlc/SKILL.md` Step 1 to extract `GITHUB:` line from prompt context and use `gh issue view {number} --repo {org}/{repo}`
- Update Step 2's `gh pr list` to also use `--repo`
- Add a verification instruction: after fetching the issue, confirm the repo in the issue URL matches the target project

### 2. Fix cross-repo `gh` in /do-* skills
- **Task ID**: build-do-skills
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/do-issue/SKILL.md` — `gh issue create`, `gh issue list`, `gh pr list` with `--repo`
- Update `.claude/skills/do-plan/SKILL.md` — `gh issue list`, `gh pr list`, `gh issue edit`, `gh issue view`, `gh issue create` with `--repo`
- Update `.claude/skills/do-pr-review/SKILL.md` — `gh pr view`, `gh pr diff`, `gh issue view`, `gh pr review`, `gh pr comment` with `--repo`
- Update `.claude/skills/do-docs/SKILL.md` — `gh pr view`, `gh pr diff`, `gh issue list`, `gh issue comment`, `gh issue create` with `--repo`
- Update `.claude/skills/do-patch/SKILL.md` — `gh issue view` with `--repo`
- Each skill should parse the `GITHUB:` context line if present and derive the `--repo` flag

### 3. Fix classification race condition
- **Task ID**: build-classification-fix
- **Depends On**: none
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/telegram_bridge.py`, between lines 799 and 1122 (after async task creation, before enqueue):
  - If this is a reply-to continuation (`is_reply_to_valor and message.reply_to_msg_id`), look up the existing `AgentSession` by `session_id`
  - If found and it has `classification_type`, store it in `classification_result` as a fallback
  - The async classifier can still override it if it completes before enqueue
- Add `TestClassificationInheritance` class in `tests/test_stage_aware_auto_continue.py` with all 4 regression tests from the plan

### 4. Fix stage detector typed outcome merge
- **Task ID**: build-stage-detector-fix
- **Depends On**: none
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/stage_detector.py` `apply_transitions()`, after the cross-check warning at line 201-206:
  - When `outcome.status == "success"` and `outcome.stage not in regex_stages`, append a transition: `{"stage": outcome.stage, "status": "completed", "reason": f"Typed outcome: {outcome.stage} succeeded (regex missed)"}`
  - Log at INFO level: `"Stage {stage} merged from typed outcome (regex missed)"`
- Remove the early return at line 215-216 (`if not transitions: return 0`) — it should come AFTER the typed outcome merge
- Add `TestApplyTransitionsTypedOutcomeMerge` class in `tests/test_observer.py` with all 6 regression tests from the plan

### 5. Add cross-repo skill validation tests
- **Task ID**: build-cross-repo-tests
- **Depends On**: build-sdlc-skill, build-do-skills
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestCrossRepoGhResolution` class in `tests/test_observer.py` with all 3 regression tests from the plan
- Add `TestObserverSdlcSteering` class in `tests/test_observer.py` with 2 tests from the plan

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-sdlc-skill, build-do-skills, build-classification-fix, build-stage-detector-fix, build-cross-repo-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` to verify all tests pass
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify `/sdlc` skill contains `--repo` instructions
- Verify `apply_transitions()` includes typed outcome merge logic
- Verify classification inheritance code exists in `telegram_bridge.py`
- Verify all `/do-*` skills reference `GITHUB:` context line

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-first-routing.md` with cross-repo section
- Add entry to `docs/features/README.md` if needed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| SDLC skill has --repo | `grep -c '\-\-repo' .claude/skills/sdlc/SKILL.md` | output > 0 |
| Stage detector merges outcomes | `grep -c 'typed outcome' bridge/stage_detector.py` | output > 1 |
| Classification inheritance | `grep -c 'classification_type' bridge/telegram_bridge.py` | output > 2 |
