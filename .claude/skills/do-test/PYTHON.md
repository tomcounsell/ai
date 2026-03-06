# Python Test Runner

Loaded when the project uses Python (pytest, pyproject.toml, setup.py, etc.).

## Test Runner: pytest

### Argument Mapping

| Input | Command |
|-------|---------|
| _(empty)_ | `pytest tests/ -v --tb=short` |
| `unit` | `pytest tests/unit/ -v --tb=short` |
| `integration` | `pytest tests/integration/ -v --tb=short` |
| `e2e` | `pytest tests/e2e/ -v --tb=short` |
| `tools` | `pytest tests/tools/ -v --tb=short` |
| `performance` | `pytest tests/performance/ -v --tb=short` |
| `tests/unit/test_foo.py` | `pytest tests/unit/test_foo.py -v --tb=short` |

### Lint Tools

```bash
python -m ruff check .
black --check .
```

Optional (if configured in project):
```bash
mypy . --ignore-missing-imports
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
- For coverage: `pytest --cov=src --cov-report=term-missing` (only if explicitly requested)
- For parallel execution: `pytest -n auto` (requires pytest-xdist)
