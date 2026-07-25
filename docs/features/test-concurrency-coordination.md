# Test Concurrency Coordination

Companion to [Full-suite pytest advisory lock](full-suite-pytest-lock.md),
which documents the canonical lock module (`scripts/suite_lock.py`) and its
integration in `scripts/pytest-clean.sh`. This page covers the sentinel-ID
namespacing (added by issue #1967's PR #1984 on top of that lock) that
eliminates cross-run Redis contention at the source. The lock module is also
importable in-process (`suite_lock.acquire/release` with a `try/finally`) for
any tool that runs the suite outside `pytest-clean.sh`.

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
| [Full-suite pytest advisory lock](full-suite-pytest-lock.md) | Canonical lock module (`scripts/suite_lock.py`) and `pytest-clean.sh` integration |
| [`scripts/suite_lock.py`](../../scripts/suite_lock.py) | Lock module + CLI |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |
