"""The SSE parser against the provider's own accumulator. Plan 04 task 4.

P3, stream parity: for any generated SSE sequence, a complete stream parses
to the scripted usage field for field and assembles to the Message the
Anthropic SDK's accumulator builds from the same bytes; a stream truncated
before `message_delta` is charged at the reserve.

The SDK is the oracle rather than a second implementation here: it is the
same code every client of the gateway runs, so a difference between it and
this parser is a difference a caller would see.
"""

import json

import pydantic
import pytest
from anthropic.lib.streaming._messages import accumulate_event
from anthropic.types import Message, RawMessageStreamEvent
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from gateway.sse import (
    USAGE_FIELDS,
    assemble,
    iter_events,
    parse,
    split_events,
    usage_of,
)

MODEL = "claude-haiku-4-5-20251001"
PURE = settings(
    max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)

names = st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=4)
json_values = st.integers(-1000, 1000) | st.text(max_size=8) | st.booleans()


def render(events: list[dict]) -> bytes:
    """The bytes the provider sends: one `event:` line, one `data:` line,
    a blank line between."""
    out = b""
    for event in events:
        out += f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
    return out


@st.composite
def scripts(draw, allow_truncation: bool = True):
    """A scripted stream: the usage the provider reports, zero to four
    content blocks with their deltas, and whether it finishes."""
    start_usage = {
        "input_tokens": draw(st.integers(0, 100_000)),
        "output_tokens": draw(st.integers(0, 8)),
        "cache_creation_input_tokens": draw(st.integers(0, 100_000)),
        "cache_read_input_tokens": draw(st.integers(0, 100_000)),
    }
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_"
                + draw(st.text(alphabet="0123456789abcdef", min_size=4, max_size=8)),
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": dict(start_usage),
            },
        }
    ]
    for index in range(draw(st.integers(0, 4))):
        if draw(st.booleans()):
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
            for piece in draw(st.lists(st.text(max_size=12), max_size=4)):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": piece},
                    }
                )
        else:
            arguments = draw(st.dictionaries(names, json_values, max_size=3))
            events.append(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_" + str(index),
                        "name": draw(names),
                        "input": {},
                    },
                }
            )
            raw = json.dumps(arguments)
            cut = draw(st.integers(0, len(raw)))
            for piece in (raw[:cut], raw[cut:]):
                events.append(
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": piece},
                    }
                )
        events.append({"type": "content_block_stop", "index": index})

    final = dict(start_usage)
    final["output_tokens"] = draw(st.integers(0, 4096))
    tail = [
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": draw(
                    st.sampled_from(["end_turn", "max_tokens", "tool_use"])
                ),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": final["output_tokens"]},
        },
        {"type": "message_stop"},
    ]
    if allow_truncation and draw(st.booleans()):
        keep = draw(st.integers(1, len(events)))
        return events[:keep], start_usage, True
    return events + tail, final, False


def sdk_message(data: bytes) -> dict:
    """What the Anthropic SDK's own accumulator builds from these bytes."""
    snapshot, buffers = None, {}
    for event in iter_events(data):
        snapshot = accumulate_event(
            event=pydantic.TypeAdapter(RawMessageStreamEvent).validate_python(event),
            current_snapshot=snapshot,
            json_bufs=buffers,
        )
    return Message.model_validate(snapshot.model_dump()).model_dump(mode="json")


# ---------------------------------------------------------------------------
# P3, stream parity


@PURE
@given(scripts())
def test_sse_usage_parity(script):
    """Complete: the four token fields are the scripted ones. Truncated:
    the reserve, marked."""
    events, expected_usage, truncated = script
    parsed = parse(iter_events(render(events)))

    if not truncated:
        assert parsed.complete
        assert {k: parsed.usage.get(k) for k in USAGE_FIELDS} == expected_usage
        usage = usage_of(parsed, max_tokens=4096, estimated_input=7)
        assert not usage.charged_reserved
        assert usage.output_tokens == expected_usage["output_tokens"]
        return

    assert not parsed.complete
    usage = usage_of(parsed, max_tokens=4096, estimated_input=7)
    assert usage.charged_reserved
    assert usage.output_tokens == 4096
    assert usage.input_tokens == expected_usage["input_tokens"]


@PURE
@given(scripts(allow_truncation=False))
def test_sse_assembly_matches_stream(script):
    """`assemble` equals the Message the SDK's accumulator builds from the
    same bytes."""
    events, _, truncated = script
    assert not truncated
    data = render(events)
    ours = Message.model_validate(assemble(parse(iter_events(data))))
    assert ours.model_dump(mode="json") == sdk_message(data)


# ---------------------------------------------------------------------------
# Framing and folding, by example


def test_a_partial_trailing_event_is_kept_for_the_next_chunk():
    events, rest = split_events('data: {"type": "ping"}\n\ndata: {"type": "mes')
    assert events == [{"type": "ping"}] and rest == 'data: {"type": "mes'
    events, rest = split_events(rest + 'sage_stop"}\n\n')
    assert events == [{"type": "message_stop"}] and rest == ""


def test_a_multi_line_data_payload_is_one_event():
    assert iter_events('event: x\ndata: {"type":\ndata: "ping"}\n\n') == [
        {"type": "ping"}
    ]


def test_the_cut_error_event_parses_and_folds_to_nothing():
    """The gateway appends this itself on a revoke; the parser keeps what
    arrived before it rather than raising (plan 04, step 6)."""
    data = (
        b'event: message_start\ndata: {"type": "message_start", "message": '
        b'{"id": "msg_1", "usage": {"input_tokens": 5}}}\n\n'
        b'event: error\ndata: {"type": "error", "error": {"type": '
        b'"permission_error", "message": "brief revoked"}}\n\n'
    )
    parsed = parse(iter_events(data))
    assert parsed.started and not parsed.complete
    assert parsed.usage == {"input_tokens": 5}


def test_a_truncated_tool_use_block_carries_empty_arguments():
    data = render(
        [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "t",
                    "name": "f",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"a": 1'},
            },
        ]
    )
    assert parse(iter_events(data)).blocks[0]["input"] == {}


def test_a_delta_for_a_block_that_never_started_is_ignored():
    data = render(
        [
            {
                "type": "content_block_delta",
                "index": 3,
                "delta": {"type": "text_delta", "text": "x"},
            }
        ]
    )
    assert parse(iter_events(data)).blocks == []


def test_a_null_usage_field_is_not_a_zero():
    """A provider that reports a field as null reported nothing, so the
    earlier value stands."""
    data = render(
        [
            {
                "type": "message_start",
                "message": {"id": "m", "usage": {"input_tokens": 9}},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 3, "input_tokens": None},
            },
        ]
    )
    parsed = parse(iter_events(data))
    assert parsed.usage == {"input_tokens": 9, "output_tokens": 3}


def test_the_parse_result_unpacks_as_the_plans_triple():
    usage, blocks, stop_reason = parse(
        iter_events(
            render(
                [
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "max_tokens"},
                        "usage": {"output_tokens": 2},
                    }
                ]
            )
        )
    )
    assert (
        usage == {"output_tokens": 2} and blocks == [] and stop_reason == "max_tokens"
    )


def test_a_stream_that_never_started_is_charged_the_estimate():
    usage = usage_of(parse([]), max_tokens=512, estimated_input=64, usd_micros=11)
    assert usage.charged_reserved
    assert usage.input_tokens == 64 and usage.output_tokens == 512
    assert usage.usd_micros == 11


@pytest.mark.parametrize("payload", ["not json", "[1, 2]"])
def test_a_data_line_that_is_not_an_object_is_dropped(payload):
    assert iter_events(f"data: {payload}\n\n") == []
