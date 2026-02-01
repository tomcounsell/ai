"""Google Workspace OAuth authentication module.

Handles OAuth2 credential management for Google APIs.
Token stored at ~/Desktop/claude_code/google_token.json
Credentials from ~/Desktop/claude_code/google_credentials.json
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

CONFIG_DIR = Path.home() / "Desktop" / "claude_code"
CREDENTIALS_PATH = CONFIG_DIR / "google_credentials.json"
TOKEN_PATH = CONFIG_DIR / "google_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def get_credentials() -> Credentials:
    """Load or refresh OAuth credentials. Opens browser consent on first run."""
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    elif not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Google credentials not found at {CREDENTIALS_PATH}. "
                "Download OAuth client credentials from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return creds


_service_cache: dict[tuple[str, str], Resource] = {}


def get_service(api: str, version: str) -> Resource:
    """Build and cache a Google API service client."""
    key = (api, version)
    if key not in _service_cache:
        creds = get_credentials()
        _service_cache[key] = build(api, version, credentials=creds)
    return _service_cache[key]


def clear_tokens() -> None:
    """Delete stored OAuth token."""
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    _service_cache.clear()
