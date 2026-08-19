"""Unit tests for ``bridge.update.extract_update_warnings`` and the rewritten
``_queue_fix_session`` payload (issue #2845).

Covers Defect 2 (the cron summary format — ``(N warnings)`` / ``update
failed at`` — was invisible to the old ``_warning_prefixes`` scan) and
Defect 1 (the fix-session brief truncated at ``stdout[:500]``, cutting the
warning list — the entire reason the session exists).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bridge import update as bridge_update
from bridge.update import _queue_fix_session, extract_update_warnings

pytestmark = pytest.mark.unit


@pytest.fixture
def event():
    return SimpleNamespace(chat_id=111, message=SimpleNamespace(id=222))


# ---------------------------------------------------------------------------
# extract_update_warnings: the cron summary format (Defect 2)
# ---------------------------------------------------------------------------


def test_cron_summary_warnings_trigger_fix_session():
    """The central defect: the `--cron` summary format must be detected.

    Regression for #2845 — `has_warnings`'s old prefix scan matched none of
    `run.py --cron`'s actual output shapes. Captured red (against
    unmodified `bridge/update.py`, pre-#2845): `extract_update_warnings`
    did not exist and `has_warnings`'s prefix scan returned False for this
    exact transcript, because the summary line is `up to date at <sha> (N
    warnings)` and the bullets are `  ⚠️ <text>` — neither matches
    `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`.
    """
    status_lines = [
        "up to date at abc1234 (2 warnings)",
        "  ⚠️ gws auth needed (or: gws auth setup && gws auth verify)",
        "  ⚠️ Redis ACL drift detected",
    ]
    result = extract_update_warnings(status_lines)
    assert result == [
        "gws auth needed (or: gws auth setup && gws auth verify)",
        "Redis ACL drift detected",
    ]


def test_green_transcript_with_adversarial_filenames_yields_no_warnings():
    """A realistic green `/update` must not spawn a spurious fix session (Risk 2)."""
    status_lines = [
        "[update] Pulling latest code...",
        " src/error_handler.py | 5 +-",
        " tests/test_warning_utils.py | 2 +-",
        "npm warn deprecated some-package@1.0.0",
        "up to date at abc1234 (0 warnings)",
    ]
    assert extract_update_warnings(status_lines) == []


def test_zero_warnings_summary_yields_empty_list():
    """A well-formed `(0 warnings)` must NOT trigger a fix session."""
    assert extract_update_warnings(["up to date at abc1234 (0 warnings)"]) == []


def test_empty_input_yields_empty_list():
    assert extract_update_warnings([]) == []


def test_whitespace_only_lines_yield_empty_list():
    assert extract_update_warnings(["   ", "\t", ""]) == []


def test_update_successful_summary_yields_empty_list():
    assert extract_update_warnings(["update successful"]) == []


def test_27_warning_payload_round_trips_with_last_warning_present():
    """The specific regression from the issue: only warning #1 partially survived."""
    bullets = [f"  ⚠️ warning number {i}" for i in range(27)]
    status_lines = ["up to date at abc1234 (27 warnings)", *bullets]
    result = extract_update_warnings(status_lines)
    assert len(result) == 27
    assert result[-1] == "warning number 26"


def test_legacy_prefixes_still_recognised_verbatim():
    """The four legacy prefixes the non-cron `--verify` path still emits."""
    status_lines = [
        "[update] WARN: fetch/fast-forward failed or had conflicts — continuing",
        "WARNING: gws release could not be confirmed (unknown)",
        "ERROR: No Python venv at $PYTHON",
        "RESTART FAILED: worker did not come back up",
    ]
    result = extract_update_warnings(status_lines)
    assert len(result) == 4
    for line, text in zip(status_lines, result, strict=True):
        assert text == line


def test_bare_substring_does_not_match_legacy_prefix():
    """Line-anchored only — a bare substring must not over-match (#1898)."""
    status_lines = ["this line mentions WARNING in the middle, not at the start"]
    assert extract_update_warnings(status_lines) == []


# ---------------------------------------------------------------------------
# extract_update_warnings: the count mismatch cross-check
# ---------------------------------------------------------------------------


def test_count_mismatch_appends_synthetic_entry_by_content():
    status_lines = [
        "up to date at abc1234 (3 warnings)",
        "  ⚠️ only warning present",
    ]
    result = extract_update_warnings(status_lines)
    assert "only warning present" in result
    assert any(
        text == "[update] WARN: summary declared 3 warning(s) but 1 were parsed"
        for text in result
    )


def test_matched_count_appends_no_synthetic_entry():
    """A legacy-prefix line alongside a matching summary must not report a mismatch."""
    status_lines = [
        "[update] WARN: fetch/fast-forward failed or had conflicts — continuing with current code",
        "up to date at abc1234 (2 warnings)",
        "  ⚠️ warn1",
        "  ⚠️ warn2",
    ]
    result = extract_update_warnings(status_lines)
    assert len(result) == 3
    assert not any("summary declared" in text for text in result)


def test_mismatch_fires_only_when_a_summary_line_was_seen():
    """Failure-branch bullets carry no `(N warnings)` count — no spurious mismatch."""
    status_lines = [
        "update failed at abc1234",
        "  - migration failed",
        "  ⚠️ a warning on the failure path",
    ]
    result = extract_update_warnings(status_lines)
    assert result == ["migration failed", "a warning on the failure path"]
    assert not any("summary declared" in text for text in result)


# ---------------------------------------------------------------------------
# extract_update_warnings: the failure-block form
# ---------------------------------------------------------------------------


def test_failure_block_bullets_matched_only_inside_the_block():
    status_lines = [
        "  - this bullet appears before any failure summary and must not match",
        "update failed at abc1234",
        "  - git pull failed: network unreachable",
    ]
    result = extract_update_warnings(status_lines)
    assert result == ["git pull failed: network unreachable"]


def test_failed_run_with_only_warnings_still_renders_bullets():
    """success=False, empty errors, two warnings: run.py:704/:1999/:2014's shape."""
    status_lines = [
        "update failed at abc1234",
        "  ⚠️ Worker not running after install and kickstart retry",
        "  ⚠️ Worker install failed",
    ]
    result = extract_update_warnings(status_lines)
    assert result == [
        "Worker not running after install and kickstart retry",
        "Worker install failed",
    ]
    assert not any("summary declared" in text for text in result)


# ---------------------------------------------------------------------------
# extract_update_warnings: multi-line survival (producer already collapses
# newlines via _append_warning/_append_error — this parser sees one physical
# line per entry, but that line may be long/complex)
# ---------------------------------------------------------------------------


def test_embedded_newline_collapsed_by_producer_survives_complete():
    """The producer (`_append_warning`) collapses newlines before render;
    the parser must not further truncate the resulting single line."""
    collapsed = (
        "pydantic_core._pydantic_core.ValidationError: 1 validation error "
        "for Settings features.crash_autoresume_max_attempts Input should "
        "be a valid integer, unable to parse string as an integer "
        "[type=int_parsing, input_value='', input_type=str]"
    )
    status_lines = [f"  ⚠️ {collapsed}"]
    assert extract_update_warnings(status_lines) == [collapsed]


def test_readme_shaped_warning_survives_complete():
    """A README-producer warning (run.py:1804-1807) collapsed to one line."""
    collapsed = (
        "[popoto] README.md is missing a '## Running' section   "
        "Add the following to README.md:   ## Running"
    )
    status_lines = [f"  ⚠️ {collapsed}"]
    assert extract_update_warnings(status_lines) == [collapsed]


def test_multiline_error_bullet_survives_complete():
    collapsed = "Migration failed: ValidationError: multiple lines collapsed into one"
    status_lines = [
        "update failed at abc1234",
        f"  - {collapsed}",
    ]
    assert extract_update_warnings(status_lines) == [collapsed]


# ---------------------------------------------------------------------------
# extract_update_warnings: the `suppressed:` trailer is inert (shape-only —
# the load-bearing version against the real SUPPRESSED_PREFIX lives in
# tests/unit/test_bridge_update.py, added by Task 5)
# ---------------------------------------------------------------------------


def test_suppressed_trailer_shape_is_inert():
    """A line shaped like the suppression trailer (no ⚠️, no legacy prefix)
    must extract as zero warnings. This is a shape-only guess at the
    trailer's spelling — Task 5 pins the real `SUPPRESSED_PREFIX`."""
    line = "suppressed (unchanged since first warning): gws-auth — details: python -m scripts.update.warn_state"
    assert extract_update_warnings([line]) == []


# ---------------------------------------------------------------------------
# _queue_fix_session: the fix-session payload (Defect 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_session_brief_is_legible(event, monkeypatch):
    """The three shape properties containment cannot reach (Success Criteria)."""
    enqueue = AsyncMock()
    monkeypatch.setattr(bridge_update, "enqueue_agent_session", enqueue, raising=False)
    monkeypatch.setitem(
        __import__("sys").modules,
        "agent.agent_session_queue",
        SimpleNamespace(enqueue_agent_session=enqueue),
    )

    last_warning = "the final warning in a long list"
    warnings = [f"warning {i}" for i in range(50)] + [last_warning]
    long_stdout = "\n".join(f"npm output line {i} with some filler text" for i in range(500))
    long_stdout += "\n<<FILE:/repo/data/update.txt>>"

    await _queue_fix_session(event, "testbox", long_stdout, "", warnings, failed=False)

    enqueue.assert_awaited_once()
    message_text = enqueue.await_args.kwargs["message_text"]

    # (a) ordering: warnings before the elision marker
    assert message_text.index(last_warning) < message_text.index("[... ")
    # (b) marked, line-boundary cut
    assert "characters elided" in message_text
    # (c) log pointer present
    assert "/repo/data/update.txt" in message_text


@pytest.mark.asyncio
async def test_fix_session_brief_with_empty_warnings_is_usable(event, monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "agent.agent_session_queue",
        SimpleNamespace(enqueue_agent_session=enqueue),
    )
    await _queue_fix_session(event, "testbox", "some output", "", [], failed=True)
    enqueue.assert_awaited_once()
    message_text = enqueue.await_args.kwargs["message_text"]
    assert "Update failed" in message_text
    assert "none parsed" in message_text


@pytest.mark.asyncio
async def test_fix_session_brief_with_empty_stdout_and_stderr_is_coherent(event, monkeypatch):
    enqueue = AsyncMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "agent.agent_session_queue",
        SimpleNamespace(enqueue_agent_session=enqueue),
    )
    await _queue_fix_session(event, "testbox", "", "", ["one warning"], failed=False)
    enqueue.assert_awaited_once()
    message_text = enqueue.await_args.kwargs["message_text"]
    assert "one warning" in message_text
    assert "(empty)" in message_text


@pytest.mark.asyncio
async def test_queue_fix_session_swallows_raising_enqueue(event, monkeypatch, caplog):
    """_queue_fix_session must never propagate — it wraps in `except Exception`."""

    async def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(
        __import__("sys").modules,
        "agent.agent_session_queue",
        SimpleNamespace(enqueue_agent_session=_raise),
    )
    # Must not raise.
    await _queue_fix_session(event, "testbox", "out", "err", ["w"], failed=False)
