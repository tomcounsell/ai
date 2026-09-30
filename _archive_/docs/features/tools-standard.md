# Tools Standard and Audit Compliance

Feature documentation for the tools compliance standard.

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

## Tool Inventory

Current tools (18):

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
