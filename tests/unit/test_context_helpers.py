"""Unit tests for bridge/context.py helper functions.

Covers the implicit-context heuristic (`references_prior_context`), its
companion `matched_context_patterns`, and the `_build_completed_resume_text`
layered preamble. See `docs/plans/reply_thread_context_hydration.md` —
implementation notes IN-3 and IN-12.
"""

from __future__ import annotations

import pytest

from bridge.context import (
    DEICTIC_CONTEXT_PATTERNS,
    REPLY_THREAD_CONTEXT_HEADER,
    STATUS_QUESTION_PATTERNS,
    matched_context_patterns,
    references_prior_context,
)


class TestReplyThreadContextHeader:
    """The canonical header constant must match the string used by format_reply_chain."""

    def test_header_is_stable_string(self):
        assert REPLY_THREAD_CONTEXT_HEADER == "REPLY THREAD CONTEXT"

    def test_format_reply_chain_uses_the_constant(self):
        from bridge.context import format_reply_chain

        # #2732: chain dicts now carry a `media` key. The header contract must
        # hold with the key absent (legacy dict shape), None, and populated.
        chains = [
            [{"sender": "Tom", "content": "hello", "message_id": 1, "date": None}],
            [{"sender": "Tom", "content": "hello", "message_id": 1, "date": None, "media": None}],
            [
                {
                    "sender": "Tom",
                    "content": "hello",
                    "message_id": 1,
                    "date": None,
                    "media": {
                        "kind": "resolved",
                        "filename": "a.pdf",
                        "media_type": "document",
                        "local_path": "/tmp/a.pdf",
                        "reason": None,
                    },
                }
            ],
        ]
        for chain in chains:
            formatted = format_reply_chain(chain)
            # Idempotency guard in agent_session_queue relies on this substring.
            assert REPLY_THREAD_CONTEXT_HEADER in formatted
            assert formatted.count(REPLY_THREAD_CONTEXT_HEADER) == 1


class TestReferencesPriorContextNegativeGuards:
    """IN-12: locked behavior for None / empty / whitespace-only / non-string."""

    def test_none_returns_false(self):
        assert references_prior_context(None) is False

    def test_empty_string_returns_false(self):
        assert references_prior_context("") is False

    def test_whitespace_only_returns_false(self):
        assert references_prior_context("   ") is False
        assert references_prior_context("\t\n  ") is False

    def test_non_string_returns_false(self):
        assert references_prior_context(123) is False
        assert references_prior_context(["did we fix it?"]) is False
        assert references_prior_context({"text": "the bug"}) is False

    def test_does_not_raise_on_weird_input(self):
        # Must not raise for any input shape
        for weird in (0, 0.0, b"bytes", object()):
            references_prior_context(weird)  # must not raise


class TestReferencesPriorContextDeictic:
    """Deictic/back-reference phrases should trigger the directive."""

    @pytest.mark.parametrize(
        "text",
        [
            "did we get that fixed?",
            "did we ship the fix?",
            "the bug is still broken",
            "that issue is blocking release",
            "still failing in CI",
            "still broken on main",
            "we fixed the repo yesterday",
            "we shipped the change",
            "we merged the PR",
            "we resolved it",
            "last time we talked about this",
            "as I mentioned earlier",
            "as I said before",
            "what about that ticket",
            "what about the PR",
            "what about the pull request",
        ],
    )
    def test_matches_deictic_phrase(self, text):
        assert references_prior_context(text) is True, f"expected match for {text!r}"


class TestReferencesPriorContextStatusQuestions:
    """Status-question patterns are re-used unchanged — coverage sanity check."""

    @pytest.mark.parametrize(
        "text",
        [
            "what are you working on?",
            "what's the status?",
            "any updates?",
            "how's it going",
            "catch me up",
        ],
    )
    def test_status_questions_still_trigger(self, text):
        assert references_prior_context(text) is True


class TestReferencesPriorContextNegatives:
    """High-precision intent: self-contained statements must NOT trigger."""

    @pytest.mark.parametrize(
        "text",
        [
            "hello world",
            "please add logging to the auth module",
            "here is the revised plan",
            "thanks!",
            "run the tests please",
            "create a new issue about caching",
        ],
    )
    def test_self_contained_does_not_trigger(self, text):
        assert references_prior_context(text) is False


class TestMatchedContextPatterns:
    """Companion helper must expose the specific patterns that hit, for audit logs."""

    def test_returns_list(self):
        assert isinstance(matched_context_patterns("did we fix it?"), list)

    def test_empty_for_negative_input(self):
        assert matched_context_patterns(None) == []
        assert matched_context_patterns("") == []
        assert matched_context_patterns("   ") == []
        assert matched_context_patterns("hello world") == []

    def test_returns_pattern_strings_on_match(self):
        patterns = matched_context_patterns("did we fix the bug?")
        assert len(patterns) >= 1
        assert all(isinstance(p, str) for p in patterns)

    def test_multiple_matches_return_multiple_entries(self):
        # "did we" + "the bug" should both match
        patterns = matched_context_patterns("did we ship the bug fix?")
        assert len(patterns) >= 2


class TestHeuristicListSize:
    """IN-3: resist expansion -- keep pattern lists small."""

    def test_deictic_list_is_small(self):
        # Cap at ~10 per IN-3
        assert len(DEICTIC_CONTEXT_PATTERNS) <= 10

    def test_status_list_is_small(self):
        assert len(STATUS_QUESTION_PATTERNS) <= 12


class TestBuildCompletedResumeText:
    """The bridge helper must layer context_summary + reply_chain_context stably."""

    def _fake_session(self, summary):
        class FakeSession:
            context_summary = summary

        return FakeSession()

    def test_summary_only_matches_legacy_format(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        result = _build_completed_resume_text(self._fake_session("did work"), "hi")
        assert result == "[Prior session context: did work]\n\nhi"

    def test_empty_reply_chain_is_noop(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        base = _build_completed_resume_text(self._fake_session("did work"), "hi")
        with_empty = _build_completed_resume_text(
            self._fake_session("did work"), "hi", reply_chain_context=""
        )
        with_none = _build_completed_resume_text(
            self._fake_session("did work"), "hi", reply_chain_context=None
        )
        assert base == with_empty == with_none

    def test_none_summary_falls_back_to_generic_sentinel(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        result = _build_completed_resume_text(self._fake_session(None), "hi")
        assert "[Prior session context: This continues a previously completed session.]" in result
        assert result.endswith("hi")

    def test_reply_chain_appears_between_summary_and_follow_up(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        chain_block = "REPLY THREAD CONTEXT (oldest to newest):\nTom: hi\nValor: hello"
        result = _build_completed_resume_text(
            self._fake_session("prior work"), "follow up", reply_chain_context=chain_block
        )
        # Order: summary -> chain -> follow_up
        i_summary = result.index("[Prior session context:")
        i_chain = result.index(REPLY_THREAD_CONTEXT_HEADER)
        i_follow = result.index("follow up")
        assert i_summary < i_chain < i_follow

    def test_exactly_one_header_when_caller_passes_one(self):
        from bridge.telegram_bridge import _build_completed_resume_text

        chain_block = "REPLY THREAD CONTEXT (oldest to newest):\nTom: hi"
        result = _build_completed_resume_text(
            self._fake_session("ctx"), "msg", reply_chain_context=chain_block
        )
        assert result.count(REPLY_THREAD_CONTEXT_HEADER) == 1


class TestFilterToolLogsParity:
    """`bridge.context.filter_tool_logs` must be the canonical version
    in `bridge.response`.

    History: a stale local `def filter_tool_logs` lived in
    `bridge/context.py` and silently diverged from the canonical
    `bridge/response.py` implementation (variation-selector handling,
    backtick-shell echoes, `<5` char length floor — all weaker in the
    stale copy). PR #1077 consolidated `bridge/response.py` but its
    audit only grepped for *imports* of the canonical path, missing
    the orphan `def`. Issue #1359 closes the duplicate and these tests
    are the permanent guard against the same audit miss recurring.
    """

    def test_filter_tool_logs_is_response_canonical(self):
        """Identity assertion: any future re-introduction of a local
        `def filter_tool_logs` in `bridge/context.py` will shadow the
        import and fail this test, breaking CI on the offending PR."""
        import bridge.context
        import bridge.response

        assert bridge.context.filter_tool_logs is bridge.response.filter_tool_logs

    def test_format_reply_chain_drops_variation_selector_and_backtick_echo(self):
        """Through-pipeline assertion: `format_reply_chain` must drop
        U+FE0F-prefixed tool traces (`🛠️ exec:`, `📖 read:`) and
        backtick-wrapped shell echoes from Valor messages.

        Asserts the canonical filter (which handles variation selectors
        and the `_SHELL_COMMAND_HINTS` echo filter) reaches the live
        impact path that feeds `_build_completed_resume_text` at
        `bridge/telegram_bridge.py:1740` and `:2259`.
        """
        from bridge.context import format_reply_chain

        # U+FE0F variation selector after wrench emoji, plus a book emoji,
        # plus a backtick-wrapped shell command echo.
        valor_message = (
            "Here is the analysis you asked for.\n"
            "\U0001f6e0️ exec: ls -la\n"
            "\U0001f4d6 read: bridge/context.py\n"
            "`cd bridge && ls -la`\n"
            "The duplicate definition is at line 104."
        )
        chain = [
            {"sender": "Tom", "content": "what's in bridge/?", "message_id": 1, "date": None},
            {"sender": "Valor", "content": valor_message, "message_id": 2, "date": None},
        ]

        formatted = format_reply_chain(chain)

        # All three filter targets must be absent from the output.
        assert "\U0001f6e0" not in formatted, "wrench emoji tool trace leaked"
        assert "\U0001f4d6" not in formatted, "book emoji tool trace leaked"
        assert "`cd bridge && ls -la`" not in formatted, "backtick shell echo leaked"
        # The meaningful prose must still be present.
        assert "Here is the analysis you asked for." in formatted
        assert "The duplicate definition is at line 104." in formatted

        # #2732: sanitisation must still apply on a composed caption-plus-
        # descriptor line. The descriptor is appended AFTER filter_tool_logs
        # runs, so the traces stay filtered and the descriptor stays intact.
        chain[1]["media"] = {
            "kind": "resolved",
            "filename": "trace.log",
            "media_type": "document",
            "local_path": "/data/media/doc_1_2.log",
            "reason": None,
        }
        composed = format_reply_chain(chain)
        assert "\U0001f6e0" not in composed, "wrench emoji tool trace leaked past descriptor"
        assert "`cd bridge && ls -la`" not in composed, "backtick echo leaked past descriptor"
        assert "Here is the analysis you asked for." in composed
        assert "[attachment: trace.log (document) at machine path /data/media/doc_1_2.log]" in (
            composed
        )

    def test_format_reply_chain_omits_messages_below_length_floor(self):
        """Through-pipeline assertion: when `filter_tool_logs` returns `""`
        because the post-filter remainder is below the `<5` char floor,
        `format_reply_chain` must omit the Valor message entirely
        (the existing `if not content: continue` at `bridge/context.py:486-487`
        handles this once the canonical floor returns `""`).

        This floor is currently UNCOVERED through `format_reply_chain` — the
        existing direct-function tests at
        `tests/integration/test_reply_delivery.py:191-229` and
        `tests/e2e/test_message_pipeline.py:239-246` only exercise
        `filter_tool_logs` in isolation.
        """
        from bridge.context import format_reply_chain

        # After filter_tool_logs runs, only "ok" remains (2 chars, below
        # the `<5` floor) so the canonical version returns "".
        valor_short = "\U0001f6e0️ exec: ls\nok"
        chain = [
            {"sender": "Tom", "content": "run ls", "message_id": 1, "date": None},
            {"sender": "Valor", "content": valor_short, "message_id": 2, "date": None},
            {"sender": "Tom", "content": "thanks", "message_id": 3, "date": None},
        ]

        formatted = format_reply_chain(chain)

        # The Valor message must be omitted entirely.
        assert "ok" not in formatted, "below-floor remainder should not reach the output"
        assert "Valor:" not in formatted, "Valor message should be omitted, not just emptied"
        # The surrounding messages must still be present.
        assert "Tom: run ls" in formatted
        assert "Tom: thanks" in formatted


# =============================================================================
# #2732: reply-chain media descriptor — rendering states, resolver, walk
# =============================================================================


def _descriptor(
    kind="resolved",
    filename="report.pdf",
    media_type="document",
    local_path="/data/media/doc_1_10.pdf",
    reason=None,
):
    from bridge.context import media_descriptor

    return media_descriptor(kind, filename, media_type, local_path, reason)


class TestMediaDescriptorRendering:
    """The three rendering states of format_reply_chain (#2732).

    All chain dicts here are built by hand — the renderer is pure, so these
    need neither Redis nor a Telegram client.
    """

    def test_resolved_with_caption_composes_both(self):
        from bridge.context import format_reply_chain

        chain = [
            {
                "sender": "Hazem",
                "content": "here is the recommendation",
                "message_id": 10,
                "date": None,
                "media": _descriptor(),
            }
        ]
        formatted = format_reply_chain(chain)
        line = next(ln for ln in formatted.splitlines() if ln.startswith("Hazem:"))
        assert "here is the recommendation" in line
        assert "[attachment: report.pdf (document) at machine path /data/media/doc_1_10.pdf]" in (
            line
        ), "caption and descriptor must compose on ONE line"

    def test_resolved_without_caption_renders_descriptor_alone(self):
        from bridge.context import format_reply_chain

        chain = [
            {
                "sender": "Hazem",
                "content": "",
                "message_id": 10,
                "date": None,
                "media": _descriptor(),
            }
        ]
        formatted = format_reply_chain(chain)
        assert (
            "Hazem: [attachment: report.pdf (document) at machine path /data/media/doc_1_10.pdf]"
            in formatted
        ), "caption-less hop must render the descriptor alone, with no leading space"

    def test_text_only_chain_is_byte_identical_with_and_without_media_key(self):
        """Regression fence: a text-only chain renders exactly as before #2732,
        whether the entry omits the media key (legacy shape) or carries
        media=None (new shape)."""
        from bridge.context import format_reply_chain

        legacy = [
            {"sender": "Tom", "content": "hello", "message_id": 1, "date": None},
            {
                "sender": "Valor",
                "content": "hi there, what do you need?",
                "message_id": 2,
                "date": None,
            },
        ]
        new_shape = [dict(entry, media=None) for entry in legacy]
        expected = (
            f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):\n"
            + "-" * 40
            + "\nTom: hello\n\nValor: hi there, what do you need?\n\n"
            + "-" * 40
        )
        assert format_reply_chain(legacy) == expected
        assert format_reply_chain(new_shape) == expected

    @pytest.mark.parametrize(
        "reason",
        [
            "no_record",
            "no_path_recorded",
            "download_error: timeout",
            "file_missing",
            "invalid_path",
            "resolution_error",
        ],
    )
    def test_unreadable_rendering_names_file_and_reason(self, reason):
        from bridge.context import format_reply_chain

        chain = [
            {
                "sender": "Hazem",
                "content": "",
                "message_id": 10,
                "date": None,
                "media": _descriptor(kind="unreadable", local_path=None, reason=reason),
            }
        ]
        formatted = format_reply_chain(chain)
        assert f"[unreadable attachment: report.pdf (document) reason: {reason}]" in formatted

    def test_resolved_and_unreadable_renderings_are_distinguishable(self):
        from bridge.context import format_media_descriptor

        resolved = format_media_descriptor(_descriptor())
        unreadable = format_media_descriptor(
            _descriptor(kind="unreadable", local_path=None, reason="no_record")
        )
        assert resolved != unreadable
        # Each state carries a marker the other never does, so an agent (and a
        # test) can tell them apart without heuristics.
        assert "at machine path" in resolved and "at machine path" not in unreadable
        assert "unreadable attachment:" in unreadable and "unreadable attachment:" not in resolved

    def test_media_literal_appears_in_no_rendering_state(self):
        """The literal `[media]` must appear in no output under any state."""
        from bridge.context import format_reply_chain

        chains = [
            [{"sender": "Tom", "content": "text only", "message_id": 1, "date": None}],
            [
                {
                    "sender": "Tom",
                    "content": "cap",
                    "message_id": 1,
                    "date": None,
                    "media": _descriptor(),
                }
            ],
            [
                {
                    "sender": "Tom",
                    "content": "",
                    "message_id": 1,
                    "date": None,
                    "media": _descriptor(),
                }
            ],
            [
                {
                    "sender": "Tom",
                    "content": "",
                    "message_id": 1,
                    "date": None,
                    "media": _descriptor(kind="unreadable", local_path=None, reason="no_record"),
                }
            ],
            [
                {
                    "sender": "Valor",
                    "content": "",
                    "message_id": 1,
                    "date": None,
                    "media": _descriptor(
                        kind="unreadable",
                        filename=None,
                        media_type=None,
                        local_path=None,
                        reason="resolution_error",
                    ),
                }
            ],
        ]
        for chain in chains:
            assert "[media]" not in format_reply_chain(chain)

    def test_filename_none_renders_unnamed_not_dangling(self):
        from bridge.context import format_media_descriptor

        rendered = format_media_descriptor(
            _descriptor(kind="unreadable", filename=None, local_path=None, reason="no_record")
        )
        assert rendered == "[unreadable attachment: unnamed (document) reason: no_record]"

    def test_media_type_none_renders_generic_label(self):
        from bridge.context import format_media_descriptor

        rendered = format_media_descriptor(_descriptor(media_type=None))
        assert rendered == (
            "[attachment: report.pdf (file) at machine path /data/media/doc_1_10.pdf]"
        )

    def test_empty_chain_still_returns_empty_string(self):
        from bridge.context import format_reply_chain

        assert format_reply_chain([]) == ""

    def test_below_floor_valor_hop_with_media_is_retained(self):
        """Sibling of test_format_reply_chain_omits_messages_below_length_floor:
        a Valor hop whose text filters below the <5 floor is RETAINED when it
        carries media — its descriptor is the whole line. This is the case the
        old 7-char "[media]" string was accidentally protecting."""
        from bridge.context import format_reply_chain

        valor_short = "\U0001f6e0️ exec: ls\nok"
        chain = [
            {"sender": "Tom", "content": "run ls", "message_id": 1, "date": None},
            {
                "sender": "Valor",
                "content": valor_short,
                "message_id": 2,
                "date": None,
                "media": _descriptor(
                    kind="unreadable",
                    filename="shot.png",
                    media_type="photo",
                    local_path=None,
                    reason="no_path_recorded",
                ),
            },
            {"sender": "Tom", "content": "thanks", "message_id": 3, "date": None},
        ]
        formatted = format_reply_chain(chain)
        assert "Valor: [unreadable attachment: shot.png (photo) reason: no_path_recorded]" in (
            formatted
        ), "below-floor Valor hop with media must be retained as its descriptor"
        assert "ok" not in formatted, "below-floor text remainder must still be dropped"

    def test_truncation_never_bisects_descriptor_path(self):
        """Truncation measures the human-authored text only; the descriptor
        (and its path) is appended afterwards, intact."""
        from bridge.context import format_reply_chain

        long_caption = "x" * 600  # over the 500-char non-Valor cap
        path = "/data/media/doc_1_10.pdf"
        chain = [
            {
                "sender": "Tom",
                "content": long_caption,
                "message_id": 10,
                "date": None,
                "media": _descriptor(local_path=path),
            },
        ]
        formatted = format_reply_chain(chain)
        assert "x" * 500 + "..." in formatted, "caption must still truncate at 500"
        assert "x" * 501 not in formatted
        assert f"at machine path {path}]" in formatted, "path must survive truncation intact"


class _FakeFile:
    def __init__(self, name):
        self.name = name


class _FakeSender:
    first_name = "Hazem"


def _document_media(filename: str = "report.pdf"):
    """A genuine MessageMediaDocument that get_media_type classifies as
    "document" — the resolver's gate is the repo's own classifier (PR #3146
    review round 2), so descriptor tests must use media it accepts."""
    from types import SimpleNamespace

    from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument

    media = MessageMediaDocument.__new__(MessageMediaDocument)
    media.document = SimpleNamespace(attributes=[DocumentAttributeFilename(file_name=filename)])
    return media


class _FakeChainMsg:
    """Minimal Telethon Message stand-in for the resolver and the chain walk."""

    def __init__(self, id, text="", reply_to=None, media=None, file=None, out=False):
        self.id = id
        self.text = text
        self.reply_to_msg_id = reply_to
        self.media = media
        self.file = file
        self.out = out
        self.date = None

    async def get_sender(self):
        return _FakeSender()


class _FakeChainClient:
    def __init__(self, msgs):
        self._msgs = {m.id: m for m in msgs}

    async def get_messages(self, chat_id, ids=None):
        return self._msgs.get(ids)


def _save_telegram_record(chat_id: int, message_id: int, **fields):
    import time as _time

    from models.telegram import TelegramMessage

    record = TelegramMessage(
        chat_id=str(chat_id),
        message_id=message_id,
        direction="in",
        sender="Hazem",
        content="",
        timestamp=_time.time(),
        has_media=True,
        **fields,
    )
    record.save()
    return record


@pytest.fixture
def chain_chat_id():
    """Unique chat id per test; deletes that chat's records on teardown."""
    import uuid

    from models.telegram import TelegramMessage

    chat_id = int(uuid.uuid4().int % 10**9) + 10**9
    yield chat_id
    for record in TelegramMessage.query.filter(chat_id=str(chat_id)):
        record.delete()


class TestResolveMediaDescriptor:
    """_resolve_media_descriptor against real TelegramMessage records in the
    claimed test Redis db. Each unreadable reason is a distinct, asserted
    state (#2732 Failure Path Test Strategy)."""

    async def test_no_media_returns_none(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        msg = _FakeChainMsg(10, text="plain text")
        assert await _resolve_media_descriptor(msg, chain_chat_id) is None

    async def test_missing_record_is_unreadable_no_record(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "no_record"
        assert descriptor["filename"] == "report.pdf", (
            "a Redis miss must still yield a NAMED attachment — the two sources degrade separately"
        )
        assert descriptor["local_path"] is None

    async def test_download_error_is_unreadable_with_specific_reason(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        _save_telegram_record(chain_chat_id, 10, media_download_error="timeout after 120s")
        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "download_error: timeout after 120s"

    async def test_missing_path_is_unreadable_no_path_recorded(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        _save_telegram_record(chain_chat_id, 10, media_local_path=None)
        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "no_path_recorded"

    async def test_whitespace_path_is_unreadable_no_path_recorded(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        _save_telegram_record(chain_chat_id, 10, media_local_path="   ")
        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "no_path_recorded"

    async def test_path_outside_media_dir_is_unreadable_invalid_path(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        # /etc/hosts exists and is readable — outside data/media it must
        # still never be rendered as a resolved path.
        _save_telegram_record(chain_chat_id, 10, media_local_path="/etc/hosts")
        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "invalid_path"
        assert descriptor["local_path"] is None

    async def test_recorded_path_absent_from_disk_is_unreadable_file_missing(self, chain_chat_id):
        import uuid

        from bridge.context import _resolve_media_descriptor
        from bridge.media import MEDIA_DIR

        ghost = MEDIA_DIR / f"test2732_ghost_{uuid.uuid4().hex}.pdf"
        _save_telegram_record(chain_chat_id, 10, media_local_path=str(ghost))
        msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "file_missing"

    async def test_resolved_when_record_and_file_exist(self, chain_chat_id, tmp_path):
        import uuid

        from bridge.context import _resolve_media_descriptor
        from bridge.media import MEDIA_DIR

        real = MEDIA_DIR / f"test2732_{uuid.uuid4().hex}.pdf"
        real.write_bytes(b"%PDF test")
        try:
            _save_telegram_record(chain_chat_id, 10, media_local_path=str(real))
            msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
            descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
            assert descriptor["kind"] == "resolved"
            assert descriptor["filename"] == "report.pdf"
            assert descriptor["local_path"] == str(real)
            assert descriptor["reason"] is None
        finally:
            real.unlink(missing_ok=True)

    async def test_photo_without_filename_gets_synthetic_label(self, chain_chat_id):
        from telethon.tl.types import MessageMediaPhoto

        from bridge.context import _resolve_media_descriptor

        photo_media = MessageMediaPhoto.__new__(MessageMediaPhoto)
        msg = _FakeChainMsg(42, media=photo_media, file=_FakeFile(None))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["media_type"] == "photo"
        assert descriptor["filename"] == "photo-42", (
            "photos have no filename — the synthetic {media_type}-{message_id} label must apply"
        )

    async def test_unknown_extension_document_still_yields_descriptor(self, chain_chat_id):
        from bridge.context import _resolve_media_descriptor

        # A genuine document with an extension no classifier bucket knows is
        # still a downloadable file: it classifies as "document" and must
        # yield a descriptor rather than vanish.
        msg = _FakeChainMsg(7, media=_document_media("data.xyz9"), file=_FakeFile(None))
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor["media_type"] == "document"
        assert descriptor["filename"] == "document-7"

    @pytest.mark.parametrize(
        "type_name",
        [
            "MessageMediaGeoLive",
            "MessageMediaVenue",
            "MessageMediaGame",
            "MessageMediaInvoice",
            "MessageMediaPoll",
            "MessageMediaGeo",
            "MessageMediaContact",
            "MessageMediaDice",
            "MessageMediaWebPage",
        ],
    )
    async def test_non_file_media_yields_no_descriptor(self, chain_chat_id, type_name):
        """Regression (PR #3146 review round 2): every non-file media kind —
        current and future — must fail closed at the classifier gate. A
        record with has_media=True and no path exists (the production intake
        shape), so a fail-open gate would render a false
        "[unreadable attachment ... no_path_recorded]" marker."""
        from telethon.tl import types as tl_types

        from bridge.context import _resolve_media_descriptor

        media_cls = getattr(tl_types, type_name)
        _save_telegram_record(chain_chat_id, 11)
        msg = _FakeChainMsg(11, media=media_cls.__new__(media_cls))
        assert await _resolve_media_descriptor(msg, chain_chat_id) is None, (
            f"{type_name} is not a downloadable attachment and must yield no descriptor"
        )

    async def test_live_location_hop_renders_no_false_marker(self, chain_chat_id):
        """End to end: a caption-less live-location hop renders neither a
        false unreadable marker nor a bare 'Hazem:' label — the hop simply
        does not appear (PR #3146 review round 2, blocker + nit)."""
        from telethon.tl import types as tl_types

        from bridge.context import fetch_reply_chain, format_reply_chain

        geolive = tl_types.MessageMediaGeoLive.__new__(tl_types.MessageMediaGeoLive)
        _save_telegram_record(chain_chat_id, 1)
        msgs = [
            _FakeChainMsg(1, text="", media=geolive),
            _FakeChainMsg(2, text="on my way", reply_to=1),
        ]
        chain = await fetch_reply_chain(_FakeChainClient(msgs), chain_chat_id, 2)
        formatted = format_reply_chain(chain)
        assert "unreadable" not in formatted
        assert "no_path_recorded" not in formatted
        hazem_lines = [line for line in formatted.splitlines() if line.startswith("Hazem")]
        assert hazem_lines == ["Hazem: on my way"], (
            "the caption-less excluded hop must render nothing, not a bare label"
        )

    async def test_link_preview_message_yields_no_descriptor(self, chain_chat_id):
        """Regression (PR #3146 review): MessageMediaWebPage is set on any
        plain-text message that gets a link preview. It must never be
        treated as an attachment, even though Telethon's Message.file can
        fall back to the web preview's own photo/document and read truthy."""
        from telethon.tl.types import MessageMediaWebPage, WebPageEmpty

        from bridge.context import _resolve_media_descriptor

        web_page_media = MessageMediaWebPage(webpage=WebPageEmpty(id=1))
        msg = _FakeChainMsg(
            555,
            text="check this out https://example.com",
            media=web_page_media,
            file=_FakeFile("example.com"),
        )
        descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor is None, "a link-preview message must not render an attachment marker"

    async def test_chat_id_scoped_lookup_ignores_other_chats_record(self, chain_chat_id):
        """Risk 3: a record with a matching message_id in a DIFFERENT chat
        must never resolve — message ids are per-chat sequences and the
        media dir is shared across every chat on the machine."""
        import uuid

        from bridge.context import _resolve_media_descriptor
        from bridge.media import MEDIA_DIR
        from models.telegram import TelegramMessage

        other_chat = chain_chat_id + 1
        other_file = MEDIA_DIR / f"test2732_other_{uuid.uuid4().hex}.pdf"
        other_file.write_bytes(b"%PDF other tenant")
        try:
            _save_telegram_record(other_chat, 10, media_local_path=str(other_file))
            msg = _FakeChainMsg(10, media=_document_media(), file=_FakeFile("report.pdf"))
            descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
            assert descriptor["kind"] == "unreadable"
            assert descriptor["reason"] == "no_record"
            assert descriptor["local_path"] is None, (
                "another chat's file must be unnameable through this resolver"
            )
        finally:
            other_file.unlink(missing_ok=True)
            for record in TelegramMessage.query.filter(chat_id=str(other_chat)):
                record.delete()

    async def test_resolver_failure_logs_warning_and_returns_unreadable(
        self, chain_chat_id, caplog
    ):
        """The resolver's own except path: log at warning, return an
        unreadable descriptor — never None, never a re-raise."""
        import logging
        from unittest.mock import patch

        from bridge.context import _resolve_media_descriptor

        msg = _FakeChainMsg(99, media=_document_media(), file=_FakeFile("report.pdf"))
        with (
            patch("bridge.context.get_media_type", side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING, logger="bridge.context"),
        ):
            descriptor = await _resolve_media_descriptor(msg, chain_chat_id)
        assert descriptor is not None
        assert descriptor["kind"] == "unreadable"
        assert descriptor["reason"] == "resolution_error"
        assert any("Could not resolve media descriptor" in rec.message for rec in caplog.records), (
            "resolver failure must be observable at WARNING"
        )


class TestFetchReplyChainMediaFailurePath:
    """A per-hop descriptor-resolution failure must degrade that hop and let
    the walk continue — it must never reach fetch_reply_chain's loop-tail
    `except Exception: break` (#2732 Failure Path Test Strategy)."""

    async def test_hop_resolution_failure_degrades_and_walk_continues(self, chain_chat_id):
        from unittest.mock import patch

        from bridge.context import fetch_reply_chain

        msgs = [
            _FakeChainMsg(1, text="", media=_document_media(), file=_FakeFile("doc.pdf")),
            _FakeChainMsg(2, text="middle reply", reply_to=1),
            _FakeChainMsg(3, text="latest reply", reply_to=2),
        ]
        client = _FakeChainClient(msgs)

        def _exploding_get_media_type(m):
            raise RuntimeError("classifier exploded")

        with patch("bridge.context.get_media_type", _exploding_get_media_type):
            chain = await fetch_reply_chain(client, chain_chat_id, 3)

        assert len(chain) == 3, "one bad hop must not lose the rest of the walk"
        # Chronological order: the media hop is oldest (first).
        assert chain[0]["media"] is not None
        assert chain[0]["media"]["kind"] == "unreadable"
        assert chain[0]["media"]["reason"] == "resolution_error"
        assert chain[1]["media"] is None
        assert chain[2]["media"] is None
        assert [entry["content"] for entry in chain] == ["", "middle reply", "latest reply"]

    async def test_media_hop_end_to_end_renders_resolved_path(self, chain_chat_id):
        """The reported exchange shape (attachment, then two text replies)
        through fetch_reply_chain + format_reply_chain: the rendered block
        carries the readable absolute path and no `[media]` literal."""
        import uuid

        from bridge.context import fetch_reply_chain, format_reply_chain
        from bridge.media import MEDIA_DIR

        real = MEDIA_DIR / f"test2732_e2e_{uuid.uuid4().hex}.pdf"
        real.write_bytes(b"%PDF chain")
        try:
            _save_telegram_record(chain_chat_id, 1, media_local_path=str(real))
            msgs = [
                _FakeChainMsg(
                    1, text="", media=_document_media(), file=_FakeFile("recommendation.pdf")
                ),
                _FakeChainMsg(2, text="brushes over many details", reply_to=1),
                _FakeChainMsg(3, text="valor can help flesh this out", reply_to=2),
            ]
            chain = await fetch_reply_chain(_FakeChainClient(msgs), chain_chat_id, 3)
            formatted = format_reply_chain(chain)
            assert str(real) in formatted, "agent must receive the readable absolute path"
            assert "recommendation.pdf" in formatted
            assert "[media]" not in formatted
        finally:
            real.unlink(missing_ok=True)


class TestResolveMediaFlag:
    """PR #3146 review, tech debt: the session-routing walk must not pay
    per-hop Redis descriptor resolution for data it never reads."""

    async def test_resolve_media_false_skips_descriptor_resolution(self, chain_chat_id):
        from unittest.mock import AsyncMock, patch

        from bridge.context import fetch_reply_chain

        msgs = [
            _FakeChainMsg(1, text="root", media=_document_media()),
            _FakeChainMsg(2, text="reply", reply_to=1),
        ]
        spy = AsyncMock(return_value=None)
        with patch("bridge.context._resolve_media_descriptor", spy):
            chain = await fetch_reply_chain(
                _FakeChainClient(msgs), chain_chat_id, 2, resolve_media=False
            )
        assert spy.await_count == 0, "resolve_media=False must perform zero descriptor lookups"
        assert len(chain) == 2
        assert all(entry["media"] is None for entry in chain)

    async def test_session_routing_walk_passes_resolve_media_false(self):
        from unittest.mock import AsyncMock, patch

        from bridge.context import resolve_root_session_id

        recorded = {}

        async def _fake_fetch(client, chat_id, message_id, max_depth=20, resolve_media=True):
            recorded["resolve_media"] = resolve_media
            return [
                {"sender": "Hazem", "content": "root", "message_id": 5, "date": None, "media": None}
            ]

        with (
            patch("bridge.context._get_cached_root", AsyncMock(return_value=None)),
            patch("bridge.context._cache_walk_root", AsyncMock(return_value=None)),
            patch("bridge.context.fetch_reply_chain", _fake_fetch),
            patch("bridge.context._set_cached_root", AsyncMock()),
        ):
            session_id = await resolve_root_session_id(object(), 123, 9, "valor")

        assert recorded["resolve_media"] is False, (
            "the Step 2 API fallback consumes only sender/message_id and must skip media"
        )
        assert session_id == "tg_valor_123_5"
