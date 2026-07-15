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

## Full-Suite Coordination Lock

Full-suite runs (`tests/` or no positional args) acquire an advisory coordination lock at `data/full-suite-running.lock` before invoking pytest, and release it via an exit trap. This serializes concurrent full-suite invocations so they don't oversubscribe CPU (load avg 79-82 on 10-core machines when two run at once). Targeted runs (a specific file or subdirectory) skip the lock and are never blocked by a concurrent full suite. The default wait timeout is 30 minutes; on timeout the run proceeds unlocked with a warning rather than deadlocking. See [Full-suite pytest advisory lock](../features/full-suite-pytest-lock.md) for the full design and [Test Concurrency Coordination](../features/test-concurrency-coordination.md) for the `refresh_test_baseline.py` integration and sentinel-ID namespacing.

## Changed-File Source-to-Test Mappings (`--changed`)

Repo-specific mappings, applied before the generic `foo/bar.py -> tests/*/test_bar.py` rule:

| Source pattern | Test pattern |
|----------------|--------------|
| `bridge/*.py` | `tests/unit/test_bridge*.py` |
| `tools/*.py` | `tests/tools/test_*.py` |
| `agent/*.py` | `tests/unit/test_agent*.py` |
| `monitoring/*.py` | `tests/unit/test_monitoring*.py` |

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

## Shared-.venv Health Probe (Warn-Only, Stage Entry)

Issue #2050: worktrees share the repo-root `.venv` (no per-worktree isolation
yet). Before running the suite, probe the shared venv so a stripped
environment (e.g. from a `uv sync` that slipped past the PreToolUse guard) is
a loud warning here instead of a confusing wall of `ModuleNotFoundError`s:

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.venv_health || true
```

This is warn-only (`|| true`) — a missing extra does not block the test run
by itself; it just names what's missing so the failure that follows is
diagnosable instead of mysterious. See
`docs/features/uv-sync-worktree-guard.md`.

## OUTCOME Parser

The OUTCOME contract this skill emits is parsed by `classify_outcome()` in
`agent/pipeline_state.py` (Tier 0) before any text pattern matching.

## Router-Test Fixtures: Seed a Recorded Verdict for Merge-Termination Asserts

When a `tests/unit/test_sdlc_router*.py` fixture asserts the happy-path terminal
dispatches `/do-merge`, it MUST seed a recorded `APPROVED` review verdict (via
`meta["latest_review_verdict"]` or `_verdicts["REVIEW"]`) alongside the
all-`completed` stage states. A `REVIEW == completed` marker is unwritable
without a readable verdict (#2062 WS3c invariant), so an all-completed state with
no verdict is not the terminal state — Row 8e (no-verdict recovery) correctly
re-dispatches `/do-pr-review`, and Row 10 (ready-to-merge) requires the recorded
verdict (#2062 WS3a). A fixture that omits the verdict but asserts `/do-merge` is
stale, not a router bug (see #2091).
