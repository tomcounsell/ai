# Tool Audit Checks

A tool is a directory under `tools/`. Skip `_template/`, `__pycache__/`, and standalone `.py`
files in the root, which are utilities. A directory holding only an empty `__init__.py` is
`FAIL: placeholder — no implementation`; skip its remaining checks.

| # | Check | Severity | Passes when |
|---|---|---|---|
| 1 | `manifest-exists` | FAIL | `manifest.json` parses and has `name`, `version`, `description`, `type`, `status`, `capabilities`. An empty `capabilities` list is a WARN |
| 2 | `readme-exists` | FAIL | `README.md` has an overview plus sections for install/requirements/setup, quick start/usage, and API/command reference (headings case-insensitive) |
| 3 | `tests-exist` | FAIL | `tests/` holds at least one `test_*.py` |
| 4 | `inputs-documented` | WARN | Public functions in `__init__.py` (and `cli.py`) have typed parameters, and each appears in the README as a `` ### `function_name` `` heading with its parameters |
| 5 | `output-types` | WARN | Public functions have return annotations, and the README or docstrings describe the returned shape (dict keys, list contents) |
| 6 | `examples` | WARN | The README has at least 2 realistic ```` ```python ```` examples: imports, real-looking arguments, use of the result |
| 7 | `error-docs` | INFO | The README or docstrings say how failure surfaces: an `{"error": ...}` dict, an exception, or `None` |
| 8 | `test-coverage` | FAIL | Every manifest capability matches at least one `def test_` name. Split the capability on `_` and look for any of its terms |
| 9 | `tests-passing` | FAIL | The tool's tests pass under the repo's test runner. Tests that need unavailable external services (marked `integration`/`slow`) are a WARN, not a FAIL |
| 10 | `cli-quality` | WARN | A registered CLI's `--help` exceeds 5 lines and covers purpose, every argument and option, and at least one example. A `cli` or common-use `library` tool with no entry point is a WARN |
