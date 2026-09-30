"""`should_verify` and the `verification.sampled` event. Plan 11 task 2;
seams §3.6, §4; architecture §5."""

from typing import get_args

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from kernel import verify
from kernel.events import read_for
from schemas.space import EffectClass
from tests.conftest import dsn, requires_postgres

CLASSES = get_args(EffectClass)


def test_m0_verifies_every_class():
    for effect_class in CLASSES:
        for leaves_space in (False, True):
            assert verify.should_verify(effect_class, leaves_space=leaves_space) == (
                True,
                1.0,
            )


def test_act_and_leaving_the_space_are_always_verified(monkeypatch):
    monkeypatch.setattr(verify, "SAMPLE_PROBABILITY", {c: 0.0 for c in CLASSES})
    monkeypatch.setattr(verify, "_draw", lambda: 0.999)
    assert verify.should_verify("act", leaves_space=False) == (True, 1.0)
    assert verify.should_verify("read", leaves_space=True) == (True, 1.0)
    assert verify.should_verify("propose", leaves_space=False) == (False, 0.0)


@requires_postgres
@settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    effect_class=st.sampled_from(CLASSES),
    table=st.fixed_dictionaries(
        {c: st.floats(min_value=0.0, max_value=1.0) for c in CLASSES}
    ),
    draw=st.floats(min_value=0.0, max_value=1.0, exclude_max=True),
    leaves_space=st.booleans(),
)
async def test_sampled_event_carries_probability_and_selection(
    monkeypatch, effect_class, table, draw, leaves_space
):
    monkeypatch.setattr(verify, "SAMPLE_PROBABILITY", table)
    monkeypatch.setattr(verify, "_draw", lambda: draw)
    space = "space-sampling"
    objective_id = f"obj-{abs(hash((effect_class, draw, leaves_space)))}"
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        selected, probability = await verify.record_sampling(
            conn,
            space=space,
            objective_id=objective_id,
            effect_class=effect_class,
            leaves_space=leaves_space,
        )
        await conn.commit()
        events = await read_for(
            conn, space_id=space, key="objective_id", value=objective_id
        )
    expected_probability = (
        1.0 if leaves_space or effect_class == "act" else table[effect_class]
    )
    assert probability == expected_probability
    assert selected == (draw < probability)
    sampled = [e for e in events if e.type == "verification.sampled"]
    assert sampled, "no verification.sampled event"
    payload = sampled[-1].payload
    assert payload["probability"] == probability
    assert payload["selected"] is selected
    assert payload["leaves_space"] is leaves_space
    assert payload["effect_class"] == effect_class
