---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/756
last_comment_id:
---

# Fix test_pm_channels Config Tests That Depend on Machine-Local projects.json

## Problem

Three tests in `tests/unit/test_pm_channels.py::TestPmConfigValidation` fail on any machine where the local `projects.json` (either `~/Desktop/Valor/projects.json` or `config/projects.json`) does not contain the exact persona/group structure they assert against:

- `test_pm_persona_groups_exist` — fails with _"No groups with project-manager persona found in config"_
- `test_pm_persona_defined` — fails with _"project-manager persona not defined"_
- `test_valor_has_developer_group` — fails with _"valor project should have at least one Dev group"_

**Current behavior:** The `config` fixture in `TestPmConfigValidation` reads the live config file from disk. On dev-only machines with a minimal `projects.json` (no personas, empty groups), the assertions fail. The fixture only `pytest.skip()`s when the file is entirely missing — not when its contents differ from the bridge-machine shape.

**Desired outcome:** All unit tests in `tests/unit/test_pm_channels.py` pass on every machine, regardless of the local config file contents. Unit tests must be deterministic and environment-independent.

## Prior Art

No prior issues or PRs found that addressed this specific test environment dependency. The issue is freshly identified.

## Data Flow

Single-file change. No multi-component data flow.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a small, contained test refactor. The change touches one test class in one file.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Inline fixture config**: A pytest fixture that returns a hardcoded, well-formed config dict containing a `personas` section with `project-manager` and `developer` entries, plus a `valor` project with at least one `Dev:` group.
- **No file I/O in `TestPmConfigValidation`**: The tests assert structural properties of a controlled config dict, not the live machine config.

### Technical Approach

- Replace the existing `config` fixture body in `TestPmConfigValidation` (lines 281-291) with a hardcoded dict literal that satisfies all four tests in the class.
- Keep all four test method bodies unchanged — they continue to assert the same structural rules, just against deterministic input.
- Remove the `Path.home()`, `Path(__file__)`, file existence checks, `pytest.skip()`, and `json.load()` calls from the fixture.
- The fixture dict must contain:
  - `personas.project-manager` (any value, just must exist)
  - `personas.developer`
  - `projects.valor.telegram.groups` containing at least one PM-persona group AND at least one `Dev:`-prefixed developer group
- This aligns `TestPmConfigValidation` with the pattern already used by other test classes in the same file (`TestLoadPmSystemPrompt`, `TestPmModeClassificationBypass`, etc.) which all use mocks/fixtures and pass on all machines.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. The fixture-based tests are pure assertions on a literal dict.

### Empty/Invalid Input Handling
- Not applicable — the fixture provides controlled input. The original tests defended against malformed config; the new fixture removes the variability that made those defenses necessary.

### Error State Rendering
- No user-visible output. Tests are pytest-only.

## Test Impact

- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_pm_persona_groups_exist` — UPDATE: same assertion against new fixture dict
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_pm_persona_defined` — UPDATE: same assertion against new fixture dict
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_dev_groups_use_developer_persona` — UPDATE: same assertion against new fixture dict (already passing, but fixture changes underneath)
- [ ] `tests/unit/test_pm_channels.py::TestPmConfigValidation::test_valor_has_developer_group` — UPDATE: same assertion against new fixture dict

All four tests in the class must continue to assert the same structural rules. No deletions, no rewrites — only the fixture source changes.

## Rabbit Holes

- **Do NOT** introduce a separate "live config validation" test that runs only on bridge machines. That belongs in a separate integration check, not unit tests. Out of scope for this fix.
- **Do NOT** refactor the other test classes in the file — they already work correctly.
- **Do NOT** touch `config/projects.json` itself or modify the loader code in `bridge/`. The fix is test-only.

## Risks

### Risk 1: Loss of live-config validation
**Impact:** If someone breaks `config/projects.json` schema on a bridge machine, these unit tests will no longer catch it.
**Mitigation:** This was never the right layer for that check. Live config validation belongs in startup checks or a dedicated integration test (not in this plan's scope). Add a follow-up issue if desired.

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded, and confined to pytest fixture setup.

## No-Gos (Out of Scope)

- Schema validation of the live `projects.json` file (separate concern, separate test layer)
- Changes to production loader code in `bridge/`
- Changes to other test classes in `test_pm_channels.py`
- Adding a JSON schema for `projects.json`

## Update System

No update system changes required — this is a test-only change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a test-internal change. No MCP servers, bridge code, or tool wrappers are touched.

## Documentation

No documentation changes needed — this is a small test refactor that does not change any user-facing behavior, public API, or documented feature. The fix removes accidental machine-coupling in unit tests; there is nothing to document.

## Success Criteria

- [ ] All four tests in `TestPmConfigValidation` pass on dev-only machines (minimal `projects.json`)
- [ ] All four tests in `TestPmConfigValidation` pass on bridge machines (full `projects.json`)
- [ ] The `config` fixture in `TestPmConfigValidation` does not call `Path.home()`, `Path(__file__)`, `open()`, or `json.load()`
- [ ] `pytest tests/unit/test_pm_channels.py -v` exits 0 on the current machine
- [ ] No other tests in `test_pm_channels.py` are broken

## Team Orchestration

### Team Members

- **Builder (test-fixture)**
  - Name: pm-channels-fixture-builder
  - Role: Replace the live-file `config` fixture with an inline dict
  - Agent Type: builder
  - Resume: true

- **Validator (test-fixture)**
  - Name: pm-channels-fixture-validator
  - Role: Verify all four tests pass and no file I/O occurs in the fixture
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Replace fixture with inline config dict
- **Task ID**: build-fixture
- **Depends On**: none
- **Validates**: tests/unit/test_pm_channels.py::TestPmConfigValidation
- **Assigned To**: pm-channels-fixture-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `tests/unit/test_pm_channels.py` lines 281-291 to replace the `config` fixture body with a hardcoded dict literal that satisfies all four tests in `TestPmConfigValidation`.
- Ensure the dict contains: `personas.project-manager`, `personas.developer`, and `projects.valor.telegram.groups` with at least one PM-persona group AND at least one `Dev:`-prefixed developer group.
- Remove all file I/O, `Path` references, and `pytest.skip()` calls from the fixture.
- Keep all four test method bodies unchanged.

### 2. Validate
- **Task ID**: validate-fixture
- **Depends On**: build-fixture
- **Assigned To**: pm-channels-fixture-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pm_channels.py -v` and confirm all tests pass.
- Grep the modified fixture to confirm no `Path`, `open(`, or `json.load` calls remain in `TestPmConfigValidation`.
- Run `python -m ruff format tests/unit/test_pm_channels.py` to apply formatting.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Target tests pass | `pytest tests/unit/test_pm_channels.py -v` | exit code 0 |
| Full unit suite still passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_pm_channels.py` | exit code 0 |
| No file I/O in fixture | `sed -n '278,335p' tests/unit/test_pm_channels.py \| grep -E 'Path\.home\|json\.load\|open\('` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->
