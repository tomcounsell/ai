---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/858
last_comment_id:
---

# Sentry Hibernation Filter

## Problem

When the bridge cannot authenticate with Telegram (missing env vars or expired session), two error paths fire on every bridge startup attempt. Because the watchdog or launchd restarts the process repeatedly, the same auth errors are reported to Sentry thousands of times.

**Current behavior:**
- VALOR-1 (2,702 events): `"TELEGRAM_API_ID and TELEGRAM_API_HASH must be set"` -- the env-var guard at `bridge/telegram_bridge.py:673` logs an error and calls `sys.exit(1)`. The watchdog restarts the process, which hits the same missing env vars and re-reports.
- VALOR-Y (102 events): `"Bridge hibernating: auth required"` -- `enter_hibernation()` in `bridge/hibernation.py:97` fires on runtime auth failure. Even though hibernation correctly suppresses watchdog restarts, the Sentry capture fires on every bridge startup cycle that re-detects the auth failure.

**Desired outcome:**
Known hibernation-state errors are captured at most once, not on every process restart. Real, novel errors continue to be captured normally. Sentry event volume for auth errors drops from thousands to single digits.

## Prior Art

- **Issue #840**: Bridge hibernation -- shipped the hibernation system that detects auth failures and suppresses watchdog restarts. Did not address Sentry noise. Closed.
- **Issue #841**: Sentry CLI integration -- adds Sentry CLI tooling to update/setup scripts and optional reflection integration. Open, separate concern. No overlap with this fix.

No prior attempts to filter Sentry events during hibernation were found.

## Data Flow

1. **Entry point**: Bridge process starts (`bridge/telegram_bridge.py:main()`)
2. **Env-var guard** (line 673): Checks `API_ID` and `API_HASH`. If missing, logs error and exits with `sys.exit(1)`. Currently does NOT set the hibernation flag.
3. **Sentry auto-capture**: `sentry_sdk.init()` (line 52) hooks into the process. Any uncaught exception or `logger.error()` call is captured and transmitted with no filtering.
4. **Connect loop** (line 1671-1699): Attempts to connect and authorize with Telegram. On auth failure, calls `enter_hibernation()` (writes flag file) then `raise SystemExit(2)`.
5. **Watchdog/launchd**: Restarts the bridge process. On next startup, the same error is detected and reported to Sentry again.
6. **Output**: Thousands of duplicate Sentry events for the same two known errors.

## Architectural Impact

- **New dependencies**: None -- `before_send` is a built-in Sentry SDK feature
- **Interface changes**: None -- the `sentry_sdk.init()` call gains a `before_send` parameter, no external API change
- **Coupling**: Minimal new coupling -- the `before_send` callback imports `is_hibernating()` from `bridge.hibernation`, which already exists
- **Data ownership**: No change
- **Reversibility**: Trivial -- remove the `before_send` parameter to revert

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work. The change is ~20 lines of new code in a single file, plus the `enter_hibernation()` call added to the env-var guard path.

## Prerequisites

No prerequisites -- this work has no external dependencies. The `sentry_sdk` and `bridge.hibernation` modules are already available.

## Solution

### Key Elements

- **`before_send` callback**: A filter function registered with `sentry_sdk.init()` that inspects each event before transmission
- **Hibernation gate**: The callback checks `is_hibernating()` and drops auth-related events when the flag file is present
- **Env-var guard fix**: The missing-env-var path calls `enter_hibernation()` before exiting, so it gets the same flag-file protection as runtime auth errors

### Flow

**Bridge starts** -> Sentry init (with `before_send` filter) -> Error occurs -> `before_send` called -> Check `is_hibernating()` -> If hibernating AND auth-related error -> drop event (return `None`) -> If not -> pass through normally

### Technical Approach

- Define a `_sentry_before_send(event, hint)` function in `bridge/telegram_bridge.py` near the Sentry init block
- The function calls `bridge.hibernation.is_hibernating()`. If `True`, it inspects the event's exception type or message for known auth-related patterns (the VALOR-1 env-var message and VALOR-Y hibernation message). If matched, return `None` to drop. Otherwise, return `event` unchanged.
- Register the function via `before_send=_sentry_before_send` in the `sentry_sdk.init()` call
- Add `enter_hibernation()` call to the env-var guard at line 673, before `sys.exit(1)`, changing the exit code to `sys.exit(2)` for consistency with the runtime auth failure path
- The auth-error string matching uses substring checks against known messages ("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set", "Bridge hibernating: auth required") rather than exception type checks, since these are logged errors, not typed exceptions

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `before_send` callback itself must be wrapped in a try/except -- if the filter crashes, return `event` (pass-through) to avoid silently dropping all Sentry events
- [ ] Test that a crashing `is_hibernating()` (e.g., `OSError`) does not prevent event transmission

### Empty/Invalid Input Handling
- [ ] Test `before_send` with `hint=None` and `hint={}` (no exception info)
- [ ] Test `before_send` with events that have no `exception` key (e.g., breadcrumb events, transaction events)

### Error State Rendering
- [ ] Not applicable -- no user-visible output in this change

## Test Impact

- [ ] `tests/unit/test_bridge_hibernation.py` -- UPDATE: Add new test class `TestSentryBeforeSend` covering the `before_send` filter logic. Existing tests are unaffected since the hibernation module itself is not modified.

No existing tests are broken by this change -- the modification is purely additive (a new callback parameter on `sentry_sdk.init()` and a new `enter_hibernation()` call in the env-var guard). The existing hibernation tests remain valid.

## Rabbit Holes

- **Rate-limiting Sentry events with local state tracking**: The issue mentions this as an alternative, but it requires tracking "last sent time" across process restarts (file or Redis), adds complexity, and is unnecessary since `is_hibernating()` already captures the "known auth failure" state perfectly.
- **Replacing `logger.error` with `sentry_sdk.capture_message`**: Not relevant -- the errors are captured automatically by the Sentry SDK's logging integration or exception hooks, not by explicit calls. Changing the logging pattern would be a larger refactor with no benefit.
- **Filtering by Sentry fingerprint or issue ID**: Sentry fingerprints are generated server-side and not available in the `before_send` callback. Trying to match VALOR-1/VALOR-Y IDs client-side is not feasible.

## Risks

### Risk 1: Overly aggressive filtering drops real errors
**Impact:** A genuine novel error that happens to contain "auth" in its message could be silently dropped during hibernation.
**Mitigation:** The filter checks for exact known message substrings ("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set", "Bridge hibernating: auth required") rather than broad keyword matching. Additionally, the filter only activates when `is_hibernating()` is True (flag file present), which is already a strong signal that the bridge is in a known-bad auth state.

### Risk 2: `before_send` callback crash silently breaks all Sentry reporting
**Impact:** If `is_hibernating()` raises an unexpected exception inside the callback, all events could be dropped.
**Mitigation:** Wrap the callback body in `try/except Exception` and return `event` (pass-through) on any error. Add a test for this case.

## Race Conditions

No race conditions identified -- all operations are synchronous and single-process. The `before_send` callback runs synchronously within the Sentry SDK's event pipeline, and `is_hibernating()` performs a simple file existence check. The flag file is written atomically (temp + `os.replace`) by `enter_hibernation()`.

## No-Gos (Out of Scope)

- Modifying the Sentry dashboard or issue grouping rules (server-side concern)
- Adding rate-limiting or deduplication logic beyond the flag-file check
- Changing the watchdog behavior or launchd restart policy
- Addressing Sentry event volume from non-auth errors
- Refactoring the bridge startup sequence beyond the env-var guard fix

## Update System

No update system changes required -- this feature is purely internal to the bridge package. The `before_send` callback and `enter_hibernation()` call are code changes that propagate via normal git pull. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is a bridge-internal change. No new tools, MCP servers, or `.mcp.json` changes are needed. The bridge itself does not need new imports beyond the already-existing `bridge.hibernation` module.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` to document the Sentry `before_send` filter as part of the self-healing system
- [ ] Add inline code comments on the `_sentry_before_send` function explaining the filtering logic and the pass-through safety net

## Success Criteria

- [ ] `sentry_sdk.init()` includes a `before_send` callback
- [ ] When `is_hibernating()` returns `True`, auth-related Sentry events are dropped (return `None`)
- [ ] When `is_hibernating()` returns `False`, all events pass through unchanged
- [ ] Non-auth events pass through even when hibernating
- [ ] The env-var-missing path (line 673) calls `enter_hibernation()` before exiting
- [ ] The `before_send` callback is wrapped in try/except and passes through on internal error
- [ ] Unit tests cover: hibernating + auth event (dropped), hibernating + non-auth event (passed), not hibernating + auth event (passed), callback crash (passed)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sentry-filter)**
  - Name: sentry-filter-builder
  - Role: Implement the `before_send` callback and env-var guard fix
  - Agent Type: builder
  - Resume: true

- **Validator (sentry-filter)**
  - Name: sentry-filter-validator
  - Role: Verify filter behavior and test coverage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement `before_send` callback and env-var guard fix
- **Task ID**: build-sentry-filter
- **Depends On**: none
- **Validates**: tests/unit/test_sentry_hibernation_filter.py (create)
- **Assigned To**: sentry-filter-builder
- **Agent Type**: builder
- **Parallel**: true
- Define `_sentry_before_send(event, hint)` function in `bridge/telegram_bridge.py` near the Sentry init block (around line 47-57). The function: (1) calls `from bridge.hibernation import is_hibernating`, (2) if `is_hibernating()` is True, inspects the event for known auth-error messages, (3) returns `None` to drop auth events during hibernation, (4) returns `event` for all other cases. Wrap the body in `try/except Exception` returning `event` on failure.
- Register `before_send=_sentry_before_send` in the `sentry_sdk.init()` call at line 52
- Add `from bridge.hibernation import enter_hibernation` and call `enter_hibernation()` before the `sys.exit(1)` at line 674 (the env-var-missing guard). Change exit code to `sys.exit(2)` for consistency.
- Create `tests/unit/test_sentry_hibernation_filter.py` with test cases: (a) drops auth event when hibernating, (b) passes non-auth event when hibernating, (c) passes auth event when NOT hibernating, (d) passes all events when `is_hibernating()` raises an exception, (e) handles events with no exception info gracefully

### 2. Validate implementation
- **Task ID**: validate-sentry-filter
- **Depends On**: build-sentry-filter
- **Assigned To**: sentry-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `before_send` is registered in `sentry_sdk.init()`
- Verify `enter_hibernation()` is called in the env-var guard path
- Run `pytest tests/unit/test_sentry_hibernation_filter.py -v` and verify all pass
- Run `pytest tests/unit/test_bridge_hibernation.py -v` and verify existing tests still pass
- Run `python -m ruff check bridge/telegram_bridge.py` and verify clean

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-sentry-filter
- **Assigned To**: sentry-filter-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` to document the Sentry `before_send` filter
- Add inline code comments on the `_sentry_before_send` function

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sentry-filter-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Run lint: `python -m ruff check .`
- Run format: `python -m ruff format --check .`
- Verify all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sentry_hibernation_filter.py -v` | exit code 0 |
| Existing hibernation tests pass | `pytest tests/unit/test_bridge_hibernation.py -v` | exit code 0 |
| Lint clean | `python -m ruff check bridge/telegram_bridge.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/telegram_bridge.py` | exit code 0 |
| before_send registered | `grep -c 'before_send' bridge/telegram_bridge.py` | output > 0 |
| enter_hibernation in env-var guard | `grep -A2 'API_ID or not API_HASH' bridge/telegram_bridge.py \| grep -c 'enter_hibernation'` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-09. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | Sentry event field for message matching not specified — logging-captured events store text in different fields than exception-captured events | Task 1 (build-sentry-filter) | `before_send(event, hint)` receives logging events with message in `event.get("logentry", {}).get("message")` or `event.get("message")`; exception events in `event["exception"]["values"][0]["value"]`. Check all three paths: `event.get("message", "") or event.get("logentry", {}).get("message", "")` plus `str(exc_value)` from `hint.get("exc_info", (None,None,None))[1]`. |
| NIT | Operator | No observability for the filter — when `before_send` drops an event, nothing is logged or counted, making it indistinguishable from "no errors occurring" vs "filter silently dropping everything" | Task 1 (build-sentry-filter) | Add `logger.debug("[sentry-filter] Dropped auth event during hibernation: %s", msg[:80])` inside the drop path. |

---

## Open Questions

No open questions -- the issue is well-scoped with a clear solution path, confirmed by recon. The `before_send` callback approach is a standard Sentry SDK pattern and `is_hibernating()` already exists.
