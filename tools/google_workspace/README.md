# Google Workspace Auth

## Overview

Internal authentication utility for Google APIs. This is **not a standalone tool** -- it provides OAuth2 credential management used by `valor_calendar` and other Google API integrations.

**Status:** Internal (not exposed as a user-facing tool)

## Usage

```python
from tools.google_workspace.auth import get_calendar_service

service = get_calendar_service()
events = service.events().list(calendarId="primary").execute()
```

## Configuration

Credentials are stored in the directory specified by `GOOGLE_CREDENTIALS_DIR` environment variable (defaults to `~/Desktop/Valor/`).

Required files:
- `google_credentials.json` -- OAuth client configuration
- `google_token.json` -- Cached OAuth token (auto-generated on first auth)

## Consumers

- `tools/valor_calendar.py` -- Calendar event management
- `scripts/update/cal_integration.py` -- Calendar sync during updates
