"""Stale ``data/catchup-disabled`` kill switch surfaces as a WARN (issue #2473).

The operator kill switch pauses ALL message-recovery scans and, before this
feature, could sit forgotten for days (7 days in the #2473 incident) with no
alarm anywhere. `bridge.catchup.kill_switch_status()` is the single source of
truth; `tools.doctor` and `/dashboard.json` both surface it once the flag has
been set longer than `settings.timeouts.catchup_disabled_warn_hours`.

The autouse ``isolate_catchup_kill_switch`` fixture (conftest, #2552) repoints
``bridge.catchup.CATCHUP_DISABLED_FLAG`` at a per-test temp path, so touching
the flag here never leaks into live operator state.
"""

from __future__ import annotations

import os
import time

import pytest

import bridge.catchup as catchup_mod
from bridge.catchup import kill_switch_status
from config.settings import settings

pytestmark = pytest.mark.unit


def _touch_flag(age_hours: float = 0.0) -> None:
    """Create the (redirected) flag file with an mtime ``age_hours`` in the past."""
    flag = catchup_mod.CATCHUP_DISABLED_FLAG
    flag.touch()
    if age_hours:
        past = time.time() - age_hours * 3600
        os.utime(flag, (past, past))


class TestKillSwitchStatus:
    def test_absent_flag(self):
        status = kill_switch_status()
        assert status["disabled"] is False
        assert status["age_hours"] is None
        assert status["stale"] is False
        assert status["warn_hours"] == settings.timeouts.catchup_disabled_warn_hours

    def test_fresh_flag_is_disabled_but_not_stale(self):
        _touch_flag()
        status = kill_switch_status()
        assert status["disabled"] is True
        assert status["age_hours"] is not None
        assert status["age_hours"] < 1.0
        assert status["stale"] is False

    def test_flag_older_than_threshold_is_stale(self, monkeypatch):
        monkeypatch.setattr(settings.timeouts, "catchup_disabled_warn_hours", 2.0)
        _touch_flag(age_hours=3.0)
        status = kill_switch_status()
        assert status["disabled"] is True
        assert status["stale"] is True
        assert status["age_hours"] >= 2.0
        assert status["warn_hours"] == 2.0

    def test_flag_within_threshold_is_not_stale(self, monkeypatch):
        monkeypatch.setattr(settings.timeouts, "catchup_disabled_warn_hours", 48.0)
        _touch_flag(age_hours=3.0)
        status = kill_switch_status()
        assert status["disabled"] is True
        assert status["stale"] is False

    def test_never_raises_when_flag_vanishes_mid_check(self):
        # stat() on a missing path must map to the absent shape, not an exception.
        assert catchup_mod.catchup_disabled_age_hours() is None


class TestDoctorCheck:
    def test_absent_flag_passes(self):
        from tools.doctor import _check_catchup_kill_switch

        result = _check_catchup_kill_switch()
        assert result.passed is True
        assert result.category == "Services"
        assert "not set" in result.message

    def test_fresh_flag_passes_with_paused_note(self):
        from tools.doctor import _check_catchup_kill_switch

        _touch_flag()
        result = _check_catchup_kill_switch()
        assert result.passed is True
        assert "paused" in result.message

    def test_stale_flag_warns(self, monkeypatch):
        from tools.doctor import _check_catchup_kill_switch

        monkeypatch.setattr(settings.timeouts, "catchup_disabled_warn_hours", 1.0)
        _touch_flag(age_hours=2.0)
        result = _check_catchup_kill_switch()
        assert result.passed is False
        assert "WARN" in result.message
        assert result.fix is not None
        assert "rm data/catchup-disabled" in result.fix

    def test_registered_in_get_checks(self):
        from tools.doctor import _check_catchup_kill_switch, get_checks

        assert _check_catchup_kill_switch in get_checks()


class TestDashboardSurface:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from ui.app import create_app

        return TestClient(create_app())

    def test_health_block_carries_catchup_fields(self, client, monkeypatch):
        monkeypatch.setattr(settings.timeouts, "catchup_disabled_warn_hours", 1.0)
        _touch_flag(age_hours=2.0)
        health = client.get("/dashboard.json").json()["health"]
        assert health["catchup_disabled"] is True
        assert health["catchup_disabled_stale"] is True
        assert health["catchup_disabled_age_hours"] >= 1.0
        assert health["catchup_disabled_warn_hours"] == 1.0

    def test_health_block_absent_flag_defaults(self, client):
        health = client.get("/dashboard.json").json()["health"]
        assert health["catchup_disabled"] is False
        assert health["catchup_disabled_stale"] is False
        assert health["catchup_disabled_age_hours"] is None
