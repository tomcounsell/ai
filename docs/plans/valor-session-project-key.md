---
status: docs_complete
type: bug
appetite: Small
owner: valorengels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/775
last_comment_id:
---

# valor-session create: derive project_key from projects.json

## Problem

`valor-session create` hardcodes `project_key="ai"` when enqueuing an `AgentSession` to Redis. On machines where the worker's `ACTIVE_PROJECTS` does not include `"ai"` (e.g. a machine configured for `valor` and `popoto`), CLI-created sessions are silently enqueued but never picked up. There is no error message — the session sits in Redis indefinitely.

**Current behavior:** `valor-session create` always pushes `project_key="ai"` regardless of which machine or directory it is run from (see `tools/valor_session.py` line 82).

**Desired outcome:** `valor-session create` derives `project_key` by matching the current working directory against the `working_directory` field of each project in `~/Desktop/Valor/projects.json`. A `--project-key` flag allows explicit override. Falls back to `"valor"` with a stderr warning if no match is found.

## Prior Art

No prior issues found related to this specific bug. The `project_key` field was introduced with the multi-project architecture and the CLI was never updated to derive it dynamically.

## Data Flow

1. **Entry point**: User runs `valor-session create --role pm --message "..."` from a shell
2. **`tools/valor_session.py:cmd_create`**: Reads CLI args, calls `_push_agent_session(project_key="ai", ...)` — hardcoded
3. **`agent/agent_session_queue.py:_push_agent_session`**: Writes `AgentSession` to Redis with the given `project_key`
4. **Standalone worker**: Polls Redis, filters sessions where `project_key in ACTIVE_PROJECTS` — sessions with `project_key="ai"` are silently skipped on non-ai machines
5. **Fix insertion point**: Step 2 — before calling `_push_agent_session`, resolve `project_key` from `projects.json` using `bridge/routing.py:load_config()`

## Architectural Impact

- **New dependency**: `tools/valor_session.py` gains an import from `bridge/routing.py` (or a shared helper). This is acceptable — `bridge/routing.py` is already a utility module with no bridge-runtime side effects when called standalone.
- **Interface changes**: `cmd_create` gains a `--project-key` CLI argument.
- **Coupling**: Slight increase — the CLI now depends on `bridge/routing.py`. An alternative is extracting `_resolve_config_path` + `load_config` into `tools/project_config.py` to avoid reaching into `bridge/`. Either approach is valid; reusing `bridge/routing.py` directly is simpler.
- **Reversibility**: Trivial to revert — the change is isolated to one call site.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/Desktop/Valor/projects.json` exists | `python -c "from pathlib import Path; assert (Path.home() / 'Desktop/Valor/projects.json').exists()"` | Config file must be present for resolution to work |

Run all checks: `python scripts/check_prerequisites.py docs/plans/valor-session-project-key.md`

## Solution

### Key Elements

- **`resolve_project_key(cwd)`**: New function that loads `projects.json` via `bridge/routing.py:load_config()`, iterates `config["projects"]`, and returns the key whose `working_directory` equals or is a parent of `cwd`. Falls back to `"valor"` with a stderr warning.
- **`--project-key` flag**: Added to `cmd_create`'s argument parser; if provided, skips resolution entirely.
- **`cmd_create` update**: Replace the hardcoded `project_key="ai"` with a call to `resolve_project_key(os.getcwd())` (or use the flag value).

### Flow

`valor-session create` invoked → check `--project-key` flag → if set, use it → else load `projects.json`, match cwd against `working_directory` entries → use matched key or fall back to `"valor"` → pass `project_key` to `_push_agent_session`

### Technical Approach

- Import `load_config` from `bridge.routing` (already handles `PROJECTS_CONFIG_PATH` env override and `~` expansion).
- `resolve_project_key(cwd: str) -> str`: iterate `config["projects"].items()`, for each project check `Path(cwd).is_relative_to(Path(project["working_directory"]))`, return first match's key. If no match, print warning to stderr and return `"valor"`.
- The `--project-key` flag short-circuits the lookup entirely — useful in scripts and CI where the cwd may not match any project.
- Keep the function in `tools/valor_session.py` (not a separate module) since this is the only consumer.

## Failure Path Test Strategy

### Exception Handling Coverage
- `load_config()` already handles missing file gracefully (returns `{"projects": {}, "defaults": {}}`). The fallback to `"valor"` covers this path.
- `resolve_project_key` must not raise if `working_directory` is missing or malformed in a project entry — use `.get("working_directory", "")` with a guard.

### Empty/Invalid Input Handling
- `working_directory` absent from a project entry: skip that project (don't crash).
- `projects.json` missing: `load_config()` returns empty dict; `resolve_project_key` returns `"valor"` with warning.
- `cwd` is not a subdirectory of any project: return `"valor"` with warning printed to stderr.

### Error State Rendering
- The fallback warning must go to `stderr` so it doesn't pollute JSON output when `--json` is used.
- `--json` output must still be valid JSON even if the fallback warning fires.

## Test Impact

No existing tests affected — `tools/valor_session.py:cmd_create` has no unit tests for the `project_key` parameter. This is greenfield test coverage.

## Rabbit Holes

- **Extracting a shared `tools/project_config.py` module**: Unnecessary for this fix; `bridge/routing.py` is already importable standalone. Refactoring into a shared module is a separate chore.
- **Resolving project key for `steer`, `kill`, `status` commands**: Those commands operate on an existing session by ID; `project_key` is irrelevant there. Only `create` needs this fix.
- **Updating `projects.json` schema**: The `working_directory` field already exists. No schema changes required.

## Risks

### Risk 1: `bridge/routing.py` import has bridge-startup side effects
**Impact:** Importing `load_config` from `bridge.routing` could trigger unexpected initialization if `bridge/routing.py` has module-level side effects.
**Mitigation:** Confirmed that `bridge/routing.py` module-level code only defines constants and functions — no network calls, no Redis connections. Safe to import standalone.

### Risk 2: Two projects with overlapping `working_directory` paths
**Impact:** A project at `~/src` and another at `~/src/ai` — cwd `~/src/ai/foo` would match both.
**Mitigation:** Use `is_relative_to` and return the **most specific match** (longest `working_directory` path), not the first match.

## Race Conditions

No race conditions identified — `resolve_project_key` is synchronous, reads a local file once, and has no shared mutable state.

## No-Gos (Out of Scope)

- Changing how the worker reads or filters `project_key` — that logic is correct.
- Adding `project_key` to `valor-session steer`, `kill`, or `status` commands.
- Migrating existing stuck sessions — this fix is forward-only.
- Auto-detecting project key for `valor-session list` filtering.

## Update System

No update system changes required — `projects.json` already exists on all machines and the lookup uses the same resolution logic as the bridge. No new config files or dependencies to propagate.

## Agent Integration

No agent integration required — `valor-session` is a CLI tool invoked by humans and scripts, not by the agent through MCP. The fix is internal to `tools/valor_session.py`.

## Documentation

- [ ] Update docstring in `tools/valor_session.py` to document the `--project-key` flag and the automatic cwd-based resolution.
- [ ] Add a note to `docs/tools-reference.md` under the `valor-session` entry describing project_key resolution behavior.

No new feature doc required — this is a bug fix to existing behavior.

## Success Criteria

- [ ] Running `valor-session create` from `/Users/valorengels/src/ai` resolves `project_key="ai"`
- [ ] Running it from `/Users/valorengels/src/valor` resolves `project_key="valor"`
- [ ] Running it from an unrecognized directory falls back to `"valor"` with a warning on stderr
- [ ] `--project-key ai` flag overrides resolution and uses `"ai"` directly
- [ ] Unit tests cover: matched project, fallback (no match), explicit flag override
- [ ] `--json` output is valid JSON even when fallback warning fires
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (project-key-resolution)**
  - Name: session-key-builder
  - Role: Implement `resolve_project_key`, `--project-key` flag, and unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (project-key-resolution)**
  - Name: session-key-validator
  - Role: Verify resolution logic, CLI flag, fallback behavior, and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement project_key resolution
- **Task ID**: build-project-key-resolution
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_project_key.py` (create)
- **Assigned To**: session-key-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `resolve_project_key(cwd: str) -> str` to `tools/valor_session.py`
- Add `--project-key` argument to `create` subparser
- Replace hardcoded `project_key="ai"` in `cmd_create` with resolution call
- Write `tests/unit/test_valor_session_project_key.py` covering: match, fallback, explicit flag

### 2. Validate implementation
- **Task ID**: validate-project-key-resolution
- **Depends On**: build-project-key-resolution
- **Assigned To**: session-key-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_session_project_key.py -v`
- Verify `--json` output is valid JSON when fallback fires
- Confirm no module-level side effects from importing `bridge.routing`

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-project-key-resolution
- **Assigned To**: session-key-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `tools/valor_session.py` docstring and `--help` text
- Add note to `docs/tools-reference.md` for `valor-session`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: session-key-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Run `python -m ruff check tools/valor_session.py`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_valor_session_project_key.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/valor_session.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/valor_session.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

None — solution is well-defined by the issue and code inspection.
