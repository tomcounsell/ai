from unittest.mock import MagicMock, patch

from django.core.mail import EmailMessage
from django.test import TestCase

from apps.common.models.email import Email
from apps.common.tests.factories import UploadFactory


class EmailModelTestCase(TestCase):
    """Test case for the Email model."""

    def setUp(self):
        self.email = Email.objects.create(
            to_address="test@example.com",
            from_address="sender@example.com",
            subject="Test Email",
            body="This is a test email body.",
        )

    def test_email_creation(self):
        """Test basic email creation."""
        self.assertEqual(self.email.to_address, "test@example.com")
        self.assertEqual(self.email.from_address, "sender@example.com")
        self.assertEqual(self.email.subject, "Test Email")
        self.assertEqual(self.email.body, "This is a test email body.")
        self.assertEqual(self.email.type, Email.NOTIFICATION)

    def test_create_subject(self):
        """Test subject line generation."""
        self.email.subject = ""
        subject = self.email.createSubject()
        self.assertEqual(subject, "Notification")

    def test_create_body(self):
        """Test email body creation."""
        self.email.body = ""
        body = self.email.createBody()
        self.assertEqual(body, "")

    @patch("apps.common.models.email.EmailMessage")
    def test_create_message_object(self, mock_email_message):
        """Test email message object creation."""
        mock_instance = MagicMock()
        mock_email_message.return_value = mock_instance

        message = self.email.createMessageObject()

        mock_email_message.assert_called_once()
        self.assertEqual(message, mock_instance)

    @patch("apps.common.models.email.Email.send")
    def test_send_to_user(self, mock_send):
        """Test sending email to a user."""
        user = MagicMock()
        user.email = "user@example.com"

        self.email.sendToUser(user)

        self.assertEqual(self.email.to_address, "user@example.com")
        mock_send.assert_called_once()

    @patch("apps.common.models.email.Email.send_now")
    @patch("apps.common.models.email.Email.send_later")
    @patch("apps.common.models.email.EmailMessage")
    def test_send_with_confirmation(
        self, mock_email_message, mock_send_later, mock_send_now
    ):
        """Test sending email with confirmation."""
        mock_instance = MagicMock()
        mock_email_message.return_value = mock_instance

        # Test with confirmation required
        self.email.send(require_confirmation=True)

        mock_send_now.assert_called_once()
        mock_send_later.assert_not_called()

    @patch("apps.common.models.email.Email.send_now")
    @patch("apps.common.models.email.Email.send_later")
    @patch("apps.common.models.email.EmailMessage")
    def test_send_without_confirmation(
        self, mock_email_message, mock_send_later, mock_send_now
    ):
        """Test sending email without confirmation."""
        mock_instance = MagicMock()
        mock_email_message.return_value = mock_instance

        # Test without confirmation
        self.email.send(require_confirmation=False)

        mock_send_now.assert_not_called()
        mock_send_later.assert_called_once()

    @patch("apps.common.models.email.EmailMessage.send")
    def test_send_now(self, mock_send):
        """Test sending email immediately."""
        mock_send.return_value = None

        # First create the email message attribute
        self.email.email = EmailMessage()

        result = self.email.send_now()

        self.assertTrue(result)
        self.assertIsNotNone(self.email.sent_at)
        mock_send.assert_called_once_with(fail_silently=False)

    @patch("apps.common.models.email.EmailMessage.send")
    def test_send_now_with_error(self, mock_send):
        """Test sending email immediately with error."""
        mock_send.side_effect = Exception("Error sending email")

        # Create the email message attribute first
        self.email.email = EmailMessage()

        result = self.email.send_now()

        self.assertFalse(result)
        self.assertIsNone(self.email.sent_at)
        mock_send.assert_called_once_with(fail_silently=False)

    @patch("apps.common.models.email.urllib.request.urlopen")
    @patch("apps.common.models.email.EmailMessage")
    def test_send_with_attachments(self, mock_email_message, mock_urlopen):
        """Test sending email with attachments."""
        # Create an upload attachment with PDF metadata
        upload = UploadFactory.create()
        # Update meta_data to include PDF extension
        meta_data = upload.meta_data or {}
        meta_data["ext"] = "pdf"
        upload.meta_data = meta_data
        upload.save()

        self.email.attachments.add(upload)

        mock_instance = MagicMock()
        mock_email_message.return_value = mock_instance

        mock_urlopen_instance = MagicMock()
        mock_urlopen_instance.read.return_value = b"file content"
        mock_urlopen.return_value = mock_urlopen_instance

        self.email.send(require_confirmation=False)

        # Check if attach was called
        mock_instance.attach.assert_called_once()
