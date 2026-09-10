"""Teardown ladder tests (lane 7, #3274).

The export-verification guard is the lane's most important negative test and
is mutation-checked in both directions by hand (see task 12): invert the
``if not verified`` guard and both the survival and the escalation assertions
must fail. The escalation record is asserted in each direction, because a
guard whose escalation goes nowhere is indistinguishable from no escalation.
"""

from __future__ import annotations

from tools.infrastructure_budget import apply_teardown, classify_resource

PK = "test-3274-teardown"
WINDOW = "2026-W37"
NEXT_WINDOW = "2026-W38"


def standing(name="vm-1"):
    return {"name": name, "window_key": WINDOW}


def trial(name="vm-2"):
    return {"name": name, "window_key": WINDOW, "trial_ref": "exp-9"}


def teardown_kwargs(**over):
    kw = dict(
        open_sessions=[],
        export_verifier=lambda resource: True,
        destroy=lambda resource: True,
        continuation_forecast_usd=4.0,
        next_window_key=NEXT_WINDOW,
        project_key=PK,
    )
    kw.update(over)
    return kw


def escalation_rows():
    from models.improvement_evidence import ImprovementEvidence

    return [
        r
        for r in ImprovementEvidence.recent(PK, limit=200)
        if r.kind == "spend_receipt" and (r.text or "").startswith("unit-3 overrun")
    ]


class TestExportVerification:
    def test_export_verification_raise_keeps_resource_running(self):
        calls = []

        def verifier(resource):
            raise RuntimeError("export store unreachable")

        outcomes = apply_teardown(
            [standing("vm-raise")],
            **teardown_kwargs(export_verifier=verifier, destroy=calls.append),
        )
        assert len(outcomes) == 1
        assert outcomes[0].action == "kept_running"
        assert calls == []

    def test_export_verification_false_keeps_resource_running(self):
        calls = []
        outcomes = apply_teardown(
            [standing("vm-false")],
            **teardown_kwargs(export_verifier=lambda resource: False, destroy=calls.append),
        )
        assert outcomes[0].action == "kept_running"
        assert calls == []

    def test_export_verification_true_tears_down(self):
        calls = []
        outcomes = apply_teardown(
            [standing("vm-ok")],
            **teardown_kwargs(destroy=lambda resource: calls.append(resource) or True),
        )
        assert outcomes[0].action == "torn_down"
        assert len(calls) == 1

    def test_export_verification_unconfirmed_destroy_keeps_running(self):
        outcomes = apply_teardown(
            [standing("vm-unconfirmed")],
            **teardown_kwargs(destroy=lambda resource: False),
        )
        assert outcomes[0].action == "kept_running"
        assert outcomes[0].escalated is True

    def test_export_verification_raising_destroy_keeps_running(self):
        def destroy(resource):
            raise RuntimeError("provider timeout")

        outcomes = apply_teardown(
            [standing("vm-destroy-raise")], **teardown_kwargs(destroy=destroy)
        )
        assert outcomes[0].action == "kept_running"
        assert outcomes[0].escalated is True


class TestEscalationRecord:
    def test_escalation_record_on_verifier_raise(self):
        before = len(escalation_rows())
        apply_teardown(
            [standing("vm-esc-raise")],
            **teardown_kwargs(
                export_verifier=_boom,
            ),
        )
        rows = escalation_rows()
        assert len(rows) == before + 1
        import json as _json

        detail = _json.loads(rows[0].detail)
        assert detail["resource"] == "vm-esc-raise"
        assert detail["teardown_attempt"] == "teardown"
        assert "raised" in detail["verifier_failure_mode"]
        assert detail["forecast_usd"] == 4.0

    def test_escalation_record_on_verifier_false(self):
        before = len(escalation_rows())
        apply_teardown(
            [standing("vm-esc-false")],
            **teardown_kwargs(export_verifier=lambda resource: False),
        )
        rows = escalation_rows()
        assert len(rows) == before + 1
        import json as _json

        detail = _json.loads(rows[0].detail)
        assert detail["resource"] == "vm-esc-false"
        assert detail["verifier_failure_mode"] == "returned_False"

    def test_escalation_record_optional_sink_called(self):
        seen = []
        apply_teardown(
            [standing("vm-esc-sink")],
            **teardown_kwargs(
                export_verifier=lambda resource: False,
                on_escalation=seen.append,
            ),
        )
        assert len(seen) == 1 and seen[0]["resource"] == "vm-esc-sink"


def _boom(resource):
    raise RuntimeError("export store unreachable")


class TestTeardownLadder:
    def test_ladder_trial_with_open_session_continues(self):
        calls = []
        outcomes = apply_teardown(
            [standing("vm-open")],
            **teardown_kwargs(
                open_sessions=[{"resource": "vm-open", "done": False}],
                destroy=calls.append,
            ),
        )
        assert outcomes[0].classification == "trial"
        assert outcomes[0].action == "continues"
        assert calls == []

    def test_ladder_finished_session_does_not_block(self):
        outcomes = apply_teardown(
            [standing("vm-done")],
            **teardown_kwargs(open_sessions=[{"resource": "vm-done", "done": True}]),
        )
        assert outcomes[0].classification == "standing"
        assert outcomes[0].action == "torn_down"

    def test_ladder_declared_trial_continues(self):
        before = len(escalation_rows())
        outcomes = apply_teardown([trial("vm-trial")], **teardown_kwargs())
        assert outcomes[0].classification == "trial"
        assert outcomes[0].action == "continues"
        # The continuation is booked as a forecast overrun before it accrues.
        assert len(escalation_rows()) == before + 1

    def test_ladder_ended_trial_tears_down(self):
        resource = trial("vm-ended")
        resource["trial_ended"] = True
        outcomes = apply_teardown([resource], **teardown_kwargs())
        assert outcomes[0].classification == "trial"
        assert outcomes[0].action == "torn_down"

    def test_classify_resource_prefers_open_session(self):
        assert classify_resource({"name": "a"}, [{"resource": "a", "done": False}]) == "trial"
        assert (
            classify_resource({"name": "a", "trial_ref": "x"}, [{"resource": "a", "done": True}])
            == "trial"
        )
        assert classify_resource({"name": "a"}, []) == "standing"
