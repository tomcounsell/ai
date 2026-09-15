"""``reflections/improvement_plan.py`` and ``tools/improvement_plan_arm.py``
(#3217, task 5): case opening with the novelty check, the seeded cold start,
the two-tick cluster, keep-alive, the vault unblock, the idempotent single
proposal, the paused refusal, per-step failure injection, the lease race,
and the arm-runner seam that holds before and after lane 6 merges.

Rows land in the claimed per-worker test DB (autouse ``redis_test_db``,
tests/conftest.py). ``PK`` is ``"valor"`` because the tick proposes through
``tools.improvement.cmd_propose``, which is bound to that project key.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import io
import json
import sys
import types
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_evidence import ImprovementEvidence
from models.improvement_investigation import ImprovementInvestigation
from models.verifying_artifact_store import VerifyingArtifactStore
from reflections import improvement_plan as planner
from tools.improvement_control import keys
from tools.improvement_control.journal import journal_tail, set_state
from tools.improvement_control.lease import CaseLease
from tools.improvement_control.projection import apply as project_case
from tools.improvement_control.scheduler_adapter import _unadmitted_proposal
from tools.improvement_ranking import load_snapshot
from utils.redis_client import text_redis

PK = "valor"

CHARTER_TEXT = """---
title: test charter
owner: Tom Counsell
version: 2
effective: 2026-09-07
---

# Charter

## 3. Choosing the next improvement

Rank by opportunity cost, quality, resource cost, uncertainty, and unlocked capacity.
"""


@pytest.fixture(autouse=True)
def _content_root(monkeypatch, tmp_path):
    """`cmd_propose` builds its own store from the environment; keep every
    artifact out of the production retention root."""
    monkeypatch.setenv("POPOTO_IMPROVEMENT_CONTENT_PATH", str(tmp_path / "content"))


@pytest.fixture
def charter_path(tmp_path) -> Path:
    path = tmp_path / "improvement-charter.md"
    path.write_text(CHARTER_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    return VerifyingArtifactStore(base_path=str(tmp_path / "content"))


def tick(charter_path, store, **kw) -> planner.TickResult:
    return planner.plan_tick(PK, charter_path=charter_path, store=store, **kw)


def evidence(kind="correction", text="please keep the plan short and stop rewriting it", **kw):
    kw.setdefault("classification", "preference" if kind == "correction" else "unknown")
    now = datetime.now(UTC)
    return ImprovementEvidence.create(
        project_key=PK, created_at=now, observed_at=now, kind=kind, text=text, **kw
    )


def cases() -> list[ImprovementCase]:
    return list(ImprovementCase.query.filter(project_key=PK))


def investigations(kind=None) -> list[ImprovementInvestigation]:
    rows = []
    for state in ("open", "resolved", "awaiting_authorization", "abandoned", "expired"):
        rows.extend(ImprovementInvestigation.query.filter(project_key=PK, state=state))
    return [r for r in rows if kind is None or r.kind == kind]


def events(case_id) -> list[str]:
    return [e["event"] for e in journal_tail(PK, case_id, 50)]


def lease() -> CaseLease:
    return CaseLease(text_redis(), default_ttl_seconds=90)


def move_state(case_id: str, state: str) -> None:
    """The only sanctioned state move: set_state under the lease, then apply."""
    ls = lease()
    key = keys.lease_key(PK, case_id)
    generation = ls.acquire(key, ttl=90)
    assert generation is not None
    try:
        result = set_state(PK, case_id, generation=generation, state=state, by="test")
        assert result.accepted, result
    finally:
        ls.release(key, generation)
    project_case(PK, case_id)


# ---------------------------------------------------------------------------
# Case opening
# ---------------------------------------------------------------------------


class TestCaseOpening:
    def test_cluster_opens_across_two_ticks(self, charter_path, store):
        first = evidence(text="Please stop rewriting the whole plan every time you patch, ok?")
        assert tick(charter_path, store).counts["cases_opened"] == 0
        assert cases() == []
        second = evidence(text="please STOP rewriting the whole plan every time you patch!")
        result = tick(charter_path, store)
        assert result.counts["cases_opened"] == 1
        opened = cases()
        assert len(opened) == 1
        case = opened[0]
        assert set(json.loads(case.evidence_ids)) == {first.id, second.id}
        assert case.state == "observed"
        assert case.priority_area == "other"
        assert case.dedup_identity == planner.dedup_identity(first)
        assert case.charter_digest == result.charter_digest
        assert "charter §3" in case.ranking_rationale
        assert len(json.loads(case.alternative_explanations)) >= 1
        assert "case_opened" in events(case.id)

    def test_single_architectural_correction_opens_in_orchestration(self, charter_path, store):
        evidence(text="you lost the journey again and shipped half", classification="architectural")
        tick(charter_path, store)
        (case,) = cases()
        assert case.priority_area == "orchestration"

    def test_seeded_inspiration_opens_a_case(self, charter_path, store):
        row = evidence(
            kind="inspiration",
            text="charter §3: cheap inference first",
            source_ref="memory:seed-1",
            detail=json.dumps(
                {
                    "seed": "charter-s3:inference",
                    "priority_area": "inference",
                    "url": "https://example.com/muse",
                }
            ),
        )
        result = tick(charter_path, store)
        assert result.counts["cases_opened"] == 1
        (case,) = cases()
        assert case.priority_area == "inference"
        assert json.loads(case.evidence_ids) == [row.id]
        assert case.dedup_identity == "seed:charter-s3:inference"
        assert investigations(kind="inspiration_intake") == []

    def test_reseeding_attaches_instead_of_opening_a_second(self, charter_path, store):
        detail = json.dumps({"seed": "charter-s3:inference", "priority_area": "inference"})
        evidence(kind="inspiration", text="seed one", source_ref="memory:s1", detail=detail)
        tick(charter_path, store)
        again = evidence(kind="inspiration", text="seed two", source_ref="memory:s2", detail=detail)
        result = tick(charter_path, store)
        assert result.counts["cases_opened"] == 0
        (case,) = cases()
        assert again.id in json.loads(case.evidence_ids)
        assert "evidence_attached" in events(case.id)

    def test_url_inspiration_opens_an_intake_investigation_not_a_case(self, charter_path, store):
        row = evidence(
            kind="inspiration",
            text="watch this",
            source_ref="memory:m1",
            detail=json.dumps({"url": "https://youtube.com/watch?v=abc"}),
        )
        result = tick(charter_path, store)
        assert cases() == []
        (inv,) = investigations(kind="inspiration_intake")
        assert inv.stage == "draft" and inv.state == "open"
        sources = json.loads(inv.sources)
        assert sources[0]["url"] == "https://youtube.com/watch?v=abc"
        assert sources[0]["evidence_id"] == row.id
        snapshot = load_snapshot(result.snapshot_ref, store=store)
        assert snapshot["intake_pool"] == [inv.id]
        # the row is consumed by the investigation: a second tick opens nothing new
        tick(charter_path, store)
        assert len(investigations(kind="inspiration_intake")) == 1

    def test_promise_rows_share_one_case_in_personas(self, charter_path, store):
        evidence(kind="promise", text="I will definitely have it done by noon")
        evidence(kind="promise", text="this will absolutely work first time")
        tick(charter_path, store)
        (case,) = cases()
        assert case.priority_area == "personas"
        assert case.dedup_identity == "unqualified-promises"

    @pytest.mark.parametrize(
        ("stage", "area"),
        [("do-build", "orchestration"), ("do-test", "evaluators"), ("do-plan", "research_process")],
    )
    def test_lesson_rule_table(self, charter_path, store, stage, area):
        detail = json.dumps({"stage_guess": stage})
        evidence(kind="lesson", text="always run the suite before claiming green", detail=detail)
        evidence(kind="lesson", text="Always run the suite before claiming green.", detail=detail)
        tick(charter_path, store)
        (case,) = cases()
        assert case.priority_area == area

    def test_rejected_identity_attaches_and_refuses_reopen(self, charter_path, store):
        identity = planner.dedup_identity(
            types.SimpleNamespace(
                kind="correction",
                classification="preference",
                text="please keep the plan short and stop rewriting it",
                detail=None,
            )
        )
        rejected = ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="dead idea",
            priority_area="skills",
            evidence_ids=json.dumps(["old-1", "old-2"]),
            evaluation_ids=json.dumps(["eval-77"]),
            dedup_identity=identity,
            ranking_rationale="r",
        )
        move_state(rejected.id, "rejected")
        fresh_a = evidence(text="please keep the plan short and stop rewriting it")
        fresh_b = evidence(text="Please keep the plan short and STOP rewriting it")

        result = tick(charter_path, store)

        assert result.counts["cases_opened"] == 0
        (case,) = cases()
        assert case.id == rejected.id
        assert set(json.loads(case.evidence_ids)) == {"old-1", "old-2", fresh_a.id, fresh_b.id}
        assert "evidence_attached_to_rejected" in events(case.id)
        project_case(PK, case.id)
        assert ImprovementCase.query.get(project_key=PK, id=case.id).state == "rejected"
        snapshot = load_snapshot(result.snapshot_ref, store=store)
        assert snapshot["order"] == []
        assert result.proposal is None


# ---------------------------------------------------------------------------
# Keep-alive, unblock
# ---------------------------------------------------------------------------


def _awaiting(**kw) -> ImprovementInvestigation:
    return ImprovementInvestigation.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        kind=kw.pop("kind", "charter_amendment"),
        state=kw.pop("state", "awaiting_authorization"),
        **kw,
    )


class TestKeepAliveAndUnblock:
    def test_awaiting_row_kept_alive(self, charter_path, store, monkeypatch):
        awaiting = _awaiting()
        vault = _awaiting(
            kind="resource_acquisition",
            state="resolved",
            assumption_detail=json.dumps({"disposition": "vault_request_written"}),
        )
        plain = _awaiting(kind="web_research", state="resolved")
        saved: list[str] = []
        original = ImprovementInvestigation.save

        def counting_save(self, *args, **kwargs):
            saved.append(self.id)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(ImprovementInvestigation, "save", counting_save)
        result = tick(charter_path, store)
        assert result.counts["kept_alive"] == 2
        assert awaiting.id in saved and vault.id in saved
        assert plain.id not in saved

    def _blocked_case(self) -> ImprovementCase:
        return ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="cheap inference",
            priority_area="inference",
            ranking_rationale="charter §3 first priority",
            blocked_by="vault:meta_model_api",
        )

    def _probe_with(self, listing: str):
        import subprocess

        from tools.improvement_resources import probe

        def runner(argv):
            if argv[0] == "op" and "list" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=listing, stderr="")
            if argv[0] == "wrangler":
                raise FileNotFoundError("wrangler")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

        return lambda: probe(runner=runner)

    def test_blocked_case_unblocks_on_verified_probe(self, charter_path, store):
        case = self._blocked_case()
        listing = '[{"id": "m1", "title": "Meta Model API key", "vault": {"name": "m-valor"}}]'
        result = tick(charter_path, store, probe=self._probe_with(listing))
        assert result.counts["unblocked"] == 1
        fresh = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert fresh.blocked_by is None
        assert "case_unblocked" in events(case.id)
        assert fresh.state == "observed"

    def test_blocked_case_stays_blocked_on_absent_probe(self, charter_path, store):
        case = self._blocked_case()
        listing = '[{"id": "a4", "title": "Cloudflare API token", "vault": {"name": "m-valor"}}]'
        result = tick(charter_path, store, probe=self._probe_with(listing))
        assert result.counts["unblocked"] == 0
        fresh = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert fresh.blocked_by == "vault:meta_model_api"
        assert "case_unblocked" not in events(case.id)
        snapshot = load_snapshot(result.snapshot_ref, store=store)
        assert snapshot["order"][0]["blocked_by"] == "vault:meta_model_api"
        assert result.proposal is None  # the only case is blocked

    def test_probe_is_not_called_when_nothing_is_blocked(self, charter_path, store):
        calls = []
        tick(charter_path, store, probe=lambda: calls.append(1) or {})
        assert calls == []


# ---------------------------------------------------------------------------
# The proposal, the snapshot chain, and the cursor
# ---------------------------------------------------------------------------


class TestProposal:
    def test_one_proposal_per_tick_is_idempotent(self, charter_path, store):
        evidence(text="you lost the journey again and shipped half", classification="architectural")
        first = tick(charter_path, store)
        (case,) = cases()
        proposal = first.proposal
        assert proposal["status"] == "proposed"
        assert proposal["kind"] == "investigate"
        assert proposal["action_id"] == planner.action_id_for(
            case.id, first.snapshot_ref, "investigate"
        )
        admittable = _unadmitted_proposal(PK, case.id)
        assert admittable is not None
        assert admittable.action_id == proposal["action_id"]
        assert admittable.action_type == "investigate"
        assert journal_tail(PK, case.id, 1)[0]["event"] == "action_proposed"
        payload = json.loads(store.load(admittable.artifact_ref))
        assert payload["kind"] == "investigate"
        assert payload["snapshot_ref"] == first.snapshot_ref
        assert payload["position"] == 1
        assert payload["brief_hint"] == f"run valor-improve brief --case {case.id}"

        before = len(journal_tail(PK, case.id, 50))
        second = tick(charter_path, store)
        assert second.snapshot_ref == first.snapshot_ref  # nothing moved: the ref is reused
        assert second.proposal["status"] == "already_proposed"
        assert second.proposal["action_id"] == proposal["action_id"]
        assert second.proposal["idempotent"] is True
        assert len(journal_tail(PK, case.id, 50)) == before
        assert _unadmitted_proposal(PK, case.id).action_id == proposal["action_id"]

    def test_experiment_kind_when_a_proposed_experiment_exists(self, charter_path, store):
        from models.improvement_experiment import ImprovementExperiment

        evidence(text="you lost the journey again and shipped half", classification="architectural")
        tick(charter_path, store)
        (case,) = cases()
        # the first tick already proposed an investigation; a fresh snapshot
        # (the experiment changes resource_cost? no: proposed is 1) needs the
        # pending proposal cleared, so mint a new case instead.
        evidence(
            text="a second architectural miss on a different journey",
            classification="architectural",
        )
        result = tick(charter_path, store)
        second = next(c for c in cases() if c.id != case.id)
        ImprovementExperiment.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="proposed",
            case_id=second.id,
            hypothesis="h",
        )
        # the top case still holds its un-admitted proposal, so the second case is
        # reachable only once the first is busy: simulate admission.
        from tools.improvement_control.intents import admit
        from tools.improvement_control.journal import read_head

        head = read_head(PK, case.id)
        admitted = admit(
            PK,
            case.id,
            _unadmitted_proposal(PK, case.id).action_id,
            expected_revision=head.revision,
            generation=head.highest_accepted,
            action_type="investigate",
            max_concurrent=5,
        )
        assert admitted.accepted, admitted
        result = tick(charter_path, store)
        assert result.proposal["case_id"] == second.id
        assert result.proposal["kind"] == "experiment"
        assert _unadmitted_proposal(PK, second.id).action_type == "experiment"

    def test_snapshot_chain_and_cursor(self, charter_path, store):
        from models.improvement_controller_state import ImprovementControllerState

        evidence(text="you lost the journey again and shipped half", classification="architectural")
        first = tick(charter_path, store)
        state = ImprovementControllerState.get(PK)
        assert state.last_snapshot_ref == first.snapshot_ref
        assert state.charter_digest == first.charter_digest
        assert state.last_tick_at
        (case,) = cases()
        assert "ranking_recorded" in events(case.id)
        entry = next(e for e in journal_tail(PK, case.id, 50) if e["event"] == "ranking_recorded")
        assert entry["artifact_ref"] == first.snapshot_ref
        assert entry["payload_digest"].startswith("sha256:")

        evidence(
            text="a second architectural miss on a different journey",
            classification="architectural",
        )
        second = tick(charter_path, store)
        doc = load_snapshot(second.snapshot_ref, store=store)
        assert doc["previous_ref"] == first.snapshot_ref
        assert len(doc["diff"]["entered"]) == 1
        assert ImprovementControllerState.get(PK).last_snapshot_ref == second.snapshot_ref


# ---------------------------------------------------------------------------
# Refusals and failure injection
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_pinned_none_ends_tick_with_zero_writes(self, tmp_path, store):
        evidence(text="you lost the journey again and shipped half", classification="architectural")
        result = planner.plan_tick(PK, charter_path=tmp_path / "absent.md", store=store)
        assert result.status == "error"
        assert result.snapshot_ref is None
        assert cases() == []
        from models.improvement_controller_state import ImprovementControllerState

        assert ImprovementControllerState.get(PK) is None
        assert not (tmp_path / "content").exists()

    def test_paused_case_is_skipped_and_untouched(self, charter_path, store):
        """No pause pre-read: PAUSED surfaces from transition() and the tick
        records paused:<case_id>, writes nothing for that case, and proposes
        nothing for it."""
        from tools.improvement_control.journal import pause

        identity = planner.dedup_identity(
            types.SimpleNamespace(
                kind="correction", classification="preference", text="paused case row", detail=None
            )
        )
        paused = ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="paused",
            priority_area="skills",
            evidence_ids=json.dumps(["old-1"]),
            dedup_identity=identity,
            ranking_rationale="r",
            charter_digest="stale",
        )
        ls = lease()
        key = keys.lease_key(PK, paused.id)
        generation = ls.acquire(key, ttl=90)
        try:
            assert pause(
                PK, paused.id, generation=generation, reason="break-glass", by="op"
            ).accepted
        finally:
            ls.release(key, generation)
        project_case(PK, paused.id)
        before = journal_tail(PK, paused.id, 50)
        evidence(text="paused case row")
        evidence(text="paused case row again")

        result = tick(charter_path, store)

        assert f"paused:{paused.id}" in result.findings
        fresh = ImprovementCase.query.get(project_key=PK, id=paused.id)
        assert json.loads(fresh.evidence_ids) == ["old-1"]
        assert journal_tail(PK, paused.id, 50) == before
        assert result.proposal is None
        assert result.status == "partial"

    def test_unwritable_store_is_a_finding_and_no_proposal(self, charter_path, store):
        class Unwritable(VerifyingArtifactStore):
            def save(self, *args, **kwargs):
                raise OSError("disk full")

        evidence(text="you lost the journey again and shipped half", classification="architectural")
        result = tick(charter_path, Unwritable(base_path=store.base_path))
        assert result.status == "error"
        assert any(f.startswith("ranking-failed: disk full") for f in result.findings)
        assert result.snapshot_ref is None
        assert result.proposal is None
        (case,) = cases()  # the open step precedes ranking and still ran
        assert "action_proposed" not in events(case.id)

    def test_ranking_recorded_refusal_skips_the_proposal(self, charter_path, store, monkeypatch):
        from tools.improvement_control.journal import TransitionResult
        from tools.improvement_control.journal import transition as real_transition

        def refusing(project_key, case_id, **kw):
            if kw.get("event") == "ranking_recorded":
                return TransitionResult(False, "REVISION_MISMATCH", 1)
            return real_transition(project_key, case_id, **kw)

        monkeypatch.setattr(planner, "transition", refusing)
        evidence(text="you lost the journey again and shipped half", classification="architectural")
        result = tick(charter_path, store)
        (case,) = cases()
        assert f"ranking_recorded refused for {case.id}: REVISION_MISMATCH" in result.findings
        assert result.proposal is None
        assert "action_proposed" not in events(case.id)
        assert result.snapshot_ref is not None  # the snapshot itself was written

    def test_lease_busy_records_a_finding_and_writes_nothing_for_that_case(
        self, charter_path, store
    ):
        identity = planner.dedup_identity(
            types.SimpleNamespace(
                kind="correction", classification="preference", text="held lease row", detail=None
            )
        )
        held = ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="held",
            priority_area="skills",
            evidence_ids=json.dumps(["old-1"]),
            dedup_identity=identity,
            ranking_rationale="r",
        )
        generation = lease().acquire(keys.lease_key(PK, held.id), ttl=90)
        assert generation is not None
        evidence(text="held lease row")
        evidence(text="held lease row again")
        result = tick(charter_path, store)
        assert f"lease_busy:{held.id}" in result.findings
        fresh = ImprovementCase.query.get(project_key=PK, id=held.id)
        assert json.loads(fresh.evidence_ids) == ["old-1"]
        assert journal_tail(PK, held.id, 50) == []
        assert result.proposal is None

    def test_verdict_backstop_import_error_is_a_finding(self, charter_path, store, monkeypatch):
        from models.improvement_evaluation import ImprovementEvaluation
        from models.improvement_experiment import ImprovementExperiment

        case = ImprovementCase.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="observed",
            title="evaluated",
            priority_area="skills",
            ranking_rationale="r",
        )
        move_state(case.id, "evaluating")
        experiment = ImprovementExperiment.create(
            project_key=PK, created_at=datetime.now(UTC), state="complete", case_id=case.id
        )
        ImprovementEvaluation.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="complete",
            verdict="reject",
            experiment_id=experiment.id,
        )
        monkeypatch.setattr(
            sys, "meta_path", [_Blocker("tools.improvement_experiment"), *sys.meta_path]
        )
        monkeypatch.delitem(sys.modules, "tools.improvement_experiment", raising=False)
        result = tick(charter_path, store)
        assert (
            "verdict backstop unavailable: tools.improvement_experiment not built"
            in result.findings
        )
        assert result.counts["verdicts_applied"] == 0

    def test_run_improvement_planner_is_gated_on_enabled(self, monkeypatch):
        from config.settings import settings

        monkeypatch.setattr(settings.improvement, "enabled", False)
        result = planner.run_improvement_planner()
        assert result["status"] == "skipped"
        assert result["counts"] == {}
        assert cases() == []


# ---------------------------------------------------------------------------
# Import discipline
# ---------------------------------------------------------------------------


FORBIDDEN_MODULE_LEVEL = (
    "agent.llm",
    "tools.improvement_control.scheduler_adapter",
    "tools.improvement_eval.runner",
    "tools.improvement_recursion",
    "tools.improvement_experiment",
)


def _module_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "module_path",
    [Path(planner.__file__), Path(importlib.import_module("tools.improvement_plan_arm").__file__)],
    ids=["improvement_plan", "improvement_plan_arm"],
)
def test_no_llm_or_dispatch_imports(module_path):
    imported = _module_level_imports(module_path)
    for forbidden in FORBIDDEN_MODULE_LEVEL:
        assert not any(
            name == forbidden or name.startswith(forbidden + ".") for name in imported
        ), f"{module_path.name} imports {forbidden} at module level"


# ---------------------------------------------------------------------------
# The arm-runner seam (holds before and after lane 6 merges)
# ---------------------------------------------------------------------------


class _Blocker:
    """A meta_path finder that refuses one module name (and its children)."""

    def __init__(self, name: str):
        self.name = name

    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name or fullname.startswith(self.name + "."):
            raise ImportError(f"{fullname} blocked by test")
        return None


@dataclasses.dataclass(frozen=True)
class _BudgetUse:
    unit2_usd: float | None
    unit3_usd: float | None
    subscription_turns: int
    wall_seconds: float | None


@dataclasses.dataclass(frozen=True)
class _ArmResult:
    gains: dict
    budget_use: _BudgetUse


def _fake_arms_module():
    registry: list = []
    return types.SimpleNamespace(
        register_arm_runner=registry.append,
        get_arm_runner=lambda: registry[-1] if registry else None,
        ArmResult=_ArmResult,
        BudgetUse=_BudgetUse,
        _registry=registry,
    )


def test_arm_runner_registers_from_cli_entry(monkeypatch, charter_path, store):
    from tools import improvement as cli
    from tools.improvement_plan_arm import PlannerArmRunner

    fake = _fake_arms_module()
    monkeypatch.setitem(
        sys.modules, "tools.improvement_recursion", types.SimpleNamespace(__path__=[])
    )
    monkeypatch.setitem(sys.modules, "tools.improvement_recursion.arms", fake)

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["--json", "release", "compare"])
    assert code == 0
    assert len(fake._registry) == 1
    runner = fake.get_arm_runner()
    assert isinstance(runner, PlannerArmRunner)

    # and the runner produces lane 6's shapes with exactly the declared keywords
    result = runner.run("sha256:" + "0" * 64, [], None, "arm-test-1")
    assert isinstance(result, _ArmResult)
    assert result.gains == {}
    assert result.budget_use.unit2_usd is None
    assert result.budget_use.subscription_turns == 0
    assert result.budget_use.wall_seconds >= 0


def test_arm_runner_nothing_registers_at_import(monkeypatch):
    from tools.improvement_plan_arm import ArmRunnerUnavailable, PlannerArmRunner

    monkeypatch.delitem(sys.modules, "tools.improvement_recursion.arms", raising=False)
    monkeypatch.delitem(sys.modules, "tools.improvement_recursion", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_Blocker("tools.improvement_recursion"), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "tools.improvement", raising=False)

    module = importlib.import_module("tools.improvement")
    assert hasattr(module, "main")
    with pytest.raises(ArmRunnerUnavailable, match="ARM_RUNNER_UNAVAILABLE: lane 6 not merged"):
        PlannerArmRunner().run("sha256:" + "0" * 64, ["case-1"], None, "arm-test-2")
