"""Unit tests for reflections/auditing.py — focused on run_log_review side effects.

Covers:
- Telegram-send happy path: subprocess.run called with valor-telegram args
- Empty-findings heartbeat: a single-line "0 findings across N projects" message
- FileNotFoundError swallow: missing valor-telegram does NOT raise; warning emitted
- TimeoutExpired swallow: 10s subprocess timeout does NOT raise; warning emitted
- CalledProcessError swallow: non-zero exit does NOT raise; warning emitted
- Sentry helper: missing sentry-cli returns None
- Sentry helper: missing SENTRY_DSN returns None
- Sentry helper: subprocess timeout returns None
- run_log_review still returns the {"status": "ok", "findings": [...], "summary": ...} dict
  even when Telegram delivery fails

All tests use unittest.mock.patch on subprocess.run and shutil.which —
no real Telegram or Sentry calls.
"""

from __future__ import annotations

import json
import logging
import subprocess
from unittest.mock import MagicMock, patch

from reflections import auditing

# --------------------------------------------------------------------------
# _send_log_review_telegram
# --------------------------------------------------------------------------


class TestSendLogReviewTelegram:
    """The Telegram-send block must swallow every subprocess failure."""

    def test_happy_path_invokes_valor_telegram(self):
        """subprocess.run is called with valor-telegram send --chat 'Dev: Valor' <msg>."""
        findings = ["[proj-a] something noteworthy", "[proj-b] another finding"]
        with patch("reflections.auditing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            auditing._send_log_review_telegram(
                "Log review: analyzed 5 files, 2 finding(s)", findings
            )

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "valor-telegram"
        assert call_args[1] == "send"
        assert "--chat" in call_args
        assert "Dev: Valor" in call_args
        # Last positional arg is the message body
        body = call_args[-1]
        assert body.startswith("Daily Log Review —")
        assert "[proj-a]" in body
        assert "[proj-b]" in body
        # Timeout MUST be set
        kwargs = mock_run.call_args[1]
        assert kwargs.get("timeout") == 10
        assert kwargs.get("check") is False

    def test_empty_findings_sends_heartbeat(self):
        """When findings is empty, a one-line heartbeat is still sent."""
        with patch("reflections.auditing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            auditing._send_log_review_telegram(
                "Daily Log Review — 2026-04-30: 0 findings across 3 project(s)",
                [],
            )

        mock_run.assert_called_once()
        body = mock_run.call_args[0][0][-1]
        assert "Daily Log Review —" in body
        assert "0 findings" in body
        # Two lines: header + summary line
        assert body.count("\n") == 1

    def test_caps_findings_at_twelve(self):
        """When findings > 12, message shows 12 plus 'N more findings' footer."""
        findings = [f"[proj-a] finding {i}" for i in range(20)]
        with patch("reflections.auditing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            auditing._send_log_review_telegram(
                "Log review: analyzed 5 files, 20 finding(s)", findings
            )

        body = mock_run.call_args[0][0][-1]
        assert "[proj-a] finding 11" in body  # 12th included (zero-indexed)
        assert "[proj-a] finding 12" not in body  # 13th excluded
        assert "(8 more findings — see worker.log)" in body

    def test_file_not_found_is_swallowed(self, caplog):
        """Missing valor-telegram binary does NOT raise; logs warning."""
        with patch(
            "reflections.auditing.subprocess.run",
            side_effect=FileNotFoundError("valor-telegram"),
        ):
            with caplog.at_level(logging.WARNING, logger="reflections.auditing"):
                # Must not raise
                auditing._send_log_review_telegram("summary", ["[proj] finding"])

        assert any("valor-telegram not on PATH" in rec.message for rec in caplog.records)

    def test_timeout_expired_is_swallowed(self, caplog):
        """A 10s subprocess timeout does NOT raise; logs warning."""
        with patch(
            "reflections.auditing.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="valor-telegram", timeout=10),
        ):
            with caplog.at_level(logging.WARNING, logger="reflections.auditing"):
                auditing._send_log_review_telegram("summary", ["[proj] finding"])

        assert any("timed out" in rec.message for rec in caplog.records)

    def test_called_process_error_is_swallowed(self, caplog):
        """Non-zero exit raises CalledProcessError; must be swallowed."""
        with patch(
            "reflections.auditing.subprocess.run",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd="valor-telegram"),
        ):
            with caplog.at_level(logging.WARNING, logger="reflections.auditing"):
                auditing._send_log_review_telegram("summary", ["[proj] finding"])

        assert any("send failed" in rec.message for rec in caplog.records)

    def test_unexpected_exception_is_swallowed(self, caplog):
        """Any other exception is also swallowed (defensive broad catch)."""
        with patch(
            "reflections.auditing.subprocess.run",
            side_effect=RuntimeError("unexpected"),
        ):
            with caplog.at_level(logging.WARNING, logger="reflections.auditing"):
                auditing._send_log_review_telegram("summary", ["[proj] finding"])

        assert any("unexpected error" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------
# _collect_sentry_counts
# --------------------------------------------------------------------------


class TestCollectSentryCounts:
    """Per-project Sentry helper must return None on every error path."""

    def test_returns_none_when_sentry_cli_missing(self):
        """Missing sentry-cli on PATH → None (no subprocess invocation)."""
        with patch("reflections.auditing.shutil.which", return_value=None):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": "/tmp"}
            )
        assert result is None

    def test_returns_none_when_dsn_missing(self, tmp_path):
        """Project with no SENTRY_DSN in .env → None."""
        # Make sentry-cli "available" but ensure no .env file exists.
        with patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_dsn_blank(self, tmp_path):
        """Empty SENTRY_DSN= line → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=\nOTHER=value\n")
        with patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_subprocess_times_out(self, tmp_path):
        """sentry-cli hanging → TimeoutExpired → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"),
            patch(
                "reflections.auditing.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="sentry-cli", timeout=10),
            ),
        ):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_when_subprocess_returns_nonzero(self, tmp_path):
        """sentry-cli exits non-zero → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"),
            patch(
                "reflections.auditing.subprocess.run",
                return_value=MagicMock(returncode=1, stdout="", stderr="auth failed"),
            ),
        ):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        """sentry-cli prints non-JSON → JSONDecodeError → None."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"),
            patch(
                "reflections.auditing.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="not json{{{", stderr=""),
            ),
        ):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None

    def test_returns_summary_string_on_success(self, tmp_path):
        """Happy path: returns one-line summary with project slug and count."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        issues = [{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]
        with (
            patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"),
            patch(
                "reflections.auditing.subprocess.run",
                return_value=MagicMock(returncode=0, stdout=json.dumps(issues), stderr=""),
            ),
        ):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is not None
        assert "[proj-a]" in result
        assert "3 unresolved" in result

    def test_returns_none_on_zero_unresolved(self, tmp_path):
        """When unresolved count is 0, return None (no noise in findings)."""
        env = tmp_path / ".env"
        env.write_text("SENTRY_DSN=https://abc@sentry.io/123\n")
        with (
            patch("reflections.auditing.shutil.which", return_value="/usr/local/bin/sentry-cli"),
            patch(
                "reflections.auditing.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="[]", stderr=""),
            ),
        ):
            result = auditing._collect_sentry_counts(
                {"slug": "proj-a", "working_directory": str(tmp_path)}
            )
        assert result is None


# --------------------------------------------------------------------------
# run_log_review — end-to-end (mocked filesystem and subprocess)
# --------------------------------------------------------------------------


class TestRunLogReviewIntegration:
    """run_log_review must keep returning a dict even when Telegram delivery fails."""

    def test_returns_dict_with_status_ok_when_telegram_missing(self):
        """Missing valor-telegram does NOT prevent the function from returning."""
        with (
            patch("reflections.auditing.load_local_projects", return_value=[]),
            patch(
                "reflections.auditing.subprocess.run",
                side_effect=FileNotFoundError("valor-telegram"),
            ),
        ):
            result = auditing.run_log_review()

        assert result["status"] == "ok"
        assert "findings" in result
        assert "summary" in result

    def test_returns_dict_with_status_ok_when_telegram_times_out(self):
        """A subprocess timeout does NOT prevent the function from returning."""
        with (
            patch("reflections.auditing.load_local_projects", return_value=[]),
            patch(
                "reflections.auditing.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="valor-telegram", timeout=10),
            ),
        ):
            result = auditing.run_log_review()

        assert result["status"] == "ok"
        assert isinstance(result["findings"], list)
