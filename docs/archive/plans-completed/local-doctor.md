---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/855
last_comment_id:
---

# Local Doctor Tool -- Unified Health Check CLI

## Problem

Developers have no single command to validate that their local environment is healthy before starting work. Health checks exist but are scattered across four separate locations:

- `monitoring/health.py` -- Redis, Telegram bridge status, disk space, API key presence (importable library, no CLI)
- `scripts/update/verify.py` -- Python deps, system tools, Telegram session auth, SDK auth, MCP servers, gitignore issues (designed for the update system, not developer use)
- `ui/app.py` `/health` endpoint -- bridge/worker heartbeat status (requires the web server running)
- `monitoring/resource_monitor.py` -- memory/CPU/disk monitoring (importable, no CLI)

**Current behavior:**
When something breaks (missing dep, expired Telegram session, Redis down), the developer discovers it mid-task instead of upfront. There is no pre-push hook running quality checks.

**Desired outcome:**
A single `python -m tools.doctor` command that runs all environment and health checks, prints a clear pass/fail report with actionable fix suggestions, and exits with appropriate status codes.

## Freshness Check

**Baseline commit:** `8a755bc612f4614a05a85eae294382026fa9b056`
**Issue filed at:** 2026-04-09T08:34:42Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `monitoring/health.py` -- `HealthChecker` class with `check_database()`, `check_telegram_connection()`, `check_api_keys()`, `check_disk_space()`, `get_overall_health()` -- confirmed present and unchanged
- `scripts/update/verify.py` -- `verify_environment()` function with `check_system_tools()`, `check_python_deps()`, `check_dev_tools()`, `check_valor_tools()`, `check_telegram_session()`, `check_sdk_auth()`, `check_mcp_servers()`, `check_gitignore_issues()`, `verify_models()` -- confirmed present and unchanged
- `monitoring/resource_monitor.py` -- `ResourceSnapshot.capture()` for memory/CPU/disk metrics -- confirmed present and unchanged
- `ui/app.py:280` -- `/health` endpoint returning bridge/worker heartbeat status -- confirmed present and unchanged

**Cited sibling issues/PRs re-checked:** None cited.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None.

## Prior Art

No prior issues or PRs found related to a unified doctor/health-check CLI tool. The existing infrastructure was built incrementally for specific subsystems.

## Data Flow

1. **Entry point**: `python -m tools.doctor` invoked from the command line
2. **Check orchestrator** (`tools/doctor.py`): Collects check categories, runs them sequentially, gathers results
3. **Existing checkers** (reused, not duplicated):
   - `monitoring/health.py:HealthChecker` -- Redis, disk, API keys, Telegram bridge process
   - `scripts/update/verify.py` functions -- system tools, Python deps, dev tools, Telegram session, SDK auth, MCP servers
   - `scripts/update/service.py` -- `is_bridge_running()`, `is_worker_running()` for service status
4. **New check**: Quality checks (ruff, pytest) -- subprocess calls, opt-in via `--quality` flag
5. **Output**: Formatted pass/fail report to stdout (or JSON with `--json`), exit code 0 or 1

## Architectural Impact

- **New dependencies**: None. Reuses existing code from `monitoring/` and `scripts/update/`.
- **Interface changes**: None. New CLI entry point only; existing code is consumed, not modified.
- **Coupling**: Low. `tools/doctor.py` imports from existing modules but does not modify them.
- **Data ownership**: No change. Doctor is read-only -- it observes system state, never mutates it.
- **Reversibility**: Trivial. Delete `tools/doctor.py` and its test file.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. It reuses existing checked-in code.

## Solution

### Key Elements

- **Check registry**: A list of check functions, each returning a structured result (name, status, message, fix suggestion)
- **Check runner**: Iterates through registered checks, collects results, handles timeouts
- **Report formatter**: Renders results as a human-readable pass/fail table or JSON
- **CLI interface**: argparse-based with `--quality`, `--json`, `--quick`, `--install-hook` flags
- **Git hook installer**: Writes a pre-push hook that runs `python -m tools.doctor --quick`

### Flow

**Terminal** -> `python -m tools.doctor` -> **Check runner** -> [reuse HealthChecker, verify.py, service.py] -> **Report formatter** -> stdout (pass/fail table or JSON) -> exit 0 or 1

### Technical Approach

- Create `tools/doctor.py` as a single-file CLI module with `__main__` support
- Define a `CheckResult` dataclass: `name`, `category`, `passed` (bool), `message`, `fix` (optional string)
- Wrap existing check functions to produce `CheckResult` instances:
  - Import `HealthChecker` from `monitoring/health.py` for Redis, disk, API keys
  - Import individual check functions from `scripts/update/verify.py` for system tools, Python deps, dev tools, Telegram session, SDK auth, MCP servers
  - Import `is_bridge_running`, `is_worker_running` from `scripts/update/service.py` for service status
- Categories: **Environment** (Python, system tools, deps), **Services** (Redis, bridge, worker), **Auth** (Telegram session, API keys, SDK auth), **Resources** (disk, memory), **Quality** (ruff, pytest -- opt-in)
- Quality checks run ruff and pytest as subprocesses; only execute when `--quality` is passed
- `--quick` skips slow checks: Telegram session auth probe, pytest, `verify_models()`
- `--json` outputs a JSON object with `{passed: bool, checks: [CheckResult...], summary: {total, passed, failed}}`
- `--install-hook` writes `.git/hooks/pre-push` that runs `python -m tools.doctor --quick`
- Each check has a timeout (default 10s) to prevent hanging
- Exit code: 0 if all checks pass, 1 if any fail

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/doctor.py` wraps each check in try/except -- test that a single check failure does not crash the whole run (returns a failed CheckResult instead)
- [ ] Test that import errors from optional dependencies (e.g., psutil not installed) produce a graceful degraded result

### Empty/Invalid Input Handling
- [ ] Test `--json` output when zero checks are registered (edge case: empty check list)
- [ ] Test behavior when `.env` file is missing (API key checks should degrade gracefully)

### Error State Rendering
- [ ] Test that failed checks show actionable fix suggestions in both text and JSON output
- [ ] Test that the report clearly distinguishes pass/fail/skip states visually

## Test Impact

No existing tests affected -- this is a greenfield feature with no prior test coverage. The new module `tools/doctor.py` does not modify any existing code; it only imports and calls existing functions.

The existing test file `tests/unit/test_health_check.py` tests `agent/health_check.py` (the watchdog hook), not `monitoring/health.py`, so it is unaffected.

## Rabbit Holes

- **Rewriting existing checks**: The doctor must reuse `monitoring/health.py` and `scripts/update/verify.py` as-is. Do not refactor or consolidate the underlying check implementations -- that is a separate effort.
- **Cross-platform support**: This system runs on macOS only. Do not add Windows/Linux compatibility.
- **Deep API validation**: Pinging the Anthropic API costs credits and is slow. Only do this behind `--deep` flag, not by default.
- **Auto-fix mode**: Tempting to add `--fix` that automatically repairs issues. Out of scope -- the doctor diagnoses, it does not treat.
- **Dashboard integration**: Wiring doctor results into the web UI dashboard is a separate effort.

## Risks

### Risk 1: Import side effects from verify.py
**Impact:** `scripts/update/verify.py` modifies `os.environ["PATH"]` at import time (adds pyenv, homebrew paths). This could affect the doctor's own environment.
**Mitigation:** This is actually desirable for the doctor use case -- it ensures tools are findable. No action needed, but document the behavior in the doctor module's docstring.

### Risk 2: Telegram session lock contention
**Impact:** `check_telegram_session()` in verify.py opens a Telethon client, which can conflict with a running bridge that holds the session lock.
**Mitigation:** verify.py already handles this -- it checks `is_bridge_running()` first and trusts the running bridge. The doctor inherits this safe behavior by reusing the existing function.

## Race Conditions

No race conditions identified -- the doctor is a read-only CLI tool that runs synchronously in a single process. It does not mutate shared state.

## No-Gos (Out of Scope)

- Auto-fix / remediation mode (`--fix`)
- Dashboard/web UI integration
- Refactoring the underlying health check implementations
- Cross-platform (Windows/Linux) support
- Pre-commit hook (too slow; only pre-push)
- Continuous monitoring / daemon mode

## Update System

The update script (`scripts/update/verify.py`) already runs environment verification during updates. The doctor tool reuses those same checks but makes them available as a standalone CLI. No changes to the update system are needed -- the doctor is a new consumer of existing update infrastructure, not a replacement.

## Agent Integration

No agent integration required -- this is a developer-facing CLI tool. The agent does not need to run health checks via MCP; it has its own runtime monitoring (`monitoring/health.py`, `agent/health_check.py`). The doctor is for human developers at the terminal.

## Documentation

- [ ] Create `docs/features/local-doctor.md` describing the CLI tool, flags, output format, and hook installation
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add `python -m tools.doctor` entry to CLAUDE.md Quick Commands table

## Success Criteria

- [ ] `python -m tools.doctor` runs all checks and prints a pass/fail report with fix suggestions
- [ ] `python -m tools.doctor --quality` additionally runs ruff check, ruff format --check, and pytest
- [ ] `python -m tools.doctor --json` outputs machine-readable JSON
- [ ] `python -m tools.doctor --quick` skips slow checks (pytest, deep API validation, Telegram session probe)
- [ ] `python -m tools.doctor --install-hook` installs a git pre-push hook
- [ ] Exit code 0 when all checks pass, non-zero when any check fails
- [ ] Doctor reuses existing code from `monitoring/health.py` and `scripts/update/verify.py` -- no duplication
- [ ] Unit tests cover the doctor's check orchestration and output formatting
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (doctor)**
  - Name: doctor-builder
  - Role: Implement `tools/doctor.py` CLI module with check orchestration, output formatting, and hook installer
  - Agent Type: builder
  - Resume: true

- **Test Engineer (doctor)**
  - Name: doctor-tester
  - Role: Write unit tests for check orchestration, output formatting, and CLI flags
  - Agent Type: test-engineer
  - Resume: true

- **Validator (doctor)**
  - Name: doctor-validator
  - Role: Verify all success criteria are met
  - Agent Type: validator
  - Resume: true

- **Documentarian (doctor)**
  - Name: doctor-docs
  - Role: Create feature documentation and update CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build doctor CLI module
- **Task ID**: build-doctor
- **Depends On**: none
- **Validates**: tests/unit/test_doctor.py (create)
- **Assigned To**: doctor-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/doctor.py` with `CheckResult` dataclass and check registry
- Implement check wrappers that call existing functions from `monitoring/health.py`, `scripts/update/verify.py`, and `scripts/update/service.py`
- Organize checks into categories: Environment, Services, Auth, Resources, Quality
- Implement CLI with argparse: `--quality`, `--json`, `--quick`, `--install-hook`
- Implement text report formatter with pass/fail indicators and fix suggestions
- Implement JSON output mode
- Implement `--install-hook` that writes `.git/hooks/pre-push`
- Add `if __name__ == "__main__"` and `__main__.py` support for `python -m tools.doctor`

### 2. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-doctor
- **Validates**: tests/unit/test_doctor.py
- **Assigned To**: doctor-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test CheckResult dataclass serialization
- Test check orchestration: all checks run, one failure does not crash others
- Test text report output format (pass/fail indicators, fix suggestions shown)
- Test JSON output structure
- Test `--quick` flag skips slow checks
- Test `--quality` flag includes ruff/pytest checks
- Test exit code: 0 for all pass, 1 for any fail
- Test `--install-hook` writes the correct hook file
- Test graceful handling of import errors / missing dependencies

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: doctor-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/local-doctor.md` with usage examples and flag reference
- Add entry to `docs/features/README.md` index table
- Add `python -m tools.doctor` to CLAUDE.md Quick Commands table

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: doctor-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m tools.doctor` and verify output
- Run `python -m tools.doctor --json` and verify JSON structure
- Run `pytest tests/unit/test_doctor.py -v` and verify all pass
- Run `python -m ruff check tools/doctor.py` and verify clean
- Verify all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_doctor.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/doctor.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/doctor.py` | exit code 0 |
| Module runnable | `python -m tools.doctor --help` | exit code 0 |
| JSON output valid | `python -m tools.doctor --json 2>/dev/null \| python -c "import sys,json; json.load(sys.stdin)"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue is well-scoped with clear acceptance criteria, and the implementation approach reuses existing infrastructure without ambiguity.
