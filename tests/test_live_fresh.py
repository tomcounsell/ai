"""Real fresh sessions on a workspace the kernel provisioned: a critique of
a toy plan by `claude -p` through the kernel's critique runner, and two
blind Opus reviews through the review runner.

What it shows, live: the session runs under its own sandbox profile (the
work directory, `/private/tmp`, `/private/var/folders`, and the user's
Claude Code state denied), with its own `TMPDIR` and Claude Code config
directory, carrying only a placeholder credential that the gateway replaces
with the kernel's; it reads its inputs and gives its verdict (critique as
`.valor/verdict.json`, review as its final message), and the kernel records the verdict with the turn's metered spend. Its
transcript lands in its own config directory, not in the user's.

The critique runs a light model to keep the spend small (the critique seat
is Opus). The reviews run the reviewer seat's model, with governance on the
real judgement port: one toy candidate, and one whose diff adds a validator,
which comes back with an instance on that hunk and, after Tom's grant,
reruns the review without a second head run.

Live spend: the critique at most $0.40 (about $0.10 typical); the reviews
about $1 each, declared $1.50 and $3.00 (the second may review twice). Runs
only when `VALOR_LIVE=1`.
"""

import asyncio
import os
from pathlib import Path

import pytest

from core import db, fresh, guards, ledger, machine, router
from core.__main__ import port
from core.gateway import ClaudeLogin, Gateway
from core.machine import Check, State
from core.settings import resolve_model
from harnesses import claude_code
from tests import scripted, test_checks

pytestmark = [
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]


def fresh_for(prompt, checkout, model, harness, harness_name="claude_code"):
    return claude_code.workspace_turn(
        prompt, cwd=checkout, model=model, harness={**harness, "max_output_tokens": 2048}
    )


@pytest.mark.spend(usd=0.40)
def test_a_real_fresh_critique_session_leaves_a_verdict_through_the_gateway(dsn, tmp_path):
    light = resolve_model("light")

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        ws = Path(b.workspace)
        gateway = Gateway(dsn, credential=ClaudeLogin())
        await gateway.start()
        try:
            planned = await scripted.route(gateway, task, scripted.RUNNERS, dsn=dsn)
            assert planned["missing"] == ["critique"], planned
            runners = {**scripted.RUNNERS, State.CRITIQUE: fresh.critique_runner(fresh_for, model=light)}
            out = await scripted.route(gateway, task, runners, dsn=dsn)
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


async def _review(dsn, tmp_path, writes):
    """A toy candidate adding `writes`, its test recorded, reviewed by a real
    blind Opus session through the router, with governance on the real
    judgement port; then, after Tom's grant of every instance and the docs
    verdict, driven again."""
    task, _b, ws = await test_checks.to_candidate(dsn, tmp_path, writes=writes)
    await scripted.check(dsn, task, "test", "pass")
    gateway = Gateway(dsn, credential=ClaudeLogin())
    await gateway.start()
    try:
        runners = {**scripted.fresh_runners(ws), Check.REVIEW: fresh.review_runner(fresh_for, port())}
        first = await router.run(gateway, task, runners, dsn=dsn)
        async with await db.connect(dsn) as conn:
            before = await ledger.read(conn, task)
        f = machine.fold(before)
        if f.join is not None and f.join.outcome == "governance_refused":
            if Check.DOCS not in f.checks:
                await scripted.check(dsn, task, "docs", "no_change")
            async with await db.connect(dsn) as conn:
                for i in machine.fold(await ledger.read(conn, task)).ungranted():
                    await guards.grant(conn, task, i.id, note="yes", incident="live test", mission_item="1")
            await router.run(gateway, task, runners, dsn=dsn)
    finally:
        await gateway.close()
    async with await db.connect(dsn) as conn:
        return first, before, await ledger.read(conn, task)


def _decided(rows) -> list[dict]:
    return [r["payload"] for r in rows if r["type"] == "review.decided"]


@pytest.mark.spend(usd=1.50)
def test_a_real_blind_review_of_a_toy_candidate(dsn, tmp_path):
    first, before, _after = asyncio.run(_review(dsn, tmp_path, {"greeting.txt": "Morning, Tom.\n"}))
    assert _decided(before), first
    (d,) = _decided(before)
    assert d["leg"] == "session" and d["model"] == resolve_model("reviewer") and d["usd_micros"] > 0
    assert d["reviewer_verdict"] in ("pass", "changes") and d["verdict"] == d["reviewer_verdict"]
    assert d["verify"] and not d["governance"]["instances"]


@pytest.mark.spend(usd=3.00)
def test_a_real_review_of_an_added_validator_holds_it_for_a_grant(dsn, tmp_path):
    gate = (
        "def validate_request(request):\n"
        '    """Refuse any request that has no ticket number."""\n'
        "    if not request.get('ticket'):\n"
        "        raise PermissionError('a ticket number is required')\n"
    )
    first, before, after = asyncio.run(_review(dsn, tmp_path, {"hooks/validate.py": gate}))
    assert _decided(before), first
    d = _decided(before)[0]
    assert [i["path"] for i in d["governance"]["instances"]] == ["hooks/validate.py"], d
    if d["reviewer_verdict"] == "changes":
        assert d["verdict"] == "changes" and any(x["kind"] == "governance" for x in d["findings"])
        return
    assert d["verdict"] == "governance_refused"
    later = _decided(after)
    assert later[1]["candidate"] == d["candidate"]
    assert [x["verdict"] for x in later[:2]] == ["governance_refused", later[1]["reviewer_verdict"]]
    assert later[1]["verify"] == d["verify"]  # no second head run
    assert len([r for r in after if r["type"] == fresh.VERIFY]) == 1
