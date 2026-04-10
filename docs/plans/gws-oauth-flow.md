---
status: Complete
type: bug
appetite: Small
owner: valorengels
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/850
last_comment_id: none
---

# GWS OAuth Flow — Resilient Error Handling

## Problem

The Google Workspace OAuth flow had gaps that made it unreliable for new machines and token refresh scenarios.

**Current behavior (at time of filing):**
- `get_credentials()` lacked error handling for `RefreshError`, revoked tokens, and network failures — raw Python exceptions surfaced to users with no actionable recovery steps
- The `/setup` skill Step 4 referenced `valor-calendar test` which was never implemented
- No `verify_token()` function existed for proactive health checks from `/setup` or `/update`
- No `--reauth` flag existed on `valor-calendar` to clear tokens and re-run OAuth consent

**Desired outcome:**
- `get_credentials()` handles all failure modes with messages that include recovery commands
- `verify_token()` checks token health and scopes without making API calls
- `--reauth` and `--check` flags on `valor-calendar` cover the full reauthentication and verification flow
- `/setup` Step 4 references commands that actually exist and work end-to-end

## Prior Art

- **PR #865** ("Fix Google Workspace OAuth error handling"): Shipped all planned changes — merged 2026-04-10. This plan is a post-hoc record of that work.
- **Issue #851**: Tracked the implementation work for this plan. Closed when PR #865 merged.
- **PR #451** ("Per-machine Google OAuth tokens"): Established per-machine token paths to prevent iCloud sync race conditions. Foundation this work builds on.

## Solution

### Key Elements

- **`GoogleAuthError`**: Custom exception class that embeds a `recovery_command` attribute and includes it in `str(e)`, so f-string error propagation surfaces the fix automatically
- **`get_credentials()` error handling**: Catches `RefreshError` (revoked/expired), `TransportError` (network), and `OSError` (headless browser), each with a specific message and recovery command
- **`verify_token()`**: Inspects the token file without making API calls; returns structured dict with `valid`, `status`, `scopes`, `expired`, `has_refresh_token`
- **`clear_tokens()`**: Deletes both per-machine and shared legacy tokens, clears service cache
- **`--reauth` flag**: Calls `clear_tokens()` then `get_credentials()` to restart OAuth consent flow
- **`--check` flag**: Calls `verify_token()` and exits 0 (valid) or 1 (invalid) with human-readable status

### Technical Approach

- All changes in `tools/google_workspace/auth.py` and `tools/valor_calendar.py`
- No new Python dependencies — uses only `google.auth.exceptions` already in the dependency tree
- Token storage location (`~/Desktop/Valor/`) unchanged
- `gws` Node.js CLI remains independent — out of scope by design
- 29 unit tests added in `tests/unit/test_google_auth.py`, all green

## Failure Path Test Strategy

### Exception Handling Coverage
- `RefreshError` path: tested in `TestGetCredentials::test_refresh_error_raises_google_auth_error`
- `TransportError` path: tested in `TestGetCredentials::test_transport_error_raises_google_auth_error`
- `OSError` (headless) path: tested in `TestGetCredentials::test_headless_oserror_raises_google_auth_error`
- Missing credentials file: tested in `TestGetCredentials::test_missing_credentials_file_raises_file_not_found`

### Empty/Invalid Input Handling
- Invalid JSON token file: tested in `TestVerifyToken::test_invalid_json_token_file`
- Empty token file: tested in `TestVerifyToken::test_empty_token_file`
- Non-dict JSON: tested in `TestVerifyToken::test_non_dict_json_token_file`

### Error State Rendering
- `GoogleAuthError.__str__` surfaces recovery command in f-string context: tested in `TestGoogleAuthError::test_str_in_fstring_surfaces_recovery`

## Test Impact

No existing tests were broken — this was greenfield test coverage for a previously untested module. The one prior test file mentioning Google Workspace (`tests/unit/test_config_consolidation.py`) tests config paths only and was unaffected.

## No-Gos (Out of Scope)

- Unifying `gws` (Node.js) CLI auth with Python OAuth — separate tools, independent auth mechanisms
- Adding new calendar features — purely about auth reliability
- Scope expansion for Gmail or Drive in the Python auth module — calendar scope only

## Update System

No update system changes required — the changes are purely internal to `tools/` and `tests/`. No new config files, env vars, or migration steps needed.

## Agent Integration

No agent integration required — `verify_token()` and `get_credentials()` are internal Python functions called by `valor-calendar` CLI. The agent calls `valor-calendar` via shell, not directly via MCP.

## Documentation

- [x] `/setup` skill Step 4 updated in place to reference `valor-calendar --reauth` and `valor-calendar --check` (already correct as of PR #865)
- No separate feature doc needed — the module docstring in `auth.py` describes the design

## Success Criteria

- [x] `get_credentials()` handles `RefreshError`, revoked tokens, and network failures with actionable messages
- [x] `--reauth` flag exists on `valor-calendar` and `/setup` Step 4 references it correctly
- [x] `--check` flag exists and exits 0/1 based on token health
- [x] `verify_token()` function validates token health and required scopes
- [x] 29 unit tests covering all specified error paths pass
- [x] No new Python dependencies introduced

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Auth tests pass | `pytest tests/unit/test_google_auth.py -q` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/google_workspace/auth.py tools/valor_calendar.py` | exit code 0 |
| --check flag exists | `valor-calendar --check 2>&1 \| grep -E "Token status|No token"` | output contains "Token" |
| --reauth flag exists | `grep -- "--reauth" tools/valor_calendar.py` | exit code 0 |
| verify_token exported | `python -c "from tools.google_workspace.auth import verify_token; print('ok')"` | output contains "ok" |
