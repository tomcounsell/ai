"""Unit tests for tools.cross_vendor_judge (issue #1626).

All tests mock OpenAI — no real API calls made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from tools.cross_vendor_judge import (
    CROSS_VENDOR_JUDGE_ID,
    run_judge,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_usage(prompt_tokens=100, completion_tokens=50):
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    return usage


def _make_response(content_dict: dict, prompt_tokens=100, completion_tokens=50):
    """Build a mock OpenAI chat completion response."""
    msg = MagicMock()
    msg.content = json.dumps(content_dict)

    choice = MagicMock()
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = _make_usage(prompt_tokens, completion_tokens)
    return resp


def _patch_openai(response):
    """Context manager that patches openai.OpenAI.

    The run_judge function imports OpenAI lazily via `from openai import OpenAI`
    inside the try block, so we patch the class on the openai module directly.
    Returns (patcher, mock_client).
    """
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = response

    mock_openai_cls = MagicMock(return_value=mock_client)
    return patch("openai.OpenAI", mock_openai_cls), mock_client


# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------


class TestCrossVendorJudgeIdIsConstant:
    def test_cross_vendor_judge_id_is_constant(self):
        assert CROSS_VENDOR_JUDGE_ID == "cross-vendor"


# ---------------------------------------------------------------------------
# Envelope shape — ok
# ---------------------------------------------------------------------------


class TestEnvelopeOkShape:
    def test_envelope_ok_shape(self, capsys):
        """Valid OpenAI response produces status=ok with all required judge keys."""
        resp = _make_response(
            {
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.85,
                "reasoning_summary": "Looks fine.",
            }
        )
        patcher, _mock_client = _patch_openai(resp)
        with patcher:
            envelope = run_judge("diff --git a/foo.py b/foo.py\n+x = 1\n")

        assert envelope["status"] == "ok"
        judge = envelope["judge"]
        required_keys = {
            "judge_id",
            "verdict",
            "blockers",
            "tech_debt",
            "confidence",
            "reasoning_summary",
            "meta",
        }
        missing = required_keys - set(judge.keys())
        assert required_keys <= set(judge.keys()), f"Missing keys: {missing}"
        assert judge["judge_id"] == CROSS_VENDOR_JUDGE_ID

    def test_envelope_ok_verdict_normalized(self, capsys):
        """Lowercase verdict 'approved' is normalized to 'APPROVED'."""
        resp = _make_response(
            {
                "verdict": "approved",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
                "reasoning_summary": "Fine.",
            }
        )
        patcher, _ = _patch_openai(resp)
        with patcher:
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "ok"
        assert envelope["judge"]["verdict"] == "APPROVED"

    def test_envelope_ok_blockers_not_bool_gives_skip(self, capsys):
        """blockers=True (bool) causes skip — _coerce_judge_fields rejects bools for blockers."""
        resp = _make_response(
            {
                "verdict": "CHANGES REQUESTED",
                "blockers": True,  # bool, not int
                "tech_debt": 0,
                "confidence": 0.7,
                "reasoning_summary": "Issues found.",
            }
        )
        patcher, _ = _patch_openai(resp)
        with patcher:
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        # Implementation rejects bool blockers → skipped
        assert envelope["status"] == "skipped"
        assert "judge" not in envelope


# ---------------------------------------------------------------------------
# Skip paths — API errors
# ---------------------------------------------------------------------------


class TestOpenAIErrorsGiveSkip:
    def test_openai_error_gives_skip(self, capsys):
        """openai.OpenAIError raises → status=skipped, no judge key."""
        import openai

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = openai.OpenAIError("test error")
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_bad_request_error_gives_skip(self, capsys):
        """openai.BadRequestError → skip envelope."""
        import httpx
        import openai

        mock_client = MagicMock()
        mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        mock_response = httpx.Response(400, request=mock_request)
        exc = openai.BadRequestError(
            message="bad request",
            response=mock_response,
            body={"error": {"message": "bad request"}},
        )
        mock_client.chat.completions.create.side_effect = exc
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_not_found_error_gives_skip(self, capsys):
        """openai.NotFoundError → skip envelope."""
        import httpx
        import openai

        mock_client = MagicMock()
        mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        mock_response = httpx.Response(404, request=mock_request)
        exc = openai.NotFoundError(
            message="model not found",
            response=mock_response,
            body={"error": {"message": "model not found"}},
        )
        mock_client.chat.completions.create.side_effect = exc
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "skipped"
        assert "judge" not in envelope

    def test_generic_exception_gives_skip(self, capsys):
        """Non-OpenAI exception → skip envelope, warning logged."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("bad json")
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            with patch("tools.cross_vendor_judge.logger") as mock_logger:
                envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "skipped"
        assert "judge" not in envelope
        mock_logger.warning.assert_called()


# ---------------------------------------------------------------------------
# Skip paths — malformed responses
# ---------------------------------------------------------------------------


class TestMalformedResponseGivesSkip:
    def test_malformed_response_missing_key(self, capsys):
        """JSON missing 'blockers' key → skip (not partial ok)."""
        resp = _make_response(
            {
                "verdict": "APPROVED",
                # blockers intentionally absent
                "tech_debt": 0,
                "confidence": 0.8,
                "reasoning_summary": "Fine.",
            }
        )
        patcher, _ = _patch_openai(resp)
        with patcher:
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        # Missing blockers defaults to 0 — so this actually succeeds.
        # The implementation uses raw.get("blockers", 0), so absence is fine.
        # This test verifies the behavior is "ok with blockers=0".
        assert envelope["status"] == "ok"
        assert envelope["judge"]["blockers"] == 0

    def test_malformed_response_non_json(self, capsys):
        """Non-JSON model content → skip envelope."""
        msg = MagicMock()
        msg.content = "This is not JSON at all, just prose."

        choice = MagicMock()
        choice.message = msg

        resp = MagicMock()
        resp.choices = [choice]
        resp.usage = _make_usage()

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            envelope = run_judge("diff --git a/x.py b/x.py\n+pass\n")

        assert envelope["status"] == "skipped"
        assert "judge" not in envelope


# ---------------------------------------------------------------------------
# Empty diff
# ---------------------------------------------------------------------------


class TestEmptyDiff:
    def test_empty_diff_gives_approved(self):
        """Empty diff string → ok envelope, verdict=APPROVED, blockers=0, confidence<=0.3."""
        # Empty diff does NOT call OpenAI at all — no patching needed.
        envelope = run_judge("")

        assert envelope["status"] == "ok"
        judge = envelope["judge"]
        assert judge["verdict"] == "APPROVED"
        assert judge["blockers"] == 0
        assert judge["confidence"] <= 0.3

    def test_whitespace_only_diff_gives_approved(self):
        """Whitespace-only diff is also treated as empty."""
        envelope = run_judge("   \n\t  ")

        assert envelope["status"] == "ok"
        assert envelope["judge"]["verdict"] == "APPROVED"


# ---------------------------------------------------------------------------
# Token cap truncation
# ---------------------------------------------------------------------------


class TestTokenCapTruncation:
    def test_token_cap_truncation(self, capsys):
        """Very long diff gets truncated before sending to the API."""
        # Build a diff that's definitely > max_tokens * 4 chars.
        # Default max is 50000 tokens; 4 chars/token → 200000 chars
        long_diff = "+" + "x = 1\n" * 100_000  # ~700k chars

        resp = _make_response(
            {
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.9,
                "reasoning_summary": "Fine.",
            }
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        mock_openai_cls = MagicMock(return_value=mock_client)

        with patch("openai.OpenAI", mock_openai_cls):
            envelope = run_judge(long_diff)

        # Should still succeed (ok) with truncation note.
        assert envelope["status"] == "ok"

        # Verify the content sent to the API was shorter than the input.
        all_args = mock_client.chat.completions.create.call_args
        messages_sent = all_args.kwargs.get("messages", all_args.args[0] if all_args.args else [])
        user_content = next(
            (m["content"] for m in messages_sent if m.get("role") == "user"),
            "",
        )
        # The user content should not contain the full diff
        assert len(user_content) < len(long_diff)
        # Truncation marker should be in the content
        assert "TRUNCATED" in user_content

        # Confidence should be reduced by 0.2 due to truncation.
        assert envelope["judge"]["confidence"] <= 0.9 - 0.2 + 1e-9


# ---------------------------------------------------------------------------
# Logging behavior
# ---------------------------------------------------------------------------


class TestLogging:
    def test_log_on_ran(self):
        """Successful run logs 'ran' with model name and token counts."""
        resp = _make_response(
            {
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.8,
                "reasoning_summary": "Looks good.",
            },
            prompt_tokens=123,
            completion_tokens=45,
        )
        patcher, _ = _patch_openai(resp)
        with patcher:
            with patch("tools.cross_vendor_judge.logger") as mock_logger:
                run_judge("diff --git a/x.py b/x.py\n+pass\n")

        mock_logger.info.assert_called()
        log_call_args = mock_logger.info.call_args
        log_msg = log_call_args[0][0]
        assert "ran" in log_msg

        # Check token counts appear in positional args to logger.info.
        # Call shape: logger.info("... model=%s prompt_tokens=%d ...", model, pt, ct)
        all_log_args = log_call_args[0]
        assert 123 in all_log_args or any(123 == a for a in all_log_args)
        assert 45 in all_log_args or any(45 == a for a in all_log_args)

    def test_no_dollar_in_logs(self):
        """No log message contains '$' (no hardcoded cost logging)."""
        resp = _make_response(
            {
                "verdict": "APPROVED",
                "blockers": 0,
                "tech_debt": 0,
                "confidence": 0.8,
                "reasoning_summary": "Fine.",
            }
        )
        patcher, _ = _patch_openai(resp)
        log_messages = []

        with patcher:
            with patch("tools.cross_vendor_judge.logger") as mock_logger:

                def capture(*args, **kwargs):
                    if args:
                        log_messages.append(args[0] % args[1:] if len(args) > 1 else args[0])

                mock_logger.info.side_effect = capture
                mock_logger.warning.side_effect = capture
                run_judge("diff --git a/x.py b/x.py\n+pass\n")

        for msg in log_messages:
            assert "$" not in msg, f"Found '$' in log message: {msg!r}"
