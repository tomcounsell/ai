---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/936
last_comment_id:
---

# Email Bridge: Operational Test Coverage

## Problem

The email bridge (`bridge/email_bridge.py`) shipped in PR #908 with tests that cover parsing and routing logic, but the operational layer — `main()`, `_poll_imap()`, and `_email_inbox_loop()` — has zero test coverage. Three bugs slipped through to first real-world deployment:

1. Missing `load_dotenv()` in `main()` caused silent startup failure when env vars weren't pre-exported
2. No per-poll batch cap caused the bridge to hang indefinitely on inboxes with thousands of unseen messages
3. The `email:last_poll_ts` Redis health key was never verified in tests

All three bugs were hotfixed in commit `53ea6401` but without accompanying regression tests.

**Current behavior:**
Tests pass, but they don't exercise the operational layer. The same bugs (or regressions) could ship again undetected.

**Desired outcome:**
Three targeted tests that would have caught each bug before merge. Test-only changes — no runtime code modifications.

## Freshness Check

**Baseline commit:** `345f80ed179ffd0595396f2f27d7221d4c0c3c24`
**Issue filed at:** 2026-04-13T09:18:08Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/email_bridge.py:614-631` — `main()` with `load_dotenv()` calls — still holds at lines 614-631
- `bridge/email_bridge.py:37-39` — `IMAP_MAX_BATCH` constant — still holds at lines 38-39
- `bridge/email_bridge.py:44-45` — `REDIS_LAST_POLL_KEY` — still holds at line 45
- `bridge/email_bridge.py:493-496` — batch cap logic in `_poll_imap()` — still holds at lines 495-496
- `bridge/email_bridge.py:533-536` — health timestamp write in `_email_inbox_loop()` — still holds at lines 533-536

**Cited sibling issues/PRs re-checked:**
- #847 — CLOSED at 2026-04-13T03:28:53Z (email bridge feature spec, completed)
- PR #908 — MERGED at 2026-04-13T03:28:52Z (email bridge implementation, shipped)

**Commits on main since issue was filed (touching referenced files):**
- None — no commits to `bridge/email_bridge.py`, `tests/unit/test_email_bridge.py`, or `tests/integration/test_email_bridge.py` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** None

**Notes:** All file:line references verified against current HEAD. No drift.

## Prior Art

- **Issue #847 / PR #908**: Email bridge feature — shipped the bridge with unit tests for parsing/routing and integration tests for `_process_inbound_email()`. Test gaps in the operational layer were not caught during review.

## Solution

### Key Elements

- **Batch cap test**: Exercises `_poll_imap()` with a mock IMAP server returning more than `IMAP_MAX_BATCH` unseen messages. Asserts exactly `IMAP_MAX_BATCH` messages are fetched.
- **Health timestamp test**: Runs one iteration of `_email_inbox_loop()` (broken out of the infinite loop) and asserts `email:last_poll_ts` is written to Redis.
- **Env loading test**: Validates that `main()` reads IMAP config from a `.env` file on disk rather than pre-exported environment variables.

### Flow

`_poll_imap(mock_config)` → mock IMAP returns N unseen → batch cap limits to IMAP_MAX_BATCH → returns capped list

`_email_inbox_loop(config)` → calls `_poll_imap()` → writes `email:last_poll_ts` to Redis → test reads key and asserts

`main()` → calls `load_dotenv()` → reads env file → `_get_imap_config()` returns populated dict

### Technical Approach

- All three tests use `unittest.mock` for IMAP simulation — no live IMAP server needed
- The batch cap test mocks `imaplib.IMAP4_SSL` to return a configurable number of message IDs from `conn.search()`, then counts `conn.store()` and `conn.fetch()` calls
- The health timestamp test patches `_poll_imap` to return immediately (empty list), breaks out of `_email_inbox_loop` after one iteration (via a side effect on `asyncio.sleep` that raises `StopIteration` or similar), and checks Redis for the key
- The env loading test uses `tmp_path` to create a temporary `.env` file, patches `Path(__file__)` resolution so `main()` reads from the temp file, and verifies `_get_imap_config()` returns populated values after `main()` calls `load_dotenv()`
- Tests go in `tests/unit/test_email_bridge.py` (batch cap and env loading) and `tests/integration/test_email_bridge.py` (health timestamp, since it needs Redis)
- No `email` pytest marker needed — existing `bridge` pattern in FEATURE_MAP auto-tags these as `messaging`

## Failure Path Test Strategy

### Exception Handling Coverage
- `_poll_imap()` has a `finally` block for `conn.logout()` that swallows exceptions — the batch cap test should verify that fetch still works correctly even when `logout()` would fail (edge case, low priority)
- No new exception handlers are being added — these are test-only changes

### Empty/Invalid Input Handling
- The health timestamp test covers the empty inbox case (zero messages returned) — verifies `last_poll_ts` is still written
- The batch cap test covers the boundary case where `msg_ids` count equals exactly `IMAP_MAX_BATCH`

### Error State Rendering
- Not applicable — no user-visible output changes

## Test Impact

No existing tests affected — these are purely additive tests covering previously untested operational code paths. The new tests exercise `_poll_imap()`, `_email_inbox_loop()`, and `main()` which have zero existing test coverage.

## Rabbit Holes

- **Full IMAP server integration test**: Spinning up a real IMAP server or connecting to a live Gmail account is out of scope. Mock IMAP is sufficient for the batch cap and health timestamp logic.
- **Testing the full async loop**: Don't try to test `_email_inbox_loop()` as a long-running loop. Break out after one iteration using a controlled exception from `asyncio.sleep`.
- **Subprocess-based env test**: The issue suggests running `python -m bridge.email_bridge` as a subprocess. This is fragile and slow. Prefer an in-process test that patches file paths and verifies `load_dotenv()` is called with the right arguments.

## Risks

### Risk 1: Mock IMAP fidelity
**Impact:** Tests pass but don't reflect real IMAP behavior.
**Mitigation:** Mock at the `imaplib.IMAP4_SSL` level (not at the `_poll_imap` level), so the actual fetch/store/search logic is exercised against realistic mock responses.

### Risk 2: Breaking out of the infinite loop
**Impact:** `_email_inbox_loop()` runs forever — test must break out cleanly.
**Mitigation:** Patch `asyncio.sleep` to raise a custom exception after the first call. Catch that exception in the test and assert the health key was written before the break.

## Race Conditions

No race conditions identified — all three tests are synchronous unit/integration tests with no concurrent access patterns.

## No-Gos (Out of Scope)

- No runtime code changes (all bugs are already hotfixed)
- No live IMAP server tests
- No SMTP send tests (already covered by existing unit tests)
- No dashboard badge rendering tests (UI concern, separate scope)
- No new pytest markers (existing `messaging` auto-tag covers these files)

## Update System

No update system changes required — this is a test-only change with no new dependencies or runtime behavior.

## Agent Integration

No agent integration required — these are test files only. No MCP servers, bridge imports, or tool wrappers needed.

## Documentation

- [ ] Add email bridge operational tests to the `tests/README.md` Test Index under a new `email` subsection or extend the `messaging` section
- [ ] Update the Known Blind Spots section in `tests/README.md` to reflect that `bridge/email_bridge.py` operational layer is now covered

## Success Criteria

- [ ] `_poll_imap()` batch cap test: mock IMAP with `IMAP_MAX_BATCH + 10` unseen messages, assert exactly `IMAP_MAX_BATCH` fetches occur
- [ ] Health timestamp test: run one poll iteration, assert `email:last_poll_ts` exists in Redis test db
- [ ] Env loading test: validate `main()` calls `load_dotenv()` with correct file paths
- [ ] All new tests pass: `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -v`
- [ ] `tests/README.md` documents email bridge coverage
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (test-writer)**
  - Name: email-test-builder
  - Role: Write the three new test cases
  - Agent Type: test-engineer
  - Resume: true

- **Validator (test-validator)**
  - Name: email-test-validator
  - Role: Verify tests pass and cover the intended gaps
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Write batch cap unit test
- **Task ID**: build-batch-cap-test
- **Depends On**: none
- **Validates**: `tests/unit/test_email_bridge.py::TestPollImapBatchCap` (create)
- **Assigned To**: email-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestPollImapBatchCap` class to `tests/unit/test_email_bridge.py`
- Mock `imaplib.IMAP4_SSL` to return `IMAP_MAX_BATCH + 10` message IDs from `conn.search()`
- Assert `conn.store()` called exactly `IMAP_MAX_BATCH` times
- Assert `conn.fetch()` called exactly `IMAP_MAX_BATCH` times
- Assert the returned list has exactly `IMAP_MAX_BATCH` items
- Import `IMAP_MAX_BATCH` and `_poll_imap` from `bridge.email_bridge`

### 2. Write env loading unit test
- **Task ID**: build-env-loading-test
- **Depends On**: none
- **Validates**: `tests/unit/test_email_bridge.py::TestMainEnvLoading` (create)
- **Assigned To**: email-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestMainEnvLoading` class to `tests/unit/test_email_bridge.py`
- Patch `bridge.email_bridge.load_dotenv` and `bridge.email_bridge.asyncio.run` to intercept calls
- Call `main()` and assert `load_dotenv` was called twice (repo `.env` and vault `.env`)
- Verify the paths passed to `load_dotenv` match the expected locations (repo root and `~/Desktop/Valor/.env`)

### 3. Write health timestamp integration test
- **Task ID**: build-health-timestamp-test
- **Depends On**: none
- **Validates**: `tests/integration/test_email_bridge.py::TestHealthTimestamp` (create)
- **Assigned To**: email-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add `TestHealthTimestamp` class to `tests/integration/test_email_bridge.py`
- Patch `_poll_imap` to return an empty list (simulating empty inbox)
- Patch `asyncio.sleep` to raise a custom `_BreakLoop` exception after first call
- Patch `_get_redis` to return the test Redis connection (db=1)
- Call `_email_inbox_loop()` wrapped in a try/except for the break exception
- Assert `email:last_poll_ts` key exists in test Redis and contains a valid timestamp
- Clean up the Redis key after the test

### 4. Update tests/README.md
- **Task ID**: build-readme-update
- **Depends On**: build-batch-cap-test, build-env-loading-test, build-health-timestamp-test
- **Assigned To**: email-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add an `email` subsection to the Test Index with entries for both test files
- Update the Known Blind Spots table: add `bridge/email_bridge.py` operational layer as now partially covered, or note the improvement

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-readme-update
- **Assigned To**: email-test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py -v`
- Verify all new tests pass
- Verify existing tests still pass
- Run `python -m ruff check tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py`
- Confirm README updates are accurate

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_email_bridge.py -v` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_email_bridge.py -v` | exit code 0 |
| Lint clean | `python -m ruff check tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_email_bridge.py tests/integration/test_email_bridge.py` | exit code 0 |
| Batch cap test exists | `pytest tests/unit/test_email_bridge.py -k "batch" --collect-only -q` | output contains 1 |
| Health timestamp test exists | `pytest tests/integration/test_email_bridge.py -k "health" --collect-only -q` | output contains 1 |
| Env loading test exists | `pytest tests/unit/test_email_bridge.py -k "env" --collect-only -q` | output contains 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-13. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | Task 2 says patch `bridge.email_bridge.load_dotenv` but `load_dotenv` is imported locally inside `main()` (line 619), so that patch target won't exist at module level | Task 2 revision | Patch `dotenv.load_dotenv` instead of `bridge.email_bridge.load_dotenv`. The local import `from dotenv import load_dotenv` binds to the dotenv module, so `patch('dotenv.load_dotenv')` is the correct target. |
| CONCERN | Skeptic | Task 1 batch cap test must handle `_poll_imap` being async (`asyncio.to_thread` wrapper at line 514). Plan doesn't specify whether tests are async or how to handle the threading layer | Task 1 revision | Either mark the test `@pytest.mark.asyncio` and `await _poll_imap(mock_config)`, or patch `asyncio.to_thread` to call the function synchronously via `side_effect=lambda fn: fn()`. The latter avoids needing an event loop for a unit test. |
| NIT | Simplifier | Task 3 doesn't mention the `config` parameter of `_email_inbox_loop(imap_config, config)` — builder must supply a valid second argument | | Pass `config={}` since `_poll_imap` is mocked to return `[]` and `_process_inbound_email` is never reached |

---

## Open Questions

No open questions — the issue is well-scoped with clear acceptance criteria, all three bugs are already hotfixed, and this is purely additive test work.
