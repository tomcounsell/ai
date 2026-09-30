# M0 build log, <date>

Lead: <model>. Opened at commit `<sha>` on `main`. Baseline before phase 0: `<n> passed` in `uv run pytest -q`, `alembic current` = `0001`.

The durable state of the build. On resume, the lead reads this file and `git worktree list` before doing anything else.

## Phase 0

| Check | Result |
|---|---|
| Working tree clean on `main` | |
| `uv sync` | |
| Full suite green | |
| `alembic current` on `cori` | |
| Postgres 18 and Redis under `brew services` | |
| `container system status`, `cori-base:3.14`, `cori-hostonly` | |
| Keychain items under service `cori` (six) | |
| `gh auth status` | |
| GitHub fine-grained token expiry (prereqs item 13) | |
| Planning worktrees removed, branches deleted | |
| Build log created, index Building section current | |

## Waves

| Wave | Component | Status | Database | Builder | Rounds | Validator | Reviewer | Merge commit | Suite on main |
|---|---|---|---|---|---|---|---|---|---|
| 1 | events | | cori_events | | | | | | |
| 1 | sandbox | | cori_sandbox | | | | | | |
| 2 | tree | | cori_tree | | | | | | |
| 3 | spaces | | cori_spaces | | | | | | |
| 4 | gateway | | cori_gateway | | | | | | |
| 4 | memory | | cori_memory | | | | | | |
| 5 | worker | | cori_worker | | | | | | |
| 6 | broker | | cori_broker | | | | | | |
| 7 | surface | | cori_surface | | | | | | |
| 8 | verifier | | cori_verifier | | | | | | |
| 9 | supervisor | | cori_supervisor | | | | | | |
| 10 | integration | | cori | | | | | | |

Status is one of `reconciled`, `building`, `returned`, `built`, `blocked`. Rounds counts validator or reviewer returns.

## Findings carried

One row per finding a builder, validator, or reviewer raised. Disposition is one of: `seams v4` (applied below), `design edit` (made in one commit after the build), `noted` (no change), `M1` (carried to the next planning stage).

| # | Raised by | Component | Finding | Disposition |
|---|---|---|---|---|

## Seams changes during the build

Each entry is also written into `docs/plans/00-seams.md` under "Version 4, build" and named in the brief of every later builder it touches.

| # | Section | Change | Raised by | Touches |
|---|---|---|---|---|

## Live actions and spend

| Wave | Action | Target | Cost |
|---|---|---|---|

## Measurements

Filled from the integration wave: the cache measurement (input tokens, cache reads, read fraction, breakpoint positions), the chaos test's run count, the two objectives' estimate against actual.

## Blocked and open

Anything that stopped a wave, with what the person decided.
