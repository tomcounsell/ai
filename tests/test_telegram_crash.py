"""The bridge killed by its pid at the worst points, then restarted."""

import asyncio
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.bridges import of_type
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
    chat,
    child_env,
    connected,
    emu_chat,
    ids,
    machine,
    outcome,
    reconcile,
    release,
    send,
)

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def emu():
    e = Emulator(6535).start()
    yield e
    e.stop()


@pytest.fixture
def c(emu, tmp_path):
    group = chat()
    emu.control(chats=[emu_chat(group)])
    with machine(tmp_path, [group]):
        yield group


def child(tmp_path, group, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "tests.telegram_child", *args], env=child_env(tmp_path, [group])
    )


def wait_for(cond, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition did not hold in time"
        time.sleep(0.05)


def test_killed_after_telegram_accepted_the_send(emu, dsn, tmp_path, c):
    effect = asyncio.run(release(dsn, send(c, "accepted, then killed")))
    emu.control(pause_next=60)
    proc = child(tmp_path, c, "perform", emu.url, dsn, str(tmp_path), effect)
    wait_for(lambda: emu.own(int(c)))
    proc.kill()
    proc.wait(10)

    async def restart():
        assert await of_type(dsn, "effect.intent", effect_id=effect)
        assert await outcome(dsn, effect) is None
        async with connected(emu.url, dsn, tmp_path) as bridge:
            return await reconcile(dsn, bridge, effect)

    out = asyncio.run(restart())
    [m] = emu.own(int(c))
    assert out.kind == "done"
    assert asyncio.run(outcome(dsn, effect))["result"]["sent"][0]["message_id"] == str(m["id"])


def test_killed_after_receive_and_before_the_read_acknowledgement(emu, dsn, tmp_path, c):
    mark = tmp_path / "mark"
    proc = child(tmp_path, c, "receive", emu.url, dsn, str(tmp_path), str(mark), "after")
    wait_for(mark.exists)
    mid = emu.inject(int(c), "recorded, then killed")
    wait_for(Path(str(mark) + ".paused").exists)
    proc.kill()
    proc.wait(10)
    assert asyncio.run(ids(dsn, c)) == [str(mid)]

    async def restart():
        async with connected(emu.url, dsn, tmp_path) as bridge:
            await bridge.tick()
        return await ids(dsn, c)

    assert asyncio.run(restart()) == [str(mid)]


def test_killed_before_receive_the_restart_records_it(emu, dsn, tmp_path, c):
    mark = tmp_path / "mark"
    proc = child(tmp_path, c, "receive", emu.url, dsn, str(tmp_path), str(mark), "before")
    wait_for(mark.exists)
    mid = emu.inject(int(c), "killed before it was recorded")
    wait_for(Path(str(mark) + ".paused").exists)
    proc.kill()
    proc.wait(10)
    assert asyncio.run(ids(dsn, c)) == []

    async def restart():
        async with connected(emu.url, dsn, tmp_path):
            return await ids(dsn, c)

    assert asyncio.run(restart()) == [str(mid)]


def test_killed_before_a_tick_covered_a_dropped_update(emu, dsn, tmp_path, c):
    mark = tmp_path / "mark"
    proc = child(tmp_path, c, "receive", emu.url, dsn, str(tmp_path), str(mark), "none")
    wait_for(mark.exists)
    dropped = emu.inject(int(c), "its update is lost", live=False)
    after = emu.inject(int(c), "delivered")
    wait_for(lambda: asyncio.run(ids(dsn, c)))
    proc.kill()
    proc.wait(10)

    async def restart():
        async with connected(emu.url, dsn, tmp_path):
            return sorted(await ids(dsn, c), key=int)

    assert asyncio.run(restart()) == [str(dropped), str(after)]


def test_sigterm_during_a_perform_records_the_message_and_its_outcome(emu, dsn, tmp_path, c):
    mark = tmp_path / "mark"
    effect = asyncio.run(release(dsn, send(c, "slow send")))
    emu.control(pause_next=1.5)
    proc = child(tmp_path, c, "run", emu.url, dsn, str(tmp_path), str(mark), "slow send")
    wait_for(mark.exists)
    proc.send_signal(signal.SIGTERM)
    proc.wait(15)
    [m] = emu.own(int(c))
    assert asyncio.run(outcome(dsn, effect))["result"]["sent"][0]["message_id"] == str(m["id"])
