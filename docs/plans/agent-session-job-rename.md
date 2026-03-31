---
status: Ready
type: chore
appetite: Large
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/608
last_comment_id:
---

# Rename All "Job" Terminology to "Agent Session"

## Problem

The codebase uses "job" terminology pervasively (~494 occurrences across 72 files) to refer to AgentSession records, despite there being no Job model. `AgentSession` is the only persistence model for work items. The `class Job` in `job_queue.py` is a thin wrapper, `RedisJob` is a dead alias, and fields like `job_id`, `parent_job_id`, `stable_job_id` suggest a foreign key to a Job table that does not exist.

**Current behavior:**
- Primary key is `job_id` (AutoKeyField) on the AgentSession model
- Queue module is `agent/job_queue.py`, scheduler is `tools/job_scheduler.py`
- Functions named `enqueue_job()`, `_pop_job()`, `retry_job()`, `cancel_job()`
- Constants like `_JOB_FIELDS`, `JOB_TIMEOUT_DEFAULT`, `JOB_HEALTH_CHECK_INTERVAL`
- 10 test files with "job" in the filename
- 6 docs under `docs/features/` with "job" in the filename
- CLI entry point is `python -m tools.job_scheduler`

**Desired outcome:**
- Every reference to "job" that means AgentSession is renamed to `agent_session`
- Any remaining "job" that means a generic unit of work becomes `task`
- File names, function names, field names, constants, log messages, docs, CLI, and UI all use consistent terminology
- Reflections keep their own terminology unchanged
- The codebase reads clearly: `agent_session_queue.py`, `enqueue_agent_session()`, `agent_session_id`

## Prior Art

- **PR #505**: AgentSession field cleanup Phase 1 -- removed dead fields, renamed for clarity. Merged 2026-03-24.
- **PR #490**: Consolidated SDLC stage tracking, removed legacy fields. Merged 2026-03-24.
- **Issue #473**: AgentSession field naming cleanup (closed). Earlier pass at naming; did not address "job" terminology.
- **Issue #562**: Standardize session type and persona magic strings (closed). Related naming cleanup.
- **PR #607**: Fix AgentSession status KeyField -- changed `status` from KeyField to IndexedField. Merged. Prerequisite for this rename.

## Data Flow

Not applicable -- this is a rename-only refactor. No data flow changes. The system behavior is identical before and after; only symbol names and file names change.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: All public exports from `agent/__init__.py` change names (`enqueue_job` -> `enqueue_agent_session`, `Job` -> removed, `RedisJob` -> removed, `queue_revival_job` -> `queue_revival_agent_session`)
- **Coupling**: No change -- same modules, same call patterns, different names
- **Data ownership**: No change
- **Reversibility**: Easy -- purely mechanical rename, git revert would undo everything. Redis flush is the only non-reversible step (existing records lost, but they are short-lived and reconstructed on next use).

## Appetite

**Size:** Large

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is well-defined, no ambiguity)
- Review rounds: 1 (verification that grep returns only false positives)

This is a large mechanical refactor with zero behavioral changes. The risk is not complexity but thoroughness -- missing a single occurrence creates a runtime error.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #607 merged | `gh pr view 607 --json state -q .state` returns "MERGED" | Avoid merge conflicts on same files |

Run all checks: `python scripts/check_prerequisites.py docs/plans/agent-session-job-rename.md`

## Solution

### Key Elements

- **Model field renames**: `job_id` -> `agent_session_id`, `parent_job_id` -> `parent_agent_session_id`, `stable_job_id` -> `stable_agent_session_id` on AgentSession
- **Module renames**: `agent/job_queue.py` -> `agent/agent_session_queue.py`, `tools/job_scheduler.py` -> `tools/agent_session_scheduler.py`
- **Symbol renames**: All functions, constants, classes, and log messages updated across 72+ files
- **Dead code removal**: `class Job` wrapper, `RedisJob` alias, `_REDIS_JOB_FIELDS` alias removed
- **Generic "job" -> "task"**: Any remaining "job" that refers to generic work (not AgentSession) becomes "task"

### Flow

**Phase 1** (Model fields + Redis flush) -> **Phase 2** (Module/file renames) -> **Phase 3** (Symbols/constants/logs) -> **Phase 4** (CLI/UI/docs) -> **Phase 5** (Generic job -> task cleanup)

Each phase is atomic -- no half-renamed states between phases.

### Technical Approach

- Popoto AutoKeyField and KeyField names are embedded in Redis key strings. Renaming them changes the key structure, making existing records inaccessible. This requires flushing all AgentSession records after Phase 1.
- The `_JOB_FIELDS` list (62 entries) and `_extract_job_fields()` function power the delete-and-recreate pattern used for KeyField value changes. These must be renamed but their content stays the same (field names inside the list refer to model field names, which are being renamed in Phase 1).
- After Phase 1, the `_JOB_FIELDS` list entries `parent_job_id` and `stable_job_id` must also be updated to `parent_agent_session_id` and `stable_agent_session_id`.
- The `agent/__init__.py` exports must be updated atomically with the module rename to avoid import errors.
- Test files are renamed in Phase 2 alongside source modules to keep test discovery working.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers are being added or modified -- this is a rename-only refactor
- Existing exception handlers will have their log messages updated (Phase 3) but behavior is unchanged

### Empty/Invalid Input Handling
- No new functions or input paths -- rename only
- Existing edge case handling is preserved verbatim

### Error State Rendering
- Dashboard UI text updated in Phase 4 but error rendering logic is unchanged

## Test Impact

All 10 test files with "job" in the filename need renaming. Every test file that references `job_id`, `enqueue_job`, `Job`, `RedisJob`, or imports from `agent.job_queue` / `tools.job_scheduler` needs import path and symbol updates.

- [ ] `tests/unit/test_job_dependencies.py` -- REPLACE: rename to `test_agent_session_dependencies.py`, update all symbols
- [ ] `tests/unit/test_job_hierarchy.py` -- REPLACE: rename to `test_agent_session_hierarchy.py`, update all symbols
- [ ] `tests/unit/test_job_queue_async.py` -- REPLACE: rename to `test_agent_session_queue_async.py`, update all symbols
- [ ] `tests/unit/test_job_scheduler_kill.py` -- REPLACE: rename to `test_agent_session_scheduler_kill.py`, update all symbols
- [ ] `tests/unit/test_job_scheduler_persona.py` -- REPLACE: rename to `test_agent_session_scheduler_persona.py`, update all symbols
- [ ] `tests/unit/test_job_status_cli.py` -- REPLACE: rename to `test_agent_session_status_cli.py`, update all symbols
- [ ] `tests/integration/test_job_health_monitor.py` -- REPLACE: rename to `test_agent_session_health_monitor.py`, update all symbols
- [ ] `tests/integration/test_job_queue_race.py` -- REPLACE: rename to `test_agent_session_queue_race.py`, update all symbols
- [ ] `tests/integration/test_job_queue_session_type.py` -- REPLACE: rename to `test_agent_session_queue_session_type.py`, update all symbols
- [ ] `tests/integration/test_job_scheduler.py` -- REPLACE: rename to `test_agent_session_scheduler.py`, update all symbols
- [ ] `tests/conftest.py` -- UPDATE: change fixture references from `job_id`/`enqueue_job` to new names
- [ ] ~60 additional test files -- UPDATE: change `job_id` references, import paths, and function call names

## Rabbit Holes

- **Model split (separating AgentSession into Session + Queue models)**: Out of scope. This is rename-only. Issue #530 tracks the broader OOP audit.
- **Popoto migration tooling**: Do not attempt to build a migration system for Popoto. The flush-and-recreate approach is the established pattern.
- **Backward compatibility aliases**: Do not leave `job_id` aliases or `enqueue_job` shims. Clean break only -- aliases accumulate debt.
- **Renaming "job" inside user-facing message strings**: Only rename if the string clearly refers to an AgentSession. Strings like "Good job" or user message content must not be touched.

## Risks

### Risk 1: Missed occurrences cause runtime NameError/ImportError
**Impact:** Bridge crashes on first message after deploy
**Mitigation:** Phase 5 includes a verification grep. The Verification table below includes a zero-occurrence check. CI tests must all pass before merge.

### Risk 2: Redis key structure change orphans in-flight sessions
**Impact:** Any running sessions at deploy time will lose their state
**Mitigation:** Deploy during low-traffic window. All AgentSession records are short-lived and reconstructed on next use. Document the flush requirement in deployment instructions.

### Risk 3: External callers or MCP tools reference old names
**Impact:** Agent tools that reference `job_scheduler` module path will break
**Mitigation:** Phase 4 updates CLAUDE.md, all MCP server configs, and skill files. The `tools/agent_session_scheduler.py` `__main__` block preserves the same CLI interface.

## Race Conditions

No race conditions identified -- this is a rename-only refactor with no behavioral changes. The Redis flush must happen when no sessions are active, which is the same constraint as PR #607.

## No-Gos (Out of Scope)

- Model split or restructuring (issue #530)
- Popoto migration tooling
- Backward compatibility aliases for old names
- Renaming Reflections terminology (already distinct)
- Changing any behavior, logic, or data flow

## Update System

The update script (`scripts/remote-update.sh`) and update skill need awareness of the Redis flush requirement. After pulling the rename commit:
1. The bridge must be stopped before the pull
2. AgentSession records must be flushed from Redis after the code update
3. The bridge can then be restarted with the new code

Add a post-pull hook or documented step: `python -c "from models.agent_session import AgentSession; [s.delete() for s in AgentSession.query.all()]"` (or equivalent Redis FLUSHDB scoped to AgentSession keys).

## Agent Integration

- The `tools/job_scheduler.py` MCP server entry point becomes `tools/agent_session_scheduler.py` -- the `.mcp.json` file must be updated if it references the old path
- The CLI entry point `python -m tools.job_scheduler` becomes `python -m tools.agent_session_scheduler` -- all skill files and CLAUDE.md references must be updated
- No new MCP servers or tools are being created
- The bridge (`bridge/telegram_bridge.py`) imports from `agent/job_queue.py` which becomes `agent/agent_session_queue.py` -- import paths must be updated

## Documentation

### Feature Documentation
- [ ] Rename `docs/features/job-queue.md` -> `docs/features/agent-session-queue.md`
- [ ] Rename `docs/features/job-health-monitor.md` -> `docs/features/agent-session-health-monitor.md`
- [ ] Rename `docs/features/job-scheduling.md` -> `docs/features/agent-session-scheduling.md`
- [ ] Rename `docs/features/job-dependency-tracking.md` -> `docs/features/agent-session-dependency-tracking.md`
- [ ] Rename `docs/features/scale-job-queue-with-popoto-and-worktrees.md` -> `docs/features/scale-agent-session-queue-with-popoto-and-worktrees.md`
- [ ] Rename `docs/features/sdlc-job-playlist.md` -> `docs/features/sdlc-agent-session-playlist.md`
- [ ] Update content inside all renamed doc files to use "agent session" instead of "job"
- [ ] Update `docs/features/README.md` index table with new filenames and descriptions
- [ ] Update `CLAUDE.md` quick commands table (`python -m tools.job_scheduler` -> `python -m tools.agent_session_scheduler`)
- [ ] Update all other CLAUDE.md references to job terminology

### Inline Documentation
- [ ] Update all docstrings in renamed modules
- [ ] Update comments referencing "job" in all touched files
- [ ] Update AgentSession model docstring (remove "job" references like "waiting_for_children - Parent job waiting for child jobs")

## Success Criteria

- [ ] Zero occurrences of `job_id`, `parent_job_id`, or `stable_job_id` on AgentSession model
- [ ] Zero occurrences of `_JOB_FIELDS`, `enqueue_job`, `_pop_job`, `retry_job`, `cancel_job` in Python source
- [ ] No files named `*job*` in `agent/`, `tools/`, or `tests/`
- [ ] `python -m tools.agent_session_scheduler status` works as the new CLI entry point
- [ ] All docs under `docs/features/` use "agent session" instead of "job" when referring to AgentSession
- [ ] CLAUDE.md quick commands table updated with new module names
- [ ] `grep -r "job" agent/ tools/ models/ bridge/ tests/ --include="*.py"` returns only false positives (user message strings, not symbol names)
- [ ] All tests pass (`pytest tests/ -x`)
- [ ] Lint clean (`python -m ruff check .`)
- [ ] Redis flush documented in deployment instructions

## Team Orchestration

### Team Members

- **Builder (phase-1-model)**
  - Name: model-renamer
  - Role: Rename model fields, update all references, flush Redis docs
  - Agent Type: builder
  - Resume: true

- **Builder (phase-2-modules)**
  - Name: module-renamer
  - Role: Rename files, update imports, remove dead code
  - Agent Type: builder
  - Resume: true

- **Builder (phase-3-symbols)**
  - Name: symbol-renamer
  - Role: Rename constants, functions, log messages across all files
  - Agent Type: builder
  - Resume: true

- **Builder (phase-4-docs)**
  - Name: docs-updater
  - Role: Update CLI, UI, CLAUDE.md, feature docs, skill files
  - Agent Type: builder
  - Resume: true

- **Builder (phase-5-generic)**
  - Name: generic-job-renamer
  - Role: Rename remaining generic "job" -> "task" where applicable
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: rename-validator
  - Role: Run grep verification, test suite, lint, CLI smoke test
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Model Field Renames (Phase 1)
- **Task ID**: build-model-fields
- **Depends On**: none
- **Validates**: `pytest tests/unit/ -x -q`, `python -m ruff check models/`
- **Assigned To**: model-renamer
- **Agent Type**: builder
- **Parallel**: false
- Rename `job_id` (AutoKeyField) -> `agent_session_id` on AgentSession model
- Rename `parent_job_id` (KeyField) -> `parent_agent_session_id`
- Rename `stable_job_id` (KeyField) -> `stable_agent_session_id`
- Update `@property id` to return `self.agent_session_id`
- Remove the `id` property docstring comment about "Cannot rename job_id"
- Update `scheduled_after` field comment from `_pop_job()` to `_pop_agent_session()`
- Update all references to these 3 fields across ALL Python files (use grep to find every occurrence)
- Update `_JOB_FIELDS` list entries: `parent_job_id` -> `parent_agent_session_id`, `stable_job_id` -> `stable_agent_session_id`
- Update `_push_job` parameter `parent_job_id` -> `parent_agent_session_id`
- Update all `.job_id` attribute access across the entire codebase
- Document Redis flush requirement in a new section of `docs/deployment.md`

### 2. Module and File Renames (Phase 2)
- **Task ID**: build-module-renames
- **Depends On**: build-model-fields
- **Validates**: `pytest tests/ -x -q`, `python -m ruff check .`
- **Assigned To**: module-renamer
- **Agent Type**: builder
- **Parallel**: false
- Rename `agent/job_queue.py` -> `agent/agent_session_queue.py`
- Rename `tools/job_scheduler.py` -> `tools/agent_session_scheduler.py`
- Remove `class Job` wrapper entirely (inline `AgentSession` usage at call sites) OR rename to `AgentSessionWorker` if the wrapper serves a real purpose
- Remove `RedisJob = AgentSession` alias (line 97 of job_queue.py)
- Remove `_REDIS_JOB_FIELDS = _JOB_FIELDS` alias (line 260)
- Update `agent/__init__.py` exports: remove `Job`, `RedisJob`, `enqueue_job`, `queue_revival_job`; add `enqueue_agent_session`, `queue_revival_agent_session`
- Update ALL import statements across the codebase (`from agent.job_queue import` -> `from agent.agent_session_queue import`)
- Rename test files (10 files listed in Test Impact section)
- Update test imports to match new module and file names

### 3. Constants, Functions, and Log Messages (Phase 3)
- **Task ID**: build-symbol-renames
- **Depends On**: build-module-renames
- **Validates**: `pytest tests/ -x -q`, `python -m ruff check .`
- **Assigned To**: symbol-renamer
- **Agent Type**: builder
- **Parallel**: false
- Rename `_JOB_FIELDS` -> `_AGENT_SESSION_FIELDS`
- Rename `_extract_job_fields()` -> `_extract_agent_session_fields()`
- Rename `_push_job()` -> `_push_agent_session()`
- Rename `_pop_job()` -> `_pop_agent_session()`
- Rename `_pop_job_with_fallback()` -> `_pop_agent_session_with_fallback()`
- Rename `enqueue_job()` -> `enqueue_agent_session()`
- Rename `_execute_job()` -> `_execute_agent_session()`
- Rename `_complete_job()` -> `_complete_agent_session()`
- Rename `reorder_job()` -> `reorder_agent_session()`
- Rename `cancel_job()` -> `cancel_agent_session()`
- Rename `retry_job()` -> `retry_agent_session()`
- Rename `queue_revival_job()` -> `queue_revival_agent_session()`
- Rename `recover_orphaned_jobs_all_projects()` -> `recover_orphaned_agent_sessions_all_projects()`
- Rename `_recover_interrupted_jobs_startup()` -> `_recover_interrupted_agent_sessions_startup()`
- Rename `_get_pending_jobs_sync()` -> `_get_pending_agent_sessions_sync()`
- Rename `_get_job_timeout()` -> `_get_agent_session_timeout()`
- Rename `_job_health_check()` -> `_agent_session_health_check()`
- Rename `_job_hierarchy_health_check()` -> `_agent_session_hierarchy_health_check()`
- Rename `_job_health_loop()` -> `_agent_session_health_loop()`
- Rename `_cli_flush_job()` -> `_cli_flush_agent_session()`
- Rename `_cli_recover_single_job()` -> `_cli_recover_single_agent_session()`
- Rename `JOB_HEALTH_CHECK_INTERVAL` -> `AGENT_SESSION_HEALTH_CHECK_INTERVAL`
- Rename `JOB_TIMEOUT_DEFAULT` -> `AGENT_SESSION_TIMEOUT_DEFAULT`
- Rename `JOB_TIMEOUT_BUILD` -> `AGENT_SESSION_TIMEOUT_BUILD`
- Rename `JOB_HEALTH_MIN_RUNNING` -> `AGENT_SESSION_HEALTH_MIN_RUNNING`
- In `tools/agent_session_scheduler.py`: rename `_format_job_info()` -> `_format_agent_session_info()`, `_kill_job()` -> `_kill_agent_session()`
- Update ALL log messages that say "job" to say "agent session" (e.g., `f"Parent job {self.parent_job_id} not found"` -> `f"Parent agent session {self.parent_agent_session_id} not found"`)
- Update ALL callers of renamed functions across bridge/, agent/, tools/, tests/

### 4. CLI, UI, and Documentation (Phase 4)
- **Task ID**: build-docs-cli
- **Depends On**: build-symbol-renames
- **Validates**: `python -m tools.agent_session_scheduler status`, `python -m ruff check .`
- **Assigned To**: docs-updater
- **Agent Type**: builder
- **Parallel**: false
- Update `tools/agent_session_scheduler.py` `__main__` block to preserve CLI interface
- Update CLAUDE.md quick commands table (all `job_scheduler` -> `agent_session_scheduler` references)
- Update CLAUDE.md session management and any other "job" references
- Rename 6 docs/features files (listed in Documentation section)
- Update content inside all renamed doc files
- Update `docs/features/README.md` index table
- Update `.mcp.json` if it references `tools/job_scheduler`
- Update all `.claude/commands/` and `.claude/skills/` files that reference job terminology
- Update dashboard/UI templates that display "job" text
- Update `docs/deployment.md` with Redis flush instructions for this rename

### 5. Generic "job" to "task" Cleanup (Phase 5)
- **Task ID**: build-generic-rename
- **Depends On**: build-docs-cli
- **Validates**: grep verification (see Verification table)
- **Assigned To**: generic-job-renamer
- **Agent Type**: builder
- **Parallel**: false
- Search for any remaining "job" occurrences that refer to generic work items (not AgentSession)
- Rename those to "task" where appropriate
- Do NOT rename: user message content, Reflections terminology, third-party library references, or string literals that happen to contain "job" in a non-technical context
- Update AgentSession model docstring: "waiting_for_children - Parent job waiting for child jobs" -> "waiting_for_children - Parent session waiting for child sessions"
- Final grep audit: `grep -rn "job" agent/ tools/ models/ bridge/ tests/ --include="*.py"` and classify every hit as false positive or missed rename

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-generic-rename
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format check: `python -m ruff format --check .`
- Verify CLI: `python -m tools.agent_session_scheduler --help`
- Run grep verification (see Verification table)
- Verify no files named `*job*` in `agent/`, `tools/`, `tests/`
- Verify `agent/__init__.py` exports are clean (no `Job`, `RedisJob`, `enqueue_job`)
- Report pass/fail on all success criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No job_id on model | `grep -n "job_id" models/agent_session.py` | exit code 1 |
| No _JOB_FIELDS | `grep -rn "_JOB_FIELDS" agent/ tools/ --include="*.py"` | exit code 1 |
| No enqueue_job | `grep -rn "enqueue_job" agent/ tools/ bridge/ tests/ --include="*.py"` | exit code 1 |
| No job files in agent | `ls agent/*job* 2>/dev/null` | exit code 2 |
| No job files in tools | `ls tools/*job* 2>/dev/null` | exit code 2 |
| No job test files | `ls tests/unit/test_job_* tests/integration/test_job_* 2>/dev/null` | exit code 2 |
| CLI works | `python -m tools.agent_session_scheduler --help` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue provides exhaustive scope, definitions, and acceptance criteria. The rename is mechanical with no design decisions required.
