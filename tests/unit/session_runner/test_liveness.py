"""Unit tests for the ``agent.session_runner.liveness`` leaf helpers.

Owner directive (2026-07-07): the ``sdk_ever_output`` derivation is
relocated from ``agent/session_health.py`` (worker-owned, inline) to
``agent/session_runner/liveness.py`` (runner-owned, single exported
function). ``session_health.py`` becomes a pure consumer. See
``docs/plans/headless-runner-zombie-liveness.md``.

``derive_sdk_ever_output`` tests cover all 2^3 combinations of the three
OR-inputs (``last_tool_use_at``, ``last_turn_at``, ``last_stdout_at``) — the
fix's whole point is that ``last_stdout_at`` alone (the headless per-stream
liveness signal) must be sufficient, which was previously NOT the case
(the pre-fix derivation only OR'd the first two fields).

``has_demonstrable_activity`` tests (#2004 Task 2) pin the consolidated
leaf shared by the two ``_has_demonstrable_progress`` forks
(``agent/crash_signature.py`` presence-only, ``agent/session_stall_classifier.py``
freshness-windowed): field-set restriction to ``{turn_count, last_tool_use_at}``
(B1 guard), numeric-string ``turn_count`` coercion parity, and the
never-raises contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from agent.session_runner.liveness import (
    derive_sdk_ever_output,
    has_demonstrable_activity,
)


@dataclass
class _FakeEntry:
    last_tool_use_at: datetime | None = None
    last_turn_at: datetime | None = None
    last_stdout_at: datetime | None = None


_NOW = datetime.now(tz=UTC)


@pytest.mark.parametrize(
    "last_tool_use_at,last_turn_at,last_stdout_at,expected",
    [
        (None, None, None, False),
        (_NOW, None, None, True),
        (None, _NOW, None, True),
        (None, None, _NOW, True),
        (_NOW, _NOW, None, True),
        (_NOW, None, _NOW, True),
        (None, _NOW, _NOW, True),
        (_NOW, _NOW, _NOW, True),
    ],
)
def test_derive_sdk_ever_output_all_combinations(
    last_tool_use_at, last_turn_at, last_stdout_at, expected
):
    entry = _FakeEntry(
        last_tool_use_at=last_tool_use_at,
        last_turn_at=last_turn_at,
        last_stdout_at=last_stdout_at,
    )
    assert derive_sdk_ever_output(entry) is expected


def test_derive_sdk_ever_output_missing_attrs_default_false():
    """An entry missing the fields entirely (getattr default) is False."""

    class _Bare:
        pass

    assert derive_sdk_ever_output(_Bare()) is False


def test_derive_sdk_ever_output_stdout_only_is_true():
    """The headless-runner regression case: a toolless streaming turn that
    has only ever stamped ``last_stdout_at`` must derive True — this is the
    bug this plan fixes (previously the derivation ignored this field
    entirely)."""
    entry = _FakeEntry(last_stdout_at=_NOW)
    assert derive_sdk_ever_output(entry) is True


# ---------------------------------------------------------------------------
# has_demonstrable_activity (#2004 Task 2 — consolidated fork leaf)
# ---------------------------------------------------------------------------


@dataclass
class _ActivityEntry:
    """Attribute-style entry mirroring the AgentSession fields the forks read."""

    turn_count: object = None
    last_tool_use_at: object = None
    # B1 decoy fields — MUST be ignored by has_demonstrable_activity.
    log_path: object = None
    claude_session_uuid: object = None
    last_stdout_at: object = None
    last_turn_at: object = None


class TestHasDemonstrableActivityPresence:
    """freshness_window=None → presence-only semantics (crash_signature caller)."""

    def test_none_entry_is_false(self):
        assert has_demonstrable_activity(None) is False

    def test_empty_entry_is_false(self):
        assert has_demonstrable_activity(_ActivityEntry()) is False

    def test_bare_object_missing_fields_is_false(self):
        class _Bare:
            pass

        assert has_demonstrable_activity(_Bare()) is False

    @pytest.mark.parametrize(
        "turn_count,expected",
        [
            (3, True),
            (1, True),
            (0, False),
            (-1, False),
            ("3", True),  # numeric-string coercion (crash_signature parity)
            ("0", False),
            ("", False),
            ("garbage", False),
            (None, False),
        ],
    )
    def test_turn_count_shapes(self, turn_count, expected):
        entry = _ActivityEntry(turn_count=turn_count)
        assert has_demonstrable_activity(entry) is expected

    def test_tool_use_presence_is_true(self):
        entry = _ActivityEntry(last_tool_use_at=_NOW)
        assert has_demonstrable_activity(entry) is True

    def test_stale_tool_use_presence_still_true_without_window(self):
        entry = _ActivityEntry(last_tool_use_at=_NOW - timedelta(hours=6))
        assert has_demonstrable_activity(entry) is True

    def test_dict_entry_shapes(self):
        assert has_demonstrable_activity({"turn_count": 2}) is True
        assert has_demonstrable_activity({"last_tool_use_at": _NOW}) is True
        assert has_demonstrable_activity({}) is False

    def test_b1_guard_decoy_fields_are_ignored(self):
        """B1: log_path / claude_session_uuid / last_stdout_at / last_turn_at
        must NOT count as presence signals — an init-only/log-only session
        reads no-progress."""
        entry = _ActivityEntry(
            turn_count=0,
            last_tool_use_at=None,
            log_path="/tmp/session.log",
            claude_session_uuid="abc-123",
            last_stdout_at=_NOW,
            last_turn_at=_NOW,
        )
        assert has_demonstrable_activity(entry) is False
        assert has_demonstrable_activity(entry, freshness_window=300) is False


class TestHasDemonstrableActivityFreshness:
    """Caller-supplied freshness_window (stall-classifier caller)."""

    def test_fresh_tool_use_within_window_is_true(self):
        entry = _ActivityEntry(last_tool_use_at=_now() - timedelta(seconds=30))
        assert has_demonstrable_activity(entry, freshness_window=300) is True

    def test_stale_tool_use_outside_window_is_false(self):
        entry = _ActivityEntry(last_tool_use_at=_now() - timedelta(seconds=600))
        assert has_demonstrable_activity(entry, freshness_window=300) is False

    def test_naive_datetime_treated_as_utc(self):
        """Popoto strips tzinfo on save; a naive datetime must be read as UTC
        (mirrors bridge.utc.to_unix_ts), not machine-local time."""
        naive = _now().replace(tzinfo=None) - timedelta(seconds=30)
        entry = _ActivityEntry(last_tool_use_at=naive)
        assert has_demonstrable_activity(entry, freshness_window=300) is True

    def test_float_unix_timestamp_supported(self):
        import time as _time

        entry = _ActivityEntry(last_tool_use_at=_time.time() - 30)
        assert has_demonstrable_activity(entry, freshness_window=300) is True

    def test_iso_string_timestamp_supported(self):
        iso = (_now() - timedelta(seconds=30)).isoformat()
        entry = _ActivityEntry(last_tool_use_at=iso)
        assert has_demonstrable_activity(entry, freshness_window=300) is True

    def test_malformed_timestamp_is_false_never_raises(self):
        entry = _ActivityEntry(last_tool_use_at="not-a-timestamp")
        assert has_demonstrable_activity(entry, freshness_window=300) is False

    def test_turn_count_progress_is_window_independent(self):
        entry = _ActivityEntry(turn_count=2, last_tool_use_at=_now() - timedelta(hours=6))
        assert has_demonstrable_activity(entry, freshness_window=300) is True


def _now() -> datetime:
    return datetime.now(tz=UTC)


class TestForkParity:
    """Both _has_demonstrable_progress forks delegate to the shared leaf."""

    def test_numeric_string_turn_count_true_through_both_forks(self):
        """Type-coercion parity regression (#2004 cycle-3 BLOCKER 1):
        turn_count="3" must read True through BOTH forks — previously the
        stall classifier only accepted int."""
        from agent.crash_signature import (
            _has_demonstrable_progress as crash_progress,
        )
        from agent.session_stall_classifier import (
            _has_demonstrable_progress as stall_progress,
        )

        entry = _ActivityEntry(turn_count="3")
        assert crash_progress(entry) is True
        assert stall_progress(entry) is True

    @pytest.mark.parametrize("turn_count", [0, "0"])
    def test_b1_init_only_session_no_progress_through_both_forks(self, turn_count):
        """B1 pin: an init-only/log-only session (log_path / last_stdout_at set,
        turn_count 0, last_tool_use_at None) reads no-progress through BOTH forks."""
        from agent.crash_signature import (
            _has_demonstrable_progress as crash_progress,
        )
        from agent.session_stall_classifier import (
            _has_demonstrable_progress as stall_progress,
        )

        entry = _ActivityEntry(
            turn_count=turn_count,
            last_tool_use_at=None,
            log_path="/tmp/session.log",
            claude_session_uuid="abc-123",
            last_stdout_at=_now(),
            last_turn_at=_now(),
        )
        assert crash_progress(entry) is False
        assert stall_progress(entry) is False

    def test_stale_tool_use_tradeoff_crash_true_stall_false(self):
        """Deliberate divergence preserved: crash_signature is presence-only
        (any recorded tool use counts — it classifies already-terminal
        sessions), while the stall classifier requires freshness within
        IDLE_SUSPECT_SECS (it hunts *currently* stalled sessions)."""
        from agent.crash_signature import (
            _has_demonstrable_progress as crash_progress,
        )
        from agent.session_stall_classifier import (
            IDLE_SUSPECT_SECS,
        )
        from agent.session_stall_classifier import (
            _has_demonstrable_progress as stall_progress,
        )

        stale = _now() - timedelta(seconds=IDLE_SUSPECT_SECS * 4)
        entry = _ActivityEntry(turn_count=0, last_tool_use_at=stale)
        assert crash_progress(entry) is True
        assert stall_progress(entry) is False

    def test_fresh_tool_use_true_through_both_forks(self):
        from agent.crash_signature import (
            _has_demonstrable_progress as crash_progress,
        )
        from agent.session_stall_classifier import (
            _has_demonstrable_progress as stall_progress,
        )

        entry = _ActivityEntry(last_tool_use_at=_now() - timedelta(seconds=10))
        assert crash_progress(entry) is True
        assert stall_progress(entry) is True

    def test_crash_fork_none_session_is_false(self):
        from agent.crash_signature import (
            _has_demonstrable_progress as crash_progress,
        )

        assert crash_progress(None) is False


class TestSessionHealthGraceParity:
    """Grace semantics preserved post-refactor: session_health is untouched
    (its leaf ``derive_sdk_ever_output`` was already shared), so an
    in-grace-window session must still read live through ``_has_progress``."""

    def test_in_grace_window_session_still_reads_live(self):
        from agent import session_health

        now = _now()

        class _S:
            last_tool_use_at = None
            last_turn_at = None
            last_heartbeat_at = now  # fresh queue heartbeat
            last_sdk_heartbeat_at = None
            last_stdout_at = None
            started_at = now - timedelta(seconds=60)  # inside 150s D0 grace
            created_at = now - timedelta(seconds=60)
            turn_count = 0
            log_path = None
            claude_session_uuid = None
            project_key = "test"

            def get_children(self):
                return []

        assert session_health._has_progress(_S()) is True
