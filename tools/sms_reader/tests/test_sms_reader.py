"""Tests for SMS reader tool."""

import pytest
from pathlib import Path

# Import directly to avoid __init__.py chain issues
import importlib.util
sms_reader_path = Path(__file__).parent.parent / "__init__.py"
spec = importlib.util.spec_from_file_location("sms_reader", sms_reader_path)
sms_reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sms_reader)

extract_codes_from_text = sms_reader.extract_codes_from_text
_apple_time_to_datetime = sms_reader._apple_time_to_datetime
_datetime_to_apple_time = sms_reader._datetime_to_apple_time
get_recent_messages = sms_reader.get_recent_messages
list_senders = sms_reader.list_senders
get_latest_2fa_code = sms_reader.get_latest_2fa_code
MESSAGES_DB_PATH = sms_reader.MESSAGES_DB_PATH


class TestCodeExtraction:
    """Test 2FA code extraction from message text."""

    def test_extract_code_after_keyword(self):
        """Test extracting code that follows a keyword."""
        assert extract_codes_from_text("Your verification code is 123456") == ["123456"]
        assert extract_codes_from_text("Code: 9999") == ["9999"]
        assert extract_codes_from_text("Your OTP is 654321") == ["654321"]

    def test_extract_code_before_keyword(self):
        """Test extracting code that precedes 'is your code'."""
        assert extract_codes_from_text("123456 is your code") == ["123456"]
        assert extract_codes_from_text("789012 is your verification code") == ["789012"]

    def test_extract_standalone_numeric_code(self):
        """Test extracting standalone numeric codes."""
        assert extract_codes_from_text("Use 789012 to verify") == ["789012"]
        assert extract_codes_from_text("Enter 4285 now") == ["4285"]

    def test_extract_alphanumeric_code(self):
        """Test extracting alphanumeric codes with digits."""
        assert extract_codes_from_text("Your code is ABC123") == ["ABC123"]
        assert extract_codes_from_text("Code: A1B2C3") == ["A1B2C3"]

    def test_no_extract_pure_alpha(self):
        """Test that pure alphabetic strings are not extracted as codes."""
        # "CODE" alone should not be extracted
        codes = extract_codes_from_text("Your CODE is ready")
        assert "CODE" not in codes

    def test_no_extract_years(self):
        """Test that years are not extracted as codes."""
        assert extract_codes_from_text("Year 2024 is not a code") == []
        assert extract_codes_from_text("Since 1999 this has worked") == []

    def test_no_extract_long_hash(self):
        """Test that long hashes are not extracted."""
        assert extract_codes_from_text("c5cb4ef3540e31851fcd832b67e7ca87") == []

    def test_extract_multiple_codes(self):
        """Test extracting when multiple codes are present."""
        codes = extract_codes_from_text("Code: 1234 or alternatively 5678")
        assert "1234" in codes

    def test_extract_from_real_messages(self):
        """Test extraction from real-world message formats."""
        assert extract_codes_from_text("GitHub: 647832 is your verification code") == ["647832"]
        assert extract_codes_from_text("Your Uber code is 4285") == ["4285"]
        assert extract_codes_from_text("Google verification code: 891234") == ["891234"]


class TestAppleTimeConversion:
    """Test Apple epoch time conversion."""

    def test_apple_to_datetime_conversion(self):
        """Test converting Apple time to datetime."""
        from datetime import datetime

        # Known timestamp: 2024-01-01 00:00:00 UTC
        # Unix: 1704067200
        # Apple: (1704067200 - 978307200) * 1e9 = 725760000000000000
        apple_time = 725760000000000000
        dt = _apple_time_to_datetime(apple_time)
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1

    def test_datetime_to_apple_conversion(self):
        """Test converting datetime to Apple time."""
        from datetime import datetime

        dt = datetime(2024, 1, 1, 0, 0, 0)
        apple_time = _datetime_to_apple_time(dt)
        # Should be approximately 725760000000000000 (may vary by timezone)
        assert apple_time > 700000000000000000
        assert apple_time < 800000000000000000

    def test_roundtrip_conversion(self):
        """Test that conversion roundtrips correctly."""
        from datetime import datetime

        original = datetime(2024, 6, 15, 12, 30, 45)
        apple_time = _datetime_to_apple_time(original)
        recovered = _apple_time_to_datetime(apple_time)
        assert recovered is not None
        # Allow 1 second tolerance for floating point
        assert abs((recovered - original).total_seconds()) < 1

    def test_none_handling(self):
        """Test that None/0 times return None."""
        assert _apple_time_to_datetime(None) is None
        assert _apple_time_to_datetime(0) is None


class TestDatabaseAccess:
    """Test actual database access (requires Messages database)."""

    @pytest.fixture
    def db_available(self):
        """Check if Messages database is available."""
        if not MESSAGES_DB_PATH.exists():
            pytest.skip("Messages database not found")
        return True

    def test_get_recent_messages(self, db_available):
        """Test retrieving recent messages."""
        messages = get_recent_messages(limit=5)
        # Should return a list (may be empty if no messages)
        assert isinstance(messages, list)
        if messages:
            # Check structure
            msg = messages[0]
            assert "rowid" in msg
            assert "text" in msg
            assert "sender" in msg
            assert "date" in msg

    def test_list_senders(self, db_available):
        """Test listing message senders."""
        senders = list_senders(limit=10)
        assert isinstance(senders, list)
        if senders:
            sender = senders[0]
            assert "sender" in sender
            assert "message_count" in sender

    def test_get_2fa_code_returns_dict_or_none(self, db_available):
        """Test that get_latest_2fa_code returns correct type."""
        result = get_latest_2fa_code(minutes=60)
        assert result is None or isinstance(result, dict)
        if result:
            assert "code" in result
            assert "message" in result
            assert "sender" in result


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_text(self):
        """Test extracting from empty text."""
        assert extract_codes_from_text("") == []
        assert extract_codes_from_text(None) == []

    def test_whitespace_only(self):
        """Test extracting from whitespace-only text."""
        assert extract_codes_from_text("   ") == []
        assert extract_codes_from_text("\n\t") == []

    def test_code_at_boundaries(self):
        """Test codes at start/end of text."""
        assert extract_codes_from_text("123456") == ["123456"]
        assert extract_codes_from_text("Code: 123456.") == ["123456"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
