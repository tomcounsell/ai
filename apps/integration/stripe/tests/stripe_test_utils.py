"""
Utility functions for testing Stripe integrations.

This module provides test helpers for both debug mode and live mode tests,
ensuring proper environment setup for each test type and consistent mocking.
"""

from unittest.mock import MagicMock, patch


def setup_debug_mode():
    """
    Set up environment for Stripe tests in debug mode.

    Returns:
        tuple: (settings_patch, mock_settings) - use in with statement
    """
    # Create a patch for settings
    settings_patch = patch("apps.integration.stripe.client.settings")
    mock_settings = settings_patch.start()

    # Configure settings for debug mode
    mock_settings.DEBUG = True
    mock_settings.STRIPE_ENABLED = True

    return settings_patch, mock_settings


def setup_live_mode():
    """
    Set up environment for Stripe tests in live mode.

    Returns:
        tuple: (settings_patch, stripe_patch, mock_settings, mock_stripe)
    """
    # Create patches
    settings_patch = patch("apps.integration.stripe.client.settings")
    stripe_patch = patch("apps.integration.stripe.client.stripe")

    # Start patches
    mock_settings = settings_patch.start()
    mock_stripe = stripe_patch.start()

    # Configure settings for live mode
    mock_settings.DEBUG = False
    mock_settings.STRIPE_ENABLED = True

    # Configure mock stripe module
    configure_mock_stripe(mock_stripe)

    return settings_patch, stripe_patch, mock_settings, mock_stripe


def configure_mock_stripe(mock_stripe):
    """
    Configure common mock attributes and methods for the Stripe module.

    Args:
        mock_stripe: The mocked stripe module
    """
    # Set up error classes
    mock_stripe.error = MagicMock()
    mock_stripe.error.StripeError = type("StripeError", (Exception,), {})
    mock_stripe.error.SignatureVerificationError = type(
        "SignatureVerificationError", (Exception,), {}
    )

    # Set up checkout session
    mock_stripe.checkout = MagicMock()
    mock_stripe.checkout.Session = MagicMock()

    # Set up subscription
    mock_stripe.Subscription = MagicMock()

    # Set up customer
    mock_stripe.Customer = MagicMock()

    # Set up webhook
    mock_stripe.Webhook = MagicMock()


def teardown_patches(*patches):
    """
    Tear down all patches.

    Args:
        *patches: Patch objects to stop
    """
    for patch in patches:
        patch.stop()
