# Test Concurrency Coordination

Sentinel-ID namespacing: how concurrent test runs avoid Redis contention at
the source, by namespacing keys rather than serializing the machine.

## Sentinel-ID namespacing

Sentinel-ID namespacing is defense-in-depth: if two suites run simultaneously,
their session records do not collide because the sentinel issue number is
randomized per run.

| Test file | Sentinel assignment |
|-----------|----------------------|
| `tests/integration/test_sdlc_sessionless_e2e.py` | `ISSUE_NUMBER = 1_000_000 + random.randint(0, 999)`, with `LOCAL_SESSION_ID = f"sdlc-local-{ISSUE_NUMBER}"`. The `1_000_000` base keeps the number out of any real issue range while the `0..999` suffix makes collisions between concurrent runs astronomically unlikely. |
| `tests/integration/test_stage_comment.py` | Keeps the fixed `TEST_ISSUE_NUMBER = 520` deliberately — the test posts through the real `gh` CLI, and a random non-existent issue number makes `gh` return rc=1. |

Two concurrent suites will almost certainly draw different
`test_sdlc_sessionless_e2e.py` sentinel IDs, so even without the lock those
session records would not collide.

## A pool-exhausted run fails closed at the wrapper

`tests/conftest.py` claims a private Redis db per pytest process from a 15-slot
machine-global pool, and `scratch_test_db` calls `pytest.skip()` when that pool
is exhausted. Past roughly five concurrent agents this happens routinely.

A selection whose every test depends on that fixture therefore executes nothing
and pytest exits **0**. `scripts/pytest-clean.sh` now refuses that green: it
exits 1 with a `ZERO TESTS EXECUTED (#3195)` diagnostic naming pool exhaustion
as the likely cause and `scripts/reap-xdist.sh --apply` as the remedy. The skip
itself is unchanged and still correct; what changed is what a reader concludes
from it. See [pytest-clean zero-executed guard](pytest-clean-zero-test-guard.md).

## See Also

| Resource | Purpose |
|----------|---------|
| [`scripts/pytest-clean.sh`](../../scripts/pytest-clean.sh) | xdist worker reaping; the wrapper every run should use |
| [pytest-clean zero-executed guard](pytest-clean-zero-test-guard.md) | Why a pool-exhausted run now fails closed instead of reading green |
| [Test isolation hardening](test-isolation-hardening.md) | Single-run isolation, the companion to this page's cross-run namespacing |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |
