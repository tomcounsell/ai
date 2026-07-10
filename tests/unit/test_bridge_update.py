"""Unit tests for bridge/update.py's #1898 release-verify gating.

Covers:

- ``handle_update_command`` gates ``✅`` on ``verify_running_release``:
  a stale bridge → FAILED naming ``bridge running {short} but HEAD is
  {short}``; a matched fleet → ``✅ … (bridge current, worker restarted)``.
- Per-process reload-state string composition.
- ALL stdout lines are scanned for ERROR/warning (a non-first-line warning
  is detected — the old code only scanned the first line).
- Interim message: sent on bridge-plist machines (send called twice: interim
  + final), final-only elsewhere, and an interim send failure never blocks.
- Graceful degradation when the verify import/call raises.
- ``UPDATE_REPORT_CHAT_ID``/``UPDATE_REPORT_REPLY_TO`` env export.
- ``run_boot_release_check``: unconditional boot self-check (sentinel on a
  stale fresh bridge, with or without a pending report; marker cleared) and
  the conditional pending-report flush (OK/FAILED composed from the fresh
  check, file drained on success, left in place on stale).
"""

from __future__ import annotations

import json
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge import update as bridge_update

pytestmark = pytest.mark.unit

SHELL_SHA = "abc1234"
STALE_SHA = "659756a4"


def _info(classification: str, beacon_offset: float, boot_sha: str = SHELL_SHA) -> dict:
    now = time.time()
    return {
        "running": True,
        "boot_sha": STALE_SHA if classification == "stale" else boot_sha,
        "beacon_ts": now + beacon_offset,
        "process_start_ts": now - 10_000,
        "classification": classification,
    }


@pytest.fixture
def event():
    return SimpleNamespace(chat_id=111, message=SimpleNamespace(id=222))


@pytest.fixture
def tg_client():
    client = MagicMock()
    client.send_message = AsyncMock()
    return client


@pytest.fixture
def update_env(monkeypatch):
    """Neutralize side-effecting collaborators; capture the subprocess env."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "bash":
            captured["env"] = kwargs.get("env")
            return subprocess.CompletedProcess(
                cmd, captured.get("rc", 0), captured.get("stdout", "[update] Pull complete\n"), ""
            )
        if cmd and cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, f"{SHELL_SHA}\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge_update, "set_reaction", AsyncMock())
    monkeypatch.setattr(bridge_update, "get_machine_display_name", lambda: "testbox")
    monkeypatch.setattr(bridge_update, "_get_running_sessions_info", lambda: (0, []))
    monkeypatch.setattr(bridge_update, "_queue_fix_session", AsyncMock())
    monkeypatch.setattr(bridge_update, "_bridge_plist_exists", lambda: False)
    monkeypatch.setattr(bridge_update.subprocess, "run", fake_run)
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(return_value={}),
    )
    return captured


def _final_message(tg_client) -> str:
    return tg_client.send_message.call_args.args[1]


# ---------------------------------------------------------------------------
# handle_update_command: OK-gating + reload state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_bridge_reports_failed_not_ok(update_env, tg_client, event, monkeypatch):
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(return_value={"bridge": _info("stale", -9999), "worker": _info("matches", 100)}),
    )
    await bridge_update.handle_update_command(tg_client, event)
    message = _final_message(tg_client)
    assert f"❌ update FAILED @ {SHELL_SHA}" in message
    assert f"bridge running {STALE_SHA} but HEAD is {SHELL_SHA}" in message
    assert "✅" not in message
    assert f"bridge STALE {STALE_SHA}" in message
    assert "worker restarted" in message
    # The fix session is still spawned, marked failed.
    bridge_update._queue_fix_session.assert_awaited_once()
    assert bridge_update._queue_fix_session.await_args.args[-1] is True


@pytest.mark.asyncio
async def test_update_reply_machine_label_non_empty_when_computername_unresolved(
    update_env, tg_client, event, monkeypatch
):
    """#1997 review blocker: /update replies must never render an empty machine label.

    With ComputerName unresolved (``""``), the display chain must fall back to
    the OS hostname — the reply prefix is ``"host-x.local - ..."``, never
    ``" - ..."``.
    """
    import config.machine as config_machine

    # Re-wire the fixture's stub back to the real display resolver, then make
    # ComputerName unresolved so the hostname fallback is exercised end to end.
    monkeypatch.setattr(
        bridge_update, "get_machine_display_name", config_machine.get_machine_display_name
    )
    monkeypatch.setattr(config_machine, "get_machine_name", lambda: "")
    monkeypatch.setattr(config_machine.socket, "gethostname", lambda: "host-x.local")
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(
            return_value={"bridge": _info("matches", -9999), "worker": _info("matches", 100)}
        ),
    )
    await bridge_update.handle_update_command(tg_client, event)
    message = _final_message(tg_client)
    assert message.startswith("host-x.local - ")
    assert not message.startswith(" - ")


@pytest.mark.asyncio
async def test_matched_fleet_reports_ok_with_reload_state(
    update_env, tg_client, event, monkeypatch
):
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(
            return_value={"bridge": _info("matches", -9999), "worker": _info("matches", 100)}
        ),
    )
    await bridge_update.handle_update_command(tg_client, event)
    message = _final_message(tg_client)
    assert f"✅ update OK @ {SHELL_SHA}" in message
    assert "(bridge current, worker restarted)" in message
    bridge_update._queue_fix_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_shell_failure_not_masked_by_matching_verify(
    update_env, tg_client, event, monkeypatch
):
    update_env["rc"] = 1
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(
            return_value={"bridge": _info("matches", -9999), "worker": _info("matches", 100)}
        ),
    )
    await bridge_update.handle_update_command(tg_client, event)
    assert "❌ update FAILED" in _final_message(tg_client)


@pytest.mark.asyncio
async def test_warning_on_non_first_stdout_line_detected(update_env, tg_client, event):
    """The old code scanned only the first stdout line — #1898 claim 4."""
    update_env["stdout"] = (
        "[update] Pull complete\n[update] WARNING: worker .env injection failed\n"
    )
    await bridge_update.handle_update_command(tg_client, event)
    message = _final_message(tg_client)
    assert "spawning agent session to fix" in message
    bridge_update._queue_fix_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_interim_message_on_bridge_plist_machine(update_env, tg_client, event, monkeypatch):
    monkeypatch.setattr(bridge_update, "_bridge_plist_exists", lambda: True)
    await bridge_update.handle_update_command(tg_client, event)
    assert tg_client.send_message.await_count == 2
    interim = tg_client.send_message.await_args_list[0].args[1]
    assert "updating" in interim
    assert "fresh bridge" in interim


@pytest.mark.asyncio
async def test_final_only_without_bridge_plist(update_env, tg_client, event):
    await bridge_update.handle_update_command(tg_client, event)
    assert tg_client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_interim_send_failure_never_blocks_update(update_env, tg_client, event, monkeypatch):
    monkeypatch.setattr(bridge_update, "_bridge_plist_exists", lambda: True)
    tg_client.send_message = AsyncMock(side_effect=[Exception("flood wait"), None])
    await bridge_update.handle_update_command(tg_client, event)
    assert tg_client.send_message.await_count == 2
    assert "update OK" in tg_client.send_message.await_args_list[1].args[1]


@pytest.mark.asyncio
async def test_degrades_gracefully_when_verify_raises(update_env, tg_client, event, monkeypatch):
    monkeypatch.setattr(
        bridge_update,
        "_verify_release_after_update",
        AsyncMock(side_effect=RuntimeError("git exploded")),
    )
    await bridge_update.handle_update_command(tg_client, event)
    message = _final_message(tg_client)
    assert f"✅ update OK @ {SHELL_SHA}" in message  # shell result only


@pytest.mark.asyncio
async def test_env_export_of_chat_context(update_env, tg_client, event):
    await bridge_update.handle_update_command(tg_client, event)
    env = update_env["env"]
    assert env["UPDATE_REPORT_CHAT_ID"] == "111"
    assert env["UPDATE_REPORT_REPLY_TO"] == "222"


@pytest.mark.asyncio
async def test_poll_skipped_when_shell_shows_no_worker_restart(update_env, tg_client, event):
    """No 'Worker restarted' stdout marker → the beacon poll is skipped
    (the shell's --since 0 principle on the inline path)."""
    await bridge_update.handle_update_command(tg_client, event)
    call = bridge_update._verify_release_after_update.await_args
    assert call.kwargs["worker_restarted"] is False


@pytest.mark.asyncio
async def test_poll_enabled_when_shell_shows_worker_restart(update_env, tg_client, event):
    update_env["stdout"] = "[update] Pull complete\n[update] Worker restarted\n"
    await bridge_update.handle_update_command(tg_client, event)
    call = bridge_update._verify_release_after_update.await_args
    assert call.kwargs["worker_restarted"] is True


def test_shell_timeout_covers_verify_poll_budget():
    """The subprocess budget must cover the shell's 30s verify-poll window."""
    assert bridge_update.UPDATE_SHELL_TIMEOUT_SECONDS >= 120 + (
        bridge_update.UPDATE_POLL_ATTEMPTS * bridge_update.UPDATE_POLL_INTERVAL_SECONDS
    )


# ---------------------------------------------------------------------------
# _verify_release_after_update: bounded beacon poll (Race 1, inline path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_beacon_poll_bounded_at_15_attempts(monkeypatch):
    monkeypatch.setattr(bridge_update, "UPDATE_POLL_INTERVAL_SECONDS", 0)
    reads = []
    monkeypatch.setattr(
        "scripts.update.service.read_boot_beacon", lambda p: reads.append(1) or None
    )
    monkeypatch.setattr("scripts.update.git.get_short_sha", lambda pd: SHELL_SHA)
    monkeypatch.setattr("scripts.update.verify.check_machine_identity", lambda pd: {"projects": []})
    canned = {"worker": _info("matches", 100)}
    monkeypatch.setattr("scripts.update.service.verify_running_release", lambda pd, h, mc: canned)
    result = await bridge_update._verify_release_after_update(time.time())
    assert result == canned
    assert len(reads) == bridge_update.UPDATE_POLL_ATTEMPTS


@pytest.mark.asyncio
async def test_beacon_poll_skipped_entirely_when_worker_not_restarted(monkeypatch):
    """worker_restarted=False → straight to classification, zero beacon reads."""
    reads = []
    monkeypatch.setattr(
        "scripts.update.service.read_boot_beacon", lambda p: reads.append(1) or None
    )
    monkeypatch.setattr("scripts.update.git.get_short_sha", lambda pd: SHELL_SHA)
    monkeypatch.setattr("scripts.update.verify.check_machine_identity", lambda pd: {"projects": []})
    canned = {"worker": _info("matches", -50)}
    monkeypatch.setattr("scripts.update.service.verify_running_release", lambda pd, h, mc: canned)
    result = await bridge_update._verify_release_after_update(time.time(), worker_restarted=False)
    assert result == canned
    assert reads == []


@pytest.mark.asyncio
async def test_beacon_poll_stops_early_on_fresh_beacon(monkeypatch):
    monkeypatch.setattr(bridge_update, "UPDATE_POLL_INTERVAL_SECONDS", 0)
    since = time.time()
    reads = []
    monkeypatch.setattr(
        "scripts.update.service.read_boot_beacon",
        lambda p: reads.append(1) or (SHELL_SHA, since + 50),
    )
    monkeypatch.setattr("scripts.update.git.get_short_sha", lambda pd: SHELL_SHA)
    monkeypatch.setattr("scripts.update.verify.check_machine_identity", lambda pd: {"projects": []})
    monkeypatch.setattr("scripts.update.service.verify_running_release", lambda pd, h, mc: {})
    await bridge_update._verify_release_after_update(since)
    assert len(reads) == 1


# ---------------------------------------------------------------------------
# run_boot_release_check: fresh-bridge boot self-check + pending-report flush
# ---------------------------------------------------------------------------

HEAD_SHA = "6b5b998a"


@pytest.fixture
def boot_env(monkeypatch, tmp_path):
    """Point the boot check at a tmp project dir with canned verify results."""
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(bridge_update, "_PROJECT_DIR", tmp_path)
    monkeypatch.setattr(bridge_update, "get_machine_display_name", lambda: "testbox")
    monkeypatch.setattr("scripts.update.git.get_short_sha", lambda pd: HEAD_SHA)
    monkeypatch.setattr(
        "scripts.update.verify.check_machine_identity",
        lambda pd: {"projects": ["p"], "bridge_projects": ["p"]},
    )
    return tmp_path


def _stage_report(tmp_path, worker_state="worker restarted", **extra) -> None:
    payload = {
        "chat_id": "111",
        "reply_to": "222",
        "sha": HEAD_SHA,
        "worker_state": worker_state,
        "staged_ts": time.time(),
    }
    payload.update(extra)
    (tmp_path / "data" / "update-pending-report").write_text(json.dumps(payload))


def _set_verify(monkeypatch, results: dict) -> None:
    monkeypatch.setattr("scripts.update.service.verify_running_release", lambda pd, h, mc: results)


@pytest.mark.asyncio
async def test_pure_cron_stale_boot_writes_sentinel_without_report(
    boot_env, tg_client, monkeypatch
):
    """The #1898 trigger path: no pending report, stale boot → sentinel anyway."""
    _set_verify(monkeypatch, {"bridge": _info("stale", 100), "worker": _info("matches", -50)})
    marker = boot_env / "data" / "update-restart-in-progress"
    marker.write_text("123")

    await bridge_update.run_boot_release_check(tg_client)

    sentinel = boot_env / "data" / "update-release-failed"
    assert sentinel.exists()
    payload = json.loads(sentinel.read_text())
    assert payload["process"] == "bridge"
    assert payload["boot_sha"] == STALE_SHA
    assert payload["head_sha"] == HEAD_SHA
    assert not marker.exists()  # planned-restart marker cleared
    tg_client.send_message.assert_not_awaited()  # nothing staged, nothing sent


@pytest.mark.asyncio
async def test_boot_flush_sends_ok_and_drains_report(boot_env, tg_client, monkeypatch):
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    _stage_report(boot_env)
    # Pre-seed a sentinel from an earlier failed cycle: a healthy boot must
    # clear it, or the watchdog logs CRITICAL every 60s forever.
    (boot_env / "data" / "update-release-failed").write_text('{"process": "bridge"}\n')

    await bridge_update.run_boot_release_check(tg_client)

    tg_client.send_message.assert_awaited_once()
    call = tg_client.send_message.await_args
    assert call.args[0] == 111
    assert call.kwargs.get("reply_to") == 222
    message = call.args[1]
    assert f"✅ update OK @ {HEAD_SHA}" in message
    assert "(bridge restarted, worker restarted)" in message
    assert not (boot_env / "data" / "update-pending-report").exists()
    assert not (boot_env / "data" / "update-release-failed").exists()


@pytest.mark.asyncio
async def test_healthy_boot_clears_preseeded_sentinel_without_report(
    boot_env, tg_client, monkeypatch
):
    """Fleet recovered on a pure-cron boot (no report) → sentinel cleared."""
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    sentinel = boot_env / "data" / "update-release-failed"
    sentinel.write_text('{"process": "bridge", "boot_sha": "659756a4"}\n')

    await bridge_update.run_boot_release_check(tg_client)

    assert not sentinel.exists()
    tg_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_boot_does_not_clear_sentinel(boot_env, tg_client, monkeypatch):
    """An inconclusive boot must not erase a genuine failure record."""
    _set_verify(monkeypatch, {"bridge": _info("unknown", 100), "worker": _info("matches", -50)})
    sentinel = boot_env / "data" / "update-release-failed"
    sentinel.write_text('{"process": "bridge", "boot_sha": "659756a4"}\n')

    await bridge_update.run_boot_release_check(tg_client)

    assert sentinel.exists()


@pytest.mark.asyncio
async def test_boot_flush_stale_sends_failed_and_leaves_report(boot_env, tg_client, monkeypatch):
    _set_verify(monkeypatch, {"bridge": _info("stale", 100), "worker": _info("matches", -50)})
    _stage_report(boot_env)

    await bridge_update.run_boot_release_check(tg_client)

    message = tg_client.send_message.await_args.args[1]
    assert "❌ update FAILED" in message
    assert f"bridge running {STALE_SHA} but HEAD is {HEAD_SHA}" in message
    # Left in place for the watchdog's undrained-report read.
    assert (boot_env / "data" / "update-pending-report").exists()
    assert (boot_env / "data" / "update-release-failed").exists()


@pytest.mark.asyncio
async def test_boot_flush_staged_restart_failure_forces_failed(boot_env, tg_client, monkeypatch):
    """Review blocker (PR #1914): a failed worker kickstart staged by the shell
    must force FAILED even when the fresh classifications all look clean."""
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    _stage_report(boot_env, worker_state="worker restart FAILED", restart_failed=1)

    await bridge_update.run_boot_release_check(tg_client)

    message = tg_client.send_message.await_args.args[1]
    assert "❌ update FAILED" in message
    assert "✅" not in message
    assert "worker restart FAILED" in message


@pytest.mark.asyncio
async def test_boot_flush_staged_verify_failure_forces_failed(boot_env, tg_client, monkeypatch):
    """A worker that crash-looped before its beacon write sets VERIFY_FAILED=1
    in the shell; the flush must not report green off clean-looking beacons."""
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    _stage_report(boot_env, verify_failed=1)

    await bridge_update.run_boot_release_check(tg_client)

    message = tg_client.send_message.await_args.args[1]
    assert "❌ update FAILED" in message
    assert "staged failure" in message


@pytest.mark.asyncio
async def test_boot_flush_legacy_report_without_failure_bits_still_ok(
    boot_env, tg_client, monkeypatch
):
    """A report staged by an older shell (no failure-bit keys) with a healthy
    worker_state still reports OK — absence of the keys is not a failure."""
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    _stage_report(boot_env)

    await bridge_update.run_boot_release_check(tg_client)

    message = tg_client.send_message.await_args.args[1]
    assert f"✅ update OK @ {HEAD_SHA}" in message


@pytest.mark.asyncio
async def test_boot_selfcheck_never_crashes_on_verify_error(boot_env, tg_client, monkeypatch):
    monkeypatch.setattr(
        "scripts.update.service.verify_running_release",
        lambda pd, h, mc: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    marker = boot_env / "data" / "update-restart-in-progress"
    marker.write_text("123")
    await bridge_update.run_boot_release_check(tg_client)  # must not raise
    assert not marker.exists()  # marker still cleared


@pytest.mark.asyncio
async def test_no_pending_report_sends_nothing(boot_env, tg_client, monkeypatch):
    _set_verify(monkeypatch, {"bridge": _info("matches", 100), "worker": _info("matches", -50)})
    await bridge_update.run_boot_release_check(tg_client)
    tg_client.send_message.assert_not_awaited()
