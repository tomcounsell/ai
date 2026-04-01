"""Google Workspace OAuth authentication module.

Handles OAuth2 credential management for Google APIs.
Credentials and tokens stored in ~/Desktop/Valor/ (env: GOOGLE_CREDENTIALS_DIR).
Tokens are per-machine to avoid iCloud sync race conditions on refresh.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from config.settings import settings

logger = logging.getLogger(__name__)

# Resolve credentials directory: env var override or settings default (~/Desktop/Valor/)
_env_dir = os.getenv("GOOGLE_CREDENTIALS_DIR")
CONFIG_DIR = Path(_env_dir) if _env_dir else settings.google_auth.credentials_dir

CREDENTIALS_PATH = CONFIG_DIR / "google_credentials.json"

# Shared legacy token path (pre per-machine migration)
_SHARED_TOKEN_PATH = CONFIG_DIR / "google_token.json"


def _get_machine_name() -> str:
    """Get a filesystem-safe machine name for per-machine token files."""
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = result.stdout.strip()
            # Make filesystem-safe: lowercase, replace spaces with hyphens
            return name.lower().replace(" ", "-")
    except Exception:
        pass
    return platform.node().split(".")[0].lower()


def _get_token_path() -> Path:
    """Get per-machine token path, migrating from shared token if needed."""
    machine = _get_machine_name()
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
