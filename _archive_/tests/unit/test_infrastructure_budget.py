"""Unit-3 meter and teardown-adjacent budget tests (lane 7, #3274).

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py): ``REDIS_URL``
points at a claimed per-worker test DB process-wide, so the window counter —
a raw Redis string key, deliberately not a Popoto model — never touches
production. Seeded records use test-scoped project keys.
"""

from __future__ import annotations

import ast
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tools import infrastructure_budget as budget
from tools.infrastructure_budget import (
    REFUSAL_EXHAUSTED,
    REFUSAL_NO_FORECAST,
    WEEK_SECONDS,
    ResourceDecl,
    admit,
    current_window,
    forecast_usd,
    release,
    settle,
    status_dict,
    stop_now,
    window_reserved_usd,
)

PK = "test-3274-budget"

# Fixed timestamps pin window keys so tests never share a counter.
MONDAY = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)  # a Monday noon UTC
TUESDAY = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
WEDNESDAY = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
THURSDAY = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def fake_settings(cap=50.0, week_start="monday", day_boundary="UTC"):
    """The three unit-3 settings and nothing else.

    Deliberately a SimpleNamespace without ``daily_paid_inference_usd``: any
    code path reading the paid-inference pool raises AttributeError here.
    """
    return SimpleNamespace(
        improvement=SimpleNamespace(
            weekly_infrastructure_usd=cap,
            budget_week_start=week_start,
            budget_day_boundary=day_boundary,
        )
    )


def window_of(moment, week_start="monday"):
    key, start, end = current_window(moment, week_start)
    return key, start, end


def test_window_counter_never_touches_production_db():
    from popoto.redis_db import POPOTO_REDIS_DB

    db = POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db")
    assert db != 0, "window counter would reserve against production Redis"


class TestWindowBoundaries:
    def test_boundary_monday_midnight_utc(self):
        just_before = datetime(2026, 9, 7, 0, 0, tzinfo=UTC) - timedelta(seconds=1)
        key_before, _, _ = current_window(just_before, "monday")
        key_at, start_at, _ = current_window(datetime(2026, 9, 7, 0, 0, tzinfo=UTC), "monday")
        assert key_before != key_at
        assert start_at == datetime(2026, 9, 7, 0, 0, tzinfo=UTC)

    def test_boundary_sunday_start_differs(self):
        key_mon, _, _ = current_window(MONDAY, "monday")
        key_sun, start_sun, _ = current_window(MONDAY, "sunday")
        assert key_mon != key_sun
        assert start_sun == datetime(2026, 9, 6, 0, 0, tzinfo=UTC)

    def test_boundary_unknown_week_start_rejected(self):
        with pytest.raises(ValueError):
            current_window(MONDAY, "friday")

    def test_boundary_disclosed_on_every_decision(self):
        decision = admit(
            ResourceDecl(name="r-boundary", weekly_rate_usd=1.0),
            project_key=PK,
            settings=fake_settings(),
            now=MONDAY,
        )
        assert decision.budget_week_start == "monday"
        assert decision.budget_day_boundary == "UTC"
        assert decision.window_key
        assert decision.window_start
        assert decision.window_end

        from models.improvement_infrastructure_ledger import InfrastructureReservation

        rows = list(
            InfrastructureReservation.query.filter(
                project_key=PK, reservation_id=decision.reservation_id
            )
        )
        assert rows and rows[0].budget_week_start == "monday"
        assert rows[0].budget_day_boundary == "UTC"


class TestForecastRefusal:
    @pytest.mark.parametrize(
        "rate",
        [None, "", "   ", "abc", "12usd", True, False, -5.0, "-3", float("nan"), float("inf")],
        ids=[
            "none",
            "empty",
            "blank",
            "text",
            "unit",
            "bool-t",
            "bool-f",
            "negative",
            "neg-str",
            "nan",
            "inf",
        ],
    )
    def test_forecast_refusal_unforecastable_rate(self, rate):
        decision = admit(
            ResourceDecl(name=f"r-refuse-{rate!r}", weekly_rate_usd=rate),
            project_key=PK,
            settings=fake_settings(),
            now=TUESDAY,
        )
        assert decision.admitted is False
        assert decision.reason == REFUSAL_NO_FORECAST
        assert decision.reservation_id is None or decision.forecast_usd == 0.0

    def test_forecast_refusal_records_refused_row(self):
        decision = admit(
            ResourceDecl(name="r-refuse-row", weekly_rate_usd=None),
            project_key=PK,
            settings=fake_settings(),
            now=TUESDAY,
        )
        from models.improvement_infrastructure_ledger import InfrastructureReservation

        assert decision.reservation_id is not None
        rows = list(
            InfrastructureReservation.query.filter(
                project_key=PK, reservation_id=decision.reservation_id
            )
        )
        assert len(rows) == 1 and rows[0].state == "refused"
        assert rows[0].reason == REFUSAL_NO_FORECAST

    def test_forecast_refusal_zero_rate_is_admitted(self):
        decision = admit(
            ResourceDecl(name="r-free", weekly_rate_usd=0.0),
            project_key=PK,
            settings=fake_settings(),
            now=TUESDAY,
        )
        assert decision.admitted is True
        assert decision.forecast_usd == 0.0

    def test_forecast_refusal_bad_duty_cycle(self):
        for duty in (0.0, -1.0, 1.5):
            decision = admit(
                ResourceDecl(name="r-duty", weekly_rate_usd=10.0, duty_cycle=duty),
                project_key=PK,
                settings=fake_settings(),
                now=TUESDAY,
            )
            assert decision.admitted is False
            assert decision.reason == REFUSAL_NO_FORECAST

    def test_forecast_refusal_exhausted_week_names_itself(self):
        settings = fake_settings(cap=5.0)
        first = admit(
            ResourceDecl(name="r-big", weekly_rate_usd=1000.0),
            project_key=PK,
            settings=settings,
            now=WEDNESDAY,
        )
        assert first.admitted is False
        assert first.reason == REFUSAL_EXHAUSTED
        assert first.forecast_usd > 0

    def test_forecast_refusal_empty_ledger_has_full_headroom(self):
        snap = status_dict(
            project_key=PK,
            settings=fake_settings(cap=50.0),
            now=datetime(2025, 3, 4, 12, 0, tzinfo=UTC),
        )
        assert snap["reserved_usd"] == 0.0
        assert snap["headroom_usd"] == 50.0
        assert snap["budget_week_start"] == "monday"
        assert snap["budget_day_boundary"] == "UTC"

    def test_forecast_refusal_blank_project_key_rejected_before_write(self):
        from models.improvement_infrastructure_ledger import InfrastructureReservation

        key, _, _ = current_window(THURSDAY, "monday")
        before = window_reserved_usd(key)
        for bad_pk in ("", "   "):
            with pytest.raises(ValueError):
                admit(
                    ResourceDecl(name="r-badpk", weekly_rate_usd=1.0),
                    project_key=bad_pk,
                    settings=fake_settings(),
                    now=THURSDAY,
                )
            with pytest.raises(ValueError):
                InfrastructureReservation.record_decision(
                    bad_pk,
                    window_key=key,
                    resource="r-badpk",
                    amount_usd=0.0,
                    forecast_usd=0.0,
                    state="refused",
                    reservation_id="x",
                )
        assert window_reserved_usd(key) == before


class TestCredits:
    def test_credit_expiring_mid_window_converts_to_charge(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)  # Monday noon
        _, start, end = window_of(now)
        expiry = now + timedelta(days=2)
        resource = ResourceDecl(
            name="r-credit",
            weekly_rate_usd=70.0,
            credit_expires_at=expiry,
            credit_paid_rate_usd=70.0,
        )
        got = forecast_usd(resource, now, start, end)
        horizon = (end - now).total_seconds() + WEEK_SECONDS
        paid_seconds = horizon - 2 * 86400
        assert got == pytest.approx(70.0 * paid_seconds / WEEK_SECONDS)

    def test_credit_with_no_expiry_covers_only_current_window(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
        _, start, end = window_of(now)
        resource = ResourceDecl(
            name="r-credit-noexpiry",
            weekly_rate_usd=70.0,
            credit_paid_rate_usd=70.0,
        )
        got = forecast_usd(resource, now, start, end)
        # Free for the rest of this window, paid for exactly the next one.
        assert got == pytest.approx(70.0)

    def test_credit_without_paid_rate_is_unforecastable(self):
        now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
        _, start, end = window_of(now)
        resource = ResourceDecl(
            name="r-credit-norate",
            weekly_rate_usd=70.0,
            credit_expires_at=now + timedelta(days=2),
        )
        assert forecast_usd(resource, now, start, end) is None


class TestMissingMetering:
    def _admit(self, name, now):
        decision = admit(
            ResourceDecl(name=name, weekly_rate_usd=7.0),
            project_key=PK,
            settings=fake_settings(),
            now=now,
        )
        assert decision.admitted is True
        return decision

    def test_missing_metering_raising_reader_settles_at_forecast(self, caplog):
        decision = self._admit("r-meter-raise", datetime(2026, 9, 14, 12, 0, tzinfo=UTC))

        def boom():
            raise RuntimeError("billing api down")

        with caplog.at_level("WARNING"):
            settled = settle(decision.reservation_id, boom, project_key=PK)
        assert settled == pytest.approx(decision.forecast_usd)
        assert any("settling at forecast" in r.message for r in caplog.records)

    def test_missing_metering_none_reader_settles_at_forecast(self):
        decision = self._admit("r-meter-none", datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
        settled = settle(decision.reservation_id, lambda: None, project_key=PK)
        assert settled == pytest.approx(decision.forecast_usd)

    def test_missing_metering_absent_reader_settles_at_forecast(self):
        decision = self._admit("r-meter-absent", datetime(2026, 9, 16, 12, 0, tzinfo=UTC))
        settled = settle(decision.reservation_id, None, project_key=PK)
        assert settled == pytest.approx(decision.forecast_usd)

    def test_missing_metering_non_numeric_reader_settles_at_forecast(self):
        decision = self._admit("r-meter-nonnumeric", datetime(2026, 9, 17, 12, 0, tzinfo=UTC))
        settled = settle(decision.reservation_id, lambda: "lots", project_key=PK)
        assert settled == pytest.approx(decision.forecast_usd)

    def test_missing_metering_billing_api_settles_actual(self):
        from models.improvement_evidence import ImprovementEvidence

        decision = self._admit("r-meter-api", datetime(2026, 9, 18, 12, 0, tzinfo=UTC))
        settled = settle(decision.reservation_id, lambda: 12.5, project_key=PK)
        assert settled == pytest.approx(12.5)
        receipts = [
            r
            for r in ImprovementEvidence.recent(PK, limit=100)
            if r.kind == "spend_receipt"
            and r.source_ref == f"unit3-settlement-{decision.reservation_id}"
        ]
        assert len(receipts) == 1

    def test_missing_metering_settle_is_idempotent(self):
        decision = self._admit("r-meter-idem", datetime(2026, 9, 19, 12, 0, tzinfo=UTC))
        first = settle(decision.reservation_id, lambda: 3.0, project_key=PK)
        second = settle(decision.reservation_id, lambda: 99.0, project_key=PK)
        assert first == second == pytest.approx(3.0)


class TestConcurrentAdmission:
    def test_concurrent_admission_cannot_double_spend(self):
        now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
        key, _, _ = window_of(now)
        assert window_reserved_usd(key) == 0.0
        expected = forecast_usd(
            ResourceDecl(name="r-race", weekly_rate_usd=70.0), now, *window_of(now)[1:]
        )
        settings = fake_settings(cap=expected * 1.5)
        results = []

        def run(i):
            results.append(
                admit(
                    ResourceDecl(name=f"r-race-{i}", weekly_rate_usd=70.0),
                    project_key=PK,
                    settings=settings,
                    now=now,
                )
            )

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        admitted = [r for r in results if r.admitted]
        assert len(admitted) == 1
        assert window_reserved_usd(key) == pytest.approx(expected)


class TestReservationRelease:
    def test_reservation_release_returns_headroom_once(self):
        now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
        key, _, _ = window_of(now)
        settings = fake_settings(cap=1000.0)
        decision = admit(
            ResourceDecl(name="r-release", weekly_rate_usd=70.0),
            project_key=PK,
            settings=settings,
            now=now,
        )
        assert decision.admitted is True
        reserved = window_reserved_usd(key)
        assert reserved == pytest.approx(decision.forecast_usd)
        assert release(decision.reservation_id, project_key=PK) is True
        assert window_reserved_usd(key) == pytest.approx(0.0)
        # Replay is a no-op: headroom does not rise.
        assert release(decision.reservation_id, project_key=PK) is True
        assert window_reserved_usd(key) == pytest.approx(0.0)

    def test_reservation_release_unknown_id(self):
        assert release("no-such-reservation", project_key=PK) is False

    def test_reservation_release_refused_row_is_noop(self):
        decision = admit(
            ResourceDecl(name="r-rel-refused", weekly_rate_usd=None),
            project_key=PK,
            settings=fake_settings(),
            now=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
        )
        assert release(decision.reservation_id, project_key=PK) is False

    def test_window_key_expiry_outlives_window(self):
        from popoto.redis_db import POPOTO_REDIS_DB

        now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
        key, _, _ = window_of(now)
        decision = admit(
            ResourceDecl(name="r-expiry", weekly_rate_usd=1.0),
            project_key=PK,
            settings=fake_settings(),
            now=now,
        )
        assert decision.admitted is True
        ttl = POPOTO_REDIS_DB.ttl(budget.WINDOW_KEY_PREFIX + key)
        assert ttl > WEEK_SECONDS, f"window key expires in {ttl}s, inside its own window"


class TestForecastHardStop:
    def test_forecast_hard_stop_fires_on_forecast_alone(self):
        # Billing reports zero all window; the forecast still trips the stop.
        assert stop_now(0.0, 15.0, 15.0) is True
        assert stop_now(0.0, 14.99, 15.0) is False

    def test_forecast_hard_stop_fires_on_settled(self):
        assert stop_now(15.0, 0.0, 15.0) is True
        assert stop_now(3.0, 4.0, 15.0) is False


class TestNoAcquisitionBypassesAdmission:
    LANE_MODULES = (
        "tools/infrastructure_budget.py",
        "tools/improvement_operating_report.py",
    )

    def _trees(self):
        import pathlib

        root = pathlib.Path(budget.__file__).resolve().parent.parent
        return {name: ast.parse((root / name).read_text()) for name in self.LANE_MODULES}

    def test_no_acquisition_bypasses_admission_provider_surface(self):
        for name, tree in self._trees().items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in {
                            "subprocess",
                            "boto3",
                            "botocore",
                            "cloudflare",
                        }, f"{name} imports provider surface {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    module = (node.module or "").split(".")[0]
                    assert module not in {"subprocess", "boto3", "botocore", "cloudflare"}, (
                        f"{name} imports provider surface {node.module}"
                    )
                elif isinstance(node, ast.Call):
                    func = node.func
                    dotted = ""
                    while isinstance(func, ast.Attribute):
                        dotted = f".{func.attr}" + dotted
                        func = func.value
                    if isinstance(func, ast.Name):
                        dotted = func.id + dotted
                    assert not dotted.startswith("subprocess."), f"{name} shells out via {dotted}"

    def test_no_acquisition_bypasses_admission_single_reserver(self):
        # admit() is the sole reserver: the Lua reserve script appears once,
        # in infrastructure_budget, and Lua release beside it.
        import pathlib

        root = pathlib.Path(budget.__file__).resolve().parent.parent
        for name in self.LANE_MODULES:
            text = (root / name).read_text()
            if name.endswith("infrastructure_budget.py"):
                assert text.count("INCRBYFLOAT") == 1, "reserve script must appear exactly once"
            else:
                assert "INCRBYFLOAT" not in text, f"{name} reserves outside admit()"
                assert "redis.call" not in text, f"{name} runs Lua outside the meter"
                assert ".eval(" not in text, f"{name} evals outside the meter"
