"""Improvement senders hand off to an agent instead of writing to a chat (#3588).

``cmd_propose_amendment`` and ``hand_off_digest`` build a :class:`Finding` and call
``hand_off``. The journal write and the Room resolution are faked at their seams;
the assertions are on the Finding each one builds and on the exit code / return.
"""

from __future__ import annotations

import argparse
import hashlib
import uuid
from types import SimpleNamespace

import pytest

from models.improvement_investigation import ImprovementInvestigation
from reflections import agent_handoff as ah
from reflections import improvement_assumption_digest as digest
from tools import improvement as cli

ROOM = "valor|telegram:-100999"


class _Lease:
    def release(self, *_a):
        pass


@pytest.fixture
def amendment(monkeypatch):
    """``cmd_propose_amendment`` with the journal, lease and Room faked."""
    case_id = f"test-3588-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(cli, "_acquire_lease", lambda cid: (_Lease(), "k", 1))
    monkeypatch.setattr(cli, "_project", lambda cid: None)
    monkeypatch.setattr("tools.improvement_control.journal.read_head", lambda pk, cid: None)
    monkeypatch.setattr(
        "tools.improvement_control.journal.transition",
        lambda *a, **k: SimpleNamespace(accepted=True),
    )
    monkeypatch.setattr(
        "reflections.utilities.load_local_projects", lambda: [{"slug": "valor", "x": 1}]
    )
    monkeypatch.setattr(ah, "project_eng_room_id", lambda project: ROOM)
    yield case_id
    for row in ImprovementInvestigation.query.filter(project_key=cli.PROJECT_KEY):
        if row.case_id == case_id:
            row.delete()


def _args(case_id: str) -> argparse.Namespace:
    return argparse.Namespace(case=case_id, request="allow weekly digests", json=True)


@pytest.mark.parametrize(
    "kind,exit_code,notified", [("created", 0, True), ("unreachable", 1, False)]
)
def test_propose_amendment_hands_off_and_exits_on_delivery(
    amendment, monkeypatch, capsys, kind, exit_code, notified
):
    seen = []

    def fake_hand_off(finding):
        seen.append(finding)
        if kind == "created":
            return ah.HandoffResult("created", "sess-1", None)
        return ah.HandoffResult("unreachable", None, "not-owner")

    monkeypatch.setattr(ah, "hand_off", fake_hand_off)
    assert cli.cmd_propose_amendment(_args(amendment)) == exit_code
    (finding,) = seen
    assert finding.source == "improvement_charter_amendment"
    assert finding.room_id == ROOM
    assert finding.dedup_key == amendment
    assert finding.requires_delivery is True
    assert "allow weekly digests" in finding.facts[0]
    assert f'"notified": {str(notified).lower()}' in capsys.readouterr().out


def test_propose_amendment_without_an_eng_room_journals_and_exits_nonzero(
    amendment, monkeypatch, capsys
):
    monkeypatch.setattr(ah, "project_eng_room_id", lambda project: None)
    monkeypatch.setattr(ah, "hand_off", lambda f: pytest.fail("no Room, no handoff"))
    assert cli.cmd_propose_amendment(_args(amendment)) == 1
    assert '"notified": false' in capsys.readouterr().out


@pytest.fixture
def digest_room(monkeypatch):
    monkeypatch.setattr("reflections.utilities.resolve_project_for_repo", lambda: {"slug": "valor"})
    monkeypatch.setattr(ah, "project_eng_room_id", lambda project: ROOM)


def test_hand_off_digest_builds_a_verbatim_delivery_required_finding(digest_room, monkeypatch):
    seen = []
    monkeypatch.setattr(
        ah, "hand_off", lambda f: seen.append(f) or ah.HandoffResult("created", "s", None)
    )
    message = "Improvement assumption digest\n\nThis is a status report."
    assert digest.hand_off_digest(message) is True
    (finding,) = seen
    assert finding.source == "improvement_assumption_digest"
    assert finding.verbatim_payload == message and finding.requires_delivery is True
    assert finding.dedup_key.endswith(hashlib.sha256(message.encode()).hexdigest()[:12])


def test_hand_off_digest_dedup_key_changes_with_the_message(digest_room, monkeypatch):
    keys = []
    monkeypatch.setattr(
        ah, "hand_off", lambda f: keys.append(f.dedup_key) or ah.HandoffResult("created", "s", None)
    )
    digest.hand_off_digest("one")
    digest.hand_off_digest("two")
    assert keys[0] != keys[1]


def test_hand_off_digest_unreachable_is_false_and_warns(digest_room, monkeypatch, caplog):
    monkeypatch.setattr(ah, "hand_off", lambda f: ah.HandoffResult("unreachable", None, "no-room"))
    with caplog.at_level("WARNING"):
        assert digest.hand_off_digest("msg") is False
    assert "no-room" in caplog.text


def test_hand_off_digest_without_room_is_false(monkeypatch):
    monkeypatch.setattr("reflections.utilities.resolve_project_for_repo", lambda: {"slug": "valor"})
    monkeypatch.setattr(ah, "project_eng_room_id", lambda project: None)
    monkeypatch.setattr(ah, "hand_off", lambda f: pytest.fail("no Room, no handoff"))
    assert digest.hand_off_digest("msg") is False
