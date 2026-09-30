---
status: Done
type: chore
appetite: Medium
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/397
last_comment_id: 
---

# Audit: Simplify project context propagation from channel to session

## Problem

When a Telegram message arrives, `find_project_for_chat()` resolves the full project config once. But only fragments (`project_key`, `working_dir`) survive downstream. The full config is then re-derived at multiple layers through parallel registries and string comparisons.

**Current behavior:**
- `_project_configs` module-level dict in `agent_session_queue.py` acts as a parallel registry, populated at bridge startup via `register_project_config()`, then looked up again at execution time
- `AI_REPO_ROOT` string comparisons in `sdk_client.py` (4 locations) serve as a proxy for "is this the ai project" instead of checking `project_key`
- Project properties (`github.org`, `github.repo`, `name`, `mode`) are re-extracted from the project dict at 3+ separate locations in `sdk_client.py`
- `enqueue_agent_session()` accepts `project_key` and `working_dir` as separate strings, losing the full project context
- `build_context_prefix()` in `bridge/context.py` independently re-extracts project properties

**Desired outcome:**
A message arrives in a channel, project is resolved once, and a session context object carries all project properties through the entire pipeline. No re-derivation, no minimal dicts, no parallel registries.

## Prior Art

- **Issue #375**: Cross-repo gh resolution -- identified the symptom of lost project context
- **PR #378, #396**: Fix attempts via `--repo` and `GH_REPO` injection -- band-aids on the symptom
- **Commit c384309f**: Patched `_execute_job` to use registered config -- added the parallel registry as a workaround
- **Issue #459 / PR #490**: SDLC Redesign -- fixed double classification and observer simplification but did NOT address context propagation
- **Issue #285 / PR #286**: AgentSession as single source of truth for auto-continue -- established the pattern of AgentSession carrying state, but did not extend to project config
- **PR #595**: Merged DM whitelist into projects.json -- consolidated config but did not propagate it through sessions

## Data Flow

1. **Entry point**: Telegram message arrives at `bridge/telegram_bridge.py`
2. **Project resolution**: `find_project_for_chat()` resolves full project dict from `projects.json`
3. **Enqueue**: `enqueue_agent_session()` receives `project_key` + `working_dir` (full config lost here)
4. **AgentSession created**: Popoto model stores `project_key` and `working_dir` only
5. **Worker picks up session**: `_execute_agent_session()` calls `get_project_config(session.project_key)` to re-fetch from `_project_configs` registry
6. **SDK client**: `get_agent_response_sdk()` receives project dict, re-extracts `name`, `working_directory`, `github.org`, `github.repo` at multiple points
7. **Context building**: `build_context_prefix()` independently re-reads project dict for `name`, `description`, `tech_stack`, `github.repo`

**After this refactor:** Steps 3-5 collapse. AgentSession carries `project_config` DictField. No re-lookup needed at execution time.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #378 | Added `--repo` flags to gh commands | Symptom-level fix: didn't address why project context was missing |
| PR #396 | Injected `GH_REPO` env var | Better fix for gh commands, but still relies on re-deriving project config at execution time |
| Commit c384309f | Added `_project_configs` registry | Created a parallel lookup mechanism instead of propagating config through the session |

**Root cause pattern:** Each fix addressed a specific symptom (gh targeting the wrong repo, missing config at execution time) without addressing the structural issue: project config is resolved once but not carried through the pipeline.

## Architectural Impact

- **New dependencies**: None -- uses existing Popoto DictField
- **Interface changes**: `enqueue_agent_session()` gains a `project_config` parameter; `_project_configs` registry and its accessors are removed
- **Coupling**: Decreases coupling -- eliminates the implicit dependency on module-level `_project_configs` dict
- **Data ownership**: AgentSession becomes the single owner of project config for its lifetime
- **Reversibility**: Fully reversible -- the DictField can be removed and the registry restored if needed

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are internal refactoring of existing code paths.

## Solution

### Key Elements

- **AgentSession.project_config**: New DictField on the model that carries the full project config dict through the pipeline
- **Enqueue propagation**: `enqueue_agent_session()` accepts and stores the full project config at creation time
- **Registry elimination**: Remove `_project_configs`, `register_project_config()`, and `get_project_config()` from `agent_session_queue.py`
- **AI_REPO_ROOT replacement**: Replace 4 string comparisons with `project_key` identity checks

### Flow

**Message arrives** -> `find_project_for_chat()` resolves config -> `enqueue_agent_session(project_config=config)` -> AgentSession stores config in DictField -> `_execute_agent_session()` reads `session.project_config` directly -> `get_agent_response_sdk()` reads from session config -> done

### Technical Approach

- Add `project_config = DictField(required=False)` to AgentSession model (default empty dict for backward compat with existing sessions)
- Update `enqueue_agent_session()` to accept `project_config: dict` and store it on the session
- Update all callers of `enqueue_agent_session()` in `telegram_bridge.py` to pass the full project dict
- In `_execute_agent_session()`, read `session.project_config` instead of calling `get_project_config()`
- In `sdk_client.py`, replace `project_working_dir != AI_REPO_ROOT` with `project_key != "valor"` (or check `session.project_config` directly)
- Remove `_project_configs`, `register_project_config()`, `get_project_config()` once all callers are migrated
- Update `cleanup_stale_branches_all_projects()` to iterate project configs from `projects.json` directly instead of `_project_configs`

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Verify `_execute_agent_session()` handles missing/empty `project_config` gracefully (backward compat for sessions created before migration)
- [x] No new exception handlers introduced -- existing error handling paths remain unchanged

### Empty/Invalid Input Handling
- [x] Test AgentSession creation with `project_config=None` and `project_config={}` -- both should work
- [x] Test `enqueue_agent_session()` without `project_config` parameter -- should default to empty dict

### Error State Rendering
- [x] No user-visible output changes -- this is an internal refactor

## Test Impact

- [x] `tests/unit/test_cross_repo_gh_resolution.py::test_get_project_config_returns_full_config` -- DELETE: tests `get_project_config()` which is being removed
- [x] `tests/unit/test_cross_repo_gh_resolution.py::test_get_project_config_returns_empty_for_unknown` -- DELETE: tests `get_project_config()` which is being removed
- [x] `tests/unit/test_cross_repo_gh_resolution.py` (other tests using `register_project_config`) -- UPDATE: replace registry setup with direct AgentSession.project_config assignment
- [x] `tests/unit/test_summarizer.py::test_summarizer_sends_to_correct_github_repo` (line 1792) -- UPDATE: replace `register_project_config()` with session-level config
- [x] `tests/unit/test_summarizer.py::test_summarizer_skips_github_summary_without_config` (line 1845) -- UPDATE: replace `register_project_config()` with session-level config
- [x] `tests/unit/test_formatting.py` (4 tests patching `get_project_config`) -- UPDATE: patch `session.project_config` instead
- [x] `tests/unit/test_duplicate_delivery.py` (tests referencing `enqueue_agent_session`) -- UPDATE: add `project_config` parameter to test calls
- [x] `tests/unit/test_ui_sdlc_data.py` (tests patching `_load_project_configs`) -- KEEP: this is a UI-layer config loader, not the same registry

## Rabbit Holes

- Refactoring `projects.json` schema or loader -- out of scope, the config format is fine
- Creating a typed ProjectConfig dataclass -- tempting but adds complexity for no immediate benefit; a plain dict is sufficient since the schema is already defined by `projects.json`
- Migrating existing AgentSession records to populate `project_config` -- unnecessary, the DictField defaults to empty and code handles that gracefully
- Refactoring `build_context_prefix()` to take an AgentSession instead of a project dict -- desirable but separate concern; it already receives a dict, just ensure it gets it from the session

## Risks

### Risk 1: Backward compatibility with in-flight sessions
**Impact:** Sessions created before the migration won't have `project_config` populated
**Mitigation:** DictField defaults to empty dict; `_execute_agent_session()` falls back to loading from `projects.json` if `session.project_config` is empty (transitional, removable after one deploy cycle)

### Risk 2: Large dict serialization in Redis
**Impact:** Project config dicts are small (a few hundred bytes) but storing them on every session adds Redis memory
**Mitigation:** Project configs are tiny; even with hundreds of sessions the overhead is negligible

## Race Conditions

No race conditions identified -- project config is resolved once at message intake (synchronous) and stored immutably on the AgentSession. The config is read-only after creation; no concurrent writers.

## No-Gos (Out of Scope)

- Creating a typed ProjectConfig class or Pydantic model -- plain dict is sufficient
- Changing the `projects.json` schema or loader
- Refactoring `build_context_prefix()` signature to accept AgentSession
- Migrating existing Redis AgentSession records
- Changing how `find_project_for_chat()` works

## Update System

No update system changes required -- this is a purely internal refactor. No new dependencies, no config file changes, no migration steps. The existing `projects.json` format is unchanged.

## Agent Integration

No agent integration required -- this is a bridge-internal refactor. No new MCP servers, no changes to `.mcp.json`, no new tools exposed. The agent's interaction with sessions is unchanged; only the internal plumbing of how project config reaches the SDK client changes.

## Documentation

- [x] Update `docs/features/session-isolation.md` to document that AgentSession now carries `project_config`
- [x] Add inline docstring on `AgentSession.project_config` field explaining its purpose and lifecycle
- [x] Update docstring on `enqueue_agent_session()` to document the `project_config` parameter

## Success Criteria

- [x] `_project_configs` module-level dict removed from `agent/agent_session_queue.py`
- [x] `register_project_config()` and `get_project_config()` functions removed
- [x] All 4 `AI_REPO_ROOT` string comparisons in `sdk_client.py` replaced with `project_key` checks
- [x] `AgentSession` model has a `project_config` DictField
- [x] `enqueue_agent_session()` accepts and stores full project config
- [x] `_execute_agent_session()` reads config from session, not from registry
- [x] No references to `_project_configs` remain in production code (test code excluded)
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (context-propagation)**
  - Name: context-builder
  - Role: Implement all context propagation changes across model, queue, sdk_client, and bridge
  - Agent Type: builder
  - Resume: true

- **Validator (context-propagation)**
  - Name: context-validator
  - Role: Verify registry elimination, AI_REPO_ROOT removal, and backward compatibility
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add project_config DictField to AgentSession
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_cross_repo_gh_resolution.py
- **Assigned To**: context-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `project_config = DictField(required=False)` to `models/agent_session.py`
- Update `enqueue_agent_session()` in `agent/agent_session_queue.py` to accept `project_config: dict` parameter and store it on the session
- Update all callers of `enqueue_agent_session()` in `bridge/telegram_bridge.py` to pass the full project dict

### 2. Eliminate _project_configs registry
- **Task ID**: build-registry-removal
- **Depends On**: build-model
- **Validates**: tests/unit/test_cross_repo_gh_resolution.py, tests/unit/test_summarizer.py, tests/unit/test_formatting.py
- **Assigned To**: context-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_execute_agent_session()`, read `session.project_config` instead of calling `get_project_config()`
- Add transitional fallback: if `session.project_config` is empty, load from `projects.json` directly
- Update `cleanup_stale_branches_all_projects()` to load configs from `projects.json` instead of iterating `_project_configs`
- Remove `_project_configs`, `register_project_config()`, `get_project_config()` from `agent_session_queue.py`
- Remove `register_project_config()` calls from `bridge/telegram_bridge.py` startup

### 3. Replace AI_REPO_ROOT comparisons
- **Task ID**: build-repo-root-replacement
- **Depends On**: build-model
- **Validates**: tests/unit/test_cross_repo_gh_resolution.py
- **Assigned To**: context-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace 4 `project_working_dir != AI_REPO_ROOT` comparisons in `agent/sdk_client.py` with `project_key != "valor"` checks (or equivalent project_config-based check)
- Keep `AI_REPO_ROOT` constant if used elsewhere; remove if now unused

### 4. Update affected tests
- **Task ID**: build-tests
- **Depends On**: build-registry-removal, build-repo-root-replacement
- **Validates**: pytest tests/unit/ -x -q
- **Assigned To**: context-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `test_get_project_config_*` tests that test removed functions
- Update tests using `register_project_config()` to use `session.project_config` directly
- Update tests patching `get_project_config` to patch `session.project_config` instead
- Update `enqueue_agent_session` test calls to include `project_config` parameter

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: context-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify no references to `_project_configs` in production code: `grep -rn '_project_configs' agent/ bridge/ models/ --include="*.py"`
- Verify no `AI_REPO_ROOT` comparisons remain: `grep -n 'AI_REPO_ROOT' agent/sdk_client.py`
- Verify `project_config` DictField exists on AgentSession

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: context-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with project_config propagation details
- Add inline docstrings on new/modified functions

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: context-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No _project_configs in prod | `grep -rn '_project_configs' agent/ bridge/ models/ --include='*.py'` | exit code 1 |
| No AI_REPO_ROOT comparisons | `grep -c 'AI_REPO_ROOT' agent/sdk_client.py` | output contains 1 |
| project_config field exists | `grep -c 'project_config.*DictField' models/agent_session.py` | output contains 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the scope is well-defined by the code audit and the design decision is straightforward (store config on session, remove parallel registry).
