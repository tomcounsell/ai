# Google Workspace Auth

Error-resilient OAuth authentication for Google Workspace APIs with proactive token health checks and user-facing recovery commands.

## Overview

The auth module (`tools/google_workspace/auth.py`) manages OAuth2 credentials for Google APIs. Tokens are stored per-machine in `~/Desktop/Valor/` to avoid iCloud sync race conditions. This feature adds structured error handling, a `verify_token()` health check, and CLI flags for token management.

## Components

### GoogleAuthError

Custom exception that wraps raw Google auth exceptions with recovery instructions. The `__str__()` method includes the recovery command so that callers using `f"...{e}"` patterns surface actionable instructions automatically.

```python
from tools.google_workspace.auth import GoogleAuthError

try:
    creds = get_credentials()
except GoogleAuthError as e:
    # str(e) includes "Recovery: valor-calendar --reauth"
    print(f"Auth failed: {e}")
```

### verify_token()

Proactive token health check that validates token state without making API calls. Returns a structured dict:

```python
from tools.google_workspace.auth import verify_token

result = verify_token()
# {
#     "valid": True/False,
#     "status": "valid" | "missing" | "invalid" | "expired" | "scope_mismatch" | "scopes_unknown",
#     "scopes": ["https://..."] | None,
#     "expired": True/False,
#     "has_refresh_token": True/False,
# }
```

**Status values:**
- `valid` -- Token is usable (may be expired but has refresh token)
- `missing` -- No token file found
- `invalid` -- Token file exists but is corrupted or not valid JSON
- `expired` -- Token expired with no refresh token available
- `scope_mismatch` -- Token scopes do not include required Calendar scope
- `scopes_unknown` -- Token file does not contain scope information (common with older tokens)

### Error handling in get_credentials()

`get_credentials()` catches specific Google auth exceptions instead of letting raw tracebacks propagate:

- **`RefreshError`** -- Token revoked or expired beyond refresh. Raises `GoogleAuthError` with recovery command pointing to `valor-calendar --reauth`.
- **`TransportError`** -- Network error during token refresh. Raises `GoogleAuthError` with connectivity guidance.
- **`FileNotFoundError`** -- No OAuth client credentials file. Preserved from original behavior.

After a successful token refresh, the service cache (`_service_cache`) is cleared to prevent stale cached API clients from using old credentials.

### Headless environment support

When `flow.run_local_server()` fails (e.g., in a headless SSH session or launchd service), the auth flow raises a `GoogleAuthError` with instructions to run `valor-calendar --reauth` on a machine with a browser and then copy the token file to the headless machine.

## CLI Flags

### valor-calendar --check

Validates token health and prints a human-readable status. Exits with code 0 if the token is valid, 1 if not.

```bash
$ valor-calendar --check
Token status: valid
Scopes: https://www.googleapis.com/auth/calendar
```

### valor-calendar --reauth

Clears all stored tokens (per-machine and shared legacy) and re-runs the OAuth consent flow. Opens the browser for Google OAuth consent.

```bash
$ valor-calendar --reauth
Clearing stored tokens...
Starting OAuth consent flow (browser will open)...
Re-authentication successful. Token saved.
```

## Token storage

- **Per-machine token:** `~/Desktop/Valor/google_token.<machine-name>.json`
- **Legacy shared token:** `~/Desktop/Valor/google_token.json` (deleted by `clear_tokens()`)
- **OAuth client credentials:** `~/Desktop/Valor/google_credentials.json`

`clear_tokens()` removes both the per-machine token and the shared legacy token to prevent stale token re-migration when the module is re-imported.

## Related files

| File | Purpose |
|------|---------|
| `tools/google_workspace/auth.py` | Core auth module with error handling |
| `tools/valor_calendar.py` | CLI entry point with --check and --reauth flags |
| `.claude/skills/setup/SKILL.md` | Setup skill referencing --check and --reauth |
| `tests/unit/test_google_auth.py` | 29 unit tests covering all error paths |
| `config/settings.py` | GoogleAuthSettings with credentials_dir |

## Tracking

- Issue: #851
- Plan: `docs/plans/gws_oauth_flow.md`
