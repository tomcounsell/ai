# Tools Standard and Audit Compliance

Feature documentation for the tools compliance standard and the remediation work that brought the tools directory to compliance.

## Overview

The `tools/` directory contains capabilities that extend Valor's functionality. Each tool follows a consistent structure defined in [tools/STANDARD.md](/tools/STANDARD.md) that enables discoverability, validation, and documentation. An audit system validates all tools against 10 standardized checks.

## The Standard

Every tool in `tools/` must have:

| Requirement | File | Purpose |
|-------------|------|---------|
| Manifest | `manifest.json` | Machine-readable specification: name, version, type, status, capabilities, dependencies |
| Documentation | `README.md` | Human-readable usage guide with overview, installation, quick start, workflows |
| Tests | `tests/test_<name>.py` | Real integration tests (no mocks) covering core workflows and error handling |
| Python API | `__init__.py` | Importable functions with type hints and docstrings |
| CLI registration | `pyproject.toml` entry | `valor-<name>` CLI command pointing to the tool's entrypoint |

### Naming Conventions

- **Directory names**: `snake_case` (the only convention that works as both a Python package and filesystem path)
- **Manifest `name` field**: matches directory name exactly (e.g., `image_gen`, not `image-gen`)
- **CLI names**: `valor-{name}` with hyphens in `pyproject.toml` (e.g., `valor-image-gen`)
- **Test files**: `tests/test_{dir_name}.py`

### Audit Checks (10 per tool)

The audit validates each tool against these checks:

1. `manifest.json` exists and is valid JSON
2. `manifest.json` has all required fields (name, version, description, type, status, capabilities)
3. `manifest.json` `name` field matches directory name
4. `README.md` exists with required sections
5. `tests/` directory exists with test files
6. Tests pass when run
7. Python API is importable (`from tools.<name> import ...`)
8. CLI is registered in `pyproject.toml` (or tool is marked `status: internal`)
9. Dependencies declared in `requires` are accurate
10. Capabilities listed in manifest are tested

## Audit Results

### Baseline (2026-03-23)

Initial audit of all 20 tools: **117/200 checks passing (58.5%)**

Critical findings:
- `image_gen` and `image-gen` were divergent duplicates of the same capability
- `search` was a legacy wrapper delegating entirely to `tools.web`
- `google_workspace` was a placeholder (empty `__init__.py`, only `auth.py`)
- `web` and `selfie` had real implementations but zero docs or tests
- `transcribe` manifest claimed `insanely-fast-whisper` CLI but code used OpenAI Whisper API
- Three tools (`sms_reader`, `image_tagging`, `knowledge_search`) had no CLI registration
- `telegram_history` test failed due to Redis state leak

### Remediation (PR #504, issue #480)

Work was organized in three phases:

**Phase 1 -- Cleanup:**
- Consolidated `image-gen/` into `image_gen/` (moved README, manifest, tests; deleted duplicate)
- Deleted `search/` legacy wrapper entirely
- Added manifest (`status: internal`) and README to `google_workspace/` auth utility

**Phase 2 -- Fixes:**
- Fixed `transcribe` manifest to reflect OpenAI Whisper API (not `insanely-fast-whisper` CLI)
- Registered 3 missing CLIs in `pyproject.toml`: `valor-sms-reader`, `valor-image-tagging`, `valor-knowledge-search`
- Confirmed `telegram_history` test isolation via autouse fixture

**Phase 3 -- Additions:**
- Added manifest, README, and tests for `web` tool
- Added manifest, README, and tests for `selfie` tool
- Fixed all 10 tool manifest `name` fields to use `snake_case` matching directory names

**Additional:**
- Updated `tools/README.md` index with accurate categorization and API key requirements

### Post-Remediation

After remediation, the 8 priority issues from the audit are resolved. The remaining WARN-level items (untested capabilities like `image_analysis.classify`, API-key-gated test skips) are tracked separately and do not affect compliance.

## Tool Inventory

Current tools after remediation (18 tools, down from 20 after deleting duplicates):

| Tool | Type | Status | Capabilities |
|------|------|--------|-------------|
| `browser` | cli | stable | navigate, interact, screenshot, scrape |
| `code_execution` | library | stable | execute |
| `doc_summary` | api | stable | summarize |
| `documentation` | api | stable | generate |
| `google_workspace` | library | internal | auth |
| `image_analysis` | api | stable | analyze, classify |
| `image_gen` | api | stable | generate |
| `image_tagging` | api | stable | classify |
| `knowledge_search` | api | stable | search, embed |
| `link_analysis` | api | stable | analyze |
| `selfie` | api | stable | generate |
| `sms_reader` | library | stable | read |
| `telegram_history` | library | stable | search |
| `test_judge` | api | stable | judge, classify |
| `test_params` | library | stable | generate |
| `test_scheduler` | library | stable | schedule, cancel |
| `transcribe` | api | stable | transcribe |
| `web` | api | stable | search, fetch |

## Related Resources

- [tools/STANDARD.md](/tools/STANDARD.md) -- canonical standard definition
- [tools/README.md](/tools/README.md) -- quick reference and usage examples
- [Issue #480](https://github.com/tomcounsell/ai/issues/480) -- original audit report
- [PR #504](https://github.com/tomcounsell/ai/pull/504) -- remediation implementation
