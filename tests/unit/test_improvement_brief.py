"""``tools/improvement_brief.py::build_brief``: the pinned charter first, then
the case, evidence, prior answers, open investigations, the intake pool, the
resolution rule, the claim rule, the envelope, and the subcommand list, in
that order and bounded (lane 5, #3217, task 4)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from models.improvement_evidence import ImprovementEvidence
from tools import improvement as cli
from tools import improvement_brief as brief
from tools import improvement_investigations as inv

PK = "valor"

CHARTER_TEXT = (
    "---\nowner: Tom Counsell\nversion: 2\n---\n\n"
    "# Valor recursive self-improvement charter\n\n"
    "## 3. Choosing the next improvement\n\nInference first.\n"
)


def seed_charter() -> ImprovementCharter:
    return ImprovementCharter.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        digest="sha256:" + hashlib.sha256(CHARTER_TEXT.encode()).hexdigest(),
        state="active",
        version=2,
        text=CHARTER_TEXT,
    )


def seed_case(charter, *, evidence: int = 0, **overrides) -> ImprovementCase:
    ids = []
    now = datetime.now(UTC)
    for i in range(evidence):
        row = ImprovementEvidence.create(
            project_key=PK,
            created_at=now - timedelta(minutes=i),
            observed_at=now - timedelta(minutes=i),
            kind="inspiration",
            classification="unknown",
            source_ref=f"seed:{i}",
            text=f"evidence line {i}",
        )
        ids.append(row.id)
    fields = dict(
        project_key=PK,
        state="investigating",
        title="Cheap inference source",
        summary="charter §3 names inference first",
        created_at=now,
        priority_area="inference",
        ranking_rationale="§3 first priority",
        charter_digest=charter.digest,
        evidence_ids=json.dumps(ids),
    )
    fields.update(overrides)
    return ImprovementCase.create(**fields)


class TestOrder:
    def test_opens_with_charter(self):
        charter = seed_charter()
        case = seed_case(charter, evidence=2)
        text = brief.build_brief(case.id, PK)
        first_line = next(line for line in text.splitlines() if line.strip())
        assert first_line == "---"
        assert text.startswith(CHARTER_TEXT)
        assert "# Valor recursive self-improvement charter" in text
        assert f"version 2, digest {charter.digest}" in text

    def test_first_non_blank_line_is_the_charter_heading_when_no_frontmatter(self):
        heading_only = "# Valor recursive self-improvement charter\n\nbody\n"
        charter = ImprovementCharter.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            digest="sha256:x",
            state="active",
            version=3,
            text=heading_only,
        )
        case = seed_case(charter)
        first_line = next(line for line in brief.build_brief(case.id, PK).splitlines() if line)
        assert first_line == "# Valor recursive self-improvement charter"

    def test_sections_appear_in_the_plan_order(self):
        charter = seed_charter()
        case = seed_case(charter, evidence=1)
        text = brief.build_brief(case.id, PK)
        headings = [
            "## Case ",
            "## Evidence",
            "## Prior answers",
            "## Open investigations",
            "## Intake pool",
            "## Resolution rule",
            "## Claim rule",
            "## Candidate envelope",
            "## Subcommands",
        ]
        positions = [text.index(h) for h in headings]
        assert positions == sorted(positions)
        assert text.index(CHARTER_TEXT) < positions[0]

    def test_no_evidence_sentinel(self):
        charter = seed_charter()
        case = seed_case(charter)
        text = brief.build_brief(case.id, PK)
        assert "No evidence rows are attached to this case" in text
        assert "no ranking snapshot yet" in text

    def test_ranking_position_and_factors_come_from_the_real_snapshot(self, tmp_path):
        """The snapshot's ``order`` is the list of ``RankedCase.as_dict()``
        entries ``write_snapshot`` writes, never bare ids."""
        from models.improvement_controller_state import ImprovementControllerState
        from models.verifying_artifact_store import VerifyingArtifactStore
        from tools.improvement_ranking import rank, write_snapshot

        charter = seed_charter()
        other = seed_case(charter, priority_area="skills")
        case = seed_case(charter, evidence=2)
        store = VerifyingArtifactStore(base_path=str(tmp_path / "content"))
        ranked = rank(
            [other, case], evidence=[], investigations=[], experiments=[], charter=charter
        )
        ref = write_snapshot(ranked, previous_ref=None, charter_digest=charter.digest, store=store)
        ImprovementControllerState.get_or_create(PK).record(last_snapshot_ref=ref)
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(tmp_path / "content"))
            text = brief.build_brief(case.id, PK)
        mine = next(r for r in ranked if r.case_id == case.id)
        assert f"Ranking: position {mine.position} of 2" in text
        assert "Factors: " + ", ".join(f"{k}={v}" for k, v in sorted(mine.factors.items())) in text
        assert "not in the latest snapshot" not in text

    def test_evidence_is_capped_newest_first_with_the_truncation_stated(self):
        charter = seed_charter()
        case = seed_case(charter, evidence=brief.BRIEF_MAX_EVIDENCE + 3)
        text = brief.build_brief(case.id, PK)
        assert f"showing {brief.BRIEF_MAX_EVIDENCE} of {brief.BRIEF_MAX_EVIDENCE + 3}" in text
        assert text.index("evidence line 0") < text.index(
            f"evidence line {brief.BRIEF_MAX_EVIDENCE - 1}"
        )
        assert f"evidence line {brief.BRIEF_MAX_EVIDENCE + 2}" not in text

    def test_prior_answers_open_investigations_and_intake_pool(self):
        charter = seed_charter()
        case = seed_case(charter)
        done = inv.open_investigation(
            PK,
            kind="web_research",
            case_id=case.id,
            uncertainty="u",
            query="resolved question",
            decision_affected="d",
            expected_information_value="v",
        )
        inv.resolve(done.investigation_id, interpretation="the answer was forty-two")
        inv.open_investigation(
            PK,
            kind="probe",
            case_id=case.id,
            uncertainty="still open",
            query="open question",
            decision_affected="d",
            expected_information_value="v",
        )
        rejected = seed_case(
            charter,
            state="rejected",
            title="Dead idea",
            rejected_reason="rejected by evaluation",
            evaluation_ids=json.dumps(["eval-1"]),
        )
        pool = inv.open_investigation(
            PK,
            kind="inspiration_intake",
            case_id=None,
            uncertainty="u",
            query="https://example.com/talk",
            decision_affected="d",
            expected_information_value="v",
        )
        text = brief.build_brief(case.id, PK)
        assert "the answer was forty-two" in text
        assert rejected.id in text and "rejected by evaluation" in text and "eval-1" in text
        assert "still open" in text
        assert pool.investigation_id in text and "https://example.com/talk" in text

    def test_is_deterministic_and_bounded(self):
        charter = seed_charter()
        case = seed_case(charter, evidence=3)
        assert brief.build_brief(case.id, PK) == brief.build_brief(case.id, PK)

    def test_static_sections_and_template_digest(self):
        text = brief.brief_template_text()
        assert "retrieval_parameters" in text
        assert '{"limit": 10}' in text
        assert "#2082" in text
        assert "docs/features/hybrid-retrieval-eval.md" in text
        assert "provisional assumption" in text
        assert "retrieved_at" in text
        for sub in ("investigation open", "revise-model", "propose-amendment", "experiment freeze"):
            assert sub in text
        assert brief.brief_template_digest() == hashlib.sha256(text.encode()).hexdigest()

    def test_missing_case_and_missing_charter_are_lookup_errors(self):
        with pytest.raises(LookupError, match="CASE_NOT_FOUND"):
            brief.build_brief("nope", PK)
        case = ImprovementCase.create(
            project_key=PK, created_at=datetime.now(UTC), title="orphan", state="observed"
        )
        with pytest.raises(LookupError, match="CHARTER_NOT_PINNED"):
            brief.build_brief(case.id, PK)


class TestCli:
    def test_brief_prints_the_brief(self, capsys):
        charter = seed_charter()
        case = seed_case(charter, evidence=1)
        code = cli.main(["brief", "--case", case.id])
        out = capsys.readouterr().out
        assert code == 0
        assert out.startswith(CHARTER_TEXT)

    def test_brief_refuses_a_missing_case(self, capsys):
        code = cli.main(["--json", "brief", "--case", "nope"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert (code, payload["reason"]) == (1, "CASE_NOT_FOUND")
