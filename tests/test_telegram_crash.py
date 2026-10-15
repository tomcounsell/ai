"""The bridge killed by its pid at the worst points, then restarted."""

import asyncio
import contextlib
import signal
import subprocess
import sys
import time

import pytest

from tests.bridges import of_type
from tests.telegram_emulator import Emulator
from tests.telegram_port import (
    chat,
    child_env,
    connected,
    emu_chat,
    machine,
    outcome,
    reconcile,
    release,
    send,
)

pytestmark = pytest.mark.spend(usd=0)


@pytest.fixture
def emu():
    e = Emulator().start()
    yield e
    e.stop()


@pytest.fixture
def c(emu, tmp_path):
    group = chat()
    emu.control(chats=[emu_chat(group)])
    with machine(tmp_path, [group]):
        yield group


@contextlib.contextmanager
def child(tmp_path, group, *args: str):
    """The bridge in a process of its own, killed by its pid when the
    block ends, so a failing test leaks no bridge holding the machine's
    lock."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.telegram_child", *args], env=child_env(tmp_path, [group])
    )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def wait_for(cond, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "condition did not hold in time"
        time.sleep(0.05)


def test_killed_after_telegram_accepted_the_send(emu, dsn, tmp_path, c):
    effect = asyncio.run(release(dsn, send(c, "accepted, then killed")))
    emu.control(pause_next=60)
    with child(tmp_path, c, "perform", emu.url, dsn, str(tmp_path), effect) as proc:
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


def test_sigterm_during_a_perform_records_the_message_and_its_outcome(emu, dsn, tmp_path, c):
    mark = tmp_path / "mark"
    effect = asyncio.run(release(dsn, send(c, "slow send")))
    emu.control(pause_next=1.5)
    with child(tmp_path, c, "run", emu.url, dsn, str(tmp_path), str(mark), "slow send") as proc:
        wait_for(mark.exists)
        proc.send_signal(signal.SIGTERM)
        proc.wait(15)
    [m] = emu.own(int(c))
    assert asyncio.run(outcome(dsn, effect))["result"]["sent"][0]["message_id"] == str(m["id"])
