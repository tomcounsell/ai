"""``tools/improvement_investigations.py``: the eight kinds, the claim rule, the
assumption guard, the vault-request disposition, the stage lifecycle, and the
CLI wrappers around them (lane 5, #3217, task 4).

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): every ORM row
and every ``improve:*`` key lands in the claimed per-worker test db.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_charter import ImprovementCharter
from models.improvement_investigation import (
    INVESTIGATION_KINDS,
    ImprovementInvestigation,
)
from tools import improvement as cli
from tools import improvement_investigations as inv
from tools.improvement_control.journal import journal_tail, read_head
from tools.improvement_control.projection import apply
from tools.improvement_resources import RESOURCES

PK = "valor"  # the CLI is hardcoded to PROJECT_KEY = "valor"

VALID_CLAIM = {
    "claim": "OpenRouter lists a :free tier for Llama 3.3 70B",
    "url": "https://openrouter.ai/models",
    "retrieved_at": "2026-09-14T10:00:00+00:00",
}

COMPLETE_DETAIL = {
    "charter_passage": "§7 models and providers",
    "evidence_ids": [],
    "confidence": "medium",
    "consequence": "the promise judge runs on the free tier until measured",
    "overturning_observation": "a metered receipt above $0 for the free model",
}


def pinned_digest() -> str:
    pinned = ImprovementCharter.pinned(PK)
    if pinned is not None:
        return pinned.digest
    row = ImprovementCharter.create(
        project_key=PK,
        created_at=datetime.now(UTC),
        digest=f"sha256:{uuid.uuid4().hex}",
        state="active",
        version=2,
    )
    return row.digest


def new_case(*, state: str = "observed", **overrides) -> ImprovementCase:
    fields = dict(
        project_key=PK,
        state=state,
        title="seeded case",
        summary="what the system thinks is wrong",
        created_at=datetime.now(UTC),
        priority_area="inference",
        ranking_rationale="charter §3 names inference first",
        charter_digest=pinned_digest(),
    )
    fields.update(overrides)
    return ImprovementCase.create(**fields)


def open_ok(case, **overrides) -> ImprovementInvestigation:
    fields = dict(
        kind="web_research",
        case_id=case.id,
        uncertainty="what the provider offers today",
        query="openrouter free tier models september 2026",
        decision_affected="which cheap model the promise judge uses",
        expected_information_value="one config entry or one vault request",
    )
    fields.update(overrides)
    result = inv.open_investigation(PK, **fields)
    assert result.accepted, result.reason
    return ImprovementInvestigation.query.get(project_key=PK, id=result.investigation_id)


def run_cli(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli.main(["--json", *argv])
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else {})


class TestOpenInvestigation:
    def test_open_journals_and_moves_an_observed_case_to_investigating(self):
        case = new_case(state="observed")
        row = open_ok(case)
        assert row.state == "open"
        assert row.stage == "draft"
        assert json.loads(row.prior_answers) == []
        events = [e["event"] for e in journal_tail(PK, case.id, 10)]
        assert events == ["investigation_opened", "state_changed"]
        assert read_head(PK, case.id).state == "investigating"
        assert ImprovementCase.query.get(project_key=PK, id=case.id).state == "investigating"

    def test_case_state_survives_a_later_projection_apply(self):
        case = new_case(state="observed")
        open_ok(case)
        apply(PK, case.id)
        assert ImprovementCase.query.get(project_key=PK, id=case.id).state == "investigating"

    def test_second_open_on_an_investigating_case_writes_no_state_change(self):
        case = new_case(state="observed")
        open_ok(case)
        open_ok(case, query="a different question entirely about pricing")
        events = [e["event"] for e in journal_tail(PK, case.id, 10)]
        assert events == ["investigation_opened", "state_changed", "investigation_opened"]

    def test_unknown_kind_is_refused(self):
        case = new_case()
        result = inv.open_investigation(
            PK,
            kind="question",
            case_id=case.id,
            uncertainty="u",
            query="q",
            decision_affected="d",
            expected_information_value="v",
        )
        assert not result.accepted
        assert result.reason == "INVALID_KIND"

    def test_every_declared_kind_opens(self):
        case = new_case()
        for kind in INVESTIGATION_KINDS:
            open_ok(case, kind=kind, query=f"{kind} question about the case")

    def test_missing_case_is_refused(self):
        result = inv.open_investigation(
            PK,
            kind="web_research",
            case_id="no-such-case",
            uncertainty="u",
            query="q",
            decision_affected="d",
            expected_information_value="v",
        )
        assert (result.accepted, result.reason) == (False, "CASE_NOT_FOUND")

    def test_intake_needs_no_case_but_other_kinds_do(self):
        pool = inv.open_investigation(
            PK,
            kind="inspiration_intake",
            case_id=None,
            uncertainty="u",
            query="https://example.com/talk",
            decision_affected="d",
            expected_information_value="v",
        )
        assert pool.accepted
        other = inv.open_investigation(
            PK,
            kind="probe",
            case_id=None,
            uncertainty="u",
            query="q",
            decision_affected="d",
            expected_information_value="v",
        )
        assert (other.accepted, other.reason) == (False, "CASE_REQUIRED")

    def test_awaiting_authorization_only_for_charter_amendment(self):
        case = new_case()
        refused = inv.open_investigation(
            PK,
            kind="web_research",
            case_id=case.id,
            uncertainty="u",
            query="q",
            decision_affected="d",
            expected_information_value="v",
            state="awaiting_authorization",
        )
        assert (refused.accepted, refused.reason) == (False, "AWAITING_REQUIRES_AMENDMENT")
        allowed = inv.open_investigation(
            PK,
            kind="charter_amendment",
            case_id=case.id,
            uncertainty="u",
            query="raise the unit-2 ceiling to $12/day",
            decision_affected="d",
            expected_information_value="v",
            state="awaiting_authorization",
        )
        assert allowed.accepted
        row = ImprovementInvestigation.query.get(project_key=PK, id=allowed.investigation_id)
        assert row.state == "awaiting_authorization"
        case = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert "raise the unit-2 ceiling to $12/day" in case.summary

    def test_novelty_check_fills_prior_answers_and_marks_deduplicated(self):
        case = new_case()
        first = open_ok(case, query="openrouter free tier models september 2026 list all")
        done = inv.resolve(first.id, interpretation="three free models exist")
        assert done.accepted
        rejected = new_case(
            state="rejected",
            dedup_identity=inv.normalized_prefix(
                "openrouter free tier models september 2026 list all"
            ),
        )
        second = open_ok(
            case, query="OpenRouter free-tier models, September 2026: list all new ones"
        )
        assert second.stage == "deduplicated"
        assert set(json.loads(second.prior_answers)) == {first.id, rejected.id}

    def test_busy_case_is_a_reason_code(self):
        from config.settings import settings
        from tools.improvement_control import keys
        from tools.improvement_control.lease import default_lease

        case = new_case()
        lease = default_lease()
        gen = lease.acquire(keys.lease_key(PK, case.id), ttl=settings.improvement.lease_ttl_seconds)
        try:
            result = inv.open_investigation(
                PK,
                kind="probe",
                case_id=case.id,
                uncertainty="u",
                query="q",
                decision_affected="d",
                expected_information_value="v",
            )
        finally:
            lease.release(keys.lease_key(PK, case.id), gen)
        assert (result.accepted, result.reason) == (False, "CASE_BUSY")
        assert list(ImprovementInvestigation.query.filter(project_key=PK)) == []


class TestRecordClaims:
    def test_none_raises_and_empty_is_zero(self):
        row = open_ok(new_case())
        with pytest.raises(ValueError):
            inv.record_claims(row.id, None)
        assert inv.record_claims(row.id, []) == 0

    def test_valid_claim_is_a_claim_and_lands_in_sources(self):
        row = open_ok(new_case())
        assert inv.record_claims(row.id, [VALID_CLAIM]) == 1
        row = ImprovementInvestigation.query.get(project_key=PK, id=row.id)
        stored = json.loads(row.claims)
        assert stored["claims"][0]["is_claim"] is True
        assert stored["claims"][0]["url"] == VALID_CLAIM["url"]
        assert stored["notes"] == []
        assert json.loads(row.sources)[0]["url"] == VALID_CLAIM["url"]
        assert row.stage == "recorded"

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param({**VALID_CLAIM, "url": ""}, id="claim_without_url_is_a_note"),
            pytest.param({**VALID_CLAIM, "url": "ftp://x"}, id="claim_with_non_http_url_is_a_note"),
            pytest.param(
                {"claim": VALID_CLAIM["claim"], "url": VALID_CLAIM["url"]},
                id="claim_without_date_is_a_note",
            ),
            pytest.param(
                {**VALID_CLAIM, "retrieved_at": "yesterday"}, id="claim_with_bad_date_is_a_note"
            ),
            pytest.param({**VALID_CLAIM, "claim": "   "}, id="whitespace_claim_is_a_note"),
        ],
    )
    def test_incomplete_entry_is_stored_as_a_note_never_dropped(self, entry):
        row = open_ok(new_case())
        assert inv.record_claims(row.id, [entry]) == 1
        stored = json.loads(ImprovementInvestigation.query.get(project_key=PK, id=row.id).claims)
        assert stored["claims"] == []
        assert len(stored["notes"]) == 1
        assert stored["notes"][0]["is_claim"] is False

    def test_explicit_sources_are_kept(self):
        row = open_ok(new_case())
        inv.record_claims(
            row.id,
            [VALID_CLAIM],
            sources=[{"url": "https://docs.example", "retrieved_at": "2026-09-14", "title": "t"}],
        )
        sources = json.loads(ImprovementInvestigation.query.get(project_key=PK, id=row.id).sources)
        assert {s["url"] for s in sources} == {VALID_CLAIM["url"], "https://docs.example"}

    def test_missing_row_and_non_open_row_are_refused(self):
        with pytest.raises(inv.InvestigationRefusedError) as missing:
            inv.record_claims("no-such-row", [VALID_CLAIM])
        assert missing.value.reason == "INVESTIGATION_NOT_FOUND"
        row = open_ok(new_case())
        assert inv.resolve(row.id, interpretation="done").accepted
        with pytest.raises(inv.InvestigationRefusedError) as closed:
            inv.record_claims(row.id, [VALID_CLAIM])
        assert closed.value.reason == "INVESTIGATION_NOT_OPEN"


class TestResolve:
    def test_resolve_sets_interpreted_resolved_and_resolved_at(self):
        row = open_ok(new_case())
        result = inv.resolve(row.id, interpretation="the free tier exists")
        assert result.accepted
        row = ImprovementInvestigation.query.get(project_key=PK, id=row.id)
        assert (row.stage, row.state) == ("interpreted", "resolved")
        assert row.resolved_at is not None
        assert row.interpretation == "the free tier exists"

    def test_resolve_leaves_case_state_alone(self):
        case = new_case(state="observed")
        row = open_ok(case)
        inv.resolve(row.id, interpretation="done")
        assert ImprovementCase.query.get(project_key=PK, id=case.id).state == "investigating"

    @pytest.mark.parametrize(
        "missing", ["charter_passage", "confidence", "consequence", "overturning_observation"]
    )
    def test_assumption_detail_must_be_complete(self, missing):
        row = open_ok(new_case())
        detail = {k: v for k, v in COMPLETE_DETAIL.items() if k != missing}
        result = inv.resolve(
            row.id,
            interpretation="i",
            provisional_assumption="the free tier stays free",
            assumption_detail=detail,
        )
        assert (result.accepted, result.reason) == (False, "ASSUMPTION_DETAIL_INCOMPLETE")
        assert missing in result.message

    def test_assumption_without_detail_is_refused(self):
        row = open_ok(new_case())
        result = inv.resolve(row.id, interpretation="i", provisional_assumption="a")
        assert (result.accepted, result.reason) == (False, "ASSUMPTION_DETAIL_INCOMPLETE")

    @pytest.mark.parametrize(
        "consequence",
        [
            pytest.param("this redefines the intended outcome to weekly digests", id="redefines"),
            pytest.param("the requirement for a URL on every claim no longer applies", id="erases"),
            pytest.param("the session is authorized to place the credential itself", id="grants"),
            pytest.param("the unit-2 budget is raised to $20 per day", id="increases"),
        ],
    )
    def test_assumption_guard_refuses_the_four_patterns(self, consequence):
        row = open_ok(new_case())
        result = inv.resolve(
            row.id,
            interpretation="i",
            provisional_assumption="a",
            assumption_detail={**COMPLETE_DETAIL, "consequence": consequence},
        )
        assert (result.accepted, result.reason) == (False, "ASSUMPTION_EXCEEDS_AUTHORITY")
        assert "propose-amendment" in result.message
        assert ImprovementInvestigation.query.get(project_key=PK, id=row.id).state == "open"

    def test_assumption_summary_lands_on_the_case(self):
        case = new_case()
        row = open_ok(case)
        result = inv.resolve(
            row.id,
            interpretation="i",
            provisional_assumption="the free tier stays free through October",
            assumption_detail=COMPLETE_DETAIL,
        )
        assert result.accepted, result.message
        case = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert "the free tier stays free through October" in case.summary
        assert "medium" in case.summary
        row = ImprovementInvestigation.query.get(project_key=PK, id=row.id)
        assert json.loads(row.assumption_detail)["confidence"] == "medium"

    def test_blocked_by_names_a_known_resource(self):
        case = new_case()
        row = open_ok(case, kind="resource_acquisition")
        refused = inv.resolve(
            row.id,
            interpretation="needs a key",
            disposition="vault_request_written",
            resource_name="muse_spark",
        )
        assert (refused.accepted, refused.reason) == (False, "UNKNOWN_RESOURCE")
        assert ImprovementCase.query.get(project_key=PK, id=case.id).blocked_by is None
        no_name = inv.resolve(
            row.id, interpretation="needs a key", disposition="vault_request_written"
        )
        assert (no_name.accepted, no_name.reason) == (False, "RESOURCE_NAME_REQUIRED")
        accepted = inv.resolve(
            row.id,
            interpretation="needs a key",
            disposition="vault_request_written",
            resource_name="meta_model_api",
        )
        assert accepted.accepted, accepted.message
        assert "meta_model_api" in RESOURCES
        case = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert case.blocked_by == "vault:meta_model_api"
        assert "Meta Model API key" in case.summary
        assert "fingerprint" in case.summary
        assert "open-source" in case.summary
        assert inv.disposition_of(
            ImprovementInvestigation.query.get(project_key=PK, id=row.id)
        ) == ("vault_request_written", "meta_model_api")

    def test_blocked_by_survives_expiry(self):
        case = new_case()
        row = open_ok(case, kind="resource_acquisition")
        inv.resolve(
            row.id,
            interpretation="needs a key",
            disposition="vault_request_written",
            resource_name="meta_model_api",
        )
        ImprovementInvestigation.query.get(project_key=PK, id=row.id).delete()
        assert ImprovementInvestigation.query.get(project_key=PK, id=row.id) is None
        case = ImprovementCase.query.get(project_key=PK, id=case.id)
        assert case.blocked_by == "vault:meta_model_api"
        assert "Meta Model API key" in case.summary

    def test_unknown_disposition_is_refused(self):
        row = open_ok(new_case(), kind="resource_acquisition")
        result = inv.resolve(row.id, interpretation="i", disposition="bought_it")
        assert (result.accepted, result.reason) == (False, "INVALID_DISPOSITION")

    def test_resolving_twice_is_refused(self):
        row = open_ok(new_case())
        assert inv.resolve(row.id, interpretation="i").accepted
        again = inv.resolve(row.id, interpretation="i")
        assert (again.accepted, again.reason) == (False, "INVESTIGATION_NOT_OPEN")


class TestStageLifecycle:
    def test_advances_in_order_and_refuses_a_skip(self):
        row = open_ok(new_case())
        assert inv.transition_investigation(row.id, "deduplicated").accepted
        skipped = inv.transition_investigation(row.id, "running")
        assert (skipped.accepted, skipped.reason) == (False, "STAGE_SKIPPED")
        assert inv.transition_investigation(row.id, "policy_checked").accepted
        assert ImprovementInvestigation.query.get(project_key=PK, id=row.id).stage == (
            "policy_checked"
        )

    def test_exit_stages_are_reachable_from_any_open_stage(self):
        row = open_ok(new_case())
        assert inv.transition_investigation(row.id, "failed").accepted
        after = inv.transition_investigation(row.id, "deduplicated")
        assert (after.accepted, after.reason) == (False, "STAGE_TERMINAL")

    def test_unknown_stage_and_awaiting_state_guard(self):
        row = open_ok(new_case())
        assert inv.transition_investigation(row.id, "questioned").reason == "UNKNOWN_STAGE"
        guarded = inv.transition_investigation(row.id, state="awaiting_authorization")
        assert (guarded.accepted, guarded.reason) == (False, "AWAITING_REQUIRES_AMENDMENT")


class TestAmendmentResolution:
    def test_awaiting_rows_resolve_on_a_new_digest(self):
        case = new_case()
        old = inv.open_investigation(
            PK,
            kind="charter_amendment",
            case_id=case.id,
            uncertainty="u",
            query="amend §8",
            decision_affected="d",
            expected_information_value="v",
            state="awaiting_authorization",
        )
        resolved = inv.resolve_awaiting_on_new_digest(PK, "sha256:new")
        assert resolved == [old.investigation_id]
        row = ImprovementInvestigation.query.get(project_key=PK, id=old.investigation_id)
        assert row.state == "resolved"
        assert row.interpretation == "charter digest changed to sha256:new"
        assert inv.resolve_awaiting_on_new_digest(PK, "sha256:new") == []

    def test_list_filters_by_case(self):
        a, b = new_case(), new_case()
        open_ok(a)
        open_ok(b)
        assert len(inv.list_investigations(PK)) == 2
        assert [r.case_id for r in inv.list_investigations(PK, case_id=a.id)] == [a.id]


class TestCli:
    def test_open_record_resolve_list_round_trip(self, capsys, tmp_path):
        case = new_case()
        code, payload = run_cli(
            [
                "investigation",
                "open",
                "--kind",
                "web_research",
                "--case",
                case.id,
                "--uncertainty",
                "u",
                "--query",
                "q about pricing",
                "--decision-affected",
                "d",
                "--expected-information-value",
                "v",
            ],
            capsys,
        )
        assert code == 0, payload
        inv_id = payload["investigation_id"]
        claims_file = tmp_path / "claims.json"
        claims_file.write_text(json.dumps([VALID_CLAIM, {"claim": "no url"}]))
        code, payload = run_cli(
            ["investigation", "record", "--id", inv_id, "--claims", f"@{claims_file}"], capsys
        )
        assert (code, payload["recorded"]) == (0, 2)
        code, payload = run_cli(
            [
                "investigation",
                "resolve",
                "--id",
                inv_id,
                "--interpretation",
                "done",
                "--assumption",
                "a",
                "--assumption-detail",
                json.dumps(COMPLETE_DETAIL),
            ],
            capsys,
        )
        assert code == 0, payload
        code, payload = run_cli(["investigation", "list", "--case", case.id], capsys)
        assert code == 0
        assert [r["id"] for r in payload["investigations"]] == [inv_id]
        assert payload["investigations"][0]["state"] == "resolved"

    def test_cli_refusals_exit_one_with_the_reason(self, capsys):
        case = new_case()
        code, payload = run_cli(
            [
                "investigation",
                "open",
                "--kind",
                "web_research",
                "--case",
                case.id,
                "--uncertainty",
                "u",
                "--query",
                "q",
                "--decision-affected",
                "d",
                "--expected-information-value",
                "v",
                "--state",
                "awaiting_authorization",
            ],
            capsys,
        )
        assert (code, payload["reason"]) == (1, "AWAITING_REQUIRES_AMENDMENT")
        code, payload = run_cli(
            ["investigation", "record", "--id", "nope", "--claims", "[]"], capsys
        )
        assert (code, payload["reason"]) == (1, "INVESTIGATION_NOT_FOUND")
        code, payload = run_cli(
            ["investigation", "record", "--id", "nope", "--claims", "not json"], capsys
        )
        assert (code, payload["reason"]) == (1, "INVALID_CLAIMS_JSON")

    def test_revise_model_refuses_an_empty_prediction_and_writes_a_spec(self, capsys):
        case = new_case()
        code, payload = run_cli(
            [
                "revise-model",
                "--case",
                case.id,
                "--summary",
                "s",
                "--rationale",
                "r",
                "--prediction",
                "   ",
            ],
            capsys,
        )
        assert (code, payload["reason"]) == (1, "EMPTY_PREDICTION")
        code, payload = run_cli(
            [
                "revise-model",
                "--case",
                case.id,
                "--summary",
                "s",
                "--rationale",
                "r",
                "--prediction",
                "p",
            ],
            capsys,
        )
        assert code == 0, payload
        from models.improvement_model_revision import ImprovementModelRevision

        row = ImprovementModelRevision.query.get(project_key=PK, id=payload["revision_id"])
        spec = json.loads(row.research_process_spec)
        assert spec["selection_rule"] == "ordinal-lexicographic-v1"
        assert spec["investigation_budget_split"] == {}
        assert set(spec) == {
            "selection_rule",
            "investigation_budget_split",
            "revision_cadence_seconds",
            "planner_prompt_digest",
            "skill_digest",
            "extra",
        }
        assert row.research_process_spec == json.dumps(spec, sort_keys=True, separators=(",", ":"))
        assert row.prediction == "p"
        from tools.improvement_recursion.process import ResearchProcessSpec, research_process_digest

        assert row.research_process_digest == research_process_digest(ResearchProcessSpec(**spec))
        assert payload["research_process_digest"] == row.research_process_digest

    def test_revise_model_backfill_digests_fills_only_the_missing_ones(self, capsys):
        from models.improvement_model_revision import ImprovementModelRevision
        from tools.improvement_recursion.process import ResearchProcessSpec, research_process_digest

        spec = ResearchProcessSpec(
            selection_rule="ordinal-lexicographic-v1",
            investigation_budget_split={},
            revision_cadence_seconds=900,
            planner_prompt_digest="sha256:" + "a" * 64,
            skill_digest="sha256:" + "b" * 64,
            extra={"ranking_module_digest": "sha256:" + "c" * 64},
        )
        from tools.improvement_ranking import process_spec_json

        pre_merge = ImprovementModelRevision.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="superseded",
            revision=1,
            summary="s",
            rationale="r",
            prediction="p",
            research_process_spec=process_spec_json(spec),
            research_process_digest=None,
        )
        already = ImprovementModelRevision.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="current",
            revision=2,
            summary="s",
            rationale="r",
            prediction="p",
            research_process_spec=process_spec_json(spec),
            research_process_digest="sha256:" + "9" * 64,
        )
        no_spec = ImprovementModelRevision.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            state="superseded",
            revision=0,
            summary="s",
            rationale="r",
            prediction="p",
        )
        code, payload = run_cli(["revise-model", "--backfill-digests"], capsys)
        assert code == 0
        assert payload["backfilled"] == [pre_merge.id]
        assert ImprovementModelRevision.query.get(
            project_key=PK, id=pre_merge.id
        ).research_process_digest == research_process_digest(spec)
        assert (
            ImprovementModelRevision.query.get(
                project_key=PK, id=already.id
            ).research_process_digest
            == "sha256:" + "9" * 64
        )
        assert (
            ImprovementModelRevision.query.get(
                project_key=PK, id=no_spec.id
            ).research_process_digest
            is None
        )
        code, payload = run_cli(["revise-model", "--backfill-digests"], capsys)
        assert payload["backfilled"] == []
        code, payload = run_cli(["revise-model", "--summary", "s"], capsys)
        assert (code, payload["reason"]) == (1, "MISSING_ARGUMENT")

    def test_case_open_wiring_without_the_planner_module(self, capsys, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "reflections.improvement_plan", None)
        code, payload = run_cli(["case", "open"], capsys)
        assert code == 1
        assert payload["reason"] == "PLANNER_UNAVAILABLE"

    def test_case_open_runs_the_planner_seam_on_named_evidence(self, capsys):
        from models.improvement_evidence import ImprovementEvidence

        pinned_digest()
        row = ImprovementEvidence.create(
            project_key=PK,
            created_at=datetime.now(UTC),
            kind="inspiration",
            classification="unknown",
            source_ref="seed:test-case-open",
            text="charter §3: cheap inference first",
            detail=json.dumps({"seed": "charter-s3:test", "priority_area": "inference"}),
        )
        code, payload = run_cli(["case", "open", "--evidence-ids", row.id], capsys)
        assert code == 0, payload
        assert len(payload["opened"]) == 1
        (case_id,) = payload["opened"]
        case = ImprovementCase.query.get(project_key=PK, id=case_id)
        assert json.loads(case.evidence_ids) == [row.id]
        assert [e["event"] for e in journal_tail(PK, case_id, 5)] == ["case_opened"]
        code, payload = run_cli(["case", "open", "--evidence-ids", "no-such-row"], capsys)
        assert (code, payload["reason"]) == (1, "EVIDENCE_NOT_FOUND")
