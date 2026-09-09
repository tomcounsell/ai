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
    """Records RPUSH/LPUSH targets so the chosen steering key is assertable.

    ``lrem`` is modelled too, because the rollback path has to remove exactly
    the entry it pushed rather than truncating the list.
    """

    def __init__(self):
        self.pushes: list[tuple[str, str]] = []

    def rpush(self, key, payload):
        self.pushes.append((key, payload))

    def lpush(self, key, payload):
        self.pushes.append((key, payload))

    def lrem(self, key, count, payload):
        before = len(self.pushes)
        self.pushes = [p for p in self.pushes if p != (key, payload)]
        return before - len(self.pushes)


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


def test_a_failed_transition_leaves_no_orphaned_resume_steer(redis_keys):
    """The push precedes the transition, so a failed transition must roll it back.

    ``resume_session`` pushes BEFORE ``transition_status`` on purpose (it closes
    the two-write race). When the transition then raises, the row is never
    resumed -- but the steer is already on ``steering:{session_id}``, which
    carries no TTL and, unlike the Room leg, is never age-bounded at drain time
    (``_drain_list`` applies ``steering_room_max_age_s`` to the Room key only).
    Left there, "your lane stalled, continue" is injected verbatim the next
    time that session runs, however many hours later.
    """
    session = _session("sess-rollback")

    def _boom(*_a, **_kw):
        raise RuntimeError("another process raced us")

    with (
        patch("tools.valor_session._load_env"),
        patch("tools.valor_session._publish_resume_notify"),
        patch.dict(
            "sys.modules",
            {
                "models.session_lifecycle": MagicMock(
                    transition_status=_boom,
                    RESUMABLE_STATUSES=_RESUMABLE_STATUSES,
                ),
            },
        ),
    ):
        result = resume_session(session, "Continue.", source="sdlc-stall")

    assert not result.success
    assert redis_keys.pushes == [], (
        "a resume steer outlived the transition that failed; it will be "
        f"injected on the next run of this session -- leftover: {redis_keys.pushes}"
    )
