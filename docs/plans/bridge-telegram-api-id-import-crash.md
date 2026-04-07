---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/754
last_comment_id:
---

# Bridge Telegram API ID Import Crash

## Problem

`bridge/telegram_bridge.py` evaluates `int(os.getenv("TELEGRAM_API_ID", "0"))` at module import time (line 366). If `TELEGRAM_API_ID` is set to any non-numeric value — including the placeholder `12345****` shipped in `.env.example` — Python raises `ValueError: invalid literal for int() with base 10: '12345****'` *during import*, before any logging, validation, or graceful error path can run.

This crashes:
- The bridge itself (`python -m bridge.telegram_bridge`)
- Anything that imports `bridge.telegram_bridge` (tests, tools, scripts)
- The standalone worker indirectly, via shared imports
- The watchdog's restart loop, which keeps relaunching a process that crashes during import

The same fragile pattern exists in several scripts: `scripts/telegram_login.py:31`, `scripts/test_emoji_reactions.py:31`, `scripts/fetch_recent_dms.py:17`, `scripts/debug_catchup.py:26`, and `scripts/reflections.py:2883` (the latter is inside a function so it's lazy, but still raises an unhandled `ValueError`). `tools/valor_telegram.py:97` is also lazy but still uses bare `int()`.

**Current behavior:**
- Misconfigured env var ⇒ ImportError-equivalent crash with a confusing traceback pointing at line 366
- The dedicated check at line 633 (`if not API_ID or not API_HASH: logger.error(...)`) is unreachable when the value is non-numeric
- Operators following `.env.example` literally hit this immediately

**Desired outcome:**
- Invalid `TELEGRAM_API_ID` produces a clear, actionable error (logged, not a stack trace) and exits with non-zero code at runtime — never at import time
- Module import always succeeds regardless of env contents
- The same defensive parser is reused across the bridge and the affected scripts

## Prior Art

No prior issues or PRs found that addressed module-level `int()` parsing of `TELEGRAM_API_ID`. This pattern has existed since the bridge was first written and has not been challenged.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — `API_ID` remains an `int` at module scope; only its initialization changes
- **Coupling**: Slightly reduces coupling between import success and env correctness
- **Reversibility**: Trivial; one-line revert per file
- **New helper**: Add `_parse_api_id(raw: str | None) -> int` (or similar) to a small shared location — likely `bridge/telegram_bridge.py` itself, since other scripts already duplicate the pattern. To avoid scope creep, the helper lives in `bridge/telegram_bridge.py` and the affected scripts get the same defensive treatment inline (one-line `try/except`). A larger refactor that extracts shared Telegram config is explicitly out of scope.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a localized bug fix with a clear, mechanical solution.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Defensive parser**: `_parse_api_id()` returns `0` when the env var is missing, empty, or non-numeric. It logs a warning when it sees a non-numeric value so operators get an early signal even before the runtime check at line 633 fires.
- **Module-level safety**: Replace the bare `int(os.getenv(...))` calls with the helper. Module import becomes infallible.
- **Runtime guard preserved**: The existing `if not API_ID or not API_HASH` check at line 633 remains the authoritative "fail loudly and exit" path.

### Technical Approach

1. Add `_parse_api_id(raw: str | None) -> int` near the top of `bridge/telegram_bridge.py`, before line 366. Returns 0 on missing/invalid; logs a `logger.warning` (or stderr write if logger not yet configured) on invalid non-empty input.
2. Replace line 366 with `API_ID = _parse_api_id(os.getenv("TELEGRAM_API_ID"))`.
3. Apply the same one-line safe-parse fix to: `scripts/telegram_login.py`, `scripts/test_emoji_reactions.py`, `scripts/fetch_recent_dms.py`, `scripts/debug_catchup.py`. These can either import the helper from the bridge or use a local `try/except ValueError: 0` — prefer the local try/except to avoid creating an unwanted import edge from `scripts/` into `bridge/`.
4. `tools/valor_telegram.py:97` and `scripts/reflections.py:2883` are inside functions and the existing `RuntimeError` / skip paths handle missing values, but they still crash on non-numeric. Wrap them with `try/except ValueError` and route through the existing missing-credentials path.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No `except Exception: pass` blocks in scope
- [ ] New `_parse_api_id` must log a warning (observable behavior) when given a non-numeric value — covered by unit test

### Empty/Invalid Input Handling
- [ ] Test: empty string → returns 0, no warning
- [ ] Test: missing env var (None) → returns 0, no warning
- [ ] Test: `"12345****"` → returns 0, warning logged
- [ ] Test: `"12345"` → returns 12345, no warning
- [ ] Test: whitespace `"  42  "` → returns 42 (or 0 with warning — pick one and document it; recommend strict: 0 + warning, since real API IDs never have whitespace)

### Error State Rendering
- [ ] Importing `bridge.telegram_bridge` with `TELEGRAM_API_ID=12345****` succeeds (no exception)
- [ ] Running `python -m bridge.telegram_bridge` with the same env exits with code 1 and logs the existing "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set" error

## Test Impact

- [ ] `tests/e2e/test_telegram_flow.py::` (line 134) — UPDATE if needed: currently skips on missing creds, should still skip; verify the skip still triggers when env is now `0` instead of import-failed.
- [ ] No other existing tests reference `API_ID` parsing.

New tests:
- [ ] `tests/unit/test_bridge_api_id_parse.py` — covers `_parse_api_id` happy + edge cases and asserts `import bridge.telegram_bridge` succeeds with garbage env (use `monkeypatch.setenv`)

## Rabbit Holes

- Refactoring all Telegram config into a shared `config/telegram.py` module — tempting but out of scope; pure mechanical fix here
- Adding pydantic / env-parsing libraries — overkill for one int
- "Fixing" `.env.example` to use a numeric placeholder — does not address the underlying fragility; do both only if the placeholder change is trivial (it is — change `12345****` to `0` or `00000000` with a comment)

## Risks

### Risk 1: Silent misconfiguration
**Impact:** Operator sets `TELEGRAM_API_ID=garbage`, parser returns 0, bridge logs the existing error and exits. Less obvious than a Python traceback for the original cause.
**Mitigation:** The warning emitted by `_parse_api_id` includes the raw value (truncated/masked) so the cause is visible. The runtime guard at line 633 still fails loudly.

### Risk 2: Broken `tools/valor_telegram.py` flow
**Impact:** This tool is invoked by the agent; its `RuntimeError` path is the user-facing error. Changing it must keep the same UX.
**Mitigation:** Keep the existing `RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")` message; just route the `ValueError` case through the same branch.

## Race Conditions

No race conditions identified — all changes are synchronous, single-threaded, module-init code.

## No-Gos (Out of Scope)

- Refactor of Telegram config into a shared module
- Replacing `os.getenv` with a config library
- Updating other env-var parsing patterns elsewhere in the codebase (e.g., `int(os.getenv("FOO"))` for non-Telegram vars)
- Changing the bridge's runtime credential validation behavior

## Update System

No update system changes required — this is a localized code fix. Operators with previously crashing bridges will recover automatically on next restart after pulling the fix; the watchdog will succeed.

## Agent Integration

No agent integration required — this is a bridge-internal change. No new tools, no MCP changes. The agent's existing `tools/valor_telegram.py` gets a defensive `try/except` but its public interface and error messages are unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` to note that import-time crashes from misconfigured env vars are now prevented (the watchdog can no longer get stuck restarting an unimportable bridge)

### Inline Documentation
- [ ] Docstring on `_parse_api_id` explaining the contract: "returns 0 on missing or invalid; logs warning on invalid non-empty input; never raises"

If no other docs reference this code path (likely), state explicitly in the build that no further docs were touched.

## Success Criteria

- [ ] `python -c "import os; os.environ['TELEGRAM_API_ID']='12345****'; import bridge.telegram_bridge"` exits 0
- [ ] `python -m bridge.telegram_bridge` with the same env logs the credentials error and exits 1 (not a traceback)
- [ ] New unit tests in `tests/unit/test_bridge_api_id_parse.py` pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated where applicable (`/do-docs`)
- [ ] All four scripts (`telegram_login.py`, `test_emoji_reactions.py`, `fetch_recent_dms.py`, `debug_catchup.py`) no longer crash at module load with garbage env

## Team Orchestration

### Team Members

- **Builder (bridge-fix)**
  - Name: bridge-api-id-builder
  - Role: Implement `_parse_api_id`, update bridge + scripts, write unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-fix)**
  - Name: bridge-api-id-validator
  - Role: Verify import-with-garbage-env succeeds, runtime-with-garbage-env fails cleanly, all unit tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement parser and apply fixes
- **Task ID**: build-bridge-api-id
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_api_id_parse.py (create), tests/e2e/test_telegram_flow.py
- **Assigned To**: bridge-api-id-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_parse_api_id` helper to `bridge/telegram_bridge.py` above line 366
- Replace `int(os.getenv("TELEGRAM_API_ID", "0"))` with helper call on line 366
- Apply local `try/except ValueError: API_ID = 0` to `scripts/telegram_login.py:31`, `scripts/test_emoji_reactions.py:31`, `scripts/fetch_recent_dms.py:17`, `scripts/debug_catchup.py:26`
- Wrap `tools/valor_telegram.py:97` and `scripts/reflections.py:2883` with `try/except ValueError` routing to existing missing-creds branches
- Create `tests/unit/test_bridge_api_id_parse.py` with happy + edge cases
- Optionally update `.env.example` placeholder from `12345****` to `00000000  # numeric placeholder, replace with real ID`

### 2. Validate
- **Task ID**: validate-bridge-api-id
- **Depends On**: build-bridge-api-id
- **Assigned To**: bridge-api-id-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_api_id_parse.py -v`
- Run import-with-garbage-env smoke test
- Run runtime-with-garbage-env smoke test (expect exit 1, no traceback)
- Run full unit suite to verify nothing regressed
- Report pass/fail

### 3. Documentation
- **Task ID**: document-bridge-api-id
- **Depends On**: validate-bridge-api-id
- **Assigned To**: bridge-api-id-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with one-paragraph note about import-time safety

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_bridge_api_id_parse.py -x -q` | exit code 0 |
| Import-with-garbage env | `TELEGRAM_API_ID=12345**** python -c "import bridge.telegram_bridge"` | exit code 0 |
| Runtime-with-garbage env | `TELEGRAM_API_ID=12345**** python -m bridge.telegram_bridge` | exit code 1, no traceback |
| Format clean | `python -m ruff format --check bridge/ scripts/ tools/ tests/` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. Should `_parse_api_id` also handle whitespace-trimmed input (`"  42  "` → `42`), or treat any non-strict-int as invalid? Recommendation: strict — return 0 + warning. Real API IDs never have whitespace and strict parsing surfaces operator typos.
2. Should `.env.example` placeholder be updated in this PR, or is that a separate cosmetic change? Recommendation: include it — one-line, zero risk, removes the literal trap.
