"""Shared API key resolution utilities.

Handles the case where environment variables are set to empty strings
(e.g. ANTHROPIC_API_KEY="") which prevents load_dotenv from overwriting them.
Falls back to reading .env files directly.
"""

import os
from pathlib import Path

_cached_anthropic_key: str | None = None


def get_anthropic_api_key() -> str | None:
    """Resolve Anthropic API key from env or .env files.

    Checks os.environ first (skipping empty strings), then reads
    directly from .env files as fallback. Caches only a successfully
    resolved (truthy) key.
    """
    global _cached_anthropic_key
    # Only short-circuit on a truthy cached value. Do NOT short-circuit on a
    # falsy cached value (None/"") -- caching an empty resolution poisons
    # every subsequent call in this process for its entire lifetime if the
    # miss was caused by a transient startup race (env/.env not yet readable
    # when this ran first). See #1899.
    if _cached_anthropic_key:
        return _cached_anthropic_key

    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        _cached_anthropic_key = key
        return key

    # os.environ may have empty string; read directly from .env files
    for env_path in [
        Path(__file__).parent.parent / ".env",
        Path.home() / "src" / ".env",
        Path.home() / "Desktop" / "Valor" / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if val:
                        _cached_anthropic_key = val
                        return val

    # Absent/empty resolution: return None WITHOUT caching it. This is the
    # self-heal -- the next call re-reads env/.env instead of hitting a
    # poisoned cache, so a transient miss (e.g. LaunchAgent env sourcing not
    # yet settled at process start) clears itself on the next attempt
    # rather than persisting "no key found" for the rest of the process
    # lifetime. See #1899.
    return None
