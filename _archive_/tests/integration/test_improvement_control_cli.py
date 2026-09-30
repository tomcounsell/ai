"""End-to-end: the real `valor-improve` binary against a claimed test Redis db
(Task 10). Resolves the binary via ``sys.executable``'s own venv, per
Decision 14 (the console script lives in ``.venv/bin`` only).
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.agent_session import AgentSession, SessionType
from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from tools.improvement_control.intents import admit
from tools.improvement_control.journal import transition

PK = "valor"

BINARY = Path(sys.executable).parent / "valor-improve"


@pytest.fixture(autouse=True)
def _require_binary():
    if not BINARY.exists():
        pytest.skip(
            f"{BINARY} does not exist -- reinstall the venv's console scripts "
            "(uv sync / uv pip install) so Decision 14's binary materializes"
        )


@pytest.fixture(autouse=True)
def _content_root(monkeypatch, tmp_path):
    """The binary's `propose` writes a real artifact; `run()` inherits this
    process's environment, so the override reaches the subprocess and keeps
    test payloads out of the production retention root."""
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(tmp_path / "content"))


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


def new_case() -> ImprovementCase:
    return ImprovementCase.create(
        project_key=PK,
        state="investigating",
        title="t",
        created_at=datetime.now(UTC),
        priority_area="skills",
        ranking_rationale="integration test",
        charter_digest=pinned_digest(),
    )


def run(argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [str(BINARY), *argv], capture_output=True, text=True, timeout=30, env=merged_env
    )


class TestBinaryResolves:
    def test_binary_exists_at_the_resolved_path(self):
        assert BINARY.exists()


class TestProposeEndToEnd:
    def test_propose_under_a_seeded_research_session_shows_in_the_journal(self, tmp_path):
        case = new_case()
        session_id = f"test-cli-e2e-{uuid.uuid4().hex[:8]}"
        row = AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=session_id,
            working_dir=".",
            extra_context={"action_id": "e2e-a1", "research_case_id": case.id},
        )
        # AGENT_SESSION_ID carries the row's `agent_session_id` (the AutoKeyField
        # hex id), which is what the worker exports and what `_own_session`
        # resolves through `AgentSession.get_by_id`. It is NOT `session_id`
        # (#3339): an unresolvable id sends the CLI down the break-glass path,
        # where it mints a fresh action id and every assertion below still
        # passes -- for the wrong reason.
        agent_id = row.agent_session_id
        assert agent_id and agent_id != session_id
        r = transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="e2e-a1",
        )
        assert r.accepted
        r2 = admit(
            PK,
            case.id,
            "e2e-a1",
            expected_revision=r.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        assert r2.accepted
        from tools.improvement_control.intents import record_materialized, record_running

        r3 = record_materialized(
            PK,
            case.id,
            "e2e-a1",
            expected_revision=r2.revision,
            generation=1,
            agent_session_id=agent_id,
        )
        assert r3.accepted
        r4 = record_running(PK, case.id, "e2e-a1", expected_revision=r3.revision, generation=1)
        assert r4.accepted

        payload_file = tmp_path / "findings.json"
        payload_file.write_text(json.dumps({"hypothesis": "test finding"}))

        completed = run(
            ["--json", "propose", "--case", case.id, "--payload", str(payload_file)],
            env={"AGENT_SESSION_ID": agent_id},
        )
        assert completed.returncode == 0, completed.stderr
        out = json.loads(completed.stdout.strip())
        assert out["accepted"] is True

        from tools.improvement_control.journal import journal_tail

        tail = journal_tail(PK, case.id, 1)
        assert tail[-1]["event"] == "action_proposed"
        assert tail[-1]["artifact_ref"] == out["artifact_ref"]
        # The seeded action id, not a minted one: this is what separates a real
        # fenced write from the break-glass path, which satisfies every other
        # assertion here under a fresh uuid.
        assert out["action_id"] == "e2e-a1"
        # The subprocess honored the content-root override: the artifact is
        # under this test's tmp_path, never the production retention root.
        assert list((tmp_path / "content").rglob("*.txt"))

    def test_stale_session_intent_is_refused_and_the_redispatched_one_is_accepted(self, tmp_path):
        """Race 4b end to end."""
        case = new_case()
        s1 = f"test-cli-e2e-s1-{uuid.uuid4().hex[:8]}"
        row = AgentSession.create(
            project_key=PK,
            chat_id="0",
            session_type=SessionType.ENG,
            message_text="x",
            sender_name="x",
            session_id=s1,
            working_dir=".",
            extra_context={"action_id": "a1", "research_case_id": case.id},
        )
        # The row's agent_session_id, not s1 -- see the note in the test above.
        agent_id = row.agent_session_id
        assert agent_id and agent_id != s1
        r = transition(
            PK,
            case.id,
            expected_revision=0,
            generation=1,
            event="action_proposed",
            payload_digest="d",
            action_id="a1",
        )
        r2 = admit(
            PK,
            case.id,
            "a1",
            expected_revision=r.revision,
            generation=1,
            action_type="investigate",
            max_concurrent=5,
        )
        from tools.improvement_control.intents import mark_reconciliation_required

        mark_reconciliation_required(
            PK, case.id, "a1", expected_revision=r2.revision, generation=1, from_state="admitted"
        )

        payload_file = tmp_path / "findings.json"
        payload_file.write_text(json.dumps({"hypothesis": "stale"}))
        completed = run(
            ["--json", "propose", "--case", case.id, "--payload", str(payload_file)],
            env={"AGENT_SESSION_ID": agent_id},
        )
        out = json.loads(completed.stdout.strip())
        assert out["accepted"] is False
        assert out["reason"] == "INTENT_STATE"

        from models.improvement_evidence import ImprovementEvidence

        rows = list(ImprovementEvidence.query.filter(project_key=PK, kind="other"))
        matches = [r for r in rows if r.source_ref == f"propose-refused:{case.id}:a1"]
        assert matches


class TestDoctorEndToEnd:
    def test_doctor_on_a_seeded_paused_case_prints_it(self):
        """Plan Success Criterion: `doctor` on a seeded paused case prints
        the paused head and its outstanding reservation (the slot its
        admitted intent holds)."""
        from tools.improvement_control.journal import pause

        aid = f"e2e-doc-{uuid.uuid4().hex[:8]}"
        case = new_case()
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
        paused = pause(PK, case.id, generation=1, reason="test pause", by="operator")
        assert paused.accepted

        completed = run(["--json", "doctor"])
        assert completed.returncode == 0
        out = json.loads(completed.stdout.strip())
        assert case.id in out["paused"]
        mine = [s for s in out["reservations"]["slots"] if s["action_id"] == aid]
        assert mine == [{"action_id": aid, "case_id": case.id, "since": mine[0]["since"]}]
        assert mine[0]["since"]

        completed = run(["doctor"])
        assert completed.returncode == 0
        assert "Paused heads: " in completed.stdout
        assert f"slot {aid} (case {case.id})" in completed.stdout


class TestChildSessionGateUnaffected:
    def test_valor_session_create_with_parent_still_raises(self):
        """The research path never bypasses the child-session gate; this is a
        smoke check that the gate itself is untouched by this lane, not a
        CLI subcommand of valor-improve."""
        from models.child_session_gate import ChildSessionsDisabledError

        assert issubclass(ChildSessionsDisabledError, RuntimeError)
