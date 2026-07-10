"""Tests for remove_obsolete_services() in scripts/update/service.py.

Covers the launchd analog of RENAMED_REMOVALS: fully-deleted features must
have their lingering LaunchAgent booted out and their plist unlinked on every
machine on the next `/update`, idempotently and fail-soft.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from scripts.update import service


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    fake_home = tmp_path / "fake_home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setattr(service.Path, "home", staticmethod(lambda: fake_home))
    return fake_home


class TestRemoveObsoleteServices:
    def test_noop_when_nothing_present(self, tmp_path, monkeypatch):
        _fake_home(tmp_path, monkeypatch)

        def fake_run_cmd(cmd, **kwargs):
            # launchctl list returns no obsolete labels.
            return MagicMock(returncode=0, stdout="com.valor.worker\n")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        assert service.remove_obsolete_services() == []

    def test_boots_out_and_unlinks_loaded_obsolete_job(self, tmp_path, monkeypatch):
        fake_home = _fake_home(tmp_path, monkeypatch)
        label = f"{service.SERVICE_PREFIX}.issue-poller"
        plist = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        plist.write_text("<plist>dead</plist>\n")

        calls: list[list[str]] = []

        def fake_run_cmd(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:2] == ["launchctl", "list"]:
                return MagicMock(returncode=0, stdout=f"{label}\n")
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        removed = service.remove_obsolete_services()

        assert removed == [label]
        # Job was booted out AND the plist deleted.
        assert any(c[:2] == ["launchctl", "bootout"] for c in calls)
        assert not plist.exists()

    def test_unlinks_plist_even_when_not_loaded(self, tmp_path, monkeypatch):
        # Plist on disk but launchctl doesn't list it (job failed to load) —
        # still must be removed so it stops trying every interval.
        fake_home = _fake_home(tmp_path, monkeypatch)
        label = f"{service.SERVICE_PREFIX}.issue-poller"
        plist = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        plist.write_text("<plist>dead</plist>\n")

        calls: list[list[str]] = []

        def fake_run_cmd(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(returncode=0, stdout="")  # list is empty

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        removed = service.remove_obsolete_services()

        assert removed == [label]
        assert not plist.exists()
        # No bootout attempted since the job wasn't loaded.
        assert not any(c[:2] == ["launchctl", "bootout"] for c in calls)

    def test_idempotent_second_run_is_noop(self, tmp_path, monkeypatch):
        _fake_home(tmp_path, monkeypatch)

        def fake_run_cmd(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        # First run: nothing on disk, nothing loaded → empty.
        assert service.remove_obsolete_services() == []
        # Second run: still empty, no error.
        assert service.remove_obsolete_services() == []

    def test_failsoft_when_unlink_raises(self, tmp_path, monkeypatch):
        fake_home = _fake_home(tmp_path, monkeypatch)
        label = f"{service.SERVICE_PREFIX}.issue-poller"
        plist = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        plist.write_text("<plist>dead</plist>\n")

        def fake_run_cmd(cmd, **kwargs):
            if cmd[:2] == ["launchctl", "list"]:
                return MagicMock(returncode=0, stdout=f"{label}\n")
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        def boom(self):
            raise OSError("permission denied")

        monkeypatch.setattr(service.Path, "unlink", boom)

        # bootout still succeeds, so the label is reported removed; the unlink
        # failure is swallowed (no raise).
        removed = service.remove_obsolete_services()
        assert removed == [label]

    def test_uses_service_prefix_for_labels(self, tmp_path, monkeypatch):
        # Downstream forks override SERVICE_PREFIX; the sweep must target the
        # prefixed label, not a hardcoded com.valor.
        fake_home = _fake_home(tmp_path, monkeypatch)
        monkeypatch.setattr(service, "SERVICE_PREFIX", "com.fork")
        label = "com.fork.issue-poller"
        plist = fake_home / "Library" / "LaunchAgents" / f"{label}.plist"
        plist.write_text("<plist>dead</plist>\n")

        def fake_run_cmd(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="")

        monkeypatch.setattr(service, "run_cmd", fake_run_cmd)

        removed = service.remove_obsolete_services()
        assert removed == [label]
        assert not plist.exists()
