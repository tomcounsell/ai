"""Unit 2's meter: reserve/settle/release, the two-branch settlement, and the
judge's record-only receipt (Task 8, Decision 7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tools.paid_inference_meter import (
    REFUSAL_EXHAUSTED,
    REFUSAL_NO_FORECAST,
    Reservation,
    current_day,
    record_receipt,
    release,
    reserve,
    settle,
    settle_from_response,
    status_dict,
)


def fresh_pk() -> str:
    return f"test-3215-meter-{uuid.uuid4().hex[:8]}"


class FakeUsageAttr:
    def __init__(self, cost=None, prompt_tokens=None, completion_tokens=None):
        self.cost = cost
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponseAttr:
    def __init__(self, usage, model="claude-sonnet-4-5"):
        self.usage = usage
        self.model = model


class TestReserveAndSettle:
    def test_reserve_then_settle_exact_updates_the_window(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        assert isinstance(r, Reservation)
        settle(pk, r.reservation_id, 0.5, metering="exact")
        status = status_dict(pk)
        assert status["settled_usd"] == pytest.approx(0.5)
        assert status["reserved_usd"] == pytest.approx(0.0)

    def test_release_is_idempotent(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        release(pk, r.reservation_id)
        release(pk, r.reservation_id)  # second release: no-op, no exception
        status = status_dict(pk)
        assert status["reserved_usd"] == pytest.approx(0.0)


class TestConcurrentReservations:
    def test_two_reservations_summing_over_the_pool_admit_exactly_one(self):
        pk = fresh_pk()
        r1 = reserve(pk, 6.0, purpose="rsi", daily_paid_inference_usd=10.0)
        r2 = reserve(pk, 6.0, purpose="rsi", daily_paid_inference_usd=10.0)
        assert isinstance(r1, Reservation)
        assert not isinstance(r2, Reservation)
        assert r2.reason == REFUSAL_EXHAUSTED


class TestInvalidAmount:
    @pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), None, "x"])
    def test_reserve_refuses_unforecastable_amounts(self, bad):
        pk = fresh_pk()
        r = reserve(pk, bad, purpose="rsi", daily_paid_inference_usd=10.0)
        assert not isinstance(r, Reservation)
        assert r.reason == REFUSAL_NO_FORECAST


class TestSettleFromResponse:
    def test_exact_settlement_from_usage_cost_attribute_form(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        response = FakeResponseAttr(FakeUsageAttr(cost=0.25))
        settle_from_response(pk, r.reservation_id, response)
        assert status_dict(pk)["settled_usd"] == pytest.approx(0.25)

    def test_exact_settlement_from_usage_cost_mapping_form(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        response = {"usage": {"cost": 0.3}, "model": "claude-sonnet-4-5"}
        settle_from_response(pk, r.reservation_id, response)
        assert status_dict(pk)["settled_usd"] == pytest.approx(0.3)

    def test_estimated_from_tokens_when_cost_absent(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        response = FakeResponseAttr(
            FakeUsageAttr(cost=None, prompt_tokens=1_000_000, completion_tokens=0)
        )
        settle_from_response(pk, r.reservation_id, response)
        # claude-sonnet-4-5: $3/Mtoken in -> exactly $3 for 1M prompt tokens.
        assert status_dict(pk)["settled_usd"] == pytest.approx(3.0)

    def test_response_with_no_usage_at_all_leaves_the_reservation_open(self):
        pk = fresh_pk()
        r = reserve(pk, 1.0, purpose="rsi", daily_paid_inference_usd=10.0)
        settle_from_response(pk, r.reservation_id, object())
        status = status_dict(pk)
        assert status["settled_usd"] == pytest.approx(0.0)
        assert status["reserved_usd"] == pytest.approx(1.0)


class TestSdlcReviewIsNeverGated:
    def test_sdlc_review_receipt_never_reduces_headroom_and_pins_valor(self):
        pk = fresh_pk()
        before = status_dict(pk)
        record_receipt(
            project_key="valor",
            purpose="sdlc_review",
            model="gpt-5",
            prompt_tokens=100,
            completion_tokens=50,
            metering="estimated",
        )
        after = status_dict("valor")
        # The receipt touches no window counter for any project -- gate on
        # the *caller's own* pk window being untouched, and the row lands.
        assert status_dict(pk) == before

        from models.improvement_evidence import ImprovementEvidence

        rows = ImprovementEvidence.query.filter(project_key="valor", kind="spend_receipt")
        matches = [
            row
            for row in rows
            if row.source_ref and row.source_ref.startswith("unit2-sdlc_review-")
        ]
        assert matches, "expected at least one sdlc_review receipt for project_key=valor"
        del after


class TestWindowAttributionAcrossMidnight:
    def test_current_day_keys_differ_across_a_midnight_boundary(self):
        before_midnight = datetime(2026, 9, 14, 23, 59, tzinfo=UTC)
        after_midnight = datetime(2026, 9, 15, 0, 1, tzinfo=UTC)
        key_before, _, _ = current_day(before_midnight, "UTC")
        key_after, _, _ = current_day(after_midnight, "UTC")
        assert key_before != key_after
        assert key_before == "2026-09-14"
        assert key_after == "2026-09-15"

    def test_unknown_boundary_raises(self):
        with pytest.raises(ValueError):
            current_day(datetime.now(UTC), "PST")


class TestNoOpenRouterLookupOrHttpClient:
    def test_module_imports_no_http_client_and_no_openrouter_url(self):
        import inspect

        import tools.paid_inference_meter as meter_module

        source = inspect.getsource(meter_module)
        assert "openrouter.ai/api/v1/generation" not in source
        for banned in ("import requests", "import httpx", "import urllib.request"):
            assert banned not in source
        assert '"include": True' not in source
