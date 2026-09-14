"""``tools/improvement.py``'s subcommands over a seeded namespace (Task 10)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from tools import improvement as cli
from tools.improvement_control.intents import admit, mark_reconciliation_required
from tools.improvement_control.journal import transition

PK = "valor"  # the CLI is hardcoded to PROJECT_KEY = "valor"


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


class TestDoctor:
    def test_doctor_reports_clean_when_nothing_seeded(self, capsys):
        pk_isolated = "valor"  # doctor is hardcoded to "valor"; assert shape only
        code, payload = run_cli(["doctor"], capsys)
        assert code == 0
        assert "paused" in payload
        assert "wedged" in payload
        del pk_isolated


class TestBudget:
    def test_budget_json_carries_all_three_units(self, capsys):
        code, payload = run_cli(["budget"], capsys)
        assert code == 0
        assert "unit1_slots_in_use" in payload
        assert "unit2" in payload
        assert "unit3" in payload


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
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        digest = pinned_digest()
        case = new_case(digest=digest)
        payload_file = tmp_path / "payload.json"
        payload_file.write_text('{"hypothesis": "operator break-glass"}')

        code, payload = run_cli(
            ["propose", "--case", case.id, "--payload", str(payload_file), "--action-id", "a1"],
            capsys,
        )
        assert code == 0
        assert payload["accepted"] is True


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

    def test_import_refuses_a_missing_archive(self, capsys, tmp_path):
        code, payload = run_cli(["import", "--archive", str(tmp_path / "nope")], capsys)
        assert code == 1


class TestReleaseCompare:
    def test_release_compare_prints_lane_6_placeholder_when_no_records(self, capsys):
        code, payload = run_cli(["release", "compare"], capsys)
        assert code == 0
        assert "releases" in payload
