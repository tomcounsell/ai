---
status: Complete
type: chore
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/480
last_comment_id:
---

# Tools Audit Remediation

## Problem

A full audit of all 20 tools in `tools/` against STANDARD.md (10 checks each, 200 total) found only 58.5% compliance (117/200). Four tools scored 0-1 out of 10. There are duplicate tools, dead wrappers, a failing test, manifest/implementation mismatches, and unregistered CLIs.

**Current behavior:**
- `image_gen` and `image-gen` are divergent duplicates of the same capability
- `search` is a legacy wrapper that delegates entirely to `tools.web`
- `telegram_history::test_get_stats` fails due to Redis state leak
- `web` has 6 files and provider fallback chains but zero docs or tests
- `selfie` has a CLI registration but zero docs or tests
- `google_workspace` is a placeholder (empty `__init__.py`, only `auth.py`)
- `transcribe` manifest claims `insanely-fast-whisper` CLI but code uses OpenAI Whisper API
- Three tools (`sms_reader`, `image_tagging`, `knowledge_search`) have no CLI registration

**Desired outcome:**
All 8 priority issues resolved. Compliance score rises from 58.5% to 80%+.

## Prior Art

- **PR #147**: "Add documentation audit skill and daydream integration" -- established the audit skill pattern but did not audit individual tools against STANDARD.md
- No prior issues found addressing tool compliance or naming standardization

## Architectural Impact

- **Removed dependencies**: Anything importing `tools.search` must switch to `tools.web`. Grep shows: `tools/README.md`, `tools/search/tests/`, `tests/tools/test_search.py`
- **Interface changes**: None for external consumers. `image-gen` docs already point to `tools.image_gen` imports.
- **Coupling**: Decreases -- removing the `search` wrapper and `image-gen` duplicate eliminates indirection
- **Data ownership**: No changes
- **Reversibility**: High -- each item is independent; any can be reverted without affecting others

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on google_workspace decision)
- Review rounds: 1

## Prerequisites

No prerequisites -- all tools already exist and all API keys are configured.

## Solution

### Key Elements

- **Phase 1 (Cleanup)**: Remove dead code -- delete `image-gen` duplicate, delete `search` wrapper, delete or stub `google_workspace`
- **Phase 2 (Fix)**: Fix broken things -- `telegram_history` test isolation, `transcribe` manifest alignment, missing CLI registrations
- **Phase 3 (Add)**: Fill gaps -- add manifest/README/tests for `web` and `selfie`

### Flow

Audit report (issue #480) -> Phase 1 deletions -> Phase 2 fixes -> Phase 3 additions -> re-run audit -> verify 80%+ compliance

### Technical Approach

Work is organized into three phases to minimize risk: deletions first (simplest, highest impact), then fixes, then additions. Each phase is independently shippable.

**Phase 1: Cleanup (3 items)**

1. **Consolidate `image_gen`/`image-gen`**: Move README, manifest, and tests from `image-gen/` into `image_gen/`. Delete `image-gen/` entirely. The `image-gen` docs already reference `from tools.image_gen import ...` so no import changes needed.

2. **Delete `search`**: Remove `tools/search/` entirely. Update `tools/README.md` to reference `tools.web` instead. Remove `tests/tools/test_search.py`. The `valor-search` CLI already points to `tools.web:cli_search`.

3. **Delete `google_workspace` placeholder**: The only real code is `auth.py`, which is imported by `tools/valor_calendar.py`, `scripts/update/cal_integration.py`, and `.claude/skills/setup/SKILL.md`. Move `auth.py` to `tools/valor_calendar_auth.py` (or keep `google_workspace/` as a utility package with just `auth.py` but add a manifest marking it `status: "internal"`). Decision: keep `google_workspace/auth.py` in place but add a manifest with `status: "internal"` and a minimal README explaining it is an auth utility, not a standalone tool. This avoids breaking three import sites.

**Phase 2: Fix (3 items)**

4. **Fix `telegram_history` test**: The `test_get_stats` test asserts `total_messages == 5` but gets 28 due to Redis state leak. Add proper Redis cleanup in test setup/teardown using the `redis_test_db` fixture pattern.

5. **Fix `transcribe` manifest**: The manifest says `insanely-fast-whisper` CLI but the code uses OpenAI Whisper API via `requests`. Update manifest to reflect the actual implementation: `source.type: "external"`, `source.package: "openai"`, `source.command` removed. Update `requires` to list `OPENAI_API_KEY` env var instead of `insanely-fast-whisper` binary.

6. **Register missing CLIs in pyproject.toml**: Add entries for:
   - `valor-sms-reader = "tools.sms_reader.cli:main"` (cli.py already exists)
   - `valor-image-tagging = "tools.image_tagging:main"` (need to verify `main` entrypoint exists)
   - `valor-knowledge-search = "tools.knowledge_search:main"` (need to verify `main` entrypoint exists)

**Phase 3: Add docs/tests (2 items)**

7. **Add docs/tests to `web`**: Create `tools/web/manifest.json`, `tools/web/README.md`, and `tools/web/tests/test_web.py`. The manifest should list capabilities: `search`, `fetch`. Tests should cover the sync wrappers (`web_search_sync`, `fetch_sync`) with real API calls (integration tests, skip if no API key).

8. **Add docs/tests to `selfie`**: Create `tools/selfie/manifest.json`, `tools/selfie/README.md`, and `tools/selfie/tests/test_selfie.py`. Test the CLI entrypoint and core function.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/search/__init__.py` has a broad `except Exception as e` -- this file is being deleted, no coverage needed
- [ ] `tools/transcribe/__init__.py` has multiple exception handlers (Timeout, RequestException, generic Exception) -- these are not being modified, only the manifest is changing
- [ ] New `web` tests should cover provider fallback when primary provider fails

### Empty/Invalid Input Handling
- [ ] New `web` tests should cover empty query string
- [ ] New `selfie` tests should cover missing camera/display (headless environment)

### Error State Rendering
- [ ] Not applicable -- no user-visible output changes in this work

## Test Impact

- [ ] `tests/tools/test_search.py` -- DELETE: tests the `tools.search` wrapper which is being removed
- [ ] `tools/search/tests/test_search.py` -- DELETE: inline tests for the search wrapper being removed
- [ ] `tools/image-gen/tests/` -- DELETE: tests for the duplicate being removed (these test `tools.image_gen` imports anyway, so coverage is preserved by moving them to `tools/image_gen/tests/`)
- [ ] `tests/tools/test_telegram_history.py::test_get_stats` -- UPDATE: add Redis cleanup fixture to fix state leak

## Rabbit Holes

- Renaming `web` to `web_search` or `documentation` to `doc_gen` -- the issue proposes these renames but they are cosmetic and have wide blast radius. Defer to a separate issue.
- Adding tests for `doc_summary` and `documentation` API-key-gated tests -- these are WARN-level, not failures. Out of scope.
- Implementing a full Google Workspace tool suite -- the auth module is useful as-is; building out Sheets/Docs/etc. integration is a separate feature.
- Fixing all WARN-level issues (untested capabilities like `image_analysis.classify`, `test_judge.classify`) -- these are improvement opportunities, not compliance failures.

## Risks

### Risk 1: Deleting `search` breaks external references
**Impact:** Anything importing `from tools.search import search` breaks
**Mitigation:** Grep confirms only `tools/README.md`, `tools/search/` internal files, and `tests/tools/test_search.py` reference it. All are updated or deleted in this plan.

### Risk 2: Moving `image-gen` assets loses test coverage
**Impact:** Tests that existed in `image-gen/tests/` stop running
**Mitigation:** Move tests into `image_gen/tests/` before deleting `image-gen/`

## Race Conditions

No race conditions identified -- all operations are file moves, deletions, and manifest edits. No concurrent access patterns.

## No-Gos (Out of Scope)

- Directory renames (`web` -> `web_search`, `documentation` -> `doc_gen`) -- cosmetic, wide blast radius, separate issue
- Full Google Workspace tool implementation -- auth utility stays, tool suite is a separate feature
- Fixing WARN-level audit findings (untested capabilities, missing Python API docs)
- Adding tests for API-key-gated tools where tests are skipped without keys (`doc_summary`, `documentation`)

## Update System

No update system changes required -- this work only reorganizes and documents existing tools. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- tools are exposed to the agent via MCP servers in `mcp_servers/`, not via direct `tools/` imports. No MCP server changes needed. The `google_workspace/auth.py` is consumed by `valor_calendar.py` which already has its own CLI registration.

## Documentation

- [ ] Create `tools/web/README.md` describing the web search and fetch tool
- [ ] Create `tools/selfie/README.md` describing the selfie capture tool
- [ ] Create `tools/google_workspace/README.md` explaining this is an auth utility, not a standalone tool
- [ ] Update `tools/README.md` to remove `search` references, update tool inventory
- [ ] Update `docs/features/README.md` if tools are listed there

## Success Criteria

- [ ] `image-gen/` directory no longer exists; `image_gen/` has manifest, README, and tests
- [ ] `search/` directory no longer exists; no imports of `tools.search` remain
- [ ] `telegram_history` test passes consistently (no Redis state leak)
- [ ] `web/` has manifest.json, README.md, and tests/
- [ ] `selfie/` has manifest.json, README.md, and tests/
- [ ] `google_workspace/` has manifest.json (status: internal) and README.md
- [ ] `transcribe/manifest.json` reflects OpenAI Whisper API, not insanely-fast-whisper CLI
- [ ] `pyproject.toml` has CLI entries for `sms_reader`, `image_tagging`, `knowledge_search`
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)
- [ ] Re-audit score >= 80% (160/200)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Phase 1 -- delete duplicates and dead wrappers
  - Agent Type: builder
  - Resume: true

- **Builder (fixes)**
  - Name: fix-builder
  - Role: Phase 2 -- fix test, manifest, CLI registrations
  - Agent Type: builder
  - Resume: true

- **Builder (docs-tests)**
  - Name: docs-tests-builder
  - Role: Phase 3 -- add manifests, READMEs, and tests for web and selfie
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Run full audit and verify compliance score
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Consolidate image_gen and delete image-gen
- **Task ID**: build-consolidate-image-gen
- **Depends On**: none
- **Validates**: `python -c "from tools.image_gen import generate_image; print('OK')"`, `ls tools/image_gen/manifest.json tools/image_gen/README.md tools/image_gen/tests/`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Copy `tools/image-gen/manifest.json` to `tools/image_gen/manifest.json`, update `name` field to `image_gen`
- Copy `tools/image-gen/README.md` to `tools/image_gen/README.md`
- Copy `tools/image-gen/tests/` to `tools/image_gen/tests/`
- Delete `tools/image-gen/` entirely
- Verify imports still work

### 2. Delete search wrapper
- **Task ID**: build-delete-search
- **Depends On**: none
- **Validates**: `test ! -d tools/search`, no `from tools.search` imports remain
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `tools/search/` directory entirely
- Delete `tests/tools/test_search.py`
- Update `tools/README.md` to reference `tools.web` instead of `tools.search`

### 3. Add google_workspace manifest and README
- **Task ID**: build-google-workspace-docs
- **Depends On**: none
- **Validates**: `ls tools/google_workspace/manifest.json tools/google_workspace/README.md`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/google_workspace/manifest.json` with `status: "internal"`, type `library`, capabilities `["auth"]`
- Create `tools/google_workspace/README.md` explaining this is an OAuth auth utility used by valor_calendar

### 4. Fix telegram_history test
- **Task ID**: build-fix-telegram-test
- **Depends On**: none
- **Validates**: `pytest tests/tools/test_telegram_history.py -v`
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Redis cleanup in test setup/teardown for `test_get_stats`
- Ensure test isolation so message count assertions are deterministic

### 5. Fix transcribe manifest
- **Task ID**: build-fix-transcribe-manifest
- **Depends On**: none
- **Validates**: `python -c "import json; m=json.load(open('tools/transcribe/manifest.json')); assert 'openai' in m['source']['package'].lower()"`
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `tools/transcribe/manifest.json`: source.type="external", source.package="openai", remove source.command
- Update requires: remove `binaries: ["insanely-fast-whisper"]`, add `env: ["OPENAI_API_KEY"]`
- Update commands.verify to test the Python import, not the CLI binary

### 6. Register missing CLIs in pyproject.toml
- **Task ID**: build-register-clis
- **Depends On**: none
- **Validates**: `grep 'valor-sms-reader\|valor-image-tagging\|valor-knowledge-search' pyproject.toml`
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Verify `main` entrypoints exist in `sms_reader`, `image_tagging`, `knowledge_search` (create if missing)
- Add CLI entries to `pyproject.toml` `[project.scripts]` section

### 7. Add docs and tests for web tool
- **Task ID**: build-web-docs-tests
- **Depends On**: none
- **Validates**: `ls tools/web/manifest.json tools/web/README.md tools/web/tests/test_web.py`
- **Assigned To**: docs-tests-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/web/manifest.json` with capabilities: search, fetch; providers listed
- Create `tools/web/README.md` documenting web_search_sync, fetch_sync, provider fallback
- Create `tools/web/tests/__init__.py` and `tools/web/tests/test_web.py` with integration tests (skip if no API key)

### 8. Add docs and tests for selfie tool
- **Task ID**: build-selfie-docs-tests
- **Depends On**: none
- **Validates**: `ls tools/selfie/manifest.json tools/selfie/README.md tools/selfie/tests/test_selfie.py`
- **Assigned To**: docs-tests-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/selfie/manifest.json` with capabilities: capture
- Create `tools/selfie/README.md` documenting the selfie capture tool
- Create `tools/selfie/tests/__init__.py` and `tools/selfie/tests/test_selfie.py`

### 9. Update tools README
- **Task ID**: build-update-readme
- **Depends On**: build-consolidate-image-gen, build-delete-search, build-google-workspace-docs
- **Validates**: `grep -v 'tools.search' tools/README.md`
- **Assigned To**: docs-tests-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Remove references to `tools.search`
- Update tool inventory table if one exists
- Add note about naming conventions (snake_case directories)

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-consolidate-image-gen, build-delete-search, build-google-workspace-docs, build-fix-telegram-test, build-fix-transcribe-manifest, build-register-clis, build-web-docs-tests, build-selfie-docs-tests, build-update-readme
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` to verify no regressions
- Run `python -m ruff check .` for lint
- Verify `image-gen/` and `search/` directories are gone
- Verify all new manifests and READMEs exist
- Spot-check compliance on the 8 priority items

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| image-gen deleted | `test ! -d tools/image-gen` | exit code 0 |
| search deleted | `test ! -d tools/search` | exit code 0 |
| image_gen has manifest | `test -f tools/image_gen/manifest.json` | exit code 0 |
| web has manifest | `test -f tools/web/manifest.json` | exit code 0 |
| selfie has manifest | `test -f tools/selfie/manifest.json` | exit code 0 |
| transcribe manifest correct | `python -c "import json; m=json.load(open('tools/transcribe/manifest.json')); assert 'openai' in m['source']['package'].lower()"` | exit code 0 |
| CLIs registered | `grep -c 'valor-sms-reader\|valor-image-tagging\|valor-knowledge-search' pyproject.toml` | output > 2 |
| No search imports | `grep -r 'from tools.search' --include='*.py' tools/ tests/ \| grep -v __pycache__` | exit code 1 |
| telegram_history test | `pytest tests/tools/test_telegram_history.py -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **google_workspace disposition**: The plan keeps `google_workspace/` as an internal auth utility with `status: "internal"` manifest. Alternative is to move `auth.py` into `tools/valor_calendar.py` directly and delete the directory. Which approach do you prefer?
