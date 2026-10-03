"""The judgement port against the real providers: one request judgement on
each leg alone, metered in the test database's ledger.

Reads the keys from the kernel's key file (`python -m core judgement-keys`;
`VALOR_PG_PASSFILE` places the key directory). Each call's charge is checked
against its own usage at the prices checked on 2026-10-02.

Live spend: under $0.001 per run (one Jev call, about $0.00002, and one
fallback call, about $0.0003); each test declares $0.01. Runs only when
`VALOR_LIVE=1`.
"""

import asyncio
import math
import os

import pytest

from core import db, ledger, tasks
from core.__main__ import port
from core.judgement_tasks import JUDGE
from core.settings import JEV_URL, OPEN_WEIGHT_URL, settings

pytestmark = [
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]

REQUEST = {
    "request": "Add a dark mode toggle to the settings page.",
    "thread": "",
    "project": "toy",
}


@pytest.mark.spend(usd=0.01)
@pytest.mark.parametrize("leg", ["jev", "open_weight"])
def test_one_live_judgement_per_leg_is_answered_and_charged_by_its_usage(dsn, leg):
    assert settings.jev_url == JEV_URL and settings.open_weight_url == OPEN_WEIGHT_URL

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="live judgement"))
        j = await port().ask_leg(leg, JUDGE, REQUEST, task_id=task, ref={"live": leg}, dsn=dsn)
        async with await db.connect(dsn) as conn:
            charged = [r["payload"] for r in await ledger.read(conn, task) if r["type"] == "gateway.charged"]
        return j, charged

    j, charged = asyncio.run(go())
    assert j.answered, j.attempts
    usage = j.attempts[0]["usage"]
    (charge,) = charged
    if leg == "jev":
        assert charge["usd_micros"] == math.ceil(usage["input_tokens"] * 0.042)
    else:
        by_tokens = math.ceil(usage["input_tokens"] * 0.14) + math.ceil(usage["output_tokens"] * 0.80)
        reported = math.ceil(float(usage["reported_usd"]) * 1_000_000)
        assert charge["usd_micros"] == max(by_tokens, reported)
        assert abs(by_tokens - reported) <= 2  # the pinned price is the host's
    assert sum(j.answers["request"]["probabilities"].values()) == pytest.approx(1.0)
