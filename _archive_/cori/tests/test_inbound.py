"""`InboundItem` is the shape seams §1.11 fixes, and its header keys are
lowercase so routing never guesses a case. Plan 03 task 2."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from schemas.inbound import InboundItem

FIELDS = {
    "id": "item-1",
    "connector": "gmail",
    "account": "tom@yuda.me",
    "external_id": "18f0",
    "headers": {"from": "Ana <ana@psyoptimal.com>", "subject": "next week"},
    "received_at": datetime(2026, 9, 21, tzinfo=UTC),
    "space": "psyoptimal",
    "routed_by": None,
}


def item(**over) -> InboundItem:
    return InboundItem(**{**FIELDS, **over})


def test_inbound_item_is_strict_and_frozen():
    built = item()
    assert built.connector == "gmail" and built.space == "psyoptimal"
    with pytest.raises(ValidationError):
        item(body="the message text")
    with pytest.raises(ValidationError):
        built.space = "other"
    for bad in ("imap", "sms", ""):
        with pytest.raises(ValidationError):
            item(connector=bad)


def test_headers_keys_are_lowercase():
    assert item(headers={"from": "a@b.c", "message-id": "<1>"}).headers["from"]
    for bad in ({"From": "a@b.c"}, {"from": "a@b.c", "Subject": "hi"}):
        with pytest.raises(ValidationError):
            item(headers=bad)
