"""Catchup transcript rendering of outbound polls (#2701, Task 11).

spike-4 split this problem in two. `_has_valor_reply_after` is position and
`is_valor` based and never inspects text, so it already suppresses the recovery
enqueue correctly for a text-less poll — that half needs no change. What breaks
is *rendering*: `read_thread` does `m.text or ""`, `_render_transcript` then
emits a bare `"Valor: "`, and `sweep_chat` skips empty-text messages before
judging. The judge sees a blank utterance where the question should be and
re-asks a question already on screen.
"""

from unittest.mock import MagicMock

from telethon.tl.types import MessageMediaPoll, Poll, PollAnswer, TextWithEntities

from bridge.agent_catchup import _poll_transcript_text


def _text(value: str) -> TextWithEntities:
    return TextWithEntities(text=value, entities=[])


def _poll_message(question: str, options: list[str]):
    message = MagicMock()
    message.media = MessageMediaPoll(
        poll=Poll(
            id=1,
            question=_text(question),
            answers=[PollAnswer(text=_text(o), option=bytes([i])) for i, o in enumerate(options)],
        ),
        results=MagicMock(),
    )
    return message


class TestPollTranscriptRendering:
    def test_poll_renders_as_question_and_options(self):
        message = _poll_message("Which approach?", ["A", "B", "Other: wait for followup message"])
        rendered = _poll_transcript_text(message)
        assert "Which approach?" in rendered
        assert "1. A" in rendered
        assert "3. Other: wait for followup message" in rendered

    def test_poll_does_not_render_as_a_blank_line(self):
        """The whole point: a bare "Valor: " tells the judge nothing was said."""
        message = _poll_message("Which approach?", ["A", "B"])
        assert _poll_transcript_text(message).strip() != ""

    def test_non_poll_media_renders_empty(self):
        """Only polls change. Every other media type keeps today's behavior."""
        message = MagicMock()
        message.media = object()
        assert _poll_transcript_text(message) == ""

    def test_message_with_no_media_renders_empty(self):
        message = MagicMock()
        message.media = None
        assert _poll_transcript_text(message) == ""

    def test_malformed_poll_never_raises(self):
        """Transcript rendering is best-effort and must not break catchup."""
        message = MagicMock()
        message.media = MessageMediaPoll(poll=None, results=MagicMock())
        assert _poll_transcript_text(message) == ""

    def test_text_message_is_unaffected_by_the_or_chain(self):
        """`m.text or "" or _poll_transcript_text(m)` must not shadow real text."""
        message = _poll_message("Poll question", ["A", "B"])
        message.text = "actual text"
        assert (message.text or "" or _poll_transcript_text(message)) == "actual text"
