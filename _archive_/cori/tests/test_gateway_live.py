"""One real Haiku call through the gateway. Plan 04 task 10.

Everything else in this plan's tests runs against a fake provider, so what
is unproven until here is the wire: that a real provider accepts the body
the gateway forwards, that the SSE the gateway parses is the SSE the
provider sends, and that the usage row the gateway writes is the usage the
provider reports. The client is the Anthropic SDK pointed at the gateway by
base URL, which is how the worker reaches it (spike 08), and it asks for a
non-streaming message, so the forced upstream stream and the assembly back
into one Message are both on the path.

Skips without the Anthropic key in the Keychain, the way
`tests/test_models.py` does.
"""

import asyncio
import uuid

import psycopg
import pytest
import uvicorn

from gateway.app import build_app
from gateway.budget import cost
from gateway.core import Gateway
from infra.models import load
from infra.secrets import MissingSecret, read_secret
from schemas.gateway import Usage
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import FakeTree

pytestmark = requires_postgres

MODEL = load().model("summarizer").id
PRICES = load().model("summarizer").usd_micros_per_mtok

# Room for one small call at Haiku's prices, with no room to spare being
# the point of a budget the gateway enforces rather than ignores.
BUDGET = 1_000_000


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def provider_key() -> str:
    try:
        return read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("no Anthropic key in the Keychain")


async def test_a_real_haiku_call_bills_what_the_provider_reports():
    key = provider_key()
    anthropic = pytest.importorskip("anthropic")

    tree = FakeTree()
    brief = uuid.uuid4().hex
    space = f"space-{uuid.uuid4().hex[:8]}"
    tree.add(brief, BUDGET, generation=1)
    gateway = Gateway(provider_key=key, connect=connect, tree=tree)

    async with await connect() as conn:
        token = await gateway.issue_token(
            conn,
            brief_id=brief,
            generation=1,
            model_ref=MODEL,
            space=space,
        )
        await conn.commit()

    config = uvicorn.Config(
        build_app(gateway), host="127.0.0.1", port=0, log_level="error"
    )
    server = uvicorn.Server(config)
    task = asyncio.ensure_future(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        client = anthropic.AsyncAnthropic(
            api_key=token.get_secret_value(),
            base_url=f"http://127.0.0.1:{port}",
            max_retries=0,
        )
        message = await client.messages.create(
            model=MODEL,
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        await gateway.drain()
    finally:
        server.should_exit = True
        await task
        await gateway.aclose()

    assert message.model == MODEL
    assert message.content[0].text.strip()

    async with await connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT event, usage FROM gateway_log WHERE brief_id = %s "
                "AND event IN ('response', 'cut', 'refused', 'upstream_error') "
                "ORDER BY id",
                (brief,),
            )
        ).fetchall()
    assert [r[0] for r in rows] == ["response"]
    usage = Usage(**rows[0][1])

    # The four token fields are the provider's own numbers, not the
    # gateway's arithmetic on them.
    reported = message.usage
    assert usage.input_tokens == reported.input_tokens
    assert usage.output_tokens == reported.output_tokens
    assert usage.cache_creation_input_tokens == (
        reported.cache_creation_input_tokens or 0
    )
    assert usage.cache_read_input_tokens == (reported.cache_read_input_tokens or 0)
    assert usage.charged_reserved is False

    # The money is those four fields at the seat's price, and the tree was
    # asked for exactly that, as incurred spend.
    billed = cost(
        {
            "input_tokens": reported.input_tokens,
            "output_tokens": reported.output_tokens,
            "cache_creation_input_tokens": reported.cache_creation_input_tokens or 0,
            "cache_read_input_tokens": reported.cache_read_input_tokens or 0,
        },
        PRICES,
    )
    assert usage.usd_micros == billed
    assert len(tree.consumed) == 1
    consumed_brief, amount, incurred = tree.consumed[0]
    assert consumed_brief == brief
    assert amount.usd_micros == billed
    assert incurred is True
