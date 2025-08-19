import logging
from typing import Any, Dict, Optional

from django.conf import settings
from django.urls import reverse

from apps.common.models.sms import SMS
from apps.integration.twilio.client import TwilioClient

logger = logging.getLogger(__name__)


def send_sms(
    to_number: str,
    body: str,
    from_number: str | None = None,
    save_to_db: bool = True,
) -> dict[str, Any]:
    """
    Send an SMS using Twilio and optionally save to database.

    Args:
        to_number: Recipient phone number
        body: Message content
        from_number: Sender phone number (defaults to settings.TWILIO_PHONE_NUMBER)
        save_to_db: Whether to save the SMS to the database

    Returns:
        Dict with response data and sms_id if saved to database
    """
    # Initialize Twilio client
    client = TwilioClient()

    # Create status callback URL if in production
    status_callback = None
    if not getattr(settings, "DEBUG", False) and hasattr(settings, "HOSTNAME"):
        hostname = settings.HOSTNAME
        if not hostname.startswith(("http://", "https://")):
            hostname = f"https://{hostname}"
        status_callback = f"{hostname}{reverse('api:twilio-webhook')}"

    # Create SMS record in database if requested
    sms_instance = None
    if save_to_db:
        sms_instance = SMS.objects.create(
            to_number=to_number,
            from_number=from_number or getattr(settings, "TWILIO_PHONE_NUMBER", None),
            body=body,
        )

    # Send the SMS via Twilio
    result = client.send_sms(
        to_number=to_number,
        body=body,
        from_number=from_number,
        status_callback=status_callback,
    )

    # Update SMS record with message SID if available
    if sms_instance and result.get("success"):
        message_sid = result.get("sid") or result.get("data", {}).get("sid")
        if message_sid:
            sms_instance.external_id = message_sid
            sms_instance.save(update_fields=["external_id"])

        # Mark as sent
        sms_instance.mark_as_sent()

    # Combine result with SMS instance ID
    response = result.copy()
    if sms_instance:
        response["sms_id"] = sms_instance.id

    return response


def verify_phone_number(phone_number: str) -> dict[str, Any]:
    """
    Verify if a phone number is valid and can receive SMS.

    Args:
        phone_number: Phone number to verify

    Returns:
        Dict with verification result
    """
    client = TwilioClient()
    return client.verify_phone_number(phone_number)


def get_sms_status(sms_id: int) -> dict[str, Any]:
    """
    Get the status of an SMS by its database ID.

    Args:
        sms_id: Database ID of the SMS

    Returns:
        Dict with status information
    """
    try:
        sms = SMS.objects.get(id=sms_id)

        # If no external ID, we can't check status
        if not hasattr(sms, "external_id") or not sms.external_id:
            return {
                "success": False,
                "error": "No external ID (message SID) for this SMS",
                "sms_data": {
                    "to_number": sms.to_number,
                    "body": sms.body,
                    "sent_at": sms.sent_at,
                    "read_at": sms.read_at,
                },
            }

        # Get status from Twilio
        client = TwilioClient()
        result = client.get_message_status(sms.external_id)

        # Combine with SMS data
        result["sms_data"] = {
            "to_number": sms.to_number,
            "body": sms.body,
            "sent_at": sms.sent_at,
            "read_at": sms.read_at,
        }

        return result

    except SMS.DoesNotExist:
        return {"success": False, "error": f"SMS with ID {sms_id} not found"}
    except Exception as e:
        logger.error(f"Error getting SMS status: {str(e)}")
        return {"success": False, "error": str(e)}


def send_verification_code(phone_number: str, code: str) -> dict[str, Any]:
    """
    Send a verification code via SMS.

    Args:
        phone_number: Recipient phone number
        code: Verification code

    Returns:
        Dict with response data
    """
    body = f"Your verification code is: {code}"
    return send_sms(to_number=phone_number, body=body)
