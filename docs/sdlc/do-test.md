# do-test addendum — this repo only
<!-- Do not duplicate content from the global skill (~/.claude/skills/do-test/SKILL.md). Only include what is unique to this repo. Max 300 lines. -->

## TEST Owns the Full-Suite Run (#2376)

The merge gate runs no tests (`docs/sdlc/do-merge.md`), so a PR-introduced
regression in a test file the diff never touched is caught here or not before
merge. Run the full suite (`scripts/pytest-clean.sh tests/ ...`) at least once
before the stage completes; targeted runs alone do not complete TEST. Nothing
downstream can tell a targeted run from a full one (the stage records only
`{passed, failed}`), so this rule is on you; the nightly regression run
(`docs/features/nightly-regression-tests.md`) is the post-merge backstop, not a
substitute.

## Test Runner: scripts/pytest-clean.sh (never bare pytest)

The wrapper reaps xdist workers (~180 MB each, 8–12 per run) and takes its
parallelism and the per-test `--timeout=420` bound from `pyproject.toml`. A full
`tests/unit/` run takes about 20 minutes.

| Input | Command |
|-------|---------|
| _(empty)_ | `scripts/pytest-clean.sh tests/ -v --tb=short` |
| a tier | `scripts/pytest-clean.sh tests/{tier}/ -v --tb=short` |
| a file path | `scripts/pytest-clean.sh tests/unit/test_foo.py -v --tb=short` |
| a single test node | add `-n0` |

Tiers: `tests/unit/` (no external connections), `tests/integration/` (live APIs
and services, never mocked), and `pytest -m sdlc` for the SDLC slice. Index:
`tests/README.md`.

## Baseline Verification Inputs

Invoke the bundled baseline script with the checkout's own venv and no xdist,
copying the gitignored files the tests read:

```bash
.venv/bin/python <skill-dir>/scripts/baseline_verify.py \
  --runner ".venv/bin/python -m pytest -n0" \
  --copy .env --copy ~/Desktop/Valor/projects.json:config/projects.json \
  <failing-node-ids>
```

Record which checkout produced a baseline next to its count; a bare number is not
diffable. A failure count that differs between a worktree and the main checkout is
a finding, not noise. Worktree venvs are pinned to the main checkout's
`MAJOR.MINOR` (#2572; `python -m tools.doctor` reports drift), and tests needing
machine-local gitignored files skip with a stated reason (#2573).

## Stage-Entry Venv Probe (warn-only)

```bash
"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.venv_health || true
```

It names missing extras in the shared venv so a stripped environment reads as a
warning, not a wall of `ModuleNotFoundError`s
(`docs/features/uv-sync-worktree-guard.md`).

## Changed-File Mappings (`--changed`)

Applied before the generic rule:

| Source pattern | Test pattern |
|----------------|--------------|
| `bridge/*.py` | `tests/unit/test_bridge*.py` |
| `tools/*.py` | `tests/tools/test_*.py` |
| `agent/*.py` | `tests/unit/test_agent*.py` |
| `monitoring/*.py` | `tests/unit/test_monitoring*.py` |

## Isolation

Each pytest process claims a private Redis db from `[1..15]` and exports it as
`POPOTO_TEST_DB` and `REDIS_URL` (#2805). A live bridge or worker outside pytest
still needs its own test-mode `.env` to stay off db0. Cross-run isolation is by
sentinel-ID namespacing (`docs/features/test-concurrency-coordination.md`).

## Pass Thresholds

Unit 100%, integration 95%, E2E 90%. A failing unit test is a blocker.

## Lint / Format

`python -m ruff check .` and `python -m ruff format --check .`. Never `black`.

## Happy-Path Runner

`python tools/happy_path_runner.py tests/happy-paths/scripts/` (markdown table
plus a JSON summary in an HTML comment). Include it in all-tests runs when that
directory holds `.sh` files.

## OUTCOME Parser

`classify_outcome()` in `agent/pipeline_state.py`.
