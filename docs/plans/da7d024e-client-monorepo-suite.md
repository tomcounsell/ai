# A client monorepo's spec runs its real tests

**Ask.** Task 81ad1d4fd950 (2026-11-21) found that a client monorepo's spec
(served by the Captain) has `suite = "true"`, though the repo now has over
1,000 tests, so every test stage on it passes having run nothing. Set the
suite to the repo's own test command, run natively with no Docker (Tom,
2026-11-21), and report the command, its duration and the result.

**Where the change lives.** The spec is in the private folder
(`~/Desktop/Valor/projects/`), not in this public repository. The only
commit here is this plan. No kernel code changes.

## What the repo runs (observed, 2026-11-21)

The repo's README and CI workflows name four parts, each with its own
lockfile. Run natively on the Captain at the repo's main (9e8d839), fresh
clone, a throwaway Postgres 18 cluster set up as the kernel sets one up
(role `app` with CREATEDB, password file, `PGHOST=127.0.0.1`, `PGPORT`,
`PGPASSFILE`, `TEST_PGHOST=127.0.0.1`):

| Part | Command (README / CI) | Result | Time |
|---|---|---|---|
| staff API | `uv run pytest -q` | 812 passed | 125 s |
| backend | `uv run pytest -q` | 59 passed | 8 s |
| admin app | `pnpm test` (vitest; lint and typecheck also passed, 9 s) | 238 passed, 3 skipped | 10 s |
| admin app e2e | `pnpm e2e` (Playwright, Chromium, whole local stack) | 29 passed | 82 s |
| prompt-sheet tool | `uv run pytest` | 12 failed, 56 passed | 7 s |

About 4 minutes of tests per run, setup excluded. The kernel runs base and
head, so about 9 minutes per test stage. That is short enough to run the full
suite. No fast subset is needed.

## What will be built

In the spec:

1. `services = ["postgres"]`, and `env` gains `TEST_PGHOST = "127.0.0.1"`
   (the tests read `TEST_PGHOST`, default `localhost`; libpq reads the rest
   from the kernel's `PG*` variables).
2. `setup` keeps the submodule line and adds `uv sync --locked` in the staff
   API and backend, `pnpm install --frozen-lockfile` and
   `pnpm exec playwright install chromium` in the admin app.
3. `suite` runs the staff API pytest, the backend pytest, the admin vitest
   and the admin e2e. Every part runs even when an earlier one fails. Each
   part writes its own JUnit file under the checkout's `tmp/` (pytest
   `--junitxml`, vitest `--reporter=junit`, Playwright `--reporter=list,junit`
   with `PLAYWRIGHT_JUNIT_OUTPUT_NAME`), separate from `{junit}`, which sits
   outside the checkout. An inline `python3 -I` step merges them into
   `{junit}`, and writes it even when a part wrote no report, so a crashed
   part shows as missing ids, not as a run with no per-test results. Each
   classname is prefixed with its part's directory in the form the kernel
   maps back to a file (`core/checks.py` `located()`): dotted for pytest
   (`apps.staff-api.` + `tests.test_x` resolves to
   `apps/staff-api/tests/test_x.py`), a path for the admin app's `.ts` files
   (`apps/admin/`, and `apps/admin/e2e/` for Playwright, whose names are
   relative to `e2e/`). Ids stay distinct across parts, and a deleted test or a
   dropped parametrize case is still told apart from a vanished one. The
   suite exits non-zero when any part did.
   The suite unsets `DATABASE_URL`, as CI has none: the kernel's
   driverless URL reaches the staff API's settings, which then pick
   psycopg, not installed there (one test failed on it in the first run).
4. The comment "No code of its own yet" is replaced by one naming the parts
   run and what is left out.

## Out of scope

- **CI's lint** (`black --check`, `pnpm lint`, `pnpm typecheck`). The ask
  sets the suite; a spec `lint` runs at head and fails the stage on its own
  findings, an added check with no grant. Named in the report.
- **prompt-sheet's tests.** CI doesn't run them. Natively they fail 12 of
  68: ten build a database URL without the port and reach `127.0.0.1:5432`,
  where the kernel's cluster isn't; two fail on a message-format assertion.
  Fixing them is a change to the client repo, which this task may not make.
  It is named in the report.
- **Submodules' suites.** The read-only submodules are tested under their
  own projects (one has its own spec).
- **Docker.** Not used, not installed, not worked around.

## Risk named, not guarded

The e2e stack binds fixed ports (8100, 8101, 8109) with
`reuseExistingServer: false`. If two of this project's test stages overlap on
the Captain, the second one fails loudly with "port in use"; it never tests
the other run's server. Nothing is added for this until it happens.

## Tests (verification of the change)

The change is config, so its proof is runs:

- Run the new `setup` and `suite` strings verbatim, under the kernel's turn
  sandbox profile (`sandbox-exec -f` the task's `turn.sb`), on the Captain
  against a kernel-style cluster at the repo's main. Expect 812 + 59 + 238 +
  29 passing tests in the merged JUnit, and exit 0. Record the wall time.
- Break one test in a scratch copy (e.g. an assertion in a backend test) and
  confirm the suite exits non-zero, the merged JUnit shows that one id
  failed, and the later parts still ran. Then call `removed_definitions`
  (its `located()` mapping) on the merged ids against a scratch mirror
  where the diff deletes one staff-API test function: that id must map to
  `apps/staff-api/tests/<file>.py` and count as removed, and an untouched
  id must not.
- Make one part crash before writing a report (a bad path): `{junit}` is
  still written, holding the other parts' ids.
- Load the edited spec with `core.workspace`'s spec loader to confirm it
  parses (TOML and the required keys).

## Stakes and rounds

Stakes: a private config file, reversible by one edit, and the worst case is
a test stage that fails or runs long for one client project. Critique 0,
review 0.
