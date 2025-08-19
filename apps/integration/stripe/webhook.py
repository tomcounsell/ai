"""
Stripe webhook handler for processing Stripe events.

This module provides functions for handling various webhook events from Stripe,
such as payment success, subscription updates, and customer events.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from django.utils import timezone

from apps.common.models import Payment, Subscription, User
from apps.integration.stripe.shortcuts import handle_webhook_event

logger = logging.getLogger(__name__)


def handle_stripe_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    """
    Process a webhook from Stripe.

    This function verifies the webhook signature and routes the event to the
    appropriate handler function.

    Args:
        payload: The raw request payload (body)
        signature: The Stripe-Signature header value

    Returns:
        Dict with processing status and information
    """
    # Verify the webhook signature
    verification = handle_webhook_event(payload, signature)

    if not verification.get("success") or not verification.get("verified", False):
        logger.error(f"Webhook verification failed: {verification.get('error')}")
        return {
            "success": False,
            "error": verification.get("error", "Webhook verification failed"),
            "status": "invalid_signature",
        }

    event = verification.get("event", {})
    event_type = event.get("type", "")
    logger.info(f"Processing Stripe webhook: {event_type}")

    try:
        # Route event to appropriate handler
        if event_type == "checkout.session.completed":
            return handle_checkout_session_completed(event)
        elif event_type == "customer.subscription.created":
            return handle_subscription_created(event)
        elif event_type == "customer.subscription.updated":
            return handle_subscription_updated(event)
        elif event_type == "customer.subscription.deleted":
            return handle_subscription_deleted(event)
        elif event_type == "payment_intent.succeeded":
            return handle_payment_intent_succeeded(event)
        elif event_type == "payment_intent.payment_failed":
            return handle_payment_intent_failed(event)
        else:
            # Acknowledge but don't process other event types
            logger.info(f"Received unhandled Stripe webhook: {event_type}")
            return {"success": True, "status": "acknowledged", "event_type": event_type}
    except Exception as e:
        logger.error(f"Error processing webhook {event_type}: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "status": "error",
            "event_type": event_type,
        }


def handle_checkout_session_completed(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the checkout.session.completed event.

    This event occurs when a customer completes a checkout session, which could
    be for a one-time payment or the start of a subscription.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        session = event.get("data", {}).get("object", {})

        session_id = session.get("id")
        customer_id = session.get("customer")
        customer_email = session.get("customer_email")
        mode = session.get("mode", "payment")
        metadata = session.get("metadata", {})

        logger.info(f"Checkout session completed: {session_id}, Mode: {mode}")

        # Look for user based on metadata or email
        user = None
        user_id = metadata.get("user_id")
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.warning(f"User not found for user_id: {user_id}")
        elif customer_email:
            try:
                user = User.objects.get(email=customer_email)
            except User.DoesNotExist:
                logger.warning(f"User not found for email: {customer_email}")

        # Update user with Stripe customer ID if available
        if user and customer_id and not user.stripe_customer_id:
            user.stripe_customer_id = customer_id
            user.save(update_fields=["stripe_customer_id"])
            logger.info(
                f"Updated user {user.id} with Stripe customer ID: {customer_id}"
            )

        # Get payment intent details for one-time payments
        if mode == "payment":
            payment_intent = session.get("payment_intent", {})

            if payment_intent:
                # Create payment record
                payment = Payment.objects.create(
                    stripe_id=(
                        payment_intent
                        if isinstance(payment_intent, str)
                        else payment_intent.get("id")
                    ),
                    stripe_customer_id=customer_id or "",
                    status=Payment.STATUS_SUCCEEDED,
                    amount=session.get("amount_total", 0),
                    currency=session.get("currency", "USD").upper(),
                    description=metadata.get("description", "Checkout Payment"),
                    metadata=metadata,
                    user=user,
                    payment_method=Payment.PAYMENT_METHOD_CARD,
                )
                logger.info(f"Created payment record: {payment.id}")

        # For subscriptions, the actual subscription record will be created by
        # customer.subscription.created event, so we just acknowledge here

        return {
            "success": True,
            "status": "processed",
            "event_type": "checkout.session.completed",
            "session_id": session_id,
            "mode": mode,
        }
    except Exception as e:
        logger.error(f"Error processing checkout.session.completed: {str(e)}")
        raise


def handle_subscription_created(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the customer.subscription.created event.

    This event occurs when a subscription is created. We create a corresponding
    Subscription record in our database.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        subscription_data = event.get("data", {}).get("object", {})

        subscription_id = subscription_data.get("id")
        customer_id = subscription_data.get("customer")
        status = subscription_data.get("status", "incomplete")

        # Get first item and price
        items = subscription_data.get("items", {}).get("data", [])
        price_id = items[0].get("price", {}).get("id") if items else None

        metadata = subscription_data.get("metadata", {})

        # Get period data
        current_period_start = subscription_data.get("current_period_start")
        current_period_end = subscription_data.get("current_period_end")
        cancel_at_period_end = subscription_data.get("cancel_at_period_end", False)

        # Find user by customer ID
        user = None
        if customer_id:
            user = User.objects.filter(stripe_customer_id=customer_id).first()

        # Convert epoch timestamps to datetime objects
        if current_period_start:
            current_period_start = datetime.fromtimestamp(
                current_period_start, tz=timezone.get_current_timezone()
            )

        if current_period_end:
            current_period_end = datetime.fromtimestamp(
                current_period_end, tz=timezone.get_current_timezone()
            )

        # Get product details for display name
        product_id = items[0].get("price", {}).get("product") if items else None
        plan_name = metadata.get("plan_name", "Subscription")

        # Create subscription record
        subscription = Subscription.objects.create(
            stripe_id=subscription_id,
            stripe_customer_id=customer_id,
            stripe_price_id=price_id or "",
            status=status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            cancel_at_period_end=cancel_at_period_end,
            start_date=current_period_start,
            metadata=metadata,
            user=user,
            plan_name=plan_name,
            plan_description=metadata.get("plan_description", ""),
        )

        logger.info(
            f"Created subscription record: {subscription.id}, Stripe ID: {subscription_id}"
        )

        return {
            "success": True,
            "status": "processed",
            "event_type": "customer.subscription.created",
            "subscription_id": subscription_id,
            "subscription_status": status,
        }
    except Exception as e:
        logger.error(f"Error processing customer.subscription.created: {str(e)}")
        raise


def handle_subscription_updated(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the customer.subscription.updated event.

    This event occurs when a subscription is updated. We update the corresponding
    Subscription record in our database.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        subscription_data = event.get("data", {}).get("object", {})

        subscription_id = subscription_data.get("id")
        status = subscription_data.get("status", "")

        # Get period data
        current_period_start = subscription_data.get("current_period_start")
        current_period_end = subscription_data.get("current_period_end")
        cancel_at_period_end = subscription_data.get("cancel_at_period_end", False)

        # Convert epoch timestamps to datetime objects
        if current_period_start:
            current_period_start = datetime.fromtimestamp(
                current_period_start, tz=timezone.get_current_timezone()
            )

        if current_period_end:
            current_period_end = datetime.fromtimestamp(
                current_period_end, tz=timezone.get_current_timezone()
            )

        # Get canceled_at if available
        canceled_at = subscription_data.get("canceled_at")
        if canceled_at:
            canceled_at = datetime.fromtimestamp(
                canceled_at, tz=timezone.get_current_timezone()
            )

        # Update subscription record
        try:
            subscription = Subscription.objects.get(stripe_id=subscription_id)

            # Update fields
            subscription.status = status
            subscription.current_period_start = current_period_start
            subscription.current_period_end = current_period_end
            subscription.cancel_at_period_end = cancel_at_period_end

            if canceled_at:
                subscription.canceled_at = canceled_at

            subscription.save()

            logger.info(f"Updated subscription {subscription_id} to status: {status}")

            return {
                "success": True,
                "status": "processed",
                "event_type": "customer.subscription.updated",
                "subscription_id": subscription_id,
                "subscription_status": status,
            }
        except Subscription.DoesNotExist:
            logger.warning(f"Subscription {subscription_id} not found in database")
            # Create it instead
            return handle_subscription_created(event)
    except Exception as e:
        logger.error(f"Error processing customer.subscription.updated: {str(e)}")
        raise


def handle_subscription_deleted(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the customer.subscription.deleted event.

    This event occurs when a subscription is canceled or expires. We update
    the corresponding Subscription record in our database.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        subscription_data = event.get("data", {}).get("object", {})

        subscription_id = subscription_data.get("id")

        # Update subscription record
        try:
            subscription = Subscription.objects.get(stripe_id=subscription_id)

            # Update status
            subscription.status = Subscription.STATUS_CANCELED
            subscription.cancel_at_period_end = False

            # Set canceled_at if not already set
            if not subscription.canceled_at:
                subscription.canceled_at = timezone.now()

            subscription.save()

            logger.info(f"Marked subscription {subscription_id} as canceled")

            return {
                "success": True,
                "status": "processed",
                "event_type": "customer.subscription.deleted",
                "subscription_id": subscription_id,
            }
        except Subscription.DoesNotExist:
            logger.warning(f"Subscription {subscription_id} not found in database")
            return {
                "success": True,
                "status": "ignored",
                "event_type": "customer.subscription.deleted",
                "reason": "subscription_not_found",
            }
    except Exception as e:
        logger.error(f"Error processing customer.subscription.deleted: {str(e)}")
        raise


def handle_payment_intent_succeeded(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the payment_intent.succeeded event.

    This event occurs when a payment is successful. We create a Payment record
    and update the subscription if applicable.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        payment_intent = event.get("data", {}).get("object", {})

        payment_id = payment_intent.get("id")
        customer_id = payment_intent.get("customer")
        amount = payment_intent.get("amount", 0)
        currency = payment_intent.get("currency", "usd").upper()
        metadata = payment_intent.get("metadata", {})

        # Find related subscription
        subscription_id = payment_intent.get("subscription")
        subscription = None
        if subscription_id:
            try:
                subscription = Subscription.objects.get(stripe_id=subscription_id)
            except Subscription.DoesNotExist:
                logger.warning(
                    f"Subscription {subscription_id} not found for payment {payment_id}"
                )

        # Find user by customer ID
        user = None
        if customer_id:
            user = User.objects.filter(stripe_customer_id=customer_id).first()
        elif subscription and subscription.user:
            user = subscription.user

        # Create payment record
        payment_method_type = payment_intent.get("payment_method_types", ["card"])[0]
        payment_method = Payment.PAYMENT_METHOD_CARD
        if payment_method_type == "bank_transfer":
            payment_method = Payment.PAYMENT_METHOD_BANK
        elif payment_method_type in ["apple_pay", "google_pay", "alipay"]:
            payment_method = Payment.PAYMENT_METHOD_WALLET

        # Check if payment already exists
        if not Payment.objects.filter(stripe_id=payment_id).exists():
            payment = Payment.objects.create(
                stripe_id=payment_id,
                stripe_customer_id=customer_id or "",
                amount=amount,
                currency=currency,
                status=Payment.STATUS_SUCCEEDED,
                payment_method=payment_method,
                description=metadata.get("description", "Payment"),
                metadata=metadata,
                user=user,
                subscription=subscription,
                receipt_url=payment_intent.get("receipt_url", ""),
            )

            logger.info(
                f"Created payment record: {payment.id}, Stripe ID: {payment_id}"
            )
        else:
            logger.info(f"Payment {payment_id} already exists, skipping creation")

        return {
            "success": True,
            "status": "processed",
            "event_type": "payment_intent.succeeded",
            "payment_id": payment_id,
        }
    except Exception as e:
        logger.error(f"Error processing payment_intent.succeeded: {str(e)}")
        raise


def handle_payment_intent_failed(event: dict[str, Any]) -> dict[str, Any]:
    """
    Handle the payment_intent.payment_failed event.

    This event occurs when a payment fails. We create a failed Payment record.

    Args:
        event: The Stripe event data

    Returns:
        Dict with processing status and information
    """
    try:
        # Extract data from the event
        payment_intent = event.get("data", {}).get("object", {})

        payment_id = payment_intent.get("id")
        customer_id = payment_intent.get("customer")
        amount = payment_intent.get("amount", 0)
        currency = payment_intent.get("currency", "usd").upper()
        metadata = payment_intent.get("metadata", {})

        # Find related subscription
        subscription_id = payment_intent.get("subscription")
        subscription = None
        if subscription_id:
            try:
                subscription = Subscription.objects.get(stripe_id=subscription_id)
            except Subscription.DoesNotExist:
                logger.warning(
                    f"Subscription {subscription_id} not found for payment {payment_id}"
                )

        # Find user by customer ID
        user = None
        if customer_id:
            user = User.objects.filter(stripe_customer_id=customer_id).first()
        elif subscription and subscription.user:
            user = subscription.user

        # Check if payment already exists
        if not Payment.objects.filter(stripe_id=payment_id).exists():
            payment = Payment.objects.create(
                stripe_id=payment_id,
                stripe_customer_id=customer_id or "",
                amount=amount,
                currency=currency,
                status=Payment.STATUS_FAILED,
                payment_method=Payment.PAYMENT_METHOD_OTHER,
                description=metadata.get("description", "Failed Payment"),
                metadata=metadata,
                user=user,
                subscription=subscription,
            )

            logger.info(
                f"Created failed payment record: {payment.id}, Stripe ID: {payment_id}"
            )
        else:
            # Update existing payment to failed status
            Payment.objects.filter(stripe_id=payment_id).update(
                status=Payment.STATUS_FAILED
            )
            logger.info(f"Updated payment {payment_id} to failed status")

        return {
            "success": True,
            "status": "processed",
            "event_type": "payment_intent.payment_failed",
            "payment_id": payment_id,
        }
    except Exception as e:
        logger.error(f"Error processing payment_intent.payment_failed: {str(e)}")
        raise
