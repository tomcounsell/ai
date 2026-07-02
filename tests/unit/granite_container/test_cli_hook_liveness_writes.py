"""CLI-hook liveness writes for granite PM/Dev PTY children (#1843, Gap A).

Granite's PM/Dev ``claude`` PTY children run the repo CLI hooks
(``.claude/hooks/pre_tool_use.py`` / ``post_tool_use.py``), not the SDK
in-process hooks in ``agent/hooks/``. ``AGENT_SESSION_ID`` is unset in the
granite child env, so ``agent.hooks.liveness_writers.record_tool_boundary``
would silently no-op. Gap A resolves the AgentSession via the on-disk
sidecar and stamps ``current_tool_name`` / ``last_tool_use_at`` directly.

These tests assert:
  * ``_record_tool_start`` (pre) sets ``current_tool_name`` and a
    ``datetime`` ``last_tool_use_at`` on the sidecar-resolved session.
  * ``_update_agent_session`` (post) clears ``current_tool_name`` and
    refreshes ``last_tool_use_at`` (still a ``datetime``).
  * ``last_tool_use_at`` is a ``datetime``, never a float — the type the
    #1270 tier loop requires (CONCERN 4).
  * Both hooks fail-silent (return without raising) when the sidecar is
    missing or carries no ``agent_session_id``.
  * ``session_health._check_tool_timeout`` arms on a ``datetime`` value and
    short-circuits on a float (CONCERN 4 regression guard).

The hooks are standalone scripts with non-standard imports, so we add
``.claude/hooks`` to ``sys.path`` and import them directly — the pattern
already used by ``tests/unit/test_pre_tool_use_hook.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.agent_session import AgentSession, SessionType

_HOOKS_DIR = str(Path(__file__).resolve().parents[3] / ".claude" / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

import post_tool_use  # noqa: E402
import pre_tool_use  # noqa: E402

_PROJECT_KEY = "test-1843-cli-hook-liveness"


def _write_sidecar(cli_session_id: str, agent_session_id: str) -> Path:
    """Write the minimal ``agent_session.json`` sidecar the CLI hooks read.

    Uses the hook module's own ``_REPO_ROOT`` so the path matches exactly
    what ``_record_tool_start`` / ``_update_agent_session`` resolve.
    """
    sidecar_dir = pre_tool_use._REPO_ROOT / "data" / "sessions" / cli_session_id
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / "agent_session.json"
    path.write_text(json.dumps({"agent_session_id": agent_session_id}))
    return sidecar_dir


@pytest.fixture
def granite_session():
    """A granite-shaped AgentSession plus a CLI-hook sidecar pointing at it.

    ``AGENT_SESSION_ID`` is intentionally NOT set — the granite child env
    never carries it, which is the whole reason Gap A resolves via sidecar.
    """
    cli_session_id = f"cli-hook-{id(object())}"
    session = AgentSession.create(
        project_key=_PROJECT_KEY,
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"granite-liveness-{cli_session_id}",
        working_dir="/tmp",
    )
    sidecar_dir = _write_sidecar(cli_session_id, session.agent_session_id)
    yield SimpleNamespace(
        session=session,
        cli_session_id=cli_session_id,
        sidecar_dir=sidecar_dir,
    )
    try:
        session.delete()
    except Exception:
        pass
    try:
        (sidecar_dir / "agent_session.json").unlink(missing_ok=True)
        sidecar_dir.rmdir()
    except Exception:
        pass


def _reload(session_id: str) -> AgentSession:
    matches = list(AgentSession.query.filter(session_id=session_id))
    assert len(matches) == 1, f"expected exactly one session for {session_id}"
    return matches[0]


def test_pre_hook_sets_tool_name_and_datetime_timestamp(granite_session):
    pre_tool_use._record_tool_start(
        {"session_id": granite_session.cli_session_id, "tool_name": "Bash"}
    )

    refreshed = _reload(granite_session.session.session_id)
    assert refreshed.current_tool_name == "Bash"
    assert refreshed.last_tool_use_at is not None
    # CONCERN 4: the tier loop requires a datetime, never a float.
    assert isinstance(refreshed.last_tool_use_at, datetime)


def test_post_hook_clears_tool_name_and_refreshes_datetime(granite_session):
    pre_tool_use._record_tool_start(
        {"session_id": granite_session.cli_session_id, "tool_name": "Read"}
    )
    assert _reload(granite_session.session.session_id).current_tool_name == "Read"

    post_tool_use._update_agent_session(
        {"session_id": granite_session.cli_session_id, "tool_name": "Read"}
    )

    refreshed = _reload(granite_session.session.session_id)
    assert refreshed.current_tool_name is None
    assert refreshed.last_tool_use_at is not None
    assert isinstance(refreshed.last_tool_use_at, datetime)


def test_pre_hook_fails_silent_when_sidecar_missing():
    # No sidecar written for this session id → resolves to {} and returns.
    pre_tool_use._record_tool_start({"session_id": "no-such-cli-session-1843", "tool_name": "Bash"})


def test_pre_hook_fails_silent_without_agent_session_id(tmp_path):
    cli_session_id = f"cli-hook-empty-{id(object())}"
    sidecar_dir = pre_tool_use._REPO_ROOT / "data" / "sessions" / cli_session_id
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "agent_session.json").write_text(json.dumps({}))
    try:
        # Sidecar present but no agent_session_id → returns without raising.
        pre_tool_use._record_tool_start({"session_id": cli_session_id, "tool_name": "Bash"})
        post_tool_use._update_agent_session({"session_id": cli_session_id, "tool_name": "Bash"})
    finally:
        (sidecar_dir / "agent_session.json").unlink(missing_ok=True)
        sidecar_dir.rmdir()


def test_pre_hook_fails_silent_with_blank_session_id():
    # Blank session_id short-circuits before any sidecar read.
    pre_tool_use._record_tool_start({"session_id": "", "tool_name": "Bash"})
    post_tool_use._update_agent_session({"session_id": "", "tool_name": "Bash"})


def test_check_tool_timeout_arms_on_datetime_shortcircuits_on_float():
    """CONCERN 4 regression guard.

    ``_check_tool_timeout`` must fire for a granite session whose
    ``last_tool_use_at`` is a stale ``datetime``, and must short-circuit (no
    wedge) when the same value is written as a float — the exact type trap
    that would leave the #1270 tier loop silently unarmed.
    """
    from agent.session_health import TOOL_TIMEOUT_DEFAULT_SEC, _check_tool_timeout

    stale = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_DEFAULT_SEC + 60)

    datetime_entry = SimpleNamespace(current_tool_name="Bash", last_tool_use_at=stale)
    result = _check_tool_timeout(datetime_entry)
    assert result is not None, "a stale datetime last_tool_use_at must arm the tier loop"
    tier, reason = result
    assert tier == "default"
    assert "Bash" in reason

    float_entry = SimpleNamespace(current_tool_name="Bash", last_tool_use_at=stale.timestamp())
    assert _check_tool_timeout(float_entry) is None, (
        "a float last_tool_use_at must short-circuit (the tier loop stays unarmed)"
    )
