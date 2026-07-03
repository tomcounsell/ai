# do-test addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-test/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Test Tiers and Markers

Tests are organized by tier with pytest markers. See `tests/README.md` for the full index.

- `tests/unit/` — No external connections; must be fast (~60s). Run with `-n auto` for parallel execution.
- `tests/integration/` — Requires live APIs and services. Do not mock.
- `pytest -m sdlc` — Run SDLC-related tests as a feature slice.

## Test Runner: scripts/pytest-clean.sh (never bare pytest)

This repo runs the suite through `scripts/pytest-clean.sh`, a drop-in pytest wrapper that
reaps xdist workers on exit. Orphaned workers each consume ~180 MB; a full run spawns 8–12
that accumulate if interrupted. Never use bare `pytest` or `pytest -n auto` directly — the
wrapper handles parallelism via `pyproject.toml`.

| Input | Command |
|-------|---------|
| _(empty)_ | `scripts/pytest-clean.sh tests/ -v --tb=short` |
| `unit` / `integration` / `e2e` / `tools` / `performance` | `scripts/pytest-clean.sh tests/{tier}/ -v --tb=short` |
| a file path | `scripts/pytest-clean.sh tests/unit/test_foo.py -v --tb=short` |
| a single test node | add `-n0` (no xdist workers to reap; cleaner output) |

Coverage (`--cov=. --cov-report=term-missing`) only when explicitly requested.

## Redis Isolation

Unit tests must never touch production Redis. Use `REDIS_TEST_DB` or a test-specific key prefix. Bulk Redis operations (`kill --all`, mass deletes) must always be project-scoped using the `PROJECT_NAME` prefix from `config/settings.py`.

Violating this rule corrupts production session data.

## AI Judge Pattern

Integration tests that validate LLM outputs must use an AI judge (Haiku/Sonnet), not keyword matching. See `tests/integration/` for examples. Never assert on exact LLM response content.

## Test Database State

Before running integration tests, verify the bridge and worker are not running tests against the same Redis instance. Use `REDIS_TEST_DB=1` or a separate test-mode `.env`.

## Quality Gates

Tests must pass at these thresholds before a PR can merge:
- Unit: 100% pass
- Integration: 95% pass
- E2E: 90% pass

A failing unit test is a blocker; do not open a PR with failing unit tests.

## Lint / Format Commands (the generic body defers to here)

This repo uses `ruff` for both lint and format. When lint is enabled, run:

```bash
python -m ruff check .
python -m ruff format --check .
```

Do NOT run `black` — `ruff format` is the formatter. There is no separate
formatter step.

## Quality-Scan Source Directories

The post-test quality scans (exception-swallow scan, closure-coverage flag)
target this repo's primary source directories: `agent/ bridge/` (and, for wider
sweeps, `tools/ worker/ monitoring/`). Substitute these for the generic body's
`<source-dirs>` placeholder.

## Happy-Path Runner

The `happy-paths` target runs this repo's deterministic runner directly:

```bash
python tools/happy_path_runner.py tests/happy-paths/scripts/
```

It outputs a markdown summary table plus a JSON summary in an HTML comment block.
When running all tests, if `tests/happy-paths/scripts/` contains `.sh` files,
include happy-paths execution alongside the pytest and frontend targets.

## OUTCOME Parser

The OUTCOME contract this skill emits is parsed by `classify_outcome()` in
`agent/pipeline_state.py` (Tier 0) before any text pattern matching.
