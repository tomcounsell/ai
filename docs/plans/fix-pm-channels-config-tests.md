---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/yudame/valor/issues/756
last_comment_id:
---

# Fix test_pm_channels Config Tests That Depend on Machine-Local projects.json

## Problem

Three tests in `tests/unit/test_pm_channels.py::TestPmConfigValidation` fail on machines whose local `projects.json` doesn't contain the exact persona/group structure they expect:

- `test_pm_persona_groups_exist` — fails: "No groups with project-manager persona found in config"
- `test_pm_persona_defined` — fails: "project-manager persona not defined"
- `test_valor_has_developer_group` — fails: "valor project should have at least one Dev group"

**Current behavior:** A shared `config` fixture loads the live `~/Desktop/Valor/projects.json` (or `config/projects.json` fallback). On dev-only machines with minimal config, these tests fail. They are *unit* tests but depend on machine-local state.

**Desired outcome:** All tests in `tests/unit/test_pm_channels.py` pass deterministically on every machine, regardless of local config contents.

## Prior Art

No prior issues or PRs found related to this work.

## Appetite

**Size:** Small
**Team:** Solo dev
**Interactions:** PM check-ins: 0; Review rounds: 1

## Prerequisites

No prerequisites — this is a test-only change with no external dependencies.

## Solution

### Key Elements

- **Inline fixture**: Replace the file-loading `config` fixture in `TestPmConfigValidation` with an inline dict containing a known-good config structure (personas section with `project-manager`, a `valor` project with a `Dev:` group, etc.)
- **No file I/O**: Remove the `Path.home() / "Desktop" / "Valor"` lookup and the legacy `config/projects.json` fallback from this fixture. The four tests in this class become pure unit tests against controlled data.
- **Preserve assertions**: Keep all four test assertions unchanged — they validate that a well-formed config has the expected structure.

### Technical Approach

- Build a minimal but representative dict in the `config` fixture:
  - `personas` containing at least `project-manager` and `developer`
  - `projects.valor.telegram.groups` containing one PM group (`persona: project-manager`) and one `Dev: ...` group (`persona: developer`)
- Other test classes in the same file already follow this mock-based pattern, so this aligns `TestPmConfigValidation` with the rest of the file.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. The fixture currently uses `pytest.skip()`; the new fixture has no I/O so no skip is needed.

### Empty/Invalid Input Handling
- Not applicable — this is a test fixture refactor, not a function with user input.

### Error State Rendering
- Not applicable — no user-facing output.

## Test Impact

- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_pm_persona_groups_exist` — UPDATE: now reads from inline fixture instead of disk
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_pm_persona_defined` — UPDATE: same
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_dev_groups_use_developer_persona` — UPDATE: same (already passes but fixture changes)
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_valor_has_developer_group` — UPDATE: same

## Rabbit Holes

- Do NOT refactor the production code that loads `projects.json` — the bug is purely in the test fixture's coupling to disk state.
- Do NOT add a separate "real config validator" script. If we want to validate live configs on bridge machines, that's a separate issue.
- Do NOT touch the other test classes in `test_pm_channels.py` — they already use mocks and pass.

## Risks

### Risk 1: Inline fixture drifts from real config schema
**Impact:** Tests pass against a fictional structure that no real `projects.json` matches, providing false confidence.
**Mitigation:** Model the fixture on the actual production `projects.json` schema (verify against `config/projects.json` in the repo). Keep the fixture minimal so drift is obvious.

## Race Conditions

No race conditions identified — this is a synchronous, single-threaded test fixture refactor.

## No-Gos (Out of Scope)

- Validating real machine configs at test time (separate concern, separate issue if needed)
- Refactoring the production config loader
- Adding new persona types or project entries

## Update System

No update system changes required — this is a test-only change with no runtime impact.

## Agent Integration

No agent integration required — this is a test fixture change with no runtime or tool surface impact.

## Documentation

No documentation changes needed — this is a bug fix in a test fixture; behavior is internal to the test suite and not user-visible. No `docs/features/` entry, no README update, and no inline doc changes are warranted.

## Success Criteria

- [ ] `pytest tests/unit/test_pm_channels.py -v` passes on a machine with a minimal `projects.json` (no personas, empty groups)
- [ ] `pytest tests/unit/test_pm_channels.py -v` passes on a machine with a full bridge `projects.json`
- [ ] `TestPmConfigValidation` fixture does not read from any file on disk
- [ ] No other tests in the suite are broken by the change
- [ ] Tests pass (`/do-test`)

## Step by Step Tasks

### 1. Replace fixture with inline config
- **Task ID**: build-fixture
- **Depends On**: none
- **Validates**: tests/unit/test_pm_channels.py
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the `config` fixture in `TestPmConfigValidation` with an inline dict modeling a well-formed `projects.json` (personas + valor project with PM group and Dev group)
- Run `pytest tests/unit/test_pm_channels.py -v` to confirm all four tests pass
- Run `pytest tests/unit/ -q` to confirm no regressions elsewhere

### 2. Final validation
- **Task ID**: validate-all
- **Depends On**: build-fixture
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm all `TestPmConfigValidation` tests pass without reading any file on disk (grep the fixture body for `Path` / `open`)
- Confirm full unit suite still green

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Target tests pass | `pytest tests/unit/test_pm_channels.py -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -q` | exit code 0 |
| Fixture has no disk I/O | `grep -c "Path\|open(" tests/unit/test_pm_channels.py` (in TestPmConfigValidation only) | manual: zero references inside the class fixture |
| Format clean | `python -m ruff format --check tests/unit/test_pm_channels.py` | exit code 0 |
