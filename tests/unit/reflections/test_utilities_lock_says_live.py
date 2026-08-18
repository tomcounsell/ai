"""Payload-to-verdict coverage for ``reflections.utilities._lock_says_live``
(issue #2648, SC9).

Why this module exists separately from ``test_sdlc_upvote_lanes.py``: that
module stubs ``_lock_says_live`` wholesale, which exercises the *gates* that
consume the verdict but proves nothing about how a lock payload becomes one.
This is the half that matters for #2648, because ``_lock_says_live`` is the
worst-cost consumer of the shared predicate: its callers ``continue`` on
``True`` and on ``None`` but **act** on ``False``, and acting means starting an
autonomous rival SDLC lane with no operator in the loop. A false-dead here is
the #1915 duplicate-PR shape, unattended.

So every gate in the tightened branch must fail toward live, and only the full
conjunction -- complete durable renewer identity, same machine, renewer pid
dead or recycled, and ``renewed_at`` older than the grace window -- may yield
``False``.

What is faked, and why:

* ``reflections.utilities._get_redis`` is patched to hand back each payload
  shape as raw JSON. ``_lock_says_live`` does a bare ``GET`` on the lock key
  deliberately (``touch_issue_lock`` fails OPEN, which this gate would misread
  as "unheld"), so the Redis handle is the module's only seam.
* ``agent.session_health._psutil_process_for_pid`` is the ONLY working patch
  target for the pid check: ``_lock_owner_is_live`` imports it lazily inside
  its own function body, so patching the name on ``models.session_lifecycle``
  raises AttributeError rather than silently missing.

Everything else is the real predicate.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from reflections import utilities as m

pytestmark = [pytest.mark.unit]


_ISSUE = 2648

# Quiet longer than ISSUE_LOCK_RENEWER_GRACE_SECONDS (180) but well inside the
# 1200s renewal-freshness window, so the payload reads "freshly renewed"
# throughout and the renewer branch is the one that decides.
_PAST_GRACE_QUIET = 800.0
# Comfortably inside the grace: a worker restart is roughly three 60s ticks.
_INSIDE_GRACE_QUIET = 30.0


def _payload(renewed_age: float, **extra) -> dict:
    """A locally-minted lease payload whose stamped ``pid`` is long dead.

    ``renewed_age`` is how many seconds ago the last renewal landed. Callers
    layer the ``renewer_*`` group (or deliberately omit it) via ``extra``.
    """
    payload = {
        "run_id": "run-a",
        "session_id": f"sdlc-local-{_ISSUE}",
        "pid": 424242,  # the ephemeral session-ensure CLI, dead in seconds
        "hostname": "this-host",
        "machine_id": "hw-uuid-1",
        "create_time": 1000.0,
        "renewed_at": time.time() - renewed_age,
    }
    payload.update(extra)
    return payload


def _redis_returning(payload: dict) -> MagicMock:
    redis = MagicMock()
    redis.get.return_value = json.dumps(payload)
    return redis


def _dead_renewer_proc():
    """psutil cannot resolve the renewer pid at all."""
    return None


def _live_renewer_proc(create_time: float = 2000.0):
    proc = MagicMock()
    proc.create_time.return_value = create_time
    return proc


class TestLockSaysLiveRenewerIdentity:
    """The payload-to-verdict path, driven through the real predicate."""

    def test_dead_renewer_past_grace_says_not_live(self):
        """The one shape that may return ``False``: a durable renewer stamped
        its identity, died, and the lease has been silent past the grace
        window. Before #2648 the fresh ``renewed_at`` short-circuited this to
        ``True`` and the reflection declined to act on a lane that was in fact
        dead."""
        payload = _payload(
            _PAST_GRACE_QUIET,
            renewer_pid=999001,
            renewer_create_time=2000.0,
        )
        with (
            patch.object(m, "_get_redis", return_value=_redis_returning(payload)),
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid", return_value=_dead_renewer_proc()
            ),
        ):
            assert m._lock_says_live(_ISSUE) is False

    def test_live_renewer_says_live(self):
        """The renewer pid resolves alive with a matching create_time, so the
        run is alive and the grace clock is never consulted. This is the shape
        a live-but-quiet local lane presents, and the reflection must decline
        to start a rival."""
        payload = _payload(
            _PAST_GRACE_QUIET,
            renewer_pid=999001,
            renewer_create_time=2000.0,
        )
        with (
            patch.object(m, "_get_redis", return_value=_redis_returning(payload)),
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid", return_value=_live_renewer_proc()
            ),
        ):
            assert m._lock_says_live(_ISSUE) is True

    def test_dead_renewer_inside_grace_says_live(self):
        """A renewer between incarnations -- a worker restart leaves the old
        pid dead until the new one's next tick -- is not a dead run."""
        payload = _payload(
            _INSIDE_GRACE_QUIET,
            renewer_pid=999001,
            renewer_create_time=2000.0,
        )
        with (
            patch.object(m, "_get_redis", return_value=_redis_returning(payload)),
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid", return_value=_dead_renewer_proc()
            ),
        ):
            assert m._lock_says_live(_ISSUE) is True

    def test_no_renewer_identity_says_live(self):
        """No durable renewer identity on the payload -- a legacy lease, or one
        whose most recent renewal was an ephemeral CLI write. The renewal-
        freshness short-circuit is conclusive there and the verdict is
        unchanged, which is what keeps a quiet local lane exempt."""
        payload = _payload(_PAST_GRACE_QUIET)
        with (
            patch.object(m, "_get_redis", return_value=_redis_returning(payload)),
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid", return_value=_dead_renewer_proc()
            ),
        ):
            assert m._lock_says_live(_ISSUE) is True

    def test_cross_machine_renewer_says_live(self):
        """A foreign machine's pid cannot be checked from here, so the evidence
        is indeterminate and the posture is fail-toward-live."""
        payload = _payload(
            _PAST_GRACE_QUIET,
            machine_id="hw-uuid-other",
            renewer_pid=999001,
            renewer_create_time=2000.0,
        )
        with (
            patch.object(m, "_get_redis", return_value=_redis_returning(payload)),
            patch("models.session_lifecycle._local_machine_id", return_value="hw-uuid-1"),
            patch("socket.gethostname", return_value="this-host"),
            patch(
                "agent.session_health._psutil_process_for_pid", return_value=_dead_renewer_proc()
            ),
        ):
            assert m._lock_says_live(_ISSUE) is True
