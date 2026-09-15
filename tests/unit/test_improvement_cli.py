"""``tools/improvement.py``'s subcommands over a seeded namespace (Task 10)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from models.verifying_artifact_store import VerifyingArtifactStore
from tools import improvement as cli
from tools.improvement_control.intents import admit, mark_reconciliation_required
from tools.improvement_control.journal import journal_tail, read_head, transition

PK = "valor"  # the CLI is hardcoded to PROJECT_KEY = "valor"


@pytest.fixture(autouse=True)
def _content_root(monkeypatch, tmp_path):
    """Every `propose` writes a real artifact; keep it out of the production
    retention root (`~/.popoto/improvement_content`), which lane 7's export
    contract treats as durable evidence."""
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(tmp_path / "content"))


def new_case(*, digest: str) -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK,
        state="investigating",
        title="t",
        created_at=datetime.now(UTC),
        priority_area="skills",
        ranking_rationale="because the test says so",
        charter_digest=digest,
    )


def pinned_digest() -> str:
    pinned = ImprovementCharter.pinned(PK)
    if pinned is not None:
        return pinned.digest
    row = ImprovementCharter.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        digest=f"sha256:{uuid.uuid4().hex}",
        state="active",
    )
    return row.digest


def run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli.main(["--json", *argv])
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


class TestCaseShow:
    def test_case_show_json_shape(self, capsys):
        digest = pinned_digest()
        case = new_case(digest=digest)
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        code, payload = run_cli(["case", "show", "--case", case.id], capsys)
        assert code == 0
        assert payload["head"]["revision"] == 1
        assert len(payload["journal"]) == 1


class TestCaseExplain:
    def test_case_explain_names_the_blocking_intent(self, capsys):
        digest = pinned_digest()
        case = new_case(digest=digest)
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        r = admit(
            PK,
            case.id,
            "a1",
            expected_revision=1,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r.accepted
        mark_reconciliation_required(
            PK, case.id, "a1", expected_revision=r.revision, generation=1, from_state="admitted"
        )
        code, payload = run_cli(["case", "explain", "--case", case.id], capsys)
        assert code == 0
        assert payload["blocking_action_ids"] == ["a1"]
        assert payload["charter_pinned"] is True


class TestResumeForceClearsAWedgeOnAnUnpausedHead:
    def test_resume_without_force_exits_1_naming_the_action_id(self, capsys):
        digest = pinned_digest()
        case = new_case(digest=digest)
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        r = admit(
            PK,
            case.id,
            "a1",
            expected_revision=1,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        mark_reconciliation_required(
            PK, case.id, "a1", expected_revision=r.revision, generation=1, from_state="admitted"
        )
        code = cli.main(["resume", "--case", case.id])
        assert code == 1

    def test_resume_force_journals_cancelled_and_reports_not_paused(self, capsys):
        digest = pinned_digest()
        case = new_case(digest=digest)
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        r = admit(
            PK,
            case.id,
            "a1",
            expected_revision=1,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        mark_reconciliation_required(
            PK, case.id, "a1", expected_revision=r.revision, generation=1, from_state="admitted"
        )
        code = cli.main(["resume", "--case", case.id, "--force"])
        out = capsys.readouterr().out
        assert code == 0
        assert "not paused" in out
        assert "cancelled 1 intent" in out

        from tools.improvement_control.intents import list_intents

        assert list_intents(PK, case.id)[0].state == "cancelled"

        # A following admit succeeds now that the wedge is cleared.
        from tools.improvement_control.journal import read_head

        head = read_head(PK, case.id)
        r2 = admit(
            PK,
            case.id,
            "a2",
            expected_revision=head.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r2.accepted


class TestPause:
    def test_pause_surfaces_its_reason_and_refuses_a_busy_case_lease(self, capsys):
        """`pause --case` during a controller tick (the case lease held by
        another holder) refuses `CASE_BUSY` rather than presenting generation
        0 to the transition and surfacing a bare `paused: False`; once the
        lease is free the accepted pause carries its reason too."""
        from config.settings import settings
        from tools.improvement_control.lease import default_lease

        case = new_case(digest=pinned_digest())
        transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        lease = default_lease()
        lease_key = f"improve:{PK}:{case.id}:lease"
        held = lease.acquire(lease_key, ttl=settings.improvement.lease_ttl_seconds)
        assert held is not None
        try:
            code, payload = run_cli(["pause", "--case", case.id], capsys)
            assert code == 1
            assert payload == {"accepted": False, "reason": "CASE_BUSY"}
            code = cli.main(["pause", "--case", case.id])
            assert code == 1
            assert capsys.readouterr().out.strip() == "paused: False (CASE_BUSY)"
        finally:
            lease.release(lease_key, held)

        code, payload = run_cli(["pause", "--case", case.id], capsys)
        assert code == 0
        assert payload == {"accepted": True, "reason": "OK"}
        assert read_head(PK, case.id).paused is True


class TestDoctor:
    def test_doctor_reports_clean_when_nothing_seeded(self, capsys):
        pk_isolated = "valor"  # doctor is hardcoded to "valor"; assert shape only
        code, payload = run_cli(["doctor"], capsys)
        assert code == 0
        assert "paused" in payload
        assert "wedged" in payload
        assert set(payload["reservations"]) == {"slots", "unit2"}
        del pk_isolated

    def test_doctor_prints_an_outstanding_slot_with_its_holder(self, capsys):
        """Plan Success Criterion: doctor reads the unit-1 slot hash and the
        open unit-2 window for real, naming each held slot with the case whose
        live intent holds it, and prints the clean line only when every
        reservation view is empty."""
        aid = f"doc-{uuid.uuid4().hex[:8]}"
        case = new_case(digest=pinned_digest())
        r = transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id=aid,
        )
        r2 = admit(
            PK,
            case.id,
            aid,
            expected_revision=r.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=50,
        )
        assert r2.accepted

        code, payload = run_cli(["doctor"], capsys)
        assert code == 0
        mine = [s for s in payload["reservations"]["slots"] if s["action_id"] == aid]
        assert len(mine) == 1
        assert mine[0]["case_id"] == case.id
        assert "reserved_usd" in payload["reservations"]["unit2"]

        code = cli.main(["doctor"])
        out = capsys.readouterr().out
        assert code == 0
        assert "no outstanding reservations" not in out
        assert "Outstanding reservations: " in out
        assert f"slot {aid} (case {case.id})" in out

    def test_doctor_outage_break_glass_drill(self, capsys, monkeypatch):
        """Success Criterion 2's break-glass drill, end to end. `doctor`
        reports `namespace unreachable: <error>` with exit 2 during the
        outage and writes nothing; once the namespace is reachable again it
        reads the same pre-outage head."""
        import redis.exceptions

        digest = pinned_digest()
        case = new_case(digest=digest)
        r = transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d1",
            action_id="a1",
        )
        assert r.accepted

        def _raise(*_a, **_k):
            raise redis.exceptions.ConnectionError("simulated outage")

        monkeypatch.setattr("tools.improvement_control.journal._control_redis", _raise)
        monkeypatch.setattr("tools.improvement_control.intents._control_redis", _raise)

        code = cli.main(["doctor"])
        out = capsys.readouterr().out
        assert code == 2
        assert out.startswith("namespace unreachable:")

        monkeypatch.undo()

        code_after = cli.main(["doctor"])
        out_after = capsys.readouterr().out
        assert code_after == 0
        assert not out_after.startswith("namespace unreachable")
        head = read_head(PK, case.id)
        assert head is not None
        assert head.revision == r.revision


class TestBudget:
    def test_budget_json_carries_all_three_units(self, capsys):
        code, payload = run_cli(["budget"], capsys)
        assert code == 0
        assert "unit1_slots_in_use" in payload
        assert "unit2" in payload
        assert "unit3" in payload

    def test_budget_lists_unknown_metered_receipts_with_their_window(self, capsys):
        """Charter §8 / plan Error State Rendering: an unknown-metered receipt
        is printed in its own block with the window it was charged to, so it
        never reads as zero spend."""
        from tools.paid_inference_meter import record_receipt

        marker = f"case-unknown-{uuid.uuid4().hex[:8]}"
        record_receipt(
            project_key=PK,
            purpose="rsi",
            model=None,
            prompt_tokens=None,
            completion_tokens=None,
            metering="unknown",
            usd=1.25,
            case_id=marker,
            day_key="2026-01-02",
        )
        record_receipt(
            project_key=PK,
            purpose="rsi",
            model="m",
            prompt_tokens=1,
            completion_tokens=1,
            metering="exact",
            usd=0.5,
            case_id=marker,
            day_key="2026-01-02",
        )
        code, payload = run_cli(["budget"], capsys)
        assert code == 0
        mine = [r for r in payload["unit2_receipted_unknown"] if r["case_id"] == marker]
        assert len(mine) == 1
        assert mine[0]["day_key"] == "2026-01-02"
        assert mine[0]["usd"] == 1.25

        code = cli.main(["budget"])
        out = capsys.readouterr().out
        assert code == 0
        assert "unknown-metered receipts" in out
        assert f"window=2026-01-02 usd=1.25 case={marker}" in out


class TestProposeUnderAgentSessionId:
    def test_propose_under_a_non_research_session_refuses(self, capsys, monkeypatch, tmp_path):
        from models.agent_session import AgentSession, SessionType

        digest = pinned_digest()
        case = new_case(digest=digest)
        session_id = f"test-cli-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            extra_context={},
        )
        monkeypatch.setenv("AGENT_SESSION_ID", session_id)
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"hypothesis": "x"}')

        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file)], capsys
        )
        assert code == 1
        assert payload["reason"] == "NOT_A_RESEARCH_SESSION"

    def test_propose_break_glass_without_agent_session_id(self, capsys, monkeypatch, tmp_path):
        """Data Flow steps 2, 4, and 5 on the accepted path: the payload is
        loadable from the verifying store through the reference the journal
        entry carries, and the projection reflects the new revision."""
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        digest = pinned_digest()
        case = new_case(digest=digest)
        payload_file = tmp_path / "payload.json"
        payload_bytes = b'{"hypothesis": "operator break-glass"}'
        payload_file.write_bytes(payload_bytes)

        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 0
        assert payload["accepted"] is True

        ref = payload["artifact_ref"]
        assert ref.startswith("$CF:")
        assert VerifyingArtifactStore().load(ref) == payload_bytes
        assert journal_tail(PK, case.id, 1)[-1]["artifact_ref"] == ref

        reloaded = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert reloaded.revision == payload["revision"]
        assert reloaded.state == "investigating"

    def test_propose_break_glass_without_action_id_mints_one(self, capsys, monkeypatch, tmp_path):
        """An operator's propose with no `--action-id` journals a minted,
        non-empty action_id; an empty one is a proposal the scheduler adapter
        skips forever."""
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        case = new_case(digest=pinned_digest())
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"hypothesis": "no action id given"}')

        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file)], capsys
        )
        assert code == 0
        assert payload["action_id"]
        assert journal_tail(PK, case.id, 1)[-1]["action_id"] == payload["action_id"]

    def test_propose_refused_by_intent_state_keeps_the_payload_as_evidence(
        self, capsys, monkeypatch, tmp_path
    ):
        """Data Flow step 9: a session-bound propose against a case with no
        running intent is refused `INTENT_STATE`, and the `propose-refused:`
        evidence row's `detail` loads to the payload bytes."""
        from models.agent_session import AgentSession, SessionType
        from models.improvement_evidence import ImprovementEvidence

        digest = pinned_digest()
        case = new_case(digest=digest)
        session_id = f"test-cli-{uuid.uuid4().hex[:8]}"
        AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            extra_context={"action_id": "a1", "research_case_id": case.id},
        )
        monkeypatch.setenv("AGENT_SESSION_ID", session_id)
        payload_file = tmp_path / "payload.json"
        payload_bytes = b'{"hypothesis": "refused but kept"}'
        payload_file.write_bytes(payload_bytes)

        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file)], capsys
        )
        assert code == 1
        assert payload["reason"] == "INTENT_STATE"

        rows = [
            r
            for r in ImprovementEvidence.query.filter(project_key=PK, kind="other")
            if r.source_ref == f"propose-refused:{case.id}:a1"
        ]
        assert len(rows) == 1
        assert rows[0].detail.startswith("$CF:")
        assert VerifyingArtifactStore().load(rows[0].detail) == payload_bytes

    def test_propose_refuses_when_the_store_write_fails(self, capsys, monkeypatch, tmp_path):
        """The one filesystem write in `propose` is a reason-coded boundary
        like every other: an `OSError` from the store is `ARTIFACT_WRITE_FAILED`,
        never a traceback."""
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        case = new_case(digest=pinned_digest())
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"hypothesis": "disk full"}')

        def _fail(self, *_a, **_k):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(VerifyingArtifactStore, "save", _fail)
        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 1
        assert payload == {"accepted": False, "reason": "ARTIFACT_WRITE_FAILED"}
        assert read_head(PK, case.id) is None


class TestProposeValidation:
    def test_propose_refuses_a_case_with_no_ranking_rationale(self, capsys, tmp_path):
        case = ImprovementCase.create(
            project_key=PK,
            state="investigating",
            title="t",
            created_at=datetime.now(UTC),
            priority_area="skills",
            charter_digest=pinned_digest(),
        )
        payload_file = tmp_path / "payload.json"
        payload_file.write_text("x")
        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 1
        assert payload["reason"] == "MISSING_RANKING_RATIONALE"

    def test_propose_refuses_an_empty_payload(self, capsys, tmp_path):
        case = new_case(digest=pinned_digest())
        payload_file = tmp_path / "payload.json"
        payload_file.write_text("   ")
        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 1
        assert payload["reason"] == "EMPTY_PAYLOAD"


class TestExportImport:
    def test_export_writes_an_archive(self, capsys, tmp_path):
        code, payload = run_cli(["export", "--root", str(tmp_path)], capsys)
        assert code == 0
        assert "path" in payload

    def test_export_artifacts_index_carries_the_proposal_reference(
        self, capsys, monkeypatch, tmp_path
    ):
        """Lane 7's export contract: `artifacts.json["digests"]` names every
        journaled `artifact_ref`, so a restore can prove the store holds
        what the journal cites."""
        from pathlib import Path

        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        case = new_case(digest=pinned_digest())
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"hypothesis": "exported"}')
        code, proposed = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 0
        ref = proposed["artifact_ref"]

        code, exported = run_cli(["export", "--root", str(tmp_path)], capsys)
        assert code == 0
        artifacts = json.loads((Path(exported["path"]) / "artifacts.json").read_text())
        mine = [d for d in artifacts["digests"] if d["case_id"] == case.id]
        assert mine == [
            {
                "case_id": case.id,
                "action_id": "a1",
                "artifact_ref": ref,
                "payload_digest": journal_tail(PK, case.id, 1)[-1]["payload_digest"],
            }
        ]

    def test_import_refuses_a_missing_archive(self, capsys, tmp_path):
        code, payload = run_cli(["import", "--archive", str(tmp_path / "nope")], capsys)
        assert code == 1


class TestReleaseCompare:
    def test_release_compare_prints_lane_6_placeholder_when_no_records(self, capsys):
        code, payload = run_cli(["release", "compare"], capsys)
        assert code == 0
        assert "releases" in payload
