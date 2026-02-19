"""API key resolution and LLM provider detection for Open Deep Research."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path


def get_api_keys() -> dict[str, str | None]:
    """Get available API keys from environment or .env files.

    Checks for ANTHROPIC_API_KEY (default provider), OPENROUTER_API_KEY,
    OPENAI_API_KEY, and TAVILY_API_KEY (web search).

    The manual .env fallback exists for CLI usage outside Django, where
    Django's settings-based env loading hasn't run.  When called from the
    Django service layer, keys are already in ``os.environ`` and the
    fallback is a no-op.
    """
    key_names = [
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
    ]
    keys: dict[str, str | None] = {name: os.getenv(name) for name in key_names}

    # Fallback for CLI usage: walk parent directories for .env files.
    missing = [k for k, v in keys.items() if not v]
    if missing:
        for parent in [Path.cwd()] + list(Path.cwd().parents)[:3]:
            for env_name in (".env.local", ".env"):
                env_file = parent / env_name
                if env_file.exists():
                    with open(env_file) as f:
                        for line in f:
                            if "=" in line and not line.startswith("#"):
                                key, value = line.split("=", 1)
                                key = key.strip()
                                value = value.strip().strip("\"'")
                                if key in missing and not keys[key] and value:
                                    keys[key] = value

    return keys


def make_logger(verbose: bool = True, log_file: str | None = None):
    """Create a logging function that writes to stdout and/or file."""

    def log(msg: str) -> None:
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    return log


def resolve_provider(keys: dict[str, str | None]) -> tuple[str, str]:
    """Determine which LLM provider to use based on available keys.

    Returns:
        Tuple of (provider_name, model_name) for open_deep_research Configuration.
    """
    if keys.get("ANTHROPIC_API_KEY"):
        return "anthropic", "claude-sonnet-4-6"
    if keys.get("OPENAI_API_KEY"):
        return "openai", "gpt-5.2"
    if keys.get("OPENROUTER_API_KEY"):
        return "openai", "deepseek/deepseek-r1"
    return "", ""


@contextlib.contextmanager
def env_for_library(keys: dict[str, str | None]) -> Iterator[None]:
    """Temporarily inject API keys into ``os.environ`` for the library.

    The ``open-deep-research`` library reads API keys from ``os.environ``
    directly (no parameter passing).  This context manager sets the
    required keys, then restores the original values on exit so we don't
    leak state into the rest of the Django process.
    """
    env_overrides: dict[str, str] = {}
    for k, v in keys.items():
        if v:
            env_overrides[k] = v

    # For OpenRouter: the library expects OPENAI_API_KEY + OPENAI_API_BASE
    if keys.get("OPENROUTER_API_KEY") and not keys.get("OPENAI_API_KEY"):
        env_overrides["OPENAI_API_KEY"] = keys["OPENROUTER_API_KEY"]
        env_overrides["OPENAI_API_BASE"] = "https://openrouter.ai/api/v1"

    originals: dict[str, str | None] = {}
    try:
        for k, v in env_overrides.items():
            originals[k] = os.environ.get(k)
            os.environ[k] = v
        yield
    finally:
        for k, orig in originals.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig
