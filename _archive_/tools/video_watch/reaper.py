"""Age-based reaper for persistent ``video_watch_frames_*`` temp dirs.

``watch_video()`` persists emitted frames to a ``tempfile.mkdtemp`` dir that
deliberately OUTLIVES the call (the agent ``Read``s the JPEGs in a later tool
invocation), so nothing removes those dirs automatically. This module bounds
the resulting disk leak.

Deliberately stdlib-only (plus the os-only ``constants`` module) so callers
outside the pipeline — the CLI at start, and the hourly
``agent-session-cleanup`` reflection in ``agent/session_health.py`` — can
import and run the sweep without loading the heavy pull pipeline
(``tools/video_watch/pipeline.py``).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

from tools.video_watch.constants import VIDEO_WATCH_FRAME_DIR_MAX_AGE

logger = logging.getLogger(__name__)


def reap_stale_frame_dirs(max_age_seconds: int | None = None) -> int:
    """Remove ``video_watch_frames_*`` temp dirs older than ``max_age_seconds``.

    Best-effort: a failure removing one dir is logged and does not stop the
    sweep.

    Args:
        max_age_seconds: Age threshold in seconds. Defaults to
            ``VIDEO_WATCH_FRAME_DIR_MAX_AGE`` (24h) when ``None``.

    Returns:
        Count of directories removed.
    """
    if max_age_seconds is None:
        max_age_seconds = VIDEO_WATCH_FRAME_DIR_MAX_AGE

    removed = 0
    now = time.time()
    base = Path(tempfile.gettempdir())
    for entry in base.glob("video_watch_frames_*"):
        try:
            if not entry.is_dir():
                continue
            age = now - entry.stat().st_mtime
            if age > max_age_seconds:
                shutil.rmtree(entry, ignore_errors=False)
                removed += 1
        except OSError as e:
            logger.warning("Failed to reap stale frame dir %s: %s", entry, e)
    return removed
