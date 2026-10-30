"""The `verify` routine's runner, from a provisioned run to its suite."""

import asyncio
import subprocess
from datetime import UTC, datetime

from core import checks, db, routines, tasks
from routines.verify import runner


def test_a_run_digests_its_environment_before_the_suite(dsn, tmp_path, monkeypatch):
    mirror = tmp_path / "ws" / "kernel.git"
    subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True)
    sha = subprocess.run(
        ["git", "-C", str(mirror), "hash-object", "-t", "commit", "-w", "--stdin"],
        input="tree 4b825dc642cb6eb9a060e54bf8d69288fbee4904\nauthor a <a> 0 +0000\ncommitter a <a> 0 +0000\n\nx\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    digests = []

    async def suite(cc, lay, b, at, role, stop, *, digest):
        digests.append(digest)
        return {"cause": "kernel", "why": "stubbed suite"}

    monkeypatch.setattr(checks, "suite", suite)

    async def go():
        async with await db.connect(dsn) as conn:
            run = await tasks.start(
                conn,
                tasks.Brief(instruction="verify", mirror=str(mirror), base_sha=sha, harness={"env": {}}),
            )
            ctx = routines.Context(
                conn=conn, routine=routines.load("verify"), objective=run, now=datetime.now(UTC),
                last=None, dsn=dsn,
            )  # fmt: skip
            return await runner._run(ctx, run)

    ran = asyncio.run(go())
    assert (ran.outcome, ran.summary) == ("failed", "stubbed suite") and len(digests) == 1
