"""Shared API key resolution utilities.

Handles the case where environment variables are set to empty strings
(e.g. ANTHROPIC_API_KEY="") which prevents load_dotenv from overwriting them.
Falls back to reading .env files directly.
"""

import os
from pathlib import Path

_cached_anthropic_key: str | None = None


def get_anthropic_api_key() -> str:
    """Resolve Anthropic API key from env or .env files.

    Checks os.environ first (skipping empty strings), then reads
    directly from .env files as fallback. Caches the result.
    """
    global _cached_anthropic_key
    if _cached_anthropic_key is not None:
        return _cached_anthropic_key

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        _cached_anthropic_key = key
        return key

    # os.environ may have empty string; read directly from .env files
    for env_path in [
        Path(__file__).parent.parent / ".env",
        Path.home() / "src" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        _cached_anthropic_key = val
                        return val

    _cached_anthropic_key = ""
    return ""
