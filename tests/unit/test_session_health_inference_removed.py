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
    """#1036 OR semantics narrowed by #1226: queue heartbeat is the Tier 1 fallback.

    Sub-check B uses ``last_heartbeat_at`` as the executor-alive fallback
    when ``sdk_ever_output`` is False. ``last_sdk_heartbeat_at`` is a
    subprocess-watchdog signal (BackgroundTask._watchdog), NOT a progress
    signal — #1226 intentionally excluded it from the progress check
    because subprocess-alive without per-turn output is the very signature
    of a hung session.

    A queue heartbeat fresh within ``HEARTBEAT_FRESHNESS_WINDOW`` ⇒ progress.
    SDK-watchdog heartbeat alone ⇒ NOT progress (must be paired with
    per-turn evidence in sub-check A or the queue heartbeat in sub-check B).
    """
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

    # #1226: SDK watchdog heartbeat is NOT a progress signal on its own.
    assert session_health._has_progress(_S2()) is False


def test_fresh_heartbeat_with_long_stdout_silence_is_progress():
    """D0 gate (issue #1724): a session past the never-started grace window with no SDK
    output is treated as never-started (not alive), even with a fresh heartbeat.

    Previously (#1172), fresh heartbeat alone proved progress regardless of stdout
    cadence. The D0 gate overrides that fast-path when sdk_ever_output=False AND
    running time exceeds NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS
    (120 + 30 = 150s). turn_count=5 does NOT set sdk_ever_output — only last_tool_use_at
    or last_turn_at count. A 4-hour session with neither field set is a zombie.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now  # fresh
        last_sdk_heartbeat_at = now  # fresh
        # Stdout silent for 2 hours — would have tripped the deleted path.
        last_stdout_at = now - timedelta(hours=2)
        started_at = now - timedelta(hours=4)
        created_at = now - timedelta(hours=4)
        turn_count = 5
        log_path = "/tmp/log"
        claude_session_uuid = "abc"
        project_key = "test"

        def get_children(self):
            return []

    assert session_health._has_progress(_S()) is False


def test_fresh_heartbeat_no_stdout_yet_is_progress():
    """D0 gate (issue #1724): after 10 minutes with no SDK output, the session is past
    the never-started grace window (600s > 150s) and correctly treated as not alive.

    Previously the deleted FIRST_STDOUT_DEADLINE path killed at 5 min. The D0 gate
    now correctly handles this case: sdk_ever_output=False (neither last_tool_use_at
    nor last_turn_at is set) AND running_seconds=600 > threshold=150 → returns False.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now  # fresh
        last_sdk_heartbeat_at = now  # fresh
        last_stdout_at = None  # never produced stdout
        started_at = now - timedelta(minutes=10)  # 10 min after start — past 150s grace
        created_at = now - timedelta(minutes=10)
        turn_count = 0
        log_path = None
        claude_session_uuid = None
        project_key = "test"

        def get_children(self):
            return []

    assert session_health._has_progress(_S()) is False


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


def test_zombie_profile_is_not_progress():
    """
    AC2: A session matching the observed zombie profile should return False from
    _has_progress so branch-2 recovery can fire.

    Profile: running, sdk_ever_output=False (last_tool_use_at=None, last_turn_at=None),
    claude_session_uuid set, last_heartbeat_at stale by hours, past startup grace.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now - timedelta(hours=4)  # stale by hours
        last_sdk_heartbeat_at = None
        last_stdout_at = None
        started_at = now - timedelta(hours=4)  # well past NO_OUTPUT_BUDGET_SECONDS (1800s)
        created_at = now - timedelta(hours=4)
        turn_count = 0
        log_path = None
        claude_session_uuid = "abc-zombie-test"  # set but heartbeat is stale
        project_key = "test"

        def get_children(self):
            return []

    result = session_health._has_progress(_S())
    assert result is False, (
        "Zombie profile (stale heartbeat, sdk_ever_output=False, uuid set) "
        "must not return True from _has_progress — this blocks recovery"
    )


def test_own_progress_gate_fresh_heartbeat_is_progress():
    """
    D0 gate (issue #1724): fresh heartbeat does NOT protect a session that is past
    the never-started grace window with zero SDK output.

    A session running for 3600s with sdk_ever_output=False (neither last_tool_use_at
    nor last_turn_at set) is correctly treated as never-started. The D0 gate denies
    the fresh-heartbeat fast-path and returns False, enabling recovery.

    Note: the class name "fresh heartbeat is progress" is now a misnomer post-D0 gate.
    The gate fires before the heartbeat fast-path can return True.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now  # FRESH — but D0 gate fires before fast-path
        last_sdk_heartbeat_at = None
        last_stdout_at = None
        started_at = now - timedelta(seconds=3600)  # 3600s >> 150s grace
        created_at = now - timedelta(seconds=3600)
        turn_count = 0
        log_path = None
        claude_session_uuid = "abc"  # set
        project_key = "test"

        def get_children(self):
            return []

    result = session_health._has_progress(_S())
    assert result is False, (
        "Session past never-started grace (3600s >> 150s) with sdk_ever_output=False "
        "must return False — the D0 gate fires before the heartbeat fast-path"
    )


def test_own_progress_gate_inside_grace_window_is_protected():
    """
    Sessions inside the never-started grace window (< 150s running) with a fresh
    heartbeat MUST return True — the D0 gate must not fire before the grace expires.

    This is the safety boundary: sessions that just started (e.g., granite cold-start
    taking up to 120s) must not be prematurely recovered.
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now  # FRESH
        last_sdk_heartbeat_at = None
        last_stdout_at = None
        started_at = now - timedelta(seconds=60)  # 60s — well inside 150s grace
        created_at = now - timedelta(seconds=60)
        turn_count = 0
        log_path = None
        claude_session_uuid = None
        project_key = "test"

        def get_children(self):
            return []

    result = session_health._has_progress(_S())
    assert result is True, (
        "Session inside the never-started grace window (60s < 150s) with fresh "
        "heartbeat must return True — the D0 gate must not fire before grace expires"
    )


def test_own_progress_gate_stale_heartbeat_is_zombie():
    """
    AC3 STALE sibling: The exact zombie profile. claude_session_uuid set but heartbeat
    stale by 3 hours. Currently returns True (the bug); the gate must flip it to False.

    This is the authoritative AC3 boundary guard for the L847-853 own-progress gate.

    Pre-fix: this test FAILS (ungated claude_session_uuid returns True).
    Post-fix: this test PASSES (stale heartbeat + uuid → False).
    """
    now = datetime.now(tz=UTC)

    class _S:
        last_tool_use_at = None
        last_turn_at = None
        last_heartbeat_at = now - timedelta(hours=3)  # STALE — sub-check B does not return True
        last_sdk_heartbeat_at = None
        last_stdout_at = None
        started_at = now - timedelta(seconds=3600)  # past 1800s NO_OUTPUT_BUDGET
        created_at = now - timedelta(seconds=3600)
        turn_count = 0
        log_path = None
        claude_session_uuid = "abc"  # set but heartbeat is stale
        project_key = "test"

        def get_children(self):
            return []

    result = session_health._has_progress(_S())
    assert result is False, (
        "Zombie profile (stale heartbeat + claude_session_uuid set, sdk_ever_output=False) "
        "must return False — this is the bug the gate fixes"
    )
