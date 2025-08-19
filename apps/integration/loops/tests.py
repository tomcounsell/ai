from unittest import mock

from django.test import TestCase, override_settings

from apps.integration.loops.client import LoopsClient
from apps.integration.loops.shortcuts import send_waiver_signing_request


class LoopsClientTestCase(TestCase):
    @override_settings(DEBUG=True)
    def test_debug_mode_disables_email_sending(self):
        """Test that in DEBUG mode, emails are not sent but logged instead"""
        client = LoopsClient()

        with mock.patch("logging.Logger.info") as mock_logger:
            # Test transactional email
            result = client.transactional_email(
                to_email="test@example.com",
                transactional_id="test_id",
                data_variables={"key": "value"},
            )

            # Check that the method returns success
            self.assertTrue(result.get("success"))

            # Check that logging was called
            self.assertTrue(mock_logger.called)
            mock_logger.reset_mock()

            # Test event
            result = client.event(to_email="test@example.com", event_name="test_event")

            # Check that the method returns success
            self.assertTrue(result.get("success"))

            # Check that logging was called
            self.assertTrue(mock_logger.called)

    @override_settings(DEBUG=False)
    def test_transactional_email_with_bcc(self):
        """Test that BCC parameter is correctly passed to the API"""
        client = LoopsClient()

        # Set DEBUG to False in the client directly to override Django settings
        with mock.patch("apps.integration.loops.client.DEBUG", False):
            with mock.patch.object(client, "_make_request") as mock_request:
                mock_request.return_value = {"success": True}

                # Call with BCC parameter
                client.transactional_email(
                    to_email="test@example.com",
                    transactional_id="test_id",
                    data_variables={"key": "value"},
                    bcc=["bcc@example.com"],
                )

                # Check that _make_request was called and BCC was included
                mock_request.assert_called_once()

                # Extract the call arguments
                args, kwargs = mock_request.call_args

                # Check the method and endpoint
                self.assertEqual(kwargs["method"], "POST")
                self.assertEqual(kwargs["endpoint"], "/transactional")

                # Check the JSON payload contains the BCC field
                self.assertIn("bcc", kwargs["json"])
                self.assertEqual(kwargs["json"]["bcc"], ["bcc@example.com"])


class WaiverEmailTests(TestCase):
    def test_send_waiver_signing_request(self):
        """Test the waiver signing request function with patching."""
        # Set up mock objects
        guardian = mock.MagicMock()
        guardian.email = "guardian@example.com"
        guardian.get_full_name.return_value = "Guardian User"
        guardian.get_login_url.return_value = "https://example.com/login/token"

        child = mock.MagicMock()
        child.email = "child@example.com"
        child.get_full_name.return_value = "Child User"
        child.guardian_user = guardian
        child.id = 123

        team = mock.MagicMock()
        team.name = "Test Team"

        waiver = mock.MagicMock()
        waiver.team = team
        waiver.child_user = child

        # Use context manager for patching
        with mock.patch("apps.integration.loops.shortcuts.reverse") as mock_reverse:
            with mock.patch(
                "apps.integration.loops.shortcuts.LoopsClient"
            ) as MockLoopsClient:
                with mock.patch("apps.integration.loops.client.DEBUG", False):
                    # Set up client mock
                    mock_client = mock.MagicMock()
                    mock_client.transactional_email.return_value = {"success": True}
                    MockLoopsClient.return_value = mock_client

                    # Mock reverse URL
                    mock_reverse.return_value = "/account/waiver/123"

                    # Call the function
                    success = send_waiver_signing_request(waiver)

                    # Check the result
                    self.assertTrue(success)

                    # Check the transactional_email was called correctly
                    mock_client.transactional_email.assert_called_once()

                    # Extract the call arguments
                    args, kwargs = mock_client.transactional_email.call_args

                    # Check the arguments
                    self.assertEqual(
                        kwargs["transactional_id"], "cm8h2ly3x02kbwes1nz8clc24"
                    )
                    self.assertEqual(kwargs["to_email"], "guardian@example.com")
                    self.assertEqual(
                        kwargs["data_variables"]["guardian_name"], "Guardian User"
                    )
                    self.assertIn("waiver_link", kwargs["data_variables"])
                    self.assertEqual(
                        kwargs["data_variables"]["child_name"], "Child User"
                    )
                    self.assertEqual(kwargs["data_variables"]["team_name"], "Test Team")
