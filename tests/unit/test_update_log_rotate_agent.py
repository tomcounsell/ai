"""Tests for install_log_rotate_agent() and remove_newsyslog_config() in scripts/update/service.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update import service


def _write_plist(project_dir: Path) -> Path:
    plist = project_dir / "com.valor.log-rotate.plist"
    plist.write_text(
        "<?xml version=\"1.0\"?>\n"
        "<plist><dict><key>Label</key><string>__SERVICE_LABEL__</string>"
        "<key>Path</key><string>__PROJECT_DIR__</string>"
        "<key>Home</key><string>__HOME_DIR__</string></dict></plist>\n"
    )
    return plist


class TestInstallLogRotateAgent:
    def test_returns_false_when_plist_template_missing(self, tmp_path):
        # No com.valor.log-rotate.plist in project_dir.
        assert service.install_log_rotate_agent(tmp_path) is False

    def test_installs_when_plist_not_yet_on_disk(self, tmp_path, monkeypatch):
        _write_plist(tmp_path)
        fake_home = tmp_path / "fake_home"
        (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
        monkeypatch.setattr(service.Path, "home", staticmethod(lambda: fake_home))

        launchctl_calls: list[list[str]] = []

        def fake_run_cmd(cmd, **kwargs):
            launchctl_calls.append(cmd)
            if cmd[:2] == ["launchctl", "list"]:
                # After bootstrap, the label appears.
                if len(launchctl_calls) > 1:
                    return MagicMock(
                        returncode=0,
                        stdout=f"{service.SERVICE_PREFIX}.log-rotate\n",
                    )
                return MagicMock(returncode=0, stdout="")
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        result = service.install_log_rotate_agent(tmp_path)

        assert result is True
        installed_plist = fake_home / "Library" / "LaunchAgents" / f"{service.SERVICE_PREFIX}.log-rotate.plist"
        assert installed_plist.exists()
        # Template substitution should have happened.
        text = installed_plist.read_text()
        assert "__PROJECT_DIR__" not in text
        assert "__HOME_DIR__" not in text
        assert "__SERVICE_LABEL__" not in text
        assert str(tmp_path) in text
        # bootstrap was called (not just bootout).
        assert any(c[:2] == ["launchctl", "bootstrap"] for c in launchctl_calls)

    def test_is_content_idempotent_on_second_run(self, tmp_path, monkeypatch):
        _write_plist(tmp_path)
        fake_home = tmp_path / "fake_home"
        (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
        monkeypatch.setattr(service.Path, "home", staticmethod(lambda: fake_home))

        # Pre-populate the installed plist with the exact rendered content
        # that install_log_rotate_agent() would produce.
        label = f"{service.SERVICE_PREFIX}.log-rotate"
        installed = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        rendered = (tmp_path / "com.valor.log-rotate.plist").read_text()
        rendered = rendered.replace("__PROJECT_DIR__", str(tmp_path))
        rendered = rendered.replace("__HOME_DIR__", str(fake_home))
        rendered = rendered.replace("__SERVICE_LABEL__", label)
        installed.write_text(rendered)

        launchctl_calls: list[list[str]] = []

        def fake_run_cmd(cmd, **kwargs):
            launchctl_calls.append(cmd)
            if cmd[:2] == ["launchctl", "list"]:
                return MagicMock(returncode=0, stdout=f"{label}\n")
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        result = service.install_log_rotate_agent(tmp_path)

        assert result is True
        # Critical: no bootout/bootstrap when nothing changed. The only
        # launchctl call should be the `list` probe.
        assert launchctl_calls == [["launchctl", "list"]]

    def test_reinstalls_when_content_differs(self, tmp_path, monkeypatch):
        _write_plist(tmp_path)
        fake_home = tmp_path / "fake_home"
        (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
        monkeypatch.setattr(service.Path, "home", staticmethod(lambda: fake_home))

        # Existing installed plist has different content.
        label = f"{service.SERVICE_PREFIX}.log-rotate"
        installed = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        installed.write_text("<?xml version=\"1.0\"?>\n<plist>OLD</plist>\n")

        launchctl_calls: list[list[str]] = []

        def fake_run_cmd(cmd, **kwargs):
            launchctl_calls.append(cmd)
            if cmd[:2] == ["launchctl", "list"]:
                return MagicMock(returncode=0, stdout=f"{label}\n")
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        result = service.install_log_rotate_agent(tmp_path)

        assert result is True
        # bootout + bootstrap both happened because content differed.
        assert any(c[:2] == ["launchctl", "bootout"] for c in launchctl_calls)
        assert any(c[:2] == ["launchctl", "bootstrap"] for c in launchctl_calls)


class TestRemoveNewsyslogConfig:
    def test_returns_true_when_file_absent(self):
        # /etc/newsyslog.d/valor.conf does not exist (common on fresh
        # machines and after first successful cleanup).
        with patch.object(Path, "exists", return_value=False):
            assert service.remove_newsyslog_config() is True

    def test_uses_sudo_dash_n_and_never_prompts(self, monkeypatch):
        captured: dict = {}

        def fake_run_cmd(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["timeout"] = kwargs.get("timeout")
            return MagicMock(returncode=1, stderr="a password is required")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        # Pretend the file exists so we take the sudo path.
        with patch.object(Path, "exists", return_value=True):
            result = service.remove_newsyslog_config()

        assert result is False
        assert captured["cmd"][:3] == ["sudo", "-n", "rm"]
        # Timeout should be tight so a stuck sudo doesn't hang updates.
        assert captured["timeout"] == 5

    def test_returns_true_when_sudo_succeeds(self, monkeypatch):
        exists_calls = [True, False]  # first: file present → try sudo; second: gone after rm

        def fake_exists(self):
            return exists_calls.pop(0) if exists_calls else False

        def fake_run_cmd(cmd, **kwargs):
            return MagicMock(returncode=0, stderr="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)
        monkeypatch.setattr(Path, "exists", fake_exists)

        assert service.remove_newsyslog_config() is True

    def test_handles_subprocess_exception(self, monkeypatch):
        def fake_run_cmd(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)
        with patch.object(Path, "exists", return_value=True):
            assert service.remove_newsyslog_config() is False
