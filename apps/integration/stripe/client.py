"""
Client for interacting with the Stripe API.

This module provides a client for interacting with Stripe services,
handling authentication, API calls, error handling, and webhooks.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Union

from django.conf import settings
from icecream import ic

# Import Stripe only if it's available
try:
    import stripe

    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    logging.warning("Stripe package not installed. Install with 'pip install stripe'")

logger = logging.getLogger(__name__)


class StripeClient:
    """
    Client for interacting with the Stripe API.
    Handles payment processing, subscriptions, and webhook events.
    """

    def __init__(self, api_key: str | None = None, webhook_secret: str | None = None):
        """
        Initialize the Stripe client.

        Args:
            api_key: Stripe API key. Defaults to settings.STRIPE_API_KEY
            webhook_secret: Stripe webhook signing secret. Defaults to settings.STRIPE_WEBHOOK_SECRET
        """
        self.api_key = api_key or getattr(settings, "STRIPE_API_KEY", None)
        self.webhook_secret = webhook_secret or getattr(
            settings, "STRIPE_WEBHOOK_SECRET", None
        )
        self.enabled = getattr(settings, "STRIPE_ENABLED", False)

        # Initialize stripe library if available
        if STRIPE_AVAILABLE and self.api_key:
            stripe.api_key = self.api_key

    def _validate_client(self) -> bool:
        """
        Validate that the client is properly configured and ready to use.

        Returns:
            bool: True if the client is valid, False otherwise
        """
        if not STRIPE_AVAILABLE:
            logger.error("Stripe package not installed")
            return False

        if not self.enabled:
            logger.warning("Stripe integration is disabled")
            return False

        if not self.api_key:
            logger.error("Stripe API key not configured")
            return False

        return True

    def create_checkout_session(
        self,
        price_id: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None = None,
        mode: str = "payment",
        payment_method_types: list[str] | None = None,
        metadata: dict[str, str] | None = None,
        customer_id: str | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Create a Stripe Checkout Session.

        Args:
            price_id: The ID of the Stripe Price object
            success_url: The URL to redirect to after successful payment
            cancel_url: The URL to redirect to if checkout is canceled
            customer_email: The customer's email address (optional)
            mode: The mode of the checkout ('payment', 'subscription', 'setup')
            payment_method_types: List of payment method types to accept
            metadata: Additional metadata to include with the checkout session
            customer_id: The ID of an existing Stripe customer (optional)
            **kwargs: Additional parameters to pass to the Stripe API

        Returns:
            Dict containing checkout session data or error information
        """
        # In debug mode or disabled, just log and return simulated response
        if getattr(settings, "DEBUG", False) or not self.enabled:
            debug_response = {
                "success": True,
                "simulated": True,
                "session": {
                    "id": "cs_test_simulated",
                    "url": "https://checkout.stripe.com/pay/cs_test_simulated",
                    "payment_intent": "pi_simulated",
                    "client_secret": "cs_secret_simulated",
                },
            }
            logger.info(
                f"[DEBUG/DISABLED] Would create Stripe checkout session: {price_id}"
            )
            logger.info(f"[DEBUG/DISABLED] Success URL: {success_url}")
            logger.info(f"[DEBUG/DISABLED] Cancel URL: {cancel_url}")
            logger.info(f"[DEBUG/DISABLED] Customer email: {customer_email}")
            # For DEBUG usage and testing
            ic(f"Stripe checkout session for {price_id}")
            return debug_response

        # Validate client configuration
        if not self._validate_client():
            return {"success": False, "error": "Stripe client not properly configured"}

        # Set up line items
        line_items = [{"price": price_id, "quantity": 1}]

        # Set up session parameters
        session_params = {
            "line_items": line_items,
            "mode": mode,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }

        # Add optional parameters
        if customer_email:
            session_params["customer_email"] = customer_email

        if customer_id:
            session_params["customer"] = customer_id

        if payment_method_types:
            session_params["payment_method_types"] = payment_method_types

        if metadata:
            session_params["metadata"] = metadata

        # Add any additional parameters
        session_params.update(kwargs)

        try:
            # Create the checkout session
            session = stripe.checkout.Session.create(**session_params)

            return {
                "success": True,
                "session": {
                    "id": session.id,
                    "url": session.url,
                    "payment_intent": session.get("payment_intent"),
                    "client_secret": session.get("client_secret"),
                },
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {str(e)}")
            return {"success": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:
            logger.error(f"Error creating Stripe checkout session: {str(e)}")
            return {"success": False, "error": str(e)}

    def create_subscription(
        self,
        customer_id: str,
        price_id: str,
        trial_days: int | None = None,
        metadata: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Create a subscription for a customer.

        Args:
            customer_id: The ID of the Stripe customer
            price_id: The ID of the price to subscribe to
            trial_days: Number of days for the trial period (optional)
            metadata: Additional metadata to include with the subscription
            **kwargs: Additional parameters to pass to the Stripe API

        Returns:
            Dict containing subscription data or error information
        """
        # In debug mode or disabled, just log and return simulated response
        if getattr(settings, "DEBUG", False) or not self.enabled:
            debug_response = {
                "success": True,
                "simulated": True,
                "subscription": {
                    "id": "sub_simulated",
                    "customer": customer_id,
                    "status": "active",
                    "current_period_end": 1672531200,  # Example timestamp
                },
            }
            logger.info(
                f"[DEBUG/DISABLED] Would create Stripe subscription: {price_id}"
            )
            logger.info(f"[DEBUG/DISABLED] Customer: {customer_id}")
            logger.info(f"[DEBUG/DISABLED] Trial days: {trial_days}")
            # For DEBUG usage and testing
            ic(f"Stripe subscription for {customer_id} to {price_id}")
            return debug_response

        # Validate client configuration
        if not self._validate_client():
            return {"success": False, "error": "Stripe client not properly configured"}

        # Set up subscription parameters
        subscription_params = {
            "customer": customer_id,
            "items": [{"price": price_id}],
        }

        # Add trial period if specified
        if trial_days:
            subscription_params["trial_period_days"] = trial_days

        # Add metadata if provided
        if metadata:
            subscription_params["metadata"] = metadata

        # Add any additional parameters
        subscription_params.update(kwargs)

        try:
            # Create the subscription
            subscription = stripe.Subscription.create(**subscription_params)

            return {
                "success": True,
                "subscription": {
                    "id": subscription.id,
                    "customer": subscription.customer,
                    "status": subscription.status,
                    "current_period_end": subscription.current_period_end,
                },
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating subscription: {str(e)}")
            return {"success": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:
            logger.error(f"Error creating Stripe subscription: {str(e)}")
            return {"success": False, "error": str(e)}

    def cancel_subscription(
        self, subscription_id: str, at_period_end: bool = False, **kwargs
    ) -> dict[str, Any]:
        """
        Cancel a subscription.

        Args:
            subscription_id: The ID of the subscription to cancel
            at_period_end: Whether to cancel at the end of the billing period
            **kwargs: Additional parameters to pass to the Stripe API

        Returns:
            Dict containing cancellation result or error information
        """
        # In debug mode or disabled, just log and return simulated response
        if getattr(settings, "DEBUG", False) or not self.enabled:
            debug_response = {
                "success": True,
                "simulated": True,
                "subscription": {
                    "id": subscription_id,
                    "status": "canceled" if not at_period_end else "active",
                    "cancel_at_period_end": at_period_end,
                },
            }
            logger.info(
                f"[DEBUG/DISABLED] Would cancel Stripe subscription: {subscription_id}"
            )
            logger.info(f"[DEBUG/DISABLED] At period end: {at_period_end}")
            # For DEBUG usage and testing
            ic(f"Stripe cancel subscription {subscription_id}")
            return debug_response

        # Validate client configuration
        if not self._validate_client():
            return {"success": False, "error": "Stripe client not properly configured"}

        try:
            if at_period_end:
                # Schedule cancellation at period end
                subscription = stripe.Subscription.modify(
                    subscription_id, cancel_at_period_end=True, **kwargs
                )
            else:
                # Cancel immediately
                subscription = stripe.Subscription.delete(subscription_id, **kwargs)

            return {
                "success": True,
                "subscription": {
                    "id": subscription.id,
                    "status": subscription.status,
                    "cancel_at_period_end": subscription.cancel_at_period_end,
                },
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error canceling subscription: {str(e)}")
            return {"success": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:
            logger.error(f"Error canceling Stripe subscription: {str(e)}")
            return {"success": False, "error": str(e)}

    def create_customer(
        self,
        email: str,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Create a Stripe customer.

        Args:
            email: The customer's email address
            name: The customer's name (optional)
            metadata: Additional metadata to include with the customer
            **kwargs: Additional parameters to pass to the Stripe API

        Returns:
            Dict containing customer data or error information
        """
        # In debug mode or disabled, just log and return simulated response
        if getattr(settings, "DEBUG", False) or not self.enabled:
            debug_response = {
                "success": True,
                "simulated": True,
                "customer": {
                    "id": "cus_simulated",
                    "email": email,
                    "name": name,
                },
            }
            logger.info(f"[DEBUG/DISABLED] Would create Stripe customer: {email}")
            logger.info(f"[DEBUG/DISABLED] Name: {name}")
            # For DEBUG usage and testing
            ic(f"Stripe create customer {email}")
            return debug_response

        # Validate client configuration
        if not self._validate_client():
            return {"success": False, "error": "Stripe client not properly configured"}

        # Set up customer parameters
        customer_params = {
            "email": email,
        }

        # Add name if provided
        if name:
            customer_params["name"] = name

        # Add metadata if provided
        if metadata:
            customer_params["metadata"] = metadata

        # Add any additional parameters
        customer_params.update(kwargs)

        try:
            # Create the customer
            customer = stripe.Customer.create(**customer_params)

            return {
                "success": True,
                "customer": {
                    "id": customer.id,
                    "email": customer.email,
                    "name": customer.name,
                },
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating customer: {str(e)}")
            return {"success": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {str(e)}")
            return {"success": False, "error": str(e)}

    def verify_webhook_signature(
        self, payload: str | bytes, signature: str
    ) -> dict[str, Any]:
        """
        Verify a webhook signature from Stripe.

        Args:
            payload: The raw request payload (body)
            signature: The Stripe-Signature header value

        Returns:
            Dict with verification result and event data if successful
        """
        # In debug mode or disabled, just log and return simulated response
        if getattr(settings, "DEBUG", False) or not self.enabled:
            # Parse the payload to get event data for debug info
            try:
                if isinstance(payload, bytes):
                    payload_str = payload.decode("utf-8")
                else:
                    payload_str = payload

                event_data = json.loads(payload_str)
                event_type = event_data.get("type", "unknown")

                debug_response = {
                    "success": True,
                    "simulated": True,
                    "verified": True,
                    "event": {
                        "id": "evt_simulated",
                        "type": event_type,
                        "data": event_data.get("data", {}),
                    },
                }

                logger.info(
                    f"[DEBUG/DISABLED] Would verify Stripe webhook: {event_type}"
                )
                # For DEBUG usage and testing
                ic(f"Stripe webhook verification for {event_type}")
                return debug_response
            except Exception as e:
                logger.error(f"Error parsing webhook payload in debug mode: {str(e)}")
                return {"success": False, "error": f"Invalid webhook payload: {str(e)}"}

        # Validate client configuration
        if not self._validate_client():
            return {"success": False, "error": "Stripe client not properly configured"}

        # Check if webhook secret is configured
        if not self.webhook_secret:
            logger.error("Stripe webhook secret not configured")
            return {"success": False, "error": "Webhook secret not configured"}

        try:
            # Verify the signature
            event = stripe.Webhook.construct_event(
                payload=payload, sig_header=signature, secret=self.webhook_secret
            )

            return {
                "success": True,
                "verified": True,
                "event": {"id": event.id, "type": event.type, "data": event.data},
            }
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Stripe signature verification failed: {str(e)}")
            return {"success": False, "verified": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Error verifying Stripe webhook: {str(e)}")
            return {"success": False, "error": str(e)}


class StripeAPIError(Exception):
    """Custom exception for Stripe API errors"""

    pass
