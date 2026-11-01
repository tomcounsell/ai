"""Mail under Valor's name leaves only as a chat session's `email.send`
(docs/plans/one-voice-mail.md). Valor's Google sign-in for `gws` reads mail
and cannot send it, and no mail password is where a turn reads.

The scope list rollout step 6 signs in with is checked always. The live
checks run on a signed-in Mac with `VALOR_LIVE=1` and `VALOR_MAIL_LIVE=1`.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from core.settings import settings

ROLLOUT = Path(__file__).resolve().parent.parent / "docs" / "rollout.md"
SENDS = ("https://mail.google.com/", "gmail.modify", "gmail.compose", "gmail.send", "gmail.insert")
MAIL_PASSWORDS = ("IMAP_PASSWORD", "SMTP_PASSWORD")
live = pytest.mark.skipif(
    os.environ.get("VALOR_LIVE") != "1" or os.environ.get("VALOR_MAIL_LIVE") != "1",
    reason="Valor's live Google sign-in: VALOR_LIVE=1 and VALOR_MAIL_LIVE=1",
)


def test_rollout_signs_gws_in_to_read_mail_and_never_to_send_it():
    (scopes,) = re.findall(r"gws auth login --scopes (\S+?)`", ROLLOUT.read_text())
    granted = scopes.split(",")
    assert "https://www.googleapis.com/auth/gmail.readonly" in granted
    assert not [s for s in granted if any(s.endswith(send) or s == send for send in SENDS)]


def gws(*args: str, params: dict | None = None, body: dict | None = None) -> tuple[int, str]:
    argv = ["gws", *args, *(["--params", json.dumps(params)] if params else [])]
    argv += ["--json", json.dumps(body)] if body else []
    got = subprocess.run(argv, capture_output=True, text=True, check=False)
    return got.returncode, got.stdout + got.stderr


@live
def test_valors_google_token_reads_mail_and_google_refuses_its_send():
    _, status = gws("auth", "status")
    assert "gmail.readonly" in status and not [s for s in SENDS if s in status]
    code, listed = gws("gmail", "users", "messages", "list", params={"userId": "me", "maxResults": 1})
    assert code == 0 and '"messages"' in listed
    raw = "VG86IHZhbG9yQHl1ZGEubWUKU3ViamVjdDogb25lIHZvaWNlCgpyZWZ1c2VkCg"  # To: valor@yuda.me, to itself
    code, sent = gws("gmail", "users", "messages", "send", params={"userId": "me"}, body={"raw": raw})
    assert code != 0 and '"code": 403' in sent and "insufficient authentication scopes" in sent


@live
def test_no_mail_password_is_where_a_turn_reads():
    """The vault `.env` every turn's shell loads, and the 1Password vaults
    a turn's `op` reads, hold no mailbox password: only each Mac's
    `mail-keys` does."""
    vault = Path(settings.vault_env).read_text()
    assert not [n for n in MAIL_PASSWORDS if re.search(rf"(?m)^\s*(export\s+)?{n}\s*=\s*\S", vault)]
    vaults = json.loads(subprocess.run(["op", "vault", "list", "--format", "json"], capture_output=True,
                                       text=True, check=True).stdout)  # fmt: skip
    for v in vaults:
        items = json.loads(subprocess.run(["op", "item", "list", "--vault", v["id"], "--format", "json"],
                                          capture_output=True, text=True, check=True).stdout)  # fmt: skip
        assert not [i["title"] for i in items if i["title"].upper() in MAIL_PASSWORDS], v["name"]
