"""Shared mock-driver builders for the granite container-loop tests.

These helpers were previously defined inline at the top of
``tests/unit/granite_container/test_container.py`` (the ``_idle_result`` /
``_mock_driver`` / ``_mock_pm`` / ``_mock_dev`` factory set). They are the
patterns the plan's Prior Art calls out as the thing the fault-injection
harness generalizes, so they now live in one place that both the existing
tests and the new Substrate A injectors import.

Signatures and defaults are preserved BYTE-FOR-BYTE from the original
``test_container.py`` definitions so the existing tests behave identically:

    _idle_result(buffer_text="fake buffer", saw_idle=True)
        -> IdleResult(saw_idle=..., buffer=..., idle_marker="bypass permissions on",
                      elapsed_ms=100)
    _mock_driver(buffer_text="fake", saw_idle=True, session_id="mock-session-pm")
    _mock_pm(buffer_text="fake", saw_idle=True)   # session_id="mock-session-pm"
    _mock_dev(buffer_text="fake", saw_idle=True)  # session_id="mock-session-dev"

Sibling test modules (``test_granite_mid_run_steering_unit.py``,
``test_granite_startup_login_dispatch.py``, ``test_container_builder_gate.py``)
keep their OWN local copies on purpose: those use different ``elapsed_ms`` /
``idle_marker`` / ``turn_buffer`` values and different signatures, so
re-pointing them here would silently change their behavior. Only
``test_container.py`` shares this module.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agent.granite_container.pty_driver import IdleResult, PTYDriver


def _idle_result(buffer_text: str = "fake buffer", saw_idle: bool = True) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on",
        elapsed_ms=100,
    )


def _mock_driver(
    buffer_text: str = "fake", saw_idle: bool = True, session_id: str = "mock-session-pm"
) -> MagicMock:
    """Build a mock PTYDriver."""
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    # Set _session_id so _transcript_path produces a non-None value,
    # allowing last_assistant_text to be called in the container run path.
    # PM and Dev get different session IDs so stubs can discriminate.
    mock._session_id = session_id
    return mock


def _mock_pm(buffer_text: str = "fake", saw_idle: bool = True) -> MagicMock:
    """Build a mock PM PTYDriver."""
    return _mock_driver(buffer_text, saw_idle, session_id="mock-session-pm")


def _mock_dev(buffer_text: str = "fake", saw_idle: bool = True) -> MagicMock:
    """Build a mock Dev PTYDriver."""
    return _mock_driver(buffer_text, saw_idle, session_id="mock-session-dev")
