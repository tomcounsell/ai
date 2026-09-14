"""Release lifecycle (#3218, lane 6).

One test per refusal code, the happy path through ``approved`` with a
recording runner, exposure anchored on the merge, window scoring, and the
rollback push gate. Rows land in the claimed test DB (autouse
``redis_test_db``, tests/conftest.py) under a test-scoped ``project_key``;
the charter is seeded from the real file, the experiment carries a frozen
protocol, and the evaluation is written with lane 4's exact string
expressions. ``now=`` is injected everywhere; nothing sleeps.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

from models.improvement_charter import _CHARTER_PATH, ImprovementCharter
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_evidence import ImprovementEvidence
from models.improvement_experiment import ImprovementExperiment
from models.improvement_release import ImprovementRelease
from tools.improvement_eval.runner import freeze_protocol
from tools.improvement_release import lifecycle
from tools.improvement_release.evaluation_read import json_field
from tools.improvement_release.lifecycle import (
    AGENT_IDENTITIES,
    HISTORY_MAX,
    REFUSAL_CODES,
    ReleaseRefused,
    approve,
    close_window,
    due_windows,
    expose,
    get_release,
    open_pr,
    propose,
    rollback,
    withdraw,
)
from tools.improvement_release.observation import EVIDENCE_TTL_DAYS
from tools.improvement_release.runner import RecordingRunner

PK = "test-3218-lifecycle"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
MERGE_SHA = "37dc10b33f6c33d18559d4c338d23653a21dbb49"
REVERT_SHA = "a" * 40
PARENT_SHA = "b" * 40
INTERVAL = {"lower": 0.02, "upper": 0.22, "n": 12, "raw_p_value": 0.01, "adjusted_p_value": 0.02}

ROLLBACK_PLAN = {"kind": "git_revert", "verify": ["true"], "propagation": "/update"}
OBSERVATION = {
    "window_days": 7,
    "baseline_window_days": 7,
    "metrics": ["architectural_correction_rate", "coverage_ticks"],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def charter(tmp_path):
    path = tmp_path / "improvement-charter.md"
    path.write_bytes(_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n"))
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    return row


def _amend_charter(tmp_path):
    """Pin a new digest: a second charter row with one extra line."""
    path = tmp_path / "improvement-charter-v3.md"
    text = _CHARTER_PATH.read_text(encoding="utf-8") + "\n\nAmended for the drift test.\n"
    path.write_text(text, encoding="utf-8")
    row = ImprovementCharter.load_from_file(path, project_key=PK)
    assert row is not None
    row.created_at = datetime.now(UTC) + timedelta(seconds=5)
    row.save()
    return row


def _experiment(manifest_extra: dict | None = None) -> ImprovementExperiment:
    ref = freeze_protocol({"primary_endpoint": "primary", "endpoints": ["primary"]})
    manifest = {"protocol_ref": ref, "base_revision": "abc123", **(manifest_extra or {})}
    row = ImprovementExperiment(
        project_key=PK,
        created_at=NOW,
        state="complete",
        case_id=None,
        hypothesis="a wider candidate finds the gold memory",
        contract_digest="sha256:" + "c" * 64,
        candidate_surfaces=json.dumps(["tools/x.py"]),
        manifest=json.dumps(manifest),
    )
    assert row.save() is not False
    return row


def _evaluation(
    charter_row, experiment, *, verdict="accept", state="complete", effect=0.12
) -> ImprovementEvaluation:
    row = ImprovementEvaluation(
        project_key=PK,
        created_at=NOW,
        state=state,
        verdict=verdict,
        experiment_id=str(experiment.id),
        contract_digest=experiment.contract_digest,
        charter_digest=charter_row.digest,
        # runner.py:499-508's exact expressions
        effect=json.dumps({"primary": effect}, sort_keys=True),
        confidence_interval=json.dumps({"primary": INTERVAL}, sort_keys=True),
        correction="holm; fixed-batch(n=2, endpoints=1)",
        notes="\n".join(["accept"]),
        judge_records="",
    )
    assert row.save() is not False
    return row


@pytest.fixture
def evaluation(charter):
    return _evaluation(charter, _experiment())


def _propose(evaluation, **overrides) -> ImprovementRelease:
    kwargs = {
        "evaluation_id": str(evaluation.id),
        "kind": "core_workflow",
        "candidate_ref": "session/candidate",
        "surfaces": ["tools/x.py"],
        "rollback_plan": ROLLBACK_PLAN,
        "observation": OBSERVATION,
        "project_key": PK,
        "runner": RecordingRunner(),
        "now": NOW,
    }
    kwargs.update(overrides)
    return propose(**kwargs)


def _stamp_drill(release, *, result="pass", drilled_at=None) -> ImprovementRelease:
    """What ``drill.run`` writes on the row, without running git."""
    release = get_release(release.id, PK)
    release.rollback_drill = json.dumps(
        {
            "result": result,
            "drilled_at": (drilled_at or NOW + timedelta(minutes=5)).isoformat(),
            "exercised": ["range_checks", "revert", "tree_restoration"],
            "not_exercised": ["fleet_update", "production_traffic", "merge_commit_revert"],
        }
    )
    release.save()
    return release


@pytest.fixture
def proposed(evaluation):
    return _propose(evaluation)


@pytest.fixture
def approved(proposed):
    _stamp_drill(proposed)
    return approve(
        proposed.id, approved_by="Tom Counsell", project_key=PK, now=NOW + timedelta(hours=1)
    )


def _pr_view_json(merged_at: datetime, *, state="MERGED", sha=MERGE_SHA) -> str:
    return json.dumps(
        {
            "mergeCommit": {"oid": sha},
            "mergedAt": merged_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "state": state,
        }
    )


def _pr_runner(merged_at: datetime, **kwargs) -> RecordingRunner:
    return RecordingRunner(
        [
            (["gh", "pr", "create"], (0, "https://github.com/tomcounsell/ai/pull/3299\n", "")),
            (["gh", "pr", "view"], (0, _pr_view_json(merged_at, **kwargs), "")),
        ]
    )


def _seed_evidence(at: datetime, *, architectural: int, ticks: int, preference: int = 0):
    for i in range(architectural + preference):
        ImprovementEvidence.create(
            project_key=PK,
            created_at=at + timedelta(seconds=i),
            kind="correction",
            classification="architectural" if i < architectural else "preference",
            source_ref=f"memory:{at.isoformat()}:{i}",
        )
    for i in range(ticks):
        ImprovementEvidence.create(
            project_key=PK,
            created_at=at + timedelta(seconds=100 + i),
            kind="other",
            classification="unknown",
            source_ref=f"coverage:{at.isoformat()}:{i}",
        )


def _observing(approved, *, merged_at: datetime, expose_at: datetime | None = None):
    runner = _pr_runner(merged_at)
    open_pr(approved.id, project_key=PK, runner=runner)
    return expose(approved.id, project_key=PK, runner=runner, now=expose_at or merged_at)


def _outcome(release_id) -> dict:
    return json_field(get_release(release_id, PK).outcome)


def _rollback_runner(*, push=(0, "", ""), remote_sha=REVERT_SHA) -> RecordingRunner:
    return RecordingRunner(
        [
            (["git", "rev-parse", "--show-toplevel"], (0, "/repo\n", "")),
            (["git", "rev-parse", "HEAD"], (0, REVERT_SHA + "\n", "")),
            (["git", "rev-parse", "origin/main"], (0, PARENT_SHA + "\n", "")),
            (["git", "rev-list", "--parents"], (0, f"{MERGE_SHA} {PARENT_SHA} {'d' * 40}\n", "")),
            (["git", "push"], push),
            (["git", "ls-remote"], (0, f"{remote_sha}\trefs/heads/main\n", "")),
        ]
    )


def _count_releases() -> int:
    return len(list(ImprovementRelease.query.filter(project_key=PK)))


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


class TestPropose:
    def test_writes_one_proposed_row(self, evaluation):
        release = _propose(evaluation)
        row = get_release(release.id, PK)
        assert row.state == "proposed"
        assert row.kind == "core_workflow"
        assert row.evaluation_id == str(evaluation.id)
        assert row.candidate_ref == "session/candidate"
        assert row.base_revision == "abc123"
        assert json_field(row.surfaces) == ["tools/x.py"]
        assert json_field(row.exposure)["unit"] == "fleet"
        assert json_field(row.rollback_plan) == ROLLBACK_PLAN
        assert json_field(row.observation) == OBSERVATION
        assert row.charter_digest == evaluation.charter_digest
        outcome = json_field(row.outcome)
        assert outcome["history"][0]["event"] == "proposed"
        assert outcome["rollback_plan_written_at"] == NOW.isoformat()
        assert _count_releases() == 1

    def test_manifest_lacking_candidate_ref_proposes_from_argument(self, evaluation):
        release = _propose(evaluation, candidate_ref="session/from-arg")
        assert release.candidate_ref == "session/from-arg"

    def test_propose_refuses_not_accept(self, charter):
        evaluation = _evaluation(charter, _experiment(), verdict="reject")
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation)
        assert exc.value.code == "EVALUATION_NOT_ACCEPT"
        assert _count_releases() == 0

    def test_propose_refuses_missing_evaluation(self, charter):
        with pytest.raises(ReleaseRefused) as exc:
            propose(
                evaluation_id="nope",
                kind="core_workflow",
                candidate_ref="x",
                surfaces=["tools/x.py"],
                rollback_plan=ROLLBACK_PLAN,
                observation=OBSERVATION,
                project_key=PK,
                runner=RecordingRunner(),
            )
        assert exc.value.code == "EVALUATION_NOT_ACCEPT"

    def test_propose_refuses_charter_drift(self, evaluation, tmp_path):
        _amend_charter(tmp_path)
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation)
        assert exc.value.code == "CHARTER_DRIFT"
        assert _count_releases() == 0

    def test_propose_refuses_invalid_kind(self, evaluation):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, kind="prompt")
        assert exc.value.code == "INVALID_KIND"
        assert _count_releases() == 0

    def test_propose_refuses_evaluator_without_calibration(self, evaluation):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, kind="evaluator")
        assert exc.value.code == "EVALUATOR_RELEASE_NEEDS_CALIBRATION"
        assert _count_releases() == 0
        release = _propose(
            evaluation, kind="evaluator", calibration_ref="$CF:abc", argument="better assesses"
        )
        assert release.kind == "evaluator"

    @pytest.mark.parametrize(
        "surfaces",
        [["docs/improvement-charter.md"], [], ["../escape"], ["tools/*.py"], ["docs"], ["tools"]],
    )
    def test_propose_refuses_charter_surface(self, evaluation, surfaces):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, surfaces=surfaces)
        assert exc.value.code == "SURFACE_DENIED"
        assert _count_releases() == 0

    def test_propose_refuses_base_revision_conflict(self, evaluation):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, base_revision="def456")
        assert exc.value.code == "BASE_REVISION_CONFLICT"
        assert _count_releases() == 0

    def test_propose_refuses_manifest_lacking_base_revision(self, charter):
        ref = freeze_protocol({"primary_endpoint": "primary"})
        experiment = ImprovementExperiment(
            project_key=PK,
            created_at=NOW,
            state="complete",
            manifest=json.dumps({"protocol_ref": ref}),
        )
        assert experiment.save() is not False
        evaluation = _evaluation(charter, experiment)
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation)
        assert exc.value.code == "MANIFEST_LACKS_BASE_REVISION"
        assert _count_releases() == 0
        release = _propose(evaluation, base_revision="from-arg")
        assert release.base_revision == "from-arg"

    def test_propose_refuses_candidate_ref_conflict(self, charter):
        evaluation = _evaluation(charter, _experiment({"candidate_ref": "session/lane5"}))
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, candidate_ref="session/other")
        assert exc.value.code == "CANDIDATE_REF_CONFLICT"
        assert _count_releases() == 0
        release = _propose(evaluation, candidate_ref=None)
        assert release.candidate_ref == "session/lane5"

    def test_propose_refuses_unresolved_candidate_ref(self, evaluation):
        runner = RecordingRunner([(["git", "rev-parse", "--verify"], (128, "", "fatal: bad ref"))])
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, runner=runner)
        assert exc.value.code == "CANDIDATE_REF_UNRESOLVED"
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, candidate_ref="")
        assert exc.value.code == "CANDIDATE_REF_UNRESOLVED"
        assert _count_releases() == 0

    @pytest.mark.parametrize(
        "plan",
        [
            {"kind": "manual", "verify": [], "propagation": "/update"},
            {"kind": "git_revert", "verify": "true", "propagation": "/update"},
            {"kind": "git_revert", "verify": [], "propagation": "ssh"},
            "git revert",
        ],
    )
    def test_propose_refuses_invalid_rollback_plan(self, evaluation, plan):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, rollback_plan=plan)
        assert exc.value.code == "INVALID_ROLLBACK_PLAN"
        assert _count_releases() == 0

    def test_propose_accepts_empty_verify(self, evaluation):
        release = _propose(
            evaluation, rollback_plan={"kind": "git_revert", "verify": [], "propagation": "/update"}
        )
        assert json_field(release.rollback_plan)["verify"] == []

    @pytest.mark.parametrize(
        "observation",
        [
            {**OBSERVATION, "window_days": 0},
            {**OBSERVATION, "baseline_window_days": 0},
            {**OBSERVATION, "metrics": []},
            {**OBSERVATION, "metrics": ["latency"]},
        ],
    )
    def test_propose_refuses_invalid_observation_plan(self, evaluation, observation):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, observation=observation)
        assert exc.value.code == "INVALID_OBSERVATION_PLAN"
        assert _count_releases() == 0

    def test_propose_refuses_window_exceeding_ttl(self, evaluation):
        with pytest.raises(ReleaseRefused) as exc:
            _propose(evaluation, observation={**OBSERVATION, "window_days": 22})
        assert exc.value.code == "WINDOW_EXCEEDS_EVIDENCE_TTL"
        assert _count_releases() == 0


# ---------------------------------------------------------------------------
# approve / withdraw / history
# ---------------------------------------------------------------------------


class TestApprove:
    def test_happy_path_through_open_pr(self, proposed):
        _stamp_drill(proposed)
        release = approve(
            proposed.id, approved_by="Tom Counsell", project_key=PK, now=NOW + timedelta(hours=1)
        )
        assert release.state == "approved"
        row = get_release(release.id, PK)
        assert row.approved_by == "Tom Counsell"
        assert row.approved_at == NOW + timedelta(hours=1)
        assert row.observation_window_ends_at == NOW + timedelta(hours=1, days=7)
        assert json_field(row.promotion_gate)["automated"] is False
        assert row.exposed_at is None

        runner = _pr_runner(NOW)
        open_pr(row.id, project_key=PK, runner=runner)
        row = get_release(row.id, PK)
        assert row.state == "approved"
        exposure = json_field(row.exposure)
        assert exposure["pr_number"] == 3299
        assert exposure["pr_url"] == "https://github.com/tomcounsell/ai/pull/3299"
        argv = runner.argvs()[0]
        assert argv[:3] == ["gh", "pr", "create"]
        assert argv[argv.index("--head") + 1] == "session/candidate"
        assert not any(a[:3] == ["gh", "pr", "merge"] for a in runner.argvs())
        assert row.exposed_at is None

    def test_approve_refuses_without_drill(self, proposed):
        with pytest.raises(ReleaseRefused) as exc:
            approve(proposed.id, approved_by="Tom Counsell", project_key=PK)
        assert exc.value.code == "DRILL_REQUIRED"
        assert get_release(proposed.id, PK).state == "proposed"
        _stamp_drill(proposed, result="fail")
        with pytest.raises(ReleaseRefused) as exc:
            approve(proposed.id, approved_by="Tom Counsell", project_key=PK)
        assert exc.value.code == "DRILL_REQUIRED"

    def test_approve_refuses_stale_drill(self, proposed):
        _stamp_drill(proposed, drilled_at=NOW - timedelta(minutes=1))
        with pytest.raises(ReleaseRefused) as exc:
            approve(proposed.id, approved_by="Tom Counsell", project_key=PK)
        assert exc.value.code == "DRILL_STALE"
        assert get_release(proposed.id, PK).state == "proposed"

    @pytest.mark.parametrize("name", ["", "   ", *sorted(AGENT_IDENTITIES), " Valor ", "CLAUDE"])
    def test_approve_refuses_agent_identity(self, proposed, name):
        _stamp_drill(proposed)
        with pytest.raises(ReleaseRefused) as exc:
            approve(proposed.id, approved_by=name, project_key=PK)
        assert exc.value.code == "APPROVER_NOT_HUMAN"
        assert get_release(proposed.id, PK).state == "proposed"

    def test_approve_rechecks_charter_drift(self, proposed, tmp_path):
        _stamp_drill(proposed)
        _amend_charter(tmp_path)
        with pytest.raises(ReleaseRefused) as exc:
            approve(proposed.id, approved_by="Tom Counsell", project_key=PK)
        assert exc.value.code == "CHARTER_DRIFT"
        assert get_release(proposed.id, PK).state == "proposed"

    def test_approve_refuses_wrong_state(self, approved):
        with pytest.raises(ReleaseRefused) as exc:
            approve(approved.id, approved_by="Tom Counsell", project_key=PK)
        assert exc.value.code == "WRONG_STATE"

    def test_transition_rereads_before_save(self, proposed):
        _stamp_drill(proposed)
        stale = get_release(proposed.id, PK)
        withdraw(proposed.id, project_key=PK, reason="superseded", now=NOW)
        with pytest.raises(ReleaseRefused) as exc:
            lifecycle._transition(
                stale, to="approved", allowed_from=("proposed",), event="approved", now=NOW
            )
        assert exc.value.code == "WRONG_STATE"
        assert get_release(proposed.id, PK).state == "withdrawn"

    def test_transition_merges_a_concurrent_writers_history(self, proposed):
        """Race 1: a second writer keeps the first writer's event, so the record shows both."""
        _stamp_drill(proposed)
        stale = get_release(proposed.id, PK)
        stale_outcome = lifecycle._outcome(stale)
        approve(proposed.id, approved_by="Tom Counsell", project_key=PK, now=NOW)
        lifecycle._transition(
            stale,
            to="withdrawn",
            allowed_from=("proposed", "approved"),
            event="withdrawn",
            now=NOW + timedelta(seconds=1),
            outcome=stale_outcome,
        )
        history = _outcome(proposed.id)["history"]
        assert [e["event"] for e in history] == ["proposed", "approved", "withdrawn"]
        assert history[-1]["from"] == "approved"

    def test_withdraw_records_reason(self, proposed):
        release = withdraw(proposed.id, project_key=PK, reason="superseded", now=NOW)
        assert release.state == "withdrawn"
        outcome = _outcome(release.id)
        assert outcome["withdrawn"]["reason"] == "superseded"
        assert outcome["history"][-1]["event"] == "withdrawn"
        with pytest.raises(ReleaseRefused) as exc:
            withdraw(proposed.id, project_key=PK, reason="again")
        assert exc.value.code == "WRONG_STATE"

    def test_get_release_not_found(self):
        with pytest.raises(ReleaseRefused) as exc:
            get_release("nope", PK)
        assert exc.value.code == "NOT_FOUND"

    def test_refusal_codes_are_closed(self):
        with pytest.raises(ValueError):
            ReleaseRefused("MADE_UP")
        assert len(REFUSAL_CODES) == len(set(REFUSAL_CODES))

    def test_history_bounded_at_history_max(self, proposed):
        outcome = {"history": []}
        for i in range(HISTORY_MAX):
            lifecycle._append_history(outcome, f"event-{i}", NOW)
        assert len(outcome["history"]) == HISTORY_MAX
        assert "history_truncated" not in outcome
        lifecycle._append_history(outcome, "event-last", NOW)
        assert len(outcome["history"]) == HISTORY_MAX
        assert outcome["history_truncated"] is True
        assert outcome["history"][0]["event"] == "event-1"
        assert outcome["history"][-1]["event"] == "event-last"
        # the bound holds through a real transition on the row
        release = get_release(proposed.id, PK)
        release.outcome = json.dumps(outcome)
        release.save()
        withdraw(proposed.id, project_key=PK, reason="bounded", now=NOW)
        stored = _outcome(proposed.id)
        assert len(stored["history"]) == HISTORY_MAX
        assert stored["history_truncated"] is True
        assert stored["history"][-1]["event"] == "withdrawn"


# ---------------------------------------------------------------------------
# expose
# ---------------------------------------------------------------------------


class TestExpose:
    def test_expose_restamps_window_end_from_exposed_at(self, approved):
        merged_at = NOW - timedelta(days=3)
        provisional = get_release(approved.id, PK).observation_window_ends_at
        _seed_evidence(merged_at - timedelta(days=2), architectural=1, ticks=5)
        _seed_evidence(merged_at, architectural=9, ticks=9)  # on the boundary: outside

        release = _observing(approved, merged_at=merged_at, expose_at=NOW)

        assert release.state == "observing"
        row = get_release(release.id, PK)
        assert row.exposed_at == merged_at
        assert row.observation_window_ends_at == merged_at + timedelta(days=7)
        assert row.observation_window_ends_at != provisional
        exposure = json_field(row.exposure)
        assert exposure["merge_sha"] == MERGE_SHA
        assert len(exposure["merge_sha"]) == 40
        outcome = json_field(row.outcome)
        baseline = outcome["baseline"]
        assert baseline["end"] == merged_at.isoformat()
        assert baseline["start"] == (merged_at - timedelta(days=7)).isoformat()
        assert baseline["coverage_ticks"] == 5
        assert baseline["corrections_architectural"] == 1
        events = {e["event"]: e for e in outcome["history"]}
        assert events["exposed"]["merged_at"] == merged_at.isoformat()
        assert events["exposed"]["expose_called_at"] == NOW.isoformat()
        assert events["window_restamped"]["from"] == provisional.isoformat()
        assert events["window_restamped"]["to"] == (merged_at + timedelta(days=7)).isoformat()

    def test_gh_and_rev_parse_calls_carry_the_git_timeout(self, evaluation):
        """No network subprocess runs unbounded: propose, open_pr, and expose all pin it."""
        from config.settings import settings

        expected = settings.timeouts.git_subprocess_s
        propose_runner = RecordingRunner()
        proposed = _propose(evaluation, runner=propose_runner)
        assert propose_runner.calls[0]["argv"][:3] == ["git", "rev-parse", "--verify"]
        assert propose_runner.calls[0]["timeout"] == expected
        _stamp_drill(proposed)
        approve(proposed.id, approved_by="Tom Counsell", project_key=PK, now=NOW)
        runner = _pr_runner(NOW - timedelta(days=3))
        open_pr(proposed.id, project_key=PK, runner=runner)
        expose(proposed.id, project_key=PK, runner=runner, now=NOW)
        gh_calls = [c for c in runner.calls if c["argv"][0] == "gh"]
        assert [c["argv"][:3] for c in gh_calls] == [
            ["gh", "pr", "create"],
            ["gh", "pr", "view"],
        ]
        assert all(c["timeout"] == expected for c in gh_calls), gh_calls

    def test_expose_refuses_when_baseline_expired(self, approved):
        merged_at = NOW - timedelta(days=EVIDENCE_TTL_DAYS - 7 + 1)
        runner = _pr_runner(merged_at)
        open_pr(approved.id, project_key=PK, runner=runner)
        with pytest.raises(ReleaseRefused) as exc:
            expose(approved.id, project_key=PK, runner=runner, now=NOW)
        assert exc.value.code == "EVIDENCE_EXPIRED"
        row = get_release(approved.id, PK)
        assert row.state == "approved"
        assert row.exposed_at is None
        assert "baseline" not in json_field(row.outcome)

    def test_expose_refuses_pr_not_merged(self, approved):
        runner = _pr_runner(NOW, state="OPEN")
        open_pr(approved.id, project_key=PK, runner=runner)
        with pytest.raises(ReleaseRefused) as exc:
            expose(approved.id, project_key=PK, runner=runner, now=NOW)
        assert exc.value.code == "PR_NOT_MERGED"
        assert get_release(approved.id, PK).state == "approved"

    def test_expose_refuses_without_pr(self, approved):
        with pytest.raises(ReleaseRefused) as exc:
            expose(approved.id, project_key=PK, runner=RecordingRunner(), now=NOW)
        assert exc.value.code == "PR_NOT_MERGED"

    def test_expose_refuses_non_hex_merge_sha(self, approved):
        runner = _pr_runner(NOW, sha="abc")
        open_pr(approved.id, project_key=PK, runner=runner)
        with pytest.raises(ReleaseRefused) as exc:
            expose(approved.id, project_key=PK, runner=runner, now=NOW)
        assert exc.value.code == "MERGE_SHA_INVALID"
        assert get_release(approved.id, PK).state == "approved"

    def test_open_pr_refuses_failed_create(self, approved):
        runner = RecordingRunner([(["gh", "pr", "create"], (1, "", "gh: not logged in"))])
        with pytest.raises(ReleaseRefused) as exc:
            open_pr(approved.id, project_key=PK, runner=runner)
        assert exc.value.code == "PR_CREATE_FAILED"
        assert "pr_number" not in json_field(get_release(approved.id, PK).exposure)


# ---------------------------------------------------------------------------
# close_window
# ---------------------------------------------------------------------------


class TestCloseWindow:
    def _exposed(self, approved, *, baseline=(2, 20), window=(2, 20)):
        merged_at = NOW - timedelta(days=7)
        _seed_evidence(merged_at - timedelta(days=3), architectural=baseline[0], ticks=baseline[1])
        _seed_evidence(merged_at + timedelta(days=3), architectural=window[0], ticks=window[1])
        return _observing(approved, merged_at=merged_at), merged_at

    def test_refuses_window_open(self, approved):
        release, merged_at = self._exposed(approved)
        with pytest.raises(ReleaseRefused) as exc:
            close_window(release.id, project_key=PK, now=merged_at + timedelta(days=2))
        assert exc.value.code == "WINDOW_OPEN"
        with pytest.raises(ReleaseRefused) as exc:
            close_window(release.id, project_key=PK, now=merged_at + timedelta(days=2), force=True)
        assert exc.value.code == "WINDOW_OPEN"
        assert get_release(release.id, PK).state == "observing"
        assert "closed_at" not in _outcome(release.id)

    def test_forced_close_records_reason_and_shortfall(self, approved):
        release, merged_at = self._exposed(approved)
        close_window(
            release.id,
            project_key=PK,
            now=merged_at + timedelta(days=5),
            force=True,
            reason="incident review",
        )
        outcome = _outcome(release.id)
        assert outcome["forced"]["reason"] == "incident review"
        assert outcome["window_shortfall_days"] == 2
        assert outcome["claim_level_2_supported"] is False

    def test_on_time_close_held_is_accepted(self, approved):
        release, merged_at = self._exposed(approved)
        closed = close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        assert closed.state == "accepted"
        outcome = _outcome(release.id)
        assert outcome["verdict"] == "held"
        assert outcome["reason"] is None
        assert outcome["window_shortfall_days"] == 0
        assert outcome["claim_level_2_supported"] is True
        assert outcome["rollback_recommended"] is False
        assert outcome["window"]["coverage_ticks"] == 20
        assert outcome["deltas"]["window"]["per_day"]["coverage_ticks"] == pytest.approx(20 / 7)
        assert "baseline band" in outcome["falsifier"]
        assert outcome["history"][-1]["event"] == "window_closed"

    def test_regressed_stays_observing_with_rollback_recommended(self, approved):
        release, merged_at = self._exposed(approved, window=(15, 20))
        closed = close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        assert closed.state == "observing"
        outcome = _outcome(release.id)
        assert outcome["verdict"] == "regressed"
        assert outcome["rollback_recommended"] is True
        assert outcome["claim_level_2_supported"] is False

    def test_close_window_undetermined_when_evidence_expired(self, approved):
        release, merged_at = self._exposed(approved)
        close_window(
            release.id, project_key=PK, now=merged_at + timedelta(days=EVIDENCE_TTL_DAYS + 1)
        )
        outcome = _outcome(release.id)
        assert outcome["verdict"] == "undetermined"
        assert outcome["reason"] == "EVIDENCE_EXPIRED"
        assert outcome["window"] is None
        assert get_release(release.id, PK).state == "observing"

    def test_close_window_undetermined_when_detection_declines(self, approved):
        release, merged_at = self._exposed(approved, window=(0, 5))
        close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        outcome = _outcome(release.id)
        assert outcome["verdict"] == "undetermined"
        assert outcome["reason"] == "DETECTION_DECLINED"
        assert outcome["detection_declined"] is True
        assert get_release(release.id, PK).state == "observing"

    def test_zero_coverage_is_undetermined_with_none_rate(self, approved):
        release, merged_at = self._exposed(approved, window=(1, 0))
        close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        outcome = _outcome(release.id)
        assert outcome["verdict"] == "undetermined"
        assert outcome["reason"] == "ZERO_DENOMINATOR"
        assert outcome["window"]["architectural_correction_rate"] is None
        assert outcome["claim_level_2_supported"] is False

    def test_claim_needs_positive_effect(self, charter):
        evaluation = _evaluation(charter, _experiment(), effect=-0.05)
        proposed = _propose(evaluation)
        _stamp_drill(proposed)
        approved = approve(proposed.id, approved_by="Tom Counsell", project_key=PK, now=NOW)
        release, merged_at = self._exposed(approved)
        closed = close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        assert closed.state == "accepted"
        assert _outcome(release.id)["claim_level_2_supported"] is False

    def test_due_windows(self, approved):
        release, merged_at = self._exposed(approved)
        assert due_windows(PK, now=merged_at + timedelta(days=6)) == []
        assert [r.id for r in due_windows(PK, now=merged_at + timedelta(days=7))] == [release.id]

    def test_close_window_never_calls_rollback(self):
        src = inspect.getsource(lifecycle.close_window)
        tree = ast.parse(src)
        names = [
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        attrs = [
            n.func.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ]
        assert names.count("rollback") == 0
        assert attrs.count("rollback") == 0


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


class TestRollback:
    def _exposed(self, approved):
        merged_at = NOW - timedelta(days=7)
        return _observing(approved, merged_at=merged_at)

    def test_successful_rollback(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = _rollback_runner()
        rolled = rollback(
            release.id,
            project_key=PK,
            reason="regressed window",
            runner=runner,
            root=tmp_path,
            now=NOW,
        )
        assert rolled.state == "rolled_back"
        outcome = _outcome(release.id)
        record = outcome["rollback"]
        assert record["revert_sha"] == REVERT_SHA
        assert record["parent_sha"] == PARENT_SHA
        assert record["pushed_to"] == "main"
        assert record["propagation"] == "requires /update on fleet machines"
        assert record["merge_commit_parents"] == 2
        assert outcome["history"][-1]["event"] == "rolled_back"
        argvs = runner.argvs()
        assert ["git", "revert", "--no-commit", "-m", "1", MERGE_SHA] in argvs
        commit = next(a for a in argvs if a[:2] == ["git", "commit"])
        assert (
            commit[-1]
            == f"Roll back improvement release {release.id}: regressed window (Refs #3218)"
        )
        push = next(a for a in argvs if a[:2] == ["git", "push"])
        assert push == ["git", "push", "origin", "HEAD:refs/heads/main"]
        assert ["git", "ls-remote", "origin", "refs/heads/main"] in argvs
        assert any(a[:3] == ["git", "worktree", "remove"] for a in argvs)
        # the transcript is persisted, through the worktree removal
        assert record["transcript"].index("$ git push origin") < record["transcript"].index(
            "$ git worktree remove"
        )
        assert "rollback_attempt" not in outcome

    def test_rollback_keeps_a_ref_to_the_revert_before_pushing(self, approved, tmp_path):
        """``refs/improvement-rollback/<id>`` points at the revert, written in the repo
        after the commit and before the push, so the commit outlives the worktree."""
        release = self._exposed(approved)
        runner = _rollback_runner()
        rollback(release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, now=NOW)
        calls = runner.calls
        keep = next(i for i, c in enumerate(calls) if c["argv"][:2] == ["git", "update-ref"])
        commit = next(i for i, c in enumerate(calls) if c["argv"][:2] == ["git", "commit"])
        push = next(i for i, c in enumerate(calls) if c["argv"][:2] == ["git", "push"])
        assert commit < keep < push
        assert calls[keep]["argv"] == [
            "git",
            "update-ref",
            f"refs/improvement-rollback/{release.id}",
            REVERT_SHA,
        ]
        assert calls[keep]["cwd"] == "/repo", "the ref lives in the repository, not the worktree"
        record = _outcome(release.id)["rollback"]
        assert record["rollback_ref"] == lifecycle.rollback_ref(release.id)

    def test_rollback_network_calls_carry_the_git_timeout(self, approved, tmp_path):
        from config.settings import settings

        release = self._exposed(approved)
        runner = _rollback_runner()
        rollback(release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, now=NOW)
        expected = settings.timeouts.git_subprocess_s
        for name in ("fetch", "push", "ls-remote", "rev-parse", "revert", "worktree"):
            calls = [c for c in runner.calls if c["argv"][:2] == ["git", name]]
            assert calls, name
            assert all(c["timeout"] == expected for c in calls), (name, calls)

    def test_rollback_fetches_before_worktree(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = _rollback_runner()
        rollback(release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, now=NOW)
        argvs = runner.argvs()
        fetch = argvs.index(["git", "fetch", "origin", "main"])
        add = next(
            i for i, a in enumerate(argvs) if a[:4] == ["git", "worktree", "add", "--detach"]
        )
        revert = next(i for i, a in enumerate(argvs) if a[:2] == ["git", "revert"])
        assert argvs[add][-1] == "origin/main"
        assert fetch < add < revert

    def test_rollback_refuses_when_remote_head_differs(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = _rollback_runner(remote_sha="e" * 40)
        with pytest.raises(ReleaseRefused) as exc:
            rollback(release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, now=NOW)
        assert exc.value.code == "ROLLBACK_PUSH_REFUSED"
        assert get_release(release.id, PK).state == "observing"
        outcome = _outcome(release.id)
        event = outcome["history"][-1]
        assert event["event"] == "rollback_push_refused"
        assert event["revert_sha"] == REVERT_SHA
        assert event["parent_sha"] == PARENT_SHA
        assert event["rollback_ref"] == f"refs/improvement-rollback/{release.id}"
        assert "rollback" not in outcome
        attempt = outcome["rollback_attempt"]
        assert attempt["code"] == "ROLLBACK_PUSH_REFUSED"
        assert "$ git ls-remote origin refs/heads/main" in attempt["transcript"]
        assert "$ git worktree remove" in attempt["transcript"]

    def test_rollback_refuses_nonzero_push(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = _rollback_runner(push=(1, "", "! [remote rejected] main -> main (protected)"))
        with pytest.raises(ReleaseRefused) as exc:
            rollback(release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, now=NOW)
        assert exc.value.code == "ROLLBACK_PUSH_REFUSED"
        assert get_release(release.id, PK).state == "observing"
        event = _outcome(release.id)["history"][-1]
        assert event["event"] == "rollback_push_refused"
        assert "protected" in event["stderr"]
        assert event["revert_sha"] == REVERT_SHA
        assert not any(a[:2] == ["git", "ls-remote"] for a in runner.argvs())

    def test_rollback_branch_falls_back_to_main_parent(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = RecordingRunner(
            [
                (["git", "fetch", "origin", "hotfix/x"], (128, "", "couldn't find remote ref")),
                (["git", "rev-parse", "HEAD"], (0, REVERT_SHA + "\n", "")),
                (["git", "rev-parse", "origin/main"], (0, PARENT_SHA + "\n", "")),
                (["git", "ls-remote"], (0, f"{REVERT_SHA}\trefs/heads/hotfix/x\n", "")),
            ]
        )
        rolled = rollback(
            release.id,
            project_key=PK,
            reason="r",
            runner=runner,
            branch="hotfix/x",
            root=tmp_path,
            repo=tmp_path,
            now=NOW,
        )
        assert rolled.state == "rolled_back"
        record = _outcome(release.id)["rollback"]
        assert record["pushed_to"] == "hotfix/x"
        assert record["worktree_ref"] == "origin/main"
        assert "gh pr create --head hotfix/x" in record["pr_command"]
        argvs = runner.argvs()
        assert ["git", "fetch", "origin", "main"] in argvs
        push = next(a for a in argvs if a[:2] == ["git", "push"])
        assert push == ["git", "push", "origin", "HEAD:refs/heads/hotfix/x"]
        assert ["git", "ls-remote", "origin", "refs/heads/hotfix/x"] in argvs
        # squash merge: one parent, plain revert
        assert ["git", "revert", "--no-commit", MERGE_SHA] in argvs

    def test_rollback_refuses_checkout_path(self, approved, tmp_path):
        """The [DESTRUCTIVE] No-Go: a slot inside a git checkout is refused before any step."""
        checkout = tmp_path / "checkout"
        (checkout / ".git").mkdir(parents=True)
        release = self._exposed(approved)
        runner = _rollback_runner()
        with pytest.raises(ReleaseRefused) as exc:
            rollback(release.id, project_key=PK, reason="r", runner=runner, root=checkout, now=NOW)
        assert exc.value.code == "ROLLBACK_STEP_FAILED"
        assert "CHECKOUT_PATH" in exc.value.detail
        assert get_release(release.id, PK).state == "observing"
        assert not (checkout / "drills").exists()
        assert not any(a[:2] == ["git", "fetch"] for a in runner.argvs())
        assert not any(a[:2] == ["git", "worktree"] for a in runner.argvs())

    def test_rollback_refuses_wrong_state(self, approved, tmp_path):
        with pytest.raises(ReleaseRefused) as exc:
            rollback(
                approved.id, project_key=PK, reason="r", runner=_rollback_runner(), root=tmp_path
            )
        assert exc.value.code == "WRONG_STATE"

    def test_rollback_from_accepted(self, approved, tmp_path):
        merged_at = NOW - timedelta(days=7)
        _seed_evidence(merged_at - timedelta(days=3), architectural=2, ticks=20)
        _seed_evidence(merged_at + timedelta(days=3), architectural=2, ticks=20)
        release = _observing(approved, merged_at=merged_at)
        close_window(release.id, project_key=PK, now=merged_at + timedelta(days=7))
        assert get_release(release.id, PK).state == "accepted"
        rolled = rollback(
            release.id, project_key=PK, reason="r", runner=_rollback_runner(), root=tmp_path
        )
        assert rolled.state == "rolled_back"

    def test_rollback_revert_conflict_leaves_state(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = RecordingRunner(
            [
                (["git", "rev-parse", "HEAD"], (0, REVERT_SHA + "\n", "")),
                (["git", "revert"], (1, "", "CONFLICT (content): tools/x.py")),
            ]
        )
        with pytest.raises(ReleaseRefused) as exc:
            rollback(
                release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, repo=tmp_path
            )
        assert exc.value.code == "ROLLBACK_STEP_FAILED"
        assert get_release(release.id, PK).state == "observing"
        assert not any(a[:2] == ["git", "push"] for a in runner.argvs())
        assert any(a[:3] == ["git", "worktree", "remove"] for a in runner.argvs())
        # the failed attempt and its transcript land on the row
        outcome = _outcome(release.id)
        assert outcome["history"][-1]["event"] == "rollback_step_failed"
        assert "git revert exited 1" in outcome["history"][-1]["detail"]
        attempt = outcome["rollback_attempt"]
        assert attempt["code"] == "ROLLBACK_STEP_FAILED"
        assert "CONFLICT (content): tools/x.py" in attempt["transcript"]
        assert "$ git worktree remove" in attempt["transcript"]
        assert [s["name"] for s in attempt["steps"]][-1] == "revert"
        assert "rollback" not in outcome

    def test_rollback_transcript_is_bounded(self, approved, tmp_path):
        from tools.improvement_release.lifecycle import ROLLBACK_TRANSCRIPT_BYTES

        release = self._exposed(approved)
        runner = RecordingRunner(
            [
                (["git", "rev-parse", "HEAD"], (0, REVERT_SHA + "\n", "")),
                (["git", "revert"], (1, "x" * (ROLLBACK_TRANSCRIPT_BYTES * 2), "conflict")),
            ]
        )
        with pytest.raises(ReleaseRefused):
            rollback(
                release.id, project_key=PK, reason="r", runner=runner, root=tmp_path, repo=tmp_path
            )
        transcript = _outcome(release.id)["rollback_attempt"]["transcript"]
        assert len(transcript.encode("utf-8")) <= ROLLBACK_TRANSCRIPT_BYTES
        assert "[rollback refused: git revert exited 1: conflict]" in transcript
        assert transcript.endswith("$ git worktree prune\n[exit 0 in 0.00 s]"), (
            "the tail keeps the end: the refusal and the worktree removal"
        )

    @pytest.mark.parametrize("branch", ["-x", "--prune", " ", ""])
    def test_rollback_refuses_option_shaped_branch_before_fetch(self, approved, tmp_path, branch):
        release = self._exposed(approved)
        runner = _rollback_runner()
        with pytest.raises(ReleaseRefused) as exc:
            rollback(
                release.id,
                project_key=PK,
                reason="r",
                runner=runner,
                branch=branch,
                root=tmp_path,
                now=NOW,
            )
        assert exc.value.code == "ROLLBACK_STEP_FAILED"
        assert not any(a[:2] == ["git", "fetch"] for a in runner.argvs())
        assert get_release(release.id, PK).state == "observing"
        assert "rollback_attempt" not in _outcome(release.id)

    def test_rollback_refuses_a_branch_git_rejects(self, approved, tmp_path):
        release = self._exposed(approved)
        runner = RecordingRunner(
            [(["git", "check-ref-format"], (1, "", "fatal: 'a..b' is not a valid branch name"))]
        )
        with pytest.raises(ReleaseRefused) as exc:
            rollback(
                release.id,
                project_key=PK,
                reason="r",
                runner=runner,
                branch="a..b",
                root=tmp_path,
                repo=tmp_path,
                now=NOW,
            )
        assert exc.value.code == "ROLLBACK_STEP_FAILED"
        assert "check-ref-format" in exc.value.detail
        argvs = runner.argvs()
        assert ["git", "check-ref-format", "--branch", "a..b"] in argvs
        assert not any(a[:2] == ["git", "fetch"] for a in argvs)
