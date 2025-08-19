"""
Unit tests for Stripe shortcuts.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.common.models import User
from apps.integration.stripe.shortcuts import (
    cancel_user_subscription,
    create_checkout_session,
    create_customer_from_user,
    create_subscription_checkout,
    handle_webhook_event,
)


class StripeShortcutsTestCase(TestCase):
    """Test Stripe shortcuts."""

    def setUp(self):
        super().setUp()
        # Create a test user
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="password123"
        )

        # Setup patches
        self.client_patcher = patch("apps.integration.stripe.shortcuts.StripeClient")
        self.mock_client_class = self.client_patcher.start()
        self.mock_client = MagicMock()
        self.mock_client_class.return_value = self.mock_client

    def tearDown(self):
        self.client_patcher.stop()
        super().tearDown()

    def test_create_checkout_session(self):
        """Test create_checkout_session shortcut."""
        # Mock client response
        self.mock_client.create_checkout_session.return_value = {
            "success": True,
            "session": {
                "id": "cs_test123",
                "url": "https://checkout.stripe.com/pay/cs_test123",
            },
        }

        # Call shortcut without user
        result = create_checkout_session(
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            customer_email="customer@example.com",
        )

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["session"]["id"], "cs_test123")

        # Verify client was called correctly
        self.mock_client.create_checkout_session.assert_called_once()
        call_kwargs = self.mock_client.create_checkout_session.call_args[1]
        self.assertEqual(call_kwargs["price_id"], "price_test123")
        self.assertEqual(call_kwargs["success_url"], "https://example.com/success")
        self.assertEqual(call_kwargs["cancel_url"], "https://example.com/cancel")
        self.assertEqual(call_kwargs["customer_email"], "customer@example.com")

        # Reset mock
        self.mock_client.create_checkout_session.reset_mock()

        # Test with user
        result = create_checkout_session(price_id="price_test123", user=self.user)

        # Verify client was called with user data
        call_kwargs = self.mock_client.create_checkout_session.call_args[1]
        self.assertEqual(call_kwargs["customer_email"], "test@example.com")
        self.assertEqual(call_kwargs["metadata"]["user_id"], str(self.user.id))
        self.assertEqual(call_kwargs["metadata"]["user_email"], "test@example.com")

    def test_create_subscription_checkout(self):
        """Test create_subscription_checkout shortcut."""
        # Mock client response
        self.mock_client.create_checkout_session.return_value = {
            "success": True,
            "session": {
                "id": "cs_sub_test123",
                "url": "https://checkout.stripe.com/pay/cs_sub_test123",
            },
        }

        # Call shortcut
        result = create_subscription_checkout(
            price_id="price_test123", customer_email="customer@example.com"
        )

        # Verify client was called correctly
        call_kwargs = self.mock_client.create_checkout_session.call_args[1]
        self.assertEqual(call_kwargs["mode"], "subscription")

    def test_create_customer_from_user(self):
        """Test create_customer_from_user shortcut."""
        # Mock client response
        self.mock_client.create_customer.return_value = {
            "success": True,
            "customer": {"id": "cus_test123", "email": "test@example.com"},
        }

        # Call shortcut
        result = create_customer_from_user(self.user)

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["customer"]["id"], "cus_test123")

        # Verify client was called correctly
        self.mock_client.create_customer.assert_called_once()
        call_kwargs = self.mock_client.create_customer.call_args[1]
        self.assertEqual(call_kwargs["email"], "test@example.com")
        self.assertEqual(call_kwargs["metadata"]["user_id"], str(self.user.id))

    def test_cancel_user_subscription(self):
        """Test cancel_user_subscription shortcut."""
        # Mock client response
        self.mock_client.cancel_subscription.return_value = {
            "success": True,
            "subscription": {
                "id": "sub_test123",
                "status": "active",
                "cancel_at_period_end": True,
            },
        }

        # Call shortcut
        result = cancel_user_subscription("sub_test123")

        # Verify result
        self.assertTrue(result["success"])
        self.assertEqual(result["subscription"]["id"], "sub_test123")

        # Verify client was called correctly
        self.mock_client.cancel_subscription.assert_called_once_with(
            subscription_id="sub_test123",
            at_period_end=True,  # Default value
        )

        # Test with at_period_end=False
        self.mock_client.cancel_subscription.reset_mock()
        result = cancel_user_subscription("sub_test123", at_period_end=False)

        # Verify client was called correctly
        self.mock_client.cancel_subscription.assert_called_once_with(
            subscription_id="sub_test123", at_period_end=False
        )

    def test_handle_webhook_event(self):
        """Test handle_webhook_event shortcut."""
        # Mock client response
        self.mock_client.verify_webhook_signature.return_value = {
            "success": True,
            "verified": True,
            "event": {
                "id": "evt_test123",
                "type": "checkout.session.completed",
                "data": {"object": {"id": "cs_test123"}},
            },
        }

        # Call shortcut
        payload = b'{"type":"checkout.session.completed"}'
        signature = "test_signature"

        result = handle_webhook_event(payload, signature)

        # Verify result
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["event"]["id"], "evt_test123")

        # Verify client was called correctly
        self.mock_client.verify_webhook_signature.assert_called_once_with(
            payload=payload, signature=signature
        )
