"""The email bridge process: its performers and its launchd job. The watch
through intake and the bridge under `bridge.serve` are in
`tests/test_email_kernel.py`."""

import asyncio
import dataclasses
import plistlib

import pytest

from bridges.email import EmailBridge
from bridges.email import __main__ as cli
from core import bridge
from core.settings import settings
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


def test_keys_copies_the_mail_logins_into_the_kernel_key_directory(tmp_path, monkeypatch, capsys):
    vault = tmp_path / ".env"
    vault.write_text(
        "".join(f"{name}=x\n" for name in ["IMAP_USER", "IMAP_PASSWORD", "SMTP_USER", "SMTP_PASSWORD"])
    )
    keys = tmp_path / "keys"
    keys.mkdir()
    moved = dataclasses.replace(settings, vault_env=str(vault), pg_passfile=str(keys / "pgpass"))
    monkeypatch.setattr(cli, "settings", moved)
    assert cli.main(["keys"]) == 0
    assert moved.mail_keyfile == str(keys / "mail-keys")
    assert (keys / "mail-keys").exists()
    assert "IMAP_PASSWORD: written" in capsys.readouterr().out


def test_run_logs_with_timestamps(monkeypatch):
    seen = {}

    async def serve(_bridge):
        return None

    monkeypatch.setattr("bridges.email.serve", serve)
    monkeypatch.setattr("bridges.email.EmailBridge", lambda: object())
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kw: seen.update(kw))
    assert cli.main(["run"]) == 1
    assert "%(asctime)s" in seen["format"]
