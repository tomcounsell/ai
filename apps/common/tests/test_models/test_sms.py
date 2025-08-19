from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.common.models.sms import SMS


class SMSModelTestCase(TestCase):
    """Test case for the SMS model."""

    def setUp(self):
        self.sms = SMS.objects.create(
            to_number="+1234567890",
            from_number="+0987654321",
            body="This is a test SMS message.",
        )

    def test_sms_creation(self):
        """Test basic SMS creation."""
        self.assertEqual(self.sms.to_number, "+1234567890")
        self.assertEqual(self.sms.from_number, "+0987654321")
        self.assertEqual(self.sms.body, "This is a test SMS message.")

        # Test timestamp fields
        self.assertIsNotNone(self.sms.created_at)
        self.assertIsNotNone(self.sms.modified_at)

        # These should be None when first created
        self.assertIsNone(self.sms.sent_at)
        self.assertIsNone(self.sms.read_at)

        # Twilio integration fields should be None
        self.assertIsNone(self.sms.external_id)
        self.assertIsNone(self.sms.status)
        self.assertIsNone(self.sms.error_code)
        self.assertIsNone(self.sms.error_message)

    def test_mark_as_sent(self):
        """Test marking SMS as sent."""
        # Initially sent_at is None
        self.assertIsNone(self.sms.sent_at)

        # Mark as sent
        self.sms.mark_as_sent()

        # Now sent_at should have a value
        self.assertIsNotNone(self.sms.sent_at)

        # Sent time should be close to now
        now = timezone.now()
        self.assertLess(
            (now - self.sms.sent_at).total_seconds(), 10
        )  # Within 10 seconds

    def test_mark_as_read(self):
        """Test marking SMS as read."""
        # Initially read_at is None
        self.assertIsNone(self.sms.read_at)

        # Mark as read
        self.sms.mark_as_read()

        # Now read_at should have a value
        self.assertIsNotNone(self.sms.read_at)

        # Read time should be close to now
        now = timezone.now()
        self.assertLess(
            (now - self.sms.read_at).total_seconds(), 10
        )  # Within 10 seconds

    def test_string_representation(self):
        """Test the string representation of SMS."""
        expected = "SMS to +1234567890: This is a test SMS message."
        self.assertEqual(str(self.sms), expected)

    def test_update_status(self):
        """Test updating SMS status."""
        # Update status
        self.sms.update_status("sent")

        # Check that status was updated
        self.assertEqual(self.sms.status, "sent")
        self.assertIsNone(self.sms.error_code)
        self.assertIsNone(self.sms.error_message)

        # Update with error info
        self.sms.update_status("failed", "30001", "Invalid destination number")

        # Check that all fields were updated
        self.assertEqual(self.sms.status, "failed")
        self.assertEqual(self.sms.error_code, "30001")
        self.assertEqual(self.sms.error_message, "Invalid destination number")

    def test_update_status_delivered_marks_as_read(self):
        """Test that updating status to delivered marks SMS as read."""
        # Initially read_at is None
        self.assertIsNone(self.sms.read_at)

        # Update status to delivered
        self.sms.update_status("delivered")

        # Check that status was updated and SMS marked as read
        self.assertEqual(self.sms.status, "delivered")
        self.assertIsNotNone(self.sms.read_at)

    @override_settings(DEBUG=True, TWILIO_ENABLED=True)
    @patch("apps.integration.twilio.shortcuts.send_sms")
    def test_send_with_twilio_integration(self, mock_send_sms):
        """Test sending SMS via Twilio integration."""
        # Setup mock response
        mock_send_sms.return_value = {
            "success": True,
            "sid": "SM12345678901234567890123456789012",
            "status": "queued",
        }

        # Send the SMS
        result = self.sms.send()

        # Check that send_sms was called with correct parameters
        mock_send_sms.assert_called_once_with(
            to_number=self.sms.to_number,
            body=self.sms.body,
            from_number=self.sms.from_number,
            save_to_db=False,
        )

        # Check result
        self.assertTrue(result["success"])

        # Check that SMS fields were updated
        self.assertEqual(self.sms.external_id, "SM12345678901234567890123456789012")
        self.assertEqual(self.sms.status, "queued")
        self.assertIsNotNone(self.sms.sent_at)

    @override_settings(DEBUG=True, TWILIO_ENABLED=True)
    @patch("apps.integration.twilio.shortcuts.send_sms")
    def test_send_error_handling(self, mock_send_sms):
        """Test error handling when sending SMS."""
        # Setup mock response
        mock_send_sms.return_value = {"success": False, "error": "Test error"}

        # Send the SMS
        result = self.sms.send()

        # Check result
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Test error")

        # SMS should not be marked as sent
        self.assertIsNone(self.sms.sent_at)

    def test_send_validation(self):
        """Test validation when sending SMS."""
        # Create SMS without recipient
        sms = SMS.objects.create(body="Test message", from_number="+0987654321")

        # Send should fail
        result = sms.send()
        self.assertFalse(result["success"])
        self.assertIn("no recipient", result["error"].lower())

        # Create SMS without body
        sms = SMS.objects.create(
            to_number="+1234567890", from_number="+0987654321", body=""
        )

        # Send should fail
        result = sms.send()
        self.assertFalse(result["success"])
        self.assertIn("no message body", result["error"].lower())
