"""Reconciler per-chat scan-loop health monitoring (issue #2691).

The half-wedged state these tests pin down: ``get_dialogs()`` resolves (so
``bridge:last_probe_ok`` stays fresh and the wedge detector sees a healthy API
layer), while every per-chat history fetch faults (so nothing is recovered and
``bridge:last_missed_recovery`` is never stamped). Before #2691 that state was
invisible to the watchdog. These tests assert the reconciler now records it and
the watchdog can read it.

Redis here is the pytest-claimed test db; the liveness keys are freeform, so
plain get/set is correct (see bridge/liveness.py).
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.liveness import (
    _SCAN_OUTCOME_KEY,
    get_last_scan_outcome,
    record_scan_outcome,
)
from bridge.reconciler import reconcile_once
from monitoring.bridge_watchdog import (
    SCAN_STAMP_FRESHNESS_SECONDS,
    SCAN_TOTAL_FAULT_CYCLES,
    assess_scan_health,
)


def _make_dialog(chat_title, entity_id=100):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = -(1000000000000 + entity_id)
    return dialog


def _make_message(msg_id, text="hello"):
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = False
    msg.date = datetime.now(UTC) - timedelta(minutes=1)
    sender = MagicMock()
    sender.first_name = "TestUser"
    sender.username = "testuser"
    sender.id = 12345
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _redis():
    import redis as redis_lib

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis_lib.Redis.from_url(url, decode_responses=True)


@pytest.fixture(autouse=True)
def _clean_scan_key():
    """Clear the freeform scan-outcome key around each test."""
    r = _redis()
    r.delete(_SCAN_OUTCOME_KEY)
    yield
    r.delete(_SCAN_OUTCOME_KEY)


async def _run_scan(fetch_side_effect, titles=("test group",)):
    """Run one reconcile_once over `titles` with a stubbed history fetch."""
    dialogs = [_make_dialog(t, entity_id=100 + i) for i, t in enumerate(titles)]
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=dialogs)

    with patch(
        "bridge.reconciler.fetch_messages_back_to",
        new=AsyncMock(side_effect=fetch_side_effect),
    ):
        return await reconcile_once(
            client=client,
            monitored_groups=list(titles),
            should_respond_fn=AsyncMock(return_value=(False, False)),
            enqueue_agent_session_fn=AsyncMock(),
            find_project_fn=MagicMock(
                return_value={"_key": "testproj", "working_directory": "/tmp/test"}
            ),
        )


# ---------------------------------------------------------------------------
# The half-wedged state: dialogs resolve, per-chat scan faults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_half_wedged_scan_records_total_fault():
    """Dialogs resolve but every per-chat fetch throws — the stamp says so."""
    recovered = await _run_scan(
        ConnectionError("iter_messages timed out"),
        titles=("test group", "second group"),
    )

    # No recovery, so no missed-recovery evidence: exactly the invisible state.
    assert recovered == 0

    record = get_last_scan_outcome()
    assert record is not None, "reconciler must write a scan-outcome record"
    assert record["attempted"] == 2
    assert record["faulted"] == 2
    assert record["consecutive_total_fault_cycles"] == 1
    assert record["pid"] == os.getpid()
    assert "ConnectionError" in record["sample_error"]


@pytest.mark.asyncio
async def test_half_wedged_probe_stays_fresh_while_scan_faults():
    """The probe is stamped before the loop, so it cannot report the fault."""
    from bridge.liveness import get_last_probe_ok

    before = time.time()
    await _run_scan(ConnectionError("boom"))

    probe = get_last_probe_ok()
    assert probe is not None and probe >= before, (
        "get_dialogs() succeeded, so the probe is fresh — this is why the "
        "scan-outcome record is needed as a separate signal"
    )
    assert get_last_scan_outcome()["faulted"] == 1


@pytest.mark.asyncio
async def test_consecutive_total_fault_cycles_accumulate_then_reset():
    """The writer maintains the run counter; a healthy chat resets it."""
    for expected in (1, 2, 3):
        await _run_scan(ConnectionError("boom"))
        assert get_last_scan_outcome()["consecutive_total_fault_cycles"] == expected

    # One cycle where the fetch succeeds clears the run.
    async def _ok_fetch(*args, **kwargs):
        return []

    with patch("bridge.reconciler.fetch_messages_back_to", new=_ok_fetch):
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[_make_dialog("test group")])
        await reconcile_once(
            client=client,
            monitored_groups=["test group"],
            should_respond_fn=AsyncMock(return_value=(False, False)),
            enqueue_agent_session_fn=AsyncMock(),
            find_project_fn=MagicMock(
                return_value={"_key": "testproj", "working_directory": "/tmp/test"}
            ),
        )

    record = get_last_scan_outcome()
    assert record["faulted"] == 0
    assert record["consecutive_total_fault_cycles"] == 0


@pytest.mark.asyncio
async def test_partial_fault_does_not_count_as_total():
    """One chat succeeding is not a total fault, however many others fail."""
    calls = {"n": 0}

    async def _fetch(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("boom")
        return []

    with patch("bridge.reconciler.fetch_messages_back_to", new=_fetch):
        dialogs = [_make_dialog("a", 1), _make_dialog("b", 2)]
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=dialogs)
        await reconcile_once(
            client=client,
            monitored_groups=["a", "b"],
            should_respond_fn=AsyncMock(return_value=(False, False)),
            enqueue_agent_session_fn=AsyncMock(),
            find_project_fn=MagicMock(
                return_value={"_key": "testproj", "working_directory": "/tmp/test"}
            ),
        )

    record = get_last_scan_outcome()
    assert record["attempted"] == 2
    assert record["faulted"] == 1
    assert record["consecutive_total_fault_cycles"] == 0


@pytest.mark.asyncio
async def test_scan_that_never_ran_writes_nothing():
    """A cycle that dies at get_dialogs() leaves NO record — absence is the signal.

    This is the structural distinction: "all faulted" is a written record with a
    positive `attempted`; "never ran" has no representation at all.
    """
    client = AsyncMock()
    client.get_dialogs = AsyncMock(side_effect=ConnectionError("api down"))

    with pytest.raises(ConnectionError):
        await reconcile_once(
            client=client,
            monitored_groups=["test group"],
            should_respond_fn=AsyncMock(),
            enqueue_agent_session_fn=AsyncMock(),
            find_project_fn=MagicMock(),
        )

    assert get_last_scan_outcome() is None


@pytest.mark.asyncio
async def test_cycle_matching_no_chats_records_zero_attempted():
    """ "Cycling with nothing to scan" is distinguishable from "not cycling"."""
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[_make_dialog("unmonitored chat")])

    await reconcile_once(
        client=client,
        monitored_groups=["test group"],
        should_respond_fn=AsyncMock(),
        enqueue_agent_session_fn=AsyncMock(),
        find_project_fn=MagicMock(return_value=None),
    )

    record = get_last_scan_outcome()
    assert record is not None
    assert record["attempted"] == 0
    assert record["faulted"] == 0


# ---------------------------------------------------------------------------
# The watchdog can read it
# ---------------------------------------------------------------------------


def _write_record(**overrides):
    record = {
        "ts": time.time(),
        "pid": os.getpid(),
        "attempted": 3,
        "faulted": 3,
        "consecutive_total_fault_cycles": SCAN_TOTAL_FAULT_CYCLES,
        "sample_error": "ConnectionError: boom",
    }
    record.update(overrides)
    r = _redis()
    r.set(_SCAN_OUTCOME_KEY, json.dumps(record))
    return r


def test_watchdog_flags_sustained_total_fault():
    r = _write_record()
    ok, issue = assess_scan_health(r, os.getpid())
    assert ok is False
    assert "every one of 3 chat(s) faulted" in issue
    assert "ConnectionError" in issue


def test_watchdog_silent_below_threshold():
    r = _write_record(consecutive_total_fault_cycles=SCAN_TOTAL_FAULT_CYCLES - 1)
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_treats_missing_record_as_inconclusive():
    """Absence must never be evidence — that is the #2475 anti-pattern."""
    r = _redis()
    r.delete(_SCAN_OUTCOME_KEY)
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_treats_stale_record_as_inconclusive():
    r = _write_record(ts=time.time() - SCAN_STAMP_FRESHNESS_SECONDS - 60)
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_ignores_record_from_a_dead_process():
    r = _write_record(pid=os.getpid() + 999999)
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_ignores_zero_attempted_record():
    r = _write_record(attempted=0, faulted=0)
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_treats_corrupt_record_as_inconclusive():
    r = _redis()
    r.set(_SCAN_OUTCOME_KEY, "not json")
    assert assess_scan_health(r, os.getpid()) == (True, "")


def test_watchdog_suppresses_verdict_without_a_pid():
    r = _write_record()
    assert assess_scan_health(r, None) == (True, "")


# ---------------------------------------------------------------------------
# Paging, not restarting: the Telegram-outage guard
# ---------------------------------------------------------------------------


def test_sustained_total_fault_pages_but_never_escalates_recovery_level():
    """A total fault is real evidence but cannot attribute blame to this bridge.

    Under a Telegram-side outage every chat faults for every client at once, so
    escalating recovery_level here would restart every watchdog tick for the
    duration of the outage — the #2475 storm shape. It must page instead.
    """
    from monitoring.bridge_watchdog import HealthStatus, check_bridge_health

    _write_record()

    with (
        patch("monitoring.bridge_watchdog.is_bridge_running", return_value=(True, os.getpid())),
        patch("monitoring.bridge_watchdog.are_logs_fresh", return_value=True),
        patch("monitoring.bridge_watchdog.detect_crash_pattern", return_value=(False, None)),
        patch("monitoring.bridge_watchdog.get_recent_crashes", return_value=[]),
        patch("monitoring.bridge_watchdog._enumerate_claude_processes", return_value=[]),
        patch("monitoring.bridge_watchdog.assess_update_flow", return_value=(True, "")),
        patch("monitoring.bridge_watchdog._get_watchdog_redis", return_value=_redis()),
    ):
        status = check_bridge_health()

    assert isinstance(status, HealthStatus)
    assert status.scan_health_ok is False
    assert status.human_alert_needed is True, "must page a human"
    assert status.recovery_level == 0, "must NOT authorise a restart (Telegram-outage guard)"
    assert any("reconciler scan loop failing" in i for i in status.issues)


# ---------------------------------------------------------------------------
# record_scan_outcome unit behaviour
# ---------------------------------------------------------------------------


def test_record_scan_outcome_run_does_not_carry_across_a_restart():
    """A record written by another pid restarts the run rather than extending it."""
    r = _redis()
    r.set(
        _SCAN_OUTCOME_KEY,
        json.dumps(
            {
                "ts": time.time(),
                "pid": os.getpid() + 999999,
                "attempted": 2,
                "faulted": 2,
                "consecutive_total_fault_cycles": 42,
                "sample_error": "",
            }
        ),
    )
    record = record_scan_outcome(attempted=2, faulted=2, redis_client=r)
    assert record["consecutive_total_fault_cycles"] == 1


def test_record_scan_outcome_never_raises_on_redis_failure(caplog):
    r = MagicMock()
    r.get.side_effect = RuntimeError("redis down")
    assert record_scan_outcome(attempted=1, faulted=1, redis_client=r) is None
    assert any("record_scan_outcome" in m for m in caplog.messages)


def test_record_scan_outcome_truncates_sample_error():
    r = _redis()
    record = record_scan_outcome(attempted=1, faulted=1, sample_error="x" * 5000, redis_client=r)
    assert len(record["sample_error"]) == 200
