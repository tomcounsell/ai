# Python Test Runner (project override)

This project uses `scripts/pytest-clean.sh` instead of bare `pytest` to automatically
reap xdist workers on exit. Orphaned workers each consume ~180 MB; a full suite run
spawns 8–12 workers that can accumulate if interrupted.

## Test Runner: scripts/pytest-clean.sh

### Argument Mapping

| Input | Command |
|-------|---------|
| _(empty)_ | `scripts/pytest-clean.sh tests/ -v --tb=short` |
| `unit` | `scripts/pytest-clean.sh tests/unit/ -v --tb=short` |
| `integration` | `scripts/pytest-clean.sh tests/integration/ -v --tb=short` |
| `e2e` | `scripts/pytest-clean.sh tests/e2e/ -v --tb=short` |
| `tools` | `scripts/pytest-clean.sh tests/tools/ -v --tb=short` |
| `performance` | `scripts/pytest-clean.sh tests/performance/ -v --tb=short` |
| `tests/unit/test_foo.py` | `scripts/pytest-clean.sh tests/unit/test_foo.py -v --tb=short` |

For single named tests (not a full suite), `-n0` disables xdist parallelism entirely —
no workers to reap, and the output is easier to read:

| Input | Command |
|-------|---------|
| `tests/unit/test_foo.py::TestClass::test_method` | `scripts/pytest-clean.sh tests/unit/test_foo.py::TestClass::test_method -n0 -v --tb=short` |

### Lint Tools

```bash
python -m ruff format . && python -m ruff check .
```

### Changed-File Mapping

Map source files to test files using these conventions:

| Source Pattern | Test Pattern |
|---------------|-------------|
| `src/foo/bar.py` | `tests/*/test_bar.py` |
| `app/models.py` | `tests/*/test_models.py` |
| `lib/*.py` | `tests/*/test_*.py` |
| General rule | `foo/bar.py` → `tests/*/test_bar.py` |
| Test files | Include directly if changed |

Filter to existing files only.

### Test Discovery (all tests)

Check which directories exist and contain `test_*.py` files:
- `tests/unit/`
- `tests/integration/`
- `tests/e2e/`
- `tests/performance/`
- `tests/tools/`
- `tests/` (top-level `test_*.py` files)

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All tests passed |
| 1 | Some tests failed |
| 2 | Test execution error |
| 5 | No tests collected |

### Notes

- Use `-v --tb=short` for verbose names with concise tracebacks
- NEVER use bare `pytest` — always `scripts/pytest-clean.sh`
- NEVER use `pytest -n auto` directly — the clean wrapper already handles parallelism via pyproject.toml
- For coverage: add `--cov=. --cov-report=term-missing` (only if explicitly requested)
