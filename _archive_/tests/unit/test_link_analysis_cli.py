"""Unit tests for valor-youtube-transcribe CLI.

Mocks `process_youtube_url` to assert argparse + output formatting + exit
codes deterministically without making network calls.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools.link_analysis import cli as transcribe_cli
from tools.link_analysis.cli import NO_SUMMARY_NOTE, main


def _run_cli(monkeypatch, capsys, argv, mock_result):
    """Helper: patch process_youtube_url to return mock_result and run main()."""
    monkeypatch.setattr("sys.argv", ["valor-youtube-transcribe", *argv])

    async def _fake(url):
        return mock_result

    with patch.object(transcribe_cli, "process_youtube_url", _fake):
        with pytest.raises(SystemExit) as excinfo:
            main()
    out = capsys.readouterr()
    return excinfo.value.code, out.out, out.err


def test_default_human_readable_with_summary(monkeypatch, capsys):
    code, stdout, _ = _run_cli(
        monkeypatch,
        capsys,
        ["https://youtu.be/abc"],
        {
            "success": True,
            "video_id": "abc",
            "title": "Hello World",
            "transcript": "long transcript " * 200,
            "summary": "A short summary",
        },
    )
    assert code == 0
    assert "Title: Hello World" in stdout
    assert "Summary:" in stdout
    assert "A short summary" in stdout


def test_default_human_readable_without_summary(monkeypatch, capsys):
    code, stdout, _ = _run_cli(
        monkeypatch,
        capsys,
        ["https://youtu.be/abc"],
        {
            "success": True,
            "video_id": "abc",
            "title": "Short Vid",
            "transcript": "hello world",
            "summary": None,
        },
    )
    assert code == 0
    assert "Transcript:" in stdout
    assert "hello world" in stdout
    assert "Summary:" not in stdout


def test_json_flag_emits_valid_json(monkeypatch, capsys):
    payload = {
        "success": True,
        "video_id": "abc",
        "title": "T",
        "transcript": "hi",
        "summary": None,
    }
    code, stdout, _ = _run_cli(monkeypatch, capsys, ["--json", "https://youtu.be/abc"], payload)
    assert code == 0
    parsed = json.loads(stdout)
    assert parsed["video_id"] == "abc"
    assert parsed["success"] is True


def test_summary_only_with_summary(monkeypatch, capsys):
    code, stdout, _ = _run_cli(
        monkeypatch,
        capsys,
        ["--summary-only", "https://youtu.be/abc"],
        {
            "success": True,
            "video_id": "abc",
            "title": "T",
            "transcript": "long " * 500,
            "summary": "Just the summary.",
        },
    )
    assert code == 0
    assert stdout.strip() == "Just the summary."


def test_summary_only_fallback_to_transcript_with_pinned_note(monkeypatch, capsys):
    """C3: when summary is None, --summary-only must emit the pinned note prefix."""
    code, stdout, _ = _run_cli(
        monkeypatch,
        capsys,
        ["--summary-only", "https://youtu.be/abc"],
        {
            "success": True,
            "video_id": "abc",
            "title": "Short",
            "transcript": "short",
            "summary": None,
        },
    )
    assert code == 0
    assert stdout.startswith(NO_SUMMARY_NOTE)
    assert "short" in stdout


def test_invalid_url_exits_one_with_stderr(monkeypatch, capsys):
    code, _, stderr = _run_cli(
        monkeypatch,
        capsys,
        ["https://example.com"],
        {
            "success": False,
            "error": "Not a valid YouTube URL",
            "context": "",
        },
    )
    assert code == 1
    assert "Not a valid YouTube URL" in stderr


def test_live_stream_error_exits_one(monkeypatch, capsys):
    code, _, stderr = _run_cli(
        monkeypatch,
        capsys,
        ["https://youtu.be/live"],
        {
            "success": False,
            "video_id": "live",
            "error": "Cannot transcribe live streams",
            "context": "[YouTube Live Stream: ...]",
        },
    )
    assert code == 1
    assert "Cannot transcribe live streams" in stderr


def test_video_too_long_error_exits_one(monkeypatch, capsys):
    code, _, stderr = _run_cli(
        monkeypatch,
        capsys,
        ["https://youtu.be/long"],
        {
            "success": False,
            "video_id": "long",
            "error": "Video too long (10000s > 3600s limit)",
            "context": "[...]",
        },
    )
    assert code == 1
    assert "Video too long" in stderr


def test_unexpected_exception_exits_one(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["valor-youtube-transcribe", "https://youtu.be/abc"])

    async def _raises(url):
        raise RuntimeError("network down")

    with patch.object(transcribe_cli, "process_youtube_url", _raises):
        with pytest.raises(SystemExit) as excinfo:
            main()
    out = capsys.readouterr()
    assert excinfo.value.code == 1
    assert "network down" in out.err


def test_help_flag_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["valor-youtube-transcribe", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 0


def test_json_and_summary_only_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["valor-youtube-transcribe", "--json", "--summary-only", "https://youtu.be/abc"],
    )
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
