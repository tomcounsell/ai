"""Real Pi turns on GPT-6.1, through the gateway's OpenAI route (task 3a)
with the kernel's OpenAI key, on workspaces the kernel provisioned.

- A working turn is metered, and its `turn.ended` carries Pi's own reported
  cost beside the gateway's charge (Pi's is an approximation; the charge is
  the record), and the version Pi ran as.
- A compaction Pi triggers on a small context window survives, and the next
  turn resumes the session.
- A critique at the `reviewer_openai` seat runs Pi in a blind checkout and
  leaves a verdict, with `critique.decided` naming Pi's model.

Live spend: about $0.10 in all, metered by the gateway. Runs only when
`VALOR_LIVE=1` and the gateway has the OpenAI route.
"""

import asyncio
import os

import pytest

from core import db, fresh, ledger, router, runs, tasks
from core.gateway import Gateway
from core.machine import State
from core.settings import SEATS, settings
from harnesses import pi
from tests import scripted
from tests.ports import listen

pytestmark = [
    pytest.mark.spend(usd=0.30),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="live Pi needs OPENAI_API_KEY"),
    pytest.mark.skipif(
        "openai_credential" not in Gateway.__init__.__code__.co_varnames,
        reason="the gateway has no OpenAI route yet (task 3a)",
    ),
]

MODEL = SEATS["reviewer_openai"][1]


def _gateway(dsn, tmp_path) -> Gateway:
    """A gateway whose OpenAI key is the test's `OPENAI_API_KEY`, written to
    a key file (mode 600) under the test's own directory."""
    from core.gateway import OpenAIKey

    keyfile = tmp_path / "openai-key"
    keyfile.touch(mode=0o600)
    keyfile.write_text(f"OPENAI_API_KEY={os.environ['OPENAI_API_KEY']}\n")
    return Gateway(dsn, openai_credential=OpenAIKey(str(keyfile)))


def test_a_live_pi_turn_is_metered_and_records_what_ran(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        gateway = _gateway(dsn, tmp_path)
        await gateway.start(port=listen())
        try:
            build = pi.workspace_turn(
                "Reply with exactly one word: ready", cwd=b.workspace, model=MODEL,
                harness={**b.harness, "max_output_tokens": 2048},
            )  # fmt: skip
            ended = await runs.run_turn(gateway, task, build, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return ended, await ledger.read(conn, task), await tasks.status(conn, task)

    ended, rows, state = asyncio.run(go())
    assert ended["outcome"] == "done" and "ready" in ended["result"]["text"].lower()
    assert ended["metered_usd_micros"] > 0 and ended["result"]["harness_reported_usd"] > 0
    print(
        "pi reported", ended["result"]["harness_reported_usd"], "gateway charged",
        ended["metered_usd_micros"] / 1_000_000,
    )  # fmt: skip
    started = next(r["payload"] for r in rows if r["type"] == "turn.started")
    assert started["harness"] == "pi" and started["harness_version"] == pi.version(settings.pi)
    assert tasks.audit(state) == []


def test_a_live_compaction_is_survived(dsn, tmp_path, monkeypatch):
    monkeypatch.setattr(
        pi, "context_window", lambda _model: 18_000
    )  # Pi compacts above window minus its reserve

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        gateway = _gateway(dsn, tmp_path)
        await gateway.start(port=listen())
        try:
            harness = {**b.harness, "max_output_tokens": 1024}
            one = await runs.run_turn(
                gateway, task,
                pi.workspace_turn("Reply with exactly one word: one", cwd=b.workspace, model=MODEL, harness=harness),
                dsn=dsn,
            )  # fmt: skip
            two = await runs.run_turn(
                gateway, task,
                pi.workspace_turn(
                    "Reply with exactly one word: two", cwd=b.workspace, model=MODEL, harness=harness,
                    resume=one["result"]["session_id"],
                ),
                dsn=dsn,
            )  # fmt: skip
        finally:
            await gateway.close()
        return one, two

    one, two = asyncio.run(go())
    assert one["result"]["compaction"], "the first turn's context was above the window's threshold"
    assert two["outcome"] == "done" and not two["result"]["is_error"]


def _fresh_for(prompt, checkout, model, harness, harness_name="claude_code"):
    from core.__main__ import _harnesses

    return _harnesses()[harness_name].workspace_turn(
        prompt, cwd=checkout, model=model, harness={**harness, "max_output_tokens": 2048}
    )


def test_a_live_critique_at_the_openai_seat_leaves_a_verdict(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        gateway = _gateway(dsn, tmp_path)
        await gateway.start(port=listen())
        try:
            planned = await router.run(gateway, task, scripted.RUNNERS, dsn=dsn)
            assert planned["missing"] == ["critique"], planned
            runners = {
                **scripted.RUNNERS,
                State.CRITIQUE: fresh.critique_runner(_fresh_for, seat="reviewer_openai"),
            }
            out = await router.run(gateway, task, runners, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return out, await ledger.read(conn, task), b

    out, rows, _b = asyncio.run(go())
    decided = [r["payload"] for r in rows if r["type"] == "critique.decided"]
    assert decided, out
    assert decided[0]["verdict"] in ("sound", "revise") and decided[0]["model"] == MODEL
    assert decided[0]["usd_micros"] > 0
    print("critique verdict", decided[0]["verdict"], "charged", decided[0]["usd_micros"] / 1_000_000)
    started = next(r["payload"] for r in rows if r["type"] == "turn.started" and r["payload"].get("fresh"))
    assert started["harness"] == "pi"
