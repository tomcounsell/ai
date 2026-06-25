"""Unit tests for the bot-reply awaiter (issue #1574).

The settle algorithm is the load-bearing part: silence (edit-aware) decides
DONE, status patterns only clean the display, and the two timers must not be
conflated. All tests inject a fake clock and a scripted history fetch so they
run instantly and deterministically — no Redis, no Telethon, no real sleeps.
"""

from __future__ import annotations

from tools.valor_telegram_await import (
    DEFAULT_STATUS_PATTERNS,
    await_bot_reply,
)


class FakeClock:
    """Monotonic clock that advances only when sleep() is called."""

    def __init__(self):
        self.t = 1000.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def _scripted_fetch(timeline):
    """Build a _fetch that returns a different record set per poll.

    `timeline` is a list of record-lists; each call returns the next one and
    then keeps returning the last entry (the stream has gone quiet).
    """
    calls = {"n": 0}

    def fetch(chat_id):
        idx = min(calls["n"], len(timeline) - 1)
        calls["n"] += 1
        return list(timeline[idx])

    return fetch


def _rec(message_id, content, ts=1000.0, direction="in"):
    return {"message_id": message_id, "content": content, "ts": ts, "direction": direction}


def test_settles_on_silence_after_single_message():
    clock = FakeClock()
    timeline = [
        [_rec(1, "Hello, the answer is 42.")],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=5.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert result.settled is True
    assert result.timed_out is False
    assert result.settled_text == "Hello, the answer is 42."
    assert result.message_ids == [1]


def test_edit_resets_quiet_timer_no_premature_settle():
    """A streamed answer (edits on a stable message_id) must NOT settle on the
    first partial — each edit resets the quiet window."""
    clock = FakeClock()
    # Polls: partial, partial+more, final — then quiet.
    timeline = [
        [_rec(1, "The")],
        [_rec(1, "The answer")],
        [_rec(1, "The answer is 42.")],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=3.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    # Must capture the FINAL edited body, not a partial.
    assert result.settled is True
    assert result.settled_text == "The answer is 42."
    assert result.edit_count == 2


def test_timeout_returns_partial_and_flags():
    """A bot that never goes quiet hits the overall timeout and returns what it
    has with timed_out=True."""
    clock = FakeClock()

    def never_quiet_fetch(chat_id):
        # Each call returns a slightly different body so the quiet timer never
        # elapses; content changes keep resetting last_change.
        never_quiet_fetch.n += 1
        return [_rec(1, f"working {never_quiet_fetch.n}")]

    never_quiet_fetch.n = 0

    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=5.0,
        timeout=10.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=never_quiet_fetch,
    )
    assert result.timed_out is True
    assert result.settled is False
    assert result.elapsed_s >= 10.0


def test_two_timers_not_conflated_slow_turn_not_cut_off():
    """A multi-minute turn (gaps longer than the quiet window between status
    updates) must not be cut off, as long as activity keeps arriving before the
    overall timeout."""
    clock = FakeClock()
    # Status update, then a long gap (still under timeout), then the answer.
    timeline = [
        [_rec(1, "⏳ Still working... (3 min elapsed)")],
        [_rec(1, "⏳ Still working... (3 min elapsed)"), _rec(2, "Done: here is the result.")],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=4.0,
        timeout=600.0,
        poll_interval=2.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert result.settled is True
    # The answer message is captured; status line is filtered from prose.
    assert "Done: here is the result." in result.settled_text


def test_status_lines_filtered_from_prose_only():
    clock = FakeClock()
    timeline = [
        [
            _rec(1, "⏳ Working..."),
            _rec(2, "💻 terminal: ls -la"),
            _rec(3, "The real answer."),
        ],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=2.0,
        timeout=600.0,
        poll_interval=1.0,
        status_patterns=DEFAULT_STATUS_PATTERNS,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    # Prose excludes status/tool bubbles; transcript retains everything.
    assert result.settled_text == "The real answer."
    kinds = {t["kind"] for t in result.transcript}
    assert "status" in kinds
    assert "answer" in kinds
    assert len(result.transcript) == 3


def test_footer_preserved_not_stripped():
    """The glued ⚠️ footer is a test signal and must remain inside settled_text;
    footer_present is surfaced as a flag."""
    clock = FakeClock()
    answer = "I edited the file as requested.\n\n⚠️ Could not verify the file landed."
    timeline = [[_rec(1, answer)]]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=2.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert "⚠️" in result.settled_text
    assert result.footer_present is True


def test_only_status_no_answer_settles_with_empty_prose():
    clock = FakeClock()
    timeline = [[_rec(1, "⏳ Working...")]]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=2.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert result.settled is True
    assert result.settled_text == ""
    assert len(result.transcript) == 1  # tester can see "only status"


def test_messages_before_send_ts_ignored():
    clock = FakeClock()
    timeline = [
        [
            _rec(1, "old message from before", ts=500.0),
            _rec(2, "fresh reply", ts=1001.0),
        ],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=1000.0,
        quiet_window=2.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert result.settled_text == "fresh reply"
    assert result.message_ids == [2]


def test_outbound_records_ignored():
    clock = FakeClock()
    timeline = [
        [
            _rec(1, "my own probe", direction="out"),
            _rec(2, "bot reply", direction="in"),
        ],
    ]
    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=2.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=_scripted_fetch(timeline),
    )
    assert result.settled_text == "bot reply"
    assert result.message_ids == [2]


def test_transient_fetch_error_does_not_abort():
    """A failing poll must not crash the await — it retries and eventually
    settles once the store is readable again."""
    clock = FakeClock()
    state = {"n": 0}

    def flaky_fetch(chat_id):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("redis blip")
        return [_rec(1, "recovered answer")]

    result = await_bot_reply(
        chat_id="123",
        send_ts=999.0,
        quiet_window=3.0,
        timeout=600.0,
        poll_interval=1.0,
        _now=clock.now,
        _sleep=clock.sleep,
        _fetch=flaky_fetch,
    )
    assert result.settled is True
    assert result.settled_text == "recovered answer"
