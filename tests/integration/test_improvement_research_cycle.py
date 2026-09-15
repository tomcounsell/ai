"""The complete research cycle on seeded evidence (lane 5, #3217, task 8).

Three end-to-end tests over the claimed test database (autouse
``redis_test_db``, tests/conftest.py), each on the console entry point's
project key because ``valor-improve`` and the planner's proposal are bound to
``"valor"``:

- ``test_verdict_moves_ranking``: a seeded ``Memory`` becomes an
  ``inspiration`` row through the real evidence tick (injected ``gh`` runner,
  a judge transport that is never reached because the promise detector is
  off), the planner tick opens the case and proposes for it, the research
  steps run through the Python functions the CLI wraps, the experiment
  freezes with an injected known-item builder, lane 4's runner evaluates it
  on live arms with the approving test judge, the verdict is applied, and
  the next tick's snapshot names the case under ``left`` with the evaluation
  id. No LLM is called; nothing leaves the process but the arm subprocesses.
- ``test_rejected_is_not_reproposed``: a ``rejected`` case (moved through
  ``set_state`` and ``projection.apply``) with a dedup identity refuses a
  re-open on matching evidence; the ids attach to it and the tick proposes
  nothing for it.
- ``test_cli_brief_ranking_report`` (and its binary twin): ``brief``,
  ``ranking``, and ``report`` through ``tools.improvement.main`` and through
  the installed ``valor-improve`` binary.

Every artifact (snapshots, protocols, proposal payloads) lands under
``tmp_path``: the retention root is redirected for this process and, through
the environment, for the arm and CLI subprocesses.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import types
import uuid
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_evidence import ImprovementEvidence
from models.improvement_experiment import ImprovementExperiment
from models.improvement_model_revision import ImprovementModelRevision
from models.improvement_release import ImprovementRelease
from models.verifying_artifact_store import VerifyingArtifactStore, verifying_artifact_store
from reflections import improvement_collect
from reflections import improvement_plan as planner
from tools import improvement as cli
from tools import improvement_experiment as ex
from tools import improvement_investigations as inv
from tools import paid_inference_meter
from tools.improvement_control import keys
from tools.improvement_control.journal import journal_tail, read_head, set_state
from tools.improvement_control.lease import default_lease
from tools.improvement_control.projection import apply as project_case
from tools.improvement_control.scheduler_adapter import _unadmitted_proposal
from tools.improvement_eval import runner
from tools.improvement_ranking import load_snapshot
from tools.improvement_report import HEADING_CHANGE, HEADING_LIMITS, HEADING_MEASURED
from tools.memory_eval.query_set import KnownItem

PK = cli.PROJECT_KEY

SEED = "charter-s3:inference"
SEED_URL = "https://example.com/meta-model-api"
SEED_REFERENCE = json.dumps(
    {"seed": SEED, "priority_area": "inference", "seeded_by": "task 8", "url": SEED_URL}
)
LESSON_LINE = "- lesson: always run pytest before claiming a green unit test suite"

BINARY = Path(sys.executable).parent / "valor-improve"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _retention_root(monkeypatch, tmp_path):
    """Every store this cycle touches resolves to ``tmp_path``: the per-call
    stores read the env var, the module singleton (lane 4's protocol writer)
    is redirected directly, and the arm and CLI subprocesses inherit the
    variable."""
    root = tmp_path / "improvement_content"
    root.mkdir()
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(root))
    monkeypatch.setattr(verifying_artifact_store, "base_path", str(root))
    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    return root


@pytest.fixture
def charter_path(tmp_path) -> Path:
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    return path


@pytest.fixture
def charter(charter_path) -> ImprovementCharter:
    row = ImprovementCharter.load_from_file(charter_path, project_key=PK)
    assert row is not None
    return row


@pytest.fixture
def store(tmp_path):
    return VerifyingArtifactStore(base_path=str(tmp_path / "improvement_content"))


def tick(charter_path, store, **kw) -> planner.TickResult:
    return planner.plan_tick(PK, charter_path=charter_path, store=store, **kw)


def cases() -> list[ImprovementCase]:
    return list(ImprovementCase.query.filter(project_key=PK))


def reload_case(case_id: str) -> ImprovementCase:
    row = ImprovementCase.query.get(project_key=PK, id=case_id)
    assert row is not None
    return row


def events(case_id: str) -> list[str]:
    return [e["event"] for e in journal_tail(PK, case_id, 100)]


def move_state(case_id: str, state: str) -> None:
    """The only sanctioned state move: set_state under the lease, then apply."""
    lease = default_lease()
    key = keys.lease_key(PK, case_id)
    generation = lease.acquire(key, ttl=90)
    assert generation is not None
    try:
        result = set_state(PK, case_id, generation=generation, state=state, by="test")
        assert result.accepted, result
    finally:
        lease.release(key, generation)
    project_case(PK, case_id)


def run_main(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


def seed_memory(content: str, *, source: str, reference: str = ""):
    from models.memory import Memory

    record = Memory(
        agent_id="test-3217",
        project_key=PK,
        content=content,
        importance=5.0,
        source=source,
        reference=reference,
    )
    assert record.save() is not False
    return record


class OpenMeter:
    """A unit-2 meter that admits everything and records what it was asked."""

    def __init__(self):
        self.reservations: list[dict] = []
        self.settled: list[tuple] = []
        self.released: list[str] = []
        self.Refusal = paid_inference_meter.Refusal

    def reserve(self, project_key, usd, *, purpose, case_id=None):
        rid = uuid.uuid4().hex
        self.reservations.append({"id": rid, "usd": usd, "purpose": purpose, "case_id": case_id})
        return SimpleNamespace(reservation_id=rid, cents=round(usd * 100))

    def settle(self, project_key, reservation_id, usd, *, metering):
        self.settled.append((reservation_id, usd, metering))

    def release(self, project_key, reservation_id):
        self.released.append(reservation_id)


def gh_runner(prs: list[dict]):
    """A ``gh`` stand-in answering ``pr list`` with the given merged PRs."""
    calls: list[list[str]] = []

    def run(args):
        calls.append(list(args))
        return subprocess.CompletedProcess(["gh", *args], 0, stdout=json.dumps(prs), stderr="")

    run.calls = calls
    return run


def collect_with(monkeypatch, *, prs: list[dict]) -> tuple[dict, list[str]]:
    """Run the real evidence tick on this project: every adapter live, the
    ``gh`` runner injected, the judge transport injected and counted (the
    promise detector is off, so it must never be reached)."""
    from config.settings import settings

    judge_calls: list[str] = []

    def transport(prompt: str) -> str:
        judge_calls.append(prompt)
        raise AssertionError("the promise detector is off; no judge call may happen")

    monkeypatch.setattr(settings.improvement, "enabled", True)
    monkeypatch.setattr(settings.improvement, "promise_detector_enabled", False)
    monkeypatch.setattr(
        improvement_collect, "_default_gh_runner", lambda project_key: gh_runner(prs)
    )
    monkeypatch.setattr(improvement_collect, "_openrouter_judge", lambda model: transport)
    with patch("config.memory_defaults.DEFAULT_PROJECT_KEY", PK):
        result = improvement_collect.run_improvement_collect()
    return result, judge_calls


def lesson_prs() -> list[dict]:
    """Two merged PRs carrying one identical lesson line: two ``lesson`` rows
    with distinct ``source_ref`` and one dedup identity, so the cluster opens
    a second case (``do-test`` -> ``evaluators``)."""
    return [
        {"number": 7101, "title": "Tighten the suite", "body": f"Summary.\n{LESSON_LINE}\n"},
        {"number": 7102, "title": "Tighten it again", "body": f"More.\n{LESSON_LINE}\n"},
        {"number": 7103, "title": "Unrelated", "body": "no prefixed line here", "mergedAt": None},
    ]


# ---------------------------------------------------------------------------
# 1. The verdict moves the ranking
# ---------------------------------------------------------------------------


class TestVerdictMovesRanking:
    def test_verdict_moves_ranking(self, charter, charter_path, store, monkeypatch):
        from tests.unit.improvement_eval_runner_support import _approving_judge
        from tools.improvement_eval.corpus import export_corpus

        # --- evidence tick: the seeded memory becomes an inspiration row -------
        seed_memory(
            "Charter §3 first priority: cheap inference, " + SEED_URL,
            source="human",
            reference=SEED_REFERENCE,
        )
        seed_memory("cycle lighthouse beacon on the headland", source="agent")
        seed_memory("cycle grocery errands for the week", source="agent")
        collected, judge_calls = collect_with(monkeypatch, prs=lesson_prs())
        assert collected["status"] == "success", collected
        assert collected["counts"]["inspirations"] == 1
        assert collected["counts"]["lessons"] == 2
        assert collected["failed"] == []
        assert any(s.startswith("promises-skipped") for s in collected["skipped"])
        assert judge_calls == []
        (seeded_row,) = ImprovementEvidence.query.filter(project_key=PK, kind="inspiration")
        detail = json.loads(seeded_row.detail)
        assert detail["seed"] == SEED and detail["priority_area"] == "inference"
        assert detail["url"] == SEED_URL

        # --- planner tick 1: a case, a snapshot, one proposal ----------------
        first = tick(charter_path, store)
        assert first.status in ("success", "partial"), first.findings
        assert first.counts["cases_opened"] == 2
        opened = {c.priority_area: c for c in cases()}
        assert set(opened) == {"inference", "evaluators"}
        case = opened["inference"]
        assert json.loads(case.evidence_ids) == [seeded_row.id]
        assert case.dedup_identity == f"seed:{SEED}"
        assert case.state == "observed"
        snapshot = load_snapshot(first.snapshot_ref, store=store)
        assert [o["case_id"] for o in snapshot["order"]] == [case.id, opened["evaluators"].id]
        assert snapshot["intake_pool"] == []
        assert first.proposal["status"] == "proposed"
        assert first.proposal["case_id"] == case.id
        assert first.proposal["kind"] == "investigate"
        assert journal_tail(PK, case.id, 1)[0]["event"] == "action_proposed"
        admittable = _unadmitted_proposal(PK, case.id)
        assert admittable is not None
        assert admittable.action_id == first.proposal["action_id"]

        # --- the research steps, through the functions the CLI wraps ---------
        opened_inv = inv.open_investigation(
            PK,
            kind="web_research",
            case_id=case.id,
            uncertainty="whether a wider retrieval limit finds the gold memory more often",
            query="memory retrieval limit recall",
            decision_affected="which retrieval parameter the first experiment varies",
            expected_information_value="a prior answer or a candidate worth freezing",
        )
        assert opened_inv.accepted, opened_inv
        assert reload_case(case.id).state == "investigating"
        stored = inv.record_claims(
            opened_inv.investigation_id,
            [
                {
                    "claim": "RRF fusion with a wider candidate pool raises recall at 10",
                    "url": "https://example.com/rrf-paper",
                    "retrieved_at": "2026-09-14T09:00:00+00:00",
                },
                {"claim": "a note without a source"},
            ],
        )
        assert stored == 2
        resolved = inv.resolve(
            opened_inv.investigation_id,
            interpretation="the incumbent limit of 10 may miss the gold memory; vary limit only",
            provisional_assumption="the known-item set stands in for real recall demand",
            assumption_detail={
                "charter_passage": "§3 opportunities ranked by uncertainty",
                "confidence": "medium",
                "consequence": "the first experiment varies the retrieval limit only",
                "overturning_observation": "a real-query recall measurement disagrees",
            },
        )
        assert resolved.accepted, resolved
        code, out = run_main(
            [
                "--json",
                "revise-model",
                "--case",
                case.id,
                "--summary",
                "retrieval limit is the first lever",
                "--rationale",
                "one resolved web_research investigation",
                "--prediction",
                "limit=1 loses the gold memory on every known-item query",
            ]
        )
        assert code == 0, out
        revision = json.loads(out)
        assert revision["accepted"] and revision["research_process_digest"].startswith("sha256:")
        (revision_row,) = ImprovementModelRevision.query.filter(project_key=PK, state="current")
        assert revision_row.id == revision["revision_id"]
        proposed = ex.propose_experiment(
            PK,
            case.id,
            hypothesis="a narrower limit still finds the gold memory",
            mechanism="limit bounds the ranked list handed back",
            falsifier="recall_at_5 falls below the incumbent",
            candidate={"limit": 1},
        )
        assert proposed.accepted, proposed

        # --- freeze with an injected builder, evaluate on lane 4's arms -------
        monkeypatch.setattr(ex, "MIN_QUERIES", 2)
        holder: dict = {}

        def exporter(project_key):
            holder["export"] = export_corpus(project_key)
            return holder["export"]

        def builder(records, *, n_queries, seed):
            probe = [{"trial_id": "p", "query_text": "zxqvkw qvxj cycle-absent"}]
            full = runner.capture_baseline(
                PK, probe, incumbent={"limit": 10}, export=holder["export"]
            )
            gold = full["ranked_ids"]["p"][-1]
            return [
                KnownItem(query="zxqvkw qvxj cycle-absent", gold_memory_id=gold),
                KnownItem(query="zxqvkw qvxj cycle-absent again", gold_memory_id=gold),
            ]

        frozen = ex.freeze_experiment(
            PK,
            proposed.experiment_id,
            n_queries=2,
            seed=1,
            builder=builder,
            exporter=exporter,
            meter=OpenMeter(),
        )
        assert frozen.accepted, frozen
        assert reload_case(case.id).state == "experimenting"
        assert (
            ImprovementExperiment.query.get(project_key=PK, id=proposed.experiment_id).state
            == "frozen"
        )

        evaluated = ex.evaluate_experiment(
            PK, proposed.experiment_id, meter=OpenMeter(), judges=[_approving_judge]
        )
        assert evaluated.accepted, evaluated
        evaluation_id = evaluated.extra["evaluation_id"]
        evaluations = list(ImprovementEvaluation.query.filter(project_key=PK))
        assert [e.id for e in evaluations] == [evaluation_id]  # the runner is the only writer
        assert evaluations[0].verdict == "reject", evaluations[0].notes
        assert evaluated.extra["applied"]["accepted"] is True
        assert ex.apply_verdict(PK, evaluation_id).reason == "ALREADY_APPLIED"
        row = reload_case(case.id)
        assert row.state == "rejected"
        assert json.loads(row.evaluation_ids) == [evaluation_id]
        assert row.rejected_reason
        assert events(case.id)[-2:] == ["verdict_applied", "state_changed"]
        assert list(ImprovementRelease.query.filter(project_key=PK)) == []

        # --- planner tick 2: the rejected case has left the order ------------
        second = tick(charter_path, store)
        assert second.snapshot_ref != first.snapshot_ref
        doc = load_snapshot(second.snapshot_ref, store=store)
        assert doc["previous_ref"] == first.snapshot_ref
        assert [o["case_id"] for o in doc["order"]] == [opened["evaluators"].id]
        left = {entry["case_id"]: entry["reason"] for entry in doc["diff"]["left"]}
        assert left == {case.id: f"rejected: evaluation {evaluation_id}"}
        assert doc["diff"]["moved"] == [
            {
                "case_id": opened["evaluators"].id,
                "from": 2,
                "to": 1,
                "why": "order shifted around it",
            }
        ]
        assert second.proposal is None or second.proposal["case_id"] != case.id
        assert second.counts["verdicts_applied"] == 0

        # the verdict's state survives another projection: it lives on the head
        project_case(PK, case.id)
        assert reload_case(case.id).state == "rejected"
        assert read_head(PK, case.id).state == "rejected"
        assert len(list(ImprovementEvaluation.query.filter(project_key=PK))) == 1
        assert list(ImprovementRelease.query.filter(project_key=PK)) == []


# ---------------------------------------------------------------------------
# 2. A rejected hypothesis is not re-proposed
# ---------------------------------------------------------------------------


REJECTED_TEXT = "please keep the plan short and stop rewriting it"


def correction(text: str) -> ImprovementEvidence:
    now = datetime.now(UTC)
    return ImprovementEvidence.create(
        project_key=PK,
        created_at=now,
        observed_at=now,
        kind="correction",
        classification="preference",
        text=text,
    )


class TestRejectedIsNotReproposed:
    def test_rejected_is_not_reproposed(self, charter, charter_path, store):
        identity = planner.dedup_identity(
            types.SimpleNamespace(
                kind="correction", classification="preference", text=REJECTED_TEXT, detail=None
            )
        )
        evaluation_id = f"eval-{uuid.uuid4().hex[:8]}"
        rejected = ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="a dead idea",
            priority_area="skills",
            evidence_ids=json.dumps(["old-1", "old-2"]),
            evaluation_ids=json.dumps([evaluation_id]),
            rejected_reason="recall_at_5 below margin across the whole interval",
            dedup_identity=identity,
            ranking_rationale="charter §3 (skills)",
            charter_digest=charter.digest,
        )
        move_state(rejected.id, "rejected")
        assert reload_case(rejected.id).state == "rejected"
        fresh_a = correction(REJECTED_TEXT)
        fresh_b = correction("Please keep the plan SHORT and stop rewriting it!")
        assert planner.dedup_identity(fresh_a) == planner.dedup_identity(fresh_b) == identity

        result = tick(charter_path, store)

        assert result.counts["cases_opened"] == 0
        (case,) = cases()
        assert case.id == rejected.id
        ids = json.loads(case.evidence_ids)
        assert ids[:2] == ["old-1", "old-2"]
        assert set(ids[2:]) == {fresh_a.id, fresh_b.id}  # appended, newest-first scan order
        tail = journal_tail(PK, case.id, 100)
        attached = [e for e in tail if e["event"] == "evidence_attached_to_rejected"]
        assert len(attached) == 1
        assert attached[0]["payload_digest"] == planner._digest(
            {
                "case_id": case.id,
                "evidence_ids": ids[2:],
                "rejecting_evaluation": evaluation_id,
                "rejected_reason": rejected.rejected_reason,
            }
        )
        snapshot = load_snapshot(result.snapshot_ref, store=store)
        assert snapshot["order"] == []
        assert result.proposal is None
        assert "action_proposed" not in events(case.id)
        project_case(PK, case.id)
        assert reload_case(case.id).state == "rejected"


# ---------------------------------------------------------------------------
# 3. brief, ranking, report through the console entry point
# ---------------------------------------------------------------------------


def seeded_case(charter_path, store) -> tuple[ImprovementCase, planner.TickResult]:
    """One seeded inspiration row (adapter-shaped) opened into a case by a tick."""
    now = datetime.now(UTC)
    ImprovementEvidence.create(
        project_key=PK,
        created_at=now,
        observed_at=now,
        kind="inspiration",
        classification="unknown",
        source_ref="memory:seed-cli",
        text="charter §3: cheap inference first",
        detail=SEED_REFERENCE,
    )
    result = tick(charter_path, store)
    assert result.counts["cases_opened"] == 1
    (case,) = cases()
    return case, result


def charter_lines(charter_path: Path) -> tuple[str, str]:
    """The charter file's first non-blank line and its first heading."""
    lines = charter_path.read_text(encoding="utf-8").splitlines()
    first = next(line for line in lines if line.strip())
    heading = next(line for line in lines if line.startswith("# "))
    return first, heading


def assert_brief(text: str, charter_path: Path, charter, case_id: str) -> None:
    first, heading = charter_lines(charter_path)
    brief_lines = text.splitlines()
    assert next(line for line in brief_lines if line.strip()) == first
    assert next(line for line in brief_lines if line.startswith("# ")) == heading
    assert text.startswith(charter_path.read_text(encoding="utf-8").rstrip("\n"))
    assert f"Charter version {charter.version}, digest {charter.digest}" in text
    assert f"## Case {case_id}" in text
    assert "Ranking: position 1 of 1" in text
    assert "Factors: opportunity_cost=3" in text


def assert_ranking(text: str, case_id: str) -> None:
    assert f"  1. {case_id}" in text
    assert "opportunity_cost=3" in text
    assert "intake pool: empty" in text


def assert_report(text: str, case_id: str) -> None:
    for heading in (HEADING_MEASURED, HEADING_LIMITS, HEADING_CHANGE):
        assert f"## {heading}" in text
    seeded_line = next(line for line in text.splitlines() if line.startswith("- Seeded inputs:"))
    assert case_id in seeded_line
    assert f'seed marker "{SEED}"' in seeded_line
    assert f"# Qualified-result report: case {case_id}" in text


class TestCliBriefRankingReport:
    def test_cli_brief_ranking_report(self, charter, charter_path, store):
        case, _ = seeded_case(charter_path, store)

        code, brief = run_main(["brief", "--case", case.id])
        assert code == 0
        assert_brief(brief, charter_path, charter, case.id)

        code, ranking = run_main(["ranking"])
        assert code == 0
        assert_ranking(ranking, case.id)

        code, report = run_main(["report", "--case", case.id])
        assert code == 0
        assert_report(report, case.id)

        code, missing = run_main(["--json", "brief", "--case", "no-such-case"])
        assert code == 1 and json.loads(missing)["reason"] == "CASE_NOT_FOUND"

    def test_cli_binary_brief_ranking_report(self, charter, charter_path, store):
        """The same three subcommands through the installed console script,
        the way lane 3's ``test_improvement_control_cli.py`` reaches it."""
        import os

        if not BINARY.exists():
            pytest.skip(
                f"{BINARY} does not exist -- reinstall the venv's console scripts "
                "(uv sync / uv pip install) so the binary materializes"
            )
        case, _ = seeded_case(charter_path, store)

        def run(argv: list[str]) -> subprocess.CompletedProcess:
            return subprocess.run(
                [str(BINARY), *argv],
                capture_output=True,
                text=True,
                timeout=60,
                env=dict(os.environ),
            )

        brief = run(["brief", "--case", case.id])
        assert brief.returncode == 0, brief.stderr
        assert_brief(brief.stdout, charter_path, charter, case.id)

        ranking = run(["ranking"])
        assert ranking.returncode == 0, ranking.stderr
        assert_ranking(ranking.stdout, case.id)

        report = run(["report", "--case", case.id])
        assert report.returncode == 0, report.stderr
        assert_report(report.stdout, case.id)
