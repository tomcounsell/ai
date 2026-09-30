"""The gateway's invariants, as machines. Plan 04 task 9; Properties P1,
P2, P5.

Three claims the example tests can only sample. The ledger conserves: what
the tree records, plus what is left, is what the Brief was given plus every
shortfall, and a shortfall only ever follows an estimate the call outgrew.
Revoke is final: once a Brief is revoked the provider hears nothing more
for it, the call in flight ends as a cut, and every later call is refused.
The log is the trace: what the model was asked to do is rebuildable from
`gateway_log` and `request_bodies` alone, with no other record.

Every machine drives the real route against a fake provider and a fake
tree, so the rows and the consumes are the gateway's own. P2 serves its
provider under uvicorn on a loopback port of its own, because the
in-process transport hands the whole response over as one chunk and a call
that has already arrived cannot be cut.
"""

import asyncio
import json
import uuid

import psycopg
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from gateway.app import handle
from gateway.budget import reserve
from gateway.core import Gateway
from infra.models import load
from tests.conftest import dsn, requires_postgres
from tests.gateway_fakes import (
    FakeTree,
    FakeUpstream,
    message_start,
    request_for,
    text_stream,
)

pytestmark = requires_postgres

MODEL = "claude-opus-5"
PRICES = load().model("frontier").usd_micros_per_mtok

# Small enough that a handful of calls exhausts it, so refusals and
# overruns both appear inside one machine's steps.
BUDGET = 60_000

# What the counting endpoint answers. Held low on purpose: an estimate the
# call then outgrows is the only way a completed call can overrun, and P1
# is the statement that the ledger still adds up when it does.
COUNTED = 10

MACHINE = settings(max_examples=300, deadline=None, stateful_step_count=12)


def connect():
    return psycopg.AsyncConnection.connect(dsn("kernel_rw"))


def run(coro):
    return asyncio.run(coro)


def body(*, max_tokens: int, messages: list, **rest) -> dict:
    return {"model": MODEL, "max_tokens": max_tokens, "messages": messages, **rest}


async def log_rows(brief: str, events=("request", "response", "cut", "refused")):
    async with await connect() as conn:
        return await (
            await conn.execute(
                "SELECT event, call, reason, estimated_input, usage FROM gateway_log "
                "WHERE brief_id = %s AND event = ANY(%s) ORDER BY id",
                (brief, list(events)),
            )
        ).fetchall()


async def unclosed_requests(brief: str) -> list[int]:
    """Every `request` row with no closing row for the same call. The
    counting failure's `upstream_error` carries a null call, so it closes
    nothing and is not counted here."""
    async with await connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT r.call FROM gateway_log r WHERE r.brief_id = %s "
                "AND r.event = 'request' AND NOT EXISTS ("
                "  SELECT 1 FROM gateway_log c WHERE c.brief_id = r.brief_id "
                "  AND c.call = r.call "
                "  AND c.event IN ('response', 'cut', 'upstream_error')"
                ") ORDER BY r.call",
                (brief,),
            )
        ).fetchall()
    return [r[0] for r in rows]


async def counting_failures(brief: str) -> int:
    async with await connect() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) FROM gateway_log WHERE brief_id = %s "
                "AND event = 'upstream_error' AND reason LIKE 'count_tokens:%%'",
                (brief,),
            )
        ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# P1: the ledger conserves


class BudgetLedger(RuleBasedStateMachine):
    """One Brief, one allocation, and every way a call can end.

    The machine owns its event loop, the way the event store's machine
    does: Hypothesis drives rules synchronously and the gateway is async
    throughout.
    """

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.upstream = FakeUpstream(count=COUNTED)
        self.tree = FakeTree()
        self.gateway = Gateway(
            provider_key="not-a-key",
            connect=connect,
            tree=self.tree,
            client=self.upstream.client(),
        )
        self.space = f"space-{uuid.uuid4().hex[:8]}"
        self.brief = uuid.uuid4().hex
        self.tree.add(self.brief, BUDGET, generation=1)
        self.headers = {"x-api-key": self.run(self._mint())}
        self.messages = [{"role": "user", "content": "start"}]
        # One entry per forwarded call: (estimate, max_tokens, remaining).
        self.forwarded: list[tuple[int, int, int]] = []

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _mint(self) -> str:
        async with await connect() as conn:
            token = await self.gateway.issue_token(
                conn,
                brief_id=self.brief,
                generation=1,
                model_ref=MODEL,
                space=self.space,
            )
            await conn.commit()
        return token.get_secret_value()

    # -- rules ---------------------------------------------------------------

    @rule(
        max_tokens=st.integers(min_value=1, max_value=400),
        appended_chars=st.integers(min_value=0, max_value=4000),
        actual_input=st.integers(min_value=0, max_value=400),
        actual_output=st.integers(min_value=0, max_value=400),
        count_fails=st.booleans(),
    )
    def call(
        self, max_tokens, appended_chars, actual_input, actual_output, count_fails
    ):
        self.run(
            self._call(
                max_tokens, appended_chars, actual_input, actual_output, count_fails
            )
        )

    async def _call(
        self, max_tokens, appended_chars, actual_input, actual_output, count_fails
    ):
        # A provider never bills more output than `max_tokens` allowed, so
        # neither does the fake: the reserve is what that number buys.
        actual_output = min(actual_output, max_tokens)
        if appended_chars:
            self.messages.append({"role": "user", "content": "x" * appended_chars})
        self.upstream.count_status = 500 if count_fails else 200
        self.upstream.events = text_stream(
            MODEL, "ok", output_tokens=actual_output, input_tokens=actual_input
        )

        consumed_before = len(self.tree.consumed)
        overruns_before = len(self.tree.overruns)
        counted_before = len(self.upstream.count_calls)
        failures_before = await counting_failures(self.brief)
        remaining = self.tree.allocations[self.brief]

        call = body(max_tokens=max_tokens, messages=list(self.messages))
        response = await handle(self.gateway, request_for(call, self.headers))
        await self.gateway.drain()

        rows = await log_rows(self.brief)
        if response.status_code != 200:
            await self._check_refusal(
                response,
                rows,
                count_fails=count_fails,
                counted_before=counted_before,
                failures_before=failures_before,
            )
            assert len(self.tree.consumed) == consumed_before
            return

        request_row = [r for r in rows if r[0] == "request"][-1]
        estimate = request_row[3]
        # Every forwarded call fit within what the ledger said was left.
        assert reserve(estimate, max_tokens, PRICES).usd_micros <= remaining
        self.forwarded.append((estimate, max_tokens, remaining))

        assert len(self.tree.consumed) == consumed_before + 1
        _, amount, incurred = self.tree.consumed[-1]
        assert incurred is True

        closing = [r for r in rows if r[0] in ("response", "cut")][-1]
        overran = len(self.tree.overruns) > overruns_before
        assert (closing[2] == "overrun") is overran
        if overran:
            # The only way a call whose reserve fit can still exceed the
            # ledger is an input the estimate did not see coming.
            assert actual_input > estimate
            assert amount.usd_micros > remaining

    async def _check_refusal(
        self, response, rows, *, count_fails, counted_before, failures_before
    ):
        assert response.status_code == 403
        reason = [r for r in rows if r[0] == "refused"][-1][2]
        assert reason in ("revoked", "model", "stale_generation", "budget")
        if reason != "budget":
            return
        # A Brief is never refused by an estimate's margin: either the
        # counting endpoint answered for this call, or its failure to
        # answer is a row of its own.
        counted = len(self.upstream.count_calls) > counted_before
        failed = await counting_failures(self.brief) > failures_before
        assert (counted and not count_fails) or failed

    @rule(
        after_message_start=st.booleans(),
        max_tokens=st.integers(min_value=1, max_value=400),
    )
    def client_disconnect(self, after_message_start, max_tokens):
        """The reader goes away mid-call. The call in flight is the one this
        rule starts: Hypothesis drives rules one after another, so a call
        left open across a rule boundary would hold the Brief's lock and the
        next rule would wait on it."""
        self.run(self._client_disconnect(after_message_start, max_tokens))

    async def _client_disconnect(self, after_message_start, max_tokens):
        self.upstream.count_status = 200
        self.upstream.events = text_stream(MODEL, "ok", output_tokens=5, input_tokens=5)
        consumed_before = len(self.tree.consumed)

        call = body(max_tokens=max_tokens, messages=list(self.messages), stream=True)
        response = await handle(self.gateway, request_for(call, self.headers))
        if response.status_code != 200:
            await self.gateway.drain()
            assert len(self.tree.consumed) == consumed_before
            return

        if after_message_start:
            async for _ in response.body_iterator:
                break
        await response.body_iterator.aclose()
        await self.gateway.drain()

        rows = await log_rows(self.brief, events=("response", "cut", "upstream_error"))
        if after_message_start:
            # Bytes arrived, so the call is closed and charged. Whether it
            # closes as a cut or as a response depends on how much of the
            # stream had already landed: the in-process transport hands the
            # whole body over in one chunk, so a reader that stops after the
            # first chunk has in fact received a complete message. What the
            # ledger owes either way is one incurred consume, which is what
            # P1 is about; the reserve charge on a genuinely truncated
            # stream is P2's and task 7's, where the provider is a socket.
            assert rows[-1][0] in ("response", "cut")
            assert len(self.tree.consumed) == consumed_before + 1
            assert self.tree.consumed[-1][2] is True
            if rows[-1][0] == "cut":
                assert rows[-1][4]["charged_reserved"] is True
        else:
            # Nothing was generated that this gateway saw, so nothing is
            # consumed.
            assert rows[-1][0] == "upstream_error"
            assert rows[-1][2] == "client_disconnect"
            assert len(self.tree.consumed) == consumed_before

    @rule()
    def bump_generation(self):
        """A stop fences the objective. The Brief's token carries the old
        generation from then on, so every later call is stale."""
        self.tree.generations[self.brief] += 1

    # -- invariants ----------------------------------------------------------

    @invariant()
    def the_ledger_conserves(self):
        consumed = sum(amount.usd_micros for _, amount, _ in self.tree.consumed)
        shortfalls = sum(short for _, short in self.tree.overruns)
        left = self.tree.allocations[self.brief]
        assert consumed + left == BUDGET + shortfalls
        if shortfalls:
            # Once anything overran the allocation is clamped at zero, so
            # what was consumed is the allocation plus every shortfall.
            assert left == 0 and consumed == BUDGET + shortfalls
        assert all(incurred for _, _, incurred in self.tree.consumed)

    @invariant()
    def every_forwarded_call_fit(self):
        assert all(
            reserve(estimate, max_tokens, PRICES).usd_micros <= remaining
            for estimate, max_tokens, remaining in self.forwarded
        )

    @invariant()
    def overrun_rows_match_shortfalls(self):
        rows = self.run(log_rows(self.brief, events=("response", "cut")))
        marked = [r for r in rows if r[2] == "overrun"]
        assert len(marked) == len(self.tree.overruns)

    @invariant()
    def every_request_is_closed(self):
        assert self.run(unclosed_requests(self.brief)) == []

    @invariant()
    def the_lock_is_free(self):
        assert not self.gateway.lock(self.brief).locked()

    def teardown(self):
        self.run(self.gateway.drain())
        self.run(self.gateway.aclose())
        self.loop.close()


TestBudgetLedger = BudgetLedger.TestCase
TestBudgetLedger.settings = MACHINE


# ---------------------------------------------------------------------------
# P2: revoke is final


class RevokeIsFinal(RuleBasedStateMachine):
    """Calls and one revoke, interleaved, with the provider pausing between
    chunks so a call is genuinely in flight when the revoke lands."""

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.upstream = FakeUpstream(count=COUNTED)
        self.upstream.events = text_stream(
            MODEL, "hello", output_tokens=20, input_tokens=20
        )
        self.upstream.chunk_delay = 0.005
        self.server = None
        upstream_url = self.loop.run_until_complete(self._serve())
        self.tree = FakeTree()
        self.gateway = Gateway(
            provider_key="not-a-key",
            connect=connect,
            tree=self.tree,
            upstream=upstream_url,
        )
        self.space = f"space-{uuid.uuid4().hex[:8]}"
        self.brief = uuid.uuid4().hex
        self.tree.add(self.brief, 100_000_000, generation=1)
        self.headers = {"x-api-key": self.run(self._mint())}
        self.revoked = False
        self.inflight = None
        self.upstream_at_revoke = None
        self.consumed_at_revoke = None
        self.cut_call = None
        self.responses_at_revoke: list[int] = []

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    async def _serve(self) -> str:
        import uvicorn

        config = uvicorn.Config(
            self.upstream, host="127.0.0.1", port=0, log_level="error"
        )
        self.server = uvicorn.Server(config)
        self.serving = asyncio.ensure_future(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def _mint(self) -> str:
        async with await connect() as conn:
            token = await self.gateway.issue_token(
                conn,
                brief_id=self.brief,
                generation=1,
                model_ref=MODEL,
                space=self.space,
            )
            await conn.commit()
        return token.get_secret_value()

    async def _settle(self):
        """Finish the call left in flight. A call holds the Brief's lock
        until it closes, so nothing else can run while one is open."""
        if self.inflight is None:
            return None
        iterator, call = self.inflight
        self.inflight = None
        try:
            async for _ in iterator:
                pass
        finally:
            await iterator.aclose()
        await self.gateway.drain()
        return call

    # -- rules ---------------------------------------------------------------

    @rule()
    def call(self):
        self.run(self._call())

    async def _call(self):
        await self._settle()
        before = len(self.upstream.message_calls)
        request = request_for(
            body(
                max_tokens=64,
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            ),
            self.headers,
        )
        response = await handle(self.gateway, request)
        if self.revoked:
            assert response.status_code == 403
            assert json.loads(response.body)["error"]["type"] == "permission_error"
            # The provider hears nothing for a revoked Brief.
            assert len(self.upstream.message_calls) == before
            return
        assert response.status_code == 200
        async for _ in response.body_iterator:
            break
        self.inflight = (response.body_iterator, self.gateway.state(self.brief).call)

    @rule()
    def revoke(self):
        self.run(self._revoke())

    async def _revoke(self):
        if self.revoked:
            return
        in_flight = None if self.inflight is None else self.inflight[1]
        consumed_before = len(self.tree.consumed)
        async with await connect() as conn:
            await self.gateway.revoke(conn, self.brief)
            await conn.commit()
        # A stop is fence, revoke, and compute stop in one transaction, so
        # the generation has moved by the time the cut call's charge lands.
        self.tree.generations[self.brief] += 1
        self.revoked = True

        call = await self._settle()
        assert call == in_flight
        rows = await log_rows(self.brief)
        if in_flight is not None:
            closing = [r for r in rows if r[0] in ("response", "cut")]
            mine = [r for r in closing if r[1] == in_flight]
            assert [r[0] for r in mine] == ["cut"]
            assert mine[0][4]["charged_reserved"] is True
            # The one consume after a revoke is the in-flight call's
            # reserved charge, recorded as incurred though the generation
            # moved underneath it.
            assert len(self.tree.consumed) == consumed_before + 1
            _, amount, incurred = self.tree.consumed[-1]
            assert incurred is True
            assert amount.usd_micros == mine[0][4]["usd_micros"]
        else:
            assert len(self.tree.consumed) == consumed_before
        self.upstream_at_revoke = len(self.upstream.message_calls)
        self.consumed_at_revoke = len(self.tree.consumed)
        self.cut_call = in_flight
        self.responses_at_revoke = [
            r[1] for r in await log_rows(self.brief, events=("response",))
        ]

    # -- invariants ----------------------------------------------------------

    @invariant()
    def nothing_reaches_the_provider_after_a_revoke(self):
        if not self.revoked:
            return
        assert len(self.upstream.message_calls) == self.upstream_at_revoke
        assert len(self.tree.consumed) == self.consumed_at_revoke

    @invariant()
    def every_refusal_after_a_revoke_says_revoked(self):
        if not self.revoked:
            return
        rows = self.run(log_rows(self.brief, events=("refused",)))
        assert all(r[2] == "revoked" for r in rows)

    @invariant()
    def the_call_cut_by_the_revoke_never_answered(self):
        if not self.revoked:
            return
        rows = self.run(log_rows(self.brief, events=("response",)))
        # Calls that closed before the revoke are answers and stay answers.
        # The call the revoke caught is not among them, and no new answer
        # joins them afterwards.
        assert [r[1] for r in rows] == self.responses_at_revoke
        assert self.cut_call not in [r[1] for r in rows]

    def teardown(self):
        self.run(self._settle())
        self.run(self.gateway.drain())
        self.run(self.gateway.aclose())
        self.server.should_exit = True
        self.run(self.serving)
        self.loop.close()


TestRevokeIsFinal = RevokeIsFinal.TestCase
TestRevokeIsFinal.settings = MACHINE


# ---------------------------------------------------------------------------
# P5: the log is the trace


def reconstruct(records: list[dict]) -> list[tuple[str, dict, str]]:
    """Tool call sequence from the gateway log alone: tool_use blocks from each
    response, results from the tool_result blocks of the next request."""
    # Lifted verbatim from spikes/03-gateway-kill/run.py::reconstruct, which
    # cannot be imported: it is a script whose module-level imports pull in
    # the spike's own gateway and the provider SDK.
    reqs = {r["call"]: r for r in records if r["event"] == "request"}
    resps = {r["call"]: r for r in records if r["event"] == "response"}
    seq = []
    for call in sorted(resps):
        uses = [b for b in resps[call]["content"] if b.get("type") == "tool_use"]
        nxt = reqs.get(call + 1)
        results = {}
        if nxt:
            last = nxt["body"]["messages"][-1]
            if isinstance(last["content"], list):
                for blk in last["content"]:
                    if blk.get("type") == "tool_result":
                        results[blk["tool_use_id"]] = blk["content"]
        for u in uses:
            seq.append(
                (u["name"], u["input"], results.get(u["id"], "<no result in log>"))
            )
    return seq


def tool_use_stream(model: str, tool_id: str, name: str, arguments: dict) -> list[dict]:
    """A complete stream whose one content block is a tool call."""
    return [
        message_start(model, input_tokens=20),
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": tool_id,
                "name": name,
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(arguments),
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    ]


async def records_from_log(brief: str) -> list[dict]:
    """The reader: `gateway_log` joined to `request_bodies`, in the shape
    spike 03's `reconstruct` reads.

    The log keeps no response content, by design: it holds what the model
    was asked for, stored by hash. A response's content is therefore read
    back from the assistant message the next request carries, which is the
    only place the conversation records what the model said.
    """
    async with await connect() as conn:
        rows = await (
            await conn.execute(
                "SELECT l.event, l.call, b.body FROM gateway_log l "
                "LEFT JOIN request_bodies b ON b.sha256 = l.request_sha256 "
                "WHERE l.brief_id = %s AND l.event IN ('request', 'response') "
                "ORDER BY l.id",
                (brief,),
            )
        ).fetchall()

    bodies = {call: body for event, call, body in rows if event == "request"}
    records: list[dict] = [
        {"event": "request", "call": call, "body": body}
        for call, body in sorted(bodies.items())
    ]
    for event, call, _ in rows:
        if event != "response":
            continue
        nxt = bodies.get(call + 1)
        content: list = []
        if nxt is not None:
            appended = nxt["messages"][len(bodies[call]["messages"]) :]
            if appended and appended[0].get("role") == "assistant":
                content = appended[0].get("content") or []
        records.append({"event": "response", "call": call, "content": content})
    return records


TOOL_NAME = st.sampled_from(["read_file", "write_file", "run_tests", "search"])
TOOL_INPUT = st.dictionaries(
    st.sampled_from(["path", "query", "limit"]),
    st.one_of(
        st.text(
            alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=8
        ),
        st.integers(min_value=0, max_value=100),
    ),
    max_size=3,
)
TOOL_RESULT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=20
)
CONVERSATION = st.lists(
    st.tuples(TOOL_NAME, TOOL_INPUT, TOOL_RESULT), min_size=1, max_size=6
)


# Store-backed, and every example drives up to seven calls through the real
# route, so this runs at the count the memory build used for its
# store-backed properties rather than at the pure 2,000.
@settings(max_examples=50, deadline=None)
@given(turns=CONVERSATION)
def test_trace_rebuilt_from_log(turns):
    """P5. The Verifier never reads this log and the worker keeps no record
    of its own, so what the model was asked to do has to be rebuildable
    from the gateway's rows alone."""

    async def go():
        upstream = FakeUpstream(count=COUNTED)
        tree = FakeTree()
        gateway = Gateway(
            provider_key="not-a-key",
            connect=connect,
            tree=tree,
            client=upstream.client(),
        )
        space = f"space-{uuid.uuid4().hex[:8]}"
        brief = uuid.uuid4().hex
        tree.add(brief, 100_000_000, generation=1)
        async with await connect() as conn:
            token = await gateway.issue_token(
                conn, brief_id=brief, generation=1, model_ref=MODEL, space=space
            )
            await conn.commit()
        headers = {"x-api-key": token.get_secret_value()}

        try:
            trace = await _drive(gateway, upstream, headers, turns)
            rebuilt = reconstruct(await records_from_log(brief))
            assert rebuilt == trace
        finally:
            await gateway.drain()
            await gateway.aclose()

    run(go())


async def _drive(gateway, upstream, headers, turns) -> list[tuple[str, dict, str]]:
    """A scripted client: it asks, the model calls a tool, it answers with
    the result, and the last turn is plain text. Exactly the shape the
    worker's loop produces."""
    messages: list = [{"role": "user", "content": "go"}]
    trace: list[tuple[str, dict, str]] = []

    for index, (name, arguments, result) in enumerate(turns):
        tool_id = f"toolu_{index}"
        upstream.events = tool_use_stream(MODEL, tool_id, name, arguments)
        response = await handle(
            gateway,
            request_for(body(max_tokens=64, messages=list(messages)), headers),
        )
        assert response.status_code == 200
        message = json.loads(response.body)
        messages.append({"role": "assistant", "content": message["content"]})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    }
                ],
            }
        )
        trace.append((name, arguments, result))

    # The closing turn: the last tool call needs a following request for its
    # result to be in the log at all.
    upstream.events = text_stream(MODEL, "done", output_tokens=6, input_tokens=20)
    response = await handle(
        gateway, request_for(body(max_tokens=64, messages=list(messages)), headers)
    )
    assert response.status_code == 200
    await gateway.drain()
    return trace
