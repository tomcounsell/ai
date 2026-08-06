# Test Concurrency Coordination

Sentinel-ID namespacing: how concurrent test runs avoid Redis contention at
the source, by namespacing keys rather than serializing the machine.

The former full-suite advisory lock (`scripts/suite_lock.py`) is **deleted**. It
judged full-suite-ness from the pytest args, so any run naming a path below
`tests/` skipped it entirely — a full run held a lock no targeted run ever tried
to take, and its silence read as protection (#2535 Problem 1). Namespacing is
the real defense; the lock only serialized the machine while looking safe.

## Sentinel-ID namespacing (F2 defense-in-depth)

The full-suite lock (F1) serializes runs so two concurrent suites cannot race
on the same hardcoded Redis sentinel. Sentinel-ID namespacing is the F2
defense-in-depth: even if two suites *did* run simultaneously (lock disabled,
timeout-proceeded-unlocked, or operator error), their session records would
not collide because the sentinel issue number is randomized per run.

| Test file | Sentinel assignment |
|-----------|----------------------|
| `tests/integration/test_sdlc_sessionless_e2e.py` | `ISSUE_NUMBER = 1_000_000 + random.randint(0, 999)`, with `LOCAL_SESSION_ID = f"sdlc-local-{ISSUE_NUMBER}"`. The `1_000_000` base keeps the number out of any real issue range while the `0..999` suffix makes collisions between concurrent runs astronomically unlikely. |
| `tests/integration/test_stage_comment.py` | Keeps the fixed `TEST_ISSUE_NUMBER = 520` deliberately — the test posts through the real `gh` CLI, and a random non-existent issue number makes `gh` return rc=1. Its concurrency safety comes from the full-suite lock plus `--dist=loadfile`, as documented in the test's inline comment. |

Two concurrent suites will almost certainly draw different
`test_sdlc_sessionless_e2e.py` sentinel IDs, so even without the lock those
session records would not collide.

## See Also

| Resource | Purpose |
|----------|---------|
| [`scripts/pytest-clean.sh`](../../scripts/pytest-clean.sh) | xdist worker reaping; the wrapper every run should use |
| [Test isolation hardening](test-isolation-hardening.md) | Single-run isolation, the companion to this page's cross-run namespacing |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |
