"""Lock in the absence of inference-based kill paths (issue #1172).

Pillar B retired:
- ``STDOUT_FRESHNESS_WINDOW`` (#1046)
- ``FIRST_STDOUT_DEADLINE`` (#1046)
- ``_get_agent_session_timeout`` and the per-session wall-clock kill branch
- ``_last_progress_reason`` module variable + ``stdout_stale`` /
  ``first_stdout_deadline`` reason kinds
- The ``stdout`` gate inside ``_tier2_reprieve_signal()``

These tests assert the symbols are GONE — both as runtime attributes and
absent from the source so a future plan re-adding them is caught early.

The dual-heartbeat OR check from #1036 MUST still hold. Evidence-based
recovery branches (worker_dead, OOM defer, response_delivered_at, Mode 4)
MUST still hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent import session_health


def test_stdout_freshness_window_constant_removed():
    """STDOUT_FRESHNESS_WINDOW must not exist on the module."""
    assert not hasattr(session_health, "STDOUT_FRESHNESS_WINDOW"), (
        "STDOUT_FRESHNESS_WINDOW was retired by #1172. The detector no longer "
        "infers failure from stdout silence; remove this attribute and any "
        "callers that referenced it."
    )


def test_first_stdout_deadline_constant_removed():
    """FIRST_STDOUT_DEADLINE must not exist on the module."""
    assert not hasattr(session_health, "FIRST_STDOUT_DEADLINE"), (
        "FIRST_STDOUT_DEADLINE was retired by #1172. The detector no longer "
        "infers failure from absence of stdout within a deadline."
    )


def test_get_agent_session_timeout_helper_removed():
    """The per-session wall-clock timeout helper must be deleted."""
    assert not hasattr(session_health, "_get_agent_session_timeout"), (
        "_get_agent_session_timeout was retired by #1172. The detector no "
        "longer enforces a wall-clock cap; cost monitoring is the long-run "
        "backstop."
    )


def test_last_progress_reason_module_var_removed():
    """The _last_progress_reason side-channel was for the deleted reason kinds."""
    assert not hasattr(session_health, "_last_progress_reason"), (
        "_last_progress_reason was the side-channel for stdout_stale / "
        "first_stdout_deadline reason kinds — both retired by #1172."
    )


def test_source_does_not_reference_deleted_symbols_outside_docstrings():
    """Grep guard: the deleted symbols must not appear in code (docstrings OK).

    Docstrings can still reference the retired symbols by name to explain why
    they were removed. The guard strips literal triple-quoted blocks before
    searching to allow that. Any remaining hit is a real code reference.
    """
    import re

    src = Path(session_health.__file__).read_text()
    # Strip triple-quoted docstring blocks (both " and ').
    code_only = re.sub(r'"""[\s\S]*?"""', "", src)
    code_only = re.sub(r"'''[\s\S]*?'''", "", code_only)
    for symbol in (
        "STDOUT_FRESHNESS_WINDOW",
        "FIRST_STDOUT_DEADLINE",
        "_get_agent_session_timeout",
        "_last_progress_reason",
        "STDOUT_FRESHNESS_WINDOW_SECS",
        "FIRST_STDOUT_DEADLINE_SECS",
    ):
        assert symbol not in code_only, (
            f"{symbol} survived in agent/session_health.py code (outside docstrings). "
            "#1172 retired all inference-based kill paths and their constants."
        )


def test_dual_heartbeat_or_check_still_holds():
    """#1036 OR semantics: either heartbeat fresh ⇒ progress."""
    now = datetime.now(tz=UTC)

    class _S:
        last_heartbeat_at = now
        last_sdk_heartbeat_at = None
        last_stdout_at = None
        started_at = now - timedelta(seconds=30)
        turn_count = 0
        log_path = None
        claude_session_uuid = None

        def get_children(self):
            return []

    assert session_health._has_progress(_S()) is True

    class _S2:
        last_heartbeat_at = None
        last_sdk_heartbeat_at = now
        last_stdout_at = None
        started_at = now - timedelta(seconds=30)
        turn_count = 0
        log_path = None
        claude_session_uuid = None

        def get_children(self):
            return []

    assert session_health._has_progress(_S2()) is True


def test_fresh_heartbeat_with_long_stdout_silence_is_progress():
    """Regression: the deleted Tier 1 stdout-stale path must NOT fire.

    Previously, fresh heartbeats + stale stdout (>600s) was flagged as
    no-progress (#1046). #1172 retires that — fresh heartbeat alone proves
    progress regardless of stdout cadence. A 4-hour PM session with active
    tool use and no result event is NOT killed.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_heartbeat_at = now  # fresh
        last_sdk_heartbeat_at = now  # fresh
        # Stdout silent for 2 hours — would have tripped the deleted path.
        last_stdout_at = now - timedelta(hours=2)
        started_at = now - timedelta(hours=4)
        turn_count = 5
        log_path = "/tmp/log"
        claude_session_uuid = "abc"

        def get_children(self):
            return []

    assert session_health._has_progress(_S()) is True


def test_fresh_heartbeat_no_stdout_yet_is_progress():
    """Regression: deleted FIRST_STDOUT_DEADLINE path must NOT fire.

    A long-warmup PM session with fresh heartbeats and no stdout yet must
    NOT be flagged. The deleted path killed at 5 min after started_at.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_heartbeat_at = now  # fresh
        last_sdk_heartbeat_at = now  # fresh
        last_stdout_at = None  # never produced stdout
        started_at = now - timedelta(minutes=10)  # 10 min after start
        turn_count = 0
        log_path = None
        claude_session_uuid = None

        def get_children(self):
            return []

    assert session_health._has_progress(_S()) is True


def test_tier2_reprieve_no_longer_returns_stdout_gate():
    """Tier 2 must not consult last_stdout_at; the (e) gate is gone."""
    now = datetime.now(tz=UTC)

    class _S:
        # No pid (handle=None), so (c)/(d) skipped.
        last_compaction_ts = None
        last_stdout_at = now  # would have triggered the deleted (e) gate

    # All gates fail → returns None. The previous behavior would have
    # returned "stdout".
    assert session_health._tier2_reprieve_signal(handle=None, entry=_S()) is None
