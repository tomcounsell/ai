"""The three Redis wires between the bridge and the worker are typed (#3183).

The models exist so a field renamed on one side of a process boundary fails
loudly instead of silently, and so a malformed entry becomes a dead letter
instead of a warning and a dropped message.

The single most important case in this file is ``type_none``: an ordinary
text message carries no ``type`` key at all, and it is the highest-volume path
in the system. A ``Literal[...]`` without ``| None`` would dead-letter every
one of them.
"""

import json

import pytest
from pydantic import ValidationError

from bridge.wire_schemas import NotifyPayload, OutboxPayload, SteeringPayload, dump


class TestOutboxPayload:
    def test_type_none_parses(self):
        """A plain text message has no `type` key and must parse."""
        payload = OutboxPayload.model_validate_json(
            '{"chat_id": "123", "text": "hello", "session_id": "s1"}'
        )
        assert payload.type is None
        assert payload.text == "hello"

    def test_type_none_is_explicitly_accepted_too(self):
        assert OutboxPayload.model_validate_json('{"type": null, "text": "hi"}').type is None

    @pytest.mark.parametrize("kind", ["reaction", "custom_emoji_message", "poll"])
    def test_every_dispatchable_type_parses(self, kind):
        assert OutboxPayload.model_validate_json(json.dumps({"type": kind})).type == kind

    def test_an_unknown_type_is_refused(self):
        """This is what replaced the KNOWN_MESSAGE_TYPES membership check."""
        with pytest.raises(ValidationError):
            OutboxPayload.model_validate_json('{"type": "nope"}')

    def test_unknown_fields_are_kept(self):
        """Forward compatibility: a newer writer must not break an older reader.

        The relay also depends on this: it mutates `_relay_attempts` on the
        dict it dumps back out.
        """
        payload = OutboxPayload.model_validate_json('{"text": "hi", "_relay_attempts": 2}')
        assert payload.model_dump(exclude_none=True)["_relay_attempts"] == 2

    def test_a_future_version_still_parses(self):
        assert OutboxPayload.model_validate_json('{"v": 2, "text": "hi"}').v == 2

    def test_dump_omits_unset_keys(self):
        """The wire shape must match the hand-built dict it replaced."""
        emitted = json.loads(dump(OutboxPayload(chat_id="1", text="hi", session_id="s")))
        assert "file_paths" not in emitted
        assert "correlation_id" not in emitted
        assert "type" not in emitted
        assert emitted["v"] == 1

    def test_correlation_id_rides_the_payload(self):
        emitted = json.loads(dump(OutboxPayload(text="hi", correlation_id="cid-1")))
        assert emitted["correlation_id"] == "cid-1"


class TestSteeringPayload:
    def test_required_fields(self):
        with pytest.raises(ValidationError):
            SteeringPayload.model_validate_json('{"text": "steer"}')

    def test_round_trip(self):
        emitted = dump(SteeringPayload(text="steer", sender="tom", timestamp=1.5))
        parsed = SteeringPayload.model_validate_json(emitted)
        assert (parsed.text, parsed.sender, parsed.timestamp) == ("steer", "tom", 1.5)
        assert parsed.is_abort is False

    def test_target_agent_is_optional_and_omitted_when_unset(self):
        assert "target_agent" not in json.loads(
            dump(SteeringPayload(text="t", sender="s", timestamp=1.0))
        )


class TestNotifyPayload:
    def test_round_trip(self):
        parsed = NotifyPayload.model_validate_json(
            dump(NotifyPayload(worker_key="valor", session_id="s1", is_project_keyed=True))
        )
        assert parsed.worker_key == "valor"
        assert parsed.is_project_keyed is True

    def test_a_non_json_payload_is_refused(self):
        with pytest.raises(ValidationError):
            NotifyPayload.model_validate_json("not json at all")
