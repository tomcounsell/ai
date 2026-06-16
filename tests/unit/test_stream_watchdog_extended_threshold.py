"""Tests for PM session extended watchdog threshold in long-running stages (issue #1394)."""

import pytest


class FakeSession:
    """Minimal mock of AgentSession for threshold tests."""

    def __init__(self, is_pm: bool = False, current_stage: str | None = None):
        self.session_type = "pm" if is_pm else "dev"
        self._current_stage = current_stage
        self.status = "active"

    @property
    def is_pm(self) -> bool:
        return self.session_type == "pm"

    @property
    def current_stage(self) -> str | None:
        return self._current_stage


def get_effective_threshold(session, base_threshold: int = 600) -> int:
    """Mirror the threshold-selection logic from monitoring/session_watchdog.py.

    For active PM sessions in long-running stages (TEST, DEPLOY), the threshold
    is extended to 3600s. All other active sessions use the base threshold (600s).
    """
    if getattr(session, "is_pm", False) and getattr(session, "current_stage", None) in (
        "TEST",
        "DEPLOY",
    ):
        return 3600
    return base_threshold


class TestExtendedThresholdLogic:
    """Unit tests for the per-stage PM threshold override."""

    def test_pm_session_test_stage_uses_3600(self):
        session = FakeSession(is_pm=True, current_stage="TEST")
        assert get_effective_threshold(session) == 3600

    def test_pm_session_deploy_stage_uses_3600(self):
        session = FakeSession(is_pm=True, current_stage="DEPLOY")
        assert get_effective_threshold(session) == 3600

    def test_pm_session_build_stage_uses_base(self):
        session = FakeSession(is_pm=True, current_stage="BUILD")
        assert get_effective_threshold(session) == 600

    def test_pm_session_unknown_stage_uses_base(self):
        session = FakeSession(is_pm=True, current_stage="UNKNOWN_STAGE")
        assert get_effective_threshold(session) == 600

    def test_pm_session_no_stage_uses_base(self):
        session = FakeSession(is_pm=True, current_stage=None)
        assert get_effective_threshold(session) == 600

    def test_dev_session_test_stage_uses_base(self):
        """Non-PM sessions in TEST stage still use base threshold."""
        session = FakeSession(is_pm=False, current_stage="TEST")
        assert get_effective_threshold(session) == 600

    def test_dev_session_deploy_stage_uses_base(self):
        """Non-PM sessions in DEPLOY stage still use base threshold."""
        session = FakeSession(is_pm=False, current_stage="DEPLOY")
        assert get_effective_threshold(session) == 600

    def test_custom_base_threshold_respected(self):
        """Custom base threshold is respected for non-extended sessions."""
        session = FakeSession(is_pm=False, current_stage="TEST")
        assert get_effective_threshold(session, base_threshold=300) == 300

    def test_custom_base_threshold_ignored_for_extended_pm(self):
        """Extended PM sessions always use 3600, ignoring the base threshold."""
        session = FakeSession(is_pm=True, current_stage="TEST")
        assert get_effective_threshold(session, base_threshold=300) == 3600

    @pytest.mark.parametrize(
        "stage,expected",
        [
            ("TEST", 3600),
            ("DEPLOY", 3600),
            ("BUILD", 600),
            ("PLAN", 600),
            ("CRITIQUE", 600),
            ("REVIEW", 600),
            ("DOCS", 600),
            ("MERGE", 600),
            (None, 600),
        ],
    )
    def test_pm_stage_threshold_matrix(self, stage, expected):
        session = FakeSession(is_pm=True, current_stage=stage)
        assert get_effective_threshold(session) == expected


class TestWatchdogModuleConstants:
    """Verify that the session_watchdog module exposes the correct threshold constants."""

    def test_stall_threshold_active_default(self):
        """STALL_THRESHOLD_ACTIVE defaults to 600 (no STALL_TIMEOUT_SECONDS override)."""
        import os

        # Ensure env var is not set so we get the default
        os.environ.pop("STALL_TIMEOUT_SECONDS", None)
        import importlib

        import monitoring.session_watchdog as sw

        importlib.reload(sw)
        assert sw.STALL_THRESHOLD_ACTIVE == 600

    def test_pm_extended_threshold_constant(self):
        """Verify the extended threshold constant exists in the watchdog module."""
        import monitoring.session_watchdog as sw

        assert hasattr(sw, "STALL_THRESHOLD_PM_LONG_STAGE")
        assert sw.STALL_THRESHOLD_PM_LONG_STAGE == 3600

    def test_pm_long_stages_set(self):
        """Verify PM_LONG_STAGES contains TEST and DEPLOY."""
        import monitoring.session_watchdog as sw

        assert hasattr(sw, "PM_LONG_STAGES")
        assert "TEST" in sw.PM_LONG_STAGES
        assert "DEPLOY" in sw.PM_LONG_STAGES
