---
status: In Progress
type: bug
appetite: Small
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/697
last_comment_id:
---

# Fix Happy Path Testing Pipeline: Broken Rodney Install, Missing Integration Validation

## Problem

PR #687 merged the happy path testing pipeline (three-stage: discover, generate, run) but the pipeline is non-functional end-to-end. The Rodney install script points to a 404 GitHub repo (`nicois/rodney` instead of `simonw/rodney`), no example traces or generated scripts exist in the repo, and all 67 unit tests mock subprocess so nothing validates actual execution. PR #687 merged without a proper review pass.

**Current behavior:**
- `which rodney` returns nothing on all machines
- `scripts/update/rodney.py` line 22 has `GITHUB_REPO = "nicois/rodney"` which returns HTTP 404
- `tests/happy-paths/traces/` and `tests/happy-paths/scripts/` contain only `.gitkeep`
- The runner error message on line 251 of `tools/happy_path_runner.py` also references the wrong repo URL
- `docs/features/happy-path-testing-pipeline.md` and `docs/plans/happy-path-testing-pipeline.md` reference the wrong repo URL

**Desired outcome:**
- Rodney installs successfully from `simonw/rodney` (v0.4.0 has prebuilt binaries for all 4 platforms)
- At least one example trace and generated script exist as reference artifacts
- An integration test validates the trace-to-script generation pipeline
- All references to `nicois/rodney` are corrected to `simonw/rodney`

## Prior Art

- **Issue #686**: Happy path testing pipeline plan -- closed as shipped when PR #687 merged
- **PR #687**: Implementation PR -- merged with 0 reviews, critique blockers not fully verified

## Solution

This is a surgical fix, not a redesign. The three-stage architecture is sound.

### Fix 1: Correct Rodney GitHub Repo URL

Change `GITHUB_REPO` in `scripts/update/rodney.py` from `nicois/rodney` to `simonw/rodney`. Also fix the wrong URL in:
- `tools/happy_path_runner.py` line 251 (error message)
- `docs/features/happy-path-testing-pipeline.md` line 103
- `docs/plans/happy-path-testing-pipeline.md` lines 41 and 152

### Fix 2: Install Rodney and Verify

Run the fixed install script and confirm `rodney --version` returns a version string. This validates the download URL, tarball extraction, and binary placement all work.

### Fix 3: Add Example Trace JSON

Create a minimal example trace at `tests/happy-paths/traces/example-homepage.json` that navigates to a simple public page (e.g., `https://example.com`), checks the title, and asserts visible text. This trace serves as:
- A reference for the trace JSON schema
- Input for the integration test
- Documentation by example

The trace must not contain any credentials or site-specific secrets.

### Fix 4: Generate Example Script

Run the generator against the example trace to produce `tests/happy-paths/scripts/example-homepage.sh`. Commit the generated script as a reference artifact. This validates the generator works end-to-end with real input.

### Fix 5: Add Integration Test

Create `tests/integration/test_happy_path_integration.py` with:
- A test that loads the example trace JSON, runs it through `parse_trace()` and `generate_script()`, and validates the output script contains expected Rodney commands
- A conditional test (skipped if Rodney is not installed) that actually executes the generated script against a simple page
- Both tests use real files, not mocks

### Fix 6: Review Pass on PR #687 Code

Review the 17 files from PR #687 for:
- Dead code or unused imports
- Hardcoded paths or credentials
- Error handling gaps
- Inconsistencies between docs and implementation

Any issues found become patch items during the BUILD stage.

## Prerequisites

None. The `simonw/rodney` repo is public and has prebuilt binaries. No new pip dependencies required.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `install_or_update()` already wraps all failures in `InstallResult(success=False)` -- verify this works with the corrected URL when network is unavailable
- [ ] Generator handles malformed trace JSON gracefully (already covered by unit tests)

### Empty/Invalid Input Handling
- [ ] Integration test: empty trace steps array produces no script (generator returns False)
- [ ] Integration test: trace with only navigate steps produces a valid script

## Test Impact

- [ ] `tests/unit/test_happy_path_runner.py::test_check_rodney_installed` -- UPDATE: may need to handle the case where Rodney is actually installed now
- [ ] `tests/unit/test_happy_path_generator.py` -- no changes needed, mocked tests remain valid for unit coverage
- [ ] `tests/unit/test_happy_path_schema.py` -- no changes needed, schema is unchanged

No unit tests reference the `nicois/rodney` URL string directly, so the URL fix does not break existing tests.

## Rabbit Holes

- Running `/do-discover-paths` against a real site to produce the example trace -- overkill for a fix issue; hand-craft a minimal trace instead
- Adding Chrome/Chromium dependency validation -- out of scope; Rodney handles its own browser dependency
- Rewriting the existing 67 unit tests to remove mocks -- the mocks are appropriate for unit-level testing; the new integration test fills the gap

## Risks

### Risk 1: simonw/rodney binary format differs from expected
**Mitigation:** The code already expects a tarball with a `rodney` binary inside. Verify the actual archive structure from the v0.4.0 release matches before committing.

### Risk 2: Generated scripts committed to repo may contain site-specific paths
**Mitigation:** The example trace uses only `https://example.com` which is a public IANA domain. No credentials involved.

## No-Gos

- Not redesigning the three-stage pipeline architecture
- Not adding new pip dependencies
- Not changing the trace JSON schema
- Not adding Go toolchain installation (prebuilt binaries are the correct approach)
- Not building a full CI integration for happy path tests (future work)

## Update System

The update system is directly affected. `scripts/update/rodney.py` is the file with the broken URL. After fixing `GITHUB_REPO` to `simonw/rodney`:
- The next `/update` run on any machine will download and install the correct Rodney binary
- No changes to `scripts/remote-update.sh` or the update skill are needed -- `rodney.py` is already called by the update pipeline
- No new config files or migration steps required

## Agent Integration

No agent integration required. The happy path tools (`happy_path_generator.py`, `happy_path_runner.py`) are CLI tools invoked by the `/do-test` and `/do-discover-paths` skills. No MCP server changes needed. The bridge does not call these tools directly.

## Documentation

- [ ] Update `docs/features/happy-path-testing-pipeline.md` to fix the Rodney URL from `nicois/rodney` to `simonw/rodney`
- [ ] Update `docs/plans/happy-path-testing-pipeline.md` to fix the Rodney URL references
- [ ] No new documentation files needed -- the existing feature doc is comprehensive

## Success Criteria

- [ ] `scripts/update/rodney.py` has `GITHUB_REPO = "simonw/rodney"` and Rodney installs successfully
- [ ] `rodney --version` returns a version string
- [ ] `tests/happy-paths/traces/example-homepage.json` exists and validates against the schema
- [ ] `tests/happy-paths/scripts/example-homepage.sh` exists and is syntactically valid
- [ ] `tests/integration/test_happy_path_integration.py` passes (generation test always, execution test when Rodney is available)
- [ ] All references to `nicois/rodney` across the codebase are corrected to `simonw/rodney`
- [ ] No credentials hardcoded in any committed files
