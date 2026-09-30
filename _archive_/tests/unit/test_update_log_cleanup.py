"""Tests for scripts/update/log_cleanup.py — /update's log backup sweep step."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import log_rotate
from scripts.update import log_cleanup


def _write_log(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)


class TestSweepOversizedLogs:
    def test_removes_oversized_backup_and_reports_freed_bytes(self, tmp_path):
        logs_dir = tmp_path / "logs"
        huge = logs_dir / "worker.log.2"
        size = log_rotate.LOG_BACKUP_HARD_CAP + 1
        _write_log(huge, size)

        result = log_cleanup.sweep_oversized_logs(tmp_path)

        assert result.removed == [huge]
        assert result.freed_bytes == size
        assert result.warnings == []
        assert not huge.exists()

    def test_no_oversized_backups_is_clean(self, tmp_path):
        logs_dir = tmp_path / "logs"
        normal = logs_dir / "worker.log.1"
        _write_log(normal, log_rotate.LOG_MAX_SIZE + 1)

        result = log_cleanup.sweep_oversized_logs(tmp_path)

        assert result.removed == []
        assert result.freed_bytes == 0
        assert normal.exists()

    def test_missing_logs_dir_is_clean(self, tmp_path):
        result = log_cleanup.sweep_oversized_logs(tmp_path)

        assert result.removed == []
        assert result.freed_bytes == 0
        assert result.warnings == []

    def test_sweep_failure_is_captured_as_warning(self, tmp_path, monkeypatch):
        (tmp_path / "logs").mkdir()

        def boom(_logs_dir):
            raise RuntimeError("boom")

        monkeypatch.setattr(log_rotate, "sweep_oversized_backups", boom)

        result = log_cleanup.sweep_oversized_logs(tmp_path)

        assert result.removed == []
        assert any("boom" in w for w in result.warnings)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
