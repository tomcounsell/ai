"""The email bridge process: its performers and its launchd job. The poll
through intake and the bridge under `bridge.serve` are in
`tests/test_email_kernel.py`."""

import asyncio
import plistlib

import pytest

from bridges.email import EmailBridge
from bridges.email import __main__ as cli
from core import bridge
from tests.test_email_smtp import NOW, action

pytestmark = pytest.mark.spend(usd=0)


def test_the_bridge_offers_email_send_and_its_limits(mailbox):
    b = EmailBridge(mailbox.config())
    assert b.channel == "email" and b.limits is bridge.LIMITS["email"]
    perform, lookup = b.performers()["email.send"]
    act = action()
    done = asyncio.run(perform(act, act.key("effect-0")))
    assert len(mailbox.smtp.accepted) == 1
    found = asyncio.run(lookup(act, act.key("effect-0"), NOW))
    assert found["message_id"] == done["message_id"]


def test_the_launchd_job_keeps_the_bridge_running():
    job = plistlib.loads(cli.plist(python="/usr/bin/python3"))
    assert job["Label"] == "com.valor.email"
    assert job["ProgramArguments"] == ["/usr/bin/python3", "-m", "bridges.email", "run"]
    assert job["KeepAlive"] is True and job["RunAtLoad"] is True
    assert job["StandardOutPath"].endswith("email.log")
