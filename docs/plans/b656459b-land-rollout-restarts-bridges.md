# Land: a rollout restarts every bridge whose code the merge reaches

Task b656459b8ffe carries 4a54eb8bdf09's finished, reviewed work
(`docs/plans/4a54eb8b-rollout-restarts-bridges.md`, branch
`valor/carry-4a54eb8bdf09`, head `eeb800f3a`) onto main. That plan and its
review stand; this file only covers what has changed since.

## What changed on main

- The rebase onto `2d1864305` has one conflict, in `core/README.md`'s
  `serve` paragraph: main rewrote the router-queue sentence. I took main's
  paragraph and added the carry's `recover` clause (restart stale bridges
  before `rollout.ended`). The commits are `d9de5d52a`..`eddf2dc2b`.
- Main's `core/serve.py` changes (`tasks.queue`, idle ordering) touch
  `Serve` stepping, not `recover`. All tests pass except one: 118 passed,
  1 skipped.
- **That failure is the test budget.** Main's tests have grown to 4,995 of
  their 5,000 lines (the 4a54 plan counted 612 lines free). The carry adds
  245, so the suite is at 5,240 and `test_budget.py` fails. The budget is
  Tom's bar (rule 8, `strip.md`), so it stays as it is and the tests are
  made to fit it.

## What will be built

1. **The production code and docs stay as reviewed.** That covers
   `core/rollout.py`, `core/serve.py`, `core/bridge.py`,
   `core/binaries.py`, the docs and the README conflict resolution. They
   change only if a test shows a defect.
2. **Shorten the new tests in `tests/test_rollout.py` to at most 150
   lines.** Every case in the 4a54 plan's Tests section keeps an
   assertion. To get there:
   - the stamp check reuses `tests/bridges.FakeBridge` instead of its own
     `Quiet` class;
   - the kick test's three fake `subprocess.run`s become one fake that
     takes the outcome as an argument;
   - the lock holders are set up in one comprehension over
     `(name, channel)` pairs.
3. **Cut the rest from existing tests without changing what they check.**
   That means repeated setup moved into shared helpers in
   `tests/bridges.py` or `conftest.py`, and repeated cases parametrized.
   No test is deleted and no assertion is dropped or weakened. The suite
   must end at or under 5,000 lines, so it needs at least 95 lines from
   here, or more if step 2 comes in above 150.

## Out of scope

Everything the 4a54 plan puts out of scope, and also:
- raising the budget, which is Tom's bar;
- deleting or skipping any test, including the live Telegram test.

## Tests

- `tests/test_budget.py` passes: at most 15 files, at most 5,000 lines,
  and no more test lines than production lines.
- The six rollout tests still cover every case in the 4a54 plan's list:
  - the import closure, against the scratch layout and this checkout;
  - `reaches`, including quiet paths and dependency files;
  - `stale` for current, behind (both reached and missed), ahead,
    unstamped, unknown-sha and `NO_HEAD` bridges;
  - the kick's argv, a failure's reason and its timeout;
  - the restart against real Postgres: holder against waiter, this
    database against another, ordered before `rollout.ended`, with no
    kick the second time;
  - a failed pass, the no-checkout start and the stamp.
- **A mutation check.** Remove the `current_database()` filter, then the
  `granted` filter, from the restart query. The restart test must fail
  each time, as it did on the carry.
- The whole suite passes in under 5 minutes, and ruff check and format are
  clean.
- The reviewer diffs every refactored existing test against main and
  confirms no assertion was lost.

## Stakes and loops

The code runs at every kernel start that ends a rollout. It already passed
review, so the new risk is only a test refactor quietly dropping coverage.
That calls for no critique round and one review round, which reads the
test diff for lost assertions.
