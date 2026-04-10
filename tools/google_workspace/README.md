# Google Workspace Auth

## Overview

Internal authentication utility for Google APIs. This is **not a standalone tool** -- it provides OAuth2 credential management used by `valor_calendar` and other Google API integrations.

**Status:** Internal (not exposed as a user-facing tool)

## Usage

```python
from tools.google_workspace.auth import get_service, get_credentials, verify_token, GoogleAuthError

service = get_service("calendar", "v3")
events = service.events().list(calendarId="primary").execute()

# Proactive token health check (no API calls)
result = verify_token()  # {"valid": bool, "status": str, ...}

# Error handling with recovery instructions
try:
    creds = get_credentials()
except GoogleAuthError as e:
    print(f"Auth failed: {e}")  # Includes recovery command
```

## Configuration

Credentials are stored in the directory specified by `GOOGLE_CREDENTIALS_DIR` environment variable (defaults to `~/Desktop/Valor/`).

Required files:
- `google_credentials.json` -- OAuth client configuration
- `google_token.<machine-name>.json` -- Per-machine OAuth token (auto-generated on first auth)

## Consumers

- `tools/valor_calendar.py` -- Calendar event management
- `scripts/update/cal_integration.py` -- Calendar sync during updates
