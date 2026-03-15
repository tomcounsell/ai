"""MiroFish Swarm Intelligence API - HTTP client wrapper.

Communicates with a MiroFish backend service (Flask/FastAPI) to run
multi-agent swarm simulations.  The service produces perspective-oriented
outputs: stakeholder reaction modeling, prediction generation,
counter-argument stress-testing, and audience reception simulation.

MiroFish runs as a separate sidecar service (Docker or local process),
**not** as an embedded Python library.  This module is a thin HTTP client.

Configuration:
    MIROFISH_API_URL  - Base URL of the MiroFish backend (e.g. http://localhost:5001)

Usage as library::

    from apps.podcast.tools.mirofish_research import run_mirofish_simulation

    content, metadata = run_mirofish_simulation(prompt="Your simulation query")

"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

# Default timeout for MiroFish simulations (10 minutes).
# Swarm simulations can take 2-10 minutes depending on agent count and LLM
# provider latency.
DEFAULT_TIMEOUT = 600

# Default MiroFish backend URL
DEFAULT_API_URL = "http://localhost:5001"


def get_api_url() -> str:
    """Return the MiroFish API base URL from environment or default."""
    return os.getenv("MIROFISH_API_URL", DEFAULT_API_URL)


def check_health(api_url: str | None = None, timeout: int = 10) -> bool:
    """Check if the MiroFish service is reachable.

    Args:
        api_url: Base URL of the MiroFish backend.  Falls back to
            ``MIROFISH_API_URL`` env var or ``http://localhost:5001``.
        timeout: HTTP timeout in seconds for the health check.

    Returns:
        ``True`` if the service responds to the health endpoint, ``False``
        otherwise.
    """
    url = (api_url or get_api_url()).rstrip("/")
    try:
        response = httpx.get(f"{url}/health", timeout=timeout)
        return response.status_code == 200
    except (httpx.HTTPError, httpx.TimeoutException, OSError):
        return False


def run_mirofish_simulation(
    prompt: str,
    *,
    api_url: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> tuple[str | None, dict]:
    """Send a simulation request to the MiroFish backend API.

    Constructs a prediction/simulation request and sends it to the MiroFish
    Flask backend.  The prompt should emphasise perspective simulation --
    stakeholder reactions, predictions, counter-arguments -- rather than
    factual web search (which other research tools already cover).

    Args:
        prompt: The simulation query, typically assembled from episode
            context by the research service layer.
        api_url: Base URL of the MiroFish backend.  Falls back to
            ``MIROFISH_API_URL`` env var or ``http://localhost:5001``.
        timeout: HTTP timeout in seconds.  Default is 600 (10 minutes)
            to accommodate long-running swarm simulations.
        verbose: If ``True``, print progress messages to stdout.

    Returns:
        A ``(content_text, metadata)`` tuple.  ``content_text`` is the
        simulation report as a string, or ``None`` on failure.
        ``metadata`` contains structured information about the simulation
        (agent interactions, predictions, timing).
    """
    url = (api_url or get_api_url()).rstrip("/")

    if verbose:
        logger.info("MiroFish: sending simulation request to %s", url)

    payload = {
        "prompt": prompt,
        # Request a comprehensive prediction report
        "mode": "predict",
    }

    try:
        response = httpx.post(
            f"{url}/api/predict",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning(
            "MiroFish simulation timed out after %d seconds. "
            "The simulation may be too complex or the service overloaded.",
            timeout,
        )
        return None, {"error": "timeout", "timeout_seconds": timeout}
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "MiroFish API returned HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:500] if exc.response.text else "empty",
        )
        return None, {
            "error": "http_error",
            "status_code": exc.response.status_code,
        }
    except (httpx.HTTPError, OSError) as exc:
        logger.warning(
            "MiroFish connection failed: %s. Is the MiroFish service running at %s?",
            exc,
            url,
        )
        return None, {"error": "connection_error", "detail": str(exc)}

    # Parse response JSON
    try:
        data = response.json()
    except Exception:
        logger.warning(
            "MiroFish returned non-JSON response: %s",
            response.text[:500] if response.text else "empty",
        )
        return None, {"error": "parse_error", "raw_response": response.text[:1000]}

    # Extract the report content from the response.
    # MiroFish may return content under various keys depending on the
    # endpoint version.  We check the most likely keys in order.
    content_text = (
        data.get("report")
        or data.get("prediction")
        or data.get("result")
        or data.get("content")
        or data.get("output")
    )

    if isinstance(content_text, dict):
        # If the content is nested, try to extract a text field
        content_text = content_text.get("text") or content_text.get("report")

    metadata = {
        "timestamp": datetime.now().isoformat(),
        "api_url": url,
        "source": "mirofish",
    }

    # Extract structured metadata from the response
    if "agents" in data:
        metadata["agents"] = data["agents"]
    if "predictions" in data:
        metadata["predictions"] = data["predictions"]
    if "dialogues" in data or "dialogue" in data:
        metadata["dialogues"] = data.get("dialogues") or data.get("dialogue")
    if "key_findings" in data:
        metadata["key_findings"] = data["key_findings"]
    if "confidence" in data:
        metadata["confidence"] = data["confidence"]
    if "duration" in data:
        metadata["duration_seconds"] = data["duration"]

    if verbose and content_text:
        word_count = len(content_text.split())
        logger.info("MiroFish: received report (~%d words)", word_count)

    return content_text, metadata
