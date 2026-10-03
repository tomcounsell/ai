"""The test and docs runners against real providers, on the toy greeter.

One test runner whose breadth call goes to the real judgement port: the
suite runs at base and head in the task's check checkouts, and breadth,
uncalibrated, comes back as information. One real docs turn by `claude -p`
through the gateway: it commits in its own clone, the kernel fetches and
keeps the doc-path prefix, governance runs over the kept diff, and
`docs.decided` carries the turn's metered spend.

Live spend: the test runner about a cent, declared $0.05; the docs turn
about $0.50, declared $1.00. Runs only when `VALOR_LIVE=1`.
"""

import asyncio
import os
from pathlib import Path

import pytest

from core import checks, db, fresh, git, judgement_tasks, ledger, router
from core.__main__ import port
from core.gateway import ClaudeLogin, Gateway
from core.machine import Check
from core.settings import resolve_model
from harnesses import claude_code
from tests import scripted, test_checks

pytestmark = [
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]


async def _rows(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


@pytest.mark.spend(usd=0.05)
def test_a_live_test_runner_runs_both_suites_and_reports_breadth_as_information(dsn, tmp_path):
    assert judgement_tasks.BREADTH.calibrated is None

    async def go():
        task, _b, ws = await test_checks.to_candidate(dsn, tmp_path, writes={"greeting.txt": "hi\n"})
        runners = {**scripted.fresh_runners(ws), Check.TEST: checks.test_runner(port())}
        await test_checks.drive(dsn, task, runners)
        return await _rows(dsn, task)

    got = asyncio.run(go())
    d = test_checks.decided(got)
    assert d["verdict"] == "pass" and d["behaviors"] == [], d
    assert d["breadth"]["judgement_id"] and "information" in d["breadth"], d["breadth"]
    assert any(r["type"] == "gateway.charged" for r in got)


def fresh_for(prompt, checkout, model, harness):
    return claude_code.workspace_turn(
        prompt, cwd=checkout, model=model, harness={**harness, "max_output_tokens": 4096}
    )


@pytest.mark.spend(usd=1.00)
def test_a_real_docs_turn_is_kept_by_the_kernel_and_metered(dsn, tmp_path):
    light = resolve_model("light")

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        ws = Path(b.workspace)
        scripted.steer(ws, fresh_acts=["sound"])
        out = await test_checks.drive(dsn, task, scripted.fresh_runners(ws))
        assert out["missing"] == ["test", "review", "docs"], out
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        gateway = Gateway(dsn, credential=ClaudeLogin())
        await gateway.start()
        try:
            runners = {
                **scripted.fresh_runners(ws),
                Check.DOCS: fresh.docs_runner(fresh_for, port(), model=light),
            }
            out = await router.run(gateway, task, runners, dsn=dsn)
        finally:
            await gateway.close()
        return out, b, await _rows(dsn, task)

    out, b, got = asyncio.run(go())
    d = next((r["payload"] for r in got if r["type"] == "docs.decided"), None)
    assert d, out
    assert d["leg"] == "session" and d["verdict"] in ("updated", "no_change", "changes")
    assert d["usd_micros"] > 0 and d["model"] == light
    kept = next(r["payload"] for r in got if r["type"] == "docs.kept")
    assert d["head"] == kept["kept"] and d["turn_id"] == kept["turn_id"]
    assert git.trusted(b.mirror, "rev-parse", f"refs/valor/docs/{kept['turn_id']}") == d["head"]
    assert git.trusted(b.workspace, "rev-parse", "HEAD") == d["candidate"]["sha"]  # the builder never sees it
