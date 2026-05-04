"""Pillow utilities for screenshot post-processing.

Unrelated to the ``agent-browser`` 3rd-party CLI on PATH and unrelated to the
BYOB MCP surface. The only public-ish export is :func:`_downscale_if_needed`,
used by callers (e.g. ``do-design-audit``, screenshot pipelines) that need to
ensure captured screenshots fit within agent-friendly size budgets.

Browser automation in this repo is provided by:

- ``agent-browser`` (3rd-party CLI on PATH) -- anonymous, headless, single-tab.
- ``bowser`` (Playwright headless) -- anonymous, parallel.
- BYOB MCP tools (``byob_navigate``, ``byob_click``, etc.) -- real Chrome,
  logged-in. Registered in ``~/.claude.json`` ``mcpServers.byob`` by
  :mod:`scripts.update.mcp_byob`.

None of those surfaces route through this module.
"""

import logging
from io import BytesIO

# Check if Pillow is available for image downscaling
try:
    from PIL import Image

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

logger = logging.getLogger(__name__)


def _downscale_if_needed(data: bytes, max_dim: int = 1280) -> bytes:
    """Downscale image bytes so the longest edge does not exceed max_dim.

    Uses Pillow to proportionally resize the image if needed.  If Pillow is
    unavailable or any error occurs the original bytes are returned unchanged --
    this function never raises and never returns empty bytes.

    Args:
        data: Raw PNG (or other Pillow-readable) image bytes.
        max_dim: Maximum allowed length for the longest edge in pixels.

    Returns:
        PNG bytes, possibly downscaled.  Always equal to or smaller than
        max_dim on the longest edge when Pillow is available and the image
        parsed successfully.
    """
    if not PILLOW_AVAILABLE:
        logger.warning("Pillow not available; screenshot will not be downscaled")
        return data

    if not data:
        return data

    try:
        img = Image.open(BytesIO(data))
        if max(img.width, img.height) <= max_dim:
            return data  # Already within bounds -- no resize needed
        scale = max_dim / max(img.width, img.height)
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Screenshot downscale failed (%s); returning original bytes", exc)
        return data
