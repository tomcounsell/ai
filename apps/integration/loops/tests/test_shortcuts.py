"""
Unit tests for the Loops shortcuts
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.common.models import User
from apps.integration.loops.shortcuts import (
    send_login_code_email,
    send_password_reset_email,
)


class LoopsShortcutsTestCase(TestCase):
    """Test Loops shortcuts"""

    def setUp(self):
        super().setUp()
        self.user = MagicMock(spec=User)
        self.user.email = "test@example.com"
        self.user.get_full_name.return_value = "Test User"
        self.user.get_login_url.return_value = "https://example.com/login/code123"

    @patch("apps.integration.loops.shortcuts.LoopsClient")
    def test_send_password_reset_email(self, mock_client_class):
        """Test send_password_reset_email shortcut"""
        # Setup mock
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Call function
        send_password_reset_email(self.user, "https://example.com/reset/token123")

        # Verify
        mock_client.transactional_email.assert_called_once_with(
            to_email=self.user.email,
            transactional_id="__loops_email_id__",
            data_variables={
                "passwordresetlink": "https://example.com/reset/token123",
            },
        )

    @patch("apps.integration.loops.shortcuts.LoopsClient")
    def test_send_login_code_email(self, mock_client_class):
        """Test send_login_code_email shortcut"""
        # Setup mock
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Call function
        send_login_code_email(self.user)

        # Verify
        mock_client.transactional_email.assert_called_once_with(
            to_email=self.user.email,
            transactional_id="__loops_login_code_id__",
            data_variables={
                "login_url": "https://example.com/login/code123",
            },
        )

    @patch("apps.integration.loops.shortcuts.LoopsClient")
    def test_send_login_code_email_with_next(self, mock_client_class):
        """Test send_login_code_email with next parameter"""
        # Setup mock
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Update the user mock for this test
        next_url = "/dashboard"
        self.user.get_login_url.return_value = (
            f"https://example.com/login/code123?next={next_url}"
        )

        # Call function
        send_login_code_email(self.user, next_url)

        # Verify
        mock_client.transactional_email.assert_called_once()
        call_args = mock_client.transactional_email.call_args
        assert call_args[1]["to_email"] == self.user.email
        assert call_args[1]["transactional_id"] == "__loops_login_code_id__"
        assert (
            call_args[1]["data_variables"]["login_url"]
            == f"https://example.com/login/code123?next={next_url}"
        )
