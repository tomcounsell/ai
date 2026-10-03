"""The bridge killed by its pid at the worst points, then restarted."""

import asyncio
import json
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.telegram_emulator import Emulator
from tests.telegram_kernel import Action, Outbox, StandIn, connected

CHAT = "-1008"


@pytest.fixture
def emu():
    e = Emulator(6535).start()
    e.control(chats=[{"id": int(CHAT), "kind": "supergroup"}])
    yield e
    e.stop()


def child(*args: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "tests.telegram_child", *args])


def wait_for(cond, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition did not hold in time"
        time.sleep(0.05)


def test_killed_after_telegram_accepted_the_send(emu):
    at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    emu.control(pause_next=60)
    proc = child("perform", emu.url, CHAT, "accepted, then killed", "task-1:e1")
    wait_for(lambda: emu.own(int(CHAT)))
    proc.kill()
    proc.wait(10)

    async def restart():
        async with connected(emu.url, [CHAT]) as (bridge, store):
            outbox = Outbox(store, bridge)
            outbox.effects["e1"] = (
                Action("telegram.send_message", CHAT, {"text": "accepted, then killed"}),
                at,
            )
            outbox.in_flight.add("e1")  # the intent, with no outcome
            await outbox.reconcile()
            return outbox.outcomes["e1"]

    state, result = asyncio.run(restart())
    [m] = emu.own(int(CHAT))
    assert state == "done" and result["sent"][0]["message_id"] == str(m["id"])


def test_killed_after_receive_and_before_the_read_acknowledgement(emu, tmp_path):
    store, mark = tmp_path / "store.json", tmp_path / "mark"
    proc = child("receive", emu.url, CHAT, str(store), str(mark), "after")
    wait_for(mark.exists)
    mid = emu.inject(int(CHAT), "recorded, then killed")
    wait_for(Path(str(mark) + ".paused").exists)
    proc.kill()
    proc.wait(10)
    assert [r["message_id"] for r in json.loads(store.read_text())["received"]] == [str(mid)]

    async def restart():
        s = StandIn(store, owned=[CHAT])
        async with connected(emu.url, [CHAT], store=s) as (bridge, _):
            await bridge.tick()
            return s.ids(CHAT)

    assert asyncio.run(restart()) == [str(mid)]


def test_killed_before_a_tick_covered_a_dropped_update(emu, tmp_path):
    store, mark = tmp_path / "store.json", tmp_path / "mark"
    proc = child("receive", emu.url, CHAT, str(store), str(mark), "none")
    wait_for(mark.exists)
    dropped = emu.inject(int(CHAT), "104: its update is lost", live=False)
    after = emu.inject(int(CHAT), "105: delivered")
    wait_for(lambda: store.exists() and json.loads(store.read_text())["received"])
    proc.kill()
    proc.wait(10)

    async def restart():
        s = StandIn(store, owned=[CHAT])
        async with connected(emu.url, [CHAT], store=s):
            return sorted(s.ids(CHAT), key=int)

    assert asyncio.run(restart()) == [str(dropped), str(after)]


def test_sigterm_during_a_perform_records_the_message_and_its_outcome(emu, tmp_path):
    store, mark = tmp_path / "store", tmp_path / "mark"
    emu.control(pause_next=1.5)
    proc = child("run", emu.url, CHAT, str(store), str(mark))
    wait_for(mark.exists)
    proc.send_signal(signal.SIGTERM)
    proc.wait(15)
    state, result = json.loads(Path(str(store) + ".outcome").read_text())
    [m] = emu.own(int(CHAT))
    assert state == "done" and result["sent"][0]["message_id"] == str(m["id"])
