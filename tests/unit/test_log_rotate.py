"""Tests for scripts/log_rotate.py — user-space log rotator."""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.log_rotate as log_rotate


def _write_log(path: Path, size: int) -> None:
    """Create ``path`` with exactly ``size`` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use truncate to avoid actually writing 10+ MB to disk.
    with open(path, "wb") as fh:
        fh.truncate(size)


class TestRotateLogs:
    def test_rotates_oversized_file(self, tmp_path):
        log_file = tmp_path / "bridge.log"
        _write_log(log_file, log_rotate.LOG_MAX_SIZE + 1)

        rotated, skipped = log_rotate.rotate_logs(tmp_path)

        assert rotated == 1
        assert skipped == 0
        assert (tmp_path / "bridge.log.1").exists()
        assert log_file.exists()
        # Freshly-touched file is empty.
        assert log_file.stat().st_size == 0

    def test_skips_under_threshold_file(self, tmp_path):
        log_file = tmp_path / "bridge.log"
        _write_log(log_file, log_rotate.LOG_MAX_SIZE - 1)

        rotated, skipped = log_rotate.rotate_logs(tmp_path)

        assert rotated == 0
        assert skipped == 0
        assert not (tmp_path / "bridge.log.1").exists()

    def test_self_exclusion_applies_to_both_files(self, tmp_path):
        # The LaunchAgent routes its own stdout/stderr to these files —
        # rotating them would recreate the FD-hold problem.
        for name in ("log_rotate.log", "log_rotate_error.log"):
            _write_log(tmp_path / name, log_rotate.LOG_MAX_SIZE + 1)

        rotated, skipped = log_rotate.rotate_logs(tmp_path)

        assert rotated == 0
        assert skipped == 2
        # Original files untouched.
        for name in ("log_rotate.log", "log_rotate_error.log"):
            assert (tmp_path / name).stat().st_size == log_rotate.LOG_MAX_SIZE + 1
            assert not (tmp_path / f"{name}.1").exists()

    def test_shifts_existing_backups(self, tmp_path):
        # .1 and .2 exist already; rotation should shift them up.
        _write_log(tmp_path / "bridge.log", log_rotate.LOG_MAX_SIZE + 1)
        (tmp_path / "bridge.log.1").write_bytes(b"OLD-1")
        (tmp_path / "bridge.log.2").write_bytes(b"OLD-2")

        rotated, _ = log_rotate.rotate_logs(tmp_path)

        assert rotated == 1
        # .2 becomes .3; .1 becomes .2; current becomes .1.
        assert (tmp_path / "bridge.log.3").read_bytes() == b"OLD-2"
        assert (tmp_path / "bridge.log.2").read_bytes() == b"OLD-1"
        assert (tmp_path / "bridge.log.1").exists()

    def test_drops_oldest_backup_beyond_max(self, tmp_path):
        # .3 (oldest allowed) exists — it should be overwritten, not
        # accumulated into .4.
        _write_log(tmp_path / "bridge.log", log_rotate.LOG_MAX_SIZE + 1)
        (tmp_path / "bridge.log.1").write_bytes(b"OLD-1")
        (tmp_path / "bridge.log.2").write_bytes(b"OLD-2")
        (tmp_path / "bridge.log.3").write_bytes(b"OLD-3")

        log_rotate.rotate_logs(tmp_path)

        assert not (tmp_path / "bridge.log.4").exists()
        assert (tmp_path / "bridge.log.3").read_bytes() == b"OLD-2"

    def test_handles_missing_logs_dir(self, tmp_path):
        rotated, skipped = log_rotate.rotate_logs(tmp_path / "nonexistent")

        assert (rotated, skipped) == (0, 0)

    def test_continues_after_per_file_stat_error(self, tmp_path, monkeypatch):
        # First file: valid and oversized. Second file: stat raises.
        # The second failure must not prevent the first from rotating.
        good = tmp_path / "aaa.log"
        bad = tmp_path / "zzz.log"
        _write_log(good, log_rotate.LOG_MAX_SIZE + 1)
        bad.write_bytes(b"")

        original_stat = Path.stat

        def fake_stat(self, *args, **kwargs):
            if self.name == "zzz.log":
                raise OSError("permission denied")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)

        rotated, skipped = log_rotate.rotate_logs(tmp_path)

        assert rotated == 1
        assert (tmp_path / "aaa.log.1").exists()

    def test_main_returns_zero_even_when_rotate_raises(self, monkeypatch):
        # Belt-and-braces: if rotate_logs() itself blows up, main() must
        # still exit 0 so launchd does not throttle the agent.
        def boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(log_rotate, "rotate_logs", boom)

        assert log_rotate.main() == 0


class TestSelfExclusionConstant:
    def test_excluded_names_are_covered(self):
        # Regression: the LaunchAgent's StandardOutPath / StandardErrorPath
        # must match the self-exclusion set exactly. This test encodes the
        # contract so a rename in one place without the other is caught.
        assert "log_rotate.log" in log_rotate.SELF_EXCLUDED_FILES
        assert "log_rotate_error.log" in log_rotate.SELF_EXCLUDED_FILES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
