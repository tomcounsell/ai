import logging
from typing import Any, Dict, Optional

from django.conf import settings

# For local development and testing without actual API calls
from icecream import ic

logger = logging.getLogger(__name__)

# Import Twilio client class only if TWILIO_ENABLED
try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.rest import Client as TwilioRestClient

    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    logger.warning("Twilio package not installed. Install with 'pip install twilio'")


class TwilioClient:
    """
    Client for interacting with the Twilio API.
    Handles SMS sending, phone number verification, and status callbacks.
    """

    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
    ):
        """
        Initialize the Twilio client.

        Args:
            account_sid: Twilio account SID. Defaults to settings.TWILIO_ACCOUNT_SID
            auth_token: Twilio auth token. Defaults to settings.TWILIO_AUTH_TOKEN
            from_number: Default phone number to send messages from. Defaults to settings.TWILIO_PHONE_NUMBER
        """
        self.account_sid = account_sid or getattr(settings, "TWILIO_ACCOUNT_SID", None)
        self.auth_token = auth_token or getattr(settings, "TWILIO_AUTH_TOKEN", None)
        self.from_number = from_number or getattr(settings, "TWILIO_PHONE_NUMBER", None)
        self.enabled = getattr(settings, "TWILIO_ENABLED", False)

        # Initialize client if Twilio is available and enabled
        self.client = None
        if TWILIO_AVAILABLE and self.enabled and self.account_sid and self.auth_token:
            try:
                self.client = TwilioRestClient(self.account_sid, self.auth_token)
            except Exception as e:
                logger.error(f"Failed to initialize Twilio client: {str(e)}")

    def _make_request(self, func_name: str, **kwargs) -> dict[str, Any]:
        """
        Make a request to the Twilio API.

        Args:
            func_name: Name of the Twilio client function to call
            **kwargs: Arguments to pass to the function

        Returns:
            Dict with response data or simulation data
        """
        # If in debug mode or not enabled, just log and return simulation
        if getattr(settings, "DEBUG", False) or not self.enabled:
            logger.info(
                f"[DEBUG/DISABLED] Twilio API request would have been: {func_name}"
            )
            logger.info(f"[DEBUG/DISABLED] Parameters: {kwargs}")
            return {"success": True, "simulated": True}

        # If not properly initialized, log error and return failure
        if not self.client:
            error_msg = "Twilio client not initialized. Check TWILIO_ENABLED, TWILIO_ACCOUNT_SID, and TWILIO_AUTH_TOKEN settings."
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        # Attempt actual API call
        try:
            func = getattr(self.client, func_name, None)
            if not func:
                error_msg = f"Function {func_name} not found in Twilio client"
                logger.error(error_msg)
                return {"success": False, "error": error_msg}

            result = func(**kwargs)
            return {"success": True, "data": result}
        except TwilioRestException as e:
            logger.error(f"Twilio API error: {str(e)}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"Error calling Twilio API: {str(e)}")
            return {"success": False, "error": str(e)}

    def send_sms(
        self,
        to_number: str,
        body: str,
        from_number: str | None = None,
        status_callback: str | None = None,
    ) -> dict[str, Any]:
        """
        Send an SMS message using Twilio.

        Args:
            to_number: Recipient phone number
            body: Message content
            from_number: Sender phone number (defaults to self.from_number)
            status_callback: URL to receive status updates

        Returns:
            Dict with response data or simulation data
        """
        # In debug mode or disabled, just log intent and return success
        if getattr(settings, "DEBUG", False) or not self.enabled:
            logger.info(f"[DEBUG/DISABLED] Would send SMS to {to_number}: {body}")
            if status_callback:
                logger.info(f"[DEBUG/DISABLED] Status callback URL: {status_callback}")

            # For DEBUG usage and testing
            ic(f"SMS to {to_number}: {body}")
            return {
                "success": True,
                "simulated": True,
                "sid": "SM00000000000000000000000000000000",
            }

        # Prepare parameters
        params = {
            "to": to_number,
            "body": body,
            "from_": from_number or self.from_number,
        }

        if status_callback:
            params["status_callback"] = status_callback

        # Make API request to send message
        return self._make_request("messages.create", **params)

    def verify_phone_number(self, phone_number: str) -> dict[str, Any]:
        """
        Verify if a phone number is valid and can receive SMS.

        Args:
            phone_number: Phone number to verify

        Returns:
            Dict with lookup results
        """
        # In debug mode or disabled, return simulated success
        if getattr(settings, "DEBUG", False) or not self.enabled:
            logger.info(f"[DEBUG/DISABLED] Would verify phone number: {phone_number}")
            return {"success": True, "simulated": True, "valid": True}

        # Use Twilio's Lookup API to validate the number
        try:
            if not self.client:
                return {"success": False, "error": "Twilio client not initialized"}

            result = self.client.lookups.phone_numbers(phone_number).fetch(
                type="carrier"
            )
            return {
                "success": True,
                "valid": True,
                "data": {
                    "carrier": result.carrier,
                    "country_code": result.country_code,
                    "national_format": result.national_format,
                },
            }
        except TwilioRestException as e:
            if e.code == 20404:  # Invalid phone number
                return {
                    "success": True,
                    "valid": False,
                    "error": "Invalid phone number",
                }
            logger.error(f"Twilio lookup error: {str(e)}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"Error verifying phone number: {str(e)}")
            return {"success": False, "error": str(e)}

    def get_message_status(self, message_sid: str) -> dict[str, Any]:
        """
        Get the current status of a message.

        Args:
            message_sid: The SID of the message to check

        Returns:
            Dict with message status
        """
        # In debug mode or disabled, return simulated status
        if getattr(settings, "DEBUG", False) or not self.enabled:
            logger.info(
                f"[DEBUG/DISABLED] Would check status of message: {message_sid}"
            )
            return {"success": True, "simulated": True, "status": "delivered"}

        # Fetch message status from Twilio
        try:
            if not self.client:
                return {"success": False, "error": "Twilio client not initialized"}

            message = self.client.messages(message_sid).fetch()
            return {
                "success": True,
                "status": message.status,
                "data": {
                    "sid": message.sid,
                    "to": message.to,
                    "from": message.from_,
                    "body": message.body,
                    "date_sent": message.date_sent,
                    "error_code": message.error_code,
                    "error_message": message.error_message,
                },
            }
        except TwilioRestException as e:
            logger.error(f"Twilio error fetching message: {str(e)}")
            return {"success": False, "error": str(e), "code": e.code}
        except Exception as e:
            logger.error(f"Error getting message status: {str(e)}")
            return {"success": False, "error": str(e)}


class TwilioAPIError(Exception):
    """Custom exception for Twilio API errors"""

    pass
