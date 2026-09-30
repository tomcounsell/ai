"""Room model + resolution (Task 10, docs/plans/durability-room-job-agentrun.md).

Room = (project_key, addressee), addressee in ``telegram:{chat_id}`` |
``email:{address}`` | ``system``. KeyField set {project_key, addressee} was
ratified one-shot by the schema gate; no IndexedField on Room.

These tests hit real Redis (repo convention) under a ``test-`` project-key
prefix and delete every record via the ORM afterward.
"""

import json
import uuid
from pathlib import Path

import pytest

from models.room import (
    SYSTEM_ADDRESSEE,
    Room,
    addressee_for_session,
    email_addressee,
    room_id,
    room_id_for_session,
    telegram_addressee,
)
from tests.db_claim import subprocess_env

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch_project_key():
    """Unique test- prefixed project key; ORM-scoped cleanup afterward."""
    key = f"test-room-{uuid.uuid4().hex[:8]}"
    yield key
    for room in Room.query.filter(project_key=key):
        room.delete()


class TestAddressees:
    def test_telegram_addressee_includes_chat_id(self):
        assert telegram_addressee(-1001234) == "telegram:-1001234"
        assert telegram_addressee("42") == "telegram:42"

    def test_email_addressee_normalizes_case(self):
        assert email_addressee("Tom@Yuda.me") == "email:tom@yuda.me"

    def test_system_addressee_constant(self):
        assert SYSTEM_ADDRESSEE == "system"

    def test_room_id_is_pipe_composite(self):
        assert room_id("valor", "telegram:42") == "valor|telegram:42"


class TestAddresseeForSession:
    """Sessions map onto a Room via project_key + chat_id shape."""

    class _FakeSession:
        def __init__(self, project_key=None, chat_id=None):
            self.project_key = project_key
            self.chat_id = chat_id

    def test_numeric_chat_id_is_telegram(self):
        s = self._FakeSession("valor", "-100123")
        assert addressee_for_session(s) == "telegram:-100123"
        assert room_id_for_session(s) == "valor|telegram:-100123"

    def test_email_chat_id_is_email(self):
        s = self._FakeSession("valor", "Tom@Yuda.me")
        assert addressee_for_session(s) == "email:tom@yuda.me"

    def test_non_numeric_chat_id_falls_through_to_email_branch(self):
        # react-transport-derivation Task 2: numeric_peer("a@b.com") returns
        # None, and that None must NOT short-circuit to SYSTEM_ADDRESSEE --
        # control must fall through to the "@" in raw email branch, or every
        # email-bridge session silently re-homes to the system Room.
        s = self._FakeSession("valor", "a@b.com")
        assert addressee_for_session(s) == "email:a@b.com"

    def test_chatless_session_is_system_room(self):
        # spike-1: every session belongs to a Room; chatless sessions get the
        # per-project system Room (unifies chat_id="0" / chat_id=session_id /
        # default-DM inheritance).
        assert addressee_for_session(self._FakeSession("valor", None)) == SYSTEM_ADDRESSEE
        # Non-numeric, non-email chat_id (e.g. chat_id=session_id) is system too.
        assert addressee_for_session(self._FakeSession("valor", "sdlc-local-42")) == (
            SYSTEM_ADDRESSEE
        )

    def test_placeholder_zero_chat_id_is_system_room(self):
        # #2497: reflection sessions carry the literal placeholder chat_id="0",
        # which is not a valid Telegram peer (the relay's zero-guard drops it).
        # It is the third synthetic-addressee convention this model unifies —
        # a placeholder, not an address — so it maps to the system Room.
        assert addressee_for_session(self._FakeSession("valor", "0")) == SYSTEM_ADDRESSEE
        assert room_id_for_session(self._FakeSession("valor", "0")) == "valor|system"

    def test_multi_hyphen_pseudo_numeric_chat_id_is_system_not_valueerror(self):
        # "--5".lstrip("-").isdigit() is True but int("--5") raises — the
        # helper must treat it as chatless garbage, not crash a hot path.
        assert addressee_for_session(self._FakeSession("valor", "--5")) == SYSTEM_ADDRESSEE

    def test_no_project_key_yields_no_room(self):
        assert room_id_for_session(self._FakeSession(None, "42")) is None


class TestRoomResolve:
    def test_resolve_round_trips_and_is_idempotent(self, scratch_project_key):
        addressee = telegram_addressee(-100999)
        room = Room.resolve(scratch_project_key, addressee, display_name="Test Chat")
        assert room.project_key == scratch_project_key
        assert room.addressee == addressee
        assert room.room_id == room_id(scratch_project_key, addressee)

        again = Room.resolve(scratch_project_key, addressee)
        assert again.room_id == room.room_id
        assert len(Room.query.filter(project_key=scratch_project_key)) == 1

    def test_dm_and_group_resolve_identically(self, scratch_project_key):
        """The DM-loss class closer: a private chat is just another Room."""
        dm = Room.resolve(scratch_project_key, telegram_addressee(4242))
        group = Room.resolve(scratch_project_key, telegram_addressee(-1004242))
        assert dm.room_id != group.room_id
        assert len(Room.query.filter(project_key=scratch_project_key)) == 2


class TestRoomInbox:
    def test_append_and_peek_round_trip(self, scratch_project_key):
        room = Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
        entry = {"chat_id": "-100999", "message_id": 7, "text": "hello"}
        room.append_inbox(entry)
        peeked = room.peek_inbox()
        assert peeked[-1]["message_id"] == 7
        assert peeked[-1]["text"] == "hello"
        room.clear_inbox()
        assert room.peek_inbox() == []

    def test_inbox_is_capped(self, scratch_project_key, monkeypatch):
        import models.room as room_mod

        monkeypatch.setattr(room_mod, "ROOM_INBOX_MAX_ENTRIES", 5)
        room = Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
        for i in range(9):
            room.append_inbox({"message_id": i})
        peeked = room.peek_inbox()
        assert len(peeked) == 5
        # Newest survive the trim.
        assert [e["message_id"] for e in peeked] == [4, 5, 6, 7, 8]
        room.clear_inbox()

    def test_inbox_key_is_companion_of_primary_key(self, scratch_project_key):
        # "::" companion suffix keeps the inbox out of index-drift hash counts.
        room = Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
        assert room.inbox_key.endswith("::inbox")
        room.clear_inbox()

    def test_append_never_raises(self, scratch_project_key, monkeypatch):
        """Durable-append failures must be loud in logs but never crash intake."""
        room = Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
        entry_that_cannot_serialize = {"bad": object()}
        assert room.append_inbox(entry_that_cannot_serialize) is False
        assert room.append_inbox({"ok": 1}) is True
        room.clear_inbox()


class TestGuardedRepair:
    """Risk 2: a new model must not re-trigger the #2207 phantom index flood."""

    def test_room_is_guarded_elsewhere(self):
        from scripts.popoto_index_cleanup import _GUARDED_ELSEWHERE

        assert "Room" in _GUARDED_ELSEWHERE

    def test_generic_sweep_excludes_room(self):
        from scripts.popoto_index_cleanup import _get_all_models

        names = {m.__name__ for m in _get_all_models()}
        assert "Room" not in names

    def test_repair_indexes_quarantines_identity_less_hash(self, scratch_project_key):
        from popoto.redis_db import POPOTO_REDIS_DB

        # Plant an identity-less Room hash (no project_key/addressee) — the
        # exact shape that, unguarded, gets re-indexed on every rebuild.
        phantom_key = "Room:None:test-phantom"
        POPOTO_REDIS_DB.hset(phantom_key, mapping={"display_name": json.dumps("ghost")})
        try:
            healthy = Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
            stale, rebuilt = Room.repair_indexes()
            assert POPOTO_REDIS_DB.exists(phantom_key) == 0, (
                "identity-less hash must be quarantined, not re-indexed"
            )
            # The healthy record survives the rebuild and stays queryable.
            assert any(
                r.room_id == healthy.room_id
                for r in Room.query.filter(project_key=scratch_project_key)
            )
            assert isinstance(stale, int) and isinstance(rebuilt, int)
        finally:
            POPOTO_REDIS_DB.delete(phantom_key)


class TestDriftCoverage:
    def test_room_registered_in_index_drift(self):
        from agent.index_drift import covered_model_names

        assert "Room" in covered_model_names()

    def test_room_drift_reconciles_clean(self, scratch_project_key):
        from agent.index_drift import DRIFT_COVERED_MODELS, reconcile_model_index

        Room.resolve(scratch_project_key, SYSTEM_ADDRESSEE)
        hash_count, queryable, drifted, truncated = reconcile_model_index(
            DRIFT_COVERED_MODELS["Room"]
        )
        if not truncated:
            assert drifted is False
            assert hash_count == queryable


class TestPeerParseSingleHome:
    """react-transport-derivation (#2629): the peer parse lives once, in
    ``utils/peer.py`` (stdlib-only, no ``popoto`` import), and
    ``models.room`` / ``TelegramRelayOutputHandler`` both delegate to it.

    ``utils/peer.py`` -- not ``models/peer.py``. A first attempt placed the
    module inside the ``models`` package; being stdlib-only *inside the
    file* bought nothing, because importing any submodule of a package
    always runs that package's ``__init__`` first, and ``models/__init__.py``
    imports every Popoto model (i.e. ``popoto`` and ``redis``). The AST-only
    predecessor of this test scanned ``models/peer.py``'s own import
    statements and stayed green while that regression was live -- it never
    asserted the property that actually determines import cost. This
    version measures that property directly: import the module in a bare
    subprocess (no pytest/conftest fixtures already dragging in the stack)
    and assert ``popoto``/``redis`` are absent from ``sys.modules``, with a
    wall-clock ceiling so a reintroduced heavy import fails loudly instead
    of merely failing an AST check that can't see the real cost. See PR
    #2651 review (blocker) and the ~110x regression measured there
    (0.02s on ``main`` vs. 2.2-3.5s on the ``models/peer.py`` branch)."""

    def test_utils_peer_import_stays_cheap(self):
        import subprocess
        import sys

        script = (
            "import sys, json, time\n"
            "t0 = time.time()\n"
            "from utils.peer import deliverable_telegram_peer, numeric_peer\n"
            "elapsed = time.time() - t0\n"
            "print(json.dumps({\n"
            "    'popoto_loaded': 'popoto' in sys.modules,\n"
            "    'redis_loaded': 'redis' in sys.modules,\n"
            "    'elapsed': elapsed,\n"
            "}))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            env=subprocess_env(project_root=str(REPO_ROOT)),
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["popoto_loaded"] is False, (
            "importing utils.peer pulled in popoto -- the whole point of "
            "splitting this module out of models/room.py is defeated"
        )
        assert payload["redis_loaded"] is False, (
            "importing utils.peer pulled in redis -- the whole point of "
            "splitting this module out of models/room.py is defeated"
        )
        # Generous ceiling (bare interpreter + module import is ~0.02-0.05s
        # on this repo's hardware; popoto import alone measured 2.2-3.5s in
        # the PR #2651 review). 1s catches a reintroduced heavy dependency
        # without being flaky on a loaded CI box.
        assert payload["elapsed"] < 1.0, (
            f"utils.peer import took {payload['elapsed']:.2f}s -- expected "
            "well under 1s for a stdlib-only module"
        )

    def test_models_room_reexports_peer_helpers(self):
        from models.room import deliverable_telegram_peer as room_deliverable
        from models.room import numeric_peer as room_numeric_peer
        from utils.peer import deliverable_telegram_peer as canonical_deliverable
        from utils.peer import numeric_peer as canonical_numeric_peer

        assert room_numeric_peer is canonical_numeric_peer
        assert room_deliverable is canonical_deliverable

    def test_output_handler_delegates_to_models_peer(self):
        from agent.output_handler import TelegramRelayOutputHandler

        assert TelegramRelayOutputHandler._deliverable_telegram_peer("--5") is False
        assert TelegramRelayOutputHandler._deliverable_telegram_peer("-100123") is True
        assert TelegramRelayOutputHandler._deliverable_telegram_peer("0") is False
