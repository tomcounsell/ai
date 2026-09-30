"""Poll outbox payload and option encoding (#2701, Tasks 4/5).

The correlation key is the piece the whole Race-6 mitigation rests on, and its
single-producer property is the kind of thing that decays silently: nothing
breaks when a second site starts minting one, until an orphaned poll cannot be
adopted months later.
"""

import ast
import inspect
import uuid

import pytest

from agent.output_handler import build_telegram_poll_outbox_payload, render_poll_as_text
from bridge.response import (
    _OPTION_MAX_BYTES,
    correlation_matches,
    decode_option,
    encode_option,
)


class TestPayload:
    def test_shape(self):
        payload = build_telegram_poll_outbox_payload(
            chat_id="-1003449100931",
            question="Which approach?",
            options=["A", "B"],
            reply_to=42,
            session_id="sess-1",
        )
        assert payload["type"] == "poll"
        assert payload["chat_id"] == "-1003449100931"
        assert payload["question"] == "Which approach?"
        assert payload["options"] == ["A", "B"]
        assert payload["reply_to"] == 42
        assert payload["session_id"] == "sess-1"
        assert "timestamp" in payload

    def test_poll_id_hint_is_stamped_unconditionally(self):
        payload = build_telegram_poll_outbox_payload("-100", "Q", ["A"], None, "s")
        assert len(payload["poll_id_hint"]) == 32
        int(payload["poll_id_hint"], 16)  # valid hex, or this raises

    def test_two_calls_produce_different_hints(self):
        a = build_telegram_poll_outbox_payload("-100", "Q", ["A"], None, "s")
        b = build_telegram_poll_outbox_payload("-100", "Q", ["A"], None, "s")
        assert a["poll_id_hint"] != b["poll_id_hint"]

    def test_session_type_is_not_stamped_into_the_payload(self):
        """A queued payload would outlive a session's real type.

        The relay re-reads eligibility at send time instead.
        """
        payload = build_telegram_poll_outbox_payload("-100", "Q", ["A"], None, "s")
        assert "session_type" not in payload

    def test_hint_has_exactly_one_producer(self):
        """A second minting site breaks the exact match against the provisional
        row, and does so silently."""
        import agent.output_handler as mod

        tree = ast.parse(inspect.getsource(mod))
        uuid4_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "uuid4"
        ]
        assert len(uuid4_calls) == 1, "poll_id_hint must be minted in exactly one place"


class TestOptionEncoding:
    """The ceiling is 8 bytes, measured — not the 100 every reference claims.

    Telegram rejects 9 bytes at the wire with `A poll option used invalid data
    (the data may be too long)` and no local signal, so a too-long option ships
    a poll that simply never sends. Verified empirically during the Task 1 gate.
    """

    def test_ceiling_is_eight_bytes(self):
        assert _OPTION_MAX_BYTES == 8

    @pytest.mark.parametrize("index", range(10))
    def test_encoding_fits_the_ceiling_at_every_permitted_index(self, index):
        """2..10 options is what the CLI permits, so indices 0..9."""
        raw = encode_option(index, uuid.uuid4().hex)
        assert len(raw) == _OPTION_MAX_BYTES

    @pytest.mark.parametrize("index", range(10))
    def test_round_trip_recovers_the_index(self, index):
        hint = uuid.uuid4().hex
        decoded_index, prefix = decode_option(encode_option(index, hint))
        assert decoded_index == index
        assert correlation_matches(prefix, hint)

    def test_options_are_unique_per_index(self):
        """Uniqueness per option is Telegram's only constraint on `option`."""
        hint = uuid.uuid4().hex
        encoded = [encode_option(i, hint) for i in range(10)]
        assert len(set(encoded)) == 10

    def test_bare_form_is_parseable(self):
        """The probe-only affordance with no correlation id."""
        assert decode_option(encode_option(3)) == (3, None)

    def test_correlation_matches_rejects_a_different_hint(self):
        _index, prefix = decode_option(encode_option(0, uuid.uuid4().hex))
        assert correlation_matches(prefix, uuid.uuid4().hex) is False

    def test_correlation_matches_is_false_on_missing_inputs(self):
        assert correlation_matches(None, "a" * 32) is False
        assert correlation_matches("abcd", None) is False

    @pytest.mark.parametrize("raw", [None, b"", b"\xff\xff\xff"])
    def test_undecodable_input_never_raises(self, raw):
        """This runs against whatever Telegram hands back."""
        result = decode_option(raw)
        assert isinstance(result, tuple) and len(result) == 2

    def test_index_beyond_one_byte_is_rejected(self):
        with pytest.raises(ValueError):
            encode_option(256, uuid.uuid4().hex)


class TestTextRendering:
    def test_numbered_list_shape(self):
        assert render_poll_as_text("Q?", ["a", "b"]) == "Q?\n\n1. a\n2. b"

    def test_shared_by_every_degradation_path(self):
        """One rendering of a question as prose, not several.

        The CLI's ineligible branch, the relay's re-check branch and the relay's
        terminal-failure re-enqueue must agree, or the fallback drifts from what
        a reader expected to see.
        """
        import bridge.telegram_relay as relay
        import tools.ask_poll as cli

        for mod in (relay, cli):
            assert "render_poll_as_text" in inspect.getsource(mod)
