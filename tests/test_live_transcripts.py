"""A real workspace turn's transcript copied by the kernel: a turn that
starts a subagent, then one resumed turn. The subagent's file is copied
with its own digest, and the same `<session_id>.jsonl` grew and was
stored as a delta.

Live spend: about $0.30 per run, metered by the gateway (Haiku). Runs only
when `VALOR_LIVE=1`.
"""

import asyncio
import hashlib
import os

import pytest

from core import db, runs, transcripts
from core.gateway import ClaudeLogin, Gateway
from harnesses import claude_code
from tests import scripted
from tests.ports import listen

pytestmark = [
    pytest.mark.spend(usd=0.60),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]

FIRST = (
    "Use the Task tool once to start a subagent whose only job is to reply with the word 'ready'. "
    "Then reply with the subagent's answer and end the turn."
)
SECOND = "Reply with exactly one word: done"


def test_a_subagent_s_file_is_copied_and_a_resumed_session_is_stored_as_a_delta(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        harness = {**b.harness, "max_output_tokens": 2048}
        gateway = Gateway(dsn, credential=ClaudeLogin())
        await gateway.start(port=listen())
        try:
            first = await runs.run_turn(
                gateway, task, claude_code.workspace_turn(FIRST, cwd=b.workspace, harness=harness), dsn=dsn
            )
            sid = first["result"]["session_id"]
            second = await runs.run_turn(
                gateway,
                task,
                claude_code.workspace_turn(SECOND, cwd=b.workspace, harness=harness, resume=sid),
                dsn=dsn,
            )
            async with await db.connect(dsn) as conn:
                stored = {}
                for ended in (first, second):
                    for f in ended["transcript"]["files"]:
                        stored[(ended["turn_id"], f["name"])] = await transcripts.joined(
                            conn, ended["turn_id"], f["name"]
                        )
        finally:
            await gateway.close()
        return first, second, sid, stored

    first, second, sid, stored = asyncio.run(go())
    assert first["outcome"] == "done" and second["outcome"] == "done", (first, second)
    assert second["result"]["session_id"] == sid
    session = f"{sid}.jsonl"
    one = {f["name"]: f for f in first["transcript"]["files"]}
    two = {f["name"]: f for f in second["transcript"]["files"]}
    agents = [n for n in one if n.startswith(f"{sid}/subagents/agent-")]
    assert agents, one
    for name in agents:
        assert hashlib.sha256(stored[(first["turn_id"], name)]).hexdigest() == one[name]["sha256"]
    assert two[session]["offset"] == one[session]["bytes"] and two[session]["prefix_changed"] is False
    whole = stored[(first["turn_id"], session)] + stored[(second["turn_id"], session)]
    assert hashlib.sha256(whole).hexdigest() == two[session]["sha256"]
