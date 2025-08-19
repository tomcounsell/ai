"""
Unit tests for the Twilio shortcuts module
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.common.models.sms import SMS
from apps.integration.twilio.shortcuts import (
    get_sms_status,
    send_sms,
    send_verification_code,
    verify_phone_number,
)


@override_settings(DEBUG=True, TWILIO_ENABLED=True, TWILIO_PHONE_NUMBER="+19876543210")
class TwilioShortcutsTestCase(TestCase):
    """Test Twilio shortcuts functions"""

    def test_send_sms_with_db_save(self):
        """Test sending SMS and saving to database"""
        # Check initial SMS count
        initial_count = SMS.objects.count()

        # Send SMS
        result = send_sms(
            to_number="+12345678901", body="Test message", save_to_db=True
        )

        # Verify SMS was saved to database
        self.assertEqual(SMS.objects.count(), initial_count + 1)
        self.assertTrue(result["success"])
        self.assertIn("sms_id", result)

        # Verify SMS record
        sms = SMS.objects.get(id=result["sms_id"])
        self.assertEqual(sms.to_number, "+12345678901")
        self.assertEqual(sms.body, "Test message")
        self.assertEqual(sms.from_number, "+19876543210")
        self.assertIsNotNone(sms.sent_at)

    def test_send_sms_without_db_save(self):
        """Test sending SMS without saving to database"""
        # Check initial SMS count
        initial_count = SMS.objects.count()

        # Send SMS without saving to DB
        result = send_sms(
            to_number="+12345678901", body="Test message", save_to_db=False
        )

        # Verify no SMS was saved
        self.assertEqual(SMS.objects.count(), initial_count)
        self.assertTrue(result["success"])
        self.assertNotIn("sms_id", result)

    @patch("apps.integration.twilio.shortcuts.TwilioClient")
    def test_verify_phone_number(self, mock_client_cls):
        """Test phone number verification shortcut"""
        # Setup mock
        mock_client = MagicMock()
        mock_client.verify_phone_number.return_value = {
            "success": True,
            "valid": True,
            "data": {"carrier": {"name": "Test"}},
        }
        mock_client_cls.return_value = mock_client

        # Call verify_phone_number
        result = verify_phone_number("+12345678901")

        # Verify result and mock call
        self.assertTrue(result["success"])
        self.assertTrue(result["valid"])
        mock_client.verify_phone_number.assert_called_once_with("+12345678901")

    def test_get_sms_status_not_found(self):
        """Test get_sms_status with non-existent SMS"""
        # Try to get status of non-existent SMS
        result = get_sms_status(999999)

        # Verify error response
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])

    def test_get_sms_status_found(self):
        """Test get_sms_status with existing SMS"""
        # Create SMS record
        sms = SMS.objects.create(
            to_number="+12345678901", body="Test message", from_number="+19876543210"
        )
        sms.external_id = "SM12345"
        sms.save()

        # Mock TwilioClient.get_message_status
        with patch("apps.integration.twilio.shortcuts.TwilioClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_message_status.return_value = {
                "success": True,
                "status": "delivered",
                "data": {"sid": "SM12345"},
            }
            mock_client_cls.return_value = mock_client

            # Get SMS status
            result = get_sms_status(sms.id)

            # Verify result
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "delivered")
            self.assertIn("sms_data", result)
            self.assertEqual(result["sms_data"]["to_number"], "+12345678901")

            # Verify mock call
            mock_client.get_message_status.assert_called_once_with("SM12345")

    def test_get_sms_status_no_external_id(self):
        """Test get_sms_status with SMS that has no external_id"""
        # Create SMS record without external_id
        sms = SMS.objects.create(
            to_number="+12345678901", body="Test message", from_number="+19876543210"
        )

        # Get SMS status
        result = get_sms_status(sms.id)

        # Verify error about missing external ID
        self.assertFalse(result["success"])
        self.assertIn("No external ID", result["error"])
        self.assertIn("sms_data", result)

    def test_send_verification_code(self):
        """Test sending verification code"""
        # Check initial SMS count
        initial_count = SMS.objects.count()

        # Send verification code
        result = send_verification_code("+12345678901", "123456")

        # Verify SMS was sent and saved
        self.assertEqual(SMS.objects.count(), initial_count + 1)
        self.assertTrue(result["success"])

        # Verify SMS content
        sms = SMS.objects.get(id=result["sms_id"])
        self.assertEqual(sms.to_number, "+12345678901")
        self.assertIn("123456", sms.body)
        self.assertIsNotNone(sms.sent_at)
