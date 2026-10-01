"""One real fresh session: a critique of a toy plan by `claude -p`, through
the kernel's critique runner, on a workspace the kernel provisioned.

What it shows, live: the session runs under its own sandbox profile (the
work directory, `/private/tmp`, `/private/var/folders`, and the user's
Claude Code state denied), with its own `TMPDIR` and Claude Code config
directory, carrying only a placeholder credential that the gateway replaces
with the kernel's; it reads its inputs, writes `.valor/verdict.json`, and
the kernel records `critique.decided` with the turn's metered spend. Its
transcript lands in its own config directory, not in the user's.

Runs a light model to keep the spend small (the critique seat is Opus).
Live spend: at most $0.40 (about $0.10 typical). Runs only when `VALOR_LIVE=1`.
"""

import asyncio
import os
from pathlib import Path

import pytest

from core import db, fresh, ledger, router
from core.gateway import ClaudeLogin, Gateway
from core.machine import State
from core.settings import resolve_model
from harnesses import claude_code
from tests import scripted

pytestmark = [
    pytest.mark.spend(usd=0.40),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]


def fresh_for(prompt, checkout, model, harness):
    return claude_code.workspace_turn(
        prompt, cwd=checkout, model=model, harness={**harness, "max_output_tokens": 2048}
    )


def test_a_real_fresh_critique_session_leaves_a_verdict_through_the_gateway(dsn, tmp_path):
    light = resolve_model("light")

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path, budget_usd_micros=400_000)
        ws = Path(b.workspace)
        gateway = Gateway(dsn, credential=ClaudeLogin())
        await gateway.start()
        try:
            planned = await router.run(gateway, task, scripted.RUNNERS, dsn=dsn)
            assert planned["missing"] == ["critique"], planned
            runners = {**scripted.RUNNERS, State.CRITIQUE: fresh.critique_runner(fresh_for, model=light)}
            out = await router.run(gateway, task, runners, dsn=dsn)
        finally:
            await gateway.close()
        async with await db.connect(dsn) as conn:
            return out, await ledger.read(conn, task), b, ws

    out, rows, b, _ws = asyncio.run(go())
    decided = [r["payload"] for r in rows if r["type"] == "critique.decided"]
    assert decided, out
    assert decided[0]["leg"] == "session" and decided[0]["verdict"] in ("sound", "revise")
    assert decided[0]["usd_micros"] > 0 and decided[0]["model"] == light
    started = next(r["payload"] for r in rows if r["type"] == "turn.started" and r["payload"].get("fresh"))
    assert "/usr/bin/sandbox-exec" in started["argv"][0]
    check_dir = Path(b.mirror).parent / "checks"
    critique_dir = next(check_dir.glob("critique-*"))
    sessions = list((critique_dir / "claude" / "projects").rglob("*.jsonl"))
    assert sessions, "the session's transcript is in its own config directory"
    mangled = str(critique_dir / "repo").replace("/", "-").replace(".", "-")
    assert not (Path.home() / ".claude" / "projects" / mangled).exists()
