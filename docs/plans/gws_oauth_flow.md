---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/851
---

# Fix Google Workspace OAuth Flow

## Problem

The Google Workspace OAuth module (`tools/google_workspace/auth.py`) works for the happy path but fails ungracefully when tokens expire, get revoked, or when scopes change. The `/setup` skill references a `valor-calendar test` subcommand that does not exist. Error messages are raw Python exceptions instead of actionable recovery instructions.

**Current behavior:**
- `get_credentials()` calls `creds.refresh(Request())` with no try/except -- a revoked token or network error produces an unhandled `google.auth.exceptions.RefreshError` traceback
- `/setup` Step 4 tells users to run `valor-calendar test`, which does not exist (only `--version` and `<session-slug>` are supported)
- `cal_integration.py` catches all exceptions with a vague "Calendar API auth failed: {e}" message
- No way to force re-authentication without manually deleting token files
- No proactive token health check -- failures only surface when an API call is attempted

**Desired outcome:**
- Token refresh errors produce clear messages with the exact command to re-authenticate
- A `--reauth` flag on `valor-calendar` clears tokens and re-runs OAuth consent
- A `--check` flag validates token health without making API calls
- `/setup` Step 4 references commands that actually exist
- The `verify_token()` function can be called by `/setup` and `/update` scripts

## Prior Art

- **Issue #398**: Consolidate per-project config into projects.json -- established `~/Desktop/Valor/` as the canonical config directory. Succeeded.
- **Issue #416**: Config consolidation: scattered files, env vars, hardcoded paths -- broader cleanup that touched credential paths. Succeeded.
- **Issue #452**: Rename Desktop config dir from claude_code to Valor -- renamed the directory where tokens live. Succeeded.
- **PR #438**: Config consolidation: eliminate hardcoded paths, unify settings -- merged, established `GoogleAuthSettings` in `config/settings.py`. Succeeded.

None of these addressed error handling or recovery flows in the auth module itself.

## Data Flow

1. **Entry point**: User runs `valor-calendar <slug>` or agent calls `get_service("calendar", "v3")`
2. **`auth.py:get_credentials()`**: Loads token from `~/Desktop/Valor/google_token.<machine>.json`, attempts refresh if expired
3. **Google OAuth server**: Validates/refreshes token. Can fail with: `RefreshError` (revoked), network timeout, scope mismatch
4. **`auth.py:get_service()`**: Builds and caches a `googleapiclient.discovery.Resource` using the credentials
5. **`valor_calendar.py:main()`**: Uses the service to create/extend calendar events. On any exception, queues locally
6. **`cal_integration.py:generate_calendar_config()`**: Also calls `get_service()`, catches exceptions with vague error message

## Architectural Impact

- **New dependencies**: None -- `google.auth.exceptions` is already available via `google-auth` (installed dependency)
- **Interface changes**: `get_credentials()` gains structured error handling. New functions `verify_token()` and `clear_and_reauth()` are added. `valor-calendar` gains `--reauth` and `--check` flags. All additive, no breaking changes.
- **Coupling**: No change -- auth module remains the single source of Google credentials
- **Data ownership**: No change -- tokens remain in `~/Desktop/Valor/`
- **Reversibility**: Fully reversible -- all changes are additive

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies beyond what's already installed.

## Solution

### Key Elements

- **Error-resilient `get_credentials()`**: Catches `RefreshError`, `TransportError`, and scope mismatches with actionable error messages
- **`verify_token()` function**: Proactive token health check (valid, correct scopes, refreshable) without making API calls
- **CLI `--reauth` and `--check` flags**: User-facing commands for token management
- **`/setup` Step 4 fix**: References real commands instead of nonexistent `valor-calendar test`

### Flow

**User/agent** → calls `get_credentials()` → token loaded → refresh attempted → **success**: return credentials | **failure**: raise `AuthError` with recovery message pointing to `valor-calendar --reauth`

**New machine setup** → `/setup` Step 4 → `valor-calendar --check` → **no token**: `valor-calendar --reauth` → browser OAuth → token saved → **verified**

### Technical Approach

- Add a custom `GoogleAuthError` exception class that wraps raw Google auth exceptions with recovery instructions
- Catch `google.auth.exceptions.RefreshError` and `google.auth.exceptions.TransportError` specifically in `get_credentials()`
- `verify_token()` loads the token file and checks: exists, parseable, has required scopes, not expired (or refreshable). Returns a structured result, does not raise.
- `--reauth` calls `clear_tokens()` then `get_credentials()` (which triggers the browser flow)
- `--check` calls `verify_token()` and prints a human-readable status

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The bare `creds.refresh(Request())` in `get_credentials()` (line 89) currently has no exception handler -- add try/except for `RefreshError` and `TransportError` with tests asserting the custom `GoogleAuthError` is raised with recovery instructions
- [ ] The `except Exception` in `valor_calendar.py:main()` (line 285) already queues locally on failure -- verify it logs the actionable message from the new error type

### Empty/Invalid Input Handling
- [ ] Test `get_credentials()` when token file exists but contains invalid JSON
- [ ] Test `get_credentials()` when token file exists but is empty
- [ ] Test `verify_token()` when credentials dir does not exist

### Error State Rendering
- [ ] Verify `--check` prints clear status for each failure mode (no token, expired, revoked, wrong scopes)
- [ ] Verify `--reauth` prints success/failure message after completing the flow

## Test Impact

- [ ] `tests/unit/test_config_consolidation.py::TestGoogleAuthNoFallback` -- no change needed, these test path hygiene not auth behavior
- No other existing tests touch the auth module directly. All new tests are additive.

No existing tests affected -- the auth module had no behavioral tests; only path/config tests exist in `test_config_consolidation.py` which remain valid.

## Rabbit Holes

- **Unifying `gws` (Node.js) auth with Python auth**: These are independent tools with separate auth mechanisms. Forcing shared tokens adds complexity with no benefit. Out of scope.
- **Adding new Google API scopes**: The `SCOPES` list currently only has Calendar. Expanding scopes is a separate concern -- this work is about error resilience, not scope expansion.
- **Implementing automatic token rotation**: Over-engineering. Google handles token lifecycle; we just need to handle failures gracefully.

## Risks

### Risk 1: Browser OAuth flow fails in headless environments
**Impact:** `--reauth` cannot complete on servers without a display
**Mitigation:** Detect headless environment and print manual instructions ("Copy this URL, paste in browser, enter the code")

## Race Conditions

No race conditions identified -- all token operations are synchronous, single-process, and use per-machine token files to avoid cross-machine conflicts.

## No-Gos (Out of Scope)

- Unifying `gws` CLI auth with Python OAuth auth
- Expanding SCOPES beyond Calendar
- Implementing refresh token rotation or automatic re-auth
- Changing token storage location from `~/Desktop/Valor/`
- Adding new Python dependencies

## Update System

The `/update` skill's `cal_integration.py` calls `generate_calendar_config()` which uses `get_service()`. After this change, `generate_calendar_config()` will receive clearer error messages from the auth module. No changes needed to the update script itself -- the improved error messages will propagate automatically through the existing error handling in `cal_integration.py`.

No update system changes required beyond what the auth module improvements provide automatically.

## Agent Integration

No agent integration required -- the auth module is called internally by `valor_calendar.py` and `cal_integration.py`. The agent interacts with Google Calendar through the `gws` CLI (Node.js, separate auth) or via `valor-calendar` which already handles auth failures by queuing locally. No MCP server changes needed.

## Documentation

- [ ] Create `docs/features/google-workspace-auth.md` describing the auth module, error handling, and recovery commands
- [ ] Update `/setup` skill Step 4 to reference `valor-calendar --check` and `valor-calendar --reauth` instead of nonexistent `valor-calendar test`
- [ ] Add inline docstrings to new `verify_token()`, `GoogleAuthError`, and CLI flag handling

## Success Criteria

- [ ] `get_credentials()` catches `RefreshError` and `TransportError` with specific error messages including recovery commands
- [ ] `valor-calendar --reauth` clears token and re-runs OAuth consent flow end-to-end
- [ ] `valor-calendar --check` prints token health status (valid/expired/revoked/missing)
- [ ] `verify_token()` returns structured result usable by `/setup` and `/update`
- [ ] `/setup` skill Step 4 references commands that actually exist
- [ ] Unit tests cover: successful refresh, revoked token error, missing credentials file, invalid token file, scope mismatch, `--check` output, `--reauth` flow
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (auth-module)**
  - Name: auth-builder
  - Role: Implement error handling, verify_token(), CLI flags, and /setup fix
  - Agent Type: builder
  - Resume: true

- **Validator (auth-module)**
  - Name: auth-validator
  - Role: Verify all error paths produce correct messages and CLI flags work
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add error handling and verify_token() to auth.py
- **Task ID**: build-auth-module
- **Depends On**: none
- **Validates**: tests/unit/test_google_auth.py (create)
- **Assigned To**: auth-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `GoogleAuthError` exception class with `recovery_command` attribute
- Wrap `creds.refresh(Request())` in try/except catching `google.auth.exceptions.RefreshError` and `google.auth.exceptions.TransportError`
- On `RefreshError`: raise `GoogleAuthError("Token revoked or expired. Run: valor-calendar --reauth")`
- On `TransportError`: raise `GoogleAuthError("Network error during token refresh. Check connectivity and retry.")`
- Add `verify_token() -> dict` function that checks: token file exists, parseable, has required scopes, not expired or has refresh token. Returns `{"valid": bool, "status": str, "scopes": list, "expired": bool}`
- Add scope validation: if loaded token scopes don't match `SCOPES`, include mismatch in status

### 2. Add --reauth and --check CLI flags to valor_calendar.py
- **Task ID**: build-cli-flags
- **Depends On**: build-auth-module
- **Validates**: tests/unit/test_google_auth.py (extend)
- **Assigned To**: auth-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--reauth` flag handling before the slug argument check: calls `clear_tokens()` then `get_credentials()`, prints success/failure, exits
- Add `--check` flag handling: calls `verify_token()`, prints formatted status, exits with code 0 if valid, 1 if not
- Update the usage line to include new flags: `Usage: valor-calendar [--project PROJECT] [--reauth] [--check] <session-slug>`
- Update the `except Exception` block in `main()` to check for `GoogleAuthError` and print the recovery command

### 3. Fix /setup skill Step 4
- **Task ID**: build-setup-fix
- **Depends On**: build-cli-flags
- **Validates**: manual review
- **Assigned To**: auth-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `valor-calendar test` references in `.claude/skills/setup/SKILL.md` with `valor-calendar --check`
- Replace re-auth instructions with `valor-calendar --reauth`
- Verify the troubleshooting section at the bottom also uses correct commands

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-auth-module, build-cli-flags
- **Validates**: tests/unit/test_google_auth.py (create)
- **Assigned To**: auth-builder
- **Agent Type**: builder
- **Parallel**: false
- Test `get_credentials()` raises `GoogleAuthError` with recovery message when `RefreshError` occurs (mock `creds.refresh`)
- Test `get_credentials()` raises `GoogleAuthError` on `TransportError` (mock `creds.refresh`)
- Test `get_credentials()` raises `FileNotFoundError` when no credentials file exists (existing behavior, verify preserved)
- Test `verify_token()` returns `{"valid": False, "status": "missing"}` when no token file
- Test `verify_token()` returns `{"valid": False, "status": "invalid"}` when token file is not valid JSON
- Test `verify_token()` returns `{"valid": True, ...}` when token is valid and has correct scopes
- Test `verify_token()` returns scope mismatch status when token scopes differ from `SCOPES`
- Test `--check` flag exits 0 when token is valid, exits 1 when invalid
- Test `--reauth` flag calls `clear_tokens()` and `get_credentials()`

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-setup-fix
- **Assigned To**: auth-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_google_auth.py -v` and verify all pass
- Run `pytest tests/unit/test_config_consolidation.py -v` and verify existing tests still pass
- Verify `valor-calendar --check` and `valor-calendar --reauth` appear in `/setup` skill
- Verify no raw exception tracebacks leak from `get_credentials()` for handled error types

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: auth-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/google-workspace-auth.md` describing the auth module, error handling, and recovery commands
- Add entry to `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_google_auth.py -x -q` | exit code 0 |
| Existing tests pass | `pytest tests/unit/test_config_consolidation.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/google_workspace/auth.py tools/valor_calendar.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/google_workspace/auth.py tools/valor_calendar.py` | exit code 0 |
| Setup skill updated | `grep -c 'valor-calendar test' .claude/skills/setup/SKILL.md` | output contains 0 |
| New flags exist | `grep -c 'reauth\|--check' tools/valor_calendar.py` | output > 0 |
| verify_token exists | `grep -c 'def verify_token' tools/google_workspace/auth.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue is well-defined with clear acceptance criteria. All technical decisions are straightforward: catch known exception types, add CLI flags, fix stale documentation references.
