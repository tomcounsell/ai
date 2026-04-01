"""Tests for CLI syntax leak sanitizer in bridge/response.py."""

from bridge.response import _sanitize_cli_leaks


class TestSanitizeCliLeaks:
    def test_strips_valor_telegram_send(self):
        text = 'Here is the answer.\nvalor-telegram send --chat "Dev: Valor" "Hello"\nDone.'
        result = _sanitize_cli_leaks(text)
        assert "valor-telegram send" not in result
        assert "Here is the answer." in result
        assert "Done." in result

    def test_strips_valor_telegram_chat_flag(self):
        text = 'valor-telegram --chat "PM: Project" "message"'
        result = _sanitize_cli_leaks(text)
        assert "valor-telegram" not in result
        # Should return fallback when everything is stripped
        assert result == "Done."

    def test_preserves_normal_text(self):
        text = "The feature works by routing messages through the bridge."
        result = _sanitize_cli_leaks(text)
        assert result == text

    def test_handles_empty_string(self):
        assert _sanitize_cli_leaks("") == ""

    def test_handles_none(self):
        assert _sanitize_cli_leaks(None) == ""

    def test_preserves_legitimate_cli_discussion(self):
        """Mentioning CLI tools in prose should not be stripped."""
        text = "You can use the valor-telegram read command to check history."
        result = _sanitize_cli_leaks(text)
        assert "valor-telegram read" in result

    def test_collapses_blank_lines(self):
        text = 'Line one.\n\n\nvalor-telegram send --chat "X" "Y"\n\n\nLine two.'
        result = _sanitize_cli_leaks(text)
        assert "\n\n\n" not in result
        assert "Line one." in result
        assert "Line two." in result

    def test_mixed_content(self):
        text = (
            "I checked the logs.\n"
            'valor-telegram send --chat "Dev: Valor" "status update"\n'
            "Everything looks good."
        )
        result = _sanitize_cli_leaks(text)
        assert "valor-telegram send" not in result
        assert "I checked the logs." in result
        assert "Everything looks good." in result
