# do-test addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-test/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## Test Tiers and Markers

Tests are organized by tier with pytest markers. See `tests/README.md` for the full index.

- `tests/unit/` — No external connections; must be fast (~60s). Run with `-n auto` for parallel execution.
- `tests/integration/` — Requires live APIs and services. Do not mock.
- `pytest -m sdlc` — Run SDLC-related tests as a feature slice.

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
