"""Import-chain discipline tests for the video_watch package (hardened R8).

The plan's Agent Integration invariant: the bridge must never load the heavy
pull pipeline. The grep-based Verification row R8 only proves
``bridge/enrichment.py`` contains no literal ``from tools.video_watch import``
line — it passes vacuously if importing ``tools.video_watch.constants``
transitively executes a heavy package ``__init__``. These tests assert the
invariant on the actual import chain, in a fresh interpreter per case.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Modules that must NOT be loaded by the bridge's import of the constants seam.
HEAVY_MODULES = (
    "tools.video_watch.pipeline",
    "tools.video_watch.grok",
    "tools.link_analysis",
    "PIL",
    "httpx",
)


def _loaded_heavy_after(import_stmt: str) -> list[str]:
    """Run ``import_stmt`` in a fresh interpreter; return which heavy modules loaded."""
    code = (
        f"import sys; {import_stmt}; "
        f"print(','.join(m for m in {HEAVY_MODULES!r} if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert out.returncode == 0, f"import failed: {out.stderr[-500:]}"
    loaded = out.stdout.strip()
    return [m for m in loaded.split(",") if m]


def test_constants_import_loads_no_heavy_modules():
    """`import tools.video_watch.constants` (which runs the package __init__)
    must not pull the pipeline, grok/httpx, link_analysis, or Pillow."""
    assert _loaded_heavy_after("import tools.video_watch.constants") == []


def test_reaper_import_loads_no_heavy_modules():
    """The stdlib-only reaper must stay importable without the pipeline —
    agent/session_health.py's hourly sweep relies on this."""
    assert _loaded_heavy_after("import tools.video_watch.reaper") == []


def test_bridge_enrichment_import_loads_no_heavy_modules():
    """`import bridge.enrichment` — the real bridge-side consumer of the
    constants seam — must not load the heavy pipeline at import time."""
    assert _loaded_heavy_after("import bridge.enrichment") == []


def test_lazy_public_api_still_resolves():
    """Backward-compat: the package-level names still resolve (lazily)."""
    from tools.video_watch import (
        VideoWatchError,
        detect_source,
        reap_stale_frame_dirs,
        watch_video,
    )

    assert callable(watch_video)
    assert callable(detect_source)
    assert callable(reap_stale_frame_dirs)
    assert issubclass(VideoWatchError, Exception)


def test_unknown_attribute_raises():
    import tools.video_watch as pkg

    try:
        pkg.does_not_exist
    except AttributeError as e:
        assert "does_not_exist" in str(e)
    else:
        raise AssertionError("expected AttributeError")
