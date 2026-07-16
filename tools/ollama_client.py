"""Internal Ollama HTTP transport client.

This is the SOLE owner of Ollama HTTP transport and config resolution.
All callers delegate to this module — do not re-implement transport elsewhere.

Error contracts (intentionally split):
  generate() → returns str | None (fail-silent; None on any error OR empty output)
  chat()     → raises on failure (callers rely on exception-to-escalate behavior)

Config literals (ollama host, generation model, timeout) live in
config/settings.py ModelSettings field defaults ONLY — not here.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_config() -> tuple[str, str, float]:
    """Return (base_url, model, timeout_s) from settings or ModelSettings defaults.

    Reads from settings.models when available. On import failure, constructs
    ModelSettings() directly so Pydantic applies its field defaults. Config literals
    (ollama host, generation model name, timeout) live in config/settings.py
    ModelSettings field definitions only — not here.
    """
    try:
        from config.settings import settings

        m = settings.models
        return m.ollama_host, m.ollama_generation_model, m.memory_title_timeout_s
    except Exception:  # noqa: S110 -- documented import-failure fallback
        pass

    # Fallback: construct ModelSettings directly so Pydantic applies its field
    # defaults. This handles import-failure scenarios (test fixtures, fresh shell).
    from config.settings import ModelSettings

    m = ModelSettings()
    return m.ollama_host, m.ollama_generation_model, m.memory_title_timeout_s


def _close_client(client) -> None:
    """Eagerly close an ollama.Client's underlying httpx socket pool.

    ollama>=0.4 Client is NOT a context manager (no __enter__/__exit__), so we
    close the httpx connection pool by hand (it has no __del__). Best-effort:
    swallow any close error so it never masks the caller's result/exception.
    """
    inner = getattr(client, "_client", None)
    close = getattr(inner, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001, S110 — close is best-effort cleanup
            pass


def generate(
    prompt: str,
    *,
    model: str,
    timeout_s: float,
    base_url: str | None = None,
    caller: str | None = None,
) -> str | None:
    """Call Ollama generate endpoint. Returns stripped text or None.

    Fail-silent: returns None on any transport/parse error AND on empty/whitespace
    output (so callers' None-on-failure fallback chains fire correctly — e.g. the
    knowledge indexer's Haiku fallback triggers when Ollama returns an empty string).

    Constructs ollama.Client and closes its httpx socket pool via _close_client()
    in a finally block (the pool has no __del__; ollama>=0.4 Client is not a
    context manager, so we close sockets eagerly by hand).

    Args:
        prompt: The text prompt to send.
        model: Ollama model name.
        timeout_s: Per-request timeout in seconds (passed to httpx via Client).
        base_url: Ollama host URL. Defaults to resolve_config() host.
        caller: Optional label for DEBUG log lines (e.g. "indexer", "title_generator").
    """
    import ollama

    if base_url is None:
        base_url, _, _ = resolve_config()

    label = caller or "ollama_client"
    try:
        client = ollama.Client(host=base_url, timeout=timeout_s)
        try:
            response = client.generate(model=model, prompt=prompt, stream=False)
        finally:
            _close_client(client)
        text = response.response
        return text.strip() or None
    except Exception as e:  # noqa: BLE001 — fail-silent by contract
        logger.debug(f"[{label}] generate failed: {type(e).__name__}")
        return None


def chat(
    messages: list[dict],
    *,
    model: str,
    options: dict | None = None,
    base_url: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """POST to Ollama /api/chat. Returns the assistant message content string.

    RAISES on failure — callers rely on exception-to-escalate behavior.
    Does NOT return None; a transport/model error propagates to the caller's
    try/except block.

    Constructs ollama.Client and closes its httpx socket pool via _close_client()
    in a finally block (ollama>=0.4 Client is not a context manager).

    No timeout is passed when timeout_s is None (httpx default = no timeout),
    preserving the pre-existing behavior of the module-level ollama.chat() call
    this replaces (which also runs with no timeout).

    Args:
        messages: List of {"role": ..., "content": ...} dicts.
        model: Ollama model name.
        options: Optional Ollama options dict (e.g. {"temperature": 0}).
        base_url: Ollama host URL. Defaults to resolve_config() host.
        timeout_s: If provided, pass to httpx Client. Default None = no timeout.
    """
    import ollama

    if base_url is None:
        base_url, _, _ = resolve_config()

    client_kwargs: dict = {"host": base_url}
    if timeout_s is not None:
        client_kwargs["timeout"] = timeout_s

    client = ollama.Client(**client_kwargs)
    try:
        chat_kwargs: dict = {"model": model, "messages": messages}
        if options is not None:
            chat_kwargs["options"] = options
        response = client.chat(**chat_kwargs)
    finally:
        _close_client(client)

    return response.message.content
