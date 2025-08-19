"""
Shortcuts for Stripe integration.

This module provides easy-to-use functions for common Stripe operations like
creating checkout sessions, managing customers, and handling subscriptions.
"""

import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.urls import reverse

from apps.common.models import User
from apps.integration.stripe.client import StripeClient

logger = logging.getLogger(__name__)


def create_checkout_session(
    price_id: str,
    success_url: str = None,
    cancel_url: str = None,
    customer_email: str | None = None,
    mode: str = "payment",
    metadata: dict[str, str] | None = None,
    user: User | None = None,
) -> dict[str, Any]:
    """
    Create a Stripe Checkout Session for payment.

    Args:
        price_id: The ID of the Stripe Price object
        success_url: The URL to redirect to after successful payment (default: site homepage)
        cancel_url: The URL to redirect to if checkout is canceled (default: site homepage)
        customer_email: The customer's email address
        mode: The mode of the checkout ('payment', 'subscription', 'setup')
        metadata: Additional metadata to include with the checkout session
        user: User model instance (optional, used to set customer info if provided)

    Returns:
        Dict containing checkout session data with URL to redirect the user to
    """
    client = StripeClient()

    # Set default URLs if not provided
    hostname = (
        settings.HOSTNAME
        if settings.HOSTNAME.startswith(("http://", "https://"))
        else f"https://{settings.HOSTNAME}"
    )

    if not success_url:
        success_url = f"{hostname}{reverse('public:home')}?payment_result=success"

    if not cancel_url:
        cancel_url = f"{hostname}{reverse('public:home')}?payment_result=canceled"

    # Use user email if provided and no email explicitly specified
    if user and not customer_email:
        customer_email = user.email

    # Add user info to metadata if available
    if user and metadata is None:
        metadata = {}

    if user and metadata is not None:
        metadata.update(
            {
                "user_id": str(user.id),
                "user_email": user.email,
            }
        )

    # Create the checkout session
    result = client.create_checkout_session(
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=customer_email,
        mode=mode,
        metadata=metadata,
    )

    if not result.get("success"):
        logger.error(f"Failed to create checkout session: {result.get('error')}")

    return result


def create_subscription_checkout(
    price_id: str,
    success_url: str = None,
    cancel_url: str = None,
    customer_email: str | None = None,
    metadata: dict[str, str] | None = None,
    user: User | None = None,
) -> dict[str, Any]:
    """
    Create a Stripe Checkout Session for subscription.

    This is a convenience wrapper around create_checkout_session with mode='subscription'.

    Args:
        price_id: The ID of the Stripe Price object
        success_url: The URL to redirect to after successful payment
        cancel_url: The URL to redirect to if checkout is canceled
        customer_email: The customer's email address
        metadata: Additional metadata to include with the checkout session
        user: User model instance (optional, used to set customer info if provided)

    Returns:
        Dict containing checkout session data with URL to redirect the user to
    """
    return create_checkout_session(
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=customer_email,
        mode="subscription",
        metadata=metadata,
        user=user,
    )


def create_customer_from_user(user: User) -> dict[str, Any]:
    """
    Create a Stripe customer from a User model instance.

    Args:
        user: The User model instance

    Returns:
        Dict containing customer data or error information
    """
    client = StripeClient()

    # Get user information
    email = user.email
    name = user.get_full_name() or None

    # Create metadata with user information
    metadata = {"user_id": str(user.id), "source": "django_application"}

    # Create the customer
    result = client.create_customer(email=email, name=name, metadata=metadata)

    if not result.get("success"):
        logger.error(
            f"Failed to create Stripe customer for user {user.id}: {result.get('error')}"
        )

    return result


def cancel_user_subscription(
    subscription_id: str,
    at_period_end: bool = True,
) -> dict[str, Any]:
    """
    Cancel a user's subscription.

    Args:
        subscription_id: The ID of the subscription to cancel
        at_period_end: Whether to cancel at the end of the billing period (default: True)

    Returns:
        Dict containing cancellation result or error information
    """
    client = StripeClient()

    # Cancel the subscription
    result = client.cancel_subscription(
        subscription_id=subscription_id, at_period_end=at_period_end
    )

    if not result.get("success"):
        logger.error(
            f"Failed to cancel subscription {subscription_id}: {result.get('error')}"
        )

    return result


def handle_webhook_event(payload: bytes, signature: str) -> dict[str, Any]:
    """
    Handle a webhook event from Stripe.

    This function verifies the webhook signature and returns the event data.
    It does not process the event; that should be done by the calling code.

    Args:
        payload: The raw request payload (body)
        signature: The Stripe-Signature header value

    Returns:
        Dict with verification result and event data if successful
    """
    client = StripeClient()

    # Verify the webhook signature
    result = client.verify_webhook_signature(payload=payload, signature=signature)

    if not result.get("success") or not result.get("verified", False):
        logger.error(f"Failed to verify webhook signature: {result.get('error')}")

    return result
