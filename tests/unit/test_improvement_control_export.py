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
