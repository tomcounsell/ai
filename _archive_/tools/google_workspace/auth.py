"""Google Workspace OAuth authentication module.

Handles OAuth2 credential management for Google APIs.
Credentials and tokens stored in ~/Desktop/Valor/ (env: GOOGLE_CREDENTIALS_DIR).
Tokens are per-machine to avoid iCloud sync race conditions on refresh.

TOKEN_PATH is computed once at import time via _get_token_path() and remains
stable for the lifetime of the process. This is intentional -- per-machine
token paths do not change within a single process.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httplib2
import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from requests.adapters import HTTPAdapter

from config.machine import get_machine_slug
from config.settings import settings

logger = logging.getLogger(__name__)

# Hard ceiling on every Google HTTP round-trip (API calls AND token refresh).
# Without this, the httplib2 transport used by googleapiclient inherits the
# process socket default (None = infinite) and the requests transport used for
# token refresh defaults to 120s -- both far too long for a hook running on a
# 15s budget. Overridable via env so short-lived callers (the calendar hook)
# can tighten it further.
_HTTP_TIMEOUT_SECONDS = float(os.getenv("GWS_HTTP_TIMEOUT", "30"))


class _TimeoutHTTPAdapter(HTTPAdapter):
    """requests adapter that applies a default per-request timeout.

    requests Sessions have no native default timeout; this enforces one so the
    token-refresh path (google.auth.transport.requests.Request) cannot hang
    longer than _HTTP_TIMEOUT_SECONDS.
    """

    def __init__(self, *args, timeout: float, **kwargs):
        self._timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self._timeout
        return super().send(request, **kwargs)


def _timed_request() -> Request:
    """Build a google.auth Request whose underlying session enforces a timeout."""
    session = requests.Session()
    adapter = _TimeoutHTTPAdapter(timeout=_HTTP_TIMEOUT_SECONDS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return Request(session=session)


# Resolve credentials directory: env var override or settings default (~/Desktop/Valor/)
_env_dir = os.getenv("GOOGLE_CREDENTIALS_DIR")
CONFIG_DIR = Path(_env_dir) if _env_dir else settings.google_auth.credentials_dir

CREDENTIALS_PATH = CONFIG_DIR / "google_credentials.json"

# Shared legacy token path (pre per-machine migration)
_SHARED_TOKEN_PATH = CONFIG_DIR / "google_token.json"


class GoogleAuthError(Exception):
    """Raised when Google OAuth operations fail with actionable recovery instructions.

    The string representation includes the recovery command so that callers
    using f"...{e}" patterns surface it correctly without needing to access
    the recovery_command attribute directly.
    """

    def __init__(self, message: str, recovery_command: str | None = None):
        self.recovery_command = recovery_command
        if recovery_command:
            full_message = f"{message}\nRecovery: {recovery_command}"
        else:
            full_message = message
        super().__init__(full_message)


def _get_token_path() -> Path:
    """Get per-machine token path, migrating from shared token if needed."""
    machine = get_machine_slug()
    per_machine_path = CONFIG_DIR / f"google_token.{machine}.json"

    # If per-machine token exists, use it
    if per_machine_path.exists():
        return per_machine_path

    # Migrate: copy shared token to per-machine path (don't delete shared)
    if _SHARED_TOKEN_PATH.exists():
        try:
            per_machine_path.write_text(_SHARED_TOKEN_PATH.read_text())
            logger.info(f"Migrated Google token to per-machine: {per_machine_path.name}")
        except Exception as e:
            logger.warning(f"Token migration failed: {e}")
            return _SHARED_TOKEN_PATH

    return per_machine_path


TOKEN_PATH = _get_token_path()

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def verify_token() -> dict:
    """Check token health without making API calls.

    Returns a structured dict with:
        valid (bool): Whether the token is usable
        status (str): One of "valid", "missing", "invalid",
            "expired", "scope_mismatch", "scopes_unknown"
        scopes (list | None): Scopes from the token, or None if unavailable
        expired (bool): Whether the token is expired
        has_refresh_token (bool): Whether a refresh token is present
    """
    result = {
        "valid": False,
        "status": "missing",
        "scopes": None,
        "expired": False,
        "has_refresh_token": False,
    }

    if not TOKEN_PATH.exists():
        return result

    # Check if token file is valid JSON
    try:
        token_data = json.loads(TOKEN_PATH.read_text())
        if not isinstance(token_data, dict):
            result["status"] = "invalid"
            return result
    except (json.JSONDecodeError, OSError):
        result["status"] = "invalid"
        return result

    # Load credentials from token file
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception:
        result["status"] = "invalid"
        return result

    result["has_refresh_token"] = bool(creds.refresh_token)
    result["expired"] = bool(creds.expired)

    # Check scopes -- token files don't always contain granted scopes
    if creds.scopes is not None:
        result["scopes"] = list(creds.scopes)
        # Check if required scopes are present
        required = set(SCOPES)
        granted = set(creds.scopes)
        if not required.issubset(granted):
            result["status"] = "scope_mismatch"
            return result
    else:
        # Scopes unknown -- can't verify, but not necessarily a mismatch
        result["scopes"] = None

    # Check validity
    if creds.expired:
        if creds.refresh_token:
            # Expired but has refresh token -- can be refreshed
            result["status"] = "valid"
            result["valid"] = True
        else:
            result["status"] = "expired"
    elif creds.valid:
        result["status"] = "valid"
        result["valid"] = True
    else:
        # Not expired but not valid -- scopes unknown, recommend reauth
        if creds.scopes is None:
            result["status"] = "scopes_unknown"
        else:
            result["status"] = "expired"

    return result


def get_credentials() -> Credentials:
    """Load or refresh OAuth credentials. Opens browser consent on first run.

    Raises:
        GoogleAuthError: On token refresh failure (revoked, network error) with
            recovery instructions pointing to `valor-calendar --reauth`.
        FileNotFoundError: When no OAuth client credentials file exists.
    """
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(_timed_request())
        except RefreshError as e:
            raise GoogleAuthError(
                "Token revoked or expired. Re-authentication required.",
                recovery_command="valor-calendar --reauth",
            ) from e
        except TransportError as e:
            raise GoogleAuthError(
                "Network error during token refresh. Check connectivity and retry.",
            ) from e
        TOKEN_PATH.write_text(creds.to_json())
        # Invalidate cached services -- they hold stale credentials after refresh
        _service_cache.clear()
    elif not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Google credentials not found at {CREDENTIALS_PATH}. "
                "Download OAuth client credentials from Google Cloud Console."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        # Try local server; headless environments must run OAuth on a machine with a browser
        try:
            creds = flow.run_local_server(port=0)
        except OSError as e:
            raise GoogleAuthError(
                "Cannot open browser for OAuth consent (headless environment). "
                "Run `valor-calendar --reauth` on a machine with a browser, "
                "then copy the token file to this machine.",
            ) from e
        TOKEN_PATH.write_text(creds.to_json())

    return creds


_service_cache: dict[tuple[str, str], Resource] = {}


def get_service(api: str, version: str) -> Resource:
    """Build and cache a Google API service client.

    The service is built on an httplib2 transport with an explicit timeout so
    no API call can hang indefinitely. AuthorizedHttp also carries the
    credentials and performs any lazy refresh over the same bounded transport.
    """
    key = (api, version)
    if key not in _service_cache:
        creds = get_credentials()
        authed_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS))
        _service_cache[key] = build(api, version, http=authed_http)
    return _service_cache[key]


def clear_tokens() -> None:
    """Delete stored OAuth tokens (per-machine and shared legacy).

    Also clears the service cache to prevent stale credentials from persisting.
    Deletes _SHARED_TOKEN_PATH to prevent stale token re-migration on restart.
    """
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    if _SHARED_TOKEN_PATH.exists():
        _SHARED_TOKEN_PATH.unlink()
        logger.info("Removed shared legacy token to prevent re-migration")
    _service_cache.clear()
