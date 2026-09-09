"""``resume_session`` steers the ONE row it resumes, not the whole Room (#3270).

A resume names a specific session, transitions that row in place, and the
worker runs it in that row's own ``working_dir``. Writing the steer to the
Room key ``steering:room:{room_id}`` hands it to whichever session next drains
the Room, which is how a resume aimed at a stalled engineering lane landed an
unprompted message in a human conversation thread.

The assertion is on the Redis key itself rather than on the ``room_id`` kwarg,
because the key is the thing that decides who drains the message
(``agent/steering.py`` selects it from ``room_id``).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import agent.steering as steering
from tools.valor_session import resume_session

_RESUMABLE_STATUSES = frozenset({"completed", "killed", "failed", "abandoned"})


class _KeyRecordingRedis:
    """Records RPUSH/LPUSH targets so the chosen steering key is assertable."""

    def __init__(self):
        self.pushes: list[tuple[str, str]] = []

    def rpush(self, key, payload):
        self.pushes.append((key, payload))

    def lpush(self, key, payload):
        self.pushes.append((key, payload))


@pytest.fixture
def redis_keys(monkeypatch):
    r = _KeyRecordingRedis()
    monkeypatch.setattr(steering, "_get_redis", lambda: r)
    return r


def _session(session_id="sess-scope"):
    s = MagicMock()
    s.session_id = session_id
    s.status = "completed"
    s.claude_session_uuid = "uuid-1"
    s.project_key = "test"
    s.slug = ""
    s.pr_url = ""
    return s


def test_resume_steer_lands_on_the_session_key_not_the_room_key(redis_keys):
    session = _session()

    with (
        patch("tools.valor_session._load_env"),
        patch("tools.valor_session._publish_resume_notify"),
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
        result = resume_session(session, "Continue.", source="sdlc-stall")

    assert result.success
    keys = [key for key, _ in redis_keys.pushes]
    assert keys == ["steering:sess-scope"], (
        "a resume targets one row; a room-scoped key is drained by whichever "
        "session next serves the room"
    )
    assert not any(key.startswith("steering:room:") for key in keys)
    assert json.loads(redis_keys.pushes[0][1])["text"].endswith("Continue.")
