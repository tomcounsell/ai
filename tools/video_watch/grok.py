"""xAI (Grok) client for X/Twitter-native context.

For an x.com / twitter.com status URL, Grok has first-party access to the X
corpus, so it can return post context (author, text, thread) and describe an
attached video that an anti-bot HTML fetch cannot. This doubles as the
media-understanding *fallback* when ``yt-dlp`` fails to pull an X clip.

Grok is deliberately NOT used for frame vision: extracted frames go to the
agent (Claude) via ``Read``. Grok's role is X-native grounding + fallback only.

Reads ``GROK_API_KEY`` directly via ``os.getenv`` — there is intentionally no
``config.settings`` field, because the settings sub-model's
``env_nested_delimiter="__"`` would bind a field to ``API__GROK_API_KEY`` rather
than the provisioned plain ``GROK_API_KEY`` (mirrors how ``link_analysis`` reads
``OPENAI_API_KEY`` directly).
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

# xAI is OpenAI-compatible. Provisional/tunable: base URL and model are pinned
# here rather than in a settings field; grain of salt, adjust if xAI's contract
# or the recommended vision-capable model changes.
XAI_BASE_URL = os.getenv("GROK_API_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.getenv("GROK_MODEL", "grok-2-latest")

# Provisional/tunable: HTTP timeout for the single Grok call. Grain of salt.
GROK_TIMEOUT_SECONDS = float(os.getenv("GROK_TIMEOUT_SECONDS", "60"))


def fetch_x_context(url: str, question: str | None = None) -> str | None:
    """Fetch X-native context (post text/author/thread + video description) for an X URL.

    Non-fatal by contract: returns ``None`` and logs a warning when the key is
    absent or the call fails. Never raises to the caller — the watch pipeline
    treats a ``None`` here as "Grok context unavailable, degrade gracefully".

    Args:
        url: An x.com / twitter.com status URL.
        question: Optional human framing to steer the description; informs
            nothing beyond the prompt text.

    Returns:
        A context string, or ``None`` if unavailable.
    """
    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        logger.warning("No GROK_API_KEY set — skipping X-native Grok context for %s", url)
        return None

    ask = question.strip() if question and question.strip() else None
    prompt = (
        "You have first-party access to the X (Twitter) corpus. For the X post at "
        f"{url}, report: the author (handle + display name), the full post text, "
        "any thread/reply context, and — if the post has an attached video — a "
        "concise description of what the video shows (on-screen content, not just "
        "audio). Be factual and terse. If you cannot access the post, say so plainly."
    )
    if ask:
        prompt += f"\n\nThe requester specifically wants to know: {ask}"

    payload = {
        "model": XAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=GROK_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{XAI_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
        if response.status_code != 200:
            logger.warning(
                "Grok API error %s for %s: %s",
                response.status_code,
                url,
                response.text[:300],
            )
            return None
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            logger.warning("Grok returned no choices for %s", url)
            return None
        content = (choices[0].get("message") or {}).get("content") or ""
        content = content.strip()
        return content or None
    except Exception as e:  # noqa: BLE001 -- non-fatal by contract; degrade to None
        logger.warning("Grok X-context call failed for %s: %s", url, e)
        return None
