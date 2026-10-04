"""One real replay item, in the `bare` arm, through the emulator routine's
runner: the run is a child of the routine's objective, and the item's
spending and its stand-in's spending are in the routine's period figure.

Live spend: one item's `bare` replay, declared $10.00. Runs only when
`VALOR_LIVE=1`, and needs an item under `$VALOR_DEMO/items`.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import psycopg
import pytest

from core import routines, tasks
from core.settings import settings

pytestmark = [
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
    pytest.mark.spend(usd=10.00),
]

ROOT = Path(__file__).resolve().parent.parent


def test_a_real_item_in_the_bare_arm_is_in_the_routines_period_figure(dsn, tmp_path, monkeypatch):
    from routines.emulator import runner

    found = sorted((Path(settings.demo_dir) / "items").glob("*.json"))
    if not found:
        pytest.skip("no replay item under $VALOR_DEMO/items")
    demo = tmp_path / "demo"
    (demo / "items").mkdir(parents=True)
    shutil.copy(found[0], demo / "items" / found[0].name)
    monkeypatch.setattr(runner, "ARMS", ("bare",))
    monkeypatch.setattr(
        runner, "settings", settings.__class__(**{**settings.__dict__, "demo_dir": str(demo)})
    )
    monkeypatch.setenv("VALOR_DEMO", str(demo))

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            r = routines.load("emulator", ROOT / "routines")
            said = await routines.run(
                conn, "emulator", {"emulator": runner.run}, directory=ROOT / "routines", dsn=dsn
            )
            rep = await routines.report(conn, "emulator")
            (run_row,) = [e for e in rep["runs"] if e["run"]]
            kids = await tasks.children(conn, run_row["run"])
            return said, rep, run_row, kids, r

    said, rep, run_row, kids, _ = asyncio.run(go())
    assert "finished" in said and kids
    report = json.loads((demo / "sweeps" / f"{run_row['run']}.json").read_text())
    (item,) = report["items"].values()
    assert item["bare"]["outcome"]
    assert run_row["spent_usd_micros"] > 0
    assert rep["period_spent_usd_micros"] >= run_row["spent_usd_micros"]
