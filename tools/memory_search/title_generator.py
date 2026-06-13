"""Async title generator for Memory records.

Generates a compact one-line title for a Memory via the local Ollama HTTP
API and writes it back to the record. Used by the progressive-disclosure
recall path so injected `<thought>` blocks can render as
`<thought id="m1">[category] one-line title</thought>` instead of full
content (≥5× token reduction target).

Public API: ``generate_title_async(memory_id, content)`` — returns
synchronously; spawns a daemon thread that performs the LLM call out of
band. Failures (Ollama down, timeout, model error, save error) are
logged at DEBUG and never raise.

Callers MUST apply ``agent.private_tag.strip_private`` to ``content``
before invoking — the local LLM should never see content wrapped in
`<private>` segments.

Real-memory semantics: every save unconditionally re-fires title-gen
(no `if not self.title` guard at call sites). Titles evolve as new
context arrives — most recent save reflects latest understanding.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# Prompt is intentionally tight: 12-word cap, no punctuation tail, no quotes.
# The cap keeps stubs compact and the local LLM fast.
_TITLE_PROMPT_TEMPLATE = (
    "Generate a single descriptive title (max 12 words, no quotes, "
    "no period) for this memory:\n\n{content}"
)

_MAX_TITLE_CHARS = 200  # Generous bound; the prompt asks for ~12 words.


def _resolve_ollama_config() -> tuple[str, str, float]:
    """Return (base_url, model, timeout_s).

    Reads from settings if available; falls back to hardcoded defaults so
    this module is importable in environments where settings can't load
    (test fixtures, fresh-shell MCP smoke checks).
    """
    base_url = "http://localhost:11434"
    timeout_s = 5.0
    try:
        from config.settings import settings

        base_url = settings.models.ollama_host
        timeout_s = settings.models.memory_title_timeout_s
    except Exception:
        pass

    try:
        from config.settings import settings

        model = settings.models.ollama_generation_model
    except Exception:
        model = "gemma4:31b-cloud"

    return base_url, model, timeout_s


def _post_ollama_generate(base_url: str, model: str, prompt: str, timeout_s: float) -> str | None:
    """POST to Ollama /api/generate and return the response string.

    Returns None on any failure (connection, timeout, bad JSON, missing
    field). Never raises.
    """
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    url = f"{base_url.rstrip('/')}/api/generate"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.debug(f"[title_generator] Ollama unreachable/timeout: {e}")
        return None
    except Exception as e:  # noqa: BLE001 — fail-silent by contract
        logger.debug(f"[title_generator] Ollama call failed: {e}")
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.debug("[title_generator] Ollama returned non-JSON")
        return None

    response = data.get("response")
    if not isinstance(response, str):
        return None
    return response


def _normalize_title(raw: str) -> str:
    """Trim quotes, periods, and excess whitespace from the LLM response."""
    if not raw:
        return ""
    title = raw.strip()
    # Drop wrapping quotes if present
    if len(title) >= 2 and title[0] in ('"', "'") and title[-1] == title[0]:
        title = title[1:-1].strip()
    # Drop trailing period(s)
    title = title.rstrip(".").strip()
    # Collapse internal whitespace
    title = " ".join(title.split())
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS].rstrip()
    return title


def _do_generate(memory_id: str, content: str) -> None:
    """Worker body — runs in the daemon thread."""
    if not memory_id or not content:
        return

    base_url, model, timeout_s = _resolve_ollama_config()

    # Defensive <private> strip — generation is now cloud by default, so a future
    # caller that forgets strip_private would exfiltrate raw private content off
    # the machine, asynchronously and invisibly. Strip BEFORE the content[:1000]
    # truncation: an opener inside the first 1000 chars whose close falls beyond
    # char 1000 would otherwise survive a post-truncation strip into the prompt.
    from agent.private_tag import strip_private

    original = content
    content = strip_private(content)
    if content != original:
        logger.warning("title_generator: unstripped private tag — stripped defensively")
    # Unmatched-opener guard: strip_private leaves a lone <private> (no close) as
    # literal text. Aborting prevents egressing the opener + trailing secret.
    if "<private>" in content:
        logger.warning("title_generator: unmatched <private> opener — aborting")
        return

    # Typed signal: if the configured generation model is unavailable, skip
    # persistence entirely rather than persist an empty/garbage title.
    from config.models import ensure_generation_model

    gen_ok, _gen_detail = ensure_generation_model(model)
    if not gen_ok:
        logger.debug("[title_generator] generation model unavailable: %s", _gen_detail)
        return

    prompt = _TITLE_PROMPT_TEMPLATE.format(content=content[:1000])

    raw = _post_ollama_generate(base_url, model, prompt, timeout_s)
    if raw is None:
        return

    title = _normalize_title(raw)
    if not title:
        return

    try:
        from models.memory import Memory

        record = Memory.query.filter(memory_id=memory_id).first()
        if record is None:
            return
        record.title = title
        record.save()
    except Exception as e:  # noqa: BLE001 — fail-silent by contract
        logger.debug(f"[title_generator] save failed for {memory_id}: {e}")


def generate_title_async(memory_id: str, content: str) -> None:
    """Spawn a daemon thread to generate and persist a title for a memory.

    Fire-and-forget: returns immediately. Failures inside the worker are
    swallowed at DEBUG level — the writer path never blocks and never
    crashes if Ollama is unreachable.

    Args:
        memory_id: The Memory.memory_id of the record to title.
        content: The memory content. Callers MUST apply
            `agent.private_tag.strip_private(content)` before invoking
            so wrapped private regions never reach the local LLM.
    """
    if not memory_id or not content:
        return

    try:
        thread = threading.Thread(
            target=_do_generate,
            args=(memory_id, content),
            name=f"memory-title-gen-{memory_id[:12]}",
            daemon=True,
        )
        thread.start()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[title_generator] thread spawn failed: {e}")
