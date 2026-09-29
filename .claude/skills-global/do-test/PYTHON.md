# Python (pytest)

- Command: `pytest <target> -v --tb=short` unless the context file names a
  wrapper. Coverage (`--cov=<pkg> --cov-report=term-missing`) only on request.
- Lint default when the repo declares none: `python -m ruff check .` and
  `python -m ruff format --check .`; run the project's configured tools
  (`black`, `flake8`, `mypy`) instead when it has them.
- `--changed` mapping: `foo/bar.py` → `tests/*/test_bar.py`, after any
  repo-specific mappings in the context file.
- Exit codes: 0 all passed, 1 some failed, 2 execution error, 5 nothing
  collected (treat 5 as a failure to run, not a pass).
