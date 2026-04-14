"""Tests for scripts/update/newsyslog.py — log rotation config check/install."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update import newsyslog


def _write_template(project_dir: Path) -> Path:
    cfg = project_dir / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    template = cfg / "newsyslog.conf.template"
    template.write_text("__PROJECT_DIR__/logs/bridge.error.log  644  5  10240  *  NJ\n")
    return template


class TestCheckNewsyslog:
    def test_up_to_date_when_installed_content_matches(self, tmp_path, monkeypatch):
        template = _write_template(tmp_path)
        rendered = template.read_text().replace("__PROJECT_DIR__", str(tmp_path))
        fake_dst = tmp_path / "valor.conf"
        fake_dst.write_text(rendered)
        monkeypatch.setattr(newsyslog, "NEWSYSLOG_DST", fake_dst)

        status = newsyslog.check_newsyslog(tmp_path)

        assert status.up_to_date is True
        assert status.installed is False
        assert status.needs_sudo is False
        assert status.action_message == ""

    def test_installs_via_passwordless_sudo(self, tmp_path, monkeypatch):
        _write_template(tmp_path)
        fake_dst = tmp_path / "valor.conf"  # does not exist yet
        monkeypatch.setattr(newsyslog, "NEWSYSLOG_DST", fake_dst)

        with patch.object(newsyslog.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            status = newsyslog.check_newsyslog(tmp_path)

        assert status.installed is True
        assert status.needs_sudo is False
        assert status.up_to_date is True
        # sudo -n must be used so the call fails fast instead of prompting.
        called_args = mock_run.call_args[0][0]
        assert called_args[:2] == ["sudo", "-n"]

    def test_surfaces_action_when_sudo_needs_password(self, tmp_path, monkeypatch):
        _write_template(tmp_path)
        fake_dst = tmp_path / "valor.conf"
        monkeypatch.setattr(newsyslog, "NEWSYSLOG_DST", fake_dst)

        with patch.object(newsyslog.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="a password is required")
            status = newsyslog.check_newsyslog(tmp_path)

        assert status.installed is False
        assert status.needs_sudo is True
        assert "sudo cp" in status.action_message
        assert str(fake_dst) in status.action_message
        assert "missing" in status.action_message

    def test_surfaces_action_when_sudo_raises(self, tmp_path, monkeypatch):
        _write_template(tmp_path)
        fake_dst = tmp_path / "valor.conf"
        monkeypatch.setattr(newsyslog, "NEWSYSLOG_DST", fake_dst)

        with patch.object(
            newsyslog.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd=["sudo"], timeout=10),
        ):
            status = newsyslog.check_newsyslog(tmp_path)

        assert status.needs_sudo is True
        assert status.installed is False

    def test_missing_template_is_noop(self, tmp_path):
        # No config/newsyslog.conf.template at all.
        status = newsyslog.check_newsyslog(tmp_path)
        assert status.up_to_date is True
        assert status.installed is False
        assert status.needs_sudo is False

    def test_detects_drift_when_content_differs(self, tmp_path, monkeypatch):
        _write_template(tmp_path)
        fake_dst = tmp_path / "valor.conf"
        fake_dst.write_text("OLD CONTENT\n")
        monkeypatch.setattr(newsyslog, "NEWSYSLOG_DST", fake_dst)

        with patch.object(newsyslog.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="need password")
            status = newsyslog.check_newsyslog(tmp_path)

        assert status.up_to_date is False
        assert status.needs_sudo is True
        # Reason should identify the file as out-of-date, not missing.
        assert "out-of-date" in status.action_message
