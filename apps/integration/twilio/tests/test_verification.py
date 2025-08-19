from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.integration.twilio.verification import (
    MAX_VERIFICATION_ATTEMPTS,
    VERIFICATION_ATTEMPTS_PREFIX,
    VERIFICATION_CODE_PREFIX,
    generate_verification_code,
    reset_verification_attempts,
    send_phone_verification,
    verify_phone_code,
)


@override_settings(DEBUG=True, TWILIO_ENABLED=True)
class PhoneVerificationTestCase(TestCase):
    """Test phone verification functionality"""

    def setUp(self):
        # Clear cache before each test
        cache.clear()

        # Test phone number
        self.phone_number = "+12345678901"

    def tearDown(self):
        # Clear cache after each test
        cache.clear()

    def test_generate_verification_code(self):
        """Test verification code generation"""
        # Default length
        code = generate_verification_code()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        # Custom length
        code = generate_verification_code(length=4)
        self.assertEqual(len(code), 4)
        self.assertTrue(code.isdigit())

    @patch("apps.integration.twilio.verification.verify_phone_number")
    @patch("apps.integration.twilio.verification.send_verification_code")
    def test_send_phone_verification_success(self, mock_send, mock_verify):
        """Test successful phone verification code sending"""
        # Mock phone validation
        mock_verify.return_value = {"success": True, "valid": True}

        # Mock code sending
        mock_send.return_value = {"success": True}

        # Send verification code
        result = send_phone_verification(self.phone_number)

        # Check result
        self.assertTrue(result["success"])
        self.assertIn("message", result)
        self.assertIn("expires_in", result)

        # Verify mocks were called correctly
        mock_verify.assert_called_once_with(self.phone_number)
        mock_send.assert_called_once()

        # Check that code was stored in cache
        cache_key = f"{VERIFICATION_CODE_PREFIX}{self.phone_number}"
        stored_code = cache.get(cache_key)
        self.assertIsNotNone(stored_code)

        # Code should match what was sent
        sent_code = mock_send.call_args[0][1]
        self.assertEqual(stored_code, sent_code)

    @patch("apps.integration.twilio.verification.verify_phone_number")
    def test_send_phone_verification_invalid_number(self, mock_verify):
        """Test phone verification with invalid number"""
        # Mock failed phone validation
        mock_verify.return_value = {"success": True, "valid": False}

        # Send verification code
        result = send_phone_verification(self.phone_number)

        # Check result
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Invalid phone number")

    def test_verify_phone_code_success(self):
        """Test successful verification code check"""
        # Store a test code in cache
        test_code = "123456"
        cache_key = f"{VERIFICATION_CODE_PREFIX}{self.phone_number}"
        cache.set(cache_key, test_code, 300)

        # Verify the code
        result = verify_phone_code(self.phone_number, test_code)

        # Check result
        self.assertTrue(result["success"])
        self.assertIn("message", result)

        # Code should have been removed from cache
        self.assertIsNone(cache.get(cache_key))

    def test_verify_phone_code_invalid(self):
        """Test verification with invalid code"""
        # Store a test code in cache
        test_code = "123456"
        cache_key = f"{VERIFICATION_CODE_PREFIX}{self.phone_number}"
        cache.set(cache_key, test_code, 300)

        # Verify with wrong code
        result = verify_phone_code(self.phone_number, "654321")

        # Check result
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Invalid verification code")
        self.assertIn("remaining_attempts", result)

        # Code should still be in cache
        self.assertEqual(cache.get(cache_key), test_code)

        # Attempts should be incremented
        attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{self.phone_number}"
        self.assertEqual(cache.get(attempts_key), 1)

    def test_verification_max_attempts(self):
        """Test verification lockout after max attempts"""
        # Store a test code in cache
        test_code = "123456"
        cache_key = f"{VERIFICATION_CODE_PREFIX}{self.phone_number}"
        cache.set(cache_key, test_code, 300)

        # Simulate reaching max attempts
        attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{self.phone_number}"
        cache.set(attempts_key, MAX_VERIFICATION_ATTEMPTS, 300)

        # Attempt to verify
        result = verify_phone_code(self.phone_number, "wrong_code")

        # Check result
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertIn("locked", result["error"].lower())
        self.assertIn("locked_until", result)

    def test_reset_verification_attempts(self):
        """Test resetting verification attempts"""
        # Set attempts in cache
        attempts_key = f"{VERIFICATION_ATTEMPTS_PREFIX}{self.phone_number}"
        cache.set(attempts_key, 3, 300)

        # Reset attempts
        reset_verification_attempts(self.phone_number)

        # Attempts should be cleared
        self.assertIsNone(cache.get(attempts_key))

    def test_verify_expired_code(self):
        """Test verification with expired code"""
        # Try to verify a code that doesn't exist in cache
        result = verify_phone_code(self.phone_number, "123456")

        # Check result
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertIn("expired", result["error"].lower())
