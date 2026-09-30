"""Budget and Ceilings. Plan 02 task 1; seams §1.3."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from schemas.budget import DATA_CLASS_RANK, ZERO, Budget, Ceilings
from schemas.space import EFFECT_RANK

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def test_sub_raises_below_zero():
    with pytest.raises(ValueError):
        Budget(usd_micros=1) - Budget(usd_micros=2)
    assert Budget(usd_micros=5) - Budget(usd_micros=2) == Budget(usd_micros=3)
    assert Budget(usd_micros=5) - Budget(usd_micros=5) == ZERO


def test_zero_is_the_additive_identity():
    b = Budget(usd_micros=123)
    assert b + ZERO == b
    assert ZERO + b == b
    assert b - ZERO == b
    assert ZERO.usd_micros == 0


def test_negative_refused_at_construction():
    with pytest.raises(ValidationError):
        Budget(usd_micros=-1)


def test_fits_within():
    assert Budget(usd_micros=3).fits_within(Budget(usd_micros=3))
    assert Budget(usd_micros=2).fits_within(Budget(usd_micros=3))
    assert not Budget(usd_micros=4).fits_within(Budget(usd_micros=3))


def test_extra_field_refused():
    with pytest.raises(ValidationError):
        Budget(usd_micros=1, tokens=2)


def _ceilings(effect="propose", data="PROJECT", offset_s=0) -> Ceilings:
    return Ceilings(
        max_effect_class=effect,
        deadline=NOW + timedelta(seconds=offset_s),
        max_data_class=data,
    )


def test_ceilings_order_data_class_project_below_operator():
    assert DATA_CLASS_RANK["PROJECT"] < DATA_CLASS_RANK["OPERATOR"]
    assert _ceilings(data="PROJECT").fits_within(_ceilings(data="OPERATOR"))
    assert not _ceilings(data="OPERATOR").fits_within(_ceilings(data="PROJECT"))
    assert _ceilings(data="OPERATOR").fits_within(_ceilings(data="OPERATOR"))


def test_ceilings_order_effect_class_by_effect_rank():
    assert EFFECT_RANK["read"] < EFFECT_RANK["propose"] < EFFECT_RANK["act"]
    assert _ceilings(effect="read").fits_within(_ceilings(effect="propose"))
    assert _ceilings(effect="propose").fits_within(_ceilings(effect="act"))
    assert not _ceilings(effect="act").fits_within(_ceilings(effect="propose"))
    assert not _ceilings(effect="propose").fits_within(_ceilings(effect="read"))
    # ranks, never strings: "act" sorts before "propose" alphabetically
    assert not _ceilings(effect="act").fits_within(_ceilings(effect="read"))


def test_ceilings_deadline_not_later():
    assert _ceilings(offset_s=-1).fits_within(_ceilings())
    assert _ceilings().fits_within(_ceilings())
    assert not _ceilings(offset_s=1).fits_within(_ceilings())
