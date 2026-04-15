"""Unit tests for _check_worker_health() in tools/valor_session.py.

Tests cover:
- Healthy worker (heartbeat recent)
- Stale worker (heartbeat older than threshold)
- Missing heartbeat file
- JSON output includes worker_healthy field
- Non-JSON output emits WARNING to stderr when worker is absent/stale
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on path before importing from tools/
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import (  # noqa: E402
    _WORKER_HEALTHY_THRESHOLD_S,
    _check_worker_health,
)

# ---------------------------------------------------------------------------
# _check_worker_health() unit tests
# ---------------------------------------------------------------------------


class TestCheckWorkerHealth:
    """Tests for _check_worker_health() helper."""

    def test_healthy_worker(self, tmp_path):
        """Returns (True, age_s) when heartbeat file is recent."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        # File is brand-new — age should be ~0s

        with patch("tools.valor_session._WORKER_HEARTBEAT_FILE", hb):
            healthy, age_s = _check_worker_health()

        assert healthy is True
        assert age_s is not None
        assert age_s < _WORKER_HEALTHY_THRESHOLD_S

    def test_stale_worker(self, tmp_path):
        """Returns (False, age_s) when heartbeat file is older than threshold."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        # Backdate the mtime by more than the threshold
        stale_mtime = time.time() - (_WORKER_HEALTHY_THRESHOLD_S + 60)
        os.utime(hb, (stale_mtime, stale_mtime))

        with patch("tools.valor_session._WORKER_HEARTBEAT_FILE", hb):
            healthy, age_s = _check_worker_health()

        assert healthy is False
        assert age_s is not None
        assert age_s >= _WORKER_HEALTHY_THRESHOLD_S

    def test_missing_heartbeat_file(self, tmp_path):
        """Returns (False, None) when heartbeat file does not exist."""
        hb = tmp_path / "nonexistent_last_worker_connected"

        with patch("tools.valor_session._WORKER_HEARTBEAT_FILE", hb):
            healthy, age_s = _check_worker_health()

        assert healthy is False
        assert age_s is None

    def test_does_not_raise_on_permission_error(self, tmp_path):
        """Never raises — OSError is caught silently."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        hb.chmod(0o000)

        try:
            with patch("tools.valor_session._WORKER_HEARTBEAT_FILE", hb):
                result = _check_worker_health()
            # Should return (False, None) or (True/False, int) — no exception
            assert isinstance(result, tuple)
            assert len(result) == 2
        finally:
            hb.chmod(0o644)  # restore so tmp_path cleanup works

    def test_exact_threshold_boundary(self, tmp_path):
        """Age == threshold is treated as unhealthy (strict <)."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("ok")
        boundary_mtime = time.time() - _WORKER_HEALTHY_THRESHOLD_S
        os.utime(hb, (boundary_mtime, boundary_mtime))

        with patch("tools.valor_session._WORKER_HEARTBEAT_FILE", hb):
            healthy, age_s = _check_worker_health()

        assert healthy is False
