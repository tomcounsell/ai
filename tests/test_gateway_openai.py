"""The gateway's OpenAI route, against a local upstream replaying Responses
traffic recorded through the gateway (`tests/fixtures/openai/`, made by
`record.py`; see `recorded.json`).

Every expected charge is worked by hand from the recording's usage and the
GPT-6.1 table checked on 2026-10-03, never computed by `spending`. Per
token, in micro-dollars:

    tier      input  cached  cache write  output   above 272K input:
    default   2      0.1     2.5          10       4    0.2   5     15
    flex      1      0.05    1.25         5        2    0.1   2.5   7.5
    fast      4      0.2     5            20       8    0.4   10    30

A web search is 10,000 micro-dollars. The worst case prices input at 10
(the long fast cache write) and output at 30 (the long fast output).

Cases the API cannot be made to produce cheaply are edits of a recording,
each named where it is made (`_edit`).

Live spend: none, except `test_live_*` under `VALOR_LIVE=1`.
"""

import asyncio
import json
import os
import stat
import subprocess
import sys
from math import ceil
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from yarl import URL

from core import db, ledger, spending, tasks
from core.gateway import ClaudeLogin, Gateway, OpenAIKey, openai_credentialed

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openai"
MODEL = "gpt-6.1-sol"
WINDOW = 1_050_000
SSE = "text/event-stream"
JSON = "application/json"
# A recognizable fake kernel key; no part of it may reach a turn or a row.
KEY = "sk-proj-KERNELFAKEKEY-SECRETMIDDLE-Q9Z7"
KEY_PARTS = ("KERNELFAKEKEY", "SECRETMIDDLE", "Q9Z7")


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _edit(name: str, *pairs: tuple[str, str]) -> bytes:
    """A recording with each `old` replaced by `new`, every replacement
    required to happen."""
    text = fixture(name).decode()
    for old, new in pairs:
        assert old in text, (name, old)
        text = text.replace(old, new)
    return text.encode()


def _until(name: str, marker: str) -> bytes:
    """A recording cut just before `marker`: the stream as a client sees it
    when the connection drops there."""
    text = fixture(name).decode()
    return text[: text.index(marker)].encode()


def body(**extra) -> dict:
    return {"model": MODEL, "input": "Reply with exactly one word: ready", "max_output_tokens": 200, **extra}


def estimate(sent: dict) -> int:
    """The body's input estimate: its compact JSON bytes over `bytes_per_token`."""
    from core.settings import settings

    return ceil(len(json.dumps(sent, separators=(",", ":")).encode()) / settings.bytes_per_token)


class Upstream:
    """Serves one reply to every path and records each request it got.
    `hang` holds the response open after its bytes, as a provider still
    generating would."""

    def __init__(self, status=200, data=b"", content_type=JSON, headers=None, hang=False):
        self.reply = (status, data, content_type, headers or {})
        self.hang = hang
        self.seen: list[dict] = []
        self.sent = asyncio.Event()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        data = await request.read()
        self.seen.append(
            {
                "method": request.method,
                "path": request.raw_path,
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "body": data,
            }
        )
        status, payload, ctype, extra = self.reply
        response = web.StreamResponse(status=status, headers={"content-type": ctype, **extra})
        await response.prepare(request)
        await response.write(payload)
        self.sent.set()
        if self.hang:
            await asyncio.sleep(3600)
        await response.write_eof()
        return response

    async def start(self) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app, handler_cancellation=True)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        return f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


async def _rows(dsn, task, kind=None) -> list[dict]:
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task)
    return [r["payload"] for r in rows if kind is None or r["type"] == kind]


def exchange(
    dsn,
    upstream: Upstream,
    sent: dict | None = None,
    *,
    path: str = "/openai/v1/responses",
    method: str = "post",
    headers: dict | None = None,
    key: str | None = KEY,
    tmp_path: Path | None = None,
    claude: ClaudeLogin | None = None,
    openai: OpenAIKey | None = None,
    stop_after_first_bytes: bool = False,
):
    """One call through a fresh gateway: what the turn got, what the
    upstream saw, and the task's rows and state."""

    async def go():
        url = await upstream.start()
        credential = openai
        if credential is None and key is not None:
            keyfile = tmp_path / "openai-key"
            keyfile.write_text(f"OPENAI_API_KEY={key}\n")
            credential = OpenAIKey(str(keyfile))
        gateway = Gateway(
            dsn, upstream=url, openai_upstream=url, credential=claude, openai_credential=credential
        )
        await gateway.start()
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="openai route"))
        base = gateway.issue(task, "turn-1")
        got = SimpleNamespace(status=None, body=b"", headers={})

        async def client():
            async with (
                aiohttp.ClientSession() as http,
                http.request(
                    method,
                    URL(base + path, encoded=True),
                    json=sent if method == "post" else None,
                    headers={"authorization": "Bearer sk-turn-own", **(headers or {})},
                ) as r,
            ):
                got.status, got.headers = r.status, dict(r.headers)
                try:
                    got.body = await r.read()
                except aiohttp.ClientError:
                    got.body = b"<cut>"

        pending = asyncio.create_task(client())
        if stop_after_first_bytes:
            await upstream.sent.wait()
            async with await db.connect(dsn) as conn:
                assert await tasks.stop(conn, task, reason="test")
            gateway.revoke(task)
        try:
            await pending
        except aiohttp.ClientError:
            got.body = b"<cut>"
        await gateway.drain(task)
        await gateway.close()
        await upstream.runner.cleanup()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
        rows = await _rows(dsn, task)
        opened = [r for r in rows if r.get("route") == "openai" and "estimate_usd_micros" in r]
        charged = [r for r in rows if "usd_micros" in r and "call_id" in r]
        return SimpleNamespace(
            status=got.status,
            body=got.body,
            headers=got.headers,
            seen=upstream.seen,
            rows=rows,
            opened=opened[0] if opened else None,
            charged=charged[0] if charged else None,
            state=state,
            task=task,
        )

    return asyncio.run(go())


def charged(dsn, tmp_path, data: bytes, sent: dict | None = None, content_type: str = SSE, **kw):
    out = exchange(
        dsn,
        Upstream(data=data, content_type=content_type),
        sent or body(stream=True),
        tmp_path=tmp_path,
        **kw,
    )
    assert out.status == 200 and out.body == data  # forwarded byte for byte
    assert not out.state["open_calls"] and tasks.audit(out.state) == []
    assert out.state["spent_usd_micros"] == out.charged["usd_micros"]
    return out


# -- charges, from recordings ------------------------------------------------------------


def test_a_streamed_reply_charges_its_input_and_output(dsn, tmp_path):
    out = charged(dsn, tmp_path, fixture("stream_text.sse"))
    # 13 input x 2 + 5 output x 10
    assert out.charged["usd_micros"] == 13 * 2 + 5 * 10 == 76
    assert out.charged["tier"] == "default" and out.charged["complete"] is True
    assert out.charged["price_checked"] == "2026-10-03" and out.charged["route"] == "openai"
    assert out.opened["route"] == "openai" and out.opened["credential"] == "kernel"


def test_the_same_stream_in_small_chunks_is_charged_the_same(dsn, tmp_path):
    data = fixture("stream_text.sse")

    class Chunked(Upstream):
        async def handle(self, request):
            await request.read()
            response = web.StreamResponse(headers={"content-type": SSE})
            await response.prepare(request)
            for i in range(0, len(data), 7):
                await response.write(data[i : i + 7])
            await response.write_eof()
            return response

    out = exchange(dsn, Chunked(), body(stream=True), tmp_path=tmp_path)
    assert out.body == data and out.charged["usd_micros"] == 76


def test_cached_input_is_charged_at_the_cached_rate(dsn, tmp_path):
    out = charged(dsn, tmp_path, fixture("cache_second.sse"))
    # 1,886 input: 1,873 cached x 0.1 (187.3, up to 188), 10 written x 2.5
    # (25), 3 more x 2; 5 output x 10.
    assert out.charged["usd_micros"] == 188 + 25 + 3 * 2 + 5 * 10 == 269
    assert out.charged["usd_micros"] < 1886 * 2 + 5 * 10  # the same usage uncached


def test_a_cache_write_is_charged_at_the_write_rate(dsn, tmp_path):
    out = charged(dsn, tmp_path, fixture("cache_first.sse"))
    # 1,883 written x 2.5 (4,707.5, up to 4,708), 3 more x 2, 5 output x 10.
    assert out.charged["usd_micros"] == 4708 + 3 * 2 + 5 * 10 == 4764


def test_reasoning_tokens_are_counted_once_inside_output(dsn, tmp_path):
    out = charged(dsn, tmp_path, fixture("incomplete.sse"))
    usage = out.charged["usage"]
    assert usage["output_tokens_details"]["reasoning_tokens"] == usage["output_tokens"] == 16
    # response.incomplete carries usage: 14 input x 2 + 16 output x 10.
    assert out.charged["usd_micros"] == 14 * 2 + 16 * 10 == 188


def test_a_failed_response_charges_its_usage(dsn, tmp_path):
    # Edit: the incomplete recording, its final event renamed response.failed.
    data = _edit(
        "incomplete.sse",
        ("event: response.incomplete", "event: response.failed"),
        ('"type":"response.incomplete"', '"type":"response.failed"'),
    )
    out = charged(dsn, tmp_path, data)
    assert out.charged["usd_micros"] == 188


def test_input_above_the_long_context_threshold_charges_long_rates_on_input_and_output(dsn, tmp_path):
    # Edit: the text reply's reported input raised from 13 to 300,000 tokens.
    data = _edit(
        "stream_text.sse",
        ('"input_tokens":13,', '"input_tokens":300000,'),
        ('"total_tokens":18}', '"total_tokens":300005}'),
    )
    out = charged(dsn, tmp_path, data)
    assert out.charged["usd_micros"] == 300_000 * 4 + 5 * 15 == 1_200_075


@pytest.mark.parametrize(
    ("reported", "requested", "usd", "unpriced"),
    [
        ("fast", None, 13 * 4 + 5 * 20, False),
        ("default", None, 13 * 2 + 5 * 10, False),
        ("flex", "flex", ceil(13 * 1 + 5 * 5), False),
        ("priority", "priority", 13 * 4 + 5 * 20, False),
        ("scale", None, 13 * 4 + 5 * 20, True),  # charged at the highest tier
        ("scale", "scale", 13 * 4 + 5 * 20, True),  # an unlisted tier asked for is forwarded
    ],
)
def test_the_tier_charged_is_the_one_the_response_reports(dsn, tmp_path, reported, requested, usd, unpriced):
    # Edit: the completed response's service_tier set to the reported tier.
    data = _edit("stream_text.sse", ('"service_tier":"default"', f'"service_tier":"{reported}"'))
    sent = body(stream=True, **({"service_tier": requested} if requested else {}))
    out = charged(dsn, tmp_path, data, sent)
    assert out.charged["usd_micros"] == usd
    assert out.charged["tier"] == reported and out.charged["tier_unpriced"] is unpriced
    if requested:
        assert json.loads(out.seen[0]["body"])["service_tier"] == requested


def test_a_web_search_call_charges_its_tokens_and_one_fee(dsn, tmp_path):
    sent = body(stream=True, tools=[{"type": "web_search"}], max_tool_calls=1, max_output_tokens=1000)
    out = charged(dsn, tmp_path, fixture("web_search.sse"), sent)
    # 8,697 input: 4,267 written x 2.5 (10,667.5, up to 10,668), 4,430 more
    # x 2; 47 output x 10; one search 10,000. The call item shows in the
    # stream and in the completed output: one fee, not two.
    assert out.charged["tool_calls"] == {"web_search": 1}
    assert out.charged["usd_micros"] == 10_668 + 4_430 * 2 + 47 * 10 + 10_000 == 29_998


def test_a_non_streamed_reply_charges_its_usage(dsn, tmp_path):
    out = charged(dsn, tmp_path, fixture("whole.json"), body(), content_type=JSON)
    assert out.charged["usd_micros"] == 76


def test_a_queued_background_reply_with_null_usage_charges_the_worst_case(dsn, tmp_path):
    sent = body(background=True, store=True)
    out = charged(dsn, tmp_path, fixture("background_queued.json"), sent, content_type=JSON)
    assert out.charged["usage"] is None and out.charged["complete"] is False
    assert out.charged["usd_micros"] == estimate(sent) * 10 + 200 * 30


def test_a_streamed_background_reply_charges_its_usage(dsn, tmp_path):
    out = charged(
        dsn, tmp_path, fixture("background_stream.sse"), body(stream=True, background=True, store=True)
    )
    assert out.charged["usd_micros"] == 76


def test_a_400_from_the_upstream_charges_nothing(dsn, tmp_path):
    data = fixture("error_400.json")
    out = exchange(dsn, Upstream(400, data), body(max_output_tokens=16), tmp_path=tmp_path)
    assert out.status == 400 and out.body == data
    assert out.charged["usd_micros"] == 0 and not out.state["open_calls"]


def test_a_stream_cut_after_response_created_charges_the_worst_case(dsn, tmp_path):
    sent = body(stream=True)
    out = exchange(
        dsn,
        Upstream(data=_until("stream_text.sse", "event: response.in_progress"), content_type=SSE),
        sent,
        tmp_path=tmp_path,
    )
    assert out.charged["complete"] is False and not out.state["open_calls"]
    assert out.charged["usd_micros"] == estimate(sent) * 10 + 200 * 30


def test_a_stream_cut_after_a_search_charges_the_worst_case_and_the_search(dsn, tmp_path):
    sent = body(stream=True, tools=[{"type": "web_search"}], max_output_tokens=1000)
    data = _until("web_search.sse", "event: response.completed")
    out = exchange(dsn, Upstream(data=data, content_type=SSE), sent, tmp_path=tmp_path)
    assert out.charged["tool_calls"] == {"web_search": 1}
    assert out.charged["usd_micros"] == estimate(sent) * 10 + 1000 * 30 + 10_000
    assert out.charged["bounded"] is False  # a hosted tool with no max_tool_calls


def test_a_turn_stopped_mid_stream_charges_the_worst_case_and_closes(dsn, tmp_path):
    sent = body(stream=True)
    upstream = Upstream(
        data=_until("stream_text.sse", "event: response.output_text.delta"), content_type=SSE, hang=True
    )
    out = exchange(dsn, upstream, sent, tmp_path=tmp_path, stop_after_first_bytes=True)
    assert out.charged["cut"] is True and not out.state["open_calls"]
    assert out.charged["usd_micros"] == estimate(sent) * 10 + 200 * 30
    assert out.state["state"] == "stopped" and tasks.audit(out.state) == []


def test_the_estimate_takes_the_tables_maximum_output_when_the_body_sets_none(dsn, tmp_path):
    sent = {"model": MODEL, "input": "hi", "stream": True}
    out = charged(dsn, tmp_path, fixture("stream_text.sse"), sent)
    assert out.opened["max_tokens"] == 128_000
    assert out.opened["estimate_usd_micros"] == estimate(sent) * 10 + 128_000 * 30


@pytest.mark.parametrize(
    "extra",
    [
        {"previous_response_id": "resp_earlier"},
        {"conversation": "conv_1"},
        {
            "input": [
                {"role": "user", "content": [{"type": "input_file", "file_url": "https://example.com/a.pdf"}]}
            ]
        },
        {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file-1"}]}]},
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "https://example.com/a.png"}],
                }
            ]
        },
        {"input": [{"type": "item_reference", "id": "msg_1"}]},
    ],
)
def test_content_referenced_by_id_or_url_takes_the_context_window(dsn, tmp_path, extra):
    out = charged(dsn, tmp_path, fixture("stream_text.sse"), body(stream=True, **extra))
    assert out.opened["estimated_input"] == WINDOW
    assert out.charged["referenced"] is True


def test_an_inline_image_is_not_referenced(dsn, tmp_path):
    inline = [
        {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AAAA"}]}
    ]
    out = charged(dsn, tmp_path, fixture("stream_text.sse"), body(stream=True, input=inline))
    assert out.charged["referenced"] is False and out.opened["estimated_input"] < WINDOW


def test_a_hosted_tool_with_max_tool_calls_estimates_that_many_windows_plus_one(dsn, tmp_path):
    sent = body(stream=True, tools=[{"type": "web_search"}], max_tool_calls=3, max_output_tokens=1000)
    out = charged(dsn, tmp_path, fixture("web_search.sse"), sent)
    assert out.opened["estimated_input"] == 4 * WINDOW
    assert out.opened["estimate_usd_micros"] == 4 * WINDOW * 10 + 1000 * 30 + 3 * 10_000
    assert out.charged["bounded"] is True


def test_a_worst_case_past_bigint_is_recorded_and_summed_exactly(dsn, tmp_path):
    # The turn may ask for any max_tool_calls; the ledger's JSON numbers and
    # the turn's sum hold the resulting worst case exactly.
    calls = 2**70
    sent = body(stream=True, tools=[{"type": "web_search"}], max_tool_calls=calls, max_output_tokens=1000)
    upstream = Upstream(
        data=_until("stream_text.sse", "event: response.output_text.delta"), content_type=SSE, hang=True
    )
    out = exchange(dsn, upstream, sent, tmp_path=tmp_path, stop_after_first_bytes=True)
    worst = (calls + 1) * WINDOW * 10 + 1000 * 30 + calls * 10_000
    assert worst > 2**63
    assert out.opened["estimate_usd_micros"] == worst and type(out.opened["estimate_usd_micros"]) is int
    assert out.charged["cut"] is True and out.charged["usd_micros"] == worst
    assert out.state["spent_usd_micros"] == worst

    async def summed():
        async with await db.connect(dsn) as conn:
            return await spending.turn_spent(conn, out.task, "turn-1")

    assert asyncio.run(summed()) == worst


def test_a_negative_max_tool_calls_counts_as_none_allowed(dsn, tmp_path):
    sent = body(stream=True, tools=[{"type": "web_search"}], max_tool_calls=-5, max_output_tokens=1000)
    out = charged(dsn, tmp_path, fixture("web_search.sse"), sent)
    # One window and no fee cap; the search OpenAI ran is still charged.
    assert out.opened["estimated_input"] == WINDOW
    assert out.opened["estimate_usd_micros"] == WINDOW * 10 + 1000 * 30
    assert out.charged["tool_calls"] == {"web_search": 1} and out.charged["usd_micros"] == 29_998


def test_the_request_id_is_on_the_charge_not_the_opening(dsn, tmp_path):
    data = fixture("stream_text.sse")
    upstream = Upstream(data=data, content_type=SSE, headers={"x-request-id": "req_replayed1"})
    out = exchange(dsn, upstream, body(stream=True), tmp_path=tmp_path)
    assert out.charged["request_id"] == "req_replayed1"
    assert "request_id" not in out.opened


# -- what the meter cannot price, and paths ------------------------------------------------


@pytest.mark.parametrize(
    "sent",
    [
        body(model="gpt-4o"),
        body(tools=[{"type": "code_interpreter", "container": {"type": "auto"}}]),
        body(tools=[{"type": "shell", "environment": {"type": "container_auto"}}]),
        body(tools=[{"type": "shell"}]),
        body(tools=[{"type": "image_generation"}]),
        body(tools=[{"type": "some_new_tool"}]),
        body(prompt={"id": "pmpt_1"}),
    ],
)
def test_a_request_the_table_cannot_price_is_a_400_with_no_row_and_nothing_sent(dsn, tmp_path, sent):
    out = exchange(dsn, Upstream(data=fixture("whole.json")), sent, tmp_path=tmp_path)
    assert out.status == 400 and "has no price" in json.loads(out.body)["error"]["message"]
    assert not out.seen and out.opened is None and out.charged is None


@pytest.mark.parametrize(
    "tool",
    [
        {"type": "function", "name": "f", "parameters": {"type": "object"}},
        {"type": "custom", "name": "c"},
        {"type": "mcp", "server_label": "x", "server_url": "https://example.com/mcp"},
        {"type": "web_search"},
        {"type": "web_search_preview"},
        {"type": "file_search", "vector_store_ids": ["vs_1"]},
        {"type": "shell", "environment": {"type": "local"}},
        {"type": "local_shell"},
    ],
)
def test_priced_and_token_only_tools_are_forwarded(dsn, tmp_path, tool):
    out = exchange(dsn, Upstream(data=fixture("whole.json")), body(tools=[tool]), tmp_path=tmp_path)
    assert out.status == 200 and len(out.seen) == 1 and out.charged["usd_micros"] == 76


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/openai/v1/chat/completions", "post"),
        ("/openai/v1/files", "get"),
        ("/openai/v1/uploads", "post"),
        ("/openai/v1/batches", "post"),
        ("/openai/v1/assistants", "get"),
        ("/openai/v1/realtime/sessions", "post"),
        ("/openai/v1/audio/speech", "post"),
        ("/openai/v1/images/generations", "post"),
        ("/openai/v1/embeddings", "post"),
        ("/openai/v1/fine_tuning/jobs", "get"),
        ("/openai/v1/responses/resp_1", "get"),
        ("/openai/v1/responses/resp_1/cancel", "post"),
        ("/openai/v1/responses/compact", "post"),
        ("/openai/v1/responses/input_tokens", "post"),
    ],
)
@pytest.mark.parametrize("with_key", [True, False])
def test_unlisted_openai_paths_are_a_403_with_nothing_sent(dsn, tmp_path, path, method, with_key):
    out = exchange(
        dsn, Upstream(), body(), path=path, method=method, tmp_path=tmp_path, key=KEY if with_key else None
    )
    assert out.status == 403 and not out.seen and out.opened is None


@pytest.mark.parametrize(
    "path", ["/openai/v1/responses%2f..", "/openai//v1/responses", "/openai/v1/./responses"]
)
def test_unsafe_tails_are_a_400(dsn, tmp_path, path):
    out = exchange(dsn, Upstream(), body(), path=path, tmp_path=tmp_path)
    assert out.status == 400 and not out.seen


@pytest.mark.parametrize(
    ("model", "priced"),
    [
        (MODEL, True),
        (f"{MODEL}-2026-09-30", True),
        (f"{MODEL}-pro", False),
        (f"{MODEL}-pro-2026-09-30", False),
        (f"{MODEL}-mini", False),
        (f"{MODEL}-2026-09", False),
    ],
)
def test_only_the_exact_id_or_a_dated_one_is_priced(model, priced):
    assert (spending.openai_prices(model) is not None) is priced


def test_a_variant_of_a_priced_id_is_a_400_with_nothing_sent(dsn, tmp_path):
    out = exchange(dsn, Upstream(data=fixture("whole.json")), body(model=f"{MODEL}-pro"), tmp_path=tmp_path)
    assert out.status == 400 and json.loads(out.body)["error"]["message"] == f"model {MODEL}-pro has no price"
    assert not out.seen and out.opened is None


def test_a_dated_id_is_forwarded_and_charged_at_its_rates(dsn, tmp_path):
    out = exchange(
        dsn, Upstream(data=fixture("whole.json")), body(model=f"{MODEL}-2026-09-30"), tmp_path=tmp_path
    )
    assert out.status == 200 and len(out.seen) == 1 and out.charged["usd_micros"] == 76


def test_the_listed_paths():
    assert openai_credentialed("v1/responses", "POST") and openai_credentialed("v1/responses/", "POST")
    for method in ("GET", "HEAD"):
        assert openai_credentialed("v1/models", method) and openai_credentialed(f"v1/models/{MODEL}", method)
    assert not openai_credentialed("v1/models/../files", "GET")
    assert not openai_credentialed("v1/responses/x", "POST")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        (f"/openai/v1/models/{MODEL}", "delete"),
        ("/openai/v1/models/ft-abc", "delete"),
        ("/openai/v1/models", "post"),
        (f"/openai/v1/models/{MODEL}", "post"),
        ("/openai/v1/models", "put"),
        ("/openai/v1/responses", "get"),
        ("/openai/v1/responses", "delete"),
        ("/openai/v1/responses", "put"),
        ("/openai/v1/responses", "patch"),
        ("/openai/v1/responses", "head"),
    ],
)
@pytest.mark.parametrize("with_key", [True, False])
def test_a_listed_path_with_another_method_is_a_403_with_nothing_sent(dsn, tmp_path, path, method, with_key):
    out = exchange(
        dsn, Upstream(), body(), path=path, method=method, tmp_path=tmp_path, key=KEY if with_key else None
    )
    assert out.status == 403 and not out.seen and out.opened is None


def test_head_on_a_model_is_forwarded(dsn, tmp_path):
    out = exchange(dsn, Upstream(), path=f"/openai/v1/models/{MODEL}", method="head", tmp_path=tmp_path)
    assert out.status == 200 and out.seen[0]["method"] == "HEAD"
    assert out.seen[0]["headers"]["authorization"] == f"Bearer {KEY}"


def test_the_model_list_is_forwarded_with_the_key_and_charged_nothing(dsn, tmp_path):
    listing = b'{"object":"list","data":[{"id":"gpt-6.1-sol"}]}'
    out = exchange(
        dsn, Upstream(data=listing), path=f"/openai/v1/models/{MODEL}", method="get", tmp_path=tmp_path
    )
    assert out.status == 200 and out.body == listing
    assert (
        out.seen[0]["path"] == f"/v1/models/{MODEL}"
        and out.seen[0]["headers"]["authorization"] == f"Bearer {KEY}"
    )
    assert out.opened is None and out.charged is None


def test_with_no_kernel_key_the_turns_own_key_is_forwarded_and_metered(dsn, tmp_path):
    out = exchange(dsn, Upstream(data=fixture("whole.json")), body(), key=None, tmp_path=tmp_path)
    assert out.status == 200 and out.seen[0]["headers"]["authorization"] == "Bearer sk-turn-own"
    assert out.opened["credential"] == "turn" and out.charged["usd_micros"] == 76


def test_a_missing_or_empty_key_file_is_no_kernel_key(dsn, tmp_path):
    (tmp_path / "empty").write_text("OPENAI_API_KEY=\n")
    for keyfile in (tmp_path / "absent", tmp_path / "empty"):
        out = exchange(
            dsn,
            Upstream(data=fixture("whole.json")),
            body(),
            openai=OpenAIKey(str(keyfile)),
            tmp_path=tmp_path,
        )
        assert out.opened["credential"] == "turn"
        assert out.seen[0]["headers"]["authorization"] == "Bearer sk-turn-own"


# -- headers and the key -------------------------------------------------------------------


def test_the_turns_key_and_account_headers_never_reach_the_upstream(dsn, tmp_path):
    turn_headers = {
        "x-api-key": "sk-turn-x",
        "OpenAI-Organization": "org-turn",
        "OpenAI-Project": "proj_turn",
    }
    upstream = Upstream(
        data=fixture("whole.json"),
        headers={
            "openai-organization": "org-kernel",
            "openai-project": "proj_kernel",
            "x-request-id": "req_1",
        },
    )
    out = exchange(dsn, upstream, body(), headers=turn_headers, tmp_path=tmp_path)
    seen = out.seen[0]["headers"]
    assert seen["authorization"] == f"Bearer {KEY}"
    assert not {"x-api-key", "openai-organization", "openai-project"} & set(seen)
    assert "sk-turn" not in json.dumps(seen)
    got = {k.lower() for k in out.headers}
    assert not {"openai-organization", "openai-project"} & got and "x-request-id" in got


@pytest.mark.parametrize("with_key", [True, False])
def test_a_proxy_credential_never_reaches_the_upstream(dsn, tmp_path, with_key):
    out = exchange(
        dsn,
        Upstream(data=fixture("whole.json")),
        body(),
        headers={"Proxy-Authorization": "Basic dHVybjpzZWNyZXQ="},
        key=KEY if with_key else None,
        tmp_path=tmp_path,
    )
    assert out.status == 200 and "proxy-authorization" not in {k.lower() for k in out.seen[0]["headers"]}


def test_an_upstream_401_invalidates_only_the_openai_key_and_its_body_is_the_gateways(dsn, tmp_path):
    token = tmp_path / "claude-token"
    token.write_text("claude-first\n")
    claude = ClaudeLogin(str(token), ttl_s=3600)
    assert claude.token() == "claude-first"
    claude_cached = claude._cached
    keyfile = tmp_path / "openai-key"
    keyfile.write_text(f"OPENAI_API_KEY={KEY}\n")
    openai = OpenAIKey(str(keyfile), ttl_s=3600)
    assert openai.token() == KEY
    keyfile.write_text("OPENAI_API_KEY=sk-second\n")
    echo = json.dumps(
        {
            "error": {
                "message": "Incorrect API key provided: sk-proj-*****Q9Z7.",
                "type": "invalid_request_error",
            }
        }
    )
    out = exchange(dsn, Upstream(401, echo.encode()), body(), claude=claude, openai=openai, tmp_path=tmp_path)
    assert out.status == 401
    assert json.loads(out.body)["error"]["message"] == "the kernel's OpenAI key was refused"
    assert out.charged["usd_micros"] == 0
    assert claude._cached is claude_cached and not claude._stale  # untouched
    assert openai.token() == "sk-second"  # re-read at once


@pytest.mark.parametrize("metered", [True, False])
def test_a_401_on_the_turns_own_key_names_the_turn_and_leaves_the_kernel_key(dsn, tmp_path, metered):
    keyfile = tmp_path / "openai-key"  # absent: no kernel key
    openai = OpenAIKey(str(keyfile), ttl_s=3600)
    assert openai.token() is None
    cached = openai._cached
    kw = {} if metered else {"path": "/openai/v1/models", "method": "get"}
    out = exchange(
        dsn, Upstream(401, b'{"error":{"message":"bad key"}}'), body(), openai=openai, tmp_path=tmp_path, **kw
    )
    assert out.status == 401 and out.seen[0]["headers"]["authorization"] == "Bearer sk-turn-own"
    assert json.loads(out.body)["error"]["message"] == "the turn's OpenAI key was refused"
    assert openai._cached is cached  # not invalidated
    keyfile.write_text(f"OPENAI_API_KEY={KEY}\n")
    assert openai.token() is None  # still the cached read, within its ttl


def test_no_part_of_the_kernel_key_reaches_a_turn_a_row_or_stderr(dsn, tmp_path, capfd):
    echo = json.dumps(
        {"error": {"message": f"Incorrect API key provided: sk-proj-*****{KEY[-4:]}."}}
    ).encode()
    cases = [
        (Upstream(data=fixture("stream_text.sse"), content_type=SSE), body(stream=True), {}),
        (Upstream(data=fixture("whole.json")), body(), {}),
        (Upstream(400, fixture("error_400.json")), body(), {}),
        (Upstream(401, echo), body(), {}),
        (Upstream(401, echo), None, {"path": "/openai/v1/models", "method": "get"}),
        (Upstream(500, b'{"error":{"message":"server"}}'), body(), {}),
        (
            Upstream(data=_until("stream_text.sse", "event: response.in_progress"), content_type=SSE),
            body(stream=True),
            {},
        ),
        (Upstream(), body(model="gpt-4o"), {}),
        (Upstream(), body(), {"path": "/openai/v1/files", "method": "get"}),
        (Upstream(), body(), {"path": "/openai/v1/./responses"}),
    ]
    seen_all = []
    for upstream, sent, kw in cases:
        out = exchange(dsn, upstream, sent, tmp_path=tmp_path, **kw)
        received = out.body.decode(errors="replace") + json.dumps(out.headers) + json.dumps(out.rows)
        for part in KEY_PARTS:
            assert part not in received, (kw, out.status)
        seen_all += out.seen
    captured = capfd.readouterr()
    for part in KEY_PARTS:
        assert part not in captured.err and part not in captured.out
    assert any(s["headers"].get("authorization") == f"Bearer {KEY}" for s in seen_all)  # it was used


# -- the key command -----------------------------------------------------------------------


def _cli(*args, env) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


def test_openai_key_copies_the_named_key_replacing_the_old_and_prints_no_value(tmp_path):
    from core import credentials

    vault = tmp_path / "vault.env"
    vault.write_text('OPENAI_API_KEY="sk-first-VALUE"\nexport OTHER_KEY=sk-other-VALUE\n')
    env = {"VALOR_VAULT_ENV": str(vault), "VALOR_PG_PASSFILE": str(tmp_path / "kernel" / "pgpass")}
    keyfile = tmp_path / "kernel" / "openai-key"
    first = _cli("openai-key", env=env)
    assert first.returncode == 0 and "OPENAI_API_KEY (from OPENAI_API_KEY): written" in first.stdout
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert credentials.read_key(keyfile, "OPENAI_API_KEY") == "sk-first-VALUE"
    second = _cli("openai-key", env=env)
    assert "kept" in second.stdout
    swapped = _cli("openai-key", "--name", "OTHER_KEY", env=env)
    assert "OPENAI_API_KEY (from OTHER_KEY): written" in swapped.stdout
    assert credentials.read_key(keyfile, "OPENAI_API_KEY") == "sk-other-VALUE"
    assert "sk-first" not in keyfile.read_text()
    missing = _cli("openai-key", "--name", "ABSENT", env=env)
    assert "OPENAI_API_KEY (from ABSENT): missing" in missing.stdout
    for out in (first, second, swapped, missing):
        assert "VALUE" not in out.stdout + out.stderr


def test_a_missing_openai_key_names_its_command(tmp_path):
    from core import credentials

    with pytest.raises(credentials.MissingKey, match="python -m core openai-key"):
        credentials.read_key(tmp_path / "openai-key", "OPENAI_API_KEY", "openai-key")


# -- live ----------------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1")
@pytest.mark.spend(usd=0.05)
def test_live_one_streamed_reply_through_the_kernel_key(dsn):
    from core.settings import settings

    keyfile = Path(settings.openai_keyfile)
    if not keyfile.exists():
        pytest.skip("no kernel OpenAI key: python -m core openai-key")

    async def go():
        gateway = Gateway(dsn, openai_credential=OpenAIKey())
        await gateway.start()
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="live openai"))
        base = gateway.issue(task, "turn-live")
        async with (
            aiohttp.ClientSession() as http,
            http.post(base + "/openai/v1/responses", json=body(stream=True)) as r,
        ):
            assert r.status == 200
            await r.read()
        await gateway.drain(task)
        await gateway.close()
        return (await _rows(dsn, task, "gateway.charged"))[0]

    row = asyncio.run(go())
    print(json.dumps({k: row[k] for k in ("usage", "tier", "usd_micros", "request_id")}))
    assert row["complete"] and row["usd_micros"] > 0
