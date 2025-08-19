"""
Unit tests for the StripeClient class.
"""

import json
from unittest.mock import MagicMock

from django.test import TestCase

from apps.integration.stripe.client import StripeClient
from apps.integration.stripe.tests.stripe_test_utils import (
    setup_debug_mode,
    setup_live_mode,
    teardown_patches,
)


class StripeClientDebugModeTestCase(TestCase):
    """Test StripeClient in DEBUG mode."""

    def setUp(self):
        super().setUp()
        # Set up debug mode environment
        self.settings_patch, self.mock_settings = setup_debug_mode()
        self.client = StripeClient(api_key="test_key", webhook_secret="test_secret")

    def tearDown(self):
        teardown_patches(self.settings_patch)
        super().tearDown()

    def test_create_checkout_session_debug_mode(self):
        """Test create_checkout_session in DEBUG mode."""
        result = self.client.create_checkout_session(
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            customer_email="test@example.com",
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["simulated"])
        self.assertEqual(result["session"]["id"], "cs_test_simulated")
        self.assertTrue("checkout.stripe.com" in result["session"]["url"])

    def test_create_subscription_debug_mode(self):
        """Test create_subscription in DEBUG mode."""
        result = self.client.create_subscription(
            customer_id="cus_test123", price_id="price_test123", trial_days=14
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["simulated"])
        self.assertEqual(result["subscription"]["id"], "sub_simulated")
        self.assertEqual(result["subscription"]["customer"], "cus_test123")

    def test_cancel_subscription_debug_mode(self):
        """Test cancel_subscription in DEBUG mode."""
        result = self.client.cancel_subscription(
            subscription_id="sub_test123", at_period_end=True
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["simulated"])
        self.assertEqual(result["subscription"]["id"], "sub_test123")
        self.assertTrue(result["subscription"]["cancel_at_period_end"])

    def test_create_customer_debug_mode(self):
        """Test create_customer in DEBUG mode."""
        result = self.client.create_customer(email="test@example.com", name="Test User")
        self.assertTrue(result["success"])
        self.assertTrue(result["simulated"])
        self.assertEqual(result["customer"]["email"], "test@example.com")
        self.assertEqual(result["customer"]["name"], "Test User")

    def test_verify_webhook_signature_debug_mode(self):
        """Test verify_webhook_signature in DEBUG mode."""
        # Create a mock event payload
        payload = json.dumps(
            {
                "id": "evt_test123",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test123",
                        "object": "checkout.session",
                        "customer": "cus_test123",
                    }
                },
            }
        ).encode("utf-8")

        result = self.client.verify_webhook_signature(
            payload=payload, signature="test_signature"
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["simulated"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["event"]["type"], "checkout.session.completed")


class StripeClientLiveTestCase(TestCase):
    """Test StripeClient in live mode with mocked responses."""

    def setUp(self):
        super().setUp()
        # Set up live mode environment with mocked modules
        (
            self.settings_patch,
            self.stripe_patch,
            self.mock_settings,
            self.mock_stripe,
        ) = setup_live_mode()

        # Create client in this controlled environment
        self.client = StripeClient(api_key="test_key", webhook_secret="test_secret")

    def tearDown(self):
        teardown_patches(self.settings_patch, self.stripe_patch)
        super().tearDown()

    def test_validate_client(self):
        """Test _validate_client method."""
        # Valid client
        self.assertTrue(self.client._validate_client())

        # Invalid client - no API key
        self.client.api_key = None
        self.assertFalse(self.client._validate_client())

        # Invalid client - disabled
        self.client.api_key = "test_key"
        self.client.enabled = False
        self.assertFalse(self.client._validate_client())

    def test_create_checkout_session_success(self):
        """Test create_checkout_session with success response."""
        # Mock Stripe response
        mock_session = MagicMock()
        mock_session.id = "cs_test123"
        mock_session.url = "https://checkout.stripe.com/pay/cs_test123"
        mock_session.get.side_effect = lambda k, d=None: {
            "payment_intent": "pi_test123",
            "client_secret": "cs_secret_test123",
        }.get(k, d)

        self.mock_stripe.checkout.Session.create.return_value = mock_session

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True

        # Call the method
        result = self.client.create_checkout_session(
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            customer_email="test@example.com",
        )

        # Verify the result
        self.assertTrue(result["success"])
        self.assertEqual(result["session"]["id"], "cs_test123")
        self.assertEqual(
            result["session"]["url"], "https://checkout.stripe.com/pay/cs_test123"
        )
        self.assertEqual(result["session"]["payment_intent"], "pi_test123")

        # Verify Stripe was called correctly
        self.mock_stripe.checkout.Session.create.assert_called_once()
        call_kwargs = self.mock_stripe.checkout.Session.create.call_args[1]
        self.assertEqual(call_kwargs["line_items"][0]["price"], "price_test123")
        self.assertEqual(call_kwargs["success_url"], "https://example.com/success")
        self.assertEqual(call_kwargs["cancel_url"], "https://example.com/cancel")
        self.assertEqual(call_kwargs["customer_email"], "test@example.com")

    def test_create_checkout_session_error(self):
        """Test create_checkout_session with error response."""
        # Mock Stripe error
        stripe_error = self.mock_stripe.error.StripeError("Invalid API key")
        stripe_error.code = "invalid_request_error"
        self.mock_stripe.checkout.Session.create.side_effect = stripe_error

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True

        # Call the method
        result = self.client.create_checkout_session(
            price_id="price_test123",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        # Verify the result
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "Invalid API key")
        self.assertEqual(result["code"], "invalid_request_error")

    def test_create_subscription_success(self):
        """Test create_subscription with success response."""
        # Mock Stripe response
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test123"
        mock_subscription.customer = "cus_test123"
        mock_subscription.status = "active"
        mock_subscription.current_period_end = 1672531200

        self.mock_stripe.Subscription.create.return_value = mock_subscription

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True

        # Call the method
        result = self.client.create_subscription(
            customer_id="cus_test123", price_id="price_test123", trial_days=14
        )

        # Verify the result
        self.assertTrue(result["success"])
        self.assertEqual(result["subscription"]["id"], "sub_test123")
        self.assertEqual(result["subscription"]["customer"], "cus_test123")
        self.assertEqual(result["subscription"]["status"], "active")

        # Verify Stripe was called correctly
        self.mock_stripe.Subscription.create.assert_called_once()
        call_kwargs = self.mock_stripe.Subscription.create.call_args[1]
        self.assertEqual(call_kwargs["customer"], "cus_test123")
        self.assertEqual(call_kwargs["items"][0]["price"], "price_test123")
        self.assertEqual(call_kwargs["trial_period_days"], 14)

    def test_cancel_subscription_success(self):
        """Test cancel_subscription with success response."""
        # Mock Stripe responses
        mock_subscription = MagicMock()
        mock_subscription.id = "sub_test123"
        mock_subscription.status = "active"
        mock_subscription.cancel_at_period_end = True

        self.mock_stripe.Subscription.modify.return_value = mock_subscription
        self.mock_stripe.Subscription.delete.return_value = mock_subscription

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True

        # Test cancel at period end
        result = self.client.cancel_subscription(
            subscription_id="sub_test123", at_period_end=True
        )

        # Verify the result
        self.assertTrue(result["success"])
        self.assertEqual(result["subscription"]["id"], "sub_test123")
        self.assertTrue(result["subscription"]["cancel_at_period_end"])

        # Verify Stripe was called correctly
        self.mock_stripe.Subscription.modify.assert_called_once_with(
            "sub_test123", cancel_at_period_end=True
        )

        # Test immediate cancellation
        result = self.client.cancel_subscription(
            subscription_id="sub_test123", at_period_end=False
        )

        # Verify Stripe was called correctly
        self.mock_stripe.Subscription.delete.assert_called_once_with("sub_test123")

    def test_create_customer_success(self):
        """Test create_customer with success response."""
        # Mock Stripe response
        mock_customer = MagicMock()
        mock_customer.id = "cus_test123"
        mock_customer.email = "test@example.com"
        mock_customer.name = "Test User"

        self.mock_stripe.Customer.create.return_value = mock_customer

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True

        # Call the method
        result = self.client.create_customer(
            email="test@example.com", name="Test User", metadata={"user_id": "123"}
        )

        # Verify the result
        self.assertTrue(result["success"])
        self.assertEqual(result["customer"]["id"], "cus_test123")
        self.assertEqual(result["customer"]["email"], "test@example.com")
        self.assertEqual(result["customer"]["name"], "Test User")

        # Verify Stripe was called correctly
        self.mock_stripe.Customer.create.assert_called_once()
        call_kwargs = self.mock_stripe.Customer.create.call_args[1]
        self.assertEqual(call_kwargs["email"], "test@example.com")
        self.assertEqual(call_kwargs["name"], "Test User")
        self.assertEqual(call_kwargs["metadata"], {"user_id": "123"})

    def test_verify_webhook_signature_success(self):
        """Test verify_webhook_signature with success response."""
        # Mock Stripe response
        mock_event = MagicMock()
        mock_event.id = "evt_test123"
        mock_event.type = "checkout.session.completed"
        mock_event.data = {"object": {"id": "cs_test123"}}

        self.mock_stripe.Webhook.construct_event.return_value = mock_event

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True
        self.client.webhook_secret = "test_secret"

        # Call the method
        payload = b'{"type":"checkout.session.completed"}'
        signature = "test_signature"

        result = self.client.verify_webhook_signature(
            payload=payload, signature=signature
        )

        # Verify the result
        self.assertTrue(result["success"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["event"]["id"], "evt_test123")
        self.assertEqual(result["event"]["type"], "checkout.session.completed")

        # Verify Stripe was called correctly
        self.mock_stripe.Webhook.construct_event.assert_called_once_with(
            payload=payload, sig_header=signature, secret=self.client.webhook_secret
        )

    def test_verify_webhook_signature_invalid(self):
        """Test verify_webhook_signature with invalid signature."""
        # Mock Stripe error
        signature_error = self.mock_stripe.error.SignatureVerificationError(
            "Invalid signature"
        )
        self.mock_stripe.Webhook.construct_event.side_effect = signature_error

        # Restore client state
        self.client.api_key = "test_key"
        self.client.enabled = True
        self.client.webhook_secret = "test_secret"

        # Call the method
        payload = b'{"type":"checkout.session.completed"}'
        signature = "invalid_signature"

        result = self.client.verify_webhook_signature(
            payload=payload, signature=signature
        )

        # Verify the result
        self.assertFalse(result["success"])
        self.assertFalse(result["verified"])
        self.assertEqual(result["error"], "Invalid signature")
