"""Operator-visible validation for the wired granite wedge signals (#1843).

Builder note #3 requires one validation that a synthetic wedged session
advances an operator-visible ``/dashboard.json`` field — not merely an internal
"field is populated" assertion on the raw model.

Gap A makes the CLI PreToolUse hook stamp ``current_tool_name`` /
``last_tool_use_at`` on the sidecar-resolved AgentSession for granite PM/Dev
PTY children (where ``AGENT_SESSION_ID`` is unset). Both fields are already
emitted per-session by ``/dashboard.json`` (via ``ui.data.sdlc._session_to_pipeline``).
This test drives the real Gap A CLI-hook path against a synthetic granite
session and asserts the values surface through the dashboard serializer — the
signal an operator sees when a granite session wedges inside a single tool call.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.agent_session import AgentSession, SessionType

_HOOKS_DIR = str(Path(__file__).resolve().parents[3] / ".claude" / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import pre_tool_use  # noqa: E402

_PROJECT_KEY = "test-1843-gapA-dashboard"


@pytest.fixture
def granite_session():
    """A granite-shaped AgentSession plus a CLI-hook sidecar pointing at it.

    ``AGENT_SESSION_ID`` is intentionally unset — the granite child env never
    carries it, which is why Gap A resolves via the sidecar.
    """
    cli_session_id = f"gapA-dash-{id(object())}"
    session = AgentSession.create(
        project_key=_PROJECT_KEY,
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"granite-gapA-dash-{cli_session_id}",
        working_dir="/tmp",
        status="running",
    )
    sidecar_dir = pre_tool_use._REPO_ROOT / "data" / "sessions" / cli_session_id
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "agent_session.json").write_text(
        json.dumps({"agent_session_id": session.agent_session_id})
    )
    yield SimpleNamespace(session=session, cli_session_id=cli_session_id, sidecar_dir=sidecar_dir)
    try:
        session.delete()
    except Exception:
        pass
    for name in ("agent_session.json", "tool_liveness_cooldown"):
        (sidecar_dir / name).unlink(missing_ok=True)
    try:
        sidecar_dir.rmdir()
    except OSError:
        pass


def _reload(session_id: str) -> AgentSession:
    matches = list(AgentSession.query.filter(session_id=session_id))
    assert len(matches) == 1
    return matches[0]


def test_gap_a_signal_surfaces_on_dashboard_serializer(granite_session):
    from ui.data.sdlc import _session_to_pipeline

    # Baseline: a fresh granite session shows no in-flight tool on the dashboard.
    baseline = _session_to_pipeline(granite_session.session)
    assert baseline.current_tool_name is None
    assert baseline.last_tool_use_at is None

    # Drive the real Gap A CLI-hook path (the granite PTY child's PreToolUse).
    pre_tool_use._record_tool_start(
        {"session_id": granite_session.cli_session_id, "tool_name": "Bash"}
    )

    # The dashboard.json serializer now reports the in-flight tool + a fresh
    # (float epoch) tool-use timestamp — the operator-visible wedge signal.
    advanced = _session_to_pipeline(_reload(granite_session.session.session_id))
    assert advanced.current_tool_name == "Bash"
    assert advanced.last_tool_use_at is not None
    assert isinstance(advanced.last_tool_use_at, float)
    assert advanced.last_evidence_at == advanced.last_tool_use_at, (
        "last_tool_use_at must feed the dashboard's last_evidence_at freshness signal"
    )


def test_gap_a_model_write_is_datetime_even_though_dashboard_coerces_to_float(granite_session):
    """The model field stays a ``datetime`` (the type _check_tool_timeout needs);
    only the dashboard serializer coerces it to a float epoch for JSON."""
    pre_tool_use._record_tool_start(
        {"session_id": granite_session.cli_session_id, "tool_name": "Read"}
    )
    refreshed = _reload(granite_session.session.session_id)
    assert isinstance(refreshed.last_tool_use_at, datetime)
