"""Compaction measured once on a long session per harness, live.

A turn is told to read a large generated file in pieces until the harness
compacts. The test reads, from the ledger, the input tokens of each model
call of the turn (as the gateway metered them): the size climbs, then falls
when the harness compacts. It prints the size before, the size after, the
charge of the whole turn, and what the harness reported.

Live spend, metered by the gateway; runs only with `VALOR_LIVE=1`, and Pi
needs `OPENAI_API_KEY` in the environment.
"""

import asyncio
import itertools
import os
import secrets

import pytest

from core import db, ledger, runs
from core.gateway import ClaudeLogin, Gateway, OpenAIKey
from core.settings import SEATS
from harnesses import claude_code, pi
from tests import scripted

pytestmark = [
    pytest.mark.spend(usd=10.0),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]

PROMPT = (
    "The file big.txt in the working directory holds random hex text, {pieces} pieces of {piece} bytes. "
    "Read every piece in order, one shell command per piece, with `dd if=big.txt bs={piece} skip=N count=1 "
    "2>/dev/null` for N from 0 upward, and do not skip, summarize, or stop early. Reply with the first 8 "
    "characters of the last piece once you have read them all. This reading is the whole task and the "
    "measurement it serves needs every piece: keep going through all {pieces} however long it takes, "
    "and do not stop to report progress."
)


def _input_tokens(charged: dict) -> int:
    u = charged.get("usage") or {}
    if str(charged.get("model", "")).startswith("claude"):
        return (
            u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0)
        )
    return u.get("input_tokens", 0)


def _write_big(path: str, pieces: int, piece: int) -> None:
    with open(path, "w") as f:
        f.writelines(secrets.token_hex(piece // 2) for _ in range(pieces))


async def _measure(dsn, tmp_path, build_for, gateway, window: int, model: str, piece: int):
    task, b = await scripted.provisioned(dsn, tmp_path)
    # Enough text to carry the context past the window, whatever its tokenizer: one token per two bytes at worst.
    pieces = int(window * 2.4 // piece) + 2
    _write_big(f"{b.workspace}/big.txt", pieces, piece)
    await gateway.start()
    try:
        ended = await runs.run_turn(
            gateway, task, build_for(b, PROMPT.format(pieces=pieces, piece=piece)), dsn=dsn
        )
    finally:
        await gateway.close()
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task)
    sizes = [
        n
        for r in rows
        if r["type"] == "gateway.charged" and r["payload"].get("model") == model
        if (n := _input_tokens(r["payload"])) > 1000  # a side call (a title) is not the session
    ]
    drops = [(a, c) for a, c in itertools.pairwise(sizes) if c < a * 0.7]
    print(
        f"calls {len(sizes)}, largest input {max(sizes)}, drops (before, after) {drops}, "
        f"charged ${ended['metered_usd_micros'] / 1_000_000:.2f}, "
        f"harness reported {ended['result'].get('compaction')}, ended {ended['outcome']}, "
        f"text {ended['result'].get('text', '')[:200]!r}"
    )
    return ended, sizes, drops


def test_pi_compacts_a_long_session_and_survives(dsn, tmp_path):
    """Pi has no limit on a prompt, so the context is grown by prompts of
    random hex: the first measures bytes per token, the second lands just
    over Pi's compaction threshold (the window less Pi's reserve), and a
    third resumes after the compaction."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("live Pi needs OPENAI_API_KEY")
    model = SEATS["reviewer_openai"][1]
    window = pi.context_window(model)
    target = window - 8_192  # over Pi's threshold (window less its 16,384 reserve), under the window
    keyfile = tmp_path / "openai-key"
    keyfile.touch(mode=0o600)
    keyfile.write_text(f"OPENAI_API_KEY={os.environ['OPENAI_API_KEY']}\n")

    def turn_prompt(nbytes: int) -> str:
        return "Reply with just the word ok. Ignore this filler:\n" + secrets.token_hex(nbytes // 2)

    async def go():
        gateway = Gateway(dsn, openai_credential=OpenAIKey(str(keyfile)))
        task, b = await scripted.provisioned(dsn, tmp_path)
        await gateway.start()
        harness = {**b.harness, "max_output_tokens": 4096}

        async def turn(prompt, resume=None):
            return await runs.run_turn(
                gateway, task,
                pi.workspace_turn(prompt, cwd=b.workspace, model=model, harness=harness, resume=resume),
                dsn=dsn,
            )  # fmt: skip

        async def sizes():
            async with await db.connect(dsn) as conn:
                rows = await ledger.read(conn, task)
            return [
                n for r in rows if r["type"] == "gateway.charged" if (n := _input_tokens(r["payload"])) > 1000
            ]

        try:
            # Grow the context in steps, each sized from the bytes-per-token
            # the step before measured, so the last lands inside the 16k
            # between Pi's threshold and the window.
            turns, session, size, per_byte = [], None, 2_000, 0.55  # Pi's own prompt and tools, a first guess
            for stage in (200_000, 600_000, target):
                nbytes = int((stage - size) / per_byte)
                done = await turn(turn_prompt(nbytes), resume=session)
                turns.append(done)
                session = done["result"]["session_id"]
                now = (await sizes())[-1]
                per_byte, size = (now - size) / nbytes, now
                print(f"stage input {now} (aimed at {stage}), {per_byte:.3f} tokens a byte")
            two, spent = done, sum(t["metered_usd_micros"] for t in turns)
            three = await turn("Reply with just the word ok.", resume=session)
            spent = (spent + three["metered_usd_micros"]) / 1_000_000
            reported = sum(t["result"]["harness_reported_usd"] for t in [*turns, three])
        finally:
            await gateway.close()
        return two, three, await sizes(), spent, reported

    two, three, sizes, spent, reported = asyncio.run(go())
    print(
        f"calls {len(sizes)}, input sizes {sizes}, compaction reported {two['result']['compaction']}, "
        f"threshold {window - 16_384}, charged ${spent:.2f}, reported by Pi ${reported:.2f}"
    )
    assert two["result"]["compaction"], "Pi reported a compaction"
    assert three["outcome"] == "done" and not three["result"]["is_error"]
    assert sizes[-1] < sizes[-2] * 0.7, "the input size fell once, at the compaction"


def test_claude_code_compacts_a_long_session_and_survives(dsn, tmp_path):
    model = SEATS["frontier"][1]
    gateway = Gateway(dsn, credential=ClaudeLogin())

    def build_for(b, prompt):
        return claude_code.workspace_turn(prompt, cwd=b.workspace, model=model, harness=b.harness)

    # Claude Code compacts at its own threshold; read enough to pass a 1M window.
    _ended, _sizes, drops = asyncio.run(_measure(dsn, tmp_path, build_for, gateway, 1_050_000, model, 15_000))
    assert drops, "the input size fell once, at the compaction"
