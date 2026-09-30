"""Export/import round-trip against lane 7's export contract (Task 5)."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.improvement_case import ImprovementCase
from tools.improvement_control import keys
from tools.improvement_control.export import export_namespace, import_namespace
from tools.improvement_control.intents import admit, mark_reconciliation_required
from tools.improvement_control.journal import transition
from utils.redis_client import text_redis


def fresh_pk() -> str:
    return f"test-3215-export-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tmp_root():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _flush_namespace(pk: str):
    r = text_redis()
    for k in r.scan_iter(match=f"improve:{pk}:*"):
        r.delete(k)


class TestExportEmptyNamespace:
    def test_export_on_empty_namespace_writes_zero_cases(self, tmp_root):
        pk = fresh_pk()
        out = export_namespace(pk, tmp_root)
        import json

        data = json.loads((out / "namespace.json").read_text())
        assert data["cases"] == {}
        assert data["schema"] == 1


class TestRoundTrip:
    def test_seeded_case_round_trips_byte_equal_on_head_and_journal(self, tmp_root):
        pk = fresh_pk()
        case = ImprovementCase.create(
            project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC)
        )
        r1 = transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        assert r1.accepted
        r2 = admit(
            pk,
            case.id,
            "a2",
            expected_revision=r1.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r2.accepted
        r3 = mark_reconciliation_required(
            pk, case.id, "a2", expected_revision=r2.revision, generation=1, from_state="admitted"
        )
        assert r3.accepted

        head_before = text_redis().hgetall(keys.head_key(pk, case.id))
        journal_before = text_redis().lrange(keys.journal_key(pk, case.id), 0, -1)

        out = export_namespace(pk, tmp_root)
        _flush_namespace(pk)
        refusal = import_namespace(out, project_key=pk)
        assert refusal is None

        head_after = text_redis().hgetall(keys.head_key(pk, case.id))
        journal_after = text_redis().lrange(keys.journal_key(pk, case.id), 0, -1)
        assert head_after == head_before
        assert journal_after == journal_before

        # The reconciliation_required intent's index membership survives: a
        # restore that dropped the `intents` set would let admit through
        # instead of refusing INTENT_STATE.
        blocked = admit(
            pk,
            case.id,
            "a3",
            expected_revision=int(head_after["revision"]),
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert blocked.reason == "INTENT_STATE"

    def test_unit2_and_ns_pause_round_trip(self, tmp_root):
        """Unit-2 window/reservation hashes and the namespace pause hash are
        restored with the day's spend accounting intact, and every restored
        unit-2 hash carries its retention TTL again."""
        from tools.improvement_control.journal import pause
        from tools.paid_inference_meter import Reservation, reserve

        pk = fresh_pk()
        res = reserve(pk, 1.0, purpose="rsi")
        assert isinstance(res, Reservation)
        pause_result = pause(pk, None, reason="operator pause", by="operator")
        assert pause_result.accepted

        unit2_before = {
            k: text_redis().hgetall(k)
            for k in text_redis().scan_iter(match=f"improve:{pk}:budget:unit2:*")
        }
        ns_pause_before = text_redis().hgetall(keys.pause_key(pk))
        assert unit2_before
        assert ns_pause_before

        out = export_namespace(pk, tmp_root)
        _flush_namespace(pk)
        refusal = import_namespace(out, project_key=pk)
        assert refusal is None

        unit2_after = {
            k: text_redis().hgetall(k)
            for k in text_redis().scan_iter(match=f"improve:{pk}:budget:unit2:*")
        }
        ns_pause_after = text_redis().hgetall(keys.pause_key(pk))
        assert unit2_after == unit2_before
        assert ns_pause_after == ns_pause_before
        for k in unit2_after:
            assert text_redis().ttl(k) > 0

    def test_import_refuses_a_foreign_key_before_writing_anything(self, tmp_root):
        """An archive naming a unit-2 key outside `improve:{pk}:budget:unit2:`
        (or carrying another project's key) is refused `FOREIGN_KEY` with
        nothing written, so a hand-edited archive cannot HSET an arbitrary
        key through the package's private alias."""
        import json

        from tools.paid_inference_meter import Reservation, reserve

        pk = fresh_pk()
        res = reserve(pk, 1.0, purpose="rsi")
        assert isinstance(res, Reservation)
        out = export_namespace(pk, tmp_root)
        _flush_namespace(pk)

        namespace_path = out / "namespace.json"
        data = json.loads(namespace_path.read_text())
        foreign = f"lease:session:foreign-{uuid.uuid4().hex[:8]}"
        data["unit2"][foreign] = {"cents": "100"}
        namespace_path.write_text(json.dumps(data))

        refusal = import_namespace(out, project_key=pk)
        assert refusal is not None
        assert refusal.reason == "FOREIGN_KEY"
        assert not text_redis().exists(foreign)
        assert not text_redis().exists(keys.schema_key(pk))

        # The same archive, with only the foreign key removed, restores.
        del data["unit2"][foreign]
        namespace_path.write_text(json.dumps(data))
        assert import_namespace(out, project_key=pk) is None

        # And an archive for another project is foreign as a whole.
        assert import_namespace(out, project_key=fresh_pk(), force=True).reason == "FOREIGN_KEY"

    def test_import_force_replaces_history_rather_than_appending(self, tmp_root):
        """`--force` onto a live namespace deletes the case's journal, intents
        set, and intent hashes before restoring, so the journal is the
        archive's bytes and the fold can still reach the head."""
        pk = fresh_pk()
        case = ImprovementCase.create(
            project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC)
        )
        r1 = transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        r2 = admit(
            pk,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r2.accepted
        journal_before = text_redis().lrange(keys.journal_key(pk, case.id), 0, -1)
        out = export_namespace(pk, tmp_root)

        # Live state moves on after the export: one more intent on the case.
        r3 = mark_reconciliation_required(
            pk, case.id, "a1", expected_revision=r2.revision, generation=1, from_state="admitted"
        )
        assert r3.accepted

        assert import_namespace(out, project_key=pk, force=True) is None
        journal_after = text_redis().lrange(keys.journal_key(pk, case.id), 0, -1)
        assert journal_after == journal_before
        assert text_redis().hgetall(keys.intent_key(pk, case.id, "a1"))["state"] == "admitted"

    def test_import_force_drops_a_post_export_slot(self, tmp_root):
        """`--force` replaces the namespace slot hash with the archive's. A
        slot admitted after the export is dropped together with the intent
        that held it; merged in, it would survive with no intent to release
        it and stall every later admit at `SLOT_EXHAUSTED`."""
        import json

        pk = fresh_pk()
        case = ImprovementCase.create(
            project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC)
        )
        r1 = transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        assert r1.accepted
        out = export_namespace(pk, tmp_root)
        archive_slots = json.loads((out / "namespace.json").read_text())["slots"]

        # Live state moves on after the export: a1 is admitted and takes the
        # project's only slot.
        r2 = admit(
            pk,
            case.id,
            "a1",
            expected_revision=r1.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=1,
        )
        assert r2.accepted
        assert text_redis().hlen(keys.slots_key(pk)) == 1

        assert import_namespace(out, project_key=pk, force=True) is None
        assert text_redis().hlen(keys.slots_key(pk)) == len(archive_slots)

        head = text_redis().hgetall(keys.head_key(pk, case.id))
        fresh = admit(
            pk,
            case.id,
            "a2",
            expected_revision=int(head["revision"]),
            generation=1,
            action_type="investigate",
            max_concurrent=1,
        )
        assert fresh.accepted, fresh.reason

    def test_import_refuses_a_non_empty_namespace_without_force(self, tmp_root):
        pk = fresh_pk()
        case = ImprovementCase.create(
            project_key=pk, state="investigating", title="t", created_at=datetime.now(UTC)
        )
        transition(
            pk,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        out = export_namespace(pk, tmp_root)
        refusal = import_namespace(out, project_key=pk)
        assert refusal is not None
        assert refusal.reason == "NAMESPACE_NOT_EMPTY"

    def test_import_refuses_a_schema_mismatch(self, tmp_root):
        pk = fresh_pk()
        out = export_namespace(pk, tmp_root)
        import json

        namespace_path = out / "namespace.json"
        data = json.loads(namespace_path.read_text())
        data["schema"] = 999
        namespace_path.write_text(json.dumps(data))
        _flush_namespace(pk)
        refusal = import_namespace(out, project_key=pk)
        assert refusal is not None
        assert refusal.reason == "SCHEMA_MISMATCH"
