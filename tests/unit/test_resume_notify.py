"""Regression tests for resume-to-pending notify publish (#2165, plan sdlc-2143).

A terminal->pending resume used to sit unpicked for 8-16 minutes: `resume_session`
transitioned to pending but — unlike the create path (`_push_agent_session`) —
never published the session-notify, so the worker's `_session_notify_listener`
never woke a popper. Pickup then depended on the queue loop's 1.5s drain re-poll
or the 5-min health scan (whose liveness test treats a parked/busy loop as alive).

These tests pin the fix's contract:
- `resume_session` publishes the SAME payload/channel as the create path.
- The publish is a PLAIN SYNCHRONOUS `POPOTO_REDIS_DB.publish` (NOT
  `asyncio.to_thread`) — `resume_session` is a sync `def` whose CLI/reflection
  callers have no running event loop.
- It is NOTIFY-ONLY: it never calls `_ensure_worker` / `asyncio.create_task`
  (the worker's notify listener is the sole owner of the spawn, so all three
  callers — CLI, out-of-process auto-resume reflection, in-worker — stay
  single-worker-ownership-safe; B2/C1 blocker).
- The publish is fail-quiet: a Redis publish error must not fail the resume,
  because the transition already succeeded.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.valor_session import resume_session  # noqa: E402

_RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})
_TEST_CHANNEL = "valor:sessions:new:db-test"


def _make_session(
    *,
    session_id="notify-sess",
    status="completed",
    claude_session_uuid="uuid-notify",
    worker_key="notify-sess",
    project_key="proj-key",
    chat_id="chat-123",
):
    """Build a session whose notify-relevant attrs are REAL strings.

    `worker_key` on the real model is a computed property; here we set it as a
    plain string attribute so the JSON payload is serializable (a raw MagicMock
    child would make json.dumps raise and be swallowed by the fail-quiet guard).
    """
    s = MagicMock()
    s.session_id = session_id
    s.status = status
    s.claude_session_uuid = claude_session_uuid
    s.worker_key = worker_key
    s.project_key = project_key
    s.chat_id = chat_id
    s.model = None
    # Goal fields left as MagicMock children — the isinstance(str) guard skips them.
    return s


def _run_resume(session, *, redis_mock, source="cli", message="continue", publish_side_effect=None):
    """Drive resume_session with the notify surface patched. Returns ResumeResult."""
    if publish_side_effect is not None:
        redis_mock.publish.side_effect = publish_side_effect

    with (
        patch("tools.valor_session._load_env"),
        patch("agent.steering.push_steering_message"),
        patch("popoto.redis_db.POPOTO_REDIS_DB", redis_mock),
        patch("agent.agent_session_queue.notify_channel_for", return_value=_TEST_CHANNEL),
        patch.dict(
            "sys.modules",
            {
                "models.session_lifecycle": MagicMock(
                    transition_status=MagicMock(),
                    RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                ),
            },
        ),
    ):
        return resume_session(session, message, source=source)


class TestResumePublishesNotify:
    def test_publishes_notify_with_correct_payload_and_channel(self):
        """A terminal->pending resume publishes the create-path notify (#2165 AC)."""
        session = _make_session(worker_key="my-slug", project_key="proj-key", chat_id="chat-9")
        redis_mock = MagicMock()

        result = _run_resume(session, redis_mock=redis_mock)

        assert result.success is True
        redis_mock.publish.assert_called_once()
        channel, payload_json = redis_mock.publish.call_args[0]
        assert channel == _TEST_CHANNEL
        payload = json.loads(payload_json)
        assert payload == {
            "chat_id": "chat-9",
            "session_id": "notify-sess",
            "worker_key": "my-slug",
            "is_project_keyed": False,
        }

    def test_is_project_keyed_true_when_worker_key_equals_project_key(self):
        """is_project_keyed mirrors the create path: worker_key == project_key."""
        session = _make_session(worker_key="proj-key", project_key="proj-key")
        redis_mock = MagicMock()

        result = _run_resume(session, redis_mock=redis_mock)

        assert result.success is True
        payload = json.loads(redis_mock.publish.call_args[0][1])
        assert payload["worker_key"] == "proj-key"
        assert payload["is_project_keyed"] is True

    def test_publish_is_synchronous_not_to_thread(self):
        """The publish must be a plain sync call — resume callers have no event loop."""
        session = _make_session()
        redis_mock = MagicMock()

        with patch.object(asyncio, "to_thread") as mock_to_thread:
            result = _run_resume(session, redis_mock=redis_mock)

        assert result.success is True
        redis_mock.publish.assert_called_once()
        mock_to_thread.assert_not_called()

    def test_publish_happens_after_successful_transition(self):
        """Notify is published only AFTER transition_status returns (Race 1)."""
        call_order: list[str] = []
        session = _make_session()
        redis_mock = MagicMock()
        redis_mock.publish.side_effect = lambda *a, **k: call_order.append("publish")

        def _record_transition(*_a, **_kw):
            call_order.append("transition")

        with (
            patch("tools.valor_session._load_env"),
            patch("agent.steering.push_steering_message"),
            patch("popoto.redis_db.POPOTO_REDIS_DB", redis_mock),
            patch("agent.agent_session_queue.notify_channel_for", return_value=_TEST_CHANNEL),
            patch.dict(
                "sys.modules",
                {
                    "models.session_lifecycle": MagicMock(
                        transition_status=MagicMock(side_effect=_record_transition),
                        RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                    ),
                },
            ),
        ):
            result = resume_session(session, "continue", source="cli")

        assert result.success is True
        assert call_order == ["transition", "publish"], (
            "notify must publish only after the pending transition is index-visible"
        )


class TestResumeIsNotifyOnly:
    """The resume path must NEVER spawn a worker loop locally (B2/C1)."""

    def test_never_calls_ensure_worker_cli(self):
        session = _make_session()
        redis_mock = MagicMock()
        ensure_worker = MagicMock()

        with patch("agent.agent_session_queue._ensure_worker", ensure_worker):
            result = _run_resume(session, redis_mock=redis_mock, source="cli")

        assert result.success is True
        ensure_worker.assert_not_called()

    def test_never_calls_ensure_worker_reflection(self):
        """The auto-resume reflection caller must also stay notify-only."""
        session = _make_session()
        redis_mock = MagicMock()
        ensure_worker = MagicMock()

        with patch("agent.agent_session_queue._ensure_worker", ensure_worker):
            result = _run_resume(session, redis_mock=redis_mock, source="auto-resume")

        assert result.success is True
        ensure_worker.assert_not_called()

    def test_never_calls_asyncio_create_task(self):
        session = _make_session()
        redis_mock = MagicMock()

        with patch.object(asyncio, "create_task") as mock_create_task:
            result = _run_resume(session, redis_mock=redis_mock)

        assert result.success is True
        mock_create_task.assert_not_called()


class TestResumeNotifyFailQuiet:
    def test_publish_failure_does_not_fail_resume(self):
        """A Redis publish error must not fail the resume (transition already succeeded)."""
        session = _make_session()
        redis_mock = MagicMock()

        result = _run_resume(
            session,
            redis_mock=redis_mock,
            publish_side_effect=RuntimeError("redis down"),
        )

        assert result.success is True
        assert result.error is None

    def test_publish_failure_logs_warning(self, caplog):
        session = _make_session()
        redis_mock = MagicMock()

        import logging

        with caplog.at_level(logging.WARNING, logger="tools.valor_session"):
            result = _run_resume(
                session,
                redis_mock=redis_mock,
                publish_side_effect=RuntimeError("redis down"),
            )

        assert result.success is True
        assert any("resume notification" in r.message.lower() for r in caplog.records)

    def test_none_worker_key_does_not_crash(self):
        """A session with a None worker_key/chat_id resumes without crashing (fail-quiet)."""
        session = _make_session(worker_key=None, project_key=None, chat_id=None)
        redis_mock = MagicMock()

        result = _run_resume(session, redis_mock=redis_mock)

        # Publish still succeeds with a null-worker_key payload (json handles None);
        # is_project_keyed is None == None -> True. The point is: no crash.
        assert result.success is True
